import threading
import queue
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Optional
import logging
from task.TaskDatabase import TaskDatabase
from task.TaskModels import OperationMode,StationTask,InspectionTask, TaskStatus

class TaskScheduler:
    """任务调度器"""
    
    def __init__(self, robot_controller, database: TaskDatabase):
        self.robot_controller = robot_controller
        self.database = database
        self.task_queue = queue.PriorityQueue()  # 优先级队列
        self.current_task: Optional[InspectionTask] = None
        self.is_running = False
        self.scheduler_thread: Optional[threading.Thread] = None
        self.executor = ThreadPoolExecutor(max_workers=1)  # 单任务执行
        self.logger = logging.getLogger(__name__)
        
        # 回调函数注册
        self.task_callbacks = {
            "on_task_start": [],
            "on_task_complete": [],
            "on_task_failed": [],
            "on_task_skipped": []
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
    
    def add_task(self, task: InspectionTask):
        """添加任务到队列"""
        # 优先级队列使用负数，因为队列是越小优先级越高
        self.task_queue.put((-task.priority,task.task_id, task))
        self.database.save_task(task)
        self.logger.info(f"任务 {task.task_id} 已添加到队列")
    
    def _scheduler_loop(self):
        """调度器主循环"""
        while self.is_running:
            try:
                if self.current_task is None:
                    # 获取下一个任务（非阻塞）
                    try:
                        _, _, task = self.task_queue.get_nowait()
                        self._execute_task(task)
                    except queue.Empty:
                        time.sleep(0.1)  # 队列为空时短暂休眠
                        continue
                else:
                    # 检查当前任务状态
                    if self.current_task.status in [TaskStatus.COMPLETED, 
                                                    TaskStatus.FAILED, 
                                                    TaskStatus.SKIPPED,
                                                    TaskStatus.RETRYING]:
                        self.current_task = None
                    else:
                        time.sleep(0.1)
                        
            except Exception as e:
                self.logger.error(f"调度器循环异常: {e}")
                time.sleep(1)
    
    def _execute_task(self, task: InspectionTask):
        """执行任务"""
        self.current_task = task
        self.database.update_task_status(task.task_id, TaskStatus.RUNNING)
        
        # 触发任务开始回调
        self._trigger_callback("on_task_start", task)
        
        # 提交到线程池执行
        future = self.executor.submit(self._execute_task_internal, task)
        future.add_done_callback(lambda f: self._task_execution_done(f, task))
    
    def _execute_task_internal(self, task: InspectionTask) -> bool:
        """执行任务内部逻辑"""
        try:
            self.logger.info(f"开始执行任务 {task.task_id}")
            
            for station_task in task.stations:
                if not self._execute_station_task(task, station_task):
                    # 如果某个站点失败，尝试继续执行其他站点
                    self.logger.warning(f"站点 {station_task.station_id} 执行失败，继续下一个")
                    # # TODO: 丰富日志信息，包括失败原因
                    # self.database.log_task_action(
                    #     task.task_id, station_task.station_id,
                    #     "station_execution", "failed", "站点执行失败"
                    # )
                    # 根据策略决定是否继续
                    continue_after_failure = True
                    # if not self._should_continue_after_failure(task, station_task):
                    return not continue_after_failure
            
            return True
            
        except Exception as e:
            self.logger.error(f"任务执行异常: {e}")
            return False
    
    def _execute_station_task(self, task: InspectionTask, 
                            station_task: StationTask) -> bool:
        """执行单个站点任务"""
        try:
            self.database.log_task_action(
                task.task_id, station_task.station_id,
                "start", "running", "开始执行站点任务"
            )
            
            # 1. 移动AGV到指定标记点
            self.logger.info(f"移动AGV到 {station_task.agv_marker}")
            success = self.robot_controller.agv_controller.move_to_marker(
                station_task.agv_marker
            )
            if not success:
                self.logger.error(f"AGV移动失败: {station_task.agv_marker}")
                return False
            
            # 2. 机械臂移动到归位位置
            self.logger.info("移动机械臂到归位位置")
            success = self.robot_controller.jaka_controller.move_to_position(
                station_task.robot_home_pos
            )
            if not success:
                self.logger.error("机械臂移动失败")
                return False
            
            # 3. 外部轴移动到归位位置
            self.logger.info("移动外部轴到归位位置")
            success = self.robot_controller.ext_controller.move_to_position(
                station_task.ext_home_pos
            )
            if not success:
                self.logger.error("外部轴移动失败")
                return False
            
            # 4. 执行操作模式
            if station_task.operation_mode != OperationMode.NONE:
                success = self._execute_operation(
                    station_task.operation_mode, 
                    station_task.door_id
                )
                if not success:
                    self.logger.error(f"操作失败: {station_task.operation_mode}")
                    return False
            
            self.database.log_task_action(
                task.task_id, station_task.station_id,
                "complete", "completed", "站点任务完成"
            )
            return True
            
        except Exception as e:
            self.logger.error(f"站点任务执行异常: {e}")
            self.database.log_task_action(
                task.task_id, station_task.station_id,
                "error", "failed", f"执行异常: {str(e)}"
            )
            return False
    
    def _execute_operation(self, operation_mode: OperationMode, 
                          door_id: Optional[str]) -> bool:
        """执行特定操作"""
        if operation_mode == OperationMode.OPEN_DOOR:
            # 执行开门操作
            return self._open_door(door_id)
        elif operation_mode == OperationMode.CLOSE_DOOR:
            # 执行关门操作
            return self._close_door(door_id)
        else:
            self.logger.warning(f"未知操作模式: {operation_mode}")
            return True
    
    def _open_door(self, door_id: str) -> bool:
        """开门操作实现"""
        # 这里需要调用具体的开门逻辑
        try:
            self.logger.info(f"执行开门操作: {door_id}")
            # TODO: 实现具体的开门逻辑
            time.sleep(0.5)  # 模拟操作时间
            return True
        except Exception as e:
            self.logger.error(f"开门操作失败: {e}")
            return False
    
    def _close_door(self, door_id: str) -> bool:
        """关门操作实现"""
        try:
            self.logger.info(f"执行关门操作: {door_id}")
            # TODO: 实现具体的关门逻辑
            time.sleep(0.5)  # 模拟操作时间
            return True
        except Exception as e:
            self.logger.error(f"关门操作失败: {e}")
            return False
    
    def _should_continue_after_failure(self, task: InspectionTask, 
                                      failed_station: StationTask) -> bool:
        """判断任务失败后是否继续"""
        # 这里可以根据业务逻辑实现不同的策略
        # 例如：充电桩任务失败后可以跳过继续执行
        if failed_station.name == "充电桩":
            return True
        # 默认策略：非关键任务失败后继续
        return True
    
    def _task_execution_done(self, future, task: InspectionTask):
        """任务执行完成回调"""
        try:
            success = future.result()

            if success:
                task.status = TaskStatus.COMPLETED
                self.database.update_task_status(task.task_id, TaskStatus.COMPLETED)
                self._trigger_callback("on_task_complete", task)
                self.logger.info(f"\n任务 {task.task_id} 执行完成\n")
            else:
                # 检查是否需要重试
                if task.retry_count < task.max_retries:
                    task.retry_count += 1
                    task.status = TaskStatus.RETRYING
                    self.database.add_retry_count(task.task_id)
                    self.database.update_task_status(task.task_id, TaskStatus.RETRYING)
                    
                    # 重新加入队列
                    self.task_queue.put((-task.priority, task.task_id, task))
                    self.database.log_task_action(
                        task.task_id, task.stations[0].station_id,
                        "station_execution", "retrying", "站点执行重试, 重试次数: " + str(task.retry_count)
                    )
                    self.logger.info(f"任务 {task.task_id} 将进行第{task.retry_count}次重试")
                else:
                    task.status = TaskStatus.FAILED
                    self.database.update_task_status(
                        task.task_id, TaskStatus.FAILED, 
                        "达到最大重试次数"
                    )
                    self.database.log_task_action(
                        task.task_id, task.stations[0].station_id,
                        "station_execution", "failed", "站点执行失败")
                    
                    self._trigger_callback("on_task_failed", task)
                    self.logger.error(f"任务 {task.task_id} 执行失败，已达到最大重试次数")
                    
        except Exception as e:
            self.logger.error(f"任务执行回调异常: {e}")
            task.status = TaskStatus.FAILED
            self.database.update_task_status(
                task.task_id, TaskStatus.FAILED, 
                f"回调异常: {str(e)}"
            )
    
    def register_callback(self, event: str, callback: Callable):
        """注册回调函数"""
        if event in self.task_callbacks:
            self.task_callbacks[event].append(callback)
    
    def _trigger_callback(self, event: str, task: InspectionTask):
        """触发回调函数"""
        for callback in self.task_callbacks.get(event, []):
            try:
                callback(task)
            except Exception as e:
                self.logger.error(f"回调函数执行异常: {e}")