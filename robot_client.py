import grpc
import threading
import time
import logging
import queue
from typing import Optional, Generator
from concurrent import futures

# 导入生成的gRPC代码
import gRPC.RobotService_pb2 as robot_service_pb2
import gRPC.RobotService_pb2_grpc as robot_service_pb2_grpc

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

    def _request_generator(self) -> Generator[robot_service_pb2.RobotUploadRequest, None, None]:
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

    def send_message(self, request: robot_service_pb2.RobotUploadRequest) -> bool:
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

class OptimizedRobotServiceClient:
    """机器人gRPC客户端"""

    def __init__(self, config=None):
        self.config = config or ClientConfig()
        self.logger = self._setup_logging()

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

    def _setup_logging(self):
        """设置日志"""
        logging.basicConfig(
            level=getattr(logging, self.config.LOG_LEVEL),
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        return logging.getLogger(__name__)

    def connect(self, robot_id: int) -> bool:
        self.robot_id = robot_id

        try:
            # 创建gRPC通道
            self.channel = grpc.insecure_channel(
                self.config.SERVER_ADDRESS,
                options=[
                    ('grpc.keepalive_time_ms', 10000),
                    ('grpc.keepalive_timeout_ms', 5000),
                ]
            )

            # 创建存根
            self.stub = robot_service_pb2_grpc.RobotServiceStub(self.channel)

            # 测试连接
            try:
                grpc.channel_ready_future(self.channel).result(
                    timeout=self.config.CONNECTION_TIMEOUT
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

    def send_robot_status(self,
                          battery_level: float = 85.0,
                          position_info: Optional[list] = None,
                          additional_data: Optional[dict] = None) -> bool:
        if not self.is_connected or not self.stream_manager:
            self.logger.error("未连接到服务器或流管理器未初始化")
            return False

        try:
            current_time = int(time.time() * 1000)
            msg_id = current_time + self.sent_count

            # 电池信息
            battery_info = robot_service_pb2.BatteryInfo(
                power_percent=battery_level,
                charge_status="DISCHARGING"
            )

            # 位置信息
            if position_info is None:
                position_info = robot_service_pb2.PositionInfo(
                    AGVPositionInfo=[100.0, 200.0, 0.5],
                    ARMPositionInfo=[1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
                    EXTPositionInfo=[10.0, 20.0, 30.0, 40.0],
                    targetPoint="default_point",
                    pointId=1
                )

            # 机器人状态
            robot_status = robot_service_pb2.RobotStatusUpload(
                battery_info=battery_info,
                position_info=position_info
            )

            request = robot_service_pb2.RobotUploadRequest(
                msg_id=msg_id,
                msg_time=current_time,
                msg_type=robot_service_pb2.ROBOT_STATUS,
                robot_id=self.robot_id,
                robot_status=robot_status
            )

            # 通过持久化流发送消息
            if self.stream_manager.send_message(request):
                self.sent_count += 1
                self.logger.debug(f"状态消息发送成功: msg_id={msg_id}")
                return True
            else:
                return False

        except Exception as e:
            self.logger.error(f"构建或发送状态消息失败: {e}")
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

    def shutdown(self):
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

def main():
    client = OptimizedRobotServiceClient()

    def custom_response_handler(response):
        print(f"收到自定义处理响应: msg={response}")

    try:
        # 连接服务器（必须提供robot_id）
        robot_id = 12345  # 这是必要参数
        if client.connect(robot_id=robot_id):
            # 设置响应处理器
            client.set_response_handler(custom_response_handler)

            print("客户端已连接，开始发送测试消息...")

            # 发送多条测试消息（使用同一个持久化流）
            for i in range(100):
                success = client.send_robot_status(
                    battery_level= i,
                    additional_data={"test_index": i}
                )

                if success:
                    print(f"测试消息 {i+1}/100 发送成功")
                else:
                    print(f"测试消息 {i+1}/100 发送失败")

                time.sleep(2)  # 模拟实际间隔

            # 等待一段时间观察流保持情况
            print("等待10秒观察流持久化...")
            time.sleep(10)

        else:
            print("连接失败")

    except KeyboardInterrupt:
        print("\n用户中断操作")
    except Exception as e:
        print(f"\n错误: {e}")
    finally:
        client.shutdown()

if __name__ == "__main__":
    main()