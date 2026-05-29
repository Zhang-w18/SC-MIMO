from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Sequence

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
os.environ.setdefault("MPLCONFIGDIR", str(Path(os.environ.get("TMPDIR", "/tmp")) / "lls_platform_matplotlib"))

from lls_platform.core.data_structures import CWSimulationStats, SNRSummary
from lls_platform.sim.sc_mimo_orchestrator import (
    _plot_cb_histograms,
    _plot_mcs_cdf,
    run_phase3_adaptive_mcs_full_resource_sweep,
)
from lls_platform.sim.stats import save_csv, save_json
from lls_platform.utils.plotting import plot_bler, plot_throughput


def _load_yaml(path: str | Path) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Top-level YAML must be a mapping: {path}")
    return data


def _case_tuples(cases: Sequence[Dict[str, Any]]) -> tuple[tuple[str, float | None, float | None], ...]:
    out = []
    for idx, case in enumerate(cases):
        if not isinstance(case, dict):
            raise ValueError(f"csi_error_cases[{idx}] must be a mapping.")
        label = str(case.get("label", f"case_{idx}"))
        tx_nmse_db = case.get("tx_nmse_db", None)
        rx_nmse_db = case.get("rx_nmse_db", None)
        out.append((
            label,
            None if tx_nmse_db is None else float(tx_nmse_db),
            None if rx_nmse_db is None else float(rx_nmse_db),
        ))
    return tuple(out)


def _resolve_kwargs(cfg: Dict[str, Any]) -> Dict[str, Any]:
    sweep = cfg.get("sweep", {}) or {}
    if not isinstance(sweep, dict):
        raise ValueError("YAML field 'sweep' must be a mapping.")
    csi_cases = _case_tuples(sweep.get("csi_error_cases", []))
    return {
        "snr_db_values": sweep.get("snr_db_values", [-12.0, -8.0, -4.0, 0.0]),
        "ranks": sweep.get("ranks", [4]),
        "csi_error_cases": csi_cases if csi_cases else None,
        "n_trials_per_snr": int(sweep.get("n_trials_per_snr", 1000)),
        "output_dir": sweep.get("output_dir", "results_phase3_adaptive_mcs_full_resource"),
        "num_iter": int(sweep.get("num_iter", 20)),
        "seed": int(sweep.get("seed", 20260528)),
        "mcs_table_name": str(sweep.get("mcs_table_name", "nr_256qam")),
        "min_mcs": int(sweep.get("min_mcs", 0)),
        "max_mcs": int(sweep.get("max_mcs", 27)),
        "min_code_rate": float(sweep.get("min_code_rate", 0.2)),
        "shannon_gap_db": float(sweep.get("shannon_gap_db", 2.5)),
        "mcs_margin_db": float(sweep.get("mcs_margin_db", 1.0)),
        "detector": str(sweep.get("detector", "batch_mmse")),
        "speed_kmh": float(sweep.get("speed_kmh", 3.0)),
        "carrier_frequency_ghz": float(sweep.get("carrier_frequency_ghz", 4.0)),
    }


def _summary_from_json_dict(d: Dict[str, Any]) -> SNRSummary:
    cw_stats = [
        CWSimulationStats(
            cw_index=int(cw["cw_index"]),
            trials=int(cw["trials"]),
            errors=int(cw["errors"]),
            tb_size=int(cw["tb_size"]),
            n_cbs=int(cw["n_cbs"]),
            cb_trials=int(cw["cb_trials"]),
            cb_errors=int(cw["cb_errors"]),
            successful_payload_bits=float(cw["successful_payload_bits"]),
        )
        for cw in d.get("cw_stats", [])
    ]
    return SNRSummary(
        scheme_label=str(d["scheme_label"]),
        snr_db=float(d["snr_db"]),
        trials=int(d["trials"]),
        scheme_errors=int(d["scheme_errors"]),
        cw_stats=cw_stats,
        total_throughput_bits_per_slot=float(d["total_throughput_bits_per_slot"]),
        metadata=dict(d.get("metadata", {})),
        n_re_per_layer=int(d.get("n_re_per_layer", 0)),
        rank=int(d.get("rank", 0)),
    )


def _read_csv_rows(path: Path) -> List[Dict[str, object]]:
    if not path.exists():
        return []
    with open(path, "r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _write_rows(rows: List[Dict[str, object]], path: Path) -> None:
    if not rows:
        return
    fieldnames = sorted(set().union(*(row.keys() for row in rows)))
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _aggregate_worker_outputs(worker_dirs: Sequence[Path], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    summaries: List[SNRSummary] = []
    mcs_records: List[Dict[str, object]] = []
    cb_records: List[Dict[str, object]] = []
    result_rows: List[Dict[str, object]] = []
    for worker_dir in worker_dirs:
        json_path = worker_dir / "results.json"
        if json_path.exists():
            with open(json_path, "r", encoding="utf-8") as f:
                summaries.extend(_summary_from_json_dict(d) for d in json.load(f))
        result_rows.extend(_read_csv_rows(worker_dir / "results.csv"))
        mcs_records.extend(_read_csv_rows(worker_dir / "mcs_schedule.csv"))
        cb_records.extend(_read_csv_rows(worker_dir / "cb_schedule.csv"))

    save_csv(summaries, output_dir / "results.csv")
    save_json(summaries, output_dir / "results.json")
    _write_rows(result_rows, output_dir / "results_flat_from_workers.csv")
    _write_rows(mcs_records, output_dir / "mcs_schedule.csv")
    _write_rows(cb_records, output_dir / "cb_schedule.csv")
    plot_bler(summaries, output_dir / "cb_bler_vs_snr.png")
    plot_throughput(summaries, output_dir / "goodput_se_vs_snr.png")
    _plot_mcs_cdf(mcs_records, output_dir / "mcs_cdf.png")
    _plot_cb_histograms(cb_records, output_dir)


def _run_parallel(args: argparse.Namespace, kwargs: Dict[str, Any]) -> None:
    gpus = [x.strip() for x in str(args.parallel_gpus).split(",") if x.strip()]
    if not gpus:
        raise ValueError("--parallel-gpus must contain at least one GPU id.")
    snrs = [float(x) for x in kwargs["snr_db_values"]]
    root_out = Path(args.output_dir_override or kwargs["output_dir"])
    worker_root = root_out / "_workers"
    worker_root.mkdir(parents=True, exist_ok=True)

    worker_dirs = []
    failed = []
    for batch_start in range(0, len(snrs), len(gpus)):
        procs = []
        batch = list(enumerate(snrs))[batch_start:batch_start + len(gpus)]
        for local_idx, (snr_idx, _snr) in enumerate(batch):
            gpu = gpus[local_idx]
            worker_out = worker_root / f"snr_{snr_idx}"
            worker_dirs.append(worker_out)
            cmd = [
                sys.executable,
                str(Path(__file__).resolve()),
                "--config",
                str(args.config),
                "--snr-index",
                str(snr_idx),
                "--output-dir",
                str(worker_out),
            ]
            env = os.environ.copy()
            env["CUDA_VISIBLE_DEVICES"] = gpu
            env.setdefault("TF_FORCE_GPU_ALLOW_GROWTH", "true")
            env.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")
            print(f"[parallel] SNR index {snr_idx} -> GPU {gpu}, output {worker_out}")
            procs.append((snr_idx, subprocess.Popen(cmd, env=env)))

        for snr_idx, proc in procs:
            rc = proc.wait()
            if rc != 0:
                failed.append((snr_idx, rc))
        if failed:
            raise RuntimeError(f"Worker failures: {failed}")

    _aggregate_worker_outputs(worker_dirs, root_out)
    print(f"\n并行仿真完成。聚合结果保存在: {root_out}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Phase3 adaptive-MCS full-resource CSI-error sweep.")
    parser.add_argument("--config", required=True, help="YAML config path.")
    parser.add_argument("--dry-run", action="store_true", help="Validate and print resolved parameters only.")
    parser.add_argument("--snr-index", type=int, default=None, help="Run only one SNR index from the config.")
    parser.add_argument("--output-dir", dest="output_dir_override", default=None, help="Override output_dir.")
    parser.add_argument("--parallel-gpus", default=None, help="Comma-separated GPU ids; run one worker per SNR.")
    args = parser.parse_args()

    cfg = _load_yaml(args.config)
    kwargs = _resolve_kwargs(cfg)
    if args.output_dir_override:
        kwargs["output_dir"] = args.output_dir_override
    if args.snr_index is not None:
        snrs = [float(x) for x in kwargs["snr_db_values"]]
        kwargs["snr_db_values"] = [snrs[int(args.snr_index)]]
        kwargs["snr_index_offset"] = int(args.snr_index)

    if args.dry_run:
        print("Resolved Phase3 adaptive-MCS full-resource sweep:")
        for key, value in kwargs.items():
            print(f"  {key}: {value}")
        return

    if args.parallel_gpus and args.snr_index is None:
        _run_parallel(args, kwargs)
        return

    result = run_phase3_adaptive_mcs_full_resource_sweep(**kwargs)
    print(f"\n仿真完成。结果保存在: {result.output_dir}")


if __name__ == "__main__":
    main()
