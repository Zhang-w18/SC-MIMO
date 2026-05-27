import unittest

import numpy as np

from lls_platform.core.config import ResourceConfig
from lls_platform.sim.sc_mimo_orchestrator import (
    SionnaLDPCAdapter,
    build_rank2_fixed_mcs_tb_setup,
    deterministic_rank2_channel,
    map_nr_encoded_cbs_to_layer_grid,
    map_sc_mimo_encoded_cbs_to_layer_grid,
    run_rank2_phase1b_single_snr_comparison,
    run_rank2_ldpc_true_mimo_trial,
    run_rank2_ldpc_true_mimo_trial_from_tb_manager,
)


class SCMIMOPhase1BLDPCTests(unittest.TestCase):
    def test_sionna_ldpc_adapter_llr_sign_convention(self):
        payload = [np.asarray([0, 1] * 12, dtype=np.int8)]
        adapter = SionnaLDPCAdapter(cb_k_values=[24], cb_e_values=[96], num_iter=20)
        coded = adapter.encode(payload)[0]

        # The project detector convention is LLR > 0 => hard bit 1. Sionna's
        # LDPC decoder accepts the same sign convention for this path.
        llr_same_sign = (2.0 * coded.astype(np.float32) - 1.0) * 20.0
        decoded = adapter.decode([llr_same_sign], payload)
        self.assertEqual(decoded.cb_success, [True])
        self.assertTrue(decoded.tb_success)

    def test_tx_mapping_branches_share_encoded_cbs_but_preserve_sc_mimo_cb_boundaries(self):
        qm = 2
        cb_e_values = [96, 96, 96]
        n_re = sum(cb_e_values) // (2 * qm)
        payload = [
            np.asarray(([0, 1] * 12), dtype=np.int8),
            np.asarray(([1, 1, 0, 0] * 6), dtype=np.int8),
            np.asarray(([0, 0, 1, 1, 1, 0] * 4), dtype=np.int8),
        ]
        adapter = SionnaLDPCAdapter(cb_k_values=[len(bits) for bits in payload], cb_e_values=cb_e_values)
        encoded_cbs = adapter.encode(payload)

        nr_grid = map_nr_encoded_cbs_to_layer_grid(
            encoded_cbs,
            qm=qm,
            n_re_per_layer=n_re,
        )
        sc_grid, sc_plan = map_sc_mimo_encoded_cbs_to_layer_grid(
            encoded_cbs,
            cb_e_values=cb_e_values,
            qm=qm,
            n_re_per_layer=n_re,
        )

        self.assertEqual(nr_grid.shape, (n_re, 2))
        self.assertEqual(sc_grid.shape, (n_re, 2))
        self.assertEqual(sc_plan.n_cbs, 3)
        self.assertEqual(sc_plan.cb_symbol_counts, [48, 48, 48])
        self.assertEqual([a.cb_index for a in sc_plan.assignments_for_layer(0)], [0, 1, 2])
        self.assertEqual([a.cb_index for a in sc_plan.assignments_for_layer(1)], [2, 0, 1])

    def test_rank2_ldpc_nr_and_sc_mimo_noiseless_decode(self):
        qm = 2
        cb_e_values = [96, 96, 96]
        n_re = sum(cb_e_values) // (2 * qm)
        payload = [
            np.asarray(([0, 1] * 12), dtype=np.int8),
            np.asarray(([1, 1, 0, 0] * 6), dtype=np.int8),
            np.asarray(([0, 0, 1, 1, 1, 0] * 4), dtype=np.int8),
        ]
        h_eff = deterministic_rank2_channel(n_re)

        nr = run_rank2_ldpc_true_mimo_trial(
            payload,
            cb_e_values=cb_e_values,
            qm=qm,
            n_re_per_layer=n_re,
            mapping="nr",
            sic_mode="no_sic",
            h_eff=h_eff,
            noise_var=0.0,
            num_iter=20,
        )
        self.assertTrue(nr.tb_success)
        self.assertEqual(nr.cb_success, [True, True, True])
        self.assertEqual(nr.goodput_bits, 72)

        sc_no_sic = run_rank2_ldpc_true_mimo_trial(
            payload,
            cb_e_values=cb_e_values,
            qm=qm,
            n_re_per_layer=n_re,
            mapping="sc_mimo",
            sic_mode="no_sic",
            h_eff=h_eff,
            noise_var=0.0,
            num_iter=20,
        )
        self.assertTrue(sc_no_sic.tb_success)
        self.assertEqual(sc_no_sic.cb_success, [True, True, True])

        sc_decoded_sic = run_rank2_ldpc_true_mimo_trial(
            payload,
            cb_e_values=cb_e_values,
            qm=qm,
            n_re_per_layer=n_re,
            mapping="sc_mimo",
            sic_mode="decoded_sic",
            h_eff=h_eff,
            noise_var=0.0,
            num_iter=20,
        )
        self.assertTrue(sc_decoded_sic.tb_success)
        self.assertEqual(sc_decoded_sic.cb_success, [True, True, True])
        self.assertEqual(sc_decoded_sic.cancellation_count, 3)

        sc_ideal_sic = run_rank2_ldpc_true_mimo_trial(
            payload,
            cb_e_values=cb_e_values,
            qm=qm,
            n_re_per_layer=n_re,
            mapping="sc_mimo",
            sic_mode="ideal_sic",
            h_eff=h_eff,
            noise_var=0.0,
            num_iter=20,
        )
        self.assertTrue(sc_ideal_sic.tb_success)
        self.assertEqual(sc_ideal_sic.cb_success, [True, True, True])
        self.assertEqual(sc_ideal_sic.cancellation_count, 3)
        self.assertLess(sc_ideal_sic.residual_energy_after_cancellation, 1e-18)

    def test_rank2_ldpc_trial_uses_tb_manager_generated_sizes(self):
        setup = build_rank2_fixed_mcs_tb_setup(fixed_mcs=3)
        self.assertEqual(setup.qm, 2)
        self.assertEqual(sum(setup.cb_e_values), setup.n_re_per_layer * 2 * setup.qm)
        self.assertEqual(len(setup.payload_bit_lengths), int(setup.tb_info.n_cbs))
        self.assertEqual(setup.payload_bit_lengths, [int(setup.tb_info.tb_size)])
        self.assertTrue(all(e % setup.qm == 0 for e in setup.cb_e_values))

        result, used_setup = run_rank2_ldpc_true_mimo_trial_from_tb_manager(
            fixed_mcs=3,
            mapping="sc_mimo",
            sic_mode="decoded_sic",
            rng=np.random.default_rng(7),
            noise_var=0.0,
            num_iter=20,
        )
        self.assertEqual(used_setup.cb_e_values, setup.cb_e_values)
        self.assertTrue(result.tb_success)
        self.assertEqual(result.goodput_bits, int(setup.tb_info.tb_size))
        self.assertEqual(result.cancellation_count, int(setup.tb_info.n_cbs))

    def test_rank2_ldpc_tb_manager_high_snr_smoke_outputs_metrics(self):
        result, setup = run_rank2_ldpc_true_mimo_trial_from_tb_manager(
            fixed_mcs=3,
            mapping="sc_mimo",
            sic_mode="decoded_sic",
            rng=np.random.default_rng(11),
            noise_var=1e-5,
            num_iter=20,
        )
        self.assertEqual(len(result.cb_success), int(setup.tb_info.n_cbs))
        self.assertGreaterEqual(result.cb_bler, 0.0)
        self.assertLessEqual(result.cb_bler, 1.0)
        self.assertGreaterEqual(result.tb_bler, 0.0)
        self.assertLessEqual(result.tb_bler, 1.0)
        self.assertTrue(result.tb_success)
        self.assertEqual(result.goodput_bits, int(setup.tb_info.tb_size))

    def test_rank2_phase1b_single_snr_comparison_runs_nr_and_sc_mimo(self):
        result = run_rank2_phase1b_single_snr_comparison(
            fixed_mcs=3,
            seed=19,
            noise_var=1e-5,
            num_iter=20,
        )
        self.assertEqual(result.encoded_cb_lengths, result.setup.cb_e_values)
        self.assertTrue(result.nr_no_sic.tb_success)
        self.assertTrue(result.sc_no_sic.tb_success)
        self.assertTrue(result.sc_ideal_sic.tb_success)
        self.assertTrue(result.sc_decoded_sic.tb_success)
        self.assertEqual(result.nr_no_sic.goodput_bits, int(result.setup.tb_info.tb_size))
        self.assertEqual(result.sc_decoded_sic.goodput_bits, int(result.setup.tb_info.tb_size))
        self.assertEqual(result.sc_decoded_sic.cancellation_count, int(result.setup.tb_info.n_cbs))

    def test_rank2_phase1b_tb_manager_multi_cb_full_link_smoke(self):
        resource = ResourceConfig(n_prbs=31, pdsch_n_symbols=13, reserve_dmrs_re=False)
        setup = build_rank2_fixed_mcs_tb_setup(resource=resource, fixed_mcs=3)
        self.assertGreater(int(setup.tb_info.n_cbs), 1)
        self.assertEqual(len(setup.cb_e_values), int(setup.tb_info.n_cbs))
        self.assertEqual(sum(setup.cb_e_values), setup.n_re_per_layer * 2 * setup.qm)

        result = run_rank2_phase1b_single_snr_comparison(
            resource=resource,
            fixed_mcs=3,
            seed=23,
            noise_var=0.0,
            num_iter=20,
        )
        self.assertGreater(int(result.setup.tb_info.n_cbs), 1)
        self.assertTrue(result.nr_no_sic.tb_success)
        self.assertTrue(result.sc_decoded_sic.tb_success)
        self.assertEqual(result.sc_decoded_sic.cancellation_count, int(result.setup.tb_info.n_cbs))
        self.assertEqual(result.sc_decoded_sic.goodput_bits, int(result.setup.tb_info.tb_size))


if __name__ == "__main__":
    unittest.main()
