from dataModels.TaskModels import TaskStatus, Task, Station, StationTaskStatus, StationConfig, OperationConfig, OperationMode, RobotMode
from datetime import datetime
import gRPC.RobotService_pb2 as robot_pb2

from dataModels.CommandModels import (
    CommandEnvelope, CmdType, create_cmd_envelope, TaskCmd, RobotModeCmd, 
    joyControlCmd, CommandResponse, ChargeCmd, SetMarkerCmd, PositionAdjustCmd
)
from dataModels.MessageModels import (
    MessageEnvelope, MsgType, create_message_envelope, UploadResponse,
    BatteryInfo, PositionInfo, TaskListInfo, SystemStatus, ErrorInfo,
    DeviceInfo, EnvironmentInfo, ArriveServicePointInfo
)

# ====================== gRPC -> Python 转换 ======================

def convert_server_cmd_to_command_envelope(server_cmd_request: robot_pb2.ServerCmdRequest) -> CommandEnvelope:
    """将gRPC ServerCmdRequest转换为CommandEnvelope
    
    Args:
        server_cmd_request: gRPC服务端命令请求
        
    Returns:
        CommandEnvelope: 命令信封对象
    """
    # 转换CmdType枚举
    cmd_type_map = {
        robot_pb2.CmdType.RESPONSE_CMD: CmdType.RESPONSE_CMD,
        robot_pb2.CmdType.ROBOT_MODE_CMD: CmdType.ROBOT_MODE_CMD,
        robot_pb2.CmdType.TASK_CMD: CmdType.TASK_CMD,
        robot_pb2.CmdType.JOY_CONTROL_CMD: CmdType.JOY_CONTROL_CMD,
        robot_pb2.CmdType.SET_MARKER_CMD: CmdType.SET_MARKER_CMD,
        robot_pb2.CmdType.CHARGE_CMD: CmdType.CHARGE_CMD,
        robot_pb2.CmdType.POSITION_ADJUST_CMD: CmdType.POSITION_ADJUST_CMD,
    }
    
    cmd_type = cmd_type_map.get(server_cmd_request.command_type, CmdType.RESPONSE_CMD)
    
    # 根据命令类型提取数据
    data_json = {}
    
    if server_cmd_request.HasField('robot_mode_command'):
        robot_mode_cmd = server_cmd_request.robot_mode_command
        # 转换RobotMode枚举
        robot_mode_map = {
            robot_pb2.RobotMode.INSPECTION: RobotMode.INSPECTION,
            robot_pb2.RobotMode.SERVICE: RobotMode.SERVICE,
            robot_pb2.RobotMode.JOY_CONTROL: RobotMode.JOY_CONTROL,
            robot_pb2.RobotMode.ESTOP: RobotMode.ESTOP,
            robot_pb2.RobotMode.CHARGE: RobotMode.CHARGE,
        }
        
        robot_mode_enum = robot_mode_map.get(robot_mode_cmd.robot_mode, RobotMode.STAND_BY)
        
        data_json = {
            "robot_mode_cmd": {
                "robot_mode": robot_mode_enum.value
            }
        }
        
    elif server_cmd_request.HasField('task_cmd'):
        station_proto = server_cmd_request.task_cmd
        
        # 转换StationTaskStatus枚举
        station_status_map = {
            robot_pb2.StationTaskStatus.STATION_TASK_STATUS_UNSPECIFIED: StationTaskStatus.PENDING,
            robot_pb2.StationTaskStatus.STATION_TASK_STATUS_PENDING: StationTaskStatus.PENDING,
            robot_pb2.StationTaskStatus.STATION_TASK_STATUS_RUNNING: StationTaskStatus.RUNNING,
            robot_pb2.StationTaskStatus.STATION_TASK_STATUS_COMPLETED: StationTaskStatus.COMPLETED,
            robot_pb2.StationTaskStatus.STATION_TASK_STATUS_FAILED: StationTaskStatus.FAILED,
            robot_pb2.StationTaskStatus.STATION_TASK_STATUS_CANCELLED: StationTaskStatus.SKIPPED,
            robot_pb2.StationTaskStatus.STATION_TASK_STATUS_RETRYING: StationTaskStatus.RETRYING,
            robot_pb2.StationTaskStatus.STATION_TASK_STATUS_TO_RETRY: StationTaskStatus.TO_RETRY,
        }
        
        # 转换OperationMode枚举
        operation_mode_map = {
            robot_pb2.OperationMode.OPERATION_MODE_NONE: OperationMode.NONE,
            robot_pb2.OperationMode.OPERATION_MODE_OPEN_DOOR: OperationMode.OPEN_DOOR,
            robot_pb2.OperationMode.OPERATION_MODE_CLOSE_DOOR: OperationMode.CLOSE_DOOR,
            robot_pb2.OperationMode.OPERATION_MODE_CAPTURE: OperationMode.CAPTURE,
            robot_pb2.OperationMode.OPERATION_MODE_SERVICE: OperationMode.SERVE,
        }
        
        # 转换StationConfig
        station_config_proto = station_proto.station_config
        
        # 转换OperationConfig
        operation_config_proto = station_config_proto.operation_config
        operation_config = OperationConfig(
            operation_mode=operation_mode_map.get(operation_config_proto.operation_mode, OperationMode.NONE),
            door_ip=operation_config_proto.door_ip or None,
            device_id=operation_config_proto.device_id or None
        )
        
        # 创建StationConfig
        station_config = StationConfig(
            station_id=station_config_proto.station_id,
            sort=station_config_proto.sort,
            name=station_config_proto.name,
            agv_marker=station_config_proto.agv_marker,
            robot_pos=list(station_config_proto.robot_pos),
            ext_pos=list(station_config_proto.ext_pos),
            operation_config=operation_config
        )
        
        # 转换元数据
        metadata = {}
        if station_proto.meta_data:
            for key, value in station_proto.meta_data.items():
                metadata[key] = value
        
        # 创建Station对象
        station = Station(
            station_config=station_config,
            status=station_status_map.get(station_proto.status, StationTaskStatus.PENDING),
            created_at=datetime.fromtimestamp(station_proto.created_at / 1000) if station_proto.created_at else None,
            started_at=datetime.fromtimestamp(station_proto.started_at / 1000) if station_proto.started_at else None,
            completed_at=datetime.fromtimestamp(station_proto.completed_at / 1000) if station_proto.completed_at else None,
            retry_count=station_proto.retry_count,
            max_retries=station_proto.max_retries,
            error_message=station_proto.error_message or None,
            metadata=metadata
        )
        
        # 创建包含一个站点的任务列表
        task = Task(
            task_id=f"task_{station_proto.command_id}",
            task_name=station_config.name,
            station_list=[station],
            status=TaskStatus.PENDING,
            robot_mode=RobotMode.STAND_BY,  # 从其他字段获取或使用默认值
            generate_time=datetime.fromtimestamp(station_proto.generate_time / 1000) if station_proto.generate_time else None,
            created_at=datetime.now(),
            metadata={"source": "grpc_command"}
        )
        
        data_json = {
            "task_cmd": {
                "task_id": task.task_id,
                "task_name": task.task_name,
                "robot_mode": task.robot_mode,
                "generate_time": task.generate_time,
                "station_config_tasks": [station_config]  # 注意：这里传入的是StationConfig列表
            }
        }
        
    elif server_cmd_request.HasField('joy_control_cmd'):
        joy_cmd = server_cmd_request.joy_control_cmd
        
        data_json = {
            "joy_control_cmd": {
                "angular_velocity": float(joy_cmd.angular_velocity) if joy_cmd.angular_velocity else 0.0,
                "linear_velocity": float(joy_cmd.linear_velocity) if joy_cmd.linear_velocity else 0.0
            }
        }
    
    # 创建命令信封
    command_envelope = CommandEnvelope(
        cmd_id=str(server_cmd_request.command_id),
        cmd_time=int(server_cmd_request.command_time),
        cmd_type=cmd_type,
        robot_id=server_cmd_request.robot_id,
        data_json=data_json
    )
    
    return command_envelope


def convert_robot_upload_response_to_message_envelope(grpc_response: robot_pb2.RobotUploadResponse) -> MessageEnvelope:
    """将gRPC RobotUploadResponse转换为MessageEnvelope
    
    Args:
        grpc_response: gRPC上传响应
        
    Returns:
        MessageEnvelope: 消息信封对象
    """
    # 转换MsgType枚举
    msg_type_map = {
        robot_pb2.MsgType.ROBOT_STATUS: MsgType.ROBOT_STATUS,
        robot_pb2.MsgType.DEVICE_DATA: MsgType.DEVICE_DATA,
        robot_pb2.MsgType.ENVIRONMENT_DATA: MsgType.ENVIRONMENT_DATA,
        robot_pb2.MsgType.ARRIVE_SERVER_POINT: MsgType.ARRIVE_SERVER_POINT,
    }
    
    msg_type = msg_type_map.get(grpc_response.msg_type, MsgType.ROBOT_STATUS)
    
    # 创建响应对象
    response = UploadResponse(
        code=grpc_response.data_json.code,
        info=grpc_response.data_json.info
    )
    
    # 创建消息信封
    message_envelope = MessageEnvelope(
        msgId=str(grpc_response.msg_id),
        msgTime=int(grpc_response.msg_time),
        msgType=msg_type,
        robotId=grpc_response.robot_id,
        dataJson={"response": response.to_dict()}
    )
    
    return message_envelope


# ====================== Python -> gRPC 转换 ======================

def convert_message_envelope_to_robot_upload_request(msg_envelope: MessageEnvelope) -> robot_pb2.RobotUploadRequest:
    """将MessageEnvelope转换为gRPC RobotUploadRequest
    
    Args:
        msg_envelope: 消息信封对象
        
    Returns:
        robot_pb2.RobotUploadRequest: gRPC请求消息
    """
    # 转换MsgType枚举
    msg_type_map = {
        MsgType.ROBOT_STATUS: robot_pb2.MsgType.ROBOT_STATUS,
        MsgType.DEVICE_DATA: robot_pb2.MsgType.DEVICE_DATA,
        MsgType.ENVIRONMENT_DATA: robot_pb2.MsgType.ENVIRONMENT_DATA,
        MsgType.ARRIVE_SERVER_POINT: robot_pb2.MsgType.ARRIVE_SERVER_POINT,
    }
    
    grpc_msg_type = msg_type_map.get(msg_envelope.msgType, robot_pb2.MsgType.ROBOT_STATUS)
    
    # 将msgId转换为整数
    try:
        msg_id = int(msg_envelope.msgId)
    except ValueError:
        # 如果不是纯数字，使用哈希值
        msg_id = hash(msg_envelope.msgId) % (2**31)
    
    # 创建基础请求
    grpc_msg = robot_pb2.RobotUploadRequest(
        msg_id=abs(msg_id),  # 确保为正数
        msg_time=msg_envelope.msgTime,
        msg_type=grpc_msg_type,
        robot_id=msg_envelope.robotId,
    )
    
    data_json = msg_envelope.dataJson
    
    # 根据消息类型填充具体数据
    if msg_envelope.msgType == MsgType.ROBOT_STATUS:
        battery_info = data_json.get('batteryInfo', {})
        position_info = data_json.get('positionInfo', {})
        task_info = data_json.get('taskListInfo', {})
        system_status = data_json.get('systemStatus', {})
        error_info = data_json.get('errorInfo')
        
        # 转换MoveStatus枚举
        move_status_map = {
            'idle': robot_pb2.MoveStatus.IDLE,
            'running': robot_pb2.MoveStatus.RUNNING,
            'succeeded': robot_pb2.MoveStatus.SUCCEEDED,
            'failed': robot_pb2.MoveStatus.FAILED,
            'canceled': robot_pb2.MoveStatus.CANCELED
        }
        
        move_status_str = system_status.get('move_status', 'idle')
        if isinstance(move_status_str, str):
            move_status = move_status_map.get(move_status_str.lower(), robot_pb2.MoveStatus.IDLE)
        else:
            move_status = robot_pb2.MoveStatus.IDLE
        
        # 创建电池信息
        battery_info_proto = robot_pb2.BatteryInfo(
            power_percent=battery_info.get('power_percent', 0.0),
            charge_status=battery_info.get('charge_status', 'unknown')
        )
        
        # 创建位置信息
        position_info_proto = robot_pb2.PositionInfo(
            AGVPositionInfo=list(position_info.get('AGVPositionInfo', [0.0, 0.0, 0.0])),
            ARMPositionInfo=list(position_info.get('ARMPositionInfo', [0.0]*6)),
            EXTPositionInfo=list(position_info.get('EXTPositionInfo', [0.0]*4))
        )
        
        # 创建任务信息
        task_list_info_proto = robot_pb2.TaskListInfo()
        if 'task' in task_info:
            task_obj = task_info['task']
            # 这里可以添加将Task对象转换为proto的逻辑
            # 由于结构复杂，这里先创建空对象
            
        # 创建系统状态
        system_status_proto = robot_pb2.SystemStatus(
            move_status=move_status,
            is_connected=system_status.get('is_connected', True),
            soft_estop_status=system_status.get('soft_estop_status', False),
            hard_estop_status=system_status.get('hard_estop_status', False),
            estop_status=system_status.get('estop_status', False)
        )
        
        # 创建错误信息（如果有）
        error_info_proto = None
        if error_info:
            error_info_proto = robot_pb2.ErrorInfo(
                code=error_info.get('code', 0),
                level=error_info.get('level', 'info'),
                message=error_info.get('message', '')
            )
        
        # 设置机器人状态数据
        robot_status = robot_pb2.RobotStatusUpload(
            battery_info=battery_info_proto,
            position_info=position_info_proto,
            task_list_info=task_list_info_proto,
            system_status=system_status_proto,
            error_info=error_info_proto
        )
        
        grpc_msg.robot_status.CopyFrom(robot_status)
    
    elif msg_envelope.msgType == MsgType.ENVIRONMENT_DATA:
        position_info = data_json.get('positionInfo', {})
        task_info = data_json.get('taskListInfo', {})
        environment_info = data_json.get('environmentInfo', {})
        
        # 创建位置信息
        position_info_proto = robot_pb2.PositionInfo(
            AGVPositionInfo=list(position_info.get('AGVPositionInfo', [0.0, 0.0, 0.0])),
            ARMPositionInfo=list(position_info.get('ARMPositionInfo', [0.0]*6)),
            EXTPositionInfo=list(position_info.get('EXTPositionInfo', [0.0]*4))
        )
        
        # 创建任务信息
        task_list_info_proto = robot_pb2.TaskListInfo()
        
        # 创建环境信息
        environment_info_proto = robot_pb2.EnvironmentInfo(
            temperature=environment_info.get('temperature', 0.0),
            humidity=environment_info.get('humidity', 0.0),
            oxygen=environment_info.get('oxygen', 0.0),
            carbonDioxide=environment_info.get('carbonDioxide', 0.0),
            pm25=environment_info.get('pm25', 0.0),
            pm10=environment_info.get('pm10', 0.0),
            etvoc=environment_info.get('etvoc', 0.0),
            noise=environment_info.get('noise', 0.0)
        )
        
        environment_data = robot_pb2.EnvironmentDataUpload(
            position_info=position_info_proto,
            task_list_info=task_list_info_proto,
            environment_info=environment_info_proto
        )
        
        grpc_msg.environment_data.CopyFrom(environment_data)
    
    elif msg_envelope.msgType == MsgType.DEVICE_DATA:
        position_info = data_json.get('positionInfo', {})
        task_info = data_json.get('taskListInfo', {})
        device_info = data_json.get('deviceInfo', {})
        
        # 创建位置信息
        position_info_proto = robot_pb2.PositionInfo(
            AGVPositionInfo=list(position_info.get('AGVPositionInfo', [0.0, 0.0, 0.0])),
            ARMPositionInfo=list(position_info.get('ARMPositionInfo', [0.0]*6)),
            EXTPositionInfo=list(position_info.get('EXTPositionInfo', [0.0]*4))
        )
        
        # 创建任务信息
        task_list_info_proto = robot_pb2.TaskListInfo()
        
        # 创建设备信息
        device_info_proto = robot_pb2.DeviceInfo(
            device_id=device_info.get('deviceId', ''),
            data_type=device_info.get('dataType', ''),
            image_base64=list(device_info.get('imageBase64', []))
        )
        
        device_data = robot_pb2.DeviceDataUpload(
            position_info=position_info_proto,
            task_list_info=task_list_info_proto,
            device_info=device_info_proto
        )
        
        grpc_msg.device_data.CopyFrom(device_data)
    
    elif msg_envelope.msgType == MsgType.ARRIVE_SERVER_POINT:
        position_info = data_json.get('positionInfo', {})
        task_info = data_json.get('taskListInfo', {})
        arrive_info = data_json.get('arriveServePointInfo', {})
        
        # 创建位置信息
        position_info_proto = robot_pb2.PositionInfo(
            AGVPositionInfo=list(position_info.get('AGVPositionInfo', [0.0, 0.0, 0.0])),
            ARMPositionInfo=list(position_info.get('ARMPositionInfo', [0.0]*6)),
            EXTPositionInfo=list(position_info.get('EXTPositionInfo', [0.0]*4))
        )
        
        # 创建任务信息
        task_list_info_proto = robot_pb2.TaskListInfo()
        
        # 创建到达信息
        arrive_info_proto = robot_pb2.ArriveServicePointInfo(
            is_arrive=arrive_info.get('isArrive', False)
        )
        
        arrive_data = robot_pb2.ArriveServicePointUpload(
            position_info=position_info_proto,
            task_list_info=task_list_info_proto,
            arrive_service_point_info=arrive_info_proto
        )
        
        grpc_msg.arrive_service_point.CopyFrom(arrive_data)
    
    return grpc_msg


def convert_command_envelope_to_server_cmd_response(cmd_envelope: CommandEnvelope) -> robot_pb2.ServerCmdResponse:
    """将CommandEnvelope转换为gRPC ServerCmdResponse
    
    Args:
        cmd_envelope: 命令信封对象
        
    Returns:
        robot_pb2.ServerCmdResponse: gRPC响应消息
    """
    # 转换CmdType枚举
    cmd_type_map = {
        CmdType.RESPONSE_CMD: robot_pb2.CmdType.RESPONSE_CMD,
        CmdType.ROBOT_MODE_CMD: robot_pb2.CmdType.ROBOT_MODE_CMD,
        CmdType.TASK_CMD: robot_pb2.CmdType.TASK_CMD,
        CmdType.JOY_CONTROL_CMD: robot_pb2.CmdType.JOY_CONTROL_CMD,
        CmdType.SET_MARKER_CMD: robot_pb2.CmdType.SET_MARKER_CMD,
        CmdType.CHARGE_CMD: robot_pb2.CmdType.CHARGE_CMD,
        CmdType.POSITION_ADJUST_CMD: robot_pb2.CmdType.POSITION_ADJUST_CMD,
    }
    
    grpc_cmd_type = cmd_type_map.get(cmd_envelope.cmd_type, robot_pb2.CmdType.RESPONSE_CMD)
    
    # 将cmd_id转换为整数
    try:
        cmd_id = int(cmd_envelope.cmd_id)
    except ValueError:
        cmd_id = hash(cmd_envelope.cmd_id) % (2**31)
    
    # 提取响应信息
    response_data = {}
    if cmd_envelope.cmd_type == CmdType.RESPONSE_CMD:
        response_data = cmd_envelope.data_json.get('response_cmd', {})
    else:
        response_data = cmd_envelope.data_json.get('response', {})
    
    # 创建响应
    server_response = robot_pb2.ServerResponse(
        code=response_data.get('code', '0'),
        info=response_data.get('info', '')
    )
    
    # 创建gRPC响应
    grpc_response = robot_pb2.ServerCmdResponse(
        command_id=abs(cmd_id),  # 确保为正数
        command_time=cmd_envelope.cmd_time,
        command_type=grpc_cmd_type,
        robot_id=cmd_envelope.robot_id,
        data_json=server_response
    )
    
    return grpc_response


# ====================== 辅助转换函数 ======================

def convert_task_cmd_to_task(task_cmd: TaskCmd) -> Task:
    """将TaskCmd转换为Task对象
    
    Args:
        task_cmd: 任务命令对象
        
    Returns:
        Task: 转换后的任务对象
    """
    # 创建站点列表
    station_list = []
    
    for station_config in task_cmd.station_config_tasks:
        # 创建操作配置
        operation_config = OperationConfig(
            operation_mode=station_config.operation_config.operation_mode,
            door_ip=station_config.operation_config.door_ip,
            device_id=station_config.operation_config.device_id
        )
        
        # 创建站点配置
        station_config_obj = StationConfig(
            station_id=station_config.station_id,
            sort=station_config.sort,
            name=station_config.name,
            agv_marker=station_config.agv_marker,
            robot_pos=station_config.robot_pos,
            ext_pos=station_config.ext_pos,
            operation_config=operation_config
        )
        
        # 创建站点任务
        station = Station(
            station_config=station_config_obj,
            status=StationTaskStatus.PENDING,
            created_at=datetime.now(),
            retry_count=0,
            max_retries=3,
            metadata={
                "source": "task_cmd",
                "task_id": task_cmd.task_id
            }
        )
        
        station_list.append(station)
    
    # 创建任务对象
    task = Task(
        task_id=task_cmd.task_id,
        task_name=task_cmd.task_name,
        station_list=station_list,
        status=TaskStatus.PENDING,
        robot_mode=task_cmd.robot_mode,
        generate_time=task_cmd.generate_time,
        created_at=datetime.now(),
        metadata={
            "source": "task_cmd",
            "generate_time": task_cmd.generate_time.isoformat() if task_cmd.generate_time else None
        }
    )
    
    return task


def convert_station_to_proto_station(station: Station) -> robot_pb2.Station:
    """将Station对象转换为gRPC Station消息
    
    Args:
        station: Station对象
        
    Returns:
        robot_pb2.Station: gRPC Station消息
    """
    # 转换StationTaskStatus枚举
    station_status_map = {
        StationTaskStatus.PENDING: robot_pb2.StationTaskStatus.STATION_TASK_STATUS_PENDING,
        StationTaskStatus.RUNNING: robot_pb2.StationTaskStatus.STATION_TASK_STATUS_RUNNING,
        StationTaskStatus.COMPLETED: robot_pb2.StationTaskStatus.STATION_TASK_STATUS_COMPLETED,
        StationTaskStatus.FAILED: robot_pb2.StationTaskStatus.STATION_TASK_STATUS_FAILED,
        StationTaskStatus.SKIPPED: robot_pb2.StationTaskStatus.STATION_TASK_STATUS_CANCELLED,
        StationTaskStatus.RETRYING: robot_pb2.StationTaskStatus.STATION_TASK_STATUS_RETRYING,
        StationTaskStatus.TO_RETRY: robot_pb2.StationTaskStatus.STATION_TASK_STATUS_TO_RETRY,
    }
    
    # 转换OperationMode枚举
    operation_mode_map = {
        OperationMode.NONE: robot_pb2.OperationMode.OPERATION_MODE_NONE,
        OperationMode.OPEN_DOOR: robot_pb2.OperationMode.OPERATION_MODE_OPEN_DOOR,
        OperationMode.CLOSE_DOOR: robot_pb2.OperationMode.OPERATION_MODE_CLOSE_DOOR,
        OperationMode.CAPTURE: robot_pb2.OperationMode.OPERATION_MODE_CAPTURE,
        OperationMode.SERVE: robot_pb2.OperationMode.OPERATION_MODE_SERVICE,
    }
    
    # 创建OperationConfig
    operation_config = station.station_config.operation_config
    operation_config_proto = robot_pb2.OperationConfig(
        operation_mode=operation_mode_map.get(operation_config.operation_mode, robot_pb2.OperationMode.OPERATION_MODE_NONE),
        door_ip=operation_config.door_ip or "",
        device_id=operation_config.device_id or ""
    )
    
    # 创建StationConfig
    station_config_proto = robot_pb2.StationConfig(
        station_id=station.station_config.station_id,
        sort=station.station_config.sort,
        name=station.station_config.name,
        agv_marker=station.station_config.agv_marker,
        robot_pos=station.station_config.robot_pos,
        ext_pos=station.station_config.ext_pos,
        operation_config=operation_config_proto
    )
    
    # 创建元数据
    meta_data = {}
    if station.metadata:
        import google.protobuf.struct_pb2 as struct_pb2
        for key, value in station.metadata.items():
            if isinstance(value, (str, int, float, bool)):
                meta_data[key] = struct_pb2.Value(string_value=str(value))
    
    # 创建Station
    station_proto = robot_pb2.Station(
        station_config=station_config_proto,
        status=station_status_map.get(station.status, robot_pb2.StationTaskStatus.STATION_TASK_STATUS_PENDING),
        generate_time=int(station.created_at.timestamp() * 1000) if station.created_at else 0,
        created_at=int(station.created_at.timestamp() * 1000) if station.created_at else 0,
        started_at=int(station.started_at.timestamp() * 1000) if station.started_at else 0,
        completed_at=int(station.completed_at.timestamp() * 1000) if station.completed_at else 0,
        retry_count=station.retry_count,
        max_retries=station.max_retries,
        error_message=station.error_message or "",
        meta_data=meta_data
    )
    
    return station_proto