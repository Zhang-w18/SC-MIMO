# SC-MIMO 理解与链路层仿真实现说明

本文档只记录两类内容：

1. 对 Qualcomm R1-2604697 中 SC-MIMO 方案的理解。
2. 本项目如何基于已有链路层仿真平台实现该方案。

阶段任务、调试状态和验证记录见 [SC_MIMO_implementation_plan.md](SC_MIMO_implementation_plan.md)。

## 1. 项目目标与复用原则

本项目基于一个已有码字映射链路层仿真平台继续开发。SC-MIMO 仍然是链路级仿真问题，因此应尽可能复用已有模块，而不是重新写一套物理层链路。

优先复用的现有能力包括：

- `TBManager`：TBS、CB segmentation、每个 CB 的 rate matching 长度 `E`。
- `FlexibleCWScheme`：不同 CW/layer partition、per-CW MCS、Scheme 1/4/7 等 baseline。
- `GridMappingPlan`：CW coded bits 到 `[RE, layer]` 的映射计划。
- Sionna LDPC backend：每个 CB 独立 LDPC encode/decode，并统计 CB-goodput。
- CDL channel builder：生成 CDL 频域信道 `H[batch,time,freq,rx,tx]` 以及奇异值。
- Adaptive MCS / OLLA：用于 throughput-vs-SNR 评估。

SC-MIMO 的新增部分应集中在：

- CB-aware staggered mapping。
- 真实 MIMO 检测路径。
- CB-level SIC loop。
- 与 Qualcomm Table 2 条件对齐的 preset。

## 2. SC-MIMO 核心理解

SC-MIMO 的核心不是新 LDPC，也不是新 QAM，而是一个新的 code block 到 layer/resource 的映射方式，并配套使用接收端 SIC。

在 NR-like 单 codeword 多层映射中，一个 CW 的 coded-bit stream 通常按 RE-major、layer-minor 的顺序映射到多个 layer。例如 rank 2 时：

```text
CW coded bits -> RE0 L0, RE0 L1, RE1 L0, RE1 L1, ...
```

这种映射对普通 rML/MIMO demapper 是自然的，但对 CB 级 SIC 不友好。原因是每个 CB 的观测通常同时包含未解层的干扰。即使某个 CB 已经解码，也很难让下一个 CB 的某一段显式受益于“上一 CB 已被消除”。

Qualcomm 提案中的 SC-MIMO 采用 staggered code block mapping。以 rank 2 为例，可以理解成：

```text
layer 0:  CB0 part0 | CB1 part0 | CB2 part0 | ...
layer 1:  CBprev p1 | CB0 part1 | CB1 part1 | ...
```

如果 layer 1 相对 layer 0 延迟一个 CB-part，那么解码 CB0 之后，从接收信号中减掉 CB0 的贡献，再解 CB1 时，CB1 的 part0 所面对的另一层干扰已经被消除。因此每个后续 CB 都有一部分资源处在低干扰甚至无干扰条件下。

这就是提案中所谓 decoding wave 的直观含义：解码成功的 CB 向后传播，逐步为后续 CB 清除一部分层间干扰。

### 2.1 当前关键问题澄清

#### SC-MIMO 是否一定依赖 SIC 接收机

SC-MIMO 的主要性能收益依赖 SIC 接收机。没有 SIC 时，可以验证以下内容：

- staggered mapping 是否正确。
- 每个 CB part 的 bit/symbol/resource coverage 是否正确。
- 与 NR mapping 在相同 detector 下的 non-SIC baseline 差异。
- mapping 本身可能带来的频域/资源分布差异。

但是，在没有 SIC loop 之前，不能完整验证 Qualcomm 提案中 SC-MIMO 的核心增益。原因是提案中的主要机制是：某个 CB 解码成功后被重编码、重调制并从接收信号中消除，后续 CB 的一部分资源因此看到更低层间干扰。没有 SIC，就没有 decoding wave，也无法验证 “part0 becomes low-interference/interference-free after cancellation” 这一核心现象。

因此本项目的验证顺序应区分：

```text
mapping correctness validation:
  不依赖 SIC

toy non-SIC performance baseline:
  不依赖 SIC，但不能代表 SC-MIMO 最终收益

SC-MIMO gain validation:
  必须至少实现 ideal-SIC

proposal-like performance validation:
  需要 decoded/CRC-gated SIC + rML/rML+MMSE 接收
```

#### CB0 part0 是否是单流传输

不是必然如此，尤其在 `termination=cyclic` 时不是单流传输。

rank2 cyclic staggered mapping 可以写成：

```text
layer 0:  CB0 part0   CB1 part0   CB2 part0   CB3 part0
layer 1:  CB3 part1   CB0 part1   CB1 part1   CB2 part1
```

在这个结构里，`CB0 part0` 所在的 RE 上，另一层通常同时有 `CB3 part1`。也就是说，发射端不是“这段时频资源只有 layer0 有信号”，而仍然是 rank2 MIMO 传输。`CB0 part0` 只有在另一层的对应 CB contribution 已经被 SIC 消除之后，才会在 residual signal 中变成低干扰或 rank2 情况下的 interference-free 观测。

这也解释了为什么 Qualcomm 图里的 `CB0 part0` 看起来并不是单流传输：SC-MIMO 的重点不是让 transmitter 在某些 RE 上降成单流，而是通过 staggered CB order 让 receiver 在 SIC 之后得到部分低干扰资源。

对第一个 CB 的处理取决于 termination：

- `termination=cyclic`：没有天然起始边界。`CB0 part0` 会与循环移位来的前序 CB part 叠在一起，严格的 decoding wave 需要循环依赖处理或从某个 seed/已知成功 CB 开始分析。
- `termination=zero_guard`：可以把起始边界的另一层置零或保留为空，此时第一个 CB 的某个 part 可以天然低干扰，但这会引入资源开销。

#### zero guard 如何统计频谱效率和资源开销

如果 `CBprev` 用 0 或 empty resource 处理，必须把这些 guard RE 计入资源开销。频谱效率不能只除以实际承载数据的非零 RE，而应除以调度占用的完整时频层资源。

rank2、两个 group、`shift=1`、每个 CB part 占 `T` 个 RE rows、共有 `N` 个 CB 且每个 CB 尺寸相同的简单例子：

```text
layer 0:  CB0 p0   CB1 p0   ...   CB{N-1} p0   guard
layer 1:  guard    CB0 p1   ...   CB{N-2} p1   CB{N-1} p1
```

此时：

```text
useful layer-symbols    = N * T * rank
allocated layer-symbols = (N + 1) * T * rank
guard overhead fraction = 1 / (N + 1)
effective SE            = useful information bits / allocated RE resources
```

更一般地，如果最大 stagger shift 为 `S` 个 CB tile，且用 zero guard 打开边界，则等尺寸 CB 的近似资源开销为：

```text
allocated tile rows = N + S
overhead fraction   = S / (N + S)
```

如果 CB 尺寸不等，则不能只用一个常数 `T` 计算，应以 mapping plan 中实际 guard `re_indices` 和每个 CB 的 `re_indices` 统计：

```text
effective_SE = delivered_payload_bits / scheduled_time_frequency_layer_resources
guard_overhead = scheduled_resources - non_guard_data_resources
```

报告结果时应同时输出：

- `loaded_payload_se`：按完整调度资源计算。
- `guard_overhead_fraction`：zero guard 造成的资源损失。
- `goodput_se`：成功解码 payload bits 除以完整调度资源。

## 3. 原文明确内容、项目推断与实现假设

### 3.1 Qualcomm 原文明确内容

Qualcomm R1-2604697 对 SC-MIMO 明确给出的要点包括：

- SC-MIMO 是一种新的 codeword-to-layer mapping，用于支持 native SIC。
- 它不是把 code blocks 垂直堆叠，而是引入类似“对部分 spatial layers 做 cyclic shift”的 staggered structure。
- rank 2 场景中，每个 CB 被分成两个部分；结合 SIC 后，其中一部分可以看到比 NR mapping 更低的干扰，rank 2 时甚至可达到 interference-free。
- rank 更高时，layers 会被 grouped together，并对 layer groups 而不是 individual layers 做 staggering。
- 接收端假设为 `rML/rML+MMSE after SIC`。
- Table 2 对比的 layer mapping scheme 是 `NR MIMO, SC-MIMO, 2CW`。

### 3.2 本项目对提案的理解

本项目把 SC-MIMO 默认理解为 single-codeword MIMO transmission：

```text
1 TB / 1 CW across all scheduled spatial layers
  -> TB segmentation
  -> multiple LDPC CBs: CB0, CB1, CB2, ...
  -> staggered CB-to-layer/resource mapping
  -> CB-level SIC decoding wave
```

这里的 codeword 是 NR/PHY 语义中的 TB/CW，不是 LDPC code block。文档中讨论的 `CB0`, `CB1` 是一个 CW 内部的 LDPC code blocks，不是多个 codewords。SC-MIMO 的 SIC 粒度是 CB 级，而不是 CW 级。

复现 Qualcomm Table 2 / Figure 17-18 时，三类方案应分清：

```text
NR MIMO baseline:
  rank 2/4 时用 1 CW，NR-like CW-to-layer mapping，无 CB-level SIC

SC-MIMO:
  1 CW across rank 2/4，SC-MIMO staggered CB mapping，CB-level SIC

2CW baseline:
  2 CW / 2 TB，对每个 CW 独立 MCS/OLLA/HARQ 统计
```

### 3.3 本项目实现假设

以下内容是本项目为了可复现和可扩展而选择的实现假设，不应误读为 Qualcomm 原文已经完整规定：

- rank4 默认复现配置采用 `layer_groups = [[0, 1], [2, 3]]`，即两个 layer group 之间做 rank2 风格 staggering。
- `termination=cyclic` 作为第一版默认，因为它对应 cyclic shift、无额外 guard、无频谱效率损失。
- `termination=zero_guard` 保留为研究扩展，用于构造更明确的 decoding-wave 边界，但会带来资源开销。
- 第一版 full-link 复现默认启用 strict symbol/tile alignment，使每个 CB part 都能映射到完整 QAM symbol 和 rectangular layer-group tile。

## 4. 发射端仿真实现说明

SC-MIMO 发射端应保留现有 NR LDPC/TB/CB 处理，只替换 CB 到 layer/RE 的 mapping。

完整发射端流程：

```text
payload bits
  -> TB CRC / CB segmentation
  -> 每个 CB 独立 LDPC encode
  -> 每个 CB 得到 coded bits c_i[0:E_i]
  -> QAM modulation
  -> SC-MIMO CB-aware staggered mapping
  -> layer-domain symbol grid x_layer[RE, layer]
  -> SRS-based precoder V
  -> antenna-domain signal x_tx = V x_layer
```

### 4.1 CB-aware mapping plan

现有 `GridMappingPlan` 主要按 CW route 管理 bit indices。SC-MIMO 需要更细的 CB/part/resource 记录：

```text
SCMIMOGridMappingPlan
  rank
  n_re_per_layer
  qm
  cb_routes:
    cb_index
    part_index
    layer_indices
    re_indices
    bit_indices
    symbol_indices
```

每个 CB 至少要知道：

- 它被分成几个 part。
- 每个 part 映射到哪个 layer 或 layer group。
- 每个 part 的 RE 位置。
- 每个 part 对应原始 CB coded-bit stream 的哪一段。

### 4.2 rank2 初始映射

rank2 初始实现中，每个 CB 分成两个 symbol-domain parts：

```text
CB_i = [part0_i, part1_i]
```

一种 staggered 结构为：

```text
slot t:      0           1           2           3
layer 0:    CB0 part0   CB1 part0   CB2 part0   CB3 part0
layer 1:    CBprev p1   CB0 part1   CB1 part1   CB2 part1
```

`CBprev` 的处理方式可配置：

- `termination=cyclic`：循环移位，无额外 guard，频谱效率不损失，但第一 CB 没有天然无干扰边界。
- `termination=zero_guard`：起始位置为空或低功率 seed，有 decoding-wave 边界，但会带来资源开销。

### 4.3 rank>2 layer-group mapping

Qualcomm 文档只明确说 rank 更高时 “layers are grouped together and the staggering is performed for layer groups instead of individual layers”，没有给出完整可复现的 rank4 mapping 表。

本项目默认 rank4 复现配置为：

```text
rank = 4
layer groups = [[L0, L1], [L2, L3]]
number of CB parts = number of layer groups = 2
shift_pattern = [0, 1]
```

直观上，这等价于把 rank4 先分成两个 layer group，再在两个 group 之间做 rank2 风格的 stagger：

```text
group 0: L0,L1    CB0 part0 | CB1 part0 | CB2 part0 | ...
group 1: L2,L3    CBprev p1 | CB0 part1 | CB1 part1 | ...
```

在每个 group 内，part 的 symbols 再按 NR-like RE-major、layer-minor 顺序映射：

```text
group [L0,L1], tile row r:
  RE r, L0
  RE r, L1
```

后续也可研究更细粒度配置：

```yaml
sc_mimo:
  layer_groups:
    - [0]
    - [1]
    - [2]
    - [3]
  shift_pattern: [0, 1, 2, 3]
```

该配置会把每个 CB 切成 4 个 part，SIC 粒度更细，但 receiver scheduling、LLR collection 和 cancellation 都更复杂，不作为第一版复现 Qualcomm 曲线的默认配置。

### 4.4 CB offset 与 part 尺寸

SC-MIMO 的 stagger offset 不应首先理解成固定的裸 RE 数，而应理解成 CB tile index 的循环偏移。

定义：

```text
G      = number of layer groups
g      = layer group index
s_g    = shift_pattern[g]
T_i    = CB_i 的 tile rows 数
```

对于 group 0，CB 顺序是自然顺序：

```text
CB0, CB1, CB2, ...
```

对于 group g，CB 顺序是按 `s_g` 做循环移位后的顺序。例如 `shift_pattern=[0,1]` 且有 4 个 CB：

```text
group 0 order: CB0, CB1, CB2, CB3
group 1 order: CB3, CB0, CB1, CB2
```

如果所有 CB 的 tile rows 都相同，即 `T_i = T`，那么 group 1 相对 group 0 的 RE 偏移就是：

```text
Delta_RE = s_g * T
```

如果不同 CB 的 `T_i` 不完全相同，则 offset 不再是单个常数。mapping plan 必须显式记录每个 CB 在每个 group 的 `re_indices`，实际偏移按 prefix sum 计算：

```text
start_g(CB_i) = sum of T_j over CB_j before CB_i in group g order
Delta_RE_i,g  = start_g(CB_i) - start_0(CB_i)
```

高保真实现里，CB part 大小应按 layer-group rectangular tile 定义，而不是随意按 bit 数切开：

```text
Qm        = modulation bits per QAM symbol
E_i       = CB_i rate-matched coded bits
Nsym_i    = E_i / Qm
groups    = [group_0, group_1, ..., group_{G-1}]
|group_g| = group g 中的 layer 数
T_i       = CB_i 占用的 tile rows 数

Nsym_i = T_i * rank
part_symbols_i,g = T_i * |group_g|
part_bits_i,g    = T_i * |group_g| * Qm
```

严格模式要求：

```text
E_i % Qm == 0
E_i % (Qm * rank) == 0
```

更准确地说，如果 layer groups 覆盖全部 rank，且一个 CB tile row 会在所有 layer groups 上各放一段，那么每个 CB 的 `E_i` 应分配成：

```text
E_i = T_i * rank * Qm
```

非严格模式可用于 toy validation，只要求 `E_i % Qm == 0`，然后按 QAM symbol 数把 `Nsym_i` 分给多个 part。但这可能产生某些 group 的最后一个 RE row 没有填满全部 layers，不适合作为第一版高通曲线复现默认设置。

### 4.5 资源映射的时频顺序

当前已有 `GridMappingPlan` 和初始 `SCMIMOGridMappingPlan` 都主要使用一维 `re_indices`。也就是说，mapping 层现在看到的是：

```text
[RE index, layer]
```

而不是显式的：

```text
[time symbol, frequency subcarrier, layer]
```

当前 `default_re_pattern()` 生成的是 `np.arange(n_re)`，并记录 `n_time` 和 `n_freq`，但没有在 mapping plan 内把每个 `re_index` 展开为二维时频坐标。因此，在当前代码层面，SC-MIMO 的 stagger 是对一维 data-RE 序列做 CB tile shift；它还没有显式区分“先时域”还是“先频域”。

full-link 接入 CDL channel 时必须固定 flatten 约定。建议采用：

```text
re_index = time_index * n_freq + freq_index
```

也就是一个 OFDM symbol 内先沿频域递增，再进入下一个时域 symbol。这个约定与 CDL builder 的 `[time, freq]` 输出容易对接，也方便后续在 PRG/RB 粒度上做 bundling 或 interleaving。

如果未来要研究 PRG-level interleaving 或更复杂的频域分散映射，应在 `REPattern` 中显式保存 `time_indices` 和 `freq_indices`，而不是只保存一维 `re_indices`。

## 5. 接收端仿真实现说明

SC-MIMO 接收端必须和 CB-aware mapping 配套。完整流程如下：

```text
接收 y[RE, rx]
  -> channel estimation 得到 H_hat_eff[RE, rx, rank]
  -> 初始化 residual y_res = y
  -> 按 decode_order 遍历 CB
      -> 从 mapping plan 找到 CB_i 的 RE/layer positions
      -> 对这些 RE 做 MIMO soft detection
      -> scatter 得到 llr_cb_i[0:E_i]
      -> LDPC decode
      -> 若通过 CRC 或 ideal-SIC 条件:
           -> re-encode decoded bits
           -> re-modulate
           -> re-map 到该 CB 的 layer grid
           -> y_res = y_res - H_eff x_hat_i
  -> 统计 CB-BLER / TB-BLER / goodput
```

当前 Sionna backend 有一个关键限制：它把 MIMO 信道简化成 SVD 等效 diagonal layer channel：

```text
y_l = g_l x_l + n_l
```

这会提前消除层间干扰，而 SC-MIMO 的收益恰恰来自层间干扰下的 SIC。因此 SC-MIMO full-link 不能只改 mapping，还必须新增真实 MIMO 检测路径：

```text
y[RE] = H_eff[RE] x_layer[RE] + n[RE]
H_eff[RE] = H_data[RE] V_precoder
```

### 5.1 MIMO soft detector

高通仿真条件写的是：

```text
Receiver: rML / rML + MMSE after SIC
```

因此 baseline 和 SC-MIMO 都不能用简单 per-layer demapper。推荐分阶段实现：

1. `exhaustive_ml`：只用于 rank2/QPSK/16QAM sanity check。
2. `kbest_rml`：rank4 主力实现，限制 list size。
3. `mmse_pic`：低复杂度扩展，用于复杂度和性能折中研究。

max-log LLR 形式：

```text
LLR(b_t) ~= min_{x: b_t=0} ||y - Hx||^2/N0
          - min_{x: b_t=1} ||y - Hx||^2/N0
```

本文档和初始 NumPy detector 使用上述符号约定，因此 `LLR > 0` 硬判为 bit 1，`LLR < 0` 硬判为 bit 0。后续接入 Sionna LDPC decoder 时必须再次确认 Sionna decoder 期望的 LLR 符号，如果相反，应只在 decoder adapter 层统一翻转。

Qualcomm 文档给出了 `rML/rML+MMSE after SIC`，但没有规定 rML 的具体实现算法。因此本项目需要自己选择一个可验证、可扩展的 rML 近似路径。

本项目中的 rML 可以按以下方式实现：

- `exhaustive_ml` 作为参考实现：枚举所有 `M^rank` 个 QAM vector，计算 `||y - Hx||^2`，再按 max-log 规则生成 LLR。它只适合 rank2、低阶 QAM、小规模测试。
- `kbest_rml` 作为主实现：对每个 RE 的 `H_eff` 做 QR decomposition，把检测问题变成上三角树搜索；从最后一层向前扩展候选，每层只保留 metric 最小的 K 条路径；最后用候选列表近似 max-log LLR。
- `rML+MMSE after SIC` 可解释为：SIC 消除已解 CB 后，对 residual signal 中仍耦合的层使用 rML，或在低干扰 part 上退化为 MMSE/linear detector，以降低复杂度。

K-best rML 的调试标准应是：在 rank2/QPSK 或 rank2/16QAM 的小配置下，K 足够大时，它的 best candidate 和 LLR hard decision 与 `exhaustive_ml` 一致。

### 5.2 SIC 模式

建议支持三种：

```yaml
sic_mode: ideal      # 用真实发送 bits 重构，验证 mapping/SIC 上限
sic_mode: decoded    # 用 LDPC decoded bits 重构
sic_mode: crc_gated  # 仅 CB decode 成功时消除，失败则不消除或做保守处理
```

开发顺序应从 `ideal` 开始，因为它能快速判断 mapping 是否产生了理论上的干扰消除收益。

### 5.3 是否需要真实信道估计

最终对齐 Qualcomm Table 2 时，需要考虑真实或抽象的 channel estimation，因为提案假设写的是：

```text
DMRS channel estimation: RMMSE-based under PRG bundling size 4
```

但开发顺序不应一开始就实现完整 RMMSE channel estimator。建议分三层推进：

```text
Phase 1:
  perfect CSI / genie H_eff
  目的：验证 mapping、MIMO detector、SIC cancellation 机制是否正确。

Phase 2:
  abstract imperfect CSI
  目的：用可控 H_hat = H_eff + estimation_error 或 PRG-averaged H_hat 验证接收机对 CSI 误差的敏感度。

Phase 3:
  proposal-like RMMSE/PRG-bundled CE
  目的：对齐 Qualcomm Table 2 的 DMRS CE 条件。
```

因此，回答是：为了验证 SC-MIMO 的基本机制，第一版不需要真实信道估计，使用 perfect `H_eff` 更合适；为了复现 Qualcomm 曲线，后续必须加入 realistic 或至少 proposal-like 的 `H_hat_eff`，并保证 NR baseline、SC-MIMO、2CW 使用同等 CSI 质量。

## 6. 对齐 Qualcomm 仿真的 preset

SC-MIMO 复现建议提供 `configs/sc_mimo_qualcomm_table2_rank2.yaml` 和 `configs/sc_mimo_qualcomm_table2_rank4.yaml`。

提案 Table 2 条件：

```text
Carrier BW: 20 MHz
SCS: 15 kHz
Channel model: CDL-A, 30 ns
UE speed: 3 km/h, 30 km/h
gNB array: 32 TXRU, 16 AE, (M,N,P)=(4,4,2)
UE array: 4 TXRU, (M,N,P)=(1,2,2)
Precoder: SRS-based SVD, SRS periodicity 40 slots
DMRS CE: RMMSE-based, PRG bundling size 4
Receiver: rML / rML+MMSE after SIC
Layer mapping schemes: NR MIMO, SC-MIMO, 2CW
Ranks: 2 and 4
Metric: OLLA throughput, target BLER 10%
```

结果解读重点：

- rank2, 3 km/h：SC-MIMO 与 NR MIMO 应接近，因为层间干扰较小。
- rank2, 30 km/h：SC-MIMO 应优于 NR MIMO，预期约 0.5 dB 量级。
- rank4, 3 km/h：SC-MIMO 和 2CW 可能都优于 NR 1CW，来源不同。
- rank4, 30 km/h：SC-MIMO 对 inter-layer interference 更敏感，预期超过 NR baseline 约 1 dB 量级。

若仿真结果和提案不一致，优先检查：

1. 是否真实保留层间干扰，而不是 diagonal SVD equivalent。
2. NR baseline 是否使用同等强度 rML detector。
3. SC-MIMO 是否真的按 CB 做 SIC，而不是只按 CW 或 layer。
4. SRS precoder aging 是否为 40 slots。
5. OLLA 目标是 TB-BLER 还是 CB-BLER。
6. DMRS/RX CE 是否为 realistic，而不是 genie。

## 7. 可扩展仿真配置

为了避免只服务于提案条件，SC-MIMO 配置应支持：

```yaml
experiment:
  family: sc_mimo
  preset: qualcomm_r1_2604697_table2

sc_mimo:
  enabled: true
  layer_groups:
    - [0]
    - [1]
  shift_pattern: [0, 1]
  termination: cyclic
  decode_order: natural
  sic_mode: decoded
  strict_symbol_aligned_cb: true
  strict_rectangular_tiles: true

mimo_channel:
  use_true_mimo: true
  precoder: srs_svd
  srs_period_slots: 40
  precoder_granularity: wideband

mimo_receiver:
  detector: kbest_rml
  list_size: 32
  max_qm_for_exhaustive: 4
```

未来研究变量：

- rank: 2/4/8
- CDL profile: A/B/C/D
- delay spread: 30/100/300 ns
- UE speed: 3/30/120 km/h
- SRS periodicity: 5/20/40 slots
- receiver: exhaustive ML / K-best / MMSE-PIC
- SIC: ideal / decoded / CRC-gated
- termination: cyclic / zero guard / seeded low-rate CB
- layer group size: 1/2/4

## 8. 当前 SC-MIMO Python 工具说明

当前仓库里已经有一组最小 SC-MIMO Python 工具。它们不是完整链路仿真，而是为了先把 SC-MIMO 的 mapping 语义、toy MIMO detector 和后续 SIC reconstruction 所需对象验证清楚。

### 8.1 `sc_mimo_mapping.py`

该模块对应本文档中的 **rank2 cyclic staggered CB mapping**。

它实现的是：

- `termination=cyclic`。
- `rank=2`。
- 每个 CB 按 QAM symbol stream 分成两个 part。
- `part0` 映射到 layer 0，并按 CB 自然顺序排列。
- `part1` 映射到 layer 1，并按 CB index 做 cyclic shift。
- 每个 CB part 记录 `re_indices`、`bit_indices`、`symbol_indices`，用于后续收发两端按 CB/part 精确定位资源。

在完整发射链路中的位置：

```text
payload bits
  -> TB/CB segmentation
  -> LDPC encode + rate matching
  -> 每个 CB 得到 coded bits c_i[0:E_i]
  -> sc_mimo_mapping.py:
       CB coded bits -> rank2 SC-MIMO layer grid
  -> precoder / true MIMO channel
```

其中 `map_rank2_cb_bits_to_layer_grid()` 是发射端参考 mapper：输入多个 CB 的 coded bits 和 mapping plan，输出 shape 为 `[n_re_per_layer, 2]` 的 layer-domain QAM symbol grid。

在未来 SIC 接收链路中的位置：

```text
LDPC decoded CB bits 或 ideal true CB bits
  -> re-encode / rate match / re-modulate
  -> sc_mimo_mapping.py:
       reconstruct one CB's layer-grid contribution
  -> y_res = y_res - H_eff x_hat_i
```

其中 `reconstruct_rank2_cb_layer_grid()` 用来重构某一个 CB 在 layer grid 上的贡献。它返回的 grid 只有该 CB 占用的位置非零，其他位置为 0。这正是后续 SIC cancellation 前需要的 `x_hat_i`。

该模块目前没有实现：

- rank4 layer-group mapping。
- `zero_guard` termination。
- strict rectangular tile 的 rate-matching allocation。
- 与 Sionna LDPC orchestrator 的 full-link 接入。
- 真实 MIMO channel、MIMO detection、LDPC decode、SIC loop。

### 8.2 `mimo_detection.py`

该模块对应本文档中的 **`exhaustive_ml` reference detector**，不是 Qualcomm 复现阶段需要的 K-best/rML 主力 detector。

它实现的是小规模 exhaustive max-log MIMO detection：

```text
输入:
  y: 接收向量，shape [n_rx]
  h: 有效 MIMO 信道，shape [n_rx, rank]
  qm: 每个 QAM symbol 的 bit 数
  noise_var: 噪声方差

处理:
  枚举所有 M^rank 个 QAM vector
  计算 metric = ||y - Hx||^2 / noise_var
  找到 best candidate bits
  用 max-log 近似生成每个 bit 的 LLR

输出:
  best_bits
  llr
  candidate_metrics
```

在完整接收链路中的位置：

```text
received y[RE, rx] + H_eff[RE, rx, rank]
  -> mimo_detection.py:
       per-RE MIMO soft detection
  -> scatter LLR 到 CB bit order
  -> LDPC decode
  -> SIC reconstruction / cancellation
```

当前用途：

- rank2/QPSK 或低阶 QAM toy sanity check。
- 验证 LLR 符号约定：本文档和当前 detector 使用 `LLR > 0` 硬判为 bit 1。
- 给后续 K-best/rML detector 提供 correctness reference。

当前限制：

- 复杂度随 `rank * qm` 指数增长，因为候选数是 `2^(rank * qm)`，等价于 `M^rank`。
- 不适合 rank4、高阶 QAM、大 batch 或 Qualcomm Table 2 曲线仿真。
- 不包含 MMSE、K-best、sphere/list search，也不包含 channel estimation。

### 8.3 `tests/test_sc_mimo_mapping.py`

该文件是验证层，不是仿真链路的一部分。它用于确认当前最小数学工具没有偏离预期。

它覆盖：

- rank2 cyclic staggered CB mapping 的 layer 顺序和 CB 顺序。
- 每个 CB 的 bit/symbol coverage 是否完整且不重复。
- `E % Qm == 0` 的 CB symbol alignment 检查。
- noiseless rank2 QPSK exhaustive MIMO detector 能否恢复发送 bits。
- 单 CB reconstruction 是否满足：

```text
sum(reconstruct_rank2_cb_layer_grid(CB_i) for all CB_i)
  == map_rank2_cb_bits_to_layer_grid(all CBs)
```

这三个 Python 文件不是完全独立的单文件脚本。它们依赖：

- `numpy`。
- `lls_platform/phy/numpy_qam.py` 中的 `qam_modulate()`。
- 从项目根目录运行，使 Python 能导入 `lls_platform` 包。

但它们不依赖：

- Sionna。
- TensorFlow。
- CDL channel builder。
- `run.py`。
- 现有 Sionna LDPC orchestrator。

因此，这组测试通过只说明当前最小工具正确，包括 rank2 cyclic mapping、symbol coverage、exhaustive detector 和 single-CB reconstruction。它不代表 full-link SC-MIMO 仿真已经实现，也不代表已经验证了 Qualcomm 提案中的最终性能收益。
