import threading
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime
from typing import Dict, Any, Optional

from dataModels.CommandModels import CmdType, CommandEnvelope, CancelTaskCmd
from dataModels.TaskModels import Task, Station, RobotMode, StationTaskStatus
from dataModels.UnifiedCommand import UnifiedCommand, CommandStatus, CommandCategory, create_unified_command
from task.TaskDatabase import TaskDatabase
from task.TaskScheduler import TaskScheduler
from utils.logger_config import get_logger


class CancelRequestPersistenceError(RuntimeError):
    """取消请求未能持久化终态；接收消息必须保持未处理以便重试。"""


class TaskManager:
    """任务管理器 - 主控制器，负责任务的接收、解析和调度"""

    def __init__(self, config: Dict[str, Any] = None, use_mock: bool = True,
                 auto_start_on_boot: bool = True,
                 robot_enabled: bool = True,
                 camera_enabled: bool = True,
                 env_sensor_enabled: bool = True):
        """初始化任务管理器

        Args:
            config: 系统配置字典
            use_mock: 是否使用Mock机器人控制器
            auto_start_on_boot: 是否在启动时自动启动硬件（默认True保持向后兼容）
            robot_enabled: 机器人模块是否启用
            camera_enabled: 相机模块是否启用
            env_sensor_enabled: 环境传感器模块是否启用
        """
        self.config = config or {}
        self.use_mock = use_mock
        self.auto_start_on_boot = auto_start_on_boot
        self.robot_enabled = robot_enabled
        self.camera_enabled = camera_enabled
        self.env_sensor_enabled = env_sensor_enabled
        self.logger = get_logger(__name__)

        # 硬件状态管理
        self._hardware_status = {
            "robot": False,
            "camera": False,
            "env_sensor": False
        }
        self._hardware_lock = threading.Lock()
        self._cancel_request_lock = threading.RLock()
        self._cancel_requests_inflight: Dict[str, threading.Event] = {}
        self._cancel_requests_seen = set()
        cancel_config = self.config.get("task_cancel", {})
        safe_pose_config = cancel_config.get("safe_pose", {})
        charging_config = self.config.get("charging", {})
        # 协调器只承载取消控制面请求；容量和等待超时均可配置，避免占满业务线程池。
        self._cancel_wait_timeout = float(cancel_config.get("wait_timeout_seconds", 30))
        coordinator_workers = int(cancel_config.get("coordinator_workers", 4))
        coordinator_capacity = int(cancel_config.get("coordinator_capacity", 32))
        self._cancel_coordinator = ThreadPoolExecutor(
            max_workers=coordinator_workers,
            thread_name_prefix="cancel-task",
        )
        self._cancel_coordinator_slots = threading.BoundedSemaphore(
            max(coordinator_workers, coordinator_capacity)
        )
        self._async_cancel_futures = {}

        # 创建机器人控制器（不自动初始化）
        self._create_robot_controller()

        # 初始化数据库和调度器
        self.database = TaskDatabase()
        recovery_message = "机器人进程重启，取消请求结果未知，按失败收敛"
        # 进程重启后无法证明硬件是否停止，所有遗留 pending 请求必须先落为失败。
        for pending in self.database.fail_pending_task_cancel_requests(recovery_message):
            self.database.update_command_status(
                pending["cancel_command_id"], CommandStatus.FAILED, recovery_message
            )
            self.database.log_task_action(
                pending["target_task_id"], "", "recover_pending_cancel", "failed",
                f"cancel_command_id={pending['cancel_command_id']}; {recovery_message}",
            )

        # 启动数据库清理线程
        database_config = self.config.get('database_config', {})
        retention_config = database_config.get('retention_days', {})
        if retention_config:
            self.database.start_cleanup_thread(
                retention_config=retention_config,
                cleanup_interval_hours=database_config.get('cleanup_interval_hours', 6),
                vacuum_interval_hours=database_config.get('vacuum_interval_hours', 24)
            )

        self.scheduler = TaskScheduler(
            self.robot_controller,
            self.database,
            allow_running_task_cancel=bool(
                cancel_config.get("allow_running_task_cancel", False)
            ),
            safe_arm_joints=safe_pose_config.get(
                "arm_joints", [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
            ),
            auto_charge_after_cancel=bool(
                charging_config.get("auto_charge_after_cancel", False)
            ),
            charge_marker=charging_config.get("marker"),
            auto_charge_priority=int(
                charging_config.get("auto_charge_priority", 9)
            ),
        )

        # 启动调度器
        self.scheduler.start()

        # 注册调度器回调
        self.scheduler.register_callback("on_task_start", self._on_task_start)
        self.scheduler.register_callback("on_task_complete", self._on_task_complete)
        self.scheduler.register_callback("on_task_failed", self._on_task_failed)
        self.scheduler.register_callback("on_station_start", self._on_station_start)
        self.scheduler.register_callback("on_station_complete", self._on_station_complete)
        self.scheduler.register_callback("on_station_retry", self._on_station_retry)
        self.scheduler.register_callback("on_station_progress", self._on_station_progress)
        self.scheduler.register_callback("on_operation_result", self._on_operation_result)

        # 注册命令级回调
        self.scheduler.register_callback("on_command_complete", self._on_command_complete)
        self.scheduler.register_callback("on_command_failed", self._on_command_failed)
        self.scheduler.register_callback("on_command_status_change", self._on_command_status_change)

        # 新增：系统级回调（用于TaskManager -> RobotControlSystem通信）
        self.system_callbacks = {
            "on_command_status_change": None,   # 命令状态变化回调
            "on_task_progress": None,           # 任务进度回调
            "on_operation_result": None,        # 操作结果回调
        }

        # 根据 auto_start_on_boot 和各模块 enabled 配置决定是否自动启动硬件
        if auto_start_on_boot:
            self.logger.info("TaskManager: 根据配置自动启动硬件...")
            self.start_hardware(
                robot=self.robot_enabled,
                camera=self.camera_enabled,
                env_sensor=self.env_sensor_enabled
            )
        else:
            self.logger.info("TaskManager: 等待远程命令启动硬件...")

    def _create_robot_controller(self):
        """创建机器人控制器实例（不调用setup_system）"""
        try:
            if self.use_mock:
                self.logger.info("TaskManager: 创建Mock机器人控制器")
                from robot.MockRobotController import MockRobotController
                self.robot_controller = MockRobotController(
                    self.config.get('robot_config', {}),
                    auto_setup=True  # 不自动初始化
                )
            else:
                self.logger.info("TaskManager: 创建真实机器人控制器")
                from robot.RobotController import RobotController as RealRobotController
                self.robot_controller = RealRobotController(
                    self.config,
                    auto_setup=True  # 不自动初始化
                )

            self.logger.info("TaskManager: 机器人控制器创建成功（未初始化）")

        except Exception as e:
            self.logger.error(f"TaskManager: 创建机器人控制器失败: {e}")
            raise

    # ==================== 硬件控制相关方法 ====================
    def start_hardware(self, robot: bool = False, camera: bool = False, env_sensor: bool = False) -> Dict[str, Any]:
        """启动指定硬件模块

        Args:
            robot: 是否启动机器人（AGV+机械臂）
            camera: 是否启动相机
            env_sensor: 是否启动环境传感器

        Returns:
            Dict[str, Any]: 各模块启动结果
        """
        results = {
            "robot": {"requested": robot, "success": False, "message": ""},
            "camera": {"requested": camera, "success": False, "message": ""},
            "env_sensor": {"requested": env_sensor, "success": False, "message": ""},
        }

        with self._hardware_lock:
            # 启动机器人
            if robot:
                try:
                    if not self._hardware_status["robot"]:
                        self.logger.info("正在启动机器人模块...")
                        if self.robot_controller.setup_system():
                            self._hardware_status["robot"] = True
                            results["robot"]["success"] = True
                            results["robot"]["message"] = "机器人模块启动成功"
                            scheduler = getattr(self, "scheduler", None)
                            if (scheduler is not None and
                                    scheduler.is_hardware_fault_blocked()):
                                # 硬件启动命令绕过普通调度队列；只有真实重新初始化成功
                                # 才能作为人工故障恢复入口解除调度阻塞。
                                scheduler.clear_hardware_fault_block()
                                results["robot"]["message"] += "，调度阻塞已解除"
                            self.logger.info("机器人模块已启动")
                        else:
                            results["robot"]["message"] = "机器人系统初始化失败"
                            self.logger.error("机器人系统初始化失败")
                    else:
                        results["robot"]["success"] = True
                        results["robot"]["message"] = "机器人模块已在运行中"
                except Exception as e:
                    results["robot"]["message"] = f"启动失败: {str(e)}"
                    self.logger.error(f"启动机器人模块失败: {e}")

            # 启动相机
            if camera:
                try:
                    if not self._hardware_status["camera"]:
                        self.logger.info("正在启动相机模块...")
                        if self.robot_controller.start_camera():
                            self._hardware_status["camera"] = True
                            results["camera"]["success"] = True
                            results["camera"]["message"] = "相机模块启动成功"
                            self.logger.info("相机模块已启动")
                        else:
                            results["camera"]["message"] = "相机启动失败"
                            self.logger.error("相机启动失败")
                    else:
                        results["camera"]["success"] = True
                        results["camera"]["message"] = "相机模块已在运行中"
                except Exception as e:
                    results["camera"]["message"] = f"启动失败: {str(e)}"
                    self.logger.error(f"启动相机模块失败: {e}")

            # 启动环境传感器
            if env_sensor:
                try:
                    if not self._hardware_status["env_sensor"]:
                        self.logger.info("正在启动环境传感器模块...")
                        if self.robot_controller.start_env_sensor():
                            self._hardware_status["env_sensor"] = True
                            results["env_sensor"]["success"] = True
                            results["env_sensor"]["message"] = "环境传感器模块启动成功"
                            self.logger.info("环境传感器模块已启动")
                        else:
                            results["env_sensor"]["message"] = "环境传感器启动失败"
                            self.logger.error("环境传感器启动失败")
                    else:
                        results["env_sensor"]["success"] = True
                        results["env_sensor"]["message"] = "环境传感器模块已在运行中"
                except Exception as e:
                    results["env_sensor"]["message"] = f"启动失败: {str(e)}"
                    self.logger.error(f"启动环境传感器模块失败: {e}")

        return results

    def stop_hardware(self, robot: bool = False, camera: bool = False, env_sensor: bool = False) -> Dict[str, Any]:
        """关闭指定硬件模块

        Args:
            robot: 是否关闭机器人（AGV+机械臂）
            camera: 是否关闭相机
            env_sensor: 是否关闭环境传感器

        Returns:
            Dict[str, Any]: 各模块关闭结果
        """
        results = {
            "robot": {"requested": robot, "success": False, "message": ""},
            "camera": {"requested": camera, "success": False, "message": ""},
            "env_sensor": {"requested": env_sensor, "success": False, "message": ""},
        }

        with self._hardware_lock:
            # 关闭机器人
            if robot:
                try:
                    if self._hardware_status["robot"]:
                        self.logger.info("正在关闭机器人模块...")
                        self.robot_controller.shutdown_system()
                        self._hardware_status["robot"] = False
                        results["robot"]["success"] = True
                        results["robot"]["message"] = "机器人模块已关闭"
                        self.logger.info("机器人模块已关闭")
                    else:
                        results["robot"]["success"] = True
                        results["robot"]["message"] = "机器人模块未运行"
                except Exception as e:
                    results["robot"]["message"] = f"关闭失败: {str(e)}"
                    self.logger.error(f"关闭机器人模块失败: {e}")

            # 关闭相机
            if camera:
                try:
                    if self._hardware_status["camera"]:
                        self.logger.info("正在关闭相机模块...")
                        if self.robot_controller.stop_camera():
                            self._hardware_status["camera"] = False
                            results["camera"]["success"] = True
                            results["camera"]["message"] = "相机模块已关闭"
                            self.logger.info("相机模块已关闭")
                        else:
                            results["camera"]["message"] = "相机关闭失败"
                            self.logger.error("相机关闭失败")
                    else:
                        results["camera"]["success"] = True
                        results["camera"]["message"] = "相机模块未运行"
                except Exception as e:
                    results["camera"]["message"] = f"关闭失败: {str(e)}"
                    self.logger.error(f"关闭相机模块失败: {e}")

            # 关闭环境传感器
            if env_sensor:
                try:
                    if self._hardware_status["env_sensor"]:
                        self.logger.info("正在关闭环境传感器模块...")
                        if self.robot_controller.stop_env_sensor():
                            self._hardware_status["env_sensor"] = False
                            results["env_sensor"]["success"] = True
                            results["env_sensor"]["message"] = "环境传感器模块已关闭"
                            self.logger.info("环境传感器模块已关闭")
                        else:
                            results["env_sensor"]["message"] = "环境传感器关闭失败"
                            self.logger.error("环境传感器关闭失败")
                    else:
                        results["env_sensor"]["success"] = True
                        results["env_sensor"]["message"] = "环境传感器模块未运行"
                except Exception as e:
                    results["env_sensor"]["message"] = f"关闭失败: {str(e)}"
                    self.logger.error(f"关闭环境传感器模块失败: {e}")

        return results

    def get_hardware_status(self) -> Dict[str, bool]:
        """获取硬件状态

        Returns:
            Dict[str, bool]: 各模块运行状态
        """
        with self._hardware_lock:
            return self._hardware_status.copy()

    def is_robot_ready(self) -> bool:
        """检查机器人是否就绪

        Returns:
            bool: 机器人模块是否已启动且初始化完成
        """
        with self._hardware_lock:
            return self._hardware_status["robot"]

    # ==================== 状态查询相关方法 ====================
    def get_robot_status(self) -> Dict[str, Any]:
        """获取机器人状态（供RobotControlSystem调用）

        Returns:
            Dict[str, Any]: 机器人状态信息
        """
        try:
            return self.robot_controller.get_status()
        except Exception as e:
            self.logger.error(f"获取机器人状态失败: {e}")
            return {}

    def get_environment_data(self) -> Dict[str, Any]:
        """获取环境数据（供RobotControlSystem调用）

        Returns:
            Dict[str, Any]: 环境数据
        """
        try:
            return self.robot_controller.get_environment_data()
        except Exception as e:
            self.logger.error(f"获取环境数据失败: {e}")
            return {}

    def execute_emergency_stop(self) -> bool:
        """执行紧急停止（供RobotControlSystem调用）

        Returns:
            bool: 是否成功
        """
        try:
            return self.robot_controller.emergency_stop()
        except Exception as e:
            self.logger.error(f"执行紧急停止失败: {e}")
            return False

    def get_current_execution_state(self) -> Dict[str, Any]:
        """获取当前执行状态（统一接口）

        Returns:
            Dict with keys: command, task, station
        """
        state = {
            "command": None,
            "task": None,
            "station": None
        }

        if self.scheduler.current_command:
            state["command"] = {
                "command_id": self.scheduler.current_command.command_id,
                "cmd_type": self.scheduler.current_command.cmd_type.value,
                "status": self.scheduler.current_command.status.value
            }

        if self.scheduler.current_task:
            task = self.scheduler.current_task
            state["task"] = {
                "task_id": task.task_id,
                "task_name": task.task_name,
                "status": task.status.value,
                "total_stations": len(task.station_list),
                "completed_stations": sum(1 for s in task.station_list
                                        if s.status == StationTaskStatus.COMPLETED)
            }

        if self.scheduler.current_station:
            station = self.scheduler.current_station
            state["station"] = {
                "station_id": station.station_config.station_id,
                "status": station.status.value,
                "execution_phase": station.execution_phase.value,
                "progress_detail": station.progress_detail,
                "retry_count": station.retry_count
            }

        return state

    def get_current_task_info(self) -> Dict[str, Any]:
        """仅获取当前任务信息

        Returns:
            Dict: 任务信息字典，如果没有当前任务则返回 None
        """
        if not self.scheduler.current_task:
            return None

        task = self.scheduler.current_task
        return {
            "task_id": task.task_id,
            "task_name": task.task_name,
            "status": task.status.value,
            "station_list": [
                {
                    "station_id": s.station_config.station_id,
                    "status": s.status.value,
                    "retry_count": s.retry_count
                }
                for s in task.station_list
            ]
        }

    def get_current_station_info(self) -> Dict[str, Any]:
        """仅获取当前站点信息（包含执行阶段）

        Returns:
            Dict: 站点信息字典，如果没有当前站点则返回 None
        """
        if not self.scheduler.current_station:
            return None

        station = self.scheduler.current_station
        return {
            "station_id": station.station_config.station_id,
            "name": station.station_config.name,  # 新增：站点名称
            "status": station.status.value,
            "execution_phase": station.execution_phase.value,
            "progress_detail": station.progress_detail,
            "agv_marker": station.station_config.agv_marker,
            "retry_count": station.retry_count,
            "started_at": station.started_at.isoformat() if station.started_at else None
        }

    def get_progress_snapshot(self) -> Optional[Dict[str, Any]]:
        """获取当前进度快照（线程安全）

        统一的状态访问接口，避免参数传递混乱。用于RobotControlSystem获取
        当前执行状态以构建上报消息。

        Returns:
            包含 task, station, command_id 的字典，如果无任务则返回 None
        """
        if not self.scheduler.current_task:
            return None

        return {
            "task": self.scheduler.current_task,
            "station": self.scheduler.current_station,
            "command_id": (
                self.scheduler.current_command.command_id
                if self.scheduler.current_command else None
            )
        }

    # ==================== 指令响应相关方法 ====================
    def receive_command(self, command_envelope: CommandEnvelope) -> str:
        """接收并处理所有类型的命令（统一入口）

        Args:
            command_envelope: 命令信封

        Returns:
            str: 命令ID
        """
        try:
            cmd_id = command_envelope.cmd_id
            cmd_type = command_envelope.cmd_type
            data_json = command_envelope.data_json

            self.logger.info(f"TaskManager接收命令: {cmd_id}, 类型: {cmd_type.value}")

            if cmd_type == CmdType.CANCEL_TASK_CMD:
                raise ValueError("取消任务必须通过 request_cancel_task 即时处理")

            # 根据命令类型创建UnifiedCommand
            if cmd_type == CmdType.TASK_CMD:
                # dataConverter 已在解析 proto 时构建好 Task 对象，直接取用
                task = command_envelope.data
                if task is None:
                    # 兜底：data 未预填时从 data_json 重建（不应发生于正常流程）
                    from utils.dataConverter import convert_task_cmd_to_task
                    from dataModels.CommandModels import TaskCmd
                    task_cmd_dict = data_json.get('task_cmd', {})
                    self.logger.warning("TASK_CMD: command_envelope.data 为空，从 data_json 降级解析")
                    task = convert_task_cmd_to_task(
                        TaskCmd(
                            task_id=task_cmd_dict.get('task_id'),
                            task_name=task_cmd_dict.get('task_name'),
                            robot_mode=RobotMode(task_cmd_dict.get('robot_mode')),
                            generate_time=datetime.fromisoformat(task_cmd_dict.get('generate_time')),
                            station_config_list=[]
                        )
                    )
                command = create_unified_command(
                    command_id=cmd_id,
                    cmd_type=cmd_type,
                    data=task,
                    metadata={"source": "receive_command", "robot_id": command_envelope.robot_id}
                )

            else:
                # 其他命令类型直接使用data_json
                command = create_unified_command(
                    command_id=cmd_id,
                    cmd_type=cmd_type,
                    data=data_json,
                    metadata={"source": "receive_command", "robot_id": command_envelope.robot_id}
                )

            # 添加到调度器队列
            self.scheduler.add_command(command)

            self.logger.info(f"命令已提交到调度器: {cmd_id}")
            return cmd_id

        except Exception as e:
            self.logger.error(f"接收命令失败: {e}")
            raise

    def request_cancel_task(self, cancel_command_id: str, task_id: int) -> UnifiedCommand:
        """协调任务取消，并只触发一次取消请求的最终状态回调。"""
        # cancel_command_id 标识“取消请求”本身；task_id 才是要停止的业务任务。
        # 两者分离后，重复投递同一取消命令可以幂等重放，且不会误取消其他任务。
        # __new__ 构造的轻量测试实例也使用同一套并发语义。
        if not hasattr(self, "_cancel_request_lock"):
            self._cancel_request_lock = threading.RLock()
            self._cancel_requests_inflight = {}
            self._cancel_requests_seen = set()

        wait_event = None
        recovered_pending = False
        with self._cancel_request_lock:
            # 数据库中的 pending 记录用于跨进程/重启恢复；进程内 event 只负责唤醒并发等待者。
            created = self.database.begin_task_cancel_request(cancel_command_id, str(task_id))
            if created:
                owner_event = threading.Event()
                self._cancel_requests_inflight[cancel_command_id] = owner_event
                self._cancel_requests_seen.add(cancel_command_id)
            else:
                saved = self.database.get_task_cancel_request(cancel_command_id)
                if saved and saved["target_task_id"] != str(task_id):
                    conflict = self._make_cancel_result_command(
                        cancel_command_id, task_id, CommandStatus.FAILED,
                        "同一取消 command_id 不能指向不同 task_id",
                        {"idempotency_conflict": True},
                    )
                    self.database.log_task_action(
                        str(task_id), "", "cancel_request_conflict", "failed",
                        f"cancel_command_id={cancel_command_id}; original_task_id={saved['target_task_id']}",
                    )
                    return conflict

                wait_event = self._cancel_requests_inflight.get(cancel_command_id)
                if wait_event is None and saved and saved["status"] == "pending":
                    recovered_pending = True

        if wait_event is not None:
            # 非 owner 请求不重复触碰硬件，等待 owner 提交终态后读取同一结果。
            wait_event.wait()
            saved = self.database.get_task_cancel_request(cancel_command_id)
            if saved and saved["status"] == "pending":
                # owner 未能提交终态；当前等待者接管遗留请求并按失败收敛。
                return self.request_cancel_task(cancel_command_id, task_id)
            replay = self._cancel_command_from_saved(saved, task_id)
            with self._cancel_request_lock:
                should_emit = cancel_command_id not in self._cancel_requests_seen
                self._cancel_requests_seen.add(cancel_command_id)
            if should_emit:
                self._trigger_system_callback(
                    "on_command_status_change", command=replay
                )
            return replay

        if not created:
            saved = self.database.get_task_cancel_request(cancel_command_id)
            if recovered_pending:
                message = "机器人进程中断，取消请求结果未知，按失败收敛"
                try:
                    committed = self.database.complete_task_cancel_request(
                        cancel_command_id, CommandStatus.FAILED, message
                    )
                except Exception as exc:
                    raise CancelRequestPersistenceError(
                        f"取消请求终态提交失败: {cancel_command_id}"
                    ) from exc
                saved = self.database.get_task_cancel_request(cancel_command_id)
                if not committed and saved and saved["status"] == "pending":
                    raise CancelRequestPersistenceError(
                        f"取消请求仍处于 pending: {cancel_command_id}"
                    )
                recovered = self._cancel_command_from_saved(saved, task_id)
                if committed:
                    self.database.update_command_status(
                        cancel_command_id, CommandStatus.FAILED, message
                    )
                    self.database.log_task_action(
                        str(task_id), "", "recover_pending_cancel", "failed", message
                    )
                    self._trigger_system_callback(
                        "on_command_status_change", command=recovered
                    )
                    with self._cancel_request_lock:
                        self._cancel_requests_seen.add(cancel_command_id)
                return recovered
            replay = self._cancel_command_from_saved(saved, task_id)
            with self._cancel_request_lock:
                should_emit = cancel_command_id not in self._cancel_requests_seen
                self._cancel_requests_seen.add(cancel_command_id)
            if should_emit:
                self._trigger_system_callback(
                    "on_command_status_change", command=replay
                )
            return replay

        cancel_command = create_unified_command(
            command_id=cancel_command_id,
            cmd_type=CmdType.CANCEL_TASK_CMD,
            data=CancelTaskCmd(task_id),
            metadata={"target_task_id": task_id, "source": "request_cancel_task"},
        )
        try:
            self.database.save_command(cancel_command)
            result = self.scheduler.cancel_task(task_id)
            if result.get("pending"):
                # 运行中取消先设置 cancel_event，由工作线程在原子动作边界执行停止并确认。
                # 该等待只发生在独立取消协调线程，不占用业务 executor。
                wait_timeout = getattr(self, "_cancel_wait_timeout", 30.0)
                completed = result["event"].wait(wait_timeout)
                result = result["entry"].get("cancel_result") if completed else None
                result = result or {
                    "success": False,
                    "message": f"等待运行任务取消超时: {task_id}",
                }
            cancel_command.status = (
                CommandStatus.COMPLETED if result["success"] else CommandStatus.FAILED
            )
            cancel_command.error_message = result["message"]
        except Exception as exc:
            cancel_command.status = CommandStatus.FAILED
            cancel_command.error_message = f"取消任务处理异常: {exc}"
            self.logger.exception(cancel_command.error_message)

        cancel_command.completed_at = datetime.now()
        committed = False
        finalize_error = None
        try:
            try:
                self.database.update_command_status(
                    cancel_command_id, cancel_command.status, cancel_command.error_message
                )
            except Exception as exc:
                cancel_command.status = CommandStatus.FAILED
                cancel_command.error_message = f"取消请求命令落盘失败: {exc}"
                self.logger.exception(cancel_command.error_message)

            try:
                committed = self.database.complete_task_cancel_request(
                    cancel_command_id, cancel_command.status, cancel_command.error_message
                )
            except Exception:
                self.logger.exception("取消请求终态提交失败")
                finalize_error = CancelRequestPersistenceError(
                    f"取消请求终态提交失败: {cancel_command_id}"
                )
            if committed:
                try:
                    self.database.log_task_action(
                        str(task_id), "", "cancel_request", cancel_command.status.value,
                        f"cancel_command_id={cancel_command_id}; {cancel_command.error_message}",
                    )
                except Exception:
                    self.logger.exception("取消请求审计日志写入失败")

                # 取消请求的终态先反馈给上位机；自动回充是随后发生的内部行为，
                # 不得抢在最终反馈之前驱动硬件，也不追溯修改取消结果。
                self._trigger_system_callback(
                    "on_command_status_change", command=cancel_command
                )

                if cancel_command.status == CommandStatus.COMPLETED:
                    try:
                        auto_charge_result = (
                            self.scheduler.schedule_auto_charge_after_cancel(
                                task_id, cancel_command_id
                            )
                        )
                        cancel_command.metadata["auto_charge"] = auto_charge_result
                        try:
                            self.database.update_command_metadata(
                                cancel_command_id, cancel_command.metadata
                            )
                        except Exception:
                            self.logger.exception(
                                "取消请求自动回充决策 metadata 写入失败"
                            )
                    except Exception:
                        # 自动回充是取消成功后的内部行为，不追溯修改已提交的取消结果。
                        self.logger.exception(
                            f"取消成功后生成自动回充命令失败: task_id={task_id}"
                        )
        finally:
            with self._cancel_request_lock:
                # 无论硬件/数据库结果如何，都唤醒同一 command_id 的等待者。
                event = self._cancel_requests_inflight.pop(cancel_command_id, None)
                if event is not None:
                    event.set()
        if finalize_error is not None:
            raise finalize_error
        if not committed:
            saved = self.database.get_task_cancel_request(cancel_command_id)
            if saved and saved["status"] == "pending":
                raise CancelRequestPersistenceError(
                    f"取消请求仍处于 pending: {cancel_command_id}"
                )
            return self._cancel_command_from_saved(saved, task_id)
        return cancel_command

    def request_cancel_task_async(self, cancel_command_id: str, task_id: int):
        """立即返回；相同 command_id 只占用一个有界协调 worker。"""
        # 协议线程只负责入队，实际取消在有界线程池执行，避免阻塞 gRPC 接收循环。
        if not hasattr(self, "_cancel_request_lock"):
            self._cancel_request_lock = threading.RLock()
            self._cancel_requests_inflight = {}
            self._cancel_requests_seen = set()
        if not hasattr(self, "_cancel_coordinator"):
            self._cancel_wait_timeout = 30.0
            self._cancel_coordinator = ThreadPoolExecutor(
                max_workers=4, thread_name_prefix="cancel-task"
            )
            self._cancel_coordinator_slots = threading.BoundedSemaphore(32)
            self._async_cancel_futures = {}

        def coordinate():
            try:
                self.request_cancel_task(cancel_command_id, task_id)
                self.database.mark_message_processed(cancel_command_id)
            except CancelRequestPersistenceError:
                self.logger.exception(
                    f"取消请求终态未落盘: {cancel_command_id}"
                )
            except Exception:
                self.logger.exception(f"异步取消处理异常: {cancel_command_id}")
        with self._cancel_request_lock:
            # future 按取消 command_id 去重；同一 ID 的重试共享原任务结果。
            existing = self._async_cancel_futures.get(cancel_command_id)
            if existing is not None:
                return existing
            has_slot = self._cancel_coordinator_slots.acquire(blocking=False)
            if has_slot:
                try:
                    future = self._cancel_coordinator.submit(coordinate)
                except Exception:
                    self._cancel_coordinator_slots.release()
                    raise
                self._async_cancel_futures[cancel_command_id] = future

        if not has_slot:
            # 槽位耗尽时立即持久化失败终态，让上游重试而不是无限排队。
            # SQLite 和回调必须在 _cancel_request_lock 之外执行。
            self.logger.error(f"取消协调队列已满: {cancel_command_id}")
            rejected = Future()
            try:
                result = self._settle_cancel_overload(cancel_command_id, task_id)
                rejected.set_result(result)
            except Exception as error:
                rejected.set_exception(error)
            return rejected

        def release(completed):
            with self._cancel_request_lock:
                if self._async_cancel_futures.get(cancel_command_id) is completed:
                    self._async_cancel_futures.pop(cancel_command_id, None)
                self._cancel_coordinator_slots.release()
        future.add_done_callback(release)
        return future

    def _settle_cancel_overload(self, cancel_command_id: str, task_id: int):
        """容量拒绝也按正常取消请求完成幂等终态收敛。"""
        # 即使未进入协调线程，也保留原 command_id→task_id 绑定，防止后续重放改写目标。
        message = "取消协调队列已满，请稍后重试"
        created = self.database.begin_task_cancel_request(
            cancel_command_id, str(task_id)
        )
        saved = self.database.get_task_cancel_request(cancel_command_id)
        if not saved:
            raise CancelRequestPersistenceError(
                f"容量拒绝请求登记不可用: {cancel_command_id}"
            )
        if saved["target_task_id"] != str(task_id):
            # 与正常取消入口一致：不改写原请求，不复用原结果。
            conflict = self._make_cancel_result_command(
                cancel_command_id, task_id, CommandStatus.FAILED,
                "同一取消 command_id 不能指向不同 task_id",
                {"idempotency_conflict": True},
            )
            try:
                self.database.log_task_action(
                    str(task_id), "", "cancel_request_conflict", "failed",
                    f"cancel_command_id={cancel_command_id}; "
                    f"original_task_id={saved['target_task_id']}",
                )
                # server_command_received 记录的是本次冲突载荷，
                # 它必须独立收敛，不改写原 cancel request。
                self.database.mark_message_processed(cancel_command_id)
            except Exception as error:
                raise CancelRequestPersistenceError(
                    f"取消请求冲突收敛失败: {cancel_command_id}"
                ) from error
            return conflict
        result = self._make_cancel_result_command(
            cancel_command_id, task_id, CommandStatus.FAILED, message,
            {"coordinator_overloaded": True},
        )
        result.completed_at = datetime.now()
        try:
            if saved["status"] == "pending":
                committed = self.database.complete_task_cancel_request(
                    cancel_command_id, CommandStatus.FAILED, message
                )
                if not committed:
                    saved = self.database.get_task_cancel_request(cancel_command_id)
                    if not saved or saved["status"] == "pending":
                        raise CancelRequestPersistenceError(
                            f"容量拒绝终态提交失败: {cancel_command_id}"
                        )
            # 即使请求表已在前次尝试中终态，也要补齐命令表。
            self.database.save_command(result)
            self.database.update_command_status(
                cancel_command_id, CommandStatus.FAILED, message
            )
        except CancelRequestPersistenceError:
            raise
        except Exception as error:
            raise CancelRequestPersistenceError(
                f"容量拒绝关键落盘失败: {cancel_command_id}"
            ) from error

        with self._cancel_request_lock:
            should_emit = cancel_command_id not in self._cancel_requests_seen
            self._cancel_requests_seen.add(cancel_command_id)
        if should_emit:
            self._trigger_system_callback("on_command_status_change", command=result)
        try:
            self.database.mark_message_processed(cancel_command_id)
        except Exception as error:
            raise CancelRequestPersistenceError(
                f"容量拒绝 processed 标记失败: {cancel_command_id}"
            ) from error
        return result

    @staticmethod
    def _make_cancel_result_command(
        cancel_command_id: str,
        task_id: int,
        status: CommandStatus,
        message: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> UnifiedCommand:
        return UnifiedCommand(
            command_id=cancel_command_id,
            cmd_type=CmdType.CANCEL_TASK_CMD,
            category=CommandCategory.CONTROL,
            priority=0,
            data=CancelTaskCmd(task_id),
            status=status,
            error_message=message,
            metadata=metadata or {},
        )

    def _cancel_command_from_saved(
        self, saved: Optional[Dict[str, Any]], fallback_task_id: int
    ) -> UnifiedCommand:
        if not saved:
            return self._make_cancel_result_command(
                "", fallback_task_id, CommandStatus.FAILED, "取消请求结果不可用"
            )
        status = (
            CommandStatus.COMPLETED
            if saved["status"] == CommandStatus.COMPLETED.value
            else CommandStatus.FAILED
        )
        return self._make_cancel_result_command(
            saved["cancel_command_id"], int(saved["target_task_id"]), status,
            saved.get("message") or "取消请求尚未形成终态",
            {"idempotent_replay": True},
        )



    # ==================== 任务查询相关方法 ====================
    def get_command_status(self, command_id: str) -> Dict[str, Any]:
        """查询命令执行状态

        Args:
            command_id: 命令ID

        Returns:
            Dict[str, Any]: 命令状态信息
        """
        try:
            command_dict = self.database.get_command_by_id(command_id)
            if not command_dict:
                return {"error": "命令不存在"}

            return command_dict

        except Exception as e:
            self.logger.error(f"查询命令状态失败: {e}")
            return {"error": str(e)}

    def cancel_command(self, command_id: str) -> bool:
        """取消命令执行

        Args:
            command_id: 命令ID

        Returns:
            bool: 是否取消成功
        """
        try:
            # 更新命令状态为已取消
            self.database.update_command_status(command_id, CommandStatus.CANCELLED)
            self.logger.info(f"命令已取消: {command_id}")

            # 构建一个简单的命令对象用于回调通知
            # 从数据库获取命令信息
            command_info = self.get_command_status(command_id)
            if command_info and "error" not in command_info:
                # 解析命令类型和分类
                try:
                    cmd_type = CmdType(command_info.get("cmd_type", "response_cmd"))
                except ValueError:
                    cmd_type = CmdType.RESPONSE_CMD

                try:
                    category = CommandCategory(command_info.get("category", "control"))
                except ValueError:
                    category = CommandCategory.CONTROL

                # 创建一个临时的命令对象用于回调
                cancelled_command = UnifiedCommand(
                    command_id=command_id,
                    cmd_type=cmd_type,
                    category=category,
                    status=CommandStatus.CANCELLED,
                    error_message="命令已被取消"
                )
                # 触发系统回调：通知RobotControlSystem命令状态变化
                self._trigger_system_callback(
                    "on_command_status_change",
                    command=cancelled_command
                )

            return True

        except Exception as e:
            self.logger.error(f"取消命令失败: {e}")
            return False


    # ==================== 回调函数相关方法 ====================
    def register_system_callback(self, event: str, callback: callable):
        """注册系统级回调函数

        Args:
            event: 事件名称
            callback: 回调函数
        """
        if event in self.system_callbacks:
            self.system_callbacks[event] = callback
            self.logger.debug(f"已注册系统回调: {event}")
        else:
            self.logger.warning(f"未知系统回调事件: {event}")

    def _trigger_system_callback(self, event: str, *args, **kwargs):
        """触发系统级回调

        Args:
            event: 事件名称
            *args: 位置参数
            **kwargs: 关键字参数
        """
        callback = self.system_callbacks.get(event)
        if callback:
            try:
                callback(*args, **kwargs)
            except Exception as e:
                self.logger.error(f"系统回调执行异常: {e}")

    # def receive_task_from_cmd(self, task_cmd: TaskCmd) -> str:
    #     """从TaskCmd接收任务
        
    #     Args:
    #         task_cmd: 任务命令对象
            
    #     Returns:
    #         str: 生成的任务ID
    #     """
    #     try:
    #         # 解析TaskCmd为Task对象
    #         task = convert_task_cmd_to_task(task_cmd)
            
    #         # 添加到调度器
    #         self.scheduler.add_task(task)
            
    #         self.logger.info(f"从TaskCmd接收任务成功，任务ID: {task.task_id}")
    #         return task.task_id
            
    #     except Exception as e:
    #         self.logger.error(f"从TaskCmd接收任务失败: {e}")
    #         raise
    

    # def receive_task_from_dict(self, task_dict: Dict[str, Any]) -> str:
    #     """从字典接收任务
        
    #     Args:
    #         task_dict: 任务字典
            
    #     Returns:
    #         str: 生成的任务ID
    #     """
    #     try:
    #         # 提取任务信息
    #         task_id = task_dict.get("task_id", f"TASK_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    #         task_name = task_dict.get("task_name", "未知任务")
    #         robot_mode = RobotMode(task_dict.get("robot_mode", "inspection"))
    #         generate_time = datetime.fromisoformat(task_dict.get("generate_time")) if task_dict.get("generate_time") else datetime.now()
            
    #         # 创建站点列表
    #         station_list = []
    #         station_configs = task_dict.get("station_config_tasks", [])
            
    #         for station_config_dict in station_configs:
    #             # 创建操作配置
    #             operation_config_dict = station_config_dict.get("operation_config", {})
    #             operation_config = OperationConfig(
    #                 operation_mode=OperationMode(operation_config_dict.get("operation_mode", "none")),
    #                 door_ip=operation_config_dict.get("door_ip"),
    #                 device_id=operation_config_dict.get("device_id")
    #             )
                
    #             # 创建站点配置
    #             station_config = StationConfig(
    #                 station_id=station_config_dict.get("station_id"),
    #                 sort=station_config_dict.get("sort", 0),
    #                 name=station_config_dict.get("name", "未知站点"),
    #                 agv_marker=station_config_dict.get("agv_marker", ""),
    #                 robot_pos=station_config_dict.get("robot_pos", [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]),
    #                 ext_pos=station_config_dict.get("ext_pos", [0.0, 0.0, 0.0, 0.0]),
    #                 operation_config=operation_config
    #             )
                
    #             # 创建站点任务
    #             station = Station(
    #                 station_config=station_config,
    #                 status=StationTaskStatus.PENDING,
    #                 created_at=datetime.now(),
    #                 retry_count=0,
    #                 max_retries=3,
    #                 metadata={
    #                     "source": "dict",
    #                     "task_id": task_id
    #                 }
    #             )
                
    #             station_list.append(station)
            
    #         # 创建任务对象
    #         task = Task(
    #             task_id=task_id,
    #             task_name=task_name,
    #             station_list=station_list,
    #             status=TaskStatus.PENDING,
    #             robot_mode=robot_mode,
    #             generate_time=generate_time,
    #             created_at=datetime.now(),
    #             metadata={
    #                 "source": "dict",
    #                 "generate_time": generate_time.isoformat()
    #             }
    #         )
            
    #         # 添加到调度器
    #         self.scheduler.add_task(task)
            
    #         self.logger.info(f"从字典接收任务成功，任务ID: {task.task_id}")
    #         return task.task_id
            
    #     except Exception as e:
    #         self.logger.error(f"从字典接收任务失败: {e}")
    #         raise
    
    # ==================== 回调函数 ====================
    def _on_task_start(self, task: Task):
        """任务开始回调"""
        self.logger.info(f"任务开始: {task.task_id}")
        self._trigger_system_callback(
            "on_task_progress",
            task=task,
            station=None,
            command_id=self.scheduler.current_command.command_id if self.scheduler.current_command else None
        )

    def _on_task_complete(self, task: Task):
        """任务完成回调"""
        self.logger.info(f"任务完成: {task.task_id}")
        self._trigger_system_callback(
            "on_task_progress",
            task=task,
            station=None,
            command_id=self.scheduler.current_command.command_id if self.scheduler.current_command else None
        )

    def _on_task_failed(self, task: Task):
        """任务失败回调"""
        self.logger.error(f"任务失败: {task.task_id}")
        self._trigger_system_callback(
            "on_task_progress",
            task=task,
            station=None,
            command_id=self.scheduler.current_command.command_id if self.scheduler.current_command else None
        )

    def _on_station_start(self, station: Station):
        """站点开始回调"""
        self.logger.info(f"站点开始: {station.station_config.station_id}")
        self._trigger_system_callback(
            "on_task_progress",
            task=self.scheduler.current_task,
            station=station,
            command_id=self.scheduler.current_command.command_id if self.scheduler.current_command else None
        )

    def _on_station_complete(self, station: Station):
        """站点完成回调"""
        self.logger.info(f"站点完成: {station.station_config.station_id}")
        self._trigger_system_callback(
            "on_task_progress",
            task=self.scheduler.current_task,
            station=station,
            command_id=self.scheduler.current_command.command_id if self.scheduler.current_command else None
        )

    def _on_station_retry(self, station: Station):
        """站点重试回调"""
        self.logger.warning(f"站点重试: {station.station_config.station_id}, 重试次数: {station.retry_count}")
        # 可以在这里发送通知或更新UI

    def _on_station_progress(self, station: Station, command_id: str = None):
        """站点进度更新回调"""
        self.logger.info(
            f"站点进度更新: {station.station_config.station_id} - "
            f"{station.execution_phase.value} - {station.progress_detail}"
        )
        self._trigger_system_callback(
            "on_task_progress",
            task=self.scheduler.current_task,
            station=station,
            command_id=self.scheduler.current_command.command_id if self.scheduler.current_command else None
        )

    def _on_operation_result(self, operation_data: Dict[str, Any],
                             task=None, station=None, command_id: str = None):
        """操作结果回调"""
        operation_mode = operation_data.get('operation_mode', 'unknown')
        result = operation_data.get('result', {})
        success = result.get('success', False)

        device_id = station.station_config.operation_config.device_id if station and station.station_config.operation_config else 'unknown'
        error_detail = f" - {result.get('message', '')}" if not success else ""
        self.logger.info(f"操作 {operation_mode} (设备 {device_id}): {'成功' if success else f'失败{error_detail}'}")

        self._trigger_system_callback(
            "on_operation_result",
            operation_data=operation_data,
            task=task,
            station=station,
            command_id=command_id
        )

    # ==================== 命令级回调处理 ====================

    def _on_command_complete(self, command):
        """命令完成回调"""
        self.logger.info(f"命令完成: {command.command_id}")

        # 触发系统回调：通知RobotControlSystem命令状态变化
        self._trigger_system_callback(
            "on_command_status_change",
            command=command
        )

    def _on_command_failed(self, command):
        """命令失败回调"""
        self.logger.error(f"命令失败: {command.command_id}")

        # 触发系统回调：通知RobotControlSystem命令状态变化
        self._trigger_system_callback(
            "on_command_status_change",
            command=command
        )

    def _on_command_status_change(self, command):
        """命令状态变化回调（任何状态变化都触发）"""
        self.logger.info(f"命令状态变化: {command.command_id} -> {command.status.value}")

        # 触发系统回调：通知RobotControlSystem命令状态变化
        self._trigger_system_callback(
            "on_command_status_change",
            command=command
        )
    
    def shutdown(self):
        """关闭管理器"""
        self.scheduler.stop()
        if hasattr(self, "_cancel_coordinator"):
            self._cancel_coordinator.shutdown(wait=False, cancel_futures=True)

        # 停止数据库清理线程
        try:
            self.database.stop_cleanup_thread()
        except Exception as e:
            self.logger.error(f"停止数据库清理线程失败: {e}")

        # 关闭机器人系统
        try:
            if hasattr(self, 'robot_controller'):
                self.robot_controller.shutdown_system()
                self.logger.info("机器人控制器已关闭")
        except Exception as e:
            self.logger.error(f"关闭机器人控制器失败: {e}")

        self.logger.info("任务管理器已关闭")
