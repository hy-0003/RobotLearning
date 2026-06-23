# 调度核心模块
# 使用析取图（Disjunctive Graph）建模，实现基于 FIFO 策略的调度求解
# 该模块是纯算法实现，不依赖 LLM

from collections import defaultdict
from dataclasses import dataclass, field
from typing import List, Dict, Set, Tuple, Optional
import logging

from data_types import (
    Scene, Operation, Schedule, ScheduleEntry, Robot, Workpiece
)

logger = logging.getLogger(__name__)


@dataclass
class Node:
    """析取图中的节点，代表一个操作或虚拟节点"""
    id: str              # 节点 ID
    operation: Optional[Operation] = None  # 关联的操作（虚拟节点为 None）
    duration: float = 0.0  # 操作时长


@dataclass
class Edge:
    """析取图中的边"""
    from_node: str       # 源节点 ID
    to_node: str         # 目标节点 ID
    is_conjunctive: bool # True: 必须边（工序约束），False: 析取边（资源约束）


class DisjunctiveGraph:
    """
    析取图（Disjunctive Graph）

    建模思路：
    - 节点：每个操作是一个节点
    - 边：两种类型
      1. 连接边（conjunctive）：表示工序依赖关系（必须）
      2. 析取边（disjunctive）：表示资源冲突（需要通过决策确定顺序）
    """

    def __init__(self, scene: Scene):
        """
        初始化析取图

        参数：
            scene: 产线场景
        """
        self.scene = scene
        self.nodes: Dict[str, Node] = {}
        self.edges: List[Edge] = []

        # 创建虚拟的开始和结束节点
        self.source = 'SOURCE'
        self.sink = 'SINK'

        self._build_graph()

    def _build_graph(self):
        """
        构建析取图

        步骤：
        1. 为每个操作创建节点
        2. 添加虚拟开始和结束节点
        3. 根据工序依赖关系添加连接边
        4. 根据资源冲突关系添加析取边
        """
        # 创建操作节点
        for op in self.scene.operations:
            self.nodes[op.id] = Node(
                id=op.id,
                operation=op,
                duration=op.duration
            )

        # 创建虚拟节点
        self.nodes[self.source] = Node(id=self.source, operation=None, duration=0.0)
        self.nodes[self.sink] = Node(id=self.sink, operation=None, duration=0.0)

        # 添加工序依赖关系（连接边）
        self._add_precedence_edges()

        # 添加资源冲突关系（析取边）
        self._add_resource_conflict_edges()

    def _add_precedence_edges(self):
        """
        添加工序依赖关系

        对每个操作，如果没有前置操作，则从 SOURCE 连接；
        如果有前置操作，则从前置操作连接；
        最后从没有后续操作的操作连接到 SINK
        """
        # 从 SOURCE 连接到没有前置的操作
        for op in self.scene.operations:
            if not op.predecessors:
                self.edges.append(Edge(self.source, op.id, is_conjunctive=True))

        # 连接工序依赖
        for op in self.scene.operations:
            for pred_id in op.predecessors:
                self.edges.append(Edge(pred_id, op.id, is_conjunctive=True))

        # 从没有后续的操作连接到 SINK
        op_ids = set(op.id for op in self.scene.operations)
        for op in self.scene.operations:
            has_successor = False
            for other_op in self.scene.operations:
                if op.id in other_op.predecessors:
                    has_successor = True
                    break
            if not has_successor:
                self.edges.append(Edge(op.id, self.sink, is_conjunctive=True))

    def _add_resource_conflict_edges(self):
        """
        添加资源冲突关系（析取边）

        对于每对操作，如果它们竞争同一台机械臂或工位，
        则添加两条析取边（可以选择其中一条作为决策）

        决策规则（FIFO）：按操作 ID 的字典序，ID 小的先执行
        """
        operations = self.scene.operations
        n = len(operations)

        # 检查机械臂冲突
        for i in range(n):
            for j in range(i + 1, n):
                op_i = operations[i]
                op_j = operations[j]

                # 检查是否竞争机械臂
                robots_i = set(op_i.allowed_robots)
                robots_j = set(op_j.allowed_robots)
                if robots_i & robots_j:  # 有交集
                    self._add_disjunctive_edge_pair(op_i, op_j)

                # 检查是否竞争工位
                if op_i.required_station and op_j.required_station:
                    if op_i.required_station == op_j.required_station:
                        self._add_disjunctive_edge_pair(op_i, op_j)

                # 检查是否竞争工具
                tools_i = set(op_i.required_tools)
                tools_j = set(op_j.required_tools)
                if tools_i & tools_j:  # 有交集
                    self._add_disjunctive_edge_pair(op_i, op_j)

    def _add_disjunctive_edge_pair(self, op_i: Operation, op_j: Operation):
        """
        添加一对析取边

        对于操作对 (op_i, op_j)，添加两条选择边：
        - op_i -> op_j 或 op_j -> op_i

        使用 FIFO 策略：操作 ID 较小的先执行
        """
        if op_i.id < op_j.id:
            # op_i 先执行
            self.edges.append(Edge(op_i.id, op_j.id, is_conjunctive=False))
        else:
            # op_j 先执行
            self.edges.append(Edge(op_j.id, op_i.id, is_conjunctive=False))

    def get_critical_path_length(self, fixed_edges: List[Edge]) -> float:
        """
        计算关键路径长度

        给定已固定的边（析取边已决策），计算从 SOURCE 到 SINK 的最长路径

        参数：
            fixed_edges: 所有边（包括连接边和已决策的析取边）

        返回：
            关键路径长度（总完成时间）
        """
        # 使用拓扑排序 + 动态规划计算最长路径
        graph = self._build_adjacency_list(fixed_edges)

        # 拓扑排序
        in_degree = {node_id: 0 for node_id in self.nodes.keys()}
        for edge in fixed_edges:
            in_degree[edge.to_node] += 1

        queue = [node_id for node_id in self.nodes.keys() if in_degree[node_id] == 0]

        dist = {node_id: 0.0 for node_id in self.nodes.keys()}

        while queue:
            u = queue.pop(0)
            for v in graph.get(u, []):
                dist[v] = max(dist[v], dist[u] + self.nodes[u].duration)
                in_degree[v] -= 1
                if in_degree[v] == 0:
                    queue.append(v)

        return dist[self.sink]

    def _build_adjacency_list(self, edges: List[Edge]) -> Dict[str, List[str]]:
        """从边构建邻接表"""
        graph = {node_id: [] for node_id in self.nodes.keys()}
        for edge in edges:
            graph[edge.from_node].append(edge.to_node)
        return graph


class SchedulingSolver:
    """
    调度求解器

    基于析取图的调度求解器。
    - 根据机械臂分配将操作分组：不同机械臂上的操作可并行
    - 同一机械臂上的操作按 FIFO 串行
    - 共享工具/工位的操作即使在不同机械臂上也需串行
    """

    def __init__(self, scene: Scene):
        self.scene = scene
        self.graph = DisjunctiveGraph(scene)

        # 构建运输时间查找表（from, to）→ 时间
        self._travel_lookup: Dict[Tuple[str, str], float] = {}
        for edge in scene.cell_layout.transport_edges:
            self._travel_lookup[(edge.from_location, edge.to_location)] = edge.distance

        # 机器人初始位置
        self._robot_init_loc: Dict[str, str] = {}
        for robot in scene.resources.robots:
            self._robot_init_loc[robot.id] = robot.initial_location

    def _resolve_location(self, op: Operation) -> Optional[str]:
        """获取操作的目标位置"""
        return op.location or op.from_location

    def _get_travel_time(self, from_loc: str, to_loc: str) -> float:
        """查询两位置间运输时间，找不到返回 0"""
        return self._travel_lookup.get((from_loc, to_loc), 0.0)

    def solve(self, robot_assignments: Optional[Dict[str, List[str]]] = None) -> Schedule:
        """
        求解调度问题。

        参数：
            robot_assignments: 操作ID → 机械臂ID列表（可选，来自 TaskPlanner）
                              协作任务可分配多台机械臂。未提供时使用默认分配。

        返回：
            Schedule 对象
        """
        # 使用提供的机械臂分配，否则默认分配
        if robot_assignments is None:
            robot_assignments = {}
            for op in self.scene.operations:
                if op.allowed_robots:
                    robot_assignments[op.id] = op.allowed_robots.copy()
                else:
                    robot_assignments[op.id] = []

        # 收集所有边：连接边(工序约束) + 按分配策略选择的析取边
        fixed_edges = self._select_edges(robot_assignments)

        # 计算纯执行关键路径（不含运输，供参考）
        execution_makespan = self.graph.get_critical_path_length(fixed_edges)

        # 计算每个操作的最早开始时间（纯执行，用于确定拓扑顺序）
        earliest_start = self._compute_earliest_start_time(fixed_edges)

        # ── 核心：计算含运输时间的 makespan ──
        # 使用与 MRTA-Benchmark 数据集完全一致的方法：
        # 同一份 T_e（操作时长）+ 同一份 T_t（运输时间矩阵）
        # 机械臂从初始位置出发，操作间移动耗时 = T_t[cur][target]
        transport_makespan, total_travel = self._compute_transport_timeline(
            fixed_edges, robot_assignments, earliest_start
        )

        # 生成调度表：协作任务为每台参与机械臂各创建一条记录
        # 注：entries 的 start_time/end_time 仍为纯执行时间（仿真器使用）
        entries = []
        for op in self.scene.operations:
            start_time = earliest_start.get(op.id, 0.0)
            end_time = start_time + op.duration
            assigned_robots = robot_assignments.get(op.id, [])
            if not assigned_robots and op.allowed_robots:
                assigned_robots = op.allowed_robots[:1]

            resources = op.required_tools.copy()
            if op.required_station:
                resources.append(op.required_station)

            for rid in assigned_robots:
                entries.append(ScheduleEntry(
                    operation_id=op.id,
                    workpiece_id=op.workpiece_id,
                    robot_id=rid,
                    start_time=start_time,
                    end_time=end_time,
                    resources=resources
                ))

        entries.sort(key=lambda e: e.start_time)
        return Schedule(
            entries=entries,
            makespan=transport_makespan,
            transport_time=total_travel
        )

    def _has_path(self, from_node: str, to_node: str, edges: List[Edge]) -> bool:
        """BFS检查是否存在从 from_node 到 to_node 的路径（不考虑边类型）。"""
        adj: Dict[str, List[str]] = {}
        for e in edges:
            adj.setdefault(e.from_node, []).append(e.to_node)
        visited = set()
        queue = [from_node]
        while queue:
            u = queue.pop(0)
            if u == to_node:
                return True
            if u in visited:
                continue
            visited.add(u)
            for v in adj.get(u, []):
                if v not in visited:
                    queue.append(v)
        return False

    def _select_edges(self, robot_assignments: Dict[str, List[str]]) -> List[Edge]:
        """
        选择最终的边集。

        策略：
        1. 保留所有连接边（工序约束，必须遵守）
        2. 对每对冲突操作，添加析取边时进行环检测：
           如果一条方向会导致环（与现有边形成循环），则选另一方向
        3. 无冲突的操作 → 不加边（可并行）

        冲突排序启发式：耗时长的操作优先执行（LPT）
        """
        edges: List[Edge] = []

        # 1. 所有连接边
        for e in self.graph.edges:
            if e.is_conjunctive:
                edges.append(e)

        # 2. 按冲突类型逐对处理
        ops = self.scene.operations
        for i in range(len(ops)):
            for j in range(i + 1, len(ops)):
                op_i = ops[i]
                op_j = ops[j]

                has_conflict = False

                # 同机器人冲突（比较分配列表的交集）
                ri_set = set(robot_assignments.get(op_i.id, []))
                rj_set = set(robot_assignments.get(op_j.id, []))
                if ri_set & rj_set:
                    has_conflict = True

                # 同工位冲突
                if op_i.required_station and op_j.required_station:
                    if op_i.required_station == op_j.required_station:
                        has_conflict = True

                # 同工具冲突（数量=1 时不能共享）
                tools_i = set(op_i.required_tools)
                tools_j = set(op_j.required_tools)
                shared = tools_i & tools_j
                if shared:
                    for tool_id in shared:
                        tool = next((t for t in self.scene.resources.tools if t.id == tool_id), None)
                        if tool and tool.quantity == 1:
                            has_conflict = True
                            break

                if has_conflict:
                    # 环检测：选择不会形成环的方向
                    cycle_if_i_to_j = self._has_path(op_j.id, op_i.id, edges)
                    cycle_if_j_to_i = self._has_path(op_i.id, op_j.id, edges)

                    if cycle_if_i_to_j and cycle_if_j_to_i:
                        # 已有双向路径，不应发生，跳过
                        pass
                    elif cycle_if_i_to_j:
                        # i→j 会成环，只能 j→i
                        edges.append(Edge(op_j.id, op_i.id, is_conjunctive=False))
                    elif cycle_if_j_to_i:
                        # j→i 会成环，只能 i→j
                        edges.append(Edge(op_i.id, op_j.id, is_conjunctive=False))
                    else:
                        # 两个方向都不会成环，使用运输路线感知启发式
                        if self._prefer_transport_order(op_i, op_j, robot_assignments):
                            edges.append(Edge(op_i.id, op_j.id, is_conjunctive=False))
                        else:
                            edges.append(Edge(op_j.id, op_i.id, is_conjunctive=False))

        return edges

    def _compute_earliest_start_time(self, fixed_edges: List[Edge]) -> Dict[str, float]:
        """
        计算每个操作的最早开始时间

        使用动态规划：对每个节点，最早开始时间 = 其前驱节点的最早结束时间的最大值

        参数：
            fixed_edges: 所有固定的边

        返回：
            操作 ID -> 最早开始时间 的映射
        """
        graph = self._build_adjacency_list(fixed_edges)
        reverse_graph = self._build_adjacency_list_reverse(fixed_edges)

        in_degree = {node_id: 0 for node_id in self.graph.nodes.keys()}
        for edge in fixed_edges:
            in_degree[edge.to_node] += 1

        queue = [node_id for node_id in self.graph.nodes.keys() if in_degree[node_id] == 0]

        earliest = {node_id: 0.0 for node_id in self.graph.nodes.keys()}

        while queue:
            u = queue.pop(0)
            # 对于每个后继节点 v，更新其最早开始时间
            for v in graph.get(u, []):
                earliest[v] = max(earliest[v], earliest[u] + self.graph.nodes[u].duration)
                in_degree[v] -= 1
                if in_degree[v] == 0:
                    queue.append(v)

        return earliest

    def _compute_latest_start_time(self, fixed_edges: List[Edge], makespan: float) -> Dict[str, float]:
        """
        计算每个操作的最晚开始时间

        使用逆向动态规划

        参数：
            fixed_edges: 所有固定的边
            makespan: 总完成时间

        返回：
            操作 ID -> 最晚开始时间 的映射
        """
        graph = self._build_adjacency_list(fixed_edges)

        out_degree = {node_id: 0 for node_id in self.graph.nodes.keys()}
        for edge in fixed_edges:
            out_degree[edge.from_node] += 1

        queue = [node_id for node_id in self.graph.nodes.keys() if out_degree[node_id] == 0]

        latest = {node_id: makespan for node_id in self.graph.nodes.keys()}
        latest[self.graph.sink] = makespan

        while queue:
            u = queue.pop(0)
            predecessors = [edge.from_node for edge in fixed_edges if edge.to_node == u]
            for pred_id in predecessors:
                latest[pred_id] = min(latest[pred_id], latest[u] - self.graph.nodes[pred_id].duration)
                out_degree[pred_id] -= 1
                if out_degree[pred_id] == 0:
                    queue.append(pred_id)

        return latest

    def _prefer_transport_order(self, op_i: Operation, op_j: Operation, robot_assignments: Dict[str, List[str]]) -> bool:
        """比较两个冲突操作，判断是否优先执行 op_i。"""
        common_robots = set(robot_assignments.get(op_i.id, [])) & set(robot_assignments.get(op_j.id, []))
        loc_i = self._resolve_location(op_i)
        loc_j = self._resolve_location(op_j)

        if common_robots and loc_i and loc_j:
            cost_i_first = 0.0
            cost_j_first = 0.0
            for rid in common_robots:
                start_loc = self._robot_init_loc.get(rid, 'loc_0')
                cost_i_first += self._get_travel_time(start_loc, loc_i) + self._get_travel_time(loc_i, loc_j)
                cost_j_first += self._get_travel_time(start_loc, loc_j) + self._get_travel_time(loc_j, loc_i)

            if cost_i_first != cost_j_first:
                return cost_i_first < cost_j_first

        # 无运输优先信息时，回落到长工序优先
        return op_i.duration >= op_j.duration

    def _topological_order(self, fixed_edges: List[Edge]) -> List[str]:
        """返回固定边集对应的拓扑排序（含 SOURCE/SINK）。"""
        graph = self._build_adjacency_list(fixed_edges)
        in_degree = {node_id: 0 for node_id in self.graph.nodes.keys()}
        for edge in fixed_edges:
            in_degree[edge.to_node] += 1

        queue = [node_id for node_id, deg in in_degree.items() if deg == 0]
        order = []
        while queue:
            u = queue.pop(0)
            order.append(u)
            for v in graph.get(u, []):
                in_degree[v] -= 1
                if in_degree[v] == 0:
                    queue.append(v)
        return order

    def _compute_transport_timeline(
        self,
        fixed_edges: List[Edge],
        robot_assignments: Dict[str, List[str]],
        base_starts: Dict[str, float]
    ) -> Tuple[float, float]:
        """
        计算含运输时间的 makespan（与 MRTA-Benchmark 数据集同口径）。

        方法：
        1. 按析取图拓扑顺序遍历所有操作
        2. 每台机械臂从初始位置出发，操作间累加 T_t 运输时间
        3. 协作任务：所有参与机械臂到达后同步开始
        4. 遵守前置依赖：操作不早于其前置操作的完成时间

        返回：(makespan, total_travel_time)
        """
        # 拓扑排序：使用固定边集构造操作顺序，保证先序依赖关系被正确考虑
        topo_nodes = self._topological_order(fixed_edges)
        ops_by_order = [
            (next(op for op in self.scene.operations if op.id == node), base_starts.get(node, 0.0))
            for node in topo_nodes if node not in {self.graph.source, self.graph.sink}
        ]

        # 每台机械臂的时间线与当前位置
        robot_clock: Dict[str, float] = defaultdict(float)
        robot_loc: Dict[str, str] = {}
        for robot in self.scene.resources.robots:
            robot_loc[robot.id] = robot.initial_location

        # 记录每个操作的实际完成时间（用于前置依赖检查）
        actual_end: Dict[str, float] = {}

        # 协作任务集合
        collab_ops = {op_id for op_id, bots in robot_assignments.items() if len(bots) > 1}

        total_travel = 0.0
        processed: Set[str] = set()

        for op, base_start in ops_by_order:
            if op.id in processed:
                continue

            assigned = robot_assignments.get(op.id, [])
            if not assigned:
                continue

            target_loc = self._resolve_location(op)
            if target_loc is None:
                # 无位置信息的操作，直接按纯执行时间
                max_pred_end = max((actual_end.get(p, 0.0) for p in op.predecessors), default=0.0)
                actual_end[op.id] = max(base_start, max_pred_end) + op.duration
                processed.add(op.id)
                continue

            # 前置依赖的最大完成时间
            max_pred_end = max((actual_end.get(p, 0.0) for p in op.predecessors), default=0.0)

            if op.id in collab_ops:
                # ── 协作任务：所有机械臂到达后同步开始 ──
                arrivals = []
                for rid in assigned:
                    cur_loc = robot_loc.get(rid, 'loc_0')
                    travel = self._get_travel_time(cur_loc, target_loc)
                    arrivals.append(robot_clock[rid] + travel)

                sync_start = max(max(arrivals), max_pred_end)

                for rid in assigned:
                    cur_loc = robot_loc.get(rid, 'loc_0')
                    travel = self._get_travel_time(cur_loc, target_loc)
                    total_travel += travel
                    robot_clock[rid] = sync_start + op.duration
                    robot_loc[rid] = target_loc

                actual_end[op.id] = sync_start + op.duration
            else:
                # ── 单机械臂操作 ──
                rid = assigned[0]
                cur_loc = robot_loc.get(rid, 'loc_0')
                travel = self._get_travel_time(cur_loc, target_loc)
                total_travel += travel

                start = max(robot_clock[rid] + travel, max_pred_end)
                robot_clock[rid] = start + op.duration
                robot_loc[rid] = target_loc
                actual_end[op.id] = start + op.duration

            processed.add(op.id)

        # ── 每台机器人返回 depot 的运输时间 ──
        # MRTA-Benchmark 的 makespan 口径包含最后返回 depot 的运输，
        # 所以这里要补齐以与基准公平对比。
        end_depot = self._resolve_end_depot()
        if end_depot:
            for rid, clock in list(robot_clock.items()):
                cur_loc = robot_loc.get(rid, 'loc_0')
                return_travel = self._get_travel_time(cur_loc, end_depot)
                total_travel += return_travel
                robot_clock[rid] = clock + return_travel

        makespan = max(robot_clock.values()) if robot_clock else 0.0
        return makespan, total_travel

    def _resolve_end_depot(self) -> Optional[str]:
        """查找结束 depot 的位置 ID。"""
        for loc in self.scene.cell_layout.locations:
            if '结束' in (loc.name or ''):
                return loc.id
        # 回退：最后一个位置
        if self.scene.cell_layout.locations:
            return self.scene.cell_layout.locations[-1].id
        return None

    def _build_adjacency_list(self, edges: List[Edge]) -> Dict[str, List[str]]:
        """从边构建邻接表"""
        graph = {node_id: [] for node_id in self.graph.nodes.keys()}
        for edge in edges:
            graph[edge.from_node].append(edge.to_node)
        return graph

    def _build_adjacency_list_reverse(self, edges: List[Edge]) -> Dict[str, List[str]]:
        """从边构建反向邻接表（源指向）"""
        graph = {node_id: [] for node_id in self.graph.nodes.keys()}
        for edge in edges:
            graph[edge.to_node].append(edge.from_node)
        return graph
