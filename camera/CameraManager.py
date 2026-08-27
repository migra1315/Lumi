"""
CameraManager.py
相机管理器工厂模块

根据配置中的 camera_type 选择并返回对应的相机实现：
  - "orbbec" / "mock": OrbbecCameraManager（帧采集 + FFmpeg RTMP 推流）
  - "tplink":          TPLinkCameraManager（HTTP API + 相机自带 RTSP）

用法：
    from camera.CameraManager import CameraManager, CameraState

    camera = CameraManager(config)   # 返回对应子类实例
    camera.start()
    images = camera.capture_multiple(count=2)
    camera.stop()
"""

from typing import Dict, Any

from camera.base_camera import BaseCameraManager, CameraState


def CameraManager(config: Dict[str, Any] = None) -> BaseCameraManager:
    """
    相机管理器工厂函数

    根据 config["camera_type"] 返回对应的相机实现实例。
    调用方无需感知具体实现，直接使用返回对象即可。

    Args:
        config: camera_config 字典，必须包含 "camera_type" 字段

    Returns:
        BaseCameraManager 子类实例

    Raises:
        ValueError: 当 camera_type 为未知值时
    """
    config = config or {}
    camera_type = config.get('camera_type', 'orbbec')

    if camera_type == 'tplink':
        from camera.tplink_camera import TPLinkCameraManager
        return TPLinkCameraManager(config)
    elif camera_type in ('orbbec', 'mock'):
        from camera.orbbec_camera import OrbbecCameraManager
        return OrbbecCameraManager(config)
    else:
        raise ValueError(
            f"未知的相机类型: '{camera_type}'，"
            f"支持的类型: 'orbbec', 'mock', 'tplink'"
        )


__all__ = ['CameraManager', 'CameraState']
