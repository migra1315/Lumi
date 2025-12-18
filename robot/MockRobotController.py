from typing import Callable
"""
MockRobotController.py
Mock机器人控制器实现
"""

import time
import random
import threading
from typing import List, Dict, Any
from robot.RobotControllerBase import RobotControllerBase, RobotStatus, BatteryStatus

class MockRobotController(RobotControllerBase):
    """Mock机器人控制器实现类"""
    
    def __init__(self, config: Dict[str, Any] = None, debug: bool = False):
        super().__init__(config, debug)
        
        # Mock特定配置
        self.success_rate = self.config.get('success_rate', 0.9)
        self.base_latency = self.config.get('latency', 0.1)
        
        # Mock数据
        self._marker_positions = {
            "marker_1": {"x": 10.0, "y": 5.0, "z": 0.0, "theta": 0.0},
            "marker_2": {"x": 15.0, "y": 8.0, "z": 0.0, "theta": 90.0},
            "marker_3": {"x": 20.0, "y": 3.0, "z": 0.0, "theta": 180.0},
            "charge_point_1F_6010": {"x": 0.0, "y": 0.0, "z": 0.0, "theta": 0.0}
        }
        
        self.current_position = {"x": 0.0, "y": 0.0, "z": 0.0, "theta": 0.0}
        self.robot_joints = [0.0] * 6
        self.ext_axis = [0.0] * 4
        
        # Mock错误场景
        self.error_scenarios = {
            "agv_stuck": False,
            "arm_collision": False,
            "sensor_failure": False,
            "communication_error": False
        }
        
        # Mock子控制器
        self._agv_controller = MockAGVController(self)
        self._jaka_controller = MockJakaController(self)
        self._ext_controller = MockExtController(self)
        
        # 监控线程
        self._monitor_thread = None
        self._stop_monitoring = False
        
        # 启动监控
        self.start_monitoring()
    
    def setup_system(self) -> bool:
        """Mock系统初始化"""
        self.logger.info("Mock系统初始化...")
        self.status = RobotStatus.SETUP
        
        # 模拟初始化延迟
        time.sleep(0.5)
        
        self._system_initialized = True
        self.status = RobotStatus.IDLE
        self.logger.info("Mock系统初始化完成")
        return True
    
    def shutdown_system(self):
        """Mock系统关闭"""
        self.logger.info("Mock系统关闭...")
        self.stop_monitoring()
        self._system_initialized = False
        self.status = RobotStatus.IDLE
    
    def move_to_marker(self, marker_id: str) -> bool:
        """Mock移动AGV到标记点"""
        if not self._system_initialized:
            self.logger.error("系统未初始化")
            return False
        
        self.logger.info(f"Mock移动AGV到标记点: {marker_id}")
        self.status = RobotStatus.MOVING
        
        # 检查标记点是否存在
        if marker_id not in self._marker_positions:
            self.logger.error(f"未知标记点: {marker_id}")
            self.status = RobotStatus.ERROR
            return False
        
        # 模拟移动过程
        success = self._simulate_operation(f"move_to_{marker_id}")
        
        if success:
            self.current_position = self._marker_positions[marker_id].copy()
            self.current_marker = marker_id
            self.status = RobotStatus.IDLE
            self.logger.info(f"Mock AGV已到达标记点: {marker_id}")
            self._trigger_callback("on_task_complete", "move_agv", marker_id)
        else:
            self.status = RobotStatus.ERROR
            self.last_error = f"移动AGV到{marker_id}失败"
        
        return success
    
    def move_robot_to_position(self, position: List[float]) -> bool:
        """Mock移动机械臂"""
        if not self._system_initialized:
            self.logger.error("系统未初始化")
            return False
        
        if len(position) != 6:
            self.logger.error(f"无效位置数据，需要6个值")
            return False
        
        self.logger.info(f"Mock移动机械臂到位置: {position}")
        self.status = RobotStatus.ARM_OPERATING
        
        # 模拟移动过程
        success = self._simulate_operation("move_robot")
        
        if success:
            self.robot_joints = position.copy()
            self.status = RobotStatus.IDLE
            self.logger.info("Mock机械臂移动完成")
            self._trigger_callback("on_task_complete", "move_robot", position)
        else:
            self.status = RobotStatus.ERROR
            self.last_error = "机械臂移动失败"
        
        return success
    
    def move_ext_to_position(self, position: List[float]) -> bool:
        """Mock移动外部轴"""
        if not self._system_initialized:
            self.logger.error("系统未初始化")
            return False
        
        if len(position) != 4:
            self.logger.error(f"无效位置数据，需要4个值")
            return False
        
        self.logger.info(f"Mock移动外部轴到位置: {position}")
        self.status = RobotStatus.EXT_OPERATING
        
        # 模拟移动过程
        success = self._simulate_operation("move_ext")
        
        if success:
            self.ext_axis = position.copy()
            self.status = RobotStatus.IDLE
            self.logger.info("Mock外部轴移动完成")
            self._trigger_callback("on_task_complete", "move_ext", position)
        else:
            self.status = RobotStatus.ERROR
            self.last_error = "外部轴移动失败"
        
        return success
    
    def get_status(self) -> Dict[str, Any]:
        """获取Mock状态"""
        base_status = super().get_status()
        base_status.update({
            "current_position": self.current_position,
            "robot_joints": self.robot_joints,
            "ext_axis": self.ext_axis,
            "error_scenarios": self.error_scenarios,
            "mock_data": True
        })
        return base_status
    
    def emergency_stop(self) -> bool:
        """Mock紧急停止"""
        self.logger.warning("Mock紧急停止!")
        self.stop_monitoring()
        self.status = RobotStatus.IDLE
        
        # 清除错误场景
        for key in self.error_scenarios:
            self.error_scenarios[key] = False
        
        return True
    
    # ==================== Mock特有方法 ====================
    def start_monitoring(self):
        """启动监控线程"""
        if self._monitor_thread is None:
            self._stop_monitoring = False
            self._monitor_thread = threading.Thread(
                target=self._monitoring_loop,
                daemon=True
            )
            self._monitor_thread.start()
            self.logger.info("Mock监控已启动")
    
    def stop_monitoring(self):
        """停止监控线程"""
        self._stop_monitoring = True
        if self._monitor_thread:
            self._monitor_thread.join(timeout=2.0)
            self._monitor_thread = None
            self.logger.info("Mock监控已停止")
    
    def _monitoring_loop(self):
        """监控循环"""
        import time
        while not self._stop_monitoring:
            # 模拟电量消耗
            if self.status in [RobotStatus.MOVING, RobotStatus.ARM_OPERATING, 
                              RobotStatus.EXT_OPERATING, RobotStatus.DOOR_OPERATING]:
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
            
            if old_status != self.battery_status:
                self._trigger_callback("on_battery_change", self.battery_status)
            
            time.sleep(1)
    
    def _simulate_operation(self, operation_name: str) -> bool:
        """模拟操作"""
        # 模拟延迟
        latency = self.base_latency + random.uniform(0, 0.2)
        time.sleep(latency)
        
        # 基于成功率决定是否成功
        success = random.random() <= self.success_rate
        
        if success:
            self.logger.debug(f"操作成功: {operation_name}")
        else:
            self.logger.warning(f"操作失败: {operation_name}")
            
        return success


# ==================== Mock子控制器 ====================

class MockAGVController:
    def __init__(self, parent):
        self.parent = parent
    
    def move_to_marker(self, marker_id: str) -> bool:
        return self.parent.move_to_marker(marker_id)
    
    def get_position(self) -> Dict[str, float]:
        return self.parent.current_position.copy()
    
    def stop(self) -> bool:
        self.parent.logger.info("Mock AGV停止")
        return True


class MockJakaController:
    def __init__(self, parent):
        self.parent = parent
    
    def move_to_position(self, position: List[float]) -> bool:
        return self.parent.move_robot_to_position(position)
    
    def get_joint_positions(self) -> List[float]:
        return self.parent.robot_joints.copy()
    
    def home(self) -> bool:
        return self.move_to_position([0.0] * 6)
    
    def stop(self) -> bool:
        self.parent.logger.info("Mock机械臂停止")
        return True


class MockExtController:
    def __init__(self, parent):
        self.parent = parent
    
    def move_to_position(self, position: List[float]) -> bool:
        return self.parent.move_ext_to_position(position)
    
    def get_positions(self) -> List[float]:
        return self.parent.ext_axis.copy()
    
    def home(self) -> bool:
        return self.move_to_position([0.0] * 4)
    
    def stop(self) -> bool:
        self.parent.logger.info("Mock外部轴停止")
        return True