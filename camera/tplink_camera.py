"""
tplink_camera.py
TP-LINK 豆干工业相机管理类

功能实现：
1. Token 鉴权管理（自动续期）
2. 抓图（HTTP Digest 鉴权，完整补光灯流程）
3. 补光灯开关控制
4. OCR 数字识别（LCD/LED 数显）
5. 指针表盘识别
6. 历史 OCR / 表盘数据查询与下载
7. 视频流参数获取与设置

流媒体说明：
  相机自带 RTSP 推流，管理服务器直接拉取，本类无需实现推流功能。
  默认流地址格式：rtsp://{username}:{password}@{ip}:554/stream1

抓图流程（每张独立执行）：
  开补光灯 → 等待曝光稳定 → HTTP Digest 抓图 → 关补光灯 → 返回 base64
"""

import base64
import json
import threading
import time
from hashlib import md5
from typing import Dict, Any, Optional, List

import requests

from camera.base_camera import BaseCameraManager, CameraState
from utils.logger_config import get_logger


class TPLinkCameraManager(BaseCameraManager):
    """TP-LINK 豆干 IPC 相机管理类"""

    # TP-LINK API 错误码
    _ERR_OK = 0
    _ERR_AUTH = -40401       # 认证失败 / Token 失效
    _ERR_PARAM = -40209      # 参数错误（如 OCR 功能未开启）

    def __init__(self, config: Dict[str, Any] = None):
        self.config = config or {}
        self.logger = get_logger("TPLinkCamera")

        self.camera_enabled = self.config.get('enabled', self.config.get('camera_enabled', True))

        # TPLINK 专用配置块
        tplink_cfg = self.config.get('tplink', {})
        self.ip = tplink_cfg.get('ip', '192.168.8.72')
        self.admin_user = tplink_cfg.get('username', 'admin')
        self.admin_password = tplink_cfg.get('password', '')
        # customer 账户密码与 admin 相同，用于 Token 鉴权
        self.customer_user = 'customer'
        self.customer_password = self.admin_password

        self.light_warmup_seconds = float(tplink_cfg.get('light_warmup_seconds', 0.5))
        self.request_timeout = int(tplink_cfg.get('request_timeout', 5))

        # Token（线程安全）
        self._token: Optional[str] = None
        self._token_lock = threading.Lock()

        # 相机状态
        self._state = CameraState.DISCONNECTED
        self._state_lock = threading.Lock()

        # 统计
        self._stats = {
            'captures_requested': 0,
            'captures_succeeded': 0,
            'capture_errors': 0,
            'token_refreshes': 0,
            'start_time': 0.0,
        }

        self.logger.info(f"TPLinkCameraManager 初始化完成 - IP: {self.ip}, "
                         f"补光灯预热: {self.light_warmup_seconds}s")

    # ==================== 生命周期 ====================

    def start(self) -> bool:
        """
        启动相机：登录获取 Token，确认相机可达

        Returns:
            bool: 启动是否成功
        """
        if not self.camera_enabled:
            self.logger.info("相机未启用")
            return True

        self.logger.info(f"正在连接 TP-LINK 相机: {self.ip}")

        try:
            token = self._do_login()
            if token is None:
                self._set_state(CameraState.ERROR)
                return False

            with self._token_lock:
                self._token = token

            self._set_state(CameraState.CONNECTED)
            self._stats['start_time'] = time.time()
            self.logger.info("TP-LINK 相机连接成功，Token 已获取")
            return True

        except Exception as e:
            self.logger.error(f"相机连接失败: {e}")
            self._set_state(CameraState.ERROR)
            return False

    def stop(self):
        """停止相机，清除 Token"""
        self.logger.info("正在停止 TP-LINK 相机...")
        with self._token_lock:
            self._token = None
        self._set_state(CameraState.DISCONNECTED)
        self.logger.info("TP-LINK 相机已停止")

    # ==================== Token 鉴权 ====================

    def _do_login(self) -> Optional[str]:
        """
        向相机发送登录请求，返回 Token 字符串

        使用 customer 账户（密码与 admin 相同）进行 Token 鉴权。
        Token 默认半永久有效，直到下一次 customer 登录才失效。
        """
        url = f"http://{self.ip}/"
        body = {
            "method": "do",
            "login": {
                "username": self.customer_user,
                "password": self.customer_password
            }
        }

        try:
            resp = requests.post(url, data=json.dumps(body), timeout=self.request_timeout)
            result = resp.json()

            if result.get("error_code") != self._ERR_OK:
                self.logger.error(f"登录失败，错误码: {result.get('error_code')}")
                return None

            token = result.get("stok")
            self.logger.debug(f"登录成功，Token: {token[:10] if token else 'None'}...")
            return token

        except requests.exceptions.ConnectionError:
            self.logger.error(f"无法连接到相机 {self.ip}，请检查网络和相机电源")
            return None
        except requests.exceptions.Timeout:
            self.logger.error(f"登录请求超时（{self.request_timeout}s）")
            return None
        except Exception as e:
            self.logger.error(f"登录异常: {e}")
            return None

    def _refresh_token(self) -> bool:
        """Token 已失效时自动重新登录"""
        self.logger.info("Token 已失效，正在重新登录...")
        token = self._do_login()
        if token:
            with self._token_lock:
                self._token = token
            self._stats['token_refreshes'] += 1
            self.logger.info("Token 刷新成功")
            return True
        self.logger.error("Token 刷新失败")
        return False

    def _post_api(self, body: dict, _retry: bool = True) -> Optional[dict]:
        """
        发送 Token 鉴权的 POST 请求

        自动处理 Token 过期（-40401）：捕获到错误后重新登录并重试一次。

        Args:
            body:   请求体 dict
            _retry: 内部重试标志，外部调用不需要传入

        Returns:
            响应 dict，或在请求/解析失败时返回 None
        """
        with self._token_lock:
            token = self._token

        if not token:
            if not self._refresh_token():
                return None
            with self._token_lock:
                token = self._token

        url = f"http://{self.ip}/stok={token}/ds"

        try:
            resp = requests.post(url, data=json.dumps(body), timeout=self.request_timeout)
            result = resp.json()

            # Token 失效 → 自动刷新后重试一次
            if result.get("error_code") == self._ERR_AUTH and _retry:
                self.logger.warning("Token 已失效，自动刷新后重试")
                if self._refresh_token():
                    return self._post_api(body, _retry=False)
                return None

            return result

        except requests.exceptions.Timeout:
            self.logger.error(f"API 请求超时（{self.request_timeout}s）")
            return None
        except Exception as e:
            self.logger.error(f"API 请求异常: {e}")
            return None

    # ==================== 补光灯控制 ====================

    def set_light(self, on: bool) -> bool:
        """
        控制补光灯开关

        Args:
            on: True 开灯，False 关灯

        Returns:
            bool: 操作是否成功
        """
        state = "on" if on else "off"
        label = "开启" if on else "关闭"

        body = {
            "method": "set",
            "image": {
                "common": {
                    "wtl_type": state
                }
            }
        }

        result = self._post_api(body)
        if result is None:
            self.logger.error(f"补光灯{label}失败：网络或鉴权错误")
            return False

        if result.get("error_code") != self._ERR_OK:
            self.logger.error(f"补光灯{label}失败，错误码: {result.get('error_code')}")
            return False

        self.logger.debug(f"补光灯已{label}")
        return True

    # ==================== 抓图 ====================

    def _snapshot_bytes(self) -> Optional[bytes]:
        """
        通过 HTTP Digest 鉴权抓取当前画面，返回 JPEG 原始字节

        鉴权流程：
          第一次 GET → 服务端返回 401 + WWW-Authenticate 头（realm, nonce）
          计算 response = MD5(MD5(user:realm:pass):nonce:MD5(GET:/snapshot.jpg))
          第二次 GET（带 Authorization 头）→ 服务端返回 JPEG 图片
        """
        snapshot_uri = "/snapshot.jpg"
        snapshot_url = f"http://{self.ip}{snapshot_uri}"

        try:
            session = requests.Session()

            # 第一次请求：获取鉴权挑战
            resp1 = session.get(snapshot_url, timeout=self.request_timeout)

            # 部分固件无需鉴权直接返回图片
            if resp1.status_code == 200 and len(resp1.content) > 0:
                return resp1.content

            if resp1.status_code != 401:
                self.logger.error(f"抓图请求异常，状态码: {resp1.status_code}")
                return None

            # 解析 WWW-Authenticate 头，格式：
            # Digest realm="TP-LINK IP-Camera", nonce="xxxx"
            auth_header = resp1.headers.get("WWW-Authenticate", "")
            parts = auth_header.split('"')
            if len(parts) < 4:
                self.logger.error(f"无法解析鉴权信息: {auth_header}")
                return None

            realm = parts[1]
            nonce = parts[3]

            # 计算 Digest response
            ha1 = md5(f"{self.admin_user}:{realm}:{self.admin_password}".encode()).hexdigest()
            ha2 = md5(f"GET:{snapshot_uri}".encode()).hexdigest()
            response_hash = md5(f"{ha1}:{nonce}:{ha2}".encode()).hexdigest()

            authorization = (
                f'Digest username="{self.admin_user}",'
                f'realm="{realm}",'
                f'nonce="{nonce}",'
                f'uri="{snapshot_uri}",'
                f'response="{response_hash}"'
            )

            # 第二次请求：带鉴权信息
            session.headers["Authorization"] = authorization
            resp2 = session.get(snapshot_url, timeout=10)

            if resp2.status_code != 200:
                self.logger.error(f"抓图鉴权后仍失败，状态码: {resp2.status_code}")
                return None

            # JPEG 文件通常远大于 1KB，太小说明可能是错误响应
            if len(resp2.content) < 1000:
                self.logger.error(
                    f"返回数据异常（仅 {len(resp2.content)} 字节），可能不是图片"
                )
                return None

            return resp2.content

        except requests.exceptions.Timeout:
            self.logger.error("抓图请求超时")
            return None
        except Exception as e:
            self.logger.error(f"抓图异常: {e}")
            return None

    def capture_single(self) -> Optional[str]:
        """
        单次完整抓拍：开补光灯 → 等待曝光稳定 → 抓图 → 关补光灯

        Returns:
            Base64 编码的图像字符串，失败返回 None
        """
        self._stats['captures_requested'] += 1

        # 开补光灯（失败不阻断抓图，仅警告）
        if not self.set_light(True):
            self.logger.warning("补光灯开启失败，仍继续抓图")

        try:
            # 等待曝光稳定
            time.sleep(self.light_warmup_seconds)

            img_bytes = self._snapshot_bytes()
            if img_bytes is None:
                self._stats['capture_errors'] += 1
                return None

            img_base64 = base64.b64encode(img_bytes).decode('utf-8')
            self._stats['captures_succeeded'] += 1
            self.logger.debug(f"抓图成功，大小: {len(img_bytes) / 1024:.1f} KB")
            return img_base64

        finally:
            # 确保补光灯始终关闭
            if not self.set_light(False):
                self.logger.warning("补光灯关闭失败，请手动检查")

    def capture_multiple(self, count: int = 2, interval: float = 0.5,
                         quality: int = None) -> List[str]:
        """
        连续抓拍多张图像

        每张图独立走完整的补光灯流程（开灯→等待→抓图→关灯），保证图像质量。

        Args:
            count:    拍摄张数
            interval: 两次拍摄间隔（秒）
            quality:  图像质量（TPLINK 抓图质量由相机主码流设置决定，此参数保留以兼容接口）

        Returns:
            Base64 编码的图像字符串列表
        """
        if quality is not None:
            self.logger.debug("quality 参数在 TPLINK 相机中不生效，图像质量由相机主码流设置决定")

        images = []

        for i in range(count):
            img_base64 = self.capture_single()
            if img_base64:
                images.append(img_base64)
                self.logger.debug(f"第 {i + 1}/{count} 张抓图成功")
            else:
                self.logger.warning(f"第 {i + 1}/{count} 张抓图失败")

            if i < count - 1:
                time.sleep(interval)

        return images

    # ==================== OCR 数字识别 ====================

    def get_ocr_result(self, region_id: str = "1", channel: str = "1") -> Optional[str]:
        """
        获取 OCR 数字识别结果（LCD / LED 数显）

        需在相机 Web 界面提前开启 OCR 功能，否则返回错误码 -40209。

        Args:
            region_id: 识别区域编号，从 "1" 开始
            channel:   通道号，单通道填 "1"，多通道机型从 "1" 开始

        Returns:
            识别结果字符串（如 "6"），未识别到时返回空字符串 ""，失败返回 None
        """
        body = {
            "method": "do",
            "ocr": {
                "get_ocr_result": {
                    "id": region_id,
                    "channel": channel
                }
            }
        }

        result = self._post_api(body)
        if result is None:
            return None

        err = result.get("error_code")
        if err == self._ERR_PARAM:
            self.logger.error("OCR 功能未开启，请在相机 Web 界面（智能分析 → OCR）中启用")
            return None
        if err != self._ERR_OK:
            self.logger.error(f"OCR 识别失败，错误码: {err}")
            return None

        value = result.get("ocr_result", "")
        self.logger.debug(f"OCR 识别结果: '{value}'，时间戳: {result.get('timestamp', '')}")
        return value

    def get_ocr_result_with_timestamp(self, region_id: str = "1",
                                       channel: str = "1") -> Optional[Dict[str, str]]:
        """
        获取 OCR 数字识别结果（含时间戳）

        Returns:
            {"value": "6", "timestamp": "1595837240"}，失败返回 None
        """
        body = {
            "method": "do",
            "ocr": {
                "get_ocr_result": {
                    "id": region_id,
                    "channel": channel
                }
            }
        }

        result = self._post_api(body)
        if result is None:
            return None

        err = result.get("error_code")
        if err == self._ERR_PARAM:
            self.logger.error("OCR 功能未开启，请在相机 Web 界面中启用")
            return None
        if err != self._ERR_OK:
            self.logger.error(f"OCR 识别失败，错误码: {err}")
            return None

        return {
            "value": result.get("ocr_result", ""),
            "timestamp": result.get("timestamp", "")
        }

    # ==================== 指针表盘识别 ====================

    def get_dial_reading(self, region_id: str = "1", channel: str = "1") -> Optional[str]:
        """
        获取指针表盘识别结果

        需在相机 Web 界面提前开启指针表盘识别功能。

        Args:
            region_id: 识别区域编号，从 "1" 开始
            channel:   通道号，单通道填 "1"

        Returns:
            识别结果字符串（如 "10.95"），未识别到时返回空字符串 ""，失败返回 None
        """
        body = {
            "method": "do",
            "dpd": {
                "get_detection_result": {
                    "id": region_id,
                    "channel": channel
                }
            }
        }

        result = self._post_api(body)
        if result is None:
            return None

        err = result.get("error_code")
        if err == self._ERR_PARAM:
            self.logger.error("指针表盘识别功能未开启，请在相机 Web 界面中启用")
            return None
        if err != self._ERR_OK:
            self.logger.error(f"表盘识别失败，错误码: {err}")
            return None

        value = result.get("result", "")
        self.logger.debug(f"表盘识别结果: '{value}'，时间戳: {result.get('timestamp', '')}")
        return value

    def get_dial_reading_with_timestamp(self, region_id: str = "1",
                                         channel: str = "1") -> Optional[Dict[str, str]]:
        """
        获取指针表盘识别结果（含时间戳）

        Returns:
            {"value": "10.95", "timestamp": "1754638493"}，失败返回 None
        """
        body = {
            "method": "do",
            "dpd": {
                "get_detection_result": {
                    "id": region_id,
                    "channel": channel
                }
            }
        }

        result = self._post_api(body)
        if result is None:
            return None

        err = result.get("error_code")
        if err == self._ERR_PARAM:
            self.logger.error("指针表盘识别功能未开启，请在相机 Web 界面中启用")
            return None
        if err != self._ERR_OK:
            self.logger.error(f"表盘识别失败，错误码: {err}")
            return None

        return {
            "value": result.get("result", ""),
            "timestamp": result.get("timestamp", "")
        }

    # ==================== 历史数据查询与下载 ====================

    def get_ocr_history_urls(self) -> Optional[List[str]]:
        """
        获取 OCR 数字识别历史数据文件 URL 列表

        相机自动保存近 15 天数据，每天一个 CSV 文件（需插入 SD 卡）。

        Returns:
            URL 路径列表（可传入 download_history_file），失败返回 None
        """
        body = {
            "method": "do",
            "ocr": {
                "get_history_file_url": None
            }
        }

        result = self._post_api(body)
        if result is None:
            return None

        if result.get("error_code") != self._ERR_OK:
            self.logger.error(f"获取 OCR 历史文件列表失败，错误码: {result.get('error_code')}")
            return None

        urls = result.get("url", [])
        self.logger.debug(f"获取到 {len(urls)} 条 OCR 历史文件")
        return urls

    def get_dial_history_urls(self) -> Optional[List[str]]:
        """
        获取指针表盘识别历史数据文件 URL 列表

        Returns:
            URL 路径列表（可传入 download_history_file），失败返回 None
        """
        body = {
            "method": "do",
            "dpd": {
                "get_history_file_url": None
            }
        }

        result = self._post_api(body)
        if result is None:
            return None

        if result.get("error_code") != self._ERR_OK:
            self.logger.error(f"获取表盘历史文件列表失败，错误码: {result.get('error_code')}")
            return None

        urls = result.get("url", [])
        self.logger.debug(f"获取到 {len(urls)} 条表盘历史文件")
        return urls

    def download_history_file(self, file_url: str, _retry: bool = True) -> Optional[bytes]:
        """
        下载历史识别数据文件（CSV 格式，原始字节）

        Args:
            file_url: 由 get_ocr_history_urls / get_dial_history_urls 返回的 URL 路径，
                      例如 "/admin/ocr/get_history_file_url?filename=/tmp/mnt/..."

        Returns:
            CSV 文件原始内容（bytes），失败返回 None
        """
        with self._token_lock:
            token = self._token

        if not token:
            if not self._refresh_token():
                return None
            with self._token_lock:
                token = self._token

        url = f"http://{self.ip}/stok={token}{file_url}"

        try:
            resp = requests.get(url, timeout=30)

            if resp.status_code == 200:
                self.logger.debug(f"下载历史文件成功，大小: {len(resp.content)} 字节")
                return resp.content

            # Token 过期时服务器返回 401，自动刷新后重试一次
            if resp.status_code == 401 and _retry:
                self.logger.warning("下载历史文件收到 401，Token 已失效，自动刷新后重试")
                if self._refresh_token():
                    return self.download_history_file(file_url, _retry=False)
                return None

            self.logger.error(f"下载历史文件失败，状态码: {resp.status_code}")
            return None

        except requests.exceptions.Timeout:
            self.logger.error("下载历史文件超时（30s）")
            return None
        except Exception as e:
            self.logger.error(f"下载历史文件异常: {e}")
            return None

    def download_history_csv(self, file_url: str) -> Optional[str]:
        """
        下载历史数据文件并以字符串返回（UTF-8 解码）

        Returns:
            CSV 文本内容，失败返回 None
        """
        content = self.download_history_file(file_url)
        if content is None:
            return None
        return content.decode('utf-8', errors='replace')

    # ==================== 视频流参数 ====================

    def get_video_capability(self, stream: str = "main") -> Optional[Dict[str, Any]]:
        """
        获取视频流支持的参数范围

        Args:
            stream: "main"（主码流）或 "minor"（子码流）

        Returns:
            参数字典，包含 encode_types / frame_rates / bitrates / resolutions 等，失败返回 None

        帧率编码规则：
            frame_rate 为整数，高 16 位为 high，低 16 位为 low，实际帧率 = low / high
            示例：65561 (0x10019) → high=1, low=25 → 25fps
        """
        if stream not in ("main", "minor"):
            self.logger.error(f"无效的码流类型: '{stream}'，应为 'main' 或 'minor'")
            return None

        body = {
            "method": "get",
            "video_capability": {
                "name": stream
            }
        }

        result = self._post_api(body)
        if result is None:
            return None

        if result.get("error_code") != self._ERR_OK:
            self.logger.error(f"获取视频参数范围失败，错误码: {result.get('error_code')}")
            return None

        capability = result.get("video_capability", {}).get(stream)
        if not capability:
            self.logger.error(f"响应中未包含 {stream} 码流参数，原始响应: {result}")
            return None

        self.logger.debug(f"{stream} 码流参数范围: {capability}")
        return capability

    def set_video_params(self, stream: str = "main", encode_type: str = None,
                         frame_rate: str = None, bitrate: str = None,
                         bitrate_type: str = None, resolution: str = None) -> bool:
        """
        设置视频流参数

        各参数可取值范围由 get_video_capability() 返回值决定。

        Args:
            stream:       "main"（主码流）或 "minor"（子码流）
            encode_type:  编码格式，如 "H264"、"H265"
            frame_rate:   帧率编码值字符串，如 "65561"（25fps）
            bitrate:      码率（kbps），如 "4096"
            bitrate_type: 码率控制模式，"cbr"（固定码率）或 "vbr"（可变码率）
            resolution:   分辨率字符串，如 "1920*1080"

        Returns:
            bool: 设置是否成功

        帧率编码对照：
            "65537" = 1fps | "65546" = 10fps | "65551" = 15fps
            "65556" = 20fps | "65561" = 25fps
        """
        if stream not in ("main", "minor"):
            self.logger.error(f"无效的码流类型: '{stream}'")
            return False

        params = {}
        if encode_type is not None:
            params["encode_type"] = encode_type
        if frame_rate is not None:
            params["frame_rate"] = frame_rate
        if bitrate is not None:
            params["bitrate"] = bitrate
        if bitrate_type is not None:
            params["bitrate_type"] = bitrate_type
        if resolution is not None:
            params["resolution"] = resolution

        if not params:
            self.logger.warning("set_video_params 未传入任何参数，操作跳过")
            return True

        body = {
            "method": "set",
            "video": {
                stream: params
            }
        }

        result = self._post_api(body)
        if result is None:
            return False

        if result.get("error_code") != self._ERR_OK:
            self.logger.error(f"设置视频参数失败，错误码: {result.get('error_code')}")
            return False

        self.logger.info(f"视频参数设置成功: stream={stream}, params={params}")
        return True

    @staticmethod
    def decode_frame_rate(frame_rate_value: int) -> float:
        """
        将 TP-LINK 帧率编码值解码为实际帧率（fps）

        编码规则：高 16 位为 high，低 16 位为 low，实际帧率 = low / high

        Args:
            frame_rate_value: 如 65561

        Returns:
            实际帧率，如 25.0
        """
        high = (frame_rate_value >> 16) & 0xFFFF
        low = frame_rate_value & 0xFFFF
        if high == 0:
            return 0.0
        return round(low / high, 2)

    @staticmethod
    def encode_frame_rate(fps: int) -> int:
        """
        将实际帧率编码为 TP-LINK 帧率整数

        Args:
            fps: 实际帧率（整数），如 25

        Returns:
            编码值，如 65561
        """
        return (1 << 16) | (fps & 0xFFFF)

    # ==================== 状态管理 ====================

    def _set_state(self, state: CameraState):
        with self._state_lock:
            old = self._state
            self._state = state
            if old != state:
                self.logger.info(f"相机状态变更: {old.value} -> {state.value}")

    def get_state(self) -> CameraState:
        with self._state_lock:
            return self._state

    def is_streaming(self) -> bool:
        """TP-LINK 使用相机自带 RTSP，无需主动推流，始终返回 False"""
        return False

    def get_statistics(self) -> Dict[str, Any]:
        uptime = time.time() - self._stats['start_time'] if self._stats['start_time'] > 0 else 0
        succeeded = self._stats['captures_succeeded']
        requested = self._stats['captures_requested']

        return {
            'state': self.get_state().value,
            'camera_type': 'tplink',
            'ip': self.ip,
            'token_valid': self._token is not None,
            'captures_requested': requested,
            'captures_succeeded': succeeded,
            'capture_errors': self._stats['capture_errors'],
            'capture_success_rate': round(succeeded / requested * 100, 2) if requested > 0 else 100.0,
            'token_refreshes': self._stats['token_refreshes'],
            'is_streaming': False,
            'rtsp_note': '相机自带 RTSP，由管理服务器直接拉流',
            'uptime_seconds': round(uptime, 2),
        }
