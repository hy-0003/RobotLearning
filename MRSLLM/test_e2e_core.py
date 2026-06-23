"""End-to-end test of scheduler + simulator + evaluator (no LLM)."""
import json, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from task_planner import TaskPlan
from scheduler import SchedulingSolver
from simulator import MockSimulator
from evaluator import Evaluator
from data_types import Scene, GroundTruth, Schedule, SimulationFeedback

# Load config
with open("config/scene.json", encoding="utf-8") as f:
    scene_data = json.load(f)
with open("config/ground_truth.json", encoding="utf-8") as f:
    gt_data = json.load(f)

scene = Scene.from_dict(scene_data)
gt = GroundTruth.from_dict(gt_data)

print("=" * 60)
print("E2E CORE TEST (no LLM)")
print("=" * 60)

# Step 1: Build TaskPlan (default: each op uses all its allowed robots)
ops = scene.operations
robot_assignments = {}
for op in ops:
    # For collaborative tasks, use ALL allowed robots
    robot_assignments[op.id] = list(op.allowed_robots)
task_plan = TaskPlan(
    instruction="test",
    operations=[op.id for op in ops],
    robot_assignments=robot_assignments
)
print(f"\n1. TaskPlan: {len(ops)} ops")
for op in ops:
    robots = robot_assignments.get(op.id, [])
    print(f"   {op.id}: duration={op.duration}s, robots={robots}, deps={op.predecessors}")

# Step 2: Scheduler (SchedulingSolver with LPT)
solver = SchedulingSolver(scene)
schedule: Schedule = solver.solve(robot_assignments=task_plan.robot_assignments)
print(f"\n2. Schedule: {len(schedule.entries)} entries, makespan={schedule.makespan:.2f}s")
for e in schedule.entries:
    collab = " (协同)" if len(task_plan.robot_assignments.get(e.operation_id, [])) > 1 else ""
    print(f"   {e.operation_id} | {e.robot_id} | {e.start_time:.2f}-{e.end_time:.2f}{collab}")

# Step 3: Simulator
sim = MockSimulator(scene)
feedback: SimulationFeedback = sim.execute_schedule(schedule)
print(f"\n3. Simulator: {len(feedback.results)} results, {feedback.success_count} unique ops succeeded")
for r in feedback.results[:15]:
    print(f"   {r.operation_id} | {r.robot_id} | {r.status}")

# Step 4: Evaluator
evaluator = Evaluator(ground_truth=gt, scene=scene)
report = evaluator.evaluate(schedule=schedule, feedback=feedback)
print(f"\n4. Evaluation:")
for c in report.comparisons:
    direction = "↓" if c.direction == "lower_is_better" else "↑"
    print(f"   {c.metric_name}: {c.actual_value:.4f} vs {c.baseline_value:.4f} ({direction} Δ={c.delta_pct:+.1f}%) [{c.verdict}]")
print(f"   OVERALL SCORE: {report.overall_score:.1f}/100 ({report.overall_score/100:.0%})")
print(f"   Verdict: {report.overall_verdict}")
print("\n5. Gantt Chart (最优 vs 系统):")
evaluator.print_schedule_gantt(
    gt_entries=gt.schedule,
    sys_entries=schedule.entries,
    gt_makespan=gt.optimal_makespan,
    sys_makespan=schedule.makespan or feedback.total_makespan
)
print("\n" + "=" * 60)
print(f"FINAL SCORE: {report.overall_score:.1f}/100")
print("=" * 60)

if report.overall_score >= 95:
    print("\nWARNING: Score is still suspiciously high!")
else:
    print("\nScore looks realistic (not 100/100)")
