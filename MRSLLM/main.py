#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
多机械臂调度智能体系统 - 主入口

用法：
  python main.py                           # 真实 LLM，数据集 0
  python main.py --mock                    # Mock 模式，数据集 0
  python main.py --mock --problem problem_instance_1p_000005.json  # 指定数据集
"""

import sys
import os
import json
import argparse
import logging
from datetime import datetime
from dotenv import load_dotenv
load_dotenv()

if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from data_types import Scene, GroundTruth, SimulationFeedback
from llm_interface import LLMFactory
from simulator import MockSimulator
from harness import Harness
from evaluator import Evaluator

logging.basicConfig(
    level=logging.DEBUG,
    format='[%(asctime)s] %(levelname)-8s %(name)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)


def main():
    import glob
    import shutil
    for pyc_dir in glob.glob(os.path.join(os.path.dirname(__file__), 'src', '__pycache__')):
        shutil.rmtree(pyc_dir, ignore_errors=True)

    parser = argparse.ArgumentParser(
        description='多机械臂调度智能体系统',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='示例:\n  python main.py --mock\n  python main.py --mock --problem problem_instance_1p_000005.json'
    )
    parser.add_argument('--mock', action='store_true', help='Mock LLM（无需 API Key）')
    parser.add_argument('--problem', type=str, default='problem_instance_1p_000000.json',
                        help='MRTA-Benchmark 问题文件（默认数据集 0）')
    parser.add_argument('--solution', type=str, default='optimal_schedule_1p_000000.json',
                        help='MRTA-Benchmark 最优解文件')
    parser.add_argument('--instruction', type=str, default=None,
                        help='自然语言指令（不指定则从数据自动推断）')
    args = parser.parse_args()

    print("\n")
    print("╔" + "=" * 68 + "╗")
    print("║" + " " * 15 + "多机械臂调度智能体系统 - Harness 闭环框架" + " " * 15 + "║")
    print("╚" + "=" * 68 + "╝")
    print()

    try:
        return _run_single(args, args.problem, args.solution, args.instruction)

    except Exception as e:
        logger.error(f"失败: {e}")
        import traceback
        traceback.print_exc()
        return 1


def _auto_instruction(scene: Scene) -> str:
    """从 scene 数据自动推断自然语言指令"""
    n_ops = len(scene.operations)
    n_robots = len(scene.resources.robots)
    collab_ops = [op.id for op in scene.operations if len(op.allowed_robots) > 1]
    collab_hint = ""
    if collab_ops:
        collab_hint = f"，其中 {', '.join(collab_ops)} 为协作任务需多台机器人配合"
    return f"完成所有 {n_ops} 个任务的调度，最小化 makespan，{n_robots} 台机器人协作{collab_hint}"


def _run_single(args, problem_file: str, solution_file: str, instruction: str = None) -> int:
    """执行单次调度流水线，返回 exit code"""
    # ── 1. 加载数据 ──
    logger.info(f"加载 MRTA-Benchmark: {problem_file}")
    from mrta_converter import MRTABenchmarkConverter
    MRTABenchmarkConverter().convert_and_save(problem_file, solution_file)

    scene = Scene.from_json_file('config/scene.json')
    logger.info(f"✓ 场景: {scene.scene_id}")

    ground_truth = GroundTruth.from_json_file('config/ground_truth.json')
    logger.info(f"✓ 基准 makespan: {ground_truth.optimal_makespan:.2f}s")

    # 自动推断指令
    if instruction is None:
        instruction = _auto_instruction(scene)
        logger.info(f"✓ 自动指令: '{instruction}'")

    # ── 2. 初始化 LLM ──
    if args.mock:
        os.environ['LLM_PROVIDER'] = 'mock'
    llm = LLMFactory.create()
    logger.info(f"✓ LLM: {type(llm).__name__}")

    # ── 3. 初始化仿真器 ──
    simulator = MockSimulator(scene)
    logger.info("✓ 仿真器: MockSimulator")

    # ── 4. 创建 Harness ──
    harness = Harness(scene, llm, simulator, max_iterations=3)

    # ── 5. 执行 ──
    logger.info(f"指令: '{instruction}'")
    print()
    final_result = harness.run(instruction)

    # ── 6. 输出 ──
    print()
    print("=" * 70)
    print("Harness 执行结果")
    print("=" * 70)
    status_icon = "[OK]" if final_result.success else "[X]"
    print(f"状态: {status_icon} {'成功' if final_result.success else '失败'}")
    print(f"迭代: {len(harness.iterations)}")
    if final_result.feedback:
        if final_result.schedule:
            print(f"计划 makespan (含运输): {final_result.schedule.makespan:.2f}s")
        print(f"代码执行时钟: {final_result.feedback.total_makespan:.2f}s")
        print(f"成功率: {final_result.feedback.success_count}/{len(final_result.feedback.results)}")

    # ── 7. 对比评估 ──
    evaluator = Evaluator(ground_truth, scene)
    eval_report = evaluator.evaluate(
        schedule=final_result.schedule,
        feedback=final_result.feedback or SimulationFeedback(
            results=[], total_makespan=0, success_count=0, failure_count=0, failure_details=None
        ),
        n_iterations=len(harness.iterations),
        simulator_mode="Mock Simulator"
    )
    evaluator.print_comparison_table(eval_report)

    # ── 7.5 甘特图对比 ──
    if final_result.schedule and final_result.schedule.entries:
        # 使用调度器生成的 schedule entries（含正确的操作 ID 和机械臂分配），
        # 而非代码执行原语（primitive）结果，避免甘特图混乱。
        # sys_is_execution=False 让 evaluator 根据 transport_edges 模拟运输间隙。
        evaluator.print_schedule_gantt(
            gt_entries=ground_truth.schedule,
            sys_entries=final_result.schedule.entries,
            gt_makespan=ground_truth.optimal_makespan,
            sys_makespan=final_result.schedule.makespan,
            sys_is_execution=False
        )

    # ── 8. 构建统一会话 JSON（供一键生成技术报告） ──
    os.makedirs('output', exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    scene_tag = scene.scene_id.replace('problem_instance_', '')

    harness_data = harness.generate_report()

    # 场景摘要（提取关键信息，避免重复嵌入完整 scene.json）
    scene_summary = {
        'description': scene.description,
        'n_operations': len(scene.operations),
        'n_robots': len(scene.resources.robots),
        'n_workpieces': len(scene.workpieces),
        'robot_names': [
            {'id': r.id, 'name': r.name, 'tools': r.tools}
            for r in scene.resources.robots
        ],
        'operations': [
            {
                'id': op.id, 'type': op.type, 'workpiece_id': op.workpiece_id,
                'duration': op.duration, 'allowed_robots': op.allowed_robots,
                'dependencies': op.predecessors
            }
            for op in scene.operations
        ],
        'transport_edges': [
            {'from': e.from_location, 'to': e.to_location, 'time': e.distance}
            for e in scene.cell_layout.transport_edges
        ],
    }

    # 最优基准摘要
    gt_summary = {
        'optimal_makespan': ground_truth.optimal_makespan,
        'n_entries': len(ground_truth.schedule),
        'schedule': [
            {'operation_id': e.operation_id, 'robot_id': e.robot_id,
             'start_time': e.start_time, 'end_time': e.end_time}
            for e in ground_truth.schedule
        ],
    }

    # 系统调度
    sys_schedule = None
    if final_result.schedule:
        sys_schedule = {
            'entries': [
                {'operation_id': e.operation_id, 'workpiece_id': e.workpiece_id,
                 'robot_id': e.robot_id, 'start_time': e.start_time,
                 'end_time': e.end_time, 'resources': e.resources}
                for e in final_result.schedule.entries
            ],
            'makespan': final_result.schedule.makespan,
        }

    # 甘特图文本
    gantt_text = ""
    if final_result.schedule and final_result.schedule.entries:
        gantt_text = evaluator.render_schedule_gantt(
            gt_entries=ground_truth.schedule,
            sys_entries=final_result.schedule.entries,
            gt_makespan=ground_truth.optimal_makespan,
            sys_makespan=final_result.schedule.makespan,
            sys_is_execution=False,
        )

    # 评估报告 dict
    eval_dict = evaluator.to_dict(eval_report)

    # 拼接完整会话
    session = {
        'meta': {
            'generated_at': datetime.now().isoformat(),
            'scene_id': scene.scene_id,
            'scene_tag': scene_tag,
            'success': final_result.success,
            'n_iterations': len(harness.iterations),
        },
        'scene_summary': scene_summary,
        'ground_truth': gt_summary,
        'harness_report': harness_data,
        'evaluation_report': eval_dict,
        'final_schedule': sys_schedule,
        'gantt_chart': gantt_text,
    }

    session_path = f'output/session_{scene_tag}_{ts}.json'
    with open(session_path, 'w', encoding='utf-8') as f:
        json.dump(session, f, indent=2, ensure_ascii=False)
    logger.info(f"✓ 会话 JSON 已保存: {session_path}")

    # 同时保存单独的 harness / eval / schedule 副本（兼容旧流程）
    with open(f'output/harness_report_{scene_tag}_{ts}.json', 'w', encoding='utf-8') as f:
        json.dump(harness_data, f, indent=2, ensure_ascii=False)
    evaluator.save_report(eval_report)
    if final_result.schedule:
        with open(f'output/final_schedule_{scene_tag}_{ts}.json', 'w', encoding='utf-8') as f:
            json.dump(sys_schedule, f, indent=2, ensure_ascii=False)

    # ── 9. 一键生成技术报告 ──
    try:
        from src.gen_report import ReportGenerator
        gen = ReportGenerator()
        report_path = gen.generate(session, output_dir='output', filename_tag=f'{scene_tag}_{ts}')
        logger.info(f"✓ 技术报告已生成: {report_path}")
    except Exception as exc:
        logger.warning(f"⚠ 技术报告生成失败: {exc}")

    logger.info(f"✓ 输出已保存到 output/")
    print()
    return 0 if final_result.success else 1


if __name__ == '__main__':
    sys.exit(main())
