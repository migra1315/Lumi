import tempfile
import threading
import unittest
from pathlib import Path

from dataModels.CommandModels import CmdType
from dataModels.TaskModels import Task, TaskStatus, StationTaskStatus
from dataModels.UnifiedCommand import CommandStatus, create_unified_command
from task.TaskDatabase import TaskDatabase
from task.TaskManager import TaskManager, CancelRequestPersistenceError
from task.TaskScheduler import TaskScheduler
from utils.logger_config import get_logger


class CancelWaitingTaskTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database = TaskDatabase(str(Path(self.temp_dir.name) / "tasks.db"))
        self.scheduler = TaskScheduler(object(), self.database)
        self.feedback = []

        self.manager = TaskManager.__new__(TaskManager)
        self.manager.database = self.database
        self.manager.scheduler = self.scheduler
        self.manager.logger = get_logger(__name__)
        self.manager.system_callbacks = {
            "on_command_status_change": lambda **kw: self.feedback.append(kw["command"]),
        }

    def tearDown(self):
        self.scheduler.executor.shutdown(wait=False)
        self.temp_dir.cleanup()

    def add_task(self, task_id=1, command_id="task-1"):
        task = Task(task_id=task_id, task_name="waiting", station_list=[])
        command = create_unified_command(command_id, CmdType.TASK_CMD, task)
        self.scheduler.add_command(command)
        return task, command

    def test_cancel_waiting_task_marks_terminal_and_emits_one_final_feedback(self):
        task, target = self.add_task()

        result = self.manager.request_cancel_task("cancel-1", task.task_id)

        self.assertEqual(result.status, CommandStatus.COMPLETED)
        self.assertEqual(target.status, CommandStatus.CANCELLED)
        self.assertEqual(task.status, TaskStatus.CANCELLED)
        self.assertEqual([item.status for item in self.feedback], [CommandStatus.COMPLETED])
        self.assertEqual(
            self.database.get_command_by_id("task-1")["status"], "cancelled"
        )

        queued = self.scheduler.command_queue.get_nowait()
        self.scheduler._execute_command(queued)
        self.assertIsNone(self.scheduler.current_command)

    def test_cancel_marks_unfinished_stations_cancelled(self):
        task, _ = self.add_task()
        station = type("StationStub", (), {"status": StationTaskStatus.PENDING})()
        failed = type("StationStub", (), {"status": StationTaskStatus.FAILED})()
        completed = type("StationStub", (), {"status": StationTaskStatus.COMPLETED})()
        task.station_list = [station, failed, completed]

        self.manager.request_cancel_task("cancel-stations", task.task_id)

        self.assertEqual(station.status, StationTaskStatus.CANCELLED)
        self.assertEqual(failed.status, StationTaskStatus.CANCELLED)
        self.assertEqual(completed.status, StationTaskStatus.COMPLETED)

    def test_cancel_without_later_task_enqueues_one_internal_auto_charge(self):
        self.scheduler.auto_charge_after_cancel = True
        self.scheduler.charge_marker = "charge_point_1F_6010"
        task, target = self.add_task()
        original_schedule = self.scheduler.schedule_auto_charge_after_cancel

        def schedule_after_feedback(*args, **kwargs):
            self.assertEqual(len(self.feedback), 1)
            return original_schedule(*args, **kwargs)

        self.scheduler.schedule_auto_charge_after_cancel = schedule_after_feedback

        result = self.manager.request_cancel_task("cancel-auto-charge", task.task_id)

        self.assertEqual(result.status, CommandStatus.COMPLETED)
        self.assertTrue(result.metadata["auto_charge"]["scheduled"])
        with self.scheduler.command_queue.mutex:
            queued = list(self.scheduler.command_queue.queue)
        auto_charge = [
            command for command in queued
            if command.metadata.get("source") == "auto_charge_after_task_cancel"
        ]
        self.assertEqual(len(auto_charge), 1)
        self.assertEqual(auto_charge[0].cmd_type, CmdType.CHARGE_CMD)
        self.assertEqual(auto_charge[0].priority, 9)
        self.assertEqual(
            auto_charge[0].metadata["charge_marker"], "charge_point_1F_6010"
        )
        self.assertEqual(
            auto_charge[0].command_id,
            f"auto-charge-after-cancel:{target.command_id}",
        )

    def test_cancel_with_later_task_skips_auto_charge(self):
        self.scheduler.auto_charge_after_cancel = True
        self.scheduler.charge_marker = "charge_point_1F_6010"
        first, _ = self.add_task(task_id=1, command_id="task-1")
        self.add_task(task_id=2, command_id="task-2")

        result = self.manager.request_cancel_task("cancel-with-next", first.task_id)

        self.assertEqual(result.status, CommandStatus.COMPLETED)
        self.assertFalse(result.metadata["auto_charge"]["scheduled"])
        self.assertIn("后续任务", result.metadata["auto_charge"]["reason"])
        with self.scheduler.command_queue.mutex:
            queued = list(self.scheduler.command_queue.queue)
        self.assertFalse(any(
            command.metadata.get("source") == "auto_charge_after_task_cancel"
            for command in queued
        ))

    def test_task_arriving_after_auto_charge_decision_cancels_it_before_start(self):
        self.scheduler.auto_charge_after_cancel = True
        self.scheduler.charge_marker = "charge_point_1F_6010"
        task, _ = self.add_task()
        result = self.manager.request_cancel_task("cancel-before-next", task.task_id)
        self.assertTrue(result.metadata["auto_charge"]["scheduled"])
        with self.scheduler.command_queue.mutex:
            queued = list(self.scheduler.command_queue.queue)
        auto_charge = next(
            command for command in queued
            if command.metadata.get("source") == "auto_charge_after_task_cancel"
        )

        self.add_task(task_id=2, command_id="task-2")
        self.scheduler._execute_command(auto_charge)

        self.assertEqual(auto_charge.status, CommandStatus.CANCELLED)
        self.assertIn("新任务", auto_charge.error_message)

    def test_duplicate_cancel_command_is_idempotent_without_second_feedback(self):
        self.add_task()
        first = self.manager.request_cancel_task("cancel-1", 1)
        second = self.manager.request_cancel_task("cancel-1", 1)

        self.assertEqual(first.status, CommandStatus.COMPLETED)
        self.assertEqual(second.status, CommandStatus.COMPLETED)
        self.assertEqual(len(self.feedback), 1)
        self.assertTrue(second.metadata["idempotent_replay"])

    def test_running_task_cancel_is_explicitly_rejected(self):
        _, command = self.add_task()
        with self.scheduler._task_registry_lock:
            self.scheduler._active_tasks[1]["state"] = "running"
            command.status = CommandStatus.RUNNING

        result = self.manager.request_cancel_task("cancel-running", 1)

        self.assertEqual(result.status, CommandStatus.FAILED)
        self.assertIn("不支持取消正在运行", result.error_message)
        self.assertEqual(command.status, CommandStatus.RUNNING)

    def test_unknown_task_cancel_fails(self):
        result = self.manager.request_cancel_task("cancel-missing", 999)
        self.assertEqual(result.status, CommandStatus.FAILED)
        self.assertEqual(len(self.feedback), 1)

    def test_active_task_id_must_be_unique(self):
        self.add_task(command_id="task-a")
        with self.assertRaisesRegex(ValueError, "活动 task_id 重复"):
            self.add_task(command_id="task-b")

    def test_concurrent_cancel_only_creates_one_request_and_feedback(self):
        self.add_task()
        barrier = threading.Barrier(3)

        def cancel():
            barrier.wait()
            self.manager.request_cancel_task("cancel-concurrent", 1)

        threads = [threading.Thread(target=cancel) for _ in range(2)]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join()

        self.assertEqual(len(self.feedback), 1)
        saved = self.database.get_task_cancel_request("cancel-concurrent")
        self.assertEqual(saved["status"], "completed")

    def test_same_cancel_command_id_cannot_target_another_task(self):
        self.add_task(task_id=1, command_id="task-1")
        self.manager.request_cancel_task("same-cancel", 1)
        conflict = self.manager.request_cancel_task("same-cancel", 2)

        self.assertEqual(conflict.status, CommandStatus.FAILED)
        self.assertTrue(conflict.metadata["idempotency_conflict"])
        self.assertEqual(
            self.database.get_task_cancel_request("same-cancel")["target_task_id"], "1"
        )

    def test_replayed_legacy_pending_request_is_failed_and_audited(self):
        self.database.begin_task_cancel_request("legacy-pending", "7")

        result = self.manager.request_cancel_task("legacy-pending", 7)

        self.assertEqual(result.status, CommandStatus.FAILED)
        saved = self.database.get_task_cancel_request("legacy-pending")
        self.assertEqual(saved["status"], "failed")
        self.assertIsNotNone(saved["completed_at"])
        self.assertEqual(len(self.feedback), 1)

    def test_startup_recovery_and_first_replay_emit_saved_failure_once(self):
        self.database.begin_task_cancel_request("startup-pending", "9")
        recovered = self.database.fail_pending_task_cancel_requests("restart recovery")
        self.assertEqual(len(recovered), 1)

        restarted = TaskManager.__new__(TaskManager)
        restarted.database = self.database
        restarted.scheduler = self.scheduler
        restarted.logger = get_logger(__name__)
        replay_feedback = []
        restarted.system_callbacks = {
            "on_command_status_change": lambda **kw: replay_feedback.append(kw["command"]),
        }

        first = restarted.request_cancel_task("startup-pending", 9)
        second = restarted.request_cancel_task("startup-pending", 9)

        self.assertEqual(first.status, CommandStatus.FAILED)
        self.assertEqual(second.status, CommandStatus.FAILED)
        self.assertEqual(len(replay_feedback), 1)

    def test_database_failure_does_not_publish_ghost_task(self):
        original_save = self.database.save_command

        def fail_save(_command):
            raise RuntimeError("database unavailable")

        self.database.save_command = fail_save
        task = Task(task_id=88, task_name="ghost", station_list=[])
        command = create_unified_command("ghost", CmdType.TASK_CMD, task)
        with self.assertRaisesRegex(RuntimeError, "database unavailable"):
            self.scheduler.add_command(command)
        self.database.save_command = original_save

        self.assertTrue(self.scheduler.command_queue.empty())
        self.assertNotIn(88, self.scheduler._active_tasks)

    def test_owner_exception_is_committed_failed_before_feedback(self):
        self.add_task()
        original_update = self.database.update_command_status

        def fail_update(*_args, **_kwargs):
            raise RuntimeError("status write failed")

        self.database.update_command_status = fail_update
        result = self.manager.request_cancel_task("cancel-db-error", 1)
        self.database.update_command_status = original_update

        self.assertEqual(result.status, CommandStatus.FAILED)
        saved = self.database.get_task_cancel_request("cancel-db-error")
        self.assertEqual(saved["status"], "failed")
        self.assertEqual(len(self.feedback), 1)
        self.assertEqual(self.feedback[0].status, CommandStatus.FAILED)

    def test_target_status_write_failure_keeps_stable_failed_tombstone(self):
        self.add_task()
        original_update = self.database.update_command_status

        def fail_target_only(command_id, *args, **kwargs):
            if command_id == "task-1":
                raise RuntimeError("target terminal write failed")
            return original_update(command_id, *args, **kwargs)

        self.database.update_command_status = fail_target_only
        first = self.manager.request_cancel_task("cancel-write-a", 1)
        queued = self.scheduler.command_queue.get_nowait()
        self.scheduler._execute_command(queued)
        second = self.manager.request_cancel_task("cancel-write-b", 1)
        self.database.update_command_status = original_update

        self.assertEqual(first.status, CommandStatus.FAILED)
        self.assertEqual(second.status, CommandStatus.FAILED)
        self.assertEqual(first.error_message, second.error_message)
        self.assertIn(1, self.scheduler._cancelled_task_generations)
        self.assertIsNone(self.scheduler.current_command)

    def test_audit_failure_does_not_reverse_successful_cancel(self):
        self.add_task()
        original_log = self.database.log_task_action

        def fail_audit(*_args, **_kwargs):
            raise RuntimeError("audit unavailable")

        self.database.log_task_action = fail_audit
        first = self.manager.request_cancel_task("cancel-audit-a", 1)
        second = self.manager.request_cancel_task("cancel-audit-b", 1)
        self.database.log_task_action = original_log

        self.assertEqual(first.status, CommandStatus.COMPLETED)
        self.assertEqual(second.status, CommandStatus.COMPLETED)
        self.assertTrue(self.scheduler._cancelled_task_generations[1]["success"])

    def test_finalize_failure_is_retryable_and_eventually_emits_exactly_once(self):
        self.add_task()
        original_finalize = self.database.complete_task_cancel_request
        attempts = {"count": 0}

        def fail_once(*args, **kwargs):
            attempts["count"] += 1
            if attempts["count"] == 1:
                raise RuntimeError("finalize unavailable")
            return original_finalize(*args, **kwargs)

        self.database.complete_task_cancel_request = fail_once
        with self.assertRaises(CancelRequestPersistenceError):
            self.manager.request_cancel_task("cancel-finalize", 1)
        self.assertEqual(len(self.feedback), 0)
        self.assertEqual(
            self.database.get_task_cancel_request("cancel-finalize")["status"], "pending"
        )

        replay = self.manager.request_cancel_task("cancel-finalize", 1)
        self.database.complete_task_cancel_request = original_finalize

        self.assertEqual(replay.status, CommandStatus.FAILED)
        self.assertEqual(len(self.feedback), 1)
        self.assertEqual(
            self.database.get_task_cancel_request("cancel-finalize")["status"], "failed"
        )

    def test_different_cancel_ids_are_successful_before_and_after_dequeue(self):
        self.add_task()
        first = self.manager.request_cancel_task("cancel-a", 1)
        second = self.manager.request_cancel_task("cancel-b", 1)
        queued = self.scheduler.command_queue.get_nowait()
        self.scheduler._execute_command(queued)
        third = self.manager.request_cancel_task("cancel-c", 1)

        self.assertEqual(first.status, CommandStatus.COMPLETED)
        self.assertEqual(second.status, CommandStatus.COMPLETED)
        self.assertEqual(third.status, CommandStatus.COMPLETED)

    def test_reused_task_id_does_not_match_previous_generation(self):
        self.add_task(command_id="old-task")
        self.manager.request_cancel_task("cancel-old", 1)
        queued = self.scheduler.command_queue.get_nowait()
        self.scheduler._execute_command(queued)

        _, new_command = self.add_task(command_id="new-task")
        with self.scheduler._task_registry_lock:
            self.scheduler._active_tasks[1]["state"] = "running"
            new_command.status = CommandStatus.RUNNING
        result = self.manager.request_cancel_task("cancel-new", 1)

        self.assertEqual(result.status, CommandStatus.FAILED)
        self.assertIn("正在运行", result.error_message)

    def test_dequeue_and_cancel_are_serialized(self):
        self.add_task()
        queued = self.scheduler.command_queue.get_nowait()
        self.scheduler.executor.shutdown(wait=False)

        class PendingFuture:
            def add_done_callback(self, _callback):
                pass

        class PendingExecutor:
            def submit(self, *_args, **_kwargs):
                return PendingFuture()

            def shutdown(self, wait=False):
                pass

        self.scheduler.executor = PendingExecutor()
        barrier = threading.Barrier(3)
        results = {}

        def dequeue():
            barrier.wait()
            self.scheduler._execute_command(queued)

        def cancel():
            barrier.wait()
            results["cancel"] = self.scheduler.cancel_waiting_task(1)

        threads = [threading.Thread(target=dequeue), threading.Thread(target=cancel)]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join()

        if results["cancel"]["success"]:
            self.assertIsNone(self.scheduler.current_command)
        else:
            self.assertEqual(self.scheduler.current_command, queued)
            self.assertEqual(queued.status, CommandStatus.RUNNING)


if __name__ == "__main__":
    unittest.main()
