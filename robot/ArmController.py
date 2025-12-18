# coding:UTF-8
"""JAKA Integrated Control System
集成JAKA机器人、外部轴和AGV的控制功能
"""
from doctest import FAIL_FAST
import time
import requests
import json
import logging
from robot.jaka import JAKA


class ArmController(JAKA):
    """JAKA集成控制系统类
    
    继承自JAKA类，集成了外部轴和AGV的控制功能
    提供统一的接口来控制整个集成系统
    """
    
    # 默认设置
    DEFAULT_EXT_VEL = 100  # 外部轴默认速度
    DEFAULT_EXT_ACC = 100  # 外部轴默认加速度
    DEFAULT_ROB_VEL = 90   # 机器人默认速度 (度/秒)

    def __init__(self, system_config=None, ext_axis_limits=None, debug=False):
        """
        初始化集成控制系统
        
        :param system_config: 系统配置字典，包含机器人、外部轴和AGV的连接信息
        :param ext_axis_limits: 外部轴关节限制配置
        :param debug: 是否启用调试模式
        """
        # 配置logging
        self.logger = logging.getLogger(__name__)
        
        # 设置日志级别
        if debug:
            self.logger.setLevel(logging.DEBUG)
            print("调试模式已启用")
        else:
            self.logger.setLevel(logging.INFO)
        
        # 确保只添加一次处理器
        if not self.logger.handlers:
            console_handler = logging.StreamHandler()
            # 设置日志格式
            formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
            console_handler.setFormatter(formatter)
            # 添加处理器到logger
            self.logger.addHandler(console_handler)

        # 调用父类初始化，但不立即连接
        super().__init__(system_config["robot_ip"], connect=False)
        self.system_config = system_config  # 系统配置
        self.debug = debug                  # 调试模式标志
        
        # 外部轴控制相关URL
        self.ext_base_url = system_config.get("ext_base_url")
        if self.ext_base_url:
            self.EXT_MOVETO_URL = f"{self.ext_base_url}/moveto"    # 移动控制URL
            self.EXT_SYSINFO_URL = f"{self.ext_base_url}/sysinfo"  # 系统信息URL
            self.EXT_RESET_URL = f"{self.ext_base_url}/reset"      # 重置URL
            self.EXT_ENABLE_URL = f"{self.ext_base_url}/enable"    # 使能URL
            self.EXT_GETSTATE_URL = f"{self.ext_base_url}/status"  # 状态获取URL
        
        # 加载外部轴关节限制
        self.ext_axis_limits = ext_axis_limits
        
    def _adjust_to_joint_limits(self, point):
        """
        调整关节位置以确保在限制范围内
        
        :param point: 目标位置 [joint1, joint2, joint3, joint4]
        :return: (调整后的位置, 是否被调整, 调整信息)
        """
        # 如果没有加载关节限制，尝试加载
        if not hasattr(self, 'ext_axis_limits') or self.ext_axis_limits is None:
            self.ext_axis_limits = self._load_ext_axis_limits()
            
        adjusted = False  # 标记是否有调整
        messages = []     # 调整信息列表
        result = list(point)  # 复制输入点以进行调整
        
        joint_names = ["joint1", "joint2", "joint3", "joint4"]
        
        # 逐个关节检查并调整
        for i, (joint_name, value) in enumerate(zip(joint_names, point)):
            if joint_name in self.ext_axis_limits:
                min_val = self.ext_axis_limits[joint_name]["min"]  # 最小限制
                max_val = self.ext_axis_limits[joint_name]["max"]  # 最大限制
                desc = self.ext_axis_limits[joint_name]["desc"]    # 关节描述
                
                # 检查是否超出下限
                if value < min_val:
                    messages.append(f"{joint_name}({desc})超出最小限制: {value} < {min_val}")
                    result[i] = min_val  # 调整到最小值
                    adjusted = True
                # 检查是否超出上限
                elif value > max_val:
                    messages.append(f"{joint_name}({desc})超出最大限制: {value} > {max_val}")
                    result[i] = max_val  # 调整到最大值
                    adjusted = True
        
        # 生成调整信息
        adjustment_msg = "; ".join(messages) if messages else "无需调整"
        return result, adjusted, adjustment_msg
    
    # ===========================
    # 外部轴控制功能
    # ===========================
    
    def ext_check_connection(self):
        """
        检查外部轴连接状态
        
        :return: 连接正常返回True，否则返回False
        """
        if not self.ext_base_url:
            print("外部轴URL未配置")
            return False
        
        try:
            response = requests.get(self.EXT_SYSINFO_URL, timeout=2)
            if response.status_code == 200:
                self.logger.info("外部轴连接正常")
                return True
            else:
                self.logger.error(f"外部轴连接错误: {response.status_code}")
                return False
        except Exception as e:
            self.logger.error(f"外部轴连接异常: {e}")
            return False
    
    def ext_reset(self):
        """
        重置所有外部轴关节
        
        :return: 重置成功返回True，否则返回False
        """
        if not self.ext_base_url:
            print("外部轴URL未配置")
            return False
            
        response = requests.post(self.EXT_RESET_URL, json={})
        self.logger.debug(f"外部轴重置请求响应状态: {response}")
        if response.status_code == 200:
            self.logger.info("外部轴重置成功")
            return True
        else:
            self.logger.error(f"外部轴重置失败: {response.status_code}")
            return False
        
    def ext_enable(self, enable=True):
        """
        使能或禁用外部轴
        
        :param enable: True表示使能，False表示禁用
        :return: 操作成功返回True，否则返回False
        """
        if not self.ext_base_url:
            self.logger.error("外部轴URL未配置")
            return False
            
        max_retries = 5
        retry_count = 0
        
        while retry_count < max_retries:
            current_states = self.ext_get_state()
            
            # 检查所有外部轴是否已经处于目标状态
            all_in_target_state = True
            for state in current_states:
                self.logger.debug(f"外部轴 {state['id']} 当前状态:  {state}\n 使能状态: {state['enable']}")
                if state['enable'] != enable:
                    all_in_target_state = False
                    break
            
            if all_in_target_state:
                self.logger.info(f"外部轴已{'使能' if enable else '禁用'}")
                return True
            
            # 发送使能/禁用请求
            retry_count += 1
            self.logger.info(f"外部轴未{'使能' if enable else '禁用'}，尝试第{retry_count}次{'使能' if enable else '禁用'}")
            
            try:
                # 先重置外部轴
                self.ext_reset()
                
                # 发送使能/禁用请求
                response = requests.post(self.EXT_ENABLE_URL, json={"enable": 1 if enable else 0})
                self.logger.debug(f"外部轴{'使能' if enable else '禁用'}请求响应状态码: {response.status_code}")
                
                if response.status_code == 200:
                    response_json = response.json()
                    self.logger.debug(f"外部轴{'使能' if enable else '禁用'}请求响应内容: {response_json}")
                    
                    # 短暂延迟后再次检查状态
                    time.sleep(0.5)
                else:
                    self.logger.error(f"外部轴{'使能' if enable else '禁用'}失败，响应状态码: {response.status_code}")
                    time.sleep(1)
            except Exception as e:
                self.logger.error(f"外部轴{'使能' if enable else '禁用'}请求发生异常: {e}")
                time.sleep(1)
        
        self.logger.error(f"外部轴{'使能' if enable else '禁用'}失败，已达到最大重试次数({max_retries})")
        return False
    
    def ext_get_state(self):
        """
        获取外部轴状态
        
        :return: 成功返回状态信息，失败返回None
        """
        if not self.ext_base_url:
            print("外部轴URL未配置")
            return None
            
        response = requests.get(self.EXT_GETSTATE_URL)
        if response.status_code == 200:
            return json.loads(response.text)
        else:
            self.logger.error(f"获取外部轴状态失败: {response.status_code}")
            return None
    
    def ext_moveto(self, point, vel=None, acc=None):
        """
        控制外部轴移动到指定位置
        
        :param point: 目标位置坐标 [x, y, z, r]
        :param vel: 速度，默认100
        :param acc: 加速度，默认100
        :return: 成功返回True，失败返回False
        """
        if not self.ext_base_url:
            self.logger.error("外部轴URL未配置")
            return False
        
        # 检查外部轴使能状态
        current_states = self.ext_get_state()
        all_in_target_state = True
        for state in current_states:
            self.logger.debug(f"外部轴{state['id']}当前使能状态: {state['enable']}")
            if state['enable'] != True:
                all_in_target_state = False
                break
        
        # 如果未使能，尝试使能
        if not all_in_target_state:
            self.logger.info("外部轴未使能，尝试使能")
            if not self.ext_enable(True):
                return False
            
        # 检查关节限制并调整到限制范围内
        adjusted_point, was_adjusted, adjustment_msg = self._adjust_to_joint_limits(point)
        if was_adjusted:
            self.logger.warning(f"警告: {adjustment_msg}")
            self.logger.warning(f"原始位置: {point} -> 调整后位置: {adjusted_point}")
            point = adjusted_point
            
        vel = vel if vel is not None else self.DEFAULT_EXT_VEL
        acc = acc if acc is not None else self.DEFAULT_EXT_ACC
        self.logger.info(f'发送外部轴运动指令, 目标位置: {point}, 速度: {vel}, 加速度: {acc}')
        response = requests.post(
            self.EXT_MOVETO_URL,
            json={"pos": point, "vel": vel, "acc": acc},
        )
        self.logger.info(f'外部轴移动响应: {response}')
        # TODO:总是收到不到响应，需要检查是否超时
        if response.status_code == 200:
            self.logger.info('外部轴移动成功!')
            return True
        else:
            self.logger.error(f"外部轴移动失败: {response}")
            return False

    # ===========================
    # 集成控制功能
    # ===========================
    
    def setup_system(self):
        """
        初始化整个系统
        
        依次初始化外部轴和机器人，确保系统各部分正常工作
        :return: 成功返回True，失败返回False
        """
        # 连接机器人
        robot_ok = self.jaka_connect()

        if not robot_ok:
            self.logger.error("机器人连接失败")
            return False
        
        # 检查外部轴连接
        ext_ok = True
        if self.ext_base_url:
            ext_ok = self.ext_check_connection()
            if ext_ok:
                ext_ok = ext_ok and self.ext_reset()
                ext_ok = ext_ok and self.ext_enable(True)
                
        return robot_ok and ext_ok
    
    def shutdown_system(self):
        """
        关闭整个系统
        
        依次关闭机器人和外部轴，确保系统安全停止
        """
        # 断开机器人连接
        if self.robot:
            self.robot_disconnect()
        
        # 禁用外部轴
        if self.ext_base_url:
            self.ext_enable(False)
        
        self.logger.info("系统已关闭")

    # 扩展JAKA类的方法，使其更适用于集成控制系统
    
    def rob_moveto(self, jpos, vel=None):
        """
        TODO: 机械臂返回数值偶发为-1，需要检查是否为异常值
        控制机器人移动到指定关节角度(度数)
        
        将输入的关节角度(度数)转换为弧度，然后执行关节运动
        :param jpos: 目标关节角度 [J1, J2, J3, J4, J5, J6]，单位为度
        :param vel: 关节速度，默认90度/秒
        :return: 运动结果
        """
        import math
        
        vel = vel if vel is not None else self.DEFAULT_ROB_VEL
        self.logger.info(f"输入的关节角度(度): {jpos}")
        
        # 将角度转换为弧度 - 使用math.radians更精确
        joint_pos = [math.radians(angle) for angle in jpos]
        self.logger.debug(f"转换后的关节角度(弧度): {joint_pos}")
        
        # 执行关节运动
        # 注意参数顺序: joints, sp, move_mode
        # move_mode=0 表示绝对运动模式
        self.logger.info(f"开始执行关节运动, 速度: {vel}, 模式: 绝对运动(0)")
        ret = self.joint_move_origin(joint_pos, vel, 0)
        self.logger.info(f"关节运动结果: {ret}")
        return ret 