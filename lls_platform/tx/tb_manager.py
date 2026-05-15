from __future__ import annotations

import math
from typing import List

from lls_platform.core.config import ResourceConfig
from lls_platform.core.data_structures import CBInfo, CWConfig, TBInfo, TransmissionConfig


NR_VALID_ZC = [
    2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,
    18,20,22,24,26,28,30,32,36,40,44,48,52,
    56,60,64,72,80,88,96,104,112,120,128,144,
    160,176,192,208,224,240,256,288,320,352,384
]


def count_dmrs_re_per_prb(resource: ResourceConfig) -> int:
    """估算每 PRB 中 DMRS 占用的 RE 数。

    第一版默认 reserve_dmrs_re=False，因此不会调用扣除。
    如果打开 reserve_dmrs_re:
    - DMRS Type 1: 每个 DMRS OFDM 符号每 PRB 约 6 RE
    - DMRS Type 2: 每个 DMRS OFDM 符号每 PRB 约 4 RE
    """
    if not resource.reserve_dmrs_re or not resource.dmrs_symbol_indices:
        return 0
    per_symbol = 6 if resource.dmrs_type == 1 else 4
    return per_symbol * len(resource.dmrs_symbol_indices)


def compute_n_re_per_layer(resource: ResourceConfig) -> int:
    """计算每层可用数据 RE 数。"""
    n_sc = resource.n_prbs * 12
    total = n_sc * resource.pdsch_n_symbols
    dmrs = resource.n_prbs * count_dmrs_re_per_prb(resource)
    return int(total - dmrs)


def quantize_tbs(n_info: float) -> int:
    """简化版 TBS 量化。

    严格 NR TBS 规则比较复杂。第一版先按补充文档2建议：
    - 小 TB: 至少 24 bit，8bit 对齐
    - 大 TB: max(3840, 8*round(n_info/8))
    后续可以替换为 TS 38.214 5.1.3.2 完整版本。
    """
    if n_info <= 0:
        return 24
    if n_info <= 3824:
        return max(24, int(8 * round(n_info / 8)))
    return int(max(3840, 8 * round(n_info / 8)))


def choose_base_graph(tbs: int, code_rate: float) -> tuple[int, int]:
    """NR-style LDPC base graph selection with Sionna compatibility guard.

    Returns:
        (base_graph, K_cb_max)

    38.212-style base graph selection for DL-SCH/UL-SCH is approximately:
        BG2 if A <= 292, or (A <= 3824 and R <= 0.67), or R <= 0.25;
        otherwise BG1.

    Here `tbs` is used as A, the transport block payload size before TB CRC.

    Sionna compatibility:
        Sionna LDPC5GEncoder currently raises an error for low-rate BG1 cases
        that require repetition rate matching, e.g.

            ValueError: Only coderate>1/3 supported for BG1.

        Therefore, if the NR-style rule would select BG1 but R <= 1/3, this
        backend falls back to BG2. This keeps Tx/Rx parameter generation
        internally consistent and prevents unsupported Sionna encoder configs.
    """
    A = int(tbs)
    R = float(code_rate)

    # 38.212-style BG selection.
    if A <= 292 or (A <= 3824 and R <= 0.67) or R <= 0.25:
        bg = 2
    else:
        bg = 1

    # Sionna-compatible guard for low-rate BG1.
    if bg == 1 and R <= (1.0 / 3.0 + 1e-12):
        bg = 2

    if bg == 1:
        return 1, 8448
    return 2, 3840

def find_ldpc_lifting_size(K_i: int, base_graph: int) -> tuple[int, int, int]:
    """根据 K_i 找到合法 Zc 和 K_ldpc。

    BG1: K_base=22
    BG2: K_base=10
    """
    K_base = 22 if base_graph == 1 else 10
    min_zc = math.ceil(K_i / K_base)
    for zc in NR_VALID_ZC:
        if zc >= min_zc:
            K_ldpc = K_base * zc
            return zc, K_ldpc, K_ldpc - K_i
    raise ValueError(f"K_i={K_i} 太大，无法找到合法 Zc")


class TBManager:
    """TB/TBS/CB 分段管理器。"""

    def __init__(self, resource: ResourceConfig):
        self.resource = resource

    def compute_for_transmission(self, tx_cfg: TransmissionConfig) -> List[TBInfo]:
        return [self.compute_for_cw(cw) for cw in tx_cfg.cw_configs]

    def compute_for_cw(self, cw: CWConfig) -> TBInfo:
        n_re_per_layer = compute_n_re_per_layer(self.resource)

        # 该 CW 的编码 bit 容量。
        # 普通 scheme: n_re * 层数 * 统一 Qm
        # Scheme 2: n_re * sum(每层 Qm)
        n_bits_total = 0
        for layer in cw.layer_indices:
            n_bits_total += n_re_per_layer * cw.layer_modulations[layer]

        n_info = n_bits_total * cw.code_rate
        tbs = quantize_tbs(n_info)

        # TB CRC。这里用于分段尺寸计算，bit-level backend 时可进一步细化。
        L_tbcrc = 24 if tbs > 3824 else 16
        B = tbs + L_tbcrc

        bg, K_cb_max = choose_base_graph(tbs, cw.code_rate)

        if B <= K_cb_max:
            n_cbs = 1
            L_cbcrc = 0
        else:
            L_cbcrc = 24
            n_cbs = math.ceil(B / (K_cb_max - L_cbcrc))

        B_with_cbcrc = B + n_cbs * L_cbcrc
        K_base = B_with_cbcrc // n_cbs
        K_rem = B_with_cbcrc % n_cbs

        K_list = [K_base + (1 if i < K_rem else 0) for i in range(n_cbs)]

        # Rate matching 目标长度 E：让所有 CB 的 E 之和严格等于资源容量。
        E_base = n_bits_total // n_cbs
        E_rem = n_bits_total % n_cbs

        cb_infos: List[CBInfo] = []
        for i, K_i in enumerate(K_list):
            zc, K_ldpc, n_null = find_ldpc_lifting_size(K_i, bg)
            E_i = E_base + (1 if i < E_rem else 0)
            cb_infos.append(CBInfo(
                cb_index=i,
                K=K_i,
                K_ldpc=K_ldpc,
                N_null=n_null,
                E=E_i,
                Zc=zc,
            ))

        assert sum(cb.E for cb in cb_infos) == n_bits_total, \
            "sum(E_cb) 必须等于该 CW 的编码 bit 容量"

        total_re = n_re_per_layer * len(cw.layer_indices)
        actual_se = tbs / total_re if total_re > 0 else 0.0

        return TBInfo(
            cw_index=cw.cw_index,
            tb_size=tbs,
            n_cbs=n_cbs,
            base_graph=bg,
            cb_infos=cb_infos,
            n_re_per_layer=n_re_per_layer,
            n_bits_total=n_bits_total,
            actual_se=actual_se,
        )
