"""
离线消息处理模块

使用 protobuf 原生二进制序列化方式处理离线消息的存储和恢复。
支持 RobotUploadRequest (clientUpload流) 和 ClientStreamMessage (serverCommand流) 两种消息类型。
"""

from typing import Optional, Tuple, Union
from gRPC import RobotService_pb2 as robot_pb2
from utils.logger_config import get_logger

logger = get_logger(__name__)

# 消息类型映射表：类型名称字符串 -> protobuf类
MESSAGE_TYPE_MAP = {
    'RobotUploadRequest': robot_pb2.RobotUploadRequest,
    'ClientStreamMessage': robot_pb2.ClientStreamMessage,
}

# 流类型常量
STREAM_TYPE_CLIENT_UPLOAD = 'client_upload'
STREAM_TYPE_SERVER_COMMAND = 'server_command'


def serialize_message(message: Union[robot_pb2.RobotUploadRequest, robot_pb2.ClientStreamMessage]) -> Tuple[str, bytes]:
    """
    将 protobuf 消息序列化为二进制数据

    Args:
        message: protobuf 消息对象 (RobotUploadRequest 或 ClientStreamMessage)

    Returns:
        Tuple[str, bytes]: (消息类型名称, 序列化后的二进制数据)

    Raises:
        ValueError: 不支持的消息类型
    """
    msg_type_name = type(message).__name__

    if msg_type_name not in MESSAGE_TYPE_MAP:
        raise ValueError(f"不支持的消息类型: {msg_type_name}")

    try:
        payload_bytes = message.SerializeToString()
        logger.debug(f"消息序列化成功: type={msg_type_name}, size={len(payload_bytes)} bytes")
        return msg_type_name, payload_bytes
    except Exception as e:
        logger.error(f"消息序列化失败: type={msg_type_name}, error={e}")
        raise


def deserialize_message(msg_type_name: str, payload_bytes: bytes) -> Optional[Union[robot_pb2.RobotUploadRequest, robot_pb2.ClientStreamMessage]]:
    """
    将二进制数据反序列化为 protobuf 消息对象

    Args:
        msg_type_name: 消息类型名称 ('RobotUploadRequest' 或 'ClientStreamMessage')
        payload_bytes: 序列化的二进制数据

    Returns:
        反序列化后的 protobuf 消息对象，失败时返回 None
    """
    if msg_type_name not in MESSAGE_TYPE_MAP:
        logger.error(f"不支持的消息类型: {msg_type_name}")
        return None

    try:
        message_class = MESSAGE_TYPE_MAP[msg_type_name]
        message = message_class()
        message.ParseFromString(payload_bytes)
        logger.debug(f"消息反序列化成功: type={msg_type_name}, size={len(payload_bytes)} bytes")
        return message
    except Exception as e:
        logger.error(f"消息反序列化失败: type={msg_type_name}, error={e}")
        return None


def get_stream_type_for_message(message: Union[robot_pb2.RobotUploadRequest, robot_pb2.ClientStreamMessage]) -> str:
    """
    根据消息类型确定对应的流类型

    Args:
        message: protobuf 消息对象

    Returns:
        流类型字符串 ('client_upload' 或 'server_command')

    Raises:
        ValueError: 不支持的消息类型
    """
    msg_type_name = type(message).__name__

    if msg_type_name == 'RobotUploadRequest':
        return STREAM_TYPE_CLIENT_UPLOAD
    elif msg_type_name == 'ClientStreamMessage':
        return STREAM_TYPE_SERVER_COMMAND
    else:
        raise ValueError(f"不支持的消息类型: {msg_type_name}")
