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
        logger.info("RobotServiceServicer初始化")
    
    def clientUpload(self, request_iterator, context):
        """双向流式通信: 接收机器人上传的状态、设备数据等
        
        Args:
            request_iterator: 客户端请求迭代器
            context: gRPC上下文
            
        Returns:
            响应迭代器
        """
        client_id = context.peer()
        logger.info(f"新客户端连接到 clientUpload: {client_id}")
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
            logger.error(f"处理 clientUpload 请求时出错: {e}")
        finally:
            if client_id in self.client_connections:
                self.client_connections.remove(client_id)
            logger.info(f"客户端断开 clientUpload 连接: {client_id}")
    
    def serverCommand(self, request_iterator, context):
        """双向流式通信: 向机器人推送任务分配、服务指令
        
        Args:
            request_iterator: 客户端请求迭代器
            context: gRPC上下文
            
        Returns:
            响应迭代器
        """
        client_id = context.peer()
        logger.info(f"新客户端连接到 serverCommand: {client_id}")
        self.client_connections.append(client_id)
        
        # 用于存储客户端消息的队列
        client_messages = queue.Queue()
        
        # 创建一个线程来处理客户端消息
        def process_client_messages():
            """在单独的线程中处理客户端消息"""
            try:
                for request in request_iterator:
                    # 将消息放入队列供主线程处理
                    client_messages.put(request)
                    
                    # 可以在这里立即处理客户端消息
                    self._handle_cmd_response(request, client_id)
                    
            except Exception as e:
                logger.error(f"处理客户端消息时出错: {e}")
            finally:
                # 当客户端断开连接时，放入一个特殊标记
                client_messages.put(None)
        
        # 启动客户端消息处理线程
        client_thread = threading.Thread(target=process_client_messages)
        client_thread.daemon = True
        client_thread.start()
        
        try:
            # 命令类型计数器，用于循环生成不同类型的命令
            command_type_counter = 0
            
            # 上次发送命令的时间
            last_send_time = time.time()
            
            # 主循环：定期发送命令并检查客户端消息
            while True:
                current_time = time.time()
                
                # 检查是否需要发送命令（每5秒发送一次）
                if current_time - last_send_time >= 5:
                    self.command_counter += 1
                    
                    # 根据计数器决定发送哪种类型的命令
                    command_type = command_type_counter % 4  # 0-3: robot_mode, task, joy_control, charge
                    
                    if command_type == 0:
                        # 创建RobotModeCmd
                        request = robot_service_pb2.ServerCmdRequest(
                            command_id=self.command_counter,
                            command_time=int(current_time * 1000),
                            command_type=robot_service_pb2.CmdType.ROBOT_MODE_CMD,
                            robot_id="ROBOT_001"
                        )
                        # 创建RobotModeCmd对象
                        robot_mode_cmd = robot_service_pb2.RobotModeCmd()
                        robot_mode_cmd.robot_mode = robot_service_pb2.RobotMode.INSPECTION
                        
                        # 设置oneof字段
                        request.robot_mode_command.CopyFrom(robot_mode_cmd)
                        
                        # 验证字段是否设置成功
                        if not request.HasField('robot_mode_command'):
                            logger.error("RobotModeCommand字段设置失败")
                        else:
                            logger.info(f"向客户端发送RobotModeCmd: {self.command_counter}, mode={robot_service_pb2.RobotMode.Name(robot_mode_cmd.robot_mode)}")
                        
                    elif command_type == 1:
                        # 创建Task命令（Station类型）
                        # 首先创建OperationConfig
                        operation_config = robot_service_pb2.OperationConfig(
                            operation_mode=robot_service_pb2.OperationMode.OPERATION_MODE_CAPTURE,
                            door_ip="192.168.1.100",
                            device_id=f"device_{self.command_counter}"
                        )
                        
                        # 创建StationConfig
                        station_config = robot_service_pb2.StationConfig(
                            station_id=f"station_{self.command_counter}",
                            sort=1,
                            name=f"测试站点{self.command_counter}",
                            agv_marker=f"marker_{self.command_counter}",
                            robot_pos=[1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
                            ext_pos=[1.0, 2.0, 3.0, 4.0],
                            operation_config=operation_config
                        )
                        
                        # 创建Station（注意：ServerCmdRequest的task_cmd字段期望的是Station类型）
                        station = robot_service_pb2.Station(
                            station_config=station_config,
                            status=robot_service_pb2.StationTaskStatus.STATION_TASK_STATUS_PENDING,
                            generate_time=int(current_time * 1000),
                            created_at=int(current_time * 1000),
                            retry_count=0,
                            max_retries=3,
                            error_message=""
                        )
                        
                        request = robot_service_pb2.ServerCmdRequest(
                            command_id=self.command_counter,
                            command_time=int(current_time * 1000),
                            command_type=robot_service_pb2.CmdType.TASK_CMD,
                            robot_id="ROBOT_001"
                        )
                        
                        # 设置task_cmd字段（注意：这里是station对象，不是task对象）
                        request.task_cmd.CopyFrom(station)
                        logger.info(f"向客户端发送Task命令: {self.command_counter}, station_id={station_config.station_id}")
                        
                    elif command_type == 2:
                        # 创建JoyControlCmd
                        joy_control_cmd = robot_service_pb2.JoyControlCmd(
                            angular_velocity="0.5",
                            linear_velocity="0.3"
                        )
                        
                        request = robot_service_pb2.ServerCmdRequest(
                            command_id=self.command_counter,
                            command_time=int(current_time * 1000),
                            command_type=robot_service_pb2.CmdType.JOY_CONTROL_CMD,
                            robot_id="ROBOT_001"
                        )
                        
                        # 设置joy_control_cmd字段
                        request.joy_control_cmd.CopyFrom(joy_control_cmd)
                        logger.info(f"向客户端发送JoyControlCmd: {self.command_counter}")
                        
                    else:  # command_type == 3
                        # 创建RobotModeCmd - 充电模式
                        request = robot_service_pb2.ServerCmdRequest(
                            command_id=self.command_counter,
                            command_time=int(current_time * 1000),
                            command_type=robot_service_pb2.CmdType.ROBOT_MODE_CMD,
                            robot_id="ROBOT_001"
                        )
                        
                        # 创建RobotModeCmd对象 - 充电模式
                        robot_mode_cmd = robot_service_pb2.RobotModeCmd()
                        robot_mode_cmd.robot_mode = robot_service_pb2.RobotMode.CHARGE
                        
                        # 设置oneof字段
                        request.robot_mode_command.CopyFrom(robot_mode_cmd)
                        logger.info(f"向客户端发送充电命令: {self.command_counter}")
                    
                    # 打印调试信息
                    logger.debug(f"准备发送命令: {request}")
                    
                    # 发送请求
                    yield request
                    
                    # 更新发送时间和命令类型计数器
                    last_send_time = current_time
                    command_type_counter += 1
                
                # 检查客户端消息队列（非阻塞）
                try:
                    while not client_messages.empty():
                        client_msg = client_messages.get_nowait()
                        if client_msg is None:  # 客户端断开连接
                            logger.info(f"检测到客户端断开连接: {client_id}")
                            return
                except queue.Empty:
                    pass
                
                # 检查上下文是否被取消
                if not context.is_active():
                    logger.info(f"上下文被取消: {client_id}")
                    break
                
                # 短暂休眠，避免CPU占用过高
                time.sleep(0.1)
                    
        except Exception as e:
            logger.error(f"处理serverCommand请求时出错: {e}")
        finally:
            if client_id in self.client_connections:
                self.client_connections.remove(client_id)
            logger.info(f"客户端断开serverCommand连接: {client_id}")
    
    def _handle_cmd_response(self, request, client_id):
        """处理客户端上传的响应
        Args:
            request: 客户端请求
            client_id: 客户端ID
        """
        try:
            command_type = robot_service_pb2.CmdType.Name(request.command_type)
        except ValueError:
            command_type = f"未知类型({request.command_type})"
            
        logger.info(f"接收客户端 {client_id} 响应: {command_type} (command_id: {request.command_id})")
        
        # 记录响应数据
        if request.data_json:
            logger.info(f"响应数据: code={request.data_json.code}, info={request.data_json.info}")
    
    def _handle_client_message(self, request, client_id):
        """处理客户端上传的消息
        
        Args:
            request: 客户端请求
            client_id: 客户端ID
        """
        try:
            msg_type = robot_service_pb2.MsgType.Name(request.msg_type)
        except ValueError:
            msg_type = f"未知类型({request.msg_type})"
            
        logger.info(f"接收客户端 {client_id} 消息: {msg_type} (msg_id: {request.msg_id}, robot_id: {request.robot_id})")
        
        # 根据消息类型处理
        if request.HasField('robot_status'):
            self._handle_robot_status(request.robot_status)
        elif request.HasField('device_data'):
            self._handle_device_data(request.device_data)
        elif request.HasField('environment_data'):
            self._handle_environment_data(request.environment_data)
        elif request.HasField('arrive_service_point'):
            self._handle_arrive_service_point(request.arrive_service_point)
        else:
            logger.warning(f"未知的消息类型或数据字段未设置")
    
    def _handle_robot_status(self, robot_status):
        """处理机器人状态消息
        
        Args:
            robot_status: 机器人状态
        """
        try:
            move_status = robot_service_pb2.MoveStatus.Name(robot_status.system_status.move_status)
        except ValueError:
            move_status = f"未知({robot_status.system_status.move_status})"
            
        logger.info(f"机器人状态: "
                    f"电量={robot_status.battery_info.power_percent:.1f}%, "
                    f"充电状态={robot_status.battery_info.charge_status}, "
                    f"移动状态={move_status}, "
                    f"连接状态={'已连接' if robot_status.system_status.is_connected else '未连接'}")
    
    def _handle_device_data(self, device_data):
        """处理设备数据消息
        
        Args:
            device_data: 设备数据
        """
        logger.info(f"设备数据: "
                    f"设备ID={device_data.device_info.device_id}, "
                    f"数据类型={device_data.device_info.data_type}, "
                    f"图片数量={len(device_data.device_info.image_base64)}")
    
    def _handle_environment_data(self, environment_data):
        """处理环境数据消息
        
        Args:
            environment_data: 环境数据
        """
        env_info = environment_data.environment_info
        logger.info(f"环境数据: "
                    f"温度={env_info.temperature:.1f}°C, "
                    f"湿度={env_info.humidity:.1f}%, "
                    f"PM2.5={env_info.pm25:.1f}, "
                    f"噪音={env_info.noise:.1f}dB")
    
    def _handle_arrive_service_point(self, arrive_service_point):
        """处理到达服务点消息
        
        Args:
            arrive_service_point: 到达服务点数据
        """
        arrive_info = arrive_service_point.arrive_service_point_info
        logger.info(f"到达服务点: {'已到达' if arrive_info.is_arrive else '未到达'}")

def serve():
    """启动gRPC服务器
    
    Returns:
        启动的服务器实例
    """
    # 创建服务器，使用10个线程处理请求
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    
    # 注册服务
    robot_service_pb2_grpc.add_RobotServiceServicer_to_server(
        RobotServiceServicer(), server
    )
    
    # 监听端口
    server_address = '[::]:50051'
    server.add_insecure_port(server_address)
    
    # 启动服务器
    server.start()
    logger.info(f"gRPC测试服务器已启动，监听地址: {server_address}")
    
    return server

if __name__ == '__main__':
    # 启动服务器
    server = serve()
    
    try:
        # 保持服务器运行
        while True:
            time.sleep(86400)  # 一天
    except KeyboardInterrupt:
        logger.info("收到中断信号，关闭服务器...")
        server.stop(0)
        logger.info("服务器已关闭")