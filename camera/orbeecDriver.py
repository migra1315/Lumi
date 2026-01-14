import cv2
import numpy as np
from pyorbbecsdk import *
import sys

def display_rgb_image():
    # 初始化管道
    pipeline = Pipeline()
    
    # 配置管道
    config = Config()
    
    # 启用RGB流
    try:
        # 尝试获取并启用RGB流
        profile_list = pipeline.get_stream_profile_list(OBSensorType.COLOR_SENSOR)
        rgb_profile = profile_list.get_default_video_stream_profile()
        config.enable_stream(rgb_profile)
    except:
        print("无法获取RGB流配置，尝试默认配置...")
        config.enable_stream(OBStreamType.COLOR, 0, 0, OBFormat.RGB)
    
    try:
        # 启动管道
        pipeline.start(config)
    except OBError as e:
        print(f"启动相机失败: {e}")
        sys.exit(1)
    
    print("相机已启动，按ESC键退出...")
    
    try:
        while True:
            # 等待帧集合
            frames = pipeline.wait_for_frames(100)
            if frames is None:
                continue
            
            # 获取RGB帧
            color_frame = frames.get_color_frame()
            if color_frame is None:
                continue
            
            # 获取图像数据
            color_data = color_frame.get_data()
            
            # 获取帧信息
            width = color_frame.get_width()
            height = color_frame.get_height()
            format = color_frame.get_format()
            
            # 将原始数据转换为numpy数组
            if format == OBFormat.RGB:
                # RGB格式
                color_image = np.frombuffer(color_data, dtype=np.uint8)
                color_image = color_image.reshape((height, width, 3))
                # BGR转换为RGB（OpenCV使用BGR格式显示）
                display_image = cv2.cvtColor(color_image, cv2.COLOR_RGB2BGR)
            elif format == OBFormat.MJPG:
                # MJPG格式需要解码
                color_image = cv2.imdecode(np.frombuffer(color_data, dtype=np.uint8), cv2.IMREAD_COLOR)
                if color_image is not None:
                    display_image = color_image
                else:
                    continue
            else:
                print(f"不支持的图像格式: {format}")
                continue
            
            # 显示图像
            cv2.imshow('Orbbec Gemini 2 L - RGB Image', display_image)
            
            # 按ESC键退出
            key = cv2.waitKey(1)
            if key == 27:  # ESC键
                break
            
    except KeyboardInterrupt:
        print("\n程序被用户中断")
    except Exception as e:
        print(f"发生错误: {e}")
    finally:
        # 停止管道
        pipeline.stop()
        cv2.destroyAllWindows()
        print("程序已退出")

if __name__ == "__main__":
    display_rgb_image()