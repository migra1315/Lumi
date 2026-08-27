"""环境传感器的临时 Mock 实现。"""

import threading
from typing import Dict

from utils.logger_config import get_logger


class MockAirQualitySensor:
    """模拟 ``AirQualitySensor`` 接口，不访问串口硬件。"""

    DEFAULT_DATA = {
        "pm25": 12.5,
        "pm10": 25.0,
        "temperature": 22.5,
        "humidity": 45.0,
        "tvoc": 0.2,
        "co2": 400.0,
        "oxygen": 20.9,
        "noise": 45.0,
    }

    def __init__(
        self,
        port: str = "MOCK",
        baudrate: int = 4800,
        address: int = 0x01,
        timeout: float = 1,
    ):
        self.port = port
        self.baudrate = baudrate
        self.address = address
        self.timeout = timeout
        self.logger = get_logger(__name__)
        self._lock = threading.Lock()
        self._connected = False
        self._data = self.DEFAULT_DATA.copy()

        self.logger.warning("当前使用 MockAirQualitySensor：不会访问真实环境传感器")

    def connect(self) -> bool:
        """模拟连接成功。"""
        with self._lock:
            self._connected = True
        self.logger.info("Mock环境传感器连接成功")
        return True

    def disconnect(self) -> None:
        """模拟断开连接。"""
        with self._lock:
            self._connected = False
        self.logger.info("Mock环境传感器已断开")

    def read_all_parameters(self) -> Dict[str, float]:
        """返回与真实传感器一致的环境数据字段。"""
        with self._lock:
            if not self._connected:
                self.logger.warning("Mock环境传感器尚未连接")
                return {key: None for key in self.DEFAULT_DATA}
            return self._data.copy()
