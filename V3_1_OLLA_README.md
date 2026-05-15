# v3.1 OLLA update

v3.1 adds a per-CW outer-loop link adaptation (OLLA) mode on top of v3.0.

For each `(SNR, scheme, CW)`, the simulator keeps a SINR offset in dB. The offset is applied to the effective layer SINR used by the existing MCS selector. After decoding a trial, the offset is updated from the instantaneous CB error fraction:

```text
e_t = failed_CBs_in_this_trial_for_this_CW / total_CBs_in_this_trial_for_this_CW
offset_{t+1} = clip(offset_t + step_db * (target_cb_bler - e_t), offset_min_db, offset_max_db)
```

This supports normal schemes and Scheme2 uniformly:

- normal schemes: offset changes the SE used to choose the MCS index;
- Scheme2: offset changes the per-layer SE seen by the unified-rate/per-layer-Qm optimizer, indirectly changing common rate `r` and layer Qm values.

OLLA records are written into `cw_trial_link_adaptation_records.csv` with fields such as:

- `olla_phase` (`warmup` or `measure`)
- `olla_offset_db_before`
- `olla_offset_db_after`
- `olla_offset_update_db`
- `olla_cb_error_ewma`
- `mcs_selection_target_se_total`

Final `results.csv` statistics count only measurement trials. Warmup rows are retained in `cw_trial_link_adaptation_records.csv` for trace plots.

Postprocessing adds:

- `olla_mcs_trace_by_scheme_snr/`
- `olla_code_rate_trace_by_scheme_snr/`
- `olla_offset_trace_by_scheme_snr/`
- `olla_cb_error_ewma_by_scheme_snr/`

Statistical CDF plots use measurement-phase rows only when OLLA records are present.
