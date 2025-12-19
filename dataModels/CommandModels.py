from dataModels.TaskModels import StationConfig
from dataclasses import dataclass, asdict
from enum import Enum
from typing import Dict, List, Optional, Any
from datetime import datetime
import json
from dataModels.TaskCommandModels import taskCommand

class CmdType(Enum):
    """命令类型枚举"""
    RESPONSE_CMD = "response_cmd"          # 响应命令
    ROBOT_MODE_CMD = "robot_mode_cmd"     # 机器人模式命令
    TASK_CMD = "task_cmd"  # 任务下发


class RobotMode(Enum):
    """机器人模式枚举"""
    INSPECTION = "normal"  # 正常模式
    SERVICE = "service"  # 服务模式
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
    

def create_task_cmd_envelope(
    cmd_id: str,
    robot_id: str,
    task_cmd: TaskCmd
) -> CommandEnvelope:
    """创建任务下发消息信封"""
    return CommandEnvelope(
        cmdId=cmd_id,
        cmdTime=int(datetime.now().timestamp() * 1000),  # 毫秒时间戳
        cmdType=CmdType.TASK_CMD,
        robotId=robot_id,
        dataJson=TaskDataJson(taskCmd=task_cmd).to_dict()
    )

def create_response_cmd_envelope(
    cmd_id: str,
    robot_id: str,
    response_cmd: ResponseCmd
) -> CommandEnvelope:
    """创建响应消息信封"""
    return CommandEnvelope(
        cmdId=cmd_id,
        cmdTime=int(datetime.now().timestamp() * 1000),  # 毫秒时间戳
        cmdType=CmdType.RESPONSE_CMD,
        robotId=robot_id,
        dataJson=responseDataJson(responseCmd=response_cmd).to_dict()
    )

def create_robot_mode_cmd_envelope(
    cmd_id: str,
    robot_id: str,
    robot_mode_cmd: RobotModeCmd
) -> CommandEnvelope:
    """创建机器人模式命令消息信封"""
    return CommandEnvelope(
        cmdId=cmd_id,
        cmdTime=int(datetime.now().timestamp() * 1000),  # 毫秒时间戳
        cmdType=CmdType.ROBOT_MODE_CMD,
        robotId=robot_id,
        dataJson=RobotModeDataJson(robotModeCmd=robot_mode_cmd).to_dict()
    )

