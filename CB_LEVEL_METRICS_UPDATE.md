# CB 级错误统计更新说明

本版本在原有 `link_abstraction` 后端上，把错误统计从 **CW 级抽样** 下沉到了 **CB 级抽样**。MCS/Qm 自适应、TBS 计算和 CB 分段流程保持不变。

## 1. 保留的流程

```text
生成抽象 Rayleigh/SVD 信道奇异值
  -> 计算每层 post-SINR
  -> per-CW adaptive MCS
  -> Scheme 2 per-layer Qm 自适应选择
  -> TBS/CB 分段
  -> link abstraction 错误抽样
  -> 统计 CB-BLER / CW-BLER / scheme-BLER / goodput
```

## 2. MCS 与 TBS/CB 的顺序

先确定 MCS/Qm/code rate，再做 TBS/CB 分段：

```text
SINR + mapping scheme
  -> CW/layer 分配
  -> 每个 CW 的 MCS 或 Scheme 2 的 per-layer Qm + code rate
  -> n_bits_total
  -> TBS
  -> CB segmentation
  -> 每 CB 的 K、K_ldpc、N_null、E
```

## 3. TB/CW 错误概率

普通 scheme 使用线性域调和平均：

```text
SINR_eff = n / sum_i(1/SINR_i)
```

Scheme 2 如果同一 CW 内不同层 Qm 不同，则使用 Qm 加权调和平均：

```text
SINR_eff = sum_l(Qm_l) / sum_l(Qm_l / SINR_l)
```

然后用 logistic 模型得到 TB/CW 级错误概率：

```text
p_tb = 1 / (1 + exp((SINR_eff_dB - center) / slope))
center = required_sinr_db - slope * ln(9)
```

这样当 `SINR_eff_dB = required_sinr_db` 时，`p_tb ≈ 0.1`。

## 4. 从 TB/CW 错误概率换算到 CB 错误概率

如果一个 CW 有 `N_CB` 个 CB，并假设同一 CW 内 CB 错误近似独立同分布：

```text
p_tb = 1 - (1 - p_cb)^N_CB
```

因此：

```text
p_cb = 1 - (1 - p_tb)^(1/N_CB)
```

这样如果 MCS 表中的门限表示约 10% TB-BLER，那么即使一个 TB 被分成多个 CB，合成后的 CW/TB-BLER 仍然接近原来的 `p_tb`。

## 5. CB 级抽样

对每个 CB 抽一次错误事件：

```text
cb_error ~ Bernoulli(p_cb)
```

然后：

```text
cw_error = any(cb_error in this CW)
scheme_error = any(cw_error in this trial)
```

## 6. 输出指标

`results.csv` 新增或明确以下字段：

```text
scheme_bler             : scheme/slot-level BLER，任意 CW 错误则失败
scheme_cb_bler          : 所有 CB 的错误率
cwX_bler                : 第 X 个 CW 的 CW/TB-BLER，任意 CB 错误则该 CW 错误
cwX_cb_bler             : 第 X 个 CW 内的 CB-BLER
cwX_n_cbs               : 第 X 个 CW 的 CB 数
cwX_cb_trials           : 第 X 个 CW 的 CB 统计总数
cwX_cb_errors           : 第 X 个 CW 的 CB 错误总数
goodput_bits_per_slot   : 平均每 trial 成功 CB 的 payload bits
goodput_se_tf           : goodput / 每层可用数据 RE 数
goodput_se_layer_re     : goodput / (每层可用数据 RE 数 × rank)
```

为了兼容旧脚本，`total_throughput_bits_per_slot` 字段仍保留，但它现在等价于 `goodput_bits_per_slot`。

## 7. 吞吐量 / goodput 统计

每个 CW 的 TBS payload bits 按 CB 近似均分：

```text
base = TBS // N_CB
rem  = TBS % N_CB
payload_cb_i = base + 1, i < rem
payload_cb_i = base,     i >= rem
```

每个 trial 成功传输的信息比特数为：

```text
success_bits_trial = sum_{c,i} payload_cb_{c,i} * 1{CB_{c,i} success}
```

平均 goodput：

```text
goodput_bits_per_slot = mean_trial(success_bits_trial)
```

goodput 不统计 TB CRC、CB CRC、NULL/filler bits，只统计 TBS payload bits。

## 8. 重要说明

当前版本仍然是链路抽象后端，不是真实 LDPC bit-level 仿真。CB 错误事件是基于 SINR/MCS 的概率模型抽样得到的，不是通过 LDPC 解码和 bit comparison 得到的。

后续接入 Sionna bit-level backend 后，可以保留这些统计口径，但把 `cb_error` 的来源替换为：

```text
decoded_cb_bits == transmitted_cb_bits
```
