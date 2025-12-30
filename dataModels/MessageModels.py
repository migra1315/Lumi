from dataModels.TaskModels import Station
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
    UNKNOWN = "unknown"    # 未知状态


@dataclass
class UploadResponse:
    """响应信息"""
    code: str              # 命令ID，用于匹配请求
    info: str             # 响应状态，例如 "success" 或 "error"
    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "info": self.info
        }
    
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
    agv_position_info: List[float]   # AGV位置信息 [x,y,theta]
    arm_position_info: List[float]   # 机械臂位置信息 [J1,J2,J3,J4,J5,J6]
    ext_position_info: List[float]   # 外部轴位置信息 [J1,J2,J3,J4]
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class TaskListInfo:
    """任务信息"""
    # 直接封装Task类
    task: Task
    def to_dict(self) -> Dict[str, Any]:
        # 使用Task的to_dict方法直接转换
        return self.task.to_dict()


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
    device_id: str           # 设备ID
    data_type: str           # 数据类型
    image_base64: list[str]        # 图片数据
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class EnvironmentInfo:
    """环境信息"""
    temperature: float       # 温度
    humidity: float          # 湿度
    oxygen: float            # O2浓度
    carbon_dioxide: float     # CO2浓度
    pm25: float              # PM2.5浓度
    pm10: float              # PM10浓度
    etvoc: float             # eTVOC浓度
    noise: float             # 噪音传感器值
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ArriveServicePointInfo:
    """是否到达服务点"""
    is_arrive: bool          # 是否到达服务点
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class RobotStatusDataJson:
    """机器人状态数据"""
    battery_info: BatteryInfo
    position_info: PositionInfo
    task_info: Task
    system_status: SystemStatus
    error_info: Optional[ErrorInfo] = None
    
    def to_dict(self) -> Dict[str, Any]:
        result = {
            "battery_info": self.battery_info.to_dict(),
            "position_info": self.position_info.to_dict(),
            "task_info": self.task_info.to_dict(),
            "system_status": self.system_status.to_dict()
        }
        if self.error_info:
            result["error_info"] = self.error_info.to_dict()
        return result

@dataclass
class DeviceDataJson:
    """设备巡检数据"""
    position_info: PositionInfo
    task_info: Task
    device_info: DeviceInfo
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "position_info": self.position_info.to_dict(),
            "task_info": self.task_info.to_dict(),
            "device_info": self.device_info.to_dict()
        }


@dataclass
class EnvironmentDataJson:
    """环境巡检数据"""
    position_info: PositionInfo
    task_info: Task
    environment_info: EnvironmentInfo
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "position_info": self.position_info.to_dict(),
            "task_info": self.task_info.to_dict(),
            "environment_info": self.environment_info.to_dict()
        }


@dataclass
class ArriveServePointDataJson:
    """是否到达服务点数据"""
    position_info: PositionInfo
    task_info: Task
    arrive_service_point_info: ArriveServicePointInfo
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "position_info": self.position_info.to_dict(),
            "task_info": self.task_info.to_dict(),
            "arrive_service_point_info": self.arrive_service_point_info.to_dict()
        }


@dataclass
class MessageEnvelope:
    """消息信封 - 所有数据传输的包装器"""
    msg_id: str              # 唯一消息ID，用于请求响应匹配和消息去重
    msg_time: int            # 消息产生的时间戳（毫秒）
    msg_type: MsgType        # 核心路由字段，标识功能类型
    robot_id: str            # 机器人标识，用于会话管理和消息路由
    data_json: Dict[str, Any]  # 实际的功能数据Json
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "msg_id": self.msg_id,
            "msg_time": self.msg_time,
            "msg_type": self.msg_type.value,
            "robot_id": self.robot_id,
            "data_json": self.data_json
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
        "battery_info": "battery_info",
        "position_info": "position_info", 
        "task_info": "task_info",
        "system_status": "system_status",
        "error_info": "error_info"
    },
    MsgType.DEVICE_DATA: {
        "position_info": "position_info",
        "task_info": "task_info",
        "device_info": "device_info"
    },
    MsgType.ENVIRONMENT_DATA: {
        "position_info": "position_info",
        "task_info": "task_info",
        "environment_info": "environment_info"
    },
    MsgType.ARRIVE_SERVER_POINT: {
        "position_info": "position_info",
        "task_info": "task_info",
        "arrive_service_point_info": "arrive_service_point_info"
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
        msg_id=msg_id,
        msg_time=int(datetime.now().timestamp() * 1000),  # 毫秒时间戳
        msg_type=msg_type,
        robot_id=robot_id,
        data_json=data_obj.to_dict()
    )

# 保留原有的便捷方法，内部调用通用工厂方法
def create_robot_status_message(
    msg_id: str,
    robot_id: str,
    battery_info: BatteryInfo,
    position_info: PositionInfo,
    task_info: Task,
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
    task_info: TaskListInfo,
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
    task_info: TaskListInfo,
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
    task_info: TaskListInfo,
    arrive_service_point_info: ArriveServicePointInfo
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