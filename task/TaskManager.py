from datetime import datetime
import json
import uuid
import logging
from typing import Dict, List, Any
from utils.dataConverter import convert_task_cmd_to_task
from task.TaskDatabase import TaskDatabase
from task.TaskScheduler import TaskScheduler
from dataModels.CommandModels import TaskCmd, CmdType
from dataModels.TaskModels import Task, Station, StationConfig, OperationConfig, OperationMode, RobotMode, TaskStatus, StationTaskStatus

class TaskManager:
    """任务管理器 - 主控制器，负责任务的接收、解析和调度"""
    
    def __init__(self, robot_controller):
        self.robot_controller = robot_controller
        self.database = TaskDatabase()
        self.scheduler = TaskScheduler(robot_controller, self.database)
        self.logger = logging.getLogger(__name__)
        
        # 启动调度器
        self.scheduler.start()
        
        # 注册回调
        self.scheduler.register_callback("on_task_start", self._on_task_start)
        self.scheduler.register_callback("on_task_complete", self._on_task_complete)
        self.scheduler.register_callback("on_task_failed", self._on_task_failed)
        self.scheduler.register_callback("on_station_start", self._on_station_start)
        self.scheduler.register_callback("on_station_complete", self._on_station_complete)
        self.scheduler.register_callback("on_station_failed", self._on_station_failed)
        self.scheduler.register_callback("on_station_retry", self._on_station_retry)
    
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
        # 可以在这里发送通知或更新UI
    
    def _on_task_failed(self, task: Task):
        """任务失败回调"""
        self.logger.error(f"任务失败: {task.task_id}")
        # 可以在这里发送通知或更新UI
    
    def _on_station_start(self, station: Station):
        """站点开始回调"""
        self.logger.info(f"站点开始: {station.station_config.station_id}")
        # 可以在这里发送通知或更新UI
    
    def _on_station_complete(self, station: Station):
        """站点完成回调"""
        self.logger.info(f"站点完成: {station.station_config.station_id}")
        # 可以在这里发送通知或更新UI
    
    def _on_station_failed(self, station: Station):
        """站点失败回调"""
        self.logger.error(f"站点失败: {station.station_config.station_id}")
        # 可以在这里发送通知或更新UI
    
    def _on_station_retry(self, station: Station):
        """站点重试回调"""
        self.logger.warning(f"站点重试: {station.station_config.station_id}, 重试次数: {station.retry_count}")
        # 可以在这里发送通知或更新UI
    
    def shutdown(self):
        """关闭管理器"""
        self.scheduler.stop()
        self.logger.info("任务管理器已关闭")
