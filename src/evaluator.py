# 对比评估模块
# 将 Harness 闭环输出的调度结果与 MRTA-Benchmark 最优基准进行全指标对比
# 
# 评估指标（满足题目要求的最少四项）：
#   1. makespan：总完成时间（含运输时间，与基准同口径）
#   2. 任务成功率：成功操作数 / 总操作数
#   3. 资源利用率：机械臂实际工作时间 / (含运输的 makespan × 机械臂数)
#   4. 违反约束次数：资源冲突 + 顺序违反的总次数
#
# 关键设计：
#   - 基准值直接取自 ground_truth.json 的 metrics_baseline（MRTA-Benchmark 最优解）
#   - 实际值从仿真反馈 + 运输时间估算得出，与基准在同一维度对比
#   - 运输时间根据 scene.json 的 transport_edges 计算，保证公平性

import json
import os
import logging
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass, field, asdict
from datetime import datetime

from data_types import (
    GroundTruth, Schedule, SimulationFeedback, ExecutionResult,
    ScheduleEntry, ExecutionMetrics, ResourceUtilization, Scene, Operation
)

logger = logging.getLogger(__name__)


# =========================================================================
# 数据类
# =========================================================================

@dataclass
class MetricComparison:
    """单个指标的对比结果"""
    metric_name: str
    metric_key: str
    baseline_value: float
    actual_value: float
    delta: float
    delta_pct: float
    direction: str          # "lower_is_better" | "higher_is_better"
    verdict: str


@dataclass
class EvaluationReport:
    """完整评估报告"""
    report_id: str
    generated_at: str
    scene_id: str
    data_source: str
    simulator_mode: str
    n_operations: int
    n_robots: int
    n_workpieces: int
    n_iterations: int
    comparisons: List[MetricComparison]
    makespan_baseline: float
    makespan_actual: float
    makespan_delta_pct: float
    task_success_rate_baseline: float
    task_success_rate_actual: float
    resource_util_baseline: float
    resource_util_actual: float
    constraint_violations_baseline: int
    constraint_violations_actual: int
    overall_verdict: str
    overall_score: float
    # 附加说明
    note: str = ""


# =========================================================================
# 评估器
# =========================================================================

class Evaluator:
    """
    对比评估器 —— 将 Harness 输出与 MRTA-Benchmark 最优基准对比。

    makespan 公平对比策略：
      基准的 makespan 包含运输时间，而 Mock 仿真器只输出纯执行时间。
      因此本评估器根据 scene.json 的 transport_edges 为实际调度追加运输时间，
      使两边的 makespan 在同一口径下对比。
    """

    def __init__(self, ground_truth: GroundTruth, scene: Optional[Scene] = None):
        self.ground_truth = ground_truth
        self.scene = scene

        # 预建运输时间查找表
        self._travel_lookup: Dict[Tuple[str, str], float] = {}
        if scene:
            for edge in scene.cell_layout.transport_edges:
                self._travel_lookup[(edge.from_location, edge.to_location)] = edge.distance

        # 预建操作查找表
        self._op_lookup: Dict[str, Operation] = {}
        if scene:
            for op in scene.operations:
                self._op_lookup[op.id] = op

        # 机器人初始位置
        self._robot_init_loc: Dict[str, str] = {}
        if scene:
            for robot in scene.resources.robots:
                self._robot_init_loc[robot.id] = robot.initial_location

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------

    def evaluate(self,
                 schedule: Schedule,
                 feedback: SimulationFeedback,
                 n_iterations: int = 1,
                 simulator_mode: str = "Mock Simulator") -> EvaluationReport:
        """
        执行全指标对比评估。
        """
        gt = self.ground_truth
        gt_metrics = gt.metrics_baseline

        # ---- 1. Makespan（含运输时间） ----
        baseline_makespan = gt.optimal_makespan

        # 使用调度器计算的 makespan（含运输时间），与基准同口径公平对比。
        # 代码执行器的 sandbox 时间为各机械臂独立时钟的最大值，不含多机同步等待，不可直接对比。
        if schedule and schedule.makespan > 0:
            actual_makespan = schedule.makespan
            note = "（调度器计划 makespan，含运输时间）"
        else:
            actual_makespan = feedback.total_makespan if feedback else 0.0
            note = "（代码实际执行 makespan）" if actual_makespan > 0 else ""

        makespan_delta = actual_makespan - baseline_makespan
        makespan_delta_pct = (makespan_delta / baseline_makespan * 100) if baseline_makespan > 0 else 0.0

        # ---- 2. 任务成功率 ----
        # 按唯一操作计数（协作任务有多条机器人记录，但只算一次成功）
        op_results: Dict[str, str] = {}
        for r in feedback.results:
            if r.operation_id not in op_results or r.status != 'success':
                op_results[r.operation_id] = r.status
        total_ops = len(op_results)
        actual_success_count = sum(1 for s in op_results.values() if s == 'success')
        baseline_success_rate = gt_metrics.task_success_rate  # 1.0
        actual_success_rate = actual_success_count / total_ops if total_ops > 0 else 0.0
        success_delta = actual_success_rate - baseline_success_rate
        success_delta_pct = success_delta * 100

        # ---- 3. 资源利用率（从调度条目计算，与基准同口径） ----
        baseline_util = gt_metrics.resource_utilization.overall_robot_utilization  # 0.4583
        n_robots = len({e.robot_id for e in schedule.entries}) if schedule and schedule.entries else 3
        actual_util = self._compute_actual_utilization_from_schedule(
            schedule.entries if schedule else [], actual_makespan, n_robots
        )
        util_delta = actual_util - baseline_util
        util_delta_pct = (util_delta / baseline_util * 100) if baseline_util > 0 else 0.0

        # ---- 4. 违反约束次数 ----
        baseline_violations = gt_metrics.constraint_violations  # 0
        actual_violations = self._count_constraint_violations(feedback, schedule)
        violations_delta = actual_violations - baseline_violations

        # ---- 构建对比项 ----
        comparisons = [
            MetricComparison(
                metric_name="总完成时间 (makespan)",
                metric_key="makespan",
                baseline_value=baseline_makespan,
                actual_value=actual_makespan,
                delta=makespan_delta,
                delta_pct=makespan_delta_pct,
                direction="lower_is_better",
                verdict=self._verdict_lower(makespan_delta_pct)
            ),
            MetricComparison(
                metric_name="任务成功率",
                metric_key="task_success_rate",
                baseline_value=baseline_success_rate,
                actual_value=actual_success_rate,
                delta=success_delta,
                delta_pct=success_delta_pct,
                direction="higher_is_better",
                verdict=self._verdict_higher(actual_success_rate, baseline_success_rate)
            ),
            MetricComparison(
                metric_name="资源利用率（机械臂）",
                metric_key="resource_utilization",
                baseline_value=baseline_util,
                actual_value=actual_util,
                delta=util_delta,
                delta_pct=util_delta_pct,
                direction="higher_is_better",
                verdict=self._verdict_higher(actual_util, baseline_util)
            ),
            MetricComparison(
                metric_name="违反约束次数",
                metric_key="constraint_violations",
                baseline_value=float(baseline_violations),
                actual_value=float(actual_violations),
                delta=float(violations_delta),
                delta_pct=float(violations_delta) if baseline_violations == 0 else (violations_delta / baseline_violations * 100),
                direction="lower_is_better",
                verdict="[OK] 达标" if actual_violations == 0 else ("[~] 略差" if actual_violations <= 3 else "[X] 显著劣化")
            )
        ]

        overall_score, overall_verdict = self._compute_overall_score(comparisons)

        # ---- 构建报告 ----
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        report_id = f"eval_{gt.scene_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        if self.scene:
            data_source = "MRTA-Benchmark (arXiv:2603.02669)"
            n_ops = len(self.scene.operations)
            n_wp = len(self.scene.workpieces)
            n_rob = len(self.scene.resources.robots)
        else:
            data_source = gt.source
            n_ops = len(gt.operation_order)
            n_wp = 2
            n_rob = len({e.robot_id for e in gt.schedule})

        return EvaluationReport(
            report_id=report_id,
            generated_at=now_str,
            scene_id=gt.scene_id,
            data_source=data_source,
            simulator_mode=simulator_mode,
            n_operations=n_ops,
            n_robots=n_rob,
            n_workpieces=n_wp,
            n_iterations=n_iterations,
            comparisons=comparisons,
            makespan_baseline=baseline_makespan,
            makespan_actual=actual_makespan,
            makespan_delta_pct=makespan_delta_pct,
            task_success_rate_baseline=baseline_success_rate,
            task_success_rate_actual=actual_success_rate,
            resource_util_baseline=baseline_util,
            resource_util_actual=actual_util,
            constraint_violations_baseline=baseline_violations,
            constraint_violations_actual=actual_violations,
            overall_verdict=overall_verdict,
            overall_score=overall_score,
            note=note
        )

    # ==================================================================
    # 利用率计算
    # ==================================================================

    def _compute_actual_utilization_from_schedule(
        self,
        schedule_entries: List[ScheduleEntry],
        makespan: float,
        n_robots: int
    ) -> float:
        """
        从调度条目计算机械臂平均利用率（与基准同口径）。

        利用率 = Σ(每条调度条目的 duration) / (makespan × 机械臂数)

        这与 MRTA-Benchmark 的计算方式一致：只统计调度安排的工作时间，
        不使用代码执行轨迹（后者包含冗余的 move/wait 等原语调用）。
        """
        if makespan <= 0 or n_robots <= 0:
            return 0.0

        robot_working: Dict[str, float] = {}
        for e in schedule_entries:
            rid = e.robot_id
            duration = max(0.0, e.end_time - e.start_time)
            robot_working[rid] = robot_working.get(rid, 0.0) + duration

        total_working = sum(robot_working.values())
        total_available = makespan * n_robots
        util = total_working / total_available if total_available > 0 else 0.0
        return min(util, 1.0)

    # ==================================================================
    # 约束违反计数
    # ==================================================================

    def _count_constraint_violations(
        self,
        feedback: SimulationFeedback,
        schedule: Optional[Schedule] = None
    ) -> int:
        """
        统计真正的调度约束违反次数。

        计数规则：
          - 资源冲突（工具/工位被占用）：由仿真器检测，是真正的调度问题
          - 同一机械臂时间重叠：调度器应保证不重叠，如有则是 bug
          - 操作前置依赖违反：操作在其依赖未完成时开始

        注意：仿真注入的随机超时(timeout_probability)和随机错误(success_probability)
        是仿真噪声，不计入约束违反。它们影响"任务成功率"指标。

        GT 的 operation_order 是多机械臂并行操作的一种合法交织顺序，
        不是强制前置依赖，因此不作为约束违反的判断依据。
        """
        violations = 0

        # 1. 资源冲突（仿真器检测到的真实调度问题）
        for r in feedback.results:
            if r.status == 'resource_conflict':
                violations += 1

        # 2. 同一机械臂时间重叠检查
        if schedule and schedule.entries:
            robot_timeline: Dict[str, List[Tuple[float, float, str]]] = {}
            for e in schedule.entries:
                robot_timeline.setdefault(e.robot_id, []).append(
                    (e.start_time, e.end_time, e.operation_id)
                )
            for rid, intervals in robot_timeline.items():
                intervals.sort()
                for i in range(len(intervals)):
                    for j in range(i + 1, len(intervals)):
                        if intervals[i][1] > intervals[j][0]:  # 重叠
                            violations += 1

        # 3. 操作依赖检查（如果场景中有显式依赖）
        if schedule and schedule.entries and self.scene:
            entry_map = {e.operation_id: e for e in schedule.entries}
            for op in self.scene.operations:
                if op.predecessors and op.id in entry_map:
                    op_start = entry_map[op.id].start_time
                    for dep_id in op.predecessors:
                        if dep_id in entry_map:
                            dep_end = entry_map[dep_id].end_time
                            if op_start < dep_end:
                                violations += 1

        return violations

    # ==================================================================
    # 判定逻辑
    # ==================================================================

    def _verdict_lower(self, delta_pct: float) -> str:
        if delta_pct <= 10:
            return "[OK] 达标"
        elif delta_pct <= 30:
            return "[~] 略差"
        else:
            return "[X] 显著劣化"

    def _verdict_higher(self, actual: float, baseline: float) -> str:
        if baseline == 0:
            return "[OK] 达标" if actual > 0 else "[X] 显著劣化"
        ratio = actual / baseline
        if ratio >= 0.90:
            return "[OK] 达标"
        elif ratio >= 0.70:
            return "[~] 略差"
        else:
            return "[X] 显著劣化"

    def _compute_overall_score(self, comparisons: List[MetricComparison]) -> Tuple[float, str]:
        """
        综合评分 0-100。
        权重：makespan 30% | 成功率 30% | 利用率 20% | 约束违反 20%
        """
        mk, sr, ru, cv = comparisons

        # --- makespan (30) ---
        if mk.delta_pct <= 5:
            s_mk = 30
        elif mk.delta_pct <= 15:
            s_mk = 24
        elif mk.delta_pct <= 30:
            s_mk = 16
        elif mk.delta_pct <= 60:
            s_mk = 7
        else:
            s_mk = 1

        # --- 成功率 (30) ---
        if sr.actual_value >= 0.98:
            s_sr = 30
        elif sr.actual_value >= 0.85:
            s_sr = 24
        elif sr.actual_value >= 0.70:
            s_sr = 15
        elif sr.actual_value >= 0.50:
            s_sr = 6
        else:
            s_sr = 1

        # --- 利用率 (20) ---
        if ru.delta_pct >= -5:
            s_ru = 20
        elif ru.delta_pct >= -15:
            s_ru = 14
        elif ru.delta_pct >= -30:
            s_ru = 8
        else:
            s_ru = 2

        # --- 约束违反 (20) ---
        if cv.actual_value == 0:
            s_cv = 20
        elif cv.actual_value <= 2:
            s_cv = 14
        elif cv.actual_value <= 5:
            s_cv = 7
        else:
            s_cv = 1

        total = s_mk + s_sr + s_ru + s_cv

        if total >= 85:
            verdict = "系统表现接近最优基准，Harness 闭环调度有效。"
        elif total >= 65:
            verdict = "系统基本可用，部分指标有优化空间。"
        elif total >= 45:
            verdict = "系统需要进一步优化 Harness 重规划策略。"
        else:
            verdict = "系统与最优基准差距显著，需检查 LLM 任务分解和调度逻辑。"

        return total, verdict

    # ==================================================================
    # 输出
    # ==================================================================

    def print_comparison_table(self, report: EvaluationReport):
        """打印对比评估表格"""
        print()
        print("╔" + "=" * 72 + "╗")
        print("║" + " " * 22 + "对 比 评 估 报 告" + " " * 34 + "║")
        print("╠" + "=" * 72 + "╣")
        print("║  基准来源: MRTA-Benchmark最优解" + " " * 19 + "║")
        print("║  场景规模: {n_ops} 操作 × {n_rob} 机器人 × {n_wp} 工件".format(
            n_ops=report.n_operations, n_rob=report.n_robots, n_wp=report.n_workpieces
        ) + " " * (72 - 30 - len(f"{report.n_operations} 操作 × {report.n_robots} 机器人 × {report.n_workpieces} 工件")) + "║")
        print("║  仿真模式: {mode}".format(mode=report.simulator_mode) + " " * (72 - 16 - len(report.simulator_mode)) + "║")
        print("╚" + "=" * 72 + "╝")
        print()

        # 指标对比表
        sep = "  ├" + "─" * 16 + "┼" + "─" * 12 + "┼" + "─" * 12 + "┼" + "─" * 10 + "┼" + "─" * 8 + "┼" + "─" * 14 + "┤"
        print("  ┌" + "─" * 16 + "┬" + "─" * 12 + "┬" + "─" * 12 + "┬" + "─" * 10 + "┬" + "─" * 8 + "┬" + "─" * 14 + "┐")
        print(f"  │ {'指标':<14} │ {'基准值':>8} │ {'实际值':>8} │ {'差值':>6} │ {'差值%':>5} │ {'判定':<12} │")
        print(sep)

        for c in report.comparisons:
            arrow = "↓" if c.direction == "lower_is_better" else "↑"
            name = c.metric_name[:14]
            print(f"  │ {name:<14}{arrow}│ {c.baseline_value:>8.2f} │ {c.actual_value:>8.2f} │ {c.delta:>+6.2f} │ {c.delta_pct:>+5.1f}% │ {c.verdict:<12} │")
            if c != report.comparisons[-1]:
                print(sep)

        print("  └" + "─" * 16 + "┴" + "─" * 12 + "┴" + "─" * 12 + "┴" + "─" * 10 + "┴" + "─" * 8 + "┴" + "─" * 14 + "┘")
        print()

        # 说明
        if report.note:
            print(f"  📝 {report.note}")
            print()

        # 综合评分
        score_bar = "█" * int(report.overall_score / 5) + "░" * (20 - int(report.overall_score / 5))
        print(f"  综合评分: [{score_bar}] {report.overall_score:.0f} / 100")
        print(f"  综合评价: {report.overall_verdict}")
        print()

    # ── 甘特图渲染 ──

    def _build_gantt_lines(self,
                           gt_entries: List[ScheduleEntry],
                           sys_entries: List[ScheduleEntry],
                           gt_makespan: float,
                           sys_makespan: float,
                           sys_is_execution: bool = False) -> List[str]:
        """构建甘特图的所有行（共享逻辑，供 print 和 render 使用）。"""
        bar_width = 70
        lines: List[str] = []

        gt_positions, gt_sim_makespan = self._compute_transport_positions(gt_entries)
        gt_max = gt_makespan if gt_makespan > 0 else (gt_sim_makespan if gt_sim_makespan > 0 else 1.0)

        if sys_is_execution:
            sys_positions = None
            sys_max = sys_makespan if sys_makespan > 0 else 1.0
        else:
            sys_positions, sys_sim_makespan = self._compute_transport_positions(sys_entries)
            sys_max = sys_makespan if sys_makespan > 0 else (sys_sim_makespan if sys_sim_makespan > 0 else 1.0)

        robots = sorted(set(e.robot_id for e in gt_entries) | set(e.robot_id for e in sys_entries))

        lines.append("")
        lines.append("╔" + "═" * (bar_width * 2 + 5) + "╗")
        left_title = "最优调度 (MRTA-Benchmark)"
        right_title = "系统生成调度"
        lines.append(f"║{left_title:^{bar_width}}│{right_title:^{bar_width}}║")
        lines.append("╠" + "═" * bar_width + "═╤═" + "═" * bar_width + "═╣")

        gt_ruler = self._make_ruler(gt_max, bar_width)
        sys_ruler = self._make_ruler(sys_max, bar_width)
        lines.append("║ " + gt_ruler + " │ " + sys_ruler + " ║")

        for robot_id in robots:
            lines.append("╟" + "─" * bar_width + "─┼─" + "─" * bar_width + "─╢")

            gt_row = self._build_gantt_row(gt_entries, robot_id, gt_max, bar_width, gt_positions)
            sys_row = self._build_gantt_row(sys_entries, robot_id, sys_max, bar_width, sys_positions)

            gt_mk_pos = int(gt_makespan / gt_max * (bar_width - 1)) if gt_max > 0 else 0
            sys_mk_pos = int(sys_makespan / sys_max * (bar_width - 1)) if sys_max > 0 else 0
            gt_mk_pos = min(max(gt_mk_pos, 0), bar_width - 1)
            sys_mk_pos = min(max(sys_mk_pos, 0), bar_width - 1)
            gt_row = gt_row[:gt_mk_pos] + "▌" + gt_row[gt_mk_pos + 1:]
            sys_row = sys_row[:sys_mk_pos] + "▌" + sys_row[sys_mk_pos + 1:]
            lines.append(f"║ {gt_row} │ {sys_row} ║  {robot_id}")

        lines.append("╚" + "═" * bar_width + "═╧═" + "═" * bar_width + "═╝")
        lines.append("")
        lines.append("  图例: █ = 操作执行  · = 空闲/运输  ▌ = makespan 终点  数字 = 操作编号")
        lines.append(f"  最优 makespan = {gt_makespan:.1f}s   系统 makespan = {sys_makespan:.1f}s")
        lines.append("")
        return lines

    def print_schedule_gantt(self,
                             gt_entries: List[ScheduleEntry],
                             sys_entries: List[ScheduleEntry],
                             gt_makespan: float,
                             sys_makespan: float,
                             sys_is_execution: bool = False):
        """并排打印最优调度 vs 系统调度的甘特图。"""
        for line in self._build_gantt_lines(gt_entries, sys_entries, gt_makespan, sys_makespan, sys_is_execution):
            print(line)

    def render_schedule_gantt(self,
                              gt_entries: List[ScheduleEntry],
                              sys_entries: List[ScheduleEntry],
                              gt_makespan: float,
                              sys_makespan: float,
                              sys_is_execution: bool = False) -> str:
        """返回甘特图文本（供报告生成器使用），不打印到终端。"""
        return "\n".join(self._build_gantt_lines(gt_entries, sys_entries, gt_makespan, sys_makespan, sys_is_execution))

    def _compute_transport_positions(
        self, entries: List[ScheduleEntry]
    ) -> Tuple[Dict[str, List[Tuple[float, float]]], float]:
        """
        计算运输感知的操作时间位置。

        返回：( {robot_id: [(transport_aware_start, transport_aware_end), ...]}, simulated_makespan )
        simulated_makespan 是所有机器人运输模拟后的最大结束时间。
        """
        if not entries:
            return {}, 0.0

        robots = sorted(set(e.robot_id for e in entries))
        # 按 operation_id 去重（协作任务同一 op 对多台机器人各一条）
        op_robots: Dict[str, List[str]] = {}
        op_duration: Dict[str, float] = {}
        for e in entries:
            op_robots.setdefault(e.operation_id, []).append(e.robot_id)
            op_duration[e.operation_id] = e.end_time - e.start_time

        # 全局排序：用所有机器人中最早 start_time
        op_order = sorted(set(e.operation_id for e in entries),
                          key=lambda oid: min(e.start_time for e in entries if e.operation_id == oid))

        robot_clock: Dict[str, float] = {r: 0.0 for r in robots}
        robot_loc: Dict[str, str] = {}
        for r in robots:
            init_loc = self._robot_init_loc.get(r, 'loc_0')
            robot_loc[r] = init_loc if init_loc else 'loc_0'

        collab_ops = {oid: bots for oid, bots in op_robots.items() if len(bots) > 1}
        result: Dict[str, List[Tuple[float, float]]] = {r: [] for r in robots}

        for oid in op_order:
            assigned = op_robots.get(oid, [])
            if not assigned:
                continue

            op = self._op_lookup.get(oid)
            target_loc = op.location if op and op.location else (op.from_location if op else None)
            if target_loc is None:
                target_loc = 'loc_0'

            duration = op_duration.get(oid, 0)

            if oid in collab_ops:
                arrivals = []
                for rid in assigned:
                    cur = robot_loc.get(rid, 'loc_0')
                    travel = self._travel_lookup.get((cur, target_loc), 0.0)
                    arrivals.append(robot_clock[rid] + travel)
                sync_start = max(arrivals)
                for rid in assigned:
                    cur = robot_loc.get(rid, 'loc_0')
                    travel = self._travel_lookup.get((cur, target_loc), 0.0)
                    robot_clock[rid] = sync_start + duration
                    robot_loc[rid] = target_loc
                    result[rid].append((sync_start, sync_start + duration))
            else:
                rid = assigned[0]
                cur = robot_loc.get(rid, 'loc_0')
                travel = self._travel_lookup.get((cur, target_loc), 0.0)
                start_t = robot_clock[rid] + travel
                robot_clock[rid] = start_t + duration
                robot_loc[rid] = target_loc
                result[rid].append((start_t, start_t + duration))

        # ── 每台机器人返回 depot 的运输时间 ──
        end_depot = self._resolve_end_depot()
        if end_depot:
            for rid, clock in list(robot_clock.items()):
                cur = robot_loc.get(rid, 'loc_0')
                robot_clock[rid] = clock + self._travel_lookup.get((cur, end_depot), 0.0)

        sim_makespan = max(robot_clock.values()) if robot_clock else 0.0
        return result, sim_makespan

    def _resolve_end_depot(self) -> Optional[str]:
        """查找结束 depot 的位置 ID。"""
        if self.scene:
            for loc in self.scene.cell_layout.locations:
                if '结束' in (loc.name or ''):
                    return loc.id
            if self.scene.cell_layout.locations:
                return self.scene.cell_layout.locations[-1].id
        return None

    def _make_ruler(self, max_val: float, bar_width: int) -> str:
        """生成甘特图刻度尺，最后一个刻度值左移以保证完整显示。"""
        eff_w = bar_width - 1
        last_label = str(int(max_val))
        ruler = "0"
        for i in range(1, 10):
            pos = int(i * eff_w / 10)
            while len(ruler) < pos:
                ruler += " "
            ruler += str(int(max_val * i / 10))
        # 最后一个刻度：左移使得完整数字不越界
        last_pos = max(eff_w - len(last_label) + 1, int(9 * eff_w / 10) + 2)
        while len(ruler) < last_pos:
            ruler += " "
        ruler += last_label
        # 截断/补齐到 bar_width
        if len(ruler) > bar_width:
            ruler = ruler[:bar_width]
        while len(ruler) < bar_width:
            ruler += " "
        return ruler

    @staticmethod
    def _extract_gantt_label(operation_id: str) -> str:
        """从 operation_id 提取甘特图标签。

        'op_5'              → '5'
        'arm_1_primitive_3' → '3' (代码执行原语)
        其他格式              → 截取后4位
        """
        if operation_id.startswith('op_'):
            return operation_id[3:]
        if '_primitive_' in operation_id:
            return operation_id.rsplit('_', 1)[-1]
        # fallback: 取最后一个下划线后的部分，最多2字符
        parts = operation_id.rsplit('_', 1)
        fallback = parts[-1] if len(parts) > 1 else operation_id
        return fallback[:3]

    def _build_gantt_row(self, entries: List[ScheduleEntry], robot_id: str,
                         max_time: float, bar_width: int,
                         transport_positions: Dict[str, List[Tuple[float, float]]] = None) -> str:
        """为单台机器人构建一行甘特图字符串。用 bar_width-1 做分母，保证 max_time 精确对齐最后一格。"""
        eff_w = bar_width - 1  # 有效宽度：位置 eff_w 正好代表 max_time
        seen_ops: set = set()
        robot_entries = []
        for e in entries:
            if e.robot_id == robot_id and e.operation_id not in seen_ops:
                seen_ops.add(e.operation_id)
                robot_entries.append(e)
        robot_entries.sort(key=lambda e: e.start_time)

        chars = ["·"] * bar_width
        positions = transport_positions.get(robot_id, []) if transport_positions else []

        if positions and len(positions) == len(robot_entries):
            for idx, e in enumerate(robot_entries):
                t_start, t_end = positions[idx]
                left = int(t_start / max_time * eff_w) if max_time > 0 else 0
                right = int(t_end / max_time * eff_w) if max_time > 0 else 0
                left = max(0, min(left, bar_width - 1))
                right = max(left + 1, min(right, bar_width - 1))

                label = self._extract_gantt_label(e.operation_id)

                for i in range(left, right + 1):
                    if i < bar_width:
                        chars[i] = "█"
                mid = (left + right) // 2
                if mid < bar_width:
                    for j, ch in enumerate(label):
                        if mid + j < bar_width:
                            chars[mid + j] = ch
        else:
            for e in robot_entries:
                left = int(e.start_time / max_time * eff_w) if max_time > 0 else 0
                right = int(e.end_time / max_time * eff_w) if max_time > 0 else 0
                left = max(0, min(left, bar_width - 1))
                right = max(left + 1, min(right, bar_width - 1))

                label = self._extract_gantt_label(e.operation_id)

                for i in range(left, right + 1):
                    if i < bar_width:
                        chars[i] = "█"
                mid = (left + right) // 2
                if mid < bar_width:
                    for j, ch in enumerate(label):
                        if mid + j < bar_width:
                            chars[mid + j] = ch

        return "".join(chars)

    def to_dict(self, report: EvaluationReport) -> Dict[str, Any]:
        """将报告转为可序列化字典"""
        return {
            'report_id': report.report_id,
            'generated_at': report.generated_at,
            'scene_id': report.scene_id,
            'data_source': report.data_source,
            'simulator_mode': report.simulator_mode,
            'scene_scale': {
                'n_operations': report.n_operations,
                'n_robots': report.n_robots,
                'n_workpieces': report.n_workpieces
            },
            'harness_iterations': report.n_iterations,
            'metrics_comparison': {
                'makespan': {
                    'baseline': report.makespan_baseline,
                    'actual': report.makespan_actual,
                    'delta_pct': round(report.makespan_delta_pct, 2),
                    'verdict': report.comparisons[0].verdict
                },
                'task_success_rate': {
                    'baseline': report.task_success_rate_baseline,
                    'actual': report.task_success_rate_actual,
                    'delta': round(report.task_success_rate_actual - report.task_success_rate_baseline, 4),
                    'verdict': report.comparisons[1].verdict
                },
                'resource_utilization': {
                    'baseline': round(report.resource_util_baseline, 4),
                    'actual': round(report.resource_util_actual, 4),
                    'delta_pct': round(
                        (report.resource_util_actual - report.resource_util_baseline) /
                        report.resource_util_baseline * 100, 2
                    ) if report.resource_util_baseline > 0 else 0,
                    'verdict': report.comparisons[2].verdict
                },
                'constraint_violations': {
                    'baseline': report.constraint_violations_baseline,
                    'actual': report.constraint_violations_actual,
                    'verdict': report.comparisons[3].verdict
                }
            },
            'overall_score': report.overall_score,
            'overall_verdict': report.overall_verdict,
            'note': report.note,
            'detailed_comparisons': [asdict(c) for c in report.comparisons]
        }

    def save_report(self, report: EvaluationReport, output_dir: str = 'output') -> str:
        """保存评估报告到 JSON 文件"""
        os.makedirs(output_dir, exist_ok=True)
        filepath = os.path.join(output_dir, f'evaluation_report_{report.report_id}.json')
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(self.to_dict(report), f, indent=2, ensure_ascii=False)
        logger.info(f"✓ 评估报告已保存: {filepath}")
        return filepath
