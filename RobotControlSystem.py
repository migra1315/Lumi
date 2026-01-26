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
from utils.logger_config import get_logger

import grpc
import gRPC.RobotService_pb2 as robot_pb2
from gRPC import RobotService_pb2_grpc
from gRPC.StreamManager import ClientUploadStreamManager, ServerCommandStreamManager
from utils.dataConverter import convert_server_message_to_command_envelope, convert_message_envelope_to_robot_upload_request

from task.TaskManager import TaskManager


from dataModels.MessageModels import BatteryInfo, EnvironmentInfo, MessageEnvelope, MsgType, PositionInfo, SystemStatus, TaskListInfo,create_message_envelope
from dataModels.CommandModels import CmdType, CommandEnvelope, TaskCmd,create_cmd_envelope
from dataModels.TaskModels import OperationConfig, OperationMode, StationConfig, Station

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
        self.logger = get_logger(__name__)

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
            # 注意: keepalive_time_ms 需要大于服务端的 GRPC_ARG_HTTP2_MIN_RECV_PING_INTERVAL_WITHOUT_DATA_MS
            # 服务端默认最小间隔是5分钟(300秒)，所以客户端应该设置更长的间隔避免"Too many pings"错误
            self.channel = grpc.insecure_channel(
                self.server_address,
                options=[
                    ('grpc.keepalive_time_ms', 60000),  # 每60秒发送ping（如果有数据传输）
                    ('grpc.keepalive_timeout_ms', 20000),  # ping超时20秒
                    ('grpc.http2.max_pings_without_data', 2),  # 无数据时最多发送2次ping
                    ('grpc.keepalive_permit_without_calls', False),  # 没有活跃调用时不发送keepalive ping
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
            server_command_started = self.server_command_manager.start_with_heartbeat()
            # server_command_started = self.server_command_manager.start_st
            # ream()
            
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
                # 初始化任务管理器（TaskManager完全管理robot_controller）
        self.task_manager = TaskManager(self.config, use_mock=self.use_mock)


        # 注册新的系统回调
        self.task_manager.register_system_callback("on_command_status_change", self._handle_command_status_callback)
        self.task_manager.register_system_callback("on_task_progress", self._handle_task_progress_callback)
        self.task_manager.register_system_callback("on_operation_result", self._handle_operation_result_callback)

        
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

        # 1. 先停止定时上报线程（避免继续尝试发送消息）
        self.logger.info("正在停止定时上报线程...")
        self._stop_reporting = True
        if self._report_thread and self._report_thread.is_alive():
            self._report_thread.join(timeout=3)
            if self._report_thread.is_alive():
                self.logger.warning("定时上报线程未能在超时时间内停止")
            else:
                self.logger.info("定时上报线程已停止")

        # 2. 停止任务管理器（会自动关闭机器人系统）
        self.logger.info("正在关闭任务管理器...")
        self.task_manager.shutdown()

        # 3. 停止 gRPC 通信
        self.logger.info("正在关闭gRPC客户端...")

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
        self.logger.info(f"收到命令: {command_envelope.to_json()}")

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
            if cmd_type == CmdType.RESPONSE_CMD:
                return
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

            # 从快照获取任务信息（统一数据源）
            snapshot = self.task_manager.get_progress_snapshot()
            if snapshot:
                task_info = snapshot["task"]
            else:
                # 无任务时创建空任务对象
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

            # 从快照获取任务信息（统一数据源）
            snapshot = self.task_manager.get_progress_snapshot()
            if snapshot:
                task_info = snapshot["task"]
            else:
                # 无任务时创建空任务对象
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
        """处理从gRPC服务器收到的响应/命令（clientUpload流的响应）"""
        try:
            # 保存响应到数据库
            msg_id = str(uuid.uuid4())
            msg_time = int(time.time() * 1000)

            # 根据响应类型提取数据
            response_data = {}
            msg_type = "UPLOAD_RESPONSE"

            # 尝试从 protobuf 响应中提取信息
            if hasattr(response, 'code'):
                response_data['code'] = response.code
            if hasattr(response, 'info'):
                response_data['info'] = response.info
            if hasattr(response, 'msg_id'):
                response_data['msg_id'] = response.msg_id

            # 保存到 client_upload_received 表
            self.task_manager.database.save_client_upload_received(
                msg_id=msg_id,
                msg_time=msg_time,
                msg_type=msg_type,
                robot_id=str(self.robot_id),
                data_json=json.dumps(response_data, ensure_ascii=False)
            )

            self.logger.debug(f"处理gRPC响应: {response}")

        except Exception as e:
            self.logger.error(f"处理gRPC响应失败: {e}")

    #
    #                               回调函数
    #

    def _handle_command_status_callback(self, **kwargs):
        """处理命令状态变化回调

        Args:
            **kwargs: 包含command对象
        """
        command = kwargs.get("command")
        if not command:
            return

        try:
            from dataModels.CommandModels import CmdType
            from dataModels.UnifiedCommand import CommandStatus

            # 发送命令状态更新消息
            self._send_command_status_update(command)

            # 特殊处理：SET_MARKER_CMD 成功时发送位置信息响应
            if (command.cmd_type == CmdType.SET_MARKER_CMD and
                command.status == CommandStatus.COMPLETED):
                self._send_set_marker_response(command)

        except Exception as e:
            self.logger.error(f"发送命令状态更新失败: {e}")

    def _handle_task_progress_callback(self):
        """处理任务进度回调（简化版 - 无需参数）"""
        try:
            # 直接调用，无需参数（从TaskManager快照获取数据）
            self._send_task_progress_update()
        except Exception as e:
            self.logger.error(f"发送任务进度更新失败: {e}")

    def _handle_operation_result_callback(self, **kwargs):
        """处理操作结果回调

        Args:
            **kwargs: 包含operation_data（操作特定数据，如result、operation_mode）
        """
        operation_data = kwargs.get("operation_data")
        if not operation_data:
            return

        try:
            # 发送操作结果消息（task_id/station_id/command_id从快照获取）
            self._send_operation_result(operation_data)
        except Exception as e:
            self.logger.error(f"发送操作结果失败: {e}")


    # ==================== 新增消息发送方法 ====================

    def _save_server_command_message(self, msg_id: str, msg_time: int, msg_type: str, data: dict, command_id: str = None):
        """保存通过 serverCommand 流发送的消息到数据库

        Args:
            msg_id: 消息唯一标识
            msg_time: 消息时间戳（毫秒）
            msg_type: 消息类型
            data: 消息数据字典
            command_id: 关联的命令ID（可选）
        """
        try:
            self.task_manager.database.save_sent_message(
                msg_id=msg_id,
                msg_time=msg_time,
                msg_type=msg_type,
                robot_id=str(self.robot_id),
                data_json=json.dumps(data, ensure_ascii=False),
                status='sent'
            )
        except Exception as e:
            self.logger.error(f"保存 serverCommand 消息到数据库失败: {e}")

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

                # 保存发送的消息到数据库
                msg_id = str(uuid.uuid4())
                msg_time = int(time.time() * 1000)
                self._save_server_command_message(
                    msg_id=msg_id,
                    msg_time=msg_time,
                    msg_type="COMMAND_STATUS_UPDATE",
                    data={
                        "command_id": command.command_id,
                        "command_type": command.cmd_type.value,
                        "status": command.status.value,
                        "message": command.error_message or f"命令状态: {command.status.value}",
                        "retry_count": command.retry_count
                    },
                    command_id=command.command_id
                )
            else:
                self.logger.warning("serverCommand流未激活，无法发送命令状态更新")

        except Exception as e:
            self.logger.error(f"发送命令状态更新异常: {e}")

    def _send_set_marker_response(self, command):
        """发送设置标记点响应

        当 SET_MARKER_CMD 执行成功后，回传当前位置信息

        Args:
            command: UnifiedCommand对象
        """
        try:
            import gRPC.RobotService_pb2 as robot_pb2

            # 从 TaskManager 获取当前位置信息
            robot_status = self.task_manager.get_robot_status()

            # 构建 PositionInfo
            position_info = robot_pb2.PositionInfo(
                AGVPositionInfo=[
                    robot_status.get('agv_status_x', 0.0),
                    robot_status.get('agv_status_y', 0.0),
                    robot_status.get('agv_status_theta', 0.0)
                ],
                ARMPositionInfo=robot_status.get('robot_joints', [0.0]*6),
                EXTPositionInfo=robot_status.get('ext_axis', [0.0]*4)
            )

            # 创建 ClientStreamMessage（先创建基础字段）
            client_msg = robot_pb2.ClientStreamMessage(
                command_id=int(command.command_id) if command.command_id.isdigit() else abs(hash(command.command_id)) % (2**31),
                command_time=int(time.time() * 1000),
                command_type=robot_pb2.ClientMessageType.SET_MARKER_RESPONSE,
                robot_id=self.robot_id
            )

            # 使用 CopyFrom 设置 oneof 字段中的嵌套消息
            client_msg.position_info.CopyFrom(position_info)

            # 通过 serverCommand 流发送
            if self.server_command_manager and self.server_command_manager.is_stream_active:
                self.server_command_manager.send_message(client_msg)
                self.logger.info(f"SET_MARKER_RESPONSE 已发送: command_id={command.command_id}")

                # 保存发送的消息到数据库
                msg_id = str(uuid.uuid4())
                msg_time = int(time.time() * 1000)
                self._save_server_command_message(
                    msg_id=msg_id,
                    msg_time=msg_time,
                    msg_type="SET_MARKER_RESPONSE",
                    data={
                        "command_id": command.command_id,
                        "agv_position": [
                            robot_status.get('agv_status_x', 0.0),
                            robot_status.get('agv_status_y', 0.0),
                            robot_status.get('agv_status_theta', 0.0)
                        ],
                        "arm_position": robot_status.get('robot_joints', [0.0]*6),
                        "ext_position": robot_status.get('ext_axis', [0.0]*4)
                    },
                    command_id=command.command_id
                )
            else:
                self.logger.warning("serverCommand流未激活，无法发送SET_MARKER_RESPONSE")

        except Exception as e:
            self.logger.error(f"发送SET_MARKER_RESPONSE异常: {e}")

    def _send_task_progress_update(self):
        """发送任务进度更新（简化版 - 从TaskManager获取快照）"""
        try:
            import gRPC.RobotService_pb2 as robot_pb2
            from dataModels.TaskModels import TaskStatus, StationTaskStatus

            # 从 TaskManager 获取完整快照
            snapshot = self.task_manager.get_progress_snapshot()
            if not snapshot:
                self.logger.warning("无任务进度可上报")
                return

            task = snapshot["task"]
            station = snapshot["station"]
            command_id = snapshot["command_id"]

            # 统计站点状态
            total_stations = len(task.station_list)
            completed_stations = sum(1 for s in task.station_list if s.status == StationTaskStatus.COMPLETED)
            failed_stations = sum(1 for s in task.station_list if s.status == StationTaskStatus.FAILED)

            # 获取当前站点信息
            current_station_info = None
            if station:
                current_station_info = {
                    "station_id": station.station_config.station_id,
                    "name": station.station_config.name,
                    "status": station.status.value,
                    "execution_phase": station.execution_phase.value,
                    "progress_detail": station.progress_detail
                }

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

            # 使用快照中的 command_id
            msg_command_id = command_id if command_id else str(task.task_id)

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

                # 保存发送的消息到数据库
                msg_id = str(uuid.uuid4())
                msg_time = int(time.time() * 1000)
                self._save_server_command_message(
                    msg_id=msg_id,
                    msg_time=msg_time,
                    msg_type="TASK_PROGRESS_UPDATE",
                    data={
                        "task_id": task.task_id,
                        "task_name": task.task_name,
                        "task_status": task.status.value,
                        "total_stations": total_stations,
                        "completed_stations": completed_stations,
                        "failed_stations": failed_stations,
                        "current_station": current_station_info
                    },
                    command_id=command_id
                )
            else:
                self.logger.warning("serverCommand流未激活，无法发送任务进度更新")

        except Exception as e:
            self.logger.error(f"发送任务进度更新异常: {e}")


    def _send_operation_result(self, operation_data: Dict[str, Any]):
        """发送操作结果（简化版 - 从TaskManager获取task_id/station_id/command_id）

        Args:
            operation_data: 操作数据，包含operation_mode和result（特定于操作的数据）
        """
        try:
            import gRPC.RobotService_pb2 as robot_pb2
            from dataModels.TaskModels import OperationMode

            # 从快照获取task_id, station_id, command_id
            snapshot = self.task_manager.get_progress_snapshot()
            if not snapshot:
                self.logger.warning("无法获取进度快照，操作结果上报失败")
                return

            task = snapshot["task"]
            station = snapshot["station"]
            command_id = snapshot["command_id"]

            task_id = int(task.task_id) if task else 0
            station_id = int(station.station_config.station_id) if station else 0
            msg_command_id = command_id if command_id else str(task_id)

            # 从 operation_data 提取操作特定数据
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
                task_id=task_id,
                station_id=station_id,
                operation_mode=operation_mode_map.get(operation_mode, robot_pb2.OperationMode.OPERATION_MODE_NONE),
                status=operation_status,
                message=result.get('message', ''),
                image_base64=result.get('images', []),  # 仅CAPTURE操作有数据
                device_id=device_id_int,
                door_ip=door_ip,
                timestamp=int(result.get('timestamp', time.time()) * 1000),
                duration=result.get('duration', 0.0)
            )

            # 创建ClientStreamMessage
            client_msg = robot_pb2.ClientStreamMessage(
                command_id=int(msg_command_id) if str(msg_command_id).isdigit() else abs(hash(str(msg_command_id))) % (2**31),
                command_time=int(time.time() * 1000),
                command_type=robot_pb2.ClientMessageType.OPERATION_RESULT,
                robot_id=self.robot_id,
                operation_result=operation_result
            )

            # 通过serverCommand流发送
            if self.server_command_manager and self.server_command_manager.is_stream_active:
                self.server_command_manager.send_message(client_msg)
                self.logger.info(f"操作结果已发送: {operation_mode.value} -> {result.get('success')}")

                # 保存发送的消息到数据库
                msg_id = str(uuid.uuid4())
                msg_time = int(time.time() * 1000)
                self._save_server_command_message(
                    msg_id=msg_id,
                    msg_time=msg_time,
                    msg_type="OPERATION_RESULT",
                    data={
                        "task_id": task_id,
                        "station_id": station_id,
                        "operation_mode": operation_mode.value if operation_mode else "NONE",
                        "success": result.get('success', False),
                        "message": result.get('message', ''),
                        "device_id": device_id,
                        "door_ip": door_ip,
                        "duration": result.get('duration', 0.0)
                    },
                    command_id=msg_command_id
                )
            else:
                self.logger.warning("serverCommand流未激活，无法发送操作结果")

        except Exception as e:
            self.logger.error(f"发送操作结果异常: {e}")


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

def load_config(config_path: str) -> Dict[str, Any]:
    """从JSON文件加载配置

    Args:
        config_path: 配置文件路径

    Returns:
        配置字典

    Raises:
        FileNotFoundError: 配置文件不存在
        json.JSONDecodeError: 配置文件格式错误
    """
    import os

    if not os.path.exists(config_path):
        raise FileNotFoundError(f"配置文件不存在: {config_path}")

    with open(config_path, 'r', encoding='utf-8') as f:
        config = json.load(f)

    return config


if __name__ == "__main__":
    import os

    # 配置文件路径
    CONFIG_PATH = os.path.join(os.path.dirname(__file__), "conf", "config.json")

    # 加载配置
    config = load_config(CONFIG_PATH)

    # 导入并配置统一日志系统
    from utils.logger_config import setup_logging, log_system_info

    # 从配置读取日志设置
    log_config = config.get('log_config', {})
    setup_logging(
        level=log_config.get('level', 'INFO'),
        log_name_prefix=log_config.get('log_name_prefix', 'robot_control_system'),
        use_color=log_config.get('use_color', True),
        enable_file_logging=log_config.get('enable_file_logging', True),
        robot_id=config.get('robot_id', 123456)
    )

    # 记录系统信息
    log_system_info()

    # 创建机器人控制系统
    robot_system = RobotControlSystem(
        config=config,
        use_mock=config.get('use_mock', True),
        report=config.get('report', True)
    )

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
