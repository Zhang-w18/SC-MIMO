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
    integer_compositions,
    partition_to_layer_groups,
    validate_partition,
)
from lls_platform.utils.mcs_selection import (
    select_mcs_for_cw,
    select_mcs_by_se,
    select_per_layer_qm,
    select_per_layer_qm_by_se,
    select_code_rate_for_per_layer_qm,
    select_code_rate_for_per_layer_qm_by_se,
    optimize_scheme2_rate_qm_by_se,
)


def nr_baseline_partition(rank: int) -> List[int]:
    """NR-like baseline partition for ranks 1..8.

    v2.7 intentionally does not define Scheme 1 for rank>8.
    """
    rank = int(rank)
    if rank <= 4:
        return [rank]
    if rank <= 8:
        return {5: [2, 3], 6: [3, 3], 7: [3, 4], 8: [4, 4]}[rank]
    raise ValueError("Scheme 1 NR baseline only applies to rank 1..8 in this platform.")


def dual_cw_balanced_partition(rank: int) -> List[int]:
    """Scheme 4: at most 2 CWs.

    v2.7: for odd ranks, the first CW gets fewer layers than the second CW.
    This pairs the strongest early layers into a smaller CW when layers are
    ordered by descending post-SINR.
    """
    rank = int(rank)
    if rank <= 1:
        return [1]
    first = rank // 2
    second = rank - first
    return [first, second]


def scheme2_partition(rank: int) -> List[int]:
    """Scheme 2 partition.

    rank<=8: NR-like partition, same as Scheme 1.
    rank>8: use Scheme 4 partition, as requested by the user.
    """
    rank = int(rank)
    if rank <= 8:
        return nr_baseline_partition(rank)
    return dual_cw_balanced_partition(rank)


def max_two_layers_partition(rank: int) -> List[int]:
    out=[]; remain=int(rank)
    while remain>0:
        s=min(2, remain); out.append(s); remain-=s
    return out


def one_cw_per_layer_partition(rank: int) -> List[int]:
    return [1 for _ in range(int(rank))]


@register_scheme("flexible_cw")
@dataclass
class FlexibleCWScheme(MappingScheme):
    """Flexible CW/layer mapping schemes.

    v2.7 updates:
    - Scheme 1 is disabled for rank>8.
    - Scheme 2 uses Scheme 4 partition for rank>8.
    - Scheme 4 odd-rank split is [floor(rank/2), ceil(rank/2)].
    - Scheme 7 can be restricted by an explicit partition or partition list.
    - Mapping rule name reflects NR-like symbol round-robin layer mapping.
    """
    scheme_id: int = 1
    rank: int = 4
    partition: Optional[List[int]] = None
    partitions: Optional[List[List[int]]] = None
    scheme7_mode: str = "partitions_nonincreasing"

    def label(self) -> str:
        if self.scheme_id == 7 and self.partition is not None:
            return "scheme7_partition_" + "+".join(map(str, self.partition))
        return f"scheme{self.scheme_id}"

    def expand_candidates(self, rank: Optional[int] = None) -> List["FlexibleCWScheme"]:
        rank = self.rank if rank is None else int(rank)
        # Scheme 1 is intentionally inactive for rank>8.
        if self.scheme_id == 1 and rank > 8:
            return []
        if self.scheme_id != 7:
            return [self]
        if self.partition is not None:
            p = validate_partition(self.partition, rank)
            return [FlexibleCWScheme(scheme_id=7, rank=rank, partition=p, scheme7_mode=self.scheme7_mode)]
        if self.partitions is not None:
            return [FlexibleCWScheme(scheme_id=7, rank=rank, partition=validate_partition(p, rank), scheme7_mode=self.scheme7_mode)
                    for p in self.partitions]
        mode = str(self.scheme7_mode or "partitions_nonincreasing")
        if mode in ("ordered", "ordered_compositions", "compositions"):
            parts = integer_compositions(rank)
        else:
            parts = integer_partitions_nonincreasing(rank)
        return [FlexibleCWScheme(scheme_id=7, rank=rank, partition=p, scheme7_mode=mode) for p in parts]

    def _get_partition(self) -> List[int]:
        if self.partition is not None:
            return validate_partition(self.partition, self.rank)
        if self.scheme_id == 1:
            return nr_baseline_partition(self.rank)
        if self.scheme_id == 2:
            return scheme2_partition(self.rank)
        if self.scheme_id == 3:
            return [self.rank]
        if self.scheme_id == 4:
            return dual_cw_balanced_partition(self.rank)
        if self.scheme_id == 5:
            return max_two_layers_partition(self.rank)
        if self.scheme_id == 6:
            return one_cw_per_layer_partition(self.rank)
        if self.scheme_id == 7:
            return [self.rank]
        raise ValueError(f"未知 scheme_id: {self.scheme_id}")

    def configure_transmission(self, layer_sinrs_db: np.ndarray, mcs_table: List[MCSEntry], sim_cfg: SimulationConfig,
                               layer_se_eff: Optional[np.ndarray] = None, adaptive_cfg=None) -> TransmissionConfig:
        layer_sinrs_db = np.asarray(layer_sinrs_db, dtype=float)
        if len(layer_sinrs_db) < self.rank:
            raise ValueError(f"layer_sinrs_db length {len(layer_sinrs_db)} < rank {self.rank}")
        if layer_se_eff is not None:
            layer_se_eff = np.asarray(layer_se_eff, dtype=float)
            if len(layer_se_eff) < self.rank:
                raise ValueError(f"layer_se_eff length {len(layer_se_eff)} < rank {self.rank}")

        partition = self._get_partition()
        groups = partition_to_layer_groups(partition)
        cw_configs: List[CWConfig] = []
        min_mcs = int(getattr(adaptive_cfg, "min_mcs", 0)) if adaptive_cfg is not None else 0
        max_mcs = int(getattr(adaptive_cfg, "max_mcs", 999)) if adaptive_cfg is not None else 999
        min_code_rate = float(getattr(adaptive_cfg, "min_code_rate", 0.0)) if adaptive_cfg is not None else 0.0
        scheme2_min_code_rate = float(getattr(adaptive_cfg, "scheme2_min_code_rate", min_code_rate)) if adaptive_cfg is not None else min_code_rate

        if self.scheme_id == 2 and sim_cfg.fixed_mcs is None:
            allowed_qm = (
                getattr(adaptive_cfg, "scheme2_allowed_qm", [2, 4, 6, 8])
                if adaptive_cfg is not None
                else [2, 4, 6, 8]
            )
            scheme2_max_code_rate = (
                float(getattr(adaptive_cfg, "scheme2_max_code_rate", 0.95))
                if adaptive_cfg is not None
                else 0.95
            )

            for cw_idx, layers in enumerate(groups):
                if layer_se_eff is not None:
                    # Jointly optimize the unified CW code rate r and per-layer
                    # modulation orders Q_l:
                    #   maximize sum_l r*Q_l, subject to r*Q_l <= SE_l.
                    sub_se = layer_se_eff[layers]
                    opt = optimize_scheme2_rate_qm_by_se(
                        sub_se,
                        mcs_table,
                        allowed_qm=allowed_qm,
                        min_code_rate=scheme2_min_code_rate,
                        max_code_rate=scheme2_max_code_rate,
                    )
                    local_qm = opt["layer_qm_local"]
                    code_rate = float(opt["code_rate"])
                    layer_modulations = {
                        int(layer): int(local_qm[i])
                        for i, layer in enumerate(layers)
                    }
                else:
                    # Fallback for non-SE paths: keep the previous threshold-Qm
                    # logic and then select the unified code rate for that Qm.
                    all_layer_qm = select_per_layer_qm(
                        layer_sinrs_db[:self.rank],
                        sim_cfg.qpsk_max_db,
                        sim_cfg.qam16_max_db,
                        sim_cfg.qam64_max_db,
                    )
                    sub_sinrs = layer_sinrs_db[layers]
                    local_qm = {i: all_layer_qm[layer] for i, layer in enumerate(layers)}
                    code_rate = select_code_rate_for_per_layer_qm(
                        sub_sinrs,
                        local_qm,
                        mcs_table,
                        gap_db=sim_cfg.shannon_gap_db,
                        min_code_rate=min_code_rate,
                    )
                    layer_modulations = {
                        int(layer): int(all_layer_qm[layer])
                        for layer in layers
                    }

                cw_configs.append(CWConfig(
                    cw_index=cw_idx,
                    layer_indices=list(layers),
                    layer_modulations=layer_modulations,
                    mcs_index=None,
                    code_rate=float(code_rate),
                ))
            mapping_rule = "nr_symbol_round_robin_per_layer_qm"
        else:
            for cw_idx, layers in enumerate(groups):
                if sim_cfg.fixed_mcs is not None:
                    mcs = select_mcs_by_se(0.0, mcs_table, fixed_mcs=sim_cfg.fixed_mcs, min_code_rate=min_code_rate)
                elif layer_se_eff is not None:
                    cw_se = float(np.mean(layer_se_eff[layers]))
                    mcs = select_mcs_by_se(cw_se, mcs_table, min_mcs=min_mcs, max_mcs=max_mcs, min_code_rate=min_code_rate)
                else:
                    mcs = select_mcs_for_cw(layer_sinrs_db[layers], mcs_table, gap_db=sim_cfg.shannon_gap_db, min_code_rate=min_code_rate)
                cw_configs.append(CWConfig(
                    cw_index=cw_idx,
                    layer_indices=list(layers),
                    layer_modulations={layer: int(mcs.Qm) for layer in layers},
                    mcs_index=int(mcs.index),
                    code_rate=float(mcs.code_rate),
                ))
            mapping_rule = "nr_symbol_round_robin"

        if not cw_configs:
            raise RuntimeError(f"{self.label()} 没有生成任何 CWConfig")
        return TransmissionConfig(
            scheme_name=self.label(), scheme_id=int(self.scheme_id), rank=int(self.rank),
            cw_configs=cw_configs, mapping_rule=mapping_rule, description=self._description(), partition=partition
        )

    def _description(self) -> str:
        return {
            1: "NR baseline, ranks 1-8 only",
            2: "NR baseline + per-layer Qm adaptive; rank>8 uses Scheme4 split",
            3: "single CW for all ranks",
            4: "dual CW for rank>1, odd rank uses [floor, ceil]",
            5: "max 2 layers per CW",
            6: "one CW per layer",
            7: "enumerated or user-specified CW partition",
        }.get(self.scheme_id, f"scheme {self.scheme_id}")
