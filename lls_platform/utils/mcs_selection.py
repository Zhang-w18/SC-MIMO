from __future__ import annotations

import math
from typing import Dict, Iterable, List, Optional, Sequence
import numpy as np

from lls_platform.core.data_structures import MCSEntry


def db_to_linear(x_db):
    return np.power(10.0, np.asarray(x_db, dtype=float) / 10.0)


def linear_to_db(x):
    x = np.asarray(x, dtype=float)
    return 10.0 * np.log10(np.maximum(x, 1e-30))


def sinr_to_achievable_se(sinr_db: float, gap_db: float = 2.5) -> float:
    sinr = 10 ** (float(sinr_db) / 10.0)
    gap = 10 ** (float(gap_db) / 10.0)
    return math.log2(1.0 + sinr / gap)


def harmonic_mean_sinr_db(layer_sinrs_db: Iterable[float]) -> float:
    sinr = db_to_linear(list(layer_sinrs_db))
    sinr = np.maximum(sinr, 1e-12)
    hm = len(sinr) / np.sum(1.0 / sinr)
    return float(linear_to_db(hm))


def _filter_mcs_table(mcs_table: List[MCSEntry], min_mcs: int = 0, max_mcs: int = 999,
                      min_code_rate: float = 0.0) -> List[MCSEntry]:
    out = [
        m for m in mcs_table
        if int(min_mcs) <= int(m.index) <= int(max_mcs)
        and float(m.code_rate) >= float(min_code_rate) - 1e-12
    ]
    if not out:
        raise ValueError(
            f"MCS 表过滤后为空: min_mcs={min_mcs}, max_mcs={max_mcs}, min_code_rate={min_code_rate}"
        )
    return out


def select_mcs_by_se(cw_se_eff: float, mcs_table: List[MCSEntry], min_mcs: int = 0, max_mcs: int = 999,
                     fixed_mcs: Optional[int] = None, min_code_rate: float = 0.0) -> MCSEntry:
    """Select the highest MCS whose SE does not exceed cw_se_eff.

    v2.7 adds ``min_code_rate`` to avoid Sionna LDPC5GEncoder's unsupported
    r<1/5 region.
    """
    if fixed_mcs is not None:
        for m in mcs_table:
            if int(m.index) == int(fixed_mcs):
                if float(m.code_rate) < float(min_code_rate) - 1e-12:
                    raise ValueError(
                        f"fixed_mcs={fixed_mcs} 的 code_rate={m.code_rate:.4f} < min_code_rate={min_code_rate:.4f}; "
                        "Sionna LDPC5GEncoder 不支持 r<1/5，请提高 fixed_mcs 或调整后端限制。"
                    )
                return m
        raise ValueError(f"fixed_mcs={fixed_mcs} 不在 MCS 表中")
    table = _filter_mcs_table(mcs_table, min_mcs=min_mcs, max_mcs=max_mcs, min_code_rate=min_code_rate)
    best = table[0]
    for m in table:
        if m.spectral_efficiency <= float(cw_se_eff) + 1e-12:
            best = m
    return best


def select_mcs_for_cw(layer_sinrs_db: Iterable[float], mcs_table: List[MCSEntry], gap_db: float = 2.5,
                      fixed_mcs: Optional[int] = None, min_code_rate: float = 0.0) -> MCSEntry:
    if fixed_mcs is not None:
        return select_mcs_by_se(0.0, mcs_table, fixed_mcs=fixed_mcs, min_code_rate=min_code_rate)
    sinr_eff_db = harmonic_mean_sinr_db(layer_sinrs_db)
    return select_mcs_by_se(sinr_to_achievable_se(sinr_eff_db, gap_db), mcs_table, min_code_rate=min_code_rate)


def select_per_layer_qm_by_se(layer_se_eff: Sequence[float], allowed_qm=(2,4,6,8), min_code_rate: float = 0.2) -> Dict[int, int]:
    out: Dict[int, int] = {}
    allowed = sorted(int(q) for q in allowed_qm)
    for i, se in enumerate(layer_se_eff):
        best = allowed[0]
        for qm in allowed:
            if float(qm) * float(min_code_rate) <= float(se) + 1e-12:
                best = qm
        out[int(i)] = int(best)
    return out


def select_per_layer_qm(layer_sinrs_db: Iterable[float], qpsk_max_db: float = 5.0,
                        qam16_max_db: float = 12.0, qam64_max_db: float = 18.0) -> Dict[int, int]:
    result: Dict[int, int] = {}
    for layer_idx, sinr_db in enumerate(layer_sinrs_db):
        if sinr_db < qpsk_max_db:
            result[layer_idx] = 2
        elif sinr_db < qam16_max_db:
            result[layer_idx] = 4
        elif sinr_db < qam64_max_db:
            result[layer_idx] = 6
        else:
            result[layer_idx] = 8
    return result


def quantize_code_rate_to_mcs_table(target_rate: float, mcs_table: List[MCSEntry], min_code_rate: float = 0.0) -> float:
    target_rate = min(max(float(target_rate), float(min_code_rate)), 0.95)
    rates = np.asarray([m.code_rate for m in mcs_table if float(m.code_rate) >= float(min_code_rate) - 1e-12], dtype=float)
    if rates.size == 0:
        raise ValueError(f"MCS 表中没有 code_rate >= {min_code_rate} 的条目")
    valid = rates[rates <= target_rate + 1e-12]
    if valid.size == 0:
        return float(np.min(rates))
    return float(np.max(valid))


def select_code_rate_for_per_layer_qm_by_se(layer_se_eff: Sequence[float], layer_qm: Dict[int, int],
                                            mcs_table: List[MCSEntry], min_code_rate: float = 0.0) -> float:
    candidates = []
    for layer_idx, se in enumerate(layer_se_eff):
        qm_i = int(layer_qm[layer_idx])
        candidates.append(float(se) / max(qm_i, 1))
    target = min(candidates) if candidates else float(min_code_rate)
    return quantize_code_rate_to_mcs_table(target, mcs_table, min_code_rate=min_code_rate)


def select_code_rate_for_per_layer_qm(layer_sinrs_db: Iterable[float], layer_qm: Dict[int, int],
                                      mcs_table: List[MCSEntry], gap_db: float = 2.5, min_code_rate: float = 0.0) -> float:
    ses = [sinr_to_achievable_se(float(s), gap_db) for s in layer_sinrs_db]
    return select_code_rate_for_per_layer_qm_by_se(ses, layer_qm, mcs_table, min_code_rate=min_code_rate)


def optimize_scheme2_rate_qm_by_se(layer_se_eff: Sequence[float], mcs_table: List[MCSEntry],
                                   allowed_qm=(2, 4, 6, 8), min_code_rate: float = 0.2,
                                   max_code_rate: float = 0.95) -> Dict[str, object]:
    """Joint optimization for Scheme 2 mixed-Qm + unified-rate selection.

    Maximize sum_l r*Q_l subject to r*Q_l <= SE_l for every layer,
    with r selected from the MCS table rates and Q_l from allowed_qm.
    """
    allowed = sorted({int(q) for q in allowed_qm})
    if not allowed:
        raise ValueError("allowed_qm must not be empty")

    se_arr = [float(x) for x in layer_se_eff]
    if not se_arr:
        raise ValueError("layer_se_eff must not be empty")

    candidate_rates = sorted({
        float(m.code_rate)
        for m in mcs_table
        if float(m.code_rate) >= float(min_code_rate) - 1e-12
        and float(m.code_rate) <= float(max_code_rate) + 1e-12
    })
    if not candidate_rates:
        raise ValueError(
            f"MCS table has no rates satisfying {min_code_rate} <= rate <= {max_code_rate}"
        )

    best = None
    for r in candidate_rates:
        qms = []
        feasible = True
        for se in se_arr:
            feasible_q = [q for q in allowed if float(r) * float(q) <= se + 1e-12]
            if not feasible_q:
                feasible = False
                break
            qms.append(max(feasible_q))
        if not feasible:
            continue

        obj = float(r) * float(sum(qms))
        margins = [float(se - float(r) * float(q)) for se, q in zip(se_arr, qms)]

        if best is None:
            better = True
        else:
            better = (
                obj > best["objective_se_sum"] + 1e-12
                or (
                    abs(obj - best["objective_se_sum"]) <= 1e-12
                    and float(r) > float(best["code_rate"]) + 1e-12
                )
                or (
                    abs(obj - best["objective_se_sum"]) <= 1e-12
                    and abs(float(r) - float(best["code_rate"])) <= 1e-12
                    and sum(qms) > sum(best["qms"])
                )
            )
        if better:
            best = {
                "code_rate": float(r),
                "qms": [int(q) for q in qms],
                "objective_se_sum": float(obj),
                "layer_margins": margins,
                "feasible": True,
            }

    if best is None:
        r = float(candidate_rates[0])
        qms = [int(allowed[0]) for _ in se_arr]
        best = {
            "code_rate": r,
            "qms": qms,
            "objective_se_sum": float(r) * float(sum(qms)),
            "layer_margins": [float(se - r * q) for se, q in zip(se_arr, qms)],
            "feasible": False,
        }

    return {
        "code_rate": float(best["code_rate"]),
        "layer_qm_local": {int(i): int(q) for i, q in enumerate(best["qms"])},
        "objective_se_sum": float(best["objective_se_sum"]),
        "layer_margins": [float(x) for x in best["layer_margins"]],
        "feasible": bool(best["feasible"]),
    }


def estimate_post_sinr_db_from_singular_values(singular_values: np.ndarray, snr_db: float, rank: int) -> np.ndarray:
    snr_linear = 10 ** (float(snr_db) / 10.0)
    s = np.sort(np.asarray(singular_values, dtype=float))[::-1]
    sinr_linear = snr_linear * (s[:int(rank)] ** 2) / float(rank)
    return linear_to_db(sinr_linear)
