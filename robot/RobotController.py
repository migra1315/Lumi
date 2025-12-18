
"""
RobotController.py
机器人控制器主类，整合AGV、机械臂和外部轴的控制
"""

import time
import logging
from typing import Dict, List, Any, Optional, Callable
from enum import Enum
from robot.AGVController import AGVController
from robot.ArmController import ArmController

class RobotStatus(Enum):
    """机器人状态枚举"""
    IDLE = "idle"
    MOVING = "moving"  # AGV移动中
    ARM_OPERATING = "arm_operating"  # 机械臂操作中
    EXT_OPERATING = "ext_operating"  # 外部轴操作中
    DOOR_OPERATING = "door_operating"  # 门操作中
    ERROR = "error"
    CHARGING = "charging"
    SETUP = "setup"  # 系统初始化中

class BatteryStatus(Enum):
    """电池状态枚举"""
    HIGH = "high"      # > 80%
    MEDIUM = "medium"  # 30-80%
    LOW = "low"       # 10-30%
    CRITICAL = "critical"  # < 10%

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
        self.status = RobotStatus.IDLE
        self.battery_level = 100.0  # 初始电量100%
        self.battery_status = BatteryStatus.HIGH
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
            self.logger.info("正在初始化AGV控制器...")
            self.agv_controller = AGVController(self.system_config, debug=self.debug)
            
            self.logger.info("正在初始化机械臂控制器...")
            # 注意：ArmController同时包含机械臂和外部轴控制
            self.arm_controller = ArmController(
                system_config=self.system_config,
                ext_axis_limits=self.system_config.get("ext_axis_limits"),
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
            self.status = RobotStatus.SETUP
            
            # 初始化AGV控制器
            agv_ok = True  # AGV控制器在初始化时不立即连接
            
            # 初始化机械臂和外部轴系统
            arm_ok = self.arm_controller.setup_system()
            if not arm_ok:
                self.logger.error("机械臂系统初始化失败")
                return False
        
            self._system_initialized = True
            self.status = RobotStatus.IDLE
            self.logger.info("机器人系统初始化完成")
            
            return agv_ok and arm_ok
            
        except Exception as e:
            self.logger.error(f"系统初始化失败: {e}")
            self.status = RobotStatus.ERROR
            self.last_error = str(e)
            return False
    
    def shutdown_system(self):
        """关闭机器人系统"""
        try:
            self.logger.info("正在关闭机器人系统...")
            
            # 停止AGV数据接收
            if hasattr(self.agv_controller, 'agv_stop_data'):
                self.agv_controller.agv_stop_data()
            
            # 关闭机械臂和外部轴系统
            if hasattr(self.arm_controller, 'shutdown_system'):
                self.arm_controller.shutdown_system()
            
            self._system_initialized = False
            self.status = RobotStatus.IDLE
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
            # 获取AGV状态
            agv_status = None
            if hasattr(self.agv_controller, 'agv_get_status'):
                agv_status_response = self.agv_controller.agv_get_status()
                if agv_status_response and agv_status_response.get('status') == 'OK':
                    agv_status = agv_status_response.get('results', {})
            
            # 获取外部轴状态
            ext_status = None
            if hasattr(self.arm_controller, 'ext_get_state'):
                ext_status = self.arm_controller.ext_get_state()
            

            # 构建状态字典
            status_dict = {
                "status": self.status.value,
                "battery_level": self.battery_level,
                "battery_status": self.battery_status.value,
                "current_marker": self.current_marker,
                "last_error": self.last_error,
                "system_initialized": self._system_initialized,
                "agv_status": agv_status,
                "external_axis_status": ext_status
            }
            
            return status_dict
            
        except Exception as e:
            self.logger.error(f"获取状态失败: {e}")
            return {
                "status": self.status.value,
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
                self.status = RobotStatus.IDLE
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
                self.status = RobotStatus.IDLE
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
            self.status = RobotStatus.MOVING
            
            # 调用AGV控制器的移动方法
            success = self.agv_controller.agv_moveto(marker_id)
            
            if success:
                self.current_marker = marker_id
                self.status = RobotStatus.IDLE
                self.logger.info(f"AGV已成功移动到标记点: {marker_id}")
                self._trigger_callback("on_task_complete", "move_agv", marker_id)
            else:
                self.status = RobotStatus.ERROR
                self.last_error = f"移动AGV到{marker_id}失败"
                self.logger.error(self.last_error)
            
            return success
            
        except Exception as e:
            self.logger.error(f"移动AGV时发生错误: {e}")
            self.status = RobotStatus.ERROR
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
            self.status = RobotStatus.ARM_OPERATING
            
            # 调用机械臂控制器的移动方法
            if hasattr(self.arm_controller, 'rob_moveto'):
                result = self.arm_controller.rob_moveto(position, vel=velocity)
                # TODO：重新确认函数返回值含义
                success = (result is not None)  # 根据实际返回值判断
            else:
                self.logger.error("机械臂控制器不支持rob_moveto方法")
                return False
            
            if success:
                self.status = RobotStatus.IDLE
                self.logger.info("机械臂移动完成")
                self._trigger_callback("on_task_complete", "move_robot", position)
            else:
                self.status = RobotStatus.ERROR
                self.last_error = "机械臂移动失败"
                self.logger.error(self.last_error)
            
            return success
            
        except Exception as e:
            self.logger.error(f"移动机械臂时发生错误: {e}")
            self.status = RobotStatus.ERROR
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
            self.status = RobotStatus.EXT_OPERATING
            
            # 调用外部轴控制器的移动方法
            if hasattr(self.arm_controller, 'ext_moveto'):
                success = self.arm_controller.ext_moveto(position, vel=velocity, acc=acceleration)
            else:
                self.logger.error("外部轴控制器不支持ext_moveto方法")
                return False
            
            if success:
                self.status = RobotStatus.IDLE
                self.logger.info("外部轴移动完成")
                self._trigger_callback("on_task_complete", "move_ext", position)
            else:
                self.status = RobotStatus.ERROR
                self.last_error = "外部轴移动失败"
                self.logger.error(self.last_error)
            
            return success
            
        except Exception as e:
            self.logger.error(f"移动外部轴时发生错误: {e}")
            self.status = RobotStatus.ERROR
            self.last_error = str(e)
            return False
    
    def open_door(self, door_id: str) -> bool:
        """
        开门操作（需要根据实际硬件实现）
        
        Args:
            door_id: 门ID
            
        Returns:
            bool: 操作是否成功
        """
        # 这里需要根据实际的开门机制实现
        # 可能是通过机械臂操作门把手，或者发送信号给门禁系统
        
        self.logger.info(f"执行开门操作: {door_id}")
        self.status = RobotStatus.DOOR_OPERATING
        
        try:
            # TODO: 实现具体的开门逻辑
            # 示例：通过机械臂操作门把手
            if door_id == "door1":
                # 假设这是一个实验室门，需要先移动到特定位置
                success = self._operate_lab_door(door_id)
            else:
                self.logger.warning(f"未知的门ID: {door_id}")
                success = False
            
            if success:
                self.status = RobotStatus.IDLE
                self.logger.info(f"开门操作完成: {door_id}")
                self._trigger_callback("on_task_complete", "open_door", door_id)
            else:
                self.status = RobotStatus.ERROR
                self.last_error = f"开门操作失败: {door_id}"
                self.logger.error(self.last_error)
            
            return success
            
        except Exception as e:
            self.logger.error(f"开门操作时发生错误: {e}")
            self.status = RobotStatus.ERROR
            self.last_error = str(e)
            return False
    
    def close_door(self, door_id: str) -> bool:
        """
        关门操作（需要根据实际硬件实现）
        
        Args:
            door_id: 门ID
            
        Returns:
            bool: 操作是否成功
        """
        self.logger.info(f"执行关门操作: {door_id}")
        self.status = RobotStatus.DOOR_OPERATING
        
        try:
            # TODO: 实现具体的关门逻辑
            # 示例：通过机械臂操作门把手
            if door_id == "door1":
                # 假设这是一个实验室门，需要先移动到特定位置
                success = self._operate_lab_door(door_id, close=True)
            else:
                self.logger.warning(f"未知的门ID: {door_id}")
                success = False
            
            if success:
                self.status = RobotStatus.IDLE
                self.logger.info(f"关门操作完成: {door_id}")
                self._trigger_callback("on_task_complete", "close_door", door_id)
            else:
                self.status = RobotStatus.ERROR
                self.last_error = f"关门操作失败: {door_id}"
                self.logger.error(self.last_error)
            
            return success
            
        except Exception as e:
            self.logger.error(f"关门操作时发生错误: {e}")
            self.status = RobotStatus.ERROR
            self.last_error = str(e)
            return False
    
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
        self.status = RobotStatus.CHARGING
        
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
            self.status = RobotStatus.ERROR
            self.last_error = str(e)
            return False
    
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
        
        Args:
            callback: 数据接收回调函数
            
        Returns:
            bool: 启动是否成功
        """
        try:
            if hasattr(self.agv_controller, 'agv_request_data'):
                if callback:
                    return self.agv_controller.agv_request_data(callback)
                else:
                    # 使用默认回调函数
                    def default_callback(data):
                        self.logger.debug(f"收到AGV数据: {data}")
                        # 这里可以解析数据并更新状态
                    
                    return self.agv_controller.agv_request_data(default_callback)
            else:
                self.logger.warning("AGV控制器不支持数据监控")
                return False
                
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
                return self.agv_controller.agv_stop_data()
            else:
                self.logger.warning("AGV控制器不支持停止数据监控")
                return False
                
        except Exception as e:
            self.logger.error(f"停止AGV数据监控失败: {e}")
            return False
    

    def __del__(self):
        """析构函数，确保资源被正确释放"""
        try:
            self.shutdown_system()
        except Exception:
            pass  # 忽略析构函数中的错误