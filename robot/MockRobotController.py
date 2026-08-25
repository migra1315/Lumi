from typing import Callable, Optional
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

    def __init__(self, config: Dict[str, Any] = None, debug: bool = False, auto_setup: bool = True):
        super().__init__(config, debug, auto_setup)

        # Mock特定配置
        self.success_rate = self.config.get('success_rate', 0.95)
        self.base_latency = self.config.get('latency', 0.1)
        self.max_error_rate = self.config.get('max_error_rate', 0.1)
        
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
        
        # 模拟设备状态
        self.device_states = {
            "camera": "online",
            "lidar": "online",
            "ultrasonic": "online",
            "door_sensor": "closed"
        }
        
        # Mock错误场景
        self.error_scenarios = {
            "agv_stuck": False,
            "arm_collision": False,
            "sensor_failure": False,
            "communication_error": False,
            "battery_low": False,
            "camera_failure": False
        }
        
        # 模拟环境数据
        self.environment_data = {
            "temperature": 22.5,
            "humidity": 45.0,
            "oxygen": 20.9,
            "carbon_dioxide": 400.0,
            "pm25": 12.5,
            "pm10": 25.0,
            "etvoc": 0.2,
            "noise": 45.0
        }
        
        # Mock子控制器
        self._agv_controller = MockAGVController(self)
        self._jaka_controller = MockJakaController(self)
        self._ext_controller = MockExtController(self)
        
        # 监控线程
        self._monitor_thread = None
        self._stop_monitoring = False

        # 模拟环境数据更新线程
        self._env_thread = None
        self._stop_env_monitor = False

        # 相机和传感器启用状态
        self._camera_enabled = False
        self._env_sensor_enabled = False

        # 根据 auto_setup 决定是否自动启动监控
        if auto_setup:
            self.start_monitoring()
            self.start_environment_monitoring()
            self._camera_enabled = True
            self._env_sensor_enabled = True
    
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
        self.stop_environment_monitoring()
        self._system_initialized = False
        self.status = RobotStatus.IDLE
    
    def get_environment_data(self) -> Dict[str, float]:
        """获取模拟环境数据"""
        return self.environment_data.copy()
    
    def get_device_states(self) -> Dict[str, str]:
        """获取设备状态"""
        return self.device_states.copy()
    
    def set_error_scenario(self, scenario: str, enabled: bool):
        """设置错误场景"""
        if scenario in self.error_scenarios:
            self.error_scenarios[scenario] = enabled
            status_str = "启用" if enabled else "禁用"
            self.logger.debug(f"错误场景 {scenario} 已{status_str}")
        else:
            self.logger.warning(f"未知错误场景: {scenario}")
    
    def capture_image(self, device_id: str) -> str:
        """模拟拍照功能"""
        self.logger.info(f"模拟拍照设备: {device_id}")
        time.sleep(0.5)  # 模拟拍照延迟
        # 模拟返回base64编码的图像数据
        return f"mock_image_data_{device_id}_{int(time.time())}"
    
    def start_environment_monitoring(self):
        """启动环境数据监控"""
        if self._env_thread is None:
            self._stop_env_monitor = False
            self._env_thread = threading.Thread(
                target=self._environment_monitoring_loop,
                daemon=True
            )
            self._env_thread.start()
            self.logger.info("环境数据监控已启动")
    
    def stop_environment_monitoring(self):
        """停止环境数据监控"""
        self._stop_env_monitor = True
        if self._env_thread:
            self._env_thread.join(timeout=2.0)
            self._env_thread = None
            self.logger.info("环境数据监控已停止")
    
    def _environment_monitoring_loop(self):
        """环境数据监控循环"""
        self.logger.info("环境数据监控循环已启动")
        
        while not self._stop_env_monitor:
            try:
                # 模拟环境数据变化
                self.environment_data["temperature"] += random.uniform(-0.5, 0.5)
                self.environment_data["temperature"] = max(15.0, min(35.0, self.environment_data["temperature"]))
                
                self.environment_data["humidity"] += random.uniform(-2.0, 2.0)
                self.environment_data["humidity"] = max(20.0, min(80.0, self.environment_data["humidity"]))
                
                self.environment_data["pm25"] += random.uniform(-2.0, 2.0)
                self.environment_data["pm25"] = max(0.0, min(100.0, self.environment_data["pm25"]))
                
                self.environment_data["pm10"] += random.uniform(-3.0, 3.0)
                self.environment_data["pm10"] = max(0.0, min(150.0, self.environment_data["pm10"]))
                
                # 随机生成错误
                if random.random() < self.max_error_rate:
                    # 随机选择一个错误场景
                    error_scenario = random.choice(list(self.error_scenarios.keys()))
                    if not self.error_scenarios[error_scenario]:
                        self.error_scenarios[error_scenario] = True
                        self.logger.warning(f"模拟错误发生: {error_scenario}")
                
                time.sleep(5)  # 每5秒更新一次环境数据
                
            except Exception as e:
                self.logger.error(f"环境数据监控异常: {e}")
                time.sleep(1)
    
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
        """获取Mock状态 - 与真实机器人控制器返回结构一致"""
        base_status = super().get_status()

        # 将移动状态映射到AGV move_status字符串
        move_status_map = {
            RobotStatus.IDLE: "idle",
            RobotStatus.MOVING: "moving",
            RobotStatus.ARM_OPERATING: "idle",  # 机械臂操作时AGV是idle
            RobotStatus.EXT_OPERATING: "idle",
            RobotStatus.DOOR_OPERATING: "idle",
            RobotStatus.ERROR: "error",
            RobotStatus.CHARGING: "idle",
            RobotStatus.SETUP: "idle"
        }

        # 构建与真实控制器一致的状态字典
        base_status.update({
            # AGV位置信息 - 扁平化结构（与RobotControlSystem期望的一致）
            "agv_status_x": self.current_position.get("x", 0.0),
            "agv_status_y": self.current_position.get("y", 0.0),
            "agv_status_theta": self.current_position.get("theta", 0.0),

            # 机械臂和外部轴位置
            "robot_joints": self.robot_joints,
            "ext_axis": self.ext_axis,

            # 电池和充电状态
            "power_percent": self.battery_level,
            "charge_state": (self.status == RobotStatus.CHARGING),

            # 移动状态（字符串格式，用于AGV状态）
            "move_status": move_status_map.get(self.status, "idle"),

            # 急停状态
            "soft_estop_state": self.error_scenarios.get("agv_stuck", False),
            "hard_estop_state": False,  # Mock环境默认无硬急停
            "estop_state": self.error_scenarios.get("agv_stuck", False) or self.error_scenarios.get("arm_collision", False),

            # Mock特有数据（可选，用于调试）
            "error_scenarios": self.error_scenarios,
            "environment_data": self.environment_data,
            "device_states": self.device_states,
            "mock_data": True
        })
        return base_status
    
    def emergency_stop(self) -> bool:
        """Mock紧急停止"""
        self.logger.warning("Mock紧急停止!")
        self.status = RobotStatus.IDLE
        
        # 清除错误场景
        for key in self.error_scenarios:
            self.error_scenarios[key] = False
        
        # 停止所有监控
        self.stop_monitoring()
        self.stop_environment_monitoring()
        
        return True
    
    def reset_errors(self) -> bool:
        """重置错误状态"""
        self.logger.info("重置错误状态...")
        # 清除错误场景
        for key in self.error_scenarios:
            self.error_scenarios[key] = False
        
        # 恢复设备状态
        for device in self.device_states:
            self.device_states[device] = "online"
        
        # 恢复监控
        self.start_monitoring()
        self.start_environment_monitoring()
        
        self.status = RobotStatus.IDLE
        return True
    
    def charge(self) -> bool:
        """模拟充电操作"""
        self.logger.info("开始充电")
        self.status = RobotStatus.CHARGING
        
        # 模拟充电过程
        threading.Thread(target=self._simulate_charging, daemon=True).start()
        return True
    
    def _simulate_charging(self):
        """模拟充电过程"""
        while self.battery_level < 100.0 and self.status == RobotStatus.CHARGING:
            self.battery_level += random.uniform(0.5, 1.0)
            self.battery_level = min(100.0, self.battery_level)
            time.sleep(2)  # 每2秒增加一次电量

        if self.status == RobotStatus.CHARGING:
            self.logger.info("充电完成")
            self.status = RobotStatus.IDLE

    def joy_control(self, data_json: Dict[str, Any]) -> bool:
        """摇杆控制模拟

        Args:
            data_json: 摇杆控制数据，包含 joy_control_cmd 字段
                      - angular_velocity: 角速度
                      - linear_velocity: 线速度

        Returns:
            bool: 操作是否成功
        """
        try:
            joy_cmd = data_json.get('joy_control_cmd', {})
            angular_velocity = joy_cmd.get('angular_velocity', '0')
            linear_velocity = joy_cmd.get('linear_velocity', '0')

            self.logger.info(f"摇杆控制 - 线速度: {linear_velocity}, 角速度: {angular_velocity}")

            # 模拟摇杆控制，更新位置
            if self.status != RobotStatus.ERROR:
                self.status = RobotStatus.MOVING

                # 简单模拟位置变化
                try:
                    linear_vel = float(linear_velocity)
                    angular_vel = float(angular_velocity)

                    # 模拟位置更新（简化版）
                    self.current_position['x'] += linear_vel * 0.1
                    self.current_position['y'] += linear_vel * 0.1 * 0.5
                    self.current_position['theta'] += angular_vel * 0.1

                    time.sleep(0.1)  # 模拟控制延迟

                    self.status = RobotStatus.IDLE
                    return True
                except ValueError:
                    self.logger.error(f"无效的速度值: linear={linear_velocity}, angular={angular_velocity}")
                    return False
            else:
                self.logger.warning("机器人处于错误状态，无法执行摇杆控制")
                return False

        except Exception as e:
            self.logger.error(f"摇杆控制失败: {e}")
            return False
    
    def set_marker(self, marker_id: str) -> bool:
        """
        设置当前标记点

        Args:
            marker_id: 标记点ID

        Returns:
            bool: 操作是否成功
        """
        try:
            self.logger.info(f"设置当前标记点为: {marker_id}")
            marker = {"marker_id": marker_id, **self.current_position}
            self._marker_positions.append(marker)
            return True

        except Exception as e:
            self.logger.error(f"设置标记点失败: {e}")
            return False
    
    
    def position_adjust(self, marker_id: str) -> bool:
        """
        位置调整 - 移动到指定标记点

        Args:
            marker_id: 目标标记点ID

        Returns:
            bool: 操作是否成功
        """
        try:
            self.logger.info(f"开始位置调整 - 目标标记点: {marker_id}")
        
        except Exception as e:
            self.logger.error(f"位置调整失败: {e}")
            return False
    
    # ==================== 硬件模块控制方法 ====================
    def start_camera(self) -> bool:
        """启动模拟相机"""
        self.logger.info("Mock相机启动")
        self._camera_enabled = True
        return True

    def stop_camera(self) -> bool:
        """关闭模拟相机"""
        self.logger.info("Mock相机关闭")
        self._camera_enabled = False
        return True

    def start_env_sensor(self) -> bool:
        """启动模拟环境传感器"""
        self.logger.info("Mock环境传感器启动")
        self._env_sensor_enabled = True
        if self._env_thread is None or not self._env_thread.is_alive():
            self.start_environment_monitoring()
        return True

    def stop_env_sensor(self) -> bool:
        """关闭模拟环境传感器"""
        self.logger.info("Mock环境传感器关闭")
        self._env_sensor_enabled = False
        self.stop_environment_monitoring()
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
                # 充电过程在_simulate_charging中处理
                pass
            
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
                # 触发低电量错误
                self.error_scenarios["battery_low"] = True
            
            if old_status != self.battery_status:
                self._trigger_callback("on_battery_change", self.battery_status)
            
            # 随机改变设备状态
            if random.random() < 0.05:  # 5%概率改变设备状态
                device = random.choice(list(self.device_states.keys()))
                new_state = "offline" if self.device_states[device] == "online" else "online"
                self.device_states[device] = new_state
                # self.logger.info(f"设备 {device} 状态变为: {new_state}")
            
            time.sleep(1)
    
    def _simulate_operation(self, operation_name: str) -> bool:
        """模拟操作"""
        # 检查是否有错误场景启用
        for scenario, enabled in self.error_scenarios.items():
            if enabled:
                self.logger.warning(f"操作失败: {operation_name}, 错误场景: {scenario}")
                return False
        
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