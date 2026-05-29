# Phase 3b Adaptive-MCS Full-Resource Experiment

This README explains how to run the Phase 3b experiment derived from Phase 3a
`abstract CSI-error fixed-QPSK BLER`.

Main changes from Phase 3a:

- Adaptive MCS instead of fixed QPSK.
- Full Qualcomm-aligned resource use: `106 RB * 12 subcarriers * 13 PDSCH symbols = 16536 RE/layer`.
- No sampled data RE / small-resource interpretation.
- Outputs MCS CDF, goodput curves, and CB-size histograms.

## Files

```text
tools/run_sc_mimo_phase3_adaptive_mcs.py
configs/sc_mimo_phase3_adaptive_mcs_full_resource.yaml
docs/SC_MIMO_implementation_plan.md
```

## Server Environment

Use the Sionna/TensorFlow environment on the server:

```bash
/home/zhangwei/anaconda3/envs/tf_sionna_rt/bin/python
```

The runner does not require pandas. It uses `numpy`, `scipy`, `matplotlib`,
`pyyaml`, `tensorflow`, and `sionna`.

## Dry Run

From the repo root:

```bash
/home/zhangwei/anaconda3/envs/tf_sionna_rt/bin/python \
  tools/run_sc_mimo_phase3_adaptive_mcs.py \
  --config configs/sc_mimo_phase3_adaptive_mcs_full_resource.yaml \
  --dry-run
```

This only prints the resolved sweep settings and does not run simulation.

## Full Multi-GPU Run

Recommended server command:

```bash
/home/zhangwei/anaconda3/envs/tf_sionna_rt/bin/python \
  tools/run_sc_mimo_phase3_adaptive_mcs.py \
  --config configs/sc_mimo_phase3_adaptive_mcs_full_resource.yaml \
  --parallel-gpus 2,3,4,5,7
```

`--parallel-gpus` launches at most one active worker per listed GPU. If there
are more SNR points than GPUs, the runner executes them in batches and
aggregates all worker outputs at the end. Each worker gets one visible GPU
through `CUDA_VISIBLE_DEVICES`.

The default output directory is:

```text
results_phase3_adaptive_mcs_full_resource/
```

## Run One SNR

For debugging a single configured SNR point:

```bash
/home/zhangwei/anaconda3/envs/tf_sionna_rt/bin/python \
  tools/run_sc_mimo_phase3_adaptive_mcs.py \
  --config configs/sc_mimo_phase3_adaptive_mcs_full_resource.yaml \
  --snr-index 0 \
  --output-dir results_phase3b_snr0_debug
```

`--snr-index 0` means the first SNR in the YAML list.

## One-Trial Smoke Test

For a very small correctness smoke test:

```bash
/home/zhangwei/anaconda3/envs/tf_sionna_rt/bin/python - <<'PY'
from lls_platform.sim.sc_mimo_orchestrator import run_phase3_adaptive_mcs_full_resource_sweep

run_phase3_adaptive_mcs_full_resource_sweep(
    snr_db_values=[-12.0],
    ranks=[4],
    csi_error_cases=[("ideal_csi", None, None)],
    n_trials_per_snr=1,
    output_dir="results_phase3b_smoke",
    max_mcs=2,
    detector="batch_mmse",
    num_iter=2,
    seed=11,
)
PY
```

This should produce `results_phase3b_smoke/results.csv`,
`mcs_schedule.csv`, `cb_schedule.csv`, MCS CDF, goodput curve, and CB
histograms.

## Adaptive MCS Rule

Each trial selects one MCS shared by the three schemes:

```text
nr_1cw
sc_mimo_sic
baseline_2cw
```

The rule is:

1. Generate CDL-A full-grid channel.
2. Build per-RE SVD precoder from `H_tx_hat`.
3. Build true and receiver-estimated effective channels.
4. Estimate per-RE/layer post-MMSE SINR from the receiver-estimated effective channel.
5. Compute average effective spectral efficiency:

```text
SE_eff = mean log2(1 + 10^((SINR_db - mcs_margin_db)/10) / gap_linear)
```

6. Select the highest MCS in `nr_256qam` satisfying:

```text
Qm * code_rate <= SE_eff
```

Default adaptation parameters are in the YAML:

```text
mcs_table_name: nr_256qam
min_code_rate: 0.2
shannon_gap_db: 2.5
mcs_margin_db: 1.0
```

## SC-MIMO Mapping Parameters

For rank 4:

```text
layer_groups = [[0, 1], [2, 3]]
shift_pattern = [0, 1]
termination = cyclic
strict rectangular tile = true
```

For one CB:

```text
Nsym_i = E_i / Qm
T_i = Nsym_i / rank
part_symbols_i,g = T_i * |group_g|
part_re_rows_i,g = T_i
```

So in the default rank4 case, each CB has two parts. Each part occupies
`T_i` RE rows and `2*T_i` QAM symbols.

The per-CB values are written to:

```text
cb_schedule.csv
```

Important columns:

```text
cw_n_cbs
cb_payload_bits_before_rm
cb_rate_matched_bits_e
cb_qam_symbols
sc_mimo_tile_rows
sc_mimo_part_symbols_by_group
sc_mimo_part_re_rows_by_group
```

`cb_payload_bits_before_rm` is the real information-bit payload counted in
goodput. `cb_rate_matched_bits_e` is the coded-bit length after rate matching
and is not counted as throughput.

## Outputs

Top-level and per-case directories contain:

```text
results.csv
results.json
mcs_schedule.csv
cb_schedule.csv
cb_bler_vs_snr.png
tb_bler_vs_snr.png
goodput_se_vs_snr.png
mcs_cdf.png
cw_cb_count_hist.png
cb_payload_bits_hist.png
cb_rate_matched_bits_hist.png
```

Meaning:

- `mcs_cdf.png`: CDF of scheduled MCS index per scheme.
- `goodput_se_vs_snr.png`: goodput spectral efficiency vs SNR.
- `cw_cb_count_hist.png`: histogram of CB count per scheduled codeword.
- `cb_payload_bits_hist.png`: histogram of CB information-bit payload before rate matching.
- `cb_rate_matched_bits_hist.png`: histogram of rate-matched coded-bit length `E`.

## Postprocessing

No separate postprocessing command is required for the normal multi-GPU run.
The launcher automatically aggregates worker outputs after all SNR workers
finish and redraws the top-level plots.

Worker outputs are kept under:

```text
results_phase3_adaptive_mcs_full_resource/_workers/
```

If a run is interrupted, rerun the same command. Existing output directories
will be overwritten as the corresponding worker finishes.

## Notes

- The current full-resource runner uses batched linear-MMSE soft detection for
  server-scale feasibility.
- The receiver uses the per-RE effective channel with optional abstract NMSE
  error. This is still an abstract CSI-error experiment, not DMRS/RMMSE channel
  estimation.
- Throughput/goodput only counts successfully decoded information payload bits,
  not rate-matched coded bits.
