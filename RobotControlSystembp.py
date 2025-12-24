from gRPC import RobotService_pb2_grpc
from gRPC import PersistentStreamManager
"""
RobotControlSystem.py
机器人控制系统主类，负责接收、解析后台指令，协调任务管理和机器人执行
"""

import json
import threading
import time
import uuid
from datetime import datetime
from typing import Dict, Any, Optional, Callable, Generator
import logging
import grpc
from grpc import channel_ready_future

import gRPC.RobotService_pb2 as robot_pb2
from dataModels.CommandModels import CommandEnvelope, CmdType, create_cmd_envelope, TaskCmd
from dataModels.MessageModels import (
    MessageEnvelope, MsgType, create_message_envelope,
    BatteryInfo, PositionInfo, TaskInfo, SystemStatus, ErrorInfo,
    DeviceInfo, EnvironmentInfo, ArriveServicePointInfo
)
from dataModels.TaskModels import Task, TaskStatus, StationConfig, OperationConfig, OperationMode
from task.TaskManager import TaskManager
from robot.MockRobotController import MockRobotController


class ClientConfig:
    """客户端配置"""
    SERVER_HOST = "localhost"
    SERVER_PORT = 50051
    SERVER_ADDRESS = f"{SERVER_HOST}:{SERVER_PORT}"
    LOG_LEVEL = "INFO"
    # 连接超时设置
    CONNECTION_TIMEOUT = 10
    # 流保持活跃检查间隔
    STREAM_KEEPALIVE_CHECK = 30


# 只在不使用mock时导入真实控制器
RobotController = None
if False:  # 这个值会在运行时根据use_mock参数决定
    from robot.RobotController import RobotController
DBG = True

class RobotControlSystem:
    """机器人控制系统主类"""
    
    def __init__(self, config: Dict[str, Any] = None, use_mock: bool = True):
        """
        初始化机器人控制系统
        
        Args:
            config: 系统配置字典
            use_mock: 是否使用Mock机器人控制器
        """
        self.config = config or {}
        self.use_mock = use_mock
        
        # 系统状态
        self.robot_id = self.config.get('robot_id', 'ROBOT_001')
        self.current_mode = 'inspection'  # 初始为巡检模式
        self.is_running = False
        
        # 初始化日志
        self.logger = logging.getLogger(__name__)
        self.logger.setLevel(logging.INFO if not DBG else logging.DEBUG)
        self.logger.propagate = False
        if not self.logger.handlers:
            handler = logging.StreamHandler()
            formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
            handler.setFormatter(formatter)
            self.logger.addHandler(handler)
        
        # 初始化机器人控制器
        self._init_robot_controller()
        
        # 初始化任务管理器
        self.task_manager = TaskManager(self.robot_controller)
        
        # 注册任务回调
        self.task_manager.scheduler.register_callback("on_task_complete", self._on_task_complete)
        self.task_manager.scheduler.register_callback("on_task_failed", self._on_task_failed)
        
        # gRPC相关配置
        self.gRPC_config = ClientConfig()
        # 连接状态
        self.channel = None
        self.stub = None
        self.is_connected = False
        # 流管理
        self.stream_manager = None
        self.robot_id = None
        # 统计
        self.sent_count = 0
        self.received_count = 0
    
        # 通信相关
        self._communication_thread = None
        self._stop_communication = False
        
        # 定时上报线程
        self._report_thread = None
        self._stop_reporting = False
        
        # 回调函数
        self.callbacks = {
            "on_command_received": [],
            "on_status_reported": [],
            "on_task_executed": [],
            "on_communication_error": []
        }
        
        self.logger.info("机器人控制系统初始化完成")
    
    def _init_robot_controller(self):
        """初始化机器人控制器"""
        try:
            if self.use_mock:
                self.logger.info("使用Mock机器人控制器")
                self.robot_controller = MockRobotController(self.config.get('robot_config', {}))
            else:
                self.logger.info("使用真实机器人控制器")
                # 动态导入真实机器人控制器，避免启动时的导入错误
                from robot.RobotController import RobotController as RealRobotController
                self.robot_controller = RealRobotController(self.config.get('robot_config', {}))
            
            # 初始化机器人系统
            if not self.robot_controller.setup_system():
                raise Exception("机器人系统初始化失败")
            
            self.logger.info("机器人控制器初始化成功")
            
        except Exception as e:
            self.logger.error(f"初始化机器人控制器失败: {e}")
            raise
    
    def _init_grpc_client(self, robot_id: int = 1):
        """初始化gRPC客户端"""
        try:
            # 创建gRPC通道
            self.channel = grpc.insecure_channel(
                self.gRPC_config.SERVER_ADDRESS,
                options=[
                    ('grpc.keepalive_time_ms', 10000),
                    ('grpc.keepalive_timeout_ms', 5000),
                ]
            )

            # 创建存根
            self.stub = RobotService_pb2_grpc.RobotServiceStub(self.channel)

            # 测试连接
            try:
               channel_ready_future(self.channel).result(
                    timeout=10
                    # timeout=self.gRPC_config.CONNECTION_TIMEOUT
                )
            except Exception as e:
                self.logger.warning(f"连接测试警告: {e}")

            # 初始化流管理器
            self.stream_manager = PersistentStreamManager(self.stub, robot_id)

            # 启动持久化流
            if self.stream_manager.start_stream():
                self.is_connected = True
                self.logger.info(f"成功建立持久化连接，robot_id: {robot_id}")
                return True
            else:
                return False

        except Exception as e:
            self.logger.error(f"连接失败: {e}")
            return False
    
    def set_response_handler(self, handler):
        if self.stream_manager:
            self.stream_manager.response_handler = handler
            self.logger.info("自定义响应处理器已设置")

    def is_stream_healthy(self) -> bool:
        """检查流健康状态"""
        return (self.is_connected and
                self.stream_manager and
                self.stream_manager.is_stream_active)

    def start(self):
        """启动机器人控制系统"""
        if self.is_running:
            self.logger.warning("机器人控制系统已在运行")
            return
        
        self.logger.info("启动机器人控制系统...")
        
        
        # 启动通信接收
        if not self._init_grpc_client(self.robot_id):
            self.logger.error("gRPC客户端初始化失败")
            return
        
        self.stream_manager.set_response_handler(self._handle_grpc_response)
        
        # 启动定时上报
        self._start_reporting()
        
        self.logger.info("机器人控制系统已启动")
        self.is_running = True
    def stop(self):
        """停止机器人控制系统"""
        if not self.is_running:
            self.logger.warning("机器人控制系统未在运行")
            return
        
        self.logger.info("停止机器人控制系统...")
        self.is_running = False
        
        '''停止通信接收'''
        self.logger.info("正在关闭客户端...")

        if self.stream_manager:
            self.stream_manager.stop_stream()

        if self.channel:
            try:
                self.channel.close()
                self.logger.info("gRPC通道已关闭")
            except Exception as e:
                self.logger.error(f"关闭通道时出错: {e}")

        self.is_connected = False
        self.logger.info(f"客户端已关闭 - 发送: {self.sent_count} 条消息, 接收: {self.received_count} 条响应")

        '''停止任务管理器'''
        self.task_manager.shutdown()
        
        '''关闭机器人系统'''
        self.robot_controller.shutdown_system()
        
        self.logger.info("机器人控制系统已停止")
    
    def _handle_grpc_response(self, response):
        """处理从gRPC服务器收到的响应/命令"""
        try:
            # TODO: 这里需要根据实际的响应格式来解析命令
            # 目前只是记录日志
            self.logger.info(f"处理gRPC响应: {response}")
            
        except Exception as e:
            self.logger.error(f"处理gRPC响应失败: {e}")
    
    def _simulate_receive_command(self):
        """模拟接收后台指令（仅用于测试）"""
        # 从CommandModels中导入RobotMode枚举
        from dataModels.CommandModels import RobotMode
        from dataModels.TaskModels import OperationMode, OperationConfig
        
        # 创建操作配置
        operation_config1 = OperationConfig(
            operation_mode=OperationMode.CAPTURE,
            door_ip=None,
            device_id="device_1"
        )
        
        operation_config2 = OperationConfig(
            operation_mode=OperationMode.CAPTURE,
            door_ip=None,
            device_id="device_2"
        )
        
        # 创建一个模拟的TaskCmd
        task_cmd = TaskCmd(
            taskId=f"TASK_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')[:-3]}",
            taskName="测试巡检任务",
            robotMode=RobotMode.INSPECTION,
            stationTasks=[
                StationConfig(
                    station_id="station_1",
                    sort=1,
                    name="站点1",
                    agv_marker="marker_1",
                    robot_pos=[0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
                    ext_pos=[0.0, 0.0, 0.0, 0.0],
                    operation_config=operation_config1
                ),
                StationConfig(
                    station_id="station_2",
                    sort=2,
                    name="站点2",
                    agv_marker="marker_2",
                    robot_pos=[10.0, 10.0, 0.0, 0.0, 0.0, 0.0],
                    ext_pos=[1.0, 0.0, 0.0, 0.0],
                    operation_config=operation_config2
                )
            ]
        )
        
        # 创建命令信封
        cmd_envelope = create_cmd_envelope(
            cmd_id=str(uuid.uuid4()),
            robot_id=self.robot_id,
            cmd_type=CmdType.TASK_CMD,
            cmd_data=task_cmd
        )
        
        # 处理命令
        self.handle_command(cmd_envelope.to_dict())
    
    def handle_command(self, command_dict: Dict[str, Any]):
        """处理接收到的命令
        
        Args:
            command_dict: 命令字典
        """
        self.logger.debug(f"收到命令: {json.dumps(command_dict, ensure_ascii=False)}")
        
        try:
            # 保存接收到的消息到数据库
            msg_id = command_dict.get('cmdId')
            msg_time = command_dict.get('cmdTime')
            cmd_type = command_dict.get('cmdType')
            robot_id = command_dict.get('robotId')
            data_json = json.dumps(command_dict.get('dataJson', {}))
            
            # 保存到数据库
            self.task_manager.database.save_received_message(
                msg_id=msg_id,
                msg_time=msg_time,
                cmd_type=cmd_type,
                robot_id=robot_id,
                data_json=data_json
            )
            
            # 触发命令接收回调
            self._trigger_callback("on_command_received", command_dict)
            
            # 根据命令类型处理
            if cmd_type == CmdType.TASK_CMD.value:
                self._handle_task_command(command_dict.get('dataJson', {}))
            elif cmd_type == CmdType.ROBOT_MODE_CMD.value:
                self._handle_mode_command(command_dict.get('dataJson', {}))
            elif cmd_type == CmdType.JOY_CONTROL_CMD.value:
                self._handle_joy_command(command_dict.get('dataJson', {}))
            elif cmd_type == CmdType.CHARGE_CMD.value:
                self._handle_charge_command(command_dict.get('dataJson', {}))
            else:
                self.logger.warning(f"未知命令类型: {cmd_type}")
            
            # 标记消息为已处理
            self.task_manager.database.mark_message_processed(msg_id)
                
        except Exception as e:
            self.logger.error(f"处理命令失败: {e}")
            # 可以在这里发送错误响应
    
    def _handle_task_command(self, data_json: Dict[str, Any]):
        """处理任务命令
        
        Args:
            data_json: 任务命令数据
        """
        self.logger.debug(f"处理任务命令: {json.dumps(data_json, ensure_ascii=False)}")
        
        try:
            # 从TaskCmd转换为内部任务格式
            task_cmd = data_json.get('taskCmd', {})
            
            # 提取任务信息
            task_id = task_cmd.get('taskId')
            robot_mode = task_cmd.get('robotMode')
            station_tasks = task_cmd.get('stationTasks', [])
            
            # 更新机器人工作模式
            self.current_mode = robot_mode
            
            # 处理每个站点任务
            for station_data in station_tasks:
                # 提取操作配置数据
                operation_config_data = station_data.get('operation_config', {})
                
                # 创建操作配置对象
                operation_config = OperationConfig(
                    operation_mode=OperationMode(operation_config_data.get('operation_mode', 'None')),
                    door_ip=operation_config_data.get('door_ip'),
                    device_id=operation_config_data.get('device_id')
                )
                
                # 创建站点配置
                station = StationConfig(
                    station_id=station_data.get('station_id'),
                    sort=station_data.get('sort', 0),
                    name=station_data.get('name', ''),
                    agv_marker=station_data.get('agv_marker', ''),
                    robot_pos=station_data.get('robot_pos', []),
                    ext_pos=station_data.get('ext_pos', []),
                    operation_config=operation_config
                )
                # 创建内部Task对象
                task = Task(
                    task_id=f"{task_id}_{station.station_id}_{station.sort}",
                    station=station,
                    priority=1,
                    metadata={
                        # 'source_task_id': task_id,
                        # 'robot_mode': robot_mode
                    }
                )
                
                # 添加到任务管理器
                self.task_manager.scheduler.add_task(task)
            
            self.logger.info(f"成功添加{len(station_tasks)}个站点任务")
            
        except Exception as e:
            self.logger.error(f"处理任务命令失败: {e}")
            raise
    
    def _handle_mode_command(self, data_json: Dict[str, Any]):
        """处理模式命令
        
        Args:
            data_json: 模式命令数据
        """
        self.logger.info(f"处理模式命令: {json.dumps(data_json, ensure_ascii=False)}")
        
        try:
            mode_cmd = data_json.get('robotModeCmd', {})
            new_mode = mode_cmd.get('robotMode')
            
            self.current_mode = new_mode
            self.logger.info(f"机器人工作模式已更新为: {new_mode}")
            
        except Exception as e:
            self.logger.error(f"处理模式命令失败: {e}")
    
    def _handle_joy_command(self, data_json: Dict[str, Any]):
        """处理摇杆控制命令
        
        Args:
            data_json: 摇杆控制命令数据
        """
        self.logger.info(f"处理摇杆控制命令: {json.dumps(data_json, ensure_ascii=False)}")
        # TODO: 实现摇杆控制逻辑
    
    def _handle_charge_command(self, data_json: Dict[str, Any]):
        """处理充电命令
        
        Args:
            data_json: 充电命令数据
        """
        self.logger.info(f"处理充电命令: {json.dumps(data_json, ensure_ascii=False)}")
        
        try:
            charge_cmd = data_json.get('chargeCmd', {})
            charge = charge_cmd.get('charge', False)
            
            if charge:
                self.logger.info("开始充电")
                self.robot_controller.charge()
            else:
                self.logger.info("停止充电")
                # TODO: 实现停止充电逻辑
                
        except Exception as e:
            self.logger.error(f"处理充电命令失败: {e}")
    
    def _start_reporting(self):
        """启动定时上报"""
        self._stop_reporting = False
        self._report_thread = threading.Thread(
            target=self._reporting_loop,
            daemon=True
        )
        self._report_thread.start()
        self.logger.info("定时上报线程已启动")
    
    def _reporting_loop(self):
        """定时上报循环"""
        self.logger.info("定时上报循环已启动")
        
        # 上报间隔（秒）
        status_interval = 10  # 机器人状态上报间隔
        env_interval = 30     # 环境数据上报间隔
        
        last_status_report = time.time()
        last_env_report = time.time()
        
        while not self._stop_reporting:
            try:
                current_time = time.time()
                
                # 上报机器人状态
                if current_time - last_status_report >= status_interval:
                    self._report_robot_status()
                    last_status_report = current_time
                
                # 上报环境数据
                if current_time - last_env_report >= env_interval:
                    self._report_environment_data()
                    last_env_report = current_time
                
                time.sleep(1)
                
            except Exception as e:
                self.logger.error(f"定时上报异常: {e}")
                time.sleep(1)
    
    def _report_robot_status(self):
        """上报机器人状态"""
        try:
            # 获取机器人状态
            robot_status = self.robot_controller.get_status()
            
            # 构建电池信息
            battery_info = BatteryInfo(
                power_percent=robot_status.get('battery_level', 100.0),
                charge_status='charging' if robot_status.get('status') == 'charging' else 'discharging'
            )
            
            # 构建位置信息
            position_info = PositionInfo(
                AGVPositionInfo=[
                    robot_status.get('current_position', {}).get('x', 0.0),
                    robot_status.get('current_position', {}).get('y', 0.0),
                    robot_status.get('current_position', {}).get('theta', 0.0)
                ],
                ARMPositionInfo=robot_status.get('robot_joints', [0.0]*6),
                EXTPositionInfo=robot_status.get('ext_axis', [0.0]*4),
                targetPoint=robot_status.get('current_marker', '')
            )
            
            # 从MessageModels中导入MoveStatus枚举
            from dataModels.MessageModels import MoveStatus
            
            # 构建系统状态
            move_status_str = robot_status.get('status', 'idle')
            try:
                move_status = MoveStatus(move_status_str)
            except ValueError:
                # 默认为idle
                move_status = MoveStatus.IDLE
            
            system_status = SystemStatus(
                move_status=move_status,
                is_connected=True,
                soft_estop_status=False,
                hard_estop_status=False,
                estop_status=False
            )
            
            # 构建任务信息（当前执行的任务）
            current_task = self.task_manager.scheduler.current_task
            task_info = TaskInfo(
                inspection_task_list=[current_task] if current_task else []
            )
            
            # 创建消息信封
            msg_envelope = create_message_envelope(
                msg_id=str(uuid.uuid4()),
                robot_id=self.robot_id,
                msg_type=MsgType.ROBOT_STATUS,
                battery_info=battery_info,
                position_info=position_info,
                task_info=task_info,
                system_status=system_status
            )
            
            # 发送状态消息
            self._send_message(msg_envelope)
            
            # 触发状态上报回调
            self._trigger_callback("on_status_reported", msg_envelope.to_dict())
            
        except Exception as e:
            self.logger.error(f"上报机器人状态失败: {e}")
    
    def _report_environment_data(self):
        """上报环境数据"""
        try:
            # 模拟环境数据
            env_info = EnvironmentInfo(
                temperature=22.5,
                humidity=45.0,
                oxygen=20.9,
                carbonDioxide=400.0,
                pm25=12.5,
                pm10=25.0,
                etvoc=0.2,
                noise=45.0
            )
            
            # 获取机器人状态
            robot_status = self.robot_controller.get_status()
            
            # 构建位置信息
            position_info = PositionInfo(
                AGVPositionInfo=[
                    robot_status.get('current_position', {}).get('x', 0.0),
                    robot_status.get('current_position', {}).get('y', 0.0),
                    robot_status.get('current_position', {}).get('theta', 0.0)
                ],
                ARMPositionInfo=robot_status.get('robot_joints', [0.0]*6),
                EXTPositionInfo=robot_status.get('ext_axis', [0.0]*4),
                targetPoint=robot_status.get('current_marker', '')
            )
            
            # 构建任务信息
            current_task = self.task_manager.scheduler.current_task
            task_info = TaskInfo(
                inspection_task_list=[current_task] if current_task else []
            )
            
            # 创建消息信封
            msg_envelope = create_message_envelope(
                msg_id=str(uuid.uuid4()),
                robot_id=self.robot_id,
                msg_type=MsgType.ENVIRONMENT_DATA,
                position_info=position_info,
                task_info=task_info,
                environment_info=env_info
            )
            
            # 发送环境数据消息
            self._send_message(msg_envelope)
            
        except Exception as e:
            self.logger.error(f"上报环境数据失败: {e}")
    
    def _report_device_data(self, task: Task):
        """任务完成后上报设备数据
        
        Args:
            task: 完成的任务
        """
        try:
            # 模拟设备数据
            device_info = DeviceInfo(
                deviceId=task.station.operation_config.get('device_id', ''),
                dataType='image',
                imageBase64='mock_base64_image_data'
            )
            
            # 获取机器人状态
            robot_status = self.robot_controller.get_status()
            
            # 构建位置信息
            position_info = PositionInfo(
                AGVPositionInfo=[
                    robot_status.get('current_position', {}).get('x', 0.0),
                    robot_status.get('current_position', {}).get('y', 0.0),
                    robot_status.get('current_position', {}).get('theta', 0.0)
                ],
                ARMPositionInfo=robot_status.get('robot_joints', [0.0]*6),
                EXTPositionInfo=robot_status.get('ext_axis', [0.0]*4),
                targetPoint=task.station.agv_marker
            )
            
            # 构建任务信息
            task_info = TaskInfo(
                inspection_task_list=[task]
            )
            
            # 创建消息信封
            msg_envelope = create_message_envelope(
                msg_id=str(uuid.uuid4()),
                robot_id=self.robot_id,
                msg_type=MsgType.DEVICE_DATA,
                position_info=position_info,
                task_info=task_info,
                device_info=device_info
            )
            
            # 发送设备数据消息
            self._send_message(msg_envelope)
            
        except Exception as e:
            self.logger.error(f"上报设备数据失败: {e}")
    
    def _report_arrive_service_point(self, task: Task):
        """到达服务点后上报
        
        Args:
            task: 当前任务
        """
        try:
            # 构建到达服务点信息
            arrive_info = ArriveServicePointInfo(
                isArrive=True
            )
            
            # 获取机器人状态
            robot_status = self.robot_controller.get_status()
            
            # 构建位置信息
            position_info = PositionInfo(
                AGVPositionInfo=[
                    robot_status.get('current_position', {}).get('x', 0.0),
                    robot_status.get('current_position', {}).get('y', 0.0),
                    robot_status.get('current_position', {}).get('theta', 0.0)
                ],
                ARMPositionInfo=robot_status.get('robot_joints', [0.0]*6),
                EXTPositionInfo=robot_status.get('ext_axis', [0.0]*4),
                targetPoint=task.station.agv_marker
            )
            
            # 构建任务信息
            task_info = TaskInfo(
                inspection_task_list=[task]
            )
            
            # 创建消息信封
            msg_envelope = create_message_envelope(
                msg_id=str(uuid.uuid4()),
                robot_id=self.robot_id,
                msg_type=MsgType.ARRIVE_SERVER_POINT,
                position_info=position_info,
                task_info=task_info,
                arrive_service_point_info=arrive_info
            )
            
            # 发送到达服务点消息
            self._send_message(msg_envelope)
            
        except Exception as e:
            self.logger.error(f"上报到达服务点信息失败: {e}")
    
    def _send_message(self, msg_envelope: MessageEnvelope):
        """发送消息到后台 - 使用gRPC双向流
        
        Args:
            msg_envelope: 消息信封
        """
        # 实际项目中使用gRPC发送消息

        # 将MessageEnvelope转换为gRPC RobotUploadRequest
        grpc_msg = self._convert_to_grpc_message(msg_envelope)
        self.stream_manager.send_message(grpc_msg)
        
        # 保存发送的消息到数据库
        try:
            msg_dict = msg_envelope.to_dict()
            self.task_manager.database.save_sent_message(
                msg_id=msg_dict.get('msgId'),
                msg_time=msg_dict.get('msgTime'),
                msg_type=msg_dict.get('msgType'),
                robot_id=msg_dict.get('robotId'),
                data_json=json.dumps(msg_dict.get('dataJson', {})),
                status='sent'
            )
        except Exception as e:
            self.logger.error(f"保存发送的消息到数据库失败: {e}")
    
    def _convert_to_grpc_message(self, msg_envelope: MessageEnvelope) -> robot_pb2.RobotUploadRequest:
        """将MessageEnvelope转换为gRPC RobotUploadRequest
        
        Args:
            msg_envelope: 消息信封
            
        Returns:
            robot_pb2.RobotUploadRequest: gRPC请求消息
        """
        # 转换MsgType枚举
        msg_type_map = {
            MsgType.ROBOT_STATUS: robot_pb2.MsgType.ROBOT_STATUS,
            MsgType.DEVICE_DATA: robot_pb2.MsgType.DEVICE_DATA,
            MsgType.ENVIRONMENT_DATA: robot_pb2.MsgType.ENVIRONMENT_DATA,
            MsgType.ARRIVE_SERVER_POINT: robot_pb2.MsgType.ARRIVE_SERVER_POINT
        }
        
        grpc_msg_type = msg_type_map.get(msg_envelope.msgType, robot_pb2.MsgType.ROBOT_STATUS)
        robot_id = int(self.robot_id.split('_')[-1])
        
        # 创建基础请求
        # 将msgId转换为整数ID，如果是UUID格式则取前8位转为16进制整数
        msg_id_str = msg_envelope.msgId
        if '_' in msg_id_str:
            msg_id_str = msg_id_str.split('_')[-1]
        
        try:
            # 尝试直接转换为整数
            msg_id = int(msg_id_str)
        except ValueError:
            # 如果是UUID或其他格式，取前8位转为16进制整数
            msg_id = int(msg_id_str.replace('-', '')[:8], 16)
            
        grpc_msg = robot_pb2.RobotUploadRequest(
            msg_id=msg_id,
            msg_time=msg_envelope.msgTime,
            msg_type=grpc_msg_type,
            robot_id=robot_id
        )
        
        data_json = msg_envelope.dataJson
        
        # 根据消息类型填充具体数据
        if msg_envelope.msgType == MsgType.ROBOT_STATUS:
            # 转换机器人状态
            battery_info = data_json.get('batteryInfo', {})
            position_info = data_json.get('positionInfo', {})
            system_status = data_json.get('systemStatus', {})
            
            # 转换MoveStatus枚举
            move_status_map = {
                'idle': robot_pb2.MoveStatus.IDLE,
                'running': robot_pb2.MoveStatus.RUNNING,
                'succeeded': robot_pb2.MoveStatus.SUCCEEDED,
                'failed': robot_pb2.MoveStatus.FAILED,
                'canceled': robot_pb2.MoveStatus.CANCELED
            }
            
            move_status = move_status_map.get(system_status.get('move_status', 'idle'), robot_pb2.MoveStatus.IDLE)
            
            # 设置机器人状态数据
            grpc_msg.robot_status.CopyFrom(robot_pb2.RobotStatusUpload(
                battery_info=robot_pb2.BatteryInfo(
                    power_percent=battery_info.get('power_percent', 0.0),
                    charge_status=battery_info.get('charge_status', 'discharging')
                ),
                position_info=robot_pb2.PositionInfo(
                    AGVPositionInfo=position_info.get('AGVPositionInfo', [0.0, 0.0, 0.0]),
                    ARMPositionInfo=position_info.get('ARMPositionInfo', [0.0]*6),
                    EXTPositionInfo=position_info.get('EXTPositionInfo', [0.0]*4),
                    targetPoint=position_info.get('targetPoint', ''),
                    pointId=int(position_info.get('pointId', 0)) if position_info.get('pointId') else 0
                ),
                system_status=robot_pb2.SystemStatus(
                    move_status=move_status,
                    is_connected=system_status.get('is_connected', True),
                    soft_estop_status=system_status.get('soft_estop_status', False),
                    hard_estop_status=system_status.get('hard_estop_status', False),
                    estop_status=system_status.get('estop_status', False)
                )
            ))
        
        elif msg_envelope.msgType == MsgType.ENVIRONMENT_DATA:
            # 转换环境数据
            env_info = data_json.get('environmentInfo', {})
            position_info = data_json.get('positionInfo', {})
            
            grpc_msg.environment_data.CopyFrom(robot_pb2.EnvironmentDataUpload(
                position_info=robot_pb2.PositionInfo(
                    AGVPositionInfo=position_info.get('AGVPositionInfo', [0.0, 0.0, 0.0]),
                    ARMPositionInfo=position_info.get('ARMPositionInfo', [0.0]*6),
                    EXTPositionInfo=position_info.get('EXTPositionInfo', [0.0]*4),
                    targetPoint=position_info.get('targetPoint', ''),
                    pointId=int(position_info.get('pointId', 0)) if position_info.get('pointId') else 0
                ),
                sensor_data=robot_pb2.SensorData(
                    temperature=env_info.get('temperature', 0.0),
                    humidity=env_info.get('humidity', 0.0),
                    oxygen=env_info.get('oxygen', 0.0),
                    carbon_dioxide=env_info.get('carbonDioxide', 0.0),
                    pm25=env_info.get('pm25', 0.0),
                    pm10=env_info.get('pm10', 0.0),
                    etvoc=env_info.get('etvoc', 0.0),
                    noise=env_info.get('noise', 0.0)
                )
            ))
        
        elif msg_envelope.msgType == MsgType.DEVICE_DATA:
            # 转换设备数据
            device_info = data_json.get('deviceInfo', {})
            position_info = data_json.get('positionInfo', {})
            
            grpc_msg.device_data.CopyFrom(robot_pb2.DeviceDataUpload(
                position_info=robot_pb2.PositionInfo(
                    AGVPositionInfo=position_info.get('AGVPositionInfo', [0.0, 0.0, 0.0]),
                    ARMPositionInfo=position_info.get('ARMPositionInfo', [0.0]*6),
                    EXTPositionInfo=position_info.get('EXTPositionInfo', [0.0]*4),
                    targetPoint=position_info.get('targetPoint', ''),
                    pointId=int(position_info.get('pointId', 0)) if position_info.get('pointId') else 0
                ),
                device_info=robot_pb2.DeviceInfo(
                    device_id=device_info.get('deviceId', ''),
                    data_type=device_info.get('dataType', ''),
                    image_base64=device_info.get('imageBase64', '')
                )
            ))
        
        elif msg_envelope.msgType == MsgType.ARRIVE_SERVER_POINT:
            # 转换到达服务点数据
            grpc_msg.arrive_service_point.CopyFrom(robot_pb2.ArriveServicePointUpload())
        
        return grpc_msg
    
    def _on_task_complete(self, task: Task):
        """任务完成回调
        
        Args:
            task: 完成的任务
        """
        self.logger.info(f"任务完成回调: {task.task_id}")
        
        # 根据工作模式处理
        if self.current_mode == 'inspection':
            # 巡检模式：上报设备数据
            self._report_device_data(task)
        elif self.current_mode == 'service':
            # 服务模式：上报到达服务点信息
            self._report_arrive_service_point(task)
        
        # 触发任务执行回调
        self._trigger_callback("on_task_executed", task, "completed")
    
    def _on_task_failed(self, task: Task):
        """任务失败回调
        
        Args:
            task: 失败的任务
        """
        self.logger.error(f"任务失败回调: {task.task_id}")
        # 触发任务执行回调
        self._trigger_callback("on_task_executed", task, "failed")
    
    def register_callback(self, event: str, callback: Callable):
        """注册回调函数
        
        Args:
            event: 事件名称
            callback: 回调函数
        """
        if event in self.callbacks:
            self.callbacks[event].append(callback)
            self.logger.debug(f"已注册回调函数到事件: {event}")
        else:
            self.logger.warning(f"未知事件类型: {event}")
    
    def _trigger_callback(self, event: str, *args, **kwargs):
        """触发回调函数
        
        Args:
            event: 事件名称
            *args: 位置参数
            **kwargs: 关键字参数
        """
        for callback in self.callbacks.get(event, []):
            try:
                callback(*args, **kwargs)
            except Exception as e:
                self.logger.error(f"回调函数执行异常: {e}")
    
    def shutdown(self):
        """关闭机器人控制系统"""
        self.stop()
        self.logger.info("机器人控制系统已关闭")

# 测试代码
if __name__ == "__main__":
    # 配置日志
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    
    # 创建机器人控制系统
    config = {
        'robot_id': 'ROBOT_001',
        'robot_config': {
            'success_rate': 0.95,
            'latency': 10,
            'max_error_rate': 0.001
        }
    }
    
    robot_system = RobotControlSystem(config, use_mock=True)
    
    try:
        # 启动系统
        robot_system.start()
        
        # 运行一段时间
        time.sleep(60)
        
    except KeyboardInterrupt:
        print("\n收到中断信号，关闭系统...")
    finally:
        # 关闭系统
        robot_system.shutdown()
