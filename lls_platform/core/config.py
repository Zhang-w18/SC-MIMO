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
    """信道配置。v2.6 增加 CDL 支持。"""
    model: str = "Rayleigh"
    cdl_type: str = "A"
    delay_spread_ns: float = 30.0
    ue_speed_kmh: float = 3.0
    normalize: bool = True
    direction: str = "downlink"
    num_time_samples: int = 14
    n_re_samples: int = 24


@dataclass
class CSIConfig:
    """发射端 CSI/预编码配置。"""
    mode: str = "ideal"
    precoding_method: str = "svd"


@dataclass
class ChannelEstimationConfig:
    """接收端信道估计配置。

    v3.0-a adds an abstract additive-error effective-channel model.
    The transmitter still uses ideal SVD precoding. At the receiver, an
    estimated effective channel H_hat_eff = H_eff + E is used to build a
    linear equalizer. The actual post-SINR is then evaluated with the true
    H_eff and W(H_hat_eff).

    mode:
        - ideal: keep v2.9 ideal receiver behavior.
        - additive_error / nmse: enable abstract CSI error model.
    nmse_db:
        E[||E||^2] / E[||H_eff||^2] in dB.
    equalizer:
        Linear equalizer used at the UE side: mmse or zf.
    use_actual_rx_post_sinr_for_decoding:
        If True, the LDPC/LLR equivalent layer channel uses the actual RX
        post-SINR under W(H_hat_eff). MCS selection is still ideal unless
        apply_to_mcs_selection is set to True in future versions.
    """
    enabled: bool = False
    mode: str = "ideal"
    error_model: str = "nmse"
    nmse_db: float = -25.0
    equalizer: str = "mmse"
    use_actual_rx_post_sinr_for_decoding: bool = True
    apply_to_mcs_selection: bool = False
    seed_base: int = 93001
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
    mcs_table: str = "nr_64qam"
    shannon_gap_db: float = 2.5

    # Scheme 2 的 per-layer Qm 门限。
    qpsk_max_db: float = 5.0
    qam16_max_db: float = 12.0
    qam64_max_db: float = 18.0

    # link_abstraction BLER 曲线平滑参数，仅用于抽象 backend。
    bler_slope_db: float = 1.5

    backend: str = "link_abstraction"  # 可选: link_abstraction / numpy_bit_level / sionna_ldpc_bit_level


@dataclass
class AdaptiveMCSConfig:
    """v2.6 per-trial adaptive MCS 配置。"""
    enabled: bool = False
    decision_granularity: str = "per_trial"
    method: str = "capacity_average"
    shannon_gap_db: float = 2.5
    mcs_margin_db: float = 1.0
    min_mcs: int = 0
    max_mcs: int = 27
    # Sionna LDPC5GEncoder does not support r < 1/5.
    min_code_rate: float = 0.2
    scheme2_min_code_rate: float = 0.2
    scheme2_allowed_qm: List[int] = field(default_factory=lambda: [2, 4, 6, 8])




@dataclass
class OLLAConfig:
    """v3.1 Outer Loop Link Adaptation (OLLA) 配置。

    OLLA uses a per-scheme/per-CW SINR offset for MCS selection.
    After each trial, the offset is updated from the instantaneous CB error
    fraction e_t = failed_CBs / total_CBs so that the long-term CB-BLER
    approaches target_cb_bler.
    """
    enabled: bool = False
    target_metric: str = "cb_bler"
    target_cb_bler: float = 0.10
    # Default global OLLA update step, in dB.
    step_db: float = 0.05
    # Optional per-scheme step override, e.g. {scheme2: 0.10, scheme7: 0.06}.
    # Matching order in the orchestrator is:
    #   1) exact scheme label, e.g. scheme7_partition_3+1
    #   2) base scheme label, e.g. scheme7
    #   3) global step_db fallback
    scheme_step_db: Dict[str, float] = field(default_factory=dict)
    offset_init_db: float = 0.0
    offset_min_db: float = -6.0
    offset_max_db: float = 6.0
    warmup_trials_per_snr: int = 300
    measure_trials_per_snr: int = 1000
    update_granularity: str = "per_cw"
    update_rule: str = "stochastic"
    reset_per_snr: bool = True
    freeze_after_warmup: bool = False
    force_sequential_updates: bool = True
    record_trace: bool = True
    ewma_beta: float = 0.02


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
    adaptive_mcs: AdaptiveMCSConfig = field(default_factory=AdaptiveMCSConfig)
    olla: OLLAConfig = field(default_factory=OLLAConfig)
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
            "adaptive_mcs": AdaptiveMCSConfig,
            "olla": OLLAConfig,
            "comparison": ComparisonConfig,
            "debug": DebugConfig,
        }
        if field_name in child_map and isinstance(value, dict):
            kwargs[field_name] = _construct_dataclass(child_map[field_name], value)
        else:
            kwargs[field_name] = value
    return cls(**kwargs)



def _validate_config(config: PlatformConfig, source_path: str = "") -> None:
    """Validate cross-field constraints after YAML is loaded."""
    rank = int(config.mapping.rank)
    bs_n = int(config.antenna.bs_n_antennas)
    ue_n = int(config.antenna.ue_n_antennas)

    if rank < 1:
        raise ValueError(f"mapping.rank must be >= 1, got rank={rank}. config={source_path}")

    if rank > ue_n:
        raise ValueError(
            "Unsupported rank configuration: mapping.rank is larger than the number of UE receive antennas. "
            f"rank={rank}, ue_n_antennas={ue_n}. Please reduce mapping.rank or increase "
            "antenna.ue_n_antennas / ue_antenna_array. "
            f"config={source_path}"
        )

    if rank > bs_n:
        raise ValueError(
            "Unsupported rank configuration: mapping.rank is larger than the number of BS transmit antennas. "
            f"rank={rank}, bs_n_antennas={bs_n}. config={source_path}"
        )

    def _prod3(x):
        try:
            if isinstance(x, (list, tuple)) and len(x) == 3:
                return int(x[0]) * int(x[1]) * int(x[2])
        except Exception:
            return None
        return None

    bs_arr_n = _prod3(config.antenna.bs_antenna_array)
    ue_arr_n = _prod3(config.antenna.ue_antenna_array)
    if bs_arr_n is not None and bs_arr_n != bs_n:
        raise ValueError(
            "Inconsistent BS antenna configuration: bs_n_antennas does not match product(bs_antenna_array). "
            f"bs_n_antennas={bs_n}, bs_antenna_array={config.antenna.bs_antenna_array}, "
            f"product={bs_arr_n}. config={source_path}"
        )
    if ue_arr_n is not None and ue_arr_n != ue_n:
        raise ValueError(
            "Inconsistent UE antenna configuration: ue_n_antennas does not match product(ue_antenna_array). "
            f"ue_n_antennas={ue_n}, ue_antenna_array={config.antenna.ue_antenna_array}, "
            f"product={ue_arr_n}. config={source_path}"
        )


    olla = getattr(config, "olla", None)
    if olla is not None and bool(getattr(olla, "enabled", False)):
        target = float(getattr(olla, "target_cb_bler", 0.1))
        if not (0.0 < target < 1.0):
            raise ValueError(f"olla.target_cb_bler must be in (0,1), got {target}. config={source_path}")
        step = float(getattr(olla, "step_db", 0.05))
        if step <= 0.0:
            raise ValueError(f"olla.step_db must be > 0, got {step}. config={source_path}")
        scheme_steps = getattr(olla, "scheme_step_db", {}) or {}
        if not isinstance(scheme_steps, dict):
            raise ValueError(
                "olla.scheme_step_db must be a mapping/dict, e.g. {scheme2: 0.10, scheme7: 0.06}. "
                f"got {type(scheme_steps).__name__}. config={source_path}"
            )
        for scheme_key, scheme_step in scheme_steps.items():
            try:
                scheme_step_f = float(scheme_step)
            except Exception as exc:
                raise ValueError(
                    f"olla.scheme_step_db[{scheme_key!r}] must be numeric, got {scheme_step!r}. "
                    f"config={source_path}"
                ) from exc
            if scheme_step_f <= 0.0:
                raise ValueError(
                    f"olla.scheme_step_db[{scheme_key!r}] must be > 0, got {scheme_step_f}. "
                    f"config={source_path}"
                )
        if int(getattr(olla, "warmup_trials_per_snr", 0)) < 0:
            raise ValueError(f"olla.warmup_trials_per_snr must be >=0. config={source_path}")
        if int(getattr(olla, "measure_trials_per_snr", 0)) <= 0:
            raise ValueError(f"olla.measure_trials_per_snr must be >0. config={source_path}")
        if str(getattr(olla, "update_granularity", "per_cw")).lower() != "per_cw":
            raise ValueError("v3.1 currently supports olla.update_granularity: per_cw only. "
                             f"got {getattr(olla, 'update_granularity', '')}. config={source_path}")

    ce_mode = str(getattr(config.channel_estimation, "mode", "ideal")).lower()
    ce_enabled = bool(getattr(config.channel_estimation, "enabled", False)) or ce_mode in ("additive_error", "nmse")
    if ce_enabled:
        if ce_mode not in ("additive_error", "nmse", "ideal"):
            raise ValueError(
                "Unsupported channel_estimation.mode. Supported: ideal, additive_error, nmse. "
                f"mode={ce_mode}, config={source_path}"
            )
        if str(getattr(config.channel_estimation, "equalizer", "mmse")).lower() not in ("mmse", "zf"):
            raise ValueError(
                "Unsupported channel_estimation.equalizer. Supported: mmse, zf. "
                f"equalizer={getattr(config.channel_estimation, 'equalizer', '')}, config={source_path}"
            )


def load_config(path: str) -> PlatformConfig:
    """读取 YAML 并合并默认配置。"""
    default_dict = dataclass_to_dict(PlatformConfig())
    with open(path, "r", encoding="utf-8") as f:
        user_dict = yaml.safe_load(f) or {}
    merged = _deep_update(default_dict, user_dict)
    config = _construct_dataclass(PlatformConfig, merged)
    _validate_config(config, source_path=str(path))
    return config


def save_resolved_config(config: PlatformConfig, path: str) -> None:
    """保存本次运行的完整配置。

    PyYAML 5.1 之后支持 sort_keys=False；
    但你的环境是 PyYAML 3.13，safe_dump 不支持 sort_keys 参数。
    因此这里先尝试新版写法，失败后自动退回兼容写法。
    """
    with open(path, "w", encoding="utf-8") as f:
        data = dataclass_to_dict(config)
        try:
            yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)
        except TypeError:
            # 兼容 PyYAML 3.x。字段顺序可能不是原始顺序，但内容完整。
            yaml.safe_dump(data, f, allow_unicode=True)
