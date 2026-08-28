# from utils.voice_player import VoicePlayer
import queue
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Callable, Optional, Dict, Any

from dataModels.CommandModels import CmdType
from dataModels.TaskModels import (
    Task, Station, OperationMode,
    TaskStatus, StationTaskStatus, StationExecutionPhase, OperationConfig, RobotMode
)
from dataModels.UnifiedCommand import UnifiedCommand, CommandStatus
from robot.HardwareErrors import ControlledStopError
from task.TaskDatabase import TaskDatabase
from utils.logger_config import get_logger


class TaskCancelledError(RuntimeError):
    """任务在安全检查点响应了协作取消。"""


class TaskScheduler:
    """任务调度器 - 负责任务的调度和执行（支持统一命令队列）"""

    def __init__(self, robot_controller, database: TaskDatabase,
                 allow_running_task_cancel: bool = False):
        self.robot_controller = robot_controller
        self.database = database
        # 使用优先级队列，支持UnifiedCommand
        self.command_queue = queue.PriorityQueue()
        self.current_command: Optional[UnifiedCommand] = None
        self.current_task: Optional[Task] = None
        self.current_station: Optional[Station] = None
        self.is_running = False
        self.scheduler_thread: Optional[threading.Thread] = None
        self.executor = ThreadPoolExecutor(max_workers=1)  # 单任务执行
        self.logger = get_logger(__name__)
        self._task_registry_lock = threading.RLock()
        self._active_tasks: Dict[int, Dict[str, Any]] = {}
        # 仅保留当前进程内、最近一个已取消任务代际，供不同取消命令幂等查询。
        self._cancelled_task_generations: Dict[int, Dict[str, Any]] = {}
        self.allow_running_task_cancel = allow_running_task_cancel
        self._hardware_fault_blocked = False
        self._hardware_fault_reason = None
        # self.voice_player = VoicePlayer()

        # 回调函数注册（每个事件只有一个消费者，使用单个 callable 而非 list）
        self.task_callbacks = {
            "on_task_start": None,
            "on_task_complete": None,
            "on_task_failed": None,
            "on_station_start": None,
            "on_station_complete": None,
            "on_station_retry": None,
            "on_station_progress": None,
            "on_operation_result": None,
            "on_command_complete": None,
            "on_command_failed": None,
            "on_command_status_change": None,
        }
    
    def start(self):
        """启动调度器"""
        if not self.is_running:
            self.is_running = True
            self.scheduler_thread = threading.Thread(target=self._scheduler_loop)
            self.scheduler_thread.daemon = True
            self.scheduler_thread.start()
            self.logger.info("任务调度器已启动")
    
    def stop(self):
        """停止调度器"""
        self.is_running = False
        with self._task_registry_lock:
            # 关闭期间不能再等待工作线程完成硬件停止；先唤醒取消请求，明确返回失败。
            for task_id, entry in self._active_tasks.items():
                if entry["state"] == "cancelling" and not entry["cancel_complete_event"].is_set():
                    entry["cancel_result"] = {
                        "success": False,
                        "message": f"调度器关闭，取消未完成: {task_id}",
                        "target_command_id": entry["command"].command_id,
                    }
                    entry["shutdown_notified"] = True
                    entry["cancel_complete_event"].set()
        if self.scheduler_thread:
            self.scheduler_thread.join(timeout=5)
        self.executor.shutdown(wait=False)
        self.logger.info("任务调度器已停止")
    
    def add_command(self, command: UnifiedCommand):
        """添加命令到队列（新接口，支持所有命令类型）"""
        with self._task_registry_lock:
            if command.cmd_type == CmdType.TASK_CMD:
                task = command.data
                if not isinstance(task, Task):
                    raise ValueError("TASK_CMD 的数据必须是 Task")
                if task.task_id in self._active_tasks:
                    raise ValueError(f"活动 task_id 重复: {task.task_id}")
                # 必须先持久化，再向注册表和队列发布，避免保存失败的幽灵任务。
                command.status = CommandStatus.QUEUED
                self.database.save_command(command)
                # 新任务代际开始，旧代际的取消墓碑不再适用。
                self._cancelled_task_generations.pop(task.task_id, None)
                self._active_tasks[task.task_id] = {
                    "command": command,
                    "cancel_event": threading.Event(),
                    "state": "queued",
                    "cancel_complete_event": threading.Event(),
                    "cancel_result": None,
                }

                self.command_queue.put(command)
            else:
                command.status = CommandStatus.QUEUED
                self.database.save_command(command)
                self.command_queue.put(command)

        # 触发命令状态变化回调（QUEUED）
        self._trigger_callback("on_command_status_change", command)

        self.logger.debug(f"命令 {command.command_id} (类型: {command.cmd_type.value}) 已添加到队列，优先级: {command.priority}")

    def _scheduler_loop(self):
        """调度器主循环（支持统一命令队列）"""
        while self.is_running:
            try:
                if self.current_command is None:
                    if self._hardware_fault_blocked:
                        time.sleep(0.1)
                        continue
                    # 获取下一个命令（非阻塞）
                    try:
                        command = self.command_queue.get_nowait()
                        self._execute_command(command)
                    except queue.Empty:
                        time.sleep(0.1)  # 队列为空时短暂休眠
                        continue
                else:
                    # 检查当前命令状态
                    if self.current_command.status in [CommandStatus.COMPLETED, CommandStatus.FAILED, CommandStatus.CANCELLED]:
                        # 清除逻辑应该更精细
                        cmd_type = self.current_command.cmd_type

                        if cmd_type == CmdType.TASK_CMD:
                            with self._task_registry_lock:
                                entry = self._active_tasks.get(
                                    self.current_command.data.task_id
                                )
                                terminal_pending = (
                                    entry is not None and
                                    entry["command"] is self.current_command
                                )
                            if terminal_pending:
                                # done callback 尚未完成终态落盘/注册表释放。
                                time.sleep(0.01)
                                continue

                        self.current_command = None

                        # 仅当前命令是 TASK_CMD 时才清除任务和站点
                        if cmd_type == CmdType.TASK_CMD:
                            self.current_task = None
                            self.current_station = None
                    else:
                        time.sleep(0.1)

            except Exception as e:
                self.logger.error(f"调度器循环异常: {e}")
                time.sleep(1)

    def _execute_command(self, command: UnifiedCommand):
        """执行命令（统一入口）"""
        with self._task_registry_lock:
            if command.cmd_type == CmdType.TASK_CMD:
                task = command.data
                entry = self._active_tasks.get(task.task_id)
                if (entry is None or entry["command"] is not command or
                        entry["cancel_event"].is_set() or
                        command.status == CommandStatus.CANCELLED):
                    # 取消只标记注册表，不从 PriorityQueue 中删除对象；出队时在此丢弃。
                    self.logger.info(f"跳过已取消的等待任务: task_id={task.task_id}")
                    if entry is not None and entry["command"] is command:
                        self._active_tasks.pop(task.task_id, None)
                    self.command_queue.task_done()
                    return
                entry["state"] = "running"

            self.current_command = command

        # 更新命令状态为运行中
        command.status = CommandStatus.RUNNING
        command.started_at = datetime.now()
        self.database.update_command_status(command.command_id, CommandStatus.RUNNING)

        # 触发命令状态变化回调
        self._trigger_callback("on_command_status_change", command)

        self.logger.info(f"开始执行命令: {command.command_id}, 类型: {command.cmd_type.value}")

        # 根据命令类型路由到不同的执行方法
        if command.cmd_type == CmdType.TASK_CMD:
            # 提交到线程池执行
            task = command.data
            self.current_task = task
            future = self.executor.submit(self._execute_task_command, command)
            future.add_done_callback(lambda f: self._command_execution_done(f, command))

        elif command.cmd_type == CmdType.ROBOT_MODE_CMD:
            future = self.executor.submit(self._execute_mode_command, command)
            future.add_done_callback(lambda f: self._command_execution_done(f, command))

        elif command.cmd_type == CmdType.JOY_CONTROL_CMD:
            future = self.executor.submit(self._execute_joy_command, command)
            future.add_done_callback(lambda f: self._command_execution_done(f, command))

        elif command.cmd_type == CmdType.CHARGE_CMD:
            future = self.executor.submit(self._execute_charge_command, command)
            future.add_done_callback(lambda f: self._command_execution_done(f, command))

        elif command.cmd_type == CmdType.SET_MARKER_CMD:
            future = self.executor.submit(self._execute_set_marker_command, command)
            future.add_done_callback(lambda f: self._command_execution_done(f, command))

        elif command.cmd_type == CmdType.POSITION_ADJUST_CMD:
            future = self.executor.submit(self._execute_position_adjust_command, command)
            future.add_done_callback(lambda f: self._command_execution_done(f, command))

        else:
            self.logger.warning(f"未知命令类型: {command.cmd_type}")
            command.status = CommandStatus.FAILED
            command.error_message = f"未知命令类型: {command.cmd_type}"
            self.database.update_command_status(command.command_id, CommandStatus.FAILED, command.error_message)

            # 触发命令失败回调
            self._trigger_callback("on_command_failed", command)

    def cancel_task(self, task_id: int) -> Dict[str, Any]:
        """登记取消；运行中任务由工作线程在检查点完成终态。"""
        with self._task_registry_lock:
            entry = self._active_tasks.get(task_id)
            if entry is None:
                cancelled = self._cancelled_task_generations.get(task_id)
                if cancelled is not None:
                    return {
                        "success": cancelled["success"],
                        "message": cancelled["message"],
                        "target_command_id": cancelled["target_command_id"],
                    }
                return {"success": False, "message": f"任务不存在或已结束: {task_id}"}

            command = entry["command"]
            if entry["state"] == "cancelled" or command.status == CommandStatus.CANCELLED:
                return {"success": True, "message": f"任务已取消: {task_id}"}
            if entry["state"] in ("running", "cancelling"):
                if not self.allow_running_task_cancel:
                    return {
                        "success": False,
                        "message": "运行中任务取消功能未启用，不支持取消正在运行的任务",
                    }
                entry["state"] = "cancelling"
                # cancel_event 是从调度器传到每个控制器原子动作的协作取消信号。
                entry["cancel_event"].set()
                return {
                    "pending": True,
                    "event": entry["cancel_complete_event"],
                    "entry": entry,
                    "target_command_id": command.command_id,
                }
            if entry["state"] == "terminalizing":
                return {"success": False, "message": "任务已开始提交终态"}
            if command.status in (CommandStatus.COMPLETED, CommandStatus.FAILED):
                return {"success": False, "message": f"任务已进入终态: {command.status.value}"}

            entry["cancel_event"].set()
            entry["state"] = "cancelled"
            command.status = CommandStatus.CANCELLED
            command.completed_at = datetime.now()
            command.error_message = "任务在等待队列中被取消"

            task = command.data
            task.status = TaskStatus.CANCELLED
            for station in task.station_list:
                if station.status not in (StationTaskStatus.COMPLETED, StationTaskStatus.FAILED):
                    station.status = StationTaskStatus.CANCELLED

            # 内存终态与代际墓碑在同一临界区先提交。原命令终态落盘失败时，
            # 任务仍不可执行，但该代际后续取消必须稳定返回相同失败结果。
            persistence_failure = f"任务已取消，但原任务命令终态落盘失败: {task_id}"
            tombstone = {
                "success": False,
                "message": persistence_failure,
                "target_command_id": command.command_id,
            }
            self._cancelled_task_generations[task_id] = tombstone
            try:
                self.database.update_command_status(
                    command.command_id,
                    CommandStatus.CANCELLED,
                    command.error_message,
                )
            except Exception:
                self.logger.exception(persistence_failure)
                return dict(tombstone)

            tombstone["success"] = True
            tombstone["message"] = f"等待任务取消成功: {task_id}"
            try:
                self.database.log_task_action(
                    str(task_id), "", "cancel_waiting_task", "cancelled",
                    f"command_id={command.command_id}",
                )
            except Exception:
                # 审计是非关键附属写，不反转已经持久化的取消终态。
                self.logger.exception(f"等待任务取消审计写入失败: {task_id}")
            return dict(tombstone)

    # 保留第二步 API 兼容性。
    cancel_waiting_task = cancel_task

    def _raise_if_task_cancelled(self, cancel_event=None, task_id=None):
        # 在站点/动作边界调用；禁止在硬件原子动作中途强行打断。
        if cancel_event is not None:
            if cancel_event.is_set():
                raise TaskCancelledError(f"任务已请求取消: {task_id}")
            return
        task = self.current_task
        if task is None:
            return
        with self._task_registry_lock:
            entry = self._active_tasks.get(task.task_id)
            if entry is not None and entry["cancel_event"].is_set():
                raise TaskCancelledError(f"任务已请求取消: {task.task_id}")

    def _cancel_aware_wait(
        self, timeout: float, cancel_event=None, task_id=None
    ):
        # 用 Event.wait 替代 sleep，使取消请求可以立即打断重试和轮询等待。
        if cancel_event is not None:
            if cancel_event.wait(timeout):
                raise TaskCancelledError(f"任务已请求取消: {task_id}")
            return
        task = self.current_task
        with self._task_registry_lock:
            entry = self._active_tasks.get(task.task_id) if task else None
            event = entry["cancel_event"] if entry else None
        if event is not None and event.wait(timeout):
            raise TaskCancelledError(f"任务已请求取消: {task.task_id}")
        self._raise_if_task_cancelled()

    def _command_execution_done(self, future, command: UnifiedCommand):
        """命令执行完成回调；任务终态选择在注册表锁内线性化。"""
        execution_error = None
        try:
            success = future.result()
        except Exception as e:
            success = False
            execution_error = e

        if command.cmd_type != CmdType.TASK_CMD:
            self._finalize_non_task_command(command, success, execution_error)
            return

        task = command.data
        with self._task_registry_lock:
            entry = self._active_tasks.get(task.task_id)
            if entry is None or entry["command"] is not command:
                return
            cancelled = entry["cancel_event"].is_set()
            if not cancelled and execution_error is None and not success and command.retry_count < command.max_retries:
                command.retry_count += 1
                command.status = CommandStatus.RETRYING
                entry["state"] = "queued"
                try:
                    self.database.add_command_retry_count(command.command_id)
                    self.database.update_command_status(command.command_id, CommandStatus.RETRYING)
                    self._trigger_callback("on_command_status_change", command)
                    self.command_queue.put(command)
                    self.current_command = None
                except Exception as error:
                    execution_error = error
                else:
                    return
            # 线性化点：此后新取消不再被接受；此前已提交的取消必然胜出。
            entry["state"] = "terminalizing"
            # terminalizing 是唯一终态决策线性化点：此后取消与完成不会互相覆盖。
            entry["terminal_decision"] = (
                CommandStatus.CANCELLED if cancelled else
                CommandStatus.COMPLETED if success and execution_error is None else
                CommandStatus.FAILED
            )

        self._finalize_task_terminal(command, entry, execution_error)

    def _finalize_task_terminal(self, command, entry, execution_error):
        task = command.data
        decision = entry["terminal_decision"]
        cancelled = decision == CommandStatus.CANCELLED
        controlled_stop_failed = isinstance(execution_error, ControlledStopError)
        reason = (
            f"任务已取消，但受控停止失败: {execution_error}"
            if cancelled and controlled_stop_failed else
            f"硬件状态无法确认: {execution_error}" if controlled_stop_failed else
            f"任务已请求取消: {task.task_id}" if cancelled else
            f"回调异常: {execution_error}" if execution_error else
            command.error_message or "命令执行失败"
        )
        cancel_result = entry.get("cancel_result") if entry.get("shutdown_notified") else None
        if controlled_stop_failed:
            # 无法确认硬件已停稳时阻塞后续调度，须人工确认并调用 clear_hardware_fault_block。
            self._hardware_fault_blocked = True
            self._hardware_fault_reason = reason
            self.logger.error(f"硬件状态不安全，调度已阻塞: {reason}")
        if cancelled and controlled_stop_failed and cancel_result is None:
            cancel_result = {
                "success": False,
                "message": reason,
                "target_command_id": command.command_id,
            }
        try:
            now = datetime.now()
            command.status = decision
            command.completed_at = now
            if cancelled:
                command.error_message = reason
                task.status = TaskStatus.CANCELLED
                task.completed_at = now
                for station in task.station_list:
                    if station.status != StationTaskStatus.COMPLETED:
                        station.status = StationTaskStatus.CANCELLED
                        station.completed_at = now
            elif decision == CommandStatus.FAILED:
                command.error_message = reason
            status_persisted = False
            try:
                self.database.update_command_status(
                    command.command_id, decision, command.error_message
                )
                status_persisted = True
            except Exception as error:
                self.logger.exception(f"任务终态落盘失败: {task.task_id}")
                if cancelled and cancel_result is None:
                    cancel_result = {"success": False, "message": f"运行任务已退出，但终态落盘失败: {task.task_id}", "target_command_id": command.command_id}
                else:
                    command.status = CommandStatus.FAILED
                    command.error_message = f"终态落盘失败: {error}"
            if status_persisted:
                if command.metadata:
                    try:
                        self.database.update_command_metadata(
                            command.command_id, command.metadata
                        )
                    except Exception:
                        # metadata 是附属写，不得反转已提交的关键终态。
                        self.logger.exception(
                            f"任务终态 metadata 落盘失败: {task.task_id}"
                        )
                if cancelled and cancel_result is None:
                    cancel_result = {"success": True, "message": f"运行任务取消成功: {task.task_id}", "target_command_id": command.command_id}
                elif not cancelled:
                    try:
                        self._trigger_callback(
                            "on_command_complete" if decision == CommandStatus.COMPLETED
                            else "on_command_failed",
                            command,
                        )
                    except Exception:
                        self.logger.exception(
                            f"任务终态回调失败: {task.task_id}"
                        )
        finally:
            with self._task_registry_lock:
                # 先写入代际墓碑并唤醒等待者，再释放 task_id；重复取消可稳定重放结果。
                if cancelled:
                    entry["cancel_result"] = cancel_result or {"success": False, "message": f"取消终态未知: {task.task_id}", "target_command_id": command.command_id}
                    self._cancelled_task_generations[task.task_id] = dict(entry["cancel_result"])
                    entry["cancel_complete_event"].set()
                current = self._active_tasks.get(task.task_id)
                if current is entry:
                    self._active_tasks.pop(task.task_id, None)

    def clear_hardware_fault_block(self):
        """由人工确认硬件安全并完成故障恢复后解除调度阻塞。"""
        self._hardware_fault_blocked = False
        self._hardware_fault_reason = None
        self.logger.warning("硬件故障调度阻塞已由外部恢复流程解除")

    def _finalize_non_task_command(self, command, success, execution_error):
        try:
            status = CommandStatus.COMPLETED if success and execution_error is None else CommandStatus.FAILED
            command.status = status
            command.completed_at = datetime.now()
            if execution_error:
                command.error_message = f"回调异常: {execution_error}"
            self.database.update_command_status(command.command_id, status, command.error_message)
            self._trigger_callback("on_command_complete" if success else "on_command_failed", command)
        except Exception:
            self.logger.exception(f"非任务命令终态处理失败: {command.command_id}")

    def _release_task_registration(self, command: UnifiedCommand):
        if command.cmd_type != CmdType.TASK_CMD or not isinstance(command.data, Task):
            return
        with self._task_registry_lock:
            entry = self._active_tasks.get(command.data.task_id)
            if entry is not None and entry["command"] is command:
                self._active_tasks.pop(command.data.task_id, None)

    # ==================== 命令类型的执行方法 ====================
   
    def _execute_task_command(self, command: UnifiedCommand) -> bool:
        """执行Task类型命令（更新版）

        Args:
            command: 统一命令对象

        Returns:
            bool: True=至少一个站点成功, False=所有站点失败
        """
        task = command.data
        if not isinstance(task, Task):
            self.logger.error(f"命令数据类型错误，期望Task，实际: {type(task)}")
            command.error_message = f"数据类型错误: {type(task)}"
            return False

        with self._task_registry_lock:
            entry = self._active_tasks.get(task.task_id)
            if entry is None or entry["command"] is not command:
                raise RuntimeError(f"任务注册信息不存在: {task.task_id}")
            cancel_event = entry["cancel_event"]

        self._raise_if_task_cancelled(cancel_event, task.task_id)
        # 设置任务状态为运行中
        task.status = TaskStatus.RUNNING
        self.logger.info(f"任务开始执行: {task.task_id}, 任务名称: {task.task_name}")
        # if task.robot_mode == RobotMode.INSPECTION:
        #     self.voice_player.play("收到巡检任务啦.mp3")
        # 触发任务开始回调
        self._trigger_callback("on_task_start", task)

        # 执行任务
        success = self._execute_task_internal(task, cancel_event)
        self._raise_if_task_cancelled(cancel_event, task.task_id)

        # 判断任务状态
        task_status = self._determine_task_status(task)
        task.status = task_status

        # 统计站点结果
        total = len(task.station_list)
        success_count = sum(1 for s in task.station_list if s.status == StationTaskStatus.COMPLETED)
        failed_count = sum(1 for s in task.station_list if s.status == StationTaskStatus.FAILED)

        # 更新 command metadata
        if not command.metadata:
            command.metadata = {}

        command.metadata.update({
            "total_stations": total,
            "success_stations": success_count,
            "failed_stations": failed_count,
            "failed_station_ids": [
                s.station_config.station_id
                for s in task.station_list
                if s.status == StationTaskStatus.FAILED
            ]
        })

        # 设置错误信息
        if failed_count > 0:
            if failed_count == total:
                command.error_message = f"任务失败: 所有 {total} 个站点均执行失败"
            else:
                command.error_message = f"任务部分失败: {success_count}/{total} 个站点成功"

        # 任务状态已在 unified_commands 表中通过 _command_execution_done 回调更新

        # 触发任务完成或失败回调
        if task_status in [TaskStatus.COMPLETED, TaskStatus.PARTIAL_COMPLETED]:
            self._trigger_callback("on_task_complete", task)
        else:
            self._trigger_callback("on_task_failed", task)

        return success

    def _execute_task_internal(self, task: Task, cancel_event=None) -> bool:
        """执行任务内部逻辑（重构版）

        改进：
        1. 清晰的循环逻辑（for 循环代替 while 循环）
        2. 统计站点成功/失败数量
        3. 返回值语义明确：True=至少一个站点成功, False=所有站点失败或异常

        Args:
            task: 任务对象

        Returns:
            bool: True=至少有一个站点成功, False=所有站点失败或异常
        """
        # 按照 sort 顺序排序站点
        sorted_stations = sorted(task.station_list, key=lambda s: s.station_config.sort)
        total_stations = len(sorted_stations)

        # 统计变量
        success_count = 0
        failed_count = 0

        try:
            self.logger.info(f"开始执行任务 {task.task_id}，共 {total_stations} 个站点")

            # 顺序执行所有站点任务（失败后继续）
            for i, station in enumerate(sorted_stations, 1):
                self._raise_if_task_cancelled(cancel_event, task.task_id)
                station_id = station.station_config.station_id
                self.logger.info(f"执行站点 {i}/{total_stations}: {station_id}")
                # if task.robot_mode == RobotMode.INSPECTION:
                #     self.voice_player.play(f"到达站点.mp3")
                # 执行站点（包含重试逻辑）
                if self._execute_station_task_with_retry(
                    station, cancel_event, task.task_id
                ):
                    success_count += 1
                    self.logger.info(f"✓ 站点 {station_id} 执行成功")
                else:
                    failed_count += 1
                    self.logger.warning(f"✗ 站点 {station_id} 执行失败，继续执行后续站点")
                # if task.robot_mode == RobotMode.INSPECTION:
                #     self.voice_player.play(f"巡检完成.mp3")
            # 输出汇总日志
            self.logger.info(
                f"任务 {task.task_id} 执行完成: "
                f"成功 {success_count}/{total_stations}, "
                f"失败 {failed_count}/{total_stations}"
            )

            self._raise_if_task_cancelled(cancel_event, task.task_id)
            # 返回值：至少有一个站点成功则返回 True
            return success_count > 0
        except (TaskCancelledError, ControlledStopError):
            raise
        except Exception as e:
            self.logger.error(f"任务执行异常: {e}")
            return False

    def _execute_station_task_with_retry(
        self, station: Station, cancel_event=None, task_id=None
    ) -> bool:
        """执行站点任务（包含自动重试逻辑）

        功能：
        1. 首次执行站点任务
        2. 如果失败，自动重试（最多 max_retries 次）
        3. 达到最大重试次数后，标记为失败

        Args:
            station: 站点对象

        Returns:
            bool: True=站点最终成功, False=站点最终失败
        """
        station_id = station.station_config.station_id
        max_attempts = station.max_retries + 1  # 首次执行 + 重试次数

        for attempt in range(max_attempts):
            self._raise_if_task_cancelled(cancel_event, task_id)
            # 判断是否为重试
            if attempt > 0:
                station.retry_count = attempt
                station.status = StationTaskStatus.RETRYING

                # 记录重试日志
                self.database.log_task_action(
                    self.current_task.task_id,
                    station_id,
                    "retry",
                    "retrying",
                    f"站点执行重试, 第 {attempt}/{station.max_retries} 次重试"
                )

                # 触发站点重试回调
                self._trigger_callback("on_station_retry", station)

                self.logger.warning(f"站点 {station_id} 第 {attempt}/{station.max_retries} 次重试")
                self._cancel_aware_wait(1, cancel_event, task_id)  # 重试间隔

            # 执行站点任务
            if self._execute_station_task(station, cancel_event, task_id):
                # 成功
                if attempt > 0:
                    self.logger.info(f"站点 {station_id} 重试成功（第 {attempt} 次重试）")
                return True

        # 达到最大重试次数，仍然失败
        return self._mark_station_failed(
            station,
            f"达到最大重试次数 ({station.max_retries})"
        )

    def _mark_station_failed(self, station: Station, reason: str) -> bool:
        """将站点标记为失败，并更新数据库

        Args:
            station: 站点对象
            reason: 失败原因描述

        Returns:
            bool: 始终返回 False，表示站点失败
        """
        station_id = station.station_config.station_id

        # 更新站点状态
        station.status = StationTaskStatus.FAILED
        station.completed_at = datetime.now()
        station.error_message = reason

        # 记录失败日志
        self.database.log_task_action(
            self.current_task.task_id,
            station_id,
            "error",
            "failed",
            f"站点执行失败: {reason}"
        )

        self.logger.error(f"站点 {station_id} 最终失败: {reason}")

        # 返回 False 表示站点失败
        return False

    def _execute_station_task(
        self, station: Station, cancel_event=None, task_id=None
    ) -> bool:
        """执行单个站点任务（添加细粒度进度更新）"""
        try:
            self._raise_if_task_cancelled(cancel_event, task_id)
            self.current_station = station
            station_id = station.station_config.station_id

            # 更新站点状态为运行中
            station.status = StationTaskStatus.RUNNING
            station.execution_phase = StationExecutionPhase.PENDING
            station.started_at = datetime.now()

            # 记录执行日志
            self.database.log_task_action(
                self.current_task.task_id,
                station_id,
                "start",
                "running",
                "开始执行站点任务"
            )

            # 触发站点开始回调
            self._trigger_callback("on_station_start", station)

            # === 阶段 1: 移动 AGV ===
            station.execution_phase = StationExecutionPhase.AGV_MOVING
            station.progress_detail = f"AGV 移动到标记点 {station.station_config.agv_marker}"
            self.logger.debug(f"[站点 {station_id}] 阶段: AGV_MOVING - {station.progress_detail}")

            # 触发进度更新回调
            self._trigger_callback(
                "on_station_progress",
                station=station,
                command_id=self.current_command.command_id if self.current_command else None
            )

            success = self.robot_controller.move_to_marker(
                station.station_config.agv_marker,
                cancel_event=cancel_event,
            )
            self._raise_if_task_cancelled(cancel_event, task_id)
            if not success:
                station.execution_phase = StationExecutionPhase.FAILED
                station.error_message = f"AGV 移动失败: {station.station_config.agv_marker}"
                self.logger.error(station.error_message)
                return False


            # === 阶段 3: 移动外部轴 ===
            if station.station_config.ext_pos:
                
                station.execution_phase = StationExecutionPhase.EXT_POSITIONING
                station.progress_detail = f"外部轴移动到归位位置 {station.station_config.ext_pos}"
                self.logger.debug(f"[站点 {station_id}] 阶段: EXT_POSITIONING - {station.progress_detail}")

                # 触发进度更新回调
                self._trigger_callback(
                    "on_station_progress",
                    station=station,
                    command_id=self.current_command.command_id if self.current_command else None
                )

                success = self.robot_controller.move_ext_to_position(
                    station.station_config.ext_pos,
                    cancel_event=cancel_event,
                )
                self._raise_if_task_cancelled(cancel_event, task_id)
                if not success:
                    station.execution_phase = StationExecutionPhase.FAILED
                    station.error_message = "外部轴移动失败"
                    self.logger.error(station.error_message)
                    return False

            # === 阶段 2: 移动机械臂 ===
            if station.station_config.robot_pos:
                station.execution_phase = StationExecutionPhase.ARM_POSITIONING
                station.progress_detail = f"机械臂移动到归位位置 {station.station_config.robot_pos}"
                self.logger.debug(f"[站点 {station_id}] 阶段: ARM_POSITIONING - {station.progress_detail}")

                # 触发进度更新回调
                self._trigger_callback(
                    "on_station_progress",
                    station=station,
                    command_id=self.current_command.command_id if self.current_command else None
                )

                success = self.robot_controller.move_robot_to_position(
                    station.station_config.robot_pos,
                    cancel_event=cancel_event,
                )
                self._raise_if_task_cancelled(cancel_event, task_id)
                if not success:
                    station.execution_phase = StationExecutionPhase.FAILED
                    station.error_message = "机械臂移动失败"
                    self.logger.error(station.error_message)
                    return False

            # === 阶段 4: 执行操作 ===
            if station.station_config.operation_config.operation_mode != OperationMode.NONE:
                operation_mode = station.station_config.operation_config.operation_mode
                station.execution_phase = StationExecutionPhase.OPERATING
                station.progress_detail = f"执行操作: {operation_mode.value}"
                self.logger.debug(f"[站点 {station_id}] 阶段: OPERATING - {station.progress_detail}")

                # 触发进度更新回调
                self._trigger_callback(
                    "on_station_progress",
                    station=station,
                    command_id=self.current_command.command_id if self.current_command else None
                )

                operation_result = self._execute_operation(
                    station.station_config.operation_config
                )
                self._raise_if_task_cancelled(cancel_event, task_id)

                # 保存操作结果到metadata
                if not station.metadata:
                    station.metadata = {}
                station.metadata['operation_result'] = operation_result

                # 检查操作是否成功
                if not operation_result.get('success', False):
                    station.execution_phase = StationExecutionPhase.FAILED
                    station.error_message = f"操作失败: {operation_result.get('message')}"
                    self.logger.error(station.error_message)
                    return False

            # === 阶段5 === 机械臂和外部轴复位
            self._raise_if_task_cancelled(cancel_event, task_id)
            self.robot_controller.move_robot_to_position(
                  [0,1.784529347,-0.2298249559,1.5707963268,-1.5105126544,0.7853981634],
                  cancel_event=cancel_event,
                )
            self._raise_if_task_cancelled(cancel_event, task_id)
            self.robot_controller.move_ext_to_position(
                    [10,0,0,0],
                    cancel_event=cancel_event,
                )
            self._raise_if_task_cancelled(cancel_event, task_id)
            # === 完成 ===
            station.execution_phase = StationExecutionPhase.COMPLETED
            station.status = StationTaskStatus.COMPLETED
            station.progress_detail = "站点任务完成"
            station.completed_at = datetime.now()

            # 记录执行日志
            self.database.log_task_action(
                self.current_task.task_id,
                station_id,
                "complete",
                "completed",
                "站点任务完成"
            )

            # 触发站点完成回调
            self._trigger_callback("on_station_complete", station)

            return True

        except (TaskCancelledError, ControlledStopError):
            raise
        except Exception as e:
            station.execution_phase = StationExecutionPhase.FAILED
            station.error_message = f"站点执行异常: {str(e)}"
            self.logger.exception(f"站点 {station_id} 执行异常")
            return False

    def _execute_operation(self, operation_config: OperationConfig) -> Dict[str, Any]:
        """执行特定操作（返回详细结果）"""
        operation_mode = operation_config.operation_mode

        if operation_mode == OperationMode.OPEN_DOOR:
            result = self._open_door(operation_config.door_ip)
        elif operation_mode == OperationMode.CLOSE_DOOR:
            result = self._close_door(operation_config.door_ip)
        elif operation_mode == OperationMode.CAPTURE:
            result = self._capture(operation_config.device_id)
        elif operation_mode == OperationMode.SERVE:
            result = self._guide_serve()
        else:
            result = {
                'success': True,
                'message': f'跳过未知操作: {operation_mode}',
                'timestamp': time.time(),
                'duration': 0.0
            }

        # 触发操作结果回调，直接传入 task/station/command_id，避免后续再查快照
        self._trigger_callback(
            "on_operation_result",
            operation_data={
                'operation_mode': operation_config.operation_mode,
                'result': result,
                'timestamp': time.time()
            },
            task=self.current_task,
            station=self.current_station,
            command_id=self.current_command.command_id if self.current_command else None
        )

        return result
    
    def _determine_task_status(self, task: Task) -> TaskStatus:
        """根据站点执行结果判断任务状态

        判断规则：
        - 所有站点成功 → COMPLETED
        - 部分站点成功 → PARTIAL_COMPLETED
        - 所有站点失败 → FAILED

        Args:
            task: 任务对象

        Returns:
            TaskStatus: 任务最终状态
        """
        total = len(task.station_list)
        completed = sum(1 for s in task.station_list if s.status == StationTaskStatus.COMPLETED)
        failed = sum(1 for s in task.station_list if s.status == StationTaskStatus.FAILED)

        if completed == total:
            # 所有站点成功
            return TaskStatus.COMPLETED
        elif failed == total:
            # 所有站点失败
            return TaskStatus.FAILED
        else:
            # 部分成功
            return TaskStatus.PARTIAL_COMPLETED

    def _execute_mode_command(self, command: UnifiedCommand) -> bool:
        """执行模式切换命令"""
        try:
            data_json = command.data
            robot_mode_cmd = data_json.get('robot_mode_cmd', {})
            new_mode = RobotMode(robot_mode_cmd.get('robot_mode'))

            self.logger.info(f"执行模式切换命令: {new_mode.value}")

            # 调用机器人控制器切换模式（如果有相应方法）
            # 这里简单记录日志，实际可能需要调用机器人控制器的方法
            # success = self.robot_controller.set_mode(new_mode)

            # 目前仅记录日志
            self.logger.info(f"机器人模式已切换为: {new_mode.value}")
            return True

        except Exception as e:
            self.logger.error(f"执行模式切换命令失败: {e}")
            command.error_message = str(e)
            return False

    def _execute_joy_command(self, command: UnifiedCommand) -> bool:
        """执行摇杆控制命令"""
        try:
            data_json = command.data
            joy_control_cmd = data_json.get('joy_control_cmd', {})

            self.logger.info(f"执行摇杆控制命令: {joy_control_cmd}")

            # 调用机器人控制器的摇杆控制方法
            success = self.robot_controller.joy_control(data_json)
            return success
            #TODO：如果返回False，command.error_message 中也应做相应的记录

        except Exception as e:
            self.logger.error(f"执行摇杆控制命令失败: {e}")
            command.error_message = str(e)
            return False

    def _execute_charge_command(self, command: UnifiedCommand) -> bool:
        """执行充电命令 (Trigger信号，无需参数)"""
        try:
            self.logger.info("执行充电命令 (Trigger信号)")
            success = self.robot_controller.charge()

            if not success:
                command.error_message = "充电命令执行失败"

            return success

        except Exception as e:
            self.logger.error(f"执行充电命令失败: {e}")
            command.error_message = str(e)
            return False

    def _execute_set_marker_command(self, command: UnifiedCommand) -> bool:
        """执行设置标记命令"""
        try:
            data_json = command.data
            set_marker_cmd = data_json.get('set_marker_cmd', {})
            marker_id = set_marker_cmd.get('marker_name', '')

            if marker_id:
                self.logger.info(f"执行设置标记命令: {marker_id}")
                #TODO：如果返回False，command.error_message 中也应做相应的记录
                success = self.robot_controller.set_marker(marker_id)
                return success
            else:
                self.logger.warning("未指定标记ID")
                command.error_message = "未指定标记ID"
                return False

        except Exception as e:
            self.logger.error(f"执行设置标记命令失败: {e}")
            command.error_message = str(e)
            return False

    def _execute_position_adjust_command(self, command: UnifiedCommand) -> bool:
        """执行位置调整命令 (Trigger信号，无需参数，使用默认充电桩位置)"""
        try:
            self.logger.info("执行位置调整命令 (Trigger信号)")
            success = self.robot_controller.position_adjust(marker_id='charge_point_1F_6010')

            if not success:
                command.error_message = "位置调整命令执行失败"

            return success

        except Exception as e:
            self.logger.error(f"执行位置调整命令失败: {e}")
            command.error_message = str(e)
            return False

    # ==================== 回调函数 ====================

    def register_callback(self, event: str, callback: Callable):
        """注册回调函数"""
        if event in self.task_callbacks:
            self.task_callbacks[event] = callback

    def _trigger_callback(self, event: str, *args, **kwargs):
        """触发回调函数"""
        callback = self.task_callbacks.get(event)
        if callback:
            try:
                callback(*args, **kwargs)
            except Exception as e:
                self.logger.error(f"回调函数执行异常: {e}")

    def _capture(self, device_id: str) -> Dict[str, Any]:
        """捕获操作实现（返回详细结果）"""
        try:
            self.logger.info(f"执行捕获操作: {device_id}")
            result = self.robot_controller.capture(device_id)
            return result
        except Exception as e:
            self.logger.error(f"捕获操作失败: {e}")
            return {
                'success': False,
                'images': [],
                'message': f'捕获操作异常: {str(e)}',
                'device_id': device_id,
                'timestamp': time.time(),
                'duration': 0.0
            }

    def _open_door(self, door_ip: str) -> Dict[str, Any]:
        """开门操作实现（返回详细结果）"""
        try:
            self.logger.info(f"执行开门操作: {door_ip}")
            result = self.robot_controller.open_door(door_ip)
            return result
        except Exception as e:
            self.logger.error(f"开门操作失败: {e}")
            return {
                'success': False,
                'message': f'开门操作异常: {str(e)}',
                'door_ip': door_ip,
                'timestamp': time.time(),
                'duration': 0.0
            }

    def _close_door(self, door_ip: str) -> Dict[str, Any]:
        """关门操作实现（返回详细结果）"""
        try:
            self.logger.info(f"执行关门操作: {door_ip}")
            result = self.robot_controller.close_door(door_ip)
            return result
        except Exception as e:
            self.logger.error(f"关门操作失败: {e}")
            return {
                'success': False,
                'message': f'关门操作异常: {str(e)}',
                'door_ip': door_ip,
                'timestamp': time.time(),
                'duration': 0.0
            }

    def _guide_serve(self) -> Dict[str, Any]:
        """服务操作实现（返回详细结果）"""
        try:
            self.logger.info(f"执行服务操作")
            result = self.robot_controller.guide_serve()
            return result
        except Exception as e:
            self.logger.error(f"服务操作失败: {e}")
            return {
                'success': False,
                'message': f'讲解服务操作异常: {str(e)}',
                'timestamp': time.time(),
            }

