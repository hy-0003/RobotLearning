#!/usr/bin/env python
"""gen_report.py - 从会话 JSON 一键生成技术报告 (Markdown)

用法:
  # 从命令行
  python src/gen_report.py --session output/session_1p_000003_20250623_001326.json

  # 从代码调用
  from src.gen_report import ReportGenerator
  gen = ReportGenerator()
  gen.generate(session_dict, output_dir='output')
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional


class ReportGenerator:
    """从会话 JSON 生成完整技术报告 (Markdown)。"""

    # ── 公共入口 ──

    def generate(self, session: dict, output_dir: str = 'output',
                 filename_tag: Optional[str] = None) -> str:
        """生成 Markdown 报告并返回文件路径。"""
        os.makedirs(output_dir, exist_ok=True)

        tag = filename_tag or session.get('meta', {}).get('scene_tag', 'report')
        out_path = os.path.join(output_dir, f'tech_report_{tag}.md')

        sections: List[str] = []
        sections.append(self._render_header(session))
        sections.append(self._render_toc())
        sections.append(self._render_scene_overview(session))
        sections.append(self._render_execution_flow(session))
        sections.append(self._render_llm_reasoning(session))
        sections.append(self._render_generated_code(session))
        sections.append(self._render_gantt(session))
        sections.append(self._render_performance_metrics(session))
        sections.append(self._render_failure_analysis(session))
        sections.append(self._render_conclusion(session))

        report_md = "\n\n".join(sections)

        with open(out_path, 'w', encoding='utf-8') as f:
            f.write(report_md)

        return out_path

    # ── 各章节渲染 ──

    def _render_header(self, s: dict) -> str:
        meta = s.get('meta', {})
        scene_id = meta.get('scene_id', 'unknown')
        success = "✅ 成功" if meta.get('success') else "❌ 失败"
        n_iters = meta.get('n_iterations', 0)
        gen_at = meta.get('generated_at', '')

        lines = [
            f"# 🤖 多机器人调度技术报告",
            "",
            f"| 项目 | 详情 |",
            f"|------|------|",
            f"| 场景 | `{scene_id}` |",
            f"| 执行状态 | {success} |",
            f"| Harness 迭代次数 | {n_iters} |",
            f"| 生成时间 | {gen_at} |",
            "",
            "---",
        ]
        return "\n".join(lines)

    def _render_toc(self) -> str:
        return "\n".join([
            "## 📑 目录",
            "",
            "1. [产线场景概览](#1-产线场景概览)",
            "2. [执行流程](#2-执行流程)",
            "3. [LLM 推理链](#3-llm-推理链)",
            "4. [生成代码](#4-生成代码)",
            "5. [调度甘特图](#5-调度甘特图)",
            "6. [性能指标对比](#6-性能指标对比)",
            "7. [失败分析](#7-失败分析)",
            "8. [总结与建议](#8-总结与建议)",
            "",
            "---",
        ])

    # ── 1. 产线场景概览 ──

    def _render_scene_overview(self, s: dict) -> str:
        ss = s.get('scene_summary', {})
        ops = ss.get('operations', [])
        robots = ss.get('robot_names', [])
        transport = ss.get('transport_edges', [])

        lines = [
            "## 1. 产线场景概览",
            "",
            f"> {ss.get('description', '无描述')}",
            "",
            f"| 维度 | 数量 |",
            f"|------|------|",
            f"| 操作数 | {ss.get('n_operations', 0)} |",
            f"| 机器人数 | {ss.get('n_robots', 0)} |",
            f"| 工件数 | {ss.get('n_workpieces', 0)} |",
            "",
            "### 🤖 机器人资源",
            "",
        ]

        if robots:
            lines.append("| 机器人 ID | 名称 | 工具 |")
            lines.append("|-----------|------|------|")
            for r in robots:
                tools = ', '.join(r.get('tools', []))
                lines.append(f"| `{r['id']}` | {r['name']} | {tools} |")

        lines.append("")
        lines.append("### ⚙️ 操作定义")
        lines.append("")
        if ops:
            lines.append("| 操作 ID | 类型 | 工件 | 耗时(s) | 允许机器人 | 前置依赖 |")
            lines.append("|---------|------|------|---------|-----------|----------|")
            for op in ops:
                deps = ', '.join(f'`{d}`' for d in op.get('dependencies', [])) or '—'
                allowed = ', '.join(f'`{r}`' for r in op.get('allowed_robots', []))
                lines.append(
                    f"| `{op['id']}` | {op.get('type','?')} | {op.get('workpiece_id','?')} "
                    f"| {op.get('duration',0):.0f} | {allowed} | {deps} |"
                )

        lines.append("")
        lines.append("### 🚛 运输时间矩阵")
        lines.append("")
        if transport:
            lines.append("| 起点 | 终点 | 耗时(s) |")
            lines.append("|------|------|---------|")
            for t in transport:
                lines.append(f"| {t['from']} | {t['to']} | {t.get('distance', t.get('time', '?')):.1f} |")

        lines.append("")
        lines.append("---")
        return "\n".join(lines)

    # ── 2. 执行流程 ──

    def _render_execution_flow(self, s: dict) -> str:
        hr = s.get('harness_report', {})
        iterations = hr.get('iterations', [])

        lines = [
            "## 2. 执行流程",
            "",
            f"共 {len(iterations)} 次 Harness 迭代。",
            "",
        ]

        for it in iterations:
            i = it.get('iteration_num', '?')
            success = it.get('success', False)
            failure_reason = it.get('failure_reason', '')
            schedule = it.get('schedule', {})
            feedback = it.get('feedback', {})

            status_icon = "✅" if success else "❌"
            lines.append(f"### 迭代 {i} {status_icon}")
            lines.append("")

            # 调度表摘要（从 schedule.entries 取）
            schedule_entries = schedule.get('entries', []) if schedule else []
            if schedule_entries:
                lines.append(f"**调度表** ({len(schedule_entries)} 条):")
                lines.append("")
                lines.append("| 操作 ID | 工件 | 指派机器人 | 开始时间 | 结束时间 |")
                lines.append("|---------|------|-----------|---------|---------|")
                for entry in schedule_entries:
                    lines.append(
                        f"| `{entry.get('operation_id','?')}` "
                        f"| {entry.get('workpiece_id','?')} "
                        f"| `{entry.get('robot_id','?')}` "
                        f"| {entry.get('start_time',0):.1f} "
                        f"| {entry.get('end_time',0):.1f} |"
                    )
                lines.append("")

            # 执行结果
            if feedback:
                makespan = feedback.get('total_makespan', 0)
                succ = feedback.get('success_count', 0)
                fail = feedback.get('failure_count', 0)
                lines.append(f"**执行结果**: makespan = {makespan:.1f}s, "
                             f"成功 {succ} / 失败 {fail}")
                lines.append("")

            # 失败原因
            if failure_reason:
                lines.append(f"**失败原因**:")
                lines.append("")
                lines.append("```")
                lines.append(failure_reason.strip())
                lines.append("```")
                lines.append("")

            lines.append("---")

        return "\n".join(lines)

    # ── 3. LLM 推理链 ──

    def _render_llm_reasoning(self, s: dict) -> str:
        hr = s.get('harness_report', {})
        iterations = hr.get('iterations', [])

        lines = [
            "## 3. LLM 推理链",
            "",
        ]

        has_reasoning = False
        for it in iterations:
            i = it.get('iteration_num', '?')

            # 任务规划推理
            task_plan = it.get('task_plan', {})
            reasoning = task_plan.get('reasoning', '') if task_plan else ''

            if reasoning:
                has_reasoning = True
                lines.append(f"### 迭代 {i} — 任务规划推理")
                lines.append("")
                lines.append("```")
                lines.append(reasoning.strip())
                lines.append("```")
                lines.append("")

            # 代码生成解释
            codes = it.get('generated_codes', {})
            if codes:
                for robot_id, code_info in codes.items():
                    explanation = ""
                    if isinstance(code_info, dict):
                        explanation = code_info.get('explanation', '')
                    elif hasattr(code_info, 'explanation'):
                        explanation = code_info.explanation
                    if explanation:
                        has_reasoning = True
                        lines.append(f"### 迭代 {i} — `{robot_id}` 代码解释")
                        lines.append("")
                        lines.append("```")
                        lines.append(explanation.strip())
                        lines.append("```")
                        lines.append("")

            # 失败原因（作为分析推理）
            failure_reason = it.get('failure_reason', '')
            if failure_reason:
                has_reasoning = True
                lines.append(f"### 迭代 {i} — 执行分析")
                lines.append("")
                lines.append("```")
                lines.append(failure_reason.strip())
                lines.append("```")
                lines.append("")

        if not has_reasoning:
            lines.append("> 未记录 LLM 推理链，请确保 `harness_report.json` 中包含 `reasoning` / `explanation` 字段。")

        lines.append("---")
        return "\n".join(lines)

    # ── 4. 生成代码 ──

    def _render_generated_code(self, s: dict) -> str:
        hr = s.get('harness_report', {})
        iterations = hr.get('iterations', [])

        lines = [
            "## 4. 生成代码",
            "",
        ]

        has_code = False
        for it in iterations:
            i = it.get('iteration_num', '?')
            codes = it.get('generated_codes', {})
            if not codes:
                continue

            has_code = True
            lines.append(f"### 迭代 {i}")
            lines.append("")

            for robot_id, code_info in sorted(codes.items()):
                if isinstance(code_info, dict):
                    code_text = code_info.get('code', '')
                elif hasattr(code_info, 'code'):
                    code_text = code_info.code
                else:
                    code_text = str(code_info)

                lines.append(f"**`{robot_id}`**:")
                lines.append("")
                lines.append("```python")
                lines.append(code_text.strip() if code_text else "# (无代码)")
                lines.append("```")
                lines.append("")

        if not has_code:
            lines.append("> 未生成代码。")

        lines.append("---")
        return "\n".join(lines)

    # ── 5. 调度甘特图 ──

    def _render_gantt(self, s: dict) -> str:
        gantt_text = s.get('gantt_chart', '')

        lines = [
            "## 5. 调度甘特图",
            "",
        ]

        if gantt_text:
            lines.append("左侧为 MRTA-Benchmark 最优调度，右侧为系统生成调度。")
            lines.append("")
            lines.append("```")
            lines.append(gantt_text)
            lines.append("```")
        else:
            lines.append("> 无甘特图数据。")

        lines.append("")
        lines.append("---")
        return "\n".join(lines)

    # ── 6. 性能指标对比 ──

    def _render_performance_metrics(self, s: dict) -> str:
        ev = s.get('evaluation_report', {})
        metrics = ev.get('metrics_comparison', {})

        lines = [
            "## 6. 性能指标对比",
            "",
        ]

        # 综合评分
        score = ev.get('overall_score', 0)
        verdict = ev.get('overall_verdict', '?')
        score_bar = "█" * int(score / 5) + "░" * (20 - int(score / 5))

        lines.append(f"**综合评分**: [{score_bar}] {score:.0f} / 100")
        lines.append(f"**综合评价**: {verdict}")
        lines.append("")

        # 指标表
        mk = metrics.get('makespan', {})
        tsr = metrics.get('task_success_rate', {})
        ru = metrics.get('resource_utilization', {})
        cv = metrics.get('constraint_violations', {})

        lines.append("| 指标 | 基准值 | 实际值 | 差值/差值% | 判定 |")
        lines.append("|------|--------|--------|-----------|------|")

        # makespan
        b, a = mk.get('baseline', 0), mk.get('actual', 0)
        dp = mk.get('delta_pct', 0)
        lines.append(f"| ⏱️ makespan ↓ | {b:.2f}s | {a:.2f}s | {a-b:+.2f}s / {dp:+.1f}% | {mk.get('verdict','?')} |")

        # task success rate
        b, a = tsr.get('baseline', 0), tsr.get('actual', 0)
        delta = a - b
        lines.append(f"| ✅ 任务成功率 ↑ | {b:.2%} | {a:.2%} | {delta:+.2%} | {tsr.get('verdict','?')} |")

        # resource utilization
        b, a = ru.get('baseline', 0), ru.get('actual', 0)
        dp = ru.get('delta_pct', 0)
        lines.append(f"| 📊 资源利用率 ↑ | {b:.4f} | {a:.4f} | {dp:+.1f}% | {ru.get('verdict','?')} |")

        # constraint violations
        b, a = cv.get('baseline', 0), cv.get('actual', 0)
        lines.append(f"| 🚫 约束违例 ↓ | {b} | {a} | {a-b:+d} | {cv.get('verdict','?')} |")

        lines.append("")
        lines.append(f"*注: {ev.get('note', '')}*" if ev.get('note') else "")

        lines.append("")
        lines.append("---")
        return "\n".join(lines)

    # ── 7. 失败分析 ──

    def _render_failure_analysis(self, s: dict) -> str:
        hr = s.get('harness_report', {})
        iterations = hr.get('iterations', [])
        meta = s.get('meta', {})

        lines = [
            "## 7. 失败分析",
            "",
        ]

        if meta.get('success'):
            lines.append("✅ 本次运行成功，无失败。")
            lines.append("")
            lines.append("---")
            return "\n".join(lines)

        # 收集所有失败信息
        failures: List[dict] = []
        for it in iterations:
            exec_data = it.get('execution_result', {})
            failure_details = exec_data.get('failure_details', [])
            if failure_details:
                for fd in failure_details:
                    fd = fd.copy()
                    fd['_iteration'] = it.get('iteration', '?')
                    failures.append(fd)

        if not failures:
            lines.append("> 未找到详细失败信息。")
            lines.append("")
            lines.append("---")
            return "\n".join(lines)

        lines.append(f"共 {len(failures)} 个失败项：")
        lines.append("")

        for fd in failures:
            it = fd.pop('_iteration', '?')
            lines.append(f"### 迭代 {it}")
            lines.append("")
            for key, val in fd.items():
                lines.append(f"- **{key}**: {val}")
            lines.append("")

        lines.append("---")
        return "\n".join(lines)

    # ── 8. 总结 ──

    def _render_conclusion(self, s: dict) -> str:
        ev = s.get('evaluation_report', {})
        meta = s.get('meta', {})
        makespan_m = ev.get('metrics_comparison', {}).get('makespan', {})

        lines = [
            "## 8. 总结与建议",
            "",
        ]

        # 核心结论
        delta_pct = makespan_m.get('delta_pct', 0)
        if abs(delta_pct) < 1.0:
            gap = "几乎无差距 (<1%)"
        elif delta_pct < 5.0:
            gap = f"略高于最优 ({delta_pct:.1f}%)"
        elif delta_pct < 15.0:
            gap = f"有显著差距 ({delta_pct:.1f}%)"
        else:
            gap = f"差距较大 ({delta_pct:.1f}%)"

        lines.append(f"### 核心结论")
        lines.append(f"")
        lines.append(f"- 场景 `{meta.get('scene_id', '?')}` 经过 {meta.get('n_iterations', 0)} 次迭代后，")
        lines.append(f"  系统 makespan 与最优基准相比：**{gap}**。")
        lines.append(f"- 综合评分：{ev.get('overall_score', 0):.0f}/100 — {ev.get('overall_verdict', '?')}")
        lines.append("")

        # 改进建议
        lines.append("### 改进方向")
        lines.append("")
        if delta_pct > 5:
            lines.append("- 检查 LLM 任务分解粒度，是否存在可合并的串行操作。")
            lines.append("- 优化机械臂负载均衡，避免某台机器人成为瓶颈。")
            lines.append("- 检查运输路径规划，减少不必要的移动。")
        lines.append("- 增加 Harness 重规划迭代次数以进一步优化调度。")
        lines.append("- 在重规划 prompt 中加入更具体的瓶颈分析提示。")

        lines.append("")
        lines.append("---")
        lines.append("")
        lines.append(f"*报告由 `gen_report.py` 自动生成于 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*")
        return "\n".join(lines)


# =========================================================================
# CLI
# =========================================================================

def main():
    parser = argparse.ArgumentParser(
        description='从会话 JSON 一键生成技术报告 (Markdown)')
    parser.add_argument('--session', required=True,
                        help='会话 JSON 文件路径 (如 output/session_1p_000003_xxx.json)')
    parser.add_argument('--output', default='output',
                        help='输出目录 (默认: output)')
    args = parser.parse_args()

    with open(args.session, 'r', encoding='utf-8') as f:
        session = json.load(f)

    gen = ReportGenerator()
    tag = os.path.splitext(os.path.basename(args.session))[0]
    # 去掉 session_ 前缀
    if tag.startswith('session_'):
        tag = tag[len('session_'):]
    out_path = gen.generate(session, output_dir=args.output, filename_tag=tag)

    print(f"✅ 技术报告已生成: {out_path}")


if __name__ == '__main__':
    main()
