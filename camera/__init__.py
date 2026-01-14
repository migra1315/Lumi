"""
camera 模块
提供相机管理和视频流功能

主要组件：
- CameraManager: 相机管理类，统一管理相机采集和RTMP推流
- CameraState: 相机状态枚举
"""

from camera.CameraManager import CameraManager, CameraState

__all__ = ['CameraManager', 'CameraState']
