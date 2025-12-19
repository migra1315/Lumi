# DOCs
## 一、系统总体架构图
```mermaid
graph TB
    subgraph "用户/外部系统层"
        UI[Web/桌面用户界面]
        Server[上级调度服务器]
        API[REST API接口]
    end

    subgraph "应用服务层"
        TM[任务管理器 TaskManager]
        TS[任务调度器 TaskScheduler]
        TR[任务接收器 TaskReceiver]
        DB[(任务数据库)]
    end

    subgraph "机器人控制层"
        RCB[RobotControllerBase<br/>抽象基类]
        Mock[MockRobotController<br/>模拟实现]
        Real[RealRobotController<br/>真实实现]
        
        subgraph "真实硬件控制器"
            AGV[AGVController]
            ARM[ArmController<br/>（集成机械臂+外部轴）]
        end
    end

    subgraph "硬件层"
        AGV_HW[AGV硬件]
        Robot_HW[机械臂硬件]
        Ext_HW[外部轴硬件]
    end

    UI --> API
    Server --> TR
    TR --> TM
    TM --> TS
    TS --> RCB
    RCB --> Mock
    RCB --> Real
    Real --> AGV
    Real --> ARM
    AGV --> AGV_HW
    ARM --> Robot_HW
    ARM --> Ext_HW
    
    TM --> DB
    TS --> DB
    
    style RCB fill:#e1f5fe
    style Mock fill:#f3e5f5
    style Real fill:#e8f5e8
```

## 二、详细类图
### 2.1 核心类继承体系
```mermaid

classDiagram
    class RobotControllerBase {
        <<abstract>>
        #config: Dict
        #debug: bool
        #status: RobotStatus
        #battery_level: float
        #battery_status: BatteryStatus
        #current_marker: str
        #last_error: str
        #_system_initialized: bool
        +logger: Logger
        +callbacks: Dict
        
        +setup_system() bool
        +shutdown_system()
        +move_to_marker(marker_id: str) bool
        +move_robot_to_position(position: List[float]) bool
        +move_ext_to_position(position: List[float]) bool
        +get_status() Dict
        +emergency_stop() bool
        +reset_errors() bool
        +open_door(door_id: str) bool
        +close_door(door_id: str) bool
        +charge(duration: float) bool
        +register_callback(event: str, callback: Callable)
        #_trigger_callback(event: str, *args, **kwargs)
    }
    
    class MockRobotController {
        -success_rate: float
        -base_latency: float
        -_marker_positions: Dict
        -current_position: Dict
        -robot_joints: List[float]
        -ext_axis: List[float]
        -error_scenarios: Dict
        -_agv_controller: MockAGVController
        -_jaka_controller: MockJakaController
        -_ext_controller: MockExtController
        -_monitor_thread: Thread
        -_stop_monitoring: bool
        
        +setup_system() bool
        +shutdown_system()
        +move_to_marker(marker_id: str) bool
        +move_robot_to_position(position: List[float]) bool
        +move_ext_to_position(position: List[float]) bool
        +get_status() Dict
        +emergency_stop() bool
        +start_monitoring()
        +stop_monitoring()
        -_monitoring_loop()
        -_simulate_operation(operation_name: str) bool
    }
    
    class RealRobotController {
        -_agv_controller: AGVController
        -_arm_controller: ArmController
        -_jaka_controller: ArmController
        -_ext_controller: ArmController
        
        +setup_system() bool
        +shutdown_system()
        +move_to_marker(marker_id: str) bool
        +move_robot_to_position(position: List[float]) bool
        +move_ext_to_position(position: List[float]) bool
        +get_status() Dict
        +emergency_stop() bool
    }
    
    RobotControllerBase <|-- MockRobotController
    RobotControllerBase <|-- RealRobotController
    
```

### 2.2 任务管理系统类图

```mermaid
classDiagram
    class TaskStatus {
        <<enumeration>>
        PENDING
        RUNNING
        COMPLETED
        FAILED
        SKIPPED
        RETRYING
    }
    
    class OperationMode {
        <<enumeration>>
        OPEN_DOOR
        CLOSE_DOOR
        NONE
    }
    
    class StationTask {
        +station_id: str
        +name: str
        +agv_marker: str
        +robot_pos: List[float]
        +ext_pos: List[float]
        +operation_mode: OperationMode
        +door_id: Optional[str]
        +to_dict() Dict
    }
    
    class InspectionTask {
        +task_id: str
        +stations: List[StationTask]
        +priority: int
        +status: TaskStatus
        +created_at: datetime
        +started_at: Optional[datetime]
        +completed_at: Optional[datetime]
        +retry_count: int
        +max_retries: int
        +error_message: Optional[str]
        +metadata: Dict
        +to_dict() Dict
    }
    
    class TaskDatabase {
        -db_path: str
        -_init_database()
        +save_task(task: InspectionTask)
        +update_task_status(task_id: str, status: TaskStatus, error_message: str)
        +add_retry_count(task_id: str)
        +log_task_action(task_id: str, station_id: str, action: str, status: str, details: str)
        +get_pending_tasks() List[Dict]
    }
    
    class TaskScheduler {
        -robot_controller: RobotControllerBase
        -database: TaskDatabase
        -task_queue: PriorityQueue
        -current_task: Optional[InspectionTask]
        -is_running: bool
        -scheduler_thread: Thread
        -executor: ThreadPoolExecutor
        -task_callbacks: Dict
        
        +start()
        +stop()
        +add_task(task: InspectionTask)
        -_scheduler_loop()
        -_execute_task(task: InspectionTask)
        -_execute_task_internal(task: InspectionTask) bool
        -_execute_station_task(task: InspectionTask, station_task: StationTask) bool
        -_execute_operation(operation_mode: OperationMode, door_id: str) bool
        +register_callback(event: str, callback: Callable)
        -_trigger_callback(event: str, task: InspectionTask)
    }
    
    class TaskManager {
        -robot_controller: RobotControllerBase
        -database: TaskDatabase
        -scheduler: TaskScheduler
        
        +receive_task_from_json(json_data: Dict) str
        +get_task_status(task_id: str) Dict
        +get_all_tasks(status: str) List[Dict]
        +cancel_task(task_id: str) bool
        +retry_task(task_id: str) bool
        +shutdown()
        -_on_task_start(task: InspectionTask)
        -_on_task_complete(task: InspectionTask)
        -_on_task_failed(task: InspectionTask)
    }
    
    InspectionTask *-- StationTask
    TaskScheduler *-- InspectionTask
    TaskManager *-- TaskScheduler
    TaskManager *-- TaskDatabase
    TaskScheduler *-- TaskDatabase
    TaskManager ..> RobotControllerBase
    TaskScheduler ..> RobotControllerBase
```

### 2.3 真实硬件控制器类图
```mermaid
classDiagram
    class AGVController {
        -agv_ip: str
        -agv_port: int
        -agv_data_thread: Thread
        -agv_data_running: bool
        -agv_data_callback: Callable
        -agv_data_topics: List
        
        +agv_get_status() Dict
        +agv_get_specified_status(query: str) Any
        +agv_moveto(point_name: str) bool
        +agv_set_point_as_marker(point_name: str, type: int, num: int) bool
        +agv_cancel_task() bool
        +agv_estop() bool
        +agv_estop_release() bool
        +agv_position_adjust(current_point_name: str) bool
        +agv_request_data(callback: Callable) bool
        +agv_stop_data() bool
        -_send_command_to_agv(command: str) Dict
        -_receive_agv_data()
    }
    
    class ArmController {
        <<extends JAKA>>
        -system_config: Dict
        -ext_base_url: str
        -ext_axis_limits: Dict
        
        +ext_check_connection() bool
        +ext_reset() bool
        +ext_enable(enable: bool) bool
        +ext_get_state() List[Dict]
        +ext_moveto(point: List[float], vel: int, acc: int) bool
        +setup_system() bool
        +shutdown_system()
        +rob_moveto(jpos: List[float], vel: float) Any
        -_adjust_to_joint_limits(point: List[float]) Tuple
    }
    
    class JAKA {
        <<外部库>>
        +joint_move_origin(joints: List[float], sp: float, move_mode: int) Any
        +jaka_connect() bool
        +robot_disconnect()
    }
    
    ArmController --|> JAKA
    RealRobotController *-- AGVController
    RealRobotController *-- ArmController
```
## 三、系统架构说明
### 3.1 核心组件职责
| 组件                  | 职责              | 关键技术点                 |   
|---------------------|-----------------|-----------------------|
| TaskManager         | 任务接收、状态管理、历史查询  | JSON解析、任务ID生成、优先级计算   |   
| TaskScheduler       | 任务调度、执行监控、失败重试  | 优先级队列、线程池、状态机         |   
| TaskDatabase        | 任务持久化、执行历史记录    | SQLite数据库、JSON序列化     |   
| RobotControllerBase | 定义机器人控制标准接口     | 抽象基类、多态、回调机制          |   
| MockRobotController | 模拟机器人行为，用于开发和测试 | 随机成功率、延迟模拟、状态监控       |   
| RealRobotController | 控制真实机器人硬件       | TCP/IP通信、HTTP请求、错误处理  |   

### 3.2 数据流图
```mermaid
sequenceDiagram
    participant Server as 上级服务器
    participant TM as TaskManager
    participant TS as TaskScheduler
    participant RC as RobotController
    participant DB as 数据库
    participant HW as 硬件设备
    
    Note over Server, HW: 1. 任务接收阶段
    Server->>TM: JSON任务指令
    TM->>TM: 解析JSON，生成任务ID
    TM->>DB: 保存任务到数据库
    TM->>TS: 添加任务到调度队列
    
    Note over Server, HW: 2. 任务调度阶段
    TS->>TS: 从队列获取任务（按优先级）
    TS->>DB: 更新任务状态为RUNNING
    TS->>TM: 触发on_task_start回调
    
    Note over Server, HW: 3. 任务执行阶段
    loop 每个站点任务
        TS->>RC: move_to_marker(agv_marker)
        RC->>HW: 控制AGV移动
        HW-->>RC: 移动完成
        RC-->>TS: 返回成功
        
        TS->>RC: move_robot_to_position(robot_pos)
        RC->>HW: 控制机械臂移动
        HW-->>RC: 移动完成
        RC-->>TS: 返回成功
        
        TS->>RC: move_ext_to_position(ext_pos)
        RC->>HW: 控制外部轴移动
        HW-->>RC: 移动完成
        RC-->>TS: 返回成功
        
        alt operation_mode != NONE
            TS->>RC: 执行对应操作（开门/关门）
            RC->>HW: 执行操作
            HW-->>RC: 操作完成
            RC-->>TS: 返回成功
        end
        
        TS->>DB: 记录站点执行日志
    end
    
    Note over Server, HW: 4. 任务完成阶段
    TS->>DB: 更新任务状态为COMPLETED
    TS->>TM: 触发on_task_complete回调
    TM-->>Server: 任务完成通知（可选）
```

## 四、数据库设计
### 4.1 数据库表结构

```sql
-- 任务主表
CREATE TABLE tasks (
    task_id TEXT PRIMARY KEY,
    stations_json TEXT NOT NULL,           -- 任务站点信息（JSON格式）
    priority INTEGER DEFAULT 1,
    status TEXT DEFAULT 'pending',         -- pending/running/completed/failed/skipped/retrying
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    retry_count INTEGER DEFAULT 0,
    max_retries INTEGER DEFAULT 3,
    error_message TEXT,
    metadata_json TEXT DEFAULT '{}'
);

-- 任务执行历史表
CREATE TABLE task_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT,
    station_id TEXT,                       -- 站点ID
    action TEXT,                           -- start/complete/error
    status TEXT,                           -- 执行状态
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    details TEXT,                          -- 详细执行信息
    FOREIGN KEY (task_id) REFERENCES tasks (task_id)
);

-- 系统配置表
CREATE TABLE system_config (
    config_key TEXT PRIMARY KEY,
    config_value TEXT,
    description TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 机器人状态历史表
CREATE TABLE robot_status_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    status TEXT,                           -- idle/moving/operating/error/charging
    battery_level REAL,
    current_marker TEXT,
    agv_status_json TEXT,                  -- AGV详细状态（JSON）
    external_axis_status_json TEXT         -- 外部轴状态（JSON）
);

-- 创建索引
CREATE INDEX idx_tasks_status ON tasks(status);
CREATE INDEX idx_tasks_created ON tasks(created_at);
CREATE INDEX idx_task_history_task_id ON task_history(task_id);
CREATE INDEX idx_task_history_timestamp ON task_history(timestamp);
CREATE INDEX idx_robot_status_timestamp ON robot_status_history(timestamp);

```


### 4.2 数据模型关系
```text
tasks (1) ──────── (∞) task_history
    │
    ├── task_id (PK)
    ├── stations_json (包含多个StationTask)
    ├── status
    └── ...
    
task_history
    ├── task_id (FK → tasks.task_id)
    ├── station_id (对应stations_json中的某个站点)
    ├── action
    └── ...

```