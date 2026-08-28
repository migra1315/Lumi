# coding:UTF-8
"""JAKA Integrated Control System
集成JAKA机器人、外部轴和AGV的控制功能
"""
import json
import math
import time

import requests

from robot.jaka import JAKA
from robot.HardwareErrors import ControlledStopError
from utils.logger_config import get_logger
from utils.voice_player import VoicePlayer


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
        system_config = system_config or {}

        # 配置日志
        self.logger = get_logger(__name__)
        

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
        self.ext_request_timeout = (
            float(system_config.get("ext_connect_timeout_seconds", 3)),
            float(system_config.get("ext_read_timeout_seconds", 30)),
        )
        self.ext_position_tolerance = float(
            system_config.get("ext_position_tolerance", 0.5)
        )
        self.arm_motion_timeout = float(
            system_config.get("arm_motion_timeout_seconds", 120)
        )
        self.arm_stop_timeout = float(
            system_config.get("arm_stop_timeout_seconds", 5)
        )
        self.arm_poll_interval = float(
            system_config.get("arm_status_poll_interval_seconds", 0.1)
        )
        self.arm_joint_tolerance = float(
            system_config.get("arm_joint_tolerance", 0.01)
        )
        
        # 加载外部轴关节限制
        self.ext_axis_limits = ext_axis_limits
        self.WELCOME_JOINTS_1 = [-5.0,50,23,0.0,36,0]
        self.WELCOME_JOINTS_2 = [5.0,70,40,0.0,36,0]
        self.WELCOME_JOINTS_3 = [-170.0,90.0,0.0,20.0,90.0,55.0]

        # 语音播报器
        self.voice_player = VoicePlayer()
    
    def _load_ext_axis_limits(self):
        """加载外部轴关节限制参数"""
        # 默认限制值
        print(f"[MOCK] 使用默认外部轴限制值")
        return {
            "joint1": {"min": 0, "max": 200, "desc": "升降，单位mm"}, 
            "joint2": {"min": -140, "max": 140, "desc": "腰部旋转，单位度"},
            "joint3": {"min": -180, "max": 180, "desc": "头部旋转，单位度"},
            "joint4": {"min": -5, "max": 35, "desc": "头部俯仰，单位度"}
        }
    
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
            response = requests.get(self.EXT_SYSINFO_URL, timeout=self.ext_request_timeout)
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
            
        try:
            response = requests.post(
                self.EXT_RESET_URL, json={}, timeout=self.ext_request_timeout
            )
        except requests.RequestException as error:
            self.logger.error(f"外部轴重置请求异常: {error}")
            return False
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
            if not current_states:
                self.logger.error("无法读取外部轴使能状态")
                return False
            
            # 检查所有外部轴是否已经处于目标状态
            all_in_target_state = True
            for state in current_states:
                self.logger.debug(f"外部轴 {state['id']} 当前状态:  {state}\n 使能状态: {state['enable']}")
                if state['enable'] != enable:
                    all_in_target_state = False
                    break
            
            if all_in_target_state:
                status_str = "使能" if enable else "禁用"
                self.logger.debug(f"外部轴已{status_str}")
                return True

            # 发送使能/禁用请求
            retry_count += 1
            status_str = "使能" if enable else "禁用"
            self.logger.warning(f"外部轴未{status_str}，尝试第{retry_count}次{status_str}")
            
            try:
                # 先重置外部轴
                self.ext_reset()
                
                # 发送使能/禁用请求
                response = requests.post(
                    self.EXT_ENABLE_URL,
                    json={"enable": 1 if enable else 0},
                    timeout=self.ext_request_timeout,
                )
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
            
        try:
            response = requests.get(
                self.EXT_GETSTATE_URL, timeout=self.ext_request_timeout
            )
            if response.status_code == 200:
                state = response.json()
                return state if isinstance(state, list) else None
            self.logger.error(f"获取外部轴状态失败: {response.status_code}")
        except (requests.RequestException, ValueError) as error:
            self.logger.error(f"获取外部轴状态异常: {error}")
        return None

    def _ext_state_result(self, states, target):
        # 返回 (状态是否可解析, 位置是否达到目标)；无法解析时必须按未知处理。
        if not isinstance(states, list) or len(states) < len(target):
            return False, False
        try:
            positions = [float(state["pos"]) for state in states[:len(target)]]
            confirmed = all(
                abs(actual - float(expected)) <= self.ext_position_tolerance
                for actual, expected in zip(positions, target)
            )
            return True, confirmed
        except (KeyError, TypeError, ValueError):
            return False, False
    
    def ext_moveto(self, point, vel=None, acc=None, cancel_event=None):
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
        if cancel_event is not None and cancel_event.is_set():
            return False

        # 取消前仍需读取一次外部轴状态；读不到状态时抛出受控停止错误并阻塞后续任务。
        # 外部轴接口没有可靠的中止命令，因此动作作为原子请求完成后再响应 cancel_event。
        # 检查外部轴使能状态
        current_states = self.ext_get_state()
        if not current_states:
            if cancel_event is not None and cancel_event.is_set():
                raise ControlledStopError("取消期间无法确认外部轴状态")
            return False
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
        response = None
        try:
            response = requests.post(
                self.EXT_MOVETO_URL,
                json={"pos": point, "vel": vel, "acc": acc},
                timeout=self.ext_request_timeout,
            )
            self.logger.info(f'外部轴移动响应: {response}')
        except requests.RequestException as error:
            self.logger.error(f"外部轴移动请求异常: {error}")

        # 请求返回不等于运动完成；用位置容差再次确认，避免提前执行下一站点。
        state_known, confirmed = self._ext_state_result(
            self.ext_get_state(), point
        )
        if not state_known:
            raise ControlledStopError("外部轴动作返回后状态无法确认")
        if response is not None and response.status_code == 200 and confirmed:
            self.logger.info('外部轴移动成功且位置已确认')
            return True

        self.logger.error("外部轴移动失败或目标位置未确认")
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
        

        # 语音播报 + 欢迎动作（语音播放在独立线程中，不阻塞欢迎动作执行）
        # self.voice_player.play("你的实验室助手已上线.mp3")
        # time.sleep(1)
        # for i in range(2):
        #     self.rob_moveto([math.radians(angle) for angle in self.WELCOME_JOINTS_1])
        #     self.rob_moveto([math.radians(angle) for angle in self.WELCOME_JOINTS_2])
        
        # self.rob_moveto([math.radians(angle) for angle in self.WELCOME_JOINTS_3])
        
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

    def move_head(self):
        """
        控制头部移动到指定角度
        
        :param angle: 目标角度，单位为度
        :return: 成功返回True，失败返回False
        """
        ext_status_response = self.ext_get_state()
        ext_status = [ext_status_response[0].get('pos', 0.0),
                        ext_status_response[1].get('pos', 0.0),
                        ext_status_response[2].get('pos', 0.0),
                        ext_status_response[3].get('pos', 0.0) + 10,
                    ]
        self.ext_moveto(ext_status)
        ext_status[3]-=20
        self.ext_moveto(ext_status)
        
    # ===========================
    # 集成控制功能
    # ===========================
    
    def arm_get_state(self):
        """
        获取机械臂当前状态
        
        :return: 机械臂状态字典
        """
        try:
            joints = self.get_joints()
        except Exception as e:
            self.logger.error(f"获取机械臂状态失败: {e}")
            joints = [0,0,0,0,0,0]
        return joints
    
    def _arm_target_reached(self, target):
        try:
            joints = self.get_joints()
            if joints is None or len(joints) != len(target):
                raise ValueError("JAKA关节状态响应不完整")
            return all(
                abs(float(actual) - float(expected)) <= self.arm_joint_tolerance
                for actual, expected in zip(joints, target)
            )
        except ControlledStopError:
            raise
        except Exception as error:
            # 状态接口异常不能被当作“尚未到达”吞掉，否则取消时无法判断是否安全。
            raise ControlledStopError(f"JAKA运动状态无法确认: {error}") from error

    def _wait_until_arm_stopped(self, timeout=None):
        # 需要连续两个稳定采样才视为停稳，单次相同读数不足以排除通信旧值。
        deadline = time.monotonic() + (
            self.arm_stop_timeout if timeout is None else float(timeout)
        )
        previous = None
        stable_samples = 0
        while time.monotonic() < deadline:
            try:
                joints = self.get_joints()
                if joints is None or len(joints) != 6:
                    raise ValueError("JAKA关节状态响应不完整")
                joints = tuple(float(value) for value in joints)
                if previous is not None and all(
                    abs(current - old) <= self.arm_joint_tolerance
                    for current, old in zip(joints, previous)
                ):
                    stable_samples += 1
                    if stable_samples >= 2:
                        return True
                else:
                    stable_samples = 0
                previous = joints
            except Exception as error:
                self.logger.warning(f"JAKA停止确认读取状态失败: {error}")
                previous = None
                stable_samples = 0
            time.sleep(self.arm_poll_interval)
        return False

    def rob_moveto(self, jpos, vel=None, cancel_event=None):
        """
        TODO: 机械臂返回数值偶发为-1，需要检查是否为异常值
        控制机器人移动到指定关节角度(弧度)
        
        然后执行关节运动
        :param jpos: 目标关节角度 [J1, J2, J3, J4, J5, J6]，单位为弧度
        :param vel: 关节速度，默认90度/秒
        :return: 运动结果
        """

        vel = vel if vel is not None else self.DEFAULT_ROB_VEL
        if cancel_event is not None and cancel_event.is_set():
            return -1
        # self.logger.info(f"输入的关节角度(度): {jpos}")
        
        # # 将角度转换为弧度 - 使用math.radians更精确
        # joint_pos = [math.radians(angle) for angle in jpos]
        # self.logger.debug(f"转换后的关节角度(弧度): {joint_pos}")
        
        # 执行关节运动
        # 注意参数顺序: joints, sp, move_mode
        # move_mode=0 表示绝对运动模式
        self.logger.info(f"开始执行关节运动, 速度: {vel}, 模式: 绝对运动(0)")
        # 使用非阻塞 SDK 调用，保留轮询窗口以便及时响应协作取消。
        ret = self.joint_move_origin(jpos, vel, 0, is_block=False)
        if ret != 0:
            self.logger.error(f"关节运动下发失败: {ret}")
            return -1

        deadline = time.monotonic() + self.arm_motion_timeout
        while time.monotonic() < deadline:
            if cancel_event is not None and cancel_event.is_set():
                # motion_abort 仅发出停止请求；必须继续读取关节状态确认已停稳。
                if self.motion_abort_origin() != 0:
                    raise ControlledStopError("JAKA motion_abort调用失败")
                if not self._wait_until_arm_stopped():
                    raise ControlledStopError("JAKA中止后未能确认机械臂停止")
                self.logger.info("JAKA运动已中止并确认停止")
                return -1
            if self._arm_target_reached(jpos):
                self.logger.info("机械臂已确认到达目标关节位置")
                return 0
            if cancel_event is not None:
                cancel_event.wait(self.arm_poll_interval)
            else:
                time.sleep(self.arm_poll_interval)

        self.logger.error("机械臂运动超时，尝试中止")
        if self.motion_abort_origin() != 0:
            raise ControlledStopError("机械臂运动超时且motion_abort调用失败")
        if not self._wait_until_arm_stopped():
            raise ControlledStopError("机械臂运动超时后未能确认停止")
        return -1
