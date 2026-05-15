# lls_platform_v2.2_bit_level：Sionna LDPC GPU Batch 版说明

本版本在 v2.1 的基础上主要修改了 `sionna_ldpc_bit_level` 后端，使其支持 **batch-first GPU 并行**。

## 1. 主要改动

### 1.1 Sionna LDPC 后端批量化

修改文件：

```text
lls_platform/sim/sionna_ldpc_orchestrator.py
```

v2.1 中 Sionna LDPC 后端基本是逐 trial 执行：

```text
for trial in n_trials:
    payload -> LDPC -> Mapper -> AWGN -> Demapper -> Decoder
```

v2.2 改成按 batch 执行：

```text
for batch in n_trials / batch_size:
    [B, K] payload
      -> LDPC5GEncoder
      -> QAM Mapper
      -> AWGN
      -> Demapper LLR
      -> LDPC5GDecoder
      -> [B, K] bit comparison
```

其中 `B = simulation.batch_size`。

这能显著减少 Python 循环和 TensorFlow/Sionna Block 的重复调用开销，让 GPU 能处理更大的 Tensor。

### 1.2 Demapper 调用修正

当前 Sionna 1.x 版本应使用：

```python
llr = self.demapper(y, no)
```

不是：

```python
llr = self.demapper([y, no])
```

### 1.3 随机 bit 生成兼容

新增 `_rng_bits()`，兼容 NumPy 新版 `Generator.integers()` 和旧版 `RandomState.randint()`。

### 1.4 批处理脚本增强

`tools/run_multi_mcs.py` 新增：

```text
--cuda-visible-devices
```

例如只用物理 GPU 0：

```bash
python tools/run_multi_mcs.py \
  --base-config configs/sionna_ldpc_rank1_awgn.yaml \
  --mcs-list 3,5,8,10 \
  --output-root results_sionna_ldpc_multi_mcs \
  --cuda-visible-devices 0
```

如果想强制 CPU：

```bash
python tools/run_multi_mcs.py \
  --base-config configs/sionna_ldpc_rank1_awgn.yaml \
  --mcs-list 3,5,8,10 \
  --output-root results_sionna_ldpc_multi_mcs_cpu \
  --force-cpu
```

## 2. 推荐运行方式

### 2.1 先设置 XLA CUDA libdevice 路径

如果你的环境报过：

```text
libdevice not found
JIT compilation failed
```

先设置：

```bash
export XLA_FLAGS=--xla_gpu_cuda_data_dir=/home/zhangwei/anaconda3/envs/sionna_1.2/lib/python3.10/site-packages/nvidia/cuda_nvcc
```

确认：

```bash
echo $XLA_FLAGS
```

### 2.2 只使用一张 GPU

建议先只暴露一张 GPU，避免 TensorFlow 初始化所有 A100：

```bash
export CUDA_VISIBLE_DEVICES=0
```

或在批处理脚本中使用：

```bash
--cuda-visible-devices 0
```

### 2.3 单配置运行

```bash
python run.py --config configs/sionna_ldpc_rank1_awgn.yaml
```

### 2.4 GPU batch 示例配置

```bash
python run.py --config configs/sionna_ldpc_rank1_awgn_gpu_batch.yaml
```

默认：

```yaml
n_trials_per_snr: 10000
batch_size: 1024
```

如果显存不足，把 `batch_size` 改小，例如 512、256、128。

### 2.5 多 MCS 批量运行

```bash
python tools/run_multi_mcs.py \
  --base-config configs/sionna_ldpc_rank1_awgn.yaml \
  --mcs-list 3,5,8,10 \
  --output-root results_sionna_ldpc_multi_mcs \
  --cuda-visible-devices 0
```

输出：

```text
results_sionna_ldpc_multi_mcs/
├── merged_results.csv
├── multi_mcs_bler_vs_snr.png
├── multi_mcs_goodput_vs_snr.png
├── multi_mcs_goodput_se_tf_vs_snr.png
├── temp_configs/
└── runs/
```

## 3. 当前仍然保留的限制

v2.2 仍然是 Sionna LDPC 最小闭环验证版本：

```text
rank = 1
single CW
fixed MCS
AWGN
真实 Sionna LDPC + QAM + soft Demapper + LDPC Decoder
```

还没有扩展到：

```text
rank > 1
多 CW
Scheme 2 per-layer Qm
Rayleigh/CDL/OFDM/MIMO
多 GPU 分布式并行
min_block_errors 动态停止
```

## 4. batch_size 怎么选

经验建议：

```text
短 TB / rank=1 sanity：batch_size = 256 / 512 / 1024
如果 GPU 利用率低：尝试增大 batch_size
如果显存不足或 OOM：减小 batch_size
```

可以用另一个终端观察：

```bash
watch -n 1 nvidia-smi
```

如果只看到一个 GPU 有进程，是正常的。v2.2 是单进程单 GPU batch 并行，不是 8 卡分布式并行。
