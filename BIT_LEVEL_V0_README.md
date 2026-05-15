# Bit-level backend v0 说明

本项目现在包含两个 backend：

- `link_abstraction`：原来的链路抽象版，使用 SINR/MCS logistic 模型抽象 BLER。
- `numpy_bit_level`：新增的纯 NumPy bit-level v0，用真实随机 bit、QAM、信道和 bit comparison 建立最小闭环。

## 重要限制

`numpy_bit_level` 是 **第一版 bit-level 验证后端**，不是最终 NR LDPC/Sionna 后端。

当前限制：

- 只支持 `rank=1`；
- 只支持单 CW；
- 必须设置 `simulation.fixed_mcs`；
- 信道支持 `AWGN` 和简化 `Rayleigh`；
- 编码器是 repetition/rate-matching toy code，不是 5G NR LDPC；
- QAM 解调使用硬判决，不是软 LLR + LDPC BP 解码。

它的作用是先验证真实 bit 级流程：

```text
payload bits -> toy encode/rate matching -> QAM -> channel -> hard demod -> toy decode -> bit comparison -> CB/CW/goodput stats
```

## 如何运行

```bash
python run.py --config configs/bit_level_rank1_awgn.yaml
```

输出目录示例：

```text
results_bit_level/sim_YYYYMMDD_HHMMSS/
├── config_resolved.yaml
├── results.csv
├── results.json
├── bler_vs_snr.png
└── throughput_vs_snr.png
```

## 推荐验证

1. 用 `fixed_mcs=0` 或 `fixed_mcs=2` 跑 AWGN，BLER 应随 SNR 下降。
2. 提高 `n_trials_per_snr` 让曲线更平滑。
3. 切换 `channel.model: Rayleigh`，曲线会更差、更慢下降。

## 后续真正 Sionna/LDPC backend 的开发方向

后续可以把 toy code 替换为：

```text
Sionna LDPC5GEncoder / LDPC5GDecoder
Sionna Mapper / Demapper
Sionna AWGN / CDL / OFDMChannel
真实 MMSE detector + LLR router
```

但 MappingScheme、TBManager、CB/goodput 统计口径可以继续复用。
