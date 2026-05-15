# SC-MIMO 仿真实现计划与验证记录

本文档只记录 SC-MIMO 仿真实现的分阶段任务、目标、调试通过标志和验证记录。原理理解与链路层仿真实现说明见 [SC_MIMO_understanding_and_simulation.md](SC_MIMO_understanding_and_simulation.md)。

## 1. 当前状态快照

当前项目目录：

```text
/Users/zhangwei/Downloads/lls_platform_sc_mimo
```

已完成的最小实现：

- `lls_platform/phy/sc_mimo_mapping.py`
  - rank2 cyclic staggered CB mapping plan。
  - `CB coded bits -> rank2 SC-MIMO layer grid` 发射端参考 mapper。
  - 单 CB layer grid reconstruction，用作后续 SIC cancellation 的基础。
- `lls_platform/phy/mimo_detection.py`
  - 小规模 exhaustive max-log MIMO detector。
  - 当前用于 rank2/QPSK toy sanity check，不用于大规模仿真。
- `tests/test_sc_mimo_mapping.py`
  - focused tests 覆盖 mapping plan、CB symbol alignment、exhaustive detector、single-CB reconstruction。

当前尚未完成：

- SC-MIMO full-link orchestrator 尚未接入 `run.py`。
- 真实 MIMO channel/residual/SIC loop 尚未接入 LDPC backend。
- rank4 layer-group mapping 尚未实现。
- K-best/rML detector 尚未实现。
- Qualcomm Table 2 preset 尚未落地。

## 2. Phase 0：文档与最小数学工具

状态：已完成第一版，后续随实现继续维护。

目标：

- 拆分理解说明和实现计划文档。
- 明确 SC-MIMO TX/RX 设计。
- 新增 CB-aware staggered mapping utility。
- 新增小规模 MIMO exhaustive max-log detector，用于 sanity check。

调试通过标志：

- rank2 cyclic staggered CB mapping 无资源冲突。
- 每个 CB 的 part0/part1 覆盖全部 symbol/bit indices，且不重复。
- CB `E` 到 QAM symbol count 的转换会检查 `E % Qm == 0`。
- 小规模 rank2 QPSK exhaustive MIMO detector 在 noiseless 条件下能恢复 bits。
- detector 输出符合文档定义的 LLR 符号约定：`LLR > 0` 硬判为 bit 1。
- 发射端参考 mapper 可以执行 `CB coded bits -> rank2 SC-MIMO layer grid`。
- 所有 CB 的单独 grid reconstruction 相加等于完整发射 grid。

已验证命令：

```bash
cd /Users/zhangwei/Downloads/lls_platform_sc_mimo
python3 -B -m unittest tests.test_sc_mimo_mapping
```

当前验证记录：

```text
Ran 4 tests in 0.001s
OK
```

## 3. Phase 1：rank2 fixed-MCS toy link

目标：

- 不接 OLLA。
- 不接 SRS aging。
- 使用真实 `H_eff`，先使用 flat Rayleigh 或 small CDL batch。
- 比较 NR mapping 和 SC-MIMO mapping 的 CB-BLER。
- 支持 no-SIC / ideal-SIC / decoded-SIC 三类接收路径。
- 新增 `lls_platform/sim/sc_mimo_orchestrator.py`，先实现 rank2 fixed-MCS toy link：

```text
LDPC encode
  -> SC-MIMO mapping
  -> true MIMO channel
  -> exhaustive detector
  -> ideal/decoded SIC
  -> LDPC decode
```

调试通过标志：

- 仿真路径中显式存在真实 `H_eff[RE, rx, rank]`，而不是 diagonal SVD equivalent。
- NR mapping 与 SC-MIMO mapping 都能在相同 channel/noise/detector 条件下跑通。
- no-SIC、ideal-SIC、decoded-SIC 的统计结果可分别输出。
- ideal-SIC 在存在明显层间干扰的 toy 场景中表现优于 no-SIC。
- 单 CB cancellation 后，residual energy 在 noiseless ideal-SIC 场景中接近数值误差。
- LDPC decoder adapter 的 LLR 符号约定被显式验证；若 Sionna 期望相反符号，只在 adapter 层统一翻转。

建议新增测试或脚本：

- rank2 noiseless true-MIMO reconstruction test。
- rank2 ideal-SIC residual cancellation test。
- fixed seed toy simulation，用于比较 no-SIC 与 ideal-SIC 的 CB-BLER。

## 4. Phase 2：rank4 + layer-group + K-best/rML

目标：

- 支持通用 layer-group mapping。
- 默认 rank4 配置采用 `[[0, 1], [2, 3]]` 与 `shift_pattern=[0, 1]`。
- 用 K-best/list detector 替代 exhaustive detector，避免 rank4/high-Qm 复杂度爆炸。
- 对比 1CW NR、SC-MIMO、2CW 三类方案。
- 保持三类方案使用同等 channel、precoder、detector 质量和统计口径。

调试通过标志：

- rank4 layer-group mapping 无资源冲突。
- 每个 CB 的所有 parts 完整覆盖该 CB 的 coded bits/QAM symbols。
- K-best/rML 在小 rank/低 Qm 配置上与 exhaustive detector 的 best candidate 和 LLR hard decision 一致。
- 1CW NR、SC-MIMO、2CW 三类 baseline 都能输出同口径指标：CB-BLER、TB-BLER、goodput/throughput。
- rank4 SC-MIMO 的 CB reconstruction 可以只重构对应 CB 的 layer-grid contribution，并可用于 residual cancellation。

建议新增测试或脚本：

- rank4 rectangular tile coverage test。
- K-best vs exhaustive consistency test。
- 1CW/SC-MIMO/2CW baseline smoke test。

## 5. Phase 3：Qualcomm Table 2 preset

目标：

- 新增 Qualcomm Table 2 复现 preset：
  - `configs/sc_mimo_qualcomm_table2_rank2.yaml`
  - `configs/sc_mimo_qualcomm_table2_rank4.yaml`
- 对齐仿真条件：
  - CDL-A, 30 ns。
  - 20 MHz, SCS 15 kHz。
  - UE speed: 3 km/h, 30 km/h。
  - gNB 32 TXRU / UE 4 RX。
  - SRS-based SVD precoder，40-slot update。
  - RMMSE 或 abstract CE，PRG bundling size 4。
  - Receiver: rML / rML+MMSE after SIC。
  - OLLA target BLER 10%。
- 批量输出 Figure 17/18 所需 rank/speed 组合。

调试通过标志：

- CDL-A 30ns、20MHz/15kHz、SRS SVD 40-slot aging、PRG bundling、OLLA 10% target 都可配置。
- rank2 与 rank4 的 3 km/h、30 km/h 组合可以批量运行。
- 同一批结果中包含 NR MIMO、SC-MIMO、2CW 三条对比曲线或表格。
- rank2, 3 km/h：SC-MIMO 与 NR MIMO 接近。
- rank2, 30 km/h：SC-MIMO 优于 NR MIMO，预期约 0.5 dB 量级。
- rank4, 3 km/h：SC-MIMO 和 2CW 可能都优于 NR 1CW，但收益来源不同。
- rank4, 30 km/h：SC-MIMO 对 inter-layer interference 的处理应带来更明显收益，预期超过 NR baseline 约 1 dB 量级。

建议新增运行入口：

```bash
python run.py --config configs/sc_mimo_qualcomm_table2_rank2.yaml
python run.py --config configs/sc_mimo_qualcomm_table2_rank4.yaml
```

## 6. Phase 4：扩展研究

目标：

- 扫描 rank、speed、delay spread、SRS periodicity、detector complexity、SIC mode。
- 比较 cyclic、zero guard、seeded low-rate CB 等 termination 策略。
- 研究不同 layer group size 对性能和复杂度的影响。
- 输出用于机制分析的中间指标，而不仅是最终 throughput。

调试通过标志：

- rank: 2/4/8 可配置。
- CDL profile: A/B/C/D 可配置。
- delay spread: 30/100/300 ns 可配置。
- UE speed: 3/30/120 km/h 可配置。
- SRS periodicity: 5/20/40 slots 可配置。
- receiver: exhaustive ML / K-best / MMSE-PIC 可配置。
- SIC: ideal / decoded / CRC-gated 可配置。
- 输出指标包含 throughput-vs-SNR、CB-BLER、TB-BLER、SIC propagation failure、residual interference CDF。

## 7. 近期代码开发入口

优先级从高到低：

1. 在 `lls_platform/phy/sc_mimo_mapping.py` 中扩展 rank4 layer-group mapping。
2. 在 `lls_platform/phy/sc_mimo_mapping.py` 中扩展 `zero_guard` termination。
3. 在 `lls_platform/phy/mimo_detection.py` 中加入 K-best/rML detector。
4. 新增 `lls_platform/sim/sc_mimo_orchestrator.py`，先实现 rank2 fixed-MCS toy link。
5. 接入 `run.py` 与 configs，形成可重复运行的 SC-MIMO 仿真入口。

## 8. 回归检查命令

当前文档拆分不改变 Python 代码。拆分后至少运行：

```bash
cd /Users/zhangwei/Downloads/lls_platform_sc_mimo
python3 -B -m unittest tests.test_sc_mimo_mapping
```

若后续接入 full-link orchestrator，再新增对应 smoke tests 和固定随机种子的 regression tests。
