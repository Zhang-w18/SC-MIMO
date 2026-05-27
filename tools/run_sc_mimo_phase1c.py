from __future__ import annotations

import argparse
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from lls_platform.core.config import ResourceConfig
from lls_platform.sim.sc_mimo_orchestrator import run_rank2_phase1c_snr_sweep


def _parse_snr_values(text: str) -> list[float]:
    vals = [float(x.strip()) for x in str(text).split(",") if x.strip()]
    if not vals:
        raise argparse.ArgumentTypeError("SNR list must not be empty.")
    return vals


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Phase 1c rank2 SC-MIMO vs NR fixed-MCS toy sweep.")
    parser.add_argument("--snr-db", type=_parse_snr_values, default=[-2.0, 0.0, 2.0, 4.0, 6.0],
                        help="Comma-separated SNR values in dB. Default: -2,0,2,4,6")
    parser.add_argument("--trials", type=int, default=5, help="Trials per SNR point. Default: 5")
    parser.add_argument("--fixed-mcs", type=int, default=3, help="Fixed MCS index. Default: 3")
    parser.add_argument("--seed", type=int, default=123, help="Base random seed. Default: 123")
    parser.add_argument("--n-prbs", type=int, default=6, help="Resource PRBs. Default: 6")
    parser.add_argument("--pdsch-symbols", type=int, default=4, help="PDSCH data symbols. Default: 4")
    parser.add_argument("--ldpc-iters", type=int, default=20, help="LDPC decoder iterations. Default: 20")
    parser.add_argument("--output-dir", default="results/sc_mimo_phase1c_rank2_fixed_mcs",
                        help="Output directory for CSV/JSON/PNG files.")
    args = parser.parse_args()

    resource = ResourceConfig(
        n_prbs=int(args.n_prbs),
        pdsch_n_symbols=int(args.pdsch_symbols),
        reserve_dmrs_re=False,
    )
    result = run_rank2_phase1c_snr_sweep(
        snr_db_values=args.snr_db,
        n_trials_per_snr=int(args.trials),
        output_dir=Path(args.output_dir),
        resource=resource,
        fixed_mcs=int(args.fixed_mcs),
        seed=int(args.seed),
        num_iter=int(args.ldpc_iters),
    )
    print(f"Phase 1c results written to: {result.output_dir}")
    print(f"Summary rows: {len(result.summaries)}")
    print(f"CSV: {result.output_dir / 'results.csv'}")
    print(f"JSON: {result.output_dir / 'results.json'}")
    print(f"CB-BLER curve: {result.output_dir / 'cb_bler_vs_snr.png'}")
    print(f"Goodput curve: {result.output_dir / 'goodput_se_vs_snr.png'}")


if __name__ == "__main__":
    main()
