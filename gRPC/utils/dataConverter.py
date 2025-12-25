import gRPC.RobotService_pb2 as robot_pb2

from dataModels.CommandModels import (
    CommandEnvelope, CmdType, create_cmd_envelope, TaskCmd, RobotMode, 
    RobotModeCmd, TaskDataJson, RobotModeDataJson, joyControlCmd, joyControlDataJson
)
from dataModels.MessageModels import (
    MessageEnvelope, MsgType, create_message_envelope,
    BatteryInfo, PositionInfo, TaskInfo, SystemStatus, ErrorInfo,
    DeviceInfo, EnvironmentInfo, ArriveServicePointInfo
)
from dataModels.TaskModels import Task, TaskStatus, StationConfig, OperationConfig, OperationMode

def convert_server_cmd_to_command_envelope(server_cmd_request: robot_pb2.ServerCmdRequest) -> CommandEnvelope:
    """将gRPC ServerCmdRequest转换为CommandEnvelope
    
    Args:
        server_cmd_request: gRPC服务端命令请求
        
    Returns:
        CommandEnvelope: 命令信封对象
    """
    # 转换CmdType枚举
    cmd_type_map = {
        robot_pb2.CmdType.ROBOT_MODE_CMD: CmdType.ROBOT_MODE_CMD,
        robot_pb2.CmdType.INSPECTION_CMD: CmdType.TASK_CMD,
        robot_pb2.CmdType.SERVICE_TASK_CMD: CmdType.TASK_CMD,
        robot_pb2.CmdType.MOUSE_MOVE_CMD: CmdType.JOY_CONTROL_CMD,
    }

    # 获取oneof字段名称，如果出错则使用command_type作为备选
    try:
        grpc_cmd_type = server_cmd_request.WhichOneof('data_json')
    except ValueError as e:
        print(f"WhichOneof错误: {e}")
        # 如果WhichOneof失败，使用command_type来确定类型
        if server_cmd_request.command_type == robot_pb2.CmdType.ROBOT_MODE_CMD:
            grpc_cmd_type = 'robot_mode_command'
        elif server_cmd_request.command_type in [robot_pb2.CmdType.INSPECTION_CMD, robot_pb2.CmdType.SERVICE_TASK_CMD]:
            grpc_cmd_type = 'task_cmd'
        elif server_cmd_request.command_type == robot_pb2.CmdType.MOUSE_MOVE_CMD:
            grpc_cmd_type = 'joy_control_cmd'
        else:
            grpc_cmd_type = None
    
    cmd_type = cmd_type_map.get(server_cmd_request.command_type, CmdType.ROBOT_MODE_CMD)
    
    # 根据命令类型提取数据
    data_json = {}
    
    if grpc_cmd_type == 'robot_mode_command':
        robot_mode_cmd = server_cmd_request.robot_mode_command
        # 转换RobotMode枚举
        robot_mode_map = {
            robot_pb2.RobotMode.INSPECTION: RobotMode.INSPECTION,
            robot_pb2.RobotMode.SERVICE: RobotMode.SERVICE,
            robot_pb2.RobotMode.ESTOP: RobotMode.ESTOP,
        }
        
        robot_mode = robot_mode_map.get(robot_mode_cmd.robot_mode, RobotMode.INSPECTION)
        
        data_json = {
            "robotModeCmd": {
                "robotMode": robot_mode.value
            }
        }
        
    elif grpc_cmd_type == 'task_cmd':
        task_cmd = server_cmd_request.task_cmd
        # 转换RobotMode枚举
        robot_mode_map = {
            robot_pb2.RobotMode.INSPECTION: RobotMode.INSPECTION,
            robot_pb2.RobotMode.SERVICE: RobotMode.SERVICE,
            robot_pb2.RobotMode.ESTOP: RobotMode.ESTOP,
        }
        
        robot_mode = robot_mode_map.get(task_cmd.robotMode, RobotMode.INSPECTION)
        
        # 转换OperationMode枚举
        operation_mode_map = {
            robot_pb2.OperationMode.OPERATION_MODE_UNSPECIFIED: "unspecified",
            robot_pb2.OperationMode.OPERATION_MODE_PHOTO: "capture",
            robot_pb2.OperationMode.OPERATION_MODE_COLLECT: "collect",
            robot_pb2.OperationMode.OPERATION_MODE_DOOR: "door",
            robot_pb2.OperationMode.OPERATION_MODE_INSPECTION: "inspection",
            robot_pb2.OperationMode.OPERATION_MODE_SERVICE: "service",
        }
        
        # 转换TaskStatus枚举
        task_status_map = {
            robot_pb2.TaskStatus.TASK_STATUS_UNSPECIFIED: "unspecified",
            robot_pb2.TaskStatus.TASK_STATUS_PENDING: "pending",
            robot_pb2.TaskStatus.TASK_STATUS_RUNNING: "running",
            robot_pb2.TaskStatus.TASK_STATUS_COMPLETED: "completed",
            robot_pb2.TaskStatus.TASK_STATUS_FAILED: "failed",
            robot_pb2.TaskStatus.TASK_STATUS_CANCELLED: "cancelled",
            robot_pb2.TaskStatus.TASK_STATUS_RETRYING: "retrying",
        }
        
        # 转换站点配置
        station_tasks = []
        for station in task_cmd.station_tasks:
            operation_config = station.operation_config
            operation_mode = operation_mode_map.get(operation_config.operation_mode, "unspecified")
            
            station_config = {
                "station_id": station.station_id,
                "sort": station.sort,
                "name": station.name,
                "agv_marker": station.agv_marker,
                "robot_pos": list(station.robot_pos),
                "ext_pos": list(station.ext_pos),
                "operation_config": {
                    "operation_mode": operation_mode,
                    "door_ip": operation_config.door_ip,
                    "device_id": operation_config.device_id
                }
            }
            station_tasks.append(station_config)
        
        data_json = {
            "taskCmd": {
                "taskId": task_cmd.task_id,
                "taskName": task_cmd.task_id,  # 使用task_id作为task_name
                "robotMode": robot_mode.value,
                "generateTime": task_cmd.generate_time,
                "stationTasks": station_tasks
            }
        }
        
    elif grpc_cmd_type == 'joy_control_cmd':
        joy_cmd = server_cmd_request.joy_control_cmd
        
        data_json = {
            "joyControlCmd": {
                "angular_velocity": float(joy_cmd.angular_velocity),
                "linear_velocity": float(joy_cmd.linear_velocity)
            }
        }
    
    # 创建命令信封
    command_envelope = CommandEnvelope(
        cmdId=str(server_cmd_request.command_id),
        cmdTime=int(server_cmd_request.command_time),
        cmdType=cmd_type,
        robotId=server_cmd_request.robot_id,
        dataJson=data_json
    )
    
    return command_envelope

    
def convert_message_to_client_message(msg_envelope: MessageEnvelope) -> robot_pb2.RobotUploadRequest:
    """将MessageEnvelope转换为gRPC RobotUploadRequest
    
    Args:
        msg_envelope: 消息信封
        
    Returns:
        robot_pb2.RobotUploadRequest: gRPC请求消息
    """
    # 转换MsgType枚举
    msg_type_map = {
        MsgType.ROBOT_STATUS: robot_pb2.MsgType.ROBOT_STATUS,
        MsgType.DEVICE_DATA: robot_pb2.MsgType.DEVICE_DATA,
        MsgType.ENVIRONMENT_DATA: robot_pb2.MsgType.ENVIRONMENT_DATA,
        MsgType.ARRIVE_SERVER_POINT: robot_pb2.MsgType.ARRIVE_SERVER_POINT
    }
    
    grpc_msg_type = msg_type_map.get(msg_envelope.msgType, robot_pb2.MsgType.ROBOT_STATUS)

    # 创建基础请求
    # 将msgId转换为整数ID，如果是UUID格式则取前8位转为16进制整数
    msg_id_str = msg_envelope.msgId
    if '_' in msg_id_str:
        msg_id_str = msg_id_str.split('_')[-1]
    
    try:
        # 尝试直接转换为整数
        msg_id = int(msg_id_str)
    except ValueError:
        # 如果是UUID或其他格式，取前8位转为16进制整数
        msg_id = int(msg_id_str.replace('-', '')[:8], 16)
        
    grpc_msg = robot_pb2.RobotUploadRequest(
        msg_id=msg_id,
        msg_time=msg_envelope.msgTime,
        msg_type=grpc_msg_type,
        robot_id=msg_envelope.robotId,
    )
    
    data_json = msg_envelope.dataJson
    
    # 根据消息类型填充具体数据
    if msg_envelope.msgType == MsgType.ROBOT_STATUS:
        # 转换机器人状态
        battery_info = data_json.get('batteryInfo', {})
        position_info = data_json.get('positionInfo', {})
        system_status = data_json.get('systemStatus', {})
        
        # 转换MoveStatus枚举
        move_status_map = {
            'idle': robot_pb2.MoveStatus.IDLE,
            'running': robot_pb2.MoveStatus.RUNNING,
            'succeeded': robot_pb2.MoveStatus.SUCCEEDED,
            'failed': robot_pb2.MoveStatus.FAILED,
            'canceled': robot_pb2.MoveStatus.CANCELED
        }
        
        move_status = move_status_map.get(system_status.get('move_status', 'idle'), robot_pb2.MoveStatus.IDLE)
        
        # 设置机器人状态数据
        grpc_msg.robot_status.CopyFrom(robot_pb2.RobotStatusUpload(
            battery_info=robot_pb2.BatteryInfo(
                power_percent=battery_info.get('power_percent', 0.0),
                charge_status=battery_info.get('charge_status', 'discharging')
            ),
            position_info=robot_pb2.PositionInfo(
                AGVPositionInfo=position_info.get('AGVPositionInfo', [0.0, 0.0, 0.0]),
                ARMPositionInfo=position_info.get('ARMPositionInfo', [0.0]*6),
                EXTPositionInfo=position_info.get('EXTPositionInfo', [0.0]*4),
                targetPoint=position_info.get('targetPoint', ''),
                pointId=int(position_info.get('pointId', 0)) if position_info.get('pointId') else 0
            ),
            system_status=robot_pb2.SystemStatus(
                move_status=move_status,
                is_connected=system_status.get('is_connected', True),
                soft_estop_status=system_status.get('soft_estop_status', False),
                hard_estop_status=system_status.get('hard_estop_status', False),
                estop_status=system_status.get('estop_status', False)
            )
        ))
    
    elif msg_envelope.msgType == MsgType.ENVIRONMENT_DATA:
        # 转换环境数据
        env_info = data_json.get('environmentInfo', {})
        position_info = data_json.get('positionInfo', {})
        
        grpc_msg.environment_data.CopyFrom(robot_pb2.EnvironmentDataUpload(
            position_info=robot_pb2.PositionInfo(
                AGVPositionInfo=position_info.get('AGVPositionInfo', [0.0, 0.0, 0.0]),
                ARMPositionInfo=position_info.get('ARMPositionInfo', [0.0]*6),
                EXTPositionInfo=position_info.get('EXTPositionInfo', [0.0]*4),
                targetPoint=position_info.get('targetPoint', ''),
                pointId=int(position_info.get('pointId', 0)) if position_info.get('pointId') else 0
            ),
            sensor_data=robot_pb2.SensorData(
                temperature=env_info.get('temperature', 0.0),
                humidity=env_info.get('humidity', 0.0),
                oxygen=env_info.get('oxygen', 0.0),
                carbon_dioxide=env_info.get('carbonDioxide', 0.0),
                pm25=env_info.get('pm25', 0.0),
                pm10=env_info.get('pm10', 0.0),
                etvoc=env_info.get('etvoc', 0.0),
                noise=env_info.get('noise', 0.0)
            )
        ))
    
    elif msg_envelope.msgType == MsgType.DEVICE_DATA:
        # 转换设备数据
        device_info = data_json.get('deviceInfo', {})
        position_info = data_json.get('positionInfo', {})
        
        grpc_msg.device_data.CopyFrom(robot_pb2.DeviceDataUpload(
            position_info=robot_pb2.PositionInfo(
                AGVPositionInfo=position_info.get('AGVPositionInfo', [0.0, 0.0, 0.0]),
                ARMPositionInfo=position_info.get('ARMPositionInfo', [0.0]*6),
                EXTPositionInfo=position_info.get('EXTPositionInfo', [0.0]*4),
                targetPoint=position_info.get('targetPoint', ''),
                pointId=int(position_info.get('pointId', 0)) if position_info.get('pointId') else 0
            ),
            device_info=robot_pb2.DeviceInfo(
                device_id=device_info.get('deviceId', ''),
                data_type=device_info.get('dataType', ''),
                image_base64=device_info.get('imageBase64', '')
            )
        ))
    
    elif msg_envelope.msgType == MsgType.ARRIVE_SERVER_POINT:
        # 转换到达服务点数据
        grpc_msg.arrive_service_point.CopyFrom(robot_pb2.ArriveServicePointUpload())
    
    return grpc_msg

