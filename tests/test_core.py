import numpy as np

from lls_platform.utils.integer_partition import integer_partitions_nonincreasing, partition_to_layer_groups
from lls_platform.utils.mcs_tables import get_mcs_table
from lls_platform.utils.mcs_selection import select_per_layer_qm, select_mcs_for_cw
from lls_platform.schemes.flexible_cw import FlexibleCWScheme
from lls_platform.core.config import SimulationConfig, ResourceConfig
from lls_platform.tx.tb_manager import TBManager
from lls_platform.tx.symbol_mapper import SymbolMapper


def test_integer_partitions_rank4():
    assert integer_partitions_nonincreasing(4) == [
        [4], [3, 1], [2, 2], [2, 1, 1], [1, 1, 1, 1]
    ]
    assert partition_to_layer_groups([2, 1, 1]) == [[0, 1], [2], [3]]


def test_scheme_generation_rank4():
    mcs_table = get_mcs_table()
    sim = SimulationConfig()
    layer_sinrs = np.array([20.0, 18.0, 12.0, 8.0])

    s1 = FlexibleCWScheme(scheme_id=1, rank=4)
    tx1 = s1.configure_transmission(layer_sinrs, mcs_table, sim)
    assert [cw.layer_indices for cw in tx1.cw_configs] == [[0, 1, 2, 3]]

    s4 = FlexibleCWScheme(scheme_id=4, rank=4)
    tx4 = s4.configure_transmission(layer_sinrs, mcs_table, sim)
    assert [cw.layer_indices for cw in tx4.cw_configs] == [[0, 1], [2, 3]]

    s6 = FlexibleCWScheme(scheme_id=6, rank=4)
    tx6 = s6.configure_transmission(layer_sinrs, mcs_table, sim)
    assert [cw.layer_indices for cw in tx6.cw_configs] == [[0], [1], [2], [3]]


def test_scheme2_per_layer_qm():
    qms = select_per_layer_qm([20, 18, 12, 8])
    assert qms == {0: 8, 1: 8, 2: 6, 3: 4} or qms == {0: 8, 1: 8, 2: 6, 3: 4}

    mcs_table = get_mcs_table()
    sim = SimulationConfig()
    s2 = FlexibleCWScheme(scheme_id=2, rank=4)
    tx2 = s2.configure_transmission(np.array([20.0, 18.0, 12.0, 8.0]), mcs_table, sim)
    cw = tx2.cw_configs[0]
    assert len(set(cw.layer_modulations.values())) > 1
    assert 0.05 <= cw.code_rate <= 0.95


def test_mcs_selection_monotonic():
    table = get_mcs_table()
    low = select_mcs_for_cw([0.0], table, gap_db=2.5)
    high = select_mcs_for_cw([25.0], table, gap_db=2.5)
    assert high.index >= low.index
    assert high.spectral_efficiency >= low.spectral_efficiency


def test_tb_manager_closure():
    table = get_mcs_table()
    sim = SimulationConfig()
    tx = FlexibleCWScheme(scheme_id=4, rank=4).configure_transmission(
        np.array([20.0, 18.0, 12.0, 8.0]), table, sim
    )
    tb_infos = TBManager(ResourceConfig(n_prbs=10, pdsch_n_symbols=10)).compute_for_transmission(tx)
    for tb in tb_infos:
        assert tb.total_E == tb.n_bits_total
        assert tb.tb_size > 0
        assert tb.n_cbs >= 1


def test_symbol_mapping_count():
    table = get_mcs_table()
    sim = SimulationConfig()
    resource = ResourceConfig(n_prbs=4, pdsch_n_symbols=2)
    tx = FlexibleCWScheme(scheme_id=4, rank=4).configure_transmission(
        np.array([20.0, 18.0, 12.0, 8.0]), table, sim
    )
    tb_infos = TBManager(resource).compute_for_transmission(tx)
    mapping = SymbolMapper().generate(tx, tb_infos)

    expected_symbols = 0
    for cw, tb in zip(tx.cw_configs, tb_infos):
        if cw.same_qm:
            expected_symbols += tb.n_bits_total // cw.representative_qm
        else:
            expected_symbols += tb.n_re_per_layer * len(cw.layer_indices)
    assert len(mapping.entries) == expected_symbols
