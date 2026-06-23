# 代码执行引擎
# 在受限沙箱中执行 LLM 生成的机械臂行为代码
# 拦截原子技能原语调用，记录详细执行轨迹

import io
import sys
import logging
import traceback
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


# =========================================================================
# 兼容状态对象：同时支持属性访问(.attr)和字典访问(['key'])
# LLM 生成的代码可能混用两种风格，此类确保两种都可用
# =========================================================================

class FlexState:
    """
    灵活状态对象，同时支持：
      - 属性访问：state.key, state.key = value
      - 字典访问：state['key'], state['key'] = value
      - 嵌套访问：state['nested']['key']
      - in 检查：'key' in state
      - 迭代：iter(state), list(state)

    这确保无论 LLM 用哪种风格生成代码都能正常运行。
    """

    def __init__(self, **kwargs):
        self.__dict__['_data'] = {}
        for k, v in kwargs.items():
            self._data[k] = v

    # ── 属性访问 ──
    def __getattr__(self, key):
        if key.startswith('_'):
            raise AttributeError(key)
        # 不存在则返回 None，不自动创建（避免比较运算报错）
        return self._data.get(key, None)

    def __setattr__(self, key, value):
        if key.startswith('_'):
            object.__setattr__(self, key, value)
        else:
            self._data[key] = value

    # ── 字典访问 ──
    def __getitem__(self, key):
        # 不存在则返回 None，不自动创建（避免 < > 等比较运算报错）
        return self._data.get(key, None)

    def __setitem__(self, key, value):
        self._data[key] = value

    def __contains__(self, key):
        return key in self._data

    def __iter__(self):
        return iter(self._data)

    def __len__(self):
        return len(self._data)

    # ── 调试 ──
    def __repr__(self):
        return f'FlexState({self._data})'

    def get(self, key, default=None):
        return self._data.get(key, default)

    def keys(self):
        return self._data.keys()

    def values(self):
        return self._data.values()

    def items(self):
        return self._data.items()

    def to_dict(self):
        """递归转换为普通字典"""
        result = {}
        for k, v in self._data.items():
            if isinstance(v, FlexState):
                result[k] = v.to_dict()
            else:
                result[k] = v
        return result


# =========================================================================
# 执行轨迹数据结构
# =========================================================================

@dataclass
class PrimitiveCall:
    """一次原子原语调用记录"""
    robot_id: str               # 执行的机械臂
    sequence: int               # 调用序号
    primitive: str              # 原语名称（move/pick/place/operate/wait）
    args: Dict[str, Any]        # 调用参数
    start_time: float           # 调用时刻（虚拟时间）
    end_time: float             # 结束时刻
    status: str                 # "success" | "timeout" | "error"
    error_msg: str = ""         # 错误信息


@dataclass
class RobotExecutionTrace:
    """单台机械臂的完整执行轨迹"""
    robot_id: str
    code: str                   # 执行的源代码
    primitives_called: List[PrimitiveCall] = field(default_factory=list)
    total_time: float = 0.0
    error: Optional[str] = None  # 代码级错误（如 SyntaxError）
    success: bool = False

    def to_dict(self) -> Dict:
        return {
            'robot_id': self.robot_id,
            'code_preview': self.code[:200] + '...' if len(self.code) > 200 else self.code,
            'primitives_called': len(self.primitives_called),
            'primitive_log': [
                {
                    'seq': p.sequence,
                    'primitive': p.primitive,
                    'args': p.args,
                    'start': p.start_time,
                    'end': p.end_time,
                    'status': p.status,
                    'error': p.error_msg
                } for p in self.primitives_called
            ],
            'total_time': self.total_time,
            'error': self.error,
            'success': self.success
        }


# =========================================================================
# 沙箱执行环境
# =========================================================================

class CodeExecutor:
    """
    受控沙箱执行器。

    原理：
    1. 为每台机械臂创建一个受限的全局命名空间
    2. 将原子原语（move/pick/place/operate/wait）替换为拦截器
    3. exec() LLM 生成的代码
    4. 调用 robot_behavior() 入口函数
    5. 收集完整的执行轨迹
    """

    # 允许的安全内置函数 + 标准异常（LLM 代码中 try/except 常用）
    SAFE_BUILTINS = {
        'print': print, 'len': len, 'range': range, 'int': int, 'float': float,
        'str': str, 'list': list, 'dict': dict, 'bool': bool, 'True': True,
        'False': False, 'None': None, 'abs': abs, 'min': min, 'max': max,
        'round': round, 'sum': sum, 'enumerate': enumerate, 'zip': zip,
        'type': type, 'isinstance': isinstance,
        # 标准异常（LLM 生成的 try/except 常用）
        'Exception': Exception, 'ValueError': ValueError,
        'TypeError': TypeError, 'KeyError': KeyError, 'IndexError': IndexError,
        'AttributeError': AttributeError, 'RuntimeError': RuntimeError,
        'NameError': NameError, 'OSError': OSError, 'StopIteration': StopIteration,
    }

    def __init__(self, operation_durations: Dict[str, float],
                 timeout_prob: float = 0.02, success_prob: float = 0.97,
                 noise_ratio: float = 0.05, timeout_limits: Dict[str, float] = None):
        """
        参数：
            operation_durations: operation_id → 基准时长
            timeout_prob: 随机超时概率
            success_prob: 成功率
            noise_ratio: 时长噪声比例
            timeout_limits: operation_type → 超时限制
        """
        self.op_durations = operation_durations
        self.timeout_prob = timeout_prob
        self.success_prob = success_prob
        self.noise_ratio = noise_ratio
        self.timeout_limits = timeout_limits or {}

    def execute(self, robot_id: str, code: str,
                schedule: List[Any] = None,
                shared_world_state: 'FlexState' = None,
                global_clock: List[float] = None,
                collaborative_workpieces: set = None) -> RobotExecutionTrace:
        """
        在沙箱中执行 LLM 生成的代码。

        参数：
            robot_id: 机械臂 ID
            code: LLM 生成的 Python 代码
            schedule: 调度条目（可选）
            shared_world_state: 共享世界状态（多机械臂时间步进模式）
            global_clock: 全局虚拟时钟（多机械臂时间步进模式）

        返回完整的执行轨迹，包含每次原语调用的参数/时间/状态。
        """
        trace = RobotExecutionTrace(robot_id=robot_id, code=code)

        # 时间步进模式：共享世界状态用于资源冲突检测
        # 每台机械臂独立时钟从 0 开始，并行执行
        is_shared_mode = shared_world_state is not None

        # 为该机械臂创建拦截器
        call_log: List[PrimitiveCall] = []
        seq_counter = [0]
        virtual_clock = [0.0]

        # 机器人状态对象
        robot_state = FlexState(
            robot_id=robot_id,
            current_location='start',
            last_operation=None,
            last_time=virtual_clock[0],
            holding_workpiece=None
        )
        # 世界状态：共享模式使用外部传入的，否则创建独立副本
        if is_shared_mode:
            world_state = shared_world_state
        else:
            world_state = FlexState(
                workpieces=FlexState(),
                stations=FlexState()
            )

        # ── 原子原语拦截器 ──
        def move(location: str, duration: float):
            """移动到指定位置"""
            seq_counter[0] += 1
            start_t = virtual_clock[0]
            actual_dur = self._apply_noise(duration)
            end_t = start_t + actual_dur
            virtual_clock[0] = end_t
            robot_state.current_location = location
            robot_state.last_time = virtual_clock[0]

            call = PrimitiveCall(
                robot_id=robot_id, sequence=seq_counter[0],
                primitive='move',
                args={'location': location, 'requested_duration': duration, 'actual_duration': actual_dur},
                start_time=start_t, end_time=end_t, status='success'
            )
            # 随机注入超时/失败
            self._inject_noise(call, actual_dur, 'transport')
            call_log.append(call)
            logger.debug(f"  [{robot_id}] move({location}, {duration}) → {call.status}")

        def pick(workpiece_id: str):
            """拾取工件"""
            seq_counter[0] += 1
            start_t = virtual_clock[0]

            # 时间步进模式：检测工件是否被其他机械臂持有
            if is_shared_mode:
                wp_state = world_state.workpieces.get(workpiece_id)
                if wp_state is None:
                    wp_state = FlexState(holder=None, held_since=None)
                    world_state.workpieces[workpiece_id] = wp_state
                if wp_state.holder is not None and wp_state.holder != robot_id:
                    # 资源冲突：工件被其他机械臂持有
                    call = PrimitiveCall(
                        robot_id=robot_id, sequence=seq_counter[0],
                        primitive='pick',
                        args={'workpiece_id': workpiece_id},
                        start_time=start_t, end_time=start_t, status='error',
                        error_msg=f'资源冲突: 工件 {workpiece_id} 被 {wp_state.holder} 持有'
                    )
                    call_log.append(call)
                    logger.warning(f"  [{robot_id}] pick({workpiece_id}) → error: 资源冲突(被{wp_state.holder}持有)")
                    return

            duration = self._apply_noise(1.0)
            end_t = start_t + duration
            virtual_clock[0] = end_t
            robot_state.holding_workpiece = workpiece_id
            robot_state.last_time = virtual_clock[0]

            call = PrimitiveCall(
                robot_id=robot_id, sequence=seq_counter[0],
                primitive='pick',
                args={'workpiece_id': workpiece_id},
                start_time=start_t, end_time=end_t, status='success'
            )
            self._inject_noise(call, duration, 'pick')
            call_log.append(call)

            # 时间步进模式：更新共享世界状态
            if is_shared_mode:
                wp_state.holder = robot_id
                wp_state.held_since = start_t

            logger.debug(f"  [{robot_id}] pick({workpiece_id}) → {call.status}")

        def place(location: str):
            """放置工件"""
            seq_counter[0] += 1
            start_t = virtual_clock[0]
            duration = self._apply_noise(1.0)
            end_t = start_t + duration
            virtual_clock[0] = end_t

            # 时间步进模式：释放工件持有权
            held_wp = robot_state.holding_workpiece
            if is_shared_mode and held_wp:
                wp_state = world_state.workpieces.get(held_wp)
                if wp_state is not None:
                    wp_state.holder = None
                    wp_state.held_since = None

            robot_state.holding_workpiece = None
            robot_state.last_time = virtual_clock[0]

            call = PrimitiveCall(
                robot_id=robot_id, sequence=seq_counter[0],
                primitive='place',
                args={'location': location},
                start_time=start_t, end_time=end_t, status='success'
            )
            self._inject_noise(call, duration, 'place')
            call_log.append(call)
            logger.debug(f"  [{robot_id}] place({location}) → {call.status}")

        def operate(operation_type: str, workpiece_id: str, location: str):
            """执行加工操作"""
            seq_counter[0] += 1
            start_t = virtual_clock[0]

            # 时间步进模式：检测工位是否被其他机械臂占用
            if is_shared_mode:
                st_state = world_state.stations.get(location)
                if st_state is None:
                    st_state = FlexState(occupied_by=None, occupied_until=None)
                    world_state.stations[location] = st_state
                if st_state.occupied_by is not None and st_state.occupied_by != robot_id:
                    # 协作任务：同一工件的协作操作允许并发占用
                    is_collab = collaborative_workpieces and workpiece_id in collaborative_workpieces
                    if not is_collab and st_state.occupied_until is not None and st_state.occupied_until > start_t:
                        # 资源冲突：工位仍被占用
                        call = PrimitiveCall(
                            robot_id=robot_id, sequence=seq_counter[0],
                            primitive='operate',
                            args={'operation_type': operation_type, 'workpiece_id': workpiece_id, 'location': location},
                            start_time=start_t, end_time=start_t, status='error',
                            error_msg=f'资源冲突: 工位 {location} 被 {st_state.occupied_by} 占用至 {st_state.occupied_until:.1f}s'
                        )
                        call_log.append(call)
                        logger.warning(f"  [{robot_id}] operate({operation_type}, {workpiece_id}) → error: 工位冲突(被{st_state.occupied_by}占用)")
                        return

            base_dur = self.op_durations.get(workpiece_id, 10.0)
            actual_dur = self._apply_noise(base_dur)
            end_t = start_t + actual_dur
            virtual_clock[0] = end_t
            robot_state.last_time = virtual_clock[0]

            call = PrimitiveCall(
                robot_id=robot_id, sequence=seq_counter[0],
                primitive='operate',
                args={'operation_type': operation_type, 'workpiece_id': workpiece_id,
                      'location': location, 'requested_duration': base_dur, 'actual_duration': actual_dur},
                start_time=start_t, end_time=end_t, status='success'
            )
            self._inject_noise(call, actual_dur, operation_type)
            call_log.append(call)

            # 时间步进模式：更新工位占用状态
            if is_shared_mode:
                st_state.occupied_by = robot_id
                st_state.occupied_until = end_t

            logger.debug(f"  [{robot_id}] operate({operation_type}, {workpiece_id}) → {call.status}")

        def wait(duration: float):
            """等待"""
            seq_counter[0] += 1
            start_t = virtual_clock[0]
            end_t = start_t + duration
            virtual_clock[0] = end_t
            robot_state.last_time = virtual_clock[0]

            call = PrimitiveCall(
                robot_id=robot_id, sequence=seq_counter[0],
                primitive='wait',
                args={'duration': duration},
                start_time=start_t, end_time=end_t, status='success'
            )
            call_log.append(call)
            logger.debug(f"  [{robot_id}] wait({duration})")

        # ── 沙箱执行 ──
        sandbox_globals = {
            '__builtins__': self.SAFE_BUILTINS,
            'move': move, 'pick': pick, 'place': place,
            'operate': operate, 'wait': wait,
        }

        # 捕获 LLM 生成代码中的 print() 输出，避免污染控制台
        old_stdout = sys.stdout
        captured_stdout = io.StringIO()
        sys.stdout = captured_stdout

        try:
            # 编译代码（检查语法）
            compiled = compile(code, f'<robot_{robot_id}>', 'exec')
            exec(compiled, sandbox_globals)

            # 调用入口函数
            if 'robot_behavior' not in sandbox_globals:
                trace.error = f"代码中未定义 robot_behavior() 函数"
                return trace

            behavior_fn = sandbox_globals['robot_behavior']
            result = behavior_fn(robot_state, world_state)

            trace.primitives_called = call_log
            # 0 次原语调用 = 代码未执行任何实际操作 → 失败
            if len(call_log) == 0:
                trace.total_time = 0.0
                trace.success = False
                trace.error = "代码未调用任何原语 (0 次 primitives)"
                logger.warning(f"  [{robot_id}] 执行完成: 0 次原语调用 → 视为失败")
            else:
                trace.total_time = virtual_clock[0]
                trace.success = all(c.status == 'success' for c in call_log)
                logger.info(f"  [{robot_id}] 执行完成: {len(call_log)} 次原语调用, "
                            f"耗时 {trace.total_time:.2f}s, 成功={trace.success}")

        except SyntaxError as e:
            trace.error = f"语法错误: {e}\n{traceback.format_exc()}"
            logger.error(f"  [{robot_id}] 语法错误: {e}")
        except Exception as e:
            trace.error = f"运行时错误: {e}\n{traceback.format_exc()}"
            logger.error(f"  [{robot_id}] 运行时错误: {e}")
        finally:
            sys.stdout = old_stdout
            output = captured_stdout.getvalue().strip()
            if output:
                logger.debug(f"  [{robot_id}] 代码 stdout 输出:\n{output}")

        return trace

    def _apply_noise(self, base_duration: float) -> float:
        """施加时长噪声"""
        import random
        noise = random.uniform(-1, 1) * base_duration * self.noise_ratio
        return max(0.1, base_duration + noise)

    def _inject_noise(self, call: PrimitiveCall, duration: float, op_type: str):
        """注入随机失败和超时"""
        import random
        rand = random.random()

        timeout_limit = self.timeout_limits.get(op_type) or self.timeout_limits.get('task_execution', float('inf'))
        if rand < self.timeout_prob or duration > timeout_limit:
            call.status = 'timeout'
            call.error_msg = f'超时: {duration:.2f}s > {timeout_limit:.2f}s'
        elif rand >= self.success_prob:
            call.status = 'error'
            call.error_msg = f'执行失败（随机错误）'
