from __future__ import annotations

"""SC-MIMO CB-aware staggered mapping utilities.

This module intentionally starts with a small rank-2 mapping primitive. It is
not yet wired into the full Sionna LDPC orchestrator. The purpose is to make
the SC-MIMO mapping semantics explicit and testable before adding a full MIMO
receiver and SIC loop.
"""

from dataclasses import dataclass
from typing import Dict, Iterable, List, Sequence
import numpy as np

from lls_platform.phy.numpy_qam import qam_modulate


@dataclass(frozen=True)
class SCMIMOPartAssignment:
    """Mapping of one CB part to one layer and a contiguous RE interval."""

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

    The current rank-2 plan splits every CB into two symbol-domain parts:
    part0 is mapped to layer 0, and part1 is mapped to layer 1 with a cyclic
    CB shift. This captures the smallest staggered structure needed for early
    SIC experiments.
    """

    rank: int
    n_re_per_layer: int
    qm: int
    cb_symbol_counts: List[int]
    assignments: List[SCMIMOPartAssignment]
    shift: int = 1
    termination: str = "cyclic"
    mapping_rule: str = "sc_mimo_rank2_cyclic_staggered_cb"

    @property
    def n_cbs(self) -> int:
        return int(len(self.cb_symbol_counts))

    def assignments_for_cb(self, cb_index: int) -> List[SCMIMOPartAssignment]:
        return [a for a in self.assignments if int(a.cb_index) == int(cb_index)]

    def assignments_for_layer(self, layer_index: int) -> List[SCMIMOPartAssignment]:
        return [a for a in self.assignments if int(a.layer_index) == int(layer_index)]

    def validate(self) -> None:
        if int(self.rank) != 2:
            raise ValueError("Initial SC-MIMO mapping utility supports rank=2 only.")
        if int(self.qm) <= 0:
            raise ValueError("qm must be positive.")
        if self.termination != "cyclic":
            raise ValueError("Only termination='cyclic' is implemented in the initial utility.")

        used = set()
        for a in self.assignments:
            if a.n_bits != a.n_symbols * int(self.qm):
                raise ValueError(
                    f"CB{a.cb_index} part{a.part_index}: n_bits={a.n_bits} does not match "
                    f"n_symbols={a.n_symbols} * qm={self.qm}."
                )
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


def build_rank2_cyclic_sc_mimo_plan(
    cb_symbol_counts: Sequence[int],
    qm: int,
    n_re_per_layer: int,
    shift: int = 1,
) -> SCMIMOGridMappingPlan:
    """Build a minimal rank-2 cyclic staggered CB mapping plan.

    Layer 0 carries each CB's part0 in natural order. Layer 1 carries each CB's
    part1 in cyclically shifted order. With shift=1, the layer-1 order is:

        [CB_{N-1}, CB_0, CB_1, ..., CB_{N-2}]

    This mirrors the "cyclic shift of a subset of layers" interpretation and
    gives a collision-free, no-rate-loss starting point for detector/SIC work.
    """

    qm = int(qm)
    n_re_per_layer = int(n_re_per_layer)
    shift = int(shift)
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
        cb_idx: _split_count(n_sym, 2) for cb_idx, n_sym in enumerate(counts)
    }
    layer0_need = sum(parts[0] for parts in parts_by_cb.values())
    layer1_need = sum(parts[1] for parts in parts_by_cb.values())
    if layer0_need > n_re_per_layer or layer1_need > n_re_per_layer:
        raise ValueError(
            "n_re_per_layer is too small for the requested SC-MIMO plan: "
            f"layer0_need={layer0_need}, layer1_need={layer1_need}, "
            f"n_re_per_layer={n_re_per_layer}."
        )

    assignments: List[SCMIMOPartAssignment] = []

    re_cursor = 0
    for cb_idx, n_sym in enumerate(counts):
        n_part = parts_by_cb[cb_idx][0]
        sym_start = 0
        sym_end = sym_start + n_part
        bit_start = sym_start * qm
        bit_end = sym_end * qm
        assignments.append(SCMIMOPartAssignment(
            cb_index=int(cb_idx),
            part_index=0,
            layer_index=0,
            re_indices=np.arange(re_cursor, re_cursor + n_part, dtype=np.int32),
            bit_indices=np.arange(bit_start, bit_end, dtype=np.int32),
            symbol_indices=np.arange(sym_start, sym_end, dtype=np.int32),
        ))
        re_cursor += n_part

    n_cbs = len(counts)
    shifted_order = [int((slot - shift) % n_cbs) for slot in range(n_cbs)]
    re_cursor = 0
    for cb_idx in shifted_order:
        n_part0 = parts_by_cb[cb_idx][0]
        n_part1 = parts_by_cb[cb_idx][1]
        sym_start = n_part0
        sym_end = n_part0 + n_part1
        bit_start = sym_start * qm
        bit_end = sym_end * qm
        assignments.append(SCMIMOPartAssignment(
            cb_index=int(cb_idx),
            part_index=1,
            layer_index=1,
            re_indices=np.arange(re_cursor, re_cursor + n_part1, dtype=np.int32),
            bit_indices=np.arange(bit_start, bit_end, dtype=np.int32),
            symbol_indices=np.arange(sym_start, sym_end, dtype=np.int32),
        ))
        re_cursor += n_part1

    plan = SCMIMOGridMappingPlan(
        rank=2,
        n_re_per_layer=n_re_per_layer,
        qm=qm,
        cb_symbol_counts=counts,
        assignments=assignments,
        shift=shift,
        termination="cyclic",
    )
    plan.validate()
    return plan


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


def map_rank2_cb_bits_to_layer_grid(
    cb_bits_by_index: Sequence[np.ndarray],
    plan: SCMIMOGridMappingPlan,
) -> np.ndarray:
    """Map CB coded bits to a rank-2 SC-MIMO layer grid.

    Returns:
        Complex array with shape [n_re_per_layer, 2].

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


def reconstruct_rank2_cb_layer_grid(
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
