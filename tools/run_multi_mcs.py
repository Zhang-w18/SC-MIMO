#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_multi_mcs.py

批量运行多个 fixed MCS，并合并结果、绘制汇总图。

v2.4 改进版：
1. 保留原有多 MCS / 多 GPU 并行运行能力；
2. 保留 merged_results.csv；
3. 新增拆图绘图：
   - all：所有曲线画在一张图，legend 放到图外；
   - by_mcs：每个 MCS 一张图，比较不同 scheme；
   - by_scheme：每个 scheme 一张图，比较不同 MCS；
   - both：同时输出 by_mcs 与 by_scheme；
   - auto：曲线过多时自动拆图，否则画 all；
4. 默认 plot_mode=auto，避免 all schemes 下图例过大、主图变小。

典型用法：

python tools/run_multi_mcs.py \
  --base-config configs/sionna_ldpc_rank2_all_schemes_awgn_gpu_fast.yaml \
  --mcs-list 3,5,8,10 \
  --output-root results_v24_rank2_all_schemes_awgn_multi_mcs \
  --parallel \
  --gpu-list 0,1,2,3 \
  --plot-mode both
"""

from __future__ import annotations

import argparse
import csv
import os
import queue
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


def _import_yaml():
    try:
        import yaml
        return yaml
    except Exception as e:
        raise RuntimeError(
            "无法导入 PyYAML。请先安装或切换到包含 yaml 的环境。"
        ) from e


def _import_matplotlib():
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        return plt
    except Exception as e:
        raise RuntimeError(
            "无法导入 matplotlib。请确认当前环境已安装 matplotlib。"
        ) from e


@dataclass
class RunItem:
    mcs: int
    config_path: Path
    run_output_dir: Path
    log_path: Path


@dataclass
class RunResult:
    item: RunItem
    ok: bool
    gpu_id: Optional[str] = None
    sim_dir: Optional[Path] = None
    results_csv: Optional[Path] = None
    error: Optional[str] = None


def parse_mcs_list(text: str) -> List[int]:
    values: List[int] = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        values.append(int(part))
    if not values:
        raise ValueError("--mcs-list 不能为空，例如 3,5,8,10")
    return values


def parse_gpu_list(text: str) -> List[str]:
    values: List[str] = []
    for part in text.split(","):
        part = part.strip()
        if part:
            values.append(part)
    if not values:
        raise ValueError("--gpu-list 不能为空，例如 0,1,2,3")
    return values


def load_yaml(path: Path) -> Dict:
    yaml = _import_yaml()
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if data is None:
        data = {}
    return data


def dump_yaml(data: Dict, path: Path) -> None:
    yaml = _import_yaml()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(
            data,
            f,
            allow_unicode=True,
            sort_keys=False,
            default_flow_style=False,
        )


def latest_sim_dir(output_dir: Path) -> Path:
    if not output_dir.exists():
        raise FileNotFoundError("输出目录不存在: {}".format(output_dir))

    candidates = [
        p for p in output_dir.iterdir()
        if p.is_dir() and p.name.startswith("sim_")
    ]
    if not candidates:
        raise FileNotFoundError("没有找到 sim_* 子目录: {}".format(output_dir))

    return sorted(candidates, key=lambda p: p.stat().st_mtime)[-1]


def read_csv_rows(path: Path) -> Tuple[List[str], List[Dict[str, str]]]:
    with open(path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        rows = [dict(row) for row in reader]
    return fieldnames, rows


def write_csv_rows(
    path: Path,
    rows: List[Dict[str, str]],
    preferred_first_cols: Iterable[str],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    all_cols: List[str] = []
    for row in rows:
        for k in row.keys():
            if k not in all_cols:
                all_cols.append(k)

    ordered: List[str] = []
    for c in preferred_first_cols:
        if c in all_cols and c not in ordered:
            ordered.append(c)
    for c in all_cols:
        if c not in ordered:
            ordered.append(c)

    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=ordered)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def safe_float(value: str, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _safe_int_str(value: object, default: str = "") -> str:
    if value is None:
        return default
    s = str(value).strip()
    if s == "":
        return default
    try:
        return str(int(float(s)))
    except Exception:
        return s


def row_mcs(row: Mapping[str, str]) -> str:
    return _safe_int_str(row.get("meta_fixed_mcs") or row.get("meta_cw0_mcs") or "")


def row_scheme(row: Mapping[str, str]) -> str:
    return str(row.get("scheme", "")).strip() or "unknown_scheme"


def row_label(row: Mapping[str, str], label_mode: str = "scheme_mcs") -> str:
    scheme = row_scheme(row)
    mcs = row_mcs(row)
    table = str(row.get("meta_mcs_table", "")).strip()

    if label_mode == "scheme":
        return scheme
    if label_mode == "mcs":
        return "MCS {}".format(mcs)
    if label_mode == "scheme_mcs":
        if table:
            return "{} / MCS {} ({})".format(scheme, mcs, table)
        return "{} / MCS {}".format(scheme, mcs)
    if label_mode == "mcs_scheme":
        return "MCS {} / {}".format(mcs, scheme)
    return "{} / MCS {}".format(scheme, mcs)


def group_rows(
    rows: List[Dict[str, str]],
    label_mode: str = "scheme_mcs",
) -> Dict[str, List[Dict[str, str]]]:
    grouped: Dict[str, List[Dict[str, str]]] = {}
    for row in rows:
        label = row_label(row, label_mode=label_mode)
        grouped.setdefault(label, []).append(row)
    return grouped


def _sort_rows_by_snr(rows: List[Dict[str, str]]) -> List[Dict[str, str]]:
    return sorted(rows, key=lambda r: safe_float(r.get("snr_db", "0")))


def _make_plot_dir(output_root: Path, subdir: Optional[str] = None) -> Path:
    plot_dir = output_root
    if subdir:
        plot_dir = output_root / subdir
    plot_dir.mkdir(parents=True, exist_ok=True)
    return plot_dir


def _plot_metric(
    rows: List[Dict[str, str]],
    output_path: Path,
    metric_col: str,
    ylabel: str,
    title: str,
    semilogy: bool = False,
    label_mode: str = "scheme_mcs",
    legend_outside: bool = True,
    min_y: Optional[float] = None,
) -> None:
    if not rows:
        return

    plt = _import_matplotlib()
    grouped = group_rows(rows, label_mode=label_mode)

    fig, ax = plt.subplots(figsize=(11, 6))

    for label, rs in sorted(grouped.items(), key=lambda kv: kv[0]):
        rs_sorted = _sort_rows_by_snr(rs)
        xs = [safe_float(r.get("snr_db", "0")) for r in rs_sorted]
        ys = [safe_float(r.get(metric_col, "0")) for r in rs_sorted]
        if semilogy:
            floor = 1e-5 if min_y is None else min_y
            ys = [max(y, floor) for y in ys]
            ax.semilogy(xs, ys, marker="o", markersize=3, linewidth=1.2, label=label)
        else:
            ax.plot(xs, ys, marker="o", markersize=3, linewidth=1.2, label=label)

    ax.set_xlabel("SNR / dB")
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    ax.grid(True, which="both", linestyle="--", linewidth=0.5)

    if legend_outside:
        ax.legend(
            loc="center left",
            bbox_to_anchor=(1.02, 0.5),
            fontsize=7,
            frameon=True,
        )
        fig.tight_layout(rect=[0, 0, 0.78, 1])
        fig.savefig(output_path, dpi=200, bbox_inches="tight")
    else:
        ax.legend(fontsize=7, frameon=True)
        fig.tight_layout()
        fig.savefig(output_path, dpi=200)

    plt.close(fig)


def _unique_sorted(values: Iterable[str]) -> List[str]:
    vals = [str(v) for v in values if str(v).strip() != ""]
    def key_fn(x: str):
        try:
            return (0, int(float(x)))
        except Exception:
            return (1, x)
    return sorted(set(vals), key=key_fn)


def _sanitize_filename(text: str) -> str:
    keep = []
    for ch in str(text):
        if ch.isalnum() or ch in ("-", "_", ".", "+"):
            keep.append(ch)
        else:
            keep.append("_")
    s = "".join(keep).strip("_")
    return s or "unknown"


def plot_all_curves(rows: List[Dict[str, str]], output_root: Path) -> None:
    """所有 scheme/MCS 曲线放在同一张图里，legend 放图外。"""
    plot_dir = _make_plot_dir(output_root, "plots_all")

    _plot_metric(
        rows,
        plot_dir / "all_bler_vs_snr.png",
        metric_col="scheme_bler",
        ylabel="Scheme BLER",
        title="BLER vs SNR (all schemes and MCS)",
        semilogy=True,
        label_mode="scheme_mcs",
        legend_outside=True,
    )
    _plot_metric(
        rows,
        plot_dir / "all_goodput_vs_snr.png",
        metric_col="goodput_bits_per_slot",
        ylabel="Goodput / bits per slot",
        title="Goodput vs SNR (all schemes and MCS)",
        semilogy=False,
        label_mode="scheme_mcs",
        legend_outside=True,
    )
    _plot_metric(
        rows,
        plot_dir / "all_goodput_se_tf_vs_snr.png",
        metric_col="goodput_se_tf",
        ylabel="Goodput SE / bits per TF-RE",
        title="Goodput SE vs SNR (all schemes and MCS)",
        semilogy=False,
        label_mode="scheme_mcs",
        legend_outside=True,
    )


def plot_by_mcs(rows: List[Dict[str, str]], output_root: Path) -> None:
    """每个 MCS 一组三张图：同一 MCS 下比较不同 scheme。"""
    plot_dir = _make_plot_dir(output_root, "plots_by_mcs")
    mcs_values = _unique_sorted(row_mcs(r) for r in rows)

    for mcs in mcs_values:
        rs = [r for r in rows if row_mcs(r) == mcs]
        if not rs:
            continue

        prefix = "mcs_{}".format(_sanitize_filename(mcs))
        title_suffix = "MCS {}".format(mcs)

        _plot_metric(
            rs,
            plot_dir / "{}_bler_vs_snr.png".format(prefix),
            metric_col="scheme_bler",
            ylabel="Scheme BLER",
            title="BLER vs SNR ({})".format(title_suffix),
            semilogy=True,
            label_mode="scheme",
            legend_outside=True,
        )
        _plot_metric(
            rs,
            plot_dir / "{}_goodput_vs_snr.png".format(prefix),
            metric_col="goodput_bits_per_slot",
            ylabel="Goodput / bits per slot",
            title="Goodput vs SNR ({})".format(title_suffix),
            semilogy=False,
            label_mode="scheme",
            legend_outside=True,
        )
        _plot_metric(
            rs,
            plot_dir / "{}_goodput_se_tf_vs_snr.png".format(prefix),
            metric_col="goodput_se_tf",
            ylabel="Goodput SE / bits per TF-RE",
            title="Goodput SE vs SNR ({})".format(title_suffix),
            semilogy=False,
            label_mode="scheme",
            legend_outside=True,
        )


def plot_by_scheme(rows: List[Dict[str, str]], output_root: Path) -> None:
    """每个 scheme 一组三张图：同一 scheme 下比较不同 MCS。"""
    plot_dir = _make_plot_dir(output_root, "plots_by_scheme")
    scheme_values = _unique_sorted(row_scheme(r) for r in rows)

    for scheme in scheme_values:
        rs = [r for r in rows if row_scheme(r) == scheme]
        if not rs:
            continue

        prefix = "scheme_{}".format(_sanitize_filename(scheme))
        title_suffix = "Scheme {}".format(scheme)

        _plot_metric(
            rs,
            plot_dir / "{}_bler_vs_snr.png".format(prefix),
            metric_col="scheme_bler",
            ylabel="Scheme BLER",
            title="BLER vs SNR ({})".format(title_suffix),
            semilogy=True,
            label_mode="mcs",
            legend_outside=True,
        )
        _plot_metric(
            rs,
            plot_dir / "{}_goodput_vs_snr.png".format(prefix),
            metric_col="goodput_bits_per_slot",
            ylabel="Goodput / bits per slot",
            title="Goodput vs SNR ({})".format(title_suffix),
            semilogy=False,
            label_mode="mcs",
            legend_outside=True,
        )
        _plot_metric(
            rs,
            plot_dir / "{}_goodput_se_tf_vs_snr.png".format(prefix),
            metric_col="goodput_se_tf",
            ylabel="Goodput SE / bits per TF-RE",
            title="Goodput SE vs SNR ({})".format(title_suffix),
            semilogy=False,
            label_mode="mcs",
            legend_outside=True,
        )


def plot_merged_results(
    rows: List[Dict[str, str]],
    output_root: Path,
    plot_mode: str = "auto",
    auto_threshold: int = 12,
) -> None:
    """
    绘制合并结果。

    plot_mode:
      - all: 所有曲线在一张图，legend 放图外；
      - by_mcs: 每个 MCS 一张图，比较 scheme；
      - by_scheme: 每个 scheme 一张图，比较 MCS；
      - both: by_mcs + by_scheme；
      - auto: 曲线数 <= auto_threshold 时画 all，否则画 both。
    """
    if not rows:
        print("没有可绘制的行。", flush=True)
        return

    curve_keys = set((row_scheme(r), row_mcs(r)) for r in rows)
    n_curves = len(curve_keys)

    if plot_mode == "auto":
        if n_curves <= auto_threshold:
            actual_mode = "all"
        else:
            actual_mode = "both"
    else:
        actual_mode = plot_mode

    print("绘图模式: {}，曲线数量: {}".format(actual_mode, n_curves), flush=True)

    if actual_mode == "all":
        plot_all_curves(rows, output_root)
    elif actual_mode == "by_mcs":
        plot_by_mcs(rows, output_root)
    elif actual_mode == "by_scheme":
        plot_by_scheme(rows, output_root)
    elif actual_mode == "both":
        plot_by_mcs(rows, output_root)
        plot_by_scheme(rows, output_root)
        # 仍然额外画一份 all，但 legend 放图外，方便总览；如果不需要可删除。
        plot_all_curves(rows, output_root)
    else:
        raise ValueError("未知 plot_mode: {}".format(plot_mode))


def run_one_config(
    config_path: Path,
    log_path: Path,
    force_cpu: bool = False,
    cuda_visible_devices: Optional[str] = None,
) -> None:
    """运行单个配置文件，把 stdout/stderr 写入 log。"""
    cmd = [sys.executable, "run.py", "--config", str(config_path)]

    env = os.environ.copy()
    mode_desc = "默认 GPU/CPU 可见性"

    if force_cpu:
        env["CUDA_VISIBLE_DEVICES"] = "-1"
        mode_desc = "CPU only, CUDA_VISIBLE_DEVICES=-1"
    elif cuda_visible_devices is not None:
        env["CUDA_VISIBLE_DEVICES"] = str(cuda_visible_devices)
        mode_desc = "GPU visible devices = {}".format(cuda_visible_devices)

    log_path.parent.mkdir(parents=True, exist_ok=True)

    with open(log_path, "w", encoding="utf-8") as log_f:
        log_f.write("命令: {}\n".format(" ".join(cmd)))
        log_f.write("模式: {}\n".format(mode_desc))
        log_f.write("=" * 80 + "\n")
        log_f.flush()

        subprocess.run(
            cmd,
            stdout=log_f,
            stderr=subprocess.STDOUT,
            check=True,
            env=env,
        )


def _set_nested(d: Dict, keys: Sequence[str], value) -> None:
    cur = d
    for k in keys[:-1]:
        if k not in cur or cur[k] is None:
            cur[k] = {}
        cur = cur[k]
    cur[keys[-1]] = value


def create_run_items(
    base_config: Path,
    mcs_list: List[int],
    output_root: Path,
) -> List[RunItem]:
    """基于 base config 生成多个临时配置。"""
    base = load_yaml(base_config)

    temp_config_dir = output_root / "temp_configs"
    runs_dir = output_root / "runs"
    logs_dir = output_root / "logs"

    temp_config_dir.mkdir(parents=True, exist_ok=True)
    runs_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)

    items: List[RunItem] = []

    for mcs in mcs_list:
        cfg = dict(base)

        # 深拷贝，避免嵌套 dict 共享。
        import copy
        cfg = copy.deepcopy(base)

        run_output_dir = runs_dir / "mcs_{}".format(mcs)

        _set_nested(cfg, ["simulation", "fixed_mcs"], int(mcs))
        _set_nested(cfg, ["simulation", "output_dir"], str(run_output_dir))

        config_path = temp_config_dir / "config_mcs_{}.yaml".format(mcs)
        log_path = logs_dir / "mcs_{}.log".format(mcs)

        dump_yaml(cfg, config_path)

        print("已生成临时配置: {}".format(config_path), flush=True)

        items.append(
            RunItem(
                mcs=int(mcs),
                config_path=config_path,
                run_output_dir=run_output_dir,
                log_path=log_path,
            )
        )

    return items


def collect_one_result(item: RunItem) -> RunResult:
    """从某个 MCS 的输出目录中找到最新 sim_* 结果。"""
    sim_dir = latest_sim_dir(item.run_output_dir)
    results_csv = sim_dir / "results.csv"
    if not results_csv.exists():
        raise FileNotFoundError("没有找到 results.csv: {}".format(results_csv))

    return RunResult(
        item=item,
        ok=True,
        sim_dir=sim_dir,
        results_csv=results_csv,
    )


def run_items_sequential(
    items: List[RunItem],
    force_cpu: bool,
    cuda_visible_devices: Optional[str],
) -> List[RunResult]:
    """顺序运行所有 MCS。"""
    results: List[RunResult] = []

    for item in items:
        try:
            print("=" * 80, flush=True)
            print("运行 MCS {}，日志: {}".format(item.mcs, item.log_path), flush=True)
            run_one_config(
                item.config_path,
                item.log_path,
                force_cpu=force_cpu,
                cuda_visible_devices=cuda_visible_devices,
            )
            rr = collect_one_result(item)
            rr.gpu_id = cuda_visible_devices
            results.append(rr)
            print("MCS {} 完成: {}".format(item.mcs, rr.results_csv), flush=True)
        except Exception as e:
            results.append(
                RunResult(
                    item=item,
                    ok=False,
                    gpu_id=cuda_visible_devices,
                    error=repr(e),
                )
            )
            print("MCS {} 失败，日志: {}".format(item.mcs, item.log_path), flush=True)

    failures = [r for r in results if not r.ok]
    if failures:
        msgs = []
        for r in failures:
            msgs.append(
                "MCS {} failed: {}; log={}".format(
                    r.item.mcs,
                    r.error,
                    r.item.log_path,
                )
            )
        raise RuntimeError("顺序运行中有任务失败:\n" + "\n".join(msgs))

    return results


def run_items_parallel_by_gpu(
    items: List[RunItem],
    gpu_list: List[str],
    force_cpu: bool = False,
) -> List[RunResult]:
    """
    多 GPU 并发运行。
    每张 GPU 一个 worker，每个 MCS 一个子进程。
    """
    if force_cpu:
        raise ValueError("parallel 模式不应同时 force_cpu；CPU 请用顺序模式。")

    item_queue: "queue.Queue[RunItem]" = queue.Queue()
    result_queue: "queue.Queue[RunResult]" = queue.Queue()

    for item in items:
        item_queue.put(item)

    def worker(gpu_id: str) -> None:
        while True:
            try:
                item = item_queue.get_nowait()
            except queue.Empty:
                return

            try:
                print(
                    "[GPU {}] 开始 MCS {}，日志: {}".format(
                        gpu_id, item.mcs, item.log_path
                    ),
                    flush=True,
                )
                run_one_config(
                    item.config_path,
                    item.log_path,
                    force_cpu=False,
                    cuda_visible_devices=gpu_id,
                )
                rr = collect_one_result(item)
                rr.gpu_id = gpu_id
                result_queue.put(rr)
                print(
                    "[GPU {}] MCS {} 完成: {}".format(
                        gpu_id, item.mcs, rr.results_csv
                    ),
                    flush=True,
                )
            except Exception as e:
                result_queue.put(
                    RunResult(
                        item=item,
                        ok=False,
                        gpu_id=gpu_id,
                        error=repr(e),
                    )
                )
                print(
                    "[GPU {}] MCS {} 失败，日志: {}".format(
                        gpu_id, item.mcs, item.log_path
                    ),
                    flush=True,
                )
            finally:
                item_queue.task_done()

    threads: List[threading.Thread] = []
    for gpu_id in gpu_list:
        t = threading.Thread(target=worker, args=(gpu_id,), daemon=True)
        t.start()
        threads.append(t)

    for t in threads:
        t.join()

    results: List[RunResult] = []
    while not result_queue.empty():
        results.append(result_queue.get())

    results.sort(key=lambda r: r.item.mcs)

    failures = [r for r in results if not r.ok]
    if failures:
        msgs = []
        for r in failures:
            msgs.append(
                "MCS {} on GPU {} failed: {}; log={}".format(
                    r.item.mcs,
                    r.gpu_id,
                    r.error,
                    r.item.log_path,
                )
            )
        raise RuntimeError("并行运行中有任务失败:\n" + "\n".join(msgs))

    return results


def merge_results(
    run_results: List[RunResult],
    output_root: Path,
) -> List[Dict[str, str]]:
    """合并多个 MCS 的 results.csv。"""
    rows_all: List[Dict[str, str]] = []

    for rr in run_results:
        if rr.results_csv is None:
            continue

        _, rows = read_csv_rows(rr.results_csv)

        for row in rows:
            # 显式增加批处理层面的 metadata，避免某些结果 csv 中没有这些列。
            row["meta_fixed_mcs"] = str(rr.item.mcs)
            row["meta_gpu_id"] = "" if rr.gpu_id is None else str(rr.gpu_id)
            row["meta_batch_output_dir"] = str(rr.sim_dir or "")
            row["meta_run_log"] = str(rr.item.log_path)
            rows_all.append(row)

    preferred_cols = [
        "scheme",
        "meta_fixed_mcs",
        "meta_mcs_table",
        "meta_gpu_id",
        "snr_db",
        "scheme_bler",
        "scheme_cb_bler",
        "goodput_bits_per_slot",
        "goodput_se_tf",
        "goodput_se_layer_re",
        "trials",
        "meta_batch_output_dir",
        "meta_run_log",
    ]

    merged_csv = output_root / "merged_results.csv"
    write_csv_rows(merged_csv, rows_all, preferred_cols)
    print("已保存合并结果: {}".format(merged_csv), flush=True)

    return rows_all


def main() -> None:
    parser = argparse.ArgumentParser(
        description="批量运行多个 fixed MCS，并合并 CSV / 画图。"
    )
    parser.add_argument(
        "--base-config",
        required=True,
        type=Path,
        help="基础 YAML 配置文件路径。",
    )
    parser.add_argument(
        "--mcs-list",
        required=True,
        help="逗号分隔的 MCS 列表，例如 3,5,8,10。",
    )
    parser.add_argument(
        "--output-root",
        required=True,
        type=Path,
        help="批量运行输出根目录。",
    )
    parser.add_argument(
        "--force-cpu",
        action="store_true",
        help="强制所有子进程使用 CPU，即 CUDA_VISIBLE_DEVICES=-1。"
    )
    parser.add_argument(
        "--cuda-visible-devices",
        default=None,
        help="顺序运行时指定 CUDA_VISIBLE_DEVICES，例如 0 或 1。"
    )
    parser.add_argument(
        "--parallel",
        action="store_true",
        help="启用多 GPU 并行。每张 GPU 一个 worker，每个 MCS 一个子进程。"
    )
    parser.add_argument(
        "--gpu-list",
        default=None,
        help="多 GPU 并行时使用的 GPU 列表，例如 0,1,2,3。"
    )
    parser.add_argument(
        "--plot-mode",
        default="auto",
        choices=["auto", "all", "by_mcs", "by_scheme", "both", "none"],
        help=(
            "绘图模式：auto=曲线多时自动拆图；all=所有曲线一张图；"
            "by_mcs=每个 MCS 一张图；by_scheme=每个 scheme 一张图；"
            "both=同时输出 by_mcs 与 by_scheme；none=不绘图。"
        )
    )
    parser.add_argument(
        "--auto-threshold",
        default=12,
        type=int,
        help="plot-mode=auto 时，曲线数超过该值则自动拆图。默认 12。"
    )
    parser.add_argument(
        "--clean-output",
        action="store_true",
        help="运行前删除 output-root。谨慎使用。"
    )

    args = parser.parse_args()

    base_config: Path = args.base_config
    output_root: Path = args.output_root

    if not base_config.exists():
        raise FileNotFoundError("base config 不存在: {}".format(base_config))

    if args.clean_output and output_root.exists():
        print("删除旧输出目录: {}".format(output_root), flush=True)
        shutil.rmtree(output_root)

    output_root.mkdir(parents=True, exist_ok=True)

    mcs_list = parse_mcs_list(args.mcs_list)

    print("基础配置: {}".format(base_config), flush=True)
    print("MCS 列表: {}".format(mcs_list), flush=True)
    print("输出根目录: {}".format(output_root), flush=True)
    print("绘图模式: {}".format(args.plot_mode), flush=True)

    items = create_run_items(base_config, mcs_list, output_root)

    if args.parallel:
        if args.force_cpu:
            raise ValueError("--parallel 不能和 --force-cpu 同时使用。")
        if not args.gpu_list:
            raise ValueError("--parallel 模式必须提供 --gpu-list，例如 --gpu-list 0,1,2,3")
        gpu_list = parse_gpu_list(args.gpu_list)
        print(
            "并行模式: 每张 GPU 一个 worker；每个 MCS 一个子进程；GPU={}".format(gpu_list),
            flush=True,
        )
        run_results = run_items_parallel_by_gpu(
            items=items,
            gpu_list=gpu_list,
            force_cpu=False,
        )
    else:
        run_results = run_items_sequential(
            items=items,
            force_cpu=args.force_cpu,
            cuda_visible_devices=args.cuda_visible_devices,
        )

    rows = merge_results(run_results, output_root)

    if args.plot_mode != "none":
        plot_merged_results(
            rows,
            output_root,
            plot_mode=args.plot_mode,
            auto_threshold=args.auto_threshold,
        )
        print("绘图完成。", flush=True)
    else:
        print("已跳过绘图。", flush=True)

    print("全部完成。", flush=True)


if __name__ == "__main__":
    main()
