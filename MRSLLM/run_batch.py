#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
批量调度运行脚本（脚本 a）

用法（极其简单）：
  python run_batch.py --mock all        # 运行全部 10 个数据集
  python run_batch.py --mock 0,1,2      # 运行 #0, #1, #2
  python run_batch.py --mock 0-4        # 运行 #0 到 #4（含）
  python run_batch.py --mock 0-4,7,9    # 混合指定

输出：
  - output/session_*.json            每个数据集统一会话 JSON（全部信息）
  - output/tech_report_*.md          每个数据集技术报告（8 章节，一键生成）
  - output/final_schedule_*.json     每个数据集调度结果（兼容旧流程）
  - output/harness_report_*.json     每个数据集 Harness 迭代历史（兼容旧流程）
  - output/evaluation_report_*.json  每个数据集评估报告（兼容旧流程）
  - output/batch_summary_*.md        ★ 批量汇总报告（Markdown 表格 + 统计）
  - output/batch_summary_*.json      批量汇总数据（JSON）
"""

import sys
import os
import json
import glob
import shutil
import argparse
import logging
from datetime import datetime
from typing import List, Tuple

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
from aggregate_reporter import AggregateReporter

# ── 批量运行时降低日志噪音 ──
logging.basicConfig(
    level=logging.WARNING,
    format='[%(asctime)s] %(levelname)-8s %(name)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
# 把子模块的 logger 也设为 WARNING，避免刷屏
for _name in ['mrta_converter', 'harness', 'task_planner', 'code_generator',
               'code_executor', 'simulator', 'evaluator']:
    logging.getLogger(_name).setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════
# 索引解析
# ══════════════════════════════════════════════════════════════════════

def _parse_indices(raw: str) -> List[int]:
    """解析索引字符串 → 整数列表

    'all'       → [0,1,2,3,4,5,6,7,8,9]
    '0,1,2'     → [0,1,2]
    '0-4'       → [0,1,2,3,4]
    '0-2,5,7-9' → [0,1,2,5,7,8,9]
    """
    if raw.lower() == 'all':
        return list(range(10))

    indices = []
    for part in raw.split(','):
        part = part.strip()
        if '-' in part:
            a, b = part.split('-', 1)
            indices.extend(range(int(a), int(b) + 1))
        else:
            indices.append(int(part))
    return sorted(set(indices))


# ══════════════════════════════════════════════════════════════════════
# 单数据集静默运行
# ══════════════════════════════════════════════════════════════════════


def _run_one(args, index: int) -> Tuple[bool, str]:
    """对单个数据集执行完整流水线，返回 (是否成功, eval_report_path)"""
    suffix = f"1p_{index:06d}"
    problem_file = f"problem_instance_{suffix}.json"
    solution_file = f"optimal_schedule_{suffix}.json"

    if not os.path.exists(problem_file) or not os.path.exists(solution_file):
        logger.error(f"数据集 {suffix} 文件缺失，跳过")
        return False, ""

    try:
        # ── 1. 加载数据 ──
        from mrta_converter import MRTABenchmarkConverter
        MRTABenchmarkConverter().convert_and_save(problem_file, solution_file)

        scene = Scene.from_json_file('config/scene.json')
        ground_truth = GroundTruth.from_json_file('config/ground_truth.json')

        # ── 2. 自动指令 ──
        n_ops = len(scene.operations)
        n_robots = len(scene.resources.robots)
        collab_ops = [op.id for op in scene.operations if len(op.allowed_robots) > 1]
        collab_hint = ""
        if collab_ops:
            collab_hint = f"，其中 {', '.join(collab_ops)} 为协作任务需多台机器人配合"
        instruction = f"完成所有 {n_ops} 个任务的调度，最小化 makespan，{n_robots} 台机器人协作{collab_hint}"

        # ── 3. LLM ──
        if args.mock:
            os.environ['LLM_PROVIDER'] = 'mock'
        llm = LLMFactory.create()

        # ── 4. 仿真器 + Harness ──
        simulator = MockSimulator(scene)
        harness = Harness(scene, llm, simulator, max_iterations=3)

        # ── 5. 执行 ──
        final_result = harness.run(instruction)

        # ── 6. 评估 ──
        evaluator = Evaluator(ground_truth, scene)
        eval_report = evaluator.evaluate(
            schedule=final_result.schedule,
            feedback=final_result.feedback or SimulationFeedback(
                results=[], total_makespan=0, success_count=0, failure_count=0, failure_details=None
            ),
            n_iterations=len(harness.iterations),
            simulator_mode="Mock Simulator"
        )

        # ── 7. 打印单数据集结果 ──
        evaluator.print_comparison_table(eval_report)
        if final_result.schedule and final_result.schedule.entries:
            evaluator.print_schedule_gantt(
                gt_entries=ground_truth.schedule,
                sys_entries=final_result.schedule.entries,
                gt_makespan=ground_truth.optimal_makespan,
                sys_makespan=final_result.schedule.makespan,
                sys_is_execution=False
            )

        # ── 8. 构建统一会话 JSON ──
        os.makedirs('output', exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        scene_tag = f"1p_{index:06d}"

        harness_data = harness.generate_report()

        # 场景摘要
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
                    'predecessors': op.predecessors,
                }
                for op in scene.operations
            ],
            'transport_edges': [
                {'from': e.from_location, 'to': e.to_location, 'distance': e.distance}
                for e in scene.cell_layout.transport_edges
            ],
        }

        # 最优基准
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

        eval_dict = evaluator.to_dict(eval_report)

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
            json.dump(session, f, indent=2, ensure_ascii=False, default=str)

        # 兼容旧流程：单独保存
        with open(f'output/harness_report_{scene_tag}_{ts}.json', 'w', encoding='utf-8') as f:
            json.dump(harness_data, f, indent=2, ensure_ascii=False, default=str)
        eval_path = evaluator.save_report(eval_report)
        if final_result.schedule:
            with open(f'output/final_schedule_{scene_tag}_{ts}.json', 'w', encoding='utf-8') as f:
                json.dump(sys_schedule, f, indent=2, ensure_ascii=False)

        # 一键生成技术报告
        try:
            from src.gen_report import ReportGenerator
            gen = ReportGenerator()
            gen.generate(session, output_dir='output', filename_tag=f'{scene_tag}_{ts}')
        except Exception as exc:
            logger.warning(f"⚠ 技术报告生成失败: {exc}")

        return final_result.success, eval_path

    except Exception as e:
        logger.error(f"数据集 {suffix} 异常: {e}")
        return False, ""


# ══════════════════════════════════════════════════════════════════════
# 主入口
# ══════════════════════════════════════════════════════════════════════

def main():
    # 清理 __pycache__，避免过期的 .pyc 干扰
    for pyc_dir in glob.glob(os.path.join(os.path.dirname(__file__), 'src', '__pycache__')):
        shutil.rmtree(pyc_dir, ignore_errors=True)

    parser = argparse.ArgumentParser(
        description='批量调度运行脚本 - 指定 ≥1 个数据集并生成汇总报告',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            '示例:\n'
            '  python run_batch.py --mock all\n'
            '  python run_batch.py --mock 0,1,2\n'
            '  python run_batch.py --mock 0-4\n'
            '  python run_batch.py --mock 0-4,7,9'
        )
    )
    parser.add_argument('indices', type=str, help='数据集索引，如 all | 0,1,2 | 0-4 | 0-2,5,7-9')
    parser.add_argument('--mock', action='store_true', help='Mock LLM（无需 API Key）')
    args = parser.parse_args()

    # ── 解析索引 ──
    indices = _parse_indices(args.indices)

    print("\n" + "=" * 60)
    print(f"  批量调度运行 - 共 {len(indices)} 个数据集")
    print(f"  索引: {indices}")
    print("=" * 60 + "\n")

    # ── 逐数据集运行 ──
    eval_paths: List[str] = []
    success_count = 0

    for i, idx in enumerate(indices):
        tag = f"1p_{idx:06d}"
        print(f"  [{i+1}/{len(indices)}] {tag} ... ", end='', flush=True)

        ok, eval_path = _run_one(args, idx)
        if ok:
            success_count += 1
            print(f"[OK]")
            if eval_path:
                eval_paths.append(eval_path)
        else:
            print(f"[X] 失败")

    print(f"\n  完成: {success_count}/{len(indices)} 成功\n")

    # ── 汇总报告 ──
    if eval_paths:
        reporter = AggregateReporter(eval_paths)
        reporter.print_summary()
        reporter.save_markdown()
        reporter.save_json()

    return 0 if success_count == len(indices) else 1


if __name__ == '__main__':
    sys.exit(main())
