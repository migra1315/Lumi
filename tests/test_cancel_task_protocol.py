import unittest

import gRPC.RobotService_pb2 as robot_pb2
from dataModels.CommandModels import CancelTaskCmd, CmdType
from dataModels.TaskModels import StationTaskStatus, TaskStatus
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


if __name__ == "__main__":
    unittest.main()
