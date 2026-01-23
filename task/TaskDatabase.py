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
            
            # ==================== 四表消息架构 ====================

            # 1. client_upload_sent - clientUpload 流发送的消息（机器人上传的状态、环境数据）
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS client_upload_sent (
                    msg_id TEXT PRIMARY KEY,
                    msg_time INTEGER NOT NULL,
                    msg_type TEXT NOT NULL,
                    robot_id TEXT NOT NULL,
                    data_json TEXT NOT NULL,
                    status TEXT DEFAULT 'sent',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            # 2. client_upload_received - clientUpload 流接收的响应（服务器对上传的响应）
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS client_upload_received (
                    msg_id TEXT PRIMARY KEY,
                    msg_time INTEGER NOT NULL,
                    msg_type TEXT NOT NULL,
                    robot_id TEXT NOT NULL,
                    data_json TEXT NOT NULL,
                    processed BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            # 3. server_command_received - serverCommand 流接收的命令（服务器下发的命令）
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS server_command_received (
                    msg_id TEXT PRIMARY KEY,
                    msg_time INTEGER NOT NULL,
                    cmd_type TEXT NOT NULL,
                    robot_id TEXT NOT NULL,
                    data_json TEXT NOT NULL,
                    processed BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            # 4. server_command_sent - serverCommand 流发送的响应（机器人发送的命令响应）
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS server_command_sent (
                    msg_id TEXT PRIMARY KEY,
                    msg_time INTEGER NOT NULL,
                    msg_type TEXT NOT NULL,
                    robot_id TEXT NOT NULL,
                    command_id TEXT,
                    data_json TEXT NOT NULL,
                    status TEXT DEFAULT 'sent',
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

            # 四表消息架构索引
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_client_upload_sent_time
                ON client_upload_sent(msg_time)
            ''')
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_client_upload_sent_type
                ON client_upload_sent(msg_type)
            ''')
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_client_upload_received_time
                ON client_upload_received(msg_time)
            ''')
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_client_upload_received_processed
                ON client_upload_received(processed)
            ''')
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_server_command_received_time
                ON server_command_received(msg_time)
            ''')
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_server_command_received_processed
                ON server_command_received(processed)
            ''')
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_server_command_sent_time
                ON server_command_sent(msg_time)
            ''')
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_server_command_sent_type
                ON server_command_sent(msg_type)
            ''')
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_server_command_sent_command_id
                ON server_command_sent(command_id)
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

    # ==================== 四表消息架构方法 ====================

    # ---------- client_upload_sent 相关方法 ----------

    def save_client_upload_sent(self, msg_id: str, msg_time: int, msg_type: str,
                                 robot_id: str, data_json: str, status: str = 'sent'):
        """保存 clientUpload 流发送的消息（机器人上传的状态、环境数据）"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO client_upload_sent
                (msg_id, msg_time, msg_type, robot_id, data_json, status)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (msg_id, msg_time, msg_type, robot_id, data_json, status))

    def get_client_upload_sent(self, msg_type: str = None, limit: int = 100) -> List[Dict[str, Any]]:
        """获取 clientUpload 流发送的消息"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                query = 'SELECT * FROM client_upload_sent'
                params = []

                if msg_type is not None:
                    query += ' WHERE msg_type = ?'
                    params.append(msg_type)

                query += ' ORDER BY msg_time DESC LIMIT ?'
                params.append(limit)

                cursor.execute(query, params)
                return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            self.logger.error(f"获取 clientUpload 发送消息失败: {e}")
            raise

    # ---------- client_upload_received 相关方法 ----------

    def save_client_upload_received(self, msg_id: str, msg_time: int, msg_type: str,
                                     robot_id: str, data_json: str):
        """保存 clientUpload 流接收的响应（服务器对上传的响应）"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO client_upload_received
                (msg_id, msg_time, msg_type, robot_id, data_json)
                VALUES (?, ?, ?, ?, ?)
            ''', (msg_id, msg_time, msg_type, robot_id, data_json))

    def get_client_upload_received(self, processed: bool = None, limit: int = 100) -> List[Dict[str, Any]]:
        """获取 clientUpload 流接收的响应"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                query = 'SELECT * FROM client_upload_received'
                params = []

                if processed is not None:
                    query += ' WHERE processed = ?'
                    params.append(processed)

                query += ' ORDER BY msg_time DESC LIMIT ?'
                params.append(limit)

                cursor.execute(query, params)
                return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            self.logger.error(f"获取 clientUpload 接收消息失败: {e}")
            raise

    # ---------- server_command_received 相关方法 ----------

    def save_server_command_received(self, msg_id: str, msg_time: int, cmd_type: str,
                                      robot_id: str, data_json: str):
        """保存 serverCommand 流接收的命令（服务器下发的命令）"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO server_command_received
                (msg_id, msg_time, cmd_type, robot_id, data_json)
                VALUES (?, ?, ?, ?, ?)
            ''', (msg_id, msg_time, cmd_type, robot_id, data_json))

    def mark_server_command_processed(self, msg_id: str):
        """标记 serverCommand 命令已处理"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE server_command_received
                SET processed = TRUE
                WHERE msg_id = ?
            ''', (msg_id,))

    def get_server_command_received(self, processed: bool = None, limit: int = 100) -> List[Dict[str, Any]]:
        """获取 serverCommand 流接收的命令"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                query = 'SELECT * FROM server_command_received'
                params = []

                if processed is not None:
                    query += ' WHERE processed = ?'
                    params.append(processed)

                query += ' ORDER BY msg_time DESC LIMIT ?'
                params.append(limit)

                cursor.execute(query, params)
                return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            self.logger.error(f"获取 serverCommand 接收消息失败: {e}")
            raise

    # ---------- server_command_sent 相关方法 ----------

    def save_server_command_sent(self, msg_id: str, msg_time: int, msg_type: str,
                                  robot_id: str, data_json: str, command_id: str = None,
                                  status: str = 'sent'):
        """保存 serverCommand 流发送的响应（机器人发送的命令响应）"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO server_command_sent
                (msg_id, msg_time, msg_type, robot_id, command_id, data_json, status)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (msg_id, msg_time, msg_type, robot_id, command_id, data_json, status))

    def get_server_command_sent(self, command_id: str = None, msg_type: str = None,
                                 limit: int = 100) -> List[Dict[str, Any]]:
        """获取 serverCommand 流发送的响应"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                query = 'SELECT * FROM server_command_sent'
                params = []
                conditions = []

                if command_id is not None:
                    conditions.append('command_id = ?')
                    params.append(command_id)

                if msg_type is not None:
                    conditions.append('msg_type = ?')
                    params.append(msg_type)

                if conditions:
                    query += ' WHERE ' + ' AND '.join(conditions)

                query += ' ORDER BY msg_time DESC LIMIT ?'
                params.append(limit)

                cursor.execute(query, params)
                return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            self.logger.error(f"获取 serverCommand 发送消息失败: {e}")
            raise

    # ---------- 向后兼容方法（映射到新表） ----------

    def save_received_message(self, msg_id: str, msg_time: int, cmd_type: str,
                               robot_id: str, data_json: str):
        """保存机器人接收的消息（向后兼容，映射到 server_command_received）"""
        self.save_server_command_received(msg_id, msg_time, cmd_type, robot_id, data_json)

    def mark_message_processed(self, msg_id: str):
        """标记消息已处理（向后兼容，映射到 server_command_received）"""
        self.mark_server_command_processed(msg_id)

    def get_unprocessed_messages(self) -> List[Dict[str, Any]]:
        """获取未处理的消息（向后兼容，映射到 server_command_received）"""
        return self.get_server_command_received(processed=False)

    def save_sent_message(self, msg_id: str, msg_time: int, msg_type: str,
                          robot_id: str, data_json: str, status: str = 'sent'):
        """保存机器人发送的消息（向后兼容，自动判断存储到哪个表）

        根据 msg_type 自动判断：
        - robot_status/environment_data -> client_upload_sent
        - 其他（COMMAND_STATUS_UPDATE等）-> server_command_sent
        """
        # 注意：MsgType 枚举值是小写的，如 "robot_status", "environment_data"
        client_upload_types = ['robot_status', 'environment_data', 'ROBOT_STATUS', 'ENVIRONMENT_DATA']
        if msg_type in client_upload_types:
            self.save_client_upload_sent(msg_id, msg_time, msg_type, robot_id, data_json, status)
        else:
            self.save_server_command_sent(msg_id, msg_time, msg_type, robot_id, data_json, status=status)

    def update_sent_message_status(self, msg_id: str, status: str):
        """更新发送消息的状态（向后兼容，尝试更新两个表）"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                # 尝试更新 client_upload_sent
                cursor.execute('''
                    UPDATE client_upload_sent
                    SET status = ?
                    WHERE msg_id = ?
                ''', (status, msg_id))
                # 尝试更新 server_command_sent
                cursor.execute('''
                    UPDATE server_command_sent
                    SET status = ?
                    WHERE msg_id = ?
                ''', (status, msg_id))
        except Exception as e:
            self.logger.error(f"更新发送消息状态失败: {e}")
            raise

    def get_received_messages(self, processed: bool = None, limit: int = 100) -> List[Dict[str, Any]]:
        """获取接收到的消息（向后兼容，映射到 server_command_received）"""
        return self.get_server_command_received(processed=processed, limit=limit)

    def get_sent_messages(self, status: str = None, limit: int = 100) -> List[Dict[str, Any]]:
        """获取发送的消息（向后兼容，合并两个发送表的数据）"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()

                # 获取 client_upload_sent
                query1 = 'SELECT msg_id, msg_time, msg_type, robot_id, data_json, status, created_at FROM client_upload_sent'
                params1 = []
                if status is not None:
                    query1 += ' WHERE status = ?'
                    params1.append(status)

                # 获取 server_command_sent
                query2 = 'SELECT msg_id, msg_time, msg_type, robot_id, data_json, status, created_at FROM server_command_sent'
                params2 = []
                if status is not None:
                    query2 += ' WHERE status = ?'
                    params2.append(status)

                # 合并查询
                combined_query = f'''
                    SELECT * FROM (
                        {query1}
                        UNION ALL
                        {query2}
                    ) ORDER BY msg_time DESC LIMIT ?
                '''
                params = params1 + params2 + [limit]

                cursor.execute(combined_query, params)
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
