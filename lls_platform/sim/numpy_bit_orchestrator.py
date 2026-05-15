from __future__ import annotations

"""纯 NumPy bit-level backend v0。

这个 backend 的目标是先建立“真实 bit 流闭环”：
    payload bits -> 简化编码/速率匹配 -> QAM -> 信道 -> 硬判决 -> 简化解码 -> bit comparison

重要说明：
- 这是 bit-level v0，不是最终 Sionna/NR-LDPC backend。
- 当前不依赖 Sionna/TensorFlow，适合你的 Python3.7 + NumPy1.15 环境。
- 编码器是 repetition/rate-matching toy code，用于验证 bit flow、CB goodput 和统计口径。
- 第一版仅支持 rank=1、单 CW、fixed MCS。完整多层 MIMO/LDPC/CDL 将在后续 backend 中替换。
"""

from pathlib import Path
from typing import Dict, List
import datetime as _dt
import numpy as np

# 注册 flexible_cw
import lls_platform.schemes.flexible_cw  # noqa: F401

from lls_platform.core.config import PlatformConfig, save_resolved_config
from lls_platform.core.registry import create_scheme
from lls_platform.core.data_structures import CWSimulationStats, SNRSummary
from lls_platform.phy.channel import make_rng
from lls_platform.phy.numpy_qam import qam_modulate, qam_demodulate_hard, add_awgn, add_flat_rayleigh
from lls_platform.tx.tb_manager import TBManager
from lls_platform.utils.mcs_tables import get_mcs_table
from lls_platform.sim.stats import save_csv, save_json
from lls_platform.utils.plotting import plot_bler, plot_throughput


class NumpyBitLevelOrchestrator:
    """CPU bit-level v0 编排器。"""

    def __init__(self, config: PlatformConfig):
        self.cfg = config
        self.rng = make_rng(config.simulation.seed)
        self.mcs_table = get_mcs_table(config.simulation.mcs_table)
        self.tb_manager = TBManager(config.resource)

        if config.simulation.fixed_mcs is None:
            raise ValueError(
                "numpy_bit_level v0 只支持 fixed_mcs。请在 YAML 中设置 simulation.fixed_mcs。"
            )
        if config.mapping.rank != 1:
            raise ValueError(
                "numpy_bit_level v0 只支持 rank=1。后续版本再扩展 rank>1/MIMO。"
            )

    def _snr_values(self) -> List[float]:
        start, stop, step = self.cfg.simulation.snr_range_db
        vals = []
        x = start
        while x <= stop + 1e-9:
            vals.append(float(x))
            x += step
        return vals

    def _make_output_dir(self) -> Path:
        root = Path(self.cfg.simulation.output_dir)
        stamp = _dt.datetime.now().strftime("sim_%Y%m%d_%H%M%S")
        out = root / stamp
        out.mkdir(parents=True, exist_ok=True)
        return out

    def _expand_scheme_candidates(self):
        # bit-level v0 建议只跑 scheme1/rank1；如果用户配置多个 scheme，rank=1 时它们会退化为等价方案。
        candidates = []
        if self.cfg.comparison.enabled:
            scheme_specs = self.cfg.comparison.schemes
        else:
            scheme_specs = [{"scheme_id": self.cfg.mapping.scheme_id}]

        for spec in scheme_specs:
            scheme_id = int(spec.get("scheme_id", self.cfg.mapping.scheme_id))
            rank = int(spec.get("rank", self.cfg.mapping.rank))
            scheme = create_scheme("flexible_cw", scheme_id=scheme_id, rank=rank)
            candidates.extend(scheme.expand_candidates(rank=rank))
        return candidates

    @staticmethod
    def _payload_bits_per_cb(tb_info) -> List[int]:
        n = max(int(tb_info.n_cbs), 1)
        base = int(tb_info.tb_size) // n
        rem = int(tb_info.tb_size) % n
        return [base + (1 if i < rem else 0) for i in range(n)]

    @staticmethod
    def _toy_encode(payload_bits: np.ndarray, e_len: int) -> np.ndarray:
        """简化编码/速率匹配：把 payload bit 周期重复到长度 E。

        这不是 NR LDPC，只是为了先验证 bit-level 数据流。
        """
        if len(payload_bits) == 0:
            return np.zeros(e_len, dtype=np.int8)
        idx = np.arange(e_len) % len(payload_bits)
        return payload_bits[idx].astype(np.int8)

    @staticmethod
    def _toy_decode(coded_bits_hat: np.ndarray, payload_len: int) -> np.ndarray:
        """简化解码：对 repetition/rate-matching toy code 做多数表决。"""
        decoded = np.zeros(payload_len, dtype=np.int8)
        if payload_len == 0:
            return decoded
        for j in range(payload_len):
            vals = coded_bits_hat[j::payload_len]
            if len(vals) == 0:
                decoded[j] = 0
            else:
                decoded[j] = 1 if np.sum(vals) >= (len(vals) / 2.0) else 0
        return decoded

    def run(self) -> Path:
        out_dir = self._make_output_dir()
        save_resolved_config(self.cfg, out_dir / "config_resolved.yaml")

        summaries = []
        schemes = self._expand_scheme_candidates()
        snrs = self._snr_values()

        if self.cfg.debug.verbose:
            print("输出目录: {}".format(out_dir))
            print("bit-level v0 候选方案数: {}".format(len(schemes)))
            print("SNR点: {}".format(snrs))

        for scheme in schemes:
            if scheme.rank != 1:
                raise ValueError("numpy_bit_level v0 只支持 rank=1，当前 {} rank={}".format(scheme.label(), scheme.rank))
            for snr_db in snrs:
                summary = self._run_one_scheme_one_snr(scheme, snr_db)
                summaries.append(summary)
                if self.cfg.debug.verbose:
                    print(
                        "{:<24s} SNR={:5.1f} dB slotBLER={:.4g} cbBLER={:.4g} goodput={:.1f} bits/slot".format(
                            summary.scheme_label, snr_db, summary.scheme_bler, summary.cb_bler,
                            summary.goodput_bits_per_slot
                        )
                    )

        save_csv(summaries, out_dir / "results.csv")
        save_json(summaries, out_dir / "results.json")
        plot_bler(summaries, out_dir / "bler_vs_snr.png")
        plot_throughput(summaries, out_dir / "throughput_vs_snr.png")
        return out_dir

    def _run_one_scheme_one_snr(self, scheme, snr_db: float) -> SNRSummary:
        sim = self.cfg.simulation

        # rank=1 fixed MCS 时，layer_sinrs 只用于构造配置；fixed_mcs 会覆盖 MCS 选择。
        tx_cfg = scheme.configure_transmission(
            layer_sinrs_db=np.asarray([snr_db], dtype=float),
            mcs_table=self.mcs_table,
            sim_cfg=sim,
        )
        if len(tx_cfg.cw_configs) != 1:
            raise ValueError("numpy_bit_level v0 只支持单 CW。当前 {} 有 {} 个 CW".format(
                scheme.label(), len(tx_cfg.cw_configs)
            ))

        tb_infos = self.tb_manager.compute_for_transmission(tx_cfg)
        tb = tb_infos[0]
        cw = tx_cfg.cw_configs[0]
        qm = cw.representative_qm
        payload_lens = self._payload_bits_per_cb(tb)

        stat = CWSimulationStats(cw_index=0, tb_size=tb.tb_size, n_cbs=tb.n_cbs)
        scheme_errors = 0

        model = str(self.cfg.channel.model).lower()

        for _trial in range(sim.n_trials_per_snr):
            coded_blocks = []
            original_payloads = []

            for cb_idx, cb in enumerate(tb.cb_infos):
                payload_len = payload_lens[cb_idx]
                payload = self.rng.randint(0, 2, size=payload_len).astype(np.int8)
                coded = self._toy_encode(payload, cb.E)
                original_payloads.append(payload)
                coded_blocks.append(coded)

            coded_all = np.concatenate(coded_blocks) if coded_blocks else np.zeros(0, dtype=np.int8)
            if len(coded_all) % qm != 0:
                raise RuntimeError(
                    "coded bits 总长度 {} 不能被 Qm={} 整除；请检查 TBManager 的 E 分配".format(
                        len(coded_all), qm
                    )
                )

            symbols = qam_modulate(coded_all, qm)
            if model in ("awgn", "numpy_awgn"):
                rx_symbols = add_awgn(symbols, snr_db, self.rng)
            elif model in ("rayleigh", "flat_rayleigh", "numpy_rayleigh"):
                rx_symbols = add_flat_rayleigh(symbols, snr_db, self.rng)
            else:
                raise ValueError("numpy_bit_level v0 支持 channel.model=AWGN 或 Rayleigh，收到 {}".format(
                    self.cfg.channel.model
                ))

            coded_hat_all = qam_demodulate_hard(rx_symbols, qm)
            if len(coded_hat_all) != len(coded_all):
                raise RuntimeError("解调 bit 长度不一致")

            cursor = 0
            cw_has_error = False
            success_bits_this_trial = 0

            stat.trials += 1
            for cb_idx, cb in enumerate(tb.cb_infos):
                e = cb.E
                coded_hat = coded_hat_all[cursor:cursor + e]
                cursor += e

                decoded = self._toy_decode(coded_hat, len(original_payloads[cb_idx]))
                cb_error = not np.array_equal(decoded, original_payloads[cb_idx])

                stat.cb_trials += 1
                stat.cb_errors += int(cb_error)
                if cb_error:
                    cw_has_error = True
                else:
                    success_bits_this_trial += payload_lens[cb_idx]

            stat.successful_payload_bits += success_bits_this_trial
            stat.errors += int(cw_has_error)
            scheme_errors += int(cw_has_error)

        n_re_per_layer = int(tb.n_re_per_layer)
        metadata: Dict[str, object] = {
            "backend_note": "numpy_bit_level_v0_toy_code_not_ldpc",
            "scheme_id": scheme.scheme_id,
            "partition": "+".join(map(str, tx_cfg.partition)) if tx_cfg.partition else "",
            "rank": scheme.rank,
            "n_re_per_layer": n_re_per_layer,
            "cw0_layers": str(cw.layer_indices),
            "cw0_qms": str(cw.layer_modulations),
            "cw0_rate": float(cw.code_rate),
            "cw0_mcs": -1 if cw.mcs_index is None else int(cw.mcs_index),
            "cw0_n_cbs": int(tb.n_cbs),
            "cw0_n_bits_total": int(tb.n_bits_total),
            "cw0_actual_se": float(tb.actual_se),
            "cw0_cb_payload_bits": str(payload_lens),
            "cw0_cb_E": str([int(cb.E) for cb in tb.cb_infos]),
            "cw0_cb_K": str([int(cb.K) for cb in tb.cb_infos]),
            "cw0_cb_K_ldpc": str([int(cb.K_ldpc) for cb in tb.cb_infos]),
            "cw0_cb_N_null": str([int(cb.N_null) for cb in tb.cb_infos]),
        }

        return SNRSummary(
            scheme_label=tx_cfg.scheme_name,
            snr_db=snr_db,
            trials=sim.n_trials_per_snr,
            scheme_errors=scheme_errors,
            cw_stats=[stat],
            total_throughput_bits_per_slot=stat.throughput_bits_per_slot,
            metadata=metadata,
            n_re_per_layer=n_re_per_layer,
            rank=scheme.rank,
        )
