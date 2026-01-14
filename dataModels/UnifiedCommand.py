"""
统一命令模型 - 用于所有类型命令的统一调度和管理
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Any, Optional
from datetime import datetime
from dataModels.CommandModels import CmdType
import json


class CommandCategory(Enum):
    """命令分类"""
    TASK = "task"              # 巡检任务（多站点）
    CONTROL = "control"        # 控制命令（mode, joy, charge等）
    CONFIGURATION = "config"   # 配置命令（set_marker, position_adjust等）


class CommandStatus(Enum):
    """统一命令状态"""
    PENDING = "pending"        # 待处理
    QUEUED = "queued"          # 已加入队列
    RUNNING = "running"        # 执行中
    COMPLETED = "completed"    # 已完成
    FAILED = "failed"          # 执行失败
    CANCELLED = "cancelled"    # 已取消
    RETRYING = "retrying"      # 重试中


@dataclass
class UnifiedCommand:
    """统一命令模型 - 包装所有类型的命令"""
    command_id: str                              # 命令ID（唯一标识）
    cmd_type: CmdType                            # 原始命令类型
    category: CommandCategory                    # 命令分类
    priority: int = 5                            # 优先级 (1-10, 1最高)
    data: Any = None                             # 命令数据
    status: CommandStatus = CommandStatus.PENDING
    created_at: datetime = field(default_factory=datetime.now)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    retry_count: int = 0
    max_retries: int = 3
    error_message: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式"""
        return {
            "command_id": self.command_id,
            "cmd_type": self.cmd_type.value,
            "category": self.category.value,
            "priority": self.priority,
            "data": self._serialize_data(),
            "status": self.status.value,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "retry_count": self.retry_count,
            "max_retries": self.max_retries,
            "error_message": self.error_message,
            "metadata": self.metadata
        }

    def _serialize_data(self) -> Any:
        """序列化命令数据"""
        if hasattr(self.data, 'to_dict'):
            return self.data.to_dict()
        return self.data

    def to_json(self) -> str:
        """转换为JSON字符串"""
        return json.dumps(self.to_dict(), ensure_ascii=False)

    def __lt__(self, other):
        """用于优先级队列比较（优先级数字越小越优先）"""
        if not isinstance(other, UnifiedCommand):
            return NotImplemented
        return self.priority < other.priority


# 命令类型到分类的映射
CMD_TYPE_TO_CATEGORY = {
    CmdType.TASK_CMD: CommandCategory.TASK,
    CmdType.ROBOT_MODE_CMD: CommandCategory.CONTROL,
    CmdType.JOY_CONTROL_CMD: CommandCategory.CONTROL,
    CmdType.CHARGE_CMD: CommandCategory.CONTROL,
    CmdType.SET_MARKER_CMD: CommandCategory.CONFIGURATION,
    CmdType.POSITION_ADJUST_CMD: CommandCategory.CONFIGURATION,
}


# 命令类型默认优先级映射
CMD_TYPE_DEFAULT_PRIORITY = {
    CmdType.TASK_CMD: 5,                    # 巡检任务 - 普通优先级
    CmdType.ROBOT_MODE_CMD: 2,              # 模式切换 - 高优先级
    CmdType.JOY_CONTROL_CMD: 1,             # 摇杆控制 - 最高优先级（实时控制）
    CmdType.CHARGE_CMD: 4,                  # 充电 - 较高优先级
    CmdType.SET_MARKER_CMD: 3,              # 设置标记 - 高优先级
    CmdType.POSITION_ADJUST_CMD: 6,         # 位置调整 - 普通优先级
}


def create_unified_command(
    command_id: str,
    cmd_type: CmdType,
    data: Any,
    priority: Optional[int] = None,
    metadata: Optional[Dict[str, Any]] = None
) -> UnifiedCommand:
    """创建统一命令对象（工厂方法）

    Args:
        command_id: 命令ID
        cmd_type: 命令类型
        data: 命令数据
        priority: 优先级（可选，默认根据命令类型自动设置）
        metadata: 元数据（可选）

    Returns:
        UnifiedCommand: 统一命令对象
    """
    # 自动确定命令分类
    category = CMD_TYPE_TO_CATEGORY.get(cmd_type, CommandCategory.CONTROL)

    # 自动确定优先级
    if priority is None:
        priority = CMD_TYPE_DEFAULT_PRIORITY.get(cmd_type, 5)

    return UnifiedCommand(
        command_id=command_id,
        cmd_type=cmd_type,
        category=category,
        priority=priority,
        data=data,
        metadata=metadata or {}
    )
