import unittest

import numpy as np

from lls_platform.phy.mimo_detection import exhaustive_qam_mimo_detect
from lls_platform.phy.sc_mimo_mapping import (
    build_rank2_cyclic_sc_mimo_plan,
    cb_symbol_counts_from_e,
    map_rank2_cb_bits_to_layer_grid,
    reconstruct_rank2_cb_layer_grid,
)
from lls_platform.phy.numpy_qam import qam_modulate


class SCMIMOMappingTests(unittest.TestCase):
    def test_rank2_cyclic_plan_covers_cb_symbols_and_resources(self):
        plan = build_rank2_cyclic_sc_mimo_plan(
            cb_symbol_counts=[6, 6, 6],
            qm=4,
            n_re_per_layer=9,
            shift=1,
        )

        self.assertEqual(plan.rank, 2)
        self.assertEqual(plan.n_cbs, 3)
        self.assertEqual(plan.mapping_rule, "sc_mimo_rank2_cyclic_staggered_cb")

        # Layer 0 is natural CB order for part0.
        self.assertEqual([a.cb_index for a in plan.assignments_for_layer(0)], [0, 1, 2])
        # Layer 1 is cyclically shifted by one CB for part1.
        self.assertEqual([a.cb_index for a in plan.assignments_for_layer(1)], [2, 0, 1])

        for cb_idx in range(plan.n_cbs):
            assigns = plan.assignments_for_cb(cb_idx)
            self.assertEqual(len(assigns), 2)
            bit_seen = []
            symbol_seen = []
            for a in assigns:
                bit_seen.extend(a.bit_indices.tolist())
                symbol_seen.extend(a.symbol_indices.tolist())
            self.assertEqual(sorted(bit_seen), list(range(6 * 4)))
            self.assertEqual(sorted(symbol_seen), list(range(6)))

    def test_cb_symbol_counts_require_qm_alignment(self):
        self.assertEqual(cb_symbol_counts_from_e([24, 28], qm=4), [6, 7])
        with self.assertRaisesRegex(ValueError, "not divisible"):
            cb_symbol_counts_from_e([25], qm=4)

    def test_exhaustive_qam_mimo_detector_noiseless_rank2_qpsk(self):
        bits = np.asarray([[0, 1], [1, 0]], dtype=np.int8)
        x = np.asarray([qam_modulate(bits[0], 2)[0], qam_modulate(bits[1], 2)[0]])
        h = np.asarray([[1.0 + 0.0j, 0.3 + 0.2j], [0.1 - 0.1j, 1.2 + 0.0j]])
        y = h.dot(x)

        res = exhaustive_qam_mimo_detect(y, h, qm=2, noise_var=1e-6)
        self.assertTrue(np.array_equal(res.best_bits, bits))
        hard_from_llr = (res.llr.reshape(bits.shape) > 0).astype(np.int8)
        self.assertTrue(np.array_equal(hard_from_llr, bits))

    def test_rank2_cb_bits_mapper_and_single_cb_reconstruction(self):
        plan = build_rank2_cyclic_sc_mimo_plan(
            cb_symbol_counts=[4, 4, 4],
            qm=2,
            n_re_per_layer=6,
            shift=1,
        )
        cb_bits = [
            np.asarray([0, 0, 0, 1, 1, 0, 1, 1], dtype=np.int8),
            np.asarray([1, 1, 1, 0, 0, 1, 0, 0], dtype=np.int8),
            np.asarray([0, 1, 1, 1, 1, 0, 0, 0], dtype=np.int8),
        ]

        full_grid = map_rank2_cb_bits_to_layer_grid(cb_bits, plan)
        reconstructed = np.zeros_like(full_grid)
        for cb_idx, bits in enumerate(cb_bits):
            reconstructed += reconstruct_rank2_cb_layer_grid(bits, cb_idx, plan)

        self.assertTrue(np.allclose(full_grid, reconstructed))

        # The shifted layer-1 order is CB2, CB0, CB1. Therefore CB0 contributes
        # to layer 0 at its natural first two REs and to layer 1 after CB2's part.
        cb0_grid = reconstruct_rank2_cb_layer_grid(cb_bits[0], 0, plan)
        self.assertTrue(np.any(np.abs(cb0_grid[:, 0]) > 0))
        self.assertTrue(np.any(np.abs(cb0_grid[:, 1]) > 0))
        self.assertTrue(np.count_nonzero(np.abs(cb0_grid) > 0) == 4)


if __name__ == "__main__":
    unittest.main()
