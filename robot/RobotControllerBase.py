"""
RobotControllerBase.py
机器人控制器基类，定义统一接口
"""

from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional, Callable
from enum import Enum
import logging

class RobotStatus(Enum):
    IDLE = "idle"
    MOVING = "moving"
    ARM_OPERATING = "arm_operating"
    EXT_OPERATING = "ext_operating"
    DOOR_OPERATING = "door_operating"
    ERROR = "error"
    CHARGING = "charging"
    SETUP = "setup"

class BatteryStatus(Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    CRITICAL = "critical"

class RobotControllerBase(ABC):
    """机器人控制器基类，定义所有机器人控制器必须实现的接口"""
    
    def __init__(self, config: Dict[str, Any] = None, debug: bool = False):
        """
        初始化基类
        
        Args:
            config: 配置字典
            debug: 调试模式
        """
        self.config = config or {}
        self.debug = debug
        
        # 通用状态（所有实现类都应该有的状态）
        self.status = RobotStatus.IDLE
        self.battery_level = 100.0
        self.battery_status = BatteryStatus.HIGH
        self.current_marker = None
        self.last_error = None
        
        # 初始化日志
        self.logger = self._setup_logger(debug)
        
        # 回调函数
        self.callbacks = {
            "on_status_change": [],
            "on_battery_change": [],
            "on_error": [],
            "on_task_start": [],
            "on_task_complete": []
        }
        
        # 系统初始化标志
        self._system_initialized = False
    
    def _setup_logger(self, debug: bool) -> logging.Logger:
        """设置日志记录器（子类可以重写）"""
        logger = logging.getLogger(self.__class__.__name__)
        
        if debug:
            logger.setLevel(logging.DEBUG)
        else:
            logger.setLevel(logging.INFO)
        
        if not logger.handlers:
            console_handler = logging.StreamHandler()
            formatter = logging.Formatter(
                '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
            )
            console_handler.setFormatter(formatter)
            logger.addHandler(console_handler)
        
        return logger
    
    # ==================== 抽象方法 ====================
    @abstractmethod
    def setup_system(self) -> bool:
        """初始化系统（必须实现）"""
        pass
    
    @abstractmethod
    def shutdown_system(self):
        """关闭系统（必须实现）"""
        pass
    
    @abstractmethod
    def move_to_marker(self, marker_id: str) -> bool:
        """移动AGV到标记点（必须实现）"""
        pass
    
    @abstractmethod
    def move_robot_to_position(self, position: List[float]) -> bool:
        """移动机械臂到指定位置（必须实现）"""
        pass
    
    @abstractmethod
    def move_ext_to_position(self, position: List[float]) -> bool:
        """移动外部轴到指定位置（必须实现）"""
        pass
    
    # ==================== 可选方法（有默认实现） ====================
    def get_status(self) -> Dict[str, Any]:
        """获取机器人状态（有默认实现，子类可重写）"""
        return {
            "status": self.status.value,
            "battery_level": self.battery_level,
            "battery_status": self.battery_status.value,
            "current_marker": self.current_marker,
            "last_error": self.last_error,
            "system_initialized": self._system_initialized,
            "controller_type": self.__class__.__name__
        }
    
    def emergency_stop(self) -> bool:
        """紧急停止（有默认实现，子类可重写）"""
        self.logger.warning("紧急停止（基类实现）")
        self.status = RobotStatus.IDLE
        return True
    
    def reset_errors(self) -> bool:
        """重置错误（有默认实现，子类可重写）"""
        self.logger.info("重置错误状态（基类实现）")
        self.status = RobotStatus.IDLE
        self.last_error = None
        return True
    
    def open_door(self, door_id: str) -> bool:
        """开门操作（有默认实现，子类可重写）"""
        self.logger.info(f"开门操作: {door_id}（基类模拟实现）")
        self.status = RobotStatus.DOOR_OPERATING
        
        # 模拟操作
        import time
        time.sleep(0.5)
        
        self.status = RobotStatus.IDLE
        return True
    
    def close_door(self, door_id: str) -> bool:
        """关门操作（有默认实现，子类可重写）"""
        self.logger.info(f"关门操作: {door_id}（基类模拟实现）")
        self.status = RobotStatus.DOOR_OPERATING
        
        # 模拟操作
        import time
        time.sleep(0.5)
        
        self.status = RobotStatus.IDLE
        return True
    
    def charge(self, duration: float = 10.0) -> bool:
        """充电操作（有默认实现，子类可重写）"""
        self.logger.info(f"开始充电: {duration}秒（基类模拟实现）")
        self.status = RobotStatus.CHARGING
        
        # 模拟充电
        import time
        start_time = time.time()
        while time.time() - start_time < min(duration, 5.0):
            self.battery_level = min(100.0, self.battery_level + 20.0)
            time.sleep(0.5)
        
        self.status = RobotStatus.IDLE
        return True
    
    # ==================== 公共方法（不需要重写） ====================
    def register_callback(self, event: str, callback: Callable):
        """注册回调函数"""
        if event in self.callbacks:
            self.callbacks[event].append(callback)
            self.logger.debug(f"已注册回调函数到事件: {event}")
        else:
            self.logger.warning(f"未知事件类型: {event}")
    
    def _trigger_callback(self, event: str, *args, **kwargs):
        """触发回调函数"""
        for callback in self.callbacks.get(event, []):
            try:
                callback(*args, **kwargs)
            except Exception as e:
                self.logger.error(f"回调函数执行异常: {e}")
    
    def __del__(self):
        """析构函数"""
        try:
            self.shutdown_system()
        except Exception:
            pass
    
    # ==================== 属性访问器 ====================
    @property
    def agv_controller(self):
        """AGV控制器属性（子类应该实现）"""
        return getattr(self, '_agv_controller', None)
    
    @property
    def jaka_controller(self):
        """机械臂控制器属性（子类应该实现）"""
        return getattr(self, '_jaka_controller', None)
    
    @property
    def ext_controller(self):
        """外部轴控制器属性（子类应该实现）"""
        return getattr(self, '_ext_controller', None)