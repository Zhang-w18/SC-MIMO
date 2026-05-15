#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
postprocess_curves.py  (auto multi-MCS / adaptive-MCS version)

功能：
1. 自动识别输入 CSV：
   - --results-csv / --merged-csv 指定的文件优先；
   - 否则在 --output-root 下优先找 merged_results.csv；找不到再找 results.csv。

2. 自动识别 multi-MCS 和 adaptive MCS：
   - multi-MCS：CSV 中存在多个固定 mcs 值；
   - adaptive：meta_adaptive_mcs_enabled=True，或 meta_fixed_mcs=-1 / meta_cw0_mcs=-1。
   adaptive 模式下把 mcs 统一标记为 "adaptive"，避免导出文件出现 mcs-1。

3. 导出画图原始数据：
   - all_curves_long/*.csv：长表，一行一个散点；
   - curves_by_mcs/*.csv：每个 MCS/adaptive 下的宽表，一列一个 scheme；
   - envelope/*.csv：每个 SNR 下 goodput 最大的 winner；
   - 每个 .png 图旁边同步输出同名 .csv，记录该图上实际绘制的数据点。

4. 画图：
   - bars_by_mcs_snr：每个 MCS/adaptive、每个 SNR 的柱状图；
   - line_plots_by_mcs：每个 MCS/adaptive 的曲线图；
   - envelope_plots：goodput 包络曲线；
   - winner_count_bars：winner 次数统计。

典型用法：

# run.py 单次输出，目录里只有 results.csv：
python tools/postprocess_curves.py \
  --output-root results_v26_rank8_cdl_adaptive/sim_20260430_023218 \
  --rank 8 \
  --channel CDL

# run_multi_mcs.py 输出，目录里有 merged_results.csv：
python tools/postprocess_curves.py \
  --output-root results_v27_rank8_awgn_multi_mcs \
  --rank 8 \
  --channel AWGN

# 直接指定输入 CSV：
python tools/postprocess_curves.py \
  --results-csv path/to/results.csv \
  --export-dir path/to/curve_exports \
  --rank 8 \
  --channel CDL
"""

from __future__ import annotations

import argparse
import csv
import math
import re
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Tuple


def _import_matplotlib():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    return plt


def safe_float(x, default: float = 0.0) -> float:
    try:
        if x is None:
            return default
        s = str(x).strip()
        if s == "" or s.lower() in ("nan", "none"):
            return default
        return float(s)
    except Exception:
        return default


def safe_str(x, default: str = "") -> str:
    if x is None:
        return default
    s = str(x).strip()
    return s if s else default


def truthy(x) -> bool:
    s = str(x).strip().lower()
    return s in ("1", "true", "yes", "y", "on")


def safe_int_str(x, default: str = "") -> str:
    s = safe_str(x, default)
    if not s:
        return default
    try:
        return str(int(float(s)))
    except Exception:
        return s


def sanitize_filename(text: str) -> str:
    keep = []
    for ch in str(text):
        if ch.isalnum() or ch in ("-", "_", ".", "+"):
            keep.append(ch)
        else:
            keep.append("_")
    s = "".join(keep).strip("_")
    return s or "unknown"


def read_csv_rows(path: Path) -> List[Dict[str, str]]:
    with open(path, "r", encoding="utf-8", newline="") as f:
        return [dict(r) for r in csv.DictReader(f)]


def write_csv(path: Path, rows: List[Dict[str, object]], fieldnames: Optional[List[str]] = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fieldnames is None:
        fieldnames = []
        for r in rows:
            for k in r.keys():
                if k not in fieldnames:
                    fieldnames.append(k)
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow(r)


def row_scheme(row: Mapping[str, str]) -> str:
    return safe_str(row.get("scheme"), "unknown_scheme")


def is_adaptive_row(row: Mapping[str, str]) -> bool:
    if truthy(row.get("meta_adaptive_mcs_enabled", "")):
        return True
    fixed = safe_int_str(row.get("meta_fixed_mcs"), "")
    cw0 = safe_int_str(row.get("meta_cw0_mcs"), "")
    # v2.6/v2.7 adaptive usually uses -1 for fixed_mcs or mixed MCS.
    return fixed == "-1" or cw0 == "-1"


def row_mcs(row: Mapping[str, str]) -> str:
    """Return a grouping label for fixed-MCS/multi-MCS/adaptive cases."""
    if is_adaptive_row(row):
        return "adaptive"

    # run_multi_mcs may add a plain mcs column; prefer it if present.
    for key in ("mcs", "meta_fixed_mcs", "meta_cw0_mcs"):
        val = safe_int_str(row.get(key), "")
        if val not in ("", "-1"):
            return val
    return "unknown"


def row_snr(row: Mapping[str, str]) -> float:
    return safe_float(row.get("snr_db"), 0.0)


def infer_rank(rows: List[Dict[str, str]], output_root: Optional[Path]) -> str:
    for r in rows:
        for key in ("rank", "meta_rank"):
            val = safe_int_str(r.get(key), "")
            if val:
                return val
    if output_root is not None:
        m = re.search(r"rank(\d+)", str(output_root), flags=re.IGNORECASE)
        if m:
            return m.group(1)
    return "unknown"




def row_rank_value(row: Mapping[str, str], fallback: str = "unknown") -> str:
    """Return rank value for a result/CW/layer row.

    Priority:
    1. explicit rank/meta_rank column
    2. rank encoded in source_csv path, such as runs/rank_8/ or rank_8_layer...
    3. fallback
    """
    for key in ("rank", "meta_rank"):
        val = safe_int_str(row.get(key), "")
        if val:
            return val
    src = safe_str(row.get("source_csv"), "")
    if src:
        m = re.search(r"rank[_-]?(\d+)", src, flags=re.IGNORECASE)
        if m:
            return m.group(1)
    return str(fallback)


def available_ranks_from_rows(*row_lists: List[Dict[str, str]]) -> List[str]:
    ranks = set()
    for rows_i in row_lists:
        for r in rows_i or []:
            rk = row_rank_value(r, "")
            if rk and rk.lower() not in ("unknown", "multi", "all"):
                ranks.add(rk)
    if not ranks:
        return []
    return sorted(ranks, key=lambda x: (0, int(x)) if str(x).isdigit() else (1, str(x)))


def filter_rows_for_rank(rows: List[Dict[str, str]], rank: str) -> List[Dict[str, str]]:
    rank = str(rank)
    if rank.lower() in ("multi", "all", "*"):
        return list(rows)
    return [r for r in rows if row_rank_value(r, rank) == rank]


def source_dirs_from_result_rows(rows: List[Dict[str, str]]) -> List[Path]:
    """Return sim directories explicitly used by merged_results.csv.

    run_multi_rank.py writes source_results_csv. Using it avoids accidentally
    reading stale sim_* folders from previous runs under the same output_root.
    """
    dirs = []
    seen = set()
    for r in rows:
        src = safe_str(r.get("source_results_csv"), "") or safe_str(r.get("source_csv"), "")
        if not src:
            continue
        p = Path(src)
        if p.name == "results.csv":
            d = p.parent
        else:
            d = p.parent
        key = str(d)
        if key not in seen:
            dirs.append(d)
            seen.add(key)
    return dirs

def infer_channel(rows: List[Dict[str, str]], output_root: Optional[Path]) -> str:
    for r in rows:
        for key in ("channel", "meta_channel_model"):
            val = safe_str(r.get(key), "")
            if val:
                return val
    if output_root is not None:
        s = str(output_root).lower()
        if "awgn" in s:
            return "AWGN"
        if "rayleigh" in s:
            return "Rayleigh"
        if "cdl" in s:
            return "CDL"
    return "unknown"


def unique_sorted(values: Iterable[str]) -> List[str]:
    vals = [v for v in values if safe_str(v, "")]

    def key_fn(x: str):
        if x == "adaptive":
            return (-1, -1.0, x)
        try:
            return (0, float(x), x)
        except Exception:
            return (1, math.inf, x)

    return sorted(set(vals), key=key_fn)


def filter_rows_by_mcs(rows: List[Dict[str, str]], mcs: str) -> List[Dict[str, str]]:
    return [r for r in rows if row_mcs(r) == mcs]


def metric_label(metric: str) -> Tuple[str, str]:
    table = {
        "scheme_bler": ("Scheme BLER", "scheme_bler"),
        "scheme_cb_bler": ("CB-BLER", "cb_bler"),
        "goodput_bits_per_slot": ("Goodput / bits per slot", "goodput_bits_per_slot"),
        "total_throughput_bits_per_slot": ("Goodput / bits per slot", "goodput_bits_per_slot"),
        "goodput_se_tf": ("Goodput SE / bits per TF-RE", "goodput_se_tf"),
        "goodput_se_layer_re": ("Goodput SE / bits per layer-RE", "goodput_se_layer_re"),
    }
    return table.get(metric, (metric, sanitize_filename(metric)))


def get_metric(row: Mapping[str, str], metric: str) -> float:
    # Compatibility: some CSVs use total_throughput_bits_per_slot instead of goodput_bits_per_slot.
    if metric == "goodput_bits_per_slot":
        if "goodput_bits_per_slot" in row and safe_str(row.get("goodput_bits_per_slot"), ""):
            return safe_float(row.get("goodput_bits_per_slot"))
        return safe_float(row.get("total_throughput_bits_per_slot"))
    return safe_float(row.get(metric))


def pivot_metric_by_mcs(rows: List[Dict[str, str]], mcs: str, metric: str, rank: str, channel: str) -> Tuple[List[Dict[str, object]], List[str]]:
    rs = filter_rows_by_mcs(rows, mcs)
    schemes = unique_sorted(row_scheme(r) for r in rs)
    snrs = sorted(set(row_snr(r) for r in rs))

    index: Dict[Tuple[float, str], float] = {}
    for r in rs:
        index[(row_snr(r), row_scheme(r))] = get_metric(r, metric)

    out_rows: List[Dict[str, object]] = []
    for snr in snrs:
        row: Dict[str, object] = {
            "rank": rank,
            "channel": channel,
            "mcs": mcs,
            "snr_db": snr,
        }
        for scheme in schemes:
            row[scheme] = index.get((snr, scheme), "")
        out_rows.append(row)

    fields = ["rank", "channel", "mcs", "snr_db"] + schemes
    return out_rows, fields


def export_curve_csvs(rows: List[Dict[str, str]], out_dir: Path, rank: str, channel: str, metrics: List[str]) -> None:
    mcs_values = unique_sorted(row_mcs(r) for r in rows)
    for mcs in mcs_values:
        for metric in metrics:
            _ylabel, short = metric_label(metric)
            table, fields = pivot_metric_by_mcs(rows, mcs, metric, rank, channel)
            path = out_dir / "curves_by_mcs" / f"rank{rank}_{sanitize_filename(channel)}_mcs{sanitize_filename(mcs)}_{short}_curves.csv"
            write_csv(path, table, fields)


def export_long_csv(rows: List[Dict[str, str]], out_dir: Path, rank: str, channel: str) -> None:
    fields = [
        "rank",
        "channel",
        "mcs",
        "scheme",
        "snr_db",
        "scheme_bler",
        "scheme_cb_bler",
        "goodput_bits_per_slot",
        "goodput_se_tf",
        "goodput_se_layer_re",
        "trials",
        "num_cws",
        "partition",
    ]
    out_rows: List[Dict[str, object]] = []
    for r in rows:
        out_rows.append({
            "rank": rank,
            "channel": channel,
            "mcs": row_mcs(r),
            "scheme": row_scheme(r),
            "snr_db": row_snr(r),
            "scheme_bler": get_metric(r, "scheme_bler"),
            "scheme_cb_bler": get_metric(r, "scheme_cb_bler"),
            "goodput_bits_per_slot": get_metric(r, "goodput_bits_per_slot"),
            "goodput_se_tf": get_metric(r, "goodput_se_tf"),
            "goodput_se_layer_re": get_metric(r, "goodput_se_layer_re"),
            "trials": safe_int_str(r.get("trials"), ""),
            "num_cws": safe_int_str(r.get("meta_num_cws"), ""),
            "partition": safe_str(r.get("meta_partition"), ""),
        })

    path = out_dir / "all_curves_long" / f"rank{rank}_{sanitize_filename(channel)}_all_curves_long.csv"
    write_csv(path, out_rows, fields)


def export_goodput_envelope(rows: List[Dict[str, str]], out_dir: Path, rank: str, channel: str, score_metric: str = "goodput_se_tf") -> List[Dict[str, object]]:
    mcs_values = unique_sorted(row_mcs(r) for r in rows)
    envelope_rows: List[Dict[str, object]] = []

    for mcs in mcs_values:
        rs_mcs = filter_rows_by_mcs(rows, mcs)
        snrs = sorted(set(row_snr(r) for r in rs_mcs))

        for snr in snrs:
            candidates = [r for r in rs_mcs if row_snr(r) == snr]
            if not candidates:
                continue

            def score(r):
                return (
                    get_metric(r, score_metric),
                    -get_metric(r, "scheme_cb_bler"),
                    -get_metric(r, "scheme_bler"),
                    row_scheme(r),
                )

            winner = sorted(candidates, key=score, reverse=True)[0]
            envelope_rows.append({
                "rank": rank,
                "channel": channel,
                "mcs": mcs,
                "snr_db": snr,
                "winner_scheme": row_scheme(winner),
                "winner_goodput_bits_per_slot": get_metric(winner, "goodput_bits_per_slot"),
                "winner_goodput_se_tf": get_metric(winner, "goodput_se_tf"),
                "winner_goodput_se_layer_re": get_metric(winner, "goodput_se_layer_re"),
                "winner_scheme_bler": get_metric(winner, "scheme_bler"),
                "winner_scheme_cb_bler": get_metric(winner, "scheme_cb_bler"),
                "trials": safe_int_str(winner.get("trials"), ""),
            })

    fields = [
        "rank", "channel", "mcs", "snr_db", "winner_scheme",
        "winner_goodput_bits_per_slot", "winner_goodput_se_tf", "winner_goodput_se_layer_re",
        "winner_scheme_bler", "winner_scheme_cb_bler", "trials",
    ]
    path_all = out_dir / "envelope" / f"rank{rank}_{sanitize_filename(channel)}_goodput_envelope_all_mcs.csv"
    write_csv(path_all, envelope_rows, fields)

    for mcs in mcs_values:
        mcs_rows = [r for r in envelope_rows if str(r["mcs"]) == mcs]
        path = out_dir / "envelope" / f"rank{rank}_{sanitize_filename(channel)}_mcs{sanitize_filename(mcs)}_goodput_envelope.csv"
        write_csv(path, mcs_rows, fields)
    return envelope_rows


def plot_metric_lines_by_mcs(rows: List[Dict[str, str]], out_dir: Path, rank: str, channel: str, metrics: List[str]) -> None:
    plt = _import_matplotlib()
    mcs_values = unique_sorted(row_mcs(r) for r in rows)

    for mcs in mcs_values:
        rs_mcs = filter_rows_by_mcs(rows, mcs)
        schemes = unique_sorted(row_scheme(r) for r in rs_mcs)
        for metric in metrics:
            ylabel, short = metric_label(metric)
            fig, ax = plt.subplots(figsize=(10, 5.5))
            any_line = False
            plot_csv_rows: List[Dict[str, object]] = []
            for scheme in schemes:
                rs = [r for r in rs_mcs if row_scheme(r) == scheme]
                rs = sorted(rs, key=row_snr)
                if not rs:
                    continue
                xs = [row_snr(r) for r in rs]
                raw_ys = [get_metric(r, metric) for r in rs]
                if metric in ("scheme_bler", "scheme_cb_bler"):
                    ys = [max(y, 1e-5) for y in raw_ys]
                    ax.semilogy(xs, ys, marker="o", linewidth=1.2, label=scheme)
                else:
                    ys = list(raw_ys)
                    ax.plot(xs, ys, marker="o", linewidth=1.2, label=scheme)
                for x, raw_y, y in zip(xs, raw_ys, ys):
                    plot_csv_rows.append({
                        "rank": rank,
                        "channel": channel,
                        "mcs": mcs,
                        "metric": metric,
                        "scheme": scheme,
                        "snr_db": float(x),
                        "y": float(y),
                        "raw_y": float(raw_y),
                        "note": "y is clipped to 1e-5 for log-scale BLER plots" if metric in ("scheme_bler", "scheme_cb_bler") else "",
                    })
                any_line = True
            if not any_line:
                plt.close(fig)
                continue
            ax.set_xlabel("SNR / dB")
            ax.set_ylabel(ylabel)
            ax.set_title(f"{ylabel}, rank {rank}, {channel}, MCS {mcs}")
            ax.grid(True, which="both", linestyle="--", linewidth=0.5)
            ax.legend(fontsize=7)
            fig.tight_layout()
            path = out_dir / "line_plots_by_mcs" / f"rank{rank}_{sanitize_filename(channel)}_mcs{sanitize_filename(mcs)}_{short}_lines.png"
            path.parent.mkdir(parents=True, exist_ok=True)
            fig.savefig(path, dpi=200)
            if plot_csv_rows:
                write_csv(path.with_suffix(".csv"), plot_csv_rows, fieldnames=["rank", "channel", "mcs", "metric", "scheme", "snr_db", "y", "raw_y", "note"])
            plt.close(fig)


def plot_goodput_envelope(envelope_rows: List[Dict[str, object]], out_dir: Path, rank: str, channel: str) -> None:
    if not envelope_rows:
        return
    plt = _import_matplotlib()
    mcs_values = unique_sorted(str(r["mcs"]) for r in envelope_rows)
    for mcs in mcs_values:
        rs = [r for r in envelope_rows if str(r["mcs"]) == mcs]
        rs = sorted(rs, key=lambda r: float(r["snr_db"]))
        xs = [float(r["snr_db"]) for r in rs]
        ys = [float(r["winner_goodput_se_tf"]) for r in rs]
        labels = [str(r["winner_scheme"]) for r in rs]
        fig, ax = plt.subplots(figsize=(9, 5))
        ax.plot(xs, ys, marker="o", linewidth=1.5)
        for x, y, lab in zip(xs, ys, labels):
            ax.annotate(lab, (x, y), textcoords="offset points", xytext=(0, 6), ha="center", fontsize=7, rotation=30)
        ax.set_xlabel("SNR / dB")
        ax.set_ylabel("Envelope goodput SE / bits per TF-RE")
        ax.set_title(f"Goodput-SE envelope, rank {rank}, {channel}, MCS {mcs}")
        ax.grid(True, linestyle="--", linewidth=0.5)
        fig.tight_layout()
        path = out_dir / "envelope_plots" / f"rank{rank}_{sanitize_filename(channel)}_mcs{sanitize_filename(mcs)}_goodput_se_envelope.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(path, dpi=200)
        plot_csv_rows = [
            {
                "rank": rank,
                "channel": channel,
                "mcs": mcs,
                "snr_db": float(x),
                "winner_goodput_se_tf": float(y),
                "winner_scheme": str(lab),
            }
            for x, y, lab in zip(xs, ys, labels)
        ]
        write_csv(path.with_suffix(".csv"), plot_csv_rows, fieldnames=["rank", "channel", "mcs", "snr_db", "winner_goodput_se_tf", "winner_scheme"])
        plt.close(fig)


def plot_winner_counts(envelope_rows: List[Dict[str, object]], out_dir: Path, rank: str, channel: str) -> None:
    if not envelope_rows:
        return
    plt = _import_matplotlib()
    mcs_values = unique_sorted(str(r["mcs"]) for r in envelope_rows)
    for mcs in mcs_values:
        rs = [r for r in envelope_rows if str(r["mcs"]) == mcs]
        counts: Dict[str, int] = defaultdict(int)
        for r in rs:
            counts[str(r["winner_scheme"])] += 1
        labels = sorted(counts.keys())
        vals = [counts[k] for k in labels]
        fig, ax = plt.subplots(figsize=(10, 5))
        ax.bar(range(len(labels)), vals)
        ax.set_xticks(range(len(labels)))
        ax.set_xticklabels(labels, rotation=45, ha="right", fontsize=8)
        ax.set_ylabel("Winner count over SNR points")
        ax.set_title(f"Winner count, rank {rank}, {channel}, MCS {mcs}")
        ax.grid(True, axis="y", linestyle="--", linewidth=0.5)
        fig.tight_layout()
        path = out_dir / "winner_count_bars" / f"rank{rank}_{sanitize_filename(channel)}_mcs{sanitize_filename(mcs)}_winner_count_bar.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(path, dpi=200)
        plot_csv_rows = [
            {
                "rank": rank,
                "channel": channel,
                "mcs": mcs,
                "scheme": str(label),
                "winner_count": int(val),
                "x_index": int(i),
            }
            for i, (label, val) in enumerate(zip(labels, vals))
        ]
        write_csv(path.with_suffix(".csv"), plot_csv_rows, fieldnames=["rank", "channel", "mcs", "x_index", "scheme", "winner_count"])
        plt.close(fig)


def plot_bar_by_mcs_snr(rows: List[Dict[str, str]], out_dir: Path, rank: str, channel: str, metrics: List[str], max_snr_plots_per_mcs: int = 999, sort_by_metric: bool = True) -> None:
    plt = _import_matplotlib()
    mcs_values = unique_sorted(row_mcs(r) for r in rows)

    for mcs in mcs_values:
        rs_mcs = filter_rows_by_mcs(rows, mcs)
        snrs = sorted(set(row_snr(r) for r in rs_mcs))
        if len(snrs) > max_snr_plots_per_mcs:
            snrs = snrs[:max_snr_plots_per_mcs]

        for snr in snrs:
            base_rs = [r for r in rs_mcs if row_snr(r) == snr]
            for metric in metrics:
                ylabel, short = metric_label(metric)
                if sort_by_metric:
                    reverse = metric not in ("scheme_bler", "scheme_cb_bler")
                    rs = sorted(base_rs, key=lambda r: get_metric(r, metric), reverse=reverse)
                else:
                    rs = sorted(base_rs, key=lambda r: row_scheme(r))
                labels = [row_scheme(r) for r in rs]
                vals = [get_metric(r, metric) for r in rs]

                fig, ax = plt.subplots(figsize=(12, 5.5))
                ax.bar(range(len(labels)), vals)
                ax.set_xticks(range(len(labels)))
                ax.set_xticklabels(labels, rotation=45, ha="right", fontsize=8)
                ax.set_ylabel(ylabel)
                ax.set_title(f"{ylabel} by scheme, rank {rank}, {channel}, MCS {mcs}, SNR {snr:g} dB")
                ax.grid(True, axis="y", linestyle="--", linewidth=0.5)
                fig.tight_layout()

                snr_str = ("%g" % snr).replace("-", "m").replace(".", "p")
                path = out_dir / "bars_by_mcs_snr" / f"rank{rank}_{sanitize_filename(channel)}_mcs{sanitize_filename(mcs)}_snr{snr_str}_{short}_bar.png"
                path.parent.mkdir(parents=True, exist_ok=True)
                fig.savefig(path, dpi=200)
                plot_csv_rows = [
                    {
                        "rank": rank,
                        "channel": channel,
                        "mcs": mcs,
                        "snr_db": float(snr),
                        "metric": metric,
                        "scheme": str(label),
                        "value": float(val),
                        "x_index": int(i),
                    }
                    for i, (label, val) in enumerate(zip(labels, vals))
                ]
                write_csv(path.with_suffix(".csv"), plot_csv_rows, fieldnames=["rank", "channel", "mcs", "snr_db", "metric", "x_index", "scheme", "value"])
                plt.close(fig)



def _empirical_cdf(values: List[float]) -> Tuple[List[float], List[float]]:
    xs = sorted(float(v) for v in values if math.isfinite(float(v)))
    n = len(xs)
    if n == 0:
        return [], []
    ys = [(i + 1) / n for i in range(n)]
    return xs, ys


def _plot_cdf_series(series: Mapping[str, List[float]], title: str, xlabel: str, out_png: Path,
                     out_csv: Optional[Path] = None, thin_grid: bool = True) -> None:
    plt = _import_matplotlib()
    fig, ax = plt.subplots(figsize=(8.8, 5.6))
    csv_rows: List[Dict[str, object]] = []
    for label, vals in series.items():
        xs, ys = _empirical_cdf(vals)
        if not xs:
            continue
        ax.plot(xs, ys, linewidth=1.2, label=str(label))
        if out_csv is not None:
            for x, y in zip(xs, ys):
                csv_rows.append({"series": str(label), "x": float(x), "cdf": float(y)})
    ax.set_xlabel(xlabel)
    ax.set_ylabel("CDF")
    ax.set_title(title)
    if thin_grid:
        ax.grid(True, which="major", linestyle="--", linewidth=0.35, alpha=0.65)
        ax.grid(True, which="minor", linestyle=":", linewidth=0.25, alpha=0.45)
        try:
            ax.minorticks_on()
        except Exception:
            pass
    else:
        ax.grid(True, linestyle="--", linewidth=0.5)
    if len(series) <= 16:
        ax.legend(fontsize=7)
    else:
        ax.legend(fontsize=6, ncol=2)
    fig.tight_layout()
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, dpi=220)
    plt.close(fig)
    if out_csv is not None and csv_rows:
        write_csv(out_csv, csv_rows, fieldnames=["series", "x", "cdf"])


def _unique_existing_paths(paths: List[Path]) -> List[Path]:
    seen = set()
    out = []
    for p in paths:
        if not p.exists():
            continue
        try:
            key = str(p.resolve())
        except Exception:
            key = str(p)
        if key not in seen:
            out.append(p)
            seen.add(key)
    return out


def discover_cw_trial_csvs(output_root: Path, explicit: Optional[Path] = None,
                            result_rows: Optional[List[Dict[str, str]]] = None) -> List[Path]:
    if explicit is not None:
        return [explicit] if explicit.exists() else []

    # Prefer the exact sim directories referenced by merged_results.csv.
    candidates: List[Path] = []
    if result_rows:
        for d in source_dirs_from_result_rows(result_rows):
            candidates.append(d / "cw_trial_link_adaptation_records.csv")
        candidates = _unique_existing_paths(candidates)
        if candidates:
            return candidates

    # Fallbacks for single-run or older output layouts.
    candidates = [output_root / "cw_trial_link_adaptation_records.csv"]
    candidates.extend(sorted(output_root.glob("runs/rank_*/sim_*/cw_trial_link_adaptation_records.csv")))
    candidates.extend(sorted(output_root.glob("sim_*/cw_trial_link_adaptation_records.csv")))
    return _unique_existing_paths(candidates)


def discover_layer_sinr_csvs(output_root: Path, explicit: Optional[Path] = None,
                              result_rows: Optional[List[Dict[str, str]]] = None) -> List[Path]:
    if explicit is not None:
        return [explicit] if explicit.exists() else []

    # Prefer the exact sim directories referenced by merged_results.csv.
    # This avoids duplicating samples by reading both the original sim files and
    # the copied layer_post_sinr_samples_by_rank files.
    candidates: List[Path] = []
    if result_rows:
        for d in source_dirs_from_result_rows(result_rows):
            candidates.append(d / "layer_post_sinr_samples.csv")
        candidates = _unique_existing_paths(candidates)
        if candidates:
            return candidates

    # If multi-rank copied files exist, prefer them over the original sim files
    # to avoid duplicate CDF samples.
    by_rank = _unique_existing_paths(sorted(output_root.glob("layer_post_sinr_samples_by_rank/rank_*_layer_post_sinr_samples.csv")))
    if by_rank:
        return by_rank

    candidates = [output_root / "layer_post_sinr_samples.csv"]
    candidates.extend(sorted(output_root.glob("runs/rank_*/sim_*/layer_post_sinr_samples.csv")))
    candidates.extend(sorted(output_root.glob("sim_*/layer_post_sinr_samples.csv")))
    return _unique_existing_paths(candidates)


def read_many_csv(paths: List[Path]) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    for p in paths:
        if not p.exists():
            continue
        for r in read_csv_rows(p):
            r.setdefault("source_csv", str(p))
            rows.append(r)
    return rows


def filter_measurement_phase_rows(cw_rows: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """Use only measurement-phase rows for statistics/CDF when OLLA records exist."""
    if not cw_rows:
        return []
    has_phase = any("olla_phase" in r and safe_str(r.get("olla_phase"), "") != "" for r in cw_rows)
    if not has_phase:
        return list(cw_rows)
    out = [r for r in cw_rows if safe_str(r.get("olla_phase"), "measure").lower() == "measure"]
    return out or list(cw_rows)


def plot_olla_trace_plots(cw_rows: List[Dict[str, str]], export_dir: Path, rank: str, channel: str) -> None:
    """Plot OLLA traces: selected MCS, code-rate, offset and EWMA CB error.

    Rows include both warmup and measurement phases. A same-name CSV is written
    next to each figure with the exact plotted points.
    """
    if not cw_rows:
        return
    if not any(int(safe_float(r.get("olla_enabled"), 0.0)) == 1 for r in cw_rows):
        return

    trace_specs = [
        ("selected_mcs", "Selected MCS index", "olla_mcs_trace_by_scheme_snr", True),
        ("code_rate", "Code rate", "olla_code_rate_trace_by_scheme_snr", False),
        ("olla_offset_db_after", "OLLA offset after update / dB", "olla_offset_trace_by_scheme_snr", False),
        ("olla_cb_error_ewma", "EWMA CB error rate", "olla_cb_error_ewma_by_scheme_snr", False),
    ]
    snrs = sorted(set(safe_float(r.get("snr_db"), 0.0) for r in cw_rows))
    schemes = unique_sorted(safe_str(r.get("scheme"), "unknown") for r in cw_rows)
    for metric, ylabel, subdir, skip_negative in trace_specs:
        if not any(metric in r and math.isfinite(safe_float(r.get(metric), float("nan"))) for r in cw_rows):
            continue
        for snr in snrs:
            for scheme in schemes:
                rs = [r for r in cw_rows if abs(safe_float(r.get("snr_db"), 0.0) - snr) < 1e-9 and safe_str(r.get("scheme"), "") == scheme]
                if not rs:
                    continue
                series: Dict[str, List[Tuple[float, float, str]]] = defaultdict(list)
                for r in rs:
                    val = safe_float(r.get(metric), float("nan"))
                    if not math.isfinite(val):
                        continue
                    if skip_negative and val < 0:
                        continue
                    trial = safe_float(r.get("trial_id"), float("nan"))
                    if not math.isfinite(trial):
                        continue
                    cw = safe_int_str(r.get("cw_index"), "?")
                    phase = safe_str(r.get("olla_phase"), "")
                    series[f"CW{cw}"].append((trial, val, phase))
                if not series:
                    continue
                fig, ax = plt.subplots(figsize=(8.8, 4.8))
                csv_rows = []
                for label, pts in sorted(series.items()):
                    pts = sorted(pts, key=lambda x: x[0])
                    xs = [p[0] for p in pts]
                    ys = [p[1] for p in pts]
                    ax.plot(xs, ys, linewidth=1.05, label=label)
                    for x, y, phase in pts:
                        csv_rows.append({"series": label, "trial_id": x, metric: y, "phase": phase})
                ax.set_xlabel("Trial index (warmup + measurement)")
                ax.set_ylabel(ylabel)
                ax.set_title(f"{ylabel} trace, {scheme}, rank {rank}, {channel}, SNR {snr:g} dB")
                ax.grid(True, which="major", linestyle="--", linewidth=0.35, alpha=0.65)
                ax.grid(True, which="minor", linestyle=":", linewidth=0.25, alpha=0.45)
                try:
                    ax.minorticks_on()
                except Exception:
                    pass
                ax.legend(fontsize=7)
                fig.tight_layout()
                snr_str = ("%g" % snr).replace("-", "m").replace(".", "p")
                stem = f"rank{rank}_{sanitize_filename(channel)}_snr{snr_str}_{sanitize_filename(scheme)}_{sanitize_filename(metric)}_trace"
                out_dir = export_dir / subdir / sanitize_filename(scheme)
                out_dir.mkdir(parents=True, exist_ok=True)
                fig.savefig(out_dir / f"{stem}.png", dpi=220)
                plt.close(fig)
                if csv_rows:
                    write_csv(out_dir / f"{stem}.csv", csv_rows, fieldnames=["series", "trial_id", metric, "phase"])


def plot_loss_cdfs(cw_rows: List[Dict[str, str]], export_dir: Path, rank: str, channel: str) -> None:
    if not cw_rows:
        return
    out_dir = export_dir / "cdf_losses_by_snr"
    snrs = sorted(set(safe_float(r.get("snr_db"), 0.0) for r in cw_rows))
    metrics = [
        ("mcs_quantization_loss_se_clipped", "MCS quantization/capping loss SE (target - MCS, clipped)"),
        ("coding_failure_loss_se_clipped", "Coding/CB-failure loss SE (loaded - successful, clipped)"),
        ("target_se_total", "CW target theoretical SE before MCS"),
        ("mcs_se_total", "CW MCS-loaded SE before TBS rounding"),
        ("goodput_se_tf_trial", "CW trial goodput SE"),
    ]
    for snr in snrs:
        rs = [r for r in cw_rows if abs(safe_float(r.get("snr_db"), 0.0) - snr) < 1e-9]
        for metric, xlabel in metrics:
            series: Dict[str, List[float]] = defaultdict(list)
            for r in rs:
                scheme = safe_str(r.get("scheme"), "unknown")
                val = safe_float(r.get(metric), float("nan"))
                if math.isfinite(val):
                    series[scheme].append(val)
            if not series:
                continue
            snr_str = ("%g" % snr).replace("-", "m").replace(".", "p")
            stem = f"rank{rank}_{sanitize_filename(channel)}_snr{snr_str}_{sanitize_filename(metric)}_cdf"
            _plot_cdf_series(
                series,
                title=f"{xlabel} CDF, rank {rank}, {channel}, SNR {snr:g} dB",
                xlabel=xlabel,
                out_png=out_dir / metric / f"{stem}.png",
                out_csv=out_dir / metric / f"{stem}.csv",
            )


def plot_mcs_cdfs(cw_rows: List[Dict[str, str]], export_dir: Path, rank: str, channel: str) -> None:
    if not cw_rows:
        return
    out_dir = export_dir / "cdf_mcs_by_scheme_cw"
    snrs = sorted(set(safe_float(r.get("snr_db"), 0.0) for r in cw_rows))
    schemes = unique_sorted(safe_str(r.get("scheme"), "unknown") for r in cw_rows)
    for snr in snrs:
        for scheme in schemes:
            rs = [r for r in cw_rows if abs(safe_float(r.get("snr_db"), 0.0) - snr) < 1e-9 and safe_str(r.get("scheme"), "unknown") == scheme]
            if not rs:
                continue
            series: Dict[str, List[float]] = defaultdict(list)
            for r in rs:
                mcs = safe_float(r.get("selected_mcs"), -1.0)
                if mcs < 0:
                    # Mixed-Qm Scheme2 has no single MCS index; skip for this CDF.
                    continue
                cw = safe_int_str(r.get("cw_index"), "?")
                series[f"CW{cw}"] .append(mcs)
            if not series:
                continue
            snr_str = ("%g" % snr).replace("-", "m").replace(".", "p")
            stem = f"rank{rank}_{sanitize_filename(channel)}_snr{snr_str}_{sanitize_filename(scheme)}_mcs_cdf"
            _plot_cdf_series(
                series,
                title=f"Selected MCS CDF by CW, {scheme}, rank {rank}, {channel}, SNR {snr:g} dB",
                xlabel="Selected MCS index",
                out_png=out_dir / sanitize_filename(scheme) / f"{stem}.png",
                out_csv=out_dir / sanitize_filename(scheme) / f"{stem}.csv",
            )


def plot_cw_se_triplet_cdfs(cw_rows: List[Dict[str, str]], export_dir: Path, rank: str, channel: str) -> None:
    """Plot per-CW SE decomposition CDFs.

    This keeps the existing selected-MCS-index CDF untouched and adds a more
    interpretable figure for each (SNR, scheme, CW): three CDF curves on the
    same axis, all using spectral-efficiency units.

    Curves:
      - target_se_total: theoretical CW SE before MCS quantization.
      - mcs_se_total: CW SE after MCS/Qm/r selection, before TBS/CB success.
      - goodput_se_tf_trial: CW trial goodput SE from successful CB payload.

    The figure answers, for a given codeword:
      target theoretical capability -> MCS-loaded capability -> actual goodput.

    Output:
      cdf_cw_se_triplet_by_scheme_snr/<scheme>/rank*_snr*_CW*_se_triplet_cdf.png
    with a same-name CSV containing (series, x, cdf).
    """
    if not cw_rows:
        return

    out_dir = export_dir / "cdf_cw_se_triplet_by_scheme_snr"
    snrs = sorted(set(safe_float(r.get("snr_db"), 0.0) for r in cw_rows))
    schemes = unique_sorted(safe_str(r.get("scheme"), "unknown") for r in cw_rows)

    metric_defs = [
        ("target_se_total", "target theoretical SE before MCS"),
        ("mcs_se_total", "MCS-loaded SE after MCS selection"),
        ("goodput_se_tf_trial", "actual goodput SE from successful CB payload"),
    ]

    for snr in snrs:
        snr_rows = [r for r in cw_rows if abs(safe_float(r.get("snr_db"), 0.0) - snr) < 1e-9]
        if not snr_rows:
            continue
        for scheme in schemes:
            scheme_rows = [r for r in snr_rows if safe_str(r.get("scheme"), "unknown") == scheme]
            if not scheme_rows:
                continue
            cw_indices = unique_sorted(safe_int_str(r.get("cw_index"), "?") for r in scheme_rows)
            for cw in cw_indices:
                cw_rows_i = [r for r in scheme_rows if safe_int_str(r.get("cw_index"), "?") == cw]
                if not cw_rows_i:
                    continue

                series: Dict[str, List[float]] = defaultdict(list)
                for metric, label in metric_defs:
                    vals: List[float] = []
                    for r in cw_rows_i:
                        val = safe_float(r.get(metric), float("nan"))
                        if math.isfinite(val):
                            vals.append(val)
                    if vals:
                        series[label] = vals

                # Need at least one metric; normally all three exist in v2.9/v3.0.
                if not series:
                    continue

                snr_str = ("%g" % snr).replace("-", "m").replace(".", "p")
                scheme_safe = sanitize_filename(scheme)
                stem = f"rank{rank}_{sanitize_filename(channel)}_snr{snr_str}_{scheme_safe}_CW{sanitize_filename(cw)}_se_triplet_cdf"
                _plot_cdf_series(
                    series,
                    title=(
                        f"CW SE decomposition CDF, {scheme}, CW{cw}, "
                        f"rank {rank}, {channel}, SNR {snr:g} dB"
                    ),
                    xlabel="Spectral efficiency [bit/RE, CW-level]",
                    out_png=out_dir / scheme_safe / f"{stem}.png",
                    out_csv=out_dir / scheme_safe / f"{stem}.csv",
                )


def plot_layer_sinr_cdfs(layer_rows: List[Dict[str, str]], export_dir: Path, rank: str, channel: str) -> None:
    """Plot scheme-independent layer quality CDFs.

    v2.9 plotted the ideal SVD-equivalent post_sinr_db. v3.0 adds RX channel
    estimation abstraction and therefore records multiple SINR-like metrics:

    - post_sinr_ideal_db: ideal-SVD reference used by the v2.9 MCS selector.
    - post_sinr_actual_rx_db: actual SINR after W(H_hat) is applied to true H.
    - post_sinr_estimated_by_ue_db: UE-estimated SINR computed from H_hat.
    - post_sinr_loss_db: ideal - actual in dB.

    Each figure is one SNR point; each curve is one layer CDF. A same-name CSV
    is written next to the PNG and contains (series, x, cdf).
    """
    if not layer_rows:
        return

    metrics = [
        ("post_sinr_db", "Layer ideal post-SINR / dB", "cdf_layer_post_sinr_by_snr"),
        ("post_sinr_ideal_db", "Layer ideal post-SINR / dB", "cdf_layer_rx_quality_by_snr/post_sinr_ideal_db"),
        ("post_sinr_actual_rx_db", "Layer actual RX post-SINR / dB", "cdf_layer_rx_quality_by_snr/post_sinr_actual_rx_db"),
        ("post_sinr_estimated_by_ue_db", "Layer UE-estimated post-SINR / dB", "cdf_layer_rx_quality_by_snr/post_sinr_estimated_by_ue_db"),
        ("post_sinr_loss_db", "Layer post-SINR loss: ideal - actual / dB", "cdf_layer_rx_quality_by_snr/post_sinr_loss_db"),
        ("rx_ce_nmse_db_measured", "Measured RX channel-estimation NMSE / dB", "cdf_layer_rx_quality_by_snr/rx_ce_nmse_db_measured"),
        ("layer_se_ideal_eff", "Layer ideal effective SE [bit/RE/layer]", "cdf_layer_rx_quality_by_snr/layer_se_ideal_eff"),
        ("layer_se_actual_rx_eff", "Layer actual RX effective SE [bit/RE/layer]", "cdf_layer_rx_quality_by_snr/layer_se_actual_rx_eff"),
    ]

    snrs = sorted(set(safe_float(r.get("snr_db"), 0.0) for r in layer_rows))
    for metric, xlabel, subdir in metrics:
        # Skip metrics absent from older v2.9 files.
        if not any(metric in r and math.isfinite(safe_float(r.get(metric), float("nan"))) for r in layer_rows):
            continue
        out_dir = export_dir / subdir
        for snr in snrs:
            rs = [r for r in layer_rows if abs(safe_float(r.get("snr_db"), 0.0) - snr) < 1e-9]
            series: Dict[str, List[float]] = defaultdict(list)
            for r in rs:
                layer = safe_int_str(r.get("layer_index"), "?")
                val = safe_float(r.get(metric), float("nan"))
                if math.isfinite(val):
                    series[f"L{layer}"].append(val)
            if not series:
                continue
            snr_str = ("%g" % snr).replace("-", "m").replace(".", "p")
            stem = f"rank{rank}_{sanitize_filename(channel)}_snr{snr_str}_{sanitize_filename(metric)}_cdf"
            _plot_cdf_series(
                series,
                title=f"{xlabel} CDF, rank {rank}, {channel}, SNR {snr:g} dB",
                xlabel=xlabel,
                out_png=out_dir / f"{stem}.png",
                out_csv=out_dir / f"{stem}.csv",
            )


def plot_scheme_level_cdfs(cw_rows: List[Dict[str, str]], export_dir: Path, rank: str, channel: str) -> None:
    """Aggregate CW-level trial records into scheme-level trial records and plot CDFs.

    Input rows are one row per SNR/trial/scheme/CW. For fair scheme-to-scheme
    comparison, we first sum over all CWs belonging to the same
    (snr_db, trial_id, scheme). This makes Scheme3 ([8]) and multi-CW schemes
    contribute one sample per trial per scheme.
    """
    if not cw_rows:
        return

    out_dir = export_dir / "cdf_scheme_level_by_snr"
    summary_dir = export_dir / "scheme_trial_summary"

    sum_metrics = [
        "target_se_total",
        "mcs_se_total",
        "goodput_se_tf_trial",
        "mcs_quantization_loss_se_clipped",
        "coding_failure_loss_se_clipped",
        "actual_rx_target_se_total",
        "post_sinr_loss_db_mean",
        "successful_payload_bits",
        "cb_error_count",
        "cb_success_count",
    ]

    # group key: one sample per scheme per trial per SNR
    grouped: Dict[Tuple[float, str, str], Dict[str, object]] = {}
    for r in cw_rows:
        snr = safe_float(r.get("snr_db"), 0.0)
        trial_id = safe_str(r.get("trial_id"), "")
        scheme = safe_str(r.get("scheme"), "unknown")
        key = (snr, trial_id, scheme)
        if key not in grouped:
            grouped[key] = {
                "rank": safe_str(r.get("rank"), rank),
                "channel": channel,
                "snr_db": snr,
                "trial_id": trial_id,
                "scheme": scheme,
                "scheme_id": safe_str(r.get("scheme_id"), ""),
                "partition": safe_str(r.get("partition"), ""),
                "num_cw_records": 0,
            }
            for m in sum_metrics:
                grouped[key][f"scheme_{m}"] = 0.0
        g = grouped[key]
        g["num_cw_records"] = int(g["num_cw_records"]) + 1
        for m in sum_metrics:
            g[f"scheme_{m}"] = float(g[f"scheme_{m}"]) + safe_float(r.get(m), 0.0)

    summary_rows = list(grouped.values())
    if not summary_rows:
        return

    # Derived scheme-level rates/losses.
    for g in summary_rows:
        cb_err = float(g.get("scheme_cb_error_count", 0.0))
        cb_suc = float(g.get("scheme_cb_success_count", 0.0))
        denom = cb_err + cb_suc
        g["scheme_cb_error_rate_trial"] = cb_err / denom if denom > 0 else 0.0

    summary_fields = [
        "rank", "channel", "snr_db", "trial_id", "scheme", "scheme_id", "partition", "num_cw_records",
    ] + [f"scheme_{m}" for m in sum_metrics] + ["scheme_cb_error_rate_trial"]

    write_csv(summary_dir / "scheme_trial_summary_records.csv", summary_rows, summary_fields)

    cdf_metrics = [
        ("scheme_mcs_quantization_loss_se_clipped", "Scheme-level MCS quantization/capping loss SE"),
        ("scheme_coding_failure_loss_se_clipped", "Scheme-level coding/CB-failure loss SE"),
        ("scheme_target_se_total", "Scheme-level target theoretical SE before MCS"),
        ("scheme_mcs_se_total", "Scheme-level MCS-loaded SE"),
        ("scheme_goodput_se_tf_trial", "Scheme-level trial goodput SE"),
        ("scheme_actual_rx_target_se_total", "Scheme-level actual-RX target SE from post-SINR"),
        ("scheme_post_sinr_loss_db_mean", "Scheme-level sum of CW mean post-SINR losses / dB"),
        ("scheme_cb_error_rate_trial", "Scheme-level trial CB error rate"),
    ]

    snrs = sorted(set(float(r["snr_db"]) for r in summary_rows))
    for snr in snrs:
        rs = [r for r in summary_rows if abs(float(r["snr_db"]) - snr) < 1e-9]
        for metric, xlabel in cdf_metrics:
            series: Dict[str, List[float]] = defaultdict(list)
            for r in rs:
                scheme = safe_str(r.get("scheme"), "unknown")
                val = safe_float(r.get(metric), float("nan"))
                if math.isfinite(val):
                    series[scheme].append(val)
            if not series:
                continue
            snr_str = ("%g" % snr).replace("-", "m").replace(".", "p")
            stem = f"rank{rank}_{sanitize_filename(channel)}_snr{snr_str}_{sanitize_filename(metric)}_scheme_level_cdf"
            _plot_cdf_series(
                series,
                title=f"{xlabel} CDF, rank {rank}, {channel}, SNR {snr:g} dB",
                xlabel=xlabel + " [scheme-level, bit/RE except error-rate]",
                out_png=out_dir / metric / f"{stem}.png",
                out_csv=out_dir / metric / f"{stem}.csv",
            )


def infer_input_csv(output_root: Optional[Path], explicit_csv: Optional[Path]) -> Path:
    if explicit_csv is not None:
        return explicit_csv
    if output_root is None:
        raise ValueError("必须提供 --output-root 或 --results-csv/--merged-csv")
    candidates = [
        output_root / "merged_results.csv",
        output_root / "results.csv",
    ]
    for p in candidates:
        if p.exists():
            return p
    raise FileNotFoundError(f"在 {output_root} 下找不到 merged_results.csv 或 results.csv")


def parse_metric_list(text: str, default: List[str]) -> List[str]:
    if text is None or str(text).strip() == "":
        return default
    vals = [x.strip() for x in str(text).split(",") if x.strip()]
    return vals or default


def run_postprocess_one_rank(
    rows: List[Dict[str, str]],
    output_root: Path,
    rank: str,
    channel: str,
    export_dir: Path,
    args,
    curve_metrics: List[str],
    bar_metrics: List[str],
    input_csv: Path,
    cw_rows_all: Optional[List[Dict[str, str]]] = None,
    layer_rows_all: Optional[List[Dict[str, str]]] = None,
) -> None:
    if not rows:
        print(f"rank={rank}: 无 rows，跳过。")
        return

    export_dir.mkdir(parents=True, exist_ok=True)
    mcs_values = unique_sorted(row_mcs(r) for r in rows)
    mode = "adaptive" if mcs_values == ["adaptive"] else ("mixed_adaptive_and_fixed" if "adaptive" in mcs_values else "multi_mcs_or_fixed")

    print(f"读取: {input_csv}")
    print(f"rank={rank}, channel={channel}, mode={mode}, mcs_groups={mcs_values}")
    print(f"导出目录: {export_dir}")

    export_long_csv(rows, export_dir, rank, channel)
    export_curve_csvs(rows, export_dir, rank, channel, curve_metrics)
    envelope_rows = export_goodput_envelope(rows, export_dir, rank, channel, score_metric="goodput_se_tf")

    if not args.no_lines:
        plot_metric_lines_by_mcs(rows, export_dir, rank, channel, curve_metrics)
    if not args.no_envelope_plots:
        plot_goodput_envelope(envelope_rows, export_dir, rank, channel)
    if not args.no_winner_bars:
        plot_winner_counts(envelope_rows, export_dir, rank, channel)
    if not args.no_bars:
        plot_bar_by_mcs_snr(
            rows,
            export_dir,
            rank,
            channel,
            metrics=bar_metrics,
            max_snr_plots_per_mcs=args.max_snr_plots_per_mcs,
            sort_by_metric=not args.no_sort_bars,
        )

    if not args.no_cdf:
        cw_rows = filter_rows_for_rank(cw_rows_all or [], rank)
        if cw_rows:
            # OLLA records contain warmup + measurement. CDF/statistical plots use
            # measurement only; OLLA trace plots keep both phases.
            cw_rows_measure = filter_measurement_phase_rows(cw_rows)
            plot_loss_cdfs(cw_rows_measure, export_dir, rank, channel)
            plot_scheme_level_cdfs(cw_rows_measure, export_dir, rank, channel)
            plot_mcs_cdfs(cw_rows_measure, export_dir, rank, channel)
            plot_cw_se_triplet_cdfs(cw_rows_measure, export_dir, rank, channel)
            plot_olla_trace_plots(cw_rows, export_dir, rank, channel)
        else:
            print(f"rank={rank}: 未找到 cw_trial_link_adaptation_records.csv rows，跳过 v2.9 loss/MCS CDF。")

        layer_rows = filter_rows_for_rank(layer_rows_all or [], rank)
        if layer_rows:
            plot_layer_sinr_cdfs(layer_rows, export_dir, rank, channel)
        else:
            print(f"rank={rank}: 未找到 layer_post_sinr_samples rows，跳过 layer post-SINR CDF。")

    print("完成。主要输出：")
    for sub in ["all_curves_long", "curves_by_mcs", "line_plots_by_mcs", "envelope", "envelope_plots", "winner_count_bars", "bars_by_mcs_snr", "cdf_losses_by_snr", "cdf_scheme_level_by_snr", "scheme_trial_summary", "cdf_mcs_by_scheme_cw", "cdf_cw_se_triplet_by_scheme_snr", "cdf_layer_post_sinr_by_snr", "cdf_layer_rx_quality_by_snr", "olla_mcs_trace_by_scheme_snr", "olla_code_rate_trace_by_scheme_snr", "olla_offset_trace_by_scheme_snr", "olla_cb_error_ewma_by_scheme_snr"]:
        p = export_dir / sub
        if p.exists():
            print(f"  - {p}")


def main() -> None:
    parser = argparse.ArgumentParser(description="自动处理 multi-MCS / adaptive-MCS 的曲线 CSV 与柱状图后处理。")
    parser.add_argument("--output-root", type=Path, default=None, help="仿真输出目录。可包含 merged_results.csv 或 results.csv。")
    parser.add_argument("--results-csv", type=Path, default=None, help="直接指定 results.csv 或 merged_results.csv。")
    parser.add_argument("--merged-csv", type=Path, default=None, help="兼容旧参数，等价于 --results-csv。")
    parser.add_argument("--rank", default=None, help="rank 数，例如 8；multi/all 表示自动按 rank 分开画。若不填且输入含多个 rank，也会自动分开画。")
    parser.add_argument("--channel", default=None, help="信道名，例如 AWGN / Rayleigh / CDL。若不填则从 CSV 或路径推断。")
    parser.add_argument("--export-dir", type=Path, default=None, help="导出目录。默认 output-root/curve_exports_rankX_CHANNEL。多 rank 分开画时若指定该参数，则输出到该目录下的 rank_X 子目录。")
    parser.add_argument("--bar-metrics", default="goodput_se_tf,scheme_cb_bler", help="柱状图指标，逗号分隔。默认 goodput_se_tf,scheme_cb_bler。")
    parser.add_argument("--curve-metrics", default="scheme_cb_bler,scheme_bler,goodput_bits_per_slot,goodput_se_tf,goodput_se_layer_re", help="导出曲线/画线指标，逗号分隔。")
    parser.add_argument("--no-bars", action="store_true", help="不画每个 MCS/SNR 的 scheme 柱状图。")
    parser.add_argument("--no-lines", action="store_true", help="不画曲线图，只导出 CSV。")
    parser.add_argument("--no-envelope-plots", action="store_true", help="不画包络曲线图。")
    parser.add_argument("--no-winner-bars", action="store_true", help="不画 winner count 柱状图。")
    parser.add_argument("--max-snr-plots-per-mcs", type=int, default=999, help="每个 MCS/adaptive 最多画多少个 SNR 柱状图。")
    parser.add_argument("--no-sort-bars", action="store_true", help="柱状图不按指标排序，而按 scheme 名排序。")
    parser.add_argument("--cw-trial-csv", type=Path, default=None, help="v2.9 每 trial/CW link adaptation 记录 CSV。若不指定则自动搜索。")
    parser.add_argument("--layer-sinr-csv", type=Path, default=None, help="layer_post_sinr_samples.csv。若不指定则自动搜索。")
    parser.add_argument("--no-cdf", action="store_true", help="不画 v2.9 CDF 图。")
    parser.add_argument("--combine-ranks", action="store_true", help="多 rank 输入时不拆分，沿用旧行为，把所有 rank 合在同一组图里。通常不建议。")
    args = parser.parse_args()

    explicit_csv = args.results_csv or args.merged_csv
    input_csv = infer_input_csv(args.output_root, explicit_csv)
    if not input_csv.exists():
        raise FileNotFoundError(f"找不到输入 CSV: {input_csv}")

    rows_all = read_csv_rows(input_csv)
    if not rows_all:
        raise RuntimeError(f"CSV 为空: {input_csv}")

    output_root = args.output_root or input_csv.parent
    channel = str(args.channel) if args.channel is not None else infer_channel(rows_all, output_root)
    curve_metrics = parse_metric_list(args.curve_metrics, ["scheme_cb_bler", "scheme_bler", "goodput_bits_per_slot", "goodput_se_tf", "goodput_se_layer_re"])
    bar_metrics = parse_metric_list(args.bar_metrics, ["goodput_se_tf", "scheme_cb_bler"])

    cw_rows_all: List[Dict[str, str]] = []
    layer_rows_all: List[Dict[str, str]] = []
    if not args.no_cdf:
        cw_paths = discover_cw_trial_csvs(output_root, args.cw_trial_csv, rows_all)
        if cw_paths:
            print(f"读取 CW trial records: {[str(p) for p in cw_paths]}")
            cw_rows_all = read_many_csv(cw_paths)
        else:
            print("未找到 cw_trial_link_adaptation_records.csv，后续跳过 v2.9 loss/MCS CDF。")

        layer_paths = discover_layer_sinr_csvs(output_root, args.layer_sinr_csv, rows_all)
        if layer_paths:
            print(f"读取 layer SINR samples: {[str(p) for p in layer_paths]}")
            layer_rows_all = read_many_csv(layer_paths)
        else:
            print("未找到 layer_post_sinr_samples.csv，后续跳过 layer post-SINR CDF。")

    result_ranks = available_ranks_from_rows(rows_all)
    extra_ranks = available_ranks_from_rows(cw_rows_all, layer_rows_all)
    all_ranks = result_ranks or extra_ranks

    rank_arg = str(args.rank) if args.rank is not None else infer_rank(rows_all, output_root)
    rank_arg_l = rank_arg.lower()

    should_split = False
    ranks_to_plot: List[str]
    if args.combine_ranks:
        should_split = False
        ranks_to_plot = [rank_arg]
    elif rank_arg_l in ("multi", "all", "*"):
        should_split = True
        ranks_to_plot = all_ranks or result_ranks or [rank_arg]
    elif args.rank is None and len(all_ranks) > 1:
        should_split = True
        ranks_to_plot = all_ranks
    else:
        should_split = False
        ranks_to_plot = [rank_arg]

    if should_split:
        print(f"检测到多 rank，按 rank 分开后处理: {ranks_to_plot}")
        for rk in ranks_to_plot:
            rows = filter_rows_for_rank(rows_all, rk)
            if args.export_dir is not None:
                export_dir = args.export_dir / f"rank_{rk}"
            else:
                export_dir = output_root / f"curve_exports_rank{rk}_{sanitize_filename(channel)}"
            run_postprocess_one_rank(
                rows=rows,
                output_root=output_root,
                rank=rk,
                channel=channel,
                export_dir=export_dir,
                args=args,
                curve_metrics=curve_metrics,
                bar_metrics=bar_metrics,
                input_csv=input_csv,
                cw_rows_all=cw_rows_all,
                layer_rows_all=layer_rows_all,
            )
    else:
        # Single-rank mode. If a concrete rank is provided, filter rows to that rank
        # even when merged_results.csv contains multiple ranks.
        rows = filter_rows_for_rank(rows_all, rank_arg) if rank_arg_l not in ("multi", "all", "*") else list(rows_all)
        export_dir = args.export_dir or (output_root / f"curve_exports_rank{rank_arg}_{sanitize_filename(channel)}")
        run_postprocess_one_rank(
            rows=rows,
            output_root=output_root,
            rank=rank_arg,
            channel=channel,
            export_dir=export_dir,
            args=args,
            curve_metrics=curve_metrics,
            bar_metrics=bar_metrics,
            input_csv=input_csv,
            cw_rows_all=cw_rows_all,
            layer_rows_all=layer_rows_all,
        )


if __name__ == "__main__":
    main()
