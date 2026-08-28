import threading
import unittest
from unittest.mock import Mock, patch

import requests

from robot.AGVController import AGVController
from robot.ArmController import ArmController
from robot.HardwareErrors import ControlledStopError
from robot.RobotController import RobotController, SystemStatus


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class FakeJakaSdk:
    def __init__(self, abort_code=0, stop_state_error=None):
        self.abort_code = abort_code
        self.stop_state_error = stop_state_error
        self.aborted = False
        self.started = threading.Event()
        self.joint_move_calls = []

    def joint_move(self, **kwargs):
        self.joint_move_calls.append(kwargs)
        self.started.set()
        return (0,)

    def motion_abort(self):
        if self.abort_code == 0:
            self.aborted = True
        return (self.abort_code,)

    def get_joint_position(self):
        if self.aborted and self.stop_state_error == "exception":
            raise RuntimeError("joint status unavailable")
        if self.aborted and self.stop_state_error == "malformed":
            return (0,)
        position = [0.0] * 6 if self.aborted else [0.5] * 6
        return (0, position)


class HardwareControlledStopTests(unittest.TestCase):
    @staticmethod
    def make_arm_controller(sdk=None):
        controller = ArmController.__new__(ArmController)
        controller.logger = Mock()
        controller.robot = sdk
        controller.arm_motion_timeout = 0.2
        controller.arm_stop_timeout = 0.05
        controller.arm_poll_interval = 0.001
        controller.arm_joint_tolerance = 0.0001
        controller.ext_base_url = "http://ext"
        controller.EXT_MOVETO_URL = "http://ext/moveto"
        controller.EXT_GETSTATE_URL = "http://ext/status"
        controller.EXT_ENABLE_URL = "http://ext/enable"
        controller.EXT_RESET_URL = "http://ext/reset"
        controller.ext_request_timeout = (1.0, 2.0)
        controller.ext_position_tolerance = 0.01
        controller.ext_axis_limits = {
            f"joint{index}": {"min": -100, "max": 100, "desc": "test"}
            for index in range(1, 5)
        }
        return controller

    def test_agv_cancel_is_confirmed_before_move_returns(self):
        controller = AGVController({
            "agv_ip": "test",
            "agv_port": 1,
            "agv_status_poll_interval_seconds": 0.001,
            "agv_stop_timeout_seconds": 0.05,
        })
        cancel_event = threading.Event()
        status_read = threading.Event()
        cancelled = {"value": False}
        commands = []

        def send(command):
            commands.append(command)
            if command.startswith("/api/move?marker="):
                return {"status": "OK"}
            if command == "/api/move/cancel":
                cancelled["value"] = True
                return {"status": "OK"}
            status_read.set()
            return {
                "status": "OK",
                "results": {
                    "move_status": "cancelled" if cancelled["value"] else "running"
                },
            }

        controller._send_command_to_agv = send
        result = {}
        worker = threading.Thread(
            target=lambda: result.setdefault(
                "value", controller.agv_moveto("p1", cancel_event)
            )
        )
        worker.start()
        self.assertTrue(status_read.wait(1))
        cancel_event.set()
        worker.join(1)

        self.assertFalse(worker.is_alive())
        self.assertFalse(result["value"])
        self.assertIn("/api/move/cancel", commands)
        self.assertGreater(commands.count("/api/robot_status"), 1)

    def test_agv_stop_confirmation_timeout_fails(self):
        controller = AGVController({
            "agv_ip": "test",
            "agv_port": 1,
            "agv_status_poll_interval_seconds": 0.001,
            "agv_stop_timeout_seconds": 0.005,
        })
        controller._send_command_to_agv = lambda command: (
            {"status": "OK"}
            if command == "/api/move/cancel"
            else {"status": "OK", "results": {"move_status": "running"}}
        )
        self.assertFalse(controller.agv_cancel_task())

    def test_jaka_uses_nonblocking_move_and_aborts_on_cancel(self):
        sdk = FakeJakaSdk()
        controller = self.make_arm_controller(sdk)
        cancel_event = threading.Event()
        result = {}
        worker = threading.Thread(
            target=lambda: result.setdefault(
                "value", controller.rob_moveto([1.0] * 6, cancel_event=cancel_event)
            )
        )
        worker.start()
        self.assertTrue(sdk.started.wait(1))
        cancel_event.set()
        worker.join(1)

        self.assertFalse(worker.is_alive())
        self.assertEqual(result["value"], -1)
        self.assertTrue(sdk.aborted)
        self.assertFalse(sdk.joint_move_calls[0]["is_block"])

    def test_jaka_abort_sdk_failure_is_not_reported_as_cancel_success(self):
        sdk = FakeJakaSdk(abort_code=-1)
        controller = self.make_arm_controller(sdk)
        cancel_event = threading.Event()
        errors = []
        worker = threading.Thread(
            target=lambda: self._capture_error(
                errors,
                lambda: controller.rob_moveto(
                    [1.0] * 6, cancel_event=cancel_event
                ),
            )
        )
        worker.start()
        self.assertTrue(sdk.started.wait(1))
        cancel_event.set()
        worker.join(1)
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], ControlledStopError)
        self.assertIn("motion_abort", str(errors[0]))

    def test_jaka_stop_status_errors_become_controlled_stop_error(self):
        for failure in ("exception", "malformed"):
            with self.subTest(failure=failure):
                sdk = FakeJakaSdk(stop_state_error=failure)
                controller = self.make_arm_controller(sdk)
                cancel_event = threading.Event()
                errors = []
                worker = threading.Thread(
                    target=lambda: self._capture_error(
                        errors,
                        lambda: controller.rob_moveto(
                            [1.0] * 6, cancel_event=cancel_event
                        ),
                    )
                )
                worker.start()
                self.assertTrue(sdk.started.wait(1))
                cancel_event.set()
                worker.join(1)
                self.assertEqual(len(errors), 1)
                self.assertIsInstance(errors[0], ControlledStopError)

    def test_jaka_motion_timeout_abort_failure_is_hardware_unknown(self):
        sdk = FakeJakaSdk(abort_code=-1)
        controller = self.make_arm_controller(sdk)
        controller.arm_motion_timeout = 0.005
        with self.assertRaises(ControlledStopError):
            controller.rob_moveto([1.0] * 6)

    @staticmethod
    def _capture_error(errors, action):
        try:
            action()
        except Exception as error:
            errors.append(error)

    def test_external_axis_uses_timeouts_and_confirms_target_state(self):
        controller = self.make_arm_controller()
        initial = [
            {"id": index + 1, "pos": 0.0, "enable": True}
            for index in range(4)
        ]
        target = [1.0, 2.0, 3.0, 4.0]
        final = [
            {"id": index + 1, "pos": value, "enable": True}
            for index, value in enumerate(target)
        ]
        with patch("robot.ArmController.requests.get") as get, patch(
            "robot.ArmController.requests.post"
        ) as post:
            get.side_effect = [FakeResponse(payload=initial), FakeResponse(payload=final)]
            post.return_value = FakeResponse()
            self.assertTrue(controller.ext_moveto(target))

        self.assertEqual(post.call_args.kwargs["timeout"], (1.0, 2.0))
        self.assertTrue(all(call.kwargs["timeout"] == (1.0, 2.0) for call in get.call_args_list))

    def test_external_axis_unknown_state_during_cancel_raises(self):
        controller = self.make_arm_controller()
        initial = [
            {"id": index + 1, "pos": 0.0, "enable": True}
            for index in range(4)
        ]
        cancel_event = threading.Event()
        with patch("robot.ArmController.requests.get") as get, patch(
            "robot.ArmController.requests.post"
        ) as post:
            get.side_effect = [FakeResponse(payload=initial), requests.Timeout("status")]
            post.side_effect = lambda *args, **kwargs: (
                cancel_event.set(),
                (_ for _ in ()).throw(requests.Timeout("moveto")),
            )[-1]
            with self.assertRaises(ControlledStopError):
                controller.ext_moveto([1.0] * 4, cancel_event=cancel_event)

    def test_external_axis_unknown_state_without_cancel_raises(self):
        controller = self.make_arm_controller()
        initial = [
            {"id": index + 1, "pos": 0.0, "enable": True}
            for index in range(4)
        ]
        with patch("robot.ArmController.requests.get") as get, patch(
            "robot.ArmController.requests.post"
        ) as post:
            get.side_effect = [FakeResponse(payload=initial), requests.Timeout("status")]
            post.return_value = FakeResponse()
            with self.assertRaises(ControlledStopError):
                controller.ext_moveto([1.0] * 4)

    def test_external_axis_known_non_target_state_does_not_block_cancel(self):
        controller = self.make_arm_controller()
        state = [
            {"id": index + 1, "pos": 0.0, "enable": True}
            for index in range(4)
        ]
        cancel_event = threading.Event()
        with patch("robot.ArmController.requests.get") as get, patch(
            "robot.ArmController.requests.post"
        ) as post:
            get.side_effect = [FakeResponse(payload=state), FakeResponse(payload=state)]
            post.side_effect = lambda *args, **kwargs: (
                cancel_event.set(), FakeResponse()
            )[-1]
            self.assertFalse(
                controller.ext_moveto([1.0] * 4, cancel_event=cancel_event)
            )

    def test_robot_controller_uses_exact_hardware_return_codes(self):
        controller = RobotController.__new__(RobotController)
        controller._system_initialized = True
        controller.logger = Mock()
        controller.callbacks = {}
        controller.arm_controller = Mock()
        controller.arm_controller.rob_moveto.return_value = -1
        controller.arm_controller.ext_moveto.return_value = False
        controller.system_status = SystemStatus.IDLE
        controller.last_error = None

        self.assertFalse(controller.move_robot_to_position([0.0] * 6))
        self.assertEqual(controller.system_status, SystemStatus.ERROR)
        controller.system_status = SystemStatus.IDLE
        self.assertFalse(controller.move_ext_to_position([0.0] * 4))
        self.assertEqual(controller.system_status, SystemStatus.ERROR)

    def test_robot_controller_does_not_swallow_controlled_stop_error(self):
        controller = RobotController.__new__(RobotController)
        controller._system_initialized = True
        controller.logger = Mock()
        controller.callbacks = {}
        controller.arm_controller = Mock()
        controller.arm_controller.rob_moveto.side_effect = ControlledStopError(
            "joint status unavailable"
        )
        controller.system_status = SystemStatus.IDLE
        with self.assertRaises(ControlledStopError):
            controller.move_robot_to_position([0.0] * 6)

    def test_real_robot_controller_selects_arm_controller(self):
        arm = Mock()
        with patch("robot.RobotController.AGVController"), patch(
            "robot.ArmController.ArmController", return_value=arm
        ) as arm_type:
            controller = RobotController({
                "robot_config": {"robot_ip": "127.0.0.1"},
                "env_sensor_config": {},
                "camera_config": {},
            }, auto_setup=False)

        arm_type.assert_called_once()
        self.assertIs(controller.arm_controller, arm)
        self.assertIs(controller.jaka_controller, arm)
        self.assertIs(controller.ext_controller, arm)


if __name__ == "__main__":
    unittest.main()
