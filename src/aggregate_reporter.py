#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
聚合报告生成器 - 读取多个评估报告 JSON，生成汇总表与统计
由 run_batch.py 在批量运行结束后自动调用，也可独立使用。
"""

import json
import os
import sys
from datetime import datetime
from typing import List, Dict, Optional


class AggregateReporter:
    """汇总多个数据集的评估结果，输出表格和统计"""

    def __init__(self, report_paths: List[str]):
        self.report_paths = report_paths
        self.reports: List[Dict] = []
        self._load()

    def _load(self):
        for p in self.report_paths:
            try:
                with open(p, 'r', encoding='utf-8') as f:
                    self.reports.append(json.load(f))
            except Exception as e:
                print(f"[WARN] 跳过 {p}: {e}", file=sys.stderr)

    # ──────────────── 提取工具 ────────────────

    @staticmethod
    def _short_id(scene_id: str) -> str:
        """problem_instance_1p_000000 → #000000"""
        return scene_id.replace('problem_instance_1p_', '#')

    @staticmethod
    def _pct(v: Optional[float]) -> str:
        if v is None:
            return "N/A"
        sign = "+" if v > 0 else ""
        return f"{sign}{v:.1f}%"

    # ──────────────── 终端打印 ────────────────

    def print_summary(self):
        """打印汇总表到终端"""
        if not self.reports:
            print("无评估报告可汇总。")
            return

        print("\n" + "=" * 90)
        print("  多数据集调度评估 - 汇总报告")
        print("=" * 90)

        # ── 逐行表格 ──
        header = (
            f" {'数据集':>8s} │ {'GT makespan':>12s} │ {'Sys makespan':>12s} │ "
            f"{'Δ%':>8s} │ {'成功率':>6s} │ {'利用率':>6s} │ "
            f"{'违规':>4s} │ {'评分':>4s} │ {'评价':s}"
        )
        sep = "─" * len(header.replace('│', '┼'))
        print(sep)
        print(header)
        print(sep)

        for r in self.reports:
            m = r.get('metrics_comparison', {})
            sid = self._short_id(r.get('scene_id', '?'))
            gt_ms = m.get('makespan', {}).get('baseline', 0)
            sy_ms = m.get('makespan', {}).get('actual', 0)
            d_ms = m.get('makespan', {}).get('delta_pct', 0)
            sr = m.get('task_success_rate', {}).get('actual', 0)
            ru = m.get('resource_utilization', {}).get('actual', 0)
            cv = m.get('constraint_violations', {}).get('actual', 0)
            sc = r.get('overall_score', 0)
            vd = m.get('makespan', {}).get('verdict', '')
            vd_icon = {"[OK] 达标": "✓", "[~] 略差": "~", "[X] 显著劣化": "✗"}.get(vd, vd)

            print(
                f" {sid:>8s} │ {gt_ms:>12.2f} │ {sy_ms:>12.2f} │ "
                f"{self._pct(d_ms):>8s} │ {sr:>5.0%} │ {ru:>5.1%} │ "
                f"{cv:>4.0f} │ {sc:>3d} │ {vd_icon:s}"
            )

        print(sep)

        # ── 汇总统计 ──
        self._print_stats()

    def _print_stats(self):
        """打印汇总统计行"""
        deltas = []
        scores = []
        violations = []
        success_count = 0

        for r in self.reports:
            m = r.get('metrics_comparison', {})
            d = m.get('makespan', {}).get('delta_pct')
            if d is not None:
                deltas.append(d)
            scores.append(r.get('overall_score', 0))
            v = m.get('constraint_violations', {}).get('actual', 0)
            violations.append(v)
            if m.get('task_success_rate', {}).get('actual', 1) >= 1.0:
                success_count += 1

        n = len(self.reports)
        if n == 0:
            return

        mean_d = sum(deltas) / len(deltas) if deltas else 0
        # 样本标准差
        if len(deltas) > 1:
            var_d = sum((x - mean_d) ** 2 for x in deltas) / (len(deltas) - 1)
            std_d = var_d ** 0.5
        else:
            std_d = 0

        mean_s = sum(scores) / n
        pass_rate = success_count / n * 100
        total_v = sum(violations)

        print(f"  共 {n} 个数据集")
        print(f"  makespan Δ%  均值: {self._pct(mean_d)}  标准差: {std_d:.1f}%")
        print(f"  综合评分      均值: {mean_s:.0f}/100")
        print(f"  完全成功率    {success_count}/{n} ({pass_rate:.0f}%)")
        print(f"  总违规次数    {int(total_v)}")
        print()

        # 星级
        star = self._star_rating(mean_d, pass_rate, mean_s)
        print(f"  整体评级: {star}")
        print()

    @staticmethod
    def _star_rating(mean_delta: float, pass_rate: float, mean_score: float) -> str:
        """根据统计数据给出星级评价"""
        stars = 0
        # Δ% 越小越好（负数或≤5%）
        if mean_delta <= -10:
            stars += 2
        elif mean_delta <= 5:
            stars += 1
        # 成功率
        if pass_rate >= 95:
            stars += 2
        elif pass_rate >= 80:
            stars += 1
        # 评分
        if mean_score >= 90:
            stars += 2
        elif mean_score >= 60:
            stars += 1
        return "★" * stars + "☆" * (6 - stars)

    # ──────────────── 保存 ────────────────

    def save_markdown(self, output_dir: str = 'output') -> str:
        """保存汇总报告为 Markdown，返回路径"""
        os.makedirs(output_dir, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        filepath = os.path.join(output_dir, f'batch_summary_{ts}.md')

        lines = []
        lines.append("# 多数据集调度评估 - 汇总报告")
        lines.append(f"\n生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append(f"数据集数: {len(self.reports)}\n")

        if not self.reports:
            lines.append("无数据。")
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write("\n".join(lines))
            return filepath

        # 表格
        lines.append("| 数据集 | GT makespan | Sys makespan | Δ% | 成功率 | 利用率 | 违规 | 评分 |")
        lines.append("|--------|-------------|--------------|-----|--------|--------|------|------|")

        for r in self.reports:
            m = r.get('metrics_comparison', {})
            sid = self._short_id(r.get('scene_id', '?'))
            gt_ms = m.get('makespan', {}).get('baseline', 0)
            sy_ms = m.get('makespan', {}).get('actual', 0)
            d_ms = m.get('makespan', {}).get('delta_pct', 0)
            sr = m.get('task_success_rate', {}).get('actual', 0)
            ru = m.get('resource_utilization', {}).get('actual', 0)
            cv = m.get('constraint_violations', {}).get('actual', 0)
            sc = r.get('overall_score', 0)

            lines.append(
                f"| {sid} | {gt_ms:.2f} | {sy_ms:.2f} | {self._pct(d_ms)} | "
                f"{sr:.0%} | {ru:.1%} | {cv:.0f} | {sc} |"
            )

        # 统计
        deltas = []
        scores = []
        for r in self.reports:
            m = r.get('metrics_comparison', {})
            d = m.get('makespan', {}).get('delta_pct')
            if d is not None:
                deltas.append(d)
            scores.append(r.get('overall_score', 0))

        n = len(self.reports)
        mean_d = sum(deltas) / len(deltas) if deltas else 0
        if len(deltas) > 1:
            std_d = (sum((x - mean_d) ** 2 for x in deltas) / (len(deltas) - 1)) ** 0.5
        else:
            std_d = 0
        mean_s = sum(scores) / n
        pass_rate = sum(
            1 for r in self.reports
            if r.get('metrics_comparison', {}).get('task_success_rate', {}).get('actual', 1) >= 1.0
        ) / n * 100

        lines.append(f"\n## 汇总统计\n")
        lines.append(f"- 共 **{n}** 个数据集")
        lines.append(f"- makespan Δ% 均值: **{self._pct(mean_d)}**，标准差: **{std_d:.1f}%**")
        lines.append(f"- 综合评分均值: **{mean_s:.0f}/100**")
        lines.append(f"- 完全成功率: **{pass_rate:.0f}%**")
        lines.append(f"- 整体评级: **{self._star_rating(mean_d, pass_rate, mean_s)}**")

        with open(filepath, 'w', encoding='utf-8') as f:
            f.write("\n".join(lines))

        print(f"  [OK] 汇总报告已保存: {filepath}")
        return filepath

    def save_json(self, output_dir: str = 'output') -> str:
        """保存汇总报告为 JSON"""
        os.makedirs(output_dir, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        filepath = os.path.join(output_dir, f'batch_summary_{ts}.json')

        summary = {
            'generated_at': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            'n_datasets': len(self.reports),
            'details': [],
        }

        for r in self.reports:
            m = r.get('metrics_comparison', {})
            summary['details'].append({
                'scene_id': r.get('scene_id'),
                'gt_makespan': m.get('makespan', {}).get('baseline'),
                'sys_makespan': m.get('makespan', {}).get('actual'),
                'delta_pct': m.get('makespan', {}).get('delta_pct'),
                'task_success_rate': m.get('task_success_rate', {}).get('actual'),
                'resource_utilization': m.get('resource_utilization', {}).get('actual'),
                'constraint_violations': m.get('constraint_violations', {}).get('actual'),
                'overall_score': r.get('overall_score'),
            })

        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(summary, f, indent=2, ensure_ascii=False)

        print(f"  [OK] 汇总 JSON 已保存: {filepath}")
        return filepath


# ── 独立调用入口 ──
if __name__ == '__main__':
    import glob

    # 默认读取 output/ 下所有 evaluation_report_*.json
    files = sorted(glob.glob('output/evaluation_report_eval_*.json'))
    if not files:
        print("未找到 output/evaluation_report_eval_*.json，请先运行批量任务。")
        sys.exit(1)

    reporter = AggregateReporter(files)
    reporter.print_summary()
    reporter.save_markdown()
    reporter.save_json()
