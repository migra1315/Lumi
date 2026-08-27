# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Lumi is a Python-based robot control system for managing inspection robots. It coordinates AGV (Automated Guided Vehicle), robotic arm, and external axis movements, handles task scheduling, and communicates with a backend server via gRPC bidirectional streaming.

**Language**: Code comments and documentation are in Chinese (中文). When adding new code, follow the existing convention.

## Development Commands

### Initial Setup

```bash
# Install required dependencies (no requirements.txt - install manually)
pip install grpcio grpcio-tools pyserial pymodbus opencv-python numpy

# Install JAKA SDK dependencies (if using real hardware)
# See utils/JAKA_SDK_WINDOWS/LINUX_X86/LINUX_ARM for platform-specific SDKs

# Configure the system
# Edit conf/config.json with your specific settings (robot IP, gRPC server, etc.)
```

### Running the System

```bash
# Test gRPC server (run this first to simulate backend)
python grpc_test_server.py

# Main control system (with gRPC communication)
python RobotControlSystem.py

# Standalone robot control (without gRPC server)
python main_robot.py
```

**Typical development workflow (mock, no hardware):**
1. In `conf/config.json`, set `"use_mock": true` and `"grpc_config.server_port": 50051`
2. Terminal A: `python grpc_test_server.py`
3. Terminal B: `python RobotControlSystem.py`
4. Press `3` in Terminal A to send a task; observe execution logs in Terminal B
5. Inspect `tasks.db` (SQLite) to verify persistence

> **No unit test suite exists.** All functional validation is done via the test-server workflow above.

### Protocol Buffer Compilation

When modifying `gRPC/proto/RobotService.proto`, regenerate Python files:

```bash
python -m grpc_tools.protoc -I=gRPC/proto --python_out=gRPC --grpc_python_out=gRPC gRPC/proto/RobotService.proto
```

### Logging Configuration

Use the unified logging system in `utils/logger_config.py`:

```python
from utils.logger_config import setup_logging, get_logger

# In main entry point (once)
setup_logging(level="INFO", use_color=True, enable_file_logging=True)

# In each module
logger = get_logger(__name__)
```

Log files are stored in `logs/` directory with automatic rotation (10MB, 5 backups).

## Architecture

### Core System Components

**RobotControlSystem** (RobotControlSystem.py)
- Main communication coordinator (single responsibility: gRPC communication)
- Handles gRPC bidirectional streaming (clientUpload and serverCommand)
- Receives commands from backend server and forwards to TaskManager
- Reports robot status, environment data, and device data to server
- Uses StreamManager classes for gRPC stream management
- **Does NOT directly manage robot_controller** (managed by TaskManager)

**TaskManager** (task/TaskManager.py)
- **Fully manages robot_controller** (encapsulated, not exposed externally)
- Unified command entry point via `receive_command()` for ALL command types
- Converts all commands to UnifiedCommand for unified processing
- Coordinates TaskScheduler and TaskDatabase
- Provides methods for RobotControlSystem: `get_robot_status()`, `get_environment_data()`, `execute_emergency_stop()`
- Implements bidirectional callback mechanism to trigger data uploads
- Manages task lifecycle callbacks (on_task_start, on_task_complete, on_task_failed)

**TaskScheduler** (task/TaskScheduler.py)
- Executes ALL command types using priority queue (PriorityQueue)
- Supports command types: TASK_CMD, ROBOT_MODE_CMD, JOY_CONTROL_CMD, CHARGE_CMD, SET_MARKER_CMD, POSITION_ADJUST_CMD
- Coordinates robot movements: AGV navigation → arm positioning → external axis → operations
- **Station retry logic**: Failed stations automatically retry (up to max_retries), then continue with next station
- Triggers station-level callbacks (on_station_start, on_station_complete, on_station_retry)
- Command-level callbacks: on_command_complete, on_command_failed

**TaskDatabase** (task/TaskDatabase.py)
- SQLite database for task and command persistence
- Tables: task_history, unified_commands, environment_data_history, system_config, plus four-table message architecture (client_upload_sent/received, server_command_sent/received)
- Stores task execution history, command execution history, environment data, and communication logs

### Robot Controllers

**RobotControllerBase** (robot/RobotControllerBase.py)
- Abstract base class defining standard robot control interface
- Common state: RobotStatus, battery_level, current_marker
- Key methods: setup_system(), move_to_marker(), move_robot_to_position(), move_ext_to_position(), emergency_stop()

**MockRobotController** (robot/MockRobotController.py)
- Simulation implementation for development and testing
- Configurable success_rate and latency
- Simulates AGV, arm, and external axis movements without hardware

**RobotController** (robot/RobotController.py)
- Real hardware implementation
- Integrates AGVController and ArmController

**AGVController** (robot/AGVController.py)
- Controls AGV via TCP/IP socket communication
- Methods: agv_moveto(), agv_get_status(), agv_estop(), agv_position_adjust()
- Supports data streaming with callbacks

**ArmController** (robot/ArmController.py)
- Extends JAKA SDK for robotic arm control
- Controls both arm joints and external axis via HTTP
- External axis URL endpoints: /moveto, /status, /enable, /reset
- Implements joint limit checking and adjustment

**Environment Sensor Controller** (envsMonitor/AirQualitySensor.py)
- Monitors environmental parameters via serial communication (Modbus RTU)
- Reads temperature, humidity, PM2.5, noise levels
- Optional component controlled by `hardware_config.env_sensor.enabled` in config
- Runs in separate thread with configurable read_interval

**CameraManager** (camera/CameraManager.py)
- Manages Orbbec depth camera for image capture
- Supports RTMP streaming via FFmpeg subprocess
- Thread-safe frame access with configurable resolution and FPS
- States: DISCONNECTED, CONNECTED, STREAMING, ERROR

**CoordinateTransformer** (utils/coordinate_transformer.py)
- Calculates mapping between robot physical coordinates and map pixel coordinates
- Supports similarity and affine transformations
- Used for AGV position visualization on maps

### Data Models

**CommandModels** (dataModels/CommandModels.py)
- CommandEnvelope: Wrapper for all incoming commands
- CmdType: TASK_CMD, ROBOT_MODE_CMD, JOY_CONTROL_CMD, SET_MARKER_CMD, CHARGE_CMD, etc.
- TaskCmd: Task assignment from server

**UnifiedCommand** (dataModels/UnifiedCommand.py)
- UnifiedCommand: Unified wrapper for ALL command types in the system
- CommandStatus: PENDING, QUEUED, RUNNING, COMPLETED, FAILED, CANCELLED, RETRYING
- CommandCategory: TASK, CONTROL, CONFIGURATION
- Automatic priority mapping: JOY_CONTROL_CMD (1), ROBOT_MODE_CMD (2), CHARGE_CMD (4), TASK_CMD (5)
- Used by TaskScheduler's priority queue for unified command execution
- Factory method: `create_unified_command()` for easy creation

**MessageModels** (dataModels/MessageModels.py)
- MessageEnvelope: Wrapper for all outgoing status messages
- MsgType: ROBOT_STATUS, DEVICE_DATA, ENVIRONMENT_DATA, ARRIVE_SERVER_POINT
- Data classes: BatteryInfo, PositionInfo, SystemStatus, EnvironmentInfo, DeviceInfo

**TaskModels** (dataModels/TaskModels.py)
- Task: Container for inspection tasks with multiple stations
- Station: Individual station with StationConfig and execution state
- StationConfig: Defines station_id, agv_marker, robot_pos, ext_pos, operation_config
- OperationConfig: Specifies operation_mode (OPEN_DOOR, CLOSE_DOOR, CAPTURE, SERVICE, NONE)
- **TaskStatus**: PENDING, RUNNING, COMPLETED, PARTIAL_COMPLETED, FAILED, SKIPPED, RETRYING
- **StationTaskStatus**: PENDING, RUNNING, COMPLETED, FAILED, SKIPPED, RETRYING

### gRPC Communication

**Protocol Definition** (gRPC/proto/RobotService.proto)
- Two bidirectional streaming RPCs:
  - `clientUpload`: Robot → Server (status, environment, device data)
  - `serverCommand`: Server → Robot (task commands, mode changes)
- Uses Protocol Buffers with proto3 syntax

**StreamManager** (gRPC/StreamManager.py)
- `BaseStreamManager`: Abstract base with request queue, response handler thread, stats, and shutdown logic
- `ClientUploadStreamManager`: Manages clientUpload stream; includes a keepalive that sends an empty ROBOT_STATUS every 1s queue-timeout cycle
- `ServerCommandStreamManager`: Manages serverCommand stream; spawns a dedicated heartbeat thread at configurable interval (default 30s)
- Supports `on_stream_broken` and `on_message_send_failed` callbacks for reconnection handling

**Offline Message Handler** (utils/offline_message_handler.py)
- Handles protobuf binary serialization for offline message storage
- Supports both `RobotUploadRequest` (clientUpload) and `ClientStreamMessage` (serverCommand) message types
- Functions: `serialize_message()`, `deserialize_message()`, `get_stream_type_for_message()`

**Data Converter** (utils/dataConverter.py)
- Converts between protobuf messages and Python dataclasses
- Functions: convert_server_message_to_command_envelope(), convert_message_envelope_to_robot_upload_request()

### gRPC Auto-Reconnection Mechanism

**Connection State Machine** (RobotControlSystem.py)
- `ConnectionState` enum manages connection lifecycle: DISCONNECTED → CONNECTING → CONNECTED ↔ RECONNECTING
- State transitions are thread-safe via `_connection_lock`

**Heartbeat Monitoring Thread**
- Monitors connection health by checking last heartbeat timestamp
- Triggers reconnection when heartbeat timeout exceeded (default 60s)
- Runs in `_heartbeat_monitor_thread`

**Reconnect Manager Thread**
- Handles reconnection with exponential backoff: 2s → 4s → 8s → 16s → 30s (max)
- Triggered by `_reconnect_trigger` event
- Runs in `_reconnect_manager_thread`
- On successful reconnect, flushes cached offline messages

**Offline Message Caching**
- When connection is lost, outgoing messages are cached to `offline_messages` table
- Messages are serialized using protobuf binary format via `offline_message_handler.py`
- On reconnect, messages are replayed in chronological order with configurable batch size
- TTL-based cleanup removes stale messages (default 24 hours)

**ReconnectStatistics Class**
- Tracks reconnection metrics: attempts, successes, failures
- Tracks offline message stats: cached, sent, failed
- Accessible via `_reconnect_stats.to_dict()`

## Configuration

### System Config

**Primary configuration file**: `conf/config.json` (loaded at runtime)

The system reads configuration from a JSON file. Example structure:

```json
{
    "robot_id": 12345,
    "robot_config": {
        "success_rate": 0.95,
        "latency": 10,
        "robot_ip": "192.168.10.90",
        "ext_base_url": "http://192.168.10.90:5000/api/extaxis",
        "agv_ip": "192.168.10.10",
        "agv_port": 31001
    },
    "grpc_config": {
        "server_host": "192.168.8.93",
        "server_port": 9898,
        "connection_timeout": 10,
        "stream_keep_alive_check": 30,
        "reconnect": {
            "base_delay": 2,
            "max_delay": 30,
            "heartbeat_timeout": 60,
            "batch_size": 10,
            "batch_interval": 0.1,
            "max_offline_messages": 10000,
            "offline_message_ttl_hours": 24
        }
    },
    "hardware_config": {
        "auto_start_on_boot": true,
        "robot": {
            "enabled": true
        },
        "camera": {
            "enabled": true
        },
        "env_sensor": {
            "enabled": true
        }
    },
    "env_sensor_config": {
        "port": "COM4",
        "baudrate": 4800,
        "address": 1,
        "read_interval": 5
    },
    "camera_config": {
        "camera_type": "orbbec",
        "resolution": {"width": 1280, "height": 720},
        "fps": 30,
        "capture_count": 2,
        "capture_interval": 0.5,
        "capture_quality": 95,
        "stream_config": {
            "enabled": true,
            "rtmp_url": "rtmp://192.168.8.93/live/test",
            "bitrate": "2000k",
            "reconnect": {
                "base_delay": 2,
                "max_delay": 30,
                "max_attempts": 10,
                "jitter_factor": 0.3,
                "stable_reset_seconds": 60
            }
        }
    },
    "use_mock": false,
    "log_config": {
        "level": "INFO",
        "use_color": true,
        "enable_file_logging": true
    }
}
```

See `conf/config.json` for the active configuration and `robot/config_example.py` for Python dictionary format.

### Robot Mode Transitions

The system supports multiple robot modes (defined in proto):
- INSPECTION: Normal inspection mode
- SERVICE: Service mode
- JOY_CONTROL: Manual joystick control
- ESTOP: Emergency stop
- CHARGE: Charging mode
- STAND_BY: Standby mode
- CONFIGURATION: Configuration mode

Mode changes are triggered by ROBOT_MODE_CMD from server.

## Command Execution Flow (Post-Refactoring)

### Unified Command Processing

ALL command types now follow this unified flow:

1. **Server → RobotControlSystem**: Server sends command (any type) via serverCommand stream
2. **RobotControlSystem → TaskManager**: RobotControlSystem._handle_command() forwards to TaskManager.receive_command()
3. **TaskManager**: Creates UnifiedCommand with automatic priority assignment
4. **TaskManager → TaskScheduler**: Adds UnifiedCommand to priority queue
5. **TaskScheduler**: Dequeues by priority and routes to appropriate executor:
   - TASK_CMD → _execute_task_command() (multi-station inspection)
   - ROBOT_MODE_CMD → _execute_mode_command() (mode switching)
   - JOY_CONTROL_CMD → _execute_joy_command() (joystick control)
   - CHARGE_CMD → _execute_charge_command() (charging)
   - SET_MARKER_CMD → _execute_set_marker_command() (marker setting)
   - POSITION_ADJUST_CMD → _execute_position_adjust_command() (position adjustment)
6. **Execute & Update**: Update command status, retry if failed (up to max_retries)
7. **Callback**: TaskManager triggers system callbacks → RobotControlSystem auto-uploads data
8. **Database**: Save command execution history to unified_commands table

### Task (TASK_CMD) Execution Detail

For multi-station inspection tasks:

1. TaskScheduler._execute_task_command() processes Task object
2. For each Station in task:
   - Move AGV to station.agv_marker
   - Move arm to station.robot_pos (home position)
   - Move external axis to station.ext_pos (home position)
   - Execute operation_config (open door, close door, capture, etc.)
   - **If station fails**: Automatically retry (up to max_retries), then mark as FAILED and continue to next station
3. Trigger callbacks: on_station_start, on_station_complete, on_station_retry, on_arrive_station
4. **Task status determination**:
   - All stations successful → COMPLETED
   - Some stations failed → PARTIAL_COMPLETED
   - All stations failed → FAILED
5. Update task status in unified_commands table with detailed metadata (station counts, failed IDs)
6. Report status back to server via serverCommand stream callbacks (auto-triggered by on_command_status_change / on_task_progress)

## Database Schema

**Database Location**: `tasks.db` (SQLite database created in the working directory)

### Core Tables

**unified_commands table**
- command_id (PK), cmd_type, category, priority, status, data_json
- created_at, started_at, completed_at
- retry_count, max_retries, error_message, metadata_json
- Stores ALL command types (Task, Mode, Joy, Charge, etc.) with unified status tracking
- Indexed on: status, created_at, priority+status

**task_history table**
- Records station-level execution: task_id, station_id, action, status, timestamp, details
- Provides detailed execution timeline for multi-station tasks

**environment_data_history table**
- id (PK), timestamp, robot_id, position_x, position_y, position_theta
- temperature, humidity, oxygen, carbon_dioxide, pm25, pm10, etvoc, noise, metadata_json
- Stores historical environment sensor readings
- Indexed on: timestamp, robot_id

**system_config table**
- config_key (PK), config_value, config_type, description, updated_at
- Stores runtime system configuration
- Supports types: string, int, float, json, bool

**offline_messages table**
- id (PK), msg_id (UNIQUE), stream_type, msg_time, msg_type, robot_id, payload_blob, created_at
- Caches outgoing messages during connection loss for replay after reconnect
- Indexed on: (stream_type, msg_time), created_at
- Methods: `save_offline_message()`, `get_offline_messages()`, `delete_offline_message()`, `delete_offline_messages_batch()`, `cleanup_old_offline_messages()`, `get_offline_message_count()`

### Communication Logs (Four-Table Architecture)

The system uses four separate tables to track gRPC communication by stream and direction:

**client_upload_sent**
- Messages sent via clientUpload stream (robot → server)
- Content: ROBOT_STATUS, ENVIRONMENT_DATA, DEVICE_DATA
- Indexed on: msg_time, msg_type

**client_upload_received**
- Responses received via clientUpload stream (server → robot)
- Content: Upload acknowledgments and responses
- Indexed on: msg_time, processed

**server_command_received**
- Commands received via serverCommand stream (server → robot)
- Content: TASK_CMD, ROBOT_MODE_CMD, JOY_CONTROL_CMD, etc.
- Indexed on: msg_time, processed

**server_command_sent**
- Responses sent via serverCommand stream (robot → server)
- Content: COMMAND_STATUS_UPDATE, TASK_PROGRESS_UPDATE, OPERATION_RESULT, SET_MARKER_RESPONSE
- Indexed on: msg_time, msg_type, command_id

You can inspect the database using any SQLite browser tool (e.g., DB Browser for SQLite, DBeaver).
Command-line access:
- **Linux/Mac**: `sqlite3 tasks.db`
- **Windows**: Download sqlite3.exe or use Python: `python -c "import sqlite3; conn = sqlite3.connect('tasks.db'); ..."`

Query examples:
```sql
-- Recent commands
SELECT * FROM unified_commands ORDER BY created_at DESC LIMIT 10;

-- Environment history
SELECT * FROM environment_data_history ORDER BY timestamp DESC LIMIT 100;

-- Failed stations in recent tasks
SELECT task_id, station_id, details FROM task_history
WHERE status = 'FAILED' ORDER BY timestamp DESC LIMIT 20;

-- Communication logs by stream
SELECT * FROM client_upload_sent ORDER BY msg_time DESC LIMIT 50;
SELECT * FROM server_command_received WHERE processed = 0;
SELECT * FROM server_command_sent WHERE command_id = 'cmd_123';

-- Offline messages pending replay
SELECT * FROM offline_messages ORDER BY msg_time ASC;
SELECT COUNT(*) FROM offline_messages WHERE stream_type = 'client_upload';
```

## Important Implementation Notes

### Architecture Responsibilities

**Single Responsibility Principle**:
- **RobotControlSystem**: gRPC communication ONLY. Does not manage robot_controller.
- **TaskManager**: Robot control and command management. Fully encapsulates robot_controller.
- **TaskScheduler**: Command execution with priority queue.

**Key Rule**: Never access robot_controller directly from RobotControlSystem. Always use TaskManager methods:
- `task_manager.get_robot_status()` - Get current robot status
- `task_manager.get_environment_data()` - Get environment sensor data
- `task_manager.execute_emergency_stop()` - Execute emergency stop

### Callback Mechanism

**Callback Architecture**:
```
TaskScheduler (调度层)
    ↓ 触发回调
TaskManager (管理层)
    ↓ 触发系统回调
RobotControlSystem (通信层)
    ↓ 上报数据到服务器
```

**Design Principles**:
- Single-direction flow: Events propagate from lower to upper layers
- No circular callbacks or duplicate registrations
- Each layer handles only its own responsibilities

**TaskScheduler Callbacks** (registered by TaskManager):
- `on_task_start`, `on_task_complete`, `on_task_failed`
- `on_station_start`, `on_station_complete`, `on_station_retry`, `on_station_progress`
- `on_command_complete`, `on_command_failed`, `on_command_status_change`
- `on_operation_result` — carries `operation_data` dict with `operation_mode` and `result`

**TaskManager System Callbacks** (registered by RobotControlSystem):
- `on_command_status_change`: Fired on any command status change; RobotControlSystem sends `CommandStatusUpdate` via serverCommand stream
- `on_task_progress`: Fired on station start/complete/progress; RobotControlSystem sends `TaskProgressUpdate`
- `on_operation_result`: Fired when a station operation completes; RobotControlSystem sends `OperationResult`

**Registration Example**:
```python
# In RobotControlSystem.start()
self.task_manager.register_system_callback("on_command_status_change", self._handle_command_status_callback)
self.task_manager.register_system_callback("on_task_progress", self._handle_task_progress_callback)
self.task_manager.register_system_callback("on_operation_result", self._handle_operation_result_callback)
```

See `docs/阶段1/回调函数说明文档.md` for complete callback documentation.

### Using Mock vs Real Controllers

Set `use_mock` in config or pass to RobotControlSystem constructor:
- `use_mock=True`: Uses MockRobotController for testing (TaskManager creates it)
- `use_mock=False`: Uses RobotController with real AGV/arm hardware (TaskManager creates it)

**Note**: robot_controller is created and managed entirely by TaskManager, not RobotControlSystem.

### JAKA SDK Integration

The robot/jaka.py file wraps the JAKA SDK (located in utils/JAKA_SDK_WINDOWS/LINUX_X86/LINUX_ARM):
- SDK provides low-level arm control: joint_move_origin(), jaka_connect(), robot_disconnect()
- ArmController extends this with external axis control
- Platform-specific SDK loaded automatically at runtime

### External Axis Control

External axis is controlled via HTTP REST API (not JAKA SDK):
- Base URL configured in system_config["ext_base_url"]
- POST to /moveto with {"point": [j1, j2, j3, j4], "vel": 100, "acc": 100}
- Joint limits are checked and adjusted before movement

### Station and Command Retry Logic

**Station-level retries**:
- Each station has independent retry logic with max_retries (default 3)
- Failed station automatically retries with 1-second interval
- After max_retries exhausted, station marked as FAILED
- **Key behavior**: Failed stations do NOT block subsequent stations - execution continues
- Station status and retry_count tracked in database

**Command-level retries**:
- Commands can also retry at the command level
- Retry state tracked in unified_commands table

**Task status determination**:
```python
# All stations succeed → TaskStatus.COMPLETED
# Some stations fail → TaskStatus.PARTIAL_COMPLETED
# All stations fail → TaskStatus.FAILED
```

**Command metadata** includes detailed statistics:
```python
{
    "total_stations": 5,
    "success_stations": 3,
    "failed_stations": 2,
    "failed_station_ids": ["station_2", "station_4"]
}
```

### Two Outgoing Message Paths

Messages from the robot reach the server over **two independent streams**, each with its own purpose:

| Stream | Manager | Sent via | Content |
|--------|---------|----------|---------|
| `clientUpload` | `ClientUploadStreamManager` | `_send_message()` → `convert_message_envelope_to_robot_upload_request()` | Periodic status (every 10s) and environment data (every 30s) |
| `serverCommand` | `ServerCommandStreamManager` | Direct `send_message(ClientStreamMessage)` | Event-driven: `CommandStatusUpdate`, `TaskProgressUpdate`, `OperationResult`, `SetMarkerResponse` |

When modifying what the robot reports, determine which path applies before changing code. The periodic uploads go through `MessageEnvelope` → protobuf conversion; the event-driven responses construct `ClientStreamMessage` directly in `RobotControlSystem`.

### Thread Safety

The system uses multiple threads:
- gRPC stream threads (managed by grpc library)
- `TaskScheduler._scheduler_loop` thread — dequeues from `PriorityQueue`
- `TaskScheduler.executor` — a `ThreadPoolExecutor(max_workers=1)`, so only one command executes at a time
- AGV data monitoring thread (in AGVController)
- Environment sensor reading thread (if enabled)
- `ServerCommandStreamManager` heartbeat thread
- `_heartbeat_monitor_thread` — monitors connection health and triggers reconnection
- `_reconnect_manager_thread` — handles reconnection with exponential backoff

Always use thread-safe operations when accessing shared state. The priority queue itself is thread-safe; `current_task` / `current_station` on the scheduler are written only by the executor thread and read by callback chains — no lock is used, so treat them as eventually consistent when accessed from other threads. Connection state changes use `_connection_lock` for thread safety.

## Common Development Patterns

### Adding New Command Types

1. **Define in Proto**: Add enum value to CmdType in RobotService.proto
2. **Define Message Structure**: Create command message structure in proto
3. **Add to Oneof**: Add to ServerStreamMessage oneof
4. **Recompile**: `python -m grpc_tools.protoc -I=gRPC/proto --python_out=gRPC --grpc_python_out=gRPC gRPC/proto/RobotService.proto`
5. **Add to CommandModels**: Create corresponding dataclass in CommandModels.py
6. **Map Priority**: Add to CMD_TYPE_DEFAULT_PRIORITY in UnifiedCommand.py
7. **Map Category**: Add to CMD_TYPE_TO_CATEGORY in UnifiedCommand.py
8. **Implement Executor**: Add _execute_xxx_command() method in TaskScheduler.py
9. **Route Command**: Add case in TaskScheduler._execute_command() to route to new executor
10. **No Changes Needed** in RobotControlSystem (unified handling via receive_command)

### Adding New Operation Modes

1. Add to OperationMode enum in RobotService.proto and TaskModels.py
2. Implement in TaskScheduler._execute_operation()
3. Update station config JSON to include new mode
4. Recompile proto files

### Extending Robot Status

1. Modify RobotStatusUpload in proto
2. Update MessageModels.py dataclasses
3. Update TaskManager (if new robot_controller methods needed)
4. Update RobotControlSystem._report_robot_status() to use TaskManager methods
5. Recompile proto files

### Querying Command Execution Status

```python
# Get status of a specific command
status = task_manager.get_command_status("command_id_123")
# Returns: {"command_id": "...", "status": "completed", "created_at": "...", ...}

# Cancel a command
success = task_manager.cancel_command("command_id_123")
```

## Testing Strategy

### Using Mock Controller

Set `use_mock=True` when initializing RobotControlSystem for testing without hardware:
- MockRobotController simulates all robot movements with configurable success_rate
- Useful for development, CI/CD, and integration testing
- Check tasks.db SQLite database for task history and execution logs

### Using Test gRPC Server

`grpc_test_server.py` is a backend simulator that listens on **port 50051** (hardcoded). Before running the client against it, update `conf/config.json` → `grpc_config.server_port` to `50051`.

```bash
# Terminal 1
python grpc_test_server.py

# Terminal 2  (with use_mock=true and server_port=50051 in config.json)
python RobotControlSystem.py
```

**Keyboard controls** (in the test server terminal, while it is running):
| Key | Action |
|-----|--------|
| `1` | Send CHARGE_CMD |
| `2` | Send ROBOT_MODE_CMD (INSPECTION) |
| `3` | Send TASK_CMD (6-station default task) |
| `4` | Send JOY_CONTROL_CMD |
| `5` | Send POSITION_ADJUST_CMD |
| `a` | Toggle auto-send mode (off by default) |
| `q` | Quit server |

All commands are sent via the serverCommand stream. Robot responses (status uploads, progress updates, operation results) appear in the server logs.

## SDK Directories

- `utils/JAKA_SDK_WINDOWS`: JAKA SDK for Windows platform
- `utils/JAKA_SDK_LINUX_X86`: JAKA SDK for x86 Linux
- `utils/JAKA_SDK_LINUX_ARM`: JAKA SDK for ARM Linux

`robot/jaka.py` selects the appropriate SDK automatically at runtime.

## Backward Compatibility

- `TaskManager.receive_task_from_cmd()` and `receive_task_from_dict()` are **commented out** — use `TaskManager.receive_command(CommandEnvelope)` exclusively.
- `TaskDatabase` retains compat shim methods (`save_sent_message`, `save_received_message`, `mark_message_processed`) that internally route to the correct four-table targets.
- Database migrations are non-destructive.
