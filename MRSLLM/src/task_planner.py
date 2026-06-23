# 任务分解模块
# 使用 LLM 将自然语言指令分解成具体的任务计划
# 包括操作序列、资源分配建议等

import json
import logging
from typing import Dict, Any, List, Optional

from data_types import Scene, Operation, Workpiece
from llm_interface import LLMInterface

logger = logging.getLogger(__name__)


class TaskPlan:
    """任务计划"""

    def __init__(self,
                 instruction: str,
                 operations: List[str],
                 robot_assignments: Dict[str, List[str]],  # operation_id -> [robot_id, ...]
                 reasoning: str = ""):
        """
        初始化任务计划

        参数：
            instruction: 原始指令
            operations: 操作序列（操作 ID 列表）
            robot_assignments: 机械臂分配（操作 ID -> 机械臂 ID 列表）
                              协作任务可以分配多台机械臂
            reasoning: LLM 的推理过程
        """
        self.instruction = instruction
        self.operations = operations
        self.robot_assignments = robot_assignments
        self.reasoning = reasoning

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            'instruction': self.instruction,
            'operations': self.operations,
            'robot_assignments': self.robot_assignments,
            'reasoning': self.reasoning
        }


class TaskPlanner:
    """
    任务分解器

    使用 LLM 将自然语言指令分解为结构化的任务计划
    """

    def __init__(self, scene: Scene, llm: LLMInterface):
        """
        初始化任务分解器

        参数：
            scene: 产线场景配置
            llm: LLM 接口实例
        """
        self.scene = scene
        self.llm = llm

    def plan(self, instruction: str) -> TaskPlan:
        """
        根据指令生成任务计划

        工作流程：
        1. 构建 LLM 提示，包含场景信息和指令
        2. 调用 LLM 生成任务分解
        3. 解析 LLM 响应，生成 TaskPlan
        4. 验证任务计划的合法性

        参数：
            instruction: 自然语言指令（如"搬运和加工两个工件"）

        返回：
            TaskPlan 对象
        """
        # 构建 LLM 提示
        prompt = self._build_planning_prompt(instruction)
        system_prompt = self._build_system_prompt()

        logger.info(f"调用 LLM 进行任务分解...")
        logger.debug(f"用户指令: {instruction}")

        # 调用 LLM 生成任务分解（JSON 格式）
        try:
            response = self.llm.call_with_json(prompt, system_prompt=system_prompt, max_retries=3)
        except Exception as e:
            logger.warning(f"LLM 调用失败，使用默认任务计划: {e}")
            return self._generate_default_plan(instruction)

        # 解析响应并生成 TaskPlan
        task_plan = self._parse_llm_response(instruction, response)

        logger.info(f"任务分解完成: {len(task_plan.operations)} 个操作")

        return task_plan

    def _build_system_prompt(self) -> str:
        """构建系统提示"""
        return """你是一个多机械臂生产线调度专家。
你的任务是根据用户指令分解任务，并生成结构化的任务计划。

任务计划需要包含：
1. 操作序列：按照生产工艺顺序列出所有需要执行的操作
2. 机械臂分配：为每个操作分配合适的机械臂

输出必须是有效的 JSON 格式。"""

    def _build_planning_prompt(self, instruction: str) -> str:
        """构建任务分解提示"""
        # 构建场景描述
        scene_desc = self._describe_scene()

        prompt = f"""
场景信息：
{scene_desc}

用户指令：
{instruction}

请根据上述场景和指令，生成一个任务计划。返回严格的 JSON 格式，包含以下字段：
{{
    "operations": ["op_1", "op_2", ...],
    "robot_assignments": {{
        "op_1": ["arm_0"],
        "op_2": ["arm_1", "arm_2"],
        ...
    }},
    "reasoning": "分解过程的详细说明"
}}

【严格要求】
1. operations 中的操作 ID 必须与场景中列出的完全一致（含下划线，如 "op_1" 而非 "op1"）
2. robot_assignments 中每个操作的机械臂必须是数组格式，即使只有一台机械臂也要写成 ["arm_0"]
3. 协作任务（allowed_robots 中有多台机械臂）需要分配所有参与机械臂，写成 ["arm_1", "arm_2"] 格式
4. 机械臂 ID 必须是 "arm_0"、"arm_1"、"arm_2" 之一
5. 遵守操作的前置依赖关系
"""
        return prompt

    def _describe_scene(self) -> str:
        """生成场景描述"""
        desc = f"""产线名称: {self.scene.scene_id}
描述: {self.scene.description}

工件:
"""
        for wp in self.scene.workpieces:
            desc += f"  - {wp.id} ({wp.name}): {wp.initial_location} -> {wp.target_location}\n"
            desc += f"    工艺流程: {' -> '.join(wp.state_sequence)}\n"
            desc += f"    操作序列: {', '.join(wp.operation_sequence)}\n"

        desc += "\n机械臂:\n"
        for robot in self.scene.resources.robots:
            desc += f"  - {robot.id} ({robot.name}): 能力={robot.capabilities}\n"

        desc += "\n操作列表:\n"
        for op in self.scene.operations:
            desc += f"  - {op.id}: {op.type} ({op.description})\n"
            desc += f"    前置: {op.predecessors if op.predecessors else 'None'}\n"

        return desc

    def _parse_llm_response(self, instruction: str, response: Dict[str, Any]) -> TaskPlan:
        """
        解析 LLM 响应并生成 TaskPlan。

        容错处理：尝试修复常见的 LLM 格式错误（如 op1 → op_1, "arm_0" → ["arm_0"]）
        """
        operations = response.get('operations', [])
        robot_assignments_raw = response.get('robot_assignments', {})
        reasoning = response.get('reasoning', '')

        # 验证并修复操作 ID
        valid_op_ids = set(op.id for op in self.scene.operations)
        operations = self._fix_operation_ids(operations, valid_op_ids)

        invalid_ops = [op_id for op_id in operations if op_id not in valid_op_ids]
        if invalid_ops:
            logger.warning(f"LLM 生成了无效的操作 ID: {invalid_ops}，使用默认计划")
            return self._generate_default_plan(instruction)

        # 验证并修复机械臂分配 → 统一为 List[str] 格式
        valid_robot_ids = set(robot.id for robot in self.scene.resources.robots)
        fixed_assignments: Dict[str, List[str]] = {}
        for op_id, robot_val in robot_assignments_raw.items():
            # 修复操作 ID
            op_id_fixed = self._fix_single_op_id(op_id, valid_op_ids)
            if op_id_fixed is None:
                continue

            # 统一为列表格式
            if isinstance(robot_val, list):
                robot_list = robot_val
            elif isinstance(robot_val, str):
                robot_list = self._fuzzy_match_robots(robot_val, valid_robot_ids)
            else:
                robot_list = []

            # 过滤无效机器人 ID
            valid_robots = [r for r in robot_list if r in valid_robot_ids]
            if not valid_robots:
                logger.warning(f"操作 {op_id_fixed} 没有有效的机械臂分配，使用默认")
                op = next((o for o in self.scene.operations if o.id == op_id_fixed), None)
                if op and op.allowed_robots:
                    valid_robots = op.allowed_robots[:1]  # 取第一个

            fixed_assignments[op_id_fixed] = valid_robots

        return TaskPlan(
            instruction=instruction,
            operations=operations,
            robot_assignments=fixed_assignments,
            reasoning=reasoning
        )

    def _fix_operation_ids(self, ids: List[str], valid_ids: set) -> List[str]:
        """修复操作 ID 列表中的常见 LLM 格式错误"""
        fixed = []
        for op_id in ids:
            new_id = self._fix_single_op_id(op_id, valid_ids)
            if new_id:
                fixed.append(new_id)
        return fixed

    def _fix_single_op_id(self, op_id: str, valid_ids: set) -> Optional[str]:
        """修复单个操作 ID"""
        if op_id in valid_ids:
            return op_id
        # op1 → op_1
        if op_id.startswith('op') and '_' not in op_id:
            candidate = 'op_' + op_id[2:]
            if candidate in valid_ids:
                return candidate
        return None

    def _fuzzy_match_robots(self, robot_str: str, valid_ids: set) -> List[str]:
        """模糊匹配机械臂 ID，支持逗号分隔的多机器人字符串"""
        result = []
        parts = robot_str.replace('，', ',').split(',')
        for part in parts:
            part = part.strip()
            if part in valid_ids:
                result.append(part)
        return result

    def _generate_default_plan(self, instruction: str) -> TaskPlan:
        """
        生成默认任务计划

        默认计划包含所有操作，按照操作 ID 排序

        参数：
            instruction: 用户指令

        返回：
            TaskPlan 对象
        """
        logger.info("使用默认任务计划")

        # 包含所有操作
        operations = [op.id for op in self.scene.operations]

        # 为每个操作分配所有允许的机械臂（协作任务用多台）
        robot_assignments = {}
        for op in self.scene.operations:
            if op.allowed_robots:
                robot_assignments[op.id] = op.allowed_robots.copy()

        reasoning = "使用默认计划：包含所有操作，按场景定义分配所有可用机械臂"

        return TaskPlan(
            instruction=instruction,
            operations=operations,
            robot_assignments=robot_assignments,
            reasoning=reasoning
        )
