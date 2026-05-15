# lls_platform_v2.5_bit_level 更新说明

## 主要变化

v2.5 在 v2.4 多 CW / 多 Scheme / rank 1~8 的基础上，重点修改 bit-level 统计口径：

- 支持一个 CW 内多个 CB 的执行链路；
- 每个 CB 独立 LDPC 编码、解码；
- 同一个 CW 的多个 CB coded bits 串接后再进入 `GridMappingPlan`；
- 接收端从 grid 收集回 CW LLR 后，再按每个 CB 的 `E` 切分并分别 LDPC 解码；
- `goodput_bits_per_slot` 改为 **CB 级成功 payload bits 累加**。

## v2.5 goodput 口径

每个 trial 的 goodput 定义为：

```text
goodput_trial = sum over all CWs and CBs:
    CB_payload_bits if this CB is decoded correctly else 0
```

因此，如果一个 CW 内有多个 CB，部分 CB 解码成功、部分 CB 解码失败，则成功 CB 的 payload bits 会计入 goodput。

## BLER 口径保持不变

- `cw_bler`：该 CW 中任意 CB 失败，则该 CW 在本 trial 失败；
- `cw_cb_bler`：该 CW 内失败 CB 数 / 该 CW 总 CB 数；
- `scheme_bler`：任意 CW/CB 失败，则该 scheme 在本 trial 失败；
- `scheme_cb_bler`：整个 scheme 所有 CW 的失败 CB 数 / 总 CB 数。

## 关键实现文件

```text
lls_platform/sim/sionna_ldpc_orchestrator.py
```

关键类/函数：

```text
_SionnaMultiCWGridChain.run_batch()
SionnaLDPCBitLevelOrchestrator._run_one_scheme_one_snr()
```

## 仍未启用的能力

- Scheme 2 per-layer Qm 自适应；
- adaptive MCS；
- CDL/OFDM 频选信道；
- 真实 soft-output MMSE detector。
