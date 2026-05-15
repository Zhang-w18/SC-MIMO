# lls_platform_v2.4_bit_level：多 CW / 多 Scheme Sionna bit-level 后端

## 主要更新

v2.4 在 v2.3 的通用 `GridMappingPlan` 基础上，新增了多 CW 支持：

- 支持 `rank=1~8`；
- 支持 `Scheme 1/2/3/4/5/6/7` 的 fixed-MCS 模式；
- 支持多 CW：每个 CW 独立 payload、LDPC 编码/解码、QAM、GridMapping、LLR gather 与统计；
- 继续支持 AWGN 与 flat Rayleigh + ideal SVD-equivalent layers；
- 继续支持 GPU batch 并行与多 MCS 多 GPU 并发脚本；
- CSV 会输出每个 CW 的 BLER、CB-BLER、TBS、goodput，以及每个 CW 的 grid route。

## 当前限制

为了先稳定验证多 CW / 多 Scheme 逻辑，v2.4 仍保留以下限制：

- 只支持 `fixed_mcs`，暂不启用 adaptive MCS；
- Scheme 2 在 `fixed_mcs` 模式下退化为统一 Qm/rate，暂不启用 per-layer Qm；
- 每个 CW 当前只支持 1 个 CB；如果资源/MCS 太大导致某 CW 被分成多个 CB，会主动报错；
- Rayleigh 是 flat Rayleigh + ideal SVD-equivalent layers，不是 CDL/OFDM，也不是真实 MMSE detector；
- 不支持真实 DMRS、信道估计误差、HARQ。

## 新增配置

快速测试配置：

```text
configs/sionna_ldpc_rank2_all_schemes_awgn_gpu_fast.yaml
configs/sionna_ldpc_rank2_all_schemes_rayleigh_gpu_fast.yaml
configs/sionna_ldpc_rank4_all_schemes_awgn_gpu_fast.yaml
configs/sionna_ldpc_rank4_all_schemes_rayleigh_gpu_fast.yaml
configs/sionna_ldpc_rank8_all_schemes_awgn_gpu_fast.yaml
configs/sionna_ldpc_rank8_all_schemes_rayleigh_gpu_fast.yaml
```

这些配置默认：

```yaml
comparison:
  enabled: true
  schemes:
    - {scheme_id: 1}
    - {scheme_id: 2}
    - {scheme_id: 3}
    - {scheme_id: 4}
    - {scheme_id: 5}
    - {scheme_id: 6}
    - {scheme_id: 7}
```

因此会一次跑指定 rank 下所有 scheme。其中 Scheme 7 会自动展开该 rank 的所有连续整数分拆。

## 推荐运行方式

先激活环境，并设置 XLA CUDA 路径：

```bash
conda activate tf_sionna_rt
export XLA_FLAGS=--xla_gpu_cuda_data_dir=/home/zhangwei/anaconda3/envs/sionna_1.2/lib/python3.10/site-packages/nvidia/cuda_nvcc
```

### rank=2，所有 scheme，AWGN，多 MCS，多 GPU

```bash
python tools/run_multi_mcs.py \
  --base-config configs/sionna_ldpc_rank2_all_schemes_awgn_gpu_fast.yaml \
  --mcs-list 3,5,8,10 \
  --output-root results_v24_rank2_all_schemes_awgn_multi_mcs \
  --parallel \
  --gpu-list 0,1,2,3
```

### rank=4，所有 scheme，AWGN，多 MCS，多 GPU

```bash
python tools/run_multi_mcs.py \
  --base-config configs/sionna_ldpc_rank4_all_schemes_awgn_gpu_fast.yaml \
  --mcs-list 3,5,8,10 \
  --output-root results_v24_rank4_all_schemes_awgn_multi_mcs \
  --parallel \
  --gpu-list 0,1,2,3
```

### rank=8，所有 scheme，AWGN，多 MCS，多 GPU

rank=8 的 scheme7 partition 很多，建议先用较少 MCS 或较少 GPU 进行 sanity test：

```bash
python tools/run_multi_mcs.py \
  --base-config configs/sionna_ldpc_rank8_all_schemes_awgn_gpu_fast.yaml \
  --mcs-list 3,5 \
  --output-root results_v24_rank8_all_schemes_awgn_multi_mcs \
  --parallel \
  --gpu-list 0,1
```

## 验证重点

1. AWGN 下，MCS 越高，BLER 曲线越右移；
2. 同一 rank/MCS 下，多 CW 方案的 high-SNR goodput 应与对应总 TBS 对齐；
3. Rayleigh 下曲线应比 AWGN 更差、更平缓；
4. CSV 中 `meta_num_cws`、`cwX_*` 字段应正确反映各 scheme 的 CW 数；
5. `meta_cwX_grid_route` 应显示每个 CW 映射到哪些 layer、RE、bit range。

## 下一步建议

v2.4 跑通后，建议下一步再做：

1. 多 CB per CW 支持；
2. Scheme 2 per-layer Qm 自适应；
3. adaptive MCS；
4. soft-output MMSE detector；
5. CDL/OFDM 频选信道。
