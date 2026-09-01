import tempfile
import threading
import time
import unittest
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

from dataModels.CommandModels import CmdType
from dataModels.TaskModels import (
    OperationConfig, OperationMode, Station, StationConfig,
    StationTaskStatus, Task, TaskStatus,
)
from dataModels.UnifiedCommand import CommandStatus, create_unified_command
import gRPC.RobotService_pb2 as robot_pb2
from robot.MockRobotController import MockRobotController
from robot.AGVController import AGVController
from robot.ArmController import ArmController
from robot.HardwareErrors import ControlledStopError
from RobotControlSystem import RobotControlSystem
from task.TaskDatabase import TaskDatabase
from task.TaskManager import TaskManager
from task.TaskScheduler import TaskCancelledError, TaskScheduler
from utils.logger_config import get_logger


class CancelRunningTaskTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database = TaskDatabase(str(Path(self.temp_dir.name) / "tasks.db"))
        self.robot = MockRobotController(
            {"success_rate": 1.0, "latency": 0.0}, auto_setup=False
        )
        self.robot._system_initialized = True
        self.scheduler = TaskScheduler(
            self.robot, self.database, allow_running_task_cancel=True
        )
        self.scheduler.start()
        self.feedback = []
        self.manager = TaskManager.__new__(TaskManager)
        self.manager.database = self.database
        self.manager.scheduler = self.scheduler
        self.manager.logger = get_logger(__name__)
        self.manager.system_callbacks = {
            "on_command_status_change": lambda **kw: self.feedback.append(kw["command"]),
        }

    def tearDown(self):
        self.scheduler.stop()
        if hasattr(self.manager, "_cancel_coordinator"):
            self.manager._cancel_coordinator.shutdown(wait=True, cancel_futures=True)
        self.temp_dir.cleanup()

    def make_task(self, task_id=1, marker="marker_1", stations=1, retries=0):
        items = []
        for index in range(stations):
            items.append(Station(
                StationConfig(
                    station_id=index + 1,
                    sort=index,
                    name=f"s{index + 1}",
                    agv_marker=marker,
                    robot_pos=[],
                    ext_pos=[],
                    operation_config=OperationConfig(OperationMode.NONE, None, None),
                ),
                max_retries=retries,
            ))
        return Task(task_id=task_id, task_name=f"task-{task_id}", station_list=items)

    def wait_for(self, predicate, timeout=3):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if predicate():
                return True
            time.sleep(0.01)
        return False

    def test_running_cancel_waits_for_atomic_action_then_stops_following_stages(self):
        release = threading.Event()
        started = self.robot.set_action_gate("move_to_marker_1", release)
        task = self.make_task(stations=2)
        command = create_unified_command("task-1", CmdType.TASK_CMD, task)
        self.scheduler.add_command(command)
        self.assertTrue(started.wait(2))

        result_box = {}
        cancel_thread = threading.Thread(
            target=lambda: result_box.setdefault(
                "result", self.manager.request_cancel_task("cancel-1", 1)
            )
        )
        cancel_thread.start()
        self.assertTrue(self.wait_for(lambda: self.scheduler._active_tasks[1]["cancel_event"].is_set()))
        self.assertIs(self.scheduler.current_command, command)
        self.assertNotIn("move_to_marker_1", self.robot.action_history[1:])

        release.set()
        cancel_thread.join(3)
        self.assertFalse(cancel_thread.is_alive())
        self.assertEqual(result_box["result"].status, CommandStatus.COMPLETED)
        self.assertEqual(command.status, CommandStatus.CANCELLED)
        self.assertEqual(task.status, TaskStatus.CANCELLED)
        self.assertEqual(task.station_list[0].status, StationTaskStatus.CANCELLED)
        self.assertEqual(task.station_list[1].status, StationTaskStatus.CANCELLED)
        self.assertEqual(self.robot.action_history.count("move_to_marker_1"), 1)
        self.assertEqual(self.robot.robot_joints, [0.0] * 6)
        self.assertEqual(self.robot.action_history.count("move_robot"), 1)
        self.assertEqual(len(self.feedback), 1)

    def test_next_task_does_not_start_before_cancelled_worker_exits(self):
        release = threading.Event()
        started = self.robot.set_action_gate("move_to_marker_1", release)
        first = create_unified_command("task-1", CmdType.TASK_CMD, self.make_task(1))
        second = create_unified_command("task-2", CmdType.TASK_CMD, self.make_task(2, "marker_2"))
        self.scheduler.add_command(first)
        self.scheduler.add_command(second)
        self.assertTrue(started.wait(2))
        cancel = threading.Thread(target=self.manager.request_cancel_task, args=("cancel-1", 1))
        cancel.start()
        time.sleep(0.1)
        self.assertNotIn("move_to_marker_2", self.robot.action_history)
        release.set()
        cancel.join(3)
        self.assertTrue(self.wait_for(lambda: second.status == CommandStatus.COMPLETED))
        self.assertTrue(self.wait_for(lambda: 2 not in self.scheduler._active_tasks))

    def test_feature_flag_disabled_rejects_running_cancel(self):
        self.scheduler.allow_running_task_cancel = False
        task = self.make_task()
        command = create_unified_command("task-1", CmdType.TASK_CMD, task)
        self.scheduler.add_command(command)
        with self.scheduler._task_registry_lock:
            self.scheduler._active_tasks[1]["state"] = "running"
            command.status = CommandStatus.RUNNING
        result = self.manager.request_cancel_task("cancel-disabled", 1)
        self.assertEqual(result.status, CommandStatus.FAILED)
        self.assertFalse(self.scheduler._active_tasks[1]["cancel_event"].is_set())

    def test_multiple_cancel_ids_share_one_running_cancel_result(self):
        release = threading.Event()
        started = self.robot.set_action_gate("move_to_marker_1", release)
        command = create_unified_command("task-1", CmdType.TASK_CMD, self.make_task())
        self.scheduler.add_command(command)
        self.assertTrue(started.wait(2))
        results = []
        threads = [threading.Thread(
            target=lambda cid=cid: results.append(self.manager.request_cancel_task(cid, 1))
        ) for cid in ("cancel-a", "cancel-b")]
        for thread in threads:
            thread.start()
        self.assertTrue(self.wait_for(lambda: self.scheduler._active_tasks[1]["cancel_event"].is_set()))
        release.set()
        for thread in threads:
            thread.join(3)
        self.assertEqual([item.status for item in results], [CommandStatus.COMPLETED] * 2)
        self.assertEqual(len(self.feedback), 2)

    def test_cancel_interrupts_station_retry_wait(self):
        self.robot.set_error_scenario("communication_error", True)
        retry_started = threading.Event()
        self.scheduler.register_callback(
            "on_station_retry", lambda _station: retry_started.set()
        )
        command = create_unified_command(
            "task-1", CmdType.TASK_CMD, self.make_task(retries=3)
        )
        self.scheduler.add_command(command)
        self.assertTrue(retry_started.wait(2))
        # 通信故障只用于触发重试；取消终态新增的安全姿态恢复必须能正常执行。
        self.robot.set_error_scenario("communication_error", False)
        started_at = time.monotonic()
        result = self.manager.request_cancel_task("cancel-retry", 1)
        self.assertLess(time.monotonic() - started_at, 0.8)
        self.assertEqual(result.status, CommandStatus.COMPLETED)
        self.assertEqual(command.retry_count, 0)
        self.assertEqual(command.status, CommandStatus.CANCELLED)

    def test_safe_pose_recovery_failure_fails_cancel_and_blocks_scheduler(self):
        release = threading.Event()
        started = self.robot.set_action_gate("move_to_marker_1", release)
        command = create_unified_command(
            "task-safe-pose-failure", CmdType.TASK_CMD, self.make_task()
        )
        self.scheduler.add_command(command)
        self.assertTrue(started.wait(2))
        self.robot.recover_transport_safe_pose = lambda _joints: False

        result_box = {}
        cancel_thread = threading.Thread(
            target=lambda: result_box.setdefault(
                "result",
                self.manager.request_cancel_task("cancel-safe-pose-failure", 1),
            )
        )
        cancel_thread.start()
        self.assertTrue(self.wait_for(
            lambda: self.scheduler._active_tasks[1]["cancel_event"].is_set()
        ))
        release.set()
        cancel_thread.join(3)

        self.assertFalse(cancel_thread.is_alive())
        self.assertEqual(result_box["result"].status, CommandStatus.FAILED)
        self.assertEqual(command.status, CommandStatus.CANCELLED)
        self.assertTrue(self.scheduler.is_hardware_fault_blocked())

    def test_successful_robot_reinitialization_clears_hardware_fault_block(self):
        self.scheduler._hardware_fault_blocked = True
        self.scheduler._hardware_fault_reason = "安全姿态恢复失败"
        self.manager._hardware_lock = threading.RLock()
        self.manager._hardware_status = {
            "robot": False, "camera": False, "env_sensor": False,
        }
        self.manager.robot_controller = self.robot
        self.robot.setup_system = lambda: True

        result = self.manager.start_hardware(robot=True)

        self.assertTrue(result["robot"]["success"])
        self.assertIn("调度阻塞已解除", result["robot"]["message"])
        self.assertFalse(self.scheduler.is_hardware_fault_blocked())

    def test_starting_already_running_robot_does_not_clear_fault_block(self):
        self.scheduler._hardware_fault_blocked = True
        self.scheduler._hardware_fault_reason = "安全姿态恢复失败"
        self.manager._hardware_lock = threading.RLock()
        self.manager._hardware_status = {
            "robot": True, "camera": False, "env_sensor": False,
        }
        self.manager.robot_controller = self.robot

        result = self.manager.start_hardware(robot=True)

        self.assertTrue(result["robot"]["success"])
        self.assertEqual(result["robot"]["message"], "机器人模块已在运行中")
        self.assertTrue(self.scheduler.is_hardware_fault_blocked())

    def test_cancel_flag_wins_completion_callback_race(self):
        self.scheduler.stop()
        task = self.make_task()
        task.status = TaskStatus.RUNNING
        task.station_list[0].status = StationTaskStatus.COMPLETED
        command = create_unified_command("task-race", CmdType.TASK_CMD, task)
        self.scheduler.add_command(command)
        with self.scheduler._task_registry_lock:
            entry = self.scheduler._active_tasks[1]
            entry["state"] = "cancelling"
            entry["cancel_event"].set()
            command.status = CommandStatus.RUNNING
        future = Future()
        future.set_result(True)

        self.scheduler._command_execution_done(future, command)

        self.assertEqual(command.status, CommandStatus.CANCELLED)
        self.assertEqual(task.status, TaskStatus.CANCELLED)

    def test_terminal_decision_rejects_cancel_during_completion_persistence(self):
        self.scheduler.stop()
        task = self.make_task()
        command = create_unified_command("task-terminal", CmdType.TASK_CMD, task)
        self.scheduler.add_command(command)
        with self.scheduler._task_registry_lock:
            self.scheduler._active_tasks[1]["state"] = "running"
            command.status = CommandStatus.RUNNING
        entered_db = threading.Event()
        release_db = threading.Event()
        original_update = self.database.update_command_status

        def blocked_update(command_id, *args, **kwargs):
            if command_id == command.command_id:
                entered_db.set()
                release_db.wait(2)
            return original_update(command_id, *args, **kwargs)

        self.database.update_command_status = blocked_update
        future = Future()
        future.set_result(True)
        done = threading.Thread(
            target=self.scheduler._command_execution_done, args=(future, command)
        )
        done.start()
        self.assertTrue(entered_db.wait(1))
        cancel = self.manager.request_cancel_task("cancel-too-late", 1)
        release_db.set()
        done.join(2)
        self.database.update_command_status = original_update

        self.assertEqual(cancel.status, CommandStatus.FAILED)
        self.assertEqual(command.status, CommandStatus.COMPLETED)
        self.assertNotIn(1, self.scheduler._active_tasks)

    def test_future_exception_after_cancel_still_finalizes_cancel(self):
        self.scheduler.stop()
        task = self.make_task()
        command = create_unified_command("task-error", CmdType.TASK_CMD, task)
        self.scheduler.add_command(command)
        with self.scheduler._task_registry_lock:
            entry = self.scheduler._active_tasks[1]
            entry["state"] = "cancelling"
            entry["cancel_event"].set()
            command.status = CommandStatus.RUNNING
        future = Future()
        future.set_exception(RuntimeError("worker failed while cancelling"))
        self.scheduler._command_execution_done(future, command)
        self.assertEqual(command.status, CommandStatus.CANCELLED)
        self.assertTrue(entry["cancel_complete_event"].is_set())
        self.assertTrue(entry["cancel_result"]["success"])

    def test_database_error_during_cancel_wakes_waiter_with_failure(self):
        release = threading.Event()
        started = self.robot.set_action_gate("move_to_marker_1", release)
        command = create_unified_command("task-db", CmdType.TASK_CMD, self.make_task())
        self.scheduler.add_command(command)
        self.assertTrue(started.wait(2))
        original_update = self.database.update_command_status

        def fail_target(command_id, *args, **kwargs):
            if command_id == command.command_id:
                raise RuntimeError("terminal db unavailable")
            return original_update(command_id, *args, **kwargs)

        self.database.update_command_status = fail_target
        result_box = {}
        waiter = threading.Thread(target=lambda: result_box.setdefault(
            "result", self.manager.request_cancel_task("cancel-db", 1)
        ))
        waiter.start()
        self.assertTrue(self.wait_for(lambda: self.scheduler._active_tasks[1]["cancel_event"].is_set()))
        release.set()
        waiter.join(3)
        self.database.update_command_status = original_update
        self.assertFalse(waiter.is_alive())
        self.assertEqual(result_box["result"].status, CommandStatus.FAILED)
        self.assertNotIn(1, self.scheduler._active_tasks)

    def test_async_duplicate_command_id_uses_one_coordinator_job(self):
        manager = TaskManager.__new__(TaskManager)
        manager._cancel_request_lock = threading.RLock()
        manager.logger = get_logger(__name__)
        manager.database = self.database
        entered = threading.Event()
        release = threading.Event()
        calls = []

        def coordinate_once(command_id, task_id):
            calls.append((command_id, task_id))
            entered.set()
            release.wait(2)

        manager.request_cancel_task = coordinate_once
        futures = [manager.request_cancel_task_async("same-id", 1) for _ in range(20)]
        self.assertTrue(entered.wait(1))
        self.assertEqual(len({id(item) for item in futures}), 1)
        self.assertEqual(len(calls), 1)
        self.assertLessEqual(len(manager._cancel_coordinator._threads), 4)
        release.set()
        futures[0].result(2)
        manager._cancel_coordinator.shutdown(wait=True)

    def test_async_coordinator_capacity_rejects_unbounded_queueing(self):
        manager = TaskManager.__new__(TaskManager)
        manager._cancel_request_lock = threading.RLock()
        manager._cancel_requests_inflight = {}
        manager._cancel_requests_seen = set()
        manager._async_cancel_futures = {}
        manager._cancel_coordinator = ThreadPoolExecutor(max_workers=1)
        manager._cancel_coordinator_slots = threading.BoundedSemaphore(1)
        manager.logger = get_logger(__name__)
        manager.database = self.database
        overload_feedback = []
        manager.system_callbacks = {
            "on_command_status_change": lambda **kw: overload_feedback.append(kw["command"])
        }
        entered = threading.Event()
        release = threading.Event()
        manager.request_cancel_task = lambda *_args: (entered.set(), release.wait(2))

        first = manager.request_cancel_task_async("first", 1)
        self.assertTrue(entered.wait(1))
        rejected = manager.request_cancel_task_async("second", 2)
        rejected_result = rejected.result()
        replay = manager.request_cancel_task_async("second", 2).result()
        self.assertEqual(rejected_result.status, CommandStatus.FAILED)
        self.assertEqual(replay.status, CommandStatus.FAILED)
        self.assertEqual(len(overload_feedback), 1)
        self.assertEqual(
            self.database.get_task_cancel_request("second")["status"], "failed"
        )
        self.assertEqual(len(manager._cancel_coordinator._threads), 1)
        release.set()
        first.result(2)
        manager._cancel_coordinator.shutdown(wait=True)

    def test_completed_metadata_failure_does_not_reverse_terminal_status(self):
        self.scheduler.stop()
        completed = []
        self.scheduler.register_callback(
            "on_command_complete", lambda command: completed.append(command)
        )
        task = self.make_task()
        command = create_unified_command("task-meta-complete", CmdType.TASK_CMD, task)
        command.metadata = {"result": "ok"}
        self.scheduler.add_command(command)
        with self.scheduler._task_registry_lock:
            self.scheduler._active_tasks[1]["state"] = "running"
            command.status = CommandStatus.RUNNING
        original_metadata = self.database.update_command_metadata
        self.database.update_command_metadata = lambda *_args: (_ for _ in ()).throw(
            RuntimeError("metadata unavailable")
        )
        future = Future()
        future.set_result(True)
        self.scheduler._command_execution_done(future, command)
        self.database.update_command_metadata = original_metadata
        self.assertEqual(command.status, CommandStatus.COMPLETED)
        self.assertEqual(
            self.database.get_command_by_id(command.command_id)["status"], "completed"
        )
        self.assertEqual(len(completed), 1)
        self.assertNotIn(1, self.scheduler._active_tasks)

    def test_cancelled_metadata_failure_keeps_successful_cancel_result(self):
        self.scheduler.stop()
        task = self.make_task()
        command = create_unified_command("task-meta-cancel", CmdType.TASK_CMD, task)
        command.metadata = {"partial": True}
        self.scheduler.add_command(command)
        with self.scheduler._task_registry_lock:
            entry = self.scheduler._active_tasks[1]
            entry["state"] = "running"
            command.status = CommandStatus.RUNNING
        original_metadata = self.database.update_command_metadata
        self.database.update_command_metadata = lambda *_args: (_ for _ in ()).throw(
            RuntimeError("metadata unavailable")
        )
        result_box = {}
        waiter = threading.Thread(target=lambda: result_box.setdefault(
            "result", self.manager.request_cancel_task("cancel-meta", 1)
        ))
        waiter.start()
        self.assertTrue(entry["cancel_event"].wait(1))
        future = Future()
        future.set_exception(TaskCancelledError("cancelled"))
        self.scheduler._command_execution_done(future, command)
        waiter.join(2)
        self.database.update_command_metadata = original_metadata
        self.assertEqual(command.status, CommandStatus.CANCELLED)
        self.assertEqual(
            self.database.get_command_by_id(command.command_id)["status"], "cancelled"
        )
        self.assertTrue(entry["cancel_result"]["success"])
        self.assertEqual(result_box["result"].status, CommandStatus.COMPLETED)
        self.assertEqual(len(self.feedback), 1)
        self.assertTrue(entry["cancel_complete_event"].is_set())
        self.assertNotIn(1, self.scheduler._active_tasks)

    def test_rcs_overload_is_persisted_processed_and_idempotently_replayed(self):
        self.manager._cancel_coordinator = ThreadPoolExecutor(max_workers=1)
        self.manager._cancel_coordinator_slots = threading.BoundedSemaphore(1)
        self.manager._async_cancel_futures = {}
        entered = threading.Event()
        release = threading.Event()
        original_request = self.manager.request_cancel_task
        self.manager.request_cancel_task = lambda *_args: (
            entered.set(), release.wait(2)
        )
        occupying = self.manager.request_cancel_task_async("occupy", 99)
        self.assertTrue(entered.wait(1))
        self.manager.request_cancel_task = original_request

        system = RobotControlSystem.__new__(RobotControlSystem)
        system.robot_id = 123456
        system.task_manager = self.manager
        system.logger = get_logger(__name__)
        system.callbacks = {}
        request = robot_pb2.ServerStreamMessage(
            command_id=7001,
            command_time=123456789,
            command_type=robot_pb2.CmdType.CANCEL_TASK_CMD,
            robot_id=123456,
        )
        request.task_cmd.task_id = 42

        system._handle_serverCommand(request)
        system._handle_serverCommand(request)

        saved = self.database.get_task_cancel_request("7001")
        received = [row for row in self.database.get_server_command_received()
                    if row["msg_id"] == "7001"][0]
        self.assertEqual(saved["status"], "failed")
        self.assertEqual(received["processed"], 1)
        overload_feedback = [item for item in self.feedback
                             if item.command_id == "7001"]
        self.assertEqual(len(overload_feedback), 1)
        self.assertEqual(overload_feedback[0].status, CommandStatus.FAILED)
        release.set()
        occupying.result(2)

    def test_rcs_overload_replay_recovers_each_partial_persistence_failure(self):
        self.manager._cancel_coordinator = ThreadPoolExecutor(max_workers=1)
        self.manager._cancel_coordinator_slots = threading.BoundedSemaphore(1)
        self.manager._async_cancel_futures = {}
        entered = threading.Event()
        release = threading.Event()
        original_request = self.manager.request_cancel_task
        self.manager.request_cancel_task = lambda *_args: (
            entered.set(), release.wait(3)
        )
        occupying = self.manager.request_cancel_task_async("occupy", 99)
        self.assertTrue(entered.wait(1))
        self.manager.request_cancel_task = original_request
        system = RobotControlSystem.__new__(RobotControlSystem)
        system.robot_id = 123456
        system.task_manager = self.manager
        system.logger = get_logger(__name__)
        system.callbacks = {}

        cases = (
            (7101, "complete_task_cancel_request"),
            (7102, "save_command"),
            (7103, "update_command_status"),
        )
        for command_id, method_name in cases:
            request = robot_pb2.ServerStreamMessage(
                command_id=command_id,
                command_time=123456789,
                command_type=robot_pb2.CmdType.CANCEL_TASK_CMD,
                robot_id=123456,
            )
            request.task_cmd.task_id = 42
            original = getattr(self.database, method_name)
            attempts = {"count": 0}

            def fail_once(*args, _original=original, **kwargs):
                attempts["count"] += 1
                if attempts["count"] == 1:
                    raise RuntimeError(f"{method_name} unavailable")
                return _original(*args, **kwargs)

            setattr(self.database, method_name, fail_once)
            system._handle_serverCommand(request)
            setattr(self.database, method_name, original)
            received = [row for row in self.database.get_server_command_received()
                        if row["msg_id"] == str(command_id)][0]
            self.assertEqual(received["processed"], 0)
            self.assertEqual(
                len([item for item in self.feedback
                     if item.command_id == str(command_id)]), 0
            )
            if method_name == "complete_task_cancel_request":
                self.assertEqual(
                    self.database.get_task_cancel_request(str(command_id))["status"],
                    "pending",
                )

            system._handle_serverCommand(request)
            system._handle_serverCommand(request)
            received = [row for row in self.database.get_server_command_received()
                        if row["msg_id"] == str(command_id)][0]
            self.assertEqual(
                self.database.get_task_cancel_request(str(command_id))["status"],
                "failed",
            )
            self.assertEqual(received["processed"], 1)
            self.assertEqual(
                len([item for item in self.feedback
                     if item.command_id == str(command_id)]), 1
            )

        release.set()
        occupying.result(2)

    def test_rcs_overload_same_command_id_different_task_is_conflict(self):
        self.manager._cancel_coordinator = ThreadPoolExecutor(max_workers=1)
        self.manager._cancel_coordinator_slots = threading.BoundedSemaphore(1)
        self.manager._async_cancel_futures = {}
        entered = threading.Event()
        release = threading.Event()
        original_request = self.manager.request_cancel_task
        self.manager.request_cancel_task = lambda *_args: (
            entered.set(), release.wait(2)
        )
        occupying = self.manager.request_cancel_task_async("occupy", 99)
        self.assertTrue(entered.wait(1))
        self.manager.request_cancel_task = original_request
        system = RobotControlSystem.__new__(RobotControlSystem)
        system.robot_id = 123456
        system.task_manager = self.manager
        system.logger = get_logger(__name__)
        system.callbacks = {}

        def request(task_id):
            message = robot_pb2.ServerStreamMessage(
                command_id=7201, command_time=1,
                command_type=robot_pb2.CmdType.CANCEL_TASK_CMD, robot_id=123456,
            )
            message.task_cmd.task_id = task_id
            return message

        system._handle_serverCommand(request(42))
        system._handle_serverCommand(request(43))
        conflict = self.manager.request_cancel_task_async("7201", 43).result()
        self.assertTrue(conflict.metadata["idempotency_conflict"])
        self.assertEqual(
            self.database.get_task_cancel_request("7201")["target_task_id"], "42"
        )
        received = [row for row in self.database.get_server_command_received()
                    if row["msg_id"] == "7201"][0]
        self.assertEqual(received["processed"], 1)
        with self.database._get_connection() as connection:
            audit_count = connection.execute(
                "SELECT COUNT(*) FROM task_history "
                "WHERE task_id = ? AND action = ?",
                ("43", "cancel_request_conflict"),
            ).fetchone()[0]
        self.assertGreaterEqual(audit_count, 1)
        self.assertEqual(
            len([item for item in self.feedback if item.command_id == "7201"]), 1
        )
        system._handle_serverCommand(request(43))
        self.assertEqual(
            self.database.get_task_cancel_request("7201")["target_task_id"], "42"
        )
        self.assertEqual(
            len([item for item in self.feedback if item.command_id == "7201"]), 1
        )

        self.database.begin_task_cancel_request("7202", "50")
        pending_conflict = robot_pb2.ServerStreamMessage(
            command_id=7202, command_time=2,
            command_type=robot_pb2.CmdType.CANCEL_TASK_CMD, robot_id=123456,
        )
        pending_conflict.task_cmd.task_id = 51
        system._handle_serverCommand(pending_conflict)
        system._handle_serverCommand(pending_conflict)
        pending_saved = self.database.get_task_cancel_request("7202")
        pending_received = [
            row for row in self.database.get_server_command_received()
            if row["msg_id"] == "7202"
        ][0]
        self.assertEqual(pending_saved["target_task_id"], "50")
        self.assertEqual(pending_saved["status"], "pending")
        self.assertEqual(pending_received["processed"], 1)
        self.assertEqual(
            len([item for item in self.feedback if item.command_id == "7202"]), 0
        )
        release.set()
        occupying.result(2)

    def test_overload_database_wait_does_not_hold_cancel_request_lock(self):
        self.manager._cancel_coordinator = ThreadPoolExecutor(max_workers=1)
        self.manager._cancel_coordinator_slots = threading.BoundedSemaphore(1)
        self.manager._async_cancel_futures = {}
        occupying_release = threading.Event()
        self.manager.request_cancel_task = lambda *_args: occupying_release.wait(2)
        self.manager.request_cancel_task_async("occupy", 99)
        db_entered = threading.Event()
        db_release = threading.Event()
        original_begin = self.database.begin_task_cancel_request

        def block_first(command_id, task_id):
            if command_id == "blocked-db":
                db_entered.set()
                db_release.wait(2)
            return original_begin(command_id, task_id)

        self.database.begin_task_cancel_request = block_first
        caller = threading.Thread(
            target=self.manager.request_cancel_task_async,
            args=("blocked-db", 1),
        )
        caller.start()
        self.assertTrue(db_entered.wait(1))
        self.assertTrue(self.manager._cancel_request_lock.acquire(timeout=0.2))
        self.manager._cancel_request_lock.release()
        db_release.set()
        caller.join(2)
        self.database.begin_task_cancel_request = original_begin
        occupying_release.set()

    def test_scheduler_shutdown_wakes_running_cancel_coordinator(self):
        release = threading.Event()
        started = self.robot.set_action_gate("move_to_marker_1", release)
        command = create_unified_command("task-shutdown", CmdType.TASK_CMD, self.make_task())
        self.scheduler.add_command(command)
        self.assertTrue(started.wait(2))
        future = self.manager.request_cancel_task_async("cancel-shutdown", 1)
        self.assertTrue(self.wait_for(lambda: self.scheduler._active_tasks[1]["cancel_event"].is_set()))
        self.scheduler.stop()
        future.result(2)
        release.set()
        self.assertTrue(self.wait_for(lambda: 1 not in self.scheduler._active_tasks))
        self.assertEqual(self.feedback[-1].status, CommandStatus.FAILED)

    def test_controlled_stop_failure_fails_cancel_and_blocks_next_task(self):
        started = threading.Event()

        def fail_stop(_marker, cancel_event=None):
            started.set()
            self.assertIsNotNone(cancel_event)
            self.assertTrue(cancel_event.wait(2))
            raise ControlledStopError("AGV停止状态未知")

        self.robot.move_to_marker = fail_stop
        first = create_unified_command(
            "task-stop-fail", CmdType.TASK_CMD, self.make_task(1)
        )
        second = create_unified_command(
            "task-blocked", CmdType.TASK_CMD, self.make_task(2, "marker_2")
        )
        self.scheduler.add_command(first)
        self.scheduler.add_command(second)
        self.assertTrue(started.wait(2))

        result = self.manager.request_cancel_task("cancel-stop-fail", 1)

        self.assertEqual(result.status, CommandStatus.FAILED)
        self.assertEqual(first.status, CommandStatus.CANCELLED)
        self.assertTrue(self.scheduler._hardware_fault_blocked)
        time.sleep(0.2)
        self.assertEqual(second.status, CommandStatus.QUEUED)

    def test_agv_status_exception_during_cancel_fails_and_blocks_next_task(self):
        agv = AGVController({
            "agv_ip": "test",
            "agv_port": 1,
            "agv_status_poll_interval_seconds": 0.001,
            "agv_stop_timeout_seconds": 0.05,
        })
        status_reads = {"count": 0}
        moving = threading.Event()

        def send(command):
            if command.startswith("/api/move?marker="):
                return {"status": "OK"}
            if command == "/api/move/cancel":
                return {"status": "OK"}
            status_reads["count"] += 1
            if status_reads["count"] == 1:
                moving.set()
                return {"status": "OK", "results": {"move_status": "running"}}
            if status_reads["count"] == 2:
                return {"status": "OK", "results": {"move_status": "running"}}
            raise RuntimeError("AGV status unavailable")

        agv._send_command_to_agv = send
        self.robot.move_to_marker = lambda marker, cancel_event=None: agv.agv_moveto(
            marker, cancel_event=cancel_event
        )
        first = create_unified_command(
            "task-agv-status-error", CmdType.TASK_CMD, self.make_task(1)
        )
        second = create_unified_command(
            "task-after-agv-status-error", CmdType.TASK_CMD,
            self.make_task(2, "marker_2"),
        )
        self.scheduler.add_command(first)
        self.scheduler.add_command(second)
        self.assertTrue(moving.wait(2))

        result = self.manager.request_cancel_task("cancel-agv-status-error", 1)

        self.assertEqual(result.status, CommandStatus.FAILED)
        self.assertEqual(first.status, CommandStatus.CANCELLED)
        self.assertTrue(self.scheduler._hardware_fault_blocked)
        time.sleep(0.1)
        self.assertEqual(second.status, CommandStatus.QUEUED)

    def test_jaka_stop_status_exception_fails_cancel_and_blocks_next_task(self):
        started = threading.Event()

        class StatusFailureSdk:
            def __init__(self):
                self.aborted = False

            def joint_move(self, **_kwargs):
                started.set()
                return (0,)

            def motion_abort(self):
                self.aborted = True
                return (0,)

            def get_joint_position(self):
                if self.aborted:
                    raise RuntimeError("joint status unavailable")
                return (0, [0.5] * 6)

        arm = ArmController.__new__(ArmController)
        arm.logger = get_logger(__name__)
        arm.robot = StatusFailureSdk()
        arm.arm_motion_timeout = 1
        arm.arm_stop_timeout = 0.02
        arm.arm_poll_interval = 0.001
        arm.arm_joint_tolerance = 0.0001
        self.robot.move_to_marker = lambda _marker, cancel_event=None: arm.rob_moveto(
            [1.0] * 6, cancel_event=cancel_event
        )
        first = create_unified_command(
            "task-jaka-status", CmdType.TASK_CMD, self.make_task(1)
        )
        second = create_unified_command(
            "task-after-jaka-status", CmdType.TASK_CMD,
            self.make_task(2, "marker_2"),
        )
        self.scheduler.add_command(first)
        self.scheduler.add_command(second)
        self.assertTrue(started.wait(2))

        result = self.manager.request_cancel_task("cancel-jaka-status", 1)

        self.assertEqual(result.status, CommandStatus.FAILED)
        self.assertEqual(first.status, CommandStatus.CANCELLED)
        self.assertTrue(self.scheduler._hardware_fault_blocked)
        time.sleep(0.2)
        self.assertEqual(second.status, CommandStatus.QUEUED)

    def test_non_cancelled_hardware_unknown_fails_and_blocks_next_task(self):
        def fail_without_cancel(_marker, cancel_event=None):
            raise ControlledStopError("外部轴状态未知")

        self.robot.move_to_marker = fail_without_cancel
        first = create_unified_command(
            "task-hardware-unknown", CmdType.TASK_CMD, self.make_task(1)
        )
        second = create_unified_command(
            "task-after-unknown", CmdType.TASK_CMD,
            self.make_task(2, "marker_2"),
        )
        self.scheduler.add_command(first)
        self.scheduler.add_command(second)

        self.assertTrue(self.wait_for(lambda: first.status == CommandStatus.FAILED))
        self.assertTrue(self.scheduler._hardware_fault_blocked)
        time.sleep(0.2)
        self.assertEqual(second.status, CommandStatus.QUEUED)

    def test_known_non_target_result_can_cancel_without_hardware_block(self):
        started = threading.Event()

        def known_non_target(_marker, cancel_event=None):
            started.set()
            self.assertTrue(cancel_event.wait(2))
            return False

        self.robot.move_to_marker = known_non_target
        command = create_unified_command(
            "task-known-state", CmdType.TASK_CMD, self.make_task(1)
        )
        self.scheduler.add_command(command)
        self.assertTrue(started.wait(2))

        result = self.manager.request_cancel_task("cancel-known-state", 1)

        self.assertEqual(result.status, CommandStatus.COMPLETED)
        self.assertEqual(command.status, CommandStatus.CANCELLED)
        self.assertFalse(self.scheduler._hardware_fault_blocked)


if __name__ == "__main__":
    unittest.main()
