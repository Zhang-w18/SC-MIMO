# lls_platform v2.8: common channel/noise and layer post-SINR CDF

This version is based on the **updated v2.7** baseline, not the original v2.7 zip.

## Main changes

1. `run.py` still supports **one rank per run**.
2. For CDL/SVD runs, the simulator now uses a fair comparison rule:
   - for the same SNR and `trial_id`, all schemes share the same CDL/SVD singular-value samples;
   - for the same SNR and `trial_id`, all schemes share deterministic complex AWGN samples on the `[RE, layer]` grid.
3. Each run automatically saves:

```text
layer_post_sinr_samples.csv
```

This file contains one row per `snr_db, trial_id, layer_index` and is used by post-processing to draw empirical CDFs of the post-SINR of each SVD layer.

4. The default `run.py` figures now include:
   - `cb_bler_vs_snr.png`
   - `goodput_se_vs_snr.png`

Backward-compatible filenames are also saved:
   - `bler_vs_snr.png`
   - `throughput_vs_snr.png`

5. `tools/postprocess_curves.py` can automatically read:
   - `results.csv` from `run.py`
   - `merged_results.csv` from batch scripts
   - `layer_post_sinr_samples.csv` for CDF plots

## Post-processing example

```bash
python tools/postprocess_curves.py \
  --output-root results_v28_rank8_cdl/sim_YYYYMMDD_HHMMSS \
  --rank 8 \
  --channel CDL
```

It creates:

```text
curve_exports_rank8_CDL/
  layer_post_sinr_cdf/
    by_snr/
    by_layer/
    cdf_raw/
```

## Multi-rank testing

`run.py` remains single-rank. Use `tools/run_multi_rank.py` to run several ranks automatically:

```bash
python tools/run_multi_rank.py \
  --base-config configs/sionna_ldpc_rank8_all_schemes_cdl_adaptive.yaml \
  --rank-list 2,4,8 \
  --output-root results_v28_cdl_multi_rank \
  --parallel \
  --gpu-list 0,1,2
```

Each rank run uses common channel/noise **within that rank**. Cross-rank common-channel cache is not enabled in this version.
