"""
语音播报模块

提供异步音频播放功能，播放在独立线程中执行，不阻塞调用线程。
使用项目已有的ffplay进行音频解码播放，无需额外依赖。
"""

import os
import sys
import subprocess
import threading

from utils.logger_config import get_logger


class VoicePlayer:
    """语音播报器 - 异步播放音频文件，不阻塞调用线程

    播放通过独立daemon线程发起ffplay进程实现，调用play()后立即返回，
    不影响后续指令执行。同一时刻仅保持一个播放进程，新播放会自动停止旧播放。
    """

    def __init__(self, audio_dir=None, ffplay_path=None):
        """
        初始化语音播报器

        :param audio_dir: 音频文件目录，默认为 utils/src
        :param ffplay_path: ffplay可执行文件路径，默认自动查找bundled版本
        """
        self.logger = get_logger(__name__)

        self.audio_dir = audio_dir or self._get_default_audio_dir()
        self.ffplay_path = ffplay_path or self._find_ffplay()

        # 播放进程和锁
        self._process = None
        self._lock = threading.Lock()

        self.logger.info(f"语音播报器初始化 - 音频目录: {self.audio_dir}, ffplay: {self.ffplay_path}")

    # ==================== 路径解析 ====================

    def _get_default_audio_dir(self):
        """获取默认音频目录 (utils/src)"""
        utils_dir = os.path.dirname(os.path.abspath(__file__))
        return os.path.join(utils_dir, "src")

    def _find_ffplay(self):
        """查找ffplay可执行文件，优先使用项目bundled版本"""
        ffplay_name = "ffplay.exe" if sys.platform == "win32" else "ffplay"
        utils_dir = os.path.dirname(os.path.abspath(__file__))

        # 扫描utils下的ffmpeg目录（版本号可能变化）
        try:
            for name in os.listdir(utils_dir):
                if name.startswith("ffmpeg-"):
                    candidate = os.path.join(utils_dir, name, "bin", ffplay_name)
                    if os.path.isfile(candidate):
                        self.logger.debug(f"找到bundled ffplay: {candidate}")
                        return candidate
        except OSError as e:
            self.logger.warning(f"扫描ffplay路径异常: {e}")

        # 回退到系统PATH中的ffplay
        self.logger.warning("未找到bundled ffplay，将使用系统PATH中的版本")
        return ffplay_name

    # ==================== 播放控制 ====================

    def play(self, filename):
        """
        异步播放音频文件，不阻塞调用线程

        :param filename: 音频文件名（相对于audio_dir）或完整路径
        """
        # 解析完整路径
        if os.path.isabs(filename):
            audio_path = filename
        else:
            audio_path = os.path.join(self.audio_dir, filename)

        if not os.path.isfile(audio_path):
            self.logger.error(f"音频文件不存在: {audio_path}")
            return

        # 停止当前播放（如果有的话）
        self.stop()

        # 在daemon线程中启动播放，不阻塞调用线程
        thread = threading.Thread(
            target=self._play_file,
            args=(audio_path,),
            daemon=True,
            name="VoicePlayerThread"
        )
        thread.start()
        self.logger.info(f"开始播放音频: {filename}")

    def _play_file(self, audio_path):
        """在独立线程中执行ffplay播放"""
        # Windows平台使用CREATE_NO_WINDOW避免弹出控制台窗口（与CameraManager一致）
        creation_flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

        try:
            with self._lock:
                self._process = subprocess.Popen(
                    [self.ffplay_path, "-autoexit", "-nodisp", audio_path],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=creation_flags
                )

            # 等待播放结束（线程会在此阻塞，不影响主线程）
            self._process.wait()
            self.logger.debug(f"音频播放结束: {os.path.basename(audio_path)}")

        except FileNotFoundError:
            self.logger.error(f"ffplay未找到，请检查路径: {self.ffplay_path}")
        except Exception as e:
            self.logger.error(f"音频播放异常: {e}")
        finally:
            with self._lock:
                self._process = None

    def stop(self):
        """停止当前正在播放的音频"""
        with self._lock:
            if self._process and self._process.poll() is None:
                try:
                    self._process.terminate()
                    self._process.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    self._process.kill()
                except Exception as e:
                    self.logger.warning(f"停止播放时异常: {e}")
                self._process = None
                self.logger.debug("音频播放已停止")

    def is_playing(self):
        """检查是否正在播放"""
        with self._lock:
            if self._process and self._process.poll() is not None:
                # 进程已退出，清理引用
                self._process = None
            return self._process is not None
