from datetime import datetime
import json
import uuid
import hashlib
from typing import Dict, List
from task.TaskDatabase import TaskDatabase
from task.TaskScheduler import TaskScheduler
from dataModels.TaskModels import StationConfig, Task, OperationMode, OperationConfig
import logging
class TaskManager:
    """任务管理器 - 主控制器"""
    
    def __init__(self, robot_controller):
        self.robot_controller = robot_controller
        self.database = TaskDatabase()
        self.scheduler = TaskScheduler(robot_controller, self.database)
        self.logger = logging.getLogger(__name__)
        
        # 启动调度器
        self.scheduler.start()
        
        # 注册回调
        self.scheduler.register_callback("on_task_start", self._on_task_start)
        self.scheduler.register_callback("on_task_complete", self._on_task_complete)
        self.scheduler.register_callback("on_task_failed", self._on_task_failed)
    
    def receive_task_from_json(self, json_data: Dict) -> str:
        """从JSON接收单个任务"""
        try:
            # 解析JSON数据
            stations_data = json_data.get("stations", {})
            
            # 为每个站点创建一个单独的InspectionTask对象
            task_ids = []
            for station_id, station_info in stations_data.items():
                # 创建操作配置对象
                operation_config_data = station_info.get("operation_config", {})
                operation_config = OperationConfig(
                    operation_mode=OperationMode(operation_config_data.get("operation_mode", "None")),
                    door_ip=operation_config_data.get("door_ip"),
                    device_id=operation_config_data.get("device_id")
                )
                
                station = StationConfig(
                    station_id=station_id,
                    name=station_info.get("name", ""),
                    agv_marker=station_info.get("agv_marker", ""),
                    robot_pos=station_info.get("robot_pos", []),
                    ext_pos=station_info.get("ext_pos", []),
                    operation_config=operation_config
                )
                
                # 生成任务ID
                task_id = self._generate_task_id({**json_data, "station_id": station_id})
                
                # 创建巡检任务
                task = Task(
                    task_id=task_id,
                    station=station,
                    priority=self._calculate_priority([station]),
                    metadata={"source": "json", "station_id": station_id}
                )
                
                # 添加到调度器
                self.scheduler.add_task(task)
                task_ids.append(task_id)
            
            # 返回第一个任务ID或空字符串
            return task_ids[0] if task_ids else ""
            
        except Exception as e:
            self.logger.error(f"接收任务失败: {e}")
            raise
    
    def _generate_task_id(self, json_data: Dict) -> str:
        """生成唯一任务ID"""
        # 使用JSON内容的哈希值作为任务ID的一部分
        json_str = json.dumps(json_data, sort_keys=True)
        hash_str = hashlib.md5(json_str.encode()).hexdigest()[:8]
        
        # 加上时间戳和随机数
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        random_str = str(uuid.uuid4())[:4]
        
        return f"TASK_{timestamp}_{hash_str}_{random_str}"
    
    def _calculate_priority(self, stations: List[StationConfig]) -> int:
        """计算任务优先级"""
        # TODO: 实现根据任务内容计算优先级的逻辑
        # 根据任务内容计算优先级
        # 例如：有充电任务优先级较高
        for station in stations:
            if "充电" in station.name:
                return 10  # 最高优先级
        return 1
    
    def get_task_status(self, task_id: str) -> Dict:
        """获取任务状态"""
        try:
            with self.database._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,))
                row = cursor.fetchone()
                
                if row:
                    return dict(row)
                else:
                    return {"error": "任务不存在"}
        except Exception as e:
            self.logger.error(f"获取任务状态失败: {e}")
            return {"error": str(e)}
    
    def get_all_tasks(self, status: str = None) -> List[Dict]:
        """获取所有任务"""
        try:
            with self.database._get_connection() as conn:
                cursor = conn.cursor()
                
                if status:
                    cursor.execute(
                        "SELECT * FROM tasks WHERE status = ? ORDER BY created_at DESC",
                        (status,)
                    )
                else:
                    cursor.execute("SELECT * FROM tasks ORDER BY created_at DESC")
                
                return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            self.logger.error(f"获取任务列表失败: {e}")
            return []
    
    def cancel_task(self, task_id: str) -> bool:
        """取消任务"""
        # TODO: 实现任务取消逻辑
        self.logger.info(f"取消任务: {task_id}")
        return True
    
    def retry_task(self, task_id: str) -> bool:
        """重试任务"""
        # TODO: 实现任务重试逻辑
        self.logger.info(f"重试任务: {task_id}")
        return True
    
    def _on_task_start(self, task: Task):
        """任务开始回调"""
        self.logger.info(f"任务开始: {task.task_id}")
        # 可以在这里发送通知或更新UI
    
    def _on_task_complete(self, task: Task):
        """任务完成回调"""
        self.logger.info(f"任务完成: {task.task_id}")
        # 可以在这里发送通知或更新UI
    
    def _on_task_failed(self, task: Task):
        """任务失败回调"""
        self.logger.error(f"任务失败: {task.task_id}")
        # 可以在这里发送通知或更新UI
    
    def shutdown(self):
        """关闭管理器"""
        self.scheduler.stop()
        self.logger.info("任务管理器已关闭")