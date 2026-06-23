# 数据类型定义模块
# 包含多机械臂调度系统的所有核心数据结构（Robot、Workpiece、Operation、Schedule 等）
# 支持与 JSON 的双向转换

from dataclasses import dataclass, field, asdict
from typing import List, Dict, Optional, Any
import json
from datetime import datetime


@dataclass
class Location:
    """产线中的一个位置/工位"""
    id: str              # 工位 ID
    name: str            # 工位名称


@dataclass
class TransportEdge:
    """两个位置之间的运输连接"""
    from_location: str = field(metadata={'json_key': 'from'})   # 起始位置 ID
    to_location: str = field(metadata={'json_key': 'to'})       # 目标位置 ID
    distance: float = 0.0      # 距离（或时间系数）

    @classmethod
    def from_dict(cls, data):
        """从字典构造，处理 'from' 和 'to' 这样的关键字"""
        return cls(
            from_location=data['from'],
            to_location=data['to'],
            distance=data['distance']
        )

    def to_dict(self) -> dict:
        """转换为字典，使用 JSON 友好的键名"""
        return {
            'from': self.from_location,
            'to': self.to_location,
            'distance': self.distance
        }


@dataclass
class Robot:
    """一个机械臂"""
    id: str                          # 机械臂 ID
    name: str                        # 机械臂名称
    initial_location: str            # 初始位置 ID
    capabilities: List[str]          # 支持的操作类型（如 transport, weld, polish）
    tools: List[str]                 # 装备的工具列表


@dataclass
class Tool:
    """一个工具资源（夹爪、焊头等）"""
    id: str                              # 工具 ID
    quantity: int                        # 数量（可支持并行使用的数量）
    supported_operations: List[str]      # 支持的操作类型


@dataclass
class Station:
    """一个工位（焊接站、抛光站等）"""
    id: str                          # 工位 ID
    capacity: int                    # 容量（同一时刻最多支持的操作数）
    supported_operations: List[str]  # 支持的操作类型


@dataclass
class CellLayout:
    """产线布局"""
    locations: List[Location]           # 所有位置
    transport_edges: List[TransportEdge] # 运输连接图


@dataclass
class Resources:
    """产线的资源配置"""
    robots: List[Robot]                 # 所有机械臂
    tools: List[Tool]                   # 所有工具
    stations: List[Station]             # 所有工位


@dataclass
class Workpiece:
    """一个工件"""
    id: str                     # 工件 ID
    name: str                   # 工件名称
    initial_location: str       # 初始位置
    target_location: str        # 目标位置
    state_sequence: List[str]   # 状态序列（记录加工过程中的状态）
    operation_sequence: List[str]  # 操作序列（该工件需要经历的操作 ID 列表）


@dataclass
class Operation:
    """一个操作/任务"""
    id: str                          # 操作 ID
    workpiece_id: str                # 关联的工件 ID
    type: str                        # 操作类型（transport, weld, polish, inspect 等）
    description: str                 # 操作描述
    duration: float                  # 基础执行时长（秒）
    allowed_robots: List[str]        # 可以执行该操作的机械臂列表
    required_tools: List[str]        # 需要的工具列表
    required_station: Optional[str]  # 需要的工位（如果是加工操作）
    predecessors: List[str]          # 前置操作 ID 列表
    # 运输操作特有字段
    from_location: Optional[str] = None  # 起始位置
    to_location: Optional[str] = None    # 目标位置
    # 加工操作特有字段
    location: Optional[str] = None       # 操作位置

    @classmethod
    def from_dict(cls, data):
        """从字典构造，处理 'from' 和 'to' 这样的关键字"""
        return cls(
            id=data['id'],
            workpiece_id=data['workpiece_id'],
            type=data['type'],
            description=data['description'],
            duration=data['duration'],
            allowed_robots=data['allowed_robots'],
            required_tools=data['required_tools'],
            required_station=data.get('required_station'),
            predecessors=data['predecessors'],
            from_location=data.get('from'),
            to_location=data.get('to'),
            location=data.get('location')
        )

    def to_dict(self) -> dict:
        """转换为字典，使用 JSON 友好的键名（from/to 替代 from_location/to_location）"""
        result = {
            'id': self.id,
            'workpiece_id': self.workpiece_id,
            'type': self.type,
            'description': self.description,
            'duration': self.duration,
            'allowed_robots': self.allowed_robots,
            'required_tools': self.required_tools,
            'predecessors': self.predecessors,
        }
        if self.required_station is not None:
            result['required_station'] = self.required_station
        if self.from_location is not None:
            result['from'] = self.from_location
        if self.to_location is not None:
            result['to'] = self.to_location
        if self.location is not None:
            result['location'] = self.location
        return result


@dataclass
class OperationTypeDefaults:
    """操作类型的默认参数"""
    base_duration: float  # 基础执行时长
    timeout_limit: float  # 超时时限


@dataclass
class SimulationConfig:
    """仿真配置"""
    duration_noise_ratio: float          # 执行时长的噪声比例（±比例）
    success_probability: float           # 操作成功概率
    timeout_probability: float           # 超时概率
    resource_conflict_probability: float # 资源冲突概率
    station_release_rule: str            # 工位释放规则描述


@dataclass
class Scene:
    """完整的产线场景配置"""
    scene_id: str                           # 场景 ID
    version: str                            # 版本号
    source: str                             # 数据来源说明
    description: str                        # 场景描述
    time_unit: str                          # 时间单位
    cell_layout: CellLayout                 # 产线布局
    resources: Resources                    # 资源配置
    operation_type_defaults: Dict[str, OperationTypeDefaults]  # 操作类型默认参数
    workpieces: List[Workpiece]             # 所有工件
    operations: List[Operation]             # 所有操作
    simulation: SimulationConfig            # 仿真配置

    @classmethod
    def from_json_file(cls, filepath: str) -> 'Scene':
        """从 JSON 文件加载场景"""
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Scene':
        """从字典构造 Scene 对象"""
        cell_layout = CellLayout(
            locations=[Location(**loc) for loc in data['cell_layout']['locations']],
            transport_edges=[TransportEdge.from_dict(edge) for edge in data['cell_layout']['transport_edges']]
        )

        resources = Resources(
            robots=[Robot(**robot) for robot in data['resources']['robots']],
            tools=[Tool(**tool) for tool in data['resources']['tools']],
            stations=[Station(**station) for station in data['resources']['stations']]
        )

        operation_defaults = {}
        for op_type, params in data['operation_type_defaults'].items():
            operation_defaults[op_type] = OperationTypeDefaults(**params)

        workpieces = [Workpiece(**wp) for wp in data['workpieces']]

        operations = [Operation.from_dict(op) for op in data['operations']]

        simulation = SimulationConfig(**data['simulation'])

        return cls(
            scene_id=data['scene_id'],
            version=data['version'],
            source=data['source'],
            description=data['description'],
            time_unit=data['time_unit'],
            cell_layout=cell_layout,
            resources=resources,
            operation_type_defaults=operation_defaults,
            workpieces=workpieces,
            operations=operations,
            simulation=simulation
        )

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典（使用各子对象的 to_dict 方法确保键名正确）"""
        return {
            'scene_id': self.scene_id,
            'version': self.version,
            'source': self.source,
            'description': self.description,
            'time_unit': self.time_unit,
            'cell_layout': {
                'locations': [asdict(loc) for loc in self.cell_layout.locations],
                'transport_edges': [edge.to_dict() for edge in self.cell_layout.transport_edges]
            },
            'resources': {
                'robots': [asdict(r) for r in self.resources.robots],
                'tools': [asdict(t) for t in self.resources.tools],
                'stations': [asdict(s) for s in self.resources.stations]
            },
            'operation_type_defaults': {
                k: asdict(v) for k, v in self.operation_type_defaults.items()
            },
            'workpieces': [asdict(wp) for wp in self.workpieces],
            'operations': [op.to_dict() for op in self.operations],
            'simulation': asdict(self.simulation)
        }


@dataclass
class ScheduleEntry:
    """调度表中的一条记录"""
    operation_id: str      # 操作 ID
    workpiece_id: str      # 工件 ID
    robot_id: str          # 分配的机械臂 ID
    start_time: float      # 开始时间
    end_time: float        # 结束时间
    resources: List[str]   # 使用的资源（工具/工位）


@dataclass
class ResourceUtilization:
    """资源利用率"""
    robot_utilization: Dict[str, float]  # 每台机械臂的利用率
    overall_robot_utilization: float     # 平均机械臂利用率


@dataclass
class ExecutionMetrics:
    """执行指标"""
    makespan: float                  # 总完成时间
    task_success_rate: float         # 任务成功率
    resource_utilization: ResourceUtilization  # 资源利用率
    constraint_violations: int       # 违反约束次数


@dataclass
class Schedule:
    """调度计划"""
    entries: List[ScheduleEntry]     # 调度表（纯执行时间，供仿真器使用）
    metrics: Optional[ExecutionMetrics] = None  # 指标（可选）
    makespan: Optional[float] = None # 总完成时间（含运输，与 MRTA-Benchmark 同口径）
    transport_time: float = 0.0      # 运输总时间

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Schedule':
        """从字典构造"""
        entries = [ScheduleEntry(**entry) for entry in data.get('schedule', [])]

        metrics = None
        if 'metrics_baseline' in data:
            metrics_data = data['metrics_baseline']
            ru_data = metrics_data.get('resource_utilization', {})
            resource_util = ResourceUtilization(
                robot_utilization=ru_data,
                overall_robot_utilization=ru_data.get('overall_robot_utilization', 0.0)
            )
            metrics = ExecutionMetrics(
                makespan=metrics_data.get('makespan', 0.0),
                task_success_rate=metrics_data.get('task_success_rate', 0.0),
                resource_utilization=resource_util,
                constraint_violations=metrics_data.get('constraint_violations', 0)
            )

        makespan = data.get('optimal_makespan', None)
        return cls(entries=entries, metrics=metrics, makespan=makespan)

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return asdict(self)


@dataclass
class GroundTruth:
    """最优调度基准（用于评估）"""
    scene_id: str                           # 场景 ID
    version: str                            # 版本号
    source: str                             # 数据来源
    optimal_makespan: float                 # 最优总时间
    assumptions: List[str]                  # 假设条件
    schedule: List[ScheduleEntry]           # 最优调度表
    operation_order: List[str]              # 操作执行顺序
    metrics_baseline: ExecutionMetrics      # 基准指标

    @classmethod
    def from_json_file(cls, filepath: str) -> 'GroundTruth':
        """从 JSON 文件加载"""
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'GroundTruth':
        """从字典构造"""
        schedule = [ScheduleEntry(**entry) for entry in data['schedule']]

        metrics_data = data['metrics_baseline']
        ru_data = metrics_data.get('resource_utilization', {})
        resource_util = ResourceUtilization(
            robot_utilization={k: v for k, v in ru_data.items() if k != 'overall_robot_utilization'},
            overall_robot_utilization=ru_data.get('overall_robot_utilization', 0.0)
        )
        metrics = ExecutionMetrics(
            makespan=metrics_data.get('makespan', 0.0),
            task_success_rate=metrics_data.get('task_success_rate', 1.0),
            resource_utilization=resource_util,
            constraint_violations=metrics_data.get('constraint_violations', 0)
        )

        return cls(
            scene_id=data['scene_id'],
            version=data['version'],
            source=data['source'],
            optimal_makespan=data['optimal_makespan'],
            assumptions=data['assumptions'],
            schedule=schedule,
            operation_order=data['operation_order'],
            metrics_baseline=metrics
        )

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return asdict(self)


@dataclass
class ExecutionResult:
    """单个操作的执行结果"""
    operation_id: str       # 操作 ID
    robot_id: str           # 执行机械臂 ID
    workpiece_id: str       # 工件 ID
    start_time: float       # 实际开始时间
    end_time: float         # 实际结束时间
    status: str             # 状态：success, timeout, resource_conflict, error
    reason: Optional[str]   # 失败原因（如果有）
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class SimulationFeedback:
    """仿真执行的反馈"""
    results: List[ExecutionResult]   # 所有操作的执行结果
    total_makespan: float            # 实际总完成时间
    success_count: int               # 成功操作数
    failure_count: int               # 失败操作数
    failure_details: Dict[str, Any]  # 失败的详细信息

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            'results': [asdict(r) for r in self.results],
            'total_makespan': self.total_makespan,
            'success_count': self.success_count,
            'failure_count': self.failure_count,
            'failure_details': self.failure_details
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'SimulationFeedback':
        """从字典构造"""
        results = [ExecutionResult(**r) for r in data.get('results', [])]
        return cls(
            results=results,
            total_makespan=data.get('total_makespan', 0.0),
            success_count=data.get('success_count', 0),
            failure_count=data.get('failure_count', 0),
            failure_details=data.get('failure_details', {})
        )


@dataclass
class RobotState:
    """机械臂的实时状态"""
    robot_id: str          # 机械臂 ID
    current_location: str  # 当前位置
    is_idle: bool          # 是否空闲
    holding_workpiece: Optional[str] = None  # 正在夹持的工件 ID


@dataclass
class WorkpieceState:
    """工件的实时状态"""
    workpiece_id: str      # 工件 ID
    current_location: str  # 当前位置
    current_state_index: int  # 状态序列中的当前索引
    completed_operations: List[str] = field(default_factory=list)  # 已完成的操作


@dataclass
class WorldState:
    """世界状态（用于仿真追踪）"""
    current_time: float                           # 当前模拟时间
    robot_states: Dict[str, RobotState]           # 所有机械臂的状态
    workpiece_states: Dict[str, WorkpieceState]   # 所有工件的状态
    available_tools: Dict[str, int]               # 每个工具的可用数量
    occupied_stations: Dict[str, Optional[str]]   # 每个工位的占用情况（None 表示空闲）
