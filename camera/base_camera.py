"""
base_camera.py
相机管理基类 - 定义所有相机实现的统一接口

子类：
- OrbbecCameraManager: 奥比中光深度相机（帧采集 + FFmpeg RTMP 推流）
- TPLinkCameraManager: TP-LINK 豆干工业相机（HTTP API + 相机自带 RTSP）
"""

from abc import ABC, abstractmethod
from enum import Enum
from typing import List, Dict, Any


class CameraState(Enum):
    """相机状态枚举"""
    DISCONNECTED = "disconnected"
    CONNECTED = "connected"
    STREAMING = "streaming"
    ERROR = "error"


class BaseCameraManager(ABC):
    """
    相机管理基类

    定义上层代码（RobotController）使用的全部公共接口。
    推流方式、帧采集循环等属于各子类的内部实现细节，不在此约束。
    """

    @abstractmethod
    def start(self) -> bool:
        """
        启动相机

        - OrbbecCameraManager: 初始化 SDK、启动帧采集线程和 RTMP 推流
        - TPLinkCameraManager: 完成 Token 鉴权，确认相机可达

        Returns:
            bool: 启动是否成功
        """
        ...

    @abstractmethod
    def stop(self):
        """停止相机，释放所有资源"""
        ...

    @abstractmethod
    def capture_multiple(self, count: int = 2, interval: float = 0.5,
                         quality: int = None) -> List[str]:
        """
        连续抓拍多张图像

        Args:
            count:    拍摄张数
            interval: 两次拍摄间隔（秒）
            quality:  JPEG 压缩质量（1-100），None 使用各相机默认值

        Returns:
            Base64 编码的图像字符串列表（失败项不计入，长度可能小于 count）
        """
        ...

    def get_state(self) -> CameraState:
        """获取相机当前状态"""
        return CameraState.DISCONNECTED

    def is_streaming(self) -> bool:
        """
        是否正在主动推流

        主动推流模式（Orbbec RTMP）返回 True；
        被动拉流模式（TPLINK 自带 RTSP）始终返回 False。
        """
        return False

    def get_statistics(self) -> Dict[str, Any]:
        """获取运行统计信息"""
        return {
            'state': self.get_state().value,
            'camera_type': 'unknown',
            'is_streaming': self.is_streaming(),
        }
