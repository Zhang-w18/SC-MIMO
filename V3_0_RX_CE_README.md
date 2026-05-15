# v3.0 Receiver Channel-Estimation Abstraction

v3.0 extends the v2.9 bit-level CDL/adaptive-MCS platform with a lightweight
receiver-side channel-estimation error model.

## Scope

The transmitter still uses ideal CDL/SVD precoding. The first v3.0 model only
adds non-ideal CSI at the UE receiver:

```text
H_eff     = H_true V_svd
H_hat_eff = H_eff + E
W         = linear_equalizer(H_hat_eff)
G_actual  = W H_eff
```

The actual post-SINR of layer `l` is computed as

```text
SINR_l_actual = |G_ll|^2 P_l /
                (sum_{j != l}|G_lj|^2 P_j + N0 ||w_l||^2)
```

where `P_l=1/rank` and `N0=1/SNR_linear`. This captures three receiver-side
loss mechanisms:

1. desired gain loss from equalizer mismatch;
2. residual inter-layer interference;
3. noise enhancement.

## New config

Use the RX-CE example config:

```bash
configs/sionna_ldpc_7ghz_128t16r_cdl_adaptive_rxce_base.yaml
```

Main fields:

```yaml
channel_estimation:
  enabled: true
  mode: additive_error
  error_model: nmse
  nmse_db: -25.0
  equalizer: mmse
  use_actual_rx_post_sinr_for_decoding: true
  apply_to_mcs_selection: false
  seed_base: 93001
```

`apply_to_mcs_selection` is deliberately false in v3.0-a: MCS is still selected
from the ideal post-SINR, while decoding uses the actual RX post-SINR. This
isolates receiver channel-estimation effects from CQI/MCS feedback errors.

## New output columns

`layer_post_sinr_samples.csv` keeps the old `post_sinr_db` column as the ideal
reference and adds:

- `post_sinr_ideal_db`
- `post_sinr_actual_rx_db`
- `post_sinr_estimated_by_ue_db`
- `post_sinr_loss_db = ideal - actual`
- `rx_ce_nmse_db_config`
- `rx_ce_nmse_db_measured`
- `desired_power_mean`
- `inter_layer_interference_power_mean`
- `noise_enhancement_power_mean`

`cw_trial_link_adaptation_records.csv` adds CW-level aggregated RX-CE metrics:

- `actual_rx_target_se_total`
- `actual_rx_post_sinr_db_mean/min/std/gap`
- `ue_est_post_sinr_db_mean`
- `post_sinr_loss_db_mean/min/max`
- `rx_ce_nmse_db_mean`

## Post-processing

`tools/postprocess_curves.py` now plots additional scheme-independent layer CDFs:

- ideal post-SINR CDF;
- actual RX post-SINR CDF;
- UE-estimated post-SINR CDF;
- post-SINR loss CDF;
- RX CE NMSE CDF;
- ideal/actual layer effective SE CDF.

Each PNG has a same-name CSV with the plotted data points.
