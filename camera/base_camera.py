"""
base_camera.py
相机管理基类 - 定义所有相机实现的统一接口

子类：
- OrbbecCameraManager: 奥比中光深度相机（帧采集 + FFmpeg RTMP 推流）
- TPLinkCameraManager: TP-LINK 豆干工业相机（HTTP API + FFmpeg RTSP→RTMP 转发推流）
"""

import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import List, Dict, Any, Optional


@dataclass
class StreamReconnectStatistics:
    """FFmpeg 推流重连统计（供 OrbbecCameraManager 和 TPLinkCameraManager 共用）"""

    total_reconnect_attempts: int = 0
    successful_reconnects: int = 0
    failed_reconnects: int = 0
    current_attempt: int = 0

    last_stable_time: Optional[datetime] = None
    stable_duration_seconds: float = 0.0

    last_disconnect_time: Optional[datetime] = None
    last_reconnect_time: Optional[datetime] = None
    total_downtime_seconds: float = 0.0

    last_error: str = ""
    error_counts: dict = field(default_factory=dict)

    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def record_disconnect(self, error: str = ""):
        with self._lock:
            self.last_disconnect_time = datetime.now()
            self.last_error = error
            self.stable_duration_seconds = 0.0
            if error:
                self.error_counts[error] = self.error_counts.get(error, 0) + 1

    def record_reconnect_attempt(self):
        with self._lock:
            self.total_reconnect_attempts += 1
            self.current_attempt += 1

    def record_reconnect_success(self):
        with self._lock:
            self.successful_reconnects += 1
            self.last_reconnect_time = datetime.now()
            self.last_stable_time = datetime.now()
            if self.last_disconnect_time:
                downtime = (self.last_reconnect_time - self.last_disconnect_time).total_seconds()
                self.total_downtime_seconds += downtime

    def record_reconnect_failure(self):
        with self._lock:
            self.failed_reconnects += 1

    def reset_attempt_counter(self):
        with self._lock:
            self.current_attempt = 0

    def get_current_attempt(self) -> int:
        with self._lock:
            return self.current_attempt

    def update_stable_duration(self) -> float:
        with self._lock:
            if self.last_stable_time:
                self.stable_duration_seconds = (datetime.now() - self.last_stable_time).total_seconds()
            return self.stable_duration_seconds

    def should_reset_attempt_counter(self, stable_threshold_seconds: float) -> bool:
        with self._lock:
            if self.current_attempt > 0 and self.last_stable_time:
                duration = (datetime.now() - self.last_stable_time).total_seconds()
                return duration >= stable_threshold_seconds
            return False

    def to_dict(self) -> dict:
        with self._lock:
            return {
                'total_reconnect_attempts': self.total_reconnect_attempts,
                'successful_reconnects': self.successful_reconnects,
                'failed_reconnects': self.failed_reconnects,
                'current_attempt': self.current_attempt,
                'stable_duration_seconds': round(self.stable_duration_seconds, 2),
                'last_disconnect_time': self.last_disconnect_time.isoformat() if self.last_disconnect_time else None,
                'last_reconnect_time': self.last_reconnect_time.isoformat() if self.last_reconnect_time else None,
                'total_downtime_seconds': round(self.total_downtime_seconds, 2),
                'last_error': self.last_error,
                'success_rate': round(self.successful_reconnects / self.total_reconnect_attempts * 100, 2)
                               if self.total_reconnect_attempts > 0 else 100.0
            }


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
        """FFmpeg 推流进程是否正在运行"""
        return False

    def get_statistics(self) -> Dict[str, Any]:
        """获取运行统计信息"""
        return {
            'state': self.get_state().value,
            'camera_type': 'unknown',
            'is_streaming': self.is_streaming(),
        }
