from dataclasses import dataclass, asdict
from enum import Enum
from typing import Dict, List, Optional, Any
from datetime import datetime
import json
from task.TaskModels import InspectionTask


class MsgType(Enum):
    """消息类型枚举"""
    ROBOT_STATUS = "robot_status"          # 机器人状态
    DEVICE_DATA = "device_data"            # 巡检设备状态
    ENVIRONMENT_DATA = "environment_data"  # 巡检环境信息
    ARRIVE_SERVER_POINT = "arrive_server_point"  # 是否到达服务器点


class MoveStatus(Enum):
    """机器人移动状态枚举"""
    IDLE = "idle"          # 空闲,机器人服务启动后尚未收到任何移动指令
    RUNNING = "running"    # 表示机器人正在去往move_target,此时会拒绝接受新的移动指令
    SUCCEEDED = "succeeded"# 移动任务成功完成
    FAILED = "failed"      # 移动任务失败
    CANCELED = "canceled"  # 移动任务被取消


@dataclass
class BatteryInfo:
    """电池信息"""
    power_percent: float      # 剩余电量百分比
    charge_status: str        # 电池状态（充电中/放电中）
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class PositionInfo:
    """位置信息"""
    AGVPositionInfo: List[float]   # AGV位置信息 [x,y,theta]
    ARMPositionInfo: List[float]   # 机械臂位置信息 [J1,J2,J3,J4,J5,J6]
    EXTPositionInfo: List[float]   # 外部轴位置信息 [J1,J2,J3,J4]
    targetPoint: str               # 目标点位
    pointId: Optional[str] = None  # 点位ID（可选，文档中建议删除）
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class TaskInfo:
    """任务信息"""
    # 直接封装InspectionTask类
    inspection_task: InspectionTask
    
    def to_dict(self) -> Dict[str, Any]:
        # 使用InspectionTask的to_dict方法直接转换
        return self.inspection_task.to_dict()


@dataclass
class SystemStatus:
    """系统状态"""
    move_status: MoveStatus       # 机器人状态（移动、充电、故障）
    is_connected: bool            # 通信连接状态（连接中、断开）
    soft_estop_status: bool       # 软急停状态（激活/未激活）
    hard_estop_status: bool       # 硬急停状态（激活/未激活）
    estop_status: bool            # 急停状态（激活/未激活）
    
    def to_dict(self) -> Dict[str, Any]:
        result = asdict(self)
        result["move_status"] = self.move_status.value
        return result


@dataclass
class ErrorInfo:
    """错误信息"""
    code: int                # 错误编码
    level: str               # 错误级别
    message: str             # 错误信息
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class DeviceInfo:
    """设备信息"""
    deviceId: str           # 设备ID
    dataType: str           # 数据类型
    imageBase64: str        # 图片数据
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class EnvironmentInfo:
    """环境信息"""
    temperature: float       # 温度
    humidity: float          # 湿度
    oxygen: float            # O2浓度
    carbonDioxide: float     # CO2浓度
    pm25: float              # PM2.5浓度
    pm10: float              # PM10浓度
    etvoc: float             # eTVOC浓度
    noise: float             # 噪音传感器值
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class DeviceDataJson:
    """设备巡检数据"""
    positionInfo: PositionInfo
    taskInfo: List[TaskInfo]
    deviceInfo: DeviceInfo
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "positionInfo": self.positionInfo.to_dict(),
            "taskInfo": [task.to_dict() for task in self.taskInfo],
            "deviceInfo": self.deviceInfo.to_dict()
        }


@dataclass
class EnvironmentDataJson:
    """环境巡检数据"""
    positionInfo: PositionInfo
    taskInfo: List[TaskInfo]
    environmentInfo: EnvironmentInfo
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "positionInfo": self.positionInfo.to_dict(),
            "taskInfo": [task.to_dict() for task in self.taskInfo],
            "environmentInfo": self.environmentInfo.to_dict()
        }


@dataclass
class RobotStatusDataJson:
    """机器人状态数据"""
    batteryInfo: BatteryInfo
    positionInfo: PositionInfo
    taskInfo: List[TaskInfo]
    systemStatus: SystemStatus
    errorInfo: Optional[ErrorInfo] = None
    
    def to_dict(self) -> Dict[str, Any]:
        result = {
            "batteryInfo": self.batteryInfo.to_dict(),
            "positionInfo": self.positionInfo.to_dict(),
            "taskInfo": [task.to_dict() for task in self.taskInfo],
            "systemStatus": self.systemStatus.to_dict()
        }
        if self.errorInfo:
            result["errorInfo"] = self.errorInfo.to_dict()
        return result


@dataclass
class MessageEnvelope:
    """消息信封 - 所有数据传输的包装器"""
    msgId: str              # 唯一消息ID，用于请求响应匹配和消息去重
    msgTime: int            # 消息产生的时间戳（毫秒）
    msgType: MsgType        # 核心路由字段，标识功能类型
    robotId: str            # 机器人标识，用于会话管理和消息路由
    dataJson: Dict[str, Any]  # 实际的功能数据Json
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "msgId": self.msgId,
            "msgTime": self.msgTime,
            "msgType": self.msgType.value,
            "robotId": self.robotId,
            "dataJson": self.dataJson
        }
    
    def to_json(self) -> str:
        """转换为JSON字符串"""
        return json.dumps(self.to_dict(), ensure_ascii=False)


# 辅助函数

def create_robot_status_message(
    msg_id: str,
    robot_id: str,
    battery_info: BatteryInfo,
    position_info: PositionInfo,
    task_info: List[TaskInfo],
    system_status: SystemStatus,
    error_info: Optional[ErrorInfo] = None
) -> MessageEnvelope:
    """创建机器人状态消息"""
    data_json = RobotStatusDataJson(
        batteryInfo=battery_info,
        positionInfo=position_info,
        taskInfo=task_info,
        systemStatus=system_status,
        errorInfo=error_info
    )
    
    return MessageEnvelope(
        msgId=msg_id,
        msgTime=int(datetime.now().timestamp() * 1000),  # 毫秒时间戳
        msgType=MsgType.ROBOT_STATUS,
        robotId=robot_id,
        dataJson=data_json.to_dict()
    )


def create_device_data_message(
    msg_id: str,
    robot_id: str,
    position_info: PositionInfo,
    task_info: List[TaskInfo],
    device_info: DeviceInfo
) -> MessageEnvelope:
    """创建设备巡检数据消息"""
    data_json = DeviceDataJson(
        positionInfo=position_info,
        taskInfo=task_info,
        deviceInfo=device_info
    )
    
    return MessageEnvelope(
        msgId=msg_id,
        msgTime=int(datetime.now().timestamp() * 1000),  # 毫秒时间戳
        msgType=MsgType.DEVICE_DATA,
        robotId=robot_id,
        dataJson=data_json.to_dict()
    )


def create_environment_data_message(
    msg_id: str,
    robot_id: str,
    position_info: PositionInfo,
    task_info: List[TaskInfo],
    environment_info: EnvironmentInfo
) -> MessageEnvelope:
    """创建环境巡检数据消息"""
    data_json = EnvironmentDataJson(
        positionInfo=position_info,
        taskInfo=task_info,
        environmentInfo=environment_info
    )
    
    return MessageEnvelope(
        msgId=msg_id,
        msgTime=int(datetime.now().timestamp() * 1000),  # 毫秒时间戳
        msgType=MsgType.ENVIRONMENT_DATA,
        robotId=robot_id,
        dataJson=data_json.to_dict()
    )