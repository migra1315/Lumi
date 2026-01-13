from dataModels.TaskModels import TaskStatus
from dataModels.TaskModels import Task
from dataModels.TaskModels import RobotMode, StationTaskStatus
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
import gRPC.RobotService_pb2 as robot_pb2
from gRPC import RobotService_pb2_grpc
from gRPC.StreamManager import ClientUploadStreamManager, ServerCommandStreamManager
from utils.dataConverter import convert_server_message_to_command_envelope, convert_message_envelope_to_robot_upload_request

from task.TaskManager import TaskManager


from dataModels.MessageModels import ArriveServicePointInfo, BatteryInfo, DeviceInfo, EnvironmentInfo, MessageEnvelope, MsgType, PositionInfo, SystemStatus, TaskListInfo,create_message_envelope
from dataModels.CommandModels import CmdType, CommandEnvelope, TaskCmd,create_cmd_envelope
from dataModels.TaskModels import OperationConfig, OperationMode, StationConfig, Station

# 只在不使用mock时导入真实控制器
RobotController = None

DBG = True

class RobotControlSystem:
    """机器人控制系统主类"""
    
    def __init__(self, config: Dict[str, Any] = None, use_mock: bool = True, report:bool=True):
        """
        初始化机器人控制系统
        
        Args:
            config: 系统配置字典
            use_mock: 是否使用Mock机器人控制器
        """
        self.config = config or {}
        self.use_mock = use_mock
        
        # 系统状态
        self.robot_id = self.config.get('robot_id', 123456)
        self.current_mode = RobotMode.STAND_BY  # 初始为standby
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

        # 初始化任务管理器（TaskManager完全管理robot_controller）
        self.task_manager = TaskManager(self.config, use_mock=use_mock)

        # 注册TaskManager系统回调（统一回调路径：TaskScheduler → TaskManager → RobotControlSystem）
        self.task_manager.register_system_callback("on_data_ready", self._handle_data_ready_callback)
        self.task_manager.register_system_callback("on_arrive_service_station", self._handle_arrive_service_station_callback)

        # 注册新的系统回调
        self.task_manager.register_system_callback("on_command_status_change", self._handle_command_status_callback)
        self.task_manager.register_system_callback("on_task_progress", self._handle_task_progress_callback)
        self.task_manager.register_system_callback("on_operation_result", self._handle_operation_result_callback)

        # gRPC相关配置（从config读取，如果没有则使用默认值）
        grpc_config = self.config.get('grpc_config', {})
        self.server_host = grpc_config.get('server_host', 'localhost')
        self.server_port = grpc_config.get('server_port', '50051')
        self.server_address = grpc_config.get('server_address', f"{self.server_host}:{self.server_port}")
        self.connection_timeout = grpc_config.get('connection_timeout', 10.0)
        self.stream_keep_alive_check = grpc_config.get('stream_keep_alive_check', 30.0)

        # 连接状态
        self.channel = None
        self.stub = None
        self.is_connected = False
        # 流管理
        self.client_upload_manager = None
        self.server_command_manager = None
        # 统计
        self.sent_count = 0
        self.received_count = 0
    
        # 通信相关
        self.report = report
        self._communication_thread = None
        self._stop_communication = False
        
        # 定时上报线程
        self._report_thread = None
        self._stop_reporting = False
        
        # 回调函数
        self.callbacks = {
            "on_command_received": [],
            "on_status_reported": []
        }

        self.logger.info("机器人控制系统初始化完成")

    def _init_grpc_client(self):
        """初始化gRPC客户端"""
        try:
            # 创建gRPC通道
            self.channel = grpc.insecure_channel(
                self.server_address,
                options=[
                    ('grpc.keepalive_time_ms', 30000),
                    ('grpc.keepalive_timeout_ms', 10000),
                    ('grpc.http2.max_pings_without_data', 0),  # 允许无数据时的ping
                    ('grpc.keepalive_permit_without_calls', True),
                ]
            )

            # 创建存根
            self.stub = RobotService_pb2_grpc.RobotServiceStub(self.channel)

            # 测试连接
            try:
               grpc.channel_ready_future(self.channel).result(
                    timeout=self.connection_timeout
                )
            except Exception as e:
                self.logger.warning(f"连接测试警告: {e}")

            # 初始化流管理器
            self.client_upload_manager = ClientUploadStreamManager(self.stub, self.robot_id)
            self.server_command_manager = ServerCommandStreamManager(self.stub, self.robot_id)

            # 启动持久化流
            client_upload_started = self.client_upload_manager.start_stream()
            # server_command_started = self.server_command_manager.start_with_heartbeat()
            server_command_started = self.server_command_manager.start_stream()
            
            if client_upload_started and server_command_started:
                self.is_connected = True
                self.logger.info(f"成功建立双向持久化连接，robot_id: {self.robot_id}")
                return True
            else:
                self.logger.error(f"流初始化失败: client_upload={client_upload_started}, server_command={server_command_started}")
                return False

        except Exception as e:
            self.logger.error(f"连接失败: {e}")
            return False
    
    def set_client_upload_response_handler(self, handler):
        """设置clientUpload流的专用响应处理器"""
        if self.client_upload_manager:
            self.client_upload_manager.response_handler = handler
            self.logger.info("clientUpload专用响应处理器已设置")
    
    def set_server_command_response_handler(self, handler):
        """设置serverCommand流的专用响应处理器"""
        if self.server_command_manager:
            self.server_command_manager.response_handler = handler
            self.logger.info("serverCommand专用响应处理器已设置")

    def is_stream_healthy(self) -> bool:
        """检查流健康状态"""
        return (self.is_connected and
                self.client_upload_manager and
                self.client_upload_manager.is_stream_active and
                self.server_command_manager and
                self.server_command_manager.is_stream_active)

    def start(self):
        """启动机器人控制系统"""
        if self.is_running:
            self.logger.warning("机器人控制系统已在运行")
            return
        
        self.logger.info("启动机器人控制系统...")
        
        
        # 启动通信接收
        if not self._init_grpc_client():
            self.logger.error("gRPC客户端初始化失败")
            return
        
        self.set_client_upload_response_handler(self._handle_clientUpload_response)
        self.set_server_command_response_handler(self._handle_serverCommand)
        
        # 启动定时上报
        if self.report:
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

        if self.client_upload_manager:
            self.client_upload_manager.stop_stream()
            
        if self.server_command_manager:
            self.server_command_manager.stop_stream()

        if self.channel:
            try:
                self.channel.close()
                self.logger.info("gRPC通道已关闭")
            except Exception as e:
                self.logger.error(f"关闭通道时出错: {e}")

        self.is_connected = False
        self.logger.info(f"客户端已关闭 - 发送: {self.sent_count} 条消息, 接收: {self.received_count} 条响应")

        '''停止任务管理器（会自动关闭机器人系统）'''
        self.task_manager.shutdown()

        self.logger.info("机器人控制系统已停止")
    
    #
    #                               处理serverCommand数据方法
    #
   
    def _handle_serverCommand(self, response: robot_pb2.ServerStreamMessage):
        """处理接收到的命令

        Args:
            response: ServerStreamMessage
        """
        command_envelope = convert_server_message_to_command_envelope(response)
        self.logger.debug(f"收到命令: {command_envelope.to_json()}")

        try:
            # 保存接收到的消息到数据库
            msg_id = command_envelope.cmd_id
            msg_time = command_envelope.cmd_time
            cmd_type = command_envelope.cmd_type
            robot_id = command_envelope.robot_id
            data_json = json.dumps(command_envelope.data_json)

            # 保存到数据库
            self.task_manager.database.save_received_message(
                msg_id=msg_id,
                msg_time=msg_time,
                cmd_type=cmd_type.value,
                robot_id=robot_id,
                data_json=data_json
            )

            # 触发命令接收回调
            self._trigger_callback("on_command_received", command_envelope.to_dict())

            # 特殊处理：模式命令直接更新系统状态
            if cmd_type == CmdType.ROBOT_MODE_CMD:
                mode_cmd = command_envelope.data_json.get('robot_mode_cmd', {})
                new_mode = RobotMode(mode_cmd.get('robot_mode'))
                self.current_mode = new_mode
                self.logger.info(f"机器人工作模式已更新为: {new_mode}")

            # 统一通过TaskManager处理所有命令
            command_id = self.task_manager.receive_command(command_envelope)
            self.logger.info(f"命令已提交给TaskManager: {command_id}, 类型: {cmd_type.value}")

            # 标记消息为已处理
            self.task_manager.database.mark_message_processed(msg_id)

        except Exception as e:
            self.logger.error(f"_handle_command 处理命令失败: {e}")
            # 可以在这里发送错误响应

    #
    #                               处理clientUpload数据方法
    #
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
            # 通过TaskManager获取机器人状态
            robot_status = self.task_manager.get_robot_status()
                        
            # 构建位置信息
            position_info = PositionInfo(
                agv_position_info=[
                    robot_status.get('agv_status_x', 0.0),
                    robot_status.get('agv_status_y', 0.0),
                    robot_status.get('agv_status_theta', 0.0)
                ],
                arm_position_info=robot_status.get('robot_joints', [0.0]*6),
                ext_position_info=robot_status.get('ext_axis', [0.0]*4),
            )
            # 构建任务信息（当前执行的任务）
            current_task_info = self.task_manager.get_current_task_info()
            if current_task_info:
                task_info = Task(
                    task_id=current_task_info["task_id"],
                    task_name=current_task_info["task_name"],
                    station_list=[],
                    status=TaskStatus(current_task_info["status"])
                )
            else:
                task_info = Task(
                    task_id='',
                    task_name='',
                    station_list=[],
                    status=TaskStatus.PENDING
                )
            
            # 构建电池信息
            battery_info = BatteryInfo(
                power_percent=robot_status.get('power_percent', 100.0),
                charge_status='charging' if robot_status.get('charge_state', False) else 'discharging'
            )

            # 从MessageModels中导入MoveStatus枚举
            from dataModels.MessageModels import MoveStatus
            
            # 构建系统状态
            move_status_str = robot_status.get('move_status', 'idle')
            try:
                move_status = MoveStatus(move_status_str)
            except ValueError:
                # 默认为idle
                move_status = MoveStatus.UNKNOWN
            
            system_status = SystemStatus(
                move_status=move_status,
                is_connected=True,
                soft_estop_status=robot_status.get('soft_estop_state', False),
                hard_estop_status=robot_status.get('hard_estop_state', False),
                estop_status=robot_status.get('estop_state', False)
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
            # 通过TaskManager获取机器人状态
            robot_status = self.task_manager.get_robot_status()
                        
            # 构建位置信息
            position_info = PositionInfo(
                agv_position_info=[
                    robot_status.get('agv_status_x', 0.0),
                    robot_status.get('agv_status_y', 0.0),
                    robot_status.get('agv_status_theta', 0.0)
                ],
                arm_position_info=robot_status.get('robot_joints', [0.0]*6),
                ext_position_info=robot_status.get('ext_axis', [0.0]*4),
            )
            # 构建任务信息（当前执行的任务）
            current_task_info = self.task_manager.get_current_task_info()
            if current_task_info:
                task_info = Task(
                    task_id=current_task_info["task_id"],
                    task_name=current_task_info["task_name"],
                    station_list=[],
                    status=TaskStatus(current_task_info["status"])
                )
            else:
                task_info = Task(
                    task_id='',
                    task_name='',
                    station_list=[],
                    status=TaskStatus.PENDING
                )
        
            # 通过TaskManager获取真实环境数据
            env_data = self.task_manager.get_environment_data()
            env_info = EnvironmentInfo(
                temperature=env_data.get('temperature', 0.0),
                humidity=env_data.get('humidity', 0.0),
                oxygen=env_data.get('oxygen', 0.0),
                carbon_dioxide=env_data.get('carbon_dioxide', 0.0),
                pm25=env_data.get('pm25', 0.0),
                pm10=env_data.get('pm10', 0.0),
                etvoc=env_data.get('etvoc', 0.0),
                noise=env_data.get('noise', 0.0)
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
    
    def _report_device_data(self, task: Station):
        """任务完成后上报设备数据

        Args:
            task: 完成的任务
        """
        try:
            # 通过TaskManager获取机器人状态
            robot_status = self.task_manager.get_robot_status()
                        
            # 构建位置信息
            position_info = PositionInfo(
                agv_position_info=[
                    robot_status.get('current_position', {}).get('x', 0.0),
                    robot_status.get('current_position', {}).get('y', 0.0),
                    robot_status.get('current_position', {}).get('theta', 0.0)
                ],
                arm_position_info=robot_status.get('robot_joints', [0.0]*6),
                ext_position_info=robot_status.get('ext_axis', [0.0]*4),
            )


            # 构建任务信息（当前执行的任务）
            current_task_info = self.task_manager.get_current_task_info()
            if current_task_info:
                task_info = Task(
                    task_id=current_task_info["task_id"],
                    task_name=current_task_info["task_name"],
                    station_list=[],
                    status=TaskStatus(current_task_info["status"])
                )
            else:
                task_info = Task(
                    task_id='',
                    task_name='',
                    station_list=[],
                    status=TaskStatus.PENDING
                )
            
            # 模拟设备数据
            device_info = DeviceInfo(
                deviceId=task.station_config.operation_config.get('device_id', ''),
                dataType='image',
                imageBase64='mock_base64_image_data'
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
    
    def _report_arrive_service_point(self):
        """到达服务点后上报

        Args:
            task: 当前任务
        """
        try:
            # 通过TaskManager获取机器人状态
            robot_status = self.task_manager.get_robot_status()
                        
            # 构建位置信息
            position_info = PositionInfo(
                agv_position_info=[
                    robot_status.get('current_position', {}).get('x', 0.0),
                    robot_status.get('current_position', {}).get('y', 0.0),
                    robot_status.get('current_position', {}).get('theta', 0.0)
                ],
                arm_position_info=robot_status.get('robot_joints', [0.0]*6),
                ext_position_info=robot_status.get('ext_axis', [0.0]*4),
            )
            # 构建任务信息（当前执行的任务）
            current_task_info = self.task_manager.get_current_task_info()
            if current_task_info:
                task_info = Task(
                    task_id=current_task_info["task_id"],
                    task_name=current_task_info["task_name"],
                    station_list=[],
                    status=TaskStatus(current_task_info["status"])
                )
            else:
                task_info = Task(
                    task_id='',
                    task_name='',
                    station_list=[],
                    status=TaskStatus.PENDING
                )
            

            # 构建到达服务点信息
            arrive_info = ArriveServicePointInfo(
                isArrive=True
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
        grpc_msg = convert_message_envelope_to_robot_upload_request(msg_envelope)
        self.client_upload_manager.send_message(grpc_msg)
        
        # 保存发送的消息到数据库
        try:
            msg_dict = msg_envelope.to_dict()
            self.task_manager.database.save_sent_message(
                msg_id=msg_dict.get('msg_id'),
                msg_time=msg_dict.get('msg_time'),
                msg_type=msg_dict.get('msg_type'),
                robot_id=msg_dict.get('robot_id'),
                data_json=json.dumps(msg_dict.get('data_json', {})),
                status='sent'
            )
        except Exception as e:
            self.logger.error(f"保存发送的消息到数据库失败: {e}")

    def _handle_clientUpload_response(self, response):
        """处理从gRPC服务器收到的响应/命令"""
        try:
            # TODO: 这里需要根据实际的响应格式来解析命令
            # 目前只是记录日志
            self.logger.info(f"处理gRPC响应: {response}")
            
        except Exception as e:
            self.logger.error(f"处理gRPC响应失败: {e}")

    #
    #                               回调函数
    #

    def _handle_data_ready_callback(self, data_type: str, **kwargs):
        """处理TaskManager的数据准备就绪回调（新增：双向回调机制）

        Args:
            data_type: 数据类型（device_data, task_complete等）
            **kwargs: 其他参数
        """
        self.logger.info(f"收到数据就绪回调: {data_type}")

        try:
            if data_type == "device_data":
                # 站点完成后上报设备数据
                station = kwargs.get("station")
                if station:
                    self._report_device_data(station)

            elif data_type == "task_complete":
                # 任务完成后可以上报任务统计数据
                task = kwargs.get("task")
                if task:
                    self.logger.info(f"任务 {task.task_id} 完成，可以上报统计数据")
                    # TODO: 实现任务统计数据上报

        except Exception as e:
            self.logger.error(f"处理数据就绪回调失败: {e}")

    def _handle_arrive_service_station_callback(self, **kwargs):
        """处理到达站点回调（新增：双向回调机制）

        Args:
            **kwargs: 其他参数
        """
        self.logger.info("收到到达站点回调")

        try:
            device_id = kwargs.get("device_id")
            if device_id:
                self._report_arrive_service_point(device_id)

        except Exception as e:
            self.logger.error(f"处理到达站点回调失败: {e}")

    def _handle_command_status_callback(self, **kwargs):
        """处理命令状态变化回调

        Args:
            **kwargs: 包含command对象
        """
        command = kwargs.get("command")
        if not command:
            return

        try:
            # 发送命令状态更新消息
            self._send_command_status_update(command)
        except Exception as e:
            self.logger.error(f"发送命令状态更新失败: {e}")

    def _handle_task_progress_callback(self, **kwargs):
        """处理任务进度回调

        Args:
            **kwargs: 包含task、station和command_id
        """
        task = kwargs.get("task")
        station = kwargs.get("station")
        command_id = kwargs.get("command_id")  # 新增：获取 command_id

        if not task:
            return

        try:
            # 发送任务进度更新消息，传递 command_id
            self._send_task_progress_update(task, station, command_id)
        except Exception as e:
            self.logger.error(f"发送任务进度更新失败: {e}")

    def _handle_operation_result_callback(self, **kwargs):
        """处理操作结果回调

        Args:
            **kwargs: 包含operation_data
        """
        operation_data = kwargs.get("operation_data")
        if not operation_data:
            return

        try:
            # 发送操作结果消息
            self._send_operation_result(operation_data)
        except Exception as e:
            self.logger.error(f"发送操作结果失败: {e}")

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

    # ==================== 新增消息发送方法 ====================

    def _send_command_status_update(self, command):
        """发送命令状态更新

        Args:
            command: UnifiedCommand对象
        """
        try:
            from dataModels.CommandModels import CmdType
            from dataModels.UnifiedCommand import CommandStatus
            import gRPC.RobotService_pb2 as robot_pb2

            # 映射命令状态
            status_map = {
                CommandStatus.PENDING: robot_pb2.CommandStatus.COMMAND_STATUS_PENDING,
                CommandStatus.QUEUED: robot_pb2.CommandStatus.COMMAND_STATUS_QUEUED,
                CommandStatus.RUNNING: robot_pb2.CommandStatus.COMMAND_STATUS_RUNNING,
                CommandStatus.COMPLETED: robot_pb2.CommandStatus.COMMAND_STATUS_COMPLETED,
                CommandStatus.FAILED: robot_pb2.CommandStatus.COMMAND_STATUS_FAILED,
                CommandStatus.CANCELLED: robot_pb2.CommandStatus.COMMAND_STATUS_CANCELLED,
                CommandStatus.RETRYING: robot_pb2.CommandStatus.COMMAND_STATUS_RETRYING,
            }

            # 映射命令类型
            cmd_type_map = {
                CmdType.TASK_CMD: robot_pb2.CmdType.TASK_CMD,
                CmdType.ROBOT_MODE_CMD: robot_pb2.CmdType.ROBOT_MODE_CMD,
                CmdType.JOY_CONTROL_CMD: robot_pb2.CmdType.JOY_CONTROL_CMD,
                CmdType.SET_MARKER_CMD: robot_pb2.CmdType.SET_MARKER_CMD,
                CmdType.CHARGE_CMD: robot_pb2.CmdType.CHARGE_CMD,
                CmdType.POSITION_ADJUST_CMD: robot_pb2.CmdType.POSITION_ADJUST_CMD,
                CmdType.RESPONSE_CMD: robot_pb2.CmdType.RESPONSE_CMD,
            }

            # 创建CommandStatusUpdate消息
            status_update = robot_pb2.CommandStatusUpdate(
                command_id=int(command.command_id) if command.command_id.isdigit() else abs(hash(command.command_id)) % (2**31),
                command_type=cmd_type_map.get(command.cmd_type, robot_pb2.CmdType.RESPONSE_CMD),
                status=status_map.get(command.status, robot_pb2.CommandStatus.COMMAND_STATUS_PENDING),
                message=command.error_message or f"命令状态: {command.status.value}",
                timestamp=int(time.time() * 1000),
                retry_count=command.retry_count
            )

            # 创建ClientStreamMessage
            client_msg = robot_pb2.ClientStreamMessage(
                command_id=int(command.command_id) if command.command_id.isdigit() else abs(hash(command.command_id)) % (2**31),
                command_time=int(time.time() * 1000),
                command_type=robot_pb2.ClientMessageType.COMMAND_STATUS_UPDATE,
                robot_id=self.robot_id,
                command_status=status_update
            )

            # 通过serverCommand流发送
            if self.server_command_manager and self.server_command_manager.is_stream_active:
                self.server_command_manager.send_message(client_msg)
                self.logger.info(f"命令状态更新已发送: {command.command_id} -> {command.status.value}")
            else:
                self.logger.warning("serverCommand流未激活，无法发送命令状态更新")

        except Exception as e:
            self.logger.error(f"发送命令状态更新异常: {e}")


    def _send_task_progress_update(self, task, station=None, command_id=None):
        """发送任务进度更新

        Args:
            task: Task对象
            station: 当前站点（可选）
            command_id: 命令ID（从回调传递）
        """
        try:
            import gRPC.RobotService_pb2 as robot_pb2
            from dataModels.TaskModels import TaskStatus, StationTaskStatus

            # 统计站点状态
            total_stations = len(task.station_list)
            completed_stations = sum(1 for s in task.station_list if s.status == StationTaskStatus.COMPLETED)
            failed_stations = sum(1 for s in task.station_list if s.status == StationTaskStatus.FAILED)

            # 获取当前站点信息
            if station is None:
                current_station_info = self.task_manager.get_current_station_info()
            else:
                # 从 station 对象提取信息
                current_station_info = {
                    "station_id": station.station_config.station_id,
                    "name": station.station_config.name,
                    "status": station.status.value,
                    "execution_phase": station.execution_phase.value,
                    "progress_detail": station.progress_detail
                } if station else None

            # 映射任务状态
            task_status_map = {
                TaskStatus.PENDING: robot_pb2.TaskStatus.TASK_STATUS_PENDING,
                TaskStatus.RUNNING: robot_pb2.TaskStatus.TASK_STATUS_RUNNING,
                TaskStatus.COMPLETED: robot_pb2.TaskStatus.TASK_STATUS_COMPLETED,
                TaskStatus.FAILED: robot_pb2.TaskStatus.TASK_STATUS_FAILED,
                TaskStatus.PARTIAL_COMPLETED: robot_pb2.TaskStatus.TASK_STATUS_COMPLETED,
                TaskStatus.RETRYING: robot_pb2.TaskStatus.TASK_STATUS_RETRYING,
            }

            # 映射站点状态
            station_status_map = {
                StationTaskStatus.PENDING: robot_pb2.StationTaskStatus.STATION_TASK_STATUS_PENDING,
                StationTaskStatus.RUNNING: robot_pb2.StationTaskStatus.STATION_TASK_STATUS_RUNNING,
                StationTaskStatus.COMPLETED: robot_pb2.StationTaskStatus.STATION_TASK_STATUS_COMPLETED,
                StationTaskStatus.FAILED: robot_pb2.StationTaskStatus.STATION_TASK_STATUS_FAILED,
                StationTaskStatus.RETRYING: robot_pb2.StationTaskStatus.STATION_TASK_STATUS_RETRYING,
            }

            # 创建TaskProgressUpdate消息
            progress_update = robot_pb2.TaskProgressUpdate(
                task_id=int(task.task_id),
                task_name=task.task_name,
                task_status=task_status_map.get(task.status, robot_pb2.TaskStatus.TASK_STATUS_PENDING),
                total_stations=total_stations,
                completed_stations=completed_stations,
                failed_stations=failed_stations,
                current_station_id=int(current_station_info["station_id"]) if current_station_info else 0,
                current_station_name=current_station_info.get("name", "") if current_station_info else "",
                current_station_status=station_status_map.get(
                    StationTaskStatus(current_station_info["status"]),
                    robot_pb2.StationTaskStatus.STATION_TASK_STATUS_PENDING
                ) if current_station_info else robot_pb2.StationTaskStatus.STATION_TASK_STATUS_PENDING,
                current_station_phase=current_station_info.get("execution_phase", "") if current_station_info else "",
                current_station_detail=current_station_info.get("progress_detail", "") if current_station_info else "",
                message=f"进度: {completed_stations}/{total_stations}",
                timestamp=int(time.time() * 1000)
            )

            # 使用传递的 command_id，如果没有则尝试从其他来源获取
            msg_command_id = command_id
            if msg_command_id is None:
                # 备用方案：从 scheduler.current_command 获取
                if self.task_manager.scheduler.current_command:
                    msg_command_id = self.task_manager.scheduler.current_command.command_id
                else:
                    # 最后备用：使用 task_id（记录警告）
                    self.logger.warning(f"无法获取 command_id，使用 task_id: {task.task_id}")
                    msg_command_id = str(task.task_id)

            # 创建ClientStreamMessage
            client_msg = robot_pb2.ClientStreamMessage(
                command_id=int(msg_command_id) if str(msg_command_id).isdigit() else abs(hash(str(msg_command_id))) % (2**31),
                command_time=int(time.time() * 1000),
                command_type=robot_pb2.ClientMessageType.TASK_PROGRESS_UPDATE,
                robot_id=self.robot_id,
                task_progress=progress_update
            )

            # 通过serverCommand流发送
            if self.server_command_manager and self.server_command_manager.is_stream_active:
                self.server_command_manager.send_message(client_msg)
                self.logger.info(f"任务进度更新已发送: {task.task_id} - {completed_stations}/{total_stations}")
            else:
                self.logger.warning("serverCommand流未激活，无法发送任务进度更新")

        except Exception as e:
            self.logger.error(f"发送任务进度更新异常: {e}")


    def _send_operation_result(self, operation_data: Dict[str, Any]):
        """发送操作结果

        Args:
            operation_data: 操作数据，包含task_id, station_id, operation_mode, result, command_id
        """
        try:
            import gRPC.RobotService_pb2 as robot_pb2
            from dataModels.TaskModels import OperationMode

            result = operation_data.get('result', {})
            operation_mode = operation_data.get('operation_mode')

            # 映射操作模式
            operation_mode_map = {
                OperationMode.OPEN_DOOR: robot_pb2.OperationMode.OPERATION_MODE_OPEN_DOOR,
                OperationMode.CLOSE_DOOR: robot_pb2.OperationMode.OPERATION_MODE_CLOSE_DOOR,
                OperationMode.CAPTURE: robot_pb2.OperationMode.OPERATION_MODE_CAPTURE,
                OperationMode.SERVE: robot_pb2.OperationMode.OPERATION_MODE_SERVICE,
                OperationMode.NONE: robot_pb2.OperationMode.OPERATION_MODE_NONE,
            }

            # 映射操作状态
            operation_status = (
                robot_pb2.OperationStatus.OPERATION_STATUS_SUCCESS
                if result.get('success', False)
                else robot_pb2.OperationStatus.OPERATION_STATUS_FAILED
            )

            # 提取device_id和door_ip
            device_id = result.get('device_id', '')
            door_ip = result.get('door_ip', '')

            # 转换device_id为整数（如果是字符串）
            try:
                device_id_int = int(device_id) if device_id else 0
            except (ValueError, TypeError):
                device_id_int = 0

            # 创建OperationResult消息
            operation_result = robot_pb2.OperationResult(
                task_id=int(operation_data.get('task_id', 0)),
                station_id=int(operation_data.get('station_id', 0)),
                operation_mode=operation_mode_map.get(operation_mode, robot_pb2.OperationMode.OPERATION_MODE_NONE),
                status=operation_status,
                message=result.get('message', ''),
                image_base64=result.get('images', []),  # 仅CAPTURE操作有数据
                device_id=device_id_int,
                door_ip=door_ip,
                timestamp=int(result.get('timestamp', time.time()) * 1000),
                duration=result.get('duration', 0.0)
            )

            # 使用从 operation_data 中传递的 command_id
            command_id = operation_data.get('command_id')
            if command_id is None:
                # 备用方案：从 scheduler.current_command 获取
                if self.task_manager.scheduler.current_command:
                    command_id = self.task_manager.scheduler.current_command.command_id
                else:
                    # 最后备用：使用 task_id（记录警告）
                    self.logger.warning(f"无法获取 command_id，使用 task_id: {operation_data.get('task_id', 0)}")
                    command_id = str(operation_data.get('task_id', 0))

            # 创建ClientStreamMessage
            client_msg = robot_pb2.ClientStreamMessage(
                command_id=int(command_id) if str(command_id).isdigit() else abs(hash(str(command_id))) % (2**31),
                command_time=int(time.time() * 1000),
                command_type=robot_pb2.ClientMessageType.OPERATION_RESULT,
                robot_id=self.robot_id,
                operation_result=operation_result
            )

            # 通过serverCommand流发送
            if self.server_command_manager and self.server_command_manager.is_stream_active:
                self.server_command_manager.send_message(client_msg)
                self.logger.info(f"操作结果已发送: {operation_mode.value} -> {result.get('success')}")
            else:
                self.logger.warning("serverCommand流未激活，无法发送操作结果")

        except Exception as e:
            self.logger.error(f"发送操作结果异常: {e}")

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
    
    # def send_server_command(self, command_data: Dict[str, Any]) -> bool:
    #     """通过serverCommand流发送服务端命令
        
    #     Args:
    #         command_data: 命令数据字典
            
    #     Returns:
    #         bool: 发送是否成功
    #     """
    #     if not self.server_command_manager or not self.server_command_manager.is_stream_active:
    #         self.logger.error("serverCommand流未激活，无法发送命令")
    #         return False
        
    #     try:
    #         # 创建ServerStreamMessage消息
    #         import uuid
    #         msg_id = int(uuid.uuid4().hex[:8], 16)
            
    #         # 获取命令类型
    #         cmd_type = command_data.get('cmd_type', 'unknown')
            
    #         # 创建gRPC消息
    #         grpc_msg = robot_pb2.ServerStreamMessage(
    #             msg_id=msg_id,
    #             msg_time=int(time.time()),
    #             robot_id=self.robot_id,
    #             cmd_type=cmd_type
    #         )
            
    #         # 根据命令类型填充数据
    #         if cmd_type == 'task':
    #             task_data = command_data.get('task_data', {})
    #             grpc_msg.task_data.CopyFrom(robot_pb2.TaskCmdData(
    #                 task_id=task_data.get('task_id', ''),
    #                 task_type=task_data.get('task_type', 'delivery'),
    #                 priority=task_data.get('priority', 1),
    #                 start_point=task_data.get('start_point', ''),
    #                 end_point=task_data.get('end_point', ''),
    #                 operation_type=task_data.get('operation_type', 'pickup')
    #             ))
    #         elif cmd_type == 'mode':
    #             mode_data = command_data.get('mode_data', {})
    #             grpc_msg.mode_data.CopyFrom(robot_pb2.ModeCmdData(
    #                 mode=mode_data.get('mode', 'auto'),
    #                 operation_mode=mode_data.get('operation_mode', 'normal')
    #             ))
    #         elif cmd_type == 'joy':
    #             joy_data = command_data.get('joy_data', {})
    #             grpc_msg.joy_data.CopyFrom(robot_pb2.JoyCmdData(
    #                 linear_x=joy_data.get('linear_x', 0.0),
    #                 linear_y=joy_data.get('linear_y', 0.0),
    #                 angular_z=joy_data.get('angular_z', 0.0)
    #             ))
    #         elif cmd_type == 'charge':
    #             charge_data = command_data.get('charge_data', {})
    #             grpc_msg.charge_data.CopyFrom(robot_pb2.ChargeCmdData(
    #                 action=charge_data.get('action', 'start')
    #             ))
            
    #         # 发送消息
    #         success = self.server_command_manager.send_message(grpc_msg)
    #         if success:
    #             self.logger.info(f"服务端命令已发送: msg_id={msg_id}, cmd_type={cmd_type}")
    #         else:
    #             self.logger.error(f"服务端命令发送失败: msg_id={msg_id}, cmd_type={cmd_type}")
            
    #         return success
            
    #     except Exception as e:
    #         self.logger.error(f"发送服务端命令异常: {e}")
    #         return False

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
        'robot_id': 123456,
        'robot_config': {
            'success_rate': 0.95,
            'latency': 10,
            'max_error_rate': 0.0,
            "robot_ip": "192.168.10.90",
            "ext_base_url": "http://192.168.10.90:5000/api/extaxis",
            "agv_ip": "192.168.10.10",
            "agv_port": 31001,
        },

        # gRPC 配置
        'grpc_config': {
            'server_host': 'localhost',      # gRPC 服务器地址
            'server_port': 50051,            # gRPC 服务器端口
            # 'server_address': 'localhost:50051',  # 可选：直接指定完整地址
            'connection_timeout': 10,        # 连接超时时间（秒）
            'stream_keep_alive_check': 30     # 流保持活跃检查间隔（秒）
        },

        # 环境传感器配置
        "env_sensor_enabled": True,  # 设置为 False，避免没有传感器时报错
        "env_sensor_config": {
            "port": "COM4",          # 串口
            "baudrate": 4800,        # 波特率
            "address": 0x01,         # 设备地址
            "read_interval": 5       # 读取间隔（秒）
        }

    }
    
    robot_system = RobotControlSystem(config, use_mock=False,report=False)
    try:
        # 启动系统
        robot_system.start()
        # 运行一段时间
        time.sleep(6000)
        
    except KeyboardInterrupt:
        print("\n收到中断信号，关闭系统...")
    finally:
        # 关闭系统
        robot_system.shutdown()
