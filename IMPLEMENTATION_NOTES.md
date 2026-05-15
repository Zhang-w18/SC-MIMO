# 第一版实现说明

## 1. 重要实现选择

本版本严格采用“补充文档2覆盖文档1”的约束：

- 主流程实现 per-CW adaptive MCS；
- Scheme 2 实现 per-layer Qm adaptive selection；
- fixed MCS 仅作为 debug override；
- 第一版不实现 SIC/HARQ/OLLA/CRC/DMRS/LS/RMMSE。

## 2. 为什么默认是 link_abstraction backend

为了先把 scheme、MCS、TBS、CB、统计和绘图闭环跑通，本版本默认使用抽象链路模型。

这意味着：

- BLER 曲线用于开发验证和方案逻辑对比；
- 不应直接作为论文级 bit-level BLER 结果；
- 后续接入 Sionna 后可复用当前核心模块。

## 3. 后续接入 Sionna 的建议

保持以下模块不变：

- `lls_platform/schemes/flexible_cw.py`
- `lls_platform/utils/mcs_selection.py`
- `lls_platform/tx/tb_manager.py`
- `lls_platform/tx/symbol_mapper.py`
- `lls_platform/sim/stats.py`

新增 Sionna backend：

- `lls_platform/phy/sionna_channel.py`
- `lls_platform/phy/sionna_ldpc.py`
- `lls_platform/phy/sionna_detector.py`
- `lls_platform/sim/sionna_orchestrator.py`
