# 多 MCS 批处理脚本说明

## 文件位置

建议把脚本放在项目根目录下：

```text
tools/run_multi_mcs.py
```

`tools/` 文件夹用于存放批量运行、后处理、画图等辅助脚本，不影响主程序。

## 功能

该脚本会：

1. 读取一个基础 YAML 配置；
2. 根据 `--mcs-list` 自动生成多个临时配置；
3. 逐个调用 `python run.py --config 临时配置`；
4. 收集每个 MCS 的 `results.csv`；
5. 合并为 `merged_results.csv`；
6. 生成多 MCS 的 BLER、goodput、goodput SE 曲线。

## 使用示例

在项目根目录运行：

```bash
python tools/run_multi_mcs.py \
  --base-config configs/sionna_ldpc_rank1_awgn.yaml \
  --mcs-list 3,5,8,10 \
  --output-root results_sionna_ldpc_multi_mcs
```

如果想强制不用 GPU，用 CPU 跑：

```bash
python tools/run_multi_mcs.py \
  --base-config configs/sionna_ldpc_rank1_awgn.yaml \
  --mcs-list 3,5,8,10 \
  --output-root results_sionna_ldpc_multi_mcs_cpu \
  --force-cpu
```

## 输出文件

```text
results_sionna_ldpc_multi_mcs/
├── base_config_used.yaml
├── merged_results.csv
├── multi_mcs_bler_vs_snr.png
├── multi_mcs_goodput_vs_snr.png
├── multi_mcs_goodput_se_tf_vs_snr.png
├── temp_configs/
│   ├── config_mcs_3.yaml
│   ├── config_mcs_5.yaml
│   └── ...
└── runs/
    ├── mcs_3/
    │   └── sim_YYYYMMDD_HHMMSS/
    │       └── results.csv
    ├── mcs_5/
    └── ...
```

## 说明

- 该脚本不修改 `run.py`、orchestrator 或主项目逻辑。
- 暂时不补 `min_block_errors` 动态停止逻辑。
- 目前更适合用于 rank=1、AWGN、fixed MCS 的 Sionna LDPC sanity check。

## v2.2：指定 GPU

如果希望只使用一张 GPU，例如物理 GPU 0：

```bash
python tools/run_multi_mcs.py \
  --base-config configs/sionna_ldpc_rank1_awgn.yaml \
  --mcs-list 3,5,8,10 \
  --output-root results_sionna_ldpc_multi_mcs \
  --cuda-visible-devices 0
```

如果当前环境需要设置 XLA CUDA libdevice 路径，请先在同一个终端设置：

```bash
export XLA_FLAGS=--xla_gpu_cuda_data_dir=/home/zhangwei/anaconda3/envs/sionna_1.2/lib/python3.10/site-packages/nvidia/cuda_nvcc
```

如果只想 CPU 跑：

```bash
python tools/run_multi_mcs.py \
  --base-config configs/sionna_ldpc_rank1_awgn.yaml \
  --mcs-list 3,5,8,10 \
  --output-root results_sionna_ldpc_multi_mcs_cpu \
  --force-cpu
```
