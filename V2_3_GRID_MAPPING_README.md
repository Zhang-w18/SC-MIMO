# LLS Platform v2.3 bit-level 更新说明

v2.3 的目标是在 v2.2 已经跑通的 Sionna LDPC + GPU batch 基础上，先把 **CW 到时频空资源的映射接口通用化**，并把测试范围扩展到：

- rank=1 / rank=2；
- AWGN / flat Rayleigh；
- scheme1 / single CW / fixed MCS；
- 多 MCS 批处理与多 GPU 并行仍然可用。

## 1. 新增通用 Grid Mapping 接口

新增文件：

```text
lls_platform/phy/grid_mapping.py
```

核心数据结构：

```text
GridMappingPlan
  └── CWGridRoute
        └── GridSegment
```

`GridSegment` 描述一个 CW 的一组 coded bits 应该映射到：

```text
layer_index
RE indices
Qm
bit_indices
```

当前默认规则是：每个 CW 在其分配到的每个 layer 上使用全部 PDSCH data RE；不同 CW 的区别主要是 layer 集合不同。虽然当前默认 `bit_indices` 是连续块，但接口已经支持任意 bit index，后续可扩展到 NR round-robin symbol layer mapping、交错 RE、frequency shift、PRB 子集等。

发送端：

```text
CW coded bits -> GridMappingPlan -> x_grid [B, N_RE, rank]
```

接收端：

```text
y_eq_grid + no_grid -> GridMappingPlan -> CW coded-bit LLR
```

## 2. Sionna backend v2.3 支持范围

当前 `sionna_ldpc_bit_level` 支持：

```text
rank = 1 或 2
scheme_id = 1
single CW
single CB per CW
fixed MCS
channel.model = AWGN / Rayleigh
GPU batch 并行
```

当前暂不支持：

```text
Scheme 2 per-layer Qm
Scheme 4/5/6/7 多 CW
adaptive MCS
多 CB per CW
CDL/OFDM ResourceGrid
真实 MMSE MIMO detector
```

Rayleigh 模式使用 flat Rayleigh MIMO + ideal SVD-equivalent layer channel：

```text
H = U Σ V^H
layer l: y_l = σ_l x_l + n_l
ideal equalization: y_eq,l = y_l / σ_l
no_eq,l = N0 / σ_l^2
```

## 3. 快速测试配置

v2.3 新增四个快速测试配置，trial 数和 SNR 点数比 v2.2 sanity 配置更少，便于快速回归：

```text
configs/sionna_ldpc_rank1_awgn_gpu_fast.yaml
configs/sionna_ldpc_rank1_rayleigh_gpu_fast.yaml
configs/sionna_ldpc_rank2_awgn_gpu_fast.yaml
configs/sionna_ldpc_rank2_rayleigh_gpu_fast.yaml
```

单个配置运行：

```bash
export XLA_FLAGS=--xla_gpu_cuda_data_dir=/home/zhangwei/anaconda3/envs/sionna_1.2/lib/python3.10/site-packages/nvidia/cuda_nvcc
export CUDA_VISIBLE_DEVICES=0

python run.py --config configs/sionna_ldpc_rank2_awgn_gpu_fast.yaml
```

多 MCS 多 GPU 并行：

```bash
python tools/run_multi_mcs.py \
  --base-config configs/sionna_ldpc_rank2_awgn_gpu_fast.yaml \
  --mcs-list 3,5,8,10 \
  --output-root results_v23_rank2_awgn_multi_mcs \
  --parallel \
  --gpu-list 0,1,2,3
```

Rayleigh：

```bash
python tools/run_multi_mcs.py \
  --base-config configs/sionna_ldpc_rank2_rayleigh_gpu_fast.yaml \
  --mcs-list 3,5,8,10 \
  --output-root results_v23_rank2_rayleigh_multi_mcs \
  --parallel \
  --gpu-list 0,1,2,3
```

## 4. 快速验证建议

建议按下面顺序验证：

1. rank1 AWGN：确认 v2.2 baseline 没被破坏；
2. rank1 Rayleigh：应比 AWGN 更差、更平缓；
3. rank2 AWGN：高 SNR goodput 饱和值应约为 rank1 的 2 倍；
4. rank2 Rayleigh：曲线应比 rank2 AWGN 更差，且受第二奇异值影响。

为了缩短测试时间，先用：

```text
n_trials_per_snr = 3000
batch_size = 2048
SNR step = 2 dB
```

如果曲线合理，再对关键 MCS / SNR 区间增加 trial 或缩小 SNR step。
