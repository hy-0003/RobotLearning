#!/usr/bin/env python
# -*- coding: utf-8 -*-
# 调度器测试脚本

import sys
import os

# 修复 Windows 上的 UTF-8 编码问题
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from data_types import Scene, GroundTruth
from scheduler import SchedulingSolver
from simulator import MockSimulator


def test_scheduler():
    """测试调度器"""
    print("=" * 60)
    print("测试：调度求解器")
    print("=" * 60)

    # 加载场景
    scene = Scene.from_json_file('config/scene.json')
    ground_truth = GroundTruth.from_json_file('config/ground_truth.json')

    print(f"场景已加载：{scene.scene_id}")
    print(f"操作数：{len(scene.operations)}")
    print(f"最优 makespan（基准）：{ground_truth.optimal_makespan} 秒")
    print()

    # 创建求解器
    solver = SchedulingSolver(scene)
    print("求解器已初始化")
    print()

    # 求解调度问题
    print("求解中...")
    schedule = solver.solve()

    print(f"✓ 调度完成")
    print(f"  - 生成的 makespan: {schedule.makespan:.2f} 秒")
    print(f"  - 调度项数：{len(schedule.entries)}")
    print()

    # 显示调度详情
    print("调度详情（按时间排序）：")
    print("-" * 80)
    for entry in schedule.entries:
        print(f"  {entry.operation_id:4} (wp: {entry.workpiece_id}) | "
              f"时间: [{entry.start_time:5.1f}, {entry.end_time:5.1f}] | "
              f"机械臂: {entry.robot_id} | "
              f"资源: {', '.join(entry.resources)}")
    print()

    # 对比基准
    print("性能对比：")
    print("-" * 80)
    print(f"  生成的 makespan:   {schedule.makespan:.2f} 秒")
    print(f"  基准 makespan:     {ground_truth.optimal_makespan:.2f} 秒")
    diff = schedule.makespan - ground_truth.optimal_makespan
    if abs(diff) < 0.01:
        print(f"  ✓ 与基准一致！")
    elif diff > 0:
        print(f"  差异: +{diff:.2f} 秒（相对差异: {diff/ground_truth.optimal_makespan*100:.1f}%）")
    print()

    # 使用 MockSimulator 验证调度
    print("验证调度的可执行性（仿真）...")
    simulator = MockSimulator(scene)
    feedback = simulator.execute_schedule(schedule)

    print(f"✓ 仿真完成")
    print(f"  - 实际 makespan: {feedback.total_makespan:.2f} 秒")
    print(f"  - 成功操作数: {feedback.success_count}/{len(feedback.results)}")
    print(f"  - 失败操作数: {feedback.failure_count}")
    if feedback.failure_details:
        print(f"  - 失败类型: {list(feedback.failure_details.keys())}")
    print()

    return schedule, feedback


def main():
    print("\n")
    print("╔" + "=" * 58 + "╗")
    print("║" + " " * 15 + "调度求解器测试" + " " * 28 + "║")
    print("╚" + "=" * 58 + "╝")
    print()

    try:
        schedule, feedback = test_scheduler()

        print("=" * 60)
        print("✅ 调度器测试通过！")
        print("=" * 60)
        print()

    except Exception as e:
        print("=" * 60)
        print("❌ 测试失败！")
        print("=" * 60)
        print(f"错误: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
