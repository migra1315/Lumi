# 需求1: gRPC自动重连机制 — 详细解决方案

---

## 1. 现状分析

### 1.1 当前连接流程

```
RobotControlSystem.start()
    └── _init_grpc_client()          // 创建channel、stub、流管理器
            ├── ClientUploadStreamManager.start_stream()
            └── ServerCommandStreamManager.start_with_heartbeat()
```

**关键问题点**：

| 位置 | 现状 | 问题 |
|------|------|------|
| `_init_grpc_client()` | 仅在 `start()` 调用一次 | 连接断开后无重连入口 |
| `BaseStreamManager._handle_responses()` | `grpc.RpcError` 异常仅记录日志，设置 `is_stream_active = False` | 断线后流静默死亡，无恢复机制 |
| `BaseStreamManager.send_message()` | 流非活跃时直接返回 `False` | 断线期间消息丢失 |
| `ServerCommandStreamManager` | 心跳仅用于保持连接（keepalive） | 无法感知断线（服务端宕机时心跳发送不会立即报错） |
| `_reporting_loop()` | 上报消息直接调用 `client_upload_manager.send_message()` | 断线期间状态数据全部丢失 |

### 1.2 涉及的文件

| 文件 | 修改内容 |
|------|---------|
| `gRPC/StreamManager.py` | 在 `BaseStreamManager` 增加断线检测回调；修改 `send_message` 在断线时缓存消息 |
| `RobotControlSystem.py` | 增加重连管理器和重连循环线程；增加断线缓存消息的重发逻辑 |
| `task/TaskDatabase.py` | 增加 `offline_messages` 表及增删改查方法 |
| `conf/config.json` | 增加 `reconnect` 配置项 |

---

## 2. 设计方案

### 2.1 整体架构

```
┌──────────────────────────────────────────────────────┐
│                 RobotControlSystem                    │
│                                                      │
│  ┌─────────────┐    断线事件     ┌────────────────┐  │
│  │  Stream     │ ─────────────► │  ReconnectMgr  │  │
│  │  Managers   │                │  (重连管理器)   │  │
│  │             │ ◄──────────── │  - 指数退避     │  │
│  │  断线时将   │   重连成功     │  - 重建流       │  │
│  │  消息写入   │               │  - 触发缓存重发 │  │
│  │  离线队列   │               └────────────────┘  │
│  └─────┬───────┘                                    │
│        │ 写入                                        │
│        ▼                                             │
│  ┌─────────────┐                                    │
│  │  offline_   │  SQLite持久化                      │
│  │  messages   │  (系统崩溃也不丢失)                │
│  └─────────────┘                                    │
└──────────────────────────────────────────────────────┘
```

### 2.2 核心机制分解

#### 机制A — 断线感知

当前 `_handle_responses()` 在收到 `grpc.RpcError` 后设置 `is_stream_active = False` 即结束。需要在此处增加一个**断线回调**通知上层：

```
BaseStreamManager._handle_responses()
    catch grpc.RpcError
        → is_stream_active = False
        → 调用 on_stream_broken(stream_name)  ← 新增回调
```

`RobotControlSystem` 注册该回调，收到通知后将自身连接状态置为 `DISCONNECTED`，触发重连流程。

> **心跳与断线感知的关系**：gRPC 的 keepalive ping 在服务端宕机时不能即时报错（TCP层的慢超时）。因此还需要一个**应用层超时判断**：若 `last_activity` 超过 `heartbeat_timeout`（建议 60s，即两个心跳周期）未更新，主动判定断线。此检查放在重连管理器的轮询循环中。

#### 机制B — 离线消息缓存

断线期间，所有需要发送的消息不再丢弃，而是写入 SQLite 的 `offline_messages` 表。涉及两个发送路径：

1. **clientUpload 流**（`_send_message` → 状态上报、环境数据）
2. **serverCommand 流**（`_send_command_status_update`、`_send_task_progress_update`、`_send_operation_result`）

修改策略：在 `send_message()` 处，若流非活跃，调用 `_cache_offline_message()` 写入数据库，返回 `True`（对调用方透明）。

#### 机制C — 指数退避重连

重连间隔序列：`2s, 4s, 8s, 16s, 30s, 30s, ...`（上限 30s，无重试次数上限）。

```python
delay = min(base_delay * (2 ** attempt), max_delay)
# base_delay=2, max_delay=30
# attempt: 0→2s, 1→4s, 2→8s, 3→16s, 4→30s, 5+→30s
```

重连成功后 `attempt` 归零。

#### 机制D — 缓存消息重发

重连成功后，从 `offline_messages` 表按 `msg_time ASC` 分批读取，依次发送。发送成功后标记为 `sent` 并删除（或保留用于追溯，见下）。

为避免流量激增，每批发送 10 条，批间间隔 100ms。

---

## 3. 具体实现

### 3.1 配置变更 — `conf/config.json`

在 `grpc_config` 下新增：

```json
"grpc_config": {
    "server_host": "192.168.8.93",
    "server_port": 9898,
    "connection_timeout": 10,
    "stream_keep_alive_check": 30,
    "reconnect": {
        "base_delay": 2,
        "max_delay": 30,
        "heartbeat_timeout": 60,
        "reconnect_batch_size": 10,
        "reconnect_batch_interval": 0.1
    }
}
```

| 参数 | 含义 | 默认值 |
|------|------|--------|
| `base_delay` | 指数退避基础延迟（秒） | 2 |
| `max_delay` | 最大重连间隔（秒） | 30 |
| `heartbeat_timeout` | 超过此秒数无活动则判定断线 | 60 |
| `reconnect_batch_size` | 重发缓存消息每批条数 | 10 |
| `reconnect_batch_interval` | 批间间隔（秒） | 0.1 |

### 3.2 数据库变更 — `task/TaskDatabase.py`

新增 `offline_messages` 表：

```sql
CREATE TABLE IF NOT EXISTS offline_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    msg_id TEXT UNIQUE NOT NULL,
    stream_type TEXT NOT NULL,      -- 'client_upload' 或 'server_command'
    msg_time INTEGER NOT NULL,
    msg_type TEXT NOT NULL,
    robot_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,     -- 序列化后的消息原始数据
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_offline_stream_type
    ON offline_messages(stream_type, msg_time);
```

对应新增方法：

```python
def save_offline_message(self, msg_id, stream_type, msg_time, msg_type, robot_id, payload_json):
    """保存离线消息"""

def get_offline_messages(self, stream_type=None, limit=100) -> List[Dict]:
    """按 msg_time ASC 读取离线消息，用于重发"""

def delete_offline_message(self, msg_id):
    """重发成功后删除"""

def clear_offline_messages(self):
    """全部清空（可选，用于维护）"""
```

**关于 `payload_json` 的序列化**：需要保存的是 protobuf 消息对象。使用 `google.protobuf.json_format.MessageToJson()` 序列化，重发时用 `ParseDict()` 反序列化。序列化方法与消息类型信息一起存储，确保可逆。

### 3.3 StreamManager 变更 — `gRPC/StreamManager.py`

在 `BaseStreamManager` 中：

1. 增加 `on_stream_broken` 回调属性（初始为 `None`）。
2. 在 `_handle_responses()` 的 `except grpc.RpcError` 和 `finally` 分支中，若是非正常停止（`shutdown_event` 未设置），调用 `on_stream_broken`。

```python
# BaseStreamManager.__init__ 新增：
self.on_stream_broken: Optional[Callable[[str], None]] = None

# _handle_responses 的 finally 分支修改：
finally:
    self.is_stream_active = False
    if not self.shutdown_event.is_set() and self.on_stream_broken:
        self.on_stream_broken(self.__class__.__name__)
    self.logger.info("响应处理线程结束")
```

### 3.4 重连管理器 — `RobotControlSystem` 内新增类/方法

在 `RobotControlSystem` 中新增一个内部重连管理线程，由以下状态机驱动：

```
CONNECTED ──── 断线感知 ──→ DISCONNECTED ──── 重连成功 ──→ CONNECTED
                                  │
                                  ▼
                            RECONNECTING
                       (指数退避循环重试)
```

核心流程：

```python
def _reconnect_loop(self):
    """重连循环（独立线程）"""
    attempt = 0
    while not self._stop_reconnect:
        delay = min(self.base_delay * (2 ** attempt), self.max_delay)
        # 分段 sleep，支持快速退出
        for _ in range(int(delay * 10)):
            if self._stop_reconnect:
                return
            time.sleep(0.1)

        if self._try_reconnect():
            self.logger.info("重连成功")
            attempt = 0                  # 归零
            self._flush_offline_messages()  # 重发缓存
            break
        else:
            attempt += 1
            self.logger.warning(f"重连失败，第 {attempt} 次尝试，下次延迟 {min(self.base_delay * (2 ** attempt), self.max_delay)}s")

def _try_reconnect(self) -> bool:
    """单次重连尝试"""
    # 1. 关闭旧的 channel/stream（如果还活着）
    # 2. 调用 _init_grpc_client() 重新建立
    # 3. 重新注册响应处理器
    # 4. 返回是否成功
```

### 3.5 断线检测的双重机制

| 检测方式 | 触发条件 | 实现位置 |
|---------|---------|---------|
| 被动检测 | `grpc.RpcError` 抛出 | `BaseStreamManager._handle_responses` → `on_stream_broken` 回调 |
| 主动检测 | `last_activity` 超过 `heartbeat_timeout` | `_reconnect_loop` 轮询检查（每 5s 一轮） |

两种方式任一触发均启动重连。为避免重复触发，用一个锁（`threading.Lock`）保护连接状态变更：

```python
# 连接状态枚举
class ConnectionState(Enum):
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    RECONNECTING = "reconnecting"
```

### 3.6 缓存消息重发流程

```
重连成功
    ↓
_flush_offline_messages()
    ↓
按 stream_type 分组：
    ├── client_upload: 逐条调用 client_upload_manager.send_message()
    └── server_command: 逐条调用 server_command_manager.send_message()
    ↓
每 batch_size 条发送后 sleep(batch_interval)
    ↓
每条发送成功后 delete_offline_message(msg_id)
    ↓
全部发送完毕，记录日志
```

**重发时消息需要从 `payload_json` 反序列化回对应的 protobuf 对象**。因此 `offline_messages` 表的 `msg_type` 字段需要精确标识 protobuf 消息类（如 `RobotUploadRequest`、`ClientStreamMessage`），以便正确反序列化。

---

## 4. 流程对比

### 4.1 断线前（正常状态）

```
_reporting_loop 生成状态消息
    → _send_message()
        → convert_message_envelope_to_robot_upload_request()
        → client_upload_manager.send_message()  ← 放入队列 → 发送
        → 数据库记录（client_upload_sent）
```

### 4.2 断线后（离线缓存）

```
_reporting_loop 生成状态消息
    → _send_message()
        → convert_message_envelope_to_robot_upload_request()
        → client_upload_manager.send_message()
            → is_stream_active == False
            → 调用 RobotControlSystem._cache_offline_message()
                → 序列化 protobuf → 写入 offline_messages 表
        → 数据库记录（client_upload_sent，状态为 pending）
```

### 4.3 重连后（缓存重发）

```
_try_reconnect() 成功
    → 重建 channel/stub/stream managers
    → 注册响应处理器
    → _flush_offline_messages()
        → 从 offline_messages 表读取（按 msg_time ASC）
        → 分批反序列化并发送
        → 逐条删除已发送记录
    → 更新连接状态为 CONNECTED
    → 恢复定时上报
```

---

## 5. 关键边界情况

| 场景 | 处理策略 |
|------|---------|
| 重连期间重连线程本身崩溃 | 心跳超时主动检测会在下一轮重新触发重连 |
| 服务端重启时间很短（<2s） | 第一次重连尝试即可成功，无需等待完整退避 |
| 离线消息量极大（如几小时断线） | 分批重发（每批10条），避免单次流量激增 |
| 重连成功但重发中再次断线 | `send_message` 再次缓存到 `offline_messages`，下次重连继续重发；已删除的已发送条不会重复 |
| 程序崩溃后重启 | `offline_messages` 基于 SQLite 持久化，不会丢失；重启后重新连接时自动重发 |
| 心跳发送本身失败 | 不影响断线检测（主动检测依赖 `last_activity` 时间戳，不依赖心跳成功） |
| 两个流（clientUpload / serverCommand）先后断线 | 任一流断线即触发重连；重连时两个流同时重建 |

---

## 6. 测试方案

### 6.1 单元测试

| 测试用例 | 验证内容 |
|---------|---------|
| `test_exponential_backoff` | 延迟序列正确：2, 4, 8, 16, 30, 30 |
| `test_offline_message_cache` | 断线时消息写入 `offline_messages`，重连后全部重发并删除 |
| `test_reconnect_state_machine` | 状态转换：CONNECTED → DISCONNECTED → RECONNECTING → CONNECTED |
| `test_stream_broken_callback` | 模拟 `grpc.RpcError`，验证 `on_stream_broken` 被触发 |
| `test_flush_ordering` | 离线消息按 `msg_time ASC` 顺序重发 |
| `test_flush_batch_limit` | 每批仅发送 `batch_size` 条，间隔 `batch_interval` |

### 6.2 集成测试（配合 `grpc_test_server.py`）

| 步骤 | 操作 | 预期结果 |
|------|------|---------|
| 1 | 启动 `grpc_test_server.py` + `RobotControlSystem.py`，确认正常通信 | 日志中持续出现状态上报 |
| 2 | 停止 `grpc_test_server.py` | 30s 内日志出现断线感知 + 开始重连 |
| 3 | 等待 10s（期间产生约 1 条状态上报 + 离线缓存） | 日志出现离线消息缓存记录 |
| 4 | 重启 `grpc_test_server.py` | 客户端自动重连成功，离线缓存消息全部重发 |
| 5 | 查看服务端日志 | 收到了断线期间的所有状态消息 |
| 6 | 查看 `tasks.db` 的 `offline_messages` 表 | 表为空（已重发删除） |

### 6.3 验收对照

| 验收标准（原需求） | 对应测试 |
|------------------|---------|
| 手动关闭服务端后，30秒内客户端开始重连 | 集成测试步骤2（`heartbeat_timeout=60s` 为兜底，实际 RpcError 会更快触发） |
| 服务端重启后，客户端自动连接成功 | 集成测试步骤4 |
| 断线期间发送的100条状态消息在重连后全部送达 | 可调整 `status_interval` 为1s，断线100s，验证100条均重发 |
| 断线期间执行的任务，重连后服务端能看到完整结果 | 任务执行回调（`_send_task_progress_update`、`_send_operation_result`）同样经过离线缓存路径，自动覆盖 |

---

## 7. 实现优先级与依赖

```
① 数据库层：新增 offline_messages 表及方法          (无依赖，先做)
        ↓
② StreamManager：增加 on_stream_broken 回调          (依赖①)
        ↓
③ RobotControlSystem：
    ├── 连接状态机 + 重连循环线程                     (依赖②)
    ├── _cache_offline_message 缓存入口              (依赖①)
    └── _flush_offline_messages 重发逻辑             (依赖①③)
        ↓
④ 配置变更：grpc_config.reconnect                    (无依赖，可并行)
        ↓
⑤ 测试                                              (依赖①②③)
```

---

## 8. 备注

- 本方案中任务执行本身不受断线影响。`TaskScheduler` 独立运行，断线期间任务继续执行。任务完成后触发的回调消息（进度更新、操作结果）通过离线缓存路径保存，重连后自动同步。这与需求3（断线数据持久化和同步）高度耦合，本方案仅负责**消息级缓存+重发**，任务快照级同步见需求3方案。
- `offline_messages` 表不做自动清理。建议定期维护脚本或在系统正常运行超过24h后清理超龄记录（如 > 7天的已删除项）。
- 若未来需要保留离线消息用于审计，可将删除改为标记状态（`status='resent'`），不影响本方案核心逻辑。
