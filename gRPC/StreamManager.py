from abc import ABC, abstractmethod
from gRPC import RobotService_pb2 as robot_pb2
import grpc
import threading
import time
import logging
import queue
import uuid
from typing import Optional, Generator, Any, Callable
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
        self.response_handler: Optional[Callable] = None
        self.stream_thread: Optional[threading.Thread] = None
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
            self.shutdown_event.clear()
            self.stats['start_time'] = time.time()

            # 启动响应处理线程
            self.stream_thread = threading.Thread(
                target=self._handle_responses,
                daemon=True,
                name=f"{self.__class__.__name__}_ResponseHandler"
            )
            self.stream_thread.start()

            self.logger.info(f"持久化双向流已启动，robot_id: {self.robot_id}")
            return True

        except Exception as e:
            self.logger.error(f"启动流失败: {e}")
            self.is_stream_active = False
            return False

    @abstractmethod
    def _create_stream(self) -> Generator:
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
        self.logger.debug("请求生成器启动")
        
        while not self.shutdown_event.is_set() and self.is_stream_active:
            try:
                # 从队列中获取请求，设置超时以避免永久阻塞
                request = self.request_queue.get(timeout=1.0)
                if request is None:  # 停止信号
                    break
                    
                yield request
                self.request_queue.task_done()
                self.stats['messages_sent'] += 1
                self.stats['last_activity'] = time.time()
                
                # 调试日志
                self._log_request_info(request)
                
            except queue.Empty:
                # 发送心跳或保持连接的消息
                if hasattr(self, '_send_keepalive'):
                    self._send_keepalive()
                continue
            except Exception as e:
                self.logger.error(f"请求生成器错误: {e}")
                self.stats['errors'] += 1
                break
                
        self.logger.debug("请求生成器停止")

    def _log_request_info(self, request):
        """记录请求信息"""
        if hasattr(request, 'msg_id'):
            msg_type_str = robot_pb2.MsgType.Name(request.msg_type) if hasattr(robot_pb2.MsgType, 'Name') else request.msg_type
            self.logger.debug(f"发送消息: msg_id={request.msg_id}, type={msg_type_str}")
        elif hasattr(request, 'command_id'):
            cmd_type_str = robot_pb2.CmdType.Name(request.command_type) if hasattr(robot_pb2.CmdType, 'Name') else request.command_type
            self.logger.debug(f"发送命令: command_id={request.command_id}, type={cmd_type_str}")

    def _handle_responses(self):
        """处理服务器响应"""
        self.logger.debug("响应处理线程启动")
        
        try:
            for response in self.response_iterator:
                if self.shutdown_event.is_set() or not self.is_stream_active:
                    break
                    
                self.stats['messages_received'] += 1
                self.stats['last_activity'] = time.time()
                
                # 记录响应信息
                self._log_response_info(response)
                
                # 调用响应处理函数
                if self.response_handler:
                    try:
                        self.response_handler(response)
                    except Exception as e:
                        self.logger.error(f"响应处理函数执行错误: {e}")
                        self.stats['errors'] += 1

        except grpc.RpcError as e:
            if e.code() == grpc.StatusCode.CANCELLED:
                self.logger.info("RPC调用被取消")
            else:
                self.logger.error(f"RPC错误: {e}")
                self.stats['errors'] += 1
        except Exception as e:
            self.logger.error(f"响应处理错误: {e}")
            self.stats['errors'] += 1
        finally:
            self.is_stream_active = False
            self.logger.info("响应处理线程结束")

    def _log_response_info(self, response):
        """记录响应信息"""
        if hasattr(response, 'msg_id'):
            msg_type_str = robot_pb2.MsgType.Name(response.msg_type) if hasattr(robot_pb2.MsgType, 'Name') else response.msg_type
            self.logger.info(f"收到服务器响应: msg_id={response.msg_id}, type={msg_type_str}")
        elif hasattr(response, 'command_id'):
            cmd_type_str = robot_pb2.CmdType.Name(response.command_type) if hasattr(robot_pb2.CmdType, 'Name') else response.command_type
            self.logger.info(f"收到服务器命令: command_id={response.command_id}, type={cmd_type_str}")

    def send_message(self, request) -> bool:
        """通过持久化流发送消息"""
        if not self.is_stream_active:
            self.logger.error("流未激活，无法发送消息")
            return False

        try:
            self.request_queue.put(request)
            self._log_request_info(request)
            return True

        except Exception as e:
            self.logger.error(f"发送消息到队列失败: {e}")
            self.stats['errors'] += 1
            return False

    def set_response_handler(self, handler: Callable):
        """设置响应处理函数"""
        self.response_handler = handler
        self.logger.debug(f"设置响应处理函数: {handler.__name__ if hasattr(handler, '__name__') else handler}")

    def get_stats(self) -> dict:
        """获取流统计信息"""
        stats = self.stats.copy()
        if stats['start_time']:
            stats['uptime'] = time.time() - stats['start_time']
        stats['queue_size'] = self.request_queue.qsize()
        stats['is_active'] = self.is_stream_active
        return stats

    def stop_stream(self):
        """停止流"""
        self.logger.info("正在停止持久化流...")
        self.shutdown_event.set()
        self.is_stream_active = False

        # 发送停止信号到队列
        try:
            self.request_queue.put(None, timeout=1.0)
        except:
            pass

        if self.stream_thread and self.stream_thread.is_alive():
            self.stream_thread.join(timeout=5)
            if self.stream_thread.is_alive():
                self.logger.warning("响应处理线程未在超时时间内结束")

        # 清空队列
        while not self.request_queue.empty():
            try:
                self.request_queue.get_nowait()
                self.request_queue.task_done()
            except queue.Empty:
                break

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
        return robot_pb2.RobotUploadRequest

    def _get_response_type(self):
        """获取响应消息类型"""
        return robot_pb2.RobotUploadResponse

    def _send_keepalive(self):
        """发送保持连接的消息"""
        # 对于clientUpload流，可以发送一个简单的心跳消息
        try:
            # 创建一个简单的心跳消息
            request = self._get_request_type()(
                msg_id=int(uuid.uuid4().hex[:8], 16),
                msg_time=int(time.time() * 1000),
                msg_type=robot_pb2.MsgType.ROBOT_STATUS,
                robot_id=self.robot_id
            )
            # 设置空的机器人状态
            robot_status = robot_pb2.RobotStatusUpload(
                battery_info=robot_pb2.BatteryInfo(power_percent=0.0, charge_status="unknown"),
                position_info=robot_pb2.PositionInfo(),
                system_status=robot_pb2.SystemStatus(move_status=robot_pb2.MoveStatus.IDLE)
            )
            request.robot_status.CopyFrom(robot_status)
            self.send_message(request)
        except Exception as e:
            self.logger.debug(f"发送心跳失败: {e}")

    def send_robot_status(self, msg_id: int, msg_type: robot_pb2.MsgType, 
                        robot_status: robot_pb2.RobotStatusUpload) -> bool:
        """发送机器人状态"""
        request = self._get_request_type()(
            msg_id=msg_id,
            msg_time=int(time.time() * 1000),
            msg_type=msg_type,
            robot_id=self.robot_id
        )
        # 设置oneof字段
        request.robot_status.CopyFrom(robot_status)
        return self.send_message(request)

    def send_device_data(self, msg_id: int, msg_type: robot_pb2.MsgType,
                       device_data: robot_pb2.DeviceDataUpload) -> bool:
        """发送设备数据"""
        request = self._get_request_type()(
            msg_id=msg_id,
            msg_time=int(time.time() * 1000),
            msg_type=msg_type,
            robot_id=self.robot_id
        )
        # 设置oneof字段
        request.device_data.CopyFrom(device_data)
        return self.send_message(request)

    def send_environment_data(self, msg_id: int, msg_type: robot_pb2.MsgType,
                           environment_data: robot_pb2.EnvironmentDataUpload) -> bool:
        """发送环境数据"""
        request = self._get_request_type()(
            msg_id=msg_id,
            msg_time=int(time.time() * 1000),
            msg_type=msg_type,
            robot_id=self.robot_id
        )
        # 设置oneof字段
        request.environment_data.CopyFrom(environment_data)
        return self.send_message(request)

    def send_arrive_service_point(self, msg_id: int, msg_type: robot_pb2.MsgType,
                               arrive_service_point: robot_pb2.ArriveServicePointUpload) -> bool:
        """发送到达服务点信息"""
        request = self._get_request_type()(
            msg_id=msg_id,
            msg_time=int(time.time() * 1000),
            msg_type=msg_type,
            robot_id=self.robot_id
        )
        # 设置oneof字段
        request.arrive_service_point.CopyFrom(arrive_service_point)
        return self.send_message(request)

    def send_heartbeat(self) -> bool:
        """发送心跳"""
        return self._send_keepalive()


class ServerCommandStreamManager(BaseStreamManager):
    """服务端命令流管理器，用于处理serverCommand RPC"""

    def __init__(self, stub, robot_id: str):
        super().__init__(stub, robot_id)
        self.last_heartbeat_time = 0
        self.heartbeat_interval = 30  # 心跳间隔30秒

    def _create_stream(self):
        """创建serverCommand流"""
        return self.stub.serverCommand(self._request_generator())

    def _get_request_type(self):
        """获取请求消息类型"""
        return robot_pb2.ServerCmdRequest

    def _get_response_type(self):
        """获取响应消息类型"""
        return robot_pb2.ServerCmdResponse

    def _send_keepalive(self):
        """发送保持连接的心跳"""
        current_time = time.time()
        if current_time - self.last_heartbeat_time > self.heartbeat_interval:
            try:
                self.send_heartbeat()
                self.last_heartbeat_time = current_time
            except Exception as e:
                self.logger.debug(f"发送心跳失败: {e}")

    def send_heartbeat(self) -> bool:
        """发送心跳响应"""
        request = self._get_request_type()(
            command_id=int(uuid.uuid4().hex[:8], 16),
            command_time=int(time.time() * 1000),
            command_type=robot_pb2.CmdType.RESPONSE_CMD,
            robot_id=self.robot_id,
            data_json=robot_pb2.ServerResponse(
                code="0",
                info="heartbeat"
            )
        )
        return self.send_message(request)

    def send_command_response(self, command_id: int, command_type: robot_pb2.CmdType,
                           response_code: str, response_info: str) -> bool:
        """发送命令响应"""
        request = self._get_request_type()(
            command_id=command_id,
            command_time=int(time.time() * 1000),
            command_type=command_type,
            robot_id=self.robot_id,
            data_json=robot_pb2.ServerResponse(
                code=response_code,
                info=response_info
            )
        )
        return self.send_message(request)

    def send_robot_mode_response(self, command_id: int, success: bool = True, 
                               message: str = "") -> bool:
        """发送机器人模式命令响应"""
        code = "0" if success else "1"
        info = message if message else ("成功" if success else "失败")
        return self.send_command_response(
            command_id=command_id,
            command_type=robot_pb2.CmdType.ROBOT_MODE_CMD,
            response_code=code,
            response_info=info
        )

    def send_task_response(self, command_id: int, success: bool = True,
                         message: str = "") -> bool:
        """发送任务命令响应"""
        code = "0" if success else "1"
        info = message if message else ("任务接收成功" if success else "任务接收失败")
        return self.send_command_response(
            command_id=command_id,
            command_type=robot_pb2.CmdType.TASK_CMD,
            response_code=code,
            response_info=info
        )

    def send_joy_control_response(self, command_id: int, success: bool = True,
                                message: str = "") -> bool:
        """发送摇杆控制命令响应"""
        code = "0" if success else "1"
        info = message if message else ("控制指令接收成功" if success else "控制指令接收失败")
        return self.send_command_response(
            command_id=command_id,
            command_type=robot_pb2.CmdType.JOY_CONTROL_CMD,
            response_code=code,
            response_info=info
        )

    def start_with_heartbeat(self, heartbeat_interval: int = 30):
        """启动流并定期发送心跳"""
        if not self.start_stream():
            return False

        # 设置心跳间隔
        self.heartbeat_interval = heartbeat_interval
        
        # 启动心跳线程
        def heartbeat_thread():
            while self.is_stream_active and not self.shutdown_event.is_set():
                try:
                    current_time = time.time()
                    if current_time - self.last_heartbeat_time > self.heartbeat_interval:
                        self.send_heartbeat()
                        self.last_heartbeat_time = current_time
                    time.sleep(1)
                except Exception as e:
                    self.logger.error(f"心跳线程错误: {e}")
                    break

        heartbeat_thread_instance = threading.Thread(
            target=heartbeat_thread, 
            daemon=True,
            name=f"{self.__class__.__name__}_Heartbeat"
        )
        heartbeat_thread_instance.start()
        
        return True