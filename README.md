# 灵活码字映射 LLS Platform v2.1

本项目用于比较不同 codeword-to-layer mapping 方案。当前包含三个 backend：

- `link_abstraction`：链路抽象后端，快速验证 Scheme/MCS/TBS/CB/goodput 逻辑。
- `numpy_bit_level`：纯 NumPy toy bit-level 后端，用于验证 bit 流闭环，不是 NR LDPC。
- `sionna_ldpc_bit_level`：Sionna 真实 LDPC bit-level 最小后端，当前支持 rank=1、单 CW、fixed MCS、AWGN。

## 安装基础依赖

link abstraction / numpy backend：

```bash
python -m pip install numpy pyyaml matplotlib pytest
```

Sionna LDPC backend 额外需要：

```bash
python -m pip install tensorflow sionna
```

具体 TensorFlow/Sionna 版本请根据你的 CUDA/Python 环境选择。如果你的 Python 是 3.7，可能需要使用与 Python 3.7 兼容的较旧 TensorFlow/Sionna 版本。

## 快速运行

链路抽象：

```bash
python run.py --config configs/quick_test.yaml
```

NumPy toy bit-level：

```bash
python run.py --config configs/bit_level_rank1_awgn.yaml
```

Sionna LDPC bit-level：

```bash
python run.py --config configs/sionna_ldpc_rank1_awgn.yaml
```

## 标准 MCS 表

`lls_platform/utils/mcs_tables.py` 支持：

- `nr_64qam`：3GPP TS 38.214 Table 5.1.3.1-1
- `nr_256qam`：3GPP TS 38.214 Table 5.1.3.1-2
- `approx_256qam`：旧近似表，仅用于回归/抽象测试

推荐 bit-level 调试先用：

```yaml
simulation:
  mcs_table: nr_64qam
  fixed_mcs: 2
```

## 重要说明

`required_sinr_db` 不是 3GPP MCS 表的一部分，只供 link abstraction 使用。真实 bit-level 后端直接通过解码后 bit comparison 统计 BLER。

## v2.2 GPU Batch 更新

本项目新增 `sionna_ldpc_bit_level` 的 batch-first GPU 并行版本。详细说明见：

```text
V2_2_GPU_BATCH_README.md
```

推荐先运行：

```bash
export XLA_FLAGS=--xla_gpu_cuda_data_dir=/home/zhangwei/anaconda3/envs/sionna_1.2/lib/python3.10/site-packages/nvidia/cuda_nvcc
export CUDA_VISIBLE_DEVICES=0
python run.py --config configs/sionna_ldpc_rank1_awgn.yaml
```

多 MCS 批量运行：

```bash
python tools/run_multi_mcs.py \
  --base-config configs/sionna_ldpc_rank1_awgn.yaml \
  --mcs-list 3,5,8,10 \
  --output-root results_sionna_ldpc_multi_mcs \
  --cuda-visible-devices 0
```

## v2.4 multi-CW bit-level update

See `V2_4_MULTI_CW_README.md` for the multi-CW Sionna LDPC backend. v2.4 supports rank 1--8 and Scheme 1--7 in fixed-MCS mode with GPU batch execution.

## v2.6 CDL + per-trial adaptive MCS

v2.6 增加 Sionna CDL 信道入口和 per-trial adaptive MCS。详见：

```text
V2_6_CDL_ADAPTIVE_MCS_README.md
```

快速运行：

```bash
python run.py --config configs/sionna_ldpc_rank2_all_schemes_cdl_adaptive.yaml
```

## Phase 3b adaptive-MCS full-resource experiment

服务器版 SC-MIMO Phase 3b 实验说明见：

```text
PHASE3_ADAPTIVE_MCS_FULL_RESOURCE_README.md
```
