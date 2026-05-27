import tempfile
import unittest
from pathlib import Path

from lls_platform.core.config import ResourceConfig
from lls_platform.sim.sc_mimo_orchestrator import run_rank2_phase1c_snr_sweep


class SCMIMOPhase1CSweepTests(unittest.TestCase):
    def test_rank2_phase1c_sweep_writes_curves_and_results(self):
        resource = ResourceConfig(n_prbs=6, pdsch_n_symbols=4, reserve_dmrs_re=False)
        with tempfile.TemporaryDirectory() as td:
            result = run_rank2_phase1c_snr_sweep(
                snr_db_values=[0.0, 4.0],
                n_trials_per_snr=1,
                output_dir=td,
                resource=resource,
                fixed_mcs=3,
                seed=101,
                num_iter=20,
            )
            out = Path(td)
            self.assertEqual(len(result.summaries), 8)
            self.assertTrue((out / "results.csv").exists())
            self.assertTrue((out / "results.json").exists())
            self.assertTrue((out / "cb_bler_vs_snr.png").exists())
            self.assertTrue((out / "goodput_se_vs_snr.png").exists())
            labels = {s.scheme_label for s in result.summaries}
            self.assertEqual(labels, {
                "nr_no_sic",
                "sc_mimo_no_sic",
                "sc_mimo_ideal_sic",
                "sc_mimo_decoded_sic",
            })


if __name__ == "__main__":
    unittest.main()
