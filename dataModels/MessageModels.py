from dataclasses import dataclass, asdict
from enum import Enum
from typing import Dict, List, Optional, Any
from datetime import datetime
import json
from dataModels.TaskModels import Task



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
    inspection_task_list: List[Task]
    
    def to_dict(self) -> Dict[str, Any]:
        # 使用InspectionTask的to_dict方法直接转换
        return [task.to_dict() for task in self.inspection_task_list]


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
class ArriveServicePointInfo:
    """是否到达服务点"""
    isArrive: bool          # 是否到达服务点
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class DeviceDataJson:
    """设备巡检数据"""
    positionInfo: PositionInfo
    taskInfo: TaskInfo
    deviceInfo: DeviceInfo
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "positionInfo": self.positionInfo.to_dict(),
            "taskInfo": self.taskInfo.to_dict(),
            "deviceInfo": self.deviceInfo.to_dict()
        }


@dataclass
class EnvironmentDataJson:
    """环境巡检数据"""
    positionInfo: PositionInfo
    taskInfo: TaskInfo
    environmentInfo: EnvironmentInfo
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "positionInfo": self.positionInfo.to_dict(),
            "taskInfo": self.taskInfo.to_dict(),
            "environmentInfo": self.environmentInfo.to_dict()
        }


@dataclass
class RobotStatusDataJson:
    """机器人状态数据"""
    batteryInfo: BatteryInfo
    positionInfo: PositionInfo
    taskInfo: TaskInfo
    systemStatus: SystemStatus
    errorInfo: Optional[ErrorInfo] = None
    
    def to_dict(self) -> Dict[str, Any]:
        result = {
            "batteryInfo": self.batteryInfo.to_dict(),
            "positionInfo": self.positionInfo.to_dict(),
            "taskInfo": self.taskInfo.to_dict(),
            "systemStatus": self.systemStatus.to_dict()
        }
        if self.errorInfo:
            result["errorInfo"] = self.errorInfo.to_dict()
        return result


@dataclass
class ArriveServePointDataJson:
    """是否到达服务点数据"""
    positionInfo: PositionInfo
    taskInfo: TaskInfo
    arriveServePointInfo: ArriveServicePointInfo
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "positionInfo": self.positionInfo.to_dict(),
            "taskInfo": self.taskInfo.to_dict(),
            "arriveServePointInfo": self.arriveServePointInfo.to_dict()
        }

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

# 消息类型与对应数据类的映射
_MSG_TYPE_TO_DATA_CLASS = {
    MsgType.ROBOT_STATUS: RobotStatusDataJson,
    MsgType.DEVICE_DATA: DeviceDataJson,
    MsgType.ENVIRONMENT_DATA: EnvironmentDataJson,
    MsgType.ARRIVE_SERVER_POINT: ArriveServePointDataJson,
}

# 消息类型与数据类参数名的映射
_MSG_TYPE_TO_PARAM_NAME = {
    MsgType.ROBOT_STATUS: {
        "batteryInfo": "battery_info",
        "positionInfo": "position_info", 
        "taskInfo": "task_info",
        "systemStatus": "system_status",
        "errorInfo": "error_info"
    },
    MsgType.DEVICE_DATA: {
        "positionInfo": "position_info",
        "taskInfo": "task_info",
        "deviceInfo": "device_info"
    },
    MsgType.ENVIRONMENT_DATA: {
        "positionInfo": "position_info",
        "taskInfo": "task_info",
        "environmentInfo": "environment_info"
    },
    MsgType.ARRIVE_SERVER_POINT: {
        "positionInfo": "position_info",
        "taskInfo": "task_info",
        "arriveServePointInfo": "arrive_service_point_info"
    },
}

def create_message_envelope(
    msg_id: str,
    robot_id: str,
    msg_type: MsgType,
    **kwargs
) -> MessageEnvelope:
    """创建消息信封（通用工厂方法）
    
    Args:
        msg_id: 消息ID
        robot_id: 机器人ID
        msg_type: 消息类型
        **kwargs: 消息数据参数
        
    Returns:
        MessageEnvelope: 消息信封
    """
    if msg_type not in _MSG_TYPE_TO_DATA_CLASS:
        raise ValueError(f"不支持的消息类型: {msg_type}")
    
    # 获取对应的数据类和参数名映射
    data_class = _MSG_TYPE_TO_DATA_CLASS[msg_type]
    param_mapping = _MSG_TYPE_TO_PARAM_NAME[msg_type]
    
    # 转换参数名
    data_params = {}
    for data_param, kwarg_param in param_mapping.items():
        if kwarg_param in kwargs:
            data_params[data_param] = kwargs[kwarg_param]
    
    # 创建数据对象
    data_obj = data_class(**data_params)
    
    # 创建消息信封
    return MessageEnvelope(
        msgId=msg_id,
        msgTime=int(datetime.now().timestamp() * 1000),  # 毫秒时间戳
        msgType=msg_type,
        robotId=robot_id,
        dataJson=data_obj.to_dict()
    )

# 保留原有的便捷方法，内部调用通用工厂方法
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
    return create_message_envelope(
        msg_id=msg_id,
        robot_id=robot_id,
        msg_type=MsgType.ROBOT_STATUS,
        battery_info=battery_info,
        position_info=position_info,
        task_info=task_info,
        system_status=system_status,
        error_info=error_info
    )

def create_device_data_message(
    msg_id: str,
    robot_id: str,
    position_info: PositionInfo,
    task_info: List[TaskInfo],
    device_info: DeviceInfo
) -> MessageEnvelope:
    """创建设备巡检数据消息"""
    return create_message_envelope(
        msg_id=msg_id,
        robot_id=robot_id,
        msg_type=MsgType.DEVICE_DATA,
        position_info=position_info,
        task_info=task_info,
        device_info=device_info
    )

def create_environment_data_message(
    msg_id: str,
    robot_id: str,
    position_info: PositionInfo,
    task_info: List[TaskInfo],
    environment_info: EnvironmentInfo
) -> MessageEnvelope:
    """创建环境巡检数据消息"""
    return create_message_envelope(
        msg_id=msg_id,
        robot_id=robot_id,
        msg_type=MsgType.ENVIRONMENT_DATA,
        position_info=position_info,
        task_info=task_info,
        environment_info=environment_info
    )

def create_arrive_serve_point_message(
    msg_id: str,
    robot_id: str,
    position_info: PositionInfo,
    task_info: List[TaskInfo],
    arrive_service_point_info: ArriveServePointInfo
) -> MessageEnvelope:
    """创建是否到达服务点消息"""
    return create_message_envelope(
        msg_id=msg_id,
        robot_id=robot_id,
        msg_type=MsgType.ARRIVE_SERVER_POINT,
        position_info=position_info,
        task_info=task_info,
        arrive_service_point_info=arrive_service_point_info
    )