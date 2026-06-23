# 多机械臂调度智能体系统

LLM + 析取图 + Harness 闭环框架驱动的多机器人任务分配与调度系统，对接 MRTA-Benchmark 基准数据集。


## 运行方式详解

### 1. 单数据集运行 (`main.py`)

```powershell
# Mock 模式（默认数据集0）
python main.py --mock

# 真实 LLM 模式
python main.py

# 指定数据集
python main.py --problem problem_instance_1p_000003.json --solution optimal_schedule_1p_000003.json

# 自定义指令
python main.py --instruction "优先完成工件 A 的所有操作"
```

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--mock` | 跳过 LLM，使用默认分配策略 | 关 |
| `--problem` | MRTA-Benchmark 问题文件 | `problem_instance_1p_000000.json` |
| `--solution` | MRTA-Benchmark 最优解文件 | `optimal_schedule_1p_000000.json` |
| `--instruction` | 自然语言指令（不指定则自动推断） | 自动 |

> 每次运行自动从 MRTA 数据集加载场景和基准，写入 `config/scene.json` 和 `config/ground_truth.json`。

**运行后输出** (在 `output/` 目录):

| 输出文件 | 说明 |
|----------|------|
| `output/session_{tag}_{ts}.json` | **统一会话 JSON**（全部信息：场景/推理/代码/调度/甘特图/指标） |
| `output/tech_report_{tag}_{ts}.md` | ★ **技术报告**（8 章节 Markdown，自动生成） |
| `output/harness_report_{tag}_{ts}.json` | Harness 迭代历史 |
| `output/evaluation_report_eval_{tag}_{ts}.json` | 评估报告 |
| `output/final_schedule_{tag}_{ts}.json` | 最终调度表 |

### 2. 批量数据集运行 (`run_batch.py`) 

```powershell
# 运行全部 10 个数据集
python run_batch.py all

# 指定数据集
python run_batch.py --mock 0-4,7,9
```

**批量运行的两级报告体系：**

```
run_batch.py --mock all
  │
  ├─ 数据集 #0 → session_1p_000000_{ts}.json → tech_report_1p_000000_{ts}.md
  ├─ 数据集 #1 → session_1p_000001_{ts}.json → tech_report_1p_000001_{ts}.md
  ├─ ...
  └─ 数据集 #9 → session_1p_000009_{ts}.json → tech_report_1p_000009_{ts}.md
       │
       └── AggregateReporter 聚合 → batch_summary_{ts}.md  ← 整体汇总
                                     batch_summary_{ts}.json
```

| 报告层级 | 文件 | 内容 |
|----------|------|------|
| **单数据集技术报告** | `output/tech_report_*.md` | 8 章节完整报告：场景概览、执行流程、LLM 推理链、生成代码、甘特图、性能指标、失败分析、总结 |
| **批量汇总报告** | `output/batch_summary_*.md` | 所有数据集的对比表格 + 统计：makespan Δ% 均值/标准差、评分均值、成功率、星级评级 |


每跑完一个数据集，立即生成它独立的 `session_*.json` 和技术报告。全部跑完后，`AggregateReporter` 读取所有评估报告，生成一份跨数据集的整体汇总（`batch_summary_*.md`）。

### 3. 从会话 JSON 一键生成报告 (`gen_report.py`)

如果已有 `session_*.json`，可以随时重新生成技术报告，无需重新跑调度：

```powershell
# 独立使用：从 session JSON 生成技术报告
python src/gen_report.py --session output/session_1p_000003_20250623_001326.json

# 指定输出目录
python src/gen_report.py --session output/session_1p_000003_20250623_001326.json --output reports/
```

--- 
### 技术报告包含的 8 个章节：

1. **产线场景概览** — 机器人资源、操作定义、运输时间矩阵
2. **执行流程** — 每次 Harness 迭代的任务规划、执行结果、分析判定
3. **LLM 推理链** — 任务规划推理、代码生成解释、执行分析推理（原样保留）
4. **生成代码** — 每台机器人的 Python 行为代码
5. **调度甘特图** — 最优调度 vs 系统调度的并排甘特图
6. **性能指标对比** — makespan / 成功率 / 资源利用率 / 约束违例 + 综合评分
7. **失败分析** — 失败详情（如有）
8. **总结与建议** — 核心结论 + 改进方向

---

## 会话 JSON 结构

`output/session_{tag}_{ts}.json` 是唯一的数据源，包含一次运行的全部信息：

| 字段 | 类型 | 内容 |
|------|------|------|
| `meta` | object | scene_id, success, n_iterations, 时间戳 |
| `scene_summary` | object | 机器人列表、操作定义、运输边 |
| `ground_truth` | object | MRTA-Benchmark 最优 makespan + 调度 |
| `harness_report` | object | 完整 harness.generate_report()：所有迭代的 LLM 输入/输出、生成代码 |
| `evaluation_report` | object | 4 项指标对比 + 综合评分/判定 |
| `final_schedule` | object | 系统生成的调度 entries + makespan |
| `gantt_chart` | string | 最优 vs 系统的 ASCII 甘特图（含运输时间） |

---

## 完整数据流

```
python main.py
         │
         ▼
┌──────────────────────────────────────────────────────────────────┐
│  第1步：数据转换  (mrta_converter.py)                             │
│                                                                  │
│  problem_instance_*.json  ──►  config/scene.json                 │
│  optimal_schedule_*.json  ──►  config/ground_truth.json          │
│                                                                  │
│  每次运行自动转换，翻译 MRTA 标准格式（Q 矩阵、执行时长、运输距离）。│
└──────────────────────────────────────────────────────────────────┘
         │
         ▼
┌──────────────────────────────────────────────────────────────────┐
│  第2步：LLM 任务规划  (task_planner.py)                           │
│                                                                  │
│  LLM 根据场景信息为每个操作分配机器人。                              │
│  Mock 模式下跳过 LLM，直接使用 allowed_robots 列表。               │
└──────────────────────────────────────────────────────────────────┘
         │
         ▼
┌──────────────────────────────────────────────────────────────────┐
│  第3步：析取图调度  (scheduler.py) ★ 核心算法                     │
│                                                                  │
│  Disjunctive Graph 纯算法求解（不依赖 LLM）：                      │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐    │
│  │  节点 = 每个操作 (op_1 ~ op_8)                           │    │
│  │  连接边 = 工序约束 (工件必须先做A才能做B)                  │    │
│  │  析取边 = 资源冲突 (两台机器人抢同一工具/工位)             │    │
│  │                                                          │    │
│  │  策略：LPT + FIFO + 环检测 + 关键路径                     │    │
│  │  1. LPT 排序：长任务优先，减少尾端空闲                     │    │
│  │  2. BFS 环检测：防止析取边选择导致死锁                     │    │
│  │  3. 拓扑排序 → 关键路径 → start_time / end_time           │    │
│  │  4. 运输感知：_compute_transport_timeline 含 T_t 矩阵     │    │
│  └──────────────────────────────────────────────────────────┘    │
│                                                                  │
│  输出：Schedule { entries, makespan(含运输), transport_time }     │
└──────────────────────────────────────────────────────────────────┘
         │
         ▼
┌──────────────────────────────────────────────────────────────────┐
│  第4步：代码生成  (code_generator.py)                             │
│                                                                  │
│  LLM 为每台机器人动态生成 Python 行为代码（move/pick/place/...）。  │
│  Mock 模式下跳过，直接用 Schedule 数据驱动仿真。                   │
└──────────────────────────────────────────────────────────────────┘
         │
         ▼
┌──────────────────────────────────────────────────────────────────┐
│  第5步：仿真执行  (simulator.py + code_executor.py)               │
│                                                                  │
│  时间步同步仿真引擎：                                               │
│                                                                  │
│  - 共享世界状态 (FlexState) + 全局时钟                             │
│  - 资源冲突实时检测（工件持有人、工位占用）                          │
│  - LLM 代码报错 → 标记 ExecutionResult(status='error')              │
│  - 不再回退 Schedule 路径，错误直接触发 Harness 重规划               │
│                                                                  │
│  无代码时：按时序线性执行 schedule.entries，协作任务自动同步          │
│                                                                  │
│  5 个原子原语被拦截：move / pick / place / operate / wait          │
│  robot_state.last_time 在每个原语执行后自动同步                    │
│  协作任务（多机器人同操作）冲突检测自动豁免                          │
│                                                                  │
│  输出：SimulationFeedback { total_makespan, results, ... }        │
└──────────────────────────────────────────────────────────────────┘
         │
         ▼
┌──────────────────────────────────────────────────────────────────┐
│  第6步：闭环反馈  (harness.py)                                    │
│                                                                  │
│  if 全部成功 → 结束 ✓                                             │
│  if 有失败   → LLM 分析根因 → 调整策略 → 回到第2步（最多 3 轮）    │
│  （代码执行报错视为失败，同样触发 replanning，不再静默回退）          │
│                                                                  │
│  每轮记录：plan → schedule → code → feedback                      │
└──────────────────────────────────────────────────────────────────┘
         │
         ▼
┌──────────────────────────────────────────────────────────────────┐
│  第7步：对比评估  (evaluator.py)                                  │
│                                                                  │
│  4 项指标（系统输出 vs MRTA 最优基准）：                             │
│                                                                  │
│  ┌────────────────────┬──────────┬──────────┬─────────────┐      │
│  │ 指标               │ 基准     │ 实际     │ 判定        │      │
│  ├────────────────────┼──────────┼──────────┼─────────────┤      │
│  │ Makespan           │ 570.17s  │ ~580s    │ [OK] 达标   │      │
│  │ 任务成功率         │ 1.00     │ 1.00     │ [OK] 达标   │      │
│  │ 资源利用率         │ 0.46     │ 0.45     │ [OK] 达标   │      │
│  │ 约束违反次数       │ 0        │ 0        │ [OK] 达标   │      │
│  └────────────────────┴──────────┴──────────┴─────────────┘      │
│                                                                  │
│  利用率 = Σ(schedule_entry.duration) / (makespan × n_robots)      │
│  综合评分 0-100，权重：makespan 30% + 成功率 30%                   │
│                      + 利用率 20% + 约束违反 20%                   │
└──────────────────────────────────────────────────────────────────┘
         │
         ▼
┌──────────────────────────────────────────────────────────────────┐
│  第8步：会话 JSON + 技术报告                                      │
│                                                                  │
│  构建统一会话 JSON（7 大块全部信息）                                │
│       │                                                          │
│       ├─→ output/session_{tag}_{ts}.json  统一会话 JSON           │
│       │                                                          │
│       └─→ gen_report.py 自动调用                                  │
│            └─→ output/tech_report_{tag}_{ts}.md  技术报告         │
└──────────────────────────────────────────────────────────────────┘
```

---

## 模块说明

| 模块 | 文件 | 职责 |
|------|------|------|
| **数据模型** | `src/data_types.py` | Scene、Schedule、ScheduleEntry、GroundTruth 等 dataclass |
| **数据转换** | `src/mrta_converter.py` | MRTA-Benchmark JSON → 项目内部格式 |
| **任务规划** | `src/task_planner.py` | LLM 分解自然语言指令为机器人分配方案 |
| **调度求解** | `src/scheduler.py` | 析取图算法（LPT+环检测+运输感知），纯计算 |
| **代码生成** | `src/code_generator.py` | LLM 为每台机器人动态生成 Python 行为代码 |
| **代码执行** | `src/code_executor.py` | 沙箱执行 LLM 代码，拦截原子原语；FlexState 双模式访问 |
| **仿真执行** | `src/simulator.py` | MockSimulator：时间步同步仿真 |
| **闭环控制** | `src/harness.py` | Harness 闭环：规划→执行→分析→重规划（最多 3 轮） |
| **对比评估** | `src/evaluator.py` | 4 项指标评估 + 运输感知甘特图（print + render） |
| **LLM 接口** | `src/llm_interface.py` | 工厂模式，支持 OpenAI / DeepSeek / Mock |
| **技术报告** | `src/gen_report.py` | 从会话 JSON 一键生成 8 章节 Markdown 技术报告 |
| **聚合报告** | `src/aggregate_reporter.py` | 读取多个评估报告，生成批量汇总表与统计 |
| **批量运行** | `run_batch.py` | 指定 ≥1 个数据集批量运行，自动生成各级报告 |
| **单数据集** | `main.py` | 单数据集 Harness 闭环入口 |

---

## 项目结构

```
├── main.py                           # 单数据集 Harness 闭环入口
├── run_batch.py                      # 批量运行
│
├── config/
│   ├── scene.json                    # 场景配置（mrta_converter 生成）
│   └── ground_truth.json             # 最优基准（mrta_converter 生成）
│
├── src/
│   ├── data_types.py                 # 核心数据类型
│   ├── mrta_converter.py             # MRTA → 项目格式
│   ├── task_planner.py               # LLM 任务规划
│   ├── scheduler.py                  # 析取图调度（LPT + 环检测 + 运输感知）
│   ├── code_generator.py             # LLM 代码生成
│   ├── code_executor.py              # 代码沙箱 + FlexState
│   ├── simulator.py                  # MockSimulator（时间步同步仿真）
│   ├── harness.py                    # Harness 闭环控制
│   ├── evaluator.py                  # 4 项指标 + 甘特图（print + render）
│   ├── llm_interface.py              # LLM 工厂接口
│   ├── gen_report.py                 # 一键技术报告生成器
│   └── aggregate_reporter.py         # 批量汇总报告生成器
│
├── docs/
│   └── Harness设计文档.md            # 框架设计文档
│
├── problem_instance_1p_000000.json   # MRTA 问题实例 (000~009)
├── optimal_schedule_1p_000000.json   # MRTA 最优解 (000~009)
│   ... (共 10 组)
│
├── output/                           # 运行输出（自动生成）
│   ├── session_{tag}_{ts}.json       # 统一会话 JSON
│   ├── tech_report_{tag}_{ts}.md     # 技术报告
│   ├── batch_summary_{ts}.md         # 批量汇总报告
│   ├── batch_summary_{ts}.json       # 批量汇总数据
│   ├── final_schedule_*.json         # 调度结果
│   ├── harness_report_*.json         # Harness 迭代历史
│   └── evaluation_report_eval_*.json # 评估报告
│
├── test_e2e_core.py                  # 端到端核心测试（无 LLM）
├── test_scheduler.py                 # 调度算法单元测试
├── test_code_execution.py            # 代码执行器测试
├── test_modules.py                   # 模块集成测试
├── test_llm_interface.py             # LLM 接口测试
│
├── environment.yml                   # Conda 环境
├── .env                              # LLM 密钥配置
└── README.md
```

---

## 技术要点

### 析取图调度算法 (`src/scheduler.py`)

1. **建模**：节点=操作，连接边=工序约束，析取边=资源冲突
2. **LPT 启发式**：长耗时任务优先，减少瓶颈机器人尾端空闲
3. **BFS 环检测**：检查传递路径，防止析取边选择导致死锁
4. **运输感知**：`_compute_transport_timeline()` 使用 T_t 矩阵计算含运输 makespan，包含返回仓库
5. **协作支持**：同一操作对多台机器人各生成一条 ScheduleEntry

### Harness 闭环 (`src/harness.py`)

```
迭代 1：规划 → 调度 → 代码生成 → 执行 → 有失败/代码报错 → LLM 分析根因
迭代 2：重规划 → ... → 全部成功 → 结束 ✓
（最多 3 轮迭代，代码执行错误与调度失败统一处理）
```

### 代码执行沙箱 (`src/code_executor.py`)

- **FlexState**：同时支持 `state.key` 和 `state['key']`，适应 LLM 编码风格差异
- **时间步同步**：共享世界状态 + 独立虚拟时钟，实时检测资源冲突
- **`robot_state.last_time` 自动更新**：5 个原语（move/pick/place/operate/wait）执行后同步 `last_time`，确保 LLM 的 `wait(start_time - elapsed)` 时间窗对齐逻辑正确工作
- **协作任务免冲突**：同一 operation_id 被多机器人执行的协作任务，operate() 冲突检测自动放行
- **原子原语拦截**：move/pick/place/operate/wait 被拦截为 ExecutionTrace

### 代码生成 Prompt 工程 (`src/code_generator.py`)

- **时间窗强制遵守**：系统提示明确要求"每个操作必须先 wait 到 start_time 才能开始"
- **工位映射注入**：用户提示包含每个操作的 `工位=loc_X 工件=wp_Y` 映射，消除 LLM 幻觉
- **失败上下文传递**：Harness 重规划时将上轮失败分析注入代码生成 prompt

### 利用率计算 (`src/evaluator.py`)

基于 **schedule entries** 而非代码执行轨迹计算，避免 LLM 生成的冗余原语污染数据。

### 甘特图 (`src/evaluator.py`)

- `print_schedule_gantt()` — 打印到终端（向后兼容）
- `render_schedule_gantt()` — 返回字符串（供 gen_report.py 写入报告）
- `_build_gantt_lines()` — 内部共享方法
- 运输时间分散在操作块之间（不在末尾堆积），协作任务同步，▌ 标记对齐 makespan

---

## 数据来源

数据来自 **MRTA-Benchmark**（学术基准，arXiv:2603.02669），一个多机器人任务分配的标准测试集。

| 文件 | 内容 |
|------|------|
| `problem_instance_1p_000000.json` | 问题实例：**8 个任务、3 台机器人、10 个位置** |
| `optimal_schedule_1p_000000.json` | 最优解：makespan = **570.17s** |

关键特征：
- 任务执行时长 61~95 秒不等
- 包含协作任务（需 ≥2 台机器人同时参与，如 op_2、op_8）
- 位置间存在运输时间（距离/速度）
- 工件间存在工序依赖（如 op_3 必须在 op_7 之后）

---

## 环境配置

```powershell
# 创建 Conda 环境
conda env create -f environment.yml
conda activate harness_env

# 配置真实 LLM（可选，Mock 模式不需要）
# 编辑 .env 文件：
#   LLM_PROVIDER=openai
#   LLM_API_KEY=sk-your-api-key
#   OPENAI_BASE_URL=https://api.deepseek.com
```

## 测试

```powershell
# 端到端核心测试（调度→仿真→评估→甘特图，不含 LLM）
python test_e2e_core.py

# 调度算法单元测试
pytest test_scheduler.py -v

# 代码执行器测试
pytest test_code_execution.py -v

# 模块集成测试
pytest test_modules.py -v

# 全部测试
pytest test_*.py -v
```
