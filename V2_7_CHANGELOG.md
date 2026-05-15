# lls_platform v2.7 bit-level changelog

v2.7 is based on v2.6 and implements the requested scheme/mapping fixes:

- CW-internal layer mapping now uses NR-like symbol round-robin over assigned layers.
- Scheme 7 supports explicit YAML `partition` and `partitions`.
- Scheme 1 is disabled for rank > 8.
- Scheme 2 remains enabled for rank > 8 but uses Scheme 4 partition.
- Scheme 4 odd-rank split is changed to `[floor(rank/2), ceil(rank/2)]`.
- CDL/SVD layer order is explicitly descending by post-SINR / singular value.
- Adaptive MCS filters out code rates below `adaptive_mcs.min_code_rate` to avoid Sionna LDPC `r<1/5` failures.
- Added `V2_7_SCHEME_MAPPING_README.md` with detailed scheme definitions.

Recommended first test:

```bash
python run.py --config configs/sionna_ldpc_rank2_all_schemes_cdl_adaptive.yaml
```

Scheme7 fixed partition example:

```bash
python run.py --config configs/sionna_ldpc_rank5_scheme7_partition_2_2_1_cdl_adaptive.yaml
```
