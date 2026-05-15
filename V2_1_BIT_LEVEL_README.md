# LLS Platform v2.1 bit-level 说明

本版本在原有 `link_abstraction` 和 `numpy_bit_level` 后端基础上，新增两项能力：

1. **3GPP NR 标准 MCS 表**
   - `nr_64qam`：3GPP TS 38.214 Table 5.1.3.1-1，最高 64QAM。
   - `nr_256qam`：3GPP TS 38.214 Table 5.1.3.1-2，最高 256QAM。
   - `approx_256qam`：旧版近似表，仅保留用于回归测试和旧 link abstraction 对比。

2. **Sionna LDPC bit-level 后端**
   - backend 名称：`sionna_ldpc_bit_level`
   - 使用 `LDPC5GEncoder` / `LDPC5GDecoder`
   - 使用 Sionna QAM Mapper/Demapper 产生软 LLR
   - 当前仅支持 rank=1、单 CW、fixed MCS、AWGN
   - 不建模 DMRS、OFDM、CDL、MIMO 检测、SIC

## 运行 Sionna LDPC 最小验证

先确认环境中安装了 TensorFlow 和 Sionna：

```bash
python - <<'PY'
import tensorflow as tf
import sionna
print('TensorFlow:', tf.__version__)
print('Sionna:', getattr(sionna, '__version__', 'unknown'))
PY
```

运行：

```bash
python run.py --config configs/sionna_ldpc_rank1_awgn.yaml
```

结果输出目录：

```text
results_sionna_ldpc/sim_YYYYMMDD_HHMMSS/
  config_resolved.yaml
  results.csv
  results.json
  bler_vs_snr.png
  throughput_vs_snr.png
```

## MCS 表选择建议

调试真实 LDPC 链路时，建议先用：

```yaml
simulation:
  mcs_table: nr_64qam
  fixed_mcs: 2
```

等 rank=1/AWGN 瀑布曲线正常后，再提高 fixed_mcs 或切换到：

```yaml
simulation:
  mcs_table: nr_256qam
```

注意：同一个 MCS index 在 `nr_64qam` 和 `nr_256qam` 表中含义可能不同。结果分析时必须同时记录：MCS table、MCS index、Qm、code rate、actual SE。

## required_sinr_db 的说明

3GPP MCS 表只包含 Qm 和目标码率，不包含 required SINR。`required_sinr_db` 只供 `link_abstraction` 后端估计 BLER 使用。真实 `sionna_ldpc_bit_level` 后端直接通过 LDPC 解码和 bit comparison 得到 BLER，不依赖 required SINR。

## 当前限制与下一步

当前 Sionna LDPC 后端是最小真实 LDPC 闭环。下一步建议按顺序扩展：

1. rank=1 + 多 MCS AWGN 验证；
2. 支持 batch 化以提高速度；
3. 支持多 CW fixed MCS；
4. 支持 rank>1 的层映射和 MIMO 检测；
5. 支持 Scheme 2 per-layer Qm；
6. 接入 OFDM/CDL/DMRS/信道估计。
