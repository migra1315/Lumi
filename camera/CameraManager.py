"""
CameraManager.py
相机管理类 - 统一管理Orbbec相机、帧采集和RTMP推流

职责：
1. 相机生命周期管理（初始化、关闭）
2. 帧采集和缓存
3. RTMP推流管理
4. 线程安全的帧访问
5. 抓拍与推流协调
"""

import time
import threading
import subprocess
import base64
from enum import Enum
from typing import Dict, Any, Optional, List
import cv2
import numpy as np

from utils.logger_config import get_logger


class CameraState(Enum):
    """相机状态枚举"""
    DISCONNECTED = "disconnected"
    CONNECTED = "connected"
    STREAMING = "streaming"
    ERROR = "error"


class CameraManager:
    """
    相机管理类 - 统一管理相机设备、帧采集和RTMP推流

    支持的相机类型：
    - orbbec: 奥比中光深度相机
    - mock: 模拟相机（用于测试）
    """

    def __init__(self, config: Dict[str, Any] = None):
        """
        初始化相机管理器

        Args:
            config: 相机配置字典，包含：
                - camera_enabled: bool, 是否启用相机
                - camera_type: str, 相机类型 (orbbec, mock)
                - resolution: dict, {width: int, height: int}
                - fps: int, 帧率
                - stream_config: dict, 推流配置
        """
        self.config = config or {}
        self.logger = get_logger("CameraManager")

        # 相机配置
        self.camera_enabled = self.config.get('camera_enabled', True)
        self.camera_type = self.config.get('camera_type', 'orbbec')
        self.resolution = self.config.get('resolution', {'width': 1280, 'height': 720})
        self.fps = self.config.get('fps', 30)

        # 抓拍配置
        self.capture_quality = self.config.get('capture_quality', 95)

        # 推流配置
        self.stream_config = self.config.get('stream_config', {})
        self.rtmp_url = self.stream_config.get('rtmp_url', 'rtmp://127.0.0.1/live/robot')
        self.stream_enabled = self.stream_config.get('enabled', False)
        self.stream_bitrate = self.stream_config.get('bitrate', '2000k')
        self.stream_maxrate = self.stream_config.get('maxrate', '2500k')
        self.stream_bufsize = self.stream_config.get('bufsize', '5000k')
        self.stream_preset = self.stream_config.get('preset', 'ultrafast')
        self.reconnect_interval = self.stream_config.get('reconnect_interval', 5)
        self.max_reconnect_attempts = self.stream_config.get('max_reconnect_attempts', 10)

        # 状态
        self._state = CameraState.DISCONNECTED
        self._state_lock = threading.Lock()

        # Orbbec相机对象
        self._pipeline = None
        self._orbbec_config = None

        # 帧缓存
        self._frame_buffer: Optional[np.ndarray] = None
        self._frame_lock = threading.RLock()
        self._frame_timestamp = 0.0

        # 线程控制
        self._capture_thread: Optional[threading.Thread] = None
        self._stream_thread: Optional[threading.Thread] = None
        self._stop_capture = False
        self._stop_stream = False
        self._pause_stream = False

        # FFmpeg进程
        self._ffmpeg_process: Optional[subprocess.Popen] = None
        self._reconnect_count = 0

        # 统计
        self._stats = {
            'frames_captured': 0,
            'frames_streamed': 0,
            'capture_errors': 0,
            'stream_errors': 0,
            'last_capture_time': 0.0,
            'fps_actual': 0.0,
            'start_time': 0.0
        }

        self.logger.info(f"CameraManager初始化完成 - 类型: {self.camera_type}, "
                        f"分辨率: {self.resolution['width']}x{self.resolution['height']}, "
                        f"推流: {'启用' if self.stream_enabled else '禁用'}")

    # ==================== 生命周期管理 ====================

    def start(self) -> bool:
        """
        启动相机和推流

        Returns:
            bool: 启动是否成功
        """
        if not self.camera_enabled:
            self.logger.info("相机未启用")
            return True

        self.logger.info("正在启动相机管理器...")

        # 初始化相机
        if not self._init_camera():
            return False

        # 启动帧采集线程
        self._stop_capture = False
        self._capture_thread = threading.Thread(
            target=self._capture_loop,
            daemon=True,
            name="CameraCaptureThread"
        )
        self._capture_thread.start()

        # 等待预热帧
        warmup_frames = self.config.get('warmup_frames', 10)
        warmup_timeout = 5.0
        start_time = time.time()

        while self._stats['frames_captured'] < warmup_frames:
            if time.time() - start_time > warmup_timeout:
                self.logger.warning(f"预热超时，已采集 {self._stats['frames_captured']} 帧")
                break
            time.sleep(0.1)

        self.logger.info(f"相机预热完成，已采集 {self._stats['frames_captured']} 帧")

        # 启动推流（如果启用）
        if self.stream_enabled:
            if not self.start_streaming():
                self.logger.warning("推流启动失败，但相机仍可用")

        self._stats['start_time'] = time.time()
        return True

    def stop(self):
        """停止相机和推流"""
        self.logger.info("正在停止相机管理器...")

        # 停止推流
        self.stop_streaming()

        # 停止帧采集
        self._stop_capture = True
        if self._capture_thread and self._capture_thread.is_alive():
            self._capture_thread.join(timeout=3.0)
            if self._capture_thread.is_alive():
                self.logger.warning("帧采集线程未能正常停止")

        # 关闭相机
        self._close_camera()

        self._set_state(CameraState.DISCONNECTED)
        self.logger.info("相机管理器已停止")

    def _init_camera(self) -> bool:
        """初始化相机"""
        if self.camera_type == 'mock':
            return self._init_mock_camera()
        elif self.camera_type == 'orbbec':
            return self._init_orbbec_camera()
        else:
            self.logger.error(f"不支持的相机类型: {self.camera_type}")
            return False

    def _init_orbbec_camera(self) -> bool:
        """初始化Orbbec相机"""
        try:
            from pyorbbecsdk import Pipeline, Config, OBSensorType, OBFormat, OBStreamType, OBError

            self.logger.info("正在初始化Orbbec相机...")

            # 创建管道
            self._pipeline = Pipeline()
            self._orbbec_config = Config()

            # 配置RGB流
            try:
                profile_list = self._pipeline.get_stream_profile_list(OBSensorType.COLOR_SENSOR)

                # 尝试获取指定分辨率的配置
                rgb_profile = None
                profile_count = profile_list.count()

                for i in range(profile_count):
                    profile = profile_list.get_video_stream_profile(i)
                    if (profile.get_width() == self.resolution['width'] and
                        profile.get_height() == self.resolution['height']):
                        rgb_profile = profile
                        self.logger.info(f"找到匹配的分辨率配置: {profile.get_width()}x{profile.get_height()}")
                        break

                if rgb_profile is None:
                    # 使用默认配置
                    rgb_profile = profile_list.get_default_video_stream_profile()
                    actual_width = rgb_profile.get_width()
                    actual_height = rgb_profile.get_height()
                    self.logger.warning(f"未找到指定分辨率，使用默认: {actual_width}x{actual_height}")
                    self.resolution['width'] = actual_width
                    self.resolution['height'] = actual_height

                self._orbbec_config.enable_stream(rgb_profile)

            except Exception as e:
                self.logger.warning(f"无法获取RGB流配置: {e}，尝试默认配置")
                self._orbbec_config.enable_stream(OBStreamType.COLOR_STREAM)

            # 启动管道
            self._pipeline.start(self._orbbec_config)

            self._set_state(CameraState.CONNECTED)
            self.logger.info(f"Orbbec相机初始化成功: {self.resolution['width']}x{self.resolution['height']}")
            return True

        except ImportError as e:
            self.logger.error(f"pyorbbecsdk未安装: {e}")
            self._set_state(CameraState.ERROR)
            return False
        except Exception as e:
            self.logger.error(f"Orbbec相机初始化失败: {e}")
            self._set_state(CameraState.ERROR)
            return False

    def _init_mock_camera(self) -> bool:
        """初始化模拟相机"""
        self.logger.info("使用模拟相机")
        self._set_state(CameraState.CONNECTED)
        return True

    def _close_camera(self):
        """关闭相机"""
        try:
            if self._pipeline:
                self._pipeline.stop()
                self._pipeline = None
                self.logger.info("Orbbec相机已关闭")
        except Exception as e:
            self.logger.error(f"关闭相机时发生错误: {e}")

    # ==================== 帧采集 ====================

    def _capture_loop(self):
        """帧采集循环（独立线程）"""
        self.logger.info("帧采集线程已启动")

        frame_count = 0
        last_fps_time = time.time()

        while not self._stop_capture:
            try:
                if self.camera_type == 'mock':
                    frame = self._capture_mock_frame()
                else:
                    frame = self._capture_orbbec_frame()

                if frame is not None:
                    # 更新帧缓存（线程安全）
                    with self._frame_lock:
                        self._frame_buffer = frame
                        self._frame_timestamp = time.time()

                    self._stats['frames_captured'] += 1
                    self._stats['last_capture_time'] = time.time()
                    frame_count += 1

                # 计算实际FPS
                current_time = time.time()
                if current_time - last_fps_time >= 1.0:
                    self._stats['fps_actual'] = frame_count / (current_time - last_fps_time)
                    frame_count = 0
                    last_fps_time = current_time

            except Exception as e:
                self._stats['capture_errors'] += 1
                self.logger.error(f"帧采集异常: {e}")
                time.sleep(0.1)

        self.logger.info("帧采集线程已退出")

    def _capture_orbbec_frame(self) -> Optional[np.ndarray]:
        """从Orbbec相机采集一帧"""
        try:
            from pyorbbecsdk import OBFormat

            # 等待帧（100ms超时）
            frames = self._pipeline.wait_for_frames(100)
            if frames is None:
                return None

            color_frame = frames.get_color_frame()
            if color_frame is None:
                return None

            # 获取帧信息
            color_data = color_frame.get_data()
            width = color_frame.get_width()
            height = color_frame.get_height()
            format_type = color_frame.get_format()

            # 转换为numpy数组
            if format_type == OBFormat.RGB:
                frame = np.frombuffer(color_data, dtype=np.uint8)
                frame = frame.reshape((height, width, 3))
                # RGB转BGR（OpenCV格式）
                frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
            elif format_type == OBFormat.MJPG:
                frame = cv2.imdecode(np.frombuffer(color_data, dtype=np.uint8), cv2.IMREAD_COLOR)
                if frame is None:
                    return None
            else:
                self.logger.debug(f"不支持的图像格式: {format_type}")
                return None

            return frame

        except Exception as e:
            self.logger.error(f"Orbbec帧采集错误: {e}")
            return None

    def _capture_mock_frame(self) -> np.ndarray:
        """生成模拟帧"""
        # 创建测试图案
        width = self.resolution['width']
        height = self.resolution['height']

        frame = np.zeros((height, width, 3), dtype=np.uint8)

        # 绘制渐变背景
        for i in range(height):
            frame[i, :, 0] = int(255 * i / height)  # Blue gradient
            frame[i, :, 1] = 50
            frame[i, :, 2] = int(255 * (1 - i / height))  # Red gradient

        # 添加时间戳文本
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        cv2.putText(frame, f"Mock Camera - {timestamp}",
                   (50, 50), cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)
        cv2.putText(frame, f"Frame: {self._stats['frames_captured']}",
                   (50, 100), cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)

        # 控制帧率
        time.sleep(1.0 / self.fps)

        return frame

    # ==================== 帧获取（抓拍） ====================

    def capture_frame(self, timeout: float = 1.0) -> Optional[np.ndarray]:
        """
        获取当前帧

        Args:
            timeout: 等待超时时间（秒）

        Returns:
            numpy数组格式的图像，失败返回None
        """
        start_time = time.time()

        while time.time() - start_time < timeout:
            with self._frame_lock:
                if self._frame_buffer is not None:
                    # 返回帧的副本，避免外部修改
                    return self._frame_buffer.copy()
            time.sleep(0.01)

        self.logger.warning("获取帧超时")
        return None

    def capture_to_base64(self, quality: int = None, wait_new_frame: bool = True) -> Optional[str]:
        """
        获取当前帧并转换为Base64

        Args:
            quality: JPEG压缩质量 (1-100)，None则使用配置值
            wait_new_frame: 是否等待新帧到达

        Returns:
            Base64编码的图像字符串，失败返回None
        """
        if quality is None:
            quality = self.capture_quality

        # 暂停推流以确保获取最新帧
        was_streaming = self._is_streaming()
        if was_streaming and wait_new_frame:
            self._pause_streaming()

        try:
            if wait_new_frame:
                # 等待新帧到达
                old_timestamp = self._frame_timestamp
                timeout = 0.5
                start = time.time()

                while time.time() - start < timeout:
                    if self._frame_timestamp > old_timestamp:
                        break
                    time.sleep(0.01)

            # 获取帧
            frame = self.capture_frame(timeout=1.0)
            if frame is None:
                self.logger.error("获取帧失败")
                return None

            # 编码为JPEG
            encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), quality]
            success, buffer = cv2.imencode('.jpg', frame, encode_param)

            if not success:
                self.logger.error("JPEG编码失败")
                return None

            # 转换为Base64
            img_base64 = base64.b64encode(buffer).decode('utf-8')
            return img_base64

        except Exception as e:
            self.logger.error(f"图像编码失败: {e}")
            return None

        finally:
            if was_streaming and wait_new_frame:
                self._resume_streaming()

    def capture_multiple(self, count: int = 2, interval: float = 0.5,
                        quality: int = None) -> List[str]:
        """
        连续抓拍多张图像

        Args:
            count: 拍摄数量
            interval: 两次拍摄间隔（秒）
            quality: JPEG压缩质量

        Returns:
            Base64编码的图像列表
        """
        images = []

        for i in range(count):
            img_base64 = self.capture_to_base64(quality=quality, wait_new_frame=True)
            if img_base64:
                images.append(img_base64)
                self.logger.debug(f"第{i+1}张图像采集成功")
            else:
                self.logger.warning(f"第{i+1}张图像采集失败")

            if i < count - 1:
                time.sleep(interval)

        return images

    # ==================== RTMP推流 ====================

    def start_streaming(self) -> bool:
        """启动RTMP推流"""
        if not self.stream_enabled:
            self.logger.info("推流未启用")
            return True

        if self._is_streaming():
            self.logger.warning("推流已在运行")
            return True

        self.logger.info(f"正在启动RTMP推流: {self.rtmp_url}")

        # 启动FFmpeg
        if not self._start_ffmpeg():
            return False

        # 启动推流线程
        self._stop_stream = False
        self._pause_stream = False
        self._stream_thread = threading.Thread(
            target=self._stream_loop,
            daemon=True,
            name="CameraStreamThread"
        )
        self._stream_thread.start()

        self._set_state(CameraState.STREAMING)
        self._reconnect_count = 0
        self.logger.info("RTMP推流已启动")
        return True

    def stop_streaming(self):
        """停止RTMP推流"""
        if not self._is_streaming():
            return

        self.logger.info("正在停止RTMP推流...")

        self._stop_stream = True

        # 等待推流线程结束
        if self._stream_thread and self._stream_thread.is_alive():
            self._stream_thread.join(timeout=3.0)

        # 停止FFmpeg
        self._stop_ffmpeg()

        if self._state == CameraState.STREAMING:
            self._set_state(CameraState.CONNECTED)

        self.logger.info("RTMP推流已停止")

    def _start_ffmpeg(self) -> bool:
        """启动FFmpeg推流进程"""
        try:
            width = self.resolution['width']
            height = self.resolution['height']
            fps = self.fps

            # 构建FFmpeg命令
            ffmpeg_cmd = [
                'ffmpeg',
                '-y',  # 覆盖输出
                '-f', 'rawvideo',
                '-vcodec', 'rawvideo',
                '-pix_fmt', 'bgr24',
                '-s', f'{width}x{height}',
                '-r', str(fps),
                '-i', '-',  # 从stdin读取
                '-c:v', 'libx264',
                '-preset', self.stream_preset,
                '-tune', 'zerolatency',
                '-pix_fmt', 'yuv420p',
                '-g', str(fps * 2),  # GOP大小
                '-b:v', self.stream_bitrate,
                '-maxrate', self.stream_maxrate,
                '-bufsize', self.stream_bufsize,
                '-f', 'flv',
                self.rtmp_url
            ]

            self.logger.debug(f"FFmpeg命令: {' '.join(ffmpeg_cmd)}")

            self._ffmpeg_process = subprocess.Popen(
                ffmpeg_cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE
            )

            # 检查是否启动成功
            time.sleep(0.5)
            if self._ffmpeg_process.poll() is not None:
                stderr = self._ffmpeg_process.stderr.read().decode('utf-8', errors='ignore')
                self.logger.error(f"FFmpeg启动失败: {stderr[:500]}")
                return False

            self.logger.info("FFmpeg进程已启动")
            return True

        except FileNotFoundError:
            self.logger.error("FFmpeg未安装或不在PATH中")
            return False
        except Exception as e:
            self.logger.error(f"启动FFmpeg失败: {e}")
            return False

    def _stop_ffmpeg(self):
        """停止FFmpeg进程"""
        if self._ffmpeg_process:
            try:
                if self._ffmpeg_process.stdin:
                    self._ffmpeg_process.stdin.close()
                self._ffmpeg_process.terminate()
                self._ffmpeg_process.wait(timeout=3.0)
            except Exception as e:
                self.logger.warning(f"停止FFmpeg时发生错误: {e}")
                try:
                    self._ffmpeg_process.kill()
                except:
                    pass
            finally:
                self._ffmpeg_process = None
                self.logger.info("FFmpeg进程已停止")

    def _stream_loop(self):
        """推流循环（独立线程）"""
        self.logger.info("推流线程已启动")

        frame_interval = 1.0 / self.fps
        last_frame_time = time.time()

        while not self._stop_stream:
            try:
                # 检查是否暂停
                if self._pause_stream:
                    time.sleep(0.01)
                    continue

                # 控制帧率
                current_time = time.time()
                elapsed = current_time - last_frame_time
                if elapsed < frame_interval:
                    time.sleep(frame_interval - elapsed)

                # 获取当前帧
                with self._frame_lock:
                    if self._frame_buffer is None:
                        continue
                    frame = self._frame_buffer

                # 写入FFmpeg stdin
                if self._ffmpeg_process and self._ffmpeg_process.poll() is None:
                    try:
                        self._ffmpeg_process.stdin.write(frame.tobytes())
                        self._stats['frames_streamed'] += 1
                    except BrokenPipeError:
                        self.logger.error("FFmpeg进程意外退出")
                        if not self._try_reconnect():
                            break
                else:
                    if not self._try_reconnect():
                        break

                last_frame_time = time.time()

            except Exception as e:
                self._stats['stream_errors'] += 1
                self.logger.error(f"推流异常: {e}")
                time.sleep(0.1)

        self.logger.info("推流线程已退出")

    def _try_reconnect(self) -> bool:
        """尝试重连FFmpeg"""
        if self._reconnect_count >= self.max_reconnect_attempts:
            self.logger.error(f"推流重连失败次数超过限制({self.max_reconnect_attempts})")
            return False

        self._reconnect_count += 1
        self.logger.warning(f"正在尝试重连FFmpeg (第{self._reconnect_count}次)...")

        self._stop_ffmpeg()
        time.sleep(self.reconnect_interval)

        if self._start_ffmpeg():
            self.logger.info("FFmpeg重连成功")
            return True
        else:
            self.logger.error("FFmpeg重连失败")
            return False

    def _is_streaming(self) -> bool:
        """检查是否正在推流"""
        return (self._stream_thread is not None and
                self._stream_thread.is_alive() and
                not self._stop_stream)

    def _pause_streaming(self):
        """暂停推流"""
        self._pause_stream = True

    def _resume_streaming(self):
        """恢复推流"""
        self._pause_stream = False

    # ==================== 状态管理 ====================

    def _set_state(self, state: CameraState):
        """设置相机状态"""
        with self._state_lock:
            old_state = self._state
            self._state = state
            if old_state != state:
                self.logger.info(f"相机状态变更: {old_state.value} -> {state.value}")

    def get_state(self) -> CameraState:
        """获取相机状态"""
        with self._state_lock:
            return self._state

    def is_streaming(self) -> bool:
        """检查是否正在推流（公共接口）"""
        return self._is_streaming()

    def get_statistics(self) -> Dict[str, Any]:
        """获取统计信息"""
        uptime = time.time() - self._stats['start_time'] if self._stats['start_time'] > 0 else 0

        return {
            'state': self.get_state().value,
            'camera_type': self.camera_type,
            'resolution': self.resolution,
            'fps_target': self.fps,
            'fps_actual': round(self._stats['fps_actual'], 2),
            'frames_captured': self._stats['frames_captured'],
            'frames_streamed': self._stats['frames_streamed'],
            'capture_errors': self._stats['capture_errors'],
            'stream_errors': self._stats['stream_errors'],
            'is_streaming': self._is_streaming(),
            'rtmp_url': self.rtmp_url if self.stream_enabled else None,
            'uptime_seconds': round(uptime, 2)
        }

    # ==================== 深度图像（可选功能） ====================

    def capture_depth_frame(self, timeout: float = 1.0) -> Optional[np.ndarray]:
        """
        获取深度图像（如果可用）

        注意：当前实现仅支持RGB，深度图像需要额外配置

        Returns:
            深度图像numpy数组，失败返回None
        """
        self.logger.warning("深度图像采集尚未实现")
        return None

    def __del__(self):
        """析构函数"""
        try:
            self.stop()
        except Exception:
            pass
