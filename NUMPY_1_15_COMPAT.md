# NumPy 1.15.1 / PyYAML 3.13 兼容补丁说明

你的环境检查结果是：

- Python 3.7.0：可用
- NumPy 1.15.1：不支持 `np.random.default_rng`
- PyYAML 3.13：可用，但不支持新版 `yaml.safe_dump(..., sort_keys=False)` 参数
- matplotlib 2.2.3：可用
- pytest 3.8.0：可用

本补丁修改了以下文件：

1. `lls_platform/phy/channel.py`
   - 新增 `make_rng(seed=None)`。
   - 新版 NumPy 使用 `np.random.default_rng`，旧版 NumPy 自动退回 `np.random.RandomState`。

2. `lls_platform/sim/orchestrator.py`
   - 将 `np.random.default_rng(config.simulation.seed)` 改成 `make_rng(config.simulation.seed)`。

3. `lls_platform/core/config.py`
   - 保存 `config_resolved.yaml` 时，先尝试新版 `sort_keys=False` 写法。
   - 如果 PyYAML 3.13 报 `TypeError`，自动退回不带 `sort_keys` 的兼容写法。

4. `lls_platform/utils/plotting.py`
   - 增加 `matplotlib.use("Agg")`，支持无图形界面的服务器环境直接保存 png。

## 使用方法

将补丁包中的这 4 个文件覆盖到你的项目对应位置，然后运行：

```bash
python -m pytest -q
python run.py --config configs/quick_test.yaml
```

如果运行成功，会生成：

```text
results_quick/
└── sim_时间戳/
    ├── config_resolved.yaml
    ├── results.csv
    ├── results.json
    ├── bler_vs_snr.png
    └── throughput_vs_snr.png
```
