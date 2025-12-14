import logging
from robot.MockRobotController import MockRobotController  # 假设已存在的机器人控制器
from robot.RobotControllerAdapter import RobotControllerAdapter
from robot.config_example import SYSTEM_CONFIG
from task.TaskManager import TaskManager,OperationMode  # 假设已存在的任务管理器
import json
import time


# 配置日志
logging.basicConfig(level=logging.INFO)

def load_config(config_path):
    """加载系统配置"""
    try:
        # 使用绝对路径加载配置文件
        with open(config_path, 'r', encoding='utf-8') as f:
            user_config = json.load(f)
            
        # 从userCmdControl.json的systemConfig部分获取系统配置
        if "systemConfig" in user_config:
            return user_config["systemConfig"]
        else:
            print("警告: userCmdControl.json中没有systemConfig部分，使用默认配置")

    except Exception as e:
        print(f"加载配置文件失败: {e}")

    
def main():
    # 1. 创建机器人控制器
    print("\n1. 创建机器人控制器...")
    # robot_controller = RobotControllerAdapter(SYSTEM_CONFIG, debug=False)
    robot_controller = MockRobotController()
    
    # 2. 初始化系统
    print("\n2. 初始化机器人系统...")
    if not robot_controller.setup_system():
        print("系统初始化失败，退出")
        return
        
    print("系统初始化成功")
    
    # 2. 创建任务管理器
    task_manager = TaskManager(robot_controller)
    
    # 3. 接收JSON指令
    json_instruction = {
       "stations": {
        "station1": {
            "name": "实验室1门口",
            "agv_marker": "marker_1",
            "robot_home_pos": [
                0,
                30,
                100,
                0,
                60,
                -90
            ],
            "ext_home_pos": [
                10,
                0,
                0,
                0
            ],
            "operation_mode": "opendoor",
            "door_id": "door1"
        },
        "station2": {
            "name": "实验室1门里",
            "agv_marker": "marker_2",
            "robot_home_pos": [
                -50,
                30,
                100,
                0,
                60,
                -90
            ],
            "ext_home_pos": [
                50,
                0,
                0,
                0
            ],
            "operation_mode": "closedoor",
            "door_id": "door1"
        },
        "station3": {
            "name": "实验室1巡检点1",
            "agv_marker": "marker_3",
            "robot_home_pos": [
                -80,
                30,
                100,
                0,
                60,
                -90
            ],
            "ext_home_pos": [
                80,
                0,
                0,
                0
            ],
            "operation_mode": "capture"
        },
        "station4": {
            "name": "实验室1巡检点2",
            "agv_marker": "marker_4",
            "robot_home_pos": [
                -120,
                30,
                100,
                0,
                60,
                -90
            ],
            "ext_home_pos": [
                120,
                0,
                0,
                0
            ],
            "operation_mode": "capture"
        },
        "station5": {
            "name": "实验室1门里",
            "agv_marker": "marker_1",
            "robot_home_pos": [
                -90,
                30,
                100,
                0,
                60,
                -90
            ],
            "ext_home_pos": [
                150,
                0,
                0,
                30
            ],
            "operation_mode": "opendoor",
            "door_id": "door1"
        },
        "station6": {
            "name": "充电桩",
            "agv_marker": "charge_point_1F_6010",
            "robot_home_pos": [
                0,
                -90,
                0,
                0,
                0,
                -90
            ],
            "ext_home_pos": [
                80,
                0,
                0,
                0
            ],
            "operation_mode": "None",
            "door_id": "None"
        }
    }
    }
    stations_data = json_instruction.get("stations", {})
    
    # 遍历每个站点，作为单独任务提交
    for station_id, station_info in stations_data.items():
        # 为每个站点创建一个单独的任务结构
        single_station_task = {
            "stations": {
                station_id: station_info
            }
        }
        
        # 4. 提交任务
        task_id = task_manager.receive_task_from_json(single_station_task)
        print(f"站点 {station_id} 已作为单独任务提交，任务ID: {task_id}")

        # 5. 监控任务状态
        while True:
            status = task_manager.get_task_status(task_id)
            print(f"任务状态: {status.get('status')}")
            
            if status.get('status') in ['completed', 'failed']:
                break
            
            time.sleep(1)

    # 6. 获取历史任务
    all_tasks = task_manager.get_all_tasks()
    print(f"历史任务数量: {len(all_tasks)}")
    
    # 7. 关闭管理器
    task_manager.shutdown()

if __name__ == "__main__":
    main()