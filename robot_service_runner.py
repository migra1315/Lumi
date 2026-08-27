import inspect
import os
import signal
import threading

from RobotControlSystem import RobotControlSystem, load_config

stop_event = threading.Event()


def _handle_stop(signum, frame):
    del signum, frame
    stop_event.set()


def _configure_logging(config):
    from utils.logger_config import setup_logging, log_system_info

    log_config = config.get("log_config", {})
    setup_kwargs = {
        "level": log_config.get("level", "INFO"),
        "log_name_prefix": log_config.get("log_name_prefix", "robot_control_system"),
        "use_color": False,
        "enable_file_logging": log_config.get("enable_file_logging", True),
        "max_log_days": log_config.get("max_log_days", 30),
        "robot_id": config.get("robot_id", 123456),
    }

    try:
        signature = inspect.signature(setup_logging)
    except (TypeError, ValueError):
        signature = None

    if signature is not None:
        accepts_kwargs = any(
            parameter.kind == inspect.Parameter.VAR_KEYWORD
            for parameter in signature.parameters.values()
        )
        if not accepts_kwargs:
            setup_kwargs = {
                key: value
                for key, value in setup_kwargs.items()
                if key in signature.parameters
            }

    setup_logging(**setup_kwargs)
    log_system_info()


def main():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(base_dir, "conf", "config.json")
    config = load_config(config_path)
    _configure_logging(config)

    robot = RobotControlSystem(
        config=config,
        use_mock=config.get("use_mock", True),
        report=config.get("report", True),
    )

    signal.signal(signal.SIGINT, _handle_stop)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, _handle_stop)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _handle_stop)

    try:
        robot.start()
        while not stop_event.wait(5):
            pass
    finally:
        robot.shutdown()


if __name__ == "__main__":
    main()
