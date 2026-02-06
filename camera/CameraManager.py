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
import sys
import random
from enum import Enum
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Any, Optional, List
import cv2
import numpy as np

from utils.logger_config import get_logger


@dataclass
class StreamReconnectStatistics:
    """推流重连统计"""

    # 重连统计
    total_reconnect_attempts: int = 0
    successful_reconnects: int = 0
    failed_reconnects: int = 0
    current_attempt: int = 0

    # 稳定性追踪
    last_stable_time: Optional[datetime] = None  # 最后一次稳定运行的时间
    stable_duration_seconds: float = 0.0  # 当前稳定运行时长

    # 时间统计
    last_disconnect_time: Optional[datetime] = None
    last_reconnect_time: Optional[datetime] = None
    total_downtime_seconds: float = 0.0

    # 错误信息
    last_error: str = ""
    error_counts: dict = field(default_factory=dict)

    # 线程锁
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def record_disconnect(self, error: str = ""):
        """记录断开连接"""
        with self._lock:
            self.last_disconnect_time = datetime.now()
            self.last_error = error
            self.stable_duration_seconds = 0.0
            if error:
                self.error_counts[error] = self.error_counts.get(error, 0) + 1

    def record_reconnect_attempt(self):
        """记录重连尝试"""
        with self._lock:
            self.total_reconnect_attempts += 1
            self.current_attempt += 1

    def record_reconnect_success(self):
        """记录重连成功"""
        with self._lock:
            self.successful_reconnects += 1
            self.last_reconnect_time = datetime.now()
            self.last_stable_time = datetime.now()

            # 计算停机时间
            if self.last_disconnect_time:
                downtime = (self.last_reconnect_time - self.last_disconnect_time).total_seconds()
                self.total_downtime_seconds += downtime

    def record_reconnect_failure(self):
        """记录重连失败"""
        with self._lock:
            self.failed_reconnects += 1

    def reset_attempt_counter(self):
        """重置重试计数"""
        with self._lock:
            self.current_attempt = 0

    def get_current_attempt(self) -> int:
        """获取当前重试次数"""
        with self._lock:
            return self.current_attempt

    def update_stable_duration(self) -> float:
        """更新并返回稳定运行时长（秒）"""
        with self._lock:
            if self.last_stable_time:
                self.stable_duration_seconds = (datetime.now() - self.last_stable_time).total_seconds()
            return self.stable_duration_seconds

    def should_reset_attempt_counter(self, stable_threshold_seconds: float) -> bool:
        """检查是否应该重置重试计数（基于稳定运行时长）"""
        with self._lock:
            if self.current_attempt > 0 and self.last_stable_time:
                duration = (datetime.now() - self.last_stable_time).total_seconds()
                return duration >= stable_threshold_seconds
            return False

    def to_dict(self) -> dict:
        """转换为字典"""
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

        # 相机配置 - 支持新格式 enabled 和旧格式 camera_enabled
        self.camera_enabled = self.config.get('enabled', self.config.get('camera_enabled', True))
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

        # 重连配置（新增）- 支持新旧两种格式
        self._init_reconnect_config()

        # 重连统计（新增）
        self._reconnect_stats = StreamReconnectStatistics()

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
        self._ffmpeg_monitor_thread: Optional[threading.Thread] = None
        self._ffmpeg_healthy = False
        self._last_ffmpeg_error = ""

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

    def _init_reconnect_config(self):
        """初始化重连配置 - 支持向后兼容"""
        # 旧格式参数（向后兼容）
        old_interval = self.stream_config.get('reconnect_interval', 5)
        old_max_attempts = self.stream_config.get('max_reconnect_attempts', 10)

        # 新格式配置
        reconnect_config = self.stream_config.get('reconnect', {})

        self._reconnect_base_delay = float(reconnect_config.get('base_delay', old_interval))
        self._reconnect_max_delay = float(reconnect_config.get('max_delay', 30.0))
        self._reconnect_max_attempts = int(reconnect_config.get('max_attempts', old_max_attempts))
        self._reconnect_jitter_factor = float(reconnect_config.get('jitter_factor', 0.3))
        # 稳定运行N秒后重置重试计数（默认60秒）
        self._reconnect_stable_reset_seconds = float(reconnect_config.get('stable_reset_seconds', 60.0))

        self.logger.info(f"重连配置: base_delay={self._reconnect_base_delay}s, "
                        f"max_delay={self._reconnect_max_delay}s, "
                        f"max_attempts={self._reconnect_max_attempts}, "
                        f"jitter={self._reconnect_jitter_factor}, "
                        f"stable_reset={self._reconnect_stable_reset_seconds}s")

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
                self.logger.error(f"预热超时，已采集 {self._stats['frames_captured']} 帧")
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
        self._reconnect_stats.reset_attempt_counter()
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
        """启动FFmpeg推流进程 - 增强版，带进程守护"""
        try:
            width = self.resolution['width']
            height = self.resolution['height']
            fps = self.fps

            # 构建FFmpeg命令 - 增强参数
            ffmpeg_cmd = [
                'ffmpeg',
                '-y',  # 覆盖输出
                '-loglevel', 'warning',  # 设置日志级别，减少冗余输出但保留警告和错误
                '-f', 'rawvideo',
                '-vcodec', 'rawvideo',
                '-pix_fmt', 'bgr24',
                '-s', f'{width}x{height}',
                '-r', str(fps),
                '-thread_queue_size', '512',  # 增加输入队列大小
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
                '-flvflags', 'no_duration_filesize',  # 避免FLV duration问题
                self.rtmp_url
            ]

            self.logger.debug(f"FFmpeg启动: 输入=stdin, 分辨率={width}x{height}, FPS={fps}, 输出={self.rtmp_url}")

            # Windows平台使用CREATE_NO_WINDOW避免弹出控制台
            creation_flags = 0
            if sys.platform == 'win32':
                creation_flags = subprocess.CREATE_NO_WINDOW

            self._ffmpeg_process = subprocess.Popen(
                ffmpeg_cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=creation_flags
            )

            # 重置健康状态
            self._ffmpeg_healthy = False
            self._last_ffmpeg_error = ""

            # 启动stderr监控线程
            self._ffmpeg_monitor_thread = threading.Thread(
                target=self._monitor_ffmpeg_output,
                daemon=True,
                name="FFmpegMonitorThread"
            )
            self._ffmpeg_monitor_thread.start()

            # 检查是否启动成功（等待更长时间确认）
            time.sleep(1.0)
            if self._ffmpeg_process.poll() is not None:
                exit_code = self._ffmpeg_process.returncode
                self.logger.error(f"FFmpeg启动失败，退出码: {exit_code}, 最后错误: {self._last_ffmpeg_error}")
                return False

            self._ffmpeg_healthy = True
            self.logger.info(f"FFmpeg进程已启动 (PID: {self._ffmpeg_process.pid})")
            return True

        except FileNotFoundError:
            self.logger.error("FFmpeg未安装或不在PATH中")
            return False
        except Exception as e:
            self.logger.error(f"启动FFmpeg失败: {e}")
            return False

    def _monitor_ffmpeg_output(self):
        """监控FFmpeg的stderr输出（独立线程）"""
        self.logger.debug("FFmpeg输出监控线程已启动")

        try:
            while self._ffmpeg_process and self._ffmpeg_process.poll() is None:
                if self._ffmpeg_process.stderr:
                    line = self._ffmpeg_process.stderr.readline()
                    if line:
                        decoded_line = line.decode('utf-8', errors='ignore').strip()
                        if decoded_line:
                            # 记录最后的错误信息
                            self._last_ffmpeg_error = decoded_line

                            # 根据内容级别输出日志
                            if 'error' in decoded_line.lower() or 'failed' in decoded_line.lower():
                                self.logger.error(f"FFmpeg: {decoded_line}")
                            elif 'warning' in decoded_line.lower():
                                self.logger.warning(f"FFmpeg: {decoded_line}")
                            else:
                                self.logger.debug(f"FFmpeg: {decoded_line}")
                else:
                    time.sleep(0.1)

            # 进程已退出，获取退出码
            if self._ffmpeg_process:
                exit_code = self._ffmpeg_process.poll()
                if exit_code is not None and exit_code != 0:
                    # 读取剩余的stderr内容
                    remaining = self._ffmpeg_process.stderr.read() if self._ffmpeg_process.stderr else b''
                    if remaining:
                        remaining_str = remaining.decode('utf-8', errors='ignore').strip()
                        if remaining_str:
                            self._last_ffmpeg_error = remaining_str
                            self.logger.debug(f"FFmpeg剩余输出: {remaining_str[:500]}")

                    self.logger.warning(f"FFmpeg进程已退出，退出码: {exit_code}")
                    self._ffmpeg_healthy = False

        except Exception as e:
            self.logger.debug(f"FFmpeg监控线程异常: {e}")

        self.logger.debug("FFmpeg输出监控线程已退出")

    def _stop_ffmpeg(self):
        """停止FFmpeg进程"""
        if self._ffmpeg_process:
            try:
                # 先关闭stdin，让FFmpeg知道输入结束
                if self._ffmpeg_process.stdin:
                    try:
                        self._ffmpeg_process.stdin.close()
                    except Exception:
                        pass

                # 优雅终止
                self._ffmpeg_process.terminate()

                # 等待进程结束
                try:
                    self._ffmpeg_process.wait(timeout=3.0)
                except subprocess.TimeoutExpired:
                    self.logger.warning("FFmpeg未能正常终止，强制结束")
                    self._ffmpeg_process.kill()
                    self._ffmpeg_process.wait(timeout=1.0)

            except Exception as e:
                self.logger.warning(f"停止FFmpeg时发生错误: {e}")
                try:
                    self._ffmpeg_process.kill()
                except Exception:
                    pass
            finally:
                self._ffmpeg_healthy = False
                self._ffmpeg_process = None
                self.logger.info("FFmpeg进程已停止")

        # 等待监控线程结束
        if self._ffmpeg_monitor_thread and self._ffmpeg_monitor_thread.is_alive():
            self._ffmpeg_monitor_thread.join(timeout=1.0)

    def _stream_loop(self):
        """推流循环（独立线程）- 使用指数退避重连"""
        self.logger.info("推流线程已启动")

        frame_interval = 1.0 / self.fps
        last_frame_time = time.time()
        last_health_check = time.time()
        last_stability_check = time.time()
        health_check_interval = 5.0  # 每5秒进行一次健康检查
        stability_check_interval = 10.0  # 每10秒检查一次稳定性
        consecutive_write_errors = 0
        max_consecutive_errors = 3

        while not self._stop_stream:
            try:
                # 检查是否暂停
                if self._pause_stream:
                    time.sleep(0.01)
                    continue

                current_time = time.time()

                # 定期健康检查
                if current_time - last_health_check >= health_check_interval:
                    if not self._check_ffmpeg_health():
                        self.logger.warning("FFmpeg健康检查失败，触发重连")
                        self._reconnect_stats.record_disconnect("health_check_failed")

                        if not self._try_reconnect():
                            break
                        consecutive_write_errors = 0
                        # 重连后重置健康检查时间
                        last_health_check = time.time()
                        last_stability_check = time.time()
                        continue
                    last_health_check = current_time

                # 定期检查稳定性，判断是否重置重试计数
                if current_time - last_stability_check >= stability_check_interval:
                    if self._reconnect_stats.should_reset_attempt_counter(self._reconnect_stable_reset_seconds):
                        self._reconnect_stats.reset_attempt_counter()
                        self.logger.info(
                            f"推流稳定运行超过{self._reconnect_stable_reset_seconds}秒，重置重试计数"
                        )
                    last_stability_check = current_time

                # 控制帧率
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
                        self._ffmpeg_process.stdin.flush()  # 立即刷新缓冲区
                        self._stats['frames_streamed'] += 1
                        consecutive_write_errors = 0  # 重置错误计数
                    except (BrokenPipeError, OSError) as e:
                        consecutive_write_errors += 1
                        self.logger.error(
                            f"FFmpeg写入错误 ({consecutive_write_errors}/{max_consecutive_errors}): {e}"
                        )

                        if consecutive_write_errors >= max_consecutive_errors:
                            self.logger.error("连续写入错误过多，触发重连")
                            self._reconnect_stats.record_disconnect(f"write_error: {e}")

                            if not self._try_reconnect():
                                break
                            consecutive_write_errors = 0
                            # 重连后重置时间
                            last_health_check = time.time()
                            last_stability_check = time.time()
                else:
                    # FFmpeg进程不存在或已退出
                    exit_code = self._ffmpeg_process.poll() if self._ffmpeg_process else None
                    error_msg = f"process_exit: {exit_code}"
                    self.logger.error(
                        f"FFmpeg进程已退出 (退出码: {exit_code}), "
                        f"最后错误: {self._last_ffmpeg_error}"
                    )
                    self._reconnect_stats.record_disconnect(error_msg)

                    if not self._try_reconnect():
                        break
                    consecutive_write_errors = 0
                    # 重连后重置时间
                    last_health_check = time.time()
                    last_stability_check = time.time()

                last_frame_time = time.time()

            except Exception as e:
                self._stats['stream_errors'] += 1
                self.logger.error(f"推流异常: {e}")
                time.sleep(0.1)

        self.logger.info("推流线程已退出")

    def _check_ffmpeg_health(self) -> bool:
        """检查FFmpeg进程健康状态"""
        if not self._ffmpeg_process:
            self.logger.debug("FFmpeg进程不存在")
            return False

        poll_result = self._ffmpeg_process.poll()
        if poll_result is not None:
            self.logger.debug(f"FFmpeg进程已退出，退出码: {poll_result}")
            return False

        if not self._ffmpeg_healthy:
            self.logger.debug("FFmpeg健康标志为False")
            return False

        return True

    def _calculate_backoff_delay(self, attempt: int) -> float:
        """
        计算指数退避延迟

        Args:
            attempt: 当前重试次数（从1开始）

        Returns:
            float: 实际延迟时间（秒）
        """
        # 指数退避：delay = base_delay * 2^(attempt-1)
        exponential_delay = self._reconnect_base_delay * (2 ** (attempt - 1))

        # 应用最大延迟上限
        capped_delay = min(exponential_delay, self._reconnect_max_delay)

        # 添加随机抖动
        if self._reconnect_jitter_factor > 0:
            jitter = capped_delay * self._reconnect_jitter_factor * (2 * random.random() - 1)
            final_delay = max(0.1, capped_delay + jitter)
        else:
            final_delay = capped_delay

        return round(final_delay, 2)

    def _try_reconnect(self) -> bool:
        """
        尝试重连FFmpeg - 使用指数退避算法

        Returns:
            bool: 重连是否成功
        """
        current_attempt = self._reconnect_stats.get_current_attempt()

        # 检查是否超过最大重试次数
        if current_attempt >= self._reconnect_max_attempts:
            self.logger.error(f"推流重连失败次数超过限制({self._reconnect_max_attempts})")
            self._set_state(CameraState.ERROR)
            return False

        # 记录重连尝试
        self._reconnect_stats.record_reconnect_attempt()
        current_attempt = self._reconnect_stats.get_current_attempt()

        # 计算指数退避延迟
        wait_time = self._calculate_backoff_delay(current_attempt)

        self.logger.warning(
            f"正在尝试重连FFmpeg (第{current_attempt}/{self._reconnect_max_attempts}次)，"
            f"原因: {self._reconnect_stats.last_error or '未知'}，"
            f"等待{wait_time}秒..."
        )

        # 停止当前FFmpeg进程
        self._stop_ffmpeg()

        # 可中断等待
        wait_start = time.time()
        while time.time() - wait_start < wait_time:
            if self._stop_stream:
                self.logger.info("收到停止信号，取消重连")
                return False
            time.sleep(0.1)

        # 尝试启动新的FFmpeg进程
        if self._start_ffmpeg():
            self._reconnect_stats.record_reconnect_success()
            self.logger.info(f"FFmpeg重连成功 (第{current_attempt}次尝试)")
            # 注意：重试计数的重置由 _stream_loop 中的稳定性检查负责
            # 当推流稳定运行 stable_reset_seconds 后自动重置
            return True
        else:
            self._reconnect_stats.record_reconnect_failure()
            self.logger.error(
                f"FFmpeg重连失败 (第{current_attempt}次尝试)，"
                f"原因: {self._last_ffmpeg_error}"
            )
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
        """获取统计信息（增强版）"""
        uptime = time.time() - self._stats['start_time'] if self._stats['start_time'] > 0 else 0

        # FFmpeg进程状态
        ffmpeg_pid = None
        ffmpeg_running = False
        if self._ffmpeg_process:
            ffmpeg_pid = self._ffmpeg_process.pid
            ffmpeg_running = self._ffmpeg_process.poll() is None

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
            'uptime_seconds': round(uptime, 2),
            # FFmpeg状态信息
            'ffmpeg_pid': ffmpeg_pid,
            'ffmpeg_running': ffmpeg_running,
            'ffmpeg_healthy': self._ffmpeg_healthy,
            'ffmpeg_last_error': self._last_ffmpeg_error[:200] if self._last_ffmpeg_error else None,
            # 重连统计（新增）
            'reconnect_stats': self._reconnect_stats.to_dict()
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
