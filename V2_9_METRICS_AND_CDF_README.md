# lls_platform v2.9 bit-level

This version is based on the updated v2.8 baseline and adds lightweight per-CW/per-trial metrics plus CDF post-processing.

## Main changes

1. `run.py` remains single-rank.
2. CDL common-channel fairness uses batch-level deterministic streaming rather than full-SNR caching.
3. Same SNR/trial_id uses deterministic AWGN noise across schemes.
4. `layer_post_sinr_samples.csv` is still generated for layer post-SINR CDF analysis.
5. New `cw_trial_link_adaptation_records.csv` records one row per SNR/scheme/trial/CW.
6. Post-processing now draws:
   - MCS quantization/ceiling loss CDF by SNR across schemes.
   - Coding/CB failure loss CDF by SNR across schemes.
   - Per-scheme per-CW MCS CDF by SNR.
   - Per-layer post-SINR CDF by SNR.
7. `tb_manager.py` uses a 38.212-style base-graph selection with Sionna-compatible fallback from BG1 to BG2 when low-rate BG1 would trigger Sionna's unsupported repetition/rate-matching path.
8. `run_multi_rank.py` supports per-rank Scheme7 partition configuration via `--scheme7-partitions-yaml`.

## Example: run rank8

```bash
python -u tools/run_multi_rank.py \
  --base-config configs/sionna_ldpc_7ghz_128t16r_cdl_adaptive_base.yaml \
  --rank-list 8 \
  --output-root results_v29_rank8_streaming_metrics_bs8 \
  --scheme7-partitions-yaml configs/scheme7_partitions_rank2_4_8_12_16.yaml \
  --gpu-list 7
```

## Post-processing

```bash
python tools/postprocess_curves.py \
  --output-root results_v29_rank8_streaming_metrics_bs8 \
  --rank 8 \
  --channel CDL
```

New CDF outputs are written under the `curve_exports_rank*_*/` directory.
