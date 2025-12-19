from dataModels.TaskModels import StationConfig
from dataclasses import dataclass, asdict
from enum import Enum
from typing import Dict, List, Optional, Any
from datetime import datetime
import json


class CmdType(Enum):
    """命令类型枚举"""
    RESPONSE_CMD = "response_cmd"          # 响应命令
    ROBOT_MODE_CMD = "robot_mode_cmd"     # 机器人模式命令
    TASK_CMD = "task_cmd"  # 任务下发
    JOY_CONTROL_CMD = "joy_control_cmd"  # 摇杆控制命令
    SET_MARKER_CMD = "set_marker_cmd"  # 设置标记命令
    CHARGE_CMD = "charge_cmd"  # 充电命令


class RobotMode(Enum):
    """机器人模式枚举"""
    INSPECTION = "normal"  # 正常模式
    SERVICE = "service"  # 服务模式
    JOY_CONTROL="joy_control"  # 摇杆控制模式
    ESTOP = "estop"  # 紧急停止模式


@dataclass
class ResponseCmd:
    """响应信息"""
    code: str              # 命令ID，用于匹配请求
    info: str             # 响应状态，例如 "success" 或 "error"
    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "info": self.info
        }
    
@dataclass   
class responseDataJson:
    responseCmd: ResponseCmd  # 响应信息
    def to_dict(self) -> Dict[str, Any]:
        return {
            "responseCmd": self.responseCmd.to_dict()
        }


@dataclass
class RobotModeCmd:
    """机器人模式命令"""
    robotMode: RobotMode  # 机器人模式
    def to_dict(self) -> Dict[str, Any]:
        return {
            "robotMode": self.robotMode.value
        }


@dataclass
class RobotModeDataJson:
    robotModeCmd: RobotModeCmd  # 机器人模式命令
    def to_dict(self) -> Dict[str, Any]:
        return {
            "robotModeCmd": self.robotModeCmd.to_dict()
        }
    
@dataclass
class TaskCmd:
    """任务下发"""
    taskId: str  # 任务ID
    taskName: str  # 任务名称
    robotMode: RobotMode  # 机器人模式
    generateTime: datetime = None  # 任务生成时间
    stationTasks: List[StationConfig]  # 站点任务列表
    def to_dict(self) -> Dict[str, Any]:
        return {
            "taskId": self.taskId,
            "taskName": self.taskName,
            "robotMode": self.robotMode.value,
            "generateTime": self.generateTime.isoformat() if self.generateTime else None,
            "stationTasks": [task.to_dict() for task in self.stationTasks]
        }
    
@dataclass
class TaskDataJson:
    taskCmd: TaskCmd  # 任务下发
    def to_dict(self) -> Dict[str, Any]:
        return {
            "taskCmd": self.taskCmd.to_dict()
        }

@dataclass
class SetMarkerCmd:
    """设置标记命令,客户端点击设置机器人当前位置为该标记"""
    markerId: str  # 标记ID
    def to_dict(self) -> Dict[str, Any]:
        return {
            "markerId": self.markerId,
        }
    
@dataclass   
class SetMarkerDataJson:
    setMarkerCmd: SetMarkerCmd  # 设置标记命令
    def to_dict(self) -> Dict[str, Any]:
        return {
            "setMarkerCmd": self.setMarkerCmd.to_dict()
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
class ChargeDataJson:
    chargeCmd: ChargeCmd  # 充电命令
    def to_dict(self) -> Dict[str, Any]:
        return {
            "chargeCmd": self.chargeCmd.to_dict()
        }
    

@dataclass
class joyControlCmd:
    """摇杆控制命令"""
    angular_velocity: float  # 角速度 (-1.0 到 1.0)
    linear_velocity: float  # 线速度 (-0.5 到 0.5)
    def to_dict(self) -> Dict[str, Any]:
        return {
            "angular_velocity": self.angular_velocity,
            "linear_velocity": self.linear_velocity,
        }
    

@dataclass
class joyControlDataJson:
    joyControlCmd: joyControlCmd  # 摇杆控制命令
    def to_dict(self) -> Dict[str, Any]:
        return {
            "joyControlCmd": self.joyControlCmd.to_dict()
        }

@dataclass
class CommandEnvelope:
    """消息信封 - 所有数据传输的包装器"""
    cmdId: str              # 唯一消息ID，用于请求响应匹配和消息去重
    cmdTime: int            # 消息产生的时间戳（毫秒）
    cmdType: CmdType        # 核心路由字段，标识功能类型
    robotId: str            # 机器人标识，用于会话管理和消息路由
    dataJson: Dict[str, Any]  # 实际的功能数据Json
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "cmdId": self.cmdId,
            "cmdTime": self.cmdTime,
            "cmdType": self.cmdType.value,
            "robotId": self.robotId,
            "dataJson": self.dataJson
        }
    
    def to_json(self) -> str:
        """转换为JSON字符串"""
        return json.dumps(self.to_dict(), ensure_ascii=False)
    

# 命令类型与对应数据类的映射
_CMD_TYPE_TO_DATA_CLASS = {
    CmdType.TASK_CMD: TaskDataJson,
    CmdType.RESPONSE_CMD: responseDataJson,
    CmdType.ROBOT_MODE_CMD: RobotModeDataJson,
    CmdType.JOY_CONTROL_CMD: joyControlDataJson,
    CmdType.SET_MARKER_CMD: SetMarkerDataJson,
    CmdType.CHARGE_CMD: ChargeDataJson,
}

# 命令类型与数据类参数名的映射
_CMD_TYPE_TO_PARAM_NAME = {
    CmdType.TASK_CMD: "taskCmd",
    CmdType.RESPONSE_CMD: "responseCmd",
    CmdType.ROBOT_MODE_CMD: "robotModeCmd",
    CmdType.JOY_CONTROL_CMD: "joyControlCmd",
    CmdType.SET_MARKER_CMD: "setMarkerCmd",
    CmdType.CHARGE_CMD: "chargeCmd",
}

def create_cmd_envelope(
    cmd_id: str,
    robot_id: str,
    cmd_type: CmdType,
    cmd_data: Any
) -> CommandEnvelope:
    """创建命令消息信封（通用工厂方法）
    
    Args:
        cmd_id: 命令ID
        robot_id: 机器人ID
        cmd_type: 命令类型
        cmd_data: 命令数据对象
        
    Returns:
        CommandEnvelope: 命令消息信封
    """
    if cmd_type not in _CMD_TYPE_TO_DATA_CLASS:
        raise ValueError(f"不支持的命令类型: {cmd_type}")
    
    # 获取对应的数据类和参数名
    data_class = _CMD_TYPE_TO_DATA_CLASS[cmd_type]
    param_name = _CMD_TYPE_TO_PARAM_NAME[cmd_type]
    
    # 创建数据对象
    data_obj = data_class(**{param_name: cmd_data})
    
    # 创建命令信封
    return CommandEnvelope(
        cmdId=cmd_id,
        cmdTime=int(datetime.now().timestamp() * 1000),  # 毫秒时间戳
        cmdType=cmd_type,
        robotId=robot_id,
        dataJson=data_obj.to_dict()
    )

# 保留原有的便捷方法，内部调用通用工厂方法
def create_task_cmd_envelope(
    cmd_id: str,
    robot_id: str,
    task_cmd: TaskCmd
) -> CommandEnvelope:
    """创建任务下发消息信封"""
    return create_cmd_envelope(cmd_id, robot_id, CmdType.TASK_CMD, task_cmd)

def create_response_cmd_envelope(
    cmd_id: str,
    robot_id: str,
    response_cmd: ResponseCmd
) -> CommandEnvelope:
    """创建响应消息信封"""
    return create_cmd_envelope(cmd_id, robot_id, CmdType.RESPONSE_CMD, response_cmd)

def create_robot_mode_cmd_envelope(
    cmd_id: str,
    robot_id: str,
    robot_mode_cmd: RobotModeCmd
) -> CommandEnvelope:
    """创建机器人模式命令消息信封"""
    return create_cmd_envelope(cmd_id, robot_id, CmdType.ROBOT_MODE_CMD, robot_mode_cmd)

def create_joy_control_cmd_envelope(
    cmd_id: str,
    robot_id: str,
    joy_control_cmd: joyControlCmd
) -> CommandEnvelope:
    """创建摇杆控制命令消息信封"""
    return create_cmd_envelope(cmd_id, robot_id, CmdType.JOY_CONTROL_CMD, joy_control_cmd)