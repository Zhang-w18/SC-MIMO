from __future__ import annotations

"""Small SC-MIMO true-MIMO toy utilities for Phase 1 validation.

Phase 1a uses the NumPy-only pieces to validate mapping -> true MIMO channel
-> exhaustive detector -> ideal cancellation mechanics. Phase 1b adds a small
Sionna LDPC adapter around the same mapping/detection path.
"""

from dataclasses import dataclass
from itertools import product
from pathlib import Path
from typing import Dict, List, Sequence
import math
import numpy as np

from lls_platform.core.data_structures import CWSimulationStats, SNRSummary
from lls_platform.phy.mimo_detection import (
    exhaustive_qam_mimo_detect,
    kbest_qam_mimo_detect,
    mmse_qam_mimo_detect,
)
from lls_platform.phy.numpy_qam import qam_modulate
from lls_platform.phy.sc_mimo_mapping import (
    SCMIMOGridMappingPlan,
    build_layer_group_sc_mimo_plan,
    build_rank2_cyclic_sc_mimo_plan,
    cb_symbol_counts_from_e,
    map_cb_bits_to_layer_grid,
    map_rank2_cb_bits_to_layer_grid,
    reconstruct_cb_layer_grid,
    reconstruct_rank2_cb_layer_grid,
)
from lls_platform.sim.stats import save_csv, save_json


@dataclass(frozen=True)
class GridDetectionResult:
    hard_bits: np.ndarray
    llr: np.ndarray


@dataclass(frozen=True)
class LDPCDecodeResult:
    decoded_bits_by_cb: List[np.ndarray]
    cb_success: List[bool]
    tb_success: bool
    cb_bler: float
    tb_bler: float
    goodput_bits: int


@dataclass(frozen=True)
class Rank2LDPCTrialResult:
    mapping: str
    sic_mode: str
    decoded_bits_by_cb: List[np.ndarray]
    cb_success: List[bool]
    tb_success: bool
    cb_bler: float
    tb_bler: float
    goodput_bits: int
    cancellation_count: int
    residual_energy_after_cancellation: float


@dataclass(frozen=True)
class Rank2Phase1BComparisonResult:
    setup: object
    noise_var: float
    encoded_cb_lengths: List[int]
    nr_no_sic: Rank2LDPCTrialResult
    sc_no_sic: Rank2LDPCTrialResult
    sc_ideal_sic: Rank2LDPCTrialResult
    sc_decoded_sic: Rank2LDPCTrialResult


@dataclass(frozen=True)
class Rank2Phase1CSweepResult:
    output_dir: Path
    summaries: List[SNRSummary]


@dataclass(frozen=True)
class Rank4Phase2SmokeResult:
    noise_var: float
    encoded_cb_lengths: List[int]
    n_re_per_layer: int
    qm: int
    detector: str
    list_size: int
    nr_1cw: Rank2LDPCTrialResult
    sc_mimo_decoded_sic: Rank2LDPCTrialResult


@dataclass(frozen=True)
class Phase2IdealComparisonSweepResult:
    output_dir: Path
    summaries: List[SNRSummary]


@dataclass(frozen=True)
class CSIErrorComparisonSweepResult:
    output_dir: Path
    summaries: List[SNRSummary]


@dataclass(frozen=True)
class Rank2TBGeneratedTrialSetup:
    tx_config: object
    tb_info: object
    payload_bit_lengths: List[int]
    cb_e_values: List[int]
    qm: int
    n_re_per_layer: int


def map_nr_rank2_cb_bits_to_layer_grid(
    cb_bits_by_index: Sequence[np.ndarray],
    qm: int,
    n_re_per_layer: int,
) -> np.ndarray:
    """Map single-CW CB bits to a rank-2 NR-like layer grid.

    The CW coded-bit stream is formed by concatenating CB bit streams. QAM
    symbols are then placed in RE-major, layer-minor order:

        RE0 L0, RE0 L1, RE1 L0, RE1 L1, ...
    """

    return map_nr_cb_bits_to_layer_grid(
        cb_bits_by_index,
        qm=qm,
        n_re_per_layer=n_re_per_layer,
        rank=2,
    )


def map_nr_cb_bits_to_layer_grid(
    cb_bits_by_index: Sequence[np.ndarray],
    qm: int,
    n_re_per_layer: int,
    rank: int,
) -> np.ndarray:
    """Map single-CW CB bits to an NR-like rank-N layer grid."""

    qm = int(qm)
    rank = int(rank)
    n_re_per_layer = int(n_re_per_layer)
    if qm <= 0:
        raise ValueError("qm must be positive.")
    if rank <= 0:
        raise ValueError("rank must be positive.")
    if n_re_per_layer <= 0:
        raise ValueError("n_re_per_layer must be positive.")

    cb_bits = [np.asarray(bits, dtype=np.int8).reshape(-1) for bits in cb_bits_by_index]
    cw_bits = np.concatenate(cb_bits) if cb_bits else np.asarray([], dtype=np.int8)
    expected_bits = int(n_re_per_layer) * rank * qm
    if cw_bits.size != expected_bits:
        raise ValueError(f"NR rank{rank} mapping expected {expected_bits} bits, got {cw_bits.size}.")

    symbols = qam_modulate(cw_bits, qm)
    if symbols.size != int(n_re_per_layer) * rank:
        raise RuntimeError("QAM symbol count does not match rank-N grid size.")
    return symbols.reshape(int(n_re_per_layer), rank)


def map_nr_encoded_cbs_to_layer_grid(
    encoded_cb_bits_by_index: Sequence[np.ndarray],
    qm: int,
    n_re_per_layer: int,
    rank: int = 2,
) -> np.ndarray:
    """NR baseline branch: concatenate encoded CBs into one CW stream."""

    return map_nr_cb_bits_to_layer_grid(
        encoded_cb_bits_by_index,
        qm=qm,
        n_re_per_layer=n_re_per_layer,
        rank=rank,
    )


def map_sc_mimo_encoded_cbs_to_layer_grid(
    encoded_cb_bits_by_index: Sequence[np.ndarray],
    cb_e_values: Sequence[int],
    qm: int,
    n_re_per_layer: int,
    termination: str = "cyclic",
    layer_groups: Sequence[Sequence[int]] | None = None,
    shift_pattern: Sequence[int] | None = None,
    strict_rectangular_tiles: bool = True,
) -> tuple[np.ndarray, SCMIMOGridMappingPlan]:
    """SC-MIMO TX branch with explicit CB boundaries.

    Inputs are rate-matched coded bits per CB, not one flattened CW stream.
    This keeps the CB boundaries visible for SC-MIMO staggered mapping, receive
    LLR scatter, and later single-CB SIC reconstruction.
    """

    termination = str(termination).lower()
    if termination != "cyclic":
        raise ValueError("Phase 1b SC-MIMO TX branch currently supports termination='cyclic' only.")

    cb_bits = [np.asarray(bits, dtype=np.int8).reshape(-1) for bits in encoded_cb_bits_by_index]
    cb_e_values = [int(e) for e in cb_e_values]
    if len(cb_bits) != len(cb_e_values):
        raise ValueError("encoded_cb_bits_by_index and cb_e_values must have the same length.")
    for cb_idx, (bits, e) in enumerate(zip(cb_bits, cb_e_values)):
        if int(bits.size) != int(e):
            raise ValueError(f"CB{cb_idx} encoded length {bits.size} does not match E={e}.")

    cb_symbol_counts = cb_symbol_counts_from_e(cb_e_values, qm=qm)
    if layer_groups is None:
        plan = build_rank2_cyclic_sc_mimo_plan(
            cb_symbol_counts=cb_symbol_counts,
            qm=qm,
            n_re_per_layer=n_re_per_layer,
            shift=1 if shift_pattern is None else int(list(shift_pattern)[-1]),
        )
    else:
        plan = build_layer_group_sc_mimo_plan(
            cb_symbol_counts=cb_symbol_counts,
            qm=qm,
            n_re_per_layer=n_re_per_layer,
            layer_groups=layer_groups,
            shift_pattern=shift_pattern,
            termination=termination,
            strict_rectangular_tiles=strict_rectangular_tiles,
        )
    return map_cb_bits_to_layer_grid(cb_bits, plan), plan


def deterministic_rank2_channel(n_re: int, n_rx: int = 2) -> np.ndarray:
    """Build a deterministic full MIMO channel H_eff[RE, rx, rank]."""

    return deterministic_mimo_channel(n_re=n_re, rank=2, n_rx=n_rx)


def deterministic_mimo_channel(n_re: int, rank: int, n_rx: int | None = None) -> np.ndarray:
    """Build a deterministic full MIMO channel H_eff[RE, rx, rank]."""

    n_re = int(n_re)
    rank = int(rank)
    if n_rx is None:
        n_rx = rank
    n_rx = int(n_rx)
    if n_re <= 0:
        raise ValueError("n_re must be positive.")
    if rank <= 0:
        raise ValueError("rank must be positive.")
    if n_rx < rank:
        raise ValueError("deterministic toy channel requires n_rx >= rank.")

    h = np.zeros((n_re, n_rx, rank), dtype=np.complex128)
    row = np.arange(n_rx, dtype=float)[:, None]
    col = np.arange(rank, dtype=float)[None, :]
    base = (
        0.10 / (1.0 + np.abs(row - col))
        + 1j * 0.04 * ((row + 1.0) * (col + 1.0)) / max(float(n_rx * rank), 1.0)
    )
    for layer in range(rank):
        base[layer, layer] = (1.05 + 0.05 * layer) + 0.0j

    for re in range(n_re):
        for layer in range(rank):
            phase = np.exp(1j * (0.035 + 0.011 * layer) * re)
            h[re, :, layer] = base[:, layer] * phase
    return h


def apply_true_mimo_channel(
    x_layer: np.ndarray,
    h_eff: np.ndarray,
    noise_var: float = 0.0,
    rng: np.random.Generator | None = None,
) -> np.ndarray:
    """Apply y[RE] = H_eff[RE] x_layer[RE] + n."""

    x = np.asarray(x_layer, dtype=np.complex128)
    h = np.asarray(h_eff, dtype=np.complex128)
    if x.ndim != 2:
        raise ValueError("x_layer must have shape [n_re, rank].")
    if h.ndim != 3:
        raise ValueError("h_eff must have shape [n_re, n_rx, rank].")
    if h.shape[0] != x.shape[0] or h.shape[2] != x.shape[1]:
        raise ValueError("x_layer and h_eff shapes are inconsistent.")

    y = np.einsum("nrl,nl->nr", h, x)
    noise_var = float(noise_var)
    if noise_var > 0.0:
        if rng is None:
            rng = np.random.default_rng(0)
        sigma = math.sqrt(noise_var / 2.0)
        noise = sigma * (rng.normal(size=y.shape) + 1j * rng.normal(size=y.shape))
        y = y + noise
    return y


def exhaustive_detect_layer_grid(
    y: np.ndarray,
    h_eff: np.ndarray,
    qm: int,
    noise_var: float = 1e-9,
) -> GridDetectionResult:
    """Run exhaustive per-RE MIMO detection and return [RE, layer, bit] arrays."""

    return detect_layer_grid(
        y,
        h_eff,
        qm=qm,
        noise_var=noise_var,
        detector="exhaustive",
    )


def detect_layer_grid(
    y: np.ndarray,
    h_eff: np.ndarray,
    qm: int,
    noise_var: float = 1e-9,
    detector: str = "exhaustive",
    list_size: int = 16,
) -> GridDetectionResult:
    """Run per-RE MIMO detection and return [RE, layer, bit] arrays."""

    y_arr = np.asarray(y, dtype=np.complex128)
    h = np.asarray(h_eff, dtype=np.complex128)
    if y_arr.ndim != 2 or h.ndim != 3:
        raise ValueError("Expected y [n_re,n_rx] and h_eff [n_re,n_rx,rank].")
    if y_arr.shape[0] != h.shape[0] or y_arr.shape[1] != h.shape[1]:
        raise ValueError("y and h_eff receive dimensions do not match.")

    detector = str(detector).lower()
    if detector not in ("exhaustive", "kbest", "kbest_rml", "mmse"):
        raise ValueError("detector must be exhaustive, kbest, or mmse.")
    rank = int(h.shape[2])
    qm = int(qm)
    hard = np.zeros((h.shape[0], rank, qm), dtype=np.int8)
    llr = np.zeros((h.shape[0], rank, qm), dtype=np.float64)
    for re in range(h.shape[0]):
        if detector == "exhaustive":
            res = exhaustive_qam_mimo_detect(y_arr[re], h[re], qm=qm, noise_var=noise_var)
        elif detector in ("kbest", "kbest_rml"):
            res = kbest_qam_mimo_detect(
                y_arr[re],
                h[re],
                qm=qm,
                noise_var=noise_var,
                k=int(list_size),
            )
        else:
            res = mmse_qam_mimo_detect(y_arr[re], h[re], qm=qm, noise_var=noise_var)
        hard[re] = res.best_bits
        llr[re] = res.llr.reshape(rank, qm)
    return GridDetectionResult(hard_bits=hard, llr=llr)


def collect_nr_rank2_cb_bits_from_detection(
    hard_bits_grid: np.ndarray,
    cb_bit_lengths: Sequence[int],
) -> List[np.ndarray]:
    """Collect NR rank2 RE-major/layer-minor hard bits back into CB streams."""

    return collect_nr_cb_bits_from_detection(hard_bits_grid, cb_bit_lengths, rank=2)


def collect_nr_cb_bits_from_detection(
    hard_bits_grid: np.ndarray,
    cb_bit_lengths: Sequence[int],
    rank: int,
) -> List[np.ndarray]:
    """Collect NR rank-N RE-major/layer-minor hard bits back into CB streams."""

    hard = np.asarray(hard_bits_grid, dtype=np.int8)
    rank = int(rank)
    if hard.ndim != 3 or hard.shape[1] != rank:
        raise ValueError("hard_bits_grid must have shape [n_re,rank,qm].")
    cw_bits = hard.reshape(-1)
    out = []
    cursor = 0
    for n_bits in cb_bit_lengths:
        n_bits = int(n_bits)
        out.append(cw_bits[cursor:cursor + n_bits].copy())
        cursor += n_bits
    if cursor != cw_bits.size:
        raise ValueError(f"CB bit lengths sum to {cursor}, but detected stream has {cw_bits.size} bits.")
    return out


def collect_nr_rank2_cb_llrs_from_detection(
    llr_grid: np.ndarray,
    cb_bit_lengths: Sequence[int],
) -> List[np.ndarray]:
    """Collect NR rank2 RE-major/layer-minor LLRs back into CB streams."""

    return collect_nr_cb_llrs_from_detection(llr_grid, cb_bit_lengths, rank=2)


def collect_nr_cb_llrs_from_detection(
    llr_grid: np.ndarray,
    cb_bit_lengths: Sequence[int],
    rank: int,
) -> List[np.ndarray]:
    """Collect NR rank-N RE-major/layer-minor LLRs back into CB streams."""

    llr = np.asarray(llr_grid, dtype=np.float64)
    rank = int(rank)
    if llr.ndim != 3 or llr.shape[1] != rank:
        raise ValueError("llr_grid must have shape [n_re,rank,qm].")
    cw_llr = llr.reshape(-1)
    out = []
    cursor = 0
    for n_bits in cb_bit_lengths:
        n_bits = int(n_bits)
        out.append(cw_llr[cursor:cursor + n_bits].copy())
        cursor += n_bits
    if cursor != cw_llr.size:
        raise ValueError(f"CB bit lengths sum to {cursor}, but detected stream has {cw_llr.size} bits.")
    return out


def collect_scmimo_cb_bits_from_detection(
    hard_bits_grid: np.ndarray,
    plan: SCMIMOGridMappingPlan,
) -> List[np.ndarray]:
    """Collect SC-MIMO detected hard bits back to each CB bit stream."""

    plan.validate()
    hard = np.asarray(hard_bits_grid, dtype=np.int8)
    if hard.ndim != 3 or hard.shape[1] != int(plan.rank) or hard.shape[2] != int(plan.qm):
        raise ValueError("hard_bits_grid shape must be [n_re, rank, qm].")

    out = [
        np.zeros(int(n_sym) * int(plan.qm), dtype=np.int8)
        for n_sym in plan.cb_symbol_counts
    ]
    for assignment in plan.assignments:
        cb_idx = int(assignment.cb_index)
        layer = int(assignment.layer_index)
        for local_idx, re in enumerate(assignment.re_indices.tolist()):
            bit_start = int(assignment.bit_indices[local_idx * int(plan.qm)])
            bit_end = bit_start + int(plan.qm)
            out[cb_idx][bit_start:bit_end] = hard[int(re), layer]
    return out


def collect_scmimo_cb_llrs_from_detection(
    llr_grid: np.ndarray,
    plan: SCMIMOGridMappingPlan,
) -> List[np.ndarray]:
    """Collect SC-MIMO detector LLRs back to each CB coded-bit stream."""

    plan.validate()
    llr = np.asarray(llr_grid, dtype=np.float64)
    if llr.ndim != 3 or llr.shape[1] != int(plan.rank) or llr.shape[2] != int(plan.qm):
        raise ValueError("llr_grid shape must be [n_re, rank, qm].")

    out = [
        np.zeros(int(n_sym) * int(plan.qm), dtype=np.float64)
        for n_sym in plan.cb_symbol_counts
    ]
    for assignment in plan.assignments:
        cb_idx = int(assignment.cb_index)
        layer = int(assignment.layer_index)
        for local_idx, re in enumerate(assignment.re_indices.tolist()):
            bit_start = int(assignment.bit_indices[local_idx * int(plan.qm)])
            bit_end = bit_start + int(plan.qm)
            out[cb_idx][bit_start:bit_end] = llr[int(re), layer]
    return out


def residual_after_ideal_cb_cancellation(
    y: np.ndarray,
    h_eff: np.ndarray,
    x_cb_layer: np.ndarray,
) -> np.ndarray:
    """Subtract one CB's channel contribution from received samples."""

    return np.asarray(y, dtype=np.complex128) - apply_true_mimo_channel(x_cb_layer, h_eff, noise_var=0.0)


def residual_energy(y_res: np.ndarray) -> float:
    """Return sum |residual|^2 as a scalar."""

    arr = np.asarray(y_res, dtype=np.complex128)
    return float(np.vdot(arr.reshape(-1), arr.reshape(-1)).real)


def sample_cdl_ideal_svd_h_eff_rank4(
    n_re_per_layer: int,
    speed_kmh: float,
    batch_size: int = 1,
    seed: int = 0,
) -> tuple[np.ndarray, dict]:
    """Sample CDL-A 30ns 32Tx/4Rx and return rank4 ideal-SVD H_eff.

    The resource configuration follows Qualcomm Table 2's 20 MHz / 15 kHz
    allocation. The bit-level Phase-2 runner samples a configurable number of
    data REs from the full grid so the LDPC/K-best smoke remains runnable.
    """

    from lls_platform.core.config import AntennaConfig, ChannelConfig, ResourceConfig
    from lls_platform.phy.cdl_channel import CDLChannelBuilder

    try:
        import tensorflow as tf
        tf.random.set_seed(int(seed))
    except Exception:
        pass

    resource = ResourceConfig(
        carrier_frequency_ghz=4.0,
        bandwidth_mhz=20.0,
        scs_khz=15,
        n_prbs=106,
        pdsch_start_symbol=1,
        pdsch_n_symbols=13,
        reserve_dmrs_re=False,
        prb_bundling_size=4,
    )
    channel = ChannelConfig(
        model="cdl",
        cdl_type="A",
        delay_spread_ns=30.0,
        ue_speed_kmh=float(speed_kmh),
        normalize=True,
        direction="downlink",
        num_time_samples=int(resource.pdsch_n_symbols),
    )
    antenna = AntennaConfig(
        bs_n_antennas=32,
        ue_n_antennas=4,
        bs_antenna_array=[4, 4, 2],
        ue_antenna_array=[1, 2, 2],
        polarization="cross",
    )
    builder = CDLChannelBuilder(antenna, resource, channel)
    batch = builder.sample(batch_size=int(batch_size))
    h_np = np.asarray(batch.h_tf.numpy())  # [B,T,F,Rx,Tx], keep backend dtype until RE sampling.
    h_flat = h_np.reshape(-1, h_np.shape[-2], h_np.shape[-1])
    if h_flat.shape[0] < int(n_re_per_layer):
        raise ValueError("CDL sample does not contain enough REs.")
    idx = np.linspace(0, h_flat.shape[0] - 1, int(n_re_per_layer), dtype=np.int64)
    h_sel = np.asarray(h_flat[idx], dtype=np.complex128)
    u, svals, _vh = np.linalg.svd(h_sel, full_matrices=False)
    rank = 4
    h_eff = u[:, :, :rank] * svals[:, np.newaxis, :rank]
    h_eff = h_eff / np.sqrt(float(rank))
    report = dict(batch.report)
    report.update({
        "phase2_channel": "CDL-A 30ns sampled REs, ideal per-RE SVD H_eff=H V_svd/sqrt(rank)",
        "bandwidth_mhz": 20.0,
        "scs_khz": 15,
        "n_prbs": 106,
        "n_re_full_grid": int(h_flat.shape[0]),
        "n_re_sampled": int(n_re_per_layer),
        "rank": rank,
        "precoder": "ideal_per_re_svd",
        "receiver_csi": "ideal_H_eff",
    })
    return h_eff.astype(np.complex128), report


def _nmse_label(nmse_db: float | None) -> str:
    if nmse_db is None:
        return "0"
    return f"{float(nmse_db):g}dB"


def _add_nmse_complex_gaussian_error(
    h: np.ndarray,
    nmse_db: float | None,
    rng: np.random.Generator,
) -> np.ndarray:
    """Return h plus complex Gaussian estimation error with global NMSE control."""

    h_arr = np.asarray(h, dtype=np.complex128)
    if nmse_db is None:
        return h_arr.copy()
    nmse_linear = float(10.0 ** (float(nmse_db) / 10.0))
    if nmse_linear <= 0.0:
        return h_arr.copy()
    ref_power = float(np.mean(np.abs(h_arr) ** 2))
    sigma = math.sqrt(max(ref_power * nmse_linear, 0.0) / 2.0)
    err = sigma * (rng.normal(size=h_arr.shape) + 1j * rng.normal(size=h_arr.shape))
    return h_arr + err


def sample_cdl_svd_effective_channels_with_csi_error(
    n_re_per_layer: int,
    rank: int,
    speed_kmh: float,
    tx_nmse_db: float | None = None,
    rx_nmse_db: float | None = None,
    seed: int = 0,
) -> tuple[np.ndarray, np.ndarray, dict]:
    """Sample CDL-A and build true/RX-estimated effective channels.

    Memory policy: sample one full 20 MHz CDL grid, immediately select the REs
    used by the bit-level experiment, and only then create TX/RX NMSE error
    copies. The large full-grid tensor is not retained across trials.
    """

    from lls_platform.core.config import AntennaConfig, ChannelConfig, ResourceConfig
    from lls_platform.phy.cdl_channel import CDLChannelBuilder

    rank = int(rank)
    if rank <= 0:
        raise ValueError("rank must be positive.")
    if rank > 4:
        raise ValueError("Current sampled CDL helper supports rank <= 4 for the 4-RX UE.")

    try:
        import tensorflow as tf
        tf.random.set_seed(int(seed))
    except Exception:
        pass

    resource = ResourceConfig(
        carrier_frequency_ghz=4.0,
        bandwidth_mhz=20.0,
        scs_khz=15,
        n_prbs=106,
        pdsch_start_symbol=1,
        pdsch_n_symbols=13,
        reserve_dmrs_re=False,
        prb_bundling_size=4,
    )
    channel = ChannelConfig(
        model="cdl",
        cdl_type="A",
        delay_spread_ns=30.0,
        ue_speed_kmh=float(speed_kmh),
        normalize=True,
        direction="downlink",
        num_time_samples=int(resource.pdsch_n_symbols),
    )
    antenna = AntennaConfig(
        bs_n_antennas=32,
        ue_n_antennas=4,
        bs_antenna_array=[4, 4, 2],
        ue_antenna_array=[1, 2, 2],
        polarization="cross",
    )
    builder = CDLChannelBuilder(antenna, resource, channel)
    batch = builder.sample(batch_size=1)
    h_np = np.asarray(batch.h_tf.numpy())  # [1,T,F,Rx,Tx]
    h_flat = h_np.reshape(-1, h_np.shape[-2], h_np.shape[-1])
    if h_flat.shape[0] < int(n_re_per_layer):
        raise ValueError("CDL sample does not contain enough REs.")

    idx = np.linspace(0, h_flat.shape[0] - 1, int(n_re_per_layer), dtype=np.int64)
    full_grid_bytes = int(h_np.nbytes)
    h_true = np.asarray(h_flat[idx], dtype=np.complex128)
    rng_err = np.random.default_rng(int(seed) + 31337)
    h_tx_hat = _add_nmse_complex_gaussian_error(h_true, tx_nmse_db, rng_err)
    h_rx_hat = _add_nmse_complex_gaussian_error(h_true, rx_nmse_db, rng_err)

    _u_tx, _s_tx, vh_tx = np.linalg.svd(h_tx_hat, full_matrices=False)
    v_tx = np.swapaxes(np.conj(vh_tx[:, :rank, :]), -1, -2)
    scale = math.sqrt(float(rank))
    h_eff_true = np.einsum("nrt,ntl->nrl", h_true, v_tx) / scale
    h_eff_rx = np.einsum("nrt,ntl->nrl", h_rx_hat, v_tx) / scale

    report = dict(batch.report)
    report.update({
        "phase": "csi_error_sampled_cdl",
        "channel": "CDL-A 30ns",
        "bandwidth_mhz": 20.0,
        "scs_khz": 15,
        "n_prbs": 106,
        "n_re_full_grid": int(h_flat.shape[0]),
        "n_re_sampled": int(n_re_per_layer),
        "rank": rank,
        "precoder": "per_re_svd_on_H_tx_hat",
        "tx_nmse_db": None if tx_nmse_db is None else float(tx_nmse_db),
        "rx_nmse_db": None if rx_nmse_db is None else float(rx_nmse_db),
        "full_grid_bytes_per_trial": full_grid_bytes,
        "retained_channel_bytes_per_trial": int(h_true.nbytes + h_tx_hat.nbytes + h_rx_hat.nbytes),
        "memory_policy": "stream one CDL grid, sample REs, add NMSE on sampled H only",
    })
    return h_eff_true.astype(np.complex128), h_eff_rx.astype(np.complex128), report


def _qam_symbol_candidates(qm: int) -> tuple[np.ndarray, np.ndarray]:
    bits = np.asarray(list(product([0, 1], repeat=int(qm))), dtype=np.int8)
    symbols = qam_modulate(bits.reshape(-1), int(qm))
    return bits, symbols


def exhaustive_detect_layer_grid_with_known_symbols(
    y: np.ndarray,
    h_eff: np.ndarray,
    qm: int,
    known_x_grid: np.ndarray,
    known_mask: np.ndarray,
    noise_var: float = 1e-9,
    detector: str = "exhaustive",
    list_size: int = 16,
) -> GridDetectionResult:
    """Run detection while treating selected layer symbols as known.

    This is the small Phase 1b detector needed after SIC cancellation: decoded
    CB contributions are subtracted before enumerating the remaining unknown
    layer symbols at each RE.
    """

    y_arr = np.asarray(y, dtype=np.complex128)
    h = np.asarray(h_eff, dtype=np.complex128)
    known_x = np.asarray(known_x_grid, dtype=np.complex128)
    known = np.asarray(known_mask, dtype=bool)
    if y_arr.ndim != 2 or h.ndim != 3:
        raise ValueError("Expected y [n_re,n_rx] and h_eff [n_re,n_rx,rank].")
    if known_x.shape != (h.shape[0], h.shape[2]) or known.shape != known_x.shape:
        raise ValueError("known_x_grid and known_mask must have shape [n_re,rank].")
    if y_arr.shape[0] != h.shape[0] or y_arr.shape[1] != h.shape[1]:
        raise ValueError("y and h_eff receive dimensions do not match.")

    rank = int(h.shape[2])
    qm = int(qm)
    noise_var = max(float(noise_var), 1e-12)
    detector = str(detector).lower()
    if detector not in ("exhaustive", "kbest", "kbest_rml", "mmse", "hybrid_rml_mmse_after_sic"):
        raise ValueError("detector must be exhaustive, kbest, mmse, or hybrid_rml_mmse_after_sic.")
    constellation_bits, constellation_symbols = _qam_symbol_candidates(qm)
    hard = np.zeros((h.shape[0], rank, qm), dtype=np.int8)
    llr = np.zeros((h.shape[0], rank, qm), dtype=np.float64)

    for re in range(h.shape[0]):
        unknown_layers = np.asarray([layer for layer in range(rank) if not known[re, layer]], dtype=np.int32)

        # The SIC loop passes a residual signal where known CB contributions
        # have already been subtracted. The known mask only removes those
        # layers from the hypothesis search for this RE.
        y_eff = y_arr[re]

        if unknown_layers.size == 0:
            continue

        h_unknown = h[re][:, unknown_layers]
        local_detector = detector
        if detector == "hybrid_rml_mmse_after_sic":
            local_detector = "mmse" if np.any(known[re]) else "kbest"

        if local_detector == "exhaustive":
            candidate_count = int(2 ** (qm * int(unknown_layers.size)))
            metrics = np.zeros(candidate_count, dtype=np.float64)
            candidate_bits = np.zeros((candidate_count, int(unknown_layers.size), qm), dtype=np.int8)
            cursor = 0
            for combo in product(range(len(constellation_symbols)), repeat=int(unknown_layers.size)):
                x_unknown = constellation_symbols[list(combo)]
                err = y_eff - h_unknown.dot(x_unknown)
                metrics[cursor] = float(np.vdot(err, err).real / noise_var)
                candidate_bits[cursor] = constellation_bits[list(combo)]
                cursor += 1

            best_idx = int(np.argmin(metrics))
            for local_layer_idx, layer in enumerate(unknown_layers.tolist()):
                hard[re, layer] = candidate_bits[best_idx, local_layer_idx]
                for bit_idx in range(qm):
                    bit_vals = candidate_bits[:, local_layer_idx, bit_idx]
                    m0 = np.min(metrics[bit_vals == 0])
                    m1 = np.min(metrics[bit_vals == 1])
                    llr[re, layer, bit_idx] = m0 - m1
        elif local_detector in ("kbest", "kbest_rml"):
            res = kbest_qam_mimo_detect(
                y_eff,
                h_unknown,
                qm=qm,
                noise_var=noise_var,
                k=int(list_size),
            )
            for local_layer_idx, layer in enumerate(unknown_layers.tolist()):
                hard[re, layer] = res.best_bits[local_layer_idx]
                llr[re, layer] = res.llr.reshape(int(unknown_layers.size), qm)[local_layer_idx]
        else:
            res = mmse_qam_mimo_detect(
                y_eff,
                h_unknown,
                qm=qm,
                noise_var=noise_var,
            )
            for local_layer_idx, layer in enumerate(unknown_layers.tolist()):
                hard[re, layer] = res.best_bits[local_layer_idx]
                llr[re, layer] = res.llr.reshape(int(unknown_layers.size), qm)[local_layer_idx]

    return GridDetectionResult(hard_bits=hard, llr=llr)


class SionnaLDPCAdapter:
    """Small one-batch Sionna LDPC adapter for Phase 1b toy validation."""

    def __init__(self, cb_k_values: Sequence[int], cb_e_values: Sequence[int], num_iter: int = 20, llr_clip: float = 50.0):
        if len(cb_k_values) != len(cb_e_values):
            raise ValueError("cb_k_values and cb_e_values must have the same length.")
        self.cb_k_values = [int(x) for x in cb_k_values]
        self.cb_e_values = [int(x) for x in cb_e_values]
        self.num_iter = int(num_iter)
        self.llr_clip = float(llr_clip)
        self.tf, self.LDPC5GEncoder, self.LDPC5GDecoder = self._import_ldpc()
        self.encoder_by_cb: Dict[int, object] = {}
        self.decoder_by_cb: Dict[int, object] = {}
        for cb_idx, (k, n) in enumerate(zip(self.cb_k_values, self.cb_e_values)):
            enc = self.LDPC5GEncoder(k=int(k), n=int(n))
            dec = self._make_decoder(enc)
            self.encoder_by_cb[int(cb_idx)] = enc
            self.decoder_by_cb[int(cb_idx)] = dec

    @staticmethod
    def _import_ldpc():
        import tensorflow as tf
        try:
            from sionna.phy.fec.ldpc import LDPC5GEncoder, LDPC5GDecoder
        except Exception:
            from sionna.fec.ldpc.encoding import LDPC5GEncoder
            from sionna.fec.ldpc.decoding import LDPC5GDecoder
        return tf, LDPC5GEncoder, LDPC5GDecoder

    def _make_decoder(self, encoder):
        try:
            return self.LDPC5GDecoder(encoder, hard_out=True, num_iter=self.num_iter)
        except TypeError:
            try:
                return self.LDPC5GDecoder(encoder, hard_out=True, num_iter=self.num_iter, return_infobits=True)
            except TypeError:
                return self.LDPC5GDecoder(encoder, hard_out=True)

    def encode_one(self, cb_index: int, payload_bits: np.ndarray) -> np.ndarray:
        cb_index = int(cb_index)
        bits = np.asarray(payload_bits, dtype=np.float32).reshape(1, -1)
        expected = int(self.cb_k_values[cb_index])
        if bits.shape[1] != expected:
            raise ValueError(f"CB{cb_index} payload length {bits.shape[1]} does not match k={expected}.")
        c = self.encoder_by_cb[cb_index](self.tf.constant(bits, dtype=self.tf.float32))
        return np.rint(c.numpy()[0]).astype(np.int8)

    def encode(self, payload_bits_by_index: Sequence[np.ndarray]) -> List[np.ndarray]:
        if len(payload_bits_by_index) != len(self.cb_k_values):
            raise ValueError("payload_bits_by_index length does not match adapter CB count.")
        return [self.encode_one(cb_idx, bits) for cb_idx, bits in enumerate(payload_bits_by_index)]

    def decode_one(self, cb_index: int, llr: np.ndarray) -> np.ndarray:
        cb_index = int(cb_index)
        arr = np.asarray(llr, dtype=np.float32).reshape(1, -1)
        expected = int(self.cb_e_values[cb_index])
        if arr.shape[1] != expected:
            raise ValueError(f"CB{cb_index} LLR length {arr.shape[1]} does not match E={expected}.")
        if self.llr_clip > 0:
            arr = np.clip(arr, -self.llr_clip, self.llr_clip)
        b_hat = self.decoder_by_cb[cb_index](self.tf.constant(arr, dtype=self.tf.float32))
        return np.rint(b_hat.numpy()[0]).astype(np.int8)

    def decode(self, llrs_by_index: Sequence[np.ndarray], reference_payload_bits_by_index: Sequence[np.ndarray]) -> LDPCDecodeResult:
        if len(llrs_by_index) != len(self.cb_k_values):
            raise ValueError("llrs_by_index length does not match adapter CB count.")
        decoded = [self.decode_one(cb_idx, llr) for cb_idx, llr in enumerate(llrs_by_index)]
        return summarize_cb_decode(decoded, reference_payload_bits_by_index)


def summarize_cb_decode(
    decoded_bits_by_cb: Sequence[np.ndarray],
    reference_payload_bits_by_index: Sequence[np.ndarray],
) -> LDPCDecodeResult:
    decoded = [np.asarray(bits, dtype=np.int8).reshape(-1) for bits in decoded_bits_by_cb]
    refs = [np.asarray(bits, dtype=np.int8).reshape(-1) for bits in reference_payload_bits_by_index]
    if len(decoded) != len(refs):
        raise ValueError("decoded and reference CB counts differ.")
    cb_success = []
    goodput_bits = 0
    for got, ref in zip(decoded, refs):
        if got.shape != ref.shape:
            raise ValueError("decoded and reference CB shapes differ.")
        ok = bool(np.array_equal(got, ref))
        cb_success.append(ok)
        if ok:
            goodput_bits += int(ref.size)
    tb_success = bool(all(cb_success))
    cb_bler = float(1.0 - (sum(1 for ok in cb_success if ok) / len(cb_success)))
    tb_bler = 0.0 if tb_success else 1.0
    return LDPCDecodeResult(
        decoded_bits_by_cb=decoded,
        cb_success=cb_success,
        tb_success=tb_success,
        cb_bler=cb_bler,
        tb_bler=tb_bler,
        goodput_bits=goodput_bits,
    )


def payload_lengths_from_tb_info(tb_info: object) -> List[int]:
    """Return per-CB payload lengths using the existing orchestrator convention."""

    n_cbs = max(int(getattr(tb_info, "n_cbs")), 1)
    tb_size = int(getattr(tb_info, "tb_size"))
    base = tb_size // n_cbs
    rem = tb_size % n_cbs
    return [base + (1 if i < rem else 0) for i in range(n_cbs)]


def build_rank2_fixed_mcs_tb_setup(
    resource: object | None = None,
    fixed_mcs: int = 3,
    mcs_table_name: str = "nr_64qam",
    scheme_id: int = 1,
) -> Rank2TBGeneratedTrialSetup:
    """Build Phase 1b CB sizes from the existing scheme and TBManager logic."""

    import numpy as _np
    from lls_platform.core.config import ResourceConfig, SimulationConfig
    from lls_platform.schemes.flexible_cw import FlexibleCWScheme
    from lls_platform.tx.tb_manager import TBManager
    from lls_platform.utils.mcs_tables import get_mcs_table

    if resource is None:
        resource = ResourceConfig(n_prbs=6, pdsch_n_symbols=4, reserve_dmrs_re=False)

    sim_cfg = SimulationConfig(fixed_mcs=int(fixed_mcs), mcs_table=str(mcs_table_name))
    scheme = FlexibleCWScheme(scheme_id=int(scheme_id), rank=2)
    tx_cfg = scheme.configure_transmission(
        layer_sinrs_db=_np.asarray([30.0, 30.0], dtype=float),
        mcs_table=get_mcs_table(str(mcs_table_name)),
        sim_cfg=sim_cfg,
    )
    tb_infos = TBManager(resource).compute_for_transmission(tx_cfg)
    if len(tb_infos) != 1:
        raise ValueError("Phase 1b rank2 SC-MIMO toy expects a single-CW setup.")
    tb_info = tb_infos[0]
    cw = tx_cfg.cw_configs[0]
    qm = int(cw.representative_qm)
    cb_e_values = [int(cb.E) for cb in tb_info.cb_infos]
    payload_bit_lengths = payload_lengths_from_tb_info(tb_info)

    expected_total_e = int(tb_info.n_re_per_layer) * 2 * qm
    if sum(cb_e_values) != expected_total_e:
        raise ValueError(
            f"TBManager total_E={sum(cb_e_values)} does not match rank2 grid capacity {expected_total_e}."
        )
    if any(int(e) % qm != 0 for e in cb_e_values):
        raise ValueError(f"Each CB E must be divisible by Qm={qm} for current SC-MIMO symbol mapping.")

    return Rank2TBGeneratedTrialSetup(
        tx_config=tx_cfg,
        tb_info=tb_info,
        payload_bit_lengths=payload_bit_lengths,
        cb_e_values=cb_e_values,
        qm=qm,
        n_re_per_layer=int(tb_info.n_re_per_layer),
    )


def _run_nr_branch_from_encoded_cbs(
    payload_bits_by_index: Sequence[np.ndarray],
    encoded_cb_bits_by_index: Sequence[np.ndarray],
    adapter: SionnaLDPCAdapter,
    qm: int,
    n_re_per_layer: int,
    h_eff: np.ndarray,
    noise_var: float,
    rng: np.random.Generator | None,
    rank: int = 2,
    detector: str = "exhaustive",
    list_size: int = 16,
    h_det_eff: np.ndarray | None = None,
) -> Rank2LDPCTrialResult:
    payload = [np.asarray(bits, dtype=np.int8).reshape(-1) for bits in payload_bits_by_index]
    coded = [np.asarray(bits, dtype=np.int8).reshape(-1) for bits in encoded_cb_bits_by_index]
    cb_e_values = [int(bits.size) for bits in coded]
    rank = int(rank)
    h_detector = h_eff if h_det_eff is None else np.asarray(h_det_eff, dtype=np.complex128)
    x_grid = map_nr_encoded_cbs_to_layer_grid(coded, qm=qm, n_re_per_layer=n_re_per_layer, rank=rank)
    y = apply_true_mimo_channel(x_grid, h_eff, noise_var=noise_var, rng=rng)
    det = detect_layer_grid(
        y,
        h_detector,
        qm=qm,
        noise_var=max(float(noise_var), 1e-9),
        detector=detector,
        list_size=list_size,
    )
    llrs = collect_nr_cb_llrs_from_detection(det.llr, cb_e_values, rank=rank)
    dec = adapter.decode(llrs, payload)
    return Rank2LDPCTrialResult(
        mapping="nr",
        sic_mode="no_sic",
        decoded_bits_by_cb=dec.decoded_bits_by_cb,
        cb_success=dec.cb_success,
        tb_success=dec.tb_success,
        cb_bler=dec.cb_bler,
        tb_bler=dec.tb_bler,
        goodput_bits=dec.goodput_bits,
        cancellation_count=0,
        residual_energy_after_cancellation=residual_energy(y),
    )


def _run_sc_mimo_branch_from_encoded_cbs(
    payload_bits_by_index: Sequence[np.ndarray],
    encoded_cb_bits_by_index: Sequence[np.ndarray],
    adapter: SionnaLDPCAdapter,
    qm: int,
    n_re_per_layer: int,
    h_eff: np.ndarray,
    noise_var: float,
    rng: np.random.Generator | None,
    sic_mode: str,
    layer_groups: Sequence[Sequence[int]] | None = None,
    shift_pattern: Sequence[int] | None = None,
    detector: str = "exhaustive",
    list_size: int = 16,
    h_det_eff: np.ndarray | None = None,
) -> Rank2LDPCTrialResult:
    sic_mode = str(sic_mode).lower()
    if sic_mode not in ("no_sic", "ideal_sic", "decoded_sic"):
        raise ValueError("sic_mode must be no_sic, ideal_sic, or decoded_sic.")

    payload = [np.asarray(bits, dtype=np.int8).reshape(-1) for bits in payload_bits_by_index]
    coded = [np.asarray(bits, dtype=np.int8).reshape(-1) for bits in encoded_cb_bits_by_index]
    cb_e_values = [int(bits.size) for bits in coded]
    h_detector = h_eff if h_det_eff is None else np.asarray(h_det_eff, dtype=np.complex128)
    x_grid, plan = map_sc_mimo_encoded_cbs_to_layer_grid(
        coded,
        cb_e_values=cb_e_values,
        qm=qm,
        n_re_per_layer=n_re_per_layer,
        layer_groups=layer_groups,
        shift_pattern=shift_pattern,
    )
    y = apply_true_mimo_channel(x_grid, h_eff, noise_var=noise_var, rng=rng)

    if sic_mode == "no_sic":
        det = detect_layer_grid(
            y,
            h_detector,
            qm=qm,
            noise_var=max(float(noise_var), 1e-9),
            detector=detector,
            list_size=list_size,
        )
        llrs = collect_scmimo_cb_llrs_from_detection(det.llr, plan)
        dec = adapter.decode(llrs, payload)
        return Rank2LDPCTrialResult(
            mapping="sc_mimo",
            sic_mode=sic_mode,
            decoded_bits_by_cb=dec.decoded_bits_by_cb,
            cb_success=dec.cb_success,
            tb_success=dec.tb_success,
            cb_bler=dec.cb_bler,
            tb_bler=dec.tb_bler,
            goodput_bits=dec.goodput_bits,
            cancellation_count=0,
            residual_energy_after_cancellation=residual_energy(y),
        )

    y_res = y.copy()
    known_x = np.zeros_like(x_grid)
    known_mask = np.zeros(x_grid.shape, dtype=bool)
    decoded_by_cb: List[np.ndarray | None] = [None] * len(payload)
    cancellation_count = 0

    for cb_idx in range(len(payload)):
        det = exhaustive_detect_layer_grid_with_known_symbols(
            y_res,
            h_detector,
            qm=qm,
            known_x_grid=known_x,
            known_mask=known_mask,
            noise_var=max(float(noise_var), 1e-9),
            detector=detector,
            list_size=list_size,
        )
        llrs = collect_scmimo_cb_llrs_from_detection(det.llr, plan)
        b_hat = adapter.decode_one(cb_idx, llrs[cb_idx])
        decoded_by_cb[cb_idx] = b_hat
        decode_success = bool(np.array_equal(b_hat, payload[cb_idx]))

        if sic_mode == "ideal_sic":
            cancel_bits = coded[cb_idx]
            should_cancel = True
        else:
            cancel_bits = adapter.encode_one(cb_idx, b_hat)
            should_cancel = decode_success

        if should_cancel:
            cb_grid = reconstruct_cb_layer_grid(cancel_bits, cb_idx, plan)
            y_res = residual_after_ideal_cb_cancellation(y_res, h_detector, cb_grid)
            known_x = known_x + cb_grid
            known_mask = known_mask | (np.abs(cb_grid) > 0)
            cancellation_count += 1

    decoded_final = [bits if bits is not None else np.zeros_like(payload[idx]) for idx, bits in enumerate(decoded_by_cb)]
    dec = summarize_cb_decode(decoded_final, payload)
    return Rank2LDPCTrialResult(
        mapping="sc_mimo",
        sic_mode=sic_mode,
        decoded_bits_by_cb=dec.decoded_bits_by_cb,
        cb_success=dec.cb_success,
        tb_success=dec.tb_success,
        cb_bler=dec.cb_bler,
        tb_bler=dec.tb_bler,
        goodput_bits=dec.goodput_bits,
        cancellation_count=cancellation_count,
        residual_energy_after_cancellation=residual_energy(y_res),
    )


def _split_llrs_by_lengths(flat_llr: np.ndarray, cb_e_values: Sequence[int]) -> List[np.ndarray]:
    out = []
    cursor = 0
    flat = np.asarray(flat_llr, dtype=np.float64).reshape(-1)
    for e in cb_e_values:
        e = int(e)
        out.append(flat[cursor:cursor + e].copy())
        cursor += e
    if cursor != flat.size:
        raise ValueError(f"CB E values sum to {cursor}, but LLR stream has {flat.size} bits.")
    return out


def _map_2cw_encoded_cbs_to_layer_grid(
    encoded_cbs_by_cw: Sequence[Sequence[np.ndarray]],
    qm: int,
    n_re_per_layer: int,
    layer_groups: Sequence[Sequence[int]] = ((0, 1), (2, 3)),
) -> np.ndarray:
    rank = sum(len(group) for group in layer_groups)
    grid = np.zeros((int(n_re_per_layer), int(rank)), dtype=np.complex128)
    for cw_idx, layers in enumerate(layer_groups):
        layers = [int(x) for x in layers]
        cw_bits = np.concatenate([
            np.asarray(bits, dtype=np.int8).reshape(-1)
            for bits in encoded_cbs_by_cw[int(cw_idx)]
        ])
        expected_bits = int(n_re_per_layer) * len(layers) * int(qm)
        if cw_bits.size != expected_bits:
            raise ValueError(f"2CW CW{cw_idx} expected {expected_bits} bits, got {cw_bits.size}.")
        symbols = qam_modulate(cw_bits, int(qm)).reshape(int(n_re_per_layer), len(layers))
        grid[:, layers] = symbols
    return grid


def _collect_2cw_llrs_from_detection(
    llr_grid: np.ndarray,
    cb_e_values_by_cw: Sequence[Sequence[int]],
    layer_groups: Sequence[Sequence[int]] = ((0, 1), (2, 3)),
) -> List[List[np.ndarray]]:
    llr = np.asarray(llr_grid, dtype=np.float64)
    out: List[List[np.ndarray]] = []
    for cw_idx, layers in enumerate(layer_groups):
        layers = [int(x) for x in layers]
        cw_llr = llr[:, layers, :].reshape(-1)
        out.append(_split_llrs_by_lengths(cw_llr, cb_e_values_by_cw[int(cw_idx)]))
    return out


def _run_2cw_baseline_from_payload_cbs(
    payload_bits_by_index: Sequence[np.ndarray],
    cb_e_values: Sequence[int],
    qm: int,
    n_re_per_layer: int,
    h_eff: np.ndarray,
    noise_var: float,
    rng: np.random.Generator | None,
    detector: str,
    list_size: int,
    num_iter: int = 20,
    h_det_eff: np.ndarray | None = None,
    layer_groups: Sequence[Sequence[int]] = ((0, 1), (2, 3)),
) -> tuple[Rank2LDPCTrialResult, List[CWSimulationStats]]:
    payload = [np.asarray(bits, dtype=np.int8).reshape(-1) for bits in payload_bits_by_index]
    if len(payload) % 2 != 0:
        raise ValueError("2CW baseline helper expects an even number of CB payloads.")
    mid = len(payload) // 2
    payload_by_cw = [payload[:mid], payload[mid:]]
    cb_e_values = [int(x) for x in cb_e_values]
    cb_e_by_cw = [cb_e_values[:mid], cb_e_values[mid:]]
    adapters = [
        SionnaLDPCAdapter(
            [int(bits.size) for bits in payload_by_cw[cw_idx]],
            cb_e_by_cw[cw_idx],
            num_iter=num_iter,
        )
        for cw_idx in range(2)
    ]
    encoded_by_cw = [
        adapters[cw_idx].encode(payload_by_cw[cw_idx])
        for cw_idx in range(2)
    ]
    x_grid = _map_2cw_encoded_cbs_to_layer_grid(
        encoded_by_cw,
        qm=qm,
        n_re_per_layer=n_re_per_layer,
        layer_groups=layer_groups,
    )
    y = apply_true_mimo_channel(x_grid, h_eff, noise_var=noise_var, rng=rng)
    h_detector = h_eff if h_det_eff is None else np.asarray(h_det_eff, dtype=np.complex128)
    det = detect_layer_grid(
        y,
        h_detector,
        qm=qm,
        noise_var=max(float(noise_var), 1e-9),
        detector=detector,
        list_size=list_size,
    )
    llrs_by_cw = _collect_2cw_llrs_from_detection(det.llr, cb_e_by_cw, layer_groups=layer_groups)
    decs = [
        adapters[cw_idx].decode(llrs_by_cw[cw_idx], payload_by_cw[cw_idx])
        for cw_idx in range(2)
    ]
    decoded_all = decs[0].decoded_bits_by_cb + decs[1].decoded_bits_by_cb
    payload_all = payload_by_cw[0] + payload_by_cw[1]
    combined = summarize_cb_decode(decoded_all, payload_all)
    cw_stats = []
    for cw_idx, dec in enumerate(decs):
        tb_size = int(sum(bits.size for bits in payload_by_cw[cw_idx]))
        cw_stats.append(CWSimulationStats(
            cw_index=cw_idx,
            trials=1,
            errors=0 if dec.tb_success else 1,
            tb_size=tb_size,
            n_cbs=len(payload_by_cw[cw_idx]),
            cb_trials=len(payload_by_cw[cw_idx]),
            cb_errors=sum(1 for ok in dec.cb_success if not ok),
            successful_payload_bits=float(dec.goodput_bits),
        ))
    return Rank2LDPCTrialResult(
        mapping="baseline_2cw_scheme4",
        sic_mode="no_sic",
        decoded_bits_by_cb=combined.decoded_bits_by_cb,
        cb_success=combined.cb_success,
        tb_success=combined.tb_success,
        cb_bler=combined.cb_bler,
        tb_bler=combined.tb_bler,
        goodput_bits=combined.goodput_bits,
        cancellation_count=0,
        residual_energy_after_cancellation=residual_energy(y),
    ), cw_stats


def run_rank2_ldpc_true_mimo_trial_from_tb_manager(
    resource: object | None = None,
    fixed_mcs: int = 3,
    mapping: str = "nr",
    sic_mode: str = "no_sic",
    payload_bits_by_index: Sequence[np.ndarray] | None = None,
    rng: np.random.Generator | None = None,
    noise_var: float = 0.0,
    num_iter: int = 20,
) -> tuple[Rank2LDPCTrialResult, Rank2TBGeneratedTrialSetup]:
    """Run one Phase 1b trial whose CB sizes come from TBManager."""

    setup = build_rank2_fixed_mcs_tb_setup(resource=resource, fixed_mcs=fixed_mcs)
    if rng is None:
        rng = np.random.default_rng(0)
    if payload_bits_by_index is None:
        payload_bits_by_index = [
            rng.integers(0, 2, size=int(k), dtype=np.int8)
            for k in setup.payload_bit_lengths
        ]

    result = run_rank2_ldpc_true_mimo_trial(
        payload_bits_by_index=payload_bits_by_index,
        cb_e_values=setup.cb_e_values,
        qm=setup.qm,
        n_re_per_layer=setup.n_re_per_layer,
        mapping=mapping,
        sic_mode=sic_mode,
        h_eff=deterministic_rank2_channel(setup.n_re_per_layer),
        noise_var=noise_var,
        rng=rng,
        num_iter=num_iter,
    )
    return result, setup


def run_rank2_phase1b_single_snr_comparison(
    resource: object | None = None,
    fixed_mcs: int = 3,
    payload_bits_by_index: Sequence[np.ndarray] | None = None,
    seed: int = 0,
    noise_var: float = 0.0,
    num_iter: int = 20,
) -> Rank2Phase1BComparisonResult:
    """Run the Phase 1b single-SNR NR-vs-SC-MIMO full-link smoke.

    The LDPC encoder is invoked once per CB. The resulting encoded CB bit
    arrays are then shared by the NR baseline branch and the SC-MIMO branch so
    both paths use identical payload, CB sizes, MCS, and rate-matched bits.
    """

    setup = build_rank2_fixed_mcs_tb_setup(resource=resource, fixed_mcs=fixed_mcs)
    rng_payload = np.random.default_rng(int(seed))
    if payload_bits_by_index is None:
        payload_bits_by_index = [
            rng_payload.integers(0, 2, size=int(k), dtype=np.int8)
            for k in setup.payload_bit_lengths
        ]
    payload = [np.asarray(bits, dtype=np.int8).reshape(-1) for bits in payload_bits_by_index]
    adapter = SionnaLDPCAdapter(
        [int(bits.size) for bits in payload],
        setup.cb_e_values,
        num_iter=num_iter,
    )
    encoded = adapter.encode(payload)
    h_eff = deterministic_rank2_channel(setup.n_re_per_layer)

    nr = _run_nr_branch_from_encoded_cbs(
        payload,
        encoded,
        adapter,
        qm=setup.qm,
        n_re_per_layer=setup.n_re_per_layer,
        h_eff=h_eff,
        noise_var=noise_var,
        rng=np.random.default_rng(int(seed) + 1000),
    )
    sc_no_sic = _run_sc_mimo_branch_from_encoded_cbs(
        payload,
        encoded,
        adapter,
        qm=setup.qm,
        n_re_per_layer=setup.n_re_per_layer,
        h_eff=h_eff,
        noise_var=noise_var,
        rng=np.random.default_rng(int(seed) + 1000),
        sic_mode="no_sic",
    )
    sc_ideal_sic = _run_sc_mimo_branch_from_encoded_cbs(
        payload,
        encoded,
        adapter,
        qm=setup.qm,
        n_re_per_layer=setup.n_re_per_layer,
        h_eff=h_eff,
        noise_var=noise_var,
        rng=np.random.default_rng(int(seed) + 1000),
        sic_mode="ideal_sic",
    )
    sc_decoded_sic = _run_sc_mimo_branch_from_encoded_cbs(
        payload,
        encoded,
        adapter,
        qm=setup.qm,
        n_re_per_layer=setup.n_re_per_layer,
        h_eff=h_eff,
        noise_var=noise_var,
        rng=np.random.default_rng(int(seed) + 1000),
        sic_mode="decoded_sic",
    )
    return Rank2Phase1BComparisonResult(
        setup=setup,
        noise_var=float(noise_var),
        encoded_cb_lengths=[int(bits.size) for bits in encoded],
        nr_no_sic=nr,
        sc_no_sic=sc_no_sic,
        sc_ideal_sic=sc_ideal_sic,
        sc_decoded_sic=sc_decoded_sic,
    )


def _noise_var_from_snr_db(snr_db: float) -> float:
    return float(10.0 ** (-float(snr_db) / 10.0))


def _summarize_phase1c_scheme(
    scheme_label: str,
    snr_db: float,
    trial_results: Sequence[Rank2LDPCTrialResult],
    setup: Rank2TBGeneratedTrialSetup,
    metadata: Dict[str, object],
) -> SNRSummary:
    trials = int(len(trial_results))
    n_cbs = int(setup.tb_info.n_cbs)
    tb_size = int(setup.tb_info.tb_size)
    cb_errors = sum(sum(1 for ok in result.cb_success if not ok) for result in trial_results)
    tb_errors = sum(1 for result in trial_results if not result.tb_success)
    goodput_sum = float(sum(result.goodput_bits for result in trial_results))

    cw_stats = [
        CWSimulationStats(
            cw_index=0,
            trials=trials,
            errors=tb_errors,
            tb_size=tb_size,
            n_cbs=n_cbs,
            cb_trials=trials * n_cbs,
            cb_errors=cb_errors,
            successful_payload_bits=goodput_sum,
        )
    ]
    return SNRSummary(
        scheme_label=scheme_label,
        snr_db=float(snr_db),
        trials=trials,
        scheme_errors=tb_errors,
        cw_stats=cw_stats,
        total_throughput_bits_per_slot=goodput_sum / trials if trials else 0.0,
        metadata=dict(metadata),
        n_re_per_layer=int(setup.n_re_per_layer),
        rank=2,
    )


def run_rank2_phase1c_snr_sweep(
    snr_db_values: Sequence[float],
    n_trials_per_snr: int,
    output_dir: str | Path,
    resource: object | None = None,
    fixed_mcs: int = 3,
    seed: int = 0,
    num_iter: int = 20,
) -> Rank2Phase1CSweepResult:
    """Run a small Phase 1c rank2 fixed-MCS NR-vs-SC-MIMO SNR sweep."""

    snr_values = [float(x) for x in snr_db_values]
    n_trials = int(n_trials_per_snr)
    if not snr_values:
        raise ValueError("snr_db_values must not be empty.")
    if n_trials <= 0:
        raise ValueError("n_trials_per_snr must be positive.")

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    summaries: List[SNRSummary] = []

    for snr_idx, snr_db in enumerate(snr_values):
        noise_var = _noise_var_from_snr_db(snr_db)
        by_label: Dict[str, List[Rank2LDPCTrialResult]] = {
            "nr_no_sic": [],
            "sc_mimo_no_sic": [],
            "sc_mimo_ideal_sic": [],
            "sc_mimo_decoded_sic": [],
        }
        setup_for_snr = None
        for trial_idx in range(n_trials):
            trial_seed = int(seed) + snr_idx * 10000 + trial_idx
            comparison = run_rank2_phase1b_single_snr_comparison(
                resource=resource,
                fixed_mcs=fixed_mcs,
                seed=trial_seed,
                noise_var=noise_var,
                num_iter=num_iter,
            )
            setup_for_snr = comparison.setup
            by_label["nr_no_sic"].append(comparison.nr_no_sic)
            by_label["sc_mimo_no_sic"].append(comparison.sc_no_sic)
            by_label["sc_mimo_ideal_sic"].append(comparison.sc_ideal_sic)
            by_label["sc_mimo_decoded_sic"].append(comparison.sc_decoded_sic)

        if setup_for_snr is None:
            raise RuntimeError("Internal error: no trials were run.")
        base_metadata = {
            "phase": "1c",
            "fixed_mcs": int(fixed_mcs),
            "noise_var": float(noise_var),
            "qm": int(setup_for_snr.qm),
            "n_cbs": int(setup_for_snr.tb_info.n_cbs),
            "tb_size": int(setup_for_snr.tb_info.tb_size),
            "cb_e_values": str([int(x) for x in setup_for_snr.cb_e_values]),
        }
        for label, vals in by_label.items():
            summaries.append(_summarize_phase1c_scheme(
                label,
                snr_db=snr_db,
                trial_results=vals,
                setup=setup_for_snr,
                metadata={**base_metadata, "mapping": label},
            ))

    save_csv(summaries, out_dir / "results.csv")
    save_json(summaries, out_dir / "results.json")
    try:
        from lls_platform.utils.plotting import plot_bler, plot_throughput
        plot_bler(summaries, out_dir / "cb_bler_vs_snr.png")
        plot_throughput(summaries, out_dir / "goodput_se_vs_snr.png")
    except Exception as e:
        with open(out_dir / "plot_error.txt", "w", encoding="utf-8") as f:
            f.write(repr(e))

    return Rank2Phase1CSweepResult(output_dir=out_dir, summaries=summaries)


def run_rank2_ldpc_true_mimo_trial(
    payload_bits_by_index: Sequence[np.ndarray],
    cb_e_values: Sequence[int],
    qm: int,
    n_re_per_layer: int,
    mapping: str,
    sic_mode: str = "no_sic",
    h_eff: np.ndarray | None = None,
    noise_var: float = 1e-9,
    rng: np.random.Generator | None = None,
    num_iter: int = 20,
) -> Rank2LDPCTrialResult:
    """Run one Phase 1b rank2 LDPC toy trial.

    The trial is intentionally small and deterministic-friendly. It validates
    LDPC encode/decode, NR/SC-MIMO CB LLR scatter, and SC-MIMO cancellation
    gating before the full experiment runner exists.
    """

    mapping = str(mapping).lower()
    sic_mode = str(sic_mode).lower()
    if mapping not in ("nr", "sc_mimo"):
        raise ValueError("mapping must be 'nr' or 'sc_mimo'.")
    if sic_mode not in ("no_sic", "ideal_sic", "decoded_sic"):
        raise ValueError("sic_mode must be no_sic, ideal_sic, or decoded_sic.")

    payload = [np.asarray(bits, dtype=np.int8).reshape(-1) for bits in payload_bits_by_index]
    adapter = SionnaLDPCAdapter([len(bits) for bits in payload], cb_e_values, num_iter=num_iter)
    coded = adapter.encode(payload)
    cb_e_values = [int(x) for x in cb_e_values]
    if [len(bits) for bits in coded] != cb_e_values:
        raise RuntimeError("Sionna LDPC encoded lengths do not match cb_e_values.")

    if h_eff is None:
        h_eff = deterministic_rank2_channel(n_re_per_layer)

    if mapping == "nr":
        return _run_nr_branch_from_encoded_cbs(
            payload,
            coded,
            adapter,
            qm=qm,
            n_re_per_layer=n_re_per_layer,
            h_eff=h_eff,
            noise_var=noise_var,
            rng=rng,
        )

    return _run_sc_mimo_branch_from_encoded_cbs(
        payload,
        coded,
        adapter,
        qm=qm,
        n_re_per_layer=n_re_per_layer,
        h_eff=h_eff,
        noise_var=noise_var,
        rng=rng,
        sic_mode=sic_mode,
    )


def run_rank4_phase2_kbest_ldpc_smoke(
    payload_bits_by_index: Sequence[np.ndarray] | None = None,
    cb_e_values: Sequence[int] | None = None,
    qm: int = 2,
    n_re_per_layer: int | None = None,
    noise_var: float = 0.0,
    seed: int = 0,
    list_size: int = 256,
    num_iter: int = 20,
) -> Rank4Phase2SmokeResult:
    """Run one compressed Phase-2 rank4 K-best LDPC smoke.

    The same encoded CBs feed a 1CW NR branch and a rank4 SC-MIMO decoded-SIC
    branch. This is intentionally a single end-to-end smoke, not separate
    mapping/detector/cancellation micro-tests.
    """

    qm = int(qm)
    if cb_e_values is None:
        cb_e_values = [96, 96, 96, 96]
    cb_e_values = [int(x) for x in cb_e_values]
    if any(e % qm != 0 for e in cb_e_values):
        raise ValueError("Each CB E must be divisible by qm.")

    total_e = int(sum(cb_e_values))
    if n_re_per_layer is None:
        if total_e % (4 * qm) != 0:
            raise ValueError("sum(cb_e_values) must fit a rank4 layer grid.")
        n_re_per_layer = total_e // (4 * qm)
    n_re_per_layer = int(n_re_per_layer)
    if total_e != n_re_per_layer * 4 * qm:
        raise ValueError("sum(cb_e_values) must equal n_re_per_layer * rank4 * qm.")

    rng_payload = np.random.default_rng(int(seed))
    if payload_bits_by_index is None:
        payload_bits_by_index = [
            rng_payload.integers(0, 2, size=24, dtype=np.int8)
            for _ in cb_e_values
        ]
    payload = [np.asarray(bits, dtype=np.int8).reshape(-1) for bits in payload_bits_by_index]

    adapter = SionnaLDPCAdapter(
        [int(bits.size) for bits in payload],
        cb_e_values,
        num_iter=num_iter,
    )
    encoded = adapter.encode(payload)
    h_eff = deterministic_mimo_channel(n_re=n_re_per_layer, rank=4, n_rx=4)
    detector = "kbest"

    nr = _run_nr_branch_from_encoded_cbs(
        payload,
        encoded,
        adapter,
        qm=qm,
        n_re_per_layer=n_re_per_layer,
        h_eff=h_eff,
        noise_var=noise_var,
        rng=np.random.default_rng(int(seed) + 1000),
        rank=4,
        detector=detector,
        list_size=list_size,
    )
    sc_decoded_sic = _run_sc_mimo_branch_from_encoded_cbs(
        payload,
        encoded,
        adapter,
        qm=qm,
        n_re_per_layer=n_re_per_layer,
        h_eff=h_eff,
        noise_var=noise_var,
        rng=np.random.default_rng(int(seed) + 1000),
        sic_mode="decoded_sic",
        layer_groups=((0, 1), (2, 3)),
        shift_pattern=(0, 1),
        detector=detector,
        list_size=list_size,
    )
    return Rank4Phase2SmokeResult(
        noise_var=float(noise_var),
        encoded_cb_lengths=[int(bits.size) for bits in encoded],
        n_re_per_layer=n_re_per_layer,
        qm=qm,
        detector=detector,
        list_size=int(list_size),
        nr_1cw=nr,
        sc_mimo_decoded_sic=sc_decoded_sic,
    )


def _summarize_phase2_single_cw_trials(
    scheme_label: str,
    snr_db: float,
    trial_results: Sequence[Rank2LDPCTrialResult],
    n_re_per_layer: int,
    metadata: Dict[str, object],
) -> SNRSummary:
    trials = int(len(trial_results))
    if trials <= 0:
        raise ValueError("trial_results must not be empty.")
    n_cbs = len(trial_results[0].cb_success)
    tb_size = int(metadata.get("tb_size", 0)) if isinstance(metadata.get("tb_size", 0), (int, float)) else 0
    if tb_size <= 0:
        tb_size = int(sum(result.goodput_bits for result in trial_results if result.tb_success) / max(sum(1 for result in trial_results if result.tb_success), 1))
    if tb_size <= 0:
        tb_size = int(max(result.goodput_bits for result in trial_results))
    cb_errors = sum(sum(1 for ok in result.cb_success if not ok) for result in trial_results)
    tb_errors = sum(1 for result in trial_results if not result.tb_success)
    goodput_sum = float(sum(result.goodput_bits for result in trial_results))
    cw_stats = [CWSimulationStats(
        cw_index=0,
        trials=trials,
        errors=tb_errors,
        tb_size=tb_size,
        n_cbs=n_cbs,
        cb_trials=trials * n_cbs,
        cb_errors=cb_errors,
        successful_payload_bits=goodput_sum,
    )]
    return SNRSummary(
        scheme_label=scheme_label,
        snr_db=float(snr_db),
        trials=trials,
        scheme_errors=tb_errors,
        cw_stats=cw_stats,
        total_throughput_bits_per_slot=goodput_sum / trials if trials else 0.0,
        metadata=dict(metadata),
        n_re_per_layer=int(n_re_per_layer),
        rank=int(metadata.get("rank", 4)),
    )


def _summarize_phase2_2cw_trials(
    scheme_label: str,
    snr_db: float,
    trial_results: Sequence[Rank2LDPCTrialResult],
    cw_stats_by_trial: Sequence[List[CWSimulationStats]],
    n_re_per_layer: int,
    metadata: Dict[str, object],
) -> SNRSummary:
    trials = int(len(trial_results))
    if trials <= 0:
        raise ValueError("trial_results must not be empty.")
    scheme_errors = sum(1 for result in trial_results if not result.tb_success)
    goodput_sum = float(sum(result.goodput_bits for result in trial_results))
    out_cw_stats: List[CWSimulationStats] = []
    for cw_idx in range(2):
        one = [stats[cw_idx] for stats in cw_stats_by_trial]
        out_cw_stats.append(CWSimulationStats(
            cw_index=cw_idx,
            trials=trials,
            errors=sum(s.errors for s in one),
            tb_size=one[0].tb_size,
            n_cbs=one[0].n_cbs,
            cb_trials=sum(s.cb_trials for s in one),
            cb_errors=sum(s.cb_errors for s in one),
            successful_payload_bits=sum(s.successful_payload_bits for s in one),
        ))
    return SNRSummary(
        scheme_label=scheme_label,
        snr_db=float(snr_db),
        trials=trials,
        scheme_errors=scheme_errors,
        cw_stats=out_cw_stats,
        total_throughput_bits_per_slot=goodput_sum / trials if trials else 0.0,
        metadata=dict(metadata),
        n_re_per_layer=int(n_re_per_layer),
        rank=int(metadata.get("rank", 4)),
    )


def _sc_mimo_groups_for_rank(rank: int) -> tuple[tuple[tuple[int, ...], ...], tuple[int, ...]]:
    rank = int(rank)
    if rank == 2:
        return ((0,), (1,)), (0, 1)
    if rank == 4:
        return ((0, 1), (2, 3)), (0, 1)
    raise ValueError("Current SC-MIMO comparison supports rank 2 and rank 4.")


def _two_cw_groups_for_rank(rank: int) -> tuple[tuple[int, ...], tuple[int, ...]]:
    rank = int(rank)
    if rank == 2:
        return ((0,), (1,))
    if rank == 4:
        return ((0, 1), (2, 3))
    raise ValueError("Current 2CW comparison supports rank 2 and rank 4.")


def _make_accumulator(n_cbs: int, tb_size: int) -> Dict[str, float]:
    return {
        "trials": 0,
        "scheme_errors": 0,
        "cb_trials": 0,
        "cb_errors": 0,
        "goodput_sum": 0.0,
        "n_cbs": int(n_cbs),
        "tb_size": int(tb_size),
    }


def _accumulate_single_cw_trial(acc: Dict[str, float], result: Rank2LDPCTrialResult) -> None:
    acc["trials"] += 1
    acc["scheme_errors"] += 0 if result.tb_success else 1
    acc["cb_trials"] += len(result.cb_success)
    acc["cb_errors"] += sum(1 for ok in result.cb_success if not ok)
    acc["goodput_sum"] += float(result.goodput_bits)


def _single_cw_summary_from_accumulator(
    scheme_label: str,
    snr_db: float,
    acc: Dict[str, float],
    n_re_per_layer: int,
    rank: int,
    metadata: Dict[str, object],
) -> SNRSummary:
    trials = int(acc["trials"])
    cw_stats = [CWSimulationStats(
        cw_index=0,
        trials=trials,
        errors=int(acc["scheme_errors"]),
        tb_size=int(acc["tb_size"]),
        n_cbs=int(acc["n_cbs"]),
        cb_trials=int(acc["cb_trials"]),
        cb_errors=int(acc["cb_errors"]),
        successful_payload_bits=float(acc["goodput_sum"]),
    )]
    return SNRSummary(
        scheme_label=scheme_label,
        snr_db=float(snr_db),
        trials=trials,
        scheme_errors=int(acc["scheme_errors"]),
        cw_stats=cw_stats,
        total_throughput_bits_per_slot=float(acc["goodput_sum"]) / trials if trials else 0.0,
        metadata=dict(metadata),
        n_re_per_layer=int(n_re_per_layer),
        rank=int(rank),
    )


def _make_2cw_accumulator(n_cbs_by_cw: Sequence[int], tb_size_by_cw: Sequence[int]) -> Dict[str, object]:
    return {
        "trials": 0,
        "scheme_errors": 0,
        "goodput_sum": 0.0,
        "cw": [
            {
                "errors": 0,
                "tb_size": int(tb_size_by_cw[cw_idx]),
                "n_cbs": int(n_cbs_by_cw[cw_idx]),
                "cb_trials": 0,
                "cb_errors": 0,
                "successful_payload_bits": 0.0,
            }
            for cw_idx in range(2)
        ],
    }


def _accumulate_2cw_trial(
    acc: Dict[str, object],
    result: Rank2LDPCTrialResult,
    cw_stats: Sequence[CWSimulationStats],
) -> None:
    acc["trials"] = int(acc["trials"]) + 1
    acc["scheme_errors"] = int(acc["scheme_errors"]) + (0 if result.tb_success else 1)
    acc["goodput_sum"] = float(acc["goodput_sum"]) + float(result.goodput_bits)
    cw_accs = acc["cw"]
    for cw_idx, stats in enumerate(cw_stats):
        cw_acc = cw_accs[cw_idx]
        cw_acc["errors"] = int(cw_acc["errors"]) + int(stats.errors)
        cw_acc["cb_trials"] = int(cw_acc["cb_trials"]) + int(stats.cb_trials)
        cw_acc["cb_errors"] = int(cw_acc["cb_errors"]) + int(stats.cb_errors)
        cw_acc["successful_payload_bits"] = float(cw_acc["successful_payload_bits"]) + float(stats.successful_payload_bits)


def _two_cw_summary_from_accumulator(
    scheme_label: str,
    snr_db: float,
    acc: Dict[str, object],
    n_re_per_layer: int,
    rank: int,
    metadata: Dict[str, object],
) -> SNRSummary:
    trials = int(acc["trials"])
    cw_stats: List[CWSimulationStats] = []
    for cw_idx, cw_acc in enumerate(acc["cw"]):
        cw_stats.append(CWSimulationStats(
            cw_index=cw_idx,
            trials=trials,
            errors=int(cw_acc["errors"]),
            tb_size=int(cw_acc["tb_size"]),
            n_cbs=int(cw_acc["n_cbs"]),
            cb_trials=int(cw_acc["cb_trials"]),
            cb_errors=int(cw_acc["cb_errors"]),
            successful_payload_bits=float(cw_acc["successful_payload_bits"]),
        ))
    return SNRSummary(
        scheme_label=scheme_label,
        snr_db=float(snr_db),
        trials=trials,
        scheme_errors=int(acc["scheme_errors"]),
        cw_stats=cw_stats,
        total_throughput_bits_per_slot=float(acc["goodput_sum"]) / trials if trials else 0.0,
        metadata=dict(metadata),
        n_re_per_layer=int(n_re_per_layer),
        rank=int(rank),
    )


def _plot_scheme_bler(summaries: Sequence[SNRSummary], path: str | Path) -> None:
    from collections import defaultdict
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    groups = defaultdict(list)
    for summary in summaries:
        groups[summary.scheme_label].append(summary)

    plt.figure()
    for label, vals in groups.items():
        vals = sorted(vals, key=lambda x: x.snr_db)
        plt.semilogy(
            [v.snr_db for v in vals],
            [max(v.scheme_bler, 1e-5) for v in vals],
            marker="o",
            label=label,
        )
    plt.xlabel("SNR (dB)")
    plt.ylabel("TB-BLER")
    plt.grid(True, which="both")
    plt.legend(fontsize=8)
    plt.tight_layout()
    plt.savefig(path, dpi=200)
    plt.close()


def run_phase2_ideal_csi_rank4_comparison_sweep(
    snr_db_values: Sequence[float],
    speeds_kmh: Sequence[float] = (3.0, 30.0),
    n_trials_per_snr: int = 1,
    output_dir: str | Path = "results_phase2_ideal_csi_rank4",
    n_re_per_layer: int = 48,
    cb_e_values: Sequence[int] = (96, 96, 96, 96),
    payload_k_per_cb: int = 24,
    list_size: int = 256,
    num_iter: int = 20,
    seed: int = 20260516,
) -> Phase2IdealComparisonSweepResult:
    """Run Phase-2 ideal-CSI rank4 comparison and save Figure-style plots."""

    snr_values = [float(x) for x in snr_db_values]
    speeds = [float(x) for x in speeds_kmh]
    n_trials = int(n_trials_per_snr)
    if not snr_values:
        raise ValueError("snr_db_values must not be empty.")
    if not speeds:
        raise ValueError("speeds_kmh must not be empty.")
    if n_trials <= 0:
        raise ValueError("n_trials_per_snr must be positive.")

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    summaries: List[SNRSummary] = []
    qm = 2
    cb_e_values = [int(x) for x in cb_e_values]
    detector = "kbest"

    for speed in speeds:
        speed_dir = out_dir / f"speed_{int(speed)}kmh"
        speed_dir.mkdir(parents=True, exist_ok=True)
        for snr_idx, snr_db in enumerate(snr_values):
            noise_var = _noise_var_from_snr_db(snr_db)
            nr_trials: List[Rank2LDPCTrialResult] = []
            sc_trials: List[Rank2LDPCTrialResult] = []
            cw2_trials: List[Rank2LDPCTrialResult] = []
            cw2_stats: List[List[CWSimulationStats]] = []
            channel_report: Dict[str, object] = {}

            for trial_idx in range(n_trials):
                trial_seed = int(seed) + int(speed * 10) * 100000 + snr_idx * 1000 + trial_idx
                rng_payload = np.random.default_rng(trial_seed)
                payload = [
                    rng_payload.integers(0, 2, size=int(payload_k_per_cb), dtype=np.int8)
                    for _ in cb_e_values
                ]
                adapter = SionnaLDPCAdapter(
                    [int(bits.size) for bits in payload],
                    cb_e_values,
                    num_iter=num_iter,
                )
                encoded = adapter.encode(payload)
                h_eff, channel_report = sample_cdl_ideal_svd_h_eff_rank4(
                    n_re_per_layer=int(n_re_per_layer),
                    speed_kmh=speed,
                    batch_size=1,
                    seed=trial_seed,
                )
                noise_rng = np.random.default_rng(trial_seed + 777)
                nr_trials.append(_run_nr_branch_from_encoded_cbs(
                    payload,
                    encoded,
                    adapter,
                    qm=qm,
                    n_re_per_layer=n_re_per_layer,
                    h_eff=h_eff,
                    noise_var=noise_var,
                    rng=noise_rng,
                    rank=4,
                    detector=detector,
                    list_size=list_size,
                ))
                sc_trials.append(_run_sc_mimo_branch_from_encoded_cbs(
                    payload,
                    encoded,
                    adapter,
                    qm=qm,
                    n_re_per_layer=n_re_per_layer,
                    h_eff=h_eff,
                    noise_var=noise_var,
                    rng=np.random.default_rng(trial_seed + 777),
                    sic_mode="decoded_sic",
                    layer_groups=((0, 1), (2, 3)),
                    shift_pattern=(0, 1),
                    detector="hybrid_rml_mmse_after_sic",
                    list_size=list_size,
                ))
                two_cw, stats = _run_2cw_baseline_from_payload_cbs(
                    payload,
                    cb_e_values=cb_e_values,
                    qm=qm,
                    n_re_per_layer=n_re_per_layer,
                    h_eff=h_eff,
                    noise_var=noise_var,
                    rng=np.random.default_rng(trial_seed + 777),
                    detector=detector,
                    list_size=list_size,
                    num_iter=num_iter,
                )
                cw2_trials.append(two_cw)
                cw2_stats.append(stats)

            base_meta = {
                "phase": "2_ideal_csi",
                "rank": 4,
                "speed_kmh": float(speed),
                "channel": "CDL-A 30ns",
                "bandwidth_mhz": 20.0,
                "scs_khz": 15,
                "n_prbs": 106,
                "n_re_sampled": int(n_re_per_layer),
                "detector_baseline": detector,
                "sc_mimo_receiver": "hybrid_rml_mmse_after_sic",
                "list_size": int(list_size),
                "precoder": "ideal_per_re_svd",
                "receiver_csi": "ideal",
                "baseline_1": "nr_1cw_scheme1_rank4_layers_[0,1,2,3]",
                "baseline_2": "baseline_2cw_scheme4_rank4_layers_[0,1]_[2,3]",
                "cb_e_values": str(cb_e_values),
                "payload_k_per_cb": int(payload_k_per_cb),
                "tb_size": int(payload_k_per_cb) * len(cb_e_values),
                "cdl_report": str(channel_report),
            }
            summaries.append(_summarize_phase2_single_cw_trials(
                "nr_1cw",
                snr_db,
                nr_trials,
                n_re_per_layer=n_re_per_layer,
                metadata={**base_meta, "mapping": "nr_1cw"},
            ))
            summaries.append(_summarize_phase2_single_cw_trials(
                "sc_mimo_sic",
                snr_db,
                sc_trials,
                n_re_per_layer=n_re_per_layer,
                metadata={**base_meta, "mapping": "sc_mimo_sic"},
            ))
            summaries.append(_summarize_phase2_2cw_trials(
                "baseline_2cw",
                snr_db,
                cw2_trials,
                cw2_stats,
                n_re_per_layer=n_re_per_layer,
                metadata={**base_meta, "mapping": "baseline_2cw_scheme4"},
            ))

        speed_summaries = [s for s in summaries if float(s.metadata.get("speed_kmh", -1.0)) == float(speed)]
        save_csv(speed_summaries, speed_dir / "results.csv")
        save_json(speed_summaries, speed_dir / "results.json")
        from lls_platform.utils.plotting import plot_bler, plot_throughput
        plot_throughput(speed_summaries, speed_dir / "throughput_vs_snr.png")
        plot_bler(speed_summaries, speed_dir / "cb_bler_vs_snr.png")

    save_csv(summaries, out_dir / "results.csv")
    save_json(summaries, out_dir / "results.json")
    from lls_platform.utils.plotting import plot_bler, plot_throughput
    plot_throughput(summaries, out_dir / "throughput_vs_snr_all_speeds.png")
    plot_bler(summaries, out_dir / "cb_bler_vs_snr_all_speeds.png")
    return Phase2IdealComparisonSweepResult(output_dir=out_dir, summaries=summaries)


def run_phase3_csi_error_fixed_qpsk_bler_sweep(
    snr_db_values: Sequence[float] = (-12.0, -9.0, -6.0, -3.0, 0.0),
    ranks: Sequence[int] = (2, 4),
    speeds_kmh: Sequence[float] = (3.0, 30.0),
    csi_error_cases: Sequence[tuple[str, float | None, float | None]] | None = None,
    n_trials_per_snr: int = 10,
    output_dir: str | Path = "results_phase3_csi_error_qpsk_bler",
    n_re_per_layer: int = 48,
    payload_k_per_cb: int = 24,
    num_iter: int = 20,
    seed: int = 20260517,
) -> CSIErrorComparisonSweepResult:
    """Run fixed-QPSK BLER comparison with streamed TX/RX NMSE CSI errors.

    The sweep compares NR 1CW, SC-MIMO decoded SIC, and the 2CW baseline for
    rank2/rank4. It uses the same true channel, TX precoder, RX channel
    estimate, payload, and noise seed across the three schemes in each trial.
    """

    snr_values = [float(x) for x in snr_db_values]
    ranks = [int(x) for x in ranks]
    speeds = [float(x) for x in speeds_kmh]
    n_trials = int(n_trials_per_snr)
    if csi_error_cases is None:
        csi_error_cases = (
            ("ideal_csi", None, None),
            ("txrx_nmse_-15dB", -15.0, -15.0),
        )
    if not snr_values:
        raise ValueError("snr_db_values must not be empty.")
    if not ranks:
        raise ValueError("ranks must not be empty.")
    if not speeds:
        raise ValueError("speeds_kmh must not be empty.")
    if n_trials <= 0:
        raise ValueError("n_trials_per_snr must be positive.")

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    summaries: List[SNRSummary] = []
    qm = 2

    for rank in ranks:
        if rank not in (2, 4):
            raise ValueError("run_phase3_csi_error_fixed_qpsk_bler_sweep currently supports rank 2 and rank 4.")
        cb_e_values = [int(n_re_per_layer) * qm for _ in range(rank)]
        tb_size = int(payload_k_per_cb) * rank
        list_size = int(2 ** (rank * qm))
        sc_layer_groups, sc_shift_pattern = _sc_mimo_groups_for_rank(rank)
        two_cw_groups = _two_cw_groups_for_rank(rank)

        for speed in speeds:
            for case_label, tx_nmse_db, rx_nmse_db in csi_error_cases:
                case_slug = (
                    str(case_label)
                    .replace("/", "_")
                    .replace(" ", "_")
                    .replace("+", "plus")
                    .replace("-", "m")
                )
                subset_dir = out_dir / f"rank{rank}" / f"speed_{int(speed)}kmh" / case_slug
                subset_dir.mkdir(parents=True, exist_ok=True)
                subset_summaries: List[SNRSummary] = []

                for snr_idx, snr_db in enumerate(snr_values):
                    noise_var = _noise_var_from_snr_db(snr_db)
                    nr_acc = _make_accumulator(n_cbs=rank, tb_size=tb_size)
                    sc_acc = _make_accumulator(n_cbs=rank, tb_size=tb_size)
                    mid = rank // 2
                    cw2_acc = _make_2cw_accumulator(
                        n_cbs_by_cw=(mid, rank - mid),
                        tb_size_by_cw=(int(payload_k_per_cb) * mid, int(payload_k_per_cb) * (rank - mid)),
                    )
                    channel_report: Dict[str, object] = {}

                    for trial_idx in range(n_trials):
                        trial_seed = (
                            int(seed)
                            + rank * 10_000_000
                            + int(speed * 10) * 100_000
                            + snr_idx * 1_000
                            + trial_idx
                        )
                        rng_payload = np.random.default_rng(trial_seed)
                        payload = [
                            rng_payload.integers(0, 2, size=int(payload_k_per_cb), dtype=np.int8)
                            for _ in cb_e_values
                        ]
                        adapter = SionnaLDPCAdapter(
                            [int(bits.size) for bits in payload],
                            cb_e_values,
                            num_iter=num_iter,
                        )
                        encoded = adapter.encode(payload)
                        h_eff_true, h_eff_rx, channel_report = sample_cdl_svd_effective_channels_with_csi_error(
                            n_re_per_layer=int(n_re_per_layer),
                            rank=rank,
                            speed_kmh=speed,
                            tx_nmse_db=tx_nmse_db,
                            rx_nmse_db=rx_nmse_db,
                            seed=trial_seed,
                        )
                        noise_seed = trial_seed + 777
                        nr = _run_nr_branch_from_encoded_cbs(
                            payload,
                            encoded,
                            adapter,
                            qm=qm,
                            n_re_per_layer=n_re_per_layer,
                            h_eff=h_eff_true,
                            h_det_eff=h_eff_rx,
                            noise_var=noise_var,
                            rng=np.random.default_rng(noise_seed),
                            rank=rank,
                            detector="kbest",
                            list_size=list_size,
                        )
                        _accumulate_single_cw_trial(nr_acc, nr)

                        sc = _run_sc_mimo_branch_from_encoded_cbs(
                            payload,
                            encoded,
                            adapter,
                            qm=qm,
                            n_re_per_layer=n_re_per_layer,
                            h_eff=h_eff_true,
                            h_det_eff=h_eff_rx,
                            noise_var=noise_var,
                            rng=np.random.default_rng(noise_seed),
                            sic_mode="decoded_sic",
                            layer_groups=sc_layer_groups,
                            shift_pattern=sc_shift_pattern,
                            detector="hybrid_rml_mmse_after_sic",
                            list_size=list_size,
                        )
                        _accumulate_single_cw_trial(sc_acc, sc)

                        two_cw, stats = _run_2cw_baseline_from_payload_cbs(
                            payload,
                            cb_e_values=cb_e_values,
                            qm=qm,
                            n_re_per_layer=n_re_per_layer,
                            h_eff=h_eff_true,
                            h_det_eff=h_eff_rx,
                            noise_var=noise_var,
                            rng=np.random.default_rng(noise_seed),
                            detector="kbest",
                            list_size=list_size,
                            num_iter=num_iter,
                            layer_groups=two_cw_groups,
                        )
                        _accumulate_2cw_trial(cw2_acc, two_cw, stats)

                    base_meta = {
                        "phase": "3_csi_error_fixed_qpsk_bler",
                        "rank": int(rank),
                        "speed_kmh": float(speed),
                        "channel": "CDL-A 30ns",
                        "bandwidth_mhz": 20.0,
                        "scs_khz": 15,
                        "n_prbs": 106,
                        "n_re_sampled": int(n_re_per_layer),
                        "qm": qm,
                        "modulation": "QPSK",
                        "detector_baseline": "kbest_rml",
                        "sc_mimo_receiver": "hybrid_rml_mmse_after_sic",
                        "list_size": int(list_size),
                        "precoder": "per_re_svd_on_tx_estimated_channel",
                        "receiver_csi": "rx_estimated_effective_channel",
                        "csi_error_case": str(case_label),
                        "tx_nmse_db": _nmse_label(tx_nmse_db),
                        "rx_nmse_db": _nmse_label(rx_nmse_db),
                        "baseline_1": f"nr_1cw_scheme1_rank{rank}",
                        "baseline_2": f"baseline_2cw_scheme4_rank{rank}_layers_{two_cw_groups}",
                        "sc_mimo_layer_groups": str(sc_layer_groups),
                        "cb_e_values": str(cb_e_values),
                        "payload_k_per_cb": int(payload_k_per_cb),
                        "tb_size": tb_size,
                        "memory_policy": "stream full CDL grid per trial, sample data REs, add NMSE only on sampled H",
                        "full_grid_bytes_per_trial": int(channel_report.get("full_grid_bytes_per_trial", 0)),
                        "retained_channel_bytes_per_trial": int(channel_report.get("retained_channel_bytes_per_trial", 0)),
                    }
                    subset_summaries.extend([
                        _single_cw_summary_from_accumulator(
                            "nr_1cw",
                            snr_db,
                            nr_acc,
                            n_re_per_layer=n_re_per_layer,
                            rank=rank,
                            metadata={**base_meta, "mapping": "nr_1cw"},
                        ),
                        _single_cw_summary_from_accumulator(
                            "sc_mimo_sic",
                            snr_db,
                            sc_acc,
                            n_re_per_layer=n_re_per_layer,
                            rank=rank,
                            metadata={**base_meta, "mapping": "sc_mimo_sic"},
                        ),
                        _two_cw_summary_from_accumulator(
                            "baseline_2cw",
                            snr_db,
                            cw2_acc,
                            n_re_per_layer=n_re_per_layer,
                            rank=rank,
                            metadata={**base_meta, "mapping": "baseline_2cw_scheme4"},
                        ),
                    ])

                save_csv(subset_summaries, subset_dir / "results.csv")
                save_json(subset_summaries, subset_dir / "results.json")
                from lls_platform.utils.plotting import plot_bler
                plot_bler(subset_summaries, subset_dir / "cb_bler_vs_snr.png")
                _plot_scheme_bler(subset_summaries, subset_dir / "tb_bler_vs_snr.png")
                summaries.extend(subset_summaries)

    save_csv(summaries, out_dir / "results.csv")
    save_json(summaries, out_dir / "results.json")
    return CSIErrorComparisonSweepResult(output_dir=out_dir, summaries=summaries)


def ldpc_backend_available() -> tuple[bool, str]:
    """Return whether the Sionna 1.x LDPC/mapping backend is importable."""

    try:
        import tensorflow as tf
        import sionna
        from sionna.phy.fec.ldpc import LDPC5GDecoder, LDPC5GEncoder  # noqa: F401
        from sionna.phy.mapping import Demapper, Mapper  # noqa: F401
    except Exception as e:
        return False, repr(e)
    return True, f"tensorflow {tf.__version__}, sionna {sionna.__version__}, LDPC/Mapper OK"
