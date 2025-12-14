from datetime import datetime
import json
from task.TaskModels import InspectionTask,TaskStatus
import sqlite3
from contextlib import contextmanager
from typing import Generator, List, Dict, Any
import logging

class TaskDatabase:
    """任务数据库管理"""
    
    def __init__(self, db_path: str = "tasks.db"):
        self.db_path = db_path
        self._init_database()
        self.logger = logging.getLogger(__name__)
    
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
    
    def save_task(self, task: InspectionTask):
        """保存任务到数据库"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                
                stations_json = json.dumps([station.to_dict() for station in task.stations])
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
            self.logger.error(f"保存任务失败: {e}")
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