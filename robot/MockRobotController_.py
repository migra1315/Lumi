import time
import threading
import random
from typing import List, Dict, Any, Optional, Callable
from enum import Enum
import logging

class RobotStatus(Enum):
    IDLE = "idle"
    MOVING = "moving"
    OPERATING = "operating"
    ERROR = "error"
    CHARGING = "charging"

class BatteryStatus(Enum):
    HIGH = "high"      # > 80%
    MEDIUM = "medium"  # 30-80%
    LOW = "low"       # 10-30%
    CRITICAL = "critical"  # < 10%

class MockRobotController():
    """机器人控制器Mock类，模拟真实机器人控制器的行为"""
    
    def __init__(self, success_rate: float = 0.9, latency: float = 1):
        """
        初始化Mock控制器
        
        Args:
            success_rate: 操作成功率 (0.0-1.0)
            latency: 模拟操作延迟的基础时间(秒)
        """
        self.success_rate = min(1.0, max(0.0, success_rate))
        self.base_latency = latency
        
        # 控制器状态
        self.status = RobotStatus.IDLE
        self.battery_level = 85.0  # 初始电量85%
        self.battery_status = BatteryStatus.HIGH
        self.current_position = {"x": 0.0, "y": 0.0, "z": 0.0, "theta": 0.0}
        self.current_marker = None
        self.robot_joints = [0.0] * 6  # 6轴机械臂
        self.ext_axis = [0.0] * 4     # 4轴外部轴
        
        # 子控制器
        self.agv_controller = MockAGVController(self)
        self.jaka_controller = MockJakaController(self)
        self.ext_controller = MockExtController(self)
        
        # 状态监控
        self._monitor_thread = None
        self._stop_monitoring = False
        self._monitor_interval = 1.0  # 监控间隔(秒)
        
        # 回调函数
        self.callbacks = {
            "on_status_change": [],
            "on_battery_change": [],
            "on_position_change": [],
            "on_error": []
        }
        
        # 错误模拟配置
        self.error_scenarios = {
            "agv_stuck": False,
            "arm_collision": False,
            "sensor_failure": False,
            "communication_error": False
        }
        
        # 模拟数据
        self._marker_positions = {
            "marker_1": {"x": 10.0, "y": 5.0, "z": 0.0, "theta": 0.0},
            "marker_2": {"x": 15.0, "y": 8.0, "z": 0.0, "theta": 90.0},
            "marker_3": {"x": 20.0, "y": 3.0, "z": 0.0, "theta": 180.0},
            "charge_point_1F_6010": {"x": 0.0, "y": 0.0, "z": 0.0, "theta": 0.0}
        }
        
        self.logger = logging.getLogger("MockRobotController")
        
        # 启动状态监控
        self.start_monitoring()
    
    def start_monitoring(self):
        """启动状态监控线程"""
        if self._monitor_thread is None:
            self._stop_monitoring = False
            self._monitor_thread = threading.Thread(target=self._monitoring_loop)
            self._monitor_thread.daemon = True
            self._monitor_thread.start()
            self.logger.info("状态监控已启动")
    
    def stop_monitoring(self):
        """停止状态监控"""
        self._stop_monitoring = True
        if self._monitor_thread:
            self._monitor_thread.join(timeout=2.0)
            self._monitor_thread = None
            self.logger.info("状态监控已停止")
    
    def _monitoring_loop(self):
        """监控循环，模拟真实世界状态变化"""
        while not self._stop_monitoring:
            try:
                # 模拟电量消耗
                if self.status in [RobotStatus.MOVING, RobotStatus.OPERATING]:
                    self.battery_level -= random.uniform(0.05, 0.15)
                elif self.status == RobotStatus.CHARGING:
                    self.battery_level += random.uniform(0.5, 1.0)
                
                # 限制电量范围
                self.battery_level = max(0.0, min(100.0, self.battery_level))
                
                # 更新电池状态
                old_status = self.battery_status
                if self.battery_level >= 80:
                    self.battery_status = BatteryStatus.HIGH
                elif self.battery_level >= 30:
                    self.battery_status = BatteryStatus.MEDIUM
                elif self.battery_level >= 10:
                    self.battery_status = BatteryStatus.LOW
                else:
                    self.battery_status = BatteryStatus.CRITICAL
                
                # 触发电池状态变化回调
                if old_status != self.battery_status:
                    self._trigger_callback("on_battery_change", self.battery_status)
                
                # 模拟随机错误
                self._simulate_random_errors()
                
                # 睡眠
                time.sleep(self._monitor_interval)
                
            except Exception as e:
                self.logger.error(f"监控循环异常: {e}")
                time.sleep(self._monitor_interval)
    
    def _simulate_random_errors(self):
        """模拟随机错误"""
        # 根据当前状态和电池电量调整错误概率
        error_probability = 0.9  # 基础错误率1%
        
        # if self.battery_status == BatteryStatus.CRITICAL:
        #     error_probability = 0.1  # 低电量时错误率升高
        # elif self.battery_status == BatteryStatus.LOW:
        #     error_probability = 0.05
        
        if random.random() < error_probability:
            error_type = random.choice(list(self.error_scenarios.keys()))
            self.error_scenarios[error_type] = True
            self.status = RobotStatus.ERROR
            self._trigger_callback("on_error", error_type)
            # self.logger.warning(f"模拟错误: {error_type}")
    
    def _simulate_latency(self):
        """模拟操作延迟"""
        latency = self.base_latency + random.uniform(0, 5)
        time.sleep(latency)
    
    def _simulate_operation(self, operation_name: str) -> bool:
        """
        模拟操作执行
        
        Args:
            operation_name: 操作名称
            
        Returns:
            bool: 操作是否成功
        """
        self.logger.info(f"开始执行操作: {operation_name}")
        
        # 检查是否有模拟错误
        print(self.status)
        if self.status == RobotStatus.ERROR:
            self.logger.error(f"操作{operation_name}失败：机器人处于错误状态")
            return False
        
        # 检查电池电量
        if self.battery_status == BatteryStatus.CRITICAL:
            self.logger.error(f"操作{operation_name}失败：电量严重不足")
            return False
        
        # 模拟延迟
        self._simulate_latency()
        
        # 基于成功率决定是否成功
        success = random.random() <= self.success_rate
        
        if success:
            self.logger.info(f"操作{operation_name}成功")
        else:
            self.logger.warning(f"操作{operation_name}失败")
            # 失败时可能设置错误状态
            if random.random() < 0.3:  # 30%的失败会导致错误状态
                self.status = RobotStatus.ERROR
        
        return success
    
    def get_status(self) -> Dict[str, Any]:
        """获取机器人完整状态"""
        return {
            "status": self.status.value,
            "battery_level": self.battery_level,
            "battery_status": self.battery_status.value,
            "current_position": self.current_position,
            "current_marker": self.current_marker,
            "robot_joints": self.robot_joints,
            "ext_axis": self.ext_axis,
            "error_scenarios": self.error_scenarios.copy()
        }
    
    def charge(self, duration: float = 10.0) -> bool:
        """模拟充电操作"""
        self.logger.info(f"开始充电，预计时间: {duration}秒")
        self.status = RobotStatus.CHARGING
        
        # 模拟充电过程
        time.sleep(min(duration, 5.0))  # 最多模拟5秒
        
        # 充电完成
        self.battery_level = min(100.0, self.battery_level + 50.0)  # 充50%的电
        self.status = RobotStatus.IDLE
        self.logger.info(f"充电完成，当前电量: {self.battery_level}%")
        return True
    
    def emergency_stop(self) -> bool:
        """紧急停止"""
        self.logger.warning("执行紧急停止")
        self.status = RobotStatus.IDLE
        
        # 重置所有控制器
        self.agv_controller.stop()
        self.jaka_controller.stop()
        self.ext_controller.stop()
        
        return True
    
    def reset_errors(self) -> bool:
        """重置错误状态"""
        self.logger.info("重置错误状态")
        self.status = RobotStatus.IDLE
        
        # 清除所有模拟错误
        for key in self.error_scenarios:
            self.error_scenarios[key] = False
        
        return True
    
    def register_callback(self, event: str, callback: Callable):
        """注册回调函数"""
        if event in self.callbacks:
            self.callbacks[event].append(callback)
        else:
            self.logger.warning(f"未知事件类型: {event}")
    
    def _trigger_callback(self, event: str, *args, **kwargs):
        """触发回调函数"""
        for callback in self.callbacks.get(event, []):
            try:
                callback(*args, **kwargs)
            except Exception as e:
                self.logger.error(f"回调函数执行异常: {e}")
    
    def set_success_rate(self, rate: float):
        """设置操作成功率"""
        self.success_rate = min(1.0, max(0.0, rate))
        self.logger.info(f"设置操作成功率为: {self.success_rate*100}%")
    
    def set_error_scenario(self, scenario: str, active: bool = True):
        """设置错误场景"""
        if scenario in self.error_scenarios:
            self.error_scenarios[scenario] = active
            if active:
                self.status = RobotStatus.ERROR
            self.logger.info(f"设置错误场景 '{scenario}' 为: {active}")
        else:
            self.logger.warning(f"未知错误场景: {scenario}")
    
    def __del__(self):
        """析构函数"""
        self.stop_monitoring()


class MockAGVController:
    """AGV控制器Mock类"""
    
    def __init__(self, parent: MockRobotController):
        self.parent = parent
        self.is_moving = False
        self.target_marker = None
        self.logger = logging.getLogger("MockAGVController")
    
    def move_to_marker(self, marker_id: str) -> bool:
        """
        移动到指定标记点
        
        Args:
            marker_id: 标记点ID
            
        Returns:
            bool: 是否成功
        """
        if marker_id not in self.parent._marker_positions:
            self.logger.error(f"未知标记点: {marker_id}")
            return False
        
        self.logger.info(f"AGV开始移动到标记点: {marker_id}")
        self.is_moving = True
        self.target_marker = marker_id
        
        # 更新父控制器状态
        self.parent.status = RobotStatus.MOVING
        
        # 模拟移动过程
        success = self.parent._simulate_operation(f"agv_move_to_{marker_id}")
        
        if success:
            # 更新位置
            self.parent.current_position = self.parent._marker_positions[marker_id].copy()
            self.parent.current_marker = marker_id
            
            # 触发位置变化回调
            self.parent._trigger_callback("on_position_change", self.parent.current_position)
        
        self.is_moving = False
        self.parent.status = RobotStatus.IDLE
        return success
    
    def get_position(self) -> Dict[str, float]:
        """获取当前位置"""
        return self.parent.current_position.copy()
    
    def stop(self) -> bool:
        """停止移动"""
        self.logger.info("AGV停止移动")
        self.is_moving = False
        return True
    
    def set_velocity(self, velocity: float) -> bool:
        """
        设置移动速度
        
        Args:
            velocity: 速度值 (0.0-1.0)
        """
        self.logger.info(f"设置AGV速度为: {velocity}")
        return True
    
    def get_status(self) -> Dict[str, Any]:
        """获取AGV状态"""
        return {
            "is_moving": self.is_moving,
            "target_marker": self.target_marker,
            "current_position": self.parent.current_position
        }


class MockJakaController:
    """机械臂控制器Mock类"""
    
    def __init__(self, parent: MockRobotController):
        self.parent = parent
        self.is_moving = False
        self.logger = logging.getLogger("MockJakaController")
    
    def move_to_position(self, position: List[float]) -> bool:
        """
        移动到指定位置
        
        Args:
            position: 6轴位置列表
            
        Returns:
            bool: 是否成功
        """
        if len(position) != 6:
            self.logger.error(f"无效的位置数据，需要6个值，得到{len(position)}个")
            return False
        
        self.logger.info(f"机械臂开始移动到位置: {position}")
        self.is_moving = True
        
        # 更新父控制器状态
        self.parent.status = RobotStatus.OPERATING
        
        # 模拟移动过程
        success = self.parent._simulate_operation("jaka_move_to_position")
        
        if success:
            # 更新关节位置
            self.parent.robot_joints = position.copy()
        
        self.is_moving = False
        self.parent.status = RobotStatus.IDLE
        return success
    
    def get_joint_positions(self) -> List[float]:
        """获取关节位置"""
        return self.parent.robot_joints.copy()
    
    def set_speed(self, speed: float) -> bool:
        """
        设置机械臂速度
        
        Args:
            speed: 速度值 (0.0-1.0)
        """
        self.logger.info(f"设置机械臂速度为: {speed}")
        return True
    
    def home(self) -> bool:
        """回零"""
        self.logger.info("机械臂回零")
        return self.move_to_position([0.0, 0.0, 0.0, 0.0, 0.0, 0.0])
    
    def stop(self) -> bool:
        """停止运动"""
        self.logger.info("机械臂停止运动")
        self.is_moving = False
        return True
    
    def get_status(self) -> Dict[str, Any]:
        """获取机械臂状态"""
        return {
            "is_moving": self.is_moving,
            "joint_positions": self.parent.robot_joints.copy()
        }


class MockExtController:
    """外部轴控制器Mock类"""
    
    def __init__(self, parent: MockRobotController):
        self.parent = parent
        self.is_moving = False
        self.logger = logging.getLogger("MockExtController")
    
    def move_to_position(self, position: List[float]) -> bool:
        """
        移动到指定位置
        
        Args:
            position: 外部轴位置列表
            
        Returns:
            bool: 是否成功
        """
        if len(position) != 4:
            self.logger.error(f"无效的位置数据，需要4个值，得到{len(position)}个")
            return False
        
        self.logger.info(f"外部轴开始移动到位置: {position}")
        self.is_moving = True
        
        # 更新父控制器状态
        self.parent.status = RobotStatus.OPERATING
        
        # 模拟移动过程
        success = self.parent._simulate_operation("ext_move_to_position")
        
        if success:
            # 更新外部轴位置
            self.parent.ext_axis = position.copy()
        
        self.is_moving = False
        self.parent.status = RobotStatus.IDLE
        return success
    
    def get_positions(self) -> List[float]:
        """获取外部轴位置"""
        return self.parent.ext_axis.copy()
    
    def set_velocity(self, velocity: float) -> bool:
        """
        设置外部轴速度
        
        Args:
            velocity: 速度值 (0.0-1.0)
        """
        self.logger.info(f"设置外部轴速度为: {velocity}")
        return True
    
    def home(self) -> bool:
        """回零"""
        self.logger.info("外部轴回零")
        return self.move_to_position([0.0, 0.0, 0.0, 0.0])
    
    def stop(self) -> bool:
        """停止运动"""
        self.logger.info("外部轴停止运动")
        self.is_moving = False
        return True
    
    def get_status(self) -> Dict[str, Any]:
        """获取外部轴状态"""
        return {
            "is_moving": self.is_moving,
            "positions": self.parent.ext_axis.copy()
        }