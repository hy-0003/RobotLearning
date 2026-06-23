# 仿真器模块
# 包含抽象基类 Simulator 和 Mock 仿真实现
# 负责模拟多机械臂并行执行操作，并维护仿真状态和反馈

from abc import ABC, abstractmethod
from typing import List, Dict, Optional, Tuple, Any
import random
import logging
from dataclasses import field
from copy import deepcopy

from data_types import (
    Scene, Schedule, ScheduleEntry, ExecutionResult, SimulationFeedback,
    WorldState, RobotState, WorkpieceState, Operation, Workpiece,
    SimulationConfig
)
from code_executor import CodeExecutor, RobotExecutionTrace, FlexState

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class Simulator(ABC):
    """仿真器抽象基类"""

    @abstractmethod
    def execute_schedule(self, schedule: Schedule) -> SimulationFeedback:
        """执行调度计划并返回反馈"""
        pass

    @abstractmethod
    def get_world_state(self) -> WorldState:
        """获取当前世界状态"""
        pass

    @abstractmethod
    def reset(self):
        """重置仿真状态"""
        pass


class MockSimulator(Simulator):
    """Mock 仿真器 - 不依赖 Isaac Sim，用于快速验证"""

    def __init__(self, scene: Scene):
        """
        初始化 Mock Simulator

        参数：
            scene: 产线场景配置
        """
        self.scene = scene
        self.world_state: Optional[WorldState] = None
        self.execution_log: List[ExecutionResult] = []
        self._init_world_state()

    def _init_world_state(self):
        """初始化世界状态"""
        # 初始化机械臂状态
        robot_states = {}
        for robot in self.scene.resources.robots:
            robot_states[robot.id] = RobotState(
                robot_id=robot.id,
                current_location=robot.initial_location,
                is_idle=True,
                holding_workpiece=None
            )

        # 初始化工件状态
        workpiece_states = {}
        for workpiece in self.scene.workpieces:
            workpiece_states[workpiece.id] = WorkpieceState(
                workpiece_id=workpiece.id,
                current_location=workpiece.initial_location,
                current_state_index=0,
                completed_operations=[]
            )

        # 初始化工具可用数量
        available_tools = {}
        for tool in self.scene.resources.tools:
            available_tools[tool.id] = tool.quantity

        # 初始化工位占用情况
        occupied_stations = {}
        for station in self.scene.resources.stations:
            occupied_stations[station.id] = None

        self.world_state = WorldState(
            current_time=0.0,
            robot_states=robot_states,
            workpiece_states=workpiece_states,
            available_tools=available_tools,
            occupied_stations=occupied_stations
        )
        self.execution_log = []

    def execute_schedule(self, schedule: Schedule) -> SimulationFeedback:
        """
        执行调度计划

        工作流程：
        1. 识别协作任务（同一操作多条机器人记录）
        2. 对协作任务批量检查资源、同步执行
        3. 对单机器人任务按序执行
        4. 注入随机噪声和失败
        5. 收集执行结果和反馈

        参数：
            schedule: 调度计划（包含操作、时间、资源分配）

        返回：
            SimulationFeedback 包含执行结果、完成时间、失败信息
        """
        self._init_world_state()
        results = []

        # 按 (operation_id, start_time) 分组，识别协作任务
        from collections import defaultdict
        groups: Dict[Tuple[str, float], List[ScheduleEntry]] = defaultdict(list)
        for entry in schedule.entries:
            groups[(entry.operation_id, entry.start_time)].append(entry)

        # 保持原始顺序处理每组
        processed_groups = set()
        for entry in schedule.entries:
            key = (entry.operation_id, entry.start_time)
            if key in processed_groups:
                continue
            processed_groups.add(key)

            group = groups[key]
            if len(group) == 1:
                # 单机器人操作
                result = self._execute_single_operation(group[0])
                results.append(result)
                self._update_world_state(group[0], result)
            else:
                # 协作任务：检查所有机器人，统一执行
                collab_results = self._execute_collaborative_operation(group)
                for r, e in zip(collab_results, group):
                    results.append(r)
                    self._update_world_state(e, r)

        # 计算总完成时间（最后结束的操作的结束时间）
        total_makespan = max([r.end_time for r in results]) if results else 0.0

        # 统计成功/失败（按唯一操作ID，协作任务算一次）
        op_status: Dict[str, str] = {}
        for r in results:
            if r.operation_id not in op_status or r.status != 'success':
                op_status[r.operation_id] = r.status
        success_count = sum(1 for s in op_status.values() if s == 'success')
        failure_count = len(op_status) - success_count

        # 生成失败信息汇总
        failure_details = self._generate_failure_details(results)

        feedback = SimulationFeedback(
            results=results,
            total_makespan=total_makespan,
            success_count=success_count,
            failure_count=failure_count,
            failure_details=failure_details
        )

        self.execution_log = results
        return feedback

    def _execute_collaborative_operation(self, entries: List[ScheduleEntry]) -> List[ExecutionResult]:
        """
        执行协作任务：多台机器人同步执行同一操作。

        所有参与者共享相同的执行结果：要么全部成功，要么全部失败。
        协作任务使用第一条 entry 的随机结果，确保一致性。
        """
        if not entries:
            return []

        first = entries[0]
        operation_id = first.operation_id
        operation = self._get_operation_by_id(operation_id)
        start_time = first.start_time

        # 检查所有参与机器人的资源
        for entry in entries:
            conflict = self._check_resource_conflict(entry)
            if conflict:
                rand = random.random()
                if rand < self.scene.simulation.resource_conflict_probability:
                    # 所有机器人一起标记为冲突
                    return [
                        ExecutionResult(
                            operation_id=operation_id,
                            robot_id=e.robot_id,
                            workpiece_id=e.workpiece_id,
                            start_time=start_time,
                            end_time=start_time,
                            status='resource_conflict',
                            reason=f'协作任务资源冲突：{", ".join(e.resources)} 已被占用'
                        ) for e in entries
                    ]

        # 共享的随机结果：用第一次掷骰决定所有机器人
        base_duration = operation.duration
        noise = random.uniform(-1, 1) * base_duration * self.scene.simulation.duration_noise_ratio
        actual_duration = max(0.1, base_duration + noise)
        end_time = start_time + actual_duration
        timeout_limit = self.scene.operation_type_defaults[operation.type].timeout_limit

        rand = random.random()

        # 随机超时
        if rand < self.scene.simulation.timeout_probability:
            return [
                ExecutionResult(operation_id=operation_id, robot_id=e.robot_id, workpiece_id=e.workpiece_id,
                    start_time=start_time, end_time=end_time, status='timeout',
                    reason=f'协作任务仿真随机超时：{actual_duration:.2f}s > {timeout_limit:.2f}s')
                for e in entries
            ]

        # 实际超时
        if actual_duration > timeout_limit:
            return [
                ExecutionResult(operation_id=operation_id, robot_id=e.robot_id, workpiece_id=e.workpiece_id,
                    start_time=start_time, end_time=end_time, status='timeout',
                    reason=f'协作任务超时：{actual_duration:.2f}s > {timeout_limit:.2f}s')
                for e in entries
            ]

        # 随机失败
        rand = random.random()
        if rand >= self.scene.simulation.success_probability:
            return [
                ExecutionResult(operation_id=operation_id, robot_id=e.robot_id, workpiece_id=e.workpiece_id,
                    start_time=start_time, end_time=end_time, status='error',
                    reason='协作任务执行出错（随机失败）')
                for e in entries
            ]

        # 全部成功
        return [
            ExecutionResult(
                operation_id=operation_id,
                robot_id=e.robot_id,
                workpiece_id=e.workpiece_id,
                start_time=start_time,
                end_time=end_time,
                status='success',
                reason=None
            ) for e in entries
        ]

    def _execute_single_operation(self, schedule_entry: ScheduleEntry) -> ExecutionResult:
        """
        模拟执行单个操作

        步骤：
        1. 检查资源是否可用（工具、工位）
        2. 生成实际执行时长（添加随机噪声）
        3. 注入随机失败（超时、冲突）
        4. 返回执行结果

        参数：
            schedule_entry: 调度表项（包含操作、时间、资源）

        返回：
            ExecutionResult 包含操作ID、状态、实际时长等
        """
        operation_id = schedule_entry.operation_id
        operation = self._get_operation_by_id(operation_id)

        start_time = schedule_entry.start_time

        # 检查资源冲突
        has_conflict = self._check_resource_conflict(schedule_entry)
        if has_conflict:
            rand = random.random()
            if rand < self.scene.simulation.resource_conflict_probability:
                return ExecutionResult(
                    operation_id=operation_id,
                    robot_id=schedule_entry.robot_id,
                    workpiece_id=schedule_entry.workpiece_id,
                    start_time=start_time,
                    end_time=start_time,  # 未成功执行
                    status='resource_conflict',
                    reason=f'工位/工具冲突：{", ".join(schedule_entry.resources)} 已被占用'
                )

        # 生成实际执行时长（带噪声）
        base_duration = operation.duration
        noise = random.uniform(-1, 1) * base_duration * self.scene.simulation.duration_noise_ratio
        actual_duration = base_duration + noise
        actual_duration = max(0.1, actual_duration)  # 确保时长为正

        end_time = start_time + actual_duration

        # 获取操作的超时限制
        timeout_limit = self.scene.operation_type_defaults[operation.type].timeout_limit

        # 注入随机失败
        rand = random.random()

        # 检查随机超时（仿真噪声，非实际超时）
        if rand < self.scene.simulation.timeout_probability:
            return ExecutionResult(
                operation_id=operation_id,
                robot_id=schedule_entry.robot_id,
                workpiece_id=schedule_entry.workpiece_id,
                start_time=start_time,
                end_time=end_time,
                status='timeout',
                reason=f'仿真随机超时：实际耗时 {actual_duration:.2f}s（限制 {timeout_limit:.2f}s），命中 timeout_probability={self.scene.simulation.timeout_probability}'
            )

        # 检查实际超时
        if actual_duration > timeout_limit:
            return ExecutionResult(
                operation_id=operation_id,
                robot_id=schedule_entry.robot_id,
                workpiece_id=schedule_entry.workpiece_id,
                start_time=start_time,
                end_time=end_time,
                status='timeout',
                reason=f'操作超时：实际耗时 {actual_duration:.2f}s > 限制 {timeout_limit:.2f}s'
            )

        # 检查成功
        rand = random.random()
        if rand >= self.scene.simulation.success_probability:
            return ExecutionResult(
                operation_id=operation_id,
                robot_id=schedule_entry.robot_id,
                workpiece_id=schedule_entry.workpiece_id,
                start_time=start_time,
                end_time=end_time,
                status='error',
                reason=f'操作执行出错（随机失败）'
            )

        # 操作成功
        return ExecutionResult(
            operation_id=operation_id,
            robot_id=schedule_entry.robot_id,
            workpiece_id=schedule_entry.workpiece_id,
            start_time=start_time,
            end_time=end_time,
            status='success',
            reason=None
        )

    def _check_resource_conflict(self, schedule_entry: ScheduleEntry) -> bool:
        """
        检查是否存在资源冲突

        检查规则：
        - 工具是否有足够的可用数量
        - 工位是否空闲

        参数：
            schedule_entry: 调度表项

        返回：
            True 表示有冲突，False 表示无冲突
        """
        # 检查工具可用性
        for resource_id in schedule_entry.resources:
            if resource_id in self.world_state.available_tools:
                if self.world_state.available_tools[resource_id] <= 0:
                    return True

        # 检查工位可用性
        for resource_id in schedule_entry.resources:
            if resource_id in self.world_state.occupied_stations:
                if self.world_state.occupied_stations[resource_id] is not None:
                    return True

        return False

    def _update_world_state(self, schedule_entry: ScheduleEntry, result: ExecutionResult):
        """
        根据执行结果更新世界状态

        参数：
            schedule_entry: 调度表项
            result: 执行结果
        """
        if result.status != 'success':
            return  # 失败的操作不更新状态

        operation = self._get_operation_by_id(schedule_entry.operation_id)
        workpiece_id = schedule_entry.workpiece_id
        robot_id = schedule_entry.robot_id

        # 更新当前时间
        self.world_state.current_time = result.end_time

        # 更新机械臂状态
        robot_state = self.world_state.robot_states[robot_id]
        robot_state.is_idle = True

        # 根据操作类型更新工件和机械臂位置
        if operation.type == 'transport':
            # 更新工件位置
            self.world_state.workpiece_states[workpiece_id].current_location = operation.to_location
            # 更新机械臂位置
            robot_state.current_location = operation.to_location
            robot_state.holding_workpiece = None
        else:
            # 加工操作（weld, polish, inspect）
            # 工件位置不变，但状态索引可能推进
            workpiece_state = self.world_state.workpiece_states[workpiece_id]
            workpiece_state.completed_operations.append(schedule_entry.operation_id)

        # 释放资源
        operation = self._get_operation_by_id(schedule_entry.operation_id)
        for resource_id in schedule_entry.resources:
            if resource_id in self.world_state.available_tools:
                self.world_state.available_tools[resource_id] += 1

            if resource_id in self.world_state.occupied_stations:
                self.world_state.occupied_stations[resource_id] = None

    def _get_operation_by_id(self, operation_id: str) -> Operation:
        """根据 ID 获取操作对象"""
        for op in self.scene.operations:
            if op.id == operation_id:
                return op
        raise ValueError(f'未找到操作 {operation_id}')

    def _generate_failure_details(self, results: List[ExecutionResult]) -> Dict:
        """
        生成失败信息汇总

        参数：
            results: 所有操作的执行结果

        返回：
            包含失败信息的字典
        """
        failures_by_type = {}
        for result in results:
            if result.status != 'success':
                status = result.status
                if status not in failures_by_type:
                    failures_by_type[status] = []
                failures_by_type[status].append({
                    'operation_id': result.operation_id,
                    'reason': result.reason
                })

        return failures_by_type

    def get_world_state(self) -> WorldState:
        """获取当前世界状态"""
        return deepcopy(self.world_state)

    def reset(self):
        """重置仿真状态，准备下一次执行"""
        self._init_world_state()
        self.execution_log = []

    def save_execution_log(self, filepath: str):
        """
        将执行日志保存到 JSON 文件

        参数：
            filepath: 输出文件路径
        """
        import json

        log_data = {
            'execution_results': [
                {
                    'operation_id': r.operation_id,
                    'robot_id': r.robot_id,
                    'workpiece_id': r.workpiece_id,
                    'start_time': r.start_time,
                    'end_time': r.end_time,
                    'duration': r.end_time - r.start_time,
                    'status': r.status,
                    'reason': r.reason,
                    'timestamp': r.timestamp
                }
                for r in self.execution_log
            ]
        }

        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(log_data, f, indent=2, ensure_ascii=False)

        logger.info(f'执行日志已保存到 {filepath}')

    def execute_with_code(self, schedule: Schedule,
                          generated_codes: Dict[str, Any]) -> SimulationFeedback:
        """
        使用 LLM 生成的行为代码执行调度 —— 真正的代码执行路径。

        与 execute_schedule() 的区别：
        - execute_schedule() 直接读 ScheduleEntry 元数据模拟
        - execute_with_code() 在沙箱中 exec() LLM 生成的代码，拦截原子原语调用

        工作流程：
        1. 构建 CodeExecutor（传递操作时长/超时限制/噪声参数）
        2. 为每台机械臂的代码创建沙箱并执行
        3. 收集所有 ExecutionTrace → 转换为 SimulationFeedback
        4. 代码执行失败 → 标记 status='error'，触发 Harness 重规划（不再回退）

        参数：
            schedule: 调度计划
            generated_codes: 机械臂 ID → RobotBehaviorCode 的映射

        返回：
            SimulationFeedback（包含从代码执行轨迹转换的结果）
        """
        import random

        self._init_world_state()

        # 构建操作时长查找表
        op_durations: Dict[str, float] = {}
        timeout_limits: Dict[str, float] = {}
        for op in self.scene.operations:
            op_durations[op.id] = op.duration
            op_durations[op.workpiece_id] = op.duration  # 也按工件ID索引，供 operate() 查找
        for op_type, defaults in self.scene.operation_type_defaults.items():
            timeout_limits[op_type] = defaults.timeout_limit

        # 创建执行器
        executor = CodeExecutor(
            operation_durations=op_durations,
            timeout_prob=self.scene.simulation.timeout_probability,
            success_prob=self.scene.simulation.success_probability,
            noise_ratio=self.scene.simulation.duration_noise_ratio,
            timeout_limits=timeout_limits
        )

        # ── 并行时间步进：共享世界状态，每台机械臂独立时钟从 0 开始 ──
        shared_world = FlexState(workpieces=FlexState(), stations=FlexState())

        # 识别协作任务涉及的工件（同一 operation_id 出现在多个机器人的调度中）
        collaborative_workpieces: set = set()
        op_robot_count: Dict[str, int] = {}
        for entry in schedule.entries:
            op_robot_count[entry.operation_id] = op_robot_count.get(entry.operation_id, 0) + 1
        for entry in schedule.entries:
            if op_robot_count.get(entry.operation_id, 0) > 1:
                collaborative_workpieces.add(entry.workpiece_id)

        # 执行每台机械臂的代码
        all_traces: Dict[str, RobotExecutionTrace] = {}
        all_results: List[ExecutionResult] = []
        code_errors: Dict[str, str] = {}   # robot_id → 错误信息

        for robot_id, behavior_code in generated_codes.items():
            code_str = behavior_code.code if hasattr(behavior_code, 'code') else str(behavior_code)
            logger.info(f"  执行 [{robot_id}] 的行为代码 ({len(code_str)} 字符)...")

            trace = executor.execute(
                robot_id, code_str,
                shared_world_state=shared_world,
                collaborative_workpieces=collaborative_workpieces
            )
            all_traces[robot_id] = trace

            if trace.error:
                # 代码执行失败 → 不 fallback，让 Harness 检测失败并重规划
                logger.warning(f"  [{robot_id}] 代码执行失败: {trace.error}，等待 Harness 重规划")
                code_errors[robot_id] = trace.error
                # 为该机器人的调度条目生成失败结果
                for entry in schedule.entries:
                    if entry.robot_id == robot_id:
                        failed_result = ExecutionResult(
                            operation_id=entry.operation_id,
                            robot_id=robot_id,
                            workpiece_id=entry.workpiece_id,
                            start_time=entry.start_time,
                            end_time=entry.end_time if entry.end_time else entry.start_time,
                            status='error',
                            reason=f'代码执行错误 [{robot_id}]: {trace.error}'
                        )
                        all_results.append(failed_result)
                continue

            # 将原语调用转为 ExecutionResult
            for call in trace.primitives_called:
                result = ExecutionResult(
                    operation_id=f"{robot_id}_primitive_{call.sequence}",
                    robot_id=robot_id,
                    workpiece_id=call.args.get('workpiece_id', call.args.get('location', 'unknown')),
                    start_time=call.start_time,
                    end_time=call.end_time,
                    status=call.status,
                    reason=call.error_msg if call.status != 'success' else None
                )
                all_results.append(result)

        # 如果没有任何代码生成，回退
        if not all_traces:
            logger.warning("没有生成代码，回退到 schedule 路径")
            return self.execute_schedule(schedule)

        # 计算 makespan：只统计有实际工作的机械臂
        active_times = [t.total_time for t in all_traces.values() if t.total_time > 0 and len(t.primitives_called) > 0]
        total_makespan = max(active_times) if active_times else 0.0

        # 统计成功/失败
        op_status: Dict[str, str] = {}
        for r in all_results:
            if r.operation_id not in op_status or r.status != 'success':
                op_status[r.operation_id] = r.status
        success_count = sum(1 for s in op_status.values() if s == 'success')
        failure_count = len(op_status) - success_count

        # 汇总失败
        failure_details = self._generate_failure_details(all_results)

        # 附加代码执行轨迹
        failure_details['execution_traces'] = {
            rid: t.to_dict() for rid, t in all_traces.items()
        }
        if code_errors:
            failure_details['code_execution_error'] = [
                {'operation_id': rid, 'reason': err} for rid, err in code_errors.items()
            ]

        feedback = SimulationFeedback(
            results=all_results,
            total_makespan=total_makespan,
            success_count=success_count,
            failure_count=failure_count,
            failure_details=failure_details
        )

        self.execution_log = all_results
        return feedback
