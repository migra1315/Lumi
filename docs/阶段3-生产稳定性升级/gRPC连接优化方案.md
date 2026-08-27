# gRPC 连接优化方案

## 问题分析

### 当前实现存在的问题

#### 1. 连接建立前发送数据会出错

**问题描述**：
- 定时上报线程 `_reporting_loop` 在 `_start_reporting()` 时启动
- 如果 gRPC 连接未完全建立或建立失败，`_send_message()` 会调用 `client_upload_manager.send_message()`
- `send_message()` 检查 `is_stream_active` 为 False 时，记录错误并返回 False
- 调用方没有处理返回值，导致数据丢失

**相关代码**（`RobotControlSystem.py:467-476`）：
```python
def _send_message(self, msg_envelope: MessageEnvelope):
    grpc_msg = convert_message_envelope_to_robot_upload_request(msg_envelope)
    self.client_upload_manager.send_message(grpc_msg)  # 未检查返回值
```

**相关代码**（`StreamManager.py:173-188`）：
```python
def send_message(self, request) -> bool:
    if not self.is_stream_active:
        self.logger.error("流未激活，无法发送消息")
        return False
    # ...
```

#### 2. 服务器断开后没有重连机制

**问题描述**：
- 当服务器挂掉或网络中断时，`_handle_responses()` 会捕获 `grpc.RpcError`
- 捕获后只是设置 `is_stream_active = False`，没有尝试重连
- 系统处于"假死"状态：程序运行但无法通信

**相关代码**（`StreamManager.py:128-162`）：
```python
def _handle_responses(self):
    try:
        for response in self.response_iterator:
            # ...
    except grpc.RpcError as e:
        # 只记录错误，没有重连逻辑
        self.logger.error(f"RPC错误: {e}")
    finally:
        self.is_stream_active = False  # 流永久失效
```

---

## 解决方案

### 方案概述

```
┌─────────────────────────────────────────────────────────────────┐
│                    gRPC Connection Manager                       │
├─────────────────────────────────────────────────────────────────┤
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐          │
│  │   Message   │───▶│   Send      │───▶│   gRPC      │          │
│  │   Queue     │    │   Buffer    │    │   Stream    │          │
│  └─────────────┘    └─────────────┘    └─────────────┘          │
│         ▲                                     │                  │
│         │                                     ▼                  │
│  ┌─────────────┐                      ┌─────────────┐           │
│  │   Retry     │◀────────────────────│   Health    │           │
│  │   Manager   │                      │   Monitor   │           │
│  └─────────────┘                      └─────────────┘           │
│         │                                     │                  │
│         ▼                                     ▼                  │
│  ┌─────────────┐                      ┌─────────────┐           │
│  │ Exponential │                      │   State     │           │
│  │  Backoff    │                      │   Machine   │           │
│  └─────────────┘                      └─────────────┘           │
└─────────────────────────────────────────────────────────────────┘
```

### 1. 连接状态机

引入连接状态枚举，清晰管理连接生命周期：

```python
from enum import Enum

class ConnectionState(Enum):
    DISCONNECTED = "disconnected"      # 未连接
    CONNECTING = "connecting"          # 正在连接
    CONNECTED = "connected"            # 已连接
    RECONNECTING = "reconnecting"      # 正在重连
    FAILED = "failed"                  # 连接失败（达到最大重试次数）
```

**状态转换图**：

```
                    ┌───────────────┐
                    │ DISCONNECTED  │
                    └───────┬───────┘
                            │ start()
                            ▼
                    ┌───────────────┐
         ┌─────────│  CONNECTING   │─────────┐
         │ fail    └───────────────┘ success │
         ▼                                   ▼
┌───────────────┐                   ┌───────────────┐
│ RECONNECTING  │◀──────────────────│  CONNECTED    │
└───────┬───────┘   connection lost └───────────────┘
        │                                   ▲
        │ success                           │
        └───────────────────────────────────┘
        │
        │ max retries exceeded
        ▼
┌───────────────┐
│    FAILED     │
└───────────────┘
```

### 2. 消息缓冲队列

在连接不可用时，将消息缓存到本地队列，待连接恢复后自动发送：

```python
import queue
from collections import deque
from dataclasses import dataclass
from typing import Any
import time

@dataclass
class BufferedMessage:
    """缓冲消息"""
    message: Any
    timestamp: float
    priority: int = 0
    retry_count: int = 0
    max_age: float = 300.0  # 最大存活时间（秒）

    def is_expired(self) -> bool:
        return time.time() - self.timestamp > self.max_age

class MessageBuffer:
    """消息缓冲管理器"""

    def __init__(self, max_size: int = 1000, max_age: float = 300.0):
        self.buffer = deque(maxlen=max_size)
        self.max_age = max_age
        self._lock = threading.Lock()

    def add(self, message: Any, priority: int = 0) -> bool:
        """添加消息到缓冲区"""
        with self._lock:
            buffered_msg = BufferedMessage(
                message=message,
                timestamp=time.time(),
                priority=priority,
                max_age=self.max_age
            )
            self.buffer.append(buffered_msg)
            return True

    def get_all_valid(self) -> list:
        """获取所有未过期的消息"""
        with self._lock:
            valid_messages = [
                msg for msg in self.buffer
                if not msg.is_expired()
            ]
            self.buffer.clear()
            return sorted(valid_messages, key=lambda x: x.priority, reverse=True)

    def size(self) -> int:
        return len(self.buffer)
```

### 3. 自动重连机制（指数退避）

```python
import threading
import time
import math

class ReconnectionManager:
    """重连管理器 - 使用指数退避策略"""

    def __init__(
        self,
        initial_delay: float = 1.0,
        max_delay: float = 60.0,
        multiplier: float = 2.0,
        max_retries: int = -1,  # -1 表示无限重试
        jitter: float = 0.1     # 抖动因子，避免惊群效应
    ):
        self.initial_delay = initial_delay
        self.max_delay = max_delay
        self.multiplier = multiplier
        self.max_retries = max_retries
        self.jitter = jitter

        self.retry_count = 0
        self.is_reconnecting = False
        self._lock = threading.Lock()

    def get_next_delay(self) -> float:
        """计算下一次重连延迟（指数退避 + 抖动）"""
        delay = min(
            self.initial_delay * (self.multiplier ** self.retry_count),
            self.max_delay
        )
        # 添加随机抖动
        jitter_range = delay * self.jitter
        delay += (random.random() * 2 - 1) * jitter_range
        return max(0, delay)

    def should_retry(self) -> bool:
        """是否应该继续重试"""
        if self.max_retries == -1:
            return True
        return self.retry_count < self.max_retries

    def record_attempt(self):
        """记录一次重连尝试"""
        with self._lock:
            self.retry_count += 1

    def reset(self):
        """重置重连计数器（连接成功后调用）"""
        with self._lock:
            self.retry_count = 0
            self.is_reconnecting = False
```

### 4. 增强版 StreamManager

修改 `BaseStreamManager`，添加重连支持：

```python
class EnhancedStreamManager(BaseStreamManager):
    """增强版流管理器 - 支持自动重连和消息缓冲"""

    def __init__(self, stub, robot_id: int, config: dict = None):
        super().__init__(stub, robot_id)

        config = config or {}

        # 连接状态
        self.connection_state = ConnectionState.DISCONNECTED
        self._state_lock = threading.Lock()

        # 重连管理器
        self.reconnection_manager = ReconnectionManager(
            initial_delay=config.get('reconnect_initial_delay', 1.0),
            max_delay=config.get('reconnect_max_delay', 60.0),
            max_retries=config.get('reconnect_max_retries', -1)
        )

        # 消息缓冲
        self.message_buffer = MessageBuffer(
            max_size=config.get('buffer_max_size', 1000),
            max_age=config.get('buffer_max_age', 300.0)
        )

        # 健康检查
        self.health_check_interval = config.get('health_check_interval', 10.0)
        self._health_check_thread = None

        # 回调
        self.on_connected = None
        self.on_disconnected = None
        self.on_reconnecting = None
        self.on_reconnect_failed = None

    def _set_state(self, new_state: ConnectionState):
        """设置连接状态并触发回调"""
        with self._state_lock:
            old_state = self.connection_state
            self.connection_state = new_state

        self.logger.info(f"连接状态变更: {old_state.value} -> {new_state.value}")

        # 触发状态回调
        if new_state == ConnectionState.CONNECTED and self.on_connected:
            self.on_connected()
        elif new_state == ConnectionState.DISCONNECTED and self.on_disconnected:
            self.on_disconnected()
        elif new_state == ConnectionState.RECONNECTING and self.on_reconnecting:
            self.on_reconnecting(self.reconnection_manager.retry_count)
        elif new_state == ConnectionState.FAILED and self.on_reconnect_failed:
            self.on_reconnect_failed()

    def start_stream(self) -> bool:
        """启动流（带重连支持）"""
        self._set_state(ConnectionState.CONNECTING)

        if super().start_stream():
            self._set_state(ConnectionState.CONNECTED)
            self.reconnection_manager.reset()
            self._start_health_check()
            self._flush_buffer()  # 发送缓冲的消息
            return True
        else:
            self._set_state(ConnectionState.DISCONNECTED)
            return False

    def send_message(self, request, priority: int = 0) -> bool:
        """发送消息（支持缓冲）"""
        with self._state_lock:
            state = self.connection_state

        if state == ConnectionState.CONNECTED:
            # 连接正常，直接发送
            if super().send_message(request):
                return True
            else:
                # 发送失败，加入缓冲
                self.message_buffer.add(request, priority)
                self._trigger_reconnect()
                return False
        else:
            # 未连接，加入缓冲
            self.message_buffer.add(request, priority)
            self.logger.warning(
                f"消息已缓冲（当前状态: {state.value}），"
                f"缓冲区大小: {self.message_buffer.size()}"
            )
            return False

    def _flush_buffer(self):
        """发送缓冲区中的所有消息"""
        messages = self.message_buffer.get_all_valid()
        if messages:
            self.logger.info(f"正在发送 {len(messages)} 条缓冲消息")
            for buffered_msg in messages:
                super().send_message(buffered_msg.message)

    def _handle_responses(self):
        """处理响应（带自动重连）"""
        try:
            for response in self.response_iterator:
                if self.shutdown_event.is_set():
                    break

                self.stats['messages_received'] += 1
                self.stats['last_activity'] = time.time()

                if self.response_handler:
                    try:
                        self.response_handler(response)
                    except Exception as e:
                        self.logger.error(f"响应处理函数执行错误: {e}")

        except grpc.RpcError as e:
            if e.code() != grpc.StatusCode.CANCELLED:
                self.logger.error(f"RPC错误: {e.code()} - {e.details()}")
                self._trigger_reconnect()
        except Exception as e:
            self.logger.error(f"响应处理错误: {e}")
            self._trigger_reconnect()
        finally:
            self.is_stream_active = False

    def _trigger_reconnect(self):
        """触发重连"""
        with self._state_lock:
            if self.connection_state in [ConnectionState.RECONNECTING, ConnectionState.FAILED]:
                return

        self._set_state(ConnectionState.RECONNECTING)

        # 在新线程中执行重连
        reconnect_thread = threading.Thread(
            target=self._reconnect_loop,
            daemon=True,
            name=f"{self.__class__.__name__}_Reconnect"
        )
        reconnect_thread.start()

    def _reconnect_loop(self):
        """重连循环"""
        while not self.shutdown_event.is_set():
            if not self.reconnection_manager.should_retry():
                self._set_state(ConnectionState.FAILED)
                self.logger.error("达到最大重连次数，停止重连")
                return

            delay = self.reconnection_manager.get_next_delay()
            self.logger.info(
                f"将在 {delay:.1f} 秒后进行第 "
                f"{self.reconnection_manager.retry_count + 1} 次重连"
            )

            time.sleep(delay)

            if self.shutdown_event.is_set():
                return

            self.reconnection_manager.record_attempt()

            # 尝试重连
            try:
                self._cleanup_old_stream()
                if self.start_stream():
                    self.logger.info("重连成功")
                    return
            except Exception as e:
                self.logger.error(f"重连失败: {e}")

        self._set_state(ConnectionState.FAILED)

    def _cleanup_old_stream(self):
        """清理旧的流资源"""
        self.is_stream_active = False
        if hasattr(self, 'response_iterator') and self.response_iterator:
            try:
                self.response_iterator.cancel()
            except:
                pass

    def _start_health_check(self):
        """启动健康检查线程"""
        if self._health_check_thread and self._health_check_thread.is_alive():
            return

        self._health_check_thread = threading.Thread(
            target=self._health_check_loop,
            daemon=True,
            name=f"{self.__class__.__name__}_HealthCheck"
        )
        self._health_check_thread.start()

    def _health_check_loop(self):
        """健康检查循环"""
        while not self.shutdown_event.is_set():
            time.sleep(self.health_check_interval)

            with self._state_lock:
                if self.connection_state != ConnectionState.CONNECTED:
                    continue

            # 检查最后活动时间
            if self.stats['last_activity']:
                idle_time = time.time() - self.stats['last_activity']
                if idle_time > self.health_check_interval * 3:
                    self.logger.warning(f"连接可能已断开（空闲 {idle_time:.1f} 秒）")
                    self._trigger_reconnect()
```

### 5. RobotControlSystem 修改

修改 `RobotControlSystem` 以支持新的连接管理：

```python
class RobotControlSystem:
    def __init__(self, config: Dict[str, Any] = None, use_mock: bool = True, report: bool = True):
        # ... 现有初始化代码 ...

        # 新增：连接管理配置
        grpc_config = self.config.get('grpc_config', {})
        self.reconnect_config = {
            'reconnect_initial_delay': grpc_config.get('reconnect_initial_delay', 1.0),
            'reconnect_max_delay': grpc_config.get('reconnect_max_delay', 60.0),
            'reconnect_max_retries': grpc_config.get('reconnect_max_retries', -1),
            'buffer_max_size': grpc_config.get('buffer_max_size', 1000),
            'buffer_max_age': grpc_config.get('buffer_max_age', 300.0),
            'health_check_interval': grpc_config.get('health_check_interval', 10.0),
        }

    def _init_grpc_client(self) -> bool:
        """初始化gRPC客户端（增强版）"""
        try:
            # 创建通道
            self.channel = grpc.insecure_channel(
                self.server_address,
                options=[
                    ('grpc.keepalive_time_ms', 60000),
                    ('grpc.keepalive_timeout_ms', 20000),
                    ('grpc.http2.max_pings_without_data', 2),
                    ('grpc.keepalive_permit_without_calls', False),
                ]
            )

            self.stub = RobotService_pb2_grpc.RobotServiceStub(self.channel)

            # 使用增强版流管理器
            self.client_upload_manager = EnhancedClientUploadStreamManager(
                self.stub,
                self.robot_id,
                self.reconnect_config
            )
            self.server_command_manager = EnhancedServerCommandStreamManager(
                self.stub,
                self.robot_id,
                self.reconnect_config
            )

            # 注册连接状态回调
            self.client_upload_manager.on_connected = self._on_upload_connected
            self.client_upload_manager.on_disconnected = self._on_upload_disconnected
            self.client_upload_manager.on_reconnecting = self._on_upload_reconnecting

            self.server_command_manager.on_connected = self._on_command_connected
            self.server_command_manager.on_disconnected = self._on_command_disconnected
            self.server_command_manager.on_reconnecting = self._on_command_reconnecting

            # 启动流
            client_upload_started = self.client_upload_manager.start_stream()
            server_command_started = self.server_command_manager.start_stream()

            if client_upload_started and server_command_started:
                self.is_connected = True
                self.logger.info(f"成功建立双向持久化连接，robot_id: {self.robot_id}")
                return True
            else:
                # 即使初始连接失败，也返回 True，因为会自动重连
                self.logger.warning("初始连接失败，将自动重连")
                return True  # 改为返回 True，让系统继续运行

        except Exception as e:
            self.logger.error(f"gRPC客户端初始化失败: {e}")
            return False

    def _on_upload_connected(self):
        """上传流连接成功回调"""
        self.logger.info("上传流已连接")
        self._update_connection_status()

    def _on_upload_disconnected(self):
        """上传流断开回调"""
        self.logger.warning("上传流已断开")
        self._update_connection_status()

    def _on_upload_reconnecting(self, retry_count: int):
        """上传流重连回调"""
        self.logger.info(f"上传流正在重连（第 {retry_count} 次）")

    def _on_command_connected(self):
        """命令流连接成功回调"""
        self.logger.info("命令流已连接")
        self._update_connection_status()

    def _on_command_disconnected(self):
        """命令流断开回调"""
        self.logger.warning("命令流已断开")
        self._update_connection_status()

    def _on_command_reconnecting(self, retry_count: int):
        """命令流重连回调"""
        self.logger.info(f"命令流正在重连（第 {retry_count} 次）")

    def _update_connection_status(self):
        """更新整体连接状态"""
        upload_ok = (
            self.client_upload_manager and
            self.client_upload_manager.connection_state == ConnectionState.CONNECTED
        )
        command_ok = (
            self.server_command_manager and
            self.server_command_manager.connection_state == ConnectionState.CONNECTED
        )
        self.is_connected = upload_ok and command_ok

    def _send_message(self, msg_envelope: MessageEnvelope):
        """发送消息（支持缓冲）"""
        grpc_msg = convert_message_envelope_to_robot_upload_request(msg_envelope)

        # 直接调用，EnhancedStreamManager 会处理缓冲逻辑
        success = self.client_upload_manager.send_message(grpc_msg)

        if not success:
            self.logger.debug("消息已加入缓冲队列，等待连接恢复")

        # 保存到数据库（无论发送是否成功）
        try:
            msg_dict = msg_envelope.to_dict()
            status = 'sent' if success else 'buffered'
            self.task_manager.database.save_sent_message(
                msg_id=msg_dict.get('msg_id'),
                msg_time=msg_dict.get('msg_time'),
                msg_type=msg_dict.get('msg_type'),
                robot_id=msg_dict.get('robot_id'),
                data_json=json.dumps(msg_dict.get('data_json', {})),
                status=status
            )
        except Exception as e:
            self.logger.error(f"保存消息到数据库失败: {e}")
```

---

## 配置示例

更新后的 gRPC 配置：

```python
config = {
    'robot_id': 123456,

    'grpc_config': {
        'server_host': 'localhost',
        'server_port': 50051,
        'connection_timeout': 10,
        'stream_keep_alive_check': 30,

        # 新增：重连配置
        'reconnect_initial_delay': 1.0,     # 初始重连延迟（秒）
        'reconnect_max_delay': 60.0,        # 最大重连延迟（秒）
        'reconnect_max_retries': -1,        # 最大重连次数（-1 = 无限）

        # 新增：消息缓冲配置
        'buffer_max_size': 1000,            # 缓冲区最大消息数
        'buffer_max_age': 300.0,            # 消息最大存活时间（秒）

        # 新增：健康检查配置
        'health_check_interval': 10.0,      # 健康检查间隔（秒）
    }
}
```

---

## 实现步骤

### 阶段 1：基础重连机制

1. 创建 `ConnectionState` 枚举
2. 创建 `ReconnectionManager` 类
3. 修改 `BaseStreamManager._handle_responses()` 添加重连触发
4. 实现 `_reconnect_loop()` 方法

### 阶段 2：消息缓冲

1. 创建 `BufferedMessage` 数据类
2. 创建 `MessageBuffer` 类
3. 修改 `send_message()` 方法支持缓冲
4. 实现 `_flush_buffer()` 方法

### 阶段 3：健康检查

1. 实现 `_health_check_loop()` 方法
2. 添加心跳检测逻辑
3. 基于空闲时间触发重连

### 阶段 4：RobotControlSystem 集成

1. 更新配置结构
2. 使用增强版 StreamManager
3. 注册连接状态回调
4. 修改 `_send_message()` 支持缓冲

---

## 测试场景

### 场景 1：启动时服务器不可用

**预期行为**：
1. 初始连接失败
2. 自动进入重连模式
3. 消息进入缓冲队列
4. 服务器恢复后自动连接并发送缓冲消息

### 场景 2：运行中服务器断开

**预期行为**：
1. 检测到连接断开
2. 触发重连（指数退避）
3. 期间消息持续缓冲
4. 重连成功后自动恢复

### 场景 3：网络不稳定

**预期行为**：
1. 健康检查发现连接异常
2. 主动触发重连
3. 使用抖动避免惊群效应

---

## 注意事项

1. **线程安全**：所有共享状态访问都需要加锁
2. **资源清理**：重连前必须清理旧的流资源
3. **消息顺序**：缓冲消息按优先级排序，重要消息优先发送
4. **内存限制**：设置合理的缓冲区大小，避免内存溢出
5. **日志记录**：记录所有状态变更，便于问题排查
