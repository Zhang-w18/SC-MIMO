from __future__ import annotations

from typing import List
from lls_platform.core.data_structures import (
    SymbolMapping,
    SymbolMappingEntry,
    TBInfo,
    TransmissionConfig,
)


class SymbolMapper:
    """符号到 layer/RE 的映射器。

    第一版不真正执行 Tensor scatter/gather，而是生成可验证的映射表。
    这个映射表未来可以直接转换成 TensorFlow gather/scatter 索引。
    """

    def generate(self, tx_cfg: TransmissionConfig, tb_infos: List[TBInfo]) -> SymbolMapping:
        tb_by_cw = {tb.cw_index: tb for tb in tb_infos}
        mapping = SymbolMapping()

        for cw in tx_cfg.cw_configs:
            tb = tb_by_cw[cw.cw_index]
            symbol_idx = 0

            # 每个 CB 的编码 bit 数 E。普通 CW 统一 Qm 时可简单按符号数切分。
            # Scheme 2 per-layer Qm 时，bit 流按 layer 容量顺序切分。
            if cw.same_qm:
                # NR 风格 round-robin：RE 外层、layer 内层。
                qm = cw.representative_qm
                cb_symbol_budget = [cb.E // qm for cb in tb.cb_infos]
                cb_idx = 0
                used_in_cb = 0

                for re in range(tb.n_re_per_layer):
                    for layer in cw.layer_indices:
                        mapping.add(SymbolMappingEntry(
                            cw_index=cw.cw_index,
                            symbol_index=symbol_idx,
                            layer_index=layer,
                            re_index=re,
                            cb_index=cb_idx,
                        ))
                        symbol_idx += 1
                        used_in_cb += 1
                        if cb_idx < len(cb_symbol_budget) - 1 and used_in_cb >= cb_symbol_budget[cb_idx]:
                            cb_idx += 1
                            used_in_cb = 0
            else:
                # Scheme 2：每层 Qm 不同。按 layer 顺序把 bit 分组并调制，
                # 因此符号流顺序为 Layer0 所有 RE、Layer1 所有 RE、...
                # CB 归属按 E 累计近似划分，保持发射和接收反路由一致。
                bit_cursor = 0
                cb_edges = []
                acc = 0
                for cb in tb.cb_infos:
                    acc += cb.E
                    cb_edges.append(acc)

                for layer in cw.layer_indices:
                    qm = cw.layer_modulations[layer]
                    for re in range(tb.n_re_per_layer):
                        # 当前符号对应的 bit 起点
                        current_bit_pos = bit_cursor
                        cb_idx = 0
                        while cb_idx < len(cb_edges) - 1 and current_bit_pos >= cb_edges[cb_idx]:
                            cb_idx += 1
                        mapping.add(SymbolMappingEntry(
                            cw_index=cw.cw_index,
                            symbol_index=symbol_idx,
                            layer_index=layer,
                            re_index=re,
                            cb_index=cb_idx,
                        ))
                        symbol_idx += 1
                        bit_cursor += qm

        return mapping
