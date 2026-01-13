from datetime import datetime
import json
from typing import Generator, List, Dict, Any, Optional
import sqlite3
from contextlib import contextmanager
from utils.logger_config import get_logger
from dataModels.TaskModels import Task, Station, StationTaskStatus, TaskStatus
from dataModels.UnifiedCommand import UnifiedCommand, CommandStatus

class TaskDatabase:
    """任务数据库管理 - 负责任务和消息的持久化存储"""
    
    def __init__(self, db_path: str = "tasks.db"):
        self.db_path = db_path
        self.logger = get_logger(__name__)
        self._init_database()
    
    def _init_database(self):
        """初始化数据库表"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            # 创建任务主表
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY,
                    task_name TEXT NOT NULL,
                    robot_mode TEXT NOT NULL,
                    status TEXT DEFAULT 'pending',
                    generate_time TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    started_at TIMESTAMP,
                    completed_at TIMESTAMP,
                    error_message TEXT,
                    metadata_json TEXT DEFAULT '{}'
                )
            ''')
            
            # 创建站点任务表
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS station_tasks (
                    station_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    station_config_json TEXT NOT NULL,
                    status TEXT DEFAULT 'pending',
                    sort INTEGER NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    started_at TIMESTAMP,
                    completed_at TIMESTAMP,
                    retry_count INTEGER DEFAULT 0,
                    max_retries INTEGER DEFAULT 3,
                    error_message TEXT,
                    metadata_json TEXT DEFAULT '{}',
                    FOREIGN KEY (task_id) REFERENCES tasks (task_id)
                )
            ''')
            
            # 创建任务执行历史表
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS task_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT,
                    station_id TEXT,
                    action TEXT,
                    status TEXT,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    details TEXT,
                    FOREIGN KEY (task_id) REFERENCES tasks (task_id)
                )
            ''')
            
            # 创建机器人接收消息表
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS robot_received_messages (
                    msg_id TEXT PRIMARY KEY,
                    msg_time INTEGER NOT NULL,
                    cmd_type TEXT NOT NULL,
                    robot_id TEXT NOT NULL,
                    data_json TEXT NOT NULL,
                    processed BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # 创建机器人发送消息表
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS robot_sent_messages (
                    msg_id TEXT PRIMARY KEY,
                    msg_time INTEGER NOT NULL,
                    msg_type TEXT NOT NULL,
                    robot_id TEXT NOT NULL,
                    data_json TEXT NOT NULL,
                    status TEXT DEFAULT 'pending',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            # 创建统一命令表
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS unified_commands (
                    command_id TEXT PRIMARY KEY,
                    cmd_type TEXT NOT NULL,
                    category TEXT NOT NULL,
                    priority INTEGER DEFAULT 5,
                    status TEXT DEFAULT 'pending',
                    data_json TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    started_at TIMESTAMP,
                    completed_at TIMESTAMP,
                    retry_count INTEGER DEFAULT 0,
                    max_retries INTEGER DEFAULT 3,
                    error_message TEXT,
                    metadata_json TEXT DEFAULT '{}'
                )
            ''')

            # 创建索引
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_cmd_status
                ON unified_commands(status)
            ''')
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_cmd_created
                ON unified_commands(created_at)
            ''')
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_cmd_priority
                ON unified_commands(priority, status)
            ''')

            # 创建环境数据历史表
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS environment_data_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    robot_id TEXT NOT NULL,
                    position_x REAL,
                    position_y REAL,
                    position_theta REAL,
                    temperature REAL,
                    humidity REAL,
                    oxygen REAL,
                    carbon_dioxide REAL,
                    pm25 REAL,
                    pm10 REAL,
                    etvoc REAL,
                    noise REAL,
                    metadata_json TEXT DEFAULT '{}'
                )
            ''')

            # 创建环境数据表索引
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_env_timestamp
                ON environment_data_history(timestamp)
            ''')
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_env_robot_id
                ON environment_data_history(robot_id)
            ''')

            # 创建系统配置表
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS system_config (
                    config_key TEXT PRIMARY KEY,
                    config_value TEXT NOT NULL,
                    config_type TEXT DEFAULT 'string',
                    description TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            # 为现有表添加索引（优化）
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_task_status
                ON tasks(status)
            ''')
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_task_created
                ON tasks(created_at)
            ''')
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_received_processed
                ON robot_received_messages(processed, msg_time)
            ''')
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_sent_status
                ON robot_sent_messages(status, msg_time)
            ''')

            conn.commit()
    
    @contextmanager
    def _get_connection(self) -> Generator[sqlite3.Connection, None, None]:
        """获取数据库连接"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            conn.close()
    
    def save_task(self, task: Task):
        """保存任务到数据库"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                
                # 保存任务主表
                cursor.execute('''
                    INSERT OR REPLACE INTO tasks 
                    (task_id, task_name, robot_mode, status, generate_time, 
                     created_at, started_at, completed_at, error_message, metadata_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    task.task_id,
                    task.task_name,
                    task.robot_mode.value,
                    task.status.value,
                    task.generate_time,
                    task.created_at,
                    task.started_at,
                    task.completed_at,
                    task.error_message,
                    json.dumps(task.metadata)
                ))
                
                # 保存站点任务
                for station in task.station_list:
                    cursor.execute('''
                        INSERT OR REPLACE INTO station_tasks 
                        (station_id, task_id, station_config_json, status, sort, 
                         created_at, started_at, completed_at, retry_count, max_retries, 
                         error_message, metadata_json)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (
                        f"{task.task_id}_{station.station_config.station_id}",
                        task.task_id,
                        json.dumps(station.station_config.to_dict()),
                        station.status.value,
                        station.station_config.sort,
                        station.created_at,
                        station.started_at,
                        station.completed_at,
                        station.retry_count,
                        station.max_retries,
                        station.error_message,
                        json.dumps(station.metadata)
                    ))
        except Exception as e:
            self.logger.error(f"保存任务失败: {e}")
            raise
    
    def update_task_status(self, task_id: str, status: TaskStatus, error_message: str = None):
        """更新任务状态"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            update_fields = ["status = ?"]
            params = [status.value]
            
            if status == TaskStatus.RUNNING:
                update_fields.append("started_at = ?")
                params.append(datetime.now())
            elif status in [TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.SKIPPED]:
                update_fields.append("completed_at = ?")
                params.append(datetime.now())
            
            if error_message:
                update_fields.append("error_message = ?")
                params.append(error_message)
            
            params.append(task_id)
            
            cursor.execute(f'''
                UPDATE tasks 
                SET {', '.join(update_fields)}
                WHERE task_id = ?
            ''', params)
    
    def update_station_task_status(self, station_id: str, status: StationTaskStatus, error_message: str = None):
        """更新站点任务状态"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            update_fields = ["status = ?"]
            params = [status.value]
            
            if status == StationTaskStatus.RUNNING:
                update_fields.append("started_at = ?")
                params.append(datetime.now())
            elif status in [StationTaskStatus.COMPLETED, StationTaskStatus.FAILED, StationTaskStatus.SKIPPED]:
                update_fields.append("completed_at = ?")
                params.append(datetime.now())
            
            if error_message:
                update_fields.append("error_message = ?")
                params.append(error_message)
            
            params.append(station_id)
            
            cursor.execute(f'''
                UPDATE station_tasks 
                SET {', '.join(update_fields)}
                WHERE station_id = ?
            ''', params)
    
    def add_station_retry_count(self, station_id: str):
        """增加站点任务重试次数"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE station_tasks 
                SET retry_count = retry_count + 1 
                WHERE station_id = ?
            ''', (station_id,))
    
    def log_task_action(self, task_id: str, station_id: str, 
                       action: str, status: str, details: str = None):
        """记录任务执行日志"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO task_history 
                (task_id, station_id, action, status, details)
                VALUES (?, ?, ?, ?, ?)
            ''', (task_id, station_id, action, status, details))
    
    def get_pending_tasks(self) -> List[Dict[str, Any]]:
        """获取待处理任务"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT * FROM tasks 
                WHERE status IN ('pending')
                ORDER BY created_at ASC
            ''')
            return [dict(row) for row in cursor.fetchall()]
    
    def get_task_by_id(self, task_id: str) -> Optional[Dict[str, Any]]:
        """根据ID获取任务"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT * FROM tasks 
                WHERE task_id = ?
            ''', (task_id,))
            row = cursor.fetchone()
            return dict(row) if row else None
    
    def get_station_tasks(self, task_id: str) -> List[Dict[str, Any]]:
        """获取任务的所有站点任务"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT * FROM station_tasks 
                WHERE task_id = ?
                ORDER BY sort ASC
            ''', (task_id,))
            return [dict(row) for row in cursor.fetchall()]
    
    # ==================== 机器人消息相关方法 ====================
    def save_received_message(self, msg_id: str, msg_time: int, cmd_type: str, robot_id: str, data_json: str):
        """保存机器人接收的消息"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO robot_received_messages 
                (msg_id, msg_time, cmd_type, robot_id, data_json)
                VALUES (?, ?, ?, ?, ?)
            ''', (msg_id, msg_time, cmd_type, robot_id, data_json))
    
    def mark_message_processed(self, msg_id: str):
        """标记消息已处理"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE robot_received_messages 
                SET processed = TRUE 
                WHERE msg_id = ?
            ''', (msg_id,))
    
    def get_unprocessed_messages(self) -> List[Dict[str, Any]]:
        """获取未处理的消息"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT * FROM robot_received_messages 
                WHERE processed = FALSE
                ORDER BY msg_time ASC
            ''')
            return [dict(row) for row in cursor.fetchall()]
    
    def save_sent_message(self, msg_id: str, msg_time: int, msg_type: str, robot_id: str, data_json: str, status: str = 'pending'):
        """保存机器人发送的消息"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO robot_sent_messages 
                (msg_id, msg_time, msg_type, robot_id, data_json, status)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (msg_id, msg_time, msg_type, robot_id, data_json, status))
    
    def update_sent_message_status(self, msg_id: str, status: str):
        """更新发送消息的状态"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    UPDATE robot_sent_messages 
                    SET status = ? 
                    WHERE msg_id = ?
                ''', (status, msg_id))
        except Exception as e:
            self.logger.error(f"更新发送消息状态失败: {e}")
            raise
    
    def get_received_messages(self, processed: bool = None, limit: int = 100) -> List[Dict[str, Any]]:
        """获取接收到的消息"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                
                query = '''
                    SELECT * FROM robot_received_messages 
                '''
                params = []
                
                if processed is not None:
                    query += '''
                        WHERE processed = ?
                    '''
                    params.append(processed)
                
                query += '''
                    ORDER BY msg_time DESC 
                    LIMIT ?
                '''
                params.append(limit)
                
                cursor.execute(query, params)
                return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            self.logger.error(f"获取接收到的消息失败: {e}")
            raise
    
    def get_sent_messages(self, status: str = None, limit: int = 100) -> List[Dict[str, Any]]:
        """获取发送的消息"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                
                query = '''
                    SELECT * FROM robot_sent_messages 
                '''
                params = []
                
                if status is not None:
                    query += '''
                        WHERE status = ?
                    '''
                    params.append(status)
                
                query += '''
                    ORDER BY msg_time DESC 
                    LIMIT ?
                '''
                params.append(limit)
                
                cursor.execute(query, params)
                return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            self.logger.error(f"获取发送的消息失败: {e}")
            raise

    # ==================== 统一命令相关方法 ====================
    def save_command(self, command: UnifiedCommand):
        """保存统一命令"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT OR REPLACE INTO unified_commands
                    (command_id, cmd_type, category, priority, status,
                     data_json, created_at, started_at, completed_at,
                     retry_count, max_retries, error_message, metadata_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    command.command_id,
                    command.cmd_type.value,
                    command.category.value,
                    command.priority,
                    command.status.value,
                    json.dumps(command._serialize_data(), ensure_ascii=False),
                    command.created_at,
                    command.started_at,
                    command.completed_at,
                    command.retry_count,
                    command.max_retries,
                    command.error_message,
                    json.dumps(command.metadata, ensure_ascii=False)
                ))
        except Exception as e:
            self.logger.error(f"保存命令失败: {e}")
            raise

    def update_command_status(
        self,
        command_id: str,
        status: CommandStatus,
        error_message: str = None
    ):
        """更新命令状态"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()

                update_fields = ["status = ?"]
                params = [status.value]

                if status == CommandStatus.RUNNING:
                    update_fields.append("started_at = ?")
                    params.append(datetime.now())
                elif status in [CommandStatus.COMPLETED, CommandStatus.FAILED, CommandStatus.CANCELLED]:
                    update_fields.append("completed_at = ?")
                    params.append(datetime.now())

                if error_message:
                    update_fields.append("error_message = ?")
                    params.append(error_message)

                params.append(command_id)

                cursor.execute(f'''
                    UPDATE unified_commands
                    SET {', '.join(update_fields)}
                    WHERE command_id = ?
                ''', params)
        except Exception as e:
            self.logger.error(f"更新命令状态失败: {e}")
            raise

    def add_command_retry_count(self, command_id: str):
        """增加命令重试次数"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    UPDATE unified_commands
                    SET retry_count = retry_count + 1
                    WHERE command_id = ?
                ''', (command_id,))
        except Exception as e:
            self.logger.error(f"增加命令重试次数失败: {e}")
            raise

    def update_command_metadata(self, command_id: str, metadata: Dict[str, Any]):
        """更新命令元数据

        Args:
            command_id: 命令ID
            metadata: 元数据字典
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                metadata_json = json.dumps(metadata, ensure_ascii=False)
                cursor.execute('''
                    UPDATE unified_commands
                    SET metadata_json = ?
                    WHERE command_id = ?
                ''', (metadata_json, command_id))
        except Exception as e:
            self.logger.error(f"更新命令元数据失败: {e}")
            raise

    def get_command_by_id(self, command_id: str) -> Optional[Dict[str, Any]]:
        """根据ID查询命令"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT * FROM unified_commands
                    WHERE command_id = ?
                ''', (command_id,))
                row = cursor.fetchone()
                return dict(row) if row else None
        except Exception as e:
            self.logger.error(f"查询命令失败: {e}")
            raise

    def get_commands_by_status(
        self,
        status: CommandStatus,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """根据状态查询命令"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT * FROM unified_commands
                    WHERE status = ?
                    ORDER BY priority ASC, created_at ASC
                    LIMIT ?
                ''', (status.value, limit))
                return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            self.logger.error(f"根据状态查询命令失败: {e}")
            raise

    def get_pending_commands(self, limit: int = 100) -> List[Dict[str, Any]]:
        """获取待处理的命令（按优先级排序）"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT * FROM unified_commands
                    WHERE status IN ('pending', 'queued')
                    ORDER BY priority ASC, created_at ASC
                    LIMIT ?
                ''', (limit,))
                return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            self.logger.error(f"获取待处理命令失败: {e}")
            raise

    # ==================== 环境数据历史相关方法 ====================
    def save_environment_data(
        self,
        robot_id: str,
        position: Dict[str, float],
        env_data: Dict[str, float],
        metadata: Dict[str, Any] = None
    ):
        """保存环境数据到历史表

        Args:
            robot_id: 机器人ID
            position: 位置信息 {x, y, theta}
            env_data: 环境数据 {temperature, humidity, oxygen, etc.}
            metadata: 元数据（可选）
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO environment_data_history
                    (robot_id, position_x, position_y, position_theta,
                     temperature, humidity, oxygen, carbon_dioxide,
                     pm25, pm10, etvoc, noise, metadata_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    robot_id,
                    position.get('x', 0.0),
                    position.get('y', 0.0),
                    position.get('theta', 0.0),
                    env_data.get('temperature', 0.0),
                    env_data.get('humidity', 0.0),
                    env_data.get('oxygen', 0.0),
                    env_data.get('carbon_dioxide', 0.0),
                    env_data.get('pm25', 0.0),
                    env_data.get('pm10', 0.0),
                    env_data.get('etvoc', 0.0),
                    env_data.get('noise', 0.0),
                    json.dumps(metadata or {}, ensure_ascii=False)
                ))
        except Exception as e:
            self.logger.error(f"保存环境数据失败: {e}")
            raise

    def get_environment_data_history(
        self,
        robot_id: str = None,
        start_time: datetime = None,
        end_time: datetime = None,
        limit: int = 1000
    ) -> List[Dict[str, Any]]:
        """查询环境数据历史

        Args:
            robot_id: 机器人ID（可选）
            start_time: 开始时间（可选）
            end_time: 结束时间（可选）
            limit: 返回记录数限制

        Returns:
            List[Dict[str, Any]]: 环境数据历史记录列表
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()

                query = 'SELECT * FROM environment_data_history WHERE 1=1'
                params = []

                if robot_id:
                    query += ' AND robot_id = ?'
                    params.append(robot_id)

                if start_time:
                    query += ' AND timestamp >= ?'
                    params.append(start_time)

                if end_time:
                    query += ' AND timestamp <= ?'
                    params.append(end_time)

                query += ' ORDER BY timestamp DESC LIMIT ?'
                params.append(limit)

                cursor.execute(query, params)
                return [dict(row) for row in cursor.fetchall()]

        except Exception as e:
            self.logger.error(f"查询环境数据历史失败: {e}")
            raise

    # ==================== 系统配置相关方法 ====================
    def save_config(
        self,
        key: str,
        value: Any,
        config_type: str = 'string',
        description: str = None
    ):
        """保存系统配置

        Args:
            key: 配置键
            value: 配置值
            config_type: 配置类型 (string, int, float, json, bool)
            description: 配置描述
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()

                # 根据类型转换值
                if config_type == 'json':
                    value_str = json.dumps(value, ensure_ascii=False)
                else:
                    value_str = str(value)

                cursor.execute('''
                    INSERT OR REPLACE INTO system_config
                    (config_key, config_value, config_type, description, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                ''', (key, value_str, config_type, description, datetime.now()))

        except Exception as e:
            self.logger.error(f"保存系统配置失败: {e}")
            raise

    def get_config(self, key: str, default: Any = None) -> Any:
        """获取系统配置

        Args:
            key: 配置键
            default: 默认值

        Returns:
            Any: 配置值
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT config_value, config_type FROM system_config
                    WHERE config_key = ?
                ''', (key,))
                row = cursor.fetchone()

                if not row:
                    return default

                value_str = row['config_value']
                config_type = row['config_type']

                # 根据类型转换值
                if config_type == 'int':
                    return int(value_str)
                elif config_type == 'float':
                    return float(value_str)
                elif config_type == 'bool':
                    return value_str.lower() in ('true', '1', 'yes')
                elif config_type == 'json':
                    return json.loads(value_str)
                else:
                    return value_str

        except Exception as e:
            self.logger.error(f"获取系统配置失败: {e}")
            return default

    def get_all_configs(self) -> Dict[str, Any]:
        """获取所有系统配置

        Returns:
            Dict[str, Any]: 所有配置的字典
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT * FROM system_config')

                configs = {}
                for row in cursor.fetchall():
                    key = row['config_key']
                    value_str = row['config_value']
                    config_type = row['config_type']

                    # 根据类型转换值
                    if config_type == 'int':
                        configs[key] = int(value_str)
                    elif config_type == 'float':
                        configs[key] = float(value_str)
                    elif config_type == 'bool':
                        configs[key] = value_str.lower() in ('true', '1', 'yes')
                    elif config_type == 'json':
                        configs[key] = json.loads(value_str)
                    else:
                        configs[key] = value_str

                return configs

        except Exception as e:
            self.logger.error(f"获取所有系统配置失败: {e}")
            raise
