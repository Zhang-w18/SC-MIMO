from __future__ import annotations

"""Generic CW -> time-frequency-layer resource grid mapping.

v2.7 changes the default CW-to-layer mapping inside a CW from contiguous
layer-wise blocks to NR-like symbol round-robin across the layers assigned to
that CW. For a CW mapped to layers [0,1], coded bits are grouped into QAM
symbol chunks and assigned as:

    RE0 L0, RE0 L1, RE1 L0, RE1 L1, ...

For mixed-Qm Scheme 2, the same RE/layer round-robin order is used, but each
layer consumes a variable-size bit chunk according to its own Qm.

The receive side uses the same GridMappingPlan bit_indices to scatter LLRs
back to the original CW coded-bit stream, so CB decoding sees the correct
coded-bit order.
"""

from dataclasses import dataclass
from typing import Dict, List, Mapping, Tuple, Optional
import numpy as np

from lls_platform.core.config import ResourceConfig
from lls_platform.core.data_structures import TransmissionConfig
from lls_platform.tx.tb_manager import compute_n_re_per_layer


@dataclass(frozen=True)
class REPattern:
    re_indices: np.ndarray
    n_time: int
    n_freq: int

    @property
    def n_re(self) -> int:
        return int(len(self.re_indices))


@dataclass(frozen=True)
class GridSegment:
    cw_index: int
    layer_index: int
    qm: int
    re_indices: np.ndarray
    bit_indices: np.ndarray
    bit_start: int
    bit_end: int

    @property
    def n_re(self) -> int:
        return int(len(self.re_indices))

    @property
    def n_bits(self) -> int:
        return int(len(self.bit_indices))

    @property
    def n_symbols(self) -> int:
        return self.n_re


@dataclass(frozen=True)
class CWGridRoute:
    cw_index: int
    segments: List[GridSegment]
    total_bits: int


@dataclass(frozen=True)
class GridMappingPlan:
    rank: int
    n_time: int
    n_freq: int
    n_re_per_layer: int
    cw_routes: Dict[int, CWGridRoute]
    mapping_rule: str = "nr_symbol_round_robin"

    def route_for_cw(self, cw_index: int) -> CWGridRoute:
        if cw_index not in self.cw_routes:
            raise KeyError(f"Mapping plan 中没有 CW{cw_index}")
        return self.cw_routes[cw_index]


def default_re_pattern(resource: ResourceConfig) -> REPattern:
    n_time = int(resource.pdsch_n_symbols)
    n_freq = int(resource.n_prbs) * 12
    n_re = compute_n_re_per_layer(resource)
    return REPattern(re_indices=np.arange(n_re, dtype=np.int32), n_time=n_time, n_freq=n_freq)


def _round_robin_bit_indices_for_layers(layer_qm: Mapping[int, int], n_re: int) -> Dict[int, np.ndarray]:
    """Return per-layer bit indices for NR-like round-robin layer mapping.

    The CW coded-bit stream is consumed in RE-major, layer-minor order. For
    equal-Qm layers this means symbols at the same RE across layers are adjacent
    in the original modulated symbol stream. For mixed-Qm layers, adjacent chunks
    have different bit lengths.
    """
    layers = list(layer_qm.keys())
    out = {int(l): [] for l in layers}
    cursor = 0
    for _re in range(int(n_re)):
        for layer in layers:
            qm = int(layer_qm[layer])
            out[int(layer)].extend(range(cursor, cursor + qm))
            cursor += qm
    return {k: np.asarray(v, dtype=np.int32) for k, v in out.items()}


def build_grid_mapping_plan(
    tx_cfg: TransmissionConfig,
    resource: ResourceConfig,
    cw_total_bits: Mapping[int, int],
    per_cw_re_patterns: Optional[Mapping[Tuple[int, int], REPattern]] = None,
) -> GridMappingPlan:
    """Build CW/layer/RE/bit-index routing plan.

    v2.7 default:
    - every CW uses all data REs on each assigned layer;
    - within a CW, coded bits are mapped to its layers in symbol round-robin
      order over layers for each RE;
    - receiver scatters LLRs back by exactly the same non-contiguous bit_indices.
    """
    base_pattern = default_re_pattern(resource)
    patterns = per_cw_re_patterns or {}
    routes: Dict[int, CWGridRoute] = {}

    for cw in tx_cfg.cw_configs:
        segments: List[GridSegment] = []
        layers = [int(l) for l in cw.layer_indices]
        if not layers:
            raise ValueError(f"CW{cw.cw_index} 没有分配任何 layer")

        # Current implementation requires the same RE pattern length for all
        # layers of a CW for strict RE-major round-robin. This is true for all
        # current schemes. Future frequency-interleaved schemes can relax this.
        pats = {layer: patterns.get((int(cw.cw_index), int(layer)), base_pattern) for layer in layers}
        n_re_set = {int(p.n_re) for p in pats.values()}
        if len(n_re_set) != 1:
            raise ValueError("v2.7 round-robin mapping requires same n_re for all layers of a CW")
        n_re = n_re_set.pop()
        layer_qm = {layer: int(cw.layer_modulations[layer]) for layer in layers}
        bit_indices_by_layer = _round_robin_bit_indices_for_layers(layer_qm, n_re)
        total_bits = sum(int(n_re) * int(layer_qm[layer]) for layer in layers)

        expected = int(cw_total_bits[int(cw.cw_index)])
        if total_bits != expected:
            raise ValueError(
                f"CW{cw.cw_index} mapping plan total_bits={total_bits} 与 TBManager total_E={expected} 不一致。"
            )

        for layer in layers:
            qm = int(layer_qm[layer])
            pat = pats[layer]
            idx = bit_indices_by_layer[layer]
            seg = GridSegment(
                cw_index=int(cw.cw_index),
                layer_index=int(layer),
                qm=qm,
                re_indices=np.asarray(pat.re_indices, dtype=np.int32),
                bit_indices=idx,
                bit_start=int(idx[0]) if len(idx) else 0,
                bit_end=int(idx[-1] + 1) if len(idx) else 0,
            )
            segments.append(seg)

        routes[int(cw.cw_index)] = CWGridRoute(cw_index=int(cw.cw_index), segments=segments, total_bits=total_bits)

    _check_no_resource_collision(routes, rank=int(tx_cfg.rank), n_re_per_layer=base_pattern.n_re)

    return GridMappingPlan(
        rank=int(tx_cfg.rank),
        n_time=base_pattern.n_time,
        n_freq=base_pattern.n_freq,
        n_re_per_layer=base_pattern.n_re,
        cw_routes=routes,
        mapping_rule=str(tx_cfg.mapping_rule),
    )


def _check_no_resource_collision(routes: Mapping[int, CWGridRoute], rank: int, n_re_per_layer: int) -> None:
    used = set()
    for route in routes.values():
        for seg in route.segments:
            if seg.layer_index < 0 or seg.layer_index >= rank:
                raise ValueError(f"layer_index={seg.layer_index} 超出 rank={rank}")
            if seg.n_bits != seg.n_re * seg.qm:
                raise ValueError(f"GridSegment bit 数不等于 RE*Qm: n_bits={seg.n_bits}, n_re={seg.n_re}, Qm={seg.qm}")
            for re in seg.re_indices.tolist():
                if re < 0 or re >= n_re_per_layer:
                    raise ValueError(f"RE index={re} 超出 n_re_per_layer={n_re_per_layer}")
                key = (int(re), int(seg.layer_index))
                if key in used:
                    raise ValueError(f"资源冲突：多个 CW/segment 占用同一 (RE,layer)={key}")
                used.add(key)


def plan_debug_dict(plan: GridMappingPlan) -> Dict[str, object]:
    out: Dict[str, object] = {
        "grid_rank": plan.rank,
        "grid_n_time": plan.n_time,
        "grid_n_freq": plan.n_freq,
        "grid_n_re_per_layer": plan.n_re_per_layer,
        "grid_mapping_version": "v2.7_nr_symbol_round_robin_bit_indices",
        "grid_mapping_rule": plan.mapping_rule,
    }
    for cw_idx, route in plan.cw_routes.items():
        seg_desc = []
        for seg in route.segments:
            # bit_start/end are only the min/max span; idx count gives true non-contiguous length.
            seg_desc.append(f"L{seg.layer_index}:Qm{seg.qm}:RE{seg.n_re}:bits-span[{seg.bit_start}:{seg.bit_end}]:idx{seg.n_bits}")
        out[f"cw{cw_idx}_grid_route"] = ";".join(seg_desc)
        out[f"cw{cw_idx}_grid_total_bits"] = route.total_bits
    return out


def map_cw_bits_to_grid_tf(tf, cw_bits_by_cw: Mapping[int, object], plan: GridMappingPlan, mapper_by_qm: Mapping[int, object]):
    if not cw_bits_by_cw:
        raise ValueError("cw_bits_by_cw 不能为空")
    first = next(iter(cw_bits_by_cw.values()))
    batch_size = tf.shape(first)[0]
    x_grid = tf.zeros([batch_size, plan.n_re_per_layer, plan.rank], dtype=tf.complex64)
    for route in plan.cw_routes.values():
        cw_bits = cw_bits_by_cw[route.cw_index]
        for seg in route.segments:
            bit_idx = tf.constant(seg.bit_indices, dtype=tf.int32)
            bits_seg = tf.gather(cw_bits, bit_idx, axis=1)
            mapper = mapper_by_qm[int(seg.qm)]
            symbols = mapper(bits_seg)
            symbols = tf.cast(symbols, tf.complex64)
            x_grid = _scatter_segment_symbols_tf(tf, x_grid, symbols, seg)
    return x_grid


def _scatter_segment_symbols_tf(tf, x_grid, symbols, seg: GridSegment):
    b = tf.shape(symbols)[0]
    n_re = int(seg.n_re)
    re_idx = tf.constant(seg.re_indices, dtype=tf.int32)
    layer_idx = tf.fill([b * n_re], tf.constant(int(seg.layer_index), dtype=tf.int32))
    batch_idx = tf.repeat(tf.range(b, dtype=tf.int32), n_re)
    re_tiled = tf.tile(re_idx, [b])
    indices = tf.stack([batch_idx, re_tiled, layer_idx], axis=1)
    updates = tf.reshape(symbols, [b * n_re])
    return tf.tensor_scatter_nd_update(x_grid, indices, updates)


def collect_cw_llrs_from_grid_tf(tf, y_eq_grid, no_grid, plan: GridMappingPlan, demapper_by_qm: Mapping[int, object]):
    out = {}
    for route in plan.cw_routes.values():
        b = tf.shape(y_eq_grid)[0]
        cw_llr = tf.zeros([b, int(route.total_bits)], dtype=tf.float32)
        for seg in route.segments:
            re_idx = tf.constant(seg.re_indices, dtype=tf.int32)
            y_layer = y_eq_grid[:, :, int(seg.layer_index)]
            y_seg = tf.gather(y_layer, re_idx, axis=1)
            if len(getattr(no_grid, "shape", [])) == 0:
                no_seg = no_grid
            else:
                no_layer = no_grid[:, :, int(seg.layer_index)]
                no_seg = tf.gather(no_layer, re_idx, axis=1)
            demapper = demapper_by_qm[int(seg.qm)]
            llr_seg = demapper(y_seg, no_seg)
            llr_seg = tf.reshape(llr_seg, [b, int(seg.n_bits)])
            cw_llr = _scatter_segment_llrs_tf(tf, cw_llr, llr_seg, seg)
        out[route.cw_index] = cw_llr
    return out


def _scatter_segment_llrs_tf(tf, cw_llr, llr_seg, seg: GridSegment):
    b = tf.shape(llr_seg)[0]
    n_bits = int(seg.n_bits)
    bit_idx = tf.constant(seg.bit_indices, dtype=tf.int32)
    batch_idx = tf.repeat(tf.range(b, dtype=tf.int32), n_bits)
    bit_tiled = tf.tile(bit_idx, [b])
    indices = tf.stack([batch_idx, bit_tiled], axis=1)
    updates = tf.reshape(llr_seg, [b * n_bits])
    return tf.tensor_scatter_nd_update(cw_llr, indices, updates)
