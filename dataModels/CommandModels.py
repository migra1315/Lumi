import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Dict, List, Any

from dataModels.TaskModels import StationConfig, RobotMode


class CmdType(Enum):
    """命令类型枚举"""
    RESPONSE_CMD = "response_cmd"          # 响应命令
    ROBOT_MODE_CMD = "robot_mode_cmd"     # 机器人模式命令
    TASK_CMD = "task_cmd"  # 任务下发
    JOY_CONTROL_CMD = "joy_control_cmd"  # 摇杆控制命令
    SET_MARKER_CMD = "set_marker_cmd"  # 设置标记命令
    CHARGE_CMD = "charge_cmd"  # 充电命令
    POSITION_ADJUST_CMD = "position_adjust_cmd"  # 位置调整命令
    HARDWARE_START_CMD = "hardware_start_cmd"  # 硬件启动命令
    HARDWARE_SHUTDOWN_CMD = "hardware_shutdown_cmd"  # 硬件关闭命令
    CANCEL_TASK_CMD = "cancel_task_cmd"  # 任务取消命令（即时分流，不进入普通队列）


@dataclass
class CommandResponse:
    """响应信息"""
    code: str              # 命令ID，用于匹配请求
    info: str             # 响应状态，例如 "success" 或 "error"
    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "info": self.info
        }


@dataclass
class RobotModeCmd:
    """机器人模式命令"""
    robot_mode: RobotMode  # 机器人模式
    def to_dict(self) -> Dict[str, Any]:
        return {
            "robot_mode": self.robot_mode.value
        }


@dataclass
class TaskCmd:
    """任务下发"""
    task_id: int  # 任务ID
    task_name: str  # 任务名称
    robot_mode: RobotMode  # 机器人模式
    generate_time: datetime  # 任务生成时间
    station_config_list: List[StationConfig]  # 站点配置任务列表

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "task_name": self.task_name,
            "robot_mode": self.robot_mode.value,
            "generate_time": self.generate_time.isoformat(),
            "station_config_tasks": [station.to_dict() for station in self.station_config_list]
        }


@dataclass(frozen=True)
class CancelTaskCmd:
    """任务取消命令，只描述被取消的业务任务。"""
    task_id: int

    def to_dict(self) -> Dict[str, Any]:
        return {"task_id": self.task_id}


@dataclass
class SetMarkerCmd:
    """设置标记命令,客户端点击设置机器人当前位置为该标记"""
    marker_id: str  # 标记ID
    def to_dict(self) -> Dict[str, Any]:
        return {
            "marker_id": self.marker_id,
        }


@dataclass
class ChargeCmd:
    """充电命令"""
    charge: bool  # 是否充电
    def to_dict(self) -> Dict[str, Any]:
        return {
            "charge": self.charge,
        }

@dataclass
class PositionAdjustCmd:
    """位置调整命令"""
    adjust: bool  # 是否调整位置
    def to_dict(self) -> Dict[str, Any]:
        return {
            "adjust": self.adjust,
        }


@dataclass
class HardwareControlCmd:
    """硬件控制命令"""
    robot: bool = False       # 机器人（AGV+机械臂）
    camera: bool = False      # 相机
    env_sensor: bool = False  # 环境传感器

    def to_dict(self) -> Dict[str, Any]:
        return {
            "robot": self.robot,
            "camera": self.camera,
            "env_sensor": self.env_sensor,
        }


@dataclass
class JoyControlCmd:
    """摇杆控制命令"""
    angular_velocity: float  # 角速度 (-1.0 到 1.0)
    linear_velocity: float  # 线速度 (-0.5 到 0.5)
    def to_dict(self) -> Dict[str, Any]:
        return {
            "angular_velocity": self.angular_velocity,
            "linear_velocity": self.linear_velocity,
        }


@dataclass
class CommandEnvelope:
    """消息信封 - 所有数据传输的包装器"""
    cmd_id: str              # 唯一消息ID，用于请求响应匹配和消息去重
    cmd_time: int            # 消息产生的时间戳（毫秒）
    cmd_type: CmdType        # 核心路由字段，标识功能类型
    robot_id: str            # 机器人标识，用于会话管理和消息路由
    data_json: Dict[str, Any]  # 实际的功能数据Json
    data: Any = field(default=None)  # 预解析对象（如 Task），避免重复解析 data_json

    def to_dict(self) -> Dict[str, Any]:
        return {
            "cmd_id": self.cmd_id,
            "cmd_time": self.cmd_time,
            "cmd_type": self.cmd_type.value,
            "robot_id": self.robot_id,
            "data_json": self.data_json
        }

    def to_json(self) -> str:
        """转换为JSON字符串"""
        return json.dumps(self.to_dict(), ensure_ascii=False)


def create_task_cmd_envelope(
    cmd_id: str,
    robot_id: str,
    task_cmd: TaskCmd
) -> CommandEnvelope:
    """创建任务下发消息信封"""
    return CommandEnvelope(
        cmd_id=cmd_id,
        cmd_time=int(datetime.now().timestamp() * 1000),
        cmd_type=CmdType.TASK_CMD,
        robot_id=robot_id,
        data_json={"task_cmd": task_cmd.to_dict()}
    )

def create_response_cmd_envelope(
    cmd_id: str,
    robot_id: str,
    response_cmd: CommandResponse
) -> CommandEnvelope:
    """创建响应消息信封"""
    return CommandEnvelope(
        cmd_id=cmd_id,
        cmd_time=int(datetime.now().timestamp() * 1000),
        cmd_type=CmdType.RESPONSE_CMD,
        robot_id=robot_id,
        data_json={"response_cmd": response_cmd.to_dict()}
    )

def create_robot_mode_cmd_envelope(
    cmd_id: str,
    robot_id: str,
    robot_mode_cmd: RobotModeCmd
) -> CommandEnvelope:
    """创建机器人模式命令消息信封"""
    return CommandEnvelope(
        cmd_id=cmd_id,
        cmd_time=int(datetime.now().timestamp() * 1000),
        cmd_type=CmdType.ROBOT_MODE_CMD,
        robot_id=robot_id,
        data_json={"robot_mode_cmd": robot_mode_cmd.to_dict()}
    )

def create_joy_control_cmd_envelope(
    cmd_id: str,
    robot_id: str,
    joy_control_cmd: JoyControlCmd
) -> CommandEnvelope:
    """创建摇杆控制命令消息信封"""
    return CommandEnvelope(
        cmd_id=cmd_id,
        cmd_time=int(datetime.now().timestamp() * 1000),
        cmd_type=CmdType.JOY_CONTROL_CMD,
        robot_id=robot_id,
        data_json={"joy_control_cmd": joy_control_cmd.to_dict()}
    )
