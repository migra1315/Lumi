#!/usr/bin/env python3
"""
简易gRPC测试服务器
用于测试机器人控制系统的gRPC通信功能
"""

import logging
import queue
import threading
import time
import grpc
from concurrent import futures

import gRPC.RobotService_pb2 as robot_service_pb2
import gRPC.RobotService_pb2_grpc as robot_service_pb2_grpc

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class RobotServiceServicer(robot_service_pb2_grpc.RobotServiceServicer):
    """gRPC服务实现类"""

    def __init__(self):
        self.client_connections = []
        self.command_counter = 0

        # 手动命令队列：用于存储键盘输入触发的命令
        self.manual_command_queue = queue.Queue()

        # 自动发送开关（可通过键盘输入切换）
        self.auto_send_enabled = False  # 默认关闭自动发送

        logger.info("【system】RobotServiceServicer初始化")
        logger.info("【system】键盘控制说明:")
        logger.info("  1 - 发送充电命令 (CHARGE_CMD)")
        logger.info("  2 - 发送机器人模式命令 (ROBOT_MODE_CMD)")
        logger.info("  3 - 发送任务命令 (TASK_CMD)")
        logger.info("  4 - 发送摇杆控制命令 (JOY_CONTROL_CMD)")
        logger.info("  a - 切换自动发送模式 (当前: 关闭)")
        logger.info("  q - 退出服务器")
    
    def clientUpload(self, request_iterator, context):
        """双向流式通信: 接收机器人上传的状态、设备数据等
        
        Args:
            request_iterator: 客户端请求迭代器
            context: gRPC上下文
            
        Returns:
            响应迭代器
        """
        client_id = context.peer()
        logger.info(f"【clientUpload】新客户端连接: {client_id}")
        self.client_connections.append(client_id)
        
        try:
            # 接收客户端消息
            for request in request_iterator:
                self._handle_client_message(request, client_id)
                # 发送响应
                response = robot_service_pb2.RobotUploadResponse(
                    msg_id=request.msg_id,
                    msg_time=int(time.time() * 1000),
                    msg_type=request.msg_type,
                    robot_id=request.robot_id,
                    data_json=robot_service_pb2.ServerResponse(
                        code="0",
                        info="success"
                    )
                )
                yield response

        except Exception as e:
            logger.error(f"【clientUpload】处理请求时出错: {e}")
        finally:
            if client_id in self.client_connections:
                self.client_connections.remove(client_id)
            logger.info(f"【clientUpload】客户端断开连接: {client_id}")
    
    def serverCommand(self, request_iterator, context):
        """双向流式通信: 向机器人推送任务分配、服务指令
        
        Args:
            request_iterator: 客户端请求迭代器
            context: gRPC上下文
            
        Returns:
            响应迭代器
        """
        client_id = context.peer()
        logger.info(f"【serverCommand】新客户端连接: {client_id}")
        self.client_connections.append(client_id)
        
        # 用于存储客户端消息的队列
        client_messages = queue.Queue()
        
        def process_client_messages():
            """在单独的线程中处理客户端消息"""
            logger.info(f"【serverCommand】开始处理客户端消息: {client_id}")
            try:
                logger.info(f"【serverCommand】准备开始迭代请求: {client_id}")
                for request in request_iterator:
                    # 将消息放入队列供主线程处理
                    client_messages.put(request)
                    # 立即处理客户端消息
                    self._handle_client_stream_message(request, client_id)

            except Exception as e:
                logger.error(f"【serverCommand】处理客户端消息时出错: {e}")

            finally:
                # 当客户端断开连接时，放入一个特殊标记
                logger.info(f"【serverCommand】客户端消息处理结束: {client_id}")
                client_messages.put(None)

        # 启动客户端消息处理线程
        client_thread = threading.Thread(target=process_client_messages)
        client_thread.daemon = True
        client_thread.start()
        time.sleep(1)
        
        try:
            # 命令类型计数器，用于循环生成不同类型的命令
            command_type_counter = 0
            
            # 上次发送命令的时间
            last_send_time = time.time()

            # 主循环：定期发送命令并检查客户端消息
            while True:
                current_time = time.time()
                request = None

                # 优先处理手动命令队列
                try:
                    if not self.manual_command_queue.empty():
                        request = self.manual_command_queue.get_nowait()
                        logger.info(f"【手动命令】发送手动触发的命令")
                except queue.Empty:
                    pass

                # 如果没有手动命令且启用了自动发送，则定期自动发送
                if request is None and self.auto_send_enabled and (current_time - last_send_time >= 20):
                    # 根据计数器决定发送哪种类型的命令
                    command_type = command_type_counter % 4  # 0-3: charge, robot_mode, task, joy_control

                    if command_type == 0:
                        request = self.create_charge_command()
                    elif command_type == 1:
                        request = self.create_robot_mode_command()
                    elif command_type == 2:
                        request = self.create_task(log_prefix="【自动发送】")
                    else:
                        request = self.create_joy_control_command()

                    # 更新发送时间和命令类型计数器
                    last_send_time = current_time
                    command_type_counter += 1

                # 如果有命令需要发送，则发送
                if request is not None:
                    logger.debug(f"【serverCommand】准备发送命令: command_id={request.command_id}")
                    yield request
                
                # 检查客户端消息队列（非阻塞）
                try:
                    while not client_messages.empty():
                        client_msg = client_messages.get_nowait()
                        if client_msg is None:  # 客户端断开连接
                            logger.info(f"【serverCommand】检测到客户端断开连接: {client_id}")
                            return
                except queue.Empty:
                    pass

                # 检查上下文是否被取消
                if not context.is_active():
                    logger.info(f"【serverCommand】上下文被取消: {client_id}")
                    break
                
                # 短暂休眠，避免CPU占用过高
                time.sleep(0.1)

        except Exception as e:
            logger.error(f"【serverCommand】处理请求时出错: {e}")
        finally:
            if client_id in self.client_connections:
                self.client_connections.remove(client_id)
            logger.info(f"【serverCommand】客户端断开连接: {client_id}")
    
            # 创建一个线程来处理客户端消息

    # ==================== 命令创建辅助方法 ====================

    def create_charge_command(self):
        """创建充电命令"""
        current_time = time.time()
        self.command_counter += 1

        charge_cmd = robot_service_pb2.ChargeCmd(charge=True)

        request = robot_service_pb2.ServerStreamMessage(
            command_id=self.command_counter,
            command_time=int(current_time * 1000),
            command_type=robot_service_pb2.CmdType.CHARGE_CMD,
            robot_id=123456
        )
        request.charge_cmd.CopyFrom(charge_cmd)

        logger.info(f"【手动触发】创建充电命令: {self.command_counter}")
        return request

    def create_robot_mode_command(self, robot_mode=None):
        """创建机器人模式命令"""
        current_time = time.time()
        self.command_counter += 1

        if robot_mode is None:
            robot_mode = robot_service_pb2.RobotMode.INSPECTION

        robot_mode_cmd = robot_service_pb2.RobotModeCommand()
        robot_mode_cmd.robot_mode = robot_mode

        request = robot_service_pb2.ServerStreamMessage(
            command_id=self.command_counter,
            command_time=int(current_time * 1000),
            command_type=robot_service_pb2.CmdType.ROBOT_MODE_CMD,
            robot_id=123456
        )
        request.robot_mode_command.CopyFrom(robot_mode_cmd)

        logger.info(f"【手动触发】创建机器人模式命令: {self.command_counter}, mode={robot_service_pb2.RobotMode.Name(robot_mode)}")
        return request

    def create_joy_control_command(self, angular_velocity="0.5", linear_velocity="0.3"):
        """创建摇杆控制命令"""
        current_time = time.time()
        self.command_counter += 1

        joy_control_cmd = robot_service_pb2.JoyControlCmd(
            angular_velocity=angular_velocity,
            linear_velocity=linear_velocity
        )

        request = robot_service_pb2.ServerStreamMessage(
            command_id=self.command_counter,
            command_time=int(current_time * 1000),
            command_type=robot_service_pb2.CmdType.JOY_CONTROL_CMD,
            robot_id=123456
        )
        request.joy_control_cmd.CopyFrom(joy_control_cmd)

        logger.info(f"【手动触发】创建摇杆控制命令: {self.command_counter}")
        return request

    # ==================== Station和Task创建方法 ====================

    def create_station(self,
                       station_id: int,
                       sort: int,
                       name: str,
                       agv_marker: str,
                       robot_pos: list,
                       ext_pos: list,
                       operation_mode=None,
                       door_ip: str = "",
                       device_id: int = 0,
                       status=None,
                       max_retries: int = 3):
        """创建单个Station对象

        Args:
            station_id: 站点ID
            sort: 站点执行顺序
            name: 站点名称
            agv_marker: AGV导航点标识
            robot_pos: 机械臂归位位置列表 (6个值)
            ext_pos: 外部轴归位位置列表 (4个值)
            operation_mode: 操作模式 (OperationMode枚举，默认为NONE)
            door_ip: 门禁IP地址 (可选)
            device_id: 设备ID (可选)
            status: 站点状态 (StationTaskStatus枚举，默认为PENDING)
            max_retries: 最大重试次数 (默认3)

        Returns:
            robot_service_pb2.Station: Station对象
        """
        current_time = time.time()

        # 设置默认值
        if operation_mode is None:
            operation_mode = robot_service_pb2.OperationMode.OPERATION_MODE_NONE
        if status is None:
            status = robot_service_pb2.StationTaskStatus.STATION_TASK_STATUS_PENDING

        # 创建OperationConfig
        operation_config = robot_service_pb2.OperationConfig(
            operation_mode=operation_mode,
            door_ip=door_ip,
            device_id=device_id
        )

        # 创建StationConfig
        station_config = robot_service_pb2.StationConfig(
            station_id=station_id,
            sort=sort,
            name=name,
            agv_marker=agv_marker,
            robot_pos=robot_pos,
            ext_pos=ext_pos,
            operation_config=operation_config
        )

        # 创建Station
        station = robot_service_pb2.Station(
            station_config=station_config,
            status=status,
            generate_time=int(current_time * 1000),
            created_at=int(current_time * 1000),
            retry_count=0,
            max_retries=max_retries,
            error_message=""
        )

        return station

    def create_task(self,
                    task_id: int = None,
                    task_name: str = None,
                    station_list: list = None,
                    status=None,
                    robot_mode=None,
                    log_prefix=""):
        """创建Task任务

        Args:
            task_id: 任务ID (默认使用command_counter)
            task_name: 任务名称 (默认为task_{command_counter})
            station_list: Station对象列表 (如果为None则创建默认站点列表)
            status: 任务状态 (TaskStatus枚举，默认为PENDING)
            robot_mode: 机器人模式 (RobotMode枚举，默认为INSPECTION)
            log_prefix: 日志前缀 (默认为空)

        Returns:
            robot_service_pb2.ServerStreamMessage: 包含Task的服务端消息
        """
        current_time = time.time()
        self.command_counter += 1

        # 设置默认值
        if task_id is None:
            task_id = 123456
        if task_name is None:
            task_name = f"task_{self.command_counter}"
        if status is None:
            status = robot_service_pb2.TaskStatus.TASK_STATUS_PENDING
        if robot_mode is None:
            robot_mode = robot_service_pb2.RobotMode.INSPECTION

        # 如果没有提供station_list，创建默认站点列表
        if station_list is None:
            station_list = []
            # 创建4个默认测试站点
            agv_marker_list = ["marker_1", "marker_2", "marker_3","charge_point_1F_6010"]
            ext_pos_list = [[10, 0, 0, 0], [20, 0, 0, 0], [30, 0, 0, 0], [10, 0, 0, 0]]
            robot_pos_list=[[0, 30, 100, 0, 60, -90],
                            [0, 40, 90, 0, 60, -90],
                            [0, 50, 80, 0, 60, -90],
                            [0, 60, 70, 0, 60, -90]]
            operation_mode_list = [robot_service_pb2.OperationMode.OPERATION_MODE_NONE, 
                                   robot_service_pb2.OperationMode.OPERATION_MODE_SERVICE, 
                                   robot_service_pb2.OperationMode.OPERATION_MODE_NONE, 
                                   robot_service_pb2.OperationMode.OPERATION_MODE_SERVICE]
            for i in range(4):
                station = self.create_station(
                    station_id=1111 + i,
                    sort=i + 1,
                    name=f"测试站点{self.command_counter}_{i+1}",
                    agv_marker=agv_marker_list[i],  # 循环使用marker_1, marker_2, marker_3
                    robot_pos=robot_pos_list[i],
                    ext_pos=ext_pos_list[i],
                    operation_mode=operation_mode_list[i],
                    door_ip="192.168.1.100",
                    device_id=2222 + i
                )
                station_list.append(station)

        # 创建Task
        task = robot_service_pb2.Task(
            task_id=task_id,
            task_name=task_name,
            station_list=station_list,
            status=status,
            robot_mode=robot_mode,
            generate_time=int(current_time * 1000),
            created_at=int(current_time * 1000),
            error_message=""
        )

        # 创建ServerStreamMessage
        request = robot_service_pb2.ServerStreamMessage(
            command_id=self.command_counter,
            command_time=int(current_time * 1000),
            command_type=robot_service_pb2.CmdType.TASK_CMD,
            robot_id=123456
        )

        request.task_cmd.CopyFrom(task)

        log_msg = f"{log_prefix}创建任务命令: {self.command_counter}, task_id={task_id}, task_name={task_name}, stations={len(station_list)}"
        logger.info(log_msg if log_prefix else f"【手动触发】{log_msg}")

        return request
    
    # def _handle_cmd_response(self, request, client_id):
    #     """处理客户端上传的响应
    #     Args:
    #         request: 客户端请求
    #         client_id: 客户端ID
    #     """
    #     try:
    #         command_type = robot_service_pb2.CmdType.Name(request.command_type)
    #     except ValueError:
    #         command_type = f"未知类型({request.command_type})"
            
    #     logger.info(f"接收客户端 {client_id} 响应: {command_type} (command_id: {request.command_id})")
        
    #     # 记录响应数据
    #     if request.data_json:
    #         logger.info(f"响应数据: code={request.data_json.code}, info={request.data_json.info}")
    
    def _handle_client_message(self, request, client_id):
        """处理客户端上传的消息

        Args:
            request: 客户端请求(RobotUploadRequest)
            client_id: 客户端ID
        """
        try:
            # 获取消息类型名称
            try:
                msg_type = robot_service_pb2.MsgType.Name(request.msg_type)
            except ValueError:
                msg_type = f"未知类型({request.msg_type})"

            logger.info(f"【clientUpload】收到消息: {msg_type} (msg_id: {request.msg_id}, robot_id: {request.robot_id})")

            # 打印位置信息（如果有）
            if request.HasField('position_info'):
                pos = request.position_info
                logger.debug(f"  ├─ AGV位置: {list(pos.AGVPositionInfo)}")
                logger.debug(f"  ├─ 机械臂位置: {list(pos.ARMPositionInfo)}")
                logger.debug(f"  └─ 外部轴位置: {list(pos.EXTPositionInfo)}")

            # 打印任务信息（如果有）
            if request.HasField('task_info') and request.task_info.HasField('inspection_task_list'):
                task = request.task_info.inspection_task_list
                try:
                    task_status = robot_service_pb2.TaskStatus.Name(task.status)
                except ValueError:
                    task_status = f"未知({task.status})"
                logger.debug(f"  └─ 当前任务: ID={task.task_id}, 名称={task.task_name}, 状态={task_status}")

            # 根据消息类型处理数据
            if request.HasField('robot_status'):
                self._handle_robot_status(request.robot_status)
            elif request.HasField('device_data'):
                self._handle_device_data(request.device_data)
            elif request.HasField('environment_data'):
                self._handle_environment_data(request.environment_data)
            elif request.HasField('arrive_service_point'):
                self._handle_arrive_service_point(request.arrive_service_point)
            else:
                logger.warning(f"【clientUpload】消息中无有效数据字段")

        except Exception as e:
            logger.error(f"【clientUpload】处理客户端消息失败: {e}")
    
    def _handle_robot_status(self, robot_status):
        """处理机器人状态消息

        Args:
            robot_status: 机器人状态(RobotStatusUpload)
        """
        try:
            # 获取移动状态名称
            try:
                move_status = robot_service_pb2.MoveStatus.Name(robot_status.system_status.move_status)
            except (ValueError, AttributeError):
                move_status = f"未知({robot_status.system_status.move_status})"

            # 获取电池信息
            battery_percent = robot_status.battery_info.power_percent if robot_status.HasField('battery_info') else 0.0
            charge_status = robot_status.battery_info.charge_status if robot_status.HasField('battery_info') else "未知"

            # 获取系统状态
            is_connected = robot_status.system_status.is_connected if robot_status.HasField('system_status') else False
            soft_estop = robot_status.system_status.soft_estop_status if robot_status.HasField('system_status') else False
            hard_estop = robot_status.system_status.hard_estop_status if robot_status.HasField('system_status') else False
            estop = robot_status.system_status.estop_status if robot_status.HasField('system_status') else False

            logger.info(f"【clientUpload】【机器人状态】")
            logger.info(f"  ├─ 电量: {battery_percent:.1f}%")
            logger.info(f"  ├─ 充电状态: {charge_status}")
            logger.info(f"  ├─ 移动状态: {move_status}")
            logger.info(f"  ├─ 连接状态: {'已连接' if is_connected else '未连接'}")
            logger.info(f"  ├─ 软急停: {'触发' if soft_estop else '正常'}")
            logger.info(f"  ├─ 硬急停: {'触发' if hard_estop else '正常'}")
            logger.info(f"  └─ 急停状态: {'触发' if estop else '正常'}")

            # 如果有错误信息，也打印出来
            if robot_status.HasField('error_info') and robot_status.error_info.message:
                logger.warning(f"     └─ 错误信息: [{robot_status.error_info.level}] {robot_status.error_info.message} (code={robot_status.error_info.code})")

        except Exception as e:
            logger.error(f"【clientUpload】处理机器人状态消息失败: {e}")
    
    def _handle_device_data(self, device_data):
        """处理设备数据消息

        Args:
            device_data: 设备数据(DeviceDataUpload)
        """
        try:
            device_id = device_data.device_info.device_id if device_data.HasField('device_info') else 0
            data_type = device_data.device_info.data_type if device_data.HasField('device_info') else "未知"
            image_count = len(device_data.device_info.image_base64) if device_data.HasField('device_info') else 0

            logger.info(f"【clientUpload】【设备数据】")
            logger.info(f"  ├─ 设备ID: {device_id}")
            logger.info(f"  ├─ 数据类型: {data_type}")
            logger.info(f"  └─ 图片数量: {image_count}")

            # 如果有图片数据，打印每张图片的大小
            if image_count > 0:
                for idx, img_base64 in enumerate(device_data.device_info.image_base64):
                    logger.info(f"     └─ 图片{idx+1}大小: {len(img_base64)} 字符")

        except Exception as e:
            logger.error(f"【clientUpload】处理设备数据消息失败: {e}")

    def _handle_environment_data(self, environment_data):
        """处理环境数据消息

        Args:
            environment_data: 环境数据(EnvironmentDataUpload)
        """
        try:
            if not environment_data.HasField('sensor_data'):
                logger.warning(f"【clientUpload】环境数据消息中无传感器数据")
                return

            env_info = environment_data.sensor_data

            logger.info(f"【clientUpload】【环境数据】")
            logger.info(f"  ├─ 温度: {env_info.temperature:.1f}°C")
            logger.info(f"  ├─ 湿度: {env_info.humidity:.1f}%")
            logger.info(f"  ├─ 氧气(O2): {env_info.oxygen:.1f}")
            logger.info(f"  ├─ 二氧化碳(CO2): {env_info.carbonDioxide:.1f}")
            logger.info(f"  ├─ PM2.5: {env_info.pm25:.1f}")
            logger.info(f"  ├─ PM10: {env_info.pm10:.1f}")
            logger.info(f"  ├─ eTVOC: {env_info.etvoc:.1f}")
            logger.info(f"  └─ 噪音: {env_info.noise:.1f}dB")

        except Exception as e:
            logger.error(f"【clientUpload】处理环境数据消息失败: {e}")

    def _handle_arrive_service_point(self, arrive_service_point):
        """处理到达服务点消息

        Args:
            arrive_service_point: 到达服务点数据(ArriveServicePointUpload)
        """
        try:
            is_arrive = arrive_service_point.is_arrive
            logger.info(f"【clientUpload】【到达服务点】: {'✓ 已到达' if is_arrive else '✗ 未到达'}")

        except Exception as e:
            logger.error(f"【clientUpload】处理到达服务点消息失败: {e}")

    def _handle_client_stream_message(self, request, client_id):
        """处理客户端通过serverCommand流发送的消息

        Args:
            request: ClientStreamMessage对象
            client_id: 客户端ID
        """
        try:
            # 获取消息类型名称
            try:
                command_type = robot_service_pb2.ClientMessageType.Name(request.command_type)
            except ValueError:
                command_type = f"未知类型({request.command_type})"

            logger.info(f"【serverCommand】接收客户端消息: {command_type} (command_id: {request.command_id}, client: {client_id})")

            # 根据消息类型处理
            if request.command_type == robot_service_pb2.ClientMessageType.HEARTBEAT:
                logger.info(f"【serverCommand】收到心跳消息")

            elif request.command_type == robot_service_pb2.ClientMessageType.COMMAND_RESPONSE:
                if request.HasField('response_info'):
                    self._handle_command_response(request.response_info, client_id)

            elif request.command_type == robot_service_pb2.ClientMessageType.SET_MARKER_RESPONSE:
                if request.HasField('position_info'):
                    logger.info(f"【serverCommand】收到设置标记点响应")

            elif request.command_type == robot_service_pb2.ClientMessageType.COMMAND_STATUS_UPDATE:
                if request.HasField('command_status'):
                    self._handle_command_status_update(request.command_status, client_id)

            elif request.command_type == robot_service_pb2.ClientMessageType.TASK_PROGRESS_UPDATE:
                if request.HasField('task_progress'):
                    self._handle_task_progress_update(request.task_progress, client_id)

            elif request.command_type == robot_service_pb2.ClientMessageType.OPERATION_RESULT:
                if request.HasField('operation_result'):
                    self._handle_operation_result(request.operation_result, client_id)

            else:
                logger.warning(f"【serverCommand】未知的ClientMessageType: {request.command_type}")

        except Exception as e:
            logger.error(f"【serverCommand】处理客户端流消息失败: {e}")

    def _handle_command_response(self, response_info, client_id):
        """处理命令响应

        Args:
            response_info: ServerResponse对象
            client_id: 客户端ID
        """
        logger.info(f"【serverCommand】命令响应 [{client_id}]: code={response_info.code}, info={response_info.info}")

    def _handle_command_status_update(self, command_status, client_id):
        """处理命令状态更新

        Args:
            command_status: CommandStatusUpdate对象
            client_id: 客户端ID
        """
        try:
            # 获取命令类型和状态的名称
            try:
                cmd_type = robot_service_pb2.CmdType.Name(command_status.command_type)
            except (ValueError, AttributeError):
                cmd_type = f"未知({command_status.command_type})"

            try:
                status = robot_service_pb2.CommandStatus.Name(command_status.status)
            except (ValueError, AttributeError):
                status = f"未知({command_status.status})"

            # 根据状态选择日志级别和颜色标记
            if command_status.status == robot_service_pb2.CommandStatus.COMMAND_STATUS_COMPLETED:
                status_icon = "✓"
                log_func = logger.info
            elif command_status.status == robot_service_pb2.CommandStatus.COMMAND_STATUS_FAILED:
                status_icon = "✗"
                log_func = logger.warning
            elif command_status.status == robot_service_pb2.CommandStatus.COMMAND_STATUS_RUNNING:
                status_icon = "▶"
                log_func = logger.info
            elif command_status.status == robot_service_pb2.CommandStatus.COMMAND_STATUS_RETRYING:
                status_icon = "⟳"
                log_func = logger.warning
            else:
                status_icon = "◎"
                log_func = logger.info

            log_func(f"【serverCommand】【命令状态更新】{status_icon}")
            log_func(f"  ├─ 命令ID: {command_status.command_id}")
            log_func(f"  ├─ 命令类型: {cmd_type}")
            log_func(f"  ├─ 状态: {status}")
            log_func(f"  ├─ 消息: {command_status.message}")
            log_func(f"  ├─ 重试次数: {command_status.retry_count}")
            log_func(f"  └─ 时间戳: {command_status.timestamp}")

        except Exception as e:
            logger.error(f"【serverCommand】处理命令状态更新失败: {e}")

    def _handle_task_progress_update(self, task_progress, client_id):
        """处理任务进度更新

        Args:
            task_progress: TaskProgressUpdate对象
            client_id: 客户端ID
        """
        try:
            # 获取任务状态和站点状态的名称
            try:
                task_status = robot_service_pb2.TaskStatus.Name(task_progress.task_status)
            except (ValueError, AttributeError):
                task_status = f"未知({task_progress.task_status})"

            try:
                station_status = robot_service_pb2.StationTaskStatus.Name(task_progress.current_station_status)
            except (ValueError, AttributeError):
                station_status = f"未知({task_progress.current_station_status})"

            # 计算进度百分比
            progress_percent = 0
            if task_progress.total_stations > 0:
                progress_percent = (task_progress.completed_stations / task_progress.total_stations) * 100

            logger.info(f"【serverCommand】【任务进度更新】")
            logger.info(f"  ├─ 任务ID: {task_progress.task_id}")
            logger.info(f"  ├─ 任务名称: {task_progress.task_name}")
            logger.info(f"  ├─ 任务状态: {task_status}")
            logger.info(f"  ├─ 进度: {task_progress.completed_stations}/{task_progress.total_stations} ({progress_percent:.1f}%)")
            logger.info(f"  ├─ 失败站点: {task_progress.failed_stations}")

            if task_progress.current_station_id > 0:
                logger.info(f"  ├─ 当前站点ID: {task_progress.current_station_id}")
                logger.info(f"  ├─ 当前站点名称: {task_progress.current_station_name}")
                logger.info(f"  ├─ 当前站点状态: {station_status}")

                # 如果有执行阶段和详细进度信息
                if task_progress.current_station_phase:
                    logger.info(f"  ├─ 执行阶段: {task_progress.current_station_phase}")
                if task_progress.current_station_detail:
                    logger.info(f"  ├─ 详细进度: {task_progress.current_station_detail}")

            logger.info(f"  ├─ 消息: {task_progress.message}")
            logger.info(f"  └─ 时间戳: {task_progress.timestamp}")

        except Exception as e:
            logger.error(f"【serverCommand】处理任务进度更新失败: {e}")

    def _handle_operation_result(self, operation_result, client_id):
        """处理操作结果

        Args:
            operation_result: OperationResult对象
            client_id: 客户端ID
        """
        try:
            # 获取操作模式和状态的名称
            try:
                operation_mode = robot_service_pb2.OperationMode.Name(operation_result.operation_mode)
            except (ValueError, AttributeError):
                operation_mode = f"未知({operation_result.operation_mode})"

            try:
                operation_status = robot_service_pb2.OperationStatus.Name(operation_result.status)
            except (ValueError, AttributeError):
                operation_status = f"未知({operation_result.status})"

            # 根据操作状态选择日志级别
            if operation_result.status == robot_service_pb2.OperationStatus.OPERATION_STATUS_SUCCESS:
                status_icon = "✓"
                log_func = logger.info
            elif operation_result.status == robot_service_pb2.OperationStatus.OPERATION_STATUS_FAILED:
                status_icon = "✗"
                log_func = logger.warning
            elif operation_result.status == robot_service_pb2.OperationStatus.OPERATION_STATUS_TIMEOUT:
                status_icon = "⏱"
                log_func = logger.warning
            elif operation_result.status == robot_service_pb2.OperationStatus.OPERATION_STATUS_ABORT:
                status_icon = "⊗"
                log_func = logger.warning
            else:
                status_icon = "◎"
                log_func = logger.info

            log_func(f"【serverCommand】【操作结果】{status_icon}")
            log_func(f"  ├─ 任务ID: {operation_result.task_id}")
            log_func(f"  ├─ 站点ID: {operation_result.station_id}")
            log_func(f"  ├─ 操作模式: {operation_mode}")
            log_func(f"  ├─ 操作状态: {operation_status}")
            log_func(f"  ├─ 消息: {operation_result.message}")

            # 根据操作模式显示相应的信息
            if operation_result.operation_mode in [robot_service_pb2.OperationMode.OPERATION_MODE_OPEN_DOOR,
                                                     robot_service_pb2.OperationMode.OPERATION_MODE_CLOSE_DOOR]:
                if operation_result.door_ip:
                    log_func(f"  ├─ 门禁IP: {operation_result.door_ip}")

            if operation_result.device_id > 0:
                log_func(f"  ├─ 设备ID: {operation_result.device_id}")

            if operation_result.operation_mode == robot_service_pb2.OperationMode.OPERATION_MODE_CAPTURE:
                image_count = len(operation_result.image_base64)
                log_func(f"  ├─ 图像数量: {image_count}")
                # 如果有图像数据，打印图像大小信息
                if image_count > 0:
                    for idx, img_base64 in enumerate(operation_result.image_base64):
                        log_func(f"  │  └─ 图像{idx+1}大小: {len(img_base64)} 字符")

            log_func(f"  ├─ 耗时: {operation_result.duration:.2f}秒")
            log_func(f"  └─ 时间戳: {operation_result.timestamp}")

        except Exception as e:
            logger.error(f"【serverCommand】处理操作结果失败: {e}")

def keyboard_input_handler(servicer, stop_event):
    """键盘输入处理线程

    Args:
        servicer: RobotServiceServicer实例
        stop_event: 停止事件
    """
    logger.info("【键盘监听】键盘输入监听线程已启动")
    logger.info("【键盘监听】输入 1/2/3/4 发送命令, 'a' 切换自动发送, 'q' 退出")

    import sys
    import select

    while not stop_event.is_set():
        try:
            # Windows和Linux的键盘输入处理不同
            if sys.platform == 'win32':
                # Windows下使用msvcrt
                import msvcrt
                if msvcrt.kbhit():
                    key = msvcrt.getch().decode('utf-8').lower()
                else:
                    time.sleep(0.1)
                    continue
            else:
                # Linux/Mac使用select
                if select.select([sys.stdin], [], [], 0.1)[0]:
                    key = sys.stdin.read(1).lower()
                else:
                    continue

            # 处理按键
            if key == '1':
                logger.info("【键盘输入】触发: 充电命令 (CHARGE_CMD)")
                cmd = servicer.create_charge_command()
                servicer.manual_command_queue.put(cmd)

            elif key == '2':
                logger.info("【键盘输入】触发: 机器人模式命令 (ROBOT_MODE_CMD)")
                cmd = servicer.create_robot_mode_command()
                servicer.manual_command_queue.put(cmd)

            elif key == '3':
                logger.info("【键盘输入】触发: 任务命令 (TASK_CMD)")
                cmd = servicer.create_task()
                servicer.manual_command_queue.put(cmd)

            elif key == '4':
                logger.info("【键盘输入】触发: 摇杆控制命令 (JOY_CONTROL_CMD)")
                cmd = servicer.create_joy_control_command()
                servicer.manual_command_queue.put(cmd)

            elif key == 'a':
                servicer.auto_send_enabled = not servicer.auto_send_enabled
                status = "开启" if servicer.auto_send_enabled else "关闭"
                logger.info(f"【键盘输入】自动发送模式已{status}")

            elif key == 'q':
                logger.info("【键盘输入】收到退出信号")
                stop_event.set()
                break

        except Exception as e:
            logger.error(f"【键盘监听】处理键盘输入异常: {e}")
            time.sleep(0.1)

    logger.info("【键盘监听】键盘输入监听线程已退出")


def serve():
    """启动gRPC服务器

    Returns:
        启动的服务器实例和servicer实例的元组
    """
    # 创建servicer实例（需要在外部保持引用，以便键盘监听线程访问）
    servicer = RobotServiceServicer()

    # 创建服务器，使用10个线程处理请求
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))

    # 注册服务
    robot_service_pb2_grpc.add_RobotServiceServicer_to_server(
        servicer, server
    )

    # 监听端口
    server_address = '[::]:50051'
    server.add_insecure_port(server_address)

    # 启动服务器
    server.start()
    logger.info(f"【system】gRPC测试服务器已启动，监听地址: {server_address}")

    return server, servicer


if __name__ == '__main__':
    """
    使用示例：如何使用 create_station() 和 create_task() 函数

    # 示例1: 使用默认参数创建任务（4个默认站点）
    servicer = RobotServiceServicer()
    task_request = servicer.create_task()

    # 示例2: 创建自定义站点列表的任务
    servicer = RobotServiceServicer()

    # 创建站点1 - 开门操作
    station1 = servicer.create_station(
        station_id=1001,
        sort=1,
        name="一楼配电室",
        agv_marker="marker_1",
        robot_pos=[0, 30, 100, 0, 60, -90],
        ext_pos=[10, 0, 0, 0],
        operation_mode=robot_service_pb2.OperationMode.OPERATION_MODE_OPEN_DOOR,
        door_ip="192.168.1.100",
        device_id=101
    )

    # 创建站点2 - 拍照操作
    station2 = servicer.create_station(
        station_id=1002,
        sort=2,
        name="二楼机房",
        agv_marker="marker_2",
        robot_pos=[0, 45, 90, 0, 45, -90],
        ext_pos=[20, 0, 0, 0],
        operation_mode=robot_service_pb2.OperationMode.OPERATION_MODE_CAPTURE,
        device_id=102
    )

    # 创建站点3 - 关门操作
    station3 = servicer.create_station(
        station_id=1003,
        sort=3,
        name="一楼配电室",
        agv_marker="marker_1",
        robot_pos=[0, 30, 100, 0, 60, -90],
        ext_pos=[10, 0, 0, 0],
        operation_mode=robot_service_pb2.OperationMode.OPERATION_MODE_CLOSE_DOOR,
        door_ip="192.168.1.100",
        device_id=101
    )

    # 将站点组合成任务
    task_request = servicer.create_task(
        task_id=999,
        task_name="巡检任务_A区",
        station_list=[station1, station2, station3],
        robot_mode=robot_service_pb2.RobotMode.INSPECTION
    )

    # 示例3: 创建服务模式任务
    station_service = servicer.create_station(
        station_id=2001,
        sort=1,
        name="充电桩",
        agv_marker="charge_point_1F_6010",
        robot_pos=[0, 0, 0, 0, 0, 0],
        ext_pos=[0, 0, 0, 0],
        operation_mode=robot_service_pb2.OperationMode.OPERATION_MODE_SERVICE
    )

    task_request = servicer.create_task(
        task_id=888,
        task_name="充电任务",
        station_list=[station_service],
        robot_mode=robot_service_pb2.RobotMode.CHARGE
    )
    """

    # 启动服务器
    server, servicer = serve()

    # 创建停止事件
    stop_event = threading.Event()

    # 启动键盘输入监听线程
    keyboard_thread = threading.Thread(
        target=keyboard_input_handler,
        args=(servicer, stop_event),
        daemon=True
    )
    keyboard_thread.start()

    try:
        # 保持服务器运行，直到收到停止信号
        while not stop_event.is_set():
            time.sleep(0.5)

    except KeyboardInterrupt:
        logger.info("【system】收到中断信号 (Ctrl+C)，关闭服务器...")
        stop_event.set()

    finally:
        # 等待键盘线程退出
        keyboard_thread.join(timeout=2.0)

        # 关闭服务器
        logger.info("【system】正在关闭服务器...")
        server.stop(0)
        logger.info("【system】服务器已关闭")