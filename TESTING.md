# 测试说明：如何验证第一版代码正确性

## 1. 安装测试依赖

```bash
pip install numpy pyyaml matplotlib pytest
```

## 2. 运行全部单元测试

在项目根目录执行：

```bash
pytest -q
```

预期输出类似：

```text
6 passed in 0.xx s
```

## 3. 单元测试覆盖哪些内容

### 3.1 Scheme 7 整数分拆

测试文件：

```text
tests/test_core.py::test_integer_partitions_rank4
```

验证 rank=4 时 Scheme 7 是否生成：

```text
[4], [3,1], [2,2], [2,1,1], [1,1,1,1]
```

这对应补充文档中的连续层分组要求。

### 3.2 Scheme 1/4/6 的层分配

测试文件：

```text
tests/test_core.py::test_scheme_generation_rank4
```

验证：

```text
Scheme 1 -> [[0,1,2,3]]
Scheme 4 -> [[0,1], [2,3]]
Scheme 6 -> [[0], [1], [2], [3]]
```

### 3.3 Scheme 2 per-layer Qm

测试文件：

```text
tests/test_core.py::test_scheme2_per_layer_qm
```

验证 Scheme 2 会根据层 SINR 自动选择不同 Qm，而不是所有层使用同一调制阶数。

### 3.4 MCS 选择单调性

测试文件：

```text
tests/test_core.py::test_mcs_selection_monotonic
```

验证高 SINR 选择的 MCS 不应低于低 SINR 选择的 MCS。

### 3.5 TBS/CB 闭环

测试文件：

```text
tests/test_core.py::test_tb_manager_closure
```

验证每个 CW 满足：

```text
sum(E_cb) == n_bits_total
```

这是 rate matching 闭环最关键的检查。

### 3.6 SymbolMapping 数量检查

测试文件：

```text
tests/test_core.py::test_symbol_mapping_count
```

验证生成的调制符号数量与资源容量一致。

## 4. 运行冒烟仿真

```bash
python run.py --config configs/quick_test.yaml
```

检查：

1. 程序能正常结束；
2. 输出目录下有 `results.csv`；
3. 输出目录下有 `bler_vs_snr.png`；
4. 输出目录下有 `throughput_vs_snr.png`；
5. `results.csv` 中不同 scheme 有不同 throughput/BLER。

## 5. 检查 Scheme 2 是否使用了 per-layer Qm

运行：

```bash
python run.py --config configs/quick_test.yaml
```

打开输出目录中的 `results.csv`，查看列：

```text
meta_cw0_qms
```

对于 Scheme 2，应该能看到类似：

```text
{0: 8, 1: 6, 2: 4, 3: 2}
```

或者其他随 SNR 和信道变化的不同层 Qm 组合。

## 6. 检查 per-CW adaptive MCS 是否生效

打开 `results.csv`，查看：

```text
meta_cw0_mcs
meta_cw1_mcs
meta_cw0_rate
meta_cw1_rate
```

对于 Scheme 4/5/6/7，强层 CW 和弱层 CW 往往会选择不同 MCS 或不同码率。

例如 rank=4、Scheme 4 中：

```text
CW0 layers [0,1] 通常 MCS 更高
CW1 layers [2,3] 通常 MCS 更低
```

## 7. fixed MCS debug 模式

运行：

```bash
python run.py --config configs/fixed_mcs_debug.yaml
```

此时所有普通 scheme 的 CW 都强制使用 MCS 10。这个模式用于 debug，不是第一版主流程。

## 8. 当前版本的验证边界

当前版本验证的是：

- scheme 配置逻辑；
- MCS/Qm 自适应逻辑；
- TBS/CB 分段逻辑；
- 多方案统计流程；
- 曲线输出流程。

当前版本尚未验证：

- Sionna LDPC 编解码；
- Sionna CDL 信道；
- MMSE detector 输出真实 LLR；
- bit-level BLER 曲线。

这些应作为下一阶段 Sionna backend 的测试内容。

## bit-level v0 测试

运行：

```bash
python run.py --config configs/bit_level_rank1_awgn.yaml
```

检查：

1. `results_bit_level/` 下应生成 `results.csv`、`results.json`、`bler_vs_snr.png`、`throughput_vs_snr.png`。
2. AWGN + fixed MCS 下，BLER 应整体随 SNR 下降。
3. goodput 应随 SNR 增大而上升，并在高 SNR 附近接近固定 TBS。
4. `results.csv` 中 `meta_backend_note` 应为 `numpy_bit_level_v0_toy_code_not_ldpc`。

若要看更平滑曲线，可把 `configs/bit_level_rank1_awgn.yaml` 中的 `n_trials_per_snr` 增大。
