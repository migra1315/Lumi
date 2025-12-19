from dataclasses import dataclass, asdict
from enum import Enum
from typing import Dict, List, Optional, Any
from datetime import datetime
import json

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
    name: str        # 站点名称
    agv_marker: str  # AGV导航点
    robot_pos: List[float]  # 机械臂归位位置
    ext_pos: List[float]    # 外部轴归位位置
    operation_config: OperationConfig  # 操作任务
    
    def to_dict(self):
        return {
            "station_id": self.station_id,
            "name": self.name,
            "agv_marker": self.agv_marker,
            "robot_pos": self.robot_pos,
            "ext_pos": self.ext_pos,
            "operation_config": self.operation_config.to_dict(),
        }

@dataclass
class InspectionTask:
    """巡检任务(对站点任务参数的封装。增加相关任务信息)"""
    task_id: str                     # 任务ID
    station: StationConfig           # 单个站点
    priority: int = 1               # 优先级 (1-10, 10最高)
    status: TaskStatus = TaskStatus.PENDING
    created_at: datetime = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    retry_count: int = 0
    max_retries: int = 3
    error_message: Optional[str] = None
    metadata: Dict[str, Any] = None
    
    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.now()
        if self.metadata is None:
            self.metadata = {}
    
    def to_dict(self):
        return {
            "task_id": self.task_id,
            "station": self.station.to_dict(),
            "priority": self.priority,
            "status": self.status.value,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "retry_count": self.retry_count,
            "max_retries": self.max_retries,
            "error_message": self.error_message,
            "metadata": self.metadata
        }

        