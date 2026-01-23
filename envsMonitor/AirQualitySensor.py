import serial
import struct
import time
from datetime import datetime

class AirQualitySensor:
    def __init__(self, port='COM1', baudrate=4800, address=0x01, timeout=1):
        """
        初始化空气质量传感器
        
        参数:
            port: 串口名称，如 'COM1' 或 '/dev/ttyUSB0'
            baudrate: 波特率，默认4800
            address: 设备地址，默认0x01
            timeout: 超时时间，默认1秒
        """
        self.port = port
        self.baudrate = baudrate
        self.address = address
        self.timeout = timeout
        self.ser = None
        
        # 寄存器地址定义（根据文档）
        self.REGISTERS = {
            'pm25': 0x0000,      # PM2.5
            'pm10': 0x0001,      # PM10
            'humidity': 0x0002,  # 湿度
            'temperature': 0x0003, # 温度
            'tvoc': 0x0007,      # TVOC
            'co2': 0x0008,       # 二氧化碳
            'oxygen': 0x000B,    # 氧气
            'noise': 0x0013,     # 噪声
        }
        
    def connect(self):
        """连接串口设备"""
        try:
            self.ser = serial.Serial(
                port=self.port,
                baudrate=self.baudrate,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=self.timeout
            )
            print(f"成功连接到串口 {self.port}")
            return True
        except Exception as e:
            print(f"连接串口失败: {e}")
            return False
    
    def disconnect(self):
        """断开串口连接"""
        if self.ser and self.ser.is_open:
            self.ser.close()
            print("串口连接已关闭")
    
    def calculate_crc(self, data):
        """计算CRC16校验码 (Modbus RTU)"""
        crc = 0xFFFF
        for pos in data:
            crc ^= pos
            for i in range(8):
                if (crc & 1) != 0:
                    crc >>= 1
                    crc ^= 0xA001
                else:
                    crc >>= 1
        return crc
    
    def send_modbus_request(self, function_code, start_address, register_count):
        """发送Modbus请求并接收响应"""
        if not self.ser or not self.ser.is_open:
            print("串口未连接")
            return None
        
        # 构建请求帧
        request = bytearray()
        request.append(self.address)  # 地址码
        request.append(function_code)  # 功能码
        request.extend(start_address.to_bytes(2, byteorder='big'))  # 起始地址
        request.extend(register_count.to_bytes(2, byteorder='big'))  # 寄存器数量
        
        # 计算CRC
        crc = self.calculate_crc(request)
        request.extend(crc.to_bytes(2, byteorder='little'))  # CRC低位在前
        
        # 清空输入缓冲区
        self.ser.reset_input_buffer()
        
        # 发送请求
        self.ser.write(request)
        time.sleep(0.2)  # 等待设备响应
        
        # 接收响应
        response = self.ser.read(1024)
        
        if len(response) < 5:  # 最小响应长度
            print(f"响应数据长度不足: {len(response)} 字节")
            return None
        
        # 验证CRC
        received_crc = int.from_bytes(response[-2:], byteorder='little')
        calculated_crc = self.calculate_crc(response[:-2])
        
        if received_crc != calculated_crc:
            print(f"CRC校验失败: 接收={received_crc:04X}, 计算={calculated_crc:04X}")
            return None
        
        return response
    
    def read_register(self, parameter_name):
        """读取单个寄存器值"""
        if parameter_name not in self.REGISTERS:
            print(f"未知参数: {parameter_name}")
            return None
            
        register_address = self.REGISTERS[parameter_name]
        response = self.send_modbus_request(0x03, register_address, 1)
        
        if response and len(response) >= 7:
            byte_count = response[2]
            if byte_count == 2:
                raw_value = int.from_bytes(response[3:5], byteorder='big')
                return raw_value
        return None
    
    def read_pm25(self):
        """读取PM2.5浓度 (μg/m³)"""
        raw_value = self.read_register('pm25')
        if raw_value is not None:
            # PM2.5为实际值，无需转换
            return raw_value
        return None
    
    def read_pm10(self):
        """读取PM10浓度 (μg/m³)"""
        raw_value = self.read_register('pm10')
        if raw_value is not None:
            # PM10为实际值，无需转换
            return raw_value
        return None
    
    def read_temperature(self):
        """读取温度 (°C)"""
        raw_value = self.read_register('temperature')
        if raw_value is not None:
            # 根据文档，温度值扩大了10倍上传
            temperature = raw_value / 10.0
            return temperature
        return None
    
    def read_humidity(self):
        """读取湿度 (%RH)"""
        raw_value = self.read_register('humidity')
        if raw_value is not None:
            # 根据文档，湿度值扩大了10倍上传
            humidity = raw_value / 10.0
            return humidity
        return None
    
    def read_tvoc(self):
        """读取TVOC浓度 (ppb)"""
        raw_value = self.read_register('tvoc')
        if raw_value is not None:
            # TVOC为实际值，无需转换
            return raw_value
        return None
    
    def read_co2(self):
        """读取二氧化碳浓度 (ppm)"""
        raw_value = self.read_register('co2')
        if raw_value is not None:
            # CO2为实际值，无需转换
            return raw_value
        return None
    
    def read_oxygen(self):
        """读取氧气浓度 (%VOL)"""
        raw_value = self.read_register('oxygen')
        if raw_value is not None:
            # 根据文档，氧气值扩大了10倍上传
            oxygen = raw_value / 10.0
            return oxygen
        return None
    
    def read_noise(self):
        """读取噪声水平 (dB)"""
        raw_value = self.read_register('noise')
        if raw_value is not None:
            # 根据文档，噪声值扩大了10倍上传
            noise = raw_value / 10.0
            return noise
        return None
    
    def read_all_parameters(self):
        """一次性读取所有参数"""
        results = {}
        
        # 读取各个参数
        results['pm25'] = self.read_pm25()
        results['pm10'] = self.read_pm10()
        results['temperature'] = self.read_temperature()
        results['humidity'] = self.read_humidity()
        results['tvoc'] = self.read_tvoc()
        results['co2'] = self.read_co2()
        results['oxygen'] = self.read_oxygen()
        results['noise'] = self.read_noise()
        
        return results
    
    def print_parameters(self, data):
        """格式化打印参数数据"""
        print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 传感器数据:")
        print("-" * 60)
        
        # 空气质量参数
        if data['pm25'] is not None:
            print(f"PM2.5: {data['pm25']} μg/m³")
        if data['pm10'] is not None:
            print(f"PM10: {data['pm10']} μg/m³")
        if data['tvoc'] is not None:
            print(f"TVOC: {data['tvoc']} ppb")
        if data['co2'] is not None:
            print(f"CO2: {data['co2']} ppm")
        
        # 环境参数
        if data['temperature'] is not None:
            print(f"温度: {data['temperature']:.1f} °C")
        if data['humidity'] is not None:
            print(f"湿度: {data['humidity']:.1f} %RH")
        if data['oxygen'] is not None:
            print(f"氧气: {data['oxygen']:.1f} %VOL")
        if data['noise'] is not None:
            print(f"噪声: {data['noise']:.1f} dB")
        
        print("-" * 60)

def main():
    # 配置参数 - 请根据实际情况修改
    PORT = 'COM4'  # 串口名称，Windows通常是COM1, COM2等，Linux是/dev/ttyUSB0等
    BAUDRATE = 4800  # 波特率，默认4800
    ADDRESS = 0x01   # 设备地址，默认0x01
    
    # 创建传感器实例
    sensor = AirQualitySensor(port=PORT, baudrate=BAUDRATE, address=ADDRESS)
    
    try:
        # 连接设备
        if not sensor.connect():
            return
        
        print("开始读取传感器数据...")
        print("支持的参数: PM2.5, PM10, TVOC, CO2, 温度, 湿度, 氧气, 噪声")
        print("按 Ctrl+C 停止读取")
        
        read_count = 0
        success_count = 0
        
        while True:
            read_count += 1
            print(f"\n第 {read_count} 次读取...")
            
            # 读取所有参数
            data = sensor.read_all_parameters()
            
            # 检查是否成功读取到数据
            valid_data = [v for v in data.values() if v is not None]
            if len(valid_data) > 0:
                success_count += 1
                sensor.print_parameters(data)
                print(f"成功率: {success_count}/{read_count} ({success_count/read_count*100:.1f}%)")
            else:
                print("读取数据失败，请检查连接和配置")
                print("可能的原因:")
                print("1. 串口连接错误")
                print("2. 波特率不匹配")
                print("3. 设备地址错误")
                print("4. 设备未响应")
            
            # 等待5秒后再次读取
            time.sleep(5)
            
    except KeyboardInterrupt:
        print("\n程序被用户中断")
    except Exception as e:
        print(f"发生错误: {e}")
    finally:
        # 断开连接
        sensor.disconnect()

# 单次读取的简化函数
def quick_read(port='COM4', baudrate=4800, address=0x01):
    """
    快速单次读取所有参数
    """
    sensor = AirQualitySensor(port=port, baudrate=baudrate, address=address)
    
    try:
        if sensor.connect():
            data = sensor.read_all_parameters()
            sensor.print_parameters(data)
            return data
        else:
            print("连接失败")
            return None
    finally:
        sensor.disconnect()

if __name__ == "__main__":
    main()
    
    # 如果需要单次读取，可以取消下面的注释
    # quick_read(port='COM3')  # 请修改为实际的串口号