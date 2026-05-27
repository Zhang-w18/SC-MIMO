from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Dict, Sequence

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from lls_platform.sim.sc_mimo_orchestrator import run_phase3_csi_error_fixed_qpsk_bler_sweep


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


def main() -> None:
    parser = argparse.ArgumentParser(description="Run SC-MIMO Phase3 NMSE CSI-error QPSK BLER sweep.")
    parser.add_argument("--config", required=True, help="YAML config path.")
    parser.add_argument("--dry-run", action="store_true", help="Validate and print resolved sweep parameters only.")
    args = parser.parse_args()

    cfg = _load_yaml(args.config)
    sweep = cfg.get("sweep", {}) or {}
    if not isinstance(sweep, dict):
        raise ValueError("YAML field 'sweep' must be a mapping.")

    csi_cases = _case_tuples(sweep.get("csi_error_cases", []))
    kwargs = {
        "snr_db_values": sweep.get("snr_db_values", [-12.0, -8.0, -4.0, 0.0]),
        "ranks": sweep.get("ranks", [4]),
        "speeds_kmh": sweep.get("speeds_kmh", [3.0, 30.0]),
        "csi_error_cases": csi_cases if csi_cases else None,
        "n_trials_per_snr": int(sweep.get("n_trials_per_snr", 10)),
        "output_dir": sweep.get("output_dir", "results_phase3_csi_error_qpsk_bler"),
        "n_re_per_layer": int(sweep.get("n_re_per_layer", 48)),
        "payload_k_per_cb": int(sweep.get("payload_k_per_cb", 24)),
        "num_iter": int(sweep.get("num_iter", 20)),
        "seed": int(sweep.get("seed", 20260517)),
    }

    if args.dry_run:
        print("Resolved SC-MIMO Phase3 CSI-error sweep:")
        for key, value in kwargs.items():
            print(f"  {key}: {value}")
        return

    result = run_phase3_csi_error_fixed_qpsk_bler_sweep(**kwargs)
    print(f"\n仿真完成。结果保存在: {result.output_dir}")


if __name__ == "__main__":
    main()
