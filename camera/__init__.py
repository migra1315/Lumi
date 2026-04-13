"""
camera 模块
提供相机管理功能，支持奥比中光 Orbbec 和 TP-LINK 豆干工业相机

主要组件：
- CameraManager:        工厂函数，根据 camera_type 返回对应实现
- CameraState:          相机状态枚举
- BaseCameraManager:    相机管理基类（接口定义）
- OrbbecCameraManager:  奥比中光相机实现（含 RTMP 推流）
- TPLinkCameraManager:  TP-LINK 相机实现（含 OCR / 表盘识别等功能）
"""

from camera.base_camera import BaseCameraManager, CameraState
from camera.CameraManager import CameraManager

__all__ = ['CameraManager', 'CameraState', 'BaseCameraManager']
