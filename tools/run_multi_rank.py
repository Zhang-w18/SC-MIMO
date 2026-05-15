#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Run multiple ranks by generating temporary configs and invoking run.py.

Designed for lls_platform_v2.8_bit_level.

Key features:
- run.py remains single-rank.
- This script runs rank-list such as 2,4,8,12,16.
- Supports parallel execution across GPUs.
- Supports Scheme7 per-rank partition selection via:
    --scheme7-partitions-yaml configs/scheme7_partitions_rank2_4_8_12_16.yaml

Scheme7 partition YAML semantics:
  rank not written:
      keep base config behavior for Scheme7
  rank: []
      explicitly skip Scheme7 for this rank
  rank:
    - [2, 2]
    - [3, 1]
      run only these Scheme7 partitions
  rank:
    enabled: false
      explicitly skip Scheme7
  rank:
    enabled: true
    partitions:
      - [2, 2]
      - [3, 1]
      run only these Scheme7 partitions

Example:
  python tools/run_multi_rank.py \
    --base-config configs/sionna_ldpc_7ghz_128t16r_cdl_adaptive_base.yaml \
    --rank-list 2,4,8,12,16 \
    --output-root results_v28_7ghz_128t16r_cdl_multi_rank \
    --scheme7-partitions-yaml configs/scheme7_partitions_rank2_4_8_12_16.yaml \
    --parallel \
    --gpu-list 0,1,2,3,4
"""

from __future__ import annotations

import argparse
import concurrent.futures as futures
import copy
import csv
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import yaml


def parse_int_list(s: str) -> List[int]:
    if s is None or str(s).strip() == "":
        return []
    return [int(x.strip()) for x in str(s).split(",") if x.strip() != ""]


def load_yaml(path: Path) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"YAML root must be a mapping: {path}")
    return data


def save_yaml(data: Dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)


def product(xs: Sequence[int]) -> int:
    out = 1
    for x in xs:
        out *= int(x)
    return out


def load_scheme7_partitions(path: Optional[str]) -> Dict[int, Optional[List[List[int]]]]:
    """Load optional Scheme7 partition configuration.

    Return:
        dict rank -> value

    value semantics:
        rank absent: keep base config behavior
        []: explicitly skip Scheme7
        [[...], [...]]: run only listed partitions
    """
    if path is None:
        return {}

    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Scheme7 partitions YAML not found: {p}")

    with open(p, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    raw_map = data.get("rank_partitions", data)
    if raw_map is None:
        return {}
    if not isinstance(raw_map, dict):
        raise ValueError(
            f"Invalid Scheme7 partitions YAML: expected mapping under rank_partitions, got {type(raw_map).__name__}"
        )

    out: Dict[int, Optional[List[List[int]]]] = {}

    for rank_key, spec in raw_map.items():
        rank = int(rank_key)

        # Explicit skip: rank: []
        if spec == []:
            out[rank] = []
            continue

        # Explicit dict style
        if isinstance(spec, dict):
            enabled = bool(spec.get("enabled", True))
            if not enabled:
                out[rank] = []
                continue
            partitions = spec.get("partitions", [])
        else:
            partitions = spec

        # None means ignore this rank entry and keep default behavior.
        if partitions is None:
            continue

        if not isinstance(partitions, list):
            raise ValueError(
                f"Invalid Scheme7 partition specification for rank {rank}: "
                f"expected list or dict, got {type(partitions).__name__}"
            )

        # Empty list also means explicitly skip.
        if len(partitions) == 0:
            out[rank] = []
            continue

        checked: List[List[int]] = []
        for partition in partitions:
            if not isinstance(partition, list):
                raise ValueError(
                    f"Invalid Scheme7 partition {partition!r} for rank {rank}: "
                    f"each partition must be a list like [2,2,1]"
                )

            partition = [int(x) for x in partition]

            if len(partition) == 0:
                raise ValueError(
                    f"Invalid Scheme7 partition [] for rank {rank}: "
                    f"use rank_partitions: {rank}: [] to skip Scheme7, "
                    f"not a list containing an empty partition."
                )

            if any(x <= 0 for x in partition):
                raise ValueError(
                    f"Invalid Scheme7 partition {partition} for rank {rank}: "
                    f"all entries must be positive"
                )

            if sum(partition) != rank:
                raise ValueError(
                    f"Invalid Scheme7 partition {partition} for rank {rank}: "
                    f"sum(partition)={sum(partition)}"
                )

            checked.append(partition)

        out[rank] = checked

    return out


def get_base_scheme_specs(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    comp = config.get("comparison", {}) or {}
    if comp.get("enabled", False):
        schemes = comp.get("schemes", []) or []
        if not isinstance(schemes, list):
            raise ValueError("comparison.schemes must be a list")
        return [dict(x or {}) for x in schemes]

    mapping = config.get("mapping", {}) or {}
    return [{"scheme_id": int(mapping.get("scheme_id", 1)), "params": dict(mapping.get("params", {}) or {})}]


def scheme_applicable_for_rank(scheme_id: int, rank: int) -> bool:
    # Scheme1 is strict NR baseline in current platform and only applies to rank <= 8.
    if int(scheme_id) == 1 and int(rank) > 8:
        return False
    return True


def build_scheme_specs_for_rank(
    base_config: Dict[str, Any],
    rank: int,
    scheme7_partitions_by_rank: Dict[int, Optional[List[List[int]]]],
) -> List[Dict[str, Any]]:
    """Build comparison.schemes for one rank.

    Scheme7 policy:
    - rank absent from scheme7_partitions_by_rank:
        preserve base config Scheme7 entries.
    - rank present with []:
        skip Scheme7 entirely.
    - rank present with partitions:
        replace any base Scheme7 entries with the listed partitions.
    """
    base_specs = get_base_scheme_specs(base_config)
    rank = int(rank)

    rank_has_scheme7_policy = rank in scheme7_partitions_by_rank
    rank_scheme7_partitions = scheme7_partitions_by_rank.get(rank)

    out: List[Dict[str, Any]] = []
    seen_non7: List[Dict[str, Any]] = []

    for spec in base_specs:
        scheme_id = int(spec.get("scheme_id", base_config.get("mapping", {}).get("scheme_id", 1)))

        if not scheme_applicable_for_rank(scheme_id, rank):
            continue

        if scheme_id == 7:
            # If policy exists, skip base Scheme7 entries. We'll add configured entries below.
            if rank_has_scheme7_policy:
                continue
            # No policy: preserve base behavior.
            clean = copy.deepcopy(spec)
            clean.pop("rank", None)
            out.append(clean)
        else:
            clean = copy.deepcopy(spec)
            clean.pop("rank", None)
            seen_non7.append(clean)
            out.append(clean)

    if rank_has_scheme7_policy:
        if rank_scheme7_partitions == []:
            # Explicitly skip Scheme7.
            pass
        else:
            assert rank_scheme7_partitions is not None
            for partition in rank_scheme7_partitions:
                out.append({
                    "scheme_id": 7,
                    "params": {
                        "partition": [int(x) for x in partition]
                    }
                })

    if not out:
        raise ValueError(f"No scheme remains for rank {rank}. Please check comparison.schemes and Scheme7 config.")

    return out


def make_config_for_rank(
    base_config: Dict[str, Any],
    rank: int,
    output_root: Path,
    scheme7_partitions_by_rank: Dict[int, Optional[List[List[int]]]],
) -> Dict[str, Any]:
    cfg = copy.deepcopy(base_config)
    rank = int(rank)

    cfg.setdefault("mapping", {})
    cfg["mapping"]["rank"] = rank

    # Ensure mapping top-level scheme_id won't accidentally force one scheme when comparison is enabled.
    cfg.setdefault("comparison", {})
    cfg["comparison"]["enabled"] = True
    cfg["comparison"]["schemes"] = build_scheme_specs_for_rank(
        base_config=base_config,
        rank=rank,
        scheme7_partitions_by_rank=scheme7_partitions_by_rank,
    )

    cfg.setdefault("simulation", {})
    cfg["simulation"]["output_dir"] = str(output_root / "runs" / f"rank_{rank}")

    return cfg


def validate_rank_against_antennas(config: Dict[str, Any], rank: int) -> None:
    ant = config.get("antenna", {}) or {}
    bs_n = int(ant.get("bs_n_antennas", 0))
    ue_n = int(ant.get("ue_n_antennas", 0))

    if rank > bs_n:
        raise ValueError(f"rank={rank} exceeds bs_n_antennas={bs_n}")
    if rank > ue_n:
        raise ValueError(f"rank={rank} exceeds ue_n_antennas={ue_n}")

    bs_arr = ant.get("bs_antenna_array", None)
    ue_arr = ant.get("ue_antenna_array", None)
    if isinstance(bs_arr, list) and len(bs_arr) > 0:
        bs_prod = product(bs_arr)
        if bs_prod != bs_n:
            raise ValueError(f"bs_n_antennas={bs_n} does not match product(bs_antenna_array)={bs_prod}")
    if isinstance(ue_arr, list) and len(ue_arr) > 0:
        ue_prod = product(ue_arr)
        if ue_prod != ue_n:
            raise ValueError(f"ue_n_antennas={ue_n} does not match product(ue_antenna_array)={ue_prod}")


def find_latest_results_csv(run_dir: Path) -> Optional[Path]:
    if not run_dir.exists():
        return None
    candidates = sorted(run_dir.glob("sim_*/results.csv"), key=lambda p: p.stat().st_mtime, reverse=True)
    if candidates:
        return candidates[0]
    direct = run_dir / "results.csv"
    if direct.exists():
        return direct
    return None


def run_one_rank(
    rank: int,
    config_path: Path,
    output_root: Path,
    python_exe: str,
    run_py: str,
    gpu_id: Optional[int],
    extra_env: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    rank = int(rank)
    log_dir = output_root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"rank_{rank}.log"

    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    if gpu_id is not None:
        env["CUDA_VISIBLE_DEVICES"] = str(gpu_id)

    cmd = [python_exe, run_py, "--config", str(config_path)]

    with open(log_path, "w", encoding="utf-8") as log_f:
        log_f.write(f"Command: {' '.join(cmd)}\n")
        log_f.write(f"CUDA_VISIBLE_DEVICES={env.get('CUDA_VISIBLE_DEVICES', '')}\n")
        log_f.write("=" * 80 + "\n")
        log_f.flush()

        try:
            subprocess.run(
                cmd,
                stdout=log_f,
                stderr=subprocess.STDOUT,
                check=True,
                env=env,
                cwd=str(Path.cwd()),
            )
        except subprocess.CalledProcessError as e:
            return {
                "rank": rank,
                "ok": False,
                "gpu": gpu_id,
                "log": str(log_path),
                "error": repr(e),
                "results_csv": None,
            }

    run_dir = output_root / "runs" / f"rank_{rank}"
    results_csv = find_latest_results_csv(run_dir)

    return {
        "rank": rank,
        "ok": results_csv is not None,
        "gpu": gpu_id,
        "log": str(log_path),
        "error": None if results_csv is not None else f"results.csv not found under {run_dir}",
        "results_csv": str(results_csv) if results_csv is not None else None,
    }


def merge_results(run_results: List[Dict[str, Any]], merged_csv: Path) -> None:
    ok_results = [r for r in run_results if r.get("ok") and r.get("results_csv")]
    if not ok_results:
        raise RuntimeError("No successful rank results to merge.")

    rows: List[Dict[str, Any]] = []
    all_fields: List[str] = []

    for r in sorted(ok_results, key=lambda x: int(x["rank"])):
        rank = int(r["rank"])
        path = Path(r["results_csv"])
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            fields = reader.fieldnames or []
            for field in fields:
                if field not in all_fields:
                    all_fields.append(field)

            for row in reader:
                row["rank"] = str(rank)
                row["source_results_csv"] = str(path)
                rows.append(row)

    # Ensure key fields are near the front.
    front = ["rank", "scheme", "snr_db", "scheme_bler", "scheme_cb_bler",
             "goodput_bits_per_slot", "goodput_se_tf", "goodput_se_layer_re", "trials",
             "source_results_csv"]
    final_fields = []
    for f in front:
        if f not in final_fields:
            final_fields.append(f)
    for f in all_fields:
        if f not in final_fields:
            final_fields.append(f)
    if "source_results_csv" not in final_fields:
        final_fields.append("source_results_csv")

    merged_csv.parent.mkdir(parents=True, exist_ok=True)
    with open(merged_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=final_fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def copy_layer_samples(run_results: List[Dict[str, Any]], output_root: Path) -> None:
    """Optional convenience: collect per-rank layer_post_sinr_samples.csv files."""
    out_dir = output_root / "layer_post_sinr_samples_by_rank"
    out_dir.mkdir(parents=True, exist_ok=True)

    for r in run_results:
        if not r.get("ok") or not r.get("results_csv"):
            continue
        rank = int(r["rank"])
        sim_dir = Path(r["results_csv"]).parent
        src = sim_dir / "layer_post_sinr_samples.csv"
        if src.exists():
            dst = out_dir / f"rank_{rank}_layer_post_sinr_samples.csv"
            shutil.copy2(src, dst)


def print_plan(rank_list: List[int], config_paths: Dict[int, Path], output_root: Path, gpu_list: List[int], parallel: bool) -> None:
    print("=" * 80, flush=True)
    print("Multi-rank run plan", flush=True)
    print(f"Ranks: {rank_list}", flush=True)
    print(f"Output root: {output_root}", flush=True)
    print(f"Parallel: {parallel}", flush=True)
    print(f"GPU list: {gpu_list if gpu_list else 'not specified'}", flush=True)
    for rank in rank_list:
        print(f"  rank {rank}: {config_paths[rank]}", flush=True)
    print("=" * 80, flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run lls_platform for multiple ranks.")
    parser.add_argument("--base-config", required=True, help="Base YAML config path.")
    parser.add_argument("--rank-list", required=True, help="Comma-separated ranks, e.g. 2,4,8,12,16.")
    parser.add_argument("--output-root", required=True, help="Output root for multi-rank run.")
    parser.add_argument("--scheme7-partitions-yaml", default=None, help="Optional YAML for per-rank Scheme7 partitions.")
    parser.add_argument("--parallel", action="store_true", help="Run ranks in parallel.")
    parser.add_argument("--gpu-list", default="", help="Comma-separated GPU IDs, e.g. 0,1,2,3.")
    parser.add_argument("--python", default=sys.executable, help="Python executable for run.py.")
    parser.add_argument("--run-py", default="run.py", help="Path to run.py.")
    parser.add_argument("--dry-run", action="store_true", help="Only generate configs and print plan.")
    args = parser.parse_args()

    base_config_path = Path(args.base_config)
    output_root = Path(args.output_root)
    output_root.mkdir(parents=True, exist_ok=True)

    rank_list = parse_int_list(args.rank_list)
    if not rank_list:
        raise ValueError("--rank-list is empty")

    gpu_list = parse_int_list(args.gpu_list)
    scheme7_partitions_by_rank = load_scheme7_partitions(args.scheme7_partitions_yaml)

    base_config = load_yaml(base_config_path)

    temp_config_dir = output_root / "temp_configs"
    temp_config_dir.mkdir(parents=True, exist_ok=True)

    config_paths: Dict[int, Path] = {}
    for rank in rank_list:
        validate_rank_against_antennas(base_config, rank)
        cfg = make_config_for_rank(
            base_config=base_config,
            rank=rank,
            output_root=output_root,
            scheme7_partitions_by_rank=scheme7_partitions_by_rank,
        )
        config_path = temp_config_dir / f"config_rank_{rank}.yaml"
        save_yaml(cfg, config_path)
        config_paths[rank] = config_path

    print_plan(rank_list, config_paths, output_root, gpu_list, args.parallel)

    if args.dry_run:
        print("Dry run only. Configs generated.", flush=True)
        return

    run_results: List[Dict[str, Any]] = []

    if args.parallel:
        if not gpu_list:
            raise ValueError("--parallel requires --gpu-list")

        max_workers = min(len(gpu_list), len(rank_list))
        print(f"Launching parallel jobs with {max_workers} workers...", flush=True)

        with futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
            futs = []
            for idx, rank in enumerate(rank_list):
                gpu = gpu_list[idx % len(gpu_list)]
                print(f"[GPU {gpu}] Start rank {rank}, log: {output_root / 'logs' / f'rank_{rank}.log'}", flush=True)
                futs.append(ex.submit(
                    run_one_rank,
                    rank,
                    config_paths[rank],
                    output_root,
                    args.python,
                    args.run_py,
                    gpu,
                ))

            for fut in futures.as_completed(futs):
                res = fut.result()
                run_results.append(res)
                rank = res["rank"]
                gpu = res.get("gpu")
                if res.get("ok"):
                    print(f"[GPU {gpu}] Rank {rank} done: {res.get('results_csv')}", flush=True)
                else:
                    print(f"[GPU {gpu}] Rank {rank} failed, log: {res.get('log')}", flush=True)

    else:
        print("Launching serial jobs...", flush=True)
        for idx, rank in enumerate(rank_list):
            gpu = gpu_list[idx % len(gpu_list)] if gpu_list else None
            print(f"[GPU {gpu}] Start rank {rank}, log: {output_root / 'logs' / f'rank_{rank}.log'}", flush=True)
            res = run_one_rank(
                rank=rank,
                config_path=config_paths[rank],
                output_root=output_root,
                python_exe=args.python,
                run_py=args.run_py,
                gpu_id=gpu,
            )
            run_results.append(res)
            if res.get("ok"):
                print(f"[GPU {gpu}] Rank {rank} done: {res.get('results_csv')}", flush=True)
            else:
                print(f"[GPU {gpu}] Rank {rank} failed, log: {res.get('log')}", flush=True)

    failed = [r for r in run_results if not r.get("ok")]
    if failed:
        msgs = []
        for r in sorted(failed, key=lambda x: int(x["rank"])):
            msgs.append(f"Rank {r['rank']} failed: {r.get('error')}; log={r.get('log')}")
        raise RuntimeError("Some rank jobs failed:\n" + "\n".join(msgs))

    merged_csv = output_root / "merged_results.csv"
    merge_results(run_results, merged_csv)
    copy_layer_samples(run_results, output_root)

    print("=" * 80, flush=True)
    print(f"All ranks completed.", flush=True)
    print(f"Merged results: {merged_csv}", flush=True)
    print(f"Layer samples copied to: {output_root / 'layer_post_sinr_samples_by_rank'}", flush=True)


if __name__ == "__main__":
    main()
