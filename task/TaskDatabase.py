from datetime import datetime
import json
from dataModels.TaskModels import Task,TaskStatus
import sqlite3
from contextlib import contextmanager
from typing import Generator, List, Dict, Any
import logging

class TaskDatabase:
    """任务数据库管理"""
    
    def __init__(self, db_path: str = "tasks.db"):
        self.db_path = db_path
        self.logger = logging.getLogger(__name__)
        self._init_database()
    
    def _init_database(self):
        """初始化数据库表"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            # 创建任务主表
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY,
                    stations_json TEXT NOT NULL,
                    priority INTEGER DEFAULT 1,
                    status TEXT DEFAULT 'pending',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    started_at TIMESTAMP,
                    completed_at TIMESTAMP,
                    retry_count INTEGER DEFAULT 0,
                    max_retries INTEGER DEFAULT 3,
                    error_message TEXT,
                    metadata_json TEXT DEFAULT '{}'
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
                
                stations_json = json.dumps(task.station.to_dict())
                metadata_json = json.dumps(task.metadata)
                
                cursor.execute('''
                    INSERT OR REPLACE INTO tasks 
                    (task_id, stations_json, priority, status, created_at, 
                     started_at, completed_at, retry_count, max_retries, 
                     error_message, metadata_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    task.task_id,
                    stations_json,
                    task.priority,
                    task.status.value,
                    task.created_at,
                    task.started_at,
                    task.completed_at,
                    task.retry_count,
                    task.max_retries,
                    task.error_message,
                    metadata_json
                ))
        except Exception as e:
            self.logger.error(f"保存任务失败: {e},\n {task}")
            raise
    
    def update_task_status(self, task_id: str, status: TaskStatus, 
                          error_message: str = None):
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
    
    def add_retry_count(self, task_id: str):
        """增加重试次数"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE tasks 
                SET retry_count = retry_count + 1 
                WHERE task_id = ?
            ''', (task_id,))
    
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
                WHERE status IN ('pending', 'retrying')
                ORDER BY priority DESC, created_at ASC
            ''')
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