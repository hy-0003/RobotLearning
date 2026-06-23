# Harness 闭环框架模块
# 实现完整的闭环控制逻辑：规划 -> 执行 -> 反馈 -> 分析 -> 重规划

import json
import logging
from typing import List, Optional, Dict, Any
from dataclasses import dataclass, asdict

from data_types import (
    Scene, Schedule, SimulationFeedback, ExecutionResult,
    ScheduleEntry
)
from llm_interface import LLMInterface
from task_planner import TaskPlanner
from code_generator import CodeGenerator
from scheduler import SchedulingSolver
from simulator import Simulator, MockSimulator

logger = logging.getLogger(__name__)


@dataclass
class HarnessIteration:
    """Harness 的一次迭代结果"""
    iteration_num: int                  # 迭代次数
    task_plan: Any                      # 任务计划
    schedule: Optional[Schedule] = None # 调度计划
    generated_codes: Optional[Dict] = None  # 生成的代码
    feedback: Optional[SimulationFeedback] = None  # 执行反馈
    success: bool = False               # 是否成功
    failure_reason: Optional[str] = None  # 失败原因


class Harness:
    """
    Harness 闭环框架

    工作流程：
    1. [规划阶段] LLM 分解任务 -> 分配机械臂 -> 调度排序 -> 生成代码
    2. [执行阶段] 仿真器执行操作序列（并行操作并发执行）
    3. [反馈阶段] 收集所有操作的执行结果
    4. [分析阶段] 分析结果：是否全部成功
    5. [重规划阶段] 如果失败，告知 LLM 失败信息，重新规划（最多重规划 N 次）

    这是一个主动的闭环控制器，而不是简单的 try-catch。
    """

    def __init__(self,
                 scene: Scene,
                 llm: LLMInterface,
                 simulator: Optional[Simulator] = None,
                 max_iterations: int = 3):
        """
        初始化 Harness

        参数：
            scene: 产线场景配置
            llm: LLM 接口实例
            simulator: 仿真器实例（如果为 None，则使用 MockSimulator）
            max_iterations: 最多重规划次数
        """
        self.scene = scene
        self.llm = llm
        self.simulator = simulator or MockSimulator(scene)
        self.max_iterations = max_iterations

        # 初始化各个组件
        self.task_planner = TaskPlanner(scene, llm)
        self.code_generator = CodeGenerator(scene, llm)
        self.scheduler = SchedulingSolver(scene)

        # 迭代历史
        self.iterations: List[HarnessIteration] = []

        # 跨迭代的失败上下文（注入到代码生成 prompt 中）
        self.last_failure_context: Optional[str] = None

    def run(self, instruction: str) -> HarnessIteration:
        """
        执行 Harness 闭环

        参数：
            instruction: 自然语言指令

        返回：
            最终的迭代结果（包含完整的规划、执行、反馈信息）
        """
        logger.info(f"开始 Harness 执行: {instruction}")
        logger.info(f"最多迭代次数: {self.max_iterations}")

        # 第一次规划
        for iteration_num in range(1, self.max_iterations + 1):
            logger.info(f"\n{'='*60}")
            logger.info(f"迭代 {iteration_num}/{self.max_iterations}")
            logger.info(f"{'='*60}")

            # [规划阶段]
            iteration_result = self._planning_phase(instruction, iteration_num,
                                                    self.last_failure_context)

            if iteration_result is None:
                continue

            # [执行阶段]
            self._execution_phase(iteration_result)

            # [分析阶段]
            success, failure_info = self._analysis_phase(iteration_result)

            iteration_result.success = success

            if success:
                logger.info(f"✅ 迭代 {iteration_num} 成功完成")
                self.iterations.append(iteration_result)
                return iteration_result
            else:
                logger.warning(f"⚠️  迭代 {iteration_num} 失败: {failure_info}")
                iteration_result.failure_reason = failure_info

                # [重规划阶段]
                if iteration_num < self.max_iterations:
                    instruction = self._replanning_phase(instruction, iteration_result)

                self.iterations.append(iteration_result)

        # 所有迭代都失败了
        logger.error(f"❌ 经过 {self.max_iterations} 次迭代，仍未成功完成任务")
        return self.iterations[-1]

    def _planning_phase(self, instruction: str, iteration_num: int,
                        failure_context: Optional[str] = None) -> Optional[HarnessIteration]:
        """
        规划阶段

        1. 调用 TaskPlanner 分解任务
        2. 调用 Scheduler 生成调度
        3. 调用 CodeGenerator 生成代码

        参数：
            instruction: 指令
            iteration_num: 迭代次数
            failure_context: 上次失败的分析上下文（注入代码生成 prompt）

        返回：
            HarnessIteration 对象，或 None 表示规划失败
        """
        logger.info("进入规划阶段...")

        # 任务分解
        logger.info("  1. 任务分解...")
        try:
            task_plan = self.task_planner.plan(instruction)
            logger.info(f"     ✓ 分解完成: {len(task_plan.operations)} 个操作")
        except Exception as e:
            logger.error(f"     ✗ 任务分解失败: {e}")
            return None

        # 调度
        logger.info("  2. 生成调度...")
        try:
            # 将 TaskPlanner 的机械臂分配传递给调度器
            schedule = self.scheduler.solve(
                robot_assignments=task_plan.robot_assignments if task_plan else None
            )
            logger.info(f"     ✓ 调度完成: makespan(含运输) = {schedule.makespan:.2f}s")
        except Exception as e:
            logger.error(f"     ✗ 调度生成失败: {e}")
            return None

        # 代码生成
        logger.info("  3. 生成机械臂行为代码...")
        try:
            generated_codes = self.code_generator.generate(schedule.entries, failure_context)
            logger.info(f"     ✓ 代码生成完成: {len(generated_codes)} 个机械臂")
        except Exception as e:
            logger.error(f"     ✗ 代码生成失败: {e}")
            return None

        # 创建迭代结果
        iteration_result = HarnessIteration(
            iteration_num=iteration_num,
            task_plan=task_plan,
            schedule=schedule,
            generated_codes=generated_codes
        )

        logger.info("规划阶段完成")

        return iteration_result

    def _execution_phase(self, iteration_result: HarnessIteration):
        """
        执行阶段

        优先使用 LLM 生成的行为代码执行（真正的代码执行路径），
        如果代码执行失败则回退到传统 schedule 路径。

        参数：
            iteration_result: 迭代结果对象
        """
        logger.info("进入执行阶段...")

        if iteration_result.schedule is None:
            logger.error("调度计划为空，无法执行")
            return

        logger.info(f"  执行 {len(iteration_result.schedule.entries)} 个操作...")

        # 重置仿真器
        self.simulator.reset()

        # ── 真正的代码执行路径 ──
        if iteration_result.generated_codes:
            logger.info(f"  🚀 使用代码执行路径: {len(iteration_result.generated_codes)} 台机械臂的行为代码")
            feedback = self.simulator.execute_with_code(
                iteration_result.schedule,
                iteration_result.generated_codes
            )
        else:
            logger.warning("  没有生成代码，回退到 schedule 路径")
            feedback = self.simulator.execute_schedule(iteration_result.schedule)

        iteration_result.feedback = feedback

        # 输出执行摘要
        logger.info(f"  ✓ 执行完成")
        logger.info(f"    - 计划 makespan (含运输): {iteration_result.schedule.makespan:.2f}s")
        logger.info(f"    - 代码执行时钟: {feedback.total_makespan:.2f}s")
        logger.info(f"    - 成功操作: {feedback.success_count}/{len(feedback.results)}")
        logger.info(f"    - 失败操作: {feedback.failure_count}")
        if 'execution_traces' in feedback.failure_details:
            logger.info(f"    - 代码执行轨迹: {len(feedback.failure_details['execution_traces'])} 台机械臂")

        logger.info("执行阶段完成")

    def _analysis_phase(self, iteration_result: HarnessIteration) -> tuple:
        """
        分析阶段（深度分析）

        不仅判断成功/失败，还对失败进行分类：
        - 资源冲突：工位/工具被占用
        - 超时：操作超过时限
        - 执行错误：随机失败或代码逻辑错误
        - 代码级错误：语法/运行时异常（仅在代码执行路径）

        返回：
            (success, failure_info) 元组
            - success: 是否所有操作都成功
            - failure_info: 结构化的失败信息摘要
        """
        logger.info("进入分析阶段...")

        if iteration_result.feedback is None:
            logger.error("反馈为空")
            return False, "反馈为空"

        feedback = iteration_result.feedback
        success = feedback.failure_count == 0

        if success:
            logger.info("  ✓ 所有操作都成功执行")
            failure_info = ""
        else:
            logger.warning(f"  ⚠️  有 {feedback.failure_count} 个操作失败")

            # 结构化失败分类
            failure_classification = self._classify_failures(feedback)
            logger.warning(f"  失败分类: {failure_classification}")

            # 代码执行轨迹分析
            if 'execution_traces' in feedback.failure_details:
                trace_issues = self._analyze_execution_traces(
                    feedback.failure_details['execution_traces']
                )
                failure_info = self._format_failure_info(failure_classification, trace_issues)
            else:
                failure_info = self._summarize_failures(feedback)

            logger.warning(f"  失败摘要: {failure_info}")

        logger.info("分析阶段完成")

        return success, failure_info

    def _classify_failures(self, feedback: SimulationFeedback) -> Dict[str, Any]:
        """
        对失败进行结构化分类

        返回分类字典：
        {
            "resource_conflict": {"count": N, "operations": [...]},
            "timeout": {"count": N, "operations": [...]},
            "error": {"count": N, "operations": [...]},
            "code_execution_error": {...}
        }
        """
        classification = {}

        # 从 failure_details 中提取
        for failure_type, details in feedback.failure_details.items():
            if failure_type == 'execution_traces':
                continue  # 单独处理
            if isinstance(details, list):
                classification[failure_type] = {
                    'count': len(details),
                    'operations': [
                        d.get('operation_id', '?') for d in details
                    ]
                }
            elif isinstance(details, dict):
                classification[failure_type] = details

        return classification

    def _analyze_execution_traces(self, traces: Dict) -> List[str]:
        """分析代码执行轨迹中的问题。

        将所有失败如实汇报，不做"随机噪声"假设——每次失败都需要 LLM 重新审视调度策略。
        """
        issues = []
        for robot_id, trace in traces.items():
            if not trace.get('success', False):
                err = trace.get('error')
                if err:
                    issues.append(f"机械臂 {robot_id} 代码执行失败: {err}")
                else:
                    failed_prims = [
                        c for c in trace.get('primitive_log', [])
                        if c.get('status') != 'success'
                    ]
                    for c in failed_prims:
                        issues.append(
                            f"机械臂 {robot_id} 第{c.get('seq')}步 "
                            f"({c.get('primitive')}): {c.get('status')} - {c.get('error','')}"
                        )
                continue
            for call in trace.get('primitive_log', []):
                if call.get('status') != 'success':
                    issues.append(
                        f"机械臂 {robot_id} 第{call.get('seq')}步 "
                        f"({call.get('primitive')}): {call.get('status')} - {call.get('error','')}"
                    )
        return issues

    def _format_failure_info(self, classification: Dict, trace_issues: List[str]) -> str:
        """格式化失败信息供 LLM 使用"""
        parts = []

        type_names = {
            'resource_conflict': '资源冲突',
            'timeout': '超时',
            'error': '执行错误',
            'code_execution_error': '代码执行错误'
        }

        for failure_type, info in classification.items():
            label = type_names.get(failure_type, failure_type)
            count = info.get('count', '?')
            ops = info.get('operations', [])
            parts.append(f"- {label}: {count} 个操作 {ops}")

        if trace_issues:
            parts.append("\n代码执行轨迹问题:")
            for issue in trace_issues:
                parts.append(f"  - {issue}")

        return "\n".join(parts)

    def _replanning_phase(self, original_instruction: str, iteration_result: HarnessIteration) -> str:
        """
        重规划阶段（LLM 深度分析）

        不简单拼接失败文本，而是：
        1. 提取结构化失败信息（分类、执行轨迹）
        2. 调用 LLM 分析失败根因
        3. 生成针对性的策略调整建议

        参数：
            original_instruction: 原始指令
            iteration_result: 失败的迭代结果

        返回：
            新的指令（包含失败分析和调整建议）
        """
        logger.info("进入重规划阶段...")

        if iteration_result.feedback is None or iteration_result.failure_reason is None:
            return original_instruction

        failure_info = iteration_result.failure_reason
        feedback = iteration_result.feedback

        # 收集执行轨迹摘要
        trace_summary = ""
        if 'execution_traces' in feedback.failure_details:
            traces = feedback.failure_details['execution_traces']
            for robot_id, trace in traces.items():
                total_primitives = trace.get('primitives_called', 0)
                failed = sum(1 for p in trace.get('primitive_log', []) if p.get('status') != 'success')
                trace_summary += (
                    f"  - {robot_id}: {total_primitives} 步, {failed} 失败, "
                    f"耗时 {trace.get('total_time', 0):.1f}s\n"
                )

        # ── LLM 驱动的失败分析 ──
        analysis_prompt = self._build_failure_analysis_prompt(
            original_instruction, failure_info, trace_summary, iteration_result
        )

        strategy_advice = ""
        try:
            logger.info("  调用 LLM 分析失败原因...")
            response = self.llm.call_with_json(
                analysis_prompt,
                system_prompt=self._build_failure_analysis_system_prompt(),
                max_retries=2
            )
            root_cause = response.get('root_cause', '')
            adjustments = response.get('adjustments', [])
            strategy_advice = f"\nLLM 分析根因: {root_cause}\n"
            if adjustments:
                strategy_advice += "LLM 建议的调整策略:\n"
                for i, adj in enumerate(adjustments, 1):
                    strategy_advice += f"  {i}. {adj}\n"
            logger.info(f"  LLM 分析结果: {root_cause[:200]}")

            # ── 存储失败上下文，供下一轮代码生成使用 ──
            adj_text = '; '.join(adjustments) if adjustments else '无具体建议'
            self.last_failure_context = (
                f"根因: {root_cause}\n"
                f"建议: {adj_text}\n"
                f"失败详情: {failure_info[:300]}"
            )
        except Exception as e:
            logger.warning(f"  LLM 分析调用失败: {e}，使用基础重规划")
            self.last_failure_context = f"上次失败: {failure_info[:300]}"

        # 构建新的指令
        new_instruction = f"""
原始指令: {original_instruction}

上次执行的失败信息:
{failure_info}

执行轨迹摘要:
{trace_summary if trace_summary else "（无代码执行轨迹）"}
{strategy_advice}

请根据上述失败分析重新规划任务。调整建议:
1. 如果是资源冲突：考虑错开使用相同资源的操作，或调整机械臂分配
2. 如果是超时：考虑将该操作拆分为更小的步骤，或延长时间预算
3. 如果是代码执行错误：检查代码逻辑，确保原语调用顺序正确

请生成改进的任务计划，包括更合理的机械臂分配和操作排序。
"""

        logger.info("重规划阶段完成")
        return new_instruction

    def _build_failure_analysis_system_prompt(self) -> str:
        """构建失败分析系统提示"""
        return """你是一个多机械臂调度系统的故障分析专家。
你的任务是分析执行失败的根本原因，并给出可操作的调整建议。

分析时请关注：
1. 失败的模式（是资源冲突、超时还是代码逻辑错误？）
2. 是否同一资源被多次抢占
3. 协作任务中是否有机器人未能同步
4. 操作顺序是否可以优化

返回 JSON 格式：
{
    "root_cause": "失败的根本原因分析（中文，1-2句话）",
    "adjustments": ["调整建议1", "调整建议2", "调整建议3"]
}"""

    def _build_failure_analysis_prompt(self, original_instruction: str,
                                       failure_info: str, trace_summary: str,
                                       iteration_result: HarnessIteration) -> str:
        """构建失败分析提示"""
        prompt = f"""
原始任务: {original_instruction}

执行失败信息:
{failure_info}

机械臂执行轨迹:
{trace_summary if trace_summary else "无详细轨迹"}

这是第 {iteration_result.iteration_num} 次迭代失败。
请分析失败的根本原因，并给出具体的调整建议。
"""
        return prompt

    def _summarize_failures(self, feedback: SimulationFeedback) -> str:
        """总结失败信息"""
        summary = "失败操作:\n"

        for result in feedback.results:
            if result.status != 'success':
                summary += f"  - {result.operation_id}: {result.status} - {result.reason}\n"

        if feedback.failure_details:
            summary += "\n失败类型统计:\n"
            for failure_type, details in feedback.failure_details.items():
                summary += f"  - {failure_type}: {len(details)} 个\n"

        return summary

    def get_iteration_history(self) -> List[HarnessIteration]:
        """获取迭代历史"""
        return self.iterations

    @staticmethod
    def _make_json_safe(obj: Any) -> Any:
        """递归将对象中的 FlexState 等不可序列化对象转为普通 dict/str"""
        if isinstance(obj, dict):
            return {k: Harness._make_json_safe(v) for k, v in obj.items()}
        elif isinstance(obj, (list, tuple)):
            return [Harness._make_json_safe(v) for v in obj]
        elif hasattr(obj, 'to_dict'):
            return Harness._make_json_safe(obj.to_dict())
        elif hasattr(obj, '_data'):  # FlexState fallback
            return Harness._make_json_safe(obj._data)
        elif isinstance(obj, (str, int, float, bool, type(None))):
            return obj
        else:
            return str(obj)

    def generate_report(self) -> Dict[str, Any]:
        """
        生成执行报告

        返回：
            包含执行统计和迭代历史的报告字典（确保 JSON 可序列化）
        """
        report = {
            'scene_id': self.scene.scene_id,
            'total_iterations': len(self.iterations),
            'successful': any(it.success for it in self.iterations),
            'iterations': []
        }

        for it in self.iterations:
            raw_feedback = it.feedback.to_dict() if it.feedback else None
            iteration_report = {
                'iteration_num': it.iteration_num,
                'success': it.success,
                'task_plan': it.task_plan.to_dict() if it.task_plan else None,
                'schedule': it.schedule.to_dict() if it.schedule else None,
                'generated_codes': {
                    rid: code.to_dict()
                    for rid, code in it.generated_codes.items()
                } if it.generated_codes else None,
                'feedback': self._make_json_safe(raw_feedback),
                'failure_reason': it.failure_reason
            }
            report['iterations'].append(iteration_report)

        return report
