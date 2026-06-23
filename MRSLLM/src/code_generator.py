# 代码生成模块
# 使用 LLM 为每个机械臂生成动态的行为代码
# 代码可以调用原子技能原语

import json
import logging
from typing import Dict, List, Optional, Any

from data_types import Scene, Operation, ScheduleEntry
from llm_interface import LLMInterface

logger = logging.getLogger(__name__)


class RobotBehaviorCode:
    """机械臂的行为代码"""

    def __init__(self, robot_id: str, operations: List[str], code: str, explanation: str = ""):
        """
        初始化机械臂行为代码

        参数：
            robot_id: 机械臂 ID
            operations: 该机械臂需要执行的操作列表
            code: Python 代码字符串
            explanation: 代码说明
        """
        self.robot_id = robot_id
        self.operations = operations
        self.code = code
        self.explanation = explanation

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            'robot_id': self.robot_id,
            'operations': self.operations,
            'code': self.code,
            'explanation': self.explanation
        }


class CodeGenerator:
    """
    代码生成器

    为每个机械臂生成动态的行为代码，代码调用原子技能原语
    """

    def __init__(self, scene: Scene, llm: LLMInterface):
        """
        初始化代码生成器

        参数：
            scene: 产线场景配置
            llm: LLM 接口实例
        """
        self.scene = scene
        self.llm = llm

    def generate(self, schedule: List[ScheduleEntry],
                failure_context: Optional[str] = None) -> Dict[str, RobotBehaviorCode]:
        """
        为所有机械臂生成行为代码

        工作流程：
        1. 根据调度表，为每个机械臂分组操作
        2. 为每个机械臂生成行为代码
        3. 代码应该调用原子技能原语（如 move, pick, place 等）

        参数：
            schedule: 调度表（ScheduleEntry 列表）
            failure_context: 上次失败的分析结果（重规划时传入）

        返回：
            机械臂 ID -> RobotBehaviorCode 的映射
        """
        robot_schedules = self._group_by_robot(schedule)

        generated_codes = {}

        for robot_id, robot_schedule in robot_schedules.items():
            logger.info(f"为机械臂 {robot_id} 生成代码...")

            code = self._generate_robot_code(robot_id, robot_schedule, failure_context)
            generated_codes[robot_id] = code

        logger.info(f"代码生成完成，共生成 {len(generated_codes)} 个机械臂的代码")

        return generated_codes

    def _group_by_robot(self, schedule: List[ScheduleEntry]) -> Dict[str, List[ScheduleEntry]]:
        """按机械臂分组调度项"""
        grouped = {}

        for entry in schedule:
            if entry.robot_id not in grouped:
                grouped[entry.robot_id] = []
            grouped[entry.robot_id].append(entry)

        # 按开始时间排序
        for robot_id in grouped:
            grouped[robot_id].sort(key=lambda e: e.start_time)

        return grouped

    def _generate_robot_code(self, robot_id: str, robot_schedule: List[ScheduleEntry],
                            failure_context: Optional[str] = None) -> RobotBehaviorCode:
        """
        为单个机械臂生成行为代码

        参数：
            robot_id: 机械臂 ID
            robot_schedule: 该机械臂的调度项列表
            failure_context: 上次失败的上下文（重规划时传入）

        返回：
            RobotBehaviorCode 对象
        """
        prompt = self._build_code_generation_prompt(robot_id, robot_schedule, failure_context)
        system_prompt = self._build_code_generation_system_prompt()

        logger.debug(f"调用 LLM 为 {robot_id} 生成代码")

        # 调用 LLM 生成代码
        try:
            response = self.llm.call_with_json(prompt, system_prompt=system_prompt, max_retries=3)
        except Exception as e:
            logger.warning(f"LLM 调用失败，使用默认代码生成: {e}")
            return self._generate_default_code(robot_id, robot_schedule)

        # 解析响应
        code = response.get('code', '')
        explanation = response.get('explanation', '')
        operations = [entry.operation_id for entry in robot_schedule]

        return RobotBehaviorCode(
            robot_id=robot_id,
            operations=operations,
            code=code,
            explanation=explanation
        )

    def _build_code_generation_system_prompt(self) -> str:
        """构建代码生成的系统提示"""
        return """你是一个机械臂编程专家。
你的任务是根据调度计划为机械臂生成 Python 代码。

生成的代码应该：
1. 使用提供的原子技能原语（move, pick, place, operate, wait）
2. 遵循调度表中的时间顺序
3. 在适当的位置处理错误和异常
4. 包含必要的工件状态更新

【最重要规则 - 必须严格遵守调度时间窗】
- 调度表中的 [start_time, end_time] 是硬约束，不是建议！
- **每个操作必须先 wait 到 start_time 才能开始执行**，否则会与其他机械臂发生资源冲突
- 代码模板：
  # 操作 op_X [start_time, end_time]，位置 location_Y，工件 wp_Z
  elapsed = robot_state.last_time  # 当前已耗时
  if elapsed < start_time:
      wait(start_time - elapsed)   # 对齐调度时间窗
  move(location_Y, 5.0)           # 移动到工位
  operate(op_type, wp_Z, location_Y)
  # operate 内部消耗时间，不需要额外 wait

- **时间对齐是第一优先级**：即使移动/操作更快完成，也要通过 wait 确保不会提前进入下一个操作的 start_time
- wait() 在以下情况使用：(1) 对齐 start_time (2) 操作完成后对齐 end_time
- 调度器的 [start, end] 已经解决了所有机械臂间的资源冲突，你只需要严格按时间窗执行
- 绝对不要生成只有 wait() 调用的代码——每个操作必须调用 operate/move 等非 wait 原语

【robot_state 可用字段（支持 .field 和 ['field'] 两种访问方式）】
  - robot_state.robot_id (str): 机械臂 ID
  - robot_state.current_location (str): 当前位置
  - robot_state.last_operation (str|None): 上一个执行的操作 ID
  - robot_state.last_time (float): 上一个操作的时间戳
  - robot_state.holding_workpiece (str|None): 当前持有的工件 ID

【world_state 可用字段】
  - world_state.workpieces (dict-like): 工件状态，如 world_state.workpieces['wp_0']
  - world_state.stations (dict-like): 工位状态，如 world_state.stations['loc_1']

【重要】不要访问未声明的字段（如 world_state.time 等），它们将返回 None。

【沙箱限制 - 必须严格遵守】
代码在受限沙箱中执行，以下规则必须遵守：
1. 禁止使用 import 语句（沙箱不支持任何模块导入）
2. 禁止使用 hasattr() 函数
3. 禁止使用 next()、iter() 等迭代器函数
4. 不要写 try-except 块，用简单的 if 条件判断代替
5. 可用内置函数：print, len, range, int, float, str, list, dict, bool, abs, min, max, round, sum, enumerate, zip, type, isinstance
6. 可用异常类型：Exception, ValueError, TypeError, KeyError, IndexError, AttributeError, RuntimeError, NameError, OSError, StopIteration
7. robot_state 和 world_state 的字段使用 .field 方式访问，对可能的 None 值做防御检查

输出必须是有效的 JSON 格式，包含 "code" 和 "explanation" 字段。"""

    def _build_code_generation_prompt(self, robot_id: str, robot_schedule: List[ScheduleEntry],
                                      failure_context: Optional[str] = None) -> str:
        """构建代码生成提示"""
        robot = self._get_robot_by_id(robot_id)

        # 构建调度信息（含位置映射）
        schedule_info = "调度任务（含时间窗和工位）:\n"
        for entry in robot_schedule:
            op = self._get_operation_by_id(entry.operation_id)
            location = self._resolve_operation_location(op, entry)
            workpiece = entry.workpiece_id
            schedule_info += (
                f"  - {entry.operation_id}: {op.type} "
                f"工位={location} 工件={workpiece} "
                f"时间窗=[{entry.start_time:.0f}, {entry.end_time:.0f}]\n"
            )

        # 失败上下文
        failure_section = ""
        if failure_context:
            failure_section = f"""
【上次执行失败 - 请针对性修复】
{failure_context.strip()}

注意：上述失败的根因和调整建议必须在你生成的代码中体现！
"""

        prompt = f"""
机械臂: {robot_id} ({robot.name})
初始位置: {robot.initial_location}
能力: {', '.join(robot.capabilities)}
装备工具: {', '.join(robot.tools)}

{schedule_info}
{failure_section}
请生成该机械臂的行为代码。代码应该使用以下原子技能原语：

def move(location: str, duration: float):
    '''移动到指定位置，耗时 duration 秒'''
    pass

def pick(workpiece_id: str):
    '''拾取指定工件'''
    pass

def place(location: str):
    '''在指定位置放下工件'''
    pass

def operate(operation_type: str, workpiece_id: str, location: str):
    '''执行操作（焊接、抛光等）'''
    pass

def wait(duration: float):
    '''等待指定时间'''
    pass

【状态对象说明】
robot_state 支持以下字段（使用 .field 或 ['field'] 均可）:
  - robot_id, current_location, last_operation, last_time, holding_workpiece
world_state 支持以下字段:
  - workpieces (嵌套字典), stations (嵌套字典)
不要访问上述未列出的字段（如 world_state.time 等）。

【沙箱限制 - 生成代码前必须检查】
1. 不要写 import 语句
2. 不要使用 hasattr()、next()、iter()
3. 不需要 try-except，沙箱内不支持 import traceback
4. 只使用上述列出的 5 个原语函数和可用内置函数

代码模板：
```python
def robot_behavior(robot_state, world_state):
    '''机械臂的行为函数'''
    elapsed = robot_state.last_time

    # 操作 op_X [start_time, end_time]，工位 loc_Y
    if elapsed < start_time:
        wait(start_time - elapsed)

    move("loc_Y", 5.0)
    operate("op_type", "wp_Z", "loc_Y")

    # 下一个操作同理...
    pass
```

返回 JSON 格式：
{{
    "code": "生成的 Python 代码",
    "explanation": "代码的详细说明"
}}
"""
        return prompt

    def _resolve_operation_location(self, op: Operation, entry: ScheduleEntry) -> str:
        """解析操作对应的工位名称"""
        # 优先使用操作自带的 location 字段
        if op.location:
            return op.location
        # 运输操作使用 from_location
        if op.from_location:
            return op.from_location
        # 通过工件查找位置
        for wp in self.scene.workpieces:
            if wp.id == entry.workpiece_id:
                return wp.initial_location or f"loc_{entry.operation_id}"
        return f"loc_{entry.operation_id}"

    def _generate_default_code(self, robot_id: str, robot_schedule: List[ScheduleEntry]) -> RobotBehaviorCode:
        """生成默认代码（通用模板，兼容各种操作类型）"""
        operations = [entry.operation_id for entry in robot_schedule]

        # 生成通用的默认代码
        code = f'''def robot_behavior(robot_state, world_state):
    """
    {robot_id} 的行为函数
    按照调度表执行以下操作: {', '.join(operations)}
    """
    schedule = [
'''

        for entry in robot_schedule:
            op = self._get_operation_by_id(entry.operation_id)
            code += f'        {{"operation_id": "{entry.operation_id}", "type": "{op.type}", '
            code += f'"start_time": {entry.start_time}, "end_time": {entry.end_time}}},\n'

        code += '''    ]

    for task in schedule:
        operation_id = task["operation_id"]
        op_type = task["type"]
        duration = task["end_time"] - task["start_time"]

        # 根据操作类型调用相应的原子技能
        if op_type == "transport":
            move(f"location_{operation_id}", duration)
        elif op_type in ("weld", "polish", "inspect", "task_execution", "assembly", "drill", "cut"):
            operate(op_type, f"wp_{operation_id}", f"station_{operation_id}")
        elif op_type == "pick":
            pick(f"wp_{operation_id}")
        elif op_type == "place":
            place(f"location_{operation_id}")
        else:
            # 未知类型，默认执行 operate
            operate(op_type, f"wp_{operation_id}", f"station_{operation_id}")

        # 更新机械臂状态
        robot_state.last_operation = operation_id
        robot_state.last_time = task["end_time"]

    return robot_state
'''

        explanation = f"默认行为代码：按照调度表顺序执行 {len(operations)} 个操作"

        return RobotBehaviorCode(
            robot_id=robot_id,
            operations=operations,
            code=code,
            explanation=explanation
        )

    def _get_robot_by_id(self, robot_id: str):
        """根据 ID 获取机械臂对象"""
        for robot in self.scene.resources.robots:
            if robot.id == robot_id:
                return robot
        raise ValueError(f'未找到机械臂 {robot_id}')

    def _get_operation_by_id(self, operation_id: str) -> Operation:
        """根据 ID 获取操作对象"""
        for op in self.scene.operations:
            if op.id == operation_id:
                return op
        raise ValueError(f'未找到操作 {operation_id}')
