import unittest

from lls_platform.sim.sc_mimo_orchestrator import run_rank4_phase2_kbest_ldpc_smoke


class SCMIMOPhase2Tests(unittest.TestCase):
    def test_rank4_kbest_ldpc_smoke_runs_nr_and_sc_mimo_decoded_sic(self):
        result = run_rank4_phase2_kbest_ldpc_smoke(
            seed=31,
            noise_var=0.0,
            list_size=256,
            num_iter=20,
        )

        self.assertEqual(result.detector, "kbest")
        self.assertEqual(result.encoded_cb_lengths, [96, 96, 96, 96])
        self.assertEqual(result.n_re_per_layer, 48)
        self.assertTrue(result.nr_1cw.tb_success)
        self.assertTrue(result.sc_mimo_decoded_sic.tb_success)
        self.assertEqual(result.sc_mimo_decoded_sic.cancellation_count, 4)
        self.assertEqual(result.nr_1cw.goodput_bits, 96)
        self.assertEqual(result.sc_mimo_decoded_sic.goodput_bits, 96)


if __name__ == "__main__":
    unittest.main()
