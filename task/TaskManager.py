from datetime import datetime
import json
import uuid
import logging
from typing import Dict, List, Any
from utils.dataConverter import convert_task_cmd_to_task
from task.TaskDatabase import TaskDatabase
from task.TaskScheduler import TaskScheduler
from dataModels.CommandModels import TaskCmd, CmdType, CommandEnvelope
from dataModels.TaskModels import Task, Station, StationConfig, OperationConfig, OperationMode, RobotMode, TaskStatus, StationTaskStatus
from dataModels.UnifiedCommand import UnifiedCommand, CommandStatus, create_unified_command

class TaskManager:
    """任务管理器 - 主控制器，负责任务的接收、解析和调度"""

    def __init__(self, config: Dict[str, Any] = None, use_mock: bool = True):
        """初始化任务管理器

        Args:
            config: 系统配置字典
            use_mock: 是否使用Mock机器人控制器
        """
        self.config = config or {}
        self.use_mock = use_mock
        self.logger = logging.getLogger(__name__)

        # 初始化机器人控制器（由TaskManager完全管理）
        self._init_robot_controller()

        # 初始化数据库和调度器
        self.database = TaskDatabase()
        self.scheduler = TaskScheduler(self.robot_controller, self.database)
        
        # 启动调度器
        self.scheduler.start()
        
        # 注册调度器回调
        self.scheduler.register_callback("on_task_start", self._on_task_start)
        self.scheduler.register_callback("on_task_complete", self._on_task_complete)
        self.scheduler.register_callback("on_task_failed", self._on_task_failed)
        self.scheduler.register_callback("on_station_start", self._on_station_start)
        self.scheduler.register_callback("on_station_complete", self._on_station_complete)
        self.scheduler.register_callback("on_station_retry", self._on_station_retry)

        # 新增：系统级回调（用于TaskManager -> RobotControlSystem通信）
        self.system_callbacks = {
            "on_data_ready": None,            # 数据准备就绪回调（用于上报）
            "on_arrive_station": None,        # 到达站点回调
        }

    def _init_robot_controller(self):
        """初始化机器人控制器（内部方法，完全封装）"""
        try:
            if self.use_mock:
                self.logger.info("TaskManager: 使用Mock机器人控制器")
                from robot.MockRobotController import MockRobotController
                self.robot_controller = MockRobotController(self.config)
            else:
                self.logger.info("TaskManager: 使用真实机器人控制器")
                from robot.RobotController import RobotController as RealRobotController
                self.robot_controller = RealRobotController(self.config)

            # 初始化机器人系统
            if not self.robot_controller.setup_system():
                raise Exception("机器人系统初始化失败")

            self.logger.info("TaskManager: 机器人控制器初始化成功")

        except Exception as e:
            self.logger.error(f"TaskManager: 初始化机器人控制器失败: {e}")
            raise

    def get_robot_status(self) -> Dict[str, Any]:
        """获取机器人状态（供RobotControlSystem调用）

        Returns:
            Dict[str, Any]: 机器人状态信息
        """
        try:
            return self.robot_controller.get_status()
        except Exception as e:
            self.logger.error(f"获取机器人状态失败: {e}")
            return {}

    def get_environment_data(self) -> Dict[str, Any]:
        """获取环境数据（供RobotControlSystem调用）

        Returns:
            Dict[str, Any]: 环境数据
        """
        try:
            return self.robot_controller.get_environment_data()
        except Exception as e:
            self.logger.error(f"获取环境数据失败: {e}")
            return {}

    def execute_emergency_stop(self) -> bool:
        """执行紧急停止（供RobotControlSystem调用）

        Returns:
            bool: 是否成功
        """
        try:
            return self.robot_controller.emergency_stop()
        except Exception as e:
            self.logger.error(f"执行紧急停止失败: {e}")
            return False

    def receive_command(self, command_envelope: CommandEnvelope) -> str:
        """接收并处理所有类型的命令（统一入口）

        Args:
            command_envelope: 命令信封

        Returns:
            str: 命令ID
        """
        try:
            cmd_id = command_envelope.cmd_id
            cmd_type = command_envelope.cmd_type
            data_json = command_envelope.data_json

            self.logger.info(f"TaskManager接收命令: {cmd_id}, 类型: {cmd_type.value}")

            # 根据命令类型创建UnifiedCommand
            if cmd_type == CmdType.TASK_CMD:
                # 解析TaskCmd
                task_cmd = self._parse_task_cmd_from_data_json(data_json)
                # 转换为Task对象
                task = convert_task_cmd_to_task(task_cmd)
                # 创建统一命令
                command = create_unified_command(
                    command_id=cmd_id,
                    cmd_type=cmd_type,
                    data=task,
                    metadata={"source": "receive_command", "robot_id": command_envelope.robot_id}
                )
                # 同时保存到tasks表（兼容性）
                self.database.save_task(task)

            else:
                # 其他命令类型直接使用data_json
                command = create_unified_command(
                    command_id=cmd_id,
                    cmd_type=cmd_type,
                    data=data_json,
                    metadata={"source": "receive_command", "robot_id": command_envelope.robot_id}
                )

            # 添加到调度器队列
            self.scheduler.add_command(command)

            self.logger.info(f"命令已提交到调度器: {cmd_id}")
            return cmd_id

        except Exception as e:
            self.logger.error(f"接收命令失败: {e}")
            raise

    def _parse_task_cmd_from_data_json(self, data_json: Dict[str, Any]) -> TaskCmd:
        """从data_json解析TaskCmd对象

        Args:
            data_json: 命令数据JSON

        Returns:
            TaskCmd: 任务命令对象
        """
        task_cmd_dict = data_json.get('task_cmd', {})

        # 提取任务信息
        task_id = task_cmd_dict.get('task_id')
        task_name = task_cmd_dict.get('task_name')
        robot_mode = RobotMode(task_cmd_dict.get('robot_mode'))
        generate_time = datetime.fromisoformat(task_cmd_dict.get('generate_time'))
        station_config_tasks = task_cmd_dict.get('station_config_tasks', [])

        # 解析站点配置列表
        station_config_list = []
        for station_config_dict in station_config_tasks:
            # 解析操作配置
            operation_config_data = station_config_dict.get('operation_config', {})
            operation_config = OperationConfig(
                operation_mode=OperationMode(operation_config_data.get('operation_mode', 'None')),
                door_ip=operation_config_data.get('door_ip'),
                device_id=operation_config_data.get('device_id')
            )

            # 创建站点配置
            station_config = StationConfig(
                station_id=station_config_dict.get('station_id'),
                sort=station_config_dict.get('sort', 0),
                name=station_config_dict.get('name', ''),
                agv_marker=station_config_dict.get('agv_marker', ''),
                robot_pos=station_config_dict.get('robot_pos', []),
                ext_pos=station_config_dict.get('ext_pos', []),
                operation_config=operation_config
            )

            station_config_list.append(station_config)

        # 创建TaskCmd对象
        return TaskCmd(
            task_id=task_id,
            task_name=task_name,
            robot_mode=robot_mode,
            generate_time=generate_time,
            station_config_list=station_config_list
        )

    def get_command_status(self, command_id: str) -> Dict[str, Any]:
        """查询命令执行状态

        Args:
            command_id: 命令ID

        Returns:
            Dict[str, Any]: 命令状态信息
        """
        try:
            command_dict = self.database.get_command_by_id(command_id)
            if not command_dict:
                return {"error": "命令不存在"}

            return command_dict

        except Exception as e:
            self.logger.error(f"查询命令状态失败: {e}")
            return {"error": str(e)}

    def cancel_command(self, command_id: str) -> bool:
        """取消命令执行

        Args:
            command_id: 命令ID

        Returns:
            bool: 是否取消成功
        """
        try:
            # 更新命令状态为已取消
            self.database.update_command_status(command_id, CommandStatus.CANCELLED)
            self.logger.info(f"命令已取消: {command_id}")
            return True

        except Exception as e:
            self.logger.error(f"取消命令失败: {e}")
            return False

    def register_system_callback(self, event: str, callback: callable):
        """注册系统级回调函数

        Args:
            event: 事件名称
            callback: 回调函数
        """
        if event in self.system_callbacks:
            self.system_callbacks[event] = callback
            self.logger.info(f"已注册系统回调: {event}")
        else:
            self.logger.warning(f"未知系统回调事件: {event}")

    def _trigger_system_callback(self, event: str, *args, **kwargs):
        """触发系统级回调

        Args:
            event: 事件名称
            *args: 位置参数
            **kwargs: 关键字参数
        """
        callback = self.system_callbacks.get(event)
        if callback:
            try:
                callback(*args, **kwargs)
            except Exception as e:
                self.logger.error(f"系统回调执行异常: {e}")

    def receive_task_from_cmd(self, task_cmd: TaskCmd) -> str:
        """从TaskCmd接收任务
        
        Args:
            task_cmd: 任务命令对象
            
        Returns:
            str: 生成的任务ID
        """
        try:
            # 解析TaskCmd为Task对象
            task = convert_task_cmd_to_task(task_cmd)
            
            # 添加到调度器
            self.scheduler.add_task(task)
            
            self.logger.info(f"从TaskCmd接收任务成功，任务ID: {task.task_id}")
            return task.task_id
            
        except Exception as e:
            self.logger.error(f"从TaskCmd接收任务失败: {e}")
            raise
    

    def receive_task_from_dict(self, task_dict: Dict[str, Any]) -> str:
        """从字典接收任务
        
        Args:
            task_dict: 任务字典
            
        Returns:
            str: 生成的任务ID
        """
        try:
            # 提取任务信息
            task_id = task_dict.get("task_id", f"TASK_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
            task_name = task_dict.get("task_name", "未知任务")
            robot_mode = RobotMode(task_dict.get("robot_mode", "inspection"))
            generate_time = datetime.fromisoformat(task_dict.get("generate_time")) if task_dict.get("generate_time") else datetime.now()
            
            # 创建站点列表
            station_list = []
            station_configs = task_dict.get("station_config_tasks", [])
            
            for station_config_dict in station_configs:
                # 创建操作配置
                operation_config_dict = station_config_dict.get("operation_config", {})
                operation_config = OperationConfig(
                    operation_mode=OperationMode(operation_config_dict.get("operation_mode", "none")),
                    door_ip=operation_config_dict.get("door_ip"),
                    device_id=operation_config_dict.get("device_id")
                )
                
                # 创建站点配置
                station_config = StationConfig(
                    station_id=station_config_dict.get("station_id"),
                    sort=station_config_dict.get("sort", 0),
                    name=station_config_dict.get("name", "未知站点"),
                    agv_marker=station_config_dict.get("agv_marker", ""),
                    robot_pos=station_config_dict.get("robot_pos", [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]),
                    ext_pos=station_config_dict.get("ext_pos", [0.0, 0.0, 0.0, 0.0]),
                    operation_config=operation_config
                )
                
                # 创建站点任务
                station = Station(
                    station_config=station_config,
                    status=StationTaskStatus.PENDING,
                    created_at=datetime.now(),
                    retry_count=0,
                    max_retries=3,
                    metadata={
                        "source": "dict",
                        "task_id": task_id
                    }
                )
                
                station_list.append(station)
            
            # 创建任务对象
            task = Task(
                task_id=task_id,
                task_name=task_name,
                station_list=station_list,
                status=TaskStatus.PENDING,
                robot_mode=robot_mode,
                generate_time=generate_time,
                created_at=datetime.now(),
                metadata={
                    "source": "dict",
                    "generate_time": generate_time.isoformat()
                }
            )
            
            # 添加到调度器
            self.scheduler.add_task(task)
            
            self.logger.info(f"从字典接收任务成功，任务ID: {task.task_id}")
            return task.task_id
            
        except Exception as e:
            self.logger.error(f"从字典接收任务失败: {e}")
            raise
    
    def get_task_status(self, task_id: str) -> Dict[str, Any]:
        """获取任务状态
        
        Args:
            task_id: 任务ID
            
        Returns:
            Dict[str, Any]: 任务状态信息
        """
        try:
            task = self.database.get_task_by_id(task_id)
            if not task:
                return {"error": "任务不存在"}
            
            # 获取站点任务
            station_tasks = self.database.get_station_tasks(task_id)
            
            # 构建返回结果
            result = {
                "task": task,
                "station_tasks": station_tasks
            }
            
            return result
        except Exception as e:
            self.logger.error(f"获取任务状态失败: {e}")
            return {"error": str(e)}
    
    def get_all_tasks(self, status: str = None) -> List[Dict[str, Any]]:
        """获取所有任务
        
        Args:
            status: 任务状态过滤
            
        Returns:
            List[Dict[str, Any]]: 任务列表
        """
        try:
            # 从数据库获取任务
            # TODO: 实现按状态过滤
            tasks = self.database.get_pending_tasks()
            return tasks
        except Exception as e:
            self.logger.error(f"获取任务列表失败: {e}")
            return []
    
    def cancel_task(self, task_id: str) -> bool:
        """取消任务
        
        Args:
            task_id: 任务ID
            
        Returns:
            bool: 是否取消成功
        """
        # TODO: 实现任务取消逻辑
        self.logger.info(f"取消任务: {task_id}")
        return True
    
    def retry_task(self, task_id: str) -> bool:
        """重试任务
        
        Args:
            task_id: 任务ID
            
        Returns:
            bool: 是否重试成功
        """
        # TODO: 实现任务重试逻辑
        self.logger.info(f"重试任务: {task_id}")
        return True
    
    # ==================== 回调函数 ====================
    def _on_task_start(self, task: Task):
        """任务开始回调"""
        self.logger.info(f"任务开始: {task.task_id}")
        # 可以在这里发送通知或更新UI

    def _on_task_complete(self, task: Task):
        """任务完成回调"""
        self.logger.info(f"任务完成: {task.task_id}")

        # 触发系统回调：通知RobotControlSystem上报设备数据
        self._trigger_system_callback(
            "on_data_ready",
            data_type="task_complete",
            task=task
        )

    def _on_task_failed(self, task: Task):
        """任务失败回调"""
        self.logger.error(f"任务失败: {task.task_id}")
        # 可以在这里发送通知或更新UI

    def _on_station_start(self, station: Station):
        """站点开始回调"""
        self.logger.info(f"站点开始: {station.station_config.station_id}")

        # 触发系统回调：通知RobotControlSystem到达站点
        self._trigger_system_callback(
            "on_arrive_station",
            station=station
        )

    def _on_station_complete(self, station: Station):
        """站点完成回调"""
        self.logger.info(f"站点完成: {station.station_config.station_id}")

        # 触发系统回调：通知RobotControlSystem上报设备数据
        self._trigger_system_callback(
            "on_data_ready",
            data_type="device_data",
            station=station
        )

    def _on_station_retry(self, station: Station):
        """站点重试回调"""
        self.logger.warning(f"站点重试: {station.station_config.station_id}, 重试次数: {station.retry_count}")
        # 可以在这里发送通知或更新UI
    
    def shutdown(self):
        """关闭管理器"""
        self.scheduler.stop()

        # 关闭机器人系统
        try:
            if hasattr(self, 'robot_controller'):
                self.robot_controller.shutdown_system()
                self.logger.info("机器人控制器已关闭")
        except Exception as e:
            self.logger.error(f"关闭机器人控制器失败: {e}")

        self.logger.info("任务管理器已关闭")
