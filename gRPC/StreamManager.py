from abc import ABC, abstractmethod
from gRPC import RobotService_pb2
import grpc
import threading
import time
import logging
import queue
from typing import Optional, Generator, Any
from concurrent import futures


class BaseStreamManager(ABC):
    """持久化流管理器基类"""

    def __init__(self, stub, robot_id: str):
        """
        初始化流管理器
        
        Args:
            stub: gRPC客户端存根
            robot_id: 机器人ID
        """
        self.stub = stub
        self.robot_id = robot_id
        self.logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")

        # 流状态控制
        self.is_stream_active = False
        self.request_queue = queue.Queue()
        self.response_handler = None
        self.stream_thread = None
        self.shutdown_event = threading.Event()
        
        # 统计信息
        self.stats = {
            'messages_sent': 0,
            'messages_received': 0,
            'errors': 0,
            'start_time': None,
            'last_activity': None
        }

    def start_stream(self) -> bool:
        """启动流"""
        if self.is_stream_active:
            self.logger.warning("流已经处于活动状态")
            return False

        try:
            # 创建双向流
            self.response_iterator = self._create_stream()
            self.is_stream_active = True
            self.stats['start_time'] = time.time()

            # 启动响应处理线程
            self.stream_thread = threading.Thread(
                target=self._handle_responses,
                daemon=True
            )
            self.stream_thread.start()

            self.logger.info(f"持久化双向流已启动，robot_id: {self.robot_id}")
            return True

        except Exception as e:
            self.logger.error(f"启动流失败: {e}")
            self.is_stream_active = False
            return False

    @abstractmethod
    def _create_stream(self):
        """创建流，由子类实现"""
        pass

    @abstractmethod
    def _get_request_type(self):
        """获取请求消息类型，由子类实现"""
        pass

    @abstractmethod
    def _get_response_type(self):
        """获取响应消息类型，由子类实现"""
        pass

    def _request_generator(self) -> Generator[Any, None, None]:
        """生成请求消息的生成器"""
        while not self.shutdown_event.is_set():
            try:
                # 从队列中获取请求，设置超时以避免永久阻塞
                request = self.request_queue.get(timeout=1.0)
                yield request
                self.request_queue.task_done()
                self.stats['messages_sent'] += 1
                self.stats['last_activity'] = time.time()
            except queue.Empty:
                # 超时后继续循环，检查关闭事件
                continue
            except Exception as e:
                self.logger.error(f"请求生成器错误: {e}")
                self.stats['errors'] += 1
                break

    def _handle_responses(self):
        """处理服务器响应"""
        try:
            for response in self.response_iterator:
                if self.shutdown_event.is_set():
                    break
                if hasattr(response, 'msg_id'):
                    self.logger.info(f"收到服务器响应: msg_id={response.msg_id}, type={response.msg_type}")
                elif hasattr(response, 'command_id'):
                    self.logger.info(f"收到服务器响应: command_id={response.command_id}, type={response.command_type}")
                self.stats['messages_received'] += 1
                self.stats['last_activity'] = time.time()
                if self.response_handler:
                    self.response_handler(response)

        except Exception as e:
            self.logger.error(f"响应处理错误: {e}")
            self.stats['errors'] += 1
        finally:
            self.is_stream_active = False
            self.logger.info("响应处理线程结束")

    def send_message(self, request) -> bool:
        """通过持久化流发送消息"""
        if not self.is_stream_active:
            self.logger.error("流未激活，无法发送消息")
            return False

        try:
            self.request_queue.put(request)
            if hasattr(request, 'msg_id'):
                self.logger.debug(f"消息已加入发送队列: msg_id={request.msg_id}")
            elif hasattr(request, 'command_id'):
                self.logger.debug(f"消息已加入发送队列: command_id={request.command_id}")
            return True

        except Exception as e:
            self.logger.error(f"发送消息到队列失败: {e}")
            self.stats['errors'] += 1
            return False

    def set_response_handler(self, handler):
        """设置响应处理函数"""
        self.response_handler = handler

    def get_stats(self) -> dict:
        """获取流统计信息"""
        stats = self.stats.copy()
        if stats['start_time']:
            stats['uptime'] = time.time() - stats['start_time']
        return stats

    def stop_stream(self):
        """停止流"""
        self.logger.info("正在停止持久化流...")
        self.shutdown_event.set()
        self.is_stream_active = False

        if self.stream_thread and self.stream_thread.is_alive():
            self.stream_thread.join(timeout=5)

        self.logger.info("持久化流已停止")

    def __enter__(self):
        """支持上下文管理器"""
        self.start_stream()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """支持上下文管理器"""
        self.stop_stream()


class ClientUploadStreamManager(BaseStreamManager):
    """客户端上传流管理器，用于处理clientUpload RPC"""

    def _create_stream(self):
        """创建clientUpload流"""
        return self.stub.clientUpload(self._request_generator())

    def _get_request_type(self):
        """获取请求消息类型"""
        return RobotService_pb2.RobotUploadRequest

    def _get_response_type(self):
        """获取响应消息类型"""
        return RobotService_pb2.RobotUploadResponse

    def send_robot_status(self, msg_id: int, msg_type: RobotService_pb2.MsgType, 
                        robot_status: RobotService_pb2.RobotStatusUpload) -> bool:
        """发送机器人状态"""
        request = self._get_request_type()(
            msg_id=msg_id,
            msg_time=int(time.time() * 1000),
            msg_type=msg_type,
            robot_id=self.robot_id,
            robot_status=robot_status
        )
        return self.send_message(request)

    def send_device_data(self, msg_id: int, msg_type: RobotService_pb2.MsgType,
                       device_data: RobotService_pb2.DeviceDataUpload) -> bool:
        """发送设备数据"""
        request = self._get_request_type()(
            msg_id=msg_id,
            msg_time=int(time.time() * 1000),
            msg_type=msg_type,
            robot_id=self.robot_id,
            device_data=device_data
        )
        return self.send_message(request)

    def send_environment_data(self, msg_id: int, msg_type: RobotService_pb2.MsgType,
                           environment_data: RobotService_pb2.EnvironmentDataUpload) -> bool:
        """发送环境数据"""
        request = self._get_request_type()(
            msg_id=msg_id,
            msg_time=int(time.time() * 1000),
            msg_type=msg_type,
            robot_id=self.robot_id,
            environment_data=environment_data
        )
        return self.send_message(request)

    def send_arrive_service_point(self, msg_id: int, msg_type: RobotService_pb2.MsgType,
                               arrive_service_point: RobotService_pb2.ArriveServicePointUpload) -> bool:
        """发送到达服务点信息"""
        request = self._get_request_type()(
            msg_id=msg_id,
            msg_time=int(time.time() * 1000),
            msg_type=msg_type,
            robot_id=self.robot_id,
            arrive_service_point=arrive_service_point
        )
        return self.send_message(request)


class ServerCommandStreamManager(BaseStreamManager):
    """服务端命令流管理器，用于处理serverCommand RPC"""

    def _create_stream(self):
        """创建serverCommand流"""
        return self.stub.serverCommand(self._request_generator())

    def _get_request_type(self):
        """获取请求消息类型"""
        return RobotService_pb2.ServerCmdRequest

    def _get_response_type(self):
        """获取响应消息类型"""
        return RobotService_pb2.ServerCmdResponse

    def send_heartbeat(self) -> bool:
        """发送心跳"""
        import uuid
        request = self._get_request_type()(
            command_id=int(uuid.uuid4().hex[:8], 16),
            command_time=int(time.time() * 1000),
            command_type=RobotService_pb2.CmdType.ROBOT_MODE_CMD,
            robot_id=self.robot_id
        )
        return self.send_message(request)

    def send_command_response(self, command_id: int, command_type: RobotService_pb2.CmdType,
                           response_code: str, response_info: str) -> bool:
        """发送命令响应"""
        request = self._get_request_type()(
            command_id=command_id,
            command_time=int(time.time() * 1000),
            command_type=command_type,
            robot_id=self.robot_id,
            data_json=RobotService_pb2.ServerResponse(
                code=response_code,
                info=response_info
            )
        )
        return self.send_message(request)

    def start_with_heartbeat(self, heartbeat_interval: int = 10):
        """启动流并发送心跳"""
        if not self.start_stream():
            return False

        # 启动心跳线程
        def heartbeat_thread():
            while self.is_stream_active and not self.shutdown_event.is_set():
                self.send_heartbeat()
                time.sleep(heartbeat_interval)

        thread = threading.Thread(target=heartbeat_thread, daemon=True)
        thread.start()
        return True