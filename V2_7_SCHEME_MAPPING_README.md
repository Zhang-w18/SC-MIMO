# v2.7 flexible_cw 方案定义与 CW-to-layer 映射说明

本文档说明 `lls_platform_v2.7_bit_level` 中 Scheme 1--7 的实际实现。相比 v2.6，v2.7 按你的要求做了以下修正：

1. CW 内多层映射从“按层连续切段”改为 **NR-like symbol round-robin**。
2. Scheme 7 支持在 YAML 中指定单个子方案，例如 `partition: [2, 2, 1]`。
3. Scheme 1 在 rank>8 时不生效。
4. Scheme 2 在 rank>8 时仍生效，但 CW 划分改用 Scheme 4 的最多 2 CW 规则。
5. Scheme 4 在奇数 rank 时使用 `[floor(rank/2), ceil(rank/2)]`，即第一个 CW 的层数小于第二个 CW。
6. CDL/SVD 后端中 layer 顺序显式解释为 **按 post-SINR / SVD singular value 从大到小排序**。
7. adaptive MCS 增加 `min_code_rate=0.2` 过滤，避免 Sionna LDPC `r<1/5` 报错。

---

## 1. v2.7 的 CW 内 layer mapping 规则

如果一个 CW 映射到多个 layer，例如：

```text
CW0 -> L0, L1
```

v2.7 默认采用 **RE-major, layer-minor 的符号轮流映射**：

```text
CW0 coded-bit stream
  -> symbol/chunk 0 -> RE0, L0
  -> symbol/chunk 1 -> RE0, L1
  -> symbol/chunk 2 -> RE1, L0
  -> symbol/chunk 3 -> RE1, L1
  -> ...
```

因此，在同一个时频 RE 上，`L0` 和 `L1` 的两个调制符号来自原始 CW 调制符号流的相邻位置。

对于普通 scheme，CW 内所有 layer 的 Qm 相同，因此每次轮流取相同长度的 Qm bit。对于 Scheme 2，CW 内不同 layer 可能有不同 Qm，v2.7 仍使用同样的 RE/layer 顺序，但每个 layer 消耗的 bit chunk 长度由该层 Qm 决定。

接收端使用同一个 `GridMappingPlan.bit_indices` 把每个 layer/RE 的 LLR scatter 回原始 CW coded-bit stream，所以可以恢复每个 CB 对应的编码后 bit LLR 序列。

当前仍未实现：

```text
PRB 子集映射、时频交错、RE shift、per-PRB MCS。
```

---

## 2. Scheme 总体规则

| Scheme | Rank 适用范围 | Partition 规则 | CW 数 | 调制/码率规则 | CW-to-layer 映射 |
|---|---:|---|---:|---|---|
| Scheme 1 | 1--8 | rank≤4: `[rank]`; rank5--8: `[2,3]`,`[3,3]`,`[3,4]`,`[4,4]` | 1 或 2 | 每个 CW 内统一 MCS/Qm/rate；adaptive 时每 CW 独立选 MCS | 连续层分组；CW 内 symbol round-robin |
| Scheme 2 | 1--16 | rank≤8 同 Scheme 1；rank>8 同 Scheme 4 | 1 或 2 | 每层独立 Qm；每个 CW 统一 code rate | 连续层分组；CW 内 variable-Qm round-robin |
| Scheme 3 | 1--16 | `[rank]` | 1 | 整个 rank 一个 CW；CW 内统一 MCS/Qm/rate | 一个 CW 占所有层；symbol round-robin |
| Scheme 4 | 1--16 | rank1: `[1]`; rank>1: `[floor(rank/2), ceil(rank/2)]` | 1 或 2 | 每个 CW 内统一 MCS/Qm/rate | 连续层分组；symbol round-robin |
| Scheme 5 | 1--16 | `[2,2,...]`，奇数 rank 最后一个 `[1]` | `ceil(rank/2)` | 每 CW 最多 2 层；CW 内统一 MCS/Qm/rate | 连续层分组；symbol round-robin |
| Scheme 6 | 1--16 | `[1,1,...,1]` | rank | 每层一个 CW | 每 CW 单层，无跨层轮流 |
| Scheme 7 | 1--16 | 默认非增整数分拆；也可指定 `partition` / `partitions` | 1 到 rank | 每个 CW 内统一 MCS/Qm/rate | 连续层分组；symbol round-robin |

---

## 3. Scheme 7 指定子方案

### 3.1 单个 partition

```yaml
mapping:
  scheme: flexible_cw
  scheme_id: 7
  rank: 5
  params:
    partition: [2, 2, 1]
```

对应：

```text
CW0 -> L0,L1
CW1 -> L2,L3
CW2 -> L4
```

### 3.2 多个指定 partition

```yaml
comparison:
  enabled: true
  schemes:
    - scheme_id: 7
      rank: 8
      partitions:
        - [2, 2, 2, 2]
        - [3, 3, 2]
        - [4, 2, 1, 1]
```

### 3.3 Scheme7 枚举模式

默认：

```yaml
scheme7_mode: partitions_nonincreasing
```

这会生成非增整数分拆，例如 rank4：

```text
[4], [3,1], [2,2], [2,1,1], [1,1,1,1]
```

如果你要严格枚举所有有序组合，可以配置：

```yaml
scheme7_mode: ordered_compositions
```

这会包含 `[1,3]`, `[1,2,1]`, `[3,1]` 等所有有序 composition，但 rank 较大时数量会快速增加。

---

## 4. Rank 1--16 下 Scheme 1--6 的分配表

| Rank | Scheme 1 | Scheme 2 | Scheme 3 | Scheme 4 | Scheme 5 | Scheme 6 |
|---:|---|---|---|---|---|---|
| 1 | `[1]` | `[1]` | `[1]` | `[1]` | `[1]` | `[1]` |
| 2 | `[2]` | `[2]` | `[2]` | `[1,1]` | `[2]` | `[1,1]` |
| 3 | `[3]` | `[3]` | `[3]` | `[1,2]` | `[2,1]` | `[1,1,1]` |
| 4 | `[4]` | `[4]` | `[4]` | `[2,2]` | `[2,2]` | `[1,1,1,1]` |
| 5 | `[2,3]` | `[2,3]` | `[5]` | `[2,3]` | `[2,2,1]` | `[1,1,1,1,1]` |
| 6 | `[3,3]` | `[3,3]` | `[6]` | `[3,3]` | `[2,2,2]` | `[1,1,1,1,1,1]` |
| 7 | `[3,4]` | `[3,4]` | `[7]` | `[3,4]` | `[2,2,2,1]` | `[1,1,1,1,1,1,1]` |
| 8 | `[4,4]` | `[4,4]` | `[8]` | `[4,4]` | `[2,2,2,2]` | `[1,1,1,1,1,1,1,1]` |
| 9 | 不生效 | `[4,5]` | `[9]` | `[4,5]` | `[2,2,2,2,1]` | `[1,1,1,1,1,1,1,1,1]` |
| 10 | 不生效 | `[5,5]` | `[10]` | `[5,5]` | `[2,2,2,2,2]` | `[1,1,1,1,1,1,1,1,1,1]` |
| 11 | 不生效 | `[5,6]` | `[11]` | `[5,6]` | `[2,2,2,2,2,1]` | `[1,1,1,1,1,1,1,1,1,1,1]` |
| 12 | 不生效 | `[6,6]` | `[12]` | `[6,6]` | `[2,2,2,2,2,2]` | `[1,1,1,1,1,1,1,1,1,1,1,1]` |
| 13 | 不生效 | `[6,7]` | `[13]` | `[6,7]` | `[2,2,2,2,2,2,1]` | `[1,1,1,1,1,1,1,1,1,1,1,1,1]` |
| 14 | 不生效 | `[7,7]` | `[14]` | `[7,7]` | `[2,2,2,2,2,2,2]` | `[1,1,1,1,1,1,1,1,1,1,1,1,1,1]` |
| 15 | 不生效 | `[7,8]` | `[15]` | `[7,8]` | `[2,2,2,2,2,2,2,1]` | `[1,1,1,1,1,1,1,1,1,1,1,1,1,1,1]` |
| 16 | 不生效 | `[8,8]` | `[16]` | `[8,8]` | `[2,2,2,2,2,2,2,2]` | `[1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1]` |

---

## 5. Layer 顺序和 post-SINR

v2.7 对 CDL/SVD 后端使用：

```text
layer0 >= layer1 >= ... >= layer(rank-1)
```

即 layer 按 SVD singular value / post-SINR 从大到小排序。MCS 选择、partition 分组、grid mapping 都基于这个排序后的 layer index。

对于 ideal SVD 接收，post-SINR 使用：

```text
SINR_l[t,f] = SNR_linear * sigma_l[t,f]^2 / rank
```

如果未来接收端改成非理想 SVD 或真实 LMMSE/MMSE detector，则应基于等效层信道：

```text
G = H W
```

以及线性均衡矩阵 `B` 计算：

```text
SINR_l = |b_l^H g_l|^2 P_l / (sum_{j!=l} |b_l^H g_j|^2 P_j + N0 ||b_l||^2)
```

如果使用 Sionna LMMSE equalizer 并获得 `no_eff_l`，可按其归一化约定用：

```text
post_sinr_l ≈ 1 / no_eff_l
```

v2.7 还没有真实 MMSE detector；这部分建议放到 v2.8。

---

## 6. 低码率约束

v2.7 对 adaptive MCS 增加：

```yaml
adaptive_mcs:
  min_code_rate: 0.2
```

普通 scheme 的 MCS 选择和 Scheme 2 的 unified code rate 选择都会过滤掉 `code_rate < 0.2` 的候选项，避免 Sionna `LDPC5GEncoder` 报：

```text
ValueError: Unsupported coderate (r<1/5).
```

固定 MCS 模式下，如果指定的 `fixed_mcs` 对应码率低于 `min_code_rate`，v2.7 会提前报错并提示提高 MCS。
