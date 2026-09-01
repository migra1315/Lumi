import unittest
from unittest.mock import Mock, patch

import gRPC.RobotService_pb2 as robot_pb2
from RobotControlSystem import RobotControlSystem
from dataModels.CommandModels import CancelTaskCmd, CmdType
from dataModels.TaskModels import StationTaskStatus, TaskStatus
from dataModels.UnifiedCommand import CommandStatus, create_unified_command
from utils.dataConverter import (
    CommandValidationError,
    convert_server_message_to_command_envelope,
)


class CancelTaskProtocolTest(unittest.TestCase):
    ROBOT_ID = 123456

    def make_request(self, task_id=42, robot_id=ROBOT_ID):
        request = robot_pb2.ServerStreamMessage(
            command_id=9001,
            command_time=123456789,
            command_type=robot_pb2.CmdType.CANCEL_TASK_CMD,
            robot_id=robot_id,
        )
        request.task_cmd.task_id = task_id
        return request

    def test_generated_proto_contains_cancel_command(self):
        self.assertEqual(robot_pb2.CmdType.CANCEL_TASK_CMD, 10)
        payload = self.make_request().SerializeToString()
        restored = robot_pb2.ServerStreamMessage.FromString(payload)
        self.assertEqual(restored.command_type, robot_pb2.CmdType.CANCEL_TASK_CMD)
        self.assertEqual(restored.task_cmd.task_id, 42)

    def test_cancel_command_only_parses_target_task_id(self):
        envelope = convert_server_message_to_command_envelope(
            self.make_request(), expected_robot_id=self.ROBOT_ID
        )
        self.assertEqual(envelope.cmd_type, CmdType.CANCEL_TASK_CMD)
        self.assertEqual(envelope.data_json, {"cancel_task_cmd": {"task_id": 42}})
        self.assertEqual(envelope.data, CancelTaskCmd(task_id=42))

    def test_cancel_command_requires_task_oneof(self):
        request = self.make_request()
        request.ClearField("task_cmd")
        with self.assertRaisesRegex(CommandValidationError, "必须使用 task_cmd"):
            convert_server_message_to_command_envelope(request, self.ROBOT_ID)

    def test_cancel_command_rejects_wrong_oneof(self):
        request = self.make_request()
        request.hardware_control_cmd.robot = True
        with self.assertRaisesRegex(CommandValidationError, "必须使用 task_cmd"):
            convert_server_message_to_command_envelope(request, self.ROBOT_ID)

    def test_cancel_command_rejects_non_positive_task_id(self):
        with self.assertRaisesRegex(CommandValidationError, "必须大于 0"):
            convert_server_message_to_command_envelope(
                self.make_request(task_id=0), self.ROBOT_ID
            )

    def test_cancel_command_rejects_wrong_robot(self):
        with self.assertRaisesRegex(CommandValidationError, "机器人ID不匹配"):
            convert_server_message_to_command_envelope(
                self.make_request(robot_id=999), self.ROBOT_ID
            )

    def test_internal_cancelled_statuses_are_explicit(self):
        self.assertEqual(TaskStatus.CANCELLED.value, "cancelled")
        self.assertEqual(StationTaskStatus.CANCELLED.value, "cancelled")

    def test_cancel_feedback_matches_command_status_update_contract(self):
        system = RobotControlSystem.__new__(RobotControlSystem)
        system.robot_id = self.ROBOT_ID
        system.logger = Mock()
        sent = []
        system.server_command_manager = Mock()
        system.server_command_manager.send_message.side_effect = sent.append
        system._save_server_command_message = Mock()
        command = create_unified_command(
            "9001", CmdType.CANCEL_TASK_CMD, CancelTaskCmd(task_id=42)
        )
        command.status = CommandStatus.COMPLETED
        command.error_message = "运行任务取消成功: 42"

        with patch("RobotControlSystem.time.time", return_value=123.456):
            system._send_command_status_update(command)

        self.assertEqual(len(sent), 1)
        message = sent[0]
        self.assertEqual(message.command_id, 9001)
        self.assertEqual(message.command_time, 123456)
        self.assertEqual(
            message.command_type, robot_pb2.ClientMessageType.COMMAND_STATUS_UPDATE
        )
        self.assertEqual(message.robot_id, self.ROBOT_ID)
        self.assertEqual(message.command_status.command_id, 9001)
        self.assertEqual(
            message.command_status.command_type, robot_pb2.CmdType.CANCEL_TASK_CMD
        )
        self.assertEqual(
            message.command_status.status,
            robot_pb2.CommandStatus.COMMAND_STATUS_COMPLETED,
        )
        self.assertEqual(
            message.command_status.message, "运行任务取消成功: 42"
        )
        self.assertEqual(message.command_status.timestamp, message.command_time)
        self.assertEqual(message.command_status.retry_count, 0)


if __name__ == "__main__":
    unittest.main()
