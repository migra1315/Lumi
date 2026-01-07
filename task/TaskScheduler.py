import threading
import queue
import time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Optional, List, Dict, Any
import logging
from task.TaskDatabase import TaskDatabase
from dataModels.TaskModels import (
    Task, Station, StationConfig, OperationMode, 
    TaskStatus, StationTaskStatus, OperationConfig
)

class TaskScheduler:
    """任务调度器 - 负责任务的调度和执行"""
    
    def __init__(self, robot_controller, database: TaskDatabase):
        self.robot_controller = robot_controller
        self.database = database
        self.task_queue = queue.Queue()  # 先入先出队列，保证先下发先执行
        self.current_task: Optional[Task] = None
        self.current_station: Optional[Station] = None
        self.is_running = False
        self.scheduler_thread: Optional[threading.Thread] = None
        self.executor = ThreadPoolExecutor(max_workers=1)  # 单任务执行
        self.logger = logging.getLogger(__name__)
        
        # 回调函数注册
        self.task_callbacks = {
            "on_task_start": [],
            "on_task_complete": [],
            "on_task_failed": [],
            "on_station_start": [],
            "on_station_complete": [],
            "on_station_failed": [],
            "on_station_retry": []
        }
    
    def start(self):
        """启动调度器"""
        if not self.is_running:
            self.is_running = True
            self.scheduler_thread = threading.Thread(target=self._scheduler_loop)
            self.scheduler_thread.daemon = True
            self.scheduler_thread.start()
            self.logger.info("任务调度器已启动")
    
    def stop(self):
        """停止调度器"""
        self.is_running = False
        if self.scheduler_thread:
            self.scheduler_thread.join(timeout=5)
        self.executor.shutdown(wait=False)
        self.logger.info("任务调度器已停止")
    
    def add_task(self, task: Task):
        """添加任务到队列"""
        self.task_queue.put(task)
        # 保存任务到数据库
        self.database.save_task(task)
        self.logger.info(f"任务 {task.task_id} 已添加到队列")
    
    def _scheduler_loop(self):
        """调度器主循环"""
        while self.is_running:
            try:
                if self.current_task is None:
                    # 获取下一个任务（非阻塞）
                    try:
                        task = self.task_queue.get_nowait()
                        self._execute_task(task)
                    except queue.Empty:
                        time.sleep(0.1)  # 队列为空时短暂休眠
                        continue
                else:
                    # 检查当前任务状态
                    if self.current_task.status in [TaskStatus.COMPLETED, TaskStatus.FAILED]:
                        self.current_task = None
                        self.current_station = None
                    else:
                        time.sleep(0.1)
                        
            except Exception as e:
                self.logger.error(f"调度器循环异常: {e}")
                time.sleep(1)
    
    def _execute_task(self, task: Task):
        """执行任务"""
        self.current_task = task
        
        # 更新任务状态为运行中
        task.status = TaskStatus.RUNNING
        task.started_at = datetime.now()
        self.database.update_task_status(task.task_id, TaskStatus.RUNNING)
        
        # 触发任务开始回调
        self._trigger_callback("on_task_start", task)
        
        # 按照sort顺序排序站点
        sorted_stations = sorted(task.station_list, key=lambda s: s.station_config.sort)
        
        # 提交到线程池执行
        future = self.executor.submit(self._execute_task_internal, task, sorted_stations)
        future.add_done_callback(lambda f: self._task_execution_done(f, task))
    
    def _execute_task_internal(self, task: Task, sorted_stations: List[Station]) -> bool:
        """执行任务内部逻辑"""
        try:
            self.logger.info(f"开始执行任务 {task.task_id}")
            
            # 执行所有站点任务
            for station in sorted_stations:
                if not self._execute_station_task(station):
                    self.logger.info(f"站点 {station.station_config.station_id} 执行失败")
                    station.status = StationTaskStatus.TO_RETRY
                    while not self._retry_station_task(station):
                        self.logger.info(f"站点 {station.station_config.station_id} 重试失败")
                        time.sleep(1)
 
            return True
            
        except Exception as e:
            self.logger.error(f"任务执行异常: {e}")
            return False
    
    def _execute_station_task(self, station: Station) -> bool:
        """执行单个站点任务"""
        try:
            self.current_station = station
            
            # 更新站点状态为运行中
            station.status = StationTaskStatus.RUNNING
            station.started_at = datetime.now()
            station_id = station.station_config.station_id
            self.database.update_station_task_status(station_id, StationTaskStatus.RUNNING)
            
            # 记录执行日志
            self.database.log_task_action(
                self.current_task.task_id, 
                station_id, 
                "start", 
                "running", 
                "开始执行站点任务"
            )
            
            # 触发站点开始回调
            self._trigger_callback("on_station_start", station)
            
            # 1. 移动AGV到指定标记点
            self.logger.info(f"移动AGV到 {station.station_config.agv_marker}")
            success = self.robot_controller.move_to_marker(
                station.station_config.agv_marker
            )
            if not success:
                self.logger.error(f"AGV移动失败: {station.station_config.agv_marker}")
                return False
            
            # 2. 机械臂移动到归位位置
            self.logger.info("移动机械臂到归位位置")
            success = self.robot_controller.move_robot_to_position(
                station.station_config.robot_pos
            )
            if not success:
                self.logger.error("机械臂移动失败")
                return False
            
            # 3. 外部轴移动到归位位置
            self.logger.info("移动外部轴到归位位置")
            success = self.robot_controller.move_ext_to_position(
                station.station_config.ext_pos
            )
            if not success:
                self.logger.error("外部轴移动失败")
                return False
            
            # 4. 执行操作模式
            if station.station_config.operation_config.operation_mode != OperationMode.NONE:
                success = self._execute_operation(
                    station.station_config.operation_config
                )
                if not success:
                    self.logger.error(f"操作失败: {station.station_config.operation_config.operation_mode}")
                    return False
            
            # 更新站点状态为已完成
            station.status = StationTaskStatus.COMPLETED
            station.completed_at = datetime.now()
            self.database.update_station_task_status(station_id, StationTaskStatus.COMPLETED)
            
            # 记录执行日志
            self.database.log_task_action(
                self.current_task.task_id, 
                station_id, 
                "complete", 
                "completed", 
                "站点任务完成"
            )
            
            # 触发站点完成回调
            self._trigger_callback("on_station_complete", station)
            
            return True
            
        except Exception as e:
            self.logger.error(f"站点任务执行异常: {e}")
   
    def _retry_station_task(self, station: Station):

        station_id = station.station_config.station_id
                 
        # 检查是否需要重试
        if station.retry_count < station.max_retries:
            # 增加重试次数
            station.retry_count += 1
            station.status = StationTaskStatus.RETRYING
            self.database.add_station_retry_count(station_id)
            self.database.update_station_task_status(station_id, StationTaskStatus.RETRYING)
            
            # 记录重试日志
            self.database.log_task_action(
                self.current_task.task_id, 
                station_id, 
                "retry", 
                "retrying", 
                f"站点执行重试, 重试次数: {station.retry_count}"
            )
            
            # 触发站点重试回调
            self._trigger_callback("on_station_retry", station)
            
            # 重新执行该站点
            self.logger.info(f"站点 {station.station_config.station_id} 将进行第 {station.retry_count} 次重试")
            time.sleep(1)  # 重试间隔
            return self._execute_station_task(station)
        else:
            # 达到最大重试次数，标记为失败
            station.status = StationTaskStatus.FAILED
            station.completed_at = datetime.now()
            return True
            # station.error_message = str(e)
            # self.database.update_station_task_status(
            #     station_id, 
            #     StationTaskStatus.FAILED, 
            #     str(e)
            # )
            
            # # 记录失败日志
            # self.database.log_task_action(
            #     self.current_task.task_id, 
            #     station_id, 
            #     "error", 
            #     "failed", 
            #     f"执行异常: {str(e)}"
            # )
            
            # 触发站点失败回调
            # self._trigger_callback("on_station_failed", station)
            
            return False

    def _task_execution_done(self, future, task: Task):
        """任务执行完成回调"""
        try:
            success = future.result()

            if success:
                # 检查是否所有站点都已完成
                all_completed = all(s.status == StationTaskStatus.COMPLETED for s in task.station_list)
                if all_completed:
                    task.status = TaskStatus.COMPLETED
                    task.completed_at = datetime.now()
                    self.database.update_task_status(task.task_id, TaskStatus.COMPLETED)
                    self._trigger_callback("on_task_complete", task)
                    self.logger.info(f"任务 {task.task_id} 执行完成")
                else:
                    # 部分站点失败，但任务继续完成
                    task.status = TaskStatus.COMPLETED
                    task.completed_at = datetime.now()
                    self.database.update_task_status(task.task_id, TaskStatus.COMPLETED)
                    self._trigger_callback("on_task_complete", task)
                    self.logger.info(f"任务 {task.task_id} 执行完成，但部分站点执行失败")
            else:
                # 任务执行失败
                task.status = TaskStatus.FAILED
                task.completed_at = datetime.now()
                task.error_message = f"任务执行失败，当前站点: {self.current_station.station_config.station_id if self.current_station else '未知'}"
                self.database.update_task_status(
                    task.task_id, 
                    TaskStatus.FAILED, 
                    task.error_message
                )
                self._trigger_callback("on_task_failed", task)
                self.logger.error(f"任务 {task.task_id} 执行失败")
                
        except Exception as e:
            self.logger.error(f"任务执行回调异常: {e}")
            task.status = TaskStatus.FAILED
            task.completed_at = datetime.now()
            self.database.update_task_status(
                task.task_id, 
                TaskStatus.FAILED, 
                f"回调异常: {str(e)}"
            )
    
    def register_callback(self, event: str, callback: Callable):
        """注册回调函数"""
        if event in self.task_callbacks:
            self.task_callbacks[event].append(callback)
    
    def _trigger_callback(self, event: str, *args, **kwargs):
        """触发回调函数"""
        for callback in self.task_callbacks.get(event, []):
            try:
                callback(*args, **kwargs)
            except Exception as e:
                self.logger.error(f"回调函数执行异常: {e}")

    def _execute_operation(self, operation_config: OperationConfig) -> bool:
        """执行特定操作"""
        if operation_config.operation_mode == OperationMode.OPEN_DOOR:
            # 执行开门操作
            return self._open_door(operation_config.door_ip)
        elif operation_config.operation_mode == OperationMode.CLOSE_DOOR:
            # 执行关门操作
            return self._close_door(operation_config.door_ip)
        elif operation_config.operation_mode == OperationMode.CAPTURE:
            # 执行捕获操作
            return self._capture(operation_config.device_id)
        elif operation_config.operation_mode == OperationMode.SERVE:
            # 执行服务操作
            return self._serve(operation_config.device_id)
        else:
            self.logger.warning(f"未知操作模式: {operation_config.operation_mode}")
            return True
        
    def _capture(self, device_id: str) -> bool:
        """捕获操作实现"""
        try:
            self.logger.info(f"执行捕获操作: {device_id}")
            # 调用机器人控制器的捕获方法
            return self.robot_controller.capture(device_id)
        except Exception as e:
            self.logger.error(f"捕获操作失败: {e}")
            return False
        
    def _open_door(self, door_ip: str) -> bool:
        """开门操作实现"""
        try:
            self.logger.info(f"执行开门操作: {door_ip}")
            # 调用机器人控制器的开门方法
            return self.robot_controller.open_door(door_ip)
        except Exception as e:
            self.logger.error(f"开门操作失败: {e}")
            return False
    
    def _close_door(self, door_ip: str) -> bool:
        """关门操作实现"""
        try:
            self.logger.info(f"执行关门操作: {door_ip}")
            # 调用机器人控制器的关门方法
            return self.robot_controller.close_door(door_ip)
        except Exception as e:
            self.logger.error(f"关门操作失败: {e}")
            return False
    
    def _serve(self, device_id: str) -> bool:
        """服务操作实现"""
        try:
            self.logger.info(f"执行服务操作: {device_id}")
            # 调用机器人控制器的服务方法
            return self.robot_controller.serve(device_id)
        except Exception as e:
            self.logger.error(f"服务操作失败: {e}")
            return False
    