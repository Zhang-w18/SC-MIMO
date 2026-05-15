from __future__ import annotations

from dataclasses import asdict
from typing import Dict, List, Union
import csv
import json
from pathlib import Path

from lls_platform.core.data_structures import SNRSummary


def summaries_to_rows(summaries: List[SNRSummary]) -> List[Dict]:
    rows = []
    for s in summaries:
        row = {
            "scheme": s.scheme_label,
            "snr_db": s.snr_db,
            "trials": s.trials,

            # 三种错误率口径。
            "scheme_bler": s.scheme_bler,      # 任意 CW 错误 -> scheme/slot 错误
            "scheme_cb_bler": s.cb_bler,       # 所有 CB 维度的错误率

            # goodput 口径。保留旧字段 total_throughput_bits_per_slot，
            # 但含义已经改为“成功 CB payload bits 的平均值”。
            "goodput_bits_per_slot": s.goodput_bits_per_slot,
            "total_throughput_bits_per_slot": s.total_throughput_bits_per_slot,
            "goodput_se_tf": s.goodput_se_tf,
            "goodput_se_layer_re": s.goodput_se_layer_re,
        }
        for cw in s.cw_stats:
            prefix = f"cw{cw.cw_index}"
            row[f"{prefix}_bler"] = cw.bler                 # CW/TB-BLER
            row[f"{prefix}_cb_bler"] = cw.cb_bler           # CB-BLER
            row[f"{prefix}_tb_size"] = cw.tb_size
            row[f"{prefix}_n_cbs"] = cw.n_cbs
            row[f"{prefix}_cb_trials"] = cw.cb_trials
            row[f"{prefix}_cb_errors"] = cw.cb_errors
            row[f"{prefix}_successful_payload_bits"] = cw.successful_payload_bits
            row[f"{prefix}_throughput_bits_per_slot"] = cw.throughput_bits_per_slot
        row.update({f"meta_{k}": v for k, v in s.metadata.items() if isinstance(v, (str, int, float, bool))})
        rows.append(row)
    return rows


def save_csv(summaries: List[SNRSummary], path: Union[str, Path]) -> None:
    rows = summaries_to_rows(summaries)
    if not rows:
        return
    # 不同 scheme 的 CW 数不同，字段并集作为表头。
    fieldnames = sorted(set().union(*(r.keys() for r in rows)))
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _summary_to_jsonable(s: SNRSummary) -> Dict:
    d = asdict(s)
    d["scheme_bler"] = s.scheme_bler
    d["scheme_cb_bler"] = s.cb_bler
    d["goodput_bits_per_slot"] = s.goodput_bits_per_slot
    d["goodput_se_tf"] = s.goodput_se_tf
    d["goodput_se_layer_re"] = s.goodput_se_layer_re
    for cw_d, cw in zip(d["cw_stats"], s.cw_stats):
        cw_d["bler"] = cw.bler
        cw_d["cb_bler"] = cw.cb_bler
        cw_d["throughput_bits_per_slot"] = cw.throughput_bits_per_slot
    return d


def save_json(summaries: List[SNRSummary], path: Union[str, Path]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump([_summary_to_jsonable(s) for s in summaries], f, indent=2, ensure_ascii=False)
