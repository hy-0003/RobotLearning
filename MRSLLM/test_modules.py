#!/usr/bin/env python
# -*- coding: utf-8 -*-
# 测试脚本：验证 data_types.py 和 simulator.py 的基本功能

import sys
import os

# 修复 Windows 上的 UTF-8 编码问题
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

# 添加 src 目录到 Python 路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from data_types import Scene, GroundTruth, Schedule
from simulator import MockSimulator

def test_load_scene():
    """测试加载场景数据"""
    print("=" * 60)
    print("测试 1：加载场景数据")
    print("=" * 60)

    scene = Scene.from_json_file('config/scene.json')
    print(f"✓ 场景 ID: {scene.scene_id}")
    print(f"✓ 操作数: {len(scene.operations)}")
    print(f"✓ 工件数: {len(scene.workpieces)}")
    print(f"✓ 机械臂数: {len(scene.resources.robots)}")
    print(f"✓ 工位数: {len(scene.resources.stations)}")
    print()
    return scene


def test_load_ground_truth():
    """测试加载最优基准"""
    print("=" * 60)
    print("测试 2：加载最优基准")
    print("=" * 60)

    gt = GroundTruth.from_json_file('config/ground_truth.json')
    print(f"✓ 场景 ID: {gt.scene_id}")
    print(f"✓ 最优完成时间: {gt.optimal_makespan} 秒")
    print(f"✓ 调度项数: {len(gt.schedule)}")
    print(f"✓ 基准指标:")
    print(f"  - makespan: {gt.metrics_baseline.makespan}")
    print(f"  - 任务成功率: {gt.metrics_baseline.task_success_rate}")
    print(f"  - 机械臂平均利用率: {gt.metrics_baseline.resource_utilization.overall_robot_utilization:.4f}")
    print()
    return gt


def test_mock_simulator(scene: Scene, ground_truth: GroundTruth):
    """测试 Mock Simulator 执行"""
    print("=" * 60)
    print("测试 3：Mock Simulator 执行仿真")
    print("=" * 60)

    # 创建仿真器
    simulator = MockSimulator(scene)
    print("✓ Mock Simulator 已初始化")

    # 将最优基准转换为 Schedule 对象
    schedule = Schedule(entries=ground_truth.schedule, makespan=ground_truth.optimal_makespan)
    print(f"✓ 加载调度计划（{len(schedule.entries)} 项操作）")

    # 执行仿真
    print("  执行仿真中...")
    feedback = simulator.execute_schedule(schedule)

    print(f"✓ 仿真完成")
    print(f"  - 总完成时间: {feedback.total_makespan:.2f} 秒")
    print(f"  - 成功操作数: {feedback.success_count}/{len(feedback.results)}")
    print(f"  - 失败操作数: {feedback.failure_count}")

    if feedback.failure_details:
        print(f"  - 失败详情: {feedback.failure_details}")
    else:
        print(f"  - 无失败操作")

    print()
    return feedback


def test_world_state(simulator: MockSimulator):
    """测试获取世界状态"""
    print("=" * 60)
    print("测试 4：获取仿真后的世界状态")
    print("=" * 60)

    state = simulator.get_world_state()
    print(f"✓ 当前仿真时间: {state.current_time:.2f} 秒")
    print(f"✓ 机械臂状态:")
    for robot_id, robot_state in state.robot_states.items():
        print(f"  - {robot_id}: 位置={robot_state.current_location}, 空闲={robot_state.is_idle}")

    print(f"✓ 工件状态:")
    for wp_id, wp_state in state.workpiece_states.items():
        print(f"  - {wp_id}: 位置={wp_state.current_location}, 已完成操作数={len(wp_state.completed_operations)}")

    print()


def main():
    """主测试函数"""
    print("\n")
    print("╔" + "=" * 58 + "╗")
    print("║" + " " * 10 + "多机械臂调度系统 - 模块测试" + " " * 20 + "║")
    print("╚" + "=" * 58 + "╝")
    print()

    try:
        # 测试加载
        scene = test_load_scene()
        ground_truth = test_load_ground_truth()

        # 测试仿真
        simulator = MockSimulator(scene)
        feedback = test_mock_simulator(scene, ground_truth)

        # 测试状态
        test_world_state(simulator)

        # 保存执行日志
        os.makedirs('output', exist_ok=True)
        simulator.save_execution_log('output/test_execution_log.json')
        print("=" * 60)
        print("测试 5：保存执行日志")
        print("=" * 60)
        print("✓ 执行日志已保存到 output/test_execution_log.json")
        print()

        print("=" * 60)
        print("✅ 所有测试通过！")
        print("=" * 60)
        print()

    except Exception as e:
        print()
        print("=" * 60)
        print("❌ 测试失败！")
        print("=" * 60)
        print(f"错误: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
