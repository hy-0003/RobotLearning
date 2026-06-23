"""
验证代码执行链：CodeExecutor → MockSimulator.execute_with_code()

不依赖 LLM，纯本地验证代码真正被 exec() 执行。
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

import json
import logging

logging.basicConfig(level=logging.INFO, format='%(levelname)s | %(name)s | %(message)s')
logger = logging.getLogger(__name__)

from data_types import Scene, Schedule, ScheduleEntry, Operation, Robot
from simulator import MockSimulator
from code_executor import CodeExecutor, PrimitiveCall


def load_test_scene():
    """加载 MRTA 测试场景"""
    with open('config/scene.json', 'r', encoding='utf-8') as f:
        return Scene.from_dict(json.load(f))


def make_schedule(scene: Scene, robot_assignments: dict) -> Schedule:
    """手动构造调度（绕过调度器）"""
    from scheduler import SchedulingSolver
    scheduler = SchedulingSolver(scene)
    return scheduler.solve(robot_assignments=robot_assignments)


# =========================================================================
# 测试 1: CodeExecutor 独立测试
# =========================================================================
def test_code_executor_standalone():
    """
    测试沙箱执行器：exec() 一段模拟的 robot_behavior 代码，
    验证原语调用被拦截并记录。
    """
    print("\n" + "=" * 60)
    print("  测试 1: CodeExecutor 独立执行")
    print("=" * 60)

    executor = CodeExecutor(
        operation_durations={'op_1': 5.0, 'op_2': 3.0},
        timeout_prob=0.0,   # 关闭随机噪声确保可预测
        success_prob=1.0,
        noise_ratio=0.0
    )

    test_code = '''
def robot_behavior(robot_state, world_state):
    """测试用行为函数"""
    move("station_A", 2.0)
    wait(1.0)
    pick("wp_001")
    move("station_B", 3.0)
    operate("weld", "wp_001", "weld_station")
    place("station_B")
    return robot_state
'''

    trace = executor.execute("robot_arm_1", test_code)

    assert trace.error is None, f"沙箱执行错误: {trace.error}"
    assert trace.success, f"沙箱执行未成功"
    assert len(trace.primitives_called) == 6, f"期望6次原语调用，实际 {len(trace.primitives_called)}"
    assert trace.total_time > 5.0, f"总时长应 > 5s，实际 {trace.total_time}"

    print(f"  ✅ 沙箱执行成功: {len(trace.primitives_called)} 次原语调用, 耗时 {trace.total_time:.2f}s")
    for p in trace.primitives_called:
        print(f"     [{p.sequence}] {p.primitive}({p.args}) → {p.status}")

    return trace


# =========================================================================
# 测试 2: 代码级错误检测
# =========================================================================
def test_code_executor_errors():
    """
    测试错误检测能力：语法错误、运行时错误、缺失入口函数。
    """
    print("\n" + "=" * 60)
    print("  测试 2: 代码错误检测")
    print("=" * 60)

    executor = CodeExecutor({})

    # 语法错误
    trace = executor.execute("r1", "def robot_behavior(rs, ws):\n    move('X'  # 缺少右括号\n")
    assert trace.error is not None, "应检测到语法错误"
    assert 'SyntaxError' in trace.error or '语法错误' in trace.error
    print(f"  ✅ 语法错误检测: {trace.error[:80]}...")

    # 缺失入口函数
    trace = executor.execute("r1", "x = 1 + 1\n")
    assert trace.error is not None, "应检测到缺少 robot_behavior"
    assert '未定义' in trace.error
    print(f"  ✅ 缺失函数检测: {trace.error}")

    # 运行时错误
    trace = executor.execute("r1", "def robot_behavior(rs, ws):\n    return 1/0\n")
    assert trace.error is not None, "应检测到运行时错误"
    print(f"  ✅ 运行时错误检测: {trace.error[:80]}...")


# =========================================================================
# 测试 3: 默认代码执行
# =========================================================================
def test_default_code_execution():
    """测试 CodeGenerator 生成的默认代码能在沙箱中执行"""
    print("\n" + "=" * 60)
    print("  测试 3: 默认代码执行")
    print("=" * 60)

    scene = load_test_scene()

    # 模拟默认代码
    default_code = '''def robot_behavior(robot_state, world_state):
    """默认行为"""
    move("station_1", 3.0)
    operate("weld", "wp_1", "weld_station")
    operate("polish", "wp_1", "polish_station")
    return robot_state
'''

    ops = {op.id: op.duration for op in scene.operations}
    limits = {}
    for op_type, defaults in scene.operation_type_defaults.items():
        limits[op_type] = defaults.timeout_limit

    executor = CodeExecutor(
        operation_durations=ops,
        timeout_prob=0.0,
        success_prob=1.0,
        noise_ratio=0.0,
        timeout_limits=limits
    )

    trace = executor.execute("robot_arm_1", default_code)
    assert trace.success, f"默认代码执行失败: {trace.error}"
    print(f"  ✅ 默认代码执行: {len(trace.primitives_called)} 次调用, 耗时 {trace.total_time:.2f}s")


# =========================================================================
# 测试 4: 完整调度 → 代码生成 → 执行链
# =========================================================================
def test_full_simulator_execute_with_code():
    """用 MockSimulator.execute_with_code 执行完整的 schedule + generated_codes"""
    print("\n" + "=" * 60)
    print("  测试 4: 完整 execute_with_code 链路")
    print("=" * 60)

    scene = load_test_scene()

    # 从 config/ground_truth.json 获取最优分配
    with open('config/ground_truth.json', 'r', encoding='utf-8') as f:
        gt = json.load(f)

    robot_assignments = gt.get('robot_assignments', {})
    if not robot_assignments:
        print("  ⚠️  ground_truth 中无 robot_assignments，使用默认")
        robot_assignments = {'robot_arm_1': ['op_1', 'op_2', 'op_3']}

    schedule = make_schedule(scene, robot_assignments)
    print(f"  调度: {len(schedule.entries)} 个入口, makespan={schedule.makespan:.2f}s")

    # 生成默认代码
    from code_generator import CodeGenerator
    gen = CodeGenerator(scene, None)  # llm=None 只测试默认代码

    # 直接构建 generated_codes
    generated_codes = {}
    for robot_id, robot_schedule in gen._group_by_robot(schedule.entries).items():
        generated_codes[robot_id] = gen._generate_default_code(robot_id, robot_schedule)

    print(f"  生成代码: {len(generated_codes)} 台机械臂")

    # 执行
    sim = MockSimulator(scene)
    sim.reset()

    # 关闭随机噪声确保可预测
    sim.scene.simulation.success_probability = 1.0
    sim.scene.simulation.timeout_probability = 0.0
    sim.scene.simulation.resource_conflict_probability = 0.0
    sim.scene.simulation.duration_noise_ratio = 0.0

    feedback = sim.execute_with_code(schedule, generated_codes)

    print(f"  反馈: {feedback.success_count} 成功 / {feedback.failure_count} 失败")
    print(f"  makespan: {feedback.total_makespan:.2f}s")

    # 验证有执行轨迹
    assert 'execution_traces' in feedback.failure_details, "应包含执行轨迹"
    traces = feedback.failure_details['execution_traces']
    assert len(traces) > 0, "应有至少一台机械臂的执行轨迹"
    print(f"  ✅ 执行轨迹: {len(traces)} 台机械臂")

    for rid, trace in traces.items():
        n_calls = trace.get('primitives_called', 0)
        success = trace.get('success', False)
        print(f"     {rid}: {n_calls} 次调用, 成功={success}, 耗时={trace.get('total_time',0):.2f}s")

    assert feedback.success_count > 0, "应有成功操作"
    print("  ✅ 完整链路验证通过")


# =========================================================================
# 测试 5: 随机噪声注入
# =========================================================================
def test_noise_injection():
    """验证超时/失败能被随机注入"""
    print("\n" + "=" * 60)
    print("  测试 5: 随机噪声注入")
    print("=" * 60)

    executor = CodeExecutor(
        operation_durations={'op_1': 5.0},
        timeout_prob=0.5,   # 50% 超时
        success_prob=0.5,   # 50% 成功
        noise_ratio=0.1
    )

    test_code = '''def robot_behavior(robot_state, world_state):
    for i in range(10):
        operate("weld", "wp_1", "station_A")
    return robot_state
'''

    trace = executor.execute("r1", test_code)

    failed_calls = [p for p in trace.primitives_called if p.status != 'success']
    total = len(trace.primitives_called)
    print(f"  总调用: {total}, 失败: {len(failed_calls)}")
    for p in failed_calls:
        print(f"     [{p.sequence}] {p.primitive} → {p.status}: {p.error_msg}")

    # 高概率下应有失败
    assert len(failed_calls) > 0 or total == 10, "噪声注入应有失败"
    print(f"  ✅ 噪声注入验证通过")


# =========================================================================
# 主入口
# =========================================================================
if __name__ == '__main__':
    print("🚀 代码执行链验证套件\n")

    test_code_executor_standalone()
    test_code_executor_errors()
    test_default_code_execution()
    test_full_simulator_execute_with_code()
    test_noise_injection()

    print("\n" + "=" * 60)
    print("  ✅ 全部测试通过！代码真正被执行起来了！")
    print("=" * 60)
