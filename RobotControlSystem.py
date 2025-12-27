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
from utils.dataConverter import convert_server_cmd_to_command_envelope, convert_message_envelope_to_robot_upload_request

from task.TaskManager import TaskManager
from robot.MockRobotController import MockRobotController

from dataModels.MessageModels import ArriveServicePointInfo, BatteryInfo, DeviceInfo, EnvironmentInfo, MessageEnvelope, MsgType, PositionInfo, SystemStatus, TaskListInfo,create_message_envelope
from dataModels.CommandModels import CmdType, CommandEnvelope, TaskCmd,create_cmd_envelope
from dataModels.TaskModels import OperationConfig, OperationMode, StationConfig, Station


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
        self.client_upload_manager = None
        self.server_command_manager = None
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
    
    def _init_grpc_client(self):
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
               grpc.channel_ready_future(self.channel).result(
                    timeout=self.gRPC_config.CONNECTION_TIMEOUT
                )
            except Exception as e:
                self.logger.warning(f"连接测试警告: {e}")

            # 初始化流管理器
            self.client_upload_manager = ClientUploadStreamManager(self.stub, self.robot_id)
            self.server_command_manager = ServerCommandStreamManager(self.stub, self.robot_id)

            # 启动持久化流
            client_upload_started = self.client_upload_manager.start_stream()
            # client_upload_started = True
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
        self.set_server_command_response_handler(self._handle_serverCommand_response)
        
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

        '''停止任务管理器'''
        self.task_manager.shutdown()
        
        '''关闭机器人系统'''
        self.robot_controller.shutdown_system()
        
        self.logger.info("机器人控制系统已停止")
    
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
        
        station_config1 = StationConfig(
            station_id="station_id_1",
            sort=1,
            name="站点1",
            agv_marker="marker_1",
            robot_pos=[0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
            ext_pos=[0.0, 0.0, 0.0, 0.0],
            operation_config=operation_config1
        )
        station_config2 = StationConfig(
            station_id="station_id_2",
            sort=2,
            name="站点2",
            agv_marker="marker_2",
            robot_pos=[10.0, 10.0, 0.0, 0.0, 0.0, 0.0],
            ext_pos=[1.0, 0.0, 0.0, 0.0],
            operation_config=operation_config2
        )
        # 创建一个模拟的TaskCmd
        task_cmd = TaskCmd(
            task_id=f"TASK_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')[:-3]}",
            task_name="测试巡检任务",
            robot_mode=RobotMode.INSPECTION,
            generate_time=datetime.now(),
            station_config_tasks=[
               station_config1,
               station_config2
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
        self._handle_command(cmd_envelope)
    
    #
    #                               处理serverCommand数据方法
    #

    def _handle_serverCommand_response(self, response):
        """处理从gRPC服务器收到的响应/命令"""
        try:
            command_envelope = convert_server_cmd_to_command_envelope(response)
            self._handle_command(command_envelope)
            self.logger.info(f"处理gRPC响应: {response}")
            
        except Exception as e:
            self.logger.error(f"处理gRPC响应失败: {e}")
            
    def _handle_command(self, command_envelope: CommandEnvelope):
        """处理接收到的命令
        
        Args:
            command_envelope: 命令信封对象
        """
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
            
            # 根据命令类型处理
            if cmd_type == CmdType.TASK_CMD:
                self._handle_task_command(command_envelope.data_json)
            elif cmd_type == CmdType.ROBOT_MODE_CMD:
                self._handle_mode_command(command_envelope.data_json)
            elif cmd_type == CmdType.JOY_CONTROL_CMD:
                self._handle_joy_command(command_envelope.data_json)
            elif cmd_type == CmdType.CHARGE_CMD:
                self._handle_charge_command(command_envelope.data_json)
            else:
                self.logger.warning(f"未知命令类型: {cmd_type}")
            
            # 标记消息为已处理
            self.task_manager.database.mark_message_processed(msg_id)
                
        except Exception as e:
            self.logger.error(f"_handle_command 处理命令失败: {e}")
            # 可以在这里发送错误响应
    
    def _handle_task_command(self, data_json: Dict[str, Any]):
        """处理任务命令
        
        Args:
            data_json: 任务命令数据
        """
        self.logger.info(f"处理任务命令: {json.dumps(data_json, ensure_ascii=False)}")
        
        try:
            # 从TaskCmd转换为内部任务格式
            task_cmd = data_json.get('task_cmd', {})
            
            # 提取任务信息
            task_id = task_cmd.get('task_id')
            task_name = task_cmd.get('task_name')
            robot_mode = RobotMode(task_cmd.get('robot_mode'))
            generate_time = datetime.fromisoformat(task_cmd.get('generate_time'))
            station_config_tasks = task_cmd.get('station_config_tasks', [])
            
            # 更新机器人工作模式
            self.current_mode = robot_mode
            
            # 创建TaskCmd对象
            task_cmd_obj = TaskCmd(
                task_id=task_id,
                task_name=task_name,
                robot_mode=robot_mode,
                generate_time=generate_time,
                station_config_tasks=[]
            )
            
            # 处理每个站点任务
            for station_config_dict in station_config_tasks:
                # 提取操作配置数据
                operation_config_data = station_config_dict.get('operation_config', {})
                
                # 创建操作配置对象
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
                
                # 添加到TaskCmd对象
                task_cmd_obj.station_config_tasks.append(station_config)
            
            # 添加到任务管理器
            task_id = self.task_manager.receive_task_from_cmd(task_cmd_obj)
            
            self.logger.info(f"成功添加任务: {task_id}, 包含{len(task_cmd_obj.station_config_tasks)}个站点")
        except Exception as e:
            self.logger.error(f"_handle_task_command 处理任务命令失败: {e}")
            raise
    
    def _handle_mode_command(self, data_json: Dict[str, Any]):
        """处理模式命令
        
        Args:
            data_json: 模式命令数据
        """
        self.logger.info(f"处理模式命令: {json.dumps(data_json, ensure_ascii=False)}")
        
        try:
            mode_cmd = data_json.get('robot_mode_cmd', {})
            new_mode = RobotMode(mode_cmd.get('robot_mode'))
            
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
    
    def _handle_set_marker_command(self, data_json: Dict[str, Any]):
        """处理设置标记命令
        
        Args:
            data_json: 设置标记命令数据
        """
        self.logger.info(f"处理设置标记命令: {json.dumps(data_json, ensure_ascii=False)}")
        try:
            set_marker_cmd = data_json.get('set_marker_cmd', {})
            marker_id = set_marker_cmd.get('marker_id', '')
            
            if marker_id:
                self.logger.info(f"设置标记为: {marker_id}")
                # TODO: 实现设置标记逻辑
            else:
                self.logger.warning("未指定标记ID")
        except Exception as e:
            self.logger.error(f"处理设置标记命令失败: {e}")

    def _handle_position_adjust_command(self, data_json: Dict[str, Any]):
        """处理位置调整命令
        
        Args:
            data_json: 位置调整命令数据
        """
        self.logger.info(f"处理位置调整命令: {json.dumps(data_json, ensure_ascii=False)}")
        # TODO: 实现位置调整逻辑

    def _handle_charge_command(self, data_json: Dict[str, Any]):
        """处理充电命令
        
        Args:
            data_json: 充电命令数据
        """
        self.logger.info(f"处理充电命令: {json.dumps(data_json, ensure_ascii=False)}")
        
        try:
            charge_cmd = data_json.get('charge_cmd', {})
            charge = charge_cmd.get('charge', False)
            
            if charge:
                self.logger.info("开始充电")
                self.robot_controller.charge()
            else:
                self.logger.info("停止充电")
                # TODO: 实现停止充电逻辑
                
        except Exception as e:
            self.logger.error(f"处理充电命令失败: {e}")
    
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
            task_info = TaskListInfo(
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
            task_info = TaskListInfo(
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
    
    def _report_device_data(self, task: Station):
        """任务完成后上报设备数据
        
        Args:
            task: 完成的任务
        """
        try:
            # 模拟设备数据
            device_info = DeviceInfo(
                deviceId=task.station_config.operation_config.get('device_id', ''),
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
                targetPoint=task.station_config.agv_marker
            )
            
            # 构建任务信息
            task_info = TaskListInfo(
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
    
    def _report_arrive_service_point(self, task: Station):
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
                targetPoint=task.station_config.agv_marker
            )
            
            # 构建任务信息
            task_info = TaskListInfo(
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
        grpc_msg = convert_message_envelope_to_robot_upload_request(msg_envelope)
        self.client_upload_manager.send_message(grpc_msg)
        
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

    def _on_task_complete(self, task: Task):
        """任务完成回调
        
        Args:
            task: 完成的任务
        """
        self.logger.info(f"任务完成回调: {task.task_id}")
        
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
    
    def send_server_command(self, command_data: Dict[str, Any]) -> bool:
        """通过serverCommand流发送服务端命令
        
        Args:
            command_data: 命令数据字典
            
        Returns:
            bool: 发送是否成功
        """
        if not self.server_command_manager or not self.server_command_manager.is_stream_active:
            self.logger.error("serverCommand流未激活，无法发送命令")
            return False
        
        try:
            # 创建ServerCmdRequest消息
            import uuid
            msg_id = int(uuid.uuid4().hex[:8], 16)
            
            # 获取命令类型
            cmd_type = command_data.get('cmd_type', 'unknown')
            
            # 创建gRPC消息
            grpc_msg = robot_pb2.ServerCmdRequest(
                msg_id=msg_id,
                msg_time=int(time.time()),
                robot_id=self.robot_id,
                cmd_type=cmd_type
            )
            
            # 根据命令类型填充数据
            if cmd_type == 'task':
                task_data = command_data.get('task_data', {})
                grpc_msg.task_data.CopyFrom(robot_pb2.TaskCmdData(
                    task_id=task_data.get('task_id', ''),
                    task_type=task_data.get('task_type', 'delivery'),
                    priority=task_data.get('priority', 1),
                    start_point=task_data.get('start_point', ''),
                    end_point=task_data.get('end_point', ''),
                    operation_type=task_data.get('operation_type', 'pickup')
                ))
            elif cmd_type == 'mode':
                mode_data = command_data.get('mode_data', {})
                grpc_msg.mode_data.CopyFrom(robot_pb2.ModeCmdData(
                    mode=mode_data.get('mode', 'auto'),
                    operation_mode=mode_data.get('operation_mode', 'normal')
                ))
            elif cmd_type == 'joy':
                joy_data = command_data.get('joy_data', {})
                grpc_msg.joy_data.CopyFrom(robot_pb2.JoyCmdData(
                    linear_x=joy_data.get('linear_x', 0.0),
                    linear_y=joy_data.get('linear_y', 0.0),
                    angular_z=joy_data.get('angular_z', 0.0)
                ))
            elif cmd_type == 'charge':
                charge_data = command_data.get('charge_data', {})
                grpc_msg.charge_data.CopyFrom(robot_pb2.ChargeCmdData(
                    action=charge_data.get('action', 'start')
                ))
            
            # 发送消息
            success = self.server_command_manager.send_message(grpc_msg)
            if success:
                self.logger.info(f"服务端命令已发送: msg_id={msg_id}, cmd_type={cmd_type}")
            else:
                self.logger.error(f"服务端命令发送失败: msg_id={msg_id}, cmd_type={cmd_type}")
            
            return success
            
        except Exception as e:
            self.logger.error(f"发送服务端命令异常: {e}")
            return False

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
        'robot_id': "ROBOT_001",
        'robot_config': {
            'success_rate': 0.95,
            'latency': 1,
            'max_error_rate': 0.001
        }
    }
    
    robot_system = RobotControlSystem(config, use_mock=True)
    try:
        # 启动系统
        robot_system.start()
        # robot_system._simulate_receive_command()
        # 运行一段时间
        time.sleep(6000)
        
    except KeyboardInterrupt:
        print("\n收到中断信号，关闭系统...")
    finally:
        # 关闭系统
        robot_system.shutdown()
