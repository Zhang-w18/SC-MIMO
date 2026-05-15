# lls_platform_v3.1_bit_level_latest

This package is based on v3.1 and includes the latest hotfix/update:

- Per-CW OLLA support from v3.1.
- Scheme-level OLLA step override via `olla.scheme_step_db`.
  - Match priority: full scheme name, e.g. `scheme7_partition_3+1` > base scheme name, e.g. `scheme7` / `scheme2` > global `olla.step_db`.
- CW trial records include `olla_step_db` and `olla_step_source` when available.
- The rank4 ideal OLLA test YAML includes an example:

```yaml
olla:
  step_db: 0.05
  scheme_step_db:
    scheme2: 0.10
```

The postprocess script also includes a `matplotlib.pyplot as plt` import fix for OLLA trace plotting.
