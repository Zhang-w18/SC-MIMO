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
    """某个 SNR 点下某个 CW 的统计结果。

    2026-04 更新：错误抽样从 CW 级下沉到 CB 级。

    字段含义：
    - trials/errors：CW/TB 级统计；只要该 CW 中任意 CB 错误，则该 CW/TB 错误。
    - cb_trials/cb_errors：CB 级统计；用于计算真正的 CB-BLER。
    - successful_payload_bits：所有成功 CB 携带的 payload bits 总和。
      注意它不包括 TB CRC、CB CRC、NULL/filler bits。
    """
    cw_index: int
    trials: int = 0
    errors: int = 0
    tb_size: int = 0
    n_cbs: int = 0
    cb_trials: int = 0
    cb_errors: int = 0
    successful_payload_bits: float = 0.0

    @property
    def bler(self) -> float:
        """CW/TB-BLER：任意 CB 错误则该 CW 错误。"""
        return self.errors / self.trials if self.trials else 0.0

    @property
    def cb_bler(self) -> float:
        """CB-BLER：失败 CB 数 / 总 CB 数。"""
        return self.cb_errors / self.cb_trials if self.cb_trials else 0.0

    @property
    def throughput_bits_per_slot(self) -> float:
        """CB-goodput：平均每 trial 成功 CB 的 payload bits。"""
        return self.successful_payload_bits / self.trials if self.trials else 0.0


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
    n_re_per_layer: int = 0
    rank: int = 0

    @property
    def scheme_bler(self) -> float:
        """scheme/slot-level BLER：任意 CW 错误则该 trial 错误。"""
        return self.scheme_errors / self.trials if self.trials else 0.0

    @property
    def cb_bler(self) -> float:
        """整个 scheme 的 CB-BLER：所有 CW 的 failed CB / total CB。"""
        total_cb_trials = sum(cw.cb_trials for cw in self.cw_stats)
        total_cb_errors = sum(cw.cb_errors for cw in self.cw_stats)
        return total_cb_errors / total_cb_trials if total_cb_trials else 0.0

    @property
    def goodput_bits_per_slot(self) -> float:
        """总 goodput：平均每 trial 成功 CB 的 payload bits。"""
        return self.total_throughput_bits_per_slot

    @property
    def goodput_se_tf(self) -> float:
        """时频 RE 谱效：goodput / 每层可用数据 RE 数。

        该口径不除以 rank，因此能体现空间复用增益。
        """
        return self.goodput_bits_per_slot / self.n_re_per_layer if self.n_re_per_layer else 0.0

    @property
    def goodput_se_layer_re(self) -> float:
        """layer-RE 谱效：goodput / (每层可用数据 RE 数 × rank)。"""
        denom = self.n_re_per_layer * self.rank
        return self.goodput_bits_per_slot / denom if denom else 0.0
