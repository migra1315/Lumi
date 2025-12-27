from dataclasses import dataclass, asdict
from enum import Enum
from typing import Dict, List, Optional, Any
from datetime import datetime
import json

class StationTaskStatus(Enum):
    PENDING = "pending"      # 待执行
    RUNNING = "running"      # 执行中
    COMPLETED = "completed"  # 已完成
    FAILED = "failed"        # 执行失败
    SKIPPED = "skipped"      # 已跳过
    RETRYING = "retrying"    # 重试中
    TO_RETRY = "to_retry"    # 重试中


class TaskStatus(Enum):
    PENDING = "pending"      # 待执行
    RUNNING = "running"      # 执行中
    COMPLETED = "completed"  # 已完成
    FAILED = "failed"        # 执行失败
    SKIPPED = "skipped"      # 已跳过
    RETRYING = "retrying"    # 重试中


class OperationMode(Enum):
    OPEN_DOOR = "open_door" # 打开门
    CLOSE_DOOR = "close_door" # 关闭门
    NONE = "None" # 无操作
    CAPTURE = "capture" # 拍照
    SERVE = "serve" # 服务
    # 可以扩展其他操作模式


class RobotMode(Enum):
    """机器人模式枚举"""
    INSPECTION = "inspection"  # 正常模式
    SERVICE = "service"  # 服务模式
    JOY_CONTROL="joy_control"  # 摇杆控制模式
    ESTOP = "estop"  # 紧急停止模式
    CHARGE = "charge"  # 充电模式
    STAND_BY = "stand_by"  # 待命模式


@dataclass
class OperationConfig:
    """操作任务"""
    operation_mode: OperationMode  # 操作模式
    door_ip: Optional[str]        # 门ID
    device_id: Optional[str]      # 设备ID
    
    def to_dict(self):
        return {
            "operation_mode": self.operation_mode.value,
            "door_ip": self.door_ip,
            "device_id": self.device_id
        }
    

@dataclass
class StationConfig:
    """单个站点任务"""
    station_id: str  # 如 "station1"
    sort: int        # 站点排序
    name: str        # 站点名称
    agv_marker: str  # AGV导航点
    robot_pos: List[float]  # 机械臂归位位置
    ext_pos: List[float]    # 外部轴归位位置
    operation_config: OperationConfig  # 操作任务
    def to_dict(self):
        return {
            "station_id": self.station_id,
            "sort": self.sort,
            "name": self.name,
            "agv_marker": self.agv_marker,
            "robot_pos": self.robot_pos,
            "ext_pos": self.ext_pos,
            "operation_config": self.operation_config.to_dict(),
        }

@dataclass
class Station:
    """站点任务(对站点配置参数的封装。增加相关任务信息)"""
    station_config: StationConfig           # 单个站点
    status: StationTaskStatus = StationTaskStatus.PENDING # 任务状态
    created_at: datetime = None # 创建时间
    started_at: Optional[datetime] = None # 开始时间
    completed_at: Optional[datetime] = None # 完成时间
    retry_count: int = 0 # 重试次数
    max_retries: int = 3  # 最大重试次数
    error_message: Optional[str] = None
    metadata: Dict[str, Any] = None  # 元数据，用于存储额外信息
    
    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.now()
        if self.metadata is None:
            self.metadata = {}
    
    def to_dict(self):
        return {
            "station_config": self.station_config.to_dict(),
            "status": self.status.value,
            "generate_time": self.generate_time.isoformat() if self.generate_time else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "retry_count": self.retry_count,
            "max_retries": self.max_retries,
            "error_message": self.error_message,
            "metadata": self.metadata
        }

@dataclass
class Task:
    """任务信息"""
    task_id: str    # 任务ID
    task_name: str  # 任务名称
    station_list: List[Station]
    status: TaskStatus = TaskStatus.PENDING # 任务状态
    robot_mode:RobotMode = RobotMode.STAND_BY
    generate_time: datetime = None  # 任务生成时间
    created_at: datetime = None     # 创建时间
    started_at: Optional[datetime] = None # 开始时间
    completed_at: Optional[datetime] = None # 完成时间
    error_message: Optional[str] = None
    metadata: Dict[str, Any] = None  # 元数据，用于存储额外信息
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "task_name": self.task_name,
            "station_list": [station.to_dict() for station in self.station_list],
            "status": self.status.value,
            "robot_mode": self.robot_mode.value,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "error_message": self.error_message,
            "metadata": self.metadata
        }
