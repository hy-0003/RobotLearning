# MRTA-Benchmark 数据转换器
# 将 MRTA-Benchmark 格式的问题实例和最优解转换为项目内部的 Scene 和 GroundTruth 格式
#
# MRTA-Benchmark 格式说明（参考 arXiv:2603.02669 IMR-Bench 数据集）：
#   - Q: 任务资格矩阵（task × robot），Q[t][r]=1 表示机器人 r 可以执行任务 t
#   - R: 机器人起始位置坐标
#   - T_e: 每个任务的执行时长数组
#   - T_t: 位置间运输时间矩阵（对称矩阵）
#
# 转换策略：
#   1. 每个真实任务 → 一个 Operation
#   2. 每个机器人 → 一个 Robot
#   3. T_e 值 → 操作的基础时长
#   4. T_t 值 → 位置间运输操作时长
#   5. Q 矩阵 → 操作的 allowed_robots
#   6. optimal_schedule → GroundTruth

import json
import os
import logging
from typing import Dict, Any, List, Optional, Tuple

from data_types import (
    Scene, GroundTruth, Operation, Workpiece, Robot, Station, Tool, Location,
    CellLayout, TransportEdge, Resources, ScheduleEntry, ExecutionMetrics,
    ResourceUtilization, OperationTypeDefaults, SimulationConfig
)

logger = logging.getLogger(__name__)


class MRTABenchmarkConverter:
    """
    MRTA-Benchmark 数据转换器

    用法：
        converter = MRTABenchmarkConverter()
        scene = converter.problem_to_scene(problem_json_path)
        ground_truth = converter.solution_to_ground_truth(scene, solution_json_path)
    """

    def __init__(self, random_seed: int = 42):
        self.random_seed = random_seed

    # ------------------------------------------------------------------
    # 主转换入口
    # ------------------------------------------------------------------

    def problem_to_scene(self, problem_path: str, solution_path: Optional[str] = None) -> Scene:
        """
        将 MRTA-Benchmark 问题实例 JSON 转换为 Scene

        参数：
            problem_path: problem_instance_*.json 的文件路径
            solution_path: optimal_schedule_*.json 的文件路径（可选，用于推断协作任务）

        返回：
            Scene 对象
        """
        with open(problem_path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        # 解析原始数据
        Q = data['Q']               # 资格矩阵（task_types × robots 或其他布局）
        R_positions = data['R']     # 所有位置的坐标（10 个位置，含 depot）
        T_e = data['T_e']           # 任务执行时长
        T_t = data['T_t']           # 位置间运输时间矩阵

        n_locations = len(T_t)          # 总位置数（含 start/end depot）
        n_tasks_all = len(T_e)          # 总任务数（含 dummy start/end）

        # 从 Q 矩阵推断机器人数量（Q 的列数 = 机器人数）
        # Q 可能是 task_types × robots 或 tasks × robots
        n_robots = len(Q[0]) if Q else 0

        # 识别 dummy 任务（开始/结束任务，T_e=0）
        real_task_indices = [i for i in range(n_tasks_all) if T_e[i] > 0]
        dummy_start_idx = 0
        dummy_end_idx = n_tasks_all - 1

        logger.info(f"MRTA-Benchmark 数据解析：")
        logger.info(f"  位置数: {n_locations}（含起始/结束 depot）")
        logger.info(f"  总任务数: {n_tasks_all}（含 dummy），真实任务: {len(real_task_indices)}")
        logger.info(f"  机器人数量: {n_robots}（从 Q 矩阵列数推断）")
        logger.info(f"  Q 矩阵尺寸: {len(Q)}×{len(Q[0]) if Q else 0}")

        # 构建位置列表
        locations = self._build_locations(n_locations, dummy_start_idx, dummy_end_idx)

        # 构建运输边
        transport_edges = self._build_transport_edges(T_t, locations)

        # 构建机械臂
        robots, robot_id_list = self._build_robots(n_robots, locations)

        # 构建工具和工位
        tools = self._build_tools()
        stations = self._build_stations(n_locations, dummy_start_idx, dummy_end_idx)

        # 提取前置约束
        precedence_constraints = data.get('precedence_constraints', [])

        # 构建操作（结合 Q 矩阵和最优解推断 allowed_robots）
        operations = self._build_operations(
            real_task_indices, T_e, Q, n_robots, robot_id_list,
            solution_path,  # 传入最优解路径以推断协作任务
            precedence_constraints  # 传入前置约束
        )

        # 构建工件（每个任务对应一个"工件"的概念，或是多个工件共享任务）
        workpieces = self._build_workpieces(real_task_indices, T_e, operations)

        # 场景元信息
        instance_name = os.path.splitext(os.path.basename(problem_path))[0]

        scene = Scene(
            scene_id=instance_name,
            version='1.0',
            source=f"数据来源于 MRTA-Benchmark 数据集（arXiv:2603.02669, IMR-Bench），"
                   f"文件: {os.path.basename(problem_path)}。"
                   f"任务数={len(real_task_indices)}, 机器人={n_robots}, "
                   f"包含单机器人和协作任务。",
            description=f"MRTA-Benchmark 问题实例: {len(real_task_indices)} 个任务, "
                        f"{n_robots} 台机器人。位置数: {n_locations}。"
                        f"任务执行时长范围: {min(T_e[i] for i in real_task_indices)}-{max(T_e[i] for i in real_task_indices)}。",
            time_unit='second',
            cell_layout=CellLayout(
                locations=locations,
                transport_edges=transport_edges
            ),
            resources=Resources(
                robots=robots,
                tools=tools,
                stations=stations
            ),
            operation_type_defaults={
                'task_execution': OperationTypeDefaults(
                    base_duration=80.0,
                    timeout_limit=200.0
                ),
                'transport': OperationTypeDefaults(
                    base_duration=30.0,
                    timeout_limit=120.0
                )
            },
            workpieces=workpieces,
            operations=operations,
            simulation=SimulationConfig(
                duration_noise_ratio=0.05,
                success_probability=float(os.getenv('MOCK_SUCCESS_PROBABILITY', '0.97')),
                timeout_probability=float(os.getenv('MOCK_TIMEOUT_PROBABILITY', '0.02')),
                resource_conflict_probability=float(os.getenv('MOCK_CONFLICT_PROBABILITY', '1.0')),
                station_release_rule='操作完成后立即释放工位和工具'
            )
        )

        return scene

    def solution_to_ground_truth(self, scene: Scene, solution_path: str) -> GroundTruth:
        """
        将 MRTA-Benchmark 最优解 JSON 转换为 GroundTruth

        参数：
            scene: 已转换的 Scene 对象
            solution_path: optimal_schedule_*.json 的文件路径

        返回：
            GroundTruth 对象
        """
        with open(solution_path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        optimal_makespan = data['makespan']
        robot_schedules = data['robot_schedules']

        # 构建 ScheduleEntry 列表
        schedule_entries = []
        operation_order = []

        # 收集所有 (start_time, task, robot) 并排序
        all_events: List[Tuple[float, int, str]] = []
        for robot_id, task_list in robot_schedules.items():
            for task_info in task_list:
                task_idx = task_info['task']
                all_events.append((task_info['start_time'], task_idx, robot_id))

        # 按开始时间排序
        all_events.sort()

        for robot_id, task_list in robot_schedules.items():
            for task_info in task_list:
                task_idx = task_info['task']
                start_time = task_info['start_time']
                end_time = task_info['end_time']

                # 查找该操作对应的工件
                op = self._find_operation_by_task(scene, task_idx)
                if op is None:
                    continue

                # 确定操作类型
                workpiece_id = op.workpiece_id

                entry = ScheduleEntry(
                    operation_id=op.id,
                    workpiece_id=workpiece_id,
                    robot_id=f'arm_{robot_id}',
                    start_time=round(start_time, 4),
                    end_time=round(end_time, 4),
                    resources=op.required_tools.copy()
                )
                schedule_entries.append(entry)
                operation_order.append(op.id)

        # 按开始时间排序
        schedule_entries.sort(key=lambda e: e.start_time)

        # 为每个机器人去重（同一个操作可能被多个机器人协作执行，保留所有）
        seen = set()
        deduped_entries = []
        for e in schedule_entries:
            key = (e.operation_id, e.robot_id)
            if key not in seen:
                seen.add(key)
                deduped_entries.append(e)
        schedule_entries = deduped_entries

        # 计算机器人利用率
        total_available = optimal_makespan * len(robot_schedules)
        total_working = 0.0
        robot_util = {}
        for robot_id, task_list in robot_schedules.items():
            working = sum(t['end_time'] - t['start_time'] for t in task_list)
            robot_util[f'arm_{robot_id}'] = round(working / optimal_makespan, 4)
            total_working += working

        resource_utilization = ResourceUtilization(
            robot_utilization=robot_util,
            overall_robot_utilization=round(total_working / total_available, 4) if total_available > 0 else 0.0
        )

        metrics = ExecutionMetrics(
            makespan=optimal_makespan,
            task_success_rate=1.0,  # 最优解默认全部成功
            resource_utilization=resource_utilization,
            constraint_violations=0  # 最优解无违反约束
        )

        instance_name = os.path.splitext(os.path.basename(solution_path))[0]

        return GroundTruth(
            scene_id=scene.scene_id,
            version='1.0',
            source=f"最优解来源于 MRTA-Benchmark 数据集（arXiv:2603.02669, IMR-Bench），"
                   f"文件: {os.path.basename(solution_path)}。"
                   f"makespan={optimal_makespan}, 任务数={data.get('n_tasks', 'N/A')}, "
                   f"机器人={data.get('n_robots', 'N/A')}。",
            optimal_makespan=optimal_makespan,
            assumptions=[
                '每台机器人同一时刻最多执行一个任务。',
                '协作任务需要多台机器人同时参与。',
                '运输时间已在 makespan 中体现。',
                '最优解来自 MRTA-Benchmark 数据集，不包含随机失败。',
                '运输操作从任务结束位置出发，到达下一个任务位置。'
            ],
            schedule=schedule_entries,
            operation_order=operation_order,
            metrics_baseline=metrics
        )

    # ------------------------------------------------------------------
    # 内部构建方法
    # ------------------------------------------------------------------

    def _build_locations(self, n_locations: int, start_idx: int, end_idx: int) -> List[Location]:
        """构建位置列表"""
        locations = []
        for i in range(n_locations):
            if i == start_idx:
                name = '起始仓库(Depot)'
            elif i == end_idx:
                name = '结束仓库(Depot)'
            else:
                name = f'任务位置-{i}'
            locations.append(Location(id=f'loc_{i}', name=name))
        return locations

    def _build_transport_edges(self, T_t: List[List[float]], locations: List[Location]) -> List[TransportEdge]:
        """构建运输边（位置间均有运输连接）"""
        edges = []
        n = len(T_t)
        for i in range(n):
            for j in range(n):
                if i != j and T_t[i][j] > 0:
                    edges.append(TransportEdge(
                        from_location=f'loc_{i}',
                        to_location=f'loc_{j}',
                        distance=round(T_t[i][j], 4)
                    ))
        return edges

    def _build_robots(self, n_robots: int, locations: List[Location]) -> Tuple[List[Robot], List[str]]:
        """构建机械臂列表"""
        robots = []
        robot_ids = []
        for i in range(n_robots):
            rid = f'arm_{i}'
            robot_ids.append(rid)
            robots.append(Robot(
                id=rid,
                name=f'机械臂-{i}',
                initial_location='loc_0',  # 所有机器人从 depot 出发
                capabilities=['task_execution', 'transport'],
                tools=['gripper', f'tool_{i}']
            ))
        return robots, robot_ids

    def _build_tools(self) -> List[Tool]:
        """构建工具列表（每个机器人一个专用工具 + 共享夹爪）"""
        return [
            Tool(id='gripper', quantity=10, supported_operations=['task_execution', 'transport']),
            Tool(id='tool_0', quantity=1, supported_operations=['task_execution']),
            Tool(id='tool_1', quantity=1, supported_operations=['task_execution']),
            Tool(id='tool_2', quantity=1, supported_operations=['task_execution']),
        ]

    def _build_stations(self, n_locations: int, start_idx: int, end_idx: int) -> List[Station]:
        """构建工位列表"""
        stations = []
        # 起始 depot 是工作站
        stations.append(Station(
            id=f'loc_{start_idx}',
            capacity=10,
            supported_operations=['task_execution', 'transport']
        ))
        # 每个任务位置是一个工位
        for i in range(1, n_locations - 1):  # 跳过 start 和 end
            stations.append(Station(
                id=f'loc_{i}',
                capacity=3,  # 协作任务可能多机器人同时
                supported_operations=['task_execution']
            ))
        # 结束 depot
        stations.append(Station(
            id=f'loc_{end_idx}',
            capacity=10,
            supported_operations=['task_execution', 'transport']
        ))
        return stations

    def _build_operations(self,
                          real_task_indices: List[int],
                          T_e: List[float],
                          Q: List[List[int]],
                          n_robots: int,
                          robot_ids: List[str],
                          solution_path: Optional[str] = None,
                          precedence_constraints: Optional[List[List[int]]] = None) -> List[Operation]:
        """
        构建操作列表

        结合 Q 矩阵（任务类型×机器人资格）和最优解（推断协作任务）来确定 allowed_robots。
        从 MRTA-Benchmark 的 precedence_constraints 提取操作间依赖关系。
        """
        # 从最优解推断每个任务需要哪些机器人（处理协作任务）
        task_robots = self._infer_task_robots_from_solution(solution_path, robot_ids) if solution_path else {}

        # 构建前置关系映射: task_id → [前置 task_id, ...]
        pred_map: Dict[int, List[int]] = {}
        if precedence_constraints:
            for constraint in precedence_constraints:
                if len(constraint) >= 2:
                    before_task, after_task = constraint[0], constraint[1]
                    pred_map.setdefault(after_task, []).append(before_task)

        operations = []
        n_q_rows = len(Q)
        n_q_cols = len(Q[0]) if Q else 0

        for task_idx in real_task_indices:
            op_id = f'op_{task_idx}'

            # 1. 优先使用最优解中的机器人分配
            if task_idx in task_robots:
                allowed_robots = task_robots[task_idx]
                is_collaborative = len(allowed_robots) > 1
            else:
                # 2. 回退到 Q 矩阵推断
                allowed_robots = []
                if n_q_cols == n_robots:
                    q_row = Q[(task_idx - 1) % n_q_rows] if n_q_rows > 0 else [1] * n_robots
                    for r in range(n_robots):
                        if r < len(q_row) and q_row[r] == 1:
                            allowed_robots.append(robot_ids[r])
                is_collaborative = False

            if not allowed_robots:
                allowed_robots = robot_ids.copy()

            # 必要工具
            required_tools = ['gripper']
            if is_collaborative:
                required_tools.extend([f'tool_{r.split("_")[1]}' for r in allowed_robots])
            elif allowed_robots:
                required_tools.append(f'tool_{allowed_robots[0].split("_")[1]}')

            # 前置操作
            predecessor_tasks = pred_map.get(task_idx, [])
            predecessors = [f'op_{t}' for t in predecessor_tasks]

            # 操作描述
            collab_note = ' [协作任务]' if is_collaborative else ''
            dep_note = f' 前置: {predecessors}' if predecessors else ''
            desc = (f'任务 {task_idx}{collab_note}：执行时长 {T_e[task_idx]}s，'
                    f'可用机器人: {", ".join(allowed_robots)}{dep_note}')

            op = Operation(
                id=op_id,
                workpiece_id=f'wp_{(task_idx - 1) // 3}',
                type='task_execution',
                description=desc,
                duration=T_e[task_idx],
                allowed_robots=allowed_robots,
                required_tools=required_tools,
                required_station=f'loc_{task_idx}',
                predecessors=predecessors,
                location=f'loc_{task_idx}'
            )
            operations.append(op)

        return operations

    def _infer_task_robots_from_solution(self, solution_path: Optional[str],
                                          robot_ids: List[str]) -> Dict[int, List[str]]:
        """从最优解中推断每个任务的机器人分配"""
        if not solution_path or not os.path.exists(solution_path):
            return {}

        with open(solution_path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        task_robots: Dict[int, List[str]] = {}
        robot_schedules = data.get('robot_schedules', {})

        for robot_id_str, task_list in robot_schedules.items():
            robot_name = f'arm_{robot_id_str}'
            for task_info in task_list:
                task_idx = task_info['task']
                if task_idx not in task_robots:
                    task_robots[task_idx] = []
                if robot_name not in task_robots[task_idx]:
                    task_robots[task_idx].append(robot_name)

        return task_robots

    def _build_workpieces(self,
                          real_task_indices: List[int],
                          T_e: List[float],
                          operations: List[Operation]) -> List[Workpiece]:
        """
        构建工件列表

        MRTA-Benchmark 中任务没有明确的"工件"概念。
        我们将任务分组为工件：每 3 个任务组成一个工件（按启发式规则）。
        """
        from math import ceil
        n_tasks = len(real_task_indices)
        n_workpieces = max(2, ceil(n_tasks / 3))  # 至少 2 个工件

        workpieces = []
        for wp_idx in range(n_workpieces):
            wp_id = f'wp_{wp_idx}'
            # 该工件对应的操作列表
            wp_ops = [op for op in operations if op.workpiece_id == wp_id]
            wp_op_ids = [op.id for op in wp_ops]

            if not wp_ops:
                continue

            durations = [T_e[int(op.id.split('_')[1])] for op in wp_ops
                        if op.id.split('_')[1].isdigit()]

            # 状态序列：每个操作后更新状态
            state_sequence = [f'state_{wp_idx}_start']
            for op in wp_ops:
                state_sequence.append(f'state_{op.id}_done')
            state_sequence.append(f'state_{wp_idx}_complete')

            workpieces.append(Workpiece(
                id=wp_id,
                name=f'工件-{wp_idx}',
                initial_location='loc_0',
                target_location=f'loc_{len(T_e) - 1}',
                state_sequence=state_sequence,
                operation_sequence=wp_op_ids
            ))

        return workpieces

    def _find_operation_by_task(self, scene: Scene, task_idx: int) -> Optional[Operation]:
        """根据任务索引查找对应的 Operation"""
        op_id = f'op_{task_idx}'
        for op in scene.operations:
            if op.id == op_id:
                return op
        return None

    # ------------------------------------------------------------------
    # 便捷方法：一键转换并保存
    # ------------------------------------------------------------------

    def convert_and_save(self, problem_path: str, solution_path: str,
                         scene_output: str = 'config/scene.json',
                         ground_truth_output: str = 'config/ground_truth.json'):
        """
        一键转换 MRTA-Benchmark 数据为项目格式并保存

        参数：
            problem_path: 问题实例 JSON 路径
            solution_path: 最优解 JSON 路径
            scene_output: 输出的 scene.json 路径
            ground_truth_output: 输出的 ground_truth.json 路径
        """
        logger.info(f"转换 MRTA-Benchmark 数据...")
        logger.info(f"  问题实例: {problem_path}")
        logger.info(f"  最优解: {solution_path}")

        # 转换
        scene = self.problem_to_scene(problem_path, solution_path)
        ground_truth = self.solution_to_ground_truth(scene, solution_path)

        # 保存
        os.makedirs(os.path.dirname(scene_output) or '.', exist_ok=True)

        with open(scene_output, 'w', encoding='utf-8') as f:
            json.dump(scene.to_dict(), f, indent=2, ensure_ascii=False)
        logger.info(f"✓ Scene 已保存: {scene_output}")

        with open(ground_truth_output, 'w', encoding='utf-8') as f:
            json.dump(ground_truth.to_dict(), f, indent=2, ensure_ascii=False)
        logger.info(f"✓ GroundTruth 已保存: {ground_truth_output}")

        logger.info(f"✓ 转换完成！makespan 基准: {ground_truth.optimal_makespan}s")
        return scene, ground_truth
