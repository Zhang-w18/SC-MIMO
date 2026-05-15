# LLS Platform V1 代码总览

下面按文件列出第一版代码。你也可以直接使用压缩包中的实际项目文件。


## `.pytest_cache/README.md`

```markdown
# pytest cache directory #

This directory contains data from the pytest's cache plugin,
which provides the `--lf` and `--ff` options, as well as the `cache` fixture.

**Do not** commit this to version control.

See [the docs](https://docs.pytest.org/en/stable/how-to/cache.html) for more information.

```


## `IMPLEMENTATION_NOTES.md`

```markdown
# 第一版实现说明

## 1. 重要实现选择

本版本严格采用“补充文档2覆盖文档1”的约束：

- 主流程实现 per-CW adaptive MCS；
- Scheme 2 实现 per-layer Qm adaptive selection；
- fixed MCS 仅作为 debug override；
- 第一版不实现 SIC/HARQ/OLLA/CRC/DMRS/LS/RMMSE。

## 2. 为什么默认是 link_abstraction backend

为了先把 scheme、MCS、TBS、CB、统计和绘图闭环跑通，本版本默认使用抽象链路模型。

这意味着：

- BLER 曲线用于开发验证和方案逻辑对比；
- 不应直接作为论文级 bit-level BLER 结果；
- 后续接入 Sionna 后可复用当前核心模块。

## 3. 后续接入 Sionna 的建议

保持以下模块不变：

- `lls_platform/schemes/flexible_cw.py`
- `lls_platform/utils/mcs_selection.py`
- `lls_platform/tx/tb_manager.py`
- `lls_platform/tx/symbol_mapper.py`
- `lls_platform/sim/stats.py`

新增 Sionna backend：

- `lls_platform/phy/sionna_channel.py`
- `lls_platform/phy/sionna_ldpc.py`
- `lls_platform/phy/sionna_detector.py`
- `lls_platform/sim/sionna_orchestrator.py`

```


## `README.md`

```markdown
# 灵活码字映射链路级仿真平台 V1

## 1. 这个版本实现了什么

本交付包是第一版代码，目标是先跑通“灵活码字映射 + adaptive MCS + 统计对比”的完整软件闭环。

已经实现：

- Scheme 1–7 的统一实现；
- Scheme 7 的连续层整数分拆枚举；
- per-CW adaptive MCS；
- Scheme 2 的 per-layer Qm adaptive selection；
- 基于补充文档2的 SNR / post-SINR 计算；
- TBS 计算与 CB 分段闭环；
- 多 scheme、多 SNR 对比；
- BLER vs SNR 图；
- Throughput vs SNR 图；
- CSV/JSON 结果保存；
- pytest 单元测试。

注意：

当前默认 backend 是 `link_abstraction`，用于快速验证第一版架构和算法逻辑。它不是最终的 Sionna bit-level LDPC/CDL 仿真结果。后续接入 Sionna 时，可以复用本版本的：

- 配置系统；
- Scheme 生成；
- MCS/Qm 选择；
- TBS/CB 分段；
- SymbolMapping；
- 仿真编排与统计模块。

## 2. 目录结构

```text
lls_platform_v1/
├── run.py
├── configs/
│   ├── base.yaml
│   ├── quick_test.yaml
│   └── fixed_mcs_debug.yaml
├── lls_platform/
│   ├── core/
│   ├── schemes/
│   ├── tx/
│   ├── phy/
│   ├── sim/
│   └── utils/
├── tests/
│   └── test_core.py
├── README.md
└── TESTING.md
```

## 3. 安装依赖

建议使用 Python 3.10 或更新版本。

```bash
pip install numpy pyyaml matplotlib pytest
```

如果你后续要接入 Sionna，需要额外安装 TensorFlow/Sionna。当前版本默认不强制依赖 Sionna。

## 4. 快速运行

进入项目目录：

```bash
cd lls_platform_v1
```

运行快速测试配置：

```bash
python run.py --config configs/quick_test.yaml
```

运行完整一点的默认配置：

```bash
python run.py --config configs/base.yaml
```

运行 fixed MCS debug 模式：

```bash
python run.py --config configs/fixed_mcs_debug.yaml
```

## 5. 输出结果

运行后会生成类似目录：

```text
results_quick/
└── sim_20260427_153000/
    ├── config_resolved.yaml
    ├── results.csv
    ├── results.json
    ├── bler_vs_snr.png
    └── throughput_vs_snr.png
```

其中：

- `config_resolved.yaml`：合并默认值后的完整配置；
- `results.csv`：方便 Excel / pandas 分析；
- `results.json`：保留更完整的 per-CW 和 metadata 信息；
- `bler_vs_snr.png`：BLER 曲线；
- `throughput_vs_snr.png`：吞吐曲线。

## 6. Scheme 说明

| Scheme | 含义 |
|---|---|
| 1 | NR baseline |
| 2 | NR baseline + 每层 Qm 自适应 |
| 3 | 任意 rank 都只用 1 个 CW |
| 4 | rank>1 时用 2 个 CW，连续层均分 |
| 5 | 每个 CW 最多 2 层 |
| 6 | 每层一个独立 CW |
| 7 | 穷举给定 rank 下所有连续层整数分拆 |

例如 rank=4 时，Scheme 7 会展开为：

```text
[4]
[3,1]
[2,2]
[2,1,1]
[1,1,1,1]
```

## 7. adaptive MCS 的实现逻辑

每个 CW 独立选择 MCS：

```text
1. 收集该 CW 占用层的 post-SINR
2. 在线性域做调和平均
3. 使用 Shannon gap 计算可达 SE
4. 选择 Qm × R 不超过可达 SE 的最高 MCS
```

Scheme 2 额外执行：

```text
1. 每层根据 SINR 门限选择 Qm
2. 每层计算可达 SE
3. R = min_i(SE_i / Qm_i)
4. 将 R 量化到 MCS 表中的合法码率
```

## 8. 配置文件示例

```yaml
mapping:
  rank: 4

simulation:
  snr_range_db: [0, 30, 5]
  n_trials_per_snr: 1000
  batch_size: 100
  fixed_mcs: null
  shannon_gap_db: 2.5

comparison:
  enabled: true
  schemes:
    - {scheme_id: 1}
    - {scheme_id: 2}
    - {scheme_id: 4}
    - {scheme_id: 6}
    - {scheme_id: 7}
```

## 9. 后续接入 Sionna 的位置

建议后续新增：

```text
lls_platform/phy/sionna_channel.py
lls_platform/phy/sionna_ldpc.py
lls_platform/phy/sionna_detector.py
lls_platform/sim/sionna_orchestrator.py
```

并保持以下模块不变：

- `FlexibleCWScheme`
- `TBManager`
- `SymbolMapper`
- `mcs_selection`
- `stats`
- `plotting`

这样可以把当前抽象 backend 替换为真正 bit-level backend，而不重写 scheme 逻辑。

```


## `TESTING.md`

```markdown
# 测试说明：如何验证第一版代码正确性

## 1. 安装测试依赖

```bash
pip install numpy pyyaml matplotlib pytest
```

## 2. 运行全部单元测试

在项目根目录执行：

```bash
pytest -q
```

预期输出类似：

```text
6 passed in 0.xx s
```

## 3. 单元测试覆盖哪些内容

### 3.1 Scheme 7 整数分拆

测试文件：

```text
tests/test_core.py::test_integer_partitions_rank4
```

验证 rank=4 时 Scheme 7 是否生成：

```text
[4], [3,1], [2,2], [2,1,1], [1,1,1,1]
```

这对应补充文档中的连续层分组要求。

### 3.2 Scheme 1/4/6 的层分配

测试文件：

```text
tests/test_core.py::test_scheme_generation_rank4
```

验证：

```text
Scheme 1 -> [[0,1,2,3]]
Scheme 4 -> [[0,1], [2,3]]
Scheme 6 -> [[0], [1], [2], [3]]
```

### 3.3 Scheme 2 per-layer Qm

测试文件：

```text
tests/test_core.py::test_scheme2_per_layer_qm
```

验证 Scheme 2 会根据层 SINR 自动选择不同 Qm，而不是所有层使用同一调制阶数。

### 3.4 MCS 选择单调性

测试文件：

```text
tests/test_core.py::test_mcs_selection_monotonic
```

验证高 SINR 选择的 MCS 不应低于低 SINR 选择的 MCS。

### 3.5 TBS/CB 闭环

测试文件：

```text
tests/test_core.py::test_tb_manager_closure
```

验证每个 CW 满足：

```text
sum(E_cb) == n_bits_total
```

这是 rate matching 闭环最关键的检查。

### 3.6 SymbolMapping 数量检查

测试文件：

```text
tests/test_core.py::test_symbol_mapping_count
```

验证生成的调制符号数量与资源容量一致。

## 4. 运行冒烟仿真

```bash
python run.py --config configs/quick_test.yaml
```

检查：

1. 程序能正常结束；
2. 输出目录下有 `results.csv`；
3. 输出目录下有 `bler_vs_snr.png`；
4. 输出目录下有 `throughput_vs_snr.png`；
5. `results.csv` 中不同 scheme 有不同 throughput/BLER。

## 5. 检查 Scheme 2 是否使用了 per-layer Qm

运行：

```bash
python run.py --config configs/quick_test.yaml
```

打开输出目录中的 `results.csv`，查看列：

```text
meta_cw0_qms
```

对于 Scheme 2，应该能看到类似：

```text
{0: 8, 1: 6, 2: 4, 3: 2}
```

或者其他随 SNR 和信道变化的不同层 Qm 组合。

## 6. 检查 per-CW adaptive MCS 是否生效

打开 `results.csv`，查看：

```text
meta_cw0_mcs
meta_cw1_mcs
meta_cw0_rate
meta_cw1_rate
```

对于 Scheme 4/5/6/7，强层 CW 和弱层 CW 往往会选择不同 MCS 或不同码率。

例如 rank=4、Scheme 4 中：

```text
CW0 layers [0,1] 通常 MCS 更高
CW1 layers [2,3] 通常 MCS 更低
```

## 7. fixed MCS debug 模式

运行：

```bash
python run.py --config configs/fixed_mcs_debug.yaml
```

此时所有普通 scheme 的 CW 都强制使用 MCS 10。这个模式用于 debug，不是第一版主流程。

## 8. 当前版本的验证边界

当前版本验证的是：

- scheme 配置逻辑；
- MCS/Qm 自适应逻辑；
- TBS/CB 分段逻辑；
- 多方案统计流程；
- 曲线输出流程。

当前版本尚未验证：

- Sionna LDPC 编解码；
- Sionna CDL 信道；
- MMSE detector 输出真实 LLR；
- bit-level BLER 曲线。

这些应作为下一阶段 Sionna backend 的测试内容。

```


## `configs/base.yaml`

```yaml
# 灵活码字映射链路级仿真平台 V1 基础配置

antenna:
  bs_n_antennas: 32
  ue_n_antennas: 4

resource:
  n_prbs: 106
  pdsch_n_symbols: 13
  reserve_dmrs_re: false   # 第一版理想CE，不发DMRS，不扣除DMRS RE

channel:
  model: "Rayleigh"
  normalize: true
  n_re_samples: 24

mapping:
  scheme: "flexible_cw"
  scheme_id: 1
  rank: 4

simulation:
  backend: "link_abstraction"
  snr_range_db: [0, 30, 5]
  n_trials_per_snr: 1000
  batch_size: 100
  seed: 42
  output_dir: "./results"

  # 补充文档2覆盖后：默认使用 adaptive MCS
  fixed_mcs: null
  shannon_gap_db: 2.5

  # Scheme 2 per-layer Qm 门限
  qpsk_max_db: 5.0
  qam16_max_db: 12.0
  qam64_max_db: 18.0

comparison:
  enabled: true
  schemes:
    - {scheme_id: 1}
    - {scheme_id: 2}
    - {scheme_id: 3}
    - {scheme_id: 4}
    - {scheme_id: 5}
    - {scheme_id: 6}
    - {scheme_id: 7}

debug:
  verbose: true

```


## `configs/fixed_mcs_debug.yaml`

```yaml
# fixed MCS 仅用于 debug/ablation，不是第一版主流程
mapping:
  rank: 4

simulation:
  snr_range_db: [0, 20, 10]
  n_trials_per_snr: 200
  batch_size: 50
  fixed_mcs: 10
  output_dir: "./results_fixed_mcs_debug"

comparison:
  enabled: true
  schemes:
    - {scheme_id: 1}
    - {scheme_id: 3}
    - {scheme_id: 4}
    - {scheme_id: 5}
    - {scheme_id: 6}

```


## `configs/quick_test.yaml`

```yaml
# 快速冒烟测试配置：几十秒内验证流程
antenna:
  bs_n_antennas: 16
  ue_n_antennas: 4

resource:
  n_prbs: 24
  pdsch_n_symbols: 12
  reserve_dmrs_re: false

channel:
  n_re_samples: 4
  normalize: true

mapping:
  rank: 4

simulation:
  backend: "link_abstraction"
  snr_range_db: [0, 20, 20]
  n_trials_per_snr: 40
  batch_size: 20
  seed: 1
  output_dir: "./results_quick"
  fixed_mcs: null
  shannon_gap_db: 2.5

comparison:
  enabled: true
  schemes:
    - {scheme_id: 1}
    - {scheme_id: 2}
    - {scheme_id: 4}
    - {scheme_id: 6}
    - {scheme_id: 7}

debug:
  verbose: true

```


## `lls_platform/__init__.py`

```python
"""灵活码字映射链路级仿真平台 V1。

说明：
- 本版本优先实现 Scheme 1-7 的映射配置生成、per-CW adaptive MCS、
  Scheme 2 的 per-layer Qm 选择、TBS/CB 分段和仿真编排。
- 默认 backend 是 link_abstraction，用于快速验证第一版算法闭环。
- 后续可以在 phy/ 下替换为 Sionna bit-level backend。
"""
__version__ = "0.1.0"

```


## `lls_platform/core/__init__.py`

```python

```


## `lls_platform/core/config.py`

```python
from __future__ import annotations

from dataclasses import dataclass, field, asdict, is_dataclass
from typing import Any, Dict, List, Optional
import copy
import yaml


@dataclass
class AntennaConfig:
    """天线配置。

    第一版 link_abstraction backend 只用 bs_n_antennas / ue_n_antennas
    生成 MIMO 信道矩阵；Sionna backend 后续可继续使用阵列参数。
    """
    bs_n_antennas: int = 32
    ue_n_antennas: int = 4
    bs_antenna_array: List[int] = field(default_factory=lambda: [4, 4, 2])
    ue_antenna_array: List[int] = field(default_factory=lambda: [1, 2, 2])
    antenna_element_pattern: str = "38.901"
    polarization: str = "cross"


@dataclass
class ResourceConfig:
    """时频资源配置。"""
    carrier_frequency_ghz: float = 3.5
    bandwidth_mhz: float = 20.0
    scs_khz: int = 15
    n_prbs: int = 106
    pdsch_start_symbol: int = 1
    pdsch_n_symbols: int = 13

    # 第一版理想信道估计不发 DMRS。为了便于后续扩展，保留字段。
    dmrs_type: int = 1
    dmrs_symbol_indices: List[int] = field(default_factory=list)
    reserve_dmrs_re: bool = False   # False 表示 PDSCH RE 全部用于数据
    prb_bundling_size: int = 4


@dataclass
class ChannelConfig:
    """信道配置。

    第一版默认使用抽象 Rayleigh MIMO 信道生成奇异值。
    字段名保持与未来 Sionna CDL backend 兼容。
    """
    model: str = "Rayleigh"
    delay_spread_ns: float = 30.0
    ue_speed_kmh: float = 3.0
    normalize: bool = True
    n_re_samples: int = 24  # link_abstraction 中每个 trial 采样多少个频域 RE 的奇异值


@dataclass
class CSIConfig:
    """发射端 CSI/预编码配置。"""
    mode: str = "ideal"
    precoding_method: str = "svd"


@dataclass
class ChannelEstimationConfig:
    """接收端信道估计配置。第一版只实现 ideal。"""
    mode: str = "ideal"
    prg_bundling_size: int = 4
    interpolation: str = "linear"


@dataclass
class ReceiverConfig:
    """接收机配置。第一版只使用抽象 MMSE/link abstraction。"""
    detector: str = "mmse"
    sic_enabled: bool = False
    max_ldpc_iterations: int = 20


@dataclass
class MappingConfig:
    """映射方案配置。

    scheme_id:
        1 NR baseline
        2 NR baseline + per-layer Qm
        3 single CW for all ranks
        4 rank>1 dual-CW split
        5 max 2 layers per CW
        6 one CW per layer
        7 enumerate all integer partitions
    """
    scheme: str = "flexible_cw"
    scheme_id: int = 1
    rank: int = 4
    params: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SimulationConfig:
    """仿真配置。"""
    snr_range_db: List[float] = field(default_factory=lambda: [0, 30, 2])
    n_trials_per_snr: int = 1000
    batch_size: int = 100
    min_block_errors: int = 100
    max_trials: int = 100000
    seed: int = 42
    output_dir: str = "./results"

    # 补充文档2覆盖后：第一版主流程必须是 adaptive MCS。
    # fixed_mcs 只作为 debug override。
    fixed_mcs: Optional[int] = None
    mcs_table: str = "approx_256qam"
    shannon_gap_db: float = 2.5

    # Scheme 2 的 per-layer Qm 门限。
    qpsk_max_db: float = 5.0
    qam16_max_db: float = 12.0
    qam64_max_db: float = 18.0

    # link_abstraction BLER 曲线平滑参数，仅用于抽象 backend。
    bler_slope_db: float = 1.5

    backend: str = "link_abstraction"  # 后续可扩展为 "sionna"


@dataclass
class ComparisonConfig:
    """多方案对比配置。"""
    enabled: bool = True
    schemes: List[Dict[str, Any]] = field(default_factory=lambda: [
        {"scheme_id": 1},
        {"scheme_id": 2},
        {"scheme_id": 3},
        {"scheme_id": 4},
        {"scheme_id": 5},
        {"scheme_id": 6},
        {"scheme_id": 7},
    ])


@dataclass
class DebugConfig:
    """调试与日志配置。"""
    verbose: bool = True
    save_per_snr_json: bool = True
    log_per_cw: bool = True


@dataclass
class PlatformConfig:
    """平台总配置。"""
    antenna: AntennaConfig = field(default_factory=AntennaConfig)
    resource: ResourceConfig = field(default_factory=ResourceConfig)
    channel: ChannelConfig = field(default_factory=ChannelConfig)
    csi: CSIConfig = field(default_factory=CSIConfig)
    channel_estimation: ChannelEstimationConfig = field(default_factory=ChannelEstimationConfig)
    receiver: ReceiverConfig = field(default_factory=ReceiverConfig)
    mapping: MappingConfig = field(default_factory=MappingConfig)
    simulation: SimulationConfig = field(default_factory=SimulationConfig)
    comparison: ComparisonConfig = field(default_factory=ComparisonConfig)
    debug: DebugConfig = field(default_factory=DebugConfig)


def _deep_update(base: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
    """递归合并 dict。YAML 里只写关心的字段即可。"""
    out = copy.deepcopy(base)
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_update(out[k], v)
        else:
            out[k] = v
    return out


def dataclass_to_dict(obj: Any) -> Any:
    """把 dataclass 递归转为普通 dict，便于保存 resolved config。"""
    if is_dataclass(obj):
        return {k: dataclass_to_dict(v) for k, v in asdict(obj).items()}
    if isinstance(obj, list):
        return [dataclass_to_dict(v) for v in obj]
    if isinstance(obj, dict):
        return {k: dataclass_to_dict(v) for k, v in obj.items()}
    return obj


def _construct_dataclass(cls, data: Dict[str, Any]):
    """轻量级 dict -> dataclass 转换，避免强依赖 dacite。"""
    kwargs = {}
    for field_name, field_def in cls.__dataclass_fields__.items():
        if field_name not in data:
            continue
        value = data[field_name]
        field_type = field_def.type

        # 手动处理已知子 dataclass，避免 Python 版本下 typing 解析复杂。
        child_map = {
            "antenna": AntennaConfig,
            "resource": ResourceConfig,
            "channel": ChannelConfig,
            "csi": CSIConfig,
            "channel_estimation": ChannelEstimationConfig,
            "receiver": ReceiverConfig,
            "mapping": MappingConfig,
            "simulation": SimulationConfig,
            "comparison": ComparisonConfig,
            "debug": DebugConfig,
        }
        if field_name in child_map and isinstance(value, dict):
            kwargs[field_name] = _construct_dataclass(child_map[field_name], value)
        else:
            kwargs[field_name] = value
    return cls(**kwargs)


def load_config(path: str) -> PlatformConfig:
    """读取 YAML 并合并默认配置。"""
    default_dict = dataclass_to_dict(PlatformConfig())
    with open(path, "r", encoding="utf-8") as f:
        user_dict = yaml.safe_load(f) or {}
    merged = _deep_update(default_dict, user_dict)
    return _construct_dataclass(PlatformConfig, merged)


def save_resolved_config(config: PlatformConfig, path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(dataclass_to_dict(config), f, allow_unicode=True, sort_keys=False)

```


## `lls_platform/core/data_structures.py`

```python
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any


@dataclass
class MCSEntry:
    """MCS 表项。

    required_sinr_db 是抽象链路模型使用的 10% BLER 近似门限；
    后续可用平台 AWGN 仿真重新标定。
    """
    index: int
    modulation: str
    Qm: int
    code_rate: float
    required_sinr_db: float

    @property
    def spectral_efficiency(self) -> float:
        return self.Qm * self.code_rate


@dataclass
class CWConfig:
    """单个码字配置。"""
    cw_index: int
    layer_indices: List[int]
    layer_modulations: Dict[int, int]  # layer_idx -> Qm
    mcs_index: Optional[int]
    code_rate: float

    @property
    def n_layers(self) -> int:
        return len(self.layer_indices)

    @property
    def same_qm(self) -> bool:
        qms = [self.layer_modulations[i] for i in self.layer_indices]
        return len(set(qms)) == 1

    @property
    def representative_qm(self) -> int:
        """普通方案 CW 内统一 Qm；Scheme 2 仅作为显示用。"""
        return self.layer_modulations[self.layer_indices[0]]


@dataclass
class TransmissionConfig:
    """一次传输的完整配置。"""
    scheme_name: str
    scheme_id: int
    rank: int
    cw_configs: List[CWConfig]
    mapping_rule: str = "round_robin"
    description: str = ""
    partition: Optional[List[int]] = None


@dataclass
class CBInfo:
    """单个 CB 的尺寸信息。"""
    cb_index: int
    K: int
    K_ldpc: int
    N_null: int
    E: int
    Zc: int


@dataclass
class TBInfo:
    """单个 CW 的 TB/CB 信息。"""
    cw_index: int
    tb_size: int
    n_cbs: int
    base_graph: int
    cb_infos: List[CBInfo]
    n_re_per_layer: int
    n_bits_total: int
    actual_se: float

    @property
    def total_E(self) -> int:
        return sum(cb.E for cb in self.cb_infos)


@dataclass
class SymbolMappingEntry:
    """一个调制符号对应的 layer/RE/CB 位置。"""
    cw_index: int
    symbol_index: int
    layer_index: int
    re_index: int
    cb_index: int


@dataclass
class SymbolMapping:
    """调制符号到 layer/RE 的映射表。

    第一版用 Python list 保存，便于调试和单元测试。
    大规模 Sionna/TensorFlow backend 可以把它转换成 gather/scatter index tensor。
    """
    entries: List[SymbolMappingEntry] = field(default_factory=list)

    def add(self, entry: SymbolMappingEntry) -> None:
        self.entries.append(entry)


@dataclass
class CWSimulationStats:
    """某个 SNR 点下某个 CW 的统计结果。"""
    cw_index: int
    trials: int = 0
    errors: int = 0
    tb_size: int = 0

    @property
    def bler(self) -> float:
        return self.errors / self.trials if self.trials else 0.0

    @property
    def throughput_bits_per_slot(self) -> float:
        return self.tb_size * (1.0 - self.bler)


@dataclass
class SNRSummary:
    """一个 scheme 在一个 SNR 点的汇总结果。"""
    scheme_label: str
    snr_db: float
    trials: int
    scheme_errors: int
    cw_stats: List[CWSimulationStats]
    total_throughput_bits_per_slot: float
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def scheme_bler(self) -> float:
        return self.scheme_errors / self.trials if self.trials else 0.0

```


## `lls_platform/core/mapping_scheme.py`

```python
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List
import numpy as np

from lls_platform.core.config import MappingConfig, SimulationConfig
from lls_platform.core.data_structures import MCSEntry, TransmissionConfig


class MappingScheme(ABC):
    """映射方案抽象基类。"""

    @abstractmethod
    def expand_candidates(self, rank: int) -> List["MappingScheme"]:
        """Scheme 7 会展开成多个候选；其他 scheme 返回 [self]。"""
        raise NotImplementedError

    @abstractmethod
    def configure_transmission(
        self,
        layer_sinrs_db: np.ndarray,
        mcs_table: List[MCSEntry],
        sim_cfg: SimulationConfig,
    ) -> TransmissionConfig:
        """根据 post-SINR 和配置生成 TransmissionConfig。"""
        raise NotImplementedError

```


## `lls_platform/core/registry.py`

```python
from __future__ import annotations

from typing import Dict, Type
from lls_platform.core.mapping_scheme import MappingScheme

_SCHEME_REGISTRY: Dict[str, Type[MappingScheme]] = {}


def register_scheme(name: str):
    """方案注册装饰器。"""
    def deco(cls):
        _SCHEME_REGISTRY[name] = cls
        return cls
    return deco


def create_scheme(name: str, **kwargs) -> MappingScheme:
    if name not in _SCHEME_REGISTRY:
        raise KeyError(f"未知 mapping scheme: {name}. 已注册: {list(_SCHEME_REGISTRY)}")
    return _SCHEME_REGISTRY[name](**kwargs)


def list_schemes():
    return list(_SCHEME_REGISTRY.keys())

```


## `lls_platform/phy/__init__.py`

```python

```


## `lls_platform/phy/channel.py`

```python
from __future__ import annotations

import numpy as np


class RayleighSVDChannel:
    """第一版 link_abstraction backend 的 Rayleigh MIMO 信道。

    作用：
    - 生成 batch 个 trial、每个 trial 多个 RE 样本的 MIMO 信道矩阵；
    - 计算每个 RE 的奇异值；
    - 后续用奇异值计算 post-SINR。

    注意：
    这个 backend 用于验证 Mapping/MCS/TBS/统计闭环。
    论文级链路曲线应替换为 Sionna CDL + LDPC bit-level backend。
    """

    def __init__(
        self,
        n_rx: int,
        n_tx: int,
        n_re_samples: int = 24,
        normalize: bool = True,
        rng: np.random.Generator | None = None,
    ):
        self.n_rx = n_rx
        self.n_tx = n_tx
        self.n_re_samples = n_re_samples
        self.normalize = normalize
        self.rng = rng or np.random.default_rng()

    def sample_singular_values(self, batch_size: int, rank: int) -> np.ndarray:
        """返回 shape [batch, n_re_samples, rank] 的前 rank 个奇异值。"""
        h = (
            self.rng.normal(size=(batch_size, self.n_re_samples, self.n_rx, self.n_tx))
            + 1j * self.rng.normal(size=(batch_size, self.n_re_samples, self.n_rx, self.n_tx))
        ) / np.sqrt(2.0)

        # 常见归一化：每个发射天线到每个接收天线平均功率约为 1。
        # 如果希望去掉天线阵列增益，可进一步按 sqrt(n_tx) 归一化。
        if self.normalize:
            h = h / np.sqrt(self.n_tx)

        s = np.linalg.svd(h, compute_uv=False)
        return s[..., :rank]

```


## `lls_platform/schemes/__init__.py`

```python

```


## `lls_platform/schemes/flexible_cw.py`

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional
import numpy as np

from lls_platform.core.config import SimulationConfig
from lls_platform.core.data_structures import CWConfig, MCSEntry, TransmissionConfig
from lls_platform.core.mapping_scheme import MappingScheme
from lls_platform.core.registry import register_scheme
from lls_platform.utils.integer_partition import (
    integer_partitions_nonincreasing,
    partition_to_layer_groups,
)
from lls_platform.utils.mcs_selection import (
    select_mcs_for_cw,
    select_per_layer_qm,
    select_code_rate_for_per_layer_qm,
)


def nr_baseline_partition(rank: int) -> List[int]:
    """NR baseline 的 CW 数与层分配简化规则。

    - rank 1~4: 1 CW
    - rank 5~8: 2 CW，按 NR 常见规则近似为
      rank5 [2,3], rank6 [3,3], rank7 [3,4], rank8 [4,4]
    """
    if rank <= 4:
        return [rank]
    table = {
        5: [2, 3],
        6: [3, 3],
        7: [3, 4],
        8: [4, 4],
    }
    if rank in table:
        return table[rank]
    # 超出 8 层时，作为研究扩展，近似均分为两个 CW。
    first = rank // 2
    return [first, rank - first]


def dual_cw_balanced_partition(rank: int) -> List[int]:
    """Scheme 4：rank>1 时使用 2 个 CW，连续层尽量均分。"""
    if rank <= 1:
        return [1]
    first = int(np.ceil(rank / 2))
    return [first, rank - first]


def max_two_layers_partition(rank: int) -> List[int]:
    """Scheme 5：每个 CW 最多 2 层。"""
    out = []
    remain = rank
    while remain > 0:
        size = min(2, remain)
        out.append(size)
        remain -= size
    return out


def one_cw_per_layer_partition(rank: int) -> List[int]:
    """Scheme 6：每层一个独立 CW。"""
    return [1 for _ in range(rank)]


@register_scheme("flexible_cw")
@dataclass
class FlexibleCWScheme(MappingScheme):
    """Scheme 1-7 的统一实现。

    重点：
    - 补充文档2要求第一版实现 per-CW adaptive MCS。
    - Scheme 2 额外实现 per-layer Qm adaptive selection。
    """
    scheme_id: int = 1
    rank: int = 4
    partition: Optional[List[int]] = None

    def label(self) -> str:
        if self.scheme_id == 7 and self.partition is not None:
            return f"scheme7_partition_{'+'.join(map(str, self.partition))}"
        return f"scheme{self.scheme_id}"

    def expand_candidates(self, rank: int | None = None) -> List["FlexibleCWScheme"]:
        rank = self.rank if rank is None else rank
        if self.scheme_id != 7:
            return [self]
        return [
            FlexibleCWScheme(scheme_id=7, rank=rank, partition=p)
            for p in integer_partitions_nonincreasing(rank)
        ]

    def _get_partition(self) -> List[int]:
        if self.partition is not None:
            return list(self.partition)

        if self.scheme_id == 1:
            return nr_baseline_partition(self.rank)
        if self.scheme_id == 2:
            return nr_baseline_partition(self.rank)
        if self.scheme_id == 3:
            return [self.rank]
        if self.scheme_id == 4:
            return dual_cw_balanced_partition(self.rank)
        if self.scheme_id == 5:
            return max_two_layers_partition(self.rank)
        if self.scheme_id == 6:
            return one_cw_per_layer_partition(self.rank)
        if self.scheme_id == 7:
            # 默认用 [rank]，真正多候选由 expand_candidates() 生成。
            return [self.rank]
        raise ValueError(f"未知 scheme_id: {self.scheme_id}")

    def configure_transmission(
        self,
        layer_sinrs_db: np.ndarray,
        mcs_table: List[MCSEntry],
        sim_cfg: SimulationConfig,
    ) -> TransmissionConfig:
        """根据层 SINR 生成传输配置。

        参数：
            layer_sinrs_db: shape [rank]，每层 post-SINR(dB)，通常为 batch 平均值。
        """
        layer_sinrs_db = np.asarray(layer_sinrs_db, dtype=float)
        if len(layer_sinrs_db) < self.rank:
            raise ValueError(f"layer_sinrs_db 长度 {len(layer_sinrs_db)} 小于 rank {self.rank}")

        partition = self._get_partition()
        groups = partition_to_layer_groups(partition)

        cw_configs: List[CWConfig] = []

        # Scheme 2：先给所有层选 Qm，再每个 CW 选统一码率。
        if self.scheme_id == 2:
            all_layer_qm = select_per_layer_qm(
                layer_sinrs_db[:self.rank],
                qpsk_max_db=sim_cfg.qpsk_max_db,
                qam16_max_db=sim_cfg.qam16_max_db,
                qam64_max_db=sim_cfg.qam64_max_db,
            )

            for cw_idx, layers in enumerate(groups):
                sub_sinrs = layer_sinrs_db[layers]
                # 注意：select_code_rate_for_per_layer_qm 的 layer_qm 用局部下标，
                # 因此这里重新构造 local_layer_qm。
                local_qm = {i: all_layer_qm[layer] for i, layer in enumerate(layers)}
                code_rate = select_code_rate_for_per_layer_qm(
                    sub_sinrs,
                    local_qm,
                    mcs_table,
                    gap_db=sim_cfg.shannon_gap_db,
                )
                layer_modulations = {layer: all_layer_qm[layer] for layer in layers}
                cw_configs.append(CWConfig(
                    cw_index=cw_idx,
                    layer_indices=layers,
                    layer_modulations=layer_modulations,
                    mcs_index=None,  # Scheme 2 的 Qm/R 不一定对应单个标准 MCS
                    code_rate=code_rate,
                ))

        else:
            for cw_idx, layers in enumerate(groups):
                sub_sinrs = layer_sinrs_db[layers]
                mcs = select_mcs_for_cw(
                    sub_sinrs,
                    mcs_table,
                    gap_db=sim_cfg.shannon_gap_db,
                    fixed_mcs=sim_cfg.fixed_mcs,
                )
                layer_modulations = {layer: mcs.Qm for layer in layers}
                cw_configs.append(CWConfig(
                    cw_index=cw_idx,
                    layer_indices=layers,
                    layer_modulations=layer_modulations,
                    mcs_index=mcs.index,
                    code_rate=mcs.code_rate,
                ))

        return TransmissionConfig(
            scheme_name=self.label(),
            scheme_id=self.scheme_id,
            rank=self.rank,
            cw_configs=cw_configs,
            mapping_rule="round_robin" if self.scheme_id != 2 else "per_layer_qm",
            description=self._description(),
            partition=partition,
        )

    def _description(self) -> str:
        names = {
            1: "NR baseline",
            2: "NR baseline + per-layer Qm adaptive",
            3: "single CW for all ranks",
            4: "dual CW for rank>1",
            5: "max 2 layers per CW",
            6: "one CW per layer",
            7: "enumerated contiguous CW partition",
        }
        return names.get(self.scheme_id, f"scheme {self.scheme_id}")

```


## `lls_platform/sim/__init__.py`

```python

```


## `lls_platform/sim/orchestrator.py`

```python
from __future__ import annotations

from pathlib import Path
from typing import Dict, List
import datetime as _dt
import numpy as np

# 注册 flexible_cw
import lls_platform.schemes.flexible_cw  # noqa: F401

from lls_platform.core.config import PlatformConfig, save_resolved_config
from lls_platform.core.registry import create_scheme
from lls_platform.core.data_structures import CWSimulationStats, SNRSummary
from lls_platform.phy.channel import RayleighSVDChannel
from lls_platform.tx.tb_manager import TBManager
from lls_platform.utils.mcs_tables import get_mcs_table
from lls_platform.utils.mcs_selection import (
    estimate_post_sinr_db_from_singular_values,
    harmonic_mean_sinr_db,
)
from lls_platform.sim.stats import save_csv, save_json
from lls_platform.utils.plotting import plot_bler, plot_throughput


class LinkAbstractionOrchestrator:
    """第一版仿真编排器。

    这是一个抽象链路 backend，不直接做 LDPC bit-level 仿真。
    它用于快速验证：
    - 7 种 scheme 的 CW/layer 分配；
    - per-CW adaptive MCS；
    - Scheme 2 per-layer Qm；
    - TBS/CB 闭环；
    - BLER/Throughput 统计和画图流程。

    后续可在保持 MappingScheme/TBManager/Stats 接口不变的情况下，
    替换为 Sionna CDL + LDPC + MMSE detector 的 bit-level backend。
    """

    def __init__(self, config: PlatformConfig):
        self.cfg = config
        self.rng = np.random.default_rng(config.simulation.seed)
        self.mcs_table = get_mcs_table(config.simulation.mcs_table)
        self.tb_manager = TBManager(config.resource)

        self.channel = RayleighSVDChannel(
            n_rx=config.antenna.ue_n_antennas,
            n_tx=config.antenna.bs_n_antennas,
            n_re_samples=config.channel.n_re_samples,
            normalize=config.channel.normalize,
            rng=self.rng,
        )

    def _snr_values(self) -> List[float]:
        start, stop, step = self.cfg.simulation.snr_range_db
        vals = []
        x = start
        # 包含 stop
        while x <= stop + 1e-9:
            vals.append(float(x))
            x += step
        return vals

    def _make_output_dir(self) -> Path:
        root = Path(self.cfg.simulation.output_dir)
        stamp = _dt.datetime.now().strftime("sim_%Y%m%d_%H%M%S")
        out = root / stamp
        out.mkdir(parents=True, exist_ok=True)
        return out

    def _expand_scheme_candidates(self):
        """根据 comparison 配置展开所有候选方案。"""
        candidates = []
        if self.cfg.comparison.enabled:
            scheme_specs = self.cfg.comparison.schemes
        else:
            scheme_specs = [{"scheme_id": self.cfg.mapping.scheme_id, **self.cfg.mapping.params}]

        for spec in scheme_specs:
            scheme_id = int(spec.get("scheme_id", self.cfg.mapping.scheme_id))
            rank = int(spec.get("rank", self.cfg.mapping.rank))
            partition = spec.get("partition", None)
            scheme = create_scheme(
                "flexible_cw",
                scheme_id=scheme_id,
                rank=rank,
                partition=partition,
            )
            candidates.extend(scheme.expand_candidates(rank=rank))
        return candidates

    def _bler_probability(self, sinr_eff_db: float, required_sinr_db: float, slope_db: float) -> float:
        """抽象 BLER 模型。

        required_sinr_db 是 MCS 表中的约 10% BLER 门限。
        这里使用一个平滑函数，让 sinr_eff_db=required_sinr_db 时 BLER 约为 0.1。
        """
        # p = 1/(1 + exp((sinr - center)/slope))
        # 要使 p(req)=0.1，则 center = req - slope*log(9)
        center = required_sinr_db - slope_db * np.log(9.0)
        p = 1.0 / (1.0 + np.exp((sinr_eff_db - center) / max(slope_db, 1e-6)))
        return float(np.clip(p, 1e-5, 1.0))

    def _required_sinr_for_cw(self, cw, layer_sinrs_db_for_cfg=None) -> float:
        """得到 CW 的近似 required SINR。

        普通 scheme 直接查 mcs_index。
        Scheme 2 没有唯一 mcs_index，因此用 Qm 和 code_rate 对应的 SE，
        找 MCS 表中最接近的 SE 的 required_sinr_db。
        """
        if cw.mcs_index is not None:
            for m in self.mcs_table:
                if m.index == cw.mcs_index:
                    return m.required_sinr_db

        se = cw.code_rate * np.mean([cw.layer_modulations[l] for l in cw.layer_indices])
        best = min(self.mcs_table, key=lambda m: abs(m.spectral_efficiency - se))
        return best.required_sinr_db

    def run(self) -> Path:
        out_dir = self._make_output_dir()
        save_resolved_config(self.cfg, out_dir / "config_resolved.yaml")

        summaries: List[SNRSummary] = []
        schemes = self._expand_scheme_candidates()
        snrs = self._snr_values()

        if self.cfg.debug.verbose:
            print(f"输出目录: {out_dir}")
            print(f"候选方案数: {len(schemes)}")
            print(f"SNR点: {snrs}")

        for scheme in schemes:
            for snr_db in snrs:
                summary = self._run_one_scheme_one_snr(scheme, snr_db)
                summaries.append(summary)
                if self.cfg.debug.verbose:
                    print(
                        f"{summary.scheme_label:24s} SNR={snr_db:5.1f} dB "
                        f"BLER={summary.scheme_bler:.4g} "
                        f"TP={summary.total_throughput_bits_per_slot:.1f} bits/slot"
                    )

        save_csv(summaries, out_dir / "results.csv")
        save_json(summaries, out_dir / "results.json")
        plot_bler(summaries, out_dir / "bler_vs_snr.png")
        plot_throughput(summaries, out_dir / "throughput_vs_snr.png")
        return out_dir

    def _run_one_scheme_one_snr(self, scheme, snr_db: float) -> SNRSummary:
        sim = self.cfg.simulation
        total_trials = 0
        scheme_errors = 0
        cw_stats_by_idx: Dict[int, CWSimulationStats] = {}

        last_tx_cfg = None
        last_tb_infos = None
        last_mean_layer_sinrs_db = None

        while total_trials < sim.n_trials_per_snr:
            batch = min(sim.batch_size, sim.n_trials_per_snr - total_trials)

            # 1. 生成 batch 信道奇异值。
            s = self.channel.sample_singular_values(batch, rank=scheme.rank)
            # shape: [batch, n_re_samples, rank]
            layer_sinrs_db_samples = estimate_post_sinr_db_from_singular_values(
                s, snr_db=snr_db, rank=scheme.rank
            )

            # 2. 按我们讨论的第一版简化：同一 batch 共用一个 TransmissionConfig。
            #    用 batch+RE 平均的 layer SINR 做 MCS/Qm 选择。
            mean_layer_sinrs_db = np.mean(layer_sinrs_db_samples, axis=(0, 1))
            tx_cfg = scheme.configure_transmission(
                layer_sinrs_db=mean_layer_sinrs_db,
                mcs_table=self.mcs_table,
                sim_cfg=sim,
            )
            tb_infos = self.tb_manager.compute_for_transmission(tx_cfg)
            tb_by_cw = {tb.cw_index: tb for tb in tb_infos}

            # 初始化 CW stats
            for cw in tx_cfg.cw_configs:
                if cw.cw_index not in cw_stats_by_idx:
                    cw_stats_by_idx[cw.cw_index] = CWSimulationStats(
                        cw_index=cw.cw_index,
                        tb_size=tb_by_cw[cw.cw_index].tb_size,
                    )

            # 3. 对 batch 内每个 trial 抽象判错。
            #    每个 CW 计算实际 trial 的 harmonic SINR，再按 MCS 门限随机产生错误。
            for b in range(batch):
                trial_has_error = False
                # 对每个 trial，先对多个 RE 样本平均；更真实的 backend 可做 EESM/MIESM。
                trial_layer_sinrs_db = np.mean(layer_sinrs_db_samples[b], axis=0)

                for cw in tx_cfg.cw_configs:
                    sub_sinrs = trial_layer_sinrs_db[cw.layer_indices]
                    sinr_eff_db = harmonic_mean_sinr_db(sub_sinrs)
                    req = self._required_sinr_for_cw(cw)
                    p_err = self._bler_probability(
                        sinr_eff_db=sinr_eff_db,
                        required_sinr_db=req,
                        slope_db=sim.bler_slope_db,
                    )
                    err = self.rng.random() < p_err

                    stat = cw_stats_by_idx[cw.cw_index]
                    stat.trials += 1
                    stat.errors += int(err)
                    if err:
                        trial_has_error = True

                scheme_errors += int(trial_has_error)

            total_trials += batch
            last_tx_cfg = tx_cfg
            last_tb_infos = tb_infos
            last_mean_layer_sinrs_db = mean_layer_sinrs_db

        cw_stats = [cw_stats_by_idx[i] for i in sorted(cw_stats_by_idx)]
        total_tp = sum(cw.throughput_bits_per_slot for cw in cw_stats)

        metadata = {
            "scheme_id": scheme.scheme_id,
            "partition": "+".join(map(str, last_tx_cfg.partition)) if last_tx_cfg and last_tx_cfg.partition else "",
            "rank": scheme.rank,
        }
        if last_mean_layer_sinrs_db is not None:
            for i, x in enumerate(last_mean_layer_sinrs_db):
                metadata[f"mean_layer{i}_sinr_db"] = float(x)
        if last_tx_cfg is not None:
            for cw in last_tx_cfg.cw_configs:
                metadata[f"cw{cw.cw_index}_layers"] = str(cw.layer_indices)
                metadata[f"cw{cw.cw_index}_qms"] = str(cw.layer_modulations)
                metadata[f"cw{cw.cw_index}_rate"] = float(cw.code_rate)
                metadata[f"cw{cw.cw_index}_mcs"] = -1 if cw.mcs_index is None else int(cw.mcs_index)

        return SNRSummary(
            scheme_label=last_tx_cfg.scheme_name if last_tx_cfg else scheme.label(),
            snr_db=snr_db,
            trials=total_trials,
            scheme_errors=scheme_errors,
            cw_stats=cw_stats,
            total_throughput_bits_per_slot=total_tp,
            metadata=metadata,
        )

```


## `lls_platform/sim/stats.py`

```python
from __future__ import annotations

from dataclasses import asdict
from typing import Dict, List
import csv
import json
from pathlib import Path

from lls_platform.core.data_structures import SNRSummary


def summaries_to_rows(summaries: List[SNRSummary]) -> List[Dict]:
    rows = []
    for s in summaries:
        row = {
            "scheme": s.scheme_label,
            "snr_db": s.snr_db,
            "trials": s.trials,
            "scheme_bler": s.scheme_bler,
            "total_throughput_bits_per_slot": s.total_throughput_bits_per_slot,
        }
        for cw in s.cw_stats:
            row[f"cw{cw.cw_index}_bler"] = cw.bler
            row[f"cw{cw.cw_index}_tb_size"] = cw.tb_size
            row[f"cw{cw.cw_index}_throughput_bits_per_slot"] = cw.throughput_bits_per_slot
        row.update({f"meta_{k}": v for k, v in s.metadata.items() if isinstance(v, (str, int, float, bool))})
        rows.append(row)
    return rows


def save_csv(summaries: List[SNRSummary], path: str | Path) -> None:
    rows = summaries_to_rows(summaries)
    if not rows:
        return
    # 不同 scheme 的 CW 数不同，字段并集作为表头。
    fieldnames = sorted(set().union(*(r.keys() for r in rows)))
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _summary_to_jsonable(s: SNRSummary) -> Dict:
    d = asdict(s)
    d["scheme_bler"] = s.scheme_bler
    for cw_d, cw in zip(d["cw_stats"], s.cw_stats):
        cw_d["bler"] = cw.bler
        cw_d["throughput_bits_per_slot"] = cw.throughput_bits_per_slot
    return d


def save_json(summaries: List[SNRSummary], path: str | Path) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump([_summary_to_jsonable(s) for s in summaries], f, indent=2, ensure_ascii=False)

```


## `lls_platform/tx/__init__.py`

```python

```


## `lls_platform/tx/symbol_mapper.py`

```python
from __future__ import annotations

from typing import List
from lls_platform.core.data_structures import (
    SymbolMapping,
    SymbolMappingEntry,
    TBInfo,
    TransmissionConfig,
)


class SymbolMapper:
    """符号到 layer/RE 的映射器。

    第一版不真正执行 Tensor scatter/gather，而是生成可验证的映射表。
    这个映射表未来可以直接转换成 TensorFlow gather/scatter 索引。
    """

    def generate(self, tx_cfg: TransmissionConfig, tb_infos: List[TBInfo]) -> SymbolMapping:
        tb_by_cw = {tb.cw_index: tb for tb in tb_infos}
        mapping = SymbolMapping()

        for cw in tx_cfg.cw_configs:
            tb = tb_by_cw[cw.cw_index]
            symbol_idx = 0

            # 每个 CB 的编码 bit 数 E。普通 CW 统一 Qm 时可简单按符号数切分。
            # Scheme 2 per-layer Qm 时，bit 流按 layer 容量顺序切分。
            if cw.same_qm:
                # NR 风格 round-robin：RE 外层、layer 内层。
                qm = cw.representative_qm
                cb_symbol_budget = [cb.E // qm for cb in tb.cb_infos]
                cb_idx = 0
                used_in_cb = 0

                for re in range(tb.n_re_per_layer):
                    for layer in cw.layer_indices:
                        mapping.add(SymbolMappingEntry(
                            cw_index=cw.cw_index,
                            symbol_index=symbol_idx,
                            layer_index=layer,
                            re_index=re,
                            cb_index=cb_idx,
                        ))
                        symbol_idx += 1
                        used_in_cb += 1
                        if cb_idx < len(cb_symbol_budget) - 1 and used_in_cb >= cb_symbol_budget[cb_idx]:
                            cb_idx += 1
                            used_in_cb = 0
            else:
                # Scheme 2：每层 Qm 不同。按 layer 顺序把 bit 分组并调制，
                # 因此符号流顺序为 Layer0 所有 RE、Layer1 所有 RE、...
                # CB 归属按 E 累计近似划分，保持发射和接收反路由一致。
                bit_cursor = 0
                cb_edges = []
                acc = 0
                for cb in tb.cb_infos:
                    acc += cb.E
                    cb_edges.append(acc)

                for layer in cw.layer_indices:
                    qm = cw.layer_modulations[layer]
                    for re in range(tb.n_re_per_layer):
                        # 当前符号对应的 bit 起点
                        current_bit_pos = bit_cursor
                        cb_idx = 0
                        while cb_idx < len(cb_edges) - 1 and current_bit_pos >= cb_edges[cb_idx]:
                            cb_idx += 1
                        mapping.add(SymbolMappingEntry(
                            cw_index=cw.cw_index,
                            symbol_index=symbol_idx,
                            layer_index=layer,
                            re_index=re,
                            cb_index=cb_idx,
                        ))
                        symbol_idx += 1
                        bit_cursor += qm

        return mapping

```


## `lls_platform/tx/tb_manager.py`

```python
from __future__ import annotations

import math
from typing import List

from lls_platform.core.config import ResourceConfig
from lls_platform.core.data_structures import CBInfo, CWConfig, TBInfo, TransmissionConfig


NR_VALID_ZC = [
    2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,
    18,20,22,24,26,28,30,32,36,40,44,48,52,
    56,60,64,72,80,88,96,104,112,120,128,144,
    160,176,192,208,224,240,256,288,320,352,384
]


def count_dmrs_re_per_prb(resource: ResourceConfig) -> int:
    """估算每 PRB 中 DMRS 占用的 RE 数。

    第一版默认 reserve_dmrs_re=False，因此不会调用扣除。
    如果打开 reserve_dmrs_re:
    - DMRS Type 1: 每个 DMRS OFDM 符号每 PRB 约 6 RE
    - DMRS Type 2: 每个 DMRS OFDM 符号每 PRB 约 4 RE
    """
    if not resource.reserve_dmrs_re or not resource.dmrs_symbol_indices:
        return 0
    per_symbol = 6 if resource.dmrs_type == 1 else 4
    return per_symbol * len(resource.dmrs_symbol_indices)


def compute_n_re_per_layer(resource: ResourceConfig) -> int:
    """计算每层可用数据 RE 数。"""
    n_sc = resource.n_prbs * 12
    total = n_sc * resource.pdsch_n_symbols
    dmrs = resource.n_prbs * count_dmrs_re_per_prb(resource)
    return int(total - dmrs)


def quantize_tbs(n_info: float) -> int:
    """简化版 TBS 量化。

    严格 NR TBS 规则比较复杂。第一版先按补充文档2建议：
    - 小 TB: 至少 24 bit，8bit 对齐
    - 大 TB: max(3840, 8*round(n_info/8))
    后续可以替换为 TS 38.214 5.1.3.2 完整版本。
    """
    if n_info <= 0:
        return 24
    if n_info <= 3824:
        return max(24, int(8 * round(n_info / 8)))
    return int(max(3840, 8 * round(n_info / 8)))


def choose_base_graph(tbs: int, code_rate: float) -> tuple[int, int]:
    """简化版 LDPC base graph 选择。返回 (bg, K_cb_max)。"""
    if tbs > 3824 and code_rate > 0.25:
        return 1, 8448
    # 低码率或小 TB 使用 BG2 更稳妥
    return 2, 3840


def find_ldpc_lifting_size(K_i: int, base_graph: int) -> tuple[int, int, int]:
    """根据 K_i 找到合法 Zc 和 K_ldpc。

    BG1: K_base=22
    BG2: K_base=10
    """
    K_base = 22 if base_graph == 1 else 10
    min_zc = math.ceil(K_i / K_base)
    for zc in NR_VALID_ZC:
        if zc >= min_zc:
            K_ldpc = K_base * zc
            return zc, K_ldpc, K_ldpc - K_i
    raise ValueError(f"K_i={K_i} 太大，无法找到合法 Zc")


class TBManager:
    """TB/TBS/CB 分段管理器。"""

    def __init__(self, resource: ResourceConfig):
        self.resource = resource

    def compute_for_transmission(self, tx_cfg: TransmissionConfig) -> List[TBInfo]:
        return [self.compute_for_cw(cw) for cw in tx_cfg.cw_configs]

    def compute_for_cw(self, cw: CWConfig) -> TBInfo:
        n_re_per_layer = compute_n_re_per_layer(self.resource)

        # 该 CW 的编码 bit 容量。
        # 普通 scheme: n_re * 层数 * 统一 Qm
        # Scheme 2: n_re * sum(每层 Qm)
        n_bits_total = 0
        for layer in cw.layer_indices:
            n_bits_total += n_re_per_layer * cw.layer_modulations[layer]

        n_info = n_bits_total * cw.code_rate
        tbs = quantize_tbs(n_info)

        # TB CRC。这里用于分段尺寸计算，bit-level backend 时可进一步细化。
        L_tbcrc = 24 if tbs > 3824 else 16
        B = tbs + L_tbcrc

        bg, K_cb_max = choose_base_graph(tbs, cw.code_rate)

        if B <= K_cb_max:
            n_cbs = 1
            L_cbcrc = 0
        else:
            L_cbcrc = 24
            n_cbs = math.ceil(B / (K_cb_max - L_cbcrc))

        B_with_cbcrc = B + n_cbs * L_cbcrc
        K_base = B_with_cbcrc // n_cbs
        K_rem = B_with_cbcrc % n_cbs

        K_list = [K_base + (1 if i < K_rem else 0) for i in range(n_cbs)]

        # Rate matching 目标长度 E：让所有 CB 的 E 之和严格等于资源容量。
        E_base = n_bits_total // n_cbs
        E_rem = n_bits_total % n_cbs

        cb_infos: List[CBInfo] = []
        for i, K_i in enumerate(K_list):
            zc, K_ldpc, n_null = find_ldpc_lifting_size(K_i, bg)
            E_i = E_base + (1 if i < E_rem else 0)
            cb_infos.append(CBInfo(
                cb_index=i,
                K=K_i,
                K_ldpc=K_ldpc,
                N_null=n_null,
                E=E_i,
                Zc=zc,
            ))

        assert sum(cb.E for cb in cb_infos) == n_bits_total, \
            "sum(E_cb) 必须等于该 CW 的编码 bit 容量"

        total_re = n_re_per_layer * len(cw.layer_indices)
        actual_se = tbs / total_re if total_re > 0 else 0.0

        return TBInfo(
            cw_index=cw.cw_index,
            tb_size=tbs,
            n_cbs=n_cbs,
            base_graph=bg,
            cb_infos=cb_infos,
            n_re_per_layer=n_re_per_layer,
            n_bits_total=n_bits_total,
            actual_se=actual_se,
        )

```


## `lls_platform/utils/__init__.py`

```python

```


## `lls_platform/utils/integer_partition.py`

```python
from __future__ import annotations

from typing import List


def integer_partitions_nonincreasing(n: int, max_part: int | None = None) -> List[List[int]]:
    """生成 n 的非增整数分拆。

    例如 n=4:
        [4], [3,1], [2,2], [2,1,1], [1,1,1,1]

    这正好对应补充文档中的 Scheme 7 连续层分组方式。
    """
    if n == 0:
        return [[]]
    if n < 0:
        return []
    if max_part is None:
        max_part = n


    out: List[List[int]] = []
    for first in range(min(max_part, n), 0, -1):
        for rest in integer_partitions_nonincreasing(n - first, first):
            out.append([first] + rest)
    return out


def partition_to_layer_groups(partition: List[int]) -> List[List[int]]:
    """把整数分拆转成连续层分组。

    例如 [2,1,1] -> [[0,1], [2], [3]]
    """
    groups = []
    cursor = 0
    for size in partition:
        groups.append(list(range(cursor, cursor + size)))
        cursor += size
    return groups

```


## `lls_platform/utils/mcs_selection.py`

```python
from __future__ import annotations

import math
from typing import Dict, Iterable, List
import numpy as np

from lls_platform.core.data_structures import MCSEntry


def db_to_linear(x_db):
    return np.power(10.0, np.asarray(x_db, dtype=float) / 10.0)


def linear_to_db(x):
    x = np.asarray(x, dtype=float)
    return 10.0 * np.log10(np.maximum(x, 1e-30))


def harmonic_mean_sinr_db(layer_sinrs_db: Iterable[float]) -> float:
    """线性域调和平均 SINR，然后转回 dB。

    补充文档2要求 per-CW MCS 选择使用调和平均，
    因为一个 CW 的 bit 分散在多层上，弱层会拖累整体 BLER。
    """
    sinr = db_to_linear(list(layer_sinrs_db))
    sinr = np.maximum(sinr, 1e-12)
    hm = len(sinr) / np.sum(1.0 / sinr)
    return float(linear_to_db(hm))


def sinr_to_achievable_se(sinr_db: float, gap_db: float = 2.5) -> float:
    """Shannon gap 方法。

    SE = log2(1 + SINR/Gamma)
    Gamma = 10^(gap_db/10)
    """
    sinr = 10 ** (sinr_db / 10.0)
    gap = 10 ** (gap_db / 10.0)
    return math.log2(1.0 + sinr / gap)


def select_mcs_for_cw(
    layer_sinrs_db: Iterable[float],
    mcs_table: List[MCSEntry],
    gap_db: float = 2.5,
    fixed_mcs: int | None = None,
) -> MCSEntry:
    """根据 CW 占用层的 post-SINR 自适应选择 MCS。

    如果 fixed_mcs 不为 None，则进入 debug override 模式。
    """
    if fixed_mcs is not None:
        for m in mcs_table:
            if m.index == fixed_mcs:
                return m
        raise ValueError(f"fixed_mcs={fixed_mcs} 不在 MCS 表中")

    sinr_eff_db = harmonic_mean_sinr_db(layer_sinrs_db)
    achievable_se = sinr_to_achievable_se(sinr_eff_db, gap_db)

    # 选择 SE 不超过 achievable_se 的最高 MCS。
    best = mcs_table[0]
    for mcs in mcs_table:
        if mcs.spectral_efficiency <= achievable_se:
            best = mcs
    return best


def select_per_layer_qm(
    layer_sinrs_db: Iterable[float],
    qpsk_max_db: float = 5.0,
    qam16_max_db: float = 12.0,
    qam64_max_db: float = 18.0,
) -> Dict[int, int]:
    """Scheme 2：根据每层 post-SINR 选择每层 Qm。"""
    result: Dict[int, int] = {}
    for layer_idx, sinr_db in enumerate(layer_sinrs_db):
        if sinr_db < qpsk_max_db:
            result[layer_idx] = 2
        elif sinr_db < qam16_max_db:
            result[layer_idx] = 4
        elif sinr_db < qam64_max_db:
            result[layer_idx] = 6
        else:
            result[layer_idx] = 8
    return result


def quantize_code_rate_to_mcs_table(
    target_rate: float,
    mcs_table: List[MCSEntry],
) -> float:
    """把连续码率量化到 MCS 表中最接近的合法码率。

    Scheme 2 可能出现“每层 Qm 不同但 CW 统一 R”的情况，
    此时不一定能用某一个标准 MCS 完整表示，因此只量化 code_rate。
    """
    target_rate = min(max(target_rate, 0.05), 0.95)
    rates = np.asarray([m.code_rate for m in mcs_table])
    idx = int(np.argmin(np.abs(rates - target_rate)))
    return float(rates[idx])


def select_code_rate_for_per_layer_qm(
    layer_sinrs_db: Iterable[float],
    layer_qm: Dict[int, int],
    mcs_table: List[MCSEntry],
    gap_db: float = 2.5,
) -> float:
    """Scheme 2：给定每层 SINR 和 Qm，选择统一 CW code rate。

    R = min_i achievable_se_i / Qm_i
    然后量化到 MCS 表中的合法码率。
    """
    candidates = []
    for layer_idx, sinr_db in enumerate(layer_sinrs_db):
        se_i = sinr_to_achievable_se(float(sinr_db), gap_db)
        qm_i = layer_qm[layer_idx]
        candidates.append(se_i / qm_i)
    target = min(candidates) if candidates else 0.05
    return quantize_code_rate_to_mcs_table(target, mcs_table)


def estimate_post_sinr_db_from_singular_values(
    singular_values: np.ndarray,
    snr_db: float,
    rank: int,
) -> np.ndarray:
    """根据补充文档2公式计算每层 post-SINR。

    SINR_i = SNR * sigma_i^2 / rank

    参数：
        singular_values: [..., rank]，前 rank 个奇异值
        snr_db: per-RE SNR，单位 dB
        rank: 层数
    返回：
        与 singular_values 同 shape 的 SINR(dB)
    """
    snr_linear = 10 ** (snr_db / 10.0)
    sinr_linear = snr_linear * (np.asarray(singular_values) ** 2) / float(rank)
    return linear_to_db(sinr_linear)

```


## `lls_platform/utils/mcs_tables.py`

```python
from __future__ import annotations

from typing import List
from lls_platform.core.data_structures import MCSEntry


def get_mcs_table(name: str = "approx_256qam") -> List[MCSEntry]:
    """返回 MCS 表。

    说明：
    - 这里使用补充文档中的近似表，包含 Qm、码率和达到约 10% BLER 的所需 SINR。
    - 真实论文结果建议后续用 AWGN/Sionna 标定 required_sinr_db。
    """
    if name != "approx_256qam":
        raise ValueError(f"暂不支持 MCS 表: {name}")

    raw = [
        (0, "QPSK",   2, 0.12, -5.5),
        (1, "QPSK",   2, 0.19, -3.5),
        (2, "QPSK",   2, 0.30, -1.5),
        (3, "QPSK",   2, 0.44,  0.5),
        (4, "QPSK",   2, 0.59,  2.5),
        (5, "16QAM",  4, 0.37,  4.0),
        (6, "16QAM",  4, 0.42,  5.0),
        (7, "16QAM",  4, 0.48,  6.0),
        (8, "16QAM",  4, 0.54,  7.0),
        (9, "16QAM",  4, 0.60,  8.5),
        (10, "16QAM", 4, 0.64,  9.5),
        (11, "64QAM", 6, 0.46, 10.5),
        (12, "64QAM", 6, 0.50, 11.5),
        (13, "64QAM", 6, 0.55, 12.5),
        (14, "64QAM", 6, 0.60, 13.5),
        (15, "64QAM", 6, 0.65, 15.0),
        (16, "64QAM", 6, 0.72, 16.0),
        (17, "64QAM", 6, 0.75, 17.0),
        (18, "64QAM", 6, 0.82, 18.0),
        (19, "256QAM", 8, 0.67, 19.0),
        (20, "256QAM", 8, 0.70, 20.0),
        (21, "256QAM", 8, 0.75, 21.0),
        (22, "256QAM", 8, 0.80, 22.0),
        (23, "256QAM", 8, 0.85, 23.5),
        (24, "256QAM", 8, 0.89, 24.5),
        (25, "256QAM", 8, 0.93, 26.0),
        (26, "256QAM", 8, 0.95, 27.5),
        (27, "256QAM", 8, 0.95, 28.0),
    ]
    return [MCSEntry(*x) for x in raw]


def get_mcs_by_index(index: int, table: List[MCSEntry]) -> MCSEntry:
    for m in table:
        if m.index == index:
            return m
    raise ValueError(f"MCS index {index} 不在表中")

```


## `lls_platform/utils/plotting.py`

```python
from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import List

import matplotlib.pyplot as plt

from lls_platform.core.data_structures import SNRSummary


def plot_bler(summaries: List[SNRSummary], path: str | Path) -> None:
    groups = defaultdict(list)
    for s in summaries:
        groups[s.scheme_label].append(s)

    plt.figure()
    for label, vals in groups.items():
        vals = sorted(vals, key=lambda x: x.snr_db)
        xs = [v.snr_db for v in vals]
        ys = [max(v.scheme_bler, 1e-5) for v in vals]
        plt.semilogy(xs, ys, marker="o", label=label)
    plt.xlabel("SNR (dB)")
    plt.ylabel("BLER")
    plt.grid(True, which="both")
    plt.legend(fontsize=8)
    plt.tight_layout()
    plt.savefig(path, dpi=200)
    plt.close()


def plot_throughput(summaries: List[SNRSummary], path: str | Path) -> None:
    groups = defaultdict(list)
    for s in summaries:
        groups[s.scheme_label].append(s)

    plt.figure()
    for label, vals in groups.items():
        vals = sorted(vals, key=lambda x: x.snr_db)
        xs = [v.snr_db for v in vals]
        ys = [v.total_throughput_bits_per_slot for v in vals]
        plt.plot(xs, ys, marker="o", label=label)
    plt.xlabel("SNR (dB)")
    plt.ylabel("Throughput (bits/slot)")
    plt.grid(True)
    plt.legend(fontsize=8)
    plt.tight_layout()
    plt.savefig(path, dpi=200)
    plt.close()

```


## `run.py`

```python
from __future__ import annotations

import argparse
from lls_platform.core.config import load_config
from lls_platform.sim.orchestrator import LinkAbstractionOrchestrator


def main():
    parser = argparse.ArgumentParser(description="灵活码字映射 LLS Platform V1")
    parser.add_argument("--config", type=str, required=True, help="YAML 配置文件路径")
    args = parser.parse_args()

    cfg = load_config(args.config)

    if cfg.simulation.backend != "link_abstraction":
        raise NotImplementedError(
            "当前交付版本默认实现 link_abstraction backend。"
            "Sionna bit-level backend 建议在核心逻辑验证后接入。"
        )

    runner = LinkAbstractionOrchestrator(cfg)
    out_dir = runner.run()
    print(f"\n仿真完成。结果保存在: {out_dir}")


if __name__ == "__main__":
    main()

```


## `tests/test_core.py`

```python
import numpy as np

from lls_platform.utils.integer_partition import integer_partitions_nonincreasing, partition_to_layer_groups
from lls_platform.utils.mcs_tables import get_mcs_table
from lls_platform.utils.mcs_selection import select_per_layer_qm, select_mcs_for_cw
from lls_platform.schemes.flexible_cw import FlexibleCWScheme
from lls_platform.core.config import SimulationConfig, ResourceConfig
from lls_platform.tx.tb_manager import TBManager
from lls_platform.tx.symbol_mapper import SymbolMapper


def test_integer_partitions_rank4():
    assert integer_partitions_nonincreasing(4) == [
        [4], [3, 1], [2, 2], [2, 1, 1], [1, 1, 1, 1]
    ]
    assert partition_to_layer_groups([2, 1, 1]) == [[0, 1], [2], [3]]


def test_scheme_generation_rank4():
    mcs_table = get_mcs_table()
    sim = SimulationConfig()
    layer_sinrs = np.array([20.0, 18.0, 12.0, 8.0])

    s1 = FlexibleCWScheme(scheme_id=1, rank=4)
    tx1 = s1.configure_transmission(layer_sinrs, mcs_table, sim)
    assert [cw.layer_indices for cw in tx1.cw_configs] == [[0, 1, 2, 3]]

    s4 = FlexibleCWScheme(scheme_id=4, rank=4)
    tx4 = s4.configure_transmission(layer_sinrs, mcs_table, sim)
    assert [cw.layer_indices for cw in tx4.cw_configs] == [[0, 1], [2, 3]]

    s6 = FlexibleCWScheme(scheme_id=6, rank=4)
    tx6 = s6.configure_transmission(layer_sinrs, mcs_table, sim)
    assert [cw.layer_indices for cw in tx6.cw_configs] == [[0], [1], [2], [3]]


def test_scheme2_per_layer_qm():
    qms = select_per_layer_qm([20, 18, 12, 8])
    assert qms == {0: 8, 1: 8, 2: 6, 3: 4} or qms == {0: 8, 1: 8, 2: 6, 3: 4}

    mcs_table = get_mcs_table()
    sim = SimulationConfig()
    s2 = FlexibleCWScheme(scheme_id=2, rank=4)
    tx2 = s2.configure_transmission(np.array([20.0, 18.0, 12.0, 8.0]), mcs_table, sim)
    cw = tx2.cw_configs[0]
    assert len(set(cw.layer_modulations.values())) > 1
    assert 0.05 <= cw.code_rate <= 0.95


def test_mcs_selection_monotonic():
    table = get_mcs_table()
    low = select_mcs_for_cw([0.0], table, gap_db=2.5)
    high = select_mcs_for_cw([25.0], table, gap_db=2.5)
    assert high.index >= low.index
    assert high.spectral_efficiency >= low.spectral_efficiency


def test_tb_manager_closure():
    table = get_mcs_table()
    sim = SimulationConfig()
    tx = FlexibleCWScheme(scheme_id=4, rank=4).configure_transmission(
        np.array([20.0, 18.0, 12.0, 8.0]), table, sim
    )
    tb_infos = TBManager(ResourceConfig(n_prbs=10, pdsch_n_symbols=10)).compute_for_transmission(tx)
    for tb in tb_infos:
        assert tb.total_E == tb.n_bits_total
        assert tb.tb_size > 0
        assert tb.n_cbs >= 1


def test_symbol_mapping_count():
    table = get_mcs_table()
    sim = SimulationConfig()
    resource = ResourceConfig(n_prbs=4, pdsch_n_symbols=2)
    tx = FlexibleCWScheme(scheme_id=4, rank=4).configure_transmission(
        np.array([20.0, 18.0, 12.0, 8.0]), table, sim
    )
    tb_infos = TBManager(resource).compute_for_transmission(tx)
    mapping = SymbolMapper().generate(tx, tb_infos)

    expected_symbols = 0
    for cw, tb in zip(tx.cw_configs, tb_infos):
        if cw.same_qm:
            expected_symbols += tb.n_bits_total // cw.representative_qm
        else:
            expected_symbols += tb.n_re_per_layer * len(cw.layer_indices)
    assert len(mapping.entries) == expected_symbols

```
