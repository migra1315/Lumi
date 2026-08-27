"""
orbbec_camera.py
奥比中光相机管理类 - 统一管理 Orbbec 相机、帧采集和 RTMP 推流

从原 CameraManager.py 提取，实现 BaseCameraManager 接口。
支持 camera_type: orbbec / mock
"""

import base64
import random
import subprocess
import sys
import threading
import time
from typing import Dict, Any, Optional, List

import cv2
import numpy as np

from camera.base_camera import BaseCameraManager, CameraState, StreamReconnectStatistics
from utils.logger_config import get_logger


class OrbbecCameraManager(BaseCameraManager):
    """
    奥比中光相机管理类

    支持的 camera_type：
    - orbbec: 奥比中光深度相机
    - mock:   模拟相机（用于测试）
    """

    def __init__(self, config: Dict[str, Any] = None):
        self.config = config or {}
        self.logger = get_logger("OrbbecCamera")

        self.camera_enabled = self.config.get('enabled', self.config.get('camera_enabled', True))
        self.camera_type = self.config.get('camera_type', 'orbbec')
        self.resolution = self.config.get('resolution', {'width': 1280, 'height': 720})
        self.fps = self.config.get('fps', 30)

        self.capture_quality = self.config.get('capture_quality', 95)

        self.stream_config = self.config.get('stream_config', {})
        self.rtmp_url = self.stream_config.get('rtmp_url', 'rtmp://127.0.0.1/live/robot')
        self.stream_enabled = self.stream_config.get('enabled', False)
        self.stream_bitrate = self.stream_config.get('bitrate', '2000k')
        self.stream_maxrate = self.stream_config.get('maxrate', '2500k')
        self.stream_bufsize = self.stream_config.get('bufsize', '5000k')
        self.stream_preset = self.stream_config.get('preset', 'ultrafast')

        self._init_reconnect_config()
        self._reconnect_stats = StreamReconnectStatistics()

        self._state = CameraState.DISCONNECTED
        self._state_lock = threading.Lock()

        self._pipeline = None
        self._orbbec_config = None

        self._frame_buffer: Optional[np.ndarray] = None
        self._frame_lock = threading.RLock()
        self._frame_timestamp = 0.0

        self._capture_thread: Optional[threading.Thread] = None
        self._stream_thread: Optional[threading.Thread] = None
        self._stop_capture = False
        self._stop_stream = False
        self._pause_stream = False

        self._ffmpeg_process: Optional[subprocess.Popen] = None
        self._ffmpeg_monitor_thread: Optional[threading.Thread] = None
        self._ffmpeg_healthy = False
        self._last_ffmpeg_error = ""

        self._stats = {
            'frames_captured': 0,
            'frames_streamed': 0,
            'capture_errors': 0,
            'stream_errors': 0,
            'last_capture_time': 0.0,
            'fps_actual': 0.0,
            'start_time': 0.0
        }

        self.logger.info(f"OrbbecCameraManager 初始化完成 - 类型: {self.camera_type}, "
                         f"分辨率: {self.resolution['width']}x{self.resolution['height']}, "
                         f"推流: {'启用' if self.stream_enabled else '禁用'}")

    def _init_reconnect_config(self):
        """初始化重连配置"""
        old_interval = self.stream_config.get('reconnect_interval', 5)
        old_max_attempts = self.stream_config.get('max_reconnect_attempts', 10)

        reconnect_config = self.stream_config.get('reconnect', {})

        self._reconnect_base_delay = float(reconnect_config.get('base_delay', old_interval))
        self._reconnect_max_delay = float(reconnect_config.get('max_delay', 30.0))
        self._reconnect_max_attempts = int(reconnect_config.get('max_attempts', old_max_attempts))
        self._reconnect_jitter_factor = float(reconnect_config.get('jitter_factor', 0.3))
        self._reconnect_stable_reset_seconds = float(reconnect_config.get('stable_reset_seconds', 60.0))

        self.logger.info(f"重连配置: base_delay={self._reconnect_base_delay}s, "
                         f"max_delay={self._reconnect_max_delay}s, "
                         f"max_attempts={self._reconnect_max_attempts}, "
                         f"jitter={self._reconnect_jitter_factor}, "
                         f"stable_reset={self._reconnect_stable_reset_seconds}s")

    # ==================== 生命周期管理 ====================

    def start(self) -> bool:
        if not self.camera_enabled:
            self.logger.info("相机未启用")
            return True

        self.logger.info("正在启动 Orbbec 相机管理器...")

        if not self._init_camera():
            return False

        self._stop_capture = False
        self._capture_thread = threading.Thread(
            target=self._capture_loop,
            daemon=True,
            name="CameraCaptureThread"
        )
        self._capture_thread.start()

        warmup_frames = self.config.get('warmup_frames', 10)
        warmup_timeout = 5.0
        start_time = time.time()

        while self._stats['frames_captured'] < warmup_frames:
            if time.time() - start_time > warmup_timeout:
                self.logger.error(f"预热超时，已采集 {self._stats['frames_captured']} 帧")
                break
            time.sleep(0.1)

        self.logger.info(f"相机预热完成，已采集 {self._stats['frames_captured']} 帧")

        if self.stream_enabled:
            if not self.start_streaming():
                self.logger.warning("推流启动失败，但相机仍可用")

        self._stats['start_time'] = time.time()
        return True

    def stop(self):
        self.logger.info("正在停止 Orbbec 相机管理器...")

        self.stop_streaming()

        self._stop_capture = True
        if self._capture_thread and self._capture_thread.is_alive():
            self._capture_thread.join(timeout=3.0)
            if self._capture_thread.is_alive():
                self.logger.warning("帧采集线程未能正常停止")

        self._close_camera()

        self._set_state(CameraState.DISCONNECTED)
        self.logger.info("Orbbec 相机管理器已停止")

    def _init_camera(self) -> bool:
        if self.camera_type == 'mock':
            return self._init_mock_camera()
        elif self.camera_type == 'orbbec':
            return self._init_orbbec_camera()
        else:
            self.logger.error(f"不支持的相机类型: {self.camera_type}")
            return False

    def _init_orbbec_camera(self) -> bool:
        try:
            from pyorbbecsdk import Pipeline, Config, OBSensorType, OBFormat, OBStreamType, OBError

            self.logger.info("正在初始化 Orbbec 相机...")

            self._pipeline = Pipeline()
            self._orbbec_config = Config()

            try:
                profile_list = self._pipeline.get_stream_profile_list(OBSensorType.COLOR_SENSOR)

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
                    rgb_profile = profile_list.get_default_video_stream_profile()
                    actual_width = rgb_profile.get_width()
                    actual_height = rgb_profile.get_height()
                    self.logger.warning(f"未找到指定分辨率，使用默认: {actual_width}x{actual_height}")
                    self.resolution['width'] = actual_width
                    self.resolution['height'] = actual_height

                self._orbbec_config.enable_stream(rgb_profile)

            except Exception as e:
                self.logger.warning(f"无法获取 RGB 流配置: {e}，尝试默认配置")
                self._orbbec_config.enable_stream(OBStreamType.COLOR_STREAM)

            self._pipeline.start(self._orbbec_config)

            self._set_state(CameraState.CONNECTED)
            self.logger.info(f"Orbbec 相机初始化成功: {self.resolution['width']}x{self.resolution['height']}")
            return True

        except ImportError as e:
            self.logger.error(f"pyorbbecsdk 未安装: {e}")
            self._set_state(CameraState.ERROR)
            return False
        except Exception as e:
            self.logger.error(f"Orbbec 相机初始化失败: {e}")
            self._set_state(CameraState.ERROR)
            return False

    def _init_mock_camera(self) -> bool:
        self.logger.info("使用模拟相机")
        self._set_state(CameraState.CONNECTED)
        return True

    def _close_camera(self):
        try:
            if self._pipeline:
                self._pipeline.stop()
                self._pipeline = None
                self.logger.info("Orbbec 相机已关闭")
        except Exception as e:
            self.logger.error(f"关闭相机时发生错误: {e}")

    # ==================== 帧采集 ====================

    def _capture_loop(self):
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
                    with self._frame_lock:
                        self._frame_buffer = frame
                        self._frame_timestamp = time.time()

                    self._stats['frames_captured'] += 1
                    self._stats['last_capture_time'] = time.time()
                    frame_count += 1

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
        try:
            from pyorbbecsdk import OBFormat

            frames = self._pipeline.wait_for_frames(100)
            if frames is None:
                return None

            color_frame = frames.get_color_frame()
            if color_frame is None:
                return None

            color_data = color_frame.get_data()
            width = color_frame.get_width()
            height = color_frame.get_height()
            format_type = color_frame.get_format()

            if format_type == OBFormat.RGB:
                frame = np.frombuffer(color_data, dtype=np.uint8)
                frame = frame.reshape((height, width, 3))
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
            self.logger.error(f"Orbbec 帧采集错误: {e}")
            return None

    def _capture_mock_frame(self) -> np.ndarray:
        width = self.resolution['width']
        height = self.resolution['height']

        frame = np.zeros((height, width, 3), dtype=np.uint8)

        for i in range(height):
            frame[i, :, 0] = int(255 * i / height)
            frame[i, :, 1] = 50
            frame[i, :, 2] = int(255 * (1 - i / height))

        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        cv2.putText(frame, f"Mock Camera - {timestamp}",
                    (50, 50), cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)
        cv2.putText(frame, f"Frame: {self._stats['frames_captured']}",
                    (50, 100), cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)

        time.sleep(1.0 / self.fps)
        return frame

    # ==================== 帧获取（抓拍） ====================

    def capture_frame(self, timeout: float = 1.0) -> Optional[np.ndarray]:
        start_time = time.time()

        while time.time() - start_time < timeout:
            with self._frame_lock:
                if self._frame_buffer is not None:
                    return self._frame_buffer.copy()
            time.sleep(0.01)

        self.logger.warning("获取帧超时")
        return None

    def capture_to_base64(self, quality: int = None, wait_new_frame: bool = True) -> Optional[str]:
        if quality is None:
            quality = self.capture_quality

        was_streaming = self._is_streaming()
        if was_streaming and wait_new_frame:
            self._pause_streaming()

        try:
            if wait_new_frame:
                old_timestamp = self._frame_timestamp
                timeout = 0.5
                start = time.time()

                while time.time() - start < timeout:
                    if self._frame_timestamp > old_timestamp:
                        break
                    time.sleep(0.01)

            frame = self.capture_frame(timeout=1.0)
            if frame is None:
                self.logger.error("获取帧失败")
                return None

            encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), quality]
            success, buffer = cv2.imencode('.jpg', frame, encode_param)

            if not success:
                self.logger.error("JPEG 编码失败")
                return None

            return base64.b64encode(buffer).decode('utf-8')

        except Exception as e:
            self.logger.error(f"图像编码失败: {e}")
            return None

        finally:
            if was_streaming and wait_new_frame:
                self._resume_streaming()

    def capture_multiple(self, count: int = 2, interval: float = 0.5,
                         quality: int = None) -> List[str]:
        images = []

        for i in range(count):
            img_base64 = self.capture_to_base64(quality=quality, wait_new_frame=True)
            if img_base64:
                images.append(img_base64)
                self.logger.debug(f"第 {i + 1} 张图像采集成功")
            else:
                self.logger.warning(f"第 {i + 1} 张图像采集失败")

            if i < count - 1:
                time.sleep(interval)

        return images

    # ==================== RTMP 推流 ====================

    def start_streaming(self) -> bool:
        if not self.stream_enabled:
            self.logger.info("推流未启用")
            return True

        if self._is_streaming():
            self.logger.warning("推流已在运行")
            return True

        self.logger.info(f"正在启动 RTMP 推流: {self.rtmp_url}")

        if not self._start_ffmpeg():
            return False

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
        self.logger.info("RTMP 推流已启动")
        return True

    def stop_streaming(self):
        if not self._is_streaming():
            return

        self.logger.info("正在停止 RTMP 推流...")

        self._stop_stream = True

        if self._stream_thread and self._stream_thread.is_alive():
            self._stream_thread.join(timeout=3.0)

        self._stop_ffmpeg()

        if self._state == CameraState.STREAMING:
            self._set_state(CameraState.CONNECTED)

        self.logger.info("RTMP 推流已停止")

    def _start_ffmpeg(self) -> bool:
        try:
            width = self.resolution['width']
            height = self.resolution['height']
            fps = self.fps

            ffmpeg_cmd = [
                'ffmpeg',
                '-y',
                '-loglevel', 'warning',
                '-f', 'rawvideo',
                '-vcodec', 'rawvideo',
                '-pix_fmt', 'bgr24',
                '-s', f'{width}x{height}',
                '-r', str(fps),
                '-thread_queue_size', '512',
                '-i', '-',
                '-c:v', 'libx264',
                '-preset', self.stream_preset,
                '-tune', 'zerolatency',
                '-pix_fmt', 'yuv420p',
                '-g', str(fps * 2),
                '-b:v', self.stream_bitrate,
                '-maxrate', self.stream_maxrate,
                '-bufsize', self.stream_bufsize,
                '-f', 'flv',
                '-flvflags', 'no_duration_filesize',
                self.rtmp_url
            ]

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

            self._ffmpeg_healthy = False
            self._last_ffmpeg_error = ""

            self._ffmpeg_monitor_thread = threading.Thread(
                target=self._monitor_ffmpeg_output,
                daemon=True,
                name="FFmpegMonitorThread"
            )
            self._ffmpeg_monitor_thread.start()

            time.sleep(1.0)
            if self._ffmpeg_process.poll() is not None:
                exit_code = self._ffmpeg_process.returncode
                self.logger.error(f"FFmpeg 启动失败，退出码: {exit_code}, 最后错误: {self._last_ffmpeg_error}")
                return False

            self._ffmpeg_healthy = True
            self.logger.info(f"FFmpeg 进程已启动 (PID: {self._ffmpeg_process.pid})")
            return True

        except FileNotFoundError:
            self.logger.error("FFmpeg 未安装或不在 PATH 中")
            return False
        except Exception as e:
            self.logger.error(f"启动 FFmpeg 失败: {e}")
            return False

    def _monitor_ffmpeg_output(self):
        self.logger.debug("FFmpeg 输出监控线程已启动")

        try:
            while self._ffmpeg_process and self._ffmpeg_process.poll() is None:
                if self._ffmpeg_process.stderr:
                    line = self._ffmpeg_process.stderr.readline()
                    if line:
                        decoded_line = line.decode('utf-8', errors='ignore').strip()
                        if decoded_line:
                            self._last_ffmpeg_error = decoded_line
                            if 'error' in decoded_line.lower() or 'failed' in decoded_line.lower():
                                self.logger.error(f"FFmpeg: {decoded_line}")
                            elif 'warning' in decoded_line.lower():
                                self.logger.warning(f"FFmpeg: {decoded_line}")
                            else:
                                self.logger.debug(f"FFmpeg: {decoded_line}")
                else:
                    time.sleep(0.1)

            if self._ffmpeg_process:
                exit_code = self._ffmpeg_process.poll()
                if exit_code is not None and exit_code != 0:
                    remaining = self._ffmpeg_process.stderr.read() if self._ffmpeg_process.stderr else b''
                    if remaining:
                        remaining_str = remaining.decode('utf-8', errors='ignore').strip()
                        if remaining_str:
                            self._last_ffmpeg_error = remaining_str
                            self.logger.debug(f"FFmpeg 剩余输出: {remaining_str[:500]}")

                    self.logger.warning(f"FFmpeg 进程已退出，退出码: {exit_code}")
                    self._ffmpeg_healthy = False

        except Exception as e:
            self.logger.debug(f"FFmpeg 监控线程异常: {e}")

        self.logger.debug("FFmpeg 输出监控线程已退出")

    def _stop_ffmpeg(self):
        if self._ffmpeg_process:
            try:
                if self._ffmpeg_process.stdin:
                    try:
                        self._ffmpeg_process.stdin.close()
                    except Exception:
                        pass

                self._ffmpeg_process.terminate()

                try:
                    self._ffmpeg_process.wait(timeout=3.0)
                except subprocess.TimeoutExpired:
                    self.logger.warning("FFmpeg 未能正常终止，强制结束")
                    self._ffmpeg_process.kill()
                    self._ffmpeg_process.wait(timeout=1.0)

            except Exception as e:
                self.logger.warning(f"停止 FFmpeg 时发生错误: {e}")
                try:
                    self._ffmpeg_process.kill()
                except Exception:
                    pass
            finally:
                self._ffmpeg_healthy = False
                self._ffmpeg_process = None
                self.logger.info("FFmpeg 进程已停止")

        if self._ffmpeg_monitor_thread and self._ffmpeg_monitor_thread.is_alive():
            self._ffmpeg_monitor_thread.join(timeout=1.0)

    def _stream_loop(self):
        self.logger.info("推流线程已启动")

        frame_interval = 1.0 / self.fps
        last_frame_time = time.time()
        last_health_check = time.time()
        last_stability_check = time.time()
        health_check_interval = 5.0
        stability_check_interval = 10.0
        consecutive_write_errors = 0
        max_consecutive_errors = 3

        while not self._stop_stream:
            try:
                if self._pause_stream:
                    time.sleep(0.01)
                    continue

                current_time = time.time()

                if current_time - last_health_check >= health_check_interval:
                    if not self._check_ffmpeg_health():
                        self.logger.warning("FFmpeg 健康检查失败，触发重连")
                        self._reconnect_stats.record_disconnect("health_check_failed")

                        if not self._try_reconnect():
                            break
                        consecutive_write_errors = 0
                        last_health_check = time.time()
                        last_stability_check = time.time()
                        continue
                    last_health_check = current_time

                if current_time - last_stability_check >= stability_check_interval:
                    if self._reconnect_stats.should_reset_attempt_counter(self._reconnect_stable_reset_seconds):
                        self._reconnect_stats.reset_attempt_counter()
                        self.logger.info(
                            f"推流稳定运行超过 {self._reconnect_stable_reset_seconds}s，重置重试计数"
                        )
                    last_stability_check = current_time

                elapsed = current_time - last_frame_time
                if elapsed < frame_interval:
                    time.sleep(frame_interval - elapsed)

                with self._frame_lock:
                    if self._frame_buffer is None:
                        continue
                    frame = self._frame_buffer

                if self._ffmpeg_process and self._ffmpeg_process.poll() is None:
                    try:
                        self._ffmpeg_process.stdin.write(frame.tobytes())
                        self._ffmpeg_process.stdin.flush()
                        self._stats['frames_streamed'] += 1
                        consecutive_write_errors = 0
                    except (BrokenPipeError, OSError) as e:
                        consecutive_write_errors += 1
                        self.logger.error(
                            f"FFmpeg 写入错误 ({consecutive_write_errors}/{max_consecutive_errors}): {e}"
                        )

                        if consecutive_write_errors >= max_consecutive_errors:
                            self.logger.error("连续写入错误过多，触发重连")
                            self._reconnect_stats.record_disconnect(f"write_error: {e}")

                            if not self._try_reconnect():
                                break
                            consecutive_write_errors = 0
                            last_health_check = time.time()
                            last_stability_check = time.time()
                else:
                    exit_code = self._ffmpeg_process.poll() if self._ffmpeg_process else None
                    error_msg = f"process_exit: {exit_code}"
                    self.logger.error(
                        f"FFmpeg 进程已退出 (退出码: {exit_code}), 最后错误: {self._last_ffmpeg_error}"
                    )
                    self._reconnect_stats.record_disconnect(error_msg)

                    if not self._try_reconnect():
                        break
                    consecutive_write_errors = 0
                    last_health_check = time.time()
                    last_stability_check = time.time()

                last_frame_time = time.time()

            except Exception as e:
                self._stats['stream_errors'] += 1
                self.logger.error(f"推流异常: {e}")
                time.sleep(0.1)

        self.logger.info("推流线程已退出")

    def _check_ffmpeg_health(self) -> bool:
        if not self._ffmpeg_process:
            return False
        if self._ffmpeg_process.poll() is not None:
            return False
        if not self._ffmpeg_healthy:
            return False
        return True

    def _calculate_backoff_delay(self, attempt: int) -> float:
        exponential_delay = self._reconnect_base_delay * (2 ** (attempt - 1))
        capped_delay = min(exponential_delay, self._reconnect_max_delay)

        if self._reconnect_jitter_factor > 0:
            jitter = capped_delay * self._reconnect_jitter_factor * (2 * random.random() - 1)
            final_delay = max(0.1, capped_delay + jitter)
        else:
            final_delay = capped_delay

        return round(final_delay, 2)

    def _try_reconnect(self) -> bool:
        current_attempt = self._reconnect_stats.get_current_attempt()

        if current_attempt >= self._reconnect_max_attempts:
            self.logger.error(f"推流重连失败次数超过限制 ({self._reconnect_max_attempts})")
            self._set_state(CameraState.ERROR)
            return False

        self._reconnect_stats.record_reconnect_attempt()
        current_attempt = self._reconnect_stats.get_current_attempt()

        wait_time = self._calculate_backoff_delay(current_attempt)

        self.logger.warning(
            f"正在尝试重连 FFmpeg (第 {current_attempt}/{self._reconnect_max_attempts} 次)，"
            f"原因: {self._reconnect_stats.last_error or '未知'}，等待 {wait_time}s..."
        )

        self._stop_ffmpeg()

        wait_start = time.time()
        while time.time() - wait_start < wait_time:
            if self._stop_stream:
                self.logger.info("收到停止信号，取消重连")
                return False
            time.sleep(0.1)

        if self._start_ffmpeg():
            self._reconnect_stats.record_reconnect_success()
            self.logger.info(f"FFmpeg 重连成功 (第 {current_attempt} 次尝试)")
            return True
        else:
            self._reconnect_stats.record_reconnect_failure()
            self.logger.error(
                f"FFmpeg 重连失败 (第 {current_attempt} 次尝试)，原因: {self._last_ffmpeg_error}"
            )
            return False

    def _is_streaming(self) -> bool:
        return (self._stream_thread is not None and
                self._stream_thread.is_alive() and
                not self._stop_stream)

    def _pause_streaming(self):
        self._pause_stream = True

    def _resume_streaming(self):
        self._pause_stream = False

    # ==================== 状态管理 ====================

    def _set_state(self, state: CameraState):
        with self._state_lock:
            old_state = self._state
            self._state = state
            if old_state != state:
                self.logger.info(f"相机状态变更: {old_state.value} -> {state.value}")

    def get_state(self) -> CameraState:
        with self._state_lock:
            return self._state

    def is_streaming(self) -> bool:
        return self._is_streaming()

    def get_statistics(self) -> Dict[str, Any]:
        uptime = time.time() - self._stats['start_time'] if self._stats['start_time'] > 0 else 0

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
            'ffmpeg_pid': ffmpeg_pid,
            'ffmpeg_running': ffmpeg_running,
            'ffmpeg_healthy': self._ffmpeg_healthy,
            'ffmpeg_last_error': self._last_ffmpeg_error[:200] if self._last_ffmpeg_error else None,
            'reconnect_stats': self._reconnect_stats.to_dict()
        }

    # ==================== 深度图像（可选功能） ====================

    def capture_depth_frame(self, timeout: float = 1.0) -> Optional[np.ndarray]:
        """获取深度图像（当前仅支持 RGB，深度图像需额外配置）"""
        self.logger.warning("深度图像采集尚未实现")
        return None

    def __del__(self):
        try:
            self.stop()
        except Exception:
            pass
