import threading
import queue
import time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Optional, List, Dict, Any
from utils.logger_config import get_logger
from task.TaskDatabase import TaskDatabase
from dataModels.TaskModels import (
    Task, Station, StationConfig, OperationMode,
    TaskStatus, StationTaskStatus, StationExecutionPhase, OperationConfig, RobotMode
)
from dataModels.UnifiedCommand import UnifiedCommand, CommandStatus, CommandCategory
from dataModels.CommandModels import CmdType

class TaskScheduler:
    """任务调度器 - 负责任务的调度和执行（支持统一命令队列）"""

    def __init__(self, robot_controller, database: TaskDatabase):
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

        # 回调函数注册
        self.task_callbacks = {
            "on_task_start": [],
            "on_task_complete": [],
            "on_task_failed": [],
            "on_station_start": [],
            "on_station_complete": [],
            "on_station_retry": [],
            "on_station_progress": [],       # 站点进度更新回调
            "on_command_complete": [],       # 命令完成回调
            "on_command_failed": [],         # 命令失败回调
            "on_command_status_change": []   # 命令状态变化回调（RUNNING/RETRYING等）
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
        if self.scheduler_thread:
            self.scheduler_thread.join(timeout=5)
        self.executor.shutdown(wait=False)
        self.logger.info("任务调度器已停止")
    
    def add_command(self, command: UnifiedCommand):
        """添加命令到队列（新接口，支持所有命令类型）"""
        # 更新命令状态为已加入队列
        command.status = CommandStatus.QUEUED

        self.command_queue.put(command)
        # 保存命令到数据库
        self.database.save_command(command)

        # 触发命令状态变化回调（QUEUED）
        self._trigger_callback("on_command_status_change", command)

        self.logger.info(f"命令 {command.command_id} (类型: {command.cmd_type.value}) 已添加到队列，优先级: {command.priority}")

    def _scheduler_loop(self):
        """调度器主循环（支持统一命令队列）"""
        while self.is_running:
            try:
                if self.current_command is None:
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

    def _command_execution_done(self, future, command: UnifiedCommand):
        """命令执行完成回调"""
        try:
            success = future.result()

            if success:
                command.status = CommandStatus.COMPLETED
                command.completed_at = datetime.now()
                self.database.update_command_status(command.command_id, CommandStatus.COMPLETED)

                # 保存元数据（如果有）
                if command.metadata:
                    self.database.update_command_metadata(command.command_id, command.metadata)

                # 保存错误信息（如果有，用于 PARTIAL_COMPLETED 情况）
                if command.error_message:
                    self.database.update_command_status(
                        command.command_id,
                        CommandStatus.COMPLETED,
                        command.error_message
                    )

                self._trigger_callback("on_command_complete", command)
                self.logger.info(f"命令 {command.command_id} 执行完成")
            else:
                # 检查是否需要重试
                if command.retry_count < command.max_retries:
                    command.retry_count += 1
                    command.status = CommandStatus.RETRYING
                    self.database.add_command_retry_count(command.command_id)
                    self.database.update_command_status(command.command_id, CommandStatus.RETRYING)

                    # 触发命令状态变化回调（RETRYING）
                    self._trigger_callback("on_command_status_change", command)

                    self.logger.info(f"命令 {command.command_id} 将进行第 {command.retry_count} 次重试")
                    time.sleep(1)
                    # 重新加入队列
                    self.command_queue.put(command)
                    self.current_command = None
                else:
                    command.status = CommandStatus.FAILED
                    command.completed_at = datetime.now()
                    command.error_message = command.error_message or "命令执行失败"
                    self.database.update_command_status(
                        command.command_id,
                        CommandStatus.FAILED,
                        command.error_message
                    )

                    # 保存元数据（如果有）
                    if command.metadata:
                        self.database.update_command_metadata(command.command_id, command.metadata)

                    self._trigger_callback("on_command_failed", command)
                    self.logger.error(f"命令 {command.command_id} 执行失败")

        except Exception as e:
            self.logger.error(f"命令执行回调异常: {e}")
            command.status = CommandStatus.FAILED
            command.completed_at = datetime.now()
            command.error_message = f"回调异常: {str(e)}"
            self.database.update_command_status(
                command.command_id,
                CommandStatus.FAILED,
                command.error_message
            )

            # 保存元数据（如果有）
            if command.metadata:
                self.database.update_command_metadata(command.command_id, command.metadata)

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

        # 设置任务状态为运行中
        task.status = TaskStatus.RUNNING
        self.logger.info(f"任务开始执行: {task.task_id}, 任务名称: {task.task_name}")

        # 触发任务开始回调
        self._trigger_callback("on_task_start", task)

        # 执行任务
        success = self._execute_task_internal(task)

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

    def _execute_task_internal(self, task: Task) -> bool:
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
                station_id = station.station_config.station_id
                self.logger.info(f"执行站点 {i}/{total_stations}, 当前站点ID {station_id}")

                # 执行站点（包含重试逻辑）
                if self._execute_station_task_with_retry(station):
                    success_count += 1
                    self.logger.info(f"✓ 站点 {station_id} 执行成功")
                else:
                    failed_count += 1
                    self.logger.warning(f"✗ 站点 {station_id} 执行失败，继续执行后续站点")

            # 输出汇总日志
            self.logger.info(
                f"任务 {task.task_id} 执行完成: "
                f"成功 {success_count}/{total_stations}, "
                f"失败 {failed_count}/{total_stations}"
            )

            # 返回值：至少有一个站点成功则返回 True
            return success_count > 0

        except Exception as e:
            self.logger.error(f"任务执行异常: {e}")
            return False

    def _execute_station_task_with_retry(self, station: Station) -> bool:
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

                self.logger.info(f"站点 {station_id} 第 {attempt} 次重试")
                time.sleep(1)  # 重试间隔

            # 执行站点任务
            if self._execute_station_task(station):
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

    def _execute_station_task(self, station: Station) -> bool:
        """执行单个站点任务（添加细粒度进度更新）"""
        try:
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
            self.logger.info(f"[站点 {station_id}] {station.progress_detail}")

            # 触发进度更新回调
            self._trigger_callback(
                "on_station_progress",
                station=station,
                command_id=self.current_command.command_id if self.current_command else None
            )

            success = self.robot_controller.move_to_marker(
                station.station_config.agv_marker
            )
            if not success:
                station.execution_phase = StationExecutionPhase.FAILED
                station.error_message = f"AGV 移动失败: {station.station_config.agv_marker}"
                self.logger.error(station.error_message)
                return False

            # === 阶段 2: 移动机械臂 ===
            station.execution_phase = StationExecutionPhase.ARM_POSITIONING
            station.progress_detail = f"机械臂移动到归位位置 {station.station_config.robot_pos}"
            self.logger.info(f"[站点 {station_id}] {station.progress_detail}")

            # 触发进度更新回调
            self._trigger_callback(
                "on_station_progress",
                station=station,
                command_id=self.current_command.command_id if self.current_command else None
            )

            success = self.robot_controller.move_robot_to_position(
                station.station_config.robot_pos
            )
            if not success:
                station.execution_phase = StationExecutionPhase.FAILED
                station.error_message = "机械臂移动失败"
                self.logger.error(station.error_message)
                return False

            # === 阶段 3: 移动外部轴 ===
            station.execution_phase = StationExecutionPhase.EXT_POSITIONING
            station.progress_detail = f"外部轴移动到归位位置 {station.station_config.ext_pos}"
            self.logger.info(f"[站点 {station_id}] {station.progress_detail}")

            # 触发进度更新回调
            self._trigger_callback(
                "on_station_progress",
                station=station,
                command_id=self.current_command.command_id if self.current_command else None
            )

            success = self.robot_controller.move_ext_to_position(
                station.station_config.ext_pos
            )
            if not success:
                station.execution_phase = StationExecutionPhase.FAILED
                station.error_message = "外部轴移动失败"
                self.logger.error(station.error_message)
                return False

            # === 阶段 4: 执行操作 ===
            if station.station_config.operation_config.operation_mode != OperationMode.NONE:
                operation_mode = station.station_config.operation_config.operation_mode
                station.execution_phase = StationExecutionPhase.OPERATING
                station.progress_detail = f"执行操作: {operation_mode.value}"
                self.logger.info(f"[站点 {station_id}] {station.progress_detail}")

                # 触发进度更新回调
                self._trigger_callback(
                    "on_station_progress",
                    station=station,
                    command_id=self.current_command.command_id if self.current_command else None
                )

                operation_result = self._execute_operation(
                    station.station_config.operation_config
                )

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
            result = self._serve(operation_config.device_id)
        else:
            result = {
                'success': True,
                'message': f'跳过未知操作: {operation_mode}',
                'timestamp': time.time(),
                'duration': 0.0
            }

        # 触发操作结果回调
        # operation_data 只包含操作特定数据，task_id/station_id/command_id从快照获取
        self._trigger_callback(
            "on_operation_result",
            operation_data={
                'operation_mode': operation_config.operation_mode,
                'result': result,
                'timestamp': time.time()
            }
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
            self.task_callbacks[event].append(callback)
    
    def _trigger_callback(self, event: str, *args, **kwargs):
        """触发回调函数"""
        for callback in self.task_callbacks.get(event, []):
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

    def _serve(self, device_id: str) -> Dict[str, Any]:
        """服务操作实现（返回详细结果）"""
        try:
            self.logger.info(f"执行服务操作: {device_id}")
            result = self.robot_controller.serve(device_id)
            # 触发回调：通知TaskManager到达服务站点
            self._trigger_callback(
                "on_arrive_service_station",
                device_id=device_id
            )
            return result
        except Exception as e:
            self.logger.error(f"服务操作失败: {e}")
            return {
                'success': False,
                'message': f'服务操作异常: {str(e)}',
                'device_id': device_id,
                'timestamp': time.time(),
                'duration': 0.0
            }

