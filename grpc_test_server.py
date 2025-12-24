#!/usr/bin/env python3
"""
简易gRPC测试服务器
用于测试机器人控制系统的gRPC通信功能
"""

import logging
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
            logger.error(f"处理clientUpload请求时出错: {e}")
        finally:
            if client_id in self.client_connections:
                self.client_connections.remove(client_id)
            logger.info(f"客户端断开clientUpload连接: {client_id}")
    
    def serverCommand(self, request_iterator, context):
        """双向流式通信: 向机器人推送任务分配、服务指令
        
        Args:
            request_iterator: 客户端请求迭代器
            context: gRPC上下文
            
        Returns:
            响应迭代器
        """
        client_id = context.peer()
        logger.info(f"新客户端连接到serverCommand: {client_id}")
        self.client_connections.append(client_id)
        for request in request_iterator:
            logger.info(f"接收客户端{client_id}消息: {request}")
        try:
            # 定期发送测试命令
            while True:
                # 每30秒发送一次测试命令
                time.sleep(10)
                self.command_counter += 1
                
                # 创建任务命令
                response = robot_service_pb2.RobotUploadResponse(
                    msg_id=self.command_counter,
                    msg_time=int(time.time() * 1000),
                    msg_type=robot_service_pb2.MsgType.ROBOT_STATUS,
                    robot_id=1,
                    data_json=robot_service_pb2.ServerResponse(
                        code="0",
                        info="test_command"
                    )
                )
                yield response
                logger.info(f"向客户端发送命令: {self.command_counter}")
                
                # 检查上下文是否被取消
                if not context.is_active():
                    logger.info(f"上下文被取消{context.peer()}")
                    break
                    
        except Exception as e:
            logger.error(f"处理serverCommand请求时出错: {e}")
        finally:
            if client_id in self.client_connections:
                self.client_connections.remove(client_id)
            logger.info(f"客户端断开serverCommand连接: {client_id}")
    
    def _handle_client_message(self, request, client_id):
        """处理客户端上传的消息
        
        Args:
            request: 客户端请求
            client_id: 客户端ID
        """
        msg_type = robot_service_pb2.MsgType.Name(request.msg_type)
        logger.info(f"接收客户端 {client_id} 消息: {msg_type} (msg_id: {request.msg_id})")
        
        # 根据消息类型处理
        if request.msg_type == robot_service_pb2.MsgType.ROBOT_STATUS:
            self._handle_robot_status(request.robot_status)
        elif request.msg_type == robot_service_pb2.MsgType.DEVICE_DATA:
            self._handle_device_data(request.device_data)
        elif request.msg_type == robot_service_pb2.MsgType.ENVIRONMENT_DATA:
            self._handle_environment_data(request.environment_data)
        elif request.msg_type == robot_service_pb2.MsgType.ARRIVE_SERVER_POINT:
            self._handle_arrive_service_point(request.arrive_service_point)
    
    def _handle_robot_status(self, robot_status):
        """处理机器人状态消息
        
        Args:
            robot_status: 机器人状态
        """
        logger.info(f"机器人状态: 电量={robot_status.battery_info.power_percent}%, \
                    充电状态={robot_status.battery_info.charge_status}, \
                    移动状态={robot_service_pb2.MoveStatus.Name(robot_status.system_status.move_status)}")
    
    def _handle_device_data(self, device_data):
        """处理设备数据消息
        
        Args:
            device_data: 设备数据
        """
        logger.debug(f"设备数据: 设备ID={device_data.device_info.device_id}, \
                    数据类型={device_data.device_info.data_type}")
    
    def _handle_environment_data(self, environment_data):
        """处理环境数据消息
        
        Args:
            environment_data: 环境数据
        """
        sensor_data = environment_data.sensor_data
        logger.info(f"环境数据: 温度={sensor_data.temperature}°C, \
                    湿度={sensor_data.humidity}%, \
                    PM2.5={sensor_data.pm25}")
    
    def _handle_arrive_service_point(self, arrive_service_point):
        """处理到达服务点消息
        
        Args:
            arrive_service_point: 到达服务点数据
        """
        logger.debug("收到到达服务点消息")

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
