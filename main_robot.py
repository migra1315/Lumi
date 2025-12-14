"""
main.py
主程序示例
"""

import logging
import time
from robot.RobotControllerAdapter import RobotControllerAdapter
from robot.config_example import SYSTEM_CONFIG

def setup_logging():
    """设置日志"""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )


def main():
    """主函数"""
    print("=" * 60)
    print("巡检机器人控制系统 - 真实机器人版本")
    print("=" * 60)
    
    try:
        # 1. 创建机器人控制器（使用适配器保持接口兼容）
        print("\n1. 创建机器人控制器...")
        robot_controller = RobotControllerAdapter(SYSTEM_CONFIG, debug=True)
        
        # 2. 初始化系统
        print("\n2. 初始化机器人系统...")
        if not robot_controller.setup_system():
            print("系统初始化失败，退出")
            return
        
        print("系统初始化成功")
        
        # 3. 注册回调函数
        def on_status_change(status):
            print(f"状态变化: {status}")
        
        def on_battery_change(battery_status):
            print(f"电池状态变化: {battery_status}")
        
        robot_controller.register_callback("on_status_change", on_status_change)
        robot_controller.register_callback("on_battery_change", on_battery_change)
        
        # 4. 获取状态
        print("\n3. 获取机器人状态...")
        status = robot_controller.get_status()
        print(f"系统状态: {status.get('status')}")
        print(f"系统已初始化: {status.get('system_initialized')}")
        
        # 5. 执行示例任务
        print("\n4. 执行示例任务...")
        
        # 移动AGV到标记点
        print("\n  移动AGV到marker_1...")
        success = robot_controller.agv_controller.move_to_marker("marker_1")
        print(f"  AGV移动: {'成功' if success else '失败'}")
        
        # 移动机械臂到归位位置
        print("\n  移动机械臂到归位位置...")
        robot_position = [0, 30, 100, 0, 60, -90]
        success = robot_controller.jaka_controller.move_to_position(robot_position)
        print(f"  机械臂移动: {'成功' if success else '失败'}")
        
        # 移动外部轴到归位位置
        print("\n  移动外部轴到归位位置...")
        ext_position = [10, 0, 0, 0]
        success = robot_controller.ext_controller.move_to_position(ext_position)
        print(f"  外部轴移动: {'成功' if success else '失败'}")
        
        # 6. 显示最终状态
        print("\n5. 最终状态...")
        final_status = robot_controller.get_status()
        print(f"  当前状态: {final_status.get('status')}")
        print(f"  当前标记点: {final_status.get('current_marker')}")
        
        # 7. 清理
        print("\n6. 清理资源...")
        robot_controller.shutdown_system()
        
        print("\n程序执行完成!")
        print("=" * 60)
        
    except Exception as e:
        print(f"\n程序执行失败: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    setup_logging()
    main()