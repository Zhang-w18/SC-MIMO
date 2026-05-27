from __future__ import annotations

"""SC-MIMO CB-aware staggered mapping utilities.

This module keeps the SC-MIMO mapping semantics explicit and testable before
adding the full rank-4 receiver and SIC loop.
"""

from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Tuple
import numpy as np

from lls_platform.phy.numpy_qam import qam_modulate


@dataclass(frozen=True)
class SCMIMOPartAssignment:
    """Mapping of one CB part slice to one layer and a contiguous RE interval."""

    cb_index: int
    part_index: int
    layer_index: int
    re_indices: np.ndarray
    bit_indices: np.ndarray
    symbol_indices: np.ndarray

    @property
    def n_symbols(self) -> int:
        return int(len(self.symbol_indices))

    @property
    def n_bits(self) -> int:
        return int(len(self.bit_indices))


@dataclass(frozen=True)
class SCMIMOGridMappingPlan:
    """CB-aware SC-MIMO mapping plan.

    For rank-2, every layer group contains one layer and this reduces to the
    original staggered CB mapping. For rank>2, each CB part is mapped into a
    layer group using NR-like RE-major, layer-minor order.
    """

    rank: int
    n_re_per_layer: int
    qm: int
    cb_symbol_counts: List[int]
    assignments: List[SCMIMOPartAssignment]
    layer_groups: Tuple[Tuple[int, ...], ...] = ()
    shift_pattern: Tuple[int, ...] = ()
    strict_rectangular_tiles: bool = True
    shift: int = 1
    termination: str = "cyclic"
    mapping_rule: str = "sc_mimo_layer_group_cyclic_staggered_cb"

    @property
    def n_cbs(self) -> int:
        return int(len(self.cb_symbol_counts))

    def assignments_for_cb(self, cb_index: int) -> List[SCMIMOPartAssignment]:
        return [a for a in self.assignments if int(a.cb_index) == int(cb_index)]

    def assignments_for_layer(self, layer_index: int) -> List[SCMIMOPartAssignment]:
        return [a for a in self.assignments if int(a.layer_index) == int(layer_index)]

    def assignments_for_cb_part(self, cb_index: int, part_index: int) -> List[SCMIMOPartAssignment]:
        return [
            a for a in self.assignments
            if int(a.cb_index) == int(cb_index) and int(a.part_index) == int(part_index)
        ]

    def validate(self) -> None:
        rank = int(self.rank)
        if rank <= 0:
            raise ValueError("rank must be positive.")
        if int(self.qm) <= 0:
            raise ValueError("qm must be positive.")
        if self.termination != "cyclic":
            raise ValueError("Only termination='cyclic' is implemented.")

        layer_groups = self.layer_groups or tuple((layer,) for layer in range(rank))
        flat_layers = [int(layer) for group in layer_groups for layer in group]
        if sorted(flat_layers) != list(range(rank)):
            raise ValueError("layer_groups must cover every rank layer exactly once.")
        if self.shift_pattern and len(self.shift_pattern) != len(layer_groups):
            raise ValueError("shift_pattern length must match number of layer groups.")

        used = set()
        for a in self.assignments:
            if a.n_bits != a.n_symbols * int(self.qm):
                raise ValueError(
                    f"CB{a.cb_index} part{a.part_index}: n_bits={a.n_bits} does not match "
                    f"n_symbols={a.n_symbols} * qm={self.qm}."
                )
            if int(a.layer_index) < 0 or int(a.layer_index) >= rank:
                raise ValueError(f"Layer index {a.layer_index} outside [0,{rank}).")
            for re in a.re_indices.tolist():
                if re < 0 or re >= int(self.n_re_per_layer):
                    raise ValueError(f"RE index {re} outside [0,{self.n_re_per_layer}).")
                key = (int(re), int(a.layer_index))
                if key in used:
                    raise ValueError(f"Resource collision at (RE, layer)={key}.")
                used.add(key)

        for cb_idx, n_sym in enumerate(self.cb_symbol_counts):
            bit_seen: List[int] = []
            sym_seen: List[int] = []
            for a in self.assignments_for_cb(cb_idx):
                bit_seen.extend(int(x) for x in a.bit_indices.tolist())
                sym_seen.extend(int(x) for x in a.symbol_indices.tolist())
            expected_bits = list(range(int(n_sym) * int(self.qm)))
            expected_syms = list(range(int(n_sym)))
            if sorted(bit_seen) != expected_bits:
                raise ValueError(f"CB{cb_idx} bit coverage is not one-to-one.")
            if sorted(sym_seen) != expected_syms:
                raise ValueError(f"CB{cb_idx} symbol coverage is not one-to-one.")


def _split_count(total: int, n_parts: int) -> List[int]:
    total = int(total)
    n_parts = int(n_parts)
    if total < 0 or n_parts <= 0:
        raise ValueError("total must be >=0 and n_parts must be >0.")
    base = total // n_parts
    rem = total % n_parts
    return [base + (1 if i < rem else 0) for i in range(n_parts)]


def _normalize_layer_groups(layer_groups: Sequence[Sequence[int]]) -> Tuple[Tuple[Tuple[int, ...], ...], int]:
    groups: List[Tuple[int, ...]] = []
    flat_layers: List[int] = []
    for group in layer_groups:
        normalized = tuple(int(layer) for layer in group)
        if not normalized:
            raise ValueError("layer_groups must not contain empty groups.")
        if any(layer < 0 for layer in normalized):
            raise ValueError("layer indices must be non-negative.")
        if len(set(normalized)) != len(normalized):
            raise ValueError("layer_groups must not repeat a layer within one group.")
        groups.append(normalized)
        flat_layers.extend(normalized)

    if not groups:
        raise ValueError("layer_groups must not be empty.")
    rank = max(flat_layers) + 1
    if sorted(flat_layers) != list(range(rank)):
        raise ValueError("layer_groups must cover layers [0, rank) exactly once.")
    return tuple(groups), int(rank)


def _symbol_indices_to_bit_indices(symbol_indices: np.ndarray, qm: int) -> np.ndarray:
    symbol_indices = np.asarray(symbol_indices, dtype=np.int32).reshape(-1)
    qm = int(qm)
    if symbol_indices.size == 0:
        return np.asarray([], dtype=np.int32)
    bit_chunks = [
        np.arange(int(sym) * qm, (int(sym) + 1) * qm, dtype=np.int32)
        for sym in symbol_indices.tolist()
    ]
    return np.concatenate(bit_chunks).astype(np.int32, copy=False)


def cb_symbol_counts_from_e(cb_e_values: Iterable[int], qm: int) -> List[int]:
    """Convert CB rate-matched lengths E into symbol counts.

    SC-MIMO SIC needs a CB part to reconstruct complete QAM symbols. For the
    initial implementation, every CB E must be divisible by qm.
    """

    qm = int(qm)
    out = []
    for cb_idx, e in enumerate(cb_e_values):
        e = int(e)
        if e % qm != 0:
            raise ValueError(
                f"CB{cb_idx} E={e} is not divisible by qm={qm}. "
                "SC-MIMO CB-aware cancellation requires symbol-aligned CB boundaries "
                "in the initial implementation."
            )
        out.append(e // qm)
    return out


def build_layer_group_sc_mimo_plan(
    cb_symbol_counts: Sequence[int],
    qm: int,
    n_re_per_layer: int,
    layer_groups: Sequence[Sequence[int]],
    shift_pattern: Optional[Sequence[int]] = None,
    termination: str = "cyclic",
    strict_rectangular_tiles: bool = True,
) -> SCMIMOGridMappingPlan:
    """Build a cyclic staggered SC-MIMO layer-group mapping plan.

    Each layer group gets one CB part. The group order is natural for
    ``shift=0`` and cyclically shifted for non-zero shifts. Within a group,
    symbols are mapped RE-major, layer-minor:

        group [L0,L1], tile row r -> (RE r, L0), then (RE r, L1)

    The Phase-2 rank4 default is layer_groups=[[0,1],[2,3]] and
    shift_pattern=[0,1].
    """

    qm = int(qm)
    n_re_per_layer = int(n_re_per_layer)
    termination = str(termination)
    if termination != "cyclic":
        raise ValueError("Only termination='cyclic' is implemented.")
    groups, rank = _normalize_layer_groups(layer_groups)
    n_groups = len(groups)
    if shift_pattern is None:
        shifts = [int(i) for i in range(n_groups)]
    else:
        shifts = [int(x) for x in shift_pattern]
    if len(shifts) != n_groups:
        raise ValueError("shift_pattern length must match number of layer groups.")

    counts = [int(x) for x in cb_symbol_counts]
    if not counts:
        raise ValueError("cb_symbol_counts must not be empty.")
    if any(x <= 0 for x in counts):
        raise ValueError("All CB symbol counts must be positive.")
    if qm <= 0:
        raise ValueError("qm must be positive.")
    if n_re_per_layer <= 0:
        raise ValueError("n_re_per_layer must be positive.")

    parts_by_cb: Dict[int, List[int]] = {
        cb_idx: _split_count(n_sym, n_groups) for cb_idx, n_sym in enumerate(counts)
    }

    assignments: List[SCMIMOPartAssignment] = []
    re_cursor_by_layer = {layer: 0 for group in groups for layer in group}
    n_cbs = len(counts)

    for group_idx, group in enumerate(groups):
        group_size = len(group)
        shifted_order = [int((slot - shifts[group_idx]) % n_cbs) for slot in range(n_cbs)]
        for cb_idx in shifted_order:
            n_part = int(parts_by_cb[cb_idx][group_idx])
            if strict_rectangular_tiles and n_part % group_size != 0:
                raise ValueError(
                    f"CB{cb_idx} part{group_idx} has {n_part} symbols, which cannot form "
                    f"rectangular tiles over layer group {list(group)}."
                )
            part_symbol_start = sum(parts_by_cb[cb_idx][:group_idx])
            for layer_offset, layer in enumerate(group):
                symbol_indices = part_symbol_start + np.arange(
                    layer_offset, n_part, group_size, dtype=np.int32
                )
                if symbol_indices.size == 0:
                    continue
                re_start = int(re_cursor_by_layer[layer])
                re_end = re_start + int(symbol_indices.size)
                if re_end > n_re_per_layer:
                    raise ValueError(
                        "n_re_per_layer is too small for the requested SC-MIMO plan: "
                        f"layer{layer}_need={re_end}, n_re_per_layer={n_re_per_layer}."
                    )
                assignments.append(SCMIMOPartAssignment(
                    cb_index=int(cb_idx),
                    part_index=int(group_idx),
                    layer_index=int(layer),
                    re_indices=np.arange(re_start, re_end, dtype=np.int32),
                    bit_indices=_symbol_indices_to_bit_indices(symbol_indices, qm),
                    symbol_indices=symbol_indices,
                ))
                re_cursor_by_layer[layer] = re_end

    plan = SCMIMOGridMappingPlan(
        rank=rank,
        n_re_per_layer=n_re_per_layer,
        qm=qm,
        cb_symbol_counts=counts,
        assignments=assignments,
        layer_groups=groups,
        shift_pattern=tuple(shifts),
        strict_rectangular_tiles=bool(strict_rectangular_tiles),
        shift=int(shifts[1]) if len(shifts) > 1 else int(shifts[0]),
        termination=termination,
        mapping_rule=f"sc_mimo_rank{rank}_layer_group_cyclic_staggered_cb",
    )
    plan.validate()
    return plan


def build_rank2_cyclic_sc_mimo_plan(
    cb_symbol_counts: Sequence[int],
    qm: int,
    n_re_per_layer: int,
    shift: int = 1,
) -> SCMIMOGridMappingPlan:
    """Build the original rank-2 cyclic staggered CB mapping plan."""

    plan = build_layer_group_sc_mimo_plan(
        cb_symbol_counts=cb_symbol_counts,
        qm=qm,
        n_re_per_layer=n_re_per_layer,
        layer_groups=((0,), (1,)),
        shift_pattern=(0, int(shift)),
        termination="cyclic",
        strict_rectangular_tiles=True,
    )
    return SCMIMOGridMappingPlan(
        rank=plan.rank,
        n_re_per_layer=plan.n_re_per_layer,
        qm=plan.qm,
        cb_symbol_counts=plan.cb_symbol_counts,
        assignments=plan.assignments,
        layer_groups=plan.layer_groups,
        shift_pattern=plan.shift_pattern,
        strict_rectangular_tiles=plan.strict_rectangular_tiles,
        shift=int(shift),
        termination=plan.termination,
        mapping_rule="sc_mimo_rank2_cyclic_staggered_cb",
    )


def _validate_cb_bits(cb_bits_by_index: Sequence[np.ndarray], plan: SCMIMOGridMappingPlan) -> List[np.ndarray]:
    if len(cb_bits_by_index) != plan.n_cbs:
        raise ValueError(f"Expected {plan.n_cbs} CB bit arrays, got {len(cb_bits_by_index)}.")
    out = []
    for cb_idx, bits in enumerate(cb_bits_by_index):
        arr = np.asarray(bits, dtype=np.int8).reshape(-1)
        expected = int(plan.cb_symbol_counts[cb_idx]) * int(plan.qm)
        if arr.size != expected:
            raise ValueError(f"CB{cb_idx} bit length {arr.size} does not match expected {expected}.")
        out.append(arr)
    return out


def map_cb_bits_to_layer_grid(
    cb_bits_by_index: Sequence[np.ndarray],
    plan: SCMIMOGridMappingPlan,
) -> np.ndarray:
    """Map CB coded bits to an SC-MIMO layer grid.

    Returns:
        Complex array with shape [n_re_per_layer, rank].

    This is a transmitter-side reference mapper. The same function is also
    useful at the receiver for ideal-SIC reconstruction when the true CB bits
    are supplied.
    """

    plan.validate()
    cb_bits = _validate_cb_bits(cb_bits_by_index, plan)
    grid = np.zeros((int(plan.n_re_per_layer), int(plan.rank)), dtype=np.complex128)
    used = np.zeros((int(plan.n_re_per_layer), int(plan.rank)), dtype=bool)

    for assignment in plan.assignments:
        bits = cb_bits[int(assignment.cb_index)][assignment.bit_indices]
        symbols = qam_modulate(bits, int(plan.qm))
        if symbols.size != assignment.n_symbols:
            raise RuntimeError("QAM symbol count does not match assignment symbol count.")
        layer = int(assignment.layer_index)
        for local_idx, re in enumerate(assignment.re_indices.tolist()):
            if used[int(re), layer]:
                raise RuntimeError(f"Resource collision at RE={re}, layer={layer}.")
            grid[int(re), layer] = symbols[local_idx]
            used[int(re), layer] = True
    return grid


def map_rank2_cb_bits_to_layer_grid(
    cb_bits_by_index: Sequence[np.ndarray],
    plan: SCMIMOGridMappingPlan,
) -> np.ndarray:
    """Backward-compatible rank-2 mapper wrapper."""

    return map_cb_bits_to_layer_grid(cb_bits_by_index, plan)


def reconstruct_cb_layer_grid(
    cb_bits: np.ndarray,
    cb_index: int,
    plan: SCMIMOGridMappingPlan,
) -> np.ndarray:
    """Reconstruct one CB's contribution on the SC-MIMO layer grid.

    The returned grid has zeros everywhere except the RE/layer positions used
    by the requested CB. This is the exact object needed before applying
    ``H_eff @ x_cb`` in a future SIC loop.
    """

    plan.validate()
    cb_index = int(cb_index)
    if cb_index < 0 or cb_index >= plan.n_cbs:
        raise ValueError(f"cb_index {cb_index} outside [0,{plan.n_cbs}).")
    bits = np.asarray(cb_bits, dtype=np.int8).reshape(-1)
    expected = int(plan.cb_symbol_counts[cb_index]) * int(plan.qm)
    if bits.size != expected:
        raise ValueError(f"CB{cb_index} bit length {bits.size} does not match expected {expected}.")

    grid = np.zeros((int(plan.n_re_per_layer), int(plan.rank)), dtype=np.complex128)
    for assignment in plan.assignments_for_cb(cb_index):
        part_bits = bits[assignment.bit_indices]
        symbols = qam_modulate(part_bits, int(plan.qm))
        layer = int(assignment.layer_index)
        for local_idx, re in enumerate(assignment.re_indices.tolist()):
            grid[int(re), layer] = symbols[local_idx]
    return grid


def reconstruct_rank2_cb_layer_grid(
    cb_bits: np.ndarray,
    cb_index: int,
    plan: SCMIMOGridMappingPlan,
) -> np.ndarray:
    """Backward-compatible rank-2 reconstruction wrapper."""

    return reconstruct_cb_layer_grid(cb_bits, cb_index, plan)
