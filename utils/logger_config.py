"""
统一日志配置模块

提供集中式的日志配置和管理功能，确保整个系统使用统一的日志格式和配置。
"""

import logging
import logging.handlers
import os
import sys
from typing import Optional
from datetime import datetime


# ANSI颜色代码
class LogColors:
    """日志颜色配置"""
    RESET = '\033[0m'
    BOLD = '\033[1m'

    # 前景色
    BLACK = '\033[30m'
    RED = '\033[31m'
    GREEN = '\033[32m'
    YELLOW = '\033[33m'
    BLUE = '\033[34m'
    MAGENTA = '\033[35m'
    CYAN = '\033[36m'
    WHITE = '\033[37m'

    # 亮色
    BRIGHT_BLACK = '\033[90m'
    BRIGHT_RED = '\033[91m'
    BRIGHT_GREEN = '\033[92m'
    BRIGHT_YELLOW = '\033[93m'
    BRIGHT_BLUE = '\033[94m'
    BRIGHT_MAGENTA = '\033[95m'
    BRIGHT_CYAN = '\033[96m'
    BRIGHT_WHITE = '\033[97m'


class ColoredFormatter(logging.Formatter):
    """彩色日志格式化器"""

    # 不同日志级别的颜色配置
    LEVEL_COLORS = {
        'DEBUG': LogColors.BRIGHT_BLACK,
        'INFO': LogColors.BRIGHT_CYAN,
        'WARNING': LogColors.BRIGHT_YELLOW,
        'ERROR': LogColors.BRIGHT_RED,
        'CRITICAL': LogColors.BOLD + LogColors.BRIGHT_RED,
    }

    # 模块名颜色
    MODULE_COLOR = LogColors.BRIGHT_BLUE

    # 时间颜色
    TIME_COLOR = LogColors.BRIGHT_BLACK

    def __init__(self, fmt=None, datefmt=None, use_color=True):
        """
        初始化彩色格式化器

        Args:
            fmt: 日志格式字符串
            datefmt: 日期格式字符串
            use_color: 是否使用颜色
        """
        super().__init__(fmt, datefmt)
        self.use_color = use_color

    def format(self, record):
        """格式化日志记录"""
        if self.use_color and sys.stdout.isatty():
            # 保存原始属性
            levelname = record.levelname
            name = record.name
            asctime = self.formatTime(record, self.datefmt)

            # 应用颜色
            level_color = self.LEVEL_COLORS.get(levelname, '')
            record.levelname = f"{level_color}{levelname:8s}{LogColors.RESET}"
            record.name = f"{self.MODULE_COLOR}{name}{LogColors.RESET}"

            # 格式化消息
            result = super().format(record)

            # 恢复原始属性
            record.levelname = levelname
            record.name = name

            return result
        else:
            return super().format(record)


class ContextFilter(logging.Filter):
    """上下文过滤器，用于添加额外的上下文信息"""

    def __init__(self, robot_id=None):
        """
        初始化过滤器

        Args:
            robot_id: 机器人ID
        """
        super().__init__()
        self.robot_id = robot_id

    def filter(self, record):
        """添加额外的上下文信息"""
        if self.robot_id:
            record.robot_id = self.robot_id
        return True


def setup_logging(
    level: str = "INFO",
    log_file: Optional[str] = None,
    log_dir: str = "logs",
    use_color: bool = True,
    enable_file_logging: bool = True,
    max_bytes: int = 10 * 1024 * 1024,  # 10MB
    backup_count: int = 5,
    robot_id: Optional[int] = None
) -> None:
    """
    配置全局日志系统

    Args:
        level: 日志级别 (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_file: 日志文件名（如果为None，自动生成）
        log_dir: 日志目录
        use_color: 是否在控制台使用彩色输出
        enable_file_logging: 是否启用文件日志
        max_bytes: 日志文件最大大小（字节）
        backup_count: 保留的日志文件数量
        robot_id: 机器人ID（用于上下文信息）
    """
    # 创建日志目录
    if enable_file_logging and not os.path.exists(log_dir):
        os.makedirs(log_dir)

    # 获取根logger
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, level.upper()))

    # 清除现有的处理器（避免重复配置）
    root_logger.handlers.clear()

    # === 控制台处理器 ===
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(getattr(logging, level.upper()))

    # 控制台格式（带颜色）
    console_format = (
        '%(asctime)s | '
        '%(levelname)s | '
        '%(name)-25s | '
        '%(message)s'
    )
    console_formatter = ColoredFormatter(
        console_format,
        datefmt='%Y-%m-%d %H:%M:%S',
        use_color=use_color
    )
    console_handler.setFormatter(console_formatter)
    root_logger.addHandler(console_handler)

    # === 文件处理器 ===
    if enable_file_logging:
        # 生成日志文件名
        if log_file is None:
            timestamp = datetime.now().strftime('%Y%m%d')
            log_file = f"robot_system_{timestamp}.log"

        log_path = os.path.join(log_dir, log_file)

        # 使用RotatingFileHandler进行日志轮转
        file_handler = logging.handlers.RotatingFileHandler(
            log_path,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding='utf-8'
        )
        file_handler.setLevel(getattr(logging, level.upper()))

        # 文件格式（不带颜色）
        file_format = (
            '%(asctime)s | '
            '%(levelname)-8s | '
            '%(name)-25s | '
            '%(funcName)-20s | '
            '%(message)s'
        )
        file_formatter = logging.Formatter(
            file_format,
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        file_handler.setFormatter(file_formatter)
        root_logger.addHandler(file_handler)

        # 记录日志文件路径
        root_logger.info(f"日志文件: {os.path.abspath(log_path)}")

    # 添加上下文过滤器
    if robot_id is not None:
        context_filter = ContextFilter(robot_id=robot_id)
        root_logger.addFilter(context_filter)

    # 设置第三方库的日志级别（避免过多输出）
    logging.getLogger('urllib3').setLevel(logging.WARNING)
    logging.getLogger('requests').setLevel(logging.WARNING)
    logging.getLogger('grpc').setLevel(logging.WARNING)

    root_logger.info("=" * 80)
    root_logger.info(f"日志系统初始化完成 | 级别: {level.upper()}")
    if robot_id:
        root_logger.info(f"机器人ID: {robot_id}")
    root_logger.info("=" * 80)


def get_logger(name: str) -> logging.Logger:
    """
    获取logger实例

    Args:
        name: logger名称（通常使用 __name__）

    Returns:
        logging.Logger: logger实例

    Usage:
        from utils.logger_config import get_logger
        logger = get_logger(__name__)
        logger.info("This is a log message")
    """
    return logging.getLogger(name)


def set_module_level(module_name: str, level: str) -> None:
    """
    设置特定模块的日志级别

    Args:
        module_name: 模块名称
        level: 日志级别 (DEBUG, INFO, WARNING, ERROR, CRITICAL)

    Example:
        set_module_level('robot.AGVController', 'DEBUG')
    """
    logger = logging.getLogger(module_name)
    logger.setLevel(getattr(logging, level.upper()))
    logger.info(f"模块 {module_name} 日志级别设置为: {level.upper()}")


def log_system_info():
    """记录系统信息"""
    logger = get_logger(__name__)

    import platform
    import socket

    logger.info("=" * 80)
    logger.info("系统信息:")
    logger.info(f"  操作系统: {platform.system()} {platform.release()}")
    logger.info(f"  Python版本: {platform.python_version()}")
    logger.info(f"  主机名: {socket.gethostname()}")
    logger.info(f"  工作目录: {os.getcwd()}")
    logger.info("=" * 80)


# 便捷函数：快速设置日志
def quick_setup(level: str = "INFO", use_color: bool = True, enable_file: bool = True):
    """
    快速设置日志（使用默认配置）

    Args:
        level: 日志级别
        use_color: 是否使用颜色
        enable_file: 是否启用文件日志
    """
    setup_logging(
        level=level,
        use_color=use_color,
        enable_file_logging=enable_file
    )
