from __future__ import annotations

from dataclasses import dataclass
import numpy as np


@dataclass
class LinkQuality:
    layer_se_eff: np.ndarray
    layer_sinr_eff_db: np.ndarray
    layer_sinr_re_db_mean: np.ndarray
    gamma_total_db: float


def linear_to_db(x):
    return 10.0 * np.log10(np.maximum(np.asarray(x, dtype=float), 1e-30))


def compute_svd_capacity_quality(singular_values: np.ndarray, snr_db: float, rank: int,
                                 shannon_gap_db: float = 2.5, mcs_margin_db: float = 0.0) -> LinkQuality:
    """Compute per-layer capacity-domain effective SE for SVD layers.

    singular_values: [B,T,F,K] or [T,F,K] or [F,K]. Uses first rank values.
    SINR_l = SNR * sigma_l^2 / rank.
    SE_l,re = log2(1 + SINR_l / Gamma).
    """
    s = np.asarray(singular_values, dtype=float)
    if s.shape[-1] < int(rank):
        raise ValueError(f"singular_values last dim {s.shape[-1]} < rank {rank}")
    s = np.sort(s, axis=-1)[..., ::-1]
    s = s[..., :int(rank)]
    snr_lin = 10.0 ** (float(snr_db) / 10.0)
    gamma_db = float(shannon_gap_db) + float(mcs_margin_db)
    gamma = 10.0 ** (gamma_db / 10.0)
    sinr = snr_lin * (s ** 2) / float(max(int(rank), 1))
    se = np.log2(1.0 + sinr / gamma)
    axes = tuple(range(se.ndim - 1))
    layer_se_eff = np.mean(se, axis=axes)
    sinr_eff = gamma * (np.power(2.0, layer_se_eff) - 1.0)
    layer_sinr_eff_db = linear_to_db(sinr_eff)
    layer_sinr_re_db_mean = linear_to_db(np.mean(sinr, axis=axes))
    return LinkQuality(
        layer_se_eff=np.asarray(layer_se_eff, dtype=float),
        layer_sinr_eff_db=np.asarray(layer_sinr_eff_db, dtype=float),
        layer_sinr_re_db_mean=np.asarray(layer_sinr_re_db_mean, dtype=float),
        gamma_total_db=gamma_db,
    )
