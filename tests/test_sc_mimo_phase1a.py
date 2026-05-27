import unittest

import numpy as np

from lls_platform.phy.sc_mimo_mapping import (
    build_rank2_cyclic_sc_mimo_plan,
    map_rank2_cb_bits_to_layer_grid,
    reconstruct_rank2_cb_layer_grid,
)
from lls_platform.sim.sc_mimo_orchestrator import (
    apply_true_mimo_channel,
    collect_nr_rank2_cb_bits_from_detection,
    collect_scmimo_cb_bits_from_detection,
    deterministic_rank2_channel,
    exhaustive_detect_layer_grid,
    map_nr_rank2_cb_bits_to_layer_grid,
    residual_after_ideal_cb_cancellation,
    residual_energy,
)


class SCMIMOPhase1ATests(unittest.TestCase):
    def test_nr_and_sc_mimo_noiseless_true_mimo_detection(self):
        qm = 2
        n_re = 6
        cb_bits = [
            np.asarray([0, 0, 0, 1, 1, 0, 1, 1], dtype=np.int8),
            np.asarray([1, 1, 1, 0, 0, 1, 0, 0], dtype=np.int8),
            np.asarray([0, 1, 1, 1, 1, 0, 0, 0], dtype=np.int8),
        ]
        plan = build_rank2_cyclic_sc_mimo_plan(
            cb_symbol_counts=[4, 4, 4],
            qm=qm,
            n_re_per_layer=n_re,
            shift=1,
        )
        h_eff = deterministic_rank2_channel(n_re)

        nr_grid = map_nr_rank2_cb_bits_to_layer_grid(cb_bits, qm=qm, n_re_per_layer=n_re)
        y_nr = apply_true_mimo_channel(nr_grid, h_eff, noise_var=0.0)
        det_nr = exhaustive_detect_layer_grid(y_nr, h_eff, qm=qm, noise_var=1e-9)
        nr_detected = collect_nr_rank2_cb_bits_from_detection(
            det_nr.hard_bits,
            cb_bit_lengths=[len(x) for x in cb_bits],
        )
        for got, expected in zip(nr_detected, cb_bits):
            self.assertTrue(np.array_equal(got, expected))

        sc_grid = map_rank2_cb_bits_to_layer_grid(cb_bits, plan)
        y_sc = apply_true_mimo_channel(sc_grid, h_eff, noise_var=0.0)
        det_sc = exhaustive_detect_layer_grid(y_sc, h_eff, qm=qm, noise_var=1e-9)
        sc_detected = collect_scmimo_cb_bits_from_detection(det_sc.hard_bits, plan)
        for got, expected in zip(sc_detected, cb_bits):
            self.assertTrue(np.array_equal(got, expected))

        self.assertEqual([a.cb_index for a in plan.assignments_for_layer(1)], [2, 0, 1])

    def test_ideal_cb_cancellation_residual_matches_remaining_grid(self):
        qm = 2
        n_re = 6
        cb_bits = [
            np.asarray([0, 0, 0, 1, 1, 0, 1, 1], dtype=np.int8),
            np.asarray([1, 1, 1, 0, 0, 1, 0, 0], dtype=np.int8),
            np.asarray([0, 1, 1, 1, 1, 0, 0, 0], dtype=np.int8),
        ]
        plan = build_rank2_cyclic_sc_mimo_plan(
            cb_symbol_counts=[4, 4, 4],
            qm=qm,
            n_re_per_layer=n_re,
            shift=1,
        )
        h_eff = deterministic_rank2_channel(n_re)
        full_grid = map_rank2_cb_bits_to_layer_grid(cb_bits, plan)
        y_full = apply_true_mimo_channel(full_grid, h_eff, noise_var=0.0)

        cb0_grid = reconstruct_rank2_cb_layer_grid(cb_bits[0], 0, plan)
        y_res = residual_after_ideal_cb_cancellation(y_full, h_eff, cb0_grid)
        expected_remaining = apply_true_mimo_channel(full_grid - cb0_grid, h_eff, noise_var=0.0)

        self.assertLess(residual_energy(y_res - expected_remaining), 1e-24)

        y_only_cb0 = apply_true_mimo_channel(cb0_grid, h_eff, noise_var=0.0)
        self.assertLess(
            residual_energy(residual_after_ideal_cb_cancellation(y_only_cb0, h_eff, cb0_grid)),
            1e-24,
        )


if __name__ == "__main__":
    unittest.main()
