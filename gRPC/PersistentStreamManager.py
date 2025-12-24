
from gRPC import RobotService_pb2
import grpc
import threading
import time
import logging
import queue
from typing import Optional, Generator
from concurrent import futures

class PersistentStreamManager:
    """持久化流管理器"""

    def __init__(self, stub, robot_id: int):
        self.stub = stub
        self.robot_id = robot_id
        self.logger = logging.getLogger(__name__)

        # 流状态控制
        self.is_stream_active = False
        self.request_queue = queue.Queue()
        self.response_handler = None
        self.stream_thread = None
        self.shutdown_event = threading.Event()

    def start_stream(self):
        if self.is_stream_active:
            self.logger.warning("流已经处于活动状态")
            return False

        try:
            # 创建双向流
            self.response_iterator = self.stub.clientUpload(self._request_generator())
            self.is_stream_active = True

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

    def _request_generator(self) -> Generator[RobotService_pb2.RobotUploadRequest, None, None]:
        """生成请求消息的生成器"""
        while not self.shutdown_event.is_set():
            try:
                # 从队列中获取请求，设置超时以避免永久阻塞
                request = self.request_queue.get(timeout=1.0)
                yield request
                self.request_queue.task_done()
            except queue.Empty:
                # 超时后继续循环，检查关闭事件
                continue
            except Exception as e:
                self.logger.error(f"请求生成器错误: {e}")
                break

    def _handle_responses(self):
        """处理服务器响应"""
        try:
            for response in self.response_iterator:
                if self.shutdown_event.is_set():
                    break

                self.logger.info(f"收到服务器响应: msg_id={response.msg_id}, type={response.msg_type}")

                if self.response_handler:
                    self.response_handler(response)

        except Exception as e:
            self.logger.error(f"响应处理错误: {e}")
        finally:
            self.is_stream_active = False
            self.logger.info("响应处理线程结束")

    def send_message(self, request: RobotService_pb2.RobotUploadRequest) -> bool:
        """通过持久化流发送消息"""
        if not self.is_stream_active:
            self.logger.error("流未激活，无法发送消息")
            return False

        try:
            request.robot_id = self.robot_id

            self.request_queue.put(request)
            self.logger.debug(f"消息已加入发送队列: msg_id={request.msg_id}")
            return True

        except Exception as e:
            self.logger.error(f"发送消息到队列失败: {e}")
            return False

    def stop_stream(self):
        self.logger.info("正在停止持久化流...")
        self.shutdown_event.set()
        self.is_stream_active = False

        if self.stream_thread and self.stream_thread.is_alive():
            self.stream_thread.join(timeout=5)

        self.logger.info("持久化流已停止")

