# lls_platform v2.6: CDL + per-trial adaptive MCS

## 新增能力

v2.6 在 v2.5 的 CB-level goodput 基础上增加：

1. **Sionna CDL 信道入口**
   - `channel.model: CDL`
   - `channel.cdl_type: A/B/C/D/E`
   - `channel.delay_spread_ns`
   - `channel.ue_speed_kmh`
   - `channel.direction: downlink`
   - BS/UE 阵列由 `antenna.bs_antenna_array`、`antenna.ue_antenna_array` 指定。

2. **Sionna TR38.901 天线端口顺序适配**
   - 简单双极化 `AntennaArray(num_rows,num_cols,dual)` 的 native port order：
     `port = pol*num_rows*num_cols + col*num_rows + row`
   - 即：先 pol1 全部空间端口，再 pol2 全部空间端口；每个极化块内部列优先。
   - 该逻辑在 `lls_platform/phy/antenna_layout.py` 中固化。

3. **CDL-derived per-RE SVD-equivalent layers**
   - Sionna CDL CIR: `[B,rx,rx_ant,tx,tx_ant,path,time]`
   - Sionna OFDM H: `[B,rx,rx_ant,tx,tx_ant,time,freq]`
   - 平台内部 H: `[B,time,freq,rx_ant,tx_ant]`
   - 每个 RE 做 SVD，得到每层 singular value，用作 ideal per-RE SVD 等效层增益。

4. **per-trial adaptive MCS**
   - `simulation.fixed_mcs: null`
   - `adaptive_mcs.enabled: true`
   - 每个 trial 根据信道的容量域 link quality 选择 MCS。
   - 普通 scheme：每个 CW 独立选择一个 MCS。
   - scheme2：每层独立选择 Qm，每个 CW 选择统一 code rate。

5. **按 shape 分组执行 LDPC**
   - per-trial MCS 会导致 TBS/CB/Grid shape 不同。
   - v2.6 会先为每个 trial 选 MCS，再把相同 shape 的 trial 分组 batch 执行 Sionna LDPC。

## 重要限制

v2.6 第一版仍是 ideal SVD 等效层链路：

- CDL 真实频选信道用于生成每个 RE 的奇异值；
- 发送/接收等效为 ideal per-RE SVD precoding/equalization；
- 暂未加入真实 MMSE detector；
- 暂未加入非理想信道估计；
- MCS 粒度是 per-trial per-CW，不是 per-PRB。

## 推荐测试命令

### rank2 CDL adaptive

```bash
python run.py --config configs/sionna_ldpc_rank2_all_schemes_cdl_adaptive.yaml
```

### rank4 CDL adaptive

```bash
python run.py --config configs/sionna_ldpc_rank4_all_schemes_cdl_adaptive.yaml
```

### rank8 CDL adaptive

```bash
python run.py --config configs/sionna_ldpc_rank8_all_schemes_cdl_adaptive.yaml
```

## 结果中重点查看

CSV 中新增或更新的 metadata：

- `meta_adaptive_mcs_enabled`
- `meta_adaptive_mcs_granularity`
- `meta_gamma_total_db`
- `meta_selected_mcs_hist`
- `meta_selected_qm_hist`
- `meta_layer_se_eff_mean`
- `meta_layer_sinr_eff_db_mean`
- `meta_goodput_accounting=cb_level_successful_payload_bits`

判断是否工作正常：

1. `fixed_mcs = -1`，说明不是固定 MCS。
2. `selected_mcs_hist` 随 SNR 升高整体右移。
3. scheme2 的 `selected_qm_hist` 中可能出现不同 layer 不同 Qm。
4. goodput 随 SNR 上升。
5. CB-level goodput 仍按成功 CB payload bits 累加。
