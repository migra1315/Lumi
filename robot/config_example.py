"""
config_example.py
系统配置示例
"""

# 系统配置字典
SYSTEM_CONFIG = {
    # AGV配置
    "agv_ip": "192.168.10.10",      # AGV IP地址
    "agv_port": 31001,               # AGV端口
    
    # 机械臂配置
    "robot_ip": "192.168.10.90",    # 机械臂IP地址
    
    # 外部轴配置
    "ext_base_url": "http://192.168.10.90:5000/api/extaxis",  # 外部轴服务URL
    
    # 外部轴关节限制
    "ext_axis_limits": {
        "joint1": {
            "min": 0,
            "max": 200,
            "desc": "X轴移动"
        },
        "joint2": {
            "min": -50,
            "max": 50,
            "desc": "Y轴移动"
        },
        "joint3": {
            "min": -30,
            "max": 30,
            "desc": "Z轴升降"
        },
        "joint4": {
            "min": -100,
            "max": 100,
            "desc": "旋转轴"
        }
    },
    
    # 系统参数
    "debug": True,                  # 调试模式
    "log_level": "INFO",            # 日志级别
    "max_retries": 3,               # 最大重试次数
    "command_timeout": 30,          # 命令超时时间(秒)
    
    # 站点配置（可选）
    "stations": {
        "marker_1": {
            "name": "实验室1门口",
            "robot_home_pos": [0, 30, 100, 0, 60, -90],
            "ext_home_pos": [10, 0, 0, 0]
        },
        "charge_point_1F_6010": {
            "name": "充电桩",
            "robot_home_pos": [0, -90, 0, 0, 0, -90],
            "ext_home_pos": [80, 0, 0, 0]
        }
    }
}