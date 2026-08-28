"""机械臂、外部轴和头部关节的临时 Mock 控制器。"""

import threading
from typing import Any, Dict, Iterable, List, Optional

from utils.logger_config import get_logger
from utils.voice_player import VoicePlayer


class MockArmController:
    """模拟 ``ArmController`` 的公开接口，不访问任何运动硬件。"""

    ARM_JOINT_COUNT = 6
    EXT_AXIS_COUNT = 4

    def __init__(
        self,
        system_config: Optional[Dict[str, Any]] = None,
        ext_axis_limits: Optional[Dict[str, Any]] = None,
        debug: bool = False,
    ):
        self.system_config = system_config or {}
        self.ext_axis_limits = ext_axis_limits
        self.debug = debug
        self.logger = get_logger(__name__)
        self.voice_player = VoicePlayer()

        self._lock = threading.RLock()
        self._initialized = False
        self._ext_enabled = False
        self._arm_joints = [0.0] * self.ARM_JOINT_COUNT
        self._ext_axes = [0.0] * self.EXT_AXIS_COUNT

        self.logger.warning(
            "当前使用 MockArmController：机械臂、外部轴和头部关节不会执行真实动作"
        )

    @staticmethod
    def _validate_position(
        position: Iterable[float], expected_count: int, device_name: str
    ) -> List[float]:
        try:
            values = list(position)
        except TypeError as exc:
            raise ValueError(f"{device_name}位置必须是可迭代对象") from exc

        if len(values) != expected_count:
            raise ValueError(
                f"{device_name}位置需要{expected_count}个数值，实际收到{len(values)}个"
            )

        try:
            return [float(value) for value in values]
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{device_name}位置包含非数值内容: {values}") from exc

    def setup_system(self) -> bool:
        """模拟完成硬件初始化。"""
        with self._lock:
            self._initialized = True
            self._ext_enabled = True
        self.logger.info("Mock运动硬件初始化完成")
        return True

    def shutdown_system(self) -> None:
        """模拟关闭硬件，不清除缓存的虚拟位置。"""
        with self._lock:
            self._ext_enabled = False
            self._initialized = False
        self.logger.info("Mock运动硬件已关闭")

    def arm_get_state(self) -> List[float]:
        """返回六个机械臂关节的虚拟位置。"""
        with self._lock:
            return self._arm_joints.copy()

    def rob_moveto(
        self,
        jpos: Iterable[float],
        vel: Optional[float] = None,
        cancel_event=None,
    ) -> int:
        """记录机械臂目标位置并模拟执行成功。"""
        target = self._validate_position(jpos, self.ARM_JOINT_COUNT, "机械臂")
        with self._lock:
            self._arm_joints = target
        self.logger.info(f"[MOCK] 机械臂移动到 {target}，速度: {vel}")
        return 0

    def ext_check_connection(self) -> bool:
        """模拟外部轴连接正常。"""
        return True

    def ext_enable(self, enable: bool = True) -> bool:
        """记录外部轴的虚拟使能状态。"""
        with self._lock:
            self._ext_enabled = bool(enable)
        self.logger.info(f"[MOCK] 外部轴{'使能' if enable else '禁用'}")
        return True

    def ext_reset(self) -> bool:
        """将四个外部轴的虚拟位置复位为零。"""
        with self._lock:
            self._ext_axes = [0.0] * self.EXT_AXIS_COUNT
            self._ext_enabled = True
        self.logger.info("[MOCK] 外部轴复位")
        return True

    def ext_get_state(self) -> List[Dict[str, Any]]:
        """按照真实控制器格式返回四个外部轴的虚拟状态。"""
        with self._lock:
            positions = self._ext_axes.copy()
            enabled = self._ext_enabled
        return [
            {"id": index + 1, "pos": position, "enable": enabled}
            for index, position in enumerate(positions)
        ]

    def ext_moveto(
        self,
        point: Iterable[float],
        vel: Optional[float] = None,
        acc: Optional[float] = None,
        cancel_event=None,
    ) -> bool:
        """记录外部轴目标位置并模拟执行成功。"""
        target = self._validate_position(point, self.EXT_AXIS_COUNT, "外部轴")
        with self._lock:
            self._ext_axes = target
        self.logger.info(f"[MOCK] 外部轴移动到 {target}，速度: {vel}，加速度: {acc}")
        return True

    def move_head(self) -> bool:
        """模拟头部摆动，不改变外部轴的缓存位置。"""
        self.logger.info("[MOCK] 执行头部摆动")
        return True

    def welcome(self) -> bool:
        """保留欢迎语音，只跳过机械臂欢迎动作。"""
        self.voice_player.play("巡检开始前.mp3")
        self.logger.info("[MOCK] 跳过机械臂欢迎动作")
        return True

    def playvideo_after_inspection(self) -> bool:
        """保留巡检结束语音。"""
        self.voice_player.play("巡检结束后.mp3")
        return True
