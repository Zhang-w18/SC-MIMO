from __future__ import annotations

from pathlib import Path
from typing import Dict, List
import datetime as _dt
import numpy as np

# 注册 flexible_cw
import lls_platform.schemes.flexible_cw  # noqa: F401

from lls_platform.core.config import PlatformConfig, save_resolved_config
from lls_platform.core.registry import create_scheme
from lls_platform.core.data_structures import CWSimulationStats, SNRSummary
from lls_platform.phy.channel import RayleighSVDChannel, make_rng, rng_uniform01
from lls_platform.tx.tb_manager import TBManager
from lls_platform.utils.mcs_tables import get_mcs_table
from lls_platform.utils.mcs_selection import (
    estimate_post_sinr_db_from_singular_values,
    harmonic_mean_sinr_db,
    db_to_linear,
    linear_to_db,
)
from lls_platform.sim.stats import save_csv, save_json
from lls_platform.utils.plotting import plot_bler, plot_throughput


class LinkAbstractionOrchestrator:
    """第一版仿真编排器。

    这是一个抽象链路 backend，不直接做 LDPC bit-level 仿真。
    它用于快速验证：
    - 7 种 scheme 的 CW/layer 分配；
    - per-CW adaptive MCS；
    - Scheme 2 per-layer Qm；
    - TBS/CB 闭环；
    - CB-BLER / CW-BLER / scheme-BLER / goodput 统计。

    2026-04 更新：错误统计从原来的“每 CW 抽一次错误”下沉到
    “每 CB 抽一次错误”。具体做法：
    1. 先用 logistic 模型得到 TB/CW 级错误概率 p_tb；
    2. 根据该 CW 的 CB 数 N_CB 换算 p_cb = 1 - (1-p_tb)^(1/N_CB)；
    3. 对每个 CB 独立抽 Bernoulli(p_cb)；
    4. CW 错误 = 任意 CB 错误；scheme 错误 = 任意 CW 错误；
    5. goodput = 成功 CB 携带的 payload bits 之和的 trial 平均。

    后续可在保持 MappingScheme/TBManager/Stats 接口不变的情况下，
    替换为 Sionna CDL + LDPC + MMSE detector 的 bit-level backend。
    """

    def __init__(self, config: PlatformConfig):
        self.cfg = config
        # 兼容旧 NumPy：NumPy 1.15.1 没有 np.random.default_rng。
        self.rng = make_rng(config.simulation.seed)
        self.mcs_table = get_mcs_table(config.simulation.mcs_table)
        self.tb_manager = TBManager(config.resource)

        self.channel = RayleighSVDChannel(
            n_rx=config.antenna.ue_n_antennas,
            n_tx=config.antenna.bs_n_antennas,
            n_re_samples=config.channel.n_re_samples,
            normalize=config.channel.normalize,
            rng=self.rng,
        )

    def _snr_values(self) -> List[float]:
        start, stop, step = self.cfg.simulation.snr_range_db
        vals = []
        x = start
        # 包含 stop
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
        """根据 comparison 配置展开所有候选方案。"""
        candidates = []
        if self.cfg.comparison.enabled:
            scheme_specs = self.cfg.comparison.schemes
        else:
            scheme_specs = [{"scheme_id": self.cfg.mapping.scheme_id, **self.cfg.mapping.params}]

        for spec in scheme_specs:
            scheme_id = int(spec.get("scheme_id", self.cfg.mapping.scheme_id))
            rank = int(spec.get("rank", self.cfg.mapping.rank))
            partition = spec.get("partition", None)
            scheme = create_scheme(
                "flexible_cw",
                scheme_id=scheme_id,
                rank=rank,
                partition=partition,
            )
            candidates.extend(scheme.expand_candidates(rank=rank))
        return candidates

    def _bler_probability(self, sinr_eff_db: float, required_sinr_db: float, slope_db: float) -> float:
        """抽象 TB/CW-BLER 模型。

        required_sinr_db 是 MCS 表中的约 10% BLER 门限。
        这里使用一个平滑 logistic 函数，让
            sinr_eff_db = required_sinr_db
        时 TB/CW 级错误概率 p_tb 约为 0.1。

        注意：这个 p 是 TB/CW 级错误概率，不是 CB 级错误概率。
        CB 级错误概率在 _tb_error_to_cb_error_probability() 中换算。
        """
        # p = 1/(1 + exp((sinr - center)/slope))
        # 要使 p(req)=0.1，则 center = req - slope*log(9)
        center = required_sinr_db - slope_db * np.log(9.0)
        p = 1.0 / (1.0 + np.exp((sinr_eff_db - center) / max(slope_db, 1e-6)))
        return float(np.clip(p, 1e-5, 1.0))

    @staticmethod
    def _tb_error_to_cb_error_probability(p_tb: float, n_cbs: int) -> float:
        """把 TB/CW 错误概率换算成 CB 错误概率。

        假设同一个 CW 内的 CB 错误近似独立且同分布：
            p_tb = 1 - (1 - p_cb)^N_CB
        因此：
            p_cb = 1 - (1 - p_tb)^(1/N_CB)

        这样如果 MCS 表的门限代表“10% TB-BLER”，则一个 CW 即使分成多个 CB，
        合成后的 CW/TB-BLER 仍然约等于该 logistic 模型给出的 p_tb。
        """
        n = max(int(n_cbs), 1)
        p_tb = float(np.clip(p_tb, 0.0, 1.0))
        p_cb = 1.0 - (1.0 - p_tb) ** (1.0 / n)
        return float(np.clip(p_cb, 0.0, 1.0))

    @staticmethod
    def _payload_bits_per_cb(tb_info) -> List[int]:
        """把一个 CW 的 TBS payload bits 分摊到各个 CB。

        这里统计的是 useful payload bits，不包括 TB CRC、CB CRC、NULL/filler bits。
        第一版抽象模型中按近似均分处理；bit-level backend 后可以与真实分段保持一致。
        """
        n = max(int(tb_info.n_cbs), 1)
        base = int(tb_info.tb_size) // n
        rem = int(tb_info.tb_size) % n
        return [base + (1 if i < rem else 0) for i in range(n)]

    def _effective_sinr_for_cw(self, cw, trial_layer_sinrs_db: np.ndarray) -> float:
        """计算某个 CW 的等效 SINR。

        普通 scheme：同 CW 内所有层 Qm 相同，使用线性域调和平均。
        Scheme 2：同 CW 内可能每层 Qm 不同。为了反映高 Qm 层承载更多 coded bits，
        使用 Qm 加权的线性域调和平均：
            SINR_eff = sum(w_l) / sum(w_l / SINR_l),  w_l = Qm_l
        由于每层 RE 数相同，权重用 Qm_l 即可；等价于用 N_RE*Qm_l。
        """
        sub_sinrs_db = np.asarray(trial_layer_sinrs_db[cw.layer_indices], dtype=float)
        qms = np.asarray([cw.layer_modulations[l] for l in cw.layer_indices], dtype=float)
        if len(set(qms.tolist())) <= 1:
            return harmonic_mean_sinr_db(sub_sinrs_db)

        sinr_linear = db_to_linear(sub_sinrs_db)
        sinr_linear = np.maximum(sinr_linear, 1e-12)
        weights = np.maximum(qms, 1e-12)
        eff_linear = np.sum(weights) / np.sum(weights / sinr_linear)
        return float(linear_to_db(eff_linear))

    def _required_sinr_for_cw(self, cw) -> float:
        """得到 CW 的近似 required SINR。

        普通 scheme 直接查 mcs_index。
        Scheme 2 没有唯一 mcs_index，因此用 Qm 和 code_rate 对应的 SE，
        找 MCS 表中最接近的 SE 的 required_sinr_db。
        """
        if cw.mcs_index is not None:
            for m in self.mcs_table:
                if m.index == cw.mcs_index:
                    return m.required_sinr_db

        # Scheme 2: 每层 Qm 可能不同，使用平均 Qm × code_rate 近似有效谱效。
        se = cw.code_rate * np.mean([cw.layer_modulations[l] for l in cw.layer_indices])
        best = min(self.mcs_table, key=lambda m: abs(m.spectral_efficiency - se))
        return best.required_sinr_db

    def run(self) -> Path:
        out_dir = self._make_output_dir()
        save_resolved_config(self.cfg, out_dir / "config_resolved.yaml")

        summaries: List[SNRSummary] = []
        schemes = self._expand_scheme_candidates()
        snrs = self._snr_values()

        if self.cfg.debug.verbose:
            print(f"输出目录: {out_dir}")
            print(f"候选方案数: {len(schemes)}")
            print(f"SNR点: {snrs}")

        for scheme in schemes:
            for snr_db in snrs:
                summary = self._run_one_scheme_one_snr(scheme, snr_db)
                summaries.append(summary)
                if self.cfg.debug.verbose:
                    print(
                        f"{summary.scheme_label:24s} SNR={snr_db:5.1f} dB "
                        f"slotBLER={summary.scheme_bler:.4g} "
                        f"cbBLER={summary.cb_bler:.4g} "
                        f"goodput={summary.goodput_bits_per_slot:.1f} bits/slot"
                    )

        save_csv(summaries, out_dir / "results.csv")
        save_json(summaries, out_dir / "results.json")
        plot_bler(summaries, out_dir / "bler_vs_snr.png")
        plot_throughput(summaries, out_dir / "throughput_vs_snr.png")
        return out_dir

    def _run_one_scheme_one_snr(self, scheme, snr_db: float) -> SNRSummary:
        sim = self.cfg.simulation
        total_trials = 0
        scheme_errors = 0
        cw_stats_by_idx: Dict[int, CWSimulationStats] = {}

        last_tx_cfg = None
        last_tb_infos = None
        last_mean_layer_sinrs_db = None

        while total_trials < sim.n_trials_per_snr:
            batch = min(sim.batch_size, sim.n_trials_per_snr - total_trials)

            # 1. 生成 batch 信道奇异值。
            s = self.channel.sample_singular_values(batch, rank=scheme.rank)
            # shape: [batch, n_re_samples, rank]
            layer_sinrs_db_samples = estimate_post_sinr_db_from_singular_values(
                s, snr_db=snr_db, rank=scheme.rank
            )

            # 2. 同一 batch 共用一个 TransmissionConfig。
            #    用 batch+RE 平均的 layer SINR 做 MCS/Qm 选择。
            mean_layer_sinrs_db = np.mean(layer_sinrs_db_samples, axis=(0, 1))
            tx_cfg = scheme.configure_transmission(
                layer_sinrs_db=mean_layer_sinrs_db,
                mcs_table=self.mcs_table,
                sim_cfg=sim,
            )
            tb_infos = self.tb_manager.compute_for_transmission(tx_cfg)
            tb_by_cw = {tb.cw_index: tb for tb in tb_infos}
            payload_by_cw = {tb.cw_index: self._payload_bits_per_cb(tb) for tb in tb_infos}

            # 初始化 CW stats。注意 tb_size/n_cbs 在 adaptive MCS 下可能随 batch 变化；
            # 这里用于输出展示，采用最近一次 batch 的配置。
            for cw in tx_cfg.cw_configs:
                tb = tb_by_cw[cw.cw_index]
                if cw.cw_index not in cw_stats_by_idx:
                    cw_stats_by_idx[cw.cw_index] = CWSimulationStats(
                        cw_index=cw.cw_index,
                        tb_size=tb.tb_size,
                        n_cbs=tb.n_cbs,
                    )
                else:
                    cw_stats_by_idx[cw.cw_index].tb_size = tb.tb_size
                    cw_stats_by_idx[cw.cw_index].n_cbs = tb.n_cbs

            # 3. 对 batch 内每个 trial 做 CB 级抽象判错。
            for b in range(batch):
                trial_has_scheme_error = False
                # 对每个 trial，先对多个 RE 样本平均；更真实的 backend 可做 EESM/MIESM。
                trial_layer_sinrs_db = np.mean(layer_sinrs_db_samples[b], axis=0)

                for cw in tx_cfg.cw_configs:
                    tb = tb_by_cw[cw.cw_index]
                    payload_bits = payload_by_cw[cw.cw_index]

                    sinr_eff_db = self._effective_sinr_for_cw(cw, trial_layer_sinrs_db)
                    req = self._required_sinr_for_cw(cw)

                    # p_tb 是 TB/CW 级错误概率。
                    p_tb = self._bler_probability(
                        sinr_eff_db=sinr_eff_db,
                        required_sinr_db=req,
                        slope_db=sim.bler_slope_db,
                    )
                    # 根据该 CW 的 CB 数换算为 CB 级错误概率。
                    p_cb = self._tb_error_to_cb_error_probability(p_tb, tb.n_cbs)

                    stat = cw_stats_by_idx[cw.cw_index]
                    stat.trials += 1

                    cw_has_error = False
                    for cb_idx, _cb in enumerate(tb.cb_infos):
                        cb_error = rng_uniform01(self.rng) < p_cb
                        stat.cb_trials += 1
                        stat.cb_errors += int(cb_error)
                        if cb_error:
                            cw_has_error = True
                        else:
                            # 只统计成功 CB 的 payload bits。
                            stat.successful_payload_bits += payload_bits[cb_idx]

                    stat.errors += int(cw_has_error)
                    if cw_has_error:
                        trial_has_scheme_error = True

                scheme_errors += int(trial_has_scheme_error)

            total_trials += batch
            last_tx_cfg = tx_cfg
            last_tb_infos = tb_infos
            last_mean_layer_sinrs_db = mean_layer_sinrs_db

        cw_stats = [cw_stats_by_idx[i] for i in sorted(cw_stats_by_idx)]
        total_goodput = sum(cw.throughput_bits_per_slot for cw in cw_stats)

        n_re_per_layer = 0
        if last_tb_infos:
            n_re_per_layer = int(last_tb_infos[0].n_re_per_layer)

        metadata = {
            "scheme_id": scheme.scheme_id,
            "partition": "+".join(map(str, last_tx_cfg.partition)) if last_tx_cfg and last_tx_cfg.partition else "",
            "rank": scheme.rank,
            "n_re_per_layer": n_re_per_layer,
        }
        if last_mean_layer_sinrs_db is not None:
            for i, x in enumerate(last_mean_layer_sinrs_db):
                metadata[f"mean_layer{i}_sinr_db"] = float(x)
        if last_tx_cfg is not None and last_tb_infos is not None:
            tb_by_cw_last = {tb.cw_index: tb for tb in last_tb_infos}
            for cw in last_tx_cfg.cw_configs:
                tb = tb_by_cw_last[cw.cw_index]
                payload_bits = self._payload_bits_per_cb(tb)
                metadata[f"cw{cw.cw_index}_layers"] = str(cw.layer_indices)
                metadata[f"cw{cw.cw_index}_qms"] = str(cw.layer_modulations)
                metadata[f"cw{cw.cw_index}_rate"] = float(cw.code_rate)
                metadata[f"cw{cw.cw_index}_mcs"] = -1 if cw.mcs_index is None else int(cw.mcs_index)
                metadata[f"cw{cw.cw_index}_n_cbs"] = int(tb.n_cbs)
                metadata[f"cw{cw.cw_index}_n_bits_total"] = int(tb.n_bits_total)
                metadata[f"cw{cw.cw_index}_actual_se"] = float(tb.actual_se)
                metadata[f"cw{cw.cw_index}_cb_payload_bits"] = str(payload_bits)
                metadata[f"cw{cw.cw_index}_cb_E"] = str([int(cb.E) for cb in tb.cb_infos])
                metadata[f"cw{cw.cw_index}_cb_K"] = str([int(cb.K) for cb in tb.cb_infos])
                metadata[f"cw{cw.cw_index}_cb_K_ldpc"] = str([int(cb.K_ldpc) for cb in tb.cb_infos])
                metadata[f"cw{cw.cw_index}_cb_N_null"] = str([int(cb.N_null) for cb in tb.cb_infos])

        return SNRSummary(
            scheme_label=last_tx_cfg.scheme_name if last_tx_cfg else scheme.label(),
            snr_db=snr_db,
            trials=total_trials,
            scheme_errors=scheme_errors,
            cw_stats=cw_stats,
            total_throughput_bits_per_slot=total_goodput,
            metadata=metadata,
            n_re_per_layer=n_re_per_layer,
            rank=scheme.rank,
        )
