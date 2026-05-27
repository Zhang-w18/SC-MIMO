from __future__ import annotations

"""Small NumPy MIMO detection utilities for early SC-MIMO validation."""

from dataclasses import dataclass
from itertools import product
from typing import List, Optional, Tuple
import numpy as np

from lls_platform.phy.numpy_qam import qam_modulate


@dataclass(frozen=True)
class ExhaustiveMIMODetectionResult:
    best_bits: np.ndarray
    llr: np.ndarray
    candidate_metrics: List[Tuple[np.ndarray, np.ndarray, float]]


def _validate_mimo_inputs(y: np.ndarray, h: np.ndarray, qm: int, noise_var: float) -> tuple[np.ndarray, np.ndarray, int, int, float]:
    y_arr = np.asarray(y, dtype=np.complex128).reshape(-1)
    h_arr = np.asarray(h, dtype=np.complex128)
    if h_arr.ndim != 2:
        raise ValueError("h must have shape [n_rx, rank].")
    if h_arr.shape[0] != y_arr.shape[0]:
        raise ValueError("h and y receive dimensions do not match.")
    rank = int(h_arr.shape[1])
    qm = int(qm)
    if rank <= 0:
        raise ValueError("rank must be positive.")
    if qm <= 0:
        raise ValueError("qm must be positive.")
    return y_arr, h_arr, rank, qm, max(float(noise_var), 1e-30)


def _qam_symbol_candidates(qm: int) -> tuple[np.ndarray, np.ndarray]:
    bits = np.asarray(list(product((0, 1), repeat=int(qm))), dtype=np.int8)
    symbols = qam_modulate(bits.reshape(-1), int(qm))
    return bits, symbols


def _llr_from_candidate_metrics(
    metrics: List[Tuple[np.ndarray, np.ndarray, float]],
    rank: int,
    qm: int,
) -> np.ndarray:
    llr = np.zeros(int(rank) * int(qm), dtype=np.float64)
    inf = float("inf")
    for bit_pos in range(int(rank) * int(qm)):
        layer = bit_pos // int(qm)
        offset = bit_pos % int(qm)
        m0 = inf
        m1 = inf
        for bits, _x, metric in metrics:
            if int(bits[layer, offset]) == 0:
                m0 = min(m0, metric)
            else:
                m1 = min(m1, metric)
        llr[bit_pos] = m0 - m1
    return llr


def exhaustive_qam_mimo_detect(
    y: np.ndarray,
    h: np.ndarray,
    qm: int,
    noise_var: float = 1.0,
    max_list_size: Optional[int] = None,
) -> ExhaustiveMIMODetectionResult:
    """Exhaustive max-log detector for small rank/Qm experiments.

    This is not intended for large simulations. It provides a correctness
    reference for rank-2/rank-4 toy tests before adding K-best/rML.
    """

    y, h, rank, qm, noise_var = _validate_mimo_inputs(y, h, qm, noise_var)

    metrics: List[Tuple[np.ndarray, np.ndarray, float]] = []
    bit_candidates, symbol_candidates = _qam_symbol_candidates(qm)
    for symbol_indices in product(range(symbol_candidates.size), repeat=rank):
        bits = np.asarray([bit_candidates[i] for i in symbol_indices], dtype=np.int8)
        x = np.asarray([symbol_candidates[i] for i in symbol_indices], dtype=np.complex128)
        residual = y - h.dot(x)
        metric = float(np.vdot(residual, residual).real / noise_var)
        metrics.append((bits.copy(), x, metric))

    metrics.sort(key=lambda item: item[2])
    if max_list_size is not None:
        metrics = metrics[:int(max_list_size)]

    best_bits = metrics[0][0].copy()
    llr = _llr_from_candidate_metrics(metrics, rank=rank, qm=qm)
    return ExhaustiveMIMODetectionResult(best_bits=best_bits, llr=llr, candidate_metrics=metrics)


def kbest_qam_mimo_detect(
    y: np.ndarray,
    h: np.ndarray,
    qm: int,
    noise_var: float = 1.0,
    k: int = 16,
) -> ExhaustiveMIMODetectionResult:
    """QR-domain K-best/list max-log detector.

    The search proceeds from the last QR layer to the first and keeps the
    lowest-metric ``k`` partial paths at each level. This is the Phase-2
    scalable detector baseline before wiring rML/SIC variants into the full
    SC-MIMO orchestrator.
    """

    y, h, rank, qm, noise_var = _validate_mimo_inputs(y, h, qm, noise_var)
    k = int(k)
    if k <= 0:
        raise ValueError("k must be positive.")
    if h.shape[0] < rank:
        raise ValueError("K-best detector requires n_rx >= rank for QR tree search.")

    q_mat, r_mat = np.linalg.qr(h, mode="reduced")
    z = q_mat.conj().T.dot(y)
    bit_candidates, symbol_candidates = _qam_symbol_candidates(qm)

    empty_bits = np.zeros((rank, qm), dtype=np.int8)
    empty_x = np.zeros(rank, dtype=np.complex128)
    paths: List[Tuple[np.ndarray, np.ndarray, float]] = [(empty_bits, empty_x, 0.0)]

    for level in range(rank - 1, -1, -1):
        expanded: List[Tuple[np.ndarray, np.ndarray, float]] = []
        for bits_so_far, x_so_far, path_metric in paths:
            for cand_bits, cand_symbol in zip(bit_candidates, symbol_candidates):
                bits_new = bits_so_far.copy()
                x_new = x_so_far.copy()
                bits_new[level] = cand_bits
                x_new[level] = cand_symbol
                predicted = np.dot(r_mat[level, level:rank], x_new[level:rank])
                residual = z[level] - predicted
                metric = float(path_metric + np.vdot(residual, residual).real / noise_var)
                expanded.append((bits_new, x_new, metric))
        expanded.sort(key=lambda item: item[2])
        paths = expanded[:k]

    metrics: List[Tuple[np.ndarray, np.ndarray, float]] = []
    for bits, x_vec, _path_metric in paths:
        residual = y - h.dot(x_vec)
        metric = float(np.vdot(residual, residual).real / noise_var)
        metrics.append((bits.copy(), x_vec.copy(), metric))
    metrics.sort(key=lambda item: item[2])

    best_bits = metrics[0][0].copy()
    llr = _llr_from_candidate_metrics(metrics, rank=rank, qm=qm)
    return ExhaustiveMIMODetectionResult(best_bits=best_bits, llr=llr, candidate_metrics=metrics)


def mmse_qam_mimo_detect(
    y: np.ndarray,
    h: np.ndarray,
    qm: int,
    noise_var: float = 1.0,
    symbol_energy: float = 1.0,
) -> ExhaustiveMIMODetectionResult:
    """Linear-MMSE soft detector with Gaussian residual-interference model."""

    y, h, rank, qm, noise_var = _validate_mimo_inputs(y, h, qm, noise_var)
    symbol_energy = max(float(symbol_energy), 1e-30)
    bit_candidates, symbol_candidates = _qam_symbol_candidates(qm)

    h_h = h.conj().T
    gram = h_h.dot(h)
    reg = (noise_var / symbol_energy) * np.eye(rank, dtype=np.complex128)
    w = np.linalg.solve(gram + reg, h_h)
    z = w.dot(y)
    g = w.dot(h)

    best_bits = np.zeros((rank, qm), dtype=np.int8)
    llr = np.zeros(rank * qm, dtype=np.float64)
    for layer in range(rank):
        g_ll = g[layer, layer]
        interference = symbol_energy * (
            float(np.sum(np.abs(g[layer, :]) ** 2)) - float(abs(g_ll) ** 2)
        )
        sigma2 = max(float(noise_var * np.vdot(w[layer], w[layer]).real + interference), 1e-30)
        symbol_metrics = np.asarray(
            [float(abs(z[layer] - g_ll * s) ** 2 / sigma2) for s in symbol_candidates],
            dtype=np.float64,
        )
        best_idx = int(np.argmin(symbol_metrics))
        best_bits[layer] = bit_candidates[best_idx]
        for bit_idx in range(qm):
            bit_vals = bit_candidates[:, bit_idx]
            m0 = float(np.min(symbol_metrics[bit_vals == 0]))
            m1 = float(np.min(symbol_metrics[bit_vals == 1]))
            llr[layer * qm + bit_idx] = m0 - m1

    return ExhaustiveMIMODetectionResult(best_bits=best_bits, llr=llr, candidate_metrics=[])
