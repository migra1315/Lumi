
"""
RobotController.py
机器人控制器主类，整合AGV、机械臂和外部轴的控制
"""

import time
import logging
import threading
from typing import Dict, List, Any, Optional, Callable
from enum import Enum
from dataModels.MessageModels import MoveStatus
from robot.AGVController import AGVController
from robot.ArmController import ArmController

# 导入环境传感器
try:
    import sys
    import os
    # 添加 envsMonitor 目录到路径
    env_monitor_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'envsMonitor')
    if env_monitor_path not in sys.path:
        sys.path.insert(0, env_monitor_path)
    from envsMonitor.AirQualitySensor import AirQualitySensor
    ENV_SENSOR_AVAILABLE = True
except ImportError as e:
    ENV_SENSOR_AVAILABLE = False
    AirQualitySensor = None

class SystemStatus(Enum):
    """机器人状态枚举"""
    IDLE = "idle"
    MOVING = "moving"  # AGV移动中
    ARM_OPERATING = "arm_operating"  # 机械臂操作中
    EXT_OPERATING = "ext_operating"  # 外部轴操作中
    DOOR_OPERATING = "door_operating"  # 门操作中
    ERROR = "error"
    CHARGING = "charging"
    SETUP = "setup"  # 系统初始化中

class RobotController():
    """机器人主控制器，整合AGV、机械臂和外部轴的控制"""
    
    def __init__(self, system_config: Dict[str, Any] = None, debug: bool = False):
        """
        初始化机器人控制器
        
        Args:
            system_config: 系统配置字典
            debug: 是否启用调试模式
        """
        self.system_config = system_config or {}
        self.debug = debug
        
        # 控制器状态
        self.system_status = SystemStatus.IDLE
        self.current_marker = None
        self.last_error = None
        
        # 初始化日志
        self.logger = self._setup_logger(debug)
        
        # 初始化子控制器
        self._init_sub_controllers()
        
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

        # 环境传感器相关
        self.env_sensor = None
        self.env_sensor_enabled = system_config.get('env_sensor_enabled', False)
        self.env_sensor_config = system_config.get('env_sensor_config', {})
        self._env_data_lock = threading.Lock()
        self._env_data = {
            "temperature": 0.0,
            "humidity": 0.0,
            "oxygen": 0.0,
            "carbon_dioxide": 0.0,
            "pm25": 0.0,
            "pm10": 0.0,
            "etvoc": 0.0,
            "noise": 0.0
        }
        self._env_monitor_thread = None
        self._stop_env_monitor = False

        self.logger.info("机器人控制器初始化完成")
    
    def _setup_logger(self, debug: bool) -> logging.Logger:
        """设置日志记录器"""
        logger = logging.getLogger("RobotController")
        
        if debug:
            logger.setLevel(logging.DEBUG)
        else:
            logger.setLevel(logging.INFO)
        
        # 确保只添加一次处理器
        if not logger.handlers:
            console_handler = logging.StreamHandler()
            formatter = logging.Formatter(
                '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
            )
            console_handler.setFormatter(formatter)
            logger.addHandler(console_handler)
        
        return logger
    
    def _init_sub_controllers(self):
        """初始化子控制器"""
        try:
            # 从完整配置中提取robot_config部分传递给子控制器
            robot_config = self.system_config.get('robot_config', {})

            self.logger.info("正在初始化AGV控制器...")
            self.agv_controller = AGVController(robot_config, debug=self.debug)

            self.logger.info("正在初始化机械臂控制器...")
            # 注意：ArmController同时包含机械臂和外部轴控制
            self.arm_controller = ArmController(
                system_config=robot_config,
                ext_axis_limits=robot_config.get("ext_axis_limits"),
                debug=self.debug
            )

            # 为了与Mock接口保持一致，创建别名
            self.jaka_controller = self.arm_controller
            self.ext_controller = self.arm_controller

            self.logger.info("子控制器初始化成功")

        except Exception as e:
            self.logger.error(f"初始化子控制器失败: {e}")
            raise
    
    def setup_system(self) -> bool:
        """
        初始化整个机器人系统

        Returns:
            bool: 初始化是否成功
        """
        try:
            self.logger.info("开始初始化机器人系统...")
            self.system_status = SystemStatus.SETUP

            # 初始化AGV控制器
            agv_ok = True  # AGV控制器在初始化时不立即连接

            # 初始化机械臂和外部轴系统
            arm_ok = self.arm_controller.setup_system()
            if not arm_ok:
                self.logger.error("机械臂系统初始化失败")
                return False

            # 初始化环境传感器
            env_sensor_ok = self._setup_environment_sensor()
            if not env_sensor_ok:
                self.logger.warning("环境传感器初始化失败，将使用默认值")

            self._system_initialized = True
            self.system_status = SystemStatus.IDLE
            self.logger.info("机器人系统初始化完成")

            return agv_ok and arm_ok

        except Exception as e:
            self.logger.error(f"系统初始化失败: {e}")
            self.system_status = SystemStatus.ERROR
            self.last_error = str(e)
            return False
    
    def shutdown_system(self):
        """关闭机器人系统"""
        try:
            self.logger.info("正在关闭机器人系统...")

            # 停止环境传感器监控
            self._shutdown_environment_sensor()

            # 停止AGV数据监控
            self.stop_agv_data_monitoring()

            # 关闭机械臂和外部轴系统
            if hasattr(self.arm_controller, 'shutdown_system'):
                self.arm_controller.shutdown_system()

            self._system_initialized = False
            self.system_status = SystemStatus.IDLE
            self.logger.info("机器人系统已关闭")

        except Exception as e:
            self.logger.error(f"关闭系统时发生错误: {e}")
    
    def get_status(self) -> Dict[str, Any]:
        """
        获取机器人完整状态

        Returns:
            Dict: 包含机器人各种状态信息的字典
        """
        try:
            # 获取AGV状态（从AGVController内部缓存或网络请求）
            agv_status = None
            agv_status_response = self.agv_controller.agv_get_status()
            if agv_status_response and agv_status_response.get('status') == 'OK':
                agv_status = agv_status_response.get('results', {})

            # 获取外部轴状态

            ext_status_response = self.arm_controller.ext_get_state()
            ext_status = [ext_status_response[0].get('pos', 0.0),
                          ext_status_response[1].get('pos', 0.0),
                          ext_status_response[2].get('pos', 0.0),
                          ext_status_response[3].get('pos', 0.0),
                        ]

            arm_status = self.arm_controller.arm_get_state()
            
            # 构建状态字典
            status_dict = {
                "system_initialized": self._system_initialized,
                "agv_status_x": agv_status.get('current_pose', {}).get('x', 0.0),
                "agv_status_y": agv_status.get('current_pose', {}).get('y', 0.0),
                "agv_status_theta": agv_status.get('current_pose', {}).get('theta', 0.0),
                "ext_axis": ext_status,
                "robot_joints": arm_status,
                "power_percent": agv_status.get('power_percent', 0.0),
                "move_status": MoveStatus(agv_status.get('move_status', 'unknown')),
                "charge_state": agv_status.get('charge_state', False),
                "soft_estop_state": agv_status.get('soft_estop_state', False),
                "hard_estop_state": agv_status.get('hard_estop_state', False),
                "estop_state": agv_status.get('estop_state', False),
            }

            return status_dict

        except Exception as e:
            self.logger.error(f"获取状态失败: {e}")
            return {
                "status": self.system_status.value,
                "error": str(e),
                "system_initialized": self._system_initialized
            }
    
    def emergency_stop(self) -> bool:
        """
        紧急停止所有机器人动作
        
        Returns:
            bool: 操作是否成功
        """
        try:
            self.logger.warning("执行紧急停止!")
            
            # 停止AGV
            agv_stopped = False
            if hasattr(self.agv_controller, 'agv_estop'):
                agv_stopped = self.agv_controller.agv_estop()
            
            # 停止机械臂（假设有急停接口）
            # TODO: 实现机械臂急停逻辑
            arm_stopped = True  # 暂时标记为成功
            
            # 更新状态
            if agv_stopped and arm_stopped:
                self.system_status = SystemStatus.IDLE
                self.logger.info("紧急停止成功")
                return True
            else:
                self.logger.error("紧急停止失败")
                return False
                
        except Exception as e:
            self.logger.error(f"紧急停止时发生错误: {e}")
            return False
    
    def reset_errors(self) -> bool:
        """
        重置错误状态
        
        Returns:
            bool: 操作是否成功
        """
        try:
            self.logger.info("重置错误状态...")
            
            # 重置AGV错误
            agv_reset = True
            if hasattr(self.agv_controller, 'agv_estop_release'):
                agv_reset = self.agv_controller.agv_estop_release()
            
            # 重置机械臂错误（如果有相关接口）
            arm_reset = True
            
            # 重置外部轴错误
            ext_reset = True
            if hasattr(self.arm_controller, 'ext_reset'):
                ext_reset = self.arm_controller.ext_reset()
            
            if agv_reset and arm_reset and ext_reset:
                self.system_status = SystemStatus.IDLE
                self.last_error = None
                self.logger.info("错误状态重置成功")
                return True
            else:
                self.logger.warning("重置错误状态时部分失败")
                return False
                
        except Exception as e:
            self.logger.error(f"重置错误状态时发生错误: {e}")
            return False
    
    def move_to_marker(self, marker_id: str) -> bool:
        """
        移动AGV到指定标记点
        
        Args:
            marker_id: 标记点ID
            
        Returns:
            bool: 操作是否成功
        """
        if not self._system_initialized:
            self.logger.error("系统未初始化，无法移动")
            return False
        
        try:
            self.logger.info(f"移动AGV到标记点: {marker_id}")
            self.system_status = SystemStatus.MOVING
            
            # 调用AGV控制器的移动方法
            success = self.agv_controller.agv_moveto(marker_id)
            
            if success:
                self.current_marker = marker_id
                self.system_status = SystemStatus.IDLE
                self.logger.info(f"AGV已成功移动到标记点: {marker_id}")
                self._trigger_callback("on_task_complete", "move_agv", marker_id)
            else:
                self.system_status = SystemStatus.ERROR
                self.last_error = f"移动AGV到{marker_id}失败"
                self.logger.error(self.last_error)
            
            return success
            
        except Exception as e:
            self.logger.error(f"移动AGV时发生错误: {e}")
            self.system_status = SystemStatus.ERROR
            self.last_error = str(e)
            return False
    
    def move_robot_to_position(self, position: List[float], velocity: float = None) -> bool:
        """
        移动机械臂到指定位置
        
        Args:
            position: 机械臂关节位置 [J1, J2, J3, J4, J5, J6]（度）
            velocity: 移动速度（度/秒），可选
            
        Returns:
            bool: 操作是否成功
        """
        if not self._system_initialized:
            self.logger.error("系统未初始化，无法移动机械臂")
            return False
        
        try:
            self.logger.info(f"移动机械臂到位置: {position}")
            self.system_status = SystemStatus.ARM_OPERATING
            
            # 调用机械臂控制器的移动方法
            if hasattr(self.arm_controller, 'rob_moveto'):
                result = self.arm_controller.rob_moveto(position, vel=velocity)
                # TODO：重新确认函数返回值含义
                success = (result is not None)  # 根据实际返回值判断
            else:
                self.logger.error("机械臂控制器不支持rob_moveto方法")
                return False
            
            if success:
                self.system_status = SystemStatus.IDLE
                self.logger.info("机械臂移动完成")
                self._trigger_callback("on_task_complete", "move_robot", position)
            else:
                self.system_status = SystemStatus.ERROR
                self.last_error = "机械臂移动失败"
                self.logger.error(self.last_error)
            
            return success
            
        except Exception as e:
            self.logger.error(f"移动机械臂时发生错误: {e}")
            self.system_status = SystemStatus.ERROR
            self.last_error = str(e)
            return False
    
    def move_ext_to_position(self, position: List[float], velocity: float = None, 
                           acceleration: float = None) -> bool:
        """
        移动外部轴到指定位置
        
        Args:
            position: 外部轴位置 [x, y, z, r]
            velocity: 移动速度，可选
            acceleration: 加速度，可选
            
        Returns:
            bool: 操作是否成功
        """
        if not self._system_initialized:
            self.logger.error("系统未初始化，无法移动外部轴")
            return False
        
        try:
            self.logger.info(f"移动外部轴到位置: {position}")
            self.system_status = SystemStatus.EXT_OPERATING
            
            # 调用外部轴控制器的移动方法
            if hasattr(self.arm_controller, 'ext_moveto'):
                success = self.arm_controller.ext_moveto(position, vel=velocity, acc=acceleration)
            else:
                self.logger.error("外部轴控制器不支持ext_moveto方法")
                return False
            
            if success:
                self.system_status = SystemStatus.IDLE
                self.logger.info("外部轴移动完成")
                self._trigger_callback("on_task_complete", "move_ext", position)
            else:
                self.system_status = SystemStatus.ERROR
                self.last_error = "外部轴移动失败"
                self.logger.error(self.last_error)
            
            return success
            
        except Exception as e:
            self.logger.error(f"移动外部轴时发生错误: {e}")
            self.system_status = SystemStatus.ERROR
            self.last_error = str(e)
            return False
    
    def open_door(self, door_ip: str) -> Dict[str, Any]:
        """
        开门操作

        Args:
            door_ip: 门禁IP地址或门ID

        Returns:
            Dict: {
                'success': bool,
                'message': str,
                'door_ip': str,
                'timestamp': float,
                'duration': float
            }
        """
        self.logger.info(f"执行开门操作: {door_ip}")
        self.system_status = SystemStatus.DOOR_OPERATING
        start_time = time.time()

        try:
            # 调用现有的_operate_lab_door方法
            success = self._operate_lab_door(door_ip, close=False)
            duration = time.time() - start_time

            if success:
                self.system_status = SystemStatus.IDLE
                return {
                    'success': True,
                    'message': f'开门成功，耗时{duration:.2f}秒',
                    'door_ip': door_ip,
                    'timestamp': time.time(),
                    'duration': duration
                }
            else:
                self.system_status = SystemStatus.ERROR
                return {
                    'success': False,
                    'message': f'开门失败，耗时{duration:.2f}秒',
                    'door_ip': door_ip,
                    'timestamp': time.time(),
                    'duration': duration
                }

        except Exception as e:
            self.logger.error(f"开门操作异常: {e}")
            self.system_status = SystemStatus.ERROR
            return {
                'success': False,
                'message': f'开门异常: {str(e)}',
                'door_ip': door_ip,
                'timestamp': time.time(),
                'duration': time.time() - start_time
            }
    
    def close_door(self, door_ip: str) -> Dict[str, Any]:
        """
        关门操作

        Args:
            door_ip: 门禁IP地址或门ID

        Returns:
            Dict: {
                'success': bool,
                'message': str,
                'door_ip': str,
                'timestamp': float,
                'duration': float
            }
        """
        self.logger.info(f"执行关门操作: {door_ip}")
        self.system_status = SystemStatus.DOOR_OPERATING
        start_time = time.time()

        try:
            # 调用现有的_operate_lab_door方法
            success = self._operate_lab_door(door_ip, close=True)
            duration = time.time() - start_time

            if success:
                self.system_status = SystemStatus.IDLE
                return {
                    'success': True,
                    'message': f'关门成功，耗时{duration:.2f}秒',
                    'door_ip': door_ip,
                    'timestamp': time.time(),
                    'duration': duration
                }
            else:
                self.system_status = SystemStatus.ERROR
                return {
                    'success': False,
                    'message': f'关门失败，耗时{duration:.2f}秒',
                    'door_ip': door_ip,
                    'timestamp': time.time(),
                    'duration': duration
                }

        except Exception as e:
            self.logger.error(f"关门操作异常: {e}")
            self.system_status = SystemStatus.ERROR
            return {
                'success': False,
                'message': f'关门异常: {str(e)}',
                'door_ip': door_ip,
                'timestamp': time.time(),
                'duration': time.time() - start_time
            }
    
    def _operate_lab_door(self, door_id: str, close: bool = False) -> bool:
        """
        操作实验室门的示例方法
        
        Args:
            door_id: 门ID
            close: 是否为关门操作
            
        Returns:
            bool: 操作是否成功
        """
        operation = "关门" if close else "开门"
        self.logger.info(f"开始{operation}实验室门: {door_id}")
        
        try:
            # 示例步骤：
            # 1. 移动到门把手位置
            # 2. 操作门把手
            # 3. 推/拉门
            # 4. 返回安全位置
            
            # 这里只是示例，实际需要根据机械臂的运动学参数和门的位置来规划
            time.sleep(1)  # 模拟操作时间
            
            # 模拟90%的成功率
            import random
            success = random.random() > 0.1
            
            if success:
                self.logger.info(f"实验室门{operation}成功: {door_id}")
            else:
                self.logger.warning(f"实验室门{operation}失败: {door_id}")
            
            return success
            
        except Exception as e:
            self.logger.error(f"操作实验室门时发生错误: {e}")
            return False
    
    def charge(self) -> bool:
        """
        充电操作

        Returns:
            bool: 操作是否成功
        """
        self.logger.info(f"开始充电")
        self.system_status = SystemStatus.CHARGING

        try:
            # 移动到充电桩（如果还没在充电位置）
            if self.current_marker != "charge_point_1F_6010":
                move_success = self.move_to_marker("charge_point_1F_6010")
                if not move_success:
                    self.logger.error("无法移动到充电桩")
                    return False

            return True

        except Exception as e:
            self.logger.error(f"充电时发生错误: {e}")
            self.system_status = SystemStatus.ERROR
            self.last_error = str(e)
            return False

    def joy_control(self, data_json: Dict[str, Any]) -> bool:
        """
        摇杆控制 - 通过AGV控制器发送速度命令

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

            success = self.agv_controller.agv_joy_control(
                linear_velocity,
                angular_velocity
            )
            if success:
                self.logger.info("摇杆控制命令发送成功")
                return True
            else:
                self.logger.warning("摇杆控制命令发送失败")
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
            if self.agv_controller.agv_set_point_as_marker(marker_id):
                return True
            else:
                self.logger.warning(f"设置标记点失败: {marker_id}")
                return False    
        except Exception as e:
            self.logger.error(f"设置标记点失败: {e}")
            return False
        
    def position_adjust(self,marker_id: str) -> bool:
        """
        位置调整 - 移动到指定标记点

        Args:
            marker_id: 目标标记点ID

        Returns:
            bool: 操作是否成功
        """
        try:
            self.logger.info(f"开始位置调整 - 目标标记点: {marker_id}")
            if self.agv_controller.agv_position_adjust(marker_id):
                return True
            else:
                self.logger.warning(f"位置调整失败: {marker_id}")
                return False    
        except Exception as e:
            self.logger.error(f"位置调整失败: {e}")
            return False

    def capture(self, device_id: str) -> Dict[str, Any]:
        """
        拍照操作 - 采集图像并返回base64编码数据

        Args:
            device_id: 设备ID

        Returns:
            Dict: {
                'success': bool,
                'images': List[str],  # base64图像列表
                'message': str,
                'device_id': str,
                'timestamp': float,
                'duration': float
            }
        """
        try:
            self.logger.info(f"执行拍照操作 - 设备ID: {device_id}")
            start_time = time.time()

            # TODO: 实现实际的相机采集逻辑
            # 方案1: OpenCV采集USB相机
            # 方案2: HTTP请求网络相机
            # 方案3: 调用机械臂相机SDK

            # 示例：模拟拍照
            import base64
            mock_image_data = b"mock_image_bytes"
            img_base64 = base64.b64encode(mock_image_data).decode('utf-8')

            duration = time.time() - start_time

            return {
                'success': True,
                'images': [img_base64],
                'message': f'拍照成功，耗时{duration:.2f}秒',
                'device_id': device_id,
                'timestamp': time.time(),
                'duration': duration
            }

        except Exception as e:
            self.logger.error(f"拍照失败: {e}")
            return {
                'success': False,
                'images': [],
                'message': f'拍照失败: {str(e)}',
                'device_id': device_id,
                'timestamp': time.time(),
                'duration': 0.0
            }

    def serve(self, device_id: str) -> Dict[str, Any]:
        """
        服务操作（如仪表读数、设备检查等）

        Args:
            device_id: 设备ID

        Returns:
            Dict: {
                'success': bool,
                'message': str,
                'device_id': str,
                'data': Dict,  # 服务数据
                'timestamp': float,
                'duration': float
            }
        """
        try:
            self.logger.info(f"执行服务操作: {device_id}")
            start_time = time.time()

            # TODO: 实现具体的服务逻辑
            # 例如：仪表读数、设备状态检查等
            service_data = {
                'device_status': 'normal',
                'reading_value': 0.0
            }

            duration = time.time() - start_time

            return {
                'success': True,
                'message': f'服务操作完成，耗时{duration:.2f}秒',
                'device_id': device_id,
                'data': service_data,
                'timestamp': time.time(),
                'duration': duration
            }

        except Exception as e:
            self.logger.error(f"服务操作失败: {e}")
            return {
                'success': False,
                'message': f'服务操作失败: {str(e)}',
                'device_id': device_id,
                'data': {},
                'timestamp': time.time(),
                'duration': time.time() - start_time
            }

    def get_environment_data(self) -> Dict[str, float]:
        """
        获取环境数据（温度、湿度等）
        从独立线程缓存中读取最新的传感器数据

        Returns:
            Dict[str, float]: 环境数据字典
        """
        try:
            # 使用线程锁保护数据读取
            with self._env_data_lock:
                # 返回数据的副本，避免外部修改
                return self._env_data.copy()

        except Exception as e:
            self.logger.error(f"获取环境数据失败: {e}")
            return {
                "temperature": 0.0,
                "humidity": 0.0,
                "oxygen": 0.0,
                "carbon_dioxide": 0.0,
                "pm25": 0.0,
                "pm10": 0.0,
                "etvoc": 0.0,
                "noise": 0.0
            }

    def register_callback(self, event: str, callback: Callable):
        """
        注册回调函数
        
        Args:
            event: 事件名称
            callback: 回调函数
        """
        if event in self.callbacks:
            self.callbacks[event].append(callback)
            self.logger.debug(f"已注册回调函数到事件: {event}")
        else:
            self.logger.warning(f"未知事件类型: {event}")
    
    def _trigger_callback(self, event: str, *args, **kwargs):
        """
        触发回调函数
        
        Args:
            event: 事件名称
            *args: 位置参数
            **kwargs: 关键字参数
        """
        for callback in self.callbacks.get(event, []):
            try:
                callback(*args, **kwargs)
            except Exception as e:
                self.logger.error(f"回调函数执行异常: {e}")
    
    def start_agv_data_monitoring(self, callback: Callable = None) -> bool:
        """
        启动AGV数据监控
        数据由AGVController内部管理，可通过 agv_get_status() 获取

        Args:
            callback: 可选的用户自定义回调函数

        Returns:
            bool: 启动是否成功
        """
        try:
            success = self.agv_controller.agv_request_data(callback)

            if success:
                self.logger.info("AGV数据监控已启动")
            else:
                self.logger.error("AGV数据监控启动失败")

            return success

        except Exception as e:
            self.logger.error(f"启动AGV数据监控失败: {e}")
            return False
    
    def stop_agv_data_monitoring(self) -> bool:
        """
        停止AGV数据监控

        Returns:
            bool: 停止是否成功
        """
        try:
            if hasattr(self.agv_controller, 'agv_stop_data'):
                success = self.agv_controller.agv_stop_data()

                if success:
                    self.logger.info("AGV数据监控已停止")
                else:
                    self.logger.warning("AGV数据监控停止失败")

                return success
            else:
                self.logger.warning("AGV控制器不支持停止数据监控")
                return False

        except Exception as e:
            self.logger.error(f"停止AGV数据监控失败: {e}")
            return False

    # ==================== 环境传感器相关方法 ====================
    def _setup_environment_sensor(self) -> bool:
        """
        初始化环境传感器

        Returns:
            bool: 初始化是否成功
        """
        if not self.env_sensor_enabled:
            self.logger.info("环境传感器未启用")
            return True

        if not ENV_SENSOR_AVAILABLE:
            self.logger.warning("环境传感器模块不可用，请检查 envsMonitor/demo.py 是否存在")
            return False

        try:
            # 获取传感器配置
            port = self.env_sensor_config.get('port', 'COM4')
            baudrate = self.env_sensor_config.get('baudrate', 4800)
            address = self.env_sensor_config.get('address', 0x01)
            read_interval = self.env_sensor_config.get('read_interval', 5)  # 默认5秒读取一次

            self.logger.info(f"初始化环境传感器 - 端口: {port}, 波特率: {baudrate}, 地址: {address:#x}")

            # 创建传感器实例
            self.env_sensor = AirQualitySensor(
                port=port,
                baudrate=baudrate,
                address=address
            )

            # 连接传感器
            if not self.env_sensor.connect():
                self.logger.error("环境传感器连接失败")
                self.env_sensor = None
                return False

            # 启动监控线程
            self._stop_env_monitor = False
            self._env_monitor_thread = threading.Thread(
                target=self._environment_monitor_loop,
                args=(read_interval,),
                daemon=True,
                name="EnvSensorMonitor"
            )
            self._env_monitor_thread.start()

            self.logger.info("环境传感器初始化成功")
            return True

        except Exception as e:
            self.logger.error(f"初始化环境传感器失败: {e}")
            if self.env_sensor:
                try:
                    self.env_sensor.disconnect()
                except:
                    pass
                self.env_sensor = None
            return False

    def _shutdown_environment_sensor(self):
        """关闭环境传感器"""
        try:
            # 停止监控线程
            if self._env_monitor_thread and self._env_monitor_thread.is_alive():
                self.logger.info("正在停止环境传感器监控线程...")
                self._stop_env_monitor = True
                self._env_monitor_thread.join(timeout=3.0)

                if self._env_monitor_thread.is_alive():
                    self.logger.warning("环境传感器监控线程未能正常停止")
                else:
                    self.logger.info("环境传感器监控线程已停止")

            # 断开传感器连接
            if self.env_sensor:
                self.logger.info("正在断开环境传感器连接...")
                self.env_sensor.disconnect()
                self.env_sensor = None
                self.logger.info("环境传感器已断开")

        except Exception as e:
            self.logger.error(f"关闭环境传感器时发生错误: {e}")

    def _environment_monitor_loop(self, read_interval: float):
        """
        环境传感器监控循环（独立线程）

        Args:
            read_interval: 读取间隔（秒）
        """
        self.logger.info(f"环境传感器监控线程已启动，读取间隔: {read_interval}秒")

        consecutive_failures = 0
        max_consecutive_failures = 5

        while not self._stop_env_monitor:
            try:
                if not self.env_sensor:
                    self.logger.warning("环境传感器未初始化，监控线程退出")
                    break

                # 读取所有传感器数据
                raw_data = self.env_sensor.read_all_parameters()

                # 检查是否有有效数据
                valid_data_count = sum(1 for v in raw_data.values() if v is not None)

                if valid_data_count > 0:
                    # 重置失败计数
                    consecutive_failures = 0

                    # 更新缓存数据（使用线程锁）
                    with self._env_data_lock:
                        # 映射字段名（传感器使用的名称 -> 系统使用的名称）
                        self._env_data['temperature'] = raw_data.get('temperature') or self._env_data['temperature']
                        self._env_data['humidity'] = raw_data.get('humidity') or self._env_data['humidity']
                        self._env_data['oxygen'] = raw_data.get('oxygen') or self._env_data['oxygen']
                        self._env_data['carbon_dioxide'] = raw_data.get('co2') or self._env_data['carbon_dioxide']
                        self._env_data['pm25'] = raw_data.get('pm25') or self._env_data['pm25']
                        self._env_data['pm10'] = raw_data.get('pm10') or self._env_data['pm10']
                        self._env_data['etvoc'] = raw_data.get('tvoc') or self._env_data['etvoc']
                        self._env_data['noise'] = raw_data.get('noise') or self._env_data['noise']

                    self.logger.debug(
                        f"环境数据更新 - 温度: {self._env_data['temperature']:.1f}°C, "
                        f"湿度: {self._env_data['humidity']:.1f}%, "
                        f"PM2.5: {self._env_data['pm25']}, "
                        f"CO2: {self._env_data['carbon_dioxide']}"
                    )
                else:
                    consecutive_failures += 1
                    self.logger.warning(
                        f"环境传感器读取失败 (连续失败: {consecutive_failures}/{max_consecutive_failures})"
                    )

                    # 连续失败太多次，记录错误
                    if consecutive_failures >= max_consecutive_failures:
                        self.logger.error(
                            f"环境传感器连续失败{max_consecutive_failures}次，可能存在连接问题"
                        )
                        # 可以选择在这里尝试重连或发送告警

                # 等待下次读取
                time.sleep(read_interval)

            except Exception as e:
                self.logger.error(f"环境传感器监控循环异常: {e}")
                consecutive_failures += 1
                time.sleep(read_interval)

        self.logger.info("环境传感器监控线程已退出")

    def __del__(self):
        """析构函数，确保资源被正确释放"""
        try:
            self.shutdown_system()
        except Exception:
            pass  # 忽略析构函数中的错误