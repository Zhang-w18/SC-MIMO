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

    y = np.asarray(y, dtype=np.complex128).reshape(-1)
    h = np.asarray(h, dtype=np.complex128)
    if h.ndim != 2:
        raise ValueError("h must have shape [n_rx, rank].")
    if h.shape[0] != y.shape[0]:
        raise ValueError("h and y receive dimensions do not match.")
    rank = int(h.shape[1])
    qm = int(qm)
    noise_var = max(float(noise_var), 1e-30)

    metrics: List[Tuple[np.ndarray, np.ndarray, float]] = []
    for flat_bits in product((0, 1), repeat=rank * qm):
        bits = np.asarray(flat_bits, dtype=np.int8).reshape(rank, qm)
        x = np.asarray([qam_modulate(bits[layer], qm)[0] for layer in range(rank)], dtype=np.complex128)
        residual = y - h.dot(x)
        metric = float(np.vdot(residual, residual).real / noise_var)
        metrics.append((bits.copy(), x, metric))

    metrics.sort(key=lambda item: item[2])
    if max_list_size is not None:
        metrics = metrics[:int(max_list_size)]

    best_bits = metrics[0][0].copy()
    llr = np.zeros(rank * qm, dtype=np.float64)
    inf = float("inf")
    for bit_pos in range(rank * qm):
        layer = bit_pos // qm
        offset = bit_pos % qm
        m0 = inf
        m1 = inf
        for bits, _x, metric in metrics:
            if int(bits[layer, offset]) == 0:
                m0 = min(m0, metric)
            else:
                m1 = min(m1, metric)
        llr[bit_pos] = m0 - m1

    return ExhaustiveMIMODetectionResult(best_bits=best_bits, llr=llr, candidate_metrics=metrics)
