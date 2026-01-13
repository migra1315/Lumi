from doctest import FAIL_FAST
import time
import json
import socket
import threading
from utils.logger_config import get_logger
import uuid

class AGVController:
    """AGV集成控制系统类
    
    负责与AGV进行通信和控制，提供AGV的各种操作接口
    包括状态获取、移动控制、急停、重定位等功能
    """
    def __init__(self, system_config=None, debug=False):
        """
        初始化AGV集成控制系统
        
        :param system_config: 系统配置字典，包含AGV连接信息等
        :param debug: 是否启用调试模式
        """
        # 配置日志
        self.logger = get_logger(__name__)

        # AGV控制相关配置
        self.agv_ip = system_config.get("agv_ip")      # AGV IP地址
        self.agv_port = system_config.get("agv_port")  # AGV端口

        # AGV持续数据接收相关
        self.agv_data_thread = None          # 数据接收线程
        self.agv_data_running = False        # 数据接收运行标志
        self.agv_data_callback = None        # 数据接收回调函数
        self.agv_data_topics = []            # 订阅的数据源列表

        # AGV状态缓存相关
        self._status_lock = threading.Lock()  # 线程锁，保护状态数据
        self._cached_status = None            # 缓存的AGV状态数据
        self._monitoring_active = False       # 监控线程是否激活

    # ===========================
    # AGV控制功能
    # ===========================
    
    def _send_command_to_agv(self, command):
        """
        向AGV发送带有uuid的命令并接收响应
        
        :param command: 要发送的命令
        :return: JSON格式的响应数据,失败返回None
        """
        if not self.agv_ip or not self.agv_port:
            self.logger.error("AGV连接信息未配置")
            return None
            
        try:
            # 创建TCP/IP套接字并连接到服务器
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.connect((self.agv_ip, self.agv_port))
                _uuid = uuid.uuid4().hex
                
                # 构建带UUID的命令
                if '?' in command:
                    to_send_command = f"{command}&uuid={_uuid}"
                else:
                    to_send_command = f"{command}?uuid={_uuid}"
                
                # 发送命令并记录日志
                sock.sendall(to_send_command.encode('utf-8'))
                if not command.startswith('/api/robot_status'):
                    self.logger.info(to_send_command)
                else:
                    self.logger.debug(to_send_command)
                
                # 等待接收响应
                receiveResponse = False
                while not receiveResponse:
                    response = sock.recv(4096).decode('utf-8')
                    response_json = json.loads(response)
                    self.logger.debug(f'发送控制指令后响应: {response_json}')
                    if response_json['type']=='response' and response_json['uuid']==_uuid:
                        receiveResponse = True
                
                return response_json
        except Exception as e:
            self.logger.error("发送AGV命令时发生错误: %s", e)
            return None
    
    def agv_get_status(self):
        """
        获取AGV状态
        优先从缓存中读取（如果监控已启动），否则发送网络请求

        :return: AGV状态信息，失败返回None
        """
        # 如果监控线程已激活，从缓存中读取
        if self._monitoring_active:
            with self._status_lock:
                return self._cached_status

        # 否则发送网络请求获取
        response = self._send_command_to_agv("/api/robot_status")
        return response

    def agv_moveto(self, point_name):
        """
        控制AGV移动到指定标记点
        
        :param point_name: 目标点位的标记号
        :return: 成功返回True，失败返回False
        """
        # 发送移动命令
        task_completed = False
        while not task_completed:
            response = self._send_command_to_agv(f"/api/move?marker={point_name}")
            self.logger.debug(json.dumps(response, indent=4))
            if not response:
                self.logger.error("发送AGV移动命令失败")
                return False
            status = response.get('status', None)
            if status=='OK':
                task_completed = True
            elif status=='BUSY_NOW':
                self.logger.error("AGV当前正在执行其他任务，无法移动")
                self.agv_cancel_task()
            else:
                self.logger.error(f"AGV移动到标记点 {point_name} 失败，状态: {status}")
                return False
        self.logger.info(f"AGV开始移动到标记点 {point_name}")

        # 等待移动完成
        is_done = False
        while not is_done:
            time.sleep(0.5)
            try:
                response = self._send_command_to_agv("/api/robot_status")
                '''
                TODO: 文档中move_target表示移动指令指定的点位名称。
                当以”location”调用移动接口时, 此字段值为空
                当调用巡游接口时，此字段为当前正在前往的点位名称
                '''
                if response and response.get('results', {}).get('move_status') == "succeeded":
                    is_done = True
                    self.logger.info(f"AGV已到达标记点 {point_name}")

            except Exception as e:
                # TODO: 输出其他状态的idle/suceeded/failed/canceld对应响应
                self.logger.error(f"检查AGV状态时发生错误: {e},返回内容{response.get('results', {}).get('move_status')}")
                return False
                
        return True

    def agv_joy_control(self,angular_velocity,linear_velocity):
        """
        控制AGV通过摇杆指令移动
        
        :param angular_velocity: 角速度指令
        :param linear_velocity: 线速度指令
        :return: 成功返回True,失败返回False
        """
        # 检查参数是否为浮点数
        try:
            angular_velocity = float(angular_velocity)
            linear_velocity = float(linear_velocity)
            angular_velocity = 1.0 if angular_velocity>1.0 else angular_velocity
            linear_velocity = 0.5 if linear_velocity>0.5 else linear_velocity
            angular_velocity = -1.0 if angular_velocity<-1.0 else angular_velocity
            linear_velocity = -0.5 if linear_velocity<-0.5 else linear_velocity

        except ValueError:
            self.logger.error("角速度和线速度指令必须为浮点数")
            return False

        command = f"/api/joy_control?angular_velocity={angular_velocity}&linear_velocity={linear_velocity}"
        response = self._send_command_to_agv(command)
        if not response:
            self.logger.error("发送AGV摇杆控制指令失败")
            return False
        self.logger.debug(json.dumps(response, indent=4))
        '''
        成功设置时返回
            {
            "type": "response",
            "command": "/api/joy_control",
            "uuid": "",
            "status": "OK",
            "error_message": ""
            }
        '''
        if response and response['status']=='OK':
            self.logger.info(f"AGV已成功通过摇杆控制指令移动")
            return True
        else:
            self.logger.error(f"AGV通过摇杆控制指令移动失败")
            return False

    def agv_set_point_as_marker(self, point_name, type=0, num=1):
        """
        在机器人的当前位置和楼层标记锚点
        
        :param point_name: 目标点位的标记号
        :param type: 标记类型，默认0
        :param num: 标记数量，默认1
        :return: 成功返回True,失败返回False
        """
        try:
            response = self._send_command_to_agv(f"/api/markers/insert?name={point_name}&type={type}&num={num}")
            if not response:
                self.logger.error("发送设置marker指令失败")
                return False
            self.logger.debug(json.dumps(response, indent=4))
            '''
            成功设置时返回
                {
                "type": "response",
                "command": "/api/markers/insert",
                "uuid": "",
                "status": "OK",
                "error_message": ""
                }
            '''
            if response and response['status']=='OK':
                print(f"AGV已成功设置marker点 {point_name}")
                return True
            else:
                print(f"AGV设置marker点 {point_name}失败")
                return False

        except Exception as e:
            print(f"AGV设置marker点时发生错误: {e}")
            return False

    def agv_cancel_task(self):
        """
        判断当前是否有移动任务，若有则取消当前的移动任务
        
        :return: 成功返回True，失败返回False
        """
        try:
            # 首先判断是否位于移动状态
            status_response = self.agv_get_status()
        except Exception as e:
            self.logger.error("AGV获取状态时发生错误: %s", e)
            return False
        
        # 若有移动任务，发送取消指令
        if status_response['results']['move_status'] == 'running':
            self.logger.info("当前AGV正在移动中")
            # 发送取消移动指令
            try:
                response = self._send_command_to_agv("/api/move/cancel")
                self.logger.debug("任务取消：%s", json.dumps(response, indent=4))
                if response and response['status'] == 'OK':
                    self.logger.info("已成功取消当前移动任务")
                    return True
                else:
                    self.logger.error("取消移动任务失败")
                    return False
            except Exception as e:
                self.logger.error("AGV获取状态时发生错误: %s", e)
                return False
        else:
            self.logger.info("当前AGV未在移动中")
            return True

    def agv_estop(self):
        """
        使AGV进入急停模式
        
        :return: 成功返回True，失败返回False
        """
        # TODO：增加当前状态判断
        try:
            response = self._send_command_to_agv("/api/estop?flag=true")
            if response and response['status'] == 'OK':
                self.logger.info("已成功急停")
                return True
            else:
                self.logger.error("急停失败")
                return False
        except Exception as e:
            self.logger.error("AGV急停时发生错误: %s", e)
            return False

    def agv_estop_release(self):
        """
        使AGV退出急停模式
        
        :return: 成功返回True，失败返回False
        """
        # TODO：增加当前状态判断
        # TODO：执行前，端侧或后台需提示推行至充电桩，并重新定位
        try:
            response = self._send_command_to_agv("/api/estop?flag=false")
            if response and response['status'] == 'OK':
                self.logger.info("已成功取消急停")
                return True
            else:
                self.logger.error("取消急停失败")
                return False
        except Exception as e:
            self.logger.error("AGV取消急停时发生错误: %s", e)
            return False

    # TODO: 待测试
    def agv_position_adjust(self, current_point_name=''):
        """
        重定位AGV位置
        
        :param current_point_name: 当前位置标记点名称，默认'充电桩名'
        :return: 成功返回True，失败返回False
        """
        # TODO：增加当前状态判断
        # TODO：执行前，端侧或后台需提示推行至充电桩，并重新定位
        try:
            response = self._send_command_to_agv(f"/api/position_adjust?marker={current_point_name}")
            if response and response['status'] == 'OK':
                self.logger.info("已成功重定位机器人位置")
                return True
            else:
                self.logger.error("重定位机器人位置失败")
                return False
        except Exception as e:
            self.logger.error("AGV重定位时发生错误: %s", e)
            return False

    def agv_request_data(self, callback=None):
        """
        请求AGV服务器以一定频率发送指定topic类型的数据
        数据会自动更新到内部缓存中，并可选择性调用用户提供的回调函数

        :param callback: 可选的用户回调函数，接收一个参数：data（JSON格式的AGV数据）
        :return: 成功返回True，失败返回False
        """
        if not self.agv_ip or not self.agv_port:
            self.logger.error("AGV连接信息未配置")
            return False

        # 如果已经在接收数据，先停止
        if self.agv_data_running:
            self.logger.warning("AGV数据接收已经在运行，先停止")
            self.agv_stop_data()

        self.agv_data_callback = callback
        self.agv_data_running = True
        self._monitoring_active = True

        # 创建并启动接收数据的线程
        self.agv_data_thread = threading.Thread(target=self._receive_agv_data, daemon=True)
        self.agv_data_thread.start()

        return True
   
    def _receive_agv_data(self):
        """
        持续接收AGV数据的线程函数
        """
        try:
            # 创建新的socket连接用于持续接收数据
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.connect((self.agv_ip, self.agv_port))
                # 设置socket超时，以便定期检查是否需要停止
                sock.settimeout(2)
                
                _uuid = uuid.uuid4().hex
                to_send_command = f'/api/request_data?topic=robot_status&frequency=1&uuid={_uuid}'
                
                # 发送命令
                sock.sendall(to_send_command.encode('utf-8'))
                self.logger.info(to_send_command)
                
                # 等待接收响应
                receiveResponse = False
                while not receiveResponse:
                    response = sock.recv(4096).decode('utf-8')
                    response_json = json.loads(response)
                    self.logger.debug('发送控制指令后响应: %s', response_json)
                    if response_json['type'] == 'response' and response_json['uuid'] == _uuid:
                        receiveResponse = True
                
                self.logger.info("开始接收AGV数据")
                while self.agv_data_running:
                    try:
                        # 接收数据
                        response = sock.recv(4096).decode('utf-8')
                        if not response:
                            self.logger.debug("AGV数据为空，跳过")
                            continue

                        # 解析JSON响应
                        response_json = json.loads(response)
                        if response_json.get('type') != 'callback':
                            continue

                        # 更新缓存数据（使用线程锁保护）
                        with self._status_lock:
                            self._cached_status = response_json

                        # 如果用户提供了自定义callback，也调用它
                        if self.agv_data_callback and callable(self.agv_data_callback):
                            self.agv_data_callback(response_json)

                    except socket.timeout:
                        # 超时只是为了检查是否需要停止，不是错误
                        self.logger.debug("AGV数据接收超时，继续等待")
                        continue
                    except json.JSONDecodeError as e:
                        self.logger.error("解析AGV数据时发生JSON错误: %s", e)
                    except Exception as e:
                        self.logger.error("接收AGV数据时发生错误: %s", e)
                        break
                
            self.logger.info("停止接收AGV数据")
        except Exception as e:
            self.logger.error("AGV数据接收线程发生错误: %s", e)

    def agv_stop_data(self):
        """
        停止接收AGV数据

        :return: 成功返回True，失败返回False
        """
        if not self.agv_data_running:
            self.logger.info("AGV数据接收未在运行")
            return True

        # 设置标志位停止线程
        self.agv_data_running = False

        # 等待线程结束
        if self.agv_data_thread and self.agv_data_thread.is_alive():
            self.agv_data_thread.join(timeout=3)

        if self.agv_data_thread and self.agv_data_thread.is_alive():
            self.logger.warning("AGV数据接收线程未能正常结束")
            return False

        # 重置状态
        self.agv_data_thread = None
        self.agv_data_topics = []
        self.agv_data_callback = None
        self.agv_data_running = False
        self._monitoring_active = False

        # 可选：清空缓存数据
        with self._status_lock:
            self._cached_status = None

        self.logger.info("AGV数据接收已停止")
        return True