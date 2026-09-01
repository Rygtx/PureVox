# PureVox 顶层设计与规范（DESIGN）

> 本文件是节点化架构的**规范来源**。代码与本文件冲突时，以本文件为准修改代码；
> 修改设计必须先改本文件并同步更新日志。工程约束（功能最小化、单一实现路径等）
> 见 AGENTS.md，两者互补：AGENTS 管"做什么/不做什么"，本文件管"怎么构成"。

## 1. 分层架构

自底向上五层，每层只依赖下一层：

```
┌─────────────────────────────────────────────────────┐
│ L4 UI        ui_pyside6.py / dialog_*.py            │
│              渲染节点行 · 收集用户意图 · 展示状态      │
├─────────────────────────────────────────────────────┤
│ L3 会话      session_plan.py                        │
│              链文档 → 可执行会话计划（纯函数，可单测） │
├─────────────────────────────────────────────────────┤
│ L2 传输      audio_processor.AudioThread            │
│              流编排 · 输入混音 · 输出扇出 · AEC far   │
│              pvplatform/audio/pwpipe_client.PwBridge │
├─────────────────────────────────────────────────────┤
│ L1 引擎      pvengine（Stage 管线，纯 DSP，无 I/O） │
├─────────────────────────────────────────────────────┤
│ L0 平台      pvplatform（设备枚举 / 系统集成）       │
└─────────────────────────────────────────────────────┘
```

依赖铁律：
- 上层可以 import 下层；下层禁止 import 上层。
- L4 不直接操作音频流；一切运行时行为经由 L3 的计划与 L2 的线程 API。
- DSP（numpy/scipy/onnxruntime）只允许出现在 L1（AGENTS 工程约定第 7 条）。

## 2. 节点模型规范

### 2.1 NodeSpec

一切用户可见的音频组件都是**节点**，由唯一注册表描述：

```python
@dataclass(frozen=True)
class NodeSpec:
    name: str     # 全局唯一稳定 id：如 "audio_input"、"denoiser"
    label: str    # 中文显示名
    kind: str     # input | output | fx | viz
    tier: str     # toggle | inline | expand （UI 三级形态）
    params: dict  # 参数模式 {key: (label, lo, hi, default, step)}
```

- `kind` 决定行体形态与在信号流中的位置：
  - `input`：采集源，位于处理链**之前**；可多实例（混音）。
  - `output`：播放汇，位于处理链**之后**；可多实例（扇出）。
  - `fx`：处理级，按用户排列顺序串接。
  - `viz`：可视化旁路（tap），只读，不参与信号流。
- `fx` 节点的 spec 从插件类（NAME/LABEL/PARAMS + `ui_tier`）自动派生；
  系统（input/output/viz）节点显式注册。
- 注册表 API（pvengine/plugins.py）：`all_specs() -> List[NodeSpec]`、
  `get_spec(name) -> Optional[NodeSpec]`。UI 与会话计划**只允许**通过这两个
  入口发现节点，禁止各自维护类型清单。

### 2.2 链文档（配置的唯一事实）

```json
[{"type": "audio_input", "enabled": true, "params": {"device": "..."}},
 {"type": "denoiser",    "enabled": true, "params": {}},
 {"type": "audio_output","enabled": true, "params": {"device": ""}}]
```

- 配置键固定为 `plugin_chain`；条目三字段 `type/enabled/params`。
- 未知 `type` 一律忽略（向前兼容旧配置）；不迁移、不改写。
- `enabled=false` 的节点保留在链中（UI 显示关断态，运行时不生效）。

### 2.3 生命周期

```
定义(注册表) → 实例化(UI 行 ↔ 链文档) → 校验(SessionPlan)
→ 绑定(L2 建流) → 运行(数据面) → 拆除(stop/close)
```

任何阶段失败只影响该节点或整体启动（见 §6），不得留下半开资源。

## 3. 数据流规范（不变量）

以下不变量对所有平台成立，违反即为缺陷：

1. **格式**：内部唯一格式 F32 单声道 48kHz；重采样/声道转换只发生在 L0。
2. **10ms hop**：所有数据面按 10ms hop 前进——`hop = SAMPLE_RATE // 100`
   （48kHz → 480 样本，NFFT = 2×hop = 960），**按时间派生而非固定样本数**。
   引擎 Stage 进出帧、平台回调块、桥接 FIFO 分块、重采样输出、网络 Opus 帧
   一律对齐 10ms 网格；缓冲水位取 hop 整数倍；禁止引入与网格错位的固定
   样本块（1024/2048 等）。202609 模型三件套契约一致（波形 hop 进出、
   STFT 在模型图内、enh_hop 滞后 1 hop），未来多采样率仅换 SAMPLE_RATE。
3. **混音**：N 路 input 等权平均；某路暂无数据则跳过该路；全部无数据 = 本帧无输入。
4. **扇出**：M 路 output 各持独立环形缓冲，写入同一份降噪后音频；
   任一路积压/阻塞不得拖累其余路与处理循环。
5. **顺序**：信号流 = inputs(混合) → [fx 按链序] → outputs ∥ viz。
6. **旁路**：viz 只读 tap，永不反压、永不修改样本。
7. **远端参考**：AEC far 是独立采集支路，仅当链中存在启用的 echo_cancel 时建立。

## 4. 会话计划（SessionPlan）契约

L3 是**纯函数层**：输入链文档 + 注册表，输出可执行计划，无 Qt、无音频副作用。

```python
@dataclass(frozen=True)
class SessionPlan:
    inputs: List[str]            # 启用的采集设备（node.name / Windows 设备名）
    outputs: List[str]           # 启用的播放设备
    remote_url: Optional[str]    # 远程推流地址；None = 无网络输入
    viz: frozenset               # 启用的可视化节点名子集
    fx_chain: List[dict]         # 仅含启用的 fx 节点（引擎就绪格式）
    problems: Tuple[str, ...]    # 阻断性问题（中文，面向用户）；非空则不得建流
    warnings: Tuple[str, ...]    # 非阻断提示（未知名节点被忽略等）

    def ok(self) -> bool         # problems 为空（warnings 不影响）
    @classmethod
    def from_chain(cls, chain_cfg) -> "SessionPlan"
```

校验规则：
- 产生 `problems`（阻断）：无网络输入且无本地输入；outputs 为空；
  remote_mic 已启用但 url 为空。
- 产生 `warnings`（不阻断）：未知 type 被忽略；空 device 的 input/output 行被跳过。

L4 在点击启动时调用 `from_chain`；`ok()` 为假则展示 problems 并中止，
为真则把字段分发给 L2（AudioThread/PwBridge）与 L1（set_plugins）。

## 5. 传输层与后端插件（L2）

### 5.1 TransportBackend 契约

每个平台音频 API 是一个**可插拔后端**，与节点解耦：SessionPlan 只描述
设备名列表，不关心由谁采集/播放。后端由注册表描述：

```python
@dataclass(frozen=True)
class BackendSpec:
    name: str                # "pipewire" / "wasapi" / "mme" / "network"
    label: str               # 中文显示名
    platforms: tuple         # ("linux",) / ("windows",) / ("windows","macos")
    capabilities: frozenset  # {"multi_input","multi_output","loopback_far"}
```

后端数据面对象与 PwBridge 同形（这是既成事实的标准接口）：
`open(inputs, outputs)` / `read(n)`（混音）/ `write(samples)`（扇出）/
`close()` / `active()` / `last_error()` / `set_far(sink, enabled)` / `read_far(n)`。

### 5.2 注册表与选择规则

- 注册表唯一入口：`pvplatform/audio/backends.py` 的 `BACKENDS` 列表与
  `probe_backends()` / `select_backend(required_caps)`。
- 启动时按 **平台匹配 → probe() 可用 → 能力覆盖计划需求** 选出唯一后端，
  结果写入启动日志（名称 + 能力 + 探测结果）。
- 禁止在传输代码里散布 `if IS_LINUX` 平台分支——平台差异只允许存在于
  后端实现与 probe 内部。

### 5.3 各后端现状

| 后端 | 平台 | 能力 | 实现状态 |
|---|---|---|---|
| pipewire | Linux | multi_input, multi_output, loopback_far | ✅ PwBridge 已完全类化 |
| wasapi | Windows | loopback_far | ⚠️ 数据面内联于 AudioThread 回调，类化提取为 TODO v2 |
| mme | Windows | （无 loopback；驱动自动重采样故 48k 检测宽松） | 同上 |
| network | 全平台 | multi_input（作为一路输入注入） | 经服务器 RemoteAudioSource 注入，随会话建立 |

### 5.4 其他传输规范

- 「监听」概念已废除——监听就是一个 output 节点实例。
- 网络：remote_mic 节点经 HTTPS/WSS 服务器注入，等同一路 input；
  输出侧与本地一致（Linux 扇出走 PwBridge，Windows extras 回调）。

## 6. 错误处理与降级

| 场景 | 行为 |
|---|---|
| 计划校验失败（无输入/输出等） | UI 弹出/记录 problems，不建流 |
| 主输入或主输出建流失败 | 整体启动失败，报错（48k 检测前置拦截常见原因） |
| 额外输出建流失败 | 跳过该路，日志告警，主流程继续 |
| fx 插件实例化失败 | 该节点不入管线，记入 plugin_errors，其余继续 |
| 传输中断线 | 健康检查重建有限次；用尽后停线程（继承既有策略） |

## 7. 变更生效矩阵（热更 vs 重启）

原则：**参数类变更热更，结构类（端点绑定）变更重启**。
结构类变更在处理运行中触发时，UI 以防抖（400ms）自动执行
`stop_processing → start_processing`（`restart_processing`，带重入保护），
用户无感知手工操作；未运行则仅保存配置，下次启动生效。

| 变更 | 生效方式 | 机制 |
|---|---|---|
| fx 参数滑杆 | 热更 | `update_plugin_param` 直达运行实例 |
| fx 行启用/停用 | 热更 | `set_plugin_enabled` 只翻 Stage/Effect enabled；媒体源类（FADE_THROUGH）自行淡出/淡入。AEC 例外走重启路径（far 端采集生命周期绑定建流） |
| fx 行增删/排序 | 热更 | 整链 `set_plugins` 重建（模型经 stage_cache 复用，不断流） |
| EQ 增益/预设 | 热更 | eq 插件参数 |
| TSE 参考录音/加载 | 热更 | `set_tse_reference` |
| viz 行启停 | 热更 | UI tap 开关，不进引擎 |
| 输入/输出设备选择 | 重启（自动） | 流绑定于 open() 时的设备名 |
| remote_mic 地址 | 重启（自动） | 服务器注入路径绑定 |
| audio_input/output 行启停/增删/排序 | 重启（自动） | 端点集合变化 |
| 传输后端切换 | 重启（自动） | 后端在 open 时绑定 |

判定规则（代码层）：`SessionPlan.from_chain` 的签名三元组
`(inputs, outputs, remote_url)` 发生变化 ⇒ 结构类变更。

## 8. 扩展指南

新增 **fx 处理插件**：
1. `pvengine/components/` 新建 Stage（process/reset/release 契约）。
2. `pvengine/plugins.py` CATALOG 注册类（NAME/LABEL/PARAMS）。
3. 如需特殊 UI 形态，在 UI_TIERS 声明 tier。
4. `plugin_smoke` 加一条实例化断言。
5. 更新日志追加一行。

新增 **系统节点**（input/output/viz）：
1. `pvengine/plugins.py` SYSTEM_NODES 注册（name/label/kind/tier/params）。
2. 若是新 kind 或新参数形态：PluginRow 行体渲染分支 + SessionPlan 抽取规则。
3. L2 传输实现对应端点能力（Linux/Windows 分别评估）。
4. `session_plan` 单测 + UI 冒烟断言。
5. 更新日志追加一行。

## 9. 平台差异矩阵

| 能力 | Linux (PipeWire) | Windows (PortAudio) |
|---|---|---|
| 多输入混音 | 支持 | 单输入（TODO） |
| 多输出扇出 | 支持 | 支持（extras 回调） |
| AEC far 参考 | monitor 源采集 | WASAPI loopback |
| 远程推流输入 | 支持 | 支持 |
| 虚拟麦克风 | module-remap-source 方案 | VB-CABLE 外部 |
