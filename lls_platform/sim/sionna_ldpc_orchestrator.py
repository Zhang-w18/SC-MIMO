from __future__ import annotations

"""Sionna LDPC bit-level backend v2.6。

相对 v2.4 的主要变化：
- 保留多 CW / 多 Scheme / rank=1..8；
- 新增 multi-CB per CW 执行链路：每个 CB 独立 LDPC 编码/解码，CW coded bits 由多个 CB 的 coded bits 串接而成；
- goodput 下沉到 CB 级：只要某个 CB 解码成功，就把该 CB 的 payload bits 计入 goodput；
- CW-BLER 仍定义为该 CW 内任意 CB 失败，scheme-BLER 仍定义为任意 CW/CB 失败；
- 继续使用 GridMappingPlan 将 CW coded-bit stream 映射到 [RE, layer] grid；
- 支持 AWGN、flat Rayleigh + ideal SVD-equivalent layers；
- v2.6 新增 CDL-derived per-RE SVD-equivalent layers 和 per-trial adaptive MCS；
- 保留 batch-first GPU 并行；per-trial adaptive 时按相同 TB/CB/Grid shape 分组 batch。

当前仍故意限制：
- CDL 第一版使用 ideal per-RE SVD 等效层，不做真实 MMSE detector；
- 不做每 PRB MCS，MCS 粒度为每 trial、每 CW。
"""

from pathlib import Path
from typing import Dict, List, Tuple, Mapping
import datetime as _dt
import csv
import hashlib
import gc
import numpy as np

# 注册 flexible_cw
import lls_platform.schemes.flexible_cw  # noqa: F401

from lls_platform.core.config import PlatformConfig, save_resolved_config
from lls_platform.core.registry import create_scheme
from lls_platform.core.data_structures import CWSimulationStats, SNRSummary
from lls_platform.phy.channel import make_rng
from lls_platform.phy.cdl_channel import CDLChannelBuilder
from lls_platform.phy.link_quality import compute_svd_capacity_quality
from lls_platform.phy.grid_mapping import (
    GridMappingPlan,
    build_grid_mapping_plan,
    collect_cw_llrs_from_grid_tf,
    map_cw_bits_to_grid_tf,
    plan_debug_dict,
)
from lls_platform.tx.tb_manager import TBManager
from lls_platform.utils.mcs_tables import get_mcs_table
from lls_platform.sim.stats import save_csv, save_json
from lls_platform.utils.plotting import plot_bler, plot_throughput


def _import_sionna_components():
    """兼容不同 Sionna 版本的导入路径。"""
    try:
        import tensorflow as tf  # noqa: F401
    except Exception as e:  # pragma: no cover
        raise ImportError(
            "sionna_ldpc_bit_level 后端需要 TensorFlow。请先安装 TensorFlow/Sionna。\n"
            "原始错误: {}".format(repr(e))
        )

    try:
        from sionna.phy.fec.ldpc import LDPC5GEncoder, LDPC5GDecoder
        from sionna.phy.mapping import Mapper, Demapper
        return tf, LDPC5GEncoder, LDPC5GDecoder, Mapper, Demapper
    except Exception:
        pass

    try:
        from sionna.fec.ldpc.encoding import LDPC5GEncoder
        from sionna.fec.ldpc.decoding import LDPC5GDecoder
        from sionna.mapping import Mapper, Demapper
        return tf, LDPC5GEncoder, LDPC5GDecoder, Mapper, Demapper
    except Exception as e:  # pragma: no cover
        raise ImportError(
            "未能导入 Sionna LDPC/Mapper/Demapper。请确认已安装 Sionna，"
            "并且版本包含 LDPC5GEncoder/LDPC5GDecoder。\n原始错误: {}".format(repr(e))
        )


def _make_mapper(Mapper, qm: int):
    """兼容不同版本 Mapper 构造函数。"""
    try:
        return Mapper("qam", num_bits_per_symbol=qm)
    except TypeError:
        try:
            return Mapper(constellation_type="qam", num_bits_per_symbol=qm)
        except TypeError:
            return Mapper("qam", qm)


def _make_demapper(Demapper, qm: int):
    """兼容不同版本 Demapper 构造函数。"""
    try:
        return Demapper("app", "qam", num_bits_per_symbol=qm)
    except TypeError:
        try:
            return Demapper("app", constellation_type="qam", num_bits_per_symbol=qm)
        except TypeError:
            return Demapper("app", "qam", qm)


def _make_decoder(LDPC5GDecoder, encoder, num_iter: int):
    """兼容不同版本 LDPC5GDecoder 构造函数。"""
    try:
        return LDPC5GDecoder(encoder, hard_out=True, num_iter=num_iter)
    except TypeError:
        try:
            return LDPC5GDecoder(encoder, hard_out=True, num_iter=num_iter, return_infobits=True)
        except TypeError:
            return LDPC5GDecoder(encoder, hard_out=True)


def _rng_bits(rng, shape) -> np.ndarray:
    """生成 0/1 payload bits，兼容 numpy.random.Generator 和 RandomState。"""
    if hasattr(rng, "integers"):
        return rng.integers(0, 2, size=shape, dtype=np.int8)
    return rng.randint(0, 2, size=shape).astype(np.int8)


def _plan_signature(plan: GridMappingPlan) -> Tuple:
    """把 mapping plan 压成可哈希 signature，便于缓存链路对象。"""
    parts = [int(plan.rank), int(plan.n_re_per_layer)]
    for cw_idx in sorted(plan.cw_routes.keys()):
        route = plan.cw_routes[cw_idx]
        for seg in route.segments:
            parts.extend([
                int(cw_idx),
                int(seg.layer_index),
                int(seg.qm),
                int(seg.bit_start),
                int(seg.bit_end),
                int(seg.n_re),
                int(np.sum(seg.bit_indices) % 1000000007),
                int(np.sum(seg.bit_indices.astype(np.int64) * seg.bit_indices.astype(np.int64)) % 1000000007),
            ])
            if seg.n_re:
                parts.extend([int(seg.re_indices[0]), int(seg.re_indices[-1])])
    return tuple(parts)


def _cb_params_signature(cb_params: Mapping[int, List[Tuple[int, int, int]]]) -> Tuple:
    """把每个 CW 的 CB 参数压成可哈希 signature。

    cb_params[cw_idx] = [(cb_idx, payload_len, E), ...]
    """
    parts = []
    for cw_idx in sorted(cb_params.keys()):
        for cb_idx, k, n in cb_params[cw_idx]:
            parts.extend([int(cw_idx), int(cb_idx), int(k), int(n)])
    return tuple(parts)


class _SionnaMultiCWGridChain:
    """多 CW / 多 CB 的 Sionna LDPC + grid mapping + channel + demapping 链路。

    输入：payload_bits_by_cw[cw] = [CB0_bits, CB1_bits, ...]
          每个 CB_bits shape=[B,k_cb]
    输出：decoded_bits_by_cw[cw] = [CB0_hat, CB1_hat, ...]
          每个 CB_hat shape=[B,k_cb]

    v2.5 关键点：
    - 每个 CB 独立 LDPC encoder/decoder；
    - 同一个 CW 的多个 CB encoded bits 沿 axis=-1 串接，形成 CW coded-bit stream；
    - GridMappingPlan 仍然只看到 CW 级 coded-bit stream；
    - 接收端 collect 回 CW LLR 后，再按每个 CB 的 E 切片，分别 LDPC decode。
    """

    def __init__(self, tf, LDPC5GEncoder, LDPC5GDecoder, Mapper, Demapper,
                 cb_params: Mapping[int, List[Tuple[int, int, int]]], plan: GridMappingPlan, channel_model: str,
                 n_tx: int, n_rx: int, num_iter: int):
        self.tf = tf
        self.plan = plan
        self.channel_model = str(channel_model).lower()
        self.n_tx = int(n_tx)
        self.n_rx = int(n_rx)
        self.rank = int(plan.rank)
        self.cw_indices = sorted(int(k) for k in cb_params.keys())
        self.cb_params = {
            int(cw): [(int(cb_idx), int(k), int(n)) for cb_idx, k, n in params]
            for cw, params in cb_params.items()
        }

        if self.rank > min(self.n_tx, self.n_rx) and self.channel_model in ("rayleigh", "flat_rayleigh"):
            raise ValueError(
                f"Rayleigh SVD 等效层信道要求 rank <= min(n_tx,n_rx)，当前 rank={self.rank}, "
                f"n_tx={self.n_tx}, n_rx={self.n_rx}"
            )

        # 每个 CB 独立 encoder/decoder。
        self.encoder_by_cb = {}
        self.decoder_by_cb = {}
        self.cb_order: List[Tuple[int, int]] = []
        self.cb_E_by_key: Dict[Tuple[int, int], int] = {}
        self.cb_k_by_key: Dict[Tuple[int, int], int] = {}

        for cw_idx in self.cw_indices:
            for cb_idx, k, n in self.cb_params[cw_idx]:
                key = (int(cw_idx), int(cb_idx))
                enc = LDPC5GEncoder(k=int(k), n=int(n))
                dec = _make_decoder(LDPC5GDecoder, enc, num_iter=num_iter)
                self.encoder_by_cb[key] = enc
                self.decoder_by_cb[key] = dec
                self.cb_order.append(key)
                self.cb_E_by_key[key] = int(n)
                self.cb_k_by_key[key] = int(k)

        # 校验每个 CW 的 sum(E_cb) 是否等于 plan 中 CW total_bits。
        for cw_idx in self.cw_indices:
            total_E = sum(int(n) for _, _, n in self.cb_params[cw_idx])
            route_total = int(self.plan.route_for_cw(cw_idx).total_bits)
            if total_E != route_total:
                raise ValueError(
                    f"CW{cw_idx} sum(E_cb)={total_E} 与 GridMappingPlan total_bits={route_total} 不一致"
                )

        qms = sorted({int(seg.qm) for route in plan.cw_routes.values() for seg in route.segments})
        self.mapper_by_qm = {qm: _make_mapper(Mapper, qm) for qm in qms}
        self.demapper_by_qm = {qm: _make_demapper(Demapper, qm) for qm in qms}

        try:
            self._run_tf_graph = tf.function(self._run_tf, reduce_retracing=True)
        except TypeError:  # 兼容旧 TF
            self._run_tf_graph = tf.function(self._run_tf)

    def _run_tf(self, b_tuple, snr_db_tf, layer_gains_tf, noise_tf):
        tf = self.tf

        # 1) 每个 CB 独立 LDPC 编码；同一 CW 内多个 CB 的 coded bits 串接。
        encoded_by_cw_parts: Dict[int, List[object]] = {cw_idx: [] for cw_idx in self.cw_indices}
        pos = 0
        for cw_idx, cb_idx in self.cb_order:
            b_cb = b_tuple[pos]
            pos += 1
            c_cb = self.encoder_by_cb[(cw_idx, cb_idx)](b_cb)
            encoded_by_cw_parts[cw_idx].append(c_cb)

        cw_bits_by_cw = {}
        for cw_idx in self.cw_indices:
            if len(encoded_by_cw_parts[cw_idx]) == 1:
                cw_bits_by_cw[cw_idx] = encoded_by_cw_parts[cw_idx][0]
            else:
                cw_bits_by_cw[cw_idx] = tf.concat(encoded_by_cw_parts[cw_idx], axis=-1)

        # 2) CW coded-bit stream -> grid -> channel/equalization -> CW LLR stream。
        x_grid = map_cw_bits_to_grid_tf(tf, cw_bits_by_cw, self.plan, self.mapper_by_qm)
        y_eq_grid, no_grid = self._apply_channel_and_equalize(x_grid, snr_db_tf, layer_gains_tf, noise_tf)
        cw_llrs = collect_cw_llrs_from_grid_tf(
            tf, y_eq_grid, no_grid, self.plan, self.demapper_by_qm
        )

        # 3) 按每个 CB 的 E 从 CW LLR 中切片，分别 LDPC 解码。
        outs = []
        for cw_idx in self.cw_indices:
            offset = 0
            llr_cw = cw_llrs[cw_idx]
            for cb_idx, _k, n in self.cb_params[cw_idx]:
                n = int(n)
                llr_cb = llr_cw[:, offset:offset + n]
                offset += n
                b_hat = self.decoder_by_cb[(cw_idx, cb_idx)](llr_cb)
                outs.append(b_hat)
        return tuple(outs)

    def _apply_channel_and_equalize(self, x_grid, snr_db_tf, layer_gains_tf, noise_tf=None):
        tf = self.tf
        snr_linear = tf.pow(tf.constant(10.0, dtype=tf.float32), snr_db_tf / 10.0)
        n0 = tf.math.reciprocal(snr_linear)
        b = tf.shape(x_grid)[0]
        n_re = tf.shape(x_grid)[1]
        rank = int(self.rank)

        if noise_tf is None:
            noise_r = tf.random.normal(tf.shape(tf.math.real(x_grid)), dtype=tf.float32)
            noise_i = tf.random.normal(tf.shape(tf.math.real(x_grid)), dtype=tf.float32)
            sigma = tf.sqrt(n0 / 2.0)
            sigma_f = tf.cast(sigma, tf.float32)
            noise = tf.complex(sigma_f * noise_r, sigma_f * noise_i)
            noise = tf.cast(noise, x_grid.dtype)
        else:
            noise = tf.cast(noise_tf, x_grid.dtype)

        if self.channel_model in ("awgn", "numpy_awgn"):
            y_eq = x_grid + noise
            no = tf.cast(n0, tf.float32)
            return y_eq, no

        if self.channel_model in ("cdl", "cdl_svd", "sionna_cdl"):
            gains = tf.cast(layer_gains_tf, tf.float32)
            gains = tf.maximum(gains, tf.constant(1e-6, dtype=tf.float32))
            if gains.shape.rank == 2:
                gains = gains[:, tf.newaxis, :]
            g = tf.cast(gains, x_grid.dtype)
            y = g * x_grid + noise
            y_eq = y / g
            no_grid = n0 / tf.square(gains)
            return y_eq, no_grid

        if self.channel_model not in ("rayleigh", "flat_rayleigh"):
            raise ValueError(f"Sionna LDPC v2.6 不支持 channel.model={self.channel_model}")

        # flat Rayleigh MIMO + ideal SVD equivalent layers。
        # H entries CN(0,1/n_tx)，使接收功率量级随 n_tx 基本归一。
        h_r = tf.random.normal([b, self.n_rx, self.n_tx], dtype=tf.float32)
        h_i = tf.random.normal([b, self.n_rx, self.n_tx], dtype=tf.float32)
        scale = tf.sqrt(tf.constant(2.0 * max(self.n_tx, 1), dtype=tf.float32))
        h = tf.complex(h_r / scale, h_i / scale)
        s = tf.linalg.svd(h, compute_uv=False)  # [B, min(n_rx,n_tx)]
        gains = tf.cast(s[:, :rank], tf.float32)
        gains = tf.maximum(gains, tf.constant(1e-6, dtype=tf.float32))

        g = tf.cast(gains[:, tf.newaxis, :], x_grid.dtype)  # [B,1,rank]
        y = g * x_grid + noise
        y_eq = y / g

        no_eq = n0 / tf.square(gains)  # [B,rank]
        no_grid = tf.ones([b, n_re, rank], dtype=tf.float32) * no_eq[:, tf.newaxis, :]
        return y_eq, no_grid

    def run_batch(self, payload_bits_by_cw: Mapping[int, List[np.ndarray]], snr_db: float, layer_gains: np.ndarray = None, noise: np.ndarray = None) -> Dict[int, List[np.ndarray]]:
        tf = self.tf
        b_tensors = []
        batch_size = None

        for cw_idx, cb_idx in self.cb_order:
            if cw_idx not in payload_bits_by_cw:
                raise KeyError(f"payload_bits_by_cw 缺少 CW{cw_idx}")
            cb_list = payload_bits_by_cw[cw_idx]
            if cb_idx >= len(cb_list):
                raise KeyError(f"payload_bits_by_cw[CW{cw_idx}] 缺少 CB{cb_idx}")
            bits = np.asarray(cb_list[cb_idx], dtype=np.float32)
            k = self.cb_k_by_key[(cw_idx, cb_idx)]
            if bits.ndim != 2 or bits.shape[1] != k:
                raise ValueError(f"CW{cw_idx} CB{cb_idx} payload shape 应为 [B,{k}]，当前为 {bits.shape}")
            if batch_size is None:
                batch_size = bits.shape[0]
            elif batch_size != bits.shape[0]:
                raise ValueError("所有 CW/CB 的 batch size 必须一致")
            b_tensors.append(tf.constant(bits, dtype=tf.float32))

        snr_db_tf = tf.constant(float(snr_db), dtype=tf.float32)
        if layer_gains is None:
            layer_gains = np.ones((batch_size, self.plan.n_re_per_layer, self.rank), dtype=np.float32)
        else:
            layer_gains = np.asarray(layer_gains, dtype=np.float32)
            if layer_gains.ndim == 2:
                layer_gains = layer_gains[:, None, :] * np.ones((1, self.plan.n_re_per_layer, 1), dtype=np.float32)
            if layer_gains.shape[0] != batch_size or layer_gains.shape[-1] != self.rank:
                raise ValueError(f"layer_gains shape 应为 [B,N_RE,rank] 或 [B,rank]，当前为 {layer_gains.shape}")
        gains_tf = tf.constant(layer_gains, dtype=tf.float32)
        if noise is None:
            noise_tf = None
        else:
            noise = np.asarray(noise)
            if noise.shape != layer_gains.shape:
                raise ValueError(f"noise shape 应为 {layer_gains.shape}，当前为 {noise.shape}")
            noise_tf = tf.constant(noise, dtype=tf.complex64)
        outs = self._run_tf_graph(tuple(b_tensors), snr_db_tf, gains_tf, noise_tf)

        decoded: Dict[int, List[np.ndarray]] = {cw_idx: [] for cw_idx in self.cw_indices}
        for pos, (cw_idx, cb_idx) in enumerate(self.cb_order):
            k = self.cb_k_by_key[(cw_idx, cb_idx)]
            arr = np.asarray(outs[pos].numpy()).reshape(batch_size, -1)
            decoded[cw_idx].append((arr[:, :k] > 0.5).astype(np.int8))
        return decoded

class SionnaLDPCBitLevelOrchestrator:
    """Sionna LDPC bit-level v2.4 编排器。"""

    def __init__(self, config: PlatformConfig):
        self.cfg = config
        self.rng = make_rng(config.simulation.seed)
        self.mcs_table = get_mcs_table(config.simulation.mcs_table)
        self.tb_manager = TBManager(config.resource)
        self.tf, self.LDPC5GEncoder, self.LDPC5GDecoder, self.Mapper, self.Demapper = _import_sionna_components()
        try:
            self.tf.random.set_seed(int(config.simulation.seed))
        except Exception:
            pass

        # v2.6 允许 fixed_mcs=None。当 adaptive_mcs.enabled=True 时进入 per-trial adaptive。
        if int(config.mapping.rank) < 1 or int(config.mapping.rank) > 16:
            raise ValueError("sionna_ldpc_bit_level v2.7 当前支持 1 <= rank <= 16。")
        if int(config.simulation.batch_size) <= 0:
            raise ValueError("simulation.batch_size 必须为正整数。")
        if str(config.channel.model).lower() in ("rayleigh", "flat_rayleigh"):
            if int(config.mapping.rank) > min(int(config.antenna.bs_n_antennas), int(config.antenna.ue_n_antennas)):
                raise ValueError("Rayleigh rank 不能超过 min(bs_n_antennas, ue_n_antennas)")

        self._chain_cache: Dict[Tuple, _SionnaMultiCWGridChain] = {}
        # v2.8 fairness rule: within one run, all schemes at the same SNR share
        # the same CDL/SVD channel samples and deterministic AWGN noise samples.
        self.layer_quality_records: List[Dict[str, object]] = []
        # v2.9 lightweight per-trial/CW records for CDF analysis.
        # One row per (snr, scheme, trial, cw); no large tensors are stored.
        self.cw_trial_records: List[Dict[str, object]] = []
        self._recorded_layer_batches = set()
        self.cdl_builder = None
        if str(config.channel.model).lower() in ("cdl", "sionna_cdl", "cdl_svd"):
            self.cdl_builder = CDLChannelBuilder(config.antenna, config.resource, config.channel)

    def _get_chain(self, cb_params: Mapping[int, List[Tuple[int, int, int]]], plan: GridMappingPlan) -> _SionnaMultiCWGridChain:
        key = (
            _cb_params_signature(cb_params),
            str(self.cfg.channel.model).lower(),
            int(self.cfg.antenna.bs_n_antennas),
            int(self.cfg.antenna.ue_n_antennas),
            _plan_signature(plan),
        )
        if key not in self._chain_cache:
            self._chain_cache[key] = _SionnaMultiCWGridChain(
                self.tf,
                self.LDPC5GEncoder,
                self.LDPC5GDecoder,
                self.Mapper,
                self.Demapper,
                cb_params=cb_params,
                plan=plan,
                channel_model=str(self.cfg.channel.model),
                n_tx=int(self.cfg.antenna.bs_n_antennas),
                n_rx=int(self.cfg.antenna.ue_n_antennas),
                num_iter=int(self.cfg.receiver.max_ldpc_iterations),
            )
        return self._chain_cache[key]

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
        candidates = []
        if self.cfg.comparison.enabled:
            scheme_specs = self.cfg.comparison.schemes
        else:
            spec = {"scheme_id": self.cfg.mapping.scheme_id}
            mapping_params = getattr(self.cfg.mapping, "params", {}) or {}
            if mapping_params:
                spec["params"] = dict(mapping_params)
            scheme_specs = [spec]

        for spec in scheme_specs:
            spec = dict(spec or {})
            params = dict(spec.get("params", {}) or {})

            scheme_id = int(spec.get("scheme_id", self.cfg.mapping.scheme_id))
            rank = int(spec.get("rank", self.cfg.mapping.rank))
            kwargs = {"scheme_id": scheme_id, "rank": rank}

            for key in ("partition", "partitions", "scheme7_mode"):
                if key in spec:
                    kwargs[key] = spec.get(key)
                if key in params:
                    kwargs[key] = params.get(key)

            scheme = create_scheme("flexible_cw", **kwargs)
            candidates.extend(scheme.expand_candidates(rank=rank))
        return candidates

    @staticmethod
    def _payload_bits_per_cb(tb_info) -> List[int]:
        n = max(int(tb_info.n_cbs), 1)
        base = int(tb_info.tb_size) // n
        rem = int(tb_info.tb_size) % n
        return [base + (1 if i < rem else 0) for i in range(n)]

    @staticmethod
    def _stable_seed(*parts) -> int:
        payload = "|".join(str(x) for x in parts).encode("utf-8")
        digest = hashlib.blake2b(payload, digest_size=8).digest()
        return int.from_bytes(digest, "little") & 0xFFFFFFFF

    def _make_common_noise(self, snr_db: float, trial_ids, n_re: int, rank: int) -> np.ndarray:
        """Generate deterministic common complex AWGN samples for all schemes."""
        snr_linear = 10.0 ** (float(snr_db) / 10.0)
        n0 = 1.0 / max(snr_linear, 1e-30)
        sigma = np.sqrt(n0 / 2.0)
        out = np.zeros((len(trial_ids), int(n_re), int(rank)), dtype=np.complex64)
        base_seed = int(self.cfg.simulation.seed)
        for i, trial_id in enumerate(trial_ids):
            seed = self._stable_seed("noise", base_seed, float(snr_db), int(trial_id))
            rng = np.random.default_rng(seed)
            nr = rng.normal(size=(int(n_re), int(rank))).astype(np.float32)
            ni = rng.normal(size=(int(n_re), int(rank))).astype(np.float32)
            out[i] = (sigma * (nr + 1j * ni)).astype(np.complex64)
        return out

    def _olla_enabled(self) -> bool:
        return bool(getattr(getattr(self.cfg, "olla", None), "enabled", False))

    def _olla_cfg(self):
        return getattr(self.cfg, "olla", None)

    def _olla_layer_offsets_for_scheme(self, scheme, offsets_by_cw: Dict[int, float]) -> np.ndarray:
        """Return a rank-length vector of per-layer OLLA offsets in dB.

        Offsets are stored per CW. Since every layer belongs to exactly one CW
        for a given partition, we expand CW offsets to layer offsets before
        recomputing the SE used by the existing MCS selectors. This also works
        for Scheme2 because its unified-rate/per-layer-Qm optimizer sees the
        offset-adjusted per-layer SE.
        """
        rank = int(scheme.rank)
        out = np.zeros(rank, dtype=np.float64)
        try:
            partition = scheme._get_partition()
        except Exception:
            partition = [rank]
        start = 0
        init = float(getattr(self._olla_cfg(), "offset_init_db", 0.0)) if self._olla_enabled() else 0.0
        for cw_idx, n_layers in enumerate(partition):
            end = start + int(n_layers)
            off = float(offsets_by_cw.get(int(cw_idx), init))
            out[start:end] = off
            start = end
        return out

    def _layer_se_with_olla_offsets(self, layer_sinrs_db: np.ndarray, layer_offsets_db: np.ndarray) -> np.ndarray:
        """Recompute per-layer selection SE after applying OLLA SINR offsets."""
        sinr_db = np.asarray(layer_sinrs_db, dtype=np.float64) + np.asarray(layer_offsets_db, dtype=np.float64)
        gamma_db = float(getattr(self.cfg.adaptive_mcs, "shannon_gap_db", self.cfg.simulation.shannon_gap_db)) \
                   + float(getattr(self.cfg.adaptive_mcs, "mcs_margin_db", 0.0))
        gamma = 10.0 ** (gamma_db / 10.0)
        sinr_lin = 10.0 ** (sinr_db / 10.0)
        return np.log2(1.0 + np.maximum(sinr_lin, 1e-30) / gamma).astype(np.float32)

    def _olla_scheme_base_name(self, scheme_name: str) -> str:
        """Return a coarse scheme key for per-scheme OLLA settings.

        Examples:
          scheme7_partition_3+1 -> scheme7
          scheme2               -> scheme2
        """
        name = str(scheme_name or "").strip()
        if "_partition_" in name:
            return name.split("_partition_", 1)[0]
        return name

    def _olla_step_db_for_scheme(self, scheme_name: str) -> float:
        """Get OLLA step for a scheme, with exact/base/default fallback.

        YAML example:
          olla:
            step_db: 0.05
            scheme_step_db:
              scheme2: 0.10
              scheme7: 0.06
              scheme7_partition_3+1: 0.08

        Matching order:
          1) exact scheme label, e.g. scheme7_partition_3+1
          2) base label, e.g. scheme7
          3) global olla.step_db
        """
        cfg = self._olla_cfg()
        default_step = float(getattr(cfg, "step_db", 0.05))
        scheme_steps = getattr(cfg, "scheme_step_db", {}) or {}
        if not isinstance(scheme_steps, dict):
            return default_step

        name = str(scheme_name or "").strip()
        if name in scheme_steps:
            return float(scheme_steps[name])

        base_name = self._olla_scheme_base_name(name)
        if base_name in scheme_steps:
            return float(scheme_steps[base_name])

        return default_step

    def _olla_update_offset(self, offsets_by_cw: Dict[int, float], ewma_by_cw: Dict[int, float], cw_idx: int,
                            e_t: float, allow_update: bool = True, scheme_name: str = "") -> Tuple[float, float, float, float]:
        """Update and return (old_offset, new_offset, ewma_error, step_db)."""
        cfg = self._olla_cfg()
        init = float(getattr(cfg, "offset_init_db", 0.0))
        old = float(offsets_by_cw.get(int(cw_idx), init))
        old_ewma = float(ewma_by_cw.get(int(cw_idx), float(getattr(cfg, "target_cb_bler", 0.1))))
        beta = float(getattr(cfg, "ewma_beta", 0.02))
        beta = min(max(beta, 0.0), 1.0)
        new_ewma = (1.0 - beta) * old_ewma + beta * float(e_t)
        ewma_by_cw[int(cw_idx)] = float(new_ewma)
        step = self._olla_step_db_for_scheme(scheme_name)
        if not allow_update:
            offsets_by_cw[int(cw_idx)] = old
            return old, old, float(new_ewma), float(step)
        target = float(getattr(cfg, "target_cb_bler", 0.1))
        new = old + step * (target - float(e_t))
        lo = float(getattr(cfg, "offset_min_db", -6.0))
        hi = float(getattr(cfg, "offset_max_db", 6.0))
        new = min(max(new, lo), hi)
        offsets_by_cw[int(cw_idx)] = float(new)
        return old, float(new), float(new_ewma), float(step)

    def _rx_ce_enabled(self) -> bool:
        ce = getattr(self.cfg, "channel_estimation", None)
        if ce is None:
            return False
        mode = str(getattr(ce, "mode", "ideal")).lower()
        return bool(getattr(ce, "enabled", False)) or mode in ("additive_error", "nmse")

    def _effective_quality_from_sinr_linear(self, sinr_linear: np.ndarray, snr_db: float, rank: int):
        """Capacity-average effective layer quality from per-RE SINR samples.

        sinr_linear shape: [B,T,F,rank] or [B,N_RE,rank].
        Returns layer_se_eff, layer_sinr_eff_db, layer_sinr_re_db_mean.
        """
        sinr = np.asarray(sinr_linear, dtype=np.float64)
        gamma_db = float(getattr(self.cfg.adaptive_mcs, "shannon_gap_db", self.cfg.simulation.shannon_gap_db)) \
                   + float(getattr(self.cfg.adaptive_mcs, "mcs_margin_db", 0.0))
        gamma = 10.0 ** (gamma_db / 10.0)
        se = np.log2(1.0 + np.maximum(sinr, 1e-30) / gamma)
        axes = tuple(range(1, se.ndim - 1))
        layer_se_eff = np.mean(se, axis=axes)
        sinr_eff = gamma * (np.power(2.0, layer_se_eff) - 1.0)
        layer_sinr_eff_db = 10.0 * np.log10(np.maximum(sinr_eff, 1e-30))
        layer_sinr_re_db_mean = 10.0 * np.log10(np.maximum(np.mean(sinr, axis=axes), 1e-30))
        return {
            "layer_se_eff": np.asarray(layer_se_eff, dtype=np.float32),
            "layer_sinr_eff_db": np.asarray(layer_sinr_eff_db, dtype=np.float32),
            "layer_sinr_re_db_mean": np.asarray(layer_sinr_re_db_mean, dtype=np.float32),
            "gamma_total_db": float(gamma_db),
        }

    def _make_decode_gains_from_sinr(self, sinr_linear_re: np.ndarray, snr_db: float) -> np.ndarray:
        """Convert per-RE target SINR to diagonal equivalent layer gains for the existing chain.

        The Sionna grid chain applies y=g*x+n and equalizes by g, so its
        demapper sees SINR = g^2 / N0 = SNR_linear*g^2. Thus we choose
        g=sqrt(SINR_target/SNR_linear).
        """
        snr_linear = 10.0 ** (float(snr_db) / 10.0)
        gains = np.sqrt(np.maximum(np.asarray(sinr_linear_re, dtype=np.float32), 1e-30) / max(snr_linear, 1e-30))
        if gains.ndim == 4:
            b, t, f, r = gains.shape
            gains = gains.reshape((b, t * f, r))
        return gains.astype(np.float32)

    def _compute_rx_ce_quality(self, cdl_batch, snr_db: float, rank: int, batch_start: int):
        """Compute v3.0 abstract RX-CE actual post-SINR metrics.

        True effective channel is H_eff = H V_svd. UE estimates
        H_hat_eff = H_eff + E, builds W(H_hat_eff), and actual SINR is
        evaluated through W(H_hat_eff) H_eff. This is a receiver-side
        estimation abstraction: TX SVD is still ideal.
        """
        ce = getattr(self.cfg, "channel_estimation", None)
        mode = str(getattr(ce, "mode", "ideal")).lower() if ce is not None else "ideal"
        if not self._rx_ce_enabled() or mode == "ideal":
            return None

        equalizer = str(getattr(ce, "equalizer", "mmse")).lower()
        nmse_db = float(getattr(ce, "nmse_db", -25.0))
        nmse_lin = 10.0 ** (nmse_db / 10.0)
        seed_base = int(getattr(ce, "seed_base", 93001))
        seed = self._stable_seed("rxce", seed_base, int(self.cfg.simulation.seed), float(snr_db), int(batch_start), int(rank), nmse_db, equalizer)
        rng = np.random.default_rng(seed)

        h_np = np.asarray(cdl_batch.h_tf.numpy()).astype(np.complex64)  # [B,T,F,Rx,Tx]
        bsz, nt, nf, n_rx, _n_tx = h_np.shape
        r = int(rank)
        snr_linear = 10.0 ** (float(snr_db) / 10.0)
        n0 = 1.0 / max(snr_linear, 1e-30)
        p_layer = 1.0 / float(max(r, 1))

        # Batched SVD. np.linalg.svd supports stacked matrices.
        u, svals, _vh = np.linalg.svd(h_np, full_matrices=False)
        u_r = u[..., :r]                         # [B,T,F,Rx,r]
        s_r = svals[..., :r].astype(np.float32)  # [B,T,F,r]
        h_eff = u_r * s_r[..., np.newaxis, :]    # H V_svd = U_r diag(s_r)

        # Add complex Gaussian estimation error with per-RE NMSE.
        ref_pow = np.mean(np.abs(h_eff) ** 2, axis=(-2, -1), keepdims=True)
        sigma2_e = nmse_lin * np.maximum(ref_pow, 1e-30)
        e_r = rng.normal(size=h_eff.shape).astype(np.float32)
        e_i = rng.normal(size=h_eff.shape).astype(np.float32)
        err = np.sqrt(sigma2_e / 2.0).astype(np.float32) * (e_r + 1j * e_i)
        h_hat = (h_eff + err).astype(np.complex64)

        h_hat_h = np.swapaxes(np.conjugate(h_hat), -1, -2)  # [...,r,Rx]
        if equalizer == "zf":
            # W = (Hhat^H Hhat + eps I)^-1 Hhat^H for numerical stability.
            gram = h_hat_h @ h_hat
            eye = np.eye(r, dtype=np.complex64)
            gram = gram + (1e-7 * eye)
            w = np.linalg.solve(gram, h_hat_h)
        else:
            # Linear MMSE for equal per-layer power p_layer.
            gram = h_hat_h @ h_hat
            eye = np.eye(r, dtype=np.complex64)
            reg = (n0 / max(p_layer, 1e-30)) * eye
            w = np.linalg.solve(gram + reg, h_hat_h)

        g_true = w @ h_eff  # [...,r,r]
        g_hat = w @ h_hat
        abs_g2 = np.abs(g_true) ** 2
        abs_ghat2 = np.abs(g_hat) ** 2
        diag_true = np.diagonal(abs_g2, axis1=-2, axis2=-1)
        diag_hat = np.diagonal(abs_ghat2, axis1=-2, axis2=-1)
        total_true = np.sum(abs_g2, axis=-1)
        total_hat = np.sum(abs_ghat2, axis=-1)
        interf_true = np.maximum(total_true - diag_true, 0.0) * p_layer
        interf_hat = np.maximum(total_hat - diag_hat, 0.0) * p_layer
        desired_true = diag_true * p_layer
        desired_hat = diag_hat * p_layer
        noise_enh = n0 * np.sum(np.abs(w) ** 2, axis=-1)

        actual_sinr = desired_true / np.maximum(interf_true + noise_enh, 1e-30)
        ue_est_sinr = desired_hat / np.maximum(interf_hat + noise_enh, 1e-30)
        ideal_sinr = snr_linear * (s_r ** 2) / float(max(r, 1))

        actual_q = self._effective_quality_from_sinr_linear(actual_sinr, snr_db=snr_db, rank=r)
        ue_q = self._effective_quality_from_sinr_linear(ue_est_sinr, snr_db=snr_db, rank=r)
        ideal_q = self._effective_quality_from_sinr_linear(ideal_sinr, snr_db=snr_db, rank=r)

        err_pow = np.sum(np.abs(err) ** 2, axis=(-2, -1))
        sig_pow = np.sum(np.abs(h_eff) ** 2, axis=(-2, -1))
        nmse_re = err_pow / np.maximum(sig_pow, 1e-30)
        axes = tuple(range(1, ideal_sinr.ndim - 1))

        return {
            "enabled": True,
            "mode": str(mode),
            "equalizer": str(equalizer),
            "nmse_db_config": float(nmse_db),
            "nmse_db_measured_by_layer": np.tile(10.0 * np.log10(np.maximum(np.mean(nmse_re, axis=axes), 1e-30))[:, None], (1, r)).astype(np.float32),
            "ideal_sinr_linear_re": np.asarray(ideal_sinr, dtype=np.float32),
            "actual_sinr_linear_re": np.asarray(actual_sinr, dtype=np.float32),
            "ue_est_sinr_linear_re": np.asarray(ue_est_sinr, dtype=np.float32),
            "ideal_layer_se_eff": np.asarray(ideal_q["layer_se_eff"], dtype=np.float32),
            "ideal_layer_sinr_eff_db": np.asarray(ideal_q["layer_sinr_eff_db"], dtype=np.float32),
            "actual_layer_se_eff": np.asarray(actual_q["layer_se_eff"], dtype=np.float32),
            "actual_layer_sinr_eff_db": np.asarray(actual_q["layer_sinr_eff_db"], dtype=np.float32),
            "ue_est_layer_se_eff": np.asarray(ue_q["layer_se_eff"], dtype=np.float32),
            "ue_est_layer_sinr_eff_db": np.asarray(ue_q["layer_sinr_eff_db"], dtype=np.float32),
            "post_sinr_loss_db": np.asarray(ideal_q["layer_sinr_eff_db"] - actual_q["layer_sinr_eff_db"], dtype=np.float32),
            "desired_power_mean": np.asarray(np.mean(desired_true, axis=axes), dtype=np.float32),
            "interference_power_mean": np.asarray(np.mean(interf_true, axis=axes), dtype=np.float32),
            "noise_enhancement_power_mean": np.asarray(np.mean(noise_enh, axis=axes), dtype=np.float32),
            "decode_layer_gains": self._make_decode_gains_from_sinr(actual_sinr, snr_db=snr_db),
        }

    def _sample_common_cdl_batch(self, snr_db: float, batch_start: int, batch_size: int, rank: int):
        """Generate deterministic common CDL/SVD batch for all schemes.

        The cdl_builder itself may rely on TensorFlow/NumPy RNGs. We reset both
        seeds before sampling so that every scheme obtains the same CDL/SVD batch
        for the same (snr_db, batch_start, batch_size, rank). This gives the
        same fairness semantics as caching a full-SNR common channel, but with
        O(batch_size) memory.
        """
        if self.cdl_builder is None:
            raise RuntimeError("CDL builder is not initialized")

        seed = self._stable_seed("cdl", int(self.cfg.simulation.seed), float(snr_db), int(batch_start), int(batch_size), int(rank))
        try:
            np.random.seed(seed % (2**32 - 1))
        except Exception:
            pass
        try:
            self.tf.random.set_seed(seed)
        except Exception:
            pass

        cdl_batch = self.cdl_builder.sample(int(batch_size))
        svals = np.asarray(cdl_batch.singular_values[..., :int(rank)], dtype=np.float32)
        layer_se_list = []
        layer_sinr_list = []
        for b in range(int(batch_size)):
            q = compute_svd_capacity_quality(
                svals[b],
                snr_db=float(snr_db),
                rank=int(rank),
                shannon_gap_db=float(getattr(self.cfg.adaptive_mcs, "shannon_gap_db", self.cfg.simulation.shannon_gap_db)),
                mcs_margin_db=float(getattr(self.cfg.adaptive_mcs, "mcs_margin_db", 0.0)),
            )
            layer_se_list.append(np.asarray(q.layer_se_eff, dtype=np.float32))
            layer_sinr_list.append(np.asarray(q.layer_sinr_eff_db, dtype=np.float32))

        rx_ce_quality = self._compute_rx_ce_quality(
            cdl_batch=cdl_batch,
            snr_db=float(snr_db),
            rank=int(rank),
            batch_start=int(batch_start),
        )

        out = {
            "svals": svals,
            "layer_se_eff": np.asarray(layer_se_list, dtype=np.float32),
            "layer_sinr_eff_db": np.asarray(layer_sinr_list, dtype=np.float32),
            "cdl_report": dict(getattr(cdl_batch, "report", {}) or {}),
        }
        if rx_ce_quality is not None:
            out["rx_ce_quality"] = rx_ce_quality
        return out

    def _record_layer_quality_batch(self, snr_db: float, batch_start: int, batch_quality) -> None:
        """Record per-trial/per-layer post-SINR samples once per SNR batch."""
        rank = int(self.cfg.mapping.rank)
        svals_batch = batch_quality["svals"]
        layer_se_batch = batch_quality["layer_se_eff"]
        layer_sinr_batch = batch_quality["layer_sinr_eff_db"]
        bsz = int(svals_batch.shape[0])
        rxq = batch_quality.get("rx_ce_quality", None)

        for b in range(bsz):
            trial_id = int(batch_start + b)
            svals = svals_batch[b]
            flat_s = np.reshape(svals, (-1, rank))
            singular_mean = np.mean(flat_s, axis=0)
            singular_p10 = np.percentile(flat_s, 10, axis=0)
            singular_p50 = np.percentile(flat_s, 50, axis=0)
            singular_p90 = np.percentile(flat_s, 90, axis=0)
            for layer_idx in range(rank):
                rec = {
                    "snr_db": float(snr_db),
                    "trial_id": int(trial_id),
                    "layer_index": int(layer_idx),
                    "rank": int(rank),
                    "singular_value_mean": float(singular_mean[layer_idx]),
                    "singular_value_p10": float(singular_p10[layer_idx]),
                    "singular_value_p50": float(singular_p50[layer_idx]),
                    "singular_value_p90": float(singular_p90[layer_idx]),
                    # Backward-compatible ideal SVD post-SINR field used by v2.9 plots.
                    "post_sinr_db": float(layer_sinr_batch[b, layer_idx]),
                    "post_sinr_ideal_db": float(layer_sinr_batch[b, layer_idx]),
                    "layer_se_eff": float(layer_se_batch[b, layer_idx]),
                    "layer_se_ideal_eff": float(layer_se_batch[b, layer_idx]),
                    "channel_model": str(self.cfg.channel.model),
                    "cdl_type": str(getattr(self.cfg.channel, "cdl_type", "")),
                    "delay_spread_ns": float(getattr(self.cfg.channel, "delay_spread_ns", 0.0)),
                    "ue_speed_kmh": float(getattr(self.cfg.channel, "ue_speed_kmh", 0.0)),
                    "bs_array": str(getattr(self.cfg.antenna, "bs_antenna_array", "")),
                    "ue_array": str(getattr(self.cfg.antenna, "ue_antenna_array", "")),
                    "precoding": str(getattr(self.cfg.csi, "precoding_method", "svd")),
                    "receiver": str(getattr(self.cfg.receiver, "detector", "ideal_svd_equivalent")),
                    "gamma_total_db": float(getattr(self.cfg.adaptive_mcs, "shannon_gap_db", self.cfg.simulation.shannon_gap_db))
                                      + float(getattr(self.cfg.adaptive_mcs, "mcs_margin_db", 0.0)),
                    "rx_ce_enabled": int(rxq is not None),
                    "rx_ce_mode": "" if rxq is None else str(rxq.get("mode", "")),
                    "rx_ce_equalizer": "" if rxq is None else str(rxq.get("equalizer", "")),
                    "rx_ce_nmse_db_config": float("nan") if rxq is None else float(rxq.get("nmse_db_config", float("nan"))),
                    "rx_ce_nmse_db_measured": float("nan") if rxq is None else float(rxq["nmse_db_measured_by_layer"][b, layer_idx]),
                    "post_sinr_actual_rx_db": float("nan") if rxq is None else float(rxq["actual_layer_sinr_eff_db"][b, layer_idx]),
                    "post_sinr_estimated_by_ue_db": float("nan") if rxq is None else float(rxq["ue_est_layer_sinr_eff_db"][b, layer_idx]),
                    "post_sinr_loss_db": float("nan") if rxq is None else float(rxq["post_sinr_loss_db"][b, layer_idx]),
                    "layer_se_actual_rx_eff": float("nan") if rxq is None else float(rxq["actual_layer_se_eff"][b, layer_idx]),
                    "layer_se_estimated_by_ue_eff": float("nan") if rxq is None else float(rxq["ue_est_layer_se_eff"][b, layer_idx]),
                    "desired_power_mean": float("nan") if rxq is None else float(rxq["desired_power_mean"][b, layer_idx]),
                    "inter_layer_interference_power_mean": float("nan") if rxq is None else float(rxq["interference_power_mean"][b, layer_idx]),
                    "noise_enhancement_power_mean": float("nan") if rxq is None else float(rxq["noise_enhancement_power_mean"][b, layer_idx]),
                }
                self.layer_quality_records.append(rec)

    def _save_layer_quality_records(self, path: Path) -> None:
        if not self.layer_quality_records:
            return
        fieldnames = list(self.layer_quality_records[0].keys())
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(self.layer_quality_records)

    def _save_cw_trial_records(self, path: Path) -> None:
        """Save v2.9 per-trial/CW link-adaptation records.

        The rows are intentionally lightweight: one row per (SNR, scheme, trial, CW),
        containing only scalar summaries needed for CDF analysis of:
        - MCS quantization/capping loss
        - coding/CB-failure loss
        - per-CW selected MCS CDF
        - CW-internal layer SE/SINR imbalance
        """
        if not self.cw_trial_records:
            return
        fieldnames = []
        for r in self.cw_trial_records:
            for k in r.keys():
                if k not in fieldnames:
                    fieldnames.append(k)
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(self.cw_trial_records)

    def _build_cw_trial_record_templates(self, scheme, snr_db: float, trial_id: int, tx_cfg, tb_by_cw,
                                         layer_se_eff=None, layer_sinrs_db=None,
                                         mcs_selection_layer_se_eff=None, olla_layer_offsets_db=None,
                                         actual_layer_se_eff=None, actual_layer_sinrs_db=None,
                                         ue_est_layer_sinrs_db=None, post_sinr_loss_db=None,
                                         rx_ce_nmse_db=None, rx_ce_mode: str = "", rx_ce_equalizer: str = "",
                                         olla_enabled: bool = False, olla_phase: str = "measure") -> Dict[int, Dict[str, object]]:
        """Build per-CW record templates before decoding.

        These templates are later completed in _run_group() with CB success/error
        counts and per-trial successful payload.
        """
        rank = int(scheme.rank)
        se_arr = np.asarray(layer_se_eff if layer_se_eff is not None else [np.nan] * rank, dtype=float)
        sel_se_arr = np.asarray(mcs_selection_layer_se_eff if mcs_selection_layer_se_eff is not None else se_arr, dtype=float)
        offset_arr = np.asarray(olla_layer_offsets_db if olla_layer_offsets_db is not None else [0.0] * rank, dtype=float)
        sinr_arr = np.asarray(layer_sinrs_db if layer_sinrs_db is not None else [np.nan] * rank, dtype=float)
        actual_se_arr = np.asarray(actual_layer_se_eff if actual_layer_se_eff is not None else [np.nan] * rank, dtype=float)
        actual_sinr_arr = np.asarray(actual_layer_sinrs_db if actual_layer_sinrs_db is not None else [np.nan] * rank, dtype=float)
        ue_est_sinr_arr = np.asarray(ue_est_layer_sinrs_db if ue_est_layer_sinrs_db is not None else [np.nan] * rank, dtype=float)
        loss_arr = np.asarray(post_sinr_loss_db if post_sinr_loss_db is not None else [np.nan] * rank, dtype=float)
        nmse_arr = np.asarray(rx_ce_nmse_db if rx_ce_nmse_db is not None else [np.nan] * rank, dtype=float)
        out: Dict[int, Dict[str, object]] = {}
        for cw_cfg in tx_cfg.cw_configs:
            cw_idx = int(cw_cfg.cw_index)
            tb = tb_by_cw[cw_idx]
            layers = [int(x) for x in cw_cfg.layer_indices]
            cw_se = se_arr[layers] if len(layers) else np.asarray([], dtype=float)
            cw_sel_se = sel_se_arr[layers] if len(layers) else np.asarray([], dtype=float)
            cw_offsets = offset_arr[layers] if len(layers) else np.asarray([], dtype=float)
            cw_sinr = sinr_arr[layers] if len(layers) else np.asarray([], dtype=float)
            cw_actual_se = actual_se_arr[layers] if len(layers) else np.asarray([], dtype=float)
            cw_actual_sinr = actual_sinr_arr[layers] if len(layers) else np.asarray([], dtype=float)
            cw_ue_est_sinr = ue_est_sinr_arr[layers] if len(layers) else np.asarray([], dtype=float)
            cw_loss = loss_arr[layers] if len(layers) else np.asarray([], dtype=float)
            cw_nmse = nmse_arr[layers] if len(layers) else np.asarray([], dtype=float)
            code_rate = float(cw_cfg.code_rate)
            qms = {int(k): int(v) for k, v in cw_cfg.layer_modulations.items()}
            # MCS-loaded SE before TBS rounding: sum_l Qm_l * R.
            mcs_total_se = float(sum(float(qms.get(int(l), 0)) * code_rate for l in layers))
            target_total_se = float(np.nansum(cw_se)) if cw_se.size else 0.0
            selection_target_total_se = float(np.nansum(cw_sel_se)) if cw_sel_se.size else float(target_total_se)
            cw_olla_offset_db = float(np.nanmean(cw_offsets)) if cw_offsets.size else 0.0
            n_re = max(int(getattr(tb, "n_re_per_layer", 0)), 1)
            loaded_payload_se_tf = float(int(tb.tb_size)) / float(n_re)
            out[cw_idx] = {
                "snr_db": float(snr_db),
                "trial_id": int(trial_id),
                "scheme": str(tx_cfg.scheme_name),
                "scheme_id": int(scheme.scheme_id),
                "rank": int(rank),
                "partition": "+".join(map(str, tx_cfg.partition)) if getattr(tx_cfg, "partition", None) else "",
                "num_cws": int(len(tx_cfg.cw_configs)),
                "cw_index": int(cw_idx),
                "layers": str(layers),
                "n_layers": int(len(layers)),
                "selected_mcs": -1 if cw_cfg.mcs_index is None else int(cw_cfg.mcs_index),
                "selected_mcs_label": "mixed" if cw_cfg.mcs_index is None else f"mcs{int(cw_cfg.mcs_index)}",
                "code_rate": float(code_rate),
                "olla_enabled": int(bool(olla_enabled)),
                "olla_phase": str(olla_phase),
                "olla_offset_db_before": float(cw_olla_offset_db),
                "mcs_selection_target_se_total": float(selection_target_total_se),
                "mcs_selection_target_se_mean": float(np.nanmean(cw_sel_se)) if cw_sel_se.size else 0.0,
                "qms": str(qms),
                "target_se_total": float(target_total_se),
                "target_se_mean": float(np.nanmean(cw_se)) if cw_se.size else 0.0,
                "target_se_min": float(np.nanmin(cw_se)) if cw_se.size else 0.0,
                "target_se_std": float(np.nanstd(cw_se)) if cw_se.size else 0.0,
                "target_se_gap": float(np.nanmax(cw_se) - np.nanmin(cw_se)) if cw_se.size else 0.0,
                "post_sinr_db_mean": float(np.nanmean(cw_sinr)) if cw_sinr.size else 0.0,
                "post_sinr_db_min": float(np.nanmin(cw_sinr)) if cw_sinr.size else 0.0,
                "post_sinr_db_std": float(np.nanstd(cw_sinr)) if cw_sinr.size else 0.0,
                "post_sinr_db_gap": float(np.nanmax(cw_sinr) - np.nanmin(cw_sinr)) if cw_sinr.size else 0.0,
                "rx_ce_enabled": int(actual_layer_sinrs_db is not None),
                "rx_ce_mode": str(rx_ce_mode),
                "rx_ce_equalizer": str(rx_ce_equalizer),
                "rx_ce_nmse_db_mean": float(np.nanmean(cw_nmse)) if cw_nmse.size else float("nan"),
                "actual_rx_target_se_total": float(np.nansum(cw_actual_se)) if cw_actual_se.size else float("nan"),
                "actual_rx_post_sinr_db_mean": float(np.nanmean(cw_actual_sinr)) if cw_actual_sinr.size else float("nan"),
                "actual_rx_post_sinr_db_min": float(np.nanmin(cw_actual_sinr)) if cw_actual_sinr.size else float("nan"),
                "actual_rx_post_sinr_db_std": float(np.nanstd(cw_actual_sinr)) if cw_actual_sinr.size else float("nan"),
                "actual_rx_post_sinr_db_gap": float(np.nanmax(cw_actual_sinr) - np.nanmin(cw_actual_sinr)) if cw_actual_sinr.size else float("nan"),
                "ue_est_post_sinr_db_mean": float(np.nanmean(cw_ue_est_sinr)) if cw_ue_est_sinr.size else float("nan"),
                "post_sinr_loss_db_mean": float(np.nanmean(cw_loss)) if cw_loss.size else float("nan"),
                "post_sinr_loss_db_min": float(np.nanmin(cw_loss)) if cw_loss.size else float("nan"),
                "post_sinr_loss_db_max": float(np.nanmax(cw_loss)) if cw_loss.size else float("nan"),
                "mcs_se_total": float(mcs_total_se),
                "mcs_quantization_loss_se": float(target_total_se - mcs_total_se),
                "mcs_quantization_loss_se_clipped": float(max(target_total_se - mcs_total_se, 0.0)),
                "tb_size": int(tb.tb_size),
                "n_cbs": int(tb.n_cbs),
                "n_re_per_layer": int(n_re),
                "loaded_payload_se_tf": float(loaded_payload_se_tf),
                "tbs_rounding_loss_se": float(mcs_total_se - loaded_payload_se_tf),
                "cb_E": str([int(cb.E) for cb in tb.cb_infos]),
                "cb_K": str([int(cb.K) for cb in tb.cb_infos]),
            }
        return out

    def run(self) -> Path:
        out_dir = self._make_output_dir()
        save_resolved_config(self.cfg, out_dir / "config_resolved.yaml")

        summaries = []
        schemes = self._expand_scheme_candidates()
        snrs = self._snr_values()
        is_cdl = str(self.cfg.channel.model).lower() in ("cdl", "sionna_cdl", "cdl_svd")

        if self.cfg.debug.verbose:
            print("输出目录: {}".format(out_dir))
            print("Sionna LDPC bit-level 候选方案数: {}".format(len(schemes)))
            print("SNR点: {}".format(snrs))
            print("Sionna LDPC v3.0 batch_size: {}".format(self.cfg.simulation.batch_size))
            print("Sionna LDPC v3.0 channel: {}, rank: {}".format(self.cfg.channel.model, self.cfg.mapping.rank))
            if is_cdl:
                print("v3.0公平性: 同一SNR下所有scheme共用CDL/SVD信道样本和确定性AWGN噪声样本。")
            if self._rx_ce_enabled():
                print("v3.0 RX-CE: mode={}, nmse_db={}, equalizer={}, apply_to_decoding={}".format(
                    getattr(self.cfg.channel_estimation, "mode", "ideal"),
                    getattr(self.cfg.channel_estimation, "nmse_db", ""),
                    getattr(self.cfg.channel_estimation, "equalizer", "mmse"),
                    getattr(self.cfg.channel_estimation, "use_actual_rx_post_sinr_for_decoding", True),
                ))

        for scheme in schemes:
            if int(scheme.rank) < 1 or int(scheme.rank) > 16:
                raise ValueError("sionna_ldpc_bit_level v2.8 当前支持 1 <= rank <= 16；更高 rank 请先扩展配置和天线维度。")

        for snr_db in snrs:
            for scheme in schemes:
                summary = self._run_one_scheme_one_snr(scheme, snr_db)
                summaries.append(summary)
                if self.cfg.debug.verbose:
                    print(
                        "{:<28s} SNR={:5.1f} dB slotBLER={:.4g} cbBLER={:.4g} goodput={:.1f} bits/slot".format(
                            summary.scheme_label, snr_db, summary.scheme_bler, summary.cb_bler,
                            summary.goodput_bits_per_slot
                        )
                    )
            # Avoid retaining many LDPC encoder/decoder graphs across SNR points.
            self._chain_cache.clear()
            try:
                gc.collect()
            except Exception:
                pass

        save_csv(summaries, out_dir / "results.csv")
        save_json(summaries, out_dir / "results.json")
        self._save_layer_quality_records(out_dir / "layer_post_sinr_samples.csv")
        self._save_cw_trial_records(out_dir / "cw_trial_link_adaptation_records.csv")
        plot_bler(summaries, out_dir / "cb_bler_vs_snr.png")
        plot_throughput(summaries, out_dir / "goodput_se_vs_snr.png")
        plot_bler(summaries, out_dir / "bler_vs_snr.png")
        plot_throughput(summaries, out_dir / "throughput_vs_snr.png")
        return out_dir

    @staticmethod
    def _payload_bits_per_cb(tb_info) -> List[int]:
        n = max(int(tb_info.n_cbs), 1)
        base = int(tb_info.tb_size) // n
        rem = int(tb_info.tb_size) % n
        return [base + (1 if i < rem else 0) for i in range(n)]

    def _build_trial_plan(self, scheme, snr_db: float, layer_se_eff=None, layer_sinrs_db=None):
        if layer_sinrs_db is None:
            layer_sinrs_db = np.asarray([snr_db] * int(scheme.rank), dtype=float)
        tx_cfg = scheme.configure_transmission(
            layer_sinrs_db=np.asarray(layer_sinrs_db, dtype=float),
            layer_se_eff=None if layer_se_eff is None else np.asarray(layer_se_eff, dtype=float),
            mcs_table=self.mcs_table,
            sim_cfg=self.cfg.simulation,
            adaptive_cfg=getattr(self.cfg, "adaptive_mcs", None),
        )
        tb_infos = self.tb_manager.compute_for_transmission(tx_cfg)
        tb_by_cw = {int(tb.cw_index): tb for tb in tb_infos}
        cb_payload_lens_by_cw: Dict[int, List[int]] = {}
        cb_params: Dict[int, List[Tuple[int, int, int]]] = {}
        cw_total_bits: Dict[int, int] = {}
        for tb in tb_infos:
            cw_idx = int(tb.cw_index)
            payload_lens = [int(x) for x in self._payload_bits_per_cb(tb)]
            cb_payload_lens_by_cw[cw_idx] = payload_lens
            cb_params[cw_idx] = []
            for cb, payload_len in zip(tb.cb_infos, payload_lens):
                cb_params[cw_idx].append((int(cb.cb_index), int(payload_len), int(cb.E)))
            cw_total_bits[cw_idx] = int(tb.total_E)
        plan = build_grid_mapping_plan(tx_cfg, self.cfg.resource, cw_total_bits=cw_total_bits)
        shape_key = (_cb_params_signature(cb_params), _plan_signature(plan))
        return tx_cfg, tb_infos, tb_by_cw, cb_payload_lens_by_cw, cb_params, plan, shape_key

    def _init_stats_for_tx(self, tx_cfg, tb_by_cw, stats_by_cw):
        for cw_cfg in tx_cfg.cw_configs:
            cw_idx = int(cw_cfg.cw_index)
            if cw_idx not in stats_by_cw:
                tb = tb_by_cw[cw_idx]
                stats_by_cw[cw_idx] = CWSimulationStats(
                    cw_index=cw_idx, tb_size=int(tb.tb_size), n_cbs=int(tb.n_cbs)
                )

    def _run_group(self, group_items, snr_db: float, stats_by_cw: Dict[int, CWSimulationStats]):
        # group_items: list of dict with same tx/plan/cb shape
        first = group_items[0]
        cb_params = first["cb_params"]
        plan = first["plan"]
        tx_cfg = first["tx_cfg"]
        tb_by_cw = first["tb_by_cw"]
        self._init_stats_for_tx(tx_cfg, tb_by_cw, stats_by_cw)
        chain = self._get_chain(cb_params=cb_params, plan=plan)
        bsz = len(group_items)
        measure_mask = np.asarray([bool(it.get("measure", True)) for it in group_items], dtype=bool)
        payload_by_cw: Dict[int, List[np.ndarray]] = {}
        for cw_idx, payload_lens in first["cb_payload_lens_by_cw"].items():
            payload_by_cw[cw_idx] = [_rng_bits(self.rng, (bsz, int(k))) for k in payload_lens]
        layer_gains = np.stack([it.get("layer_gains") for it in group_items], axis=0) if first.get("layer_gains") is not None else None
        trial_ids = [int(it.get("trial_id", i)) for i, it in enumerate(group_items)]
        noise = self._make_common_noise(
            snr_db=snr_db,
            trial_ids=trial_ids,
            n_re=int(plan.n_re_per_layer),
            rank=int(plan.rank),
        )
        decoded_by_cw = chain.run_batch(payload_by_cw, snr_db=snr_db, layer_gains=layer_gains, noise=noise)
        scheme_error_vec = np.zeros(bsz, dtype=bool)
        total_success_bits = 0.0
        for cw_cfg in tx_cfg.cw_configs:
            cw_idx = int(cw_cfg.cw_index)
            stat = stats_by_cw[cw_idx]
            cw_error_vec = np.zeros(bsz, dtype=bool)
            cb_error_count_vec = np.zeros(bsz, dtype=np.int64)
            success_bits_vec_total = np.zeros(bsz, dtype=np.int64)
            cb_error_count_this_cw = 0
            success_bits_this_cw = 0.0
            for cb_idx, payload in enumerate(payload_by_cw[cw_idx]):
                decoded = decoded_by_cw[cw_idx][cb_idx]
                cb_error_vec = np.any(decoded != payload, axis=1)
                cb_payload_bits = int(first["cb_payload_lens_by_cw"][cw_idx][cb_idx])
                success_bits_vec = (~cb_error_vec).astype(np.int64) * cb_payload_bits
                cb_error_count_vec += cb_error_vec.astype(np.int64)
                success_bits_vec_total += success_bits_vec
                cb_error_count_this_cw += int(np.sum(cb_error_vec))
                success_bits_this_cw += float(np.sum(success_bits_vec))
                cw_error_vec |= cb_error_vec
            meas_n = int(np.sum(measure_mask))
            if meas_n > 0:
                stat.cb_trials += meas_n * int(stat.n_cbs)
                stat.cb_errors += int(np.sum(cb_error_count_vec[measure_mask]))
                stat.trials += meas_n
                stat.errors += int(np.sum(cw_error_vec[measure_mask]))
                stat.successful_payload_bits += float(np.sum(success_bits_vec_total[measure_mask]))
                total_success_bits += float(np.sum(success_bits_vec_total[measure_mask]))
                scheme_error_vec[measure_mask] |= cw_error_vec[measure_mask]

            # v2.9: complete per-trial/CW records with decoding outcomes.
            for i, it in enumerate(group_items):
                templates = it.get("cw_record_templates", {})
                if cw_idx not in templates:
                    continue
                rec = dict(templates[cw_idx])
                n_cbs = max(int(rec.get("n_cbs", stat.n_cbs)), 1)
                n_re = max(int(rec.get("n_re_per_layer", plan.n_re_per_layer)), 1)
                succ_bits = int(success_bits_vec_total[i])
                rec.update({
                    "cb_error_count": int(cb_error_count_vec[i]),
                    "cb_success_count": int(n_cbs - int(cb_error_count_vec[i])),
                    "cw_failed": int(bool(cw_error_vec[i])),
                    "successful_payload_bits": int(succ_bits),
                    "goodput_se_tf_trial": float(succ_bits) / float(n_re),
                    "coding_failure_loss_se": float(rec.get("loaded_payload_se_tf", 0.0)) - float(succ_bits) / float(n_re),
                    "coding_failure_loss_se_clipped": max(float(rec.get("loaded_payload_se_tf", 0.0)) - float(succ_bits) / float(n_re), 0.0),
                    "cb_error_rate_trial": float(cb_error_count_vec[i]) / float(n_cbs),
                })
                if bool(it.get("olla_enabled", False)):
                    e_t = float(cb_error_count_vec[i]) / float(n_cbs)
                    allow_update = bool(it.get("olla_update", True))
                    offsets = it.get("olla_offsets_by_cw", None)
                    ewma = it.get("olla_ewma_by_cw", None)
                    if offsets is not None and ewma is not None:
                        scheme_name_for_olla = str(getattr(tx_cfg, "scheme_name", ""))
                        old_off, new_off, ewma_val, step_used_db = self._olla_update_offset(
                            offsets, ewma, int(cw_idx), e_t, allow_update=allow_update,
                            scheme_name=scheme_name_for_olla
                        )
                        rec.update({
                            "olla_offset_db_before": float(old_off),
                            "olla_offset_db_after": float(new_off),
                            "olla_offset_update_db": float(new_off - old_off),
                            "olla_target_cb_bler": float(getattr(self._olla_cfg(), "target_cb_bler", 0.1)),
                            "olla_step_db": float(step_used_db),
                            "olla_step_source": str(scheme_name_for_olla),
                            "olla_cb_error_ewma": float(ewma_val),
                            "olla_update_applied": int(bool(allow_update)),
                        })
                self.cw_trial_records.append(rec)
        return int(np.sum(scheme_error_vec)), float(total_success_bits)

    def _run_one_scheme_one_snr(self, scheme, snr_db: float) -> SNRSummary:
        sim = self.cfg.simulation
        adaptive = bool(getattr(self.cfg.adaptive_mcs, "enabled", False)) and sim.fixed_mcs is None
        is_cdl = str(self.cfg.channel.model).lower() in ("cdl", "sionna_cdl", "cdl_svd")
        olla_enabled = self._olla_enabled() and adaptive
        olla_cfg = self._olla_cfg()
        if olla_enabled:
            warmup_trials = int(getattr(olla_cfg, "warmup_trials_per_snr", 0))
            measurement_trials = int(getattr(olla_cfg, "measure_trials_per_snr", sim.n_trials_per_snr))
            total_loop_trials = int(warmup_trials + measurement_trials)
        else:
            warmup_trials = 0
            measurement_trials = int(sim.n_trials_per_snr)
            total_loop_trials = int(sim.n_trials_per_snr)
        total_trials = int(measurement_trials)
        batch_size = max(int(sim.batch_size), 1)
        if olla_enabled and bool(getattr(olla_cfg, "force_sequential_updates", True)):
            # True per-trial OLLA: the offset updated after trial t is used by trial t+1.
            batch_size = 1
        stats_by_cw: Dict[int, CWSimulationStats] = {}
        scheme_errors = 0
        total_success_bits = 0.0
        n_re_per_layer = 0
        metadata: Dict[str, object] = {
            "backend_note": "sionna_ldpc_bit_level_v3_0_rx_ce_actual_post_sinr",
            "channel_model": str(self.cfg.channel.model),
            "mcs_table": self.cfg.simulation.mcs_table,
            "fixed_mcs": -1 if self.cfg.simulation.fixed_mcs is None else int(self.cfg.simulation.fixed_mcs),
            "batch_size": int(self.cfg.simulation.batch_size),
            "goodput_accounting": "cb_level_successful_payload_bits",
            "cw_bler_definition": "any_cb_failed_in_cw",
            "scheme_bler_definition": "any_cb_failed_in_any_cw",
            "scheme_id": int(scheme.scheme_id),
            "rank": int(scheme.rank),
            "adaptive_mcs_enabled": bool(adaptive),
            "adaptive_mcs_granularity": str(getattr(self.cfg.adaptive_mcs, "decision_granularity", "fixed")),
            "adaptive_mcs_method": str(getattr(self.cfg.adaptive_mcs, "method", "capacity_average")),
            "gamma_total_db": float(getattr(self.cfg.adaptive_mcs, "shannon_gap_db", self.cfg.simulation.shannon_gap_db)) + float(getattr(self.cfg.adaptive_mcs, "mcs_margin_db", 0.0)),
            "min_code_rate": float(getattr(self.cfg.adaptive_mcs, "min_code_rate", 0.0)),
            "layer_order_rule": "descending_post_sinr_svd_singular_values",
            "common_channel_across_schemes": bool(is_cdl),
            "common_channel_mode": "batch_streaming_deterministic" if is_cdl else "none",
            "common_noise_across_schemes": True,
            "olla_enabled": bool(olla_enabled),
            "olla_target_cb_bler": float(getattr(olla_cfg, "target_cb_bler", 0.0)) if olla_enabled else "",
            "olla_step_db": float(getattr(olla_cfg, "step_db", 0.0)) if olla_enabled else "",
            "olla_scheme_step_db": dict(getattr(olla_cfg, "scheme_step_db", {}) or {}) if olla_enabled else {},
            "olla_warmup_trials_per_snr": int(warmup_trials),
            "olla_measure_trials_per_snr": int(measurement_trials),
            "olla_force_sequential_updates": bool(getattr(olla_cfg, "force_sequential_updates", True)) if olla_enabled else False,
        }
        mcs_hist: Dict[str, int] = {}
        olla_offsets_by_cw: Dict[int, float] = {}
        olla_ewma_by_cw: Dict[int, float] = {}
        qm_hist: Dict[str, int] = {}
        layer_se_records = []
        layer_sinr_records = []
        example_tx_cfg = None
        example_tb_infos = None
        example_plan = None

        done = 0
        while done < total_loop_trials:
            this_batch = min(batch_size, total_loop_trials - done)
            group_map = {}
            if is_cdl:
                # Batch-level deterministic common channel.
                # For the same (snr_db, batch_start), every scheme regenerates
                # exactly the same CDL/SVD samples from deterministic seeds.
                # This gives trial-level fairness without keeping a full-SNR
                # channel tensor in memory.
                batch_quality = self._sample_common_cdl_batch(
                    snr_db=snr_db,
                    batch_start=done,
                    batch_size=this_batch,
                    rank=int(scheme.rank),
                )
                svals_batch = batch_quality["svals"]
                layer_se_batch = batch_quality["layer_se_eff"]
                layer_sinr_batch = batch_quality["layer_sinr_eff_db"]
                rxq = batch_quality.get("rx_ce_quality", None)
                use_rx_ce_for_decoding = bool(
                    rxq is not None
                    and getattr(self.cfg.channel_estimation, "use_actual_rx_post_sinr_for_decoding", True)
                )
                decode_gains_batch = rxq.get("decode_layer_gains") if use_rx_ce_for_decoding else None
                if rxq is not None:
                    metadata.update({
                        "rx_ce_enabled": True,
                        "rx_ce_mode": str(rxq.get("mode", "")),
                        "rx_ce_equalizer": str(rxq.get("equalizer", "")),
                        "rx_ce_nmse_db_config": float(rxq.get("nmse_db_config", float("nan"))),
                        "rx_ce_apply_to_decoding": bool(use_rx_ce_for_decoding),
                        "rx_ce_apply_to_mcs_selection": bool(getattr(self.cfg.channel_estimation, "apply_to_mcs_selection", False)),
                    })
                else:
                    metadata.update({"rx_ce_enabled": False})
                if not metadata.get("cdl_report_recorded"):
                    metadata.update({"cdl_report": batch_quality.get("cdl_report", {}), "cdl_report_recorded": True})

                # Record CDF samples only once per SNR/batch, not once per scheme.
                rec_key = (float(snr_db), int(done))
                if rec_key not in self._recorded_layer_batches:
                    self._record_layer_quality_batch(snr_db=snr_db, batch_start=done, batch_quality=batch_quality)
                    self._recorded_layer_batches.add(rec_key)

                for b in range(this_batch):
                    trial_id = done + b
                    measure = bool(int(trial_id) >= int(warmup_trials))
                    olla_phase = "measure" if measure else "warmup"
                    layer_se_eff_b = layer_se_batch[b]
                    layer_sinr_eff_db_b = layer_sinr_batch[b]
                    layer_se_records.append(layer_se_eff_b.tolist())
                    layer_sinr_records.append(layer_sinr_eff_db_b.tolist())
                    if olla_enabled:
                        layer_olla_offsets_db_b = self._olla_layer_offsets_for_scheme(scheme, olla_offsets_by_cw)
                        mcs_selection_layer_se_b = self._layer_se_with_olla_offsets(layer_sinr_eff_db_b, layer_olla_offsets_db_b)
                    else:
                        layer_olla_offsets_db_b = np.zeros(int(scheme.rank), dtype=np.float64)
                        mcs_selection_layer_se_b = layer_se_eff_b
                    tx_cfg, tb_infos, tb_by_cw, payload_lens, cb_params, plan, shape_key = self._build_trial_plan(
                        scheme, snr_db, layer_se_eff=mcs_selection_layer_se_b, layer_sinrs_db=layer_sinr_eff_db_b
                    ) if adaptive else self._build_trial_plan(scheme, snr_db)
                    if use_rx_ce_for_decoding and decode_gains_batch is not None:
                        gains = np.asarray(decode_gains_batch[b], dtype=np.float32)
                    else:
                        gains = np.reshape(svals_batch[b], (-1, int(scheme.rank))).astype(np.float32)

                    if rxq is not None:
                        actual_layer_se_b = rxq["actual_layer_se_eff"][b]
                        actual_layer_sinr_b = rxq["actual_layer_sinr_eff_db"][b]
                        ue_est_layer_sinr_b = rxq["ue_est_layer_sinr_eff_db"][b]
                        post_sinr_loss_b = rxq["post_sinr_loss_db"][b]
                        rx_nmse_b = rxq["nmse_db_measured_by_layer"][b]
                        rx_mode = str(rxq.get("mode", ""))
                        rx_equalizer = str(rxq.get("equalizer", ""))
                    else:
                        actual_layer_se_b = None
                        actual_layer_sinr_b = None
                        ue_est_layer_sinr_b = None
                        post_sinr_loss_b = None
                        rx_nmse_b = None
                        rx_mode = ""
                        rx_equalizer = ""

                    cw_record_templates = self._build_cw_trial_record_templates(
                        scheme=scheme,
                        snr_db=snr_db,
                        trial_id=int(trial_id),
                        tx_cfg=tx_cfg,
                        tb_by_cw=tb_by_cw,
                        layer_se_eff=layer_se_eff_b,
                        layer_sinrs_db=layer_sinr_eff_db_b,
                        mcs_selection_layer_se_eff=mcs_selection_layer_se_b,
                        olla_layer_offsets_db=layer_olla_offsets_db_b,
                        actual_layer_se_eff=actual_layer_se_b,
                        actual_layer_sinrs_db=actual_layer_sinr_b,
                        ue_est_layer_sinrs_db=ue_est_layer_sinr_b,
                        post_sinr_loss_db=post_sinr_loss_b,
                        rx_ce_nmse_db=rx_nmse_b,
                        rx_ce_mode=rx_mode,
                        rx_ce_equalizer=rx_equalizer,
                        olla_enabled=bool(olla_enabled),
                        olla_phase=olla_phase,
                    )
                    item = {
                        "tx_cfg": tx_cfg,
                        "tb_infos": tb_infos,
                        "tb_by_cw": tb_by_cw,
                        "cb_payload_lens_by_cw": payload_lens,
                        "cb_params": cb_params,
                        "plan": plan,
                        "layer_gains": gains,
                        "trial_id": int(trial_id),
                        "measure": bool(measure),
                        "olla_enabled": bool(olla_enabled),
                        "olla_offsets_by_cw": olla_offsets_by_cw,
                        "olla_ewma_by_cw": olla_ewma_by_cw,
                        "olla_update": bool(olla_enabled and (not (bool(getattr(olla_cfg, "freeze_after_warmup", False)) and measure))),
                        "cw_record_templates": cw_record_templates,
                    }
                    group_map.setdefault(shape_key, []).append(item)
                    if example_tx_cfg is None:
                        example_tx_cfg, example_tb_infos, example_plan = tx_cfg, tb_infos, plan
            else:
                tx_cfg, tb_infos, tb_by_cw, payload_lens, cb_params, plan, shape_key = self._build_trial_plan(scheme, snr_db)
                base_item = {
                    "tx_cfg": tx_cfg,
                    "tb_infos": tb_infos,
                    "tb_by_cw": tb_by_cw,
                    "cb_payload_lens_by_cw": payload_lens,
                    "cb_params": cb_params,
                    "plan": plan,
                    "layer_gains": None,
                }
                group_map[shape_key] = []
                for i in range(this_batch):
                    trial_id = int(done + i)
                    templates = self._build_cw_trial_record_templates(
                        scheme=scheme,
                        snr_db=snr_db,
                        trial_id=trial_id,
                        tx_cfg=tx_cfg,
                        tb_by_cw=tb_by_cw,
                        layer_se_eff=None,
                        layer_sinrs_db=None,
                    )
                    group_map[shape_key].append(dict(base_item, trial_id=trial_id, cw_record_templates=templates))
                if example_tx_cfg is None:
                    example_tx_cfg, example_tb_infos, example_plan = tx_cfg, tb_infos, plan

            for items in group_map.values():
                ge, gs = self._run_group(items, snr_db, stats_by_cw)
                scheme_errors += ge
                total_success_bits += gs
                tx_cfg = items[0]["tx_cfg"]
                n_meas_items = sum(1 for it in items if bool(it.get("measure", True)))
                for cw in tx_cfg.cw_configs:
                    if cw.mcs_index is None:
                        key = f"cw{cw.cw_index}:mixed"
                    else:
                        key = f"cw{cw.cw_index}:mcs{cw.mcs_index}"
                    mcs_hist[key] = mcs_hist.get(key, 0) + n_meas_items
                    for layer, qm in cw.layer_modulations.items():
                        qkey = f"cw{cw.cw_index}:L{layer}:Qm{qm}"
                        qm_hist[qkey] = qm_hist.get(qkey, 0) + n_meas_items
            done += this_batch

        if example_tb_infos:
            n_re_per_layer = int(example_tb_infos[0].n_re_per_layer)
        metadata.update({
            "selected_mcs_hist": str(mcs_hist),
            "selected_qm_hist": str(qm_hist),
            "num_shape_groups_cached": int(len(self._chain_cache)),
            "n_re_per_layer": int(n_re_per_layer),
            "num_cws": int(len(example_tx_cfg.cw_configs)) if example_tx_cfg is not None else 0,
            "partition": "+".join(map(str, example_tx_cfg.partition)) if example_tx_cfg is not None and example_tx_cfg.partition else "",
        })
        if layer_se_records:
            arr = np.asarray(layer_se_records, dtype=float)
            metadata["layer_se_eff_mean"] = str(np.mean(arr, axis=0).tolist())
            metadata["layer_se_eff_p10"] = str(np.percentile(arr, 10, axis=0).tolist())
            metadata["layer_se_eff_p50"] = str(np.percentile(arr, 50, axis=0).tolist())
            metadata["layer_se_eff_p90"] = str(np.percentile(arr, 90, axis=0).tolist())
        if layer_sinr_records:
            arr = np.asarray(layer_sinr_records, dtype=float)
            metadata["layer_sinr_eff_db_mean"] = str(np.mean(arr, axis=0).tolist())

        if example_tx_cfg is not None and example_tb_infos is not None:
            tb_by_cw = {int(tb.cw_index): tb for tb in example_tb_infos}
            for cw_cfg in example_tx_cfg.cw_configs:
                cw_idx = int(cw_cfg.cw_index)
                tb = tb_by_cw[cw_idx]
                prefix = f"cw{cw_idx}"
                metadata.update({
                    f"{prefix}_layers": str(cw_cfg.layer_indices),
                    f"{prefix}_qms": str(cw_cfg.layer_modulations),
                    f"{prefix}_rate": float(cw_cfg.code_rate),
                    f"{prefix}_mcs": -1 if cw_cfg.mcs_index is None else int(cw_cfg.mcs_index),
                    f"{prefix}_n_cbs": int(tb.n_cbs),
                    f"{prefix}_n_bits_total": int(tb.n_bits_total),
                    f"{prefix}_actual_se": float(tb.actual_se),
                    f"{prefix}_cb_E": str([int(cb.E) for cb in tb.cb_infos]),
                    f"{prefix}_cb_K": str([int(cb.K) for cb in tb.cb_infos]),
                    f"{prefix}_cb_K_ldpc": str([int(cb.K_ldpc) for cb in tb.cb_infos]),
                    f"{prefix}_cb_N_null": str([int(cb.N_null) for cb in tb.cb_infos]),
                })
            metadata.update(plan_debug_dict(example_plan))

        cw_stats = [stats_by_cw[k] for k in sorted(stats_by_cw.keys())]
        return SNRSummary(
            scheme_label=example_tx_cfg.scheme_name if example_tx_cfg is not None else scheme.label(),
            snr_db=snr_db,
            trials=total_trials,
            scheme_errors=scheme_errors,
            cw_stats=cw_stats,
            total_throughput_bits_per_slot=total_success_bits / total_trials if total_trials else 0.0,
            metadata=metadata,
            n_re_per_layer=n_re_per_layer,
            rank=int(scheme.rank),
        )
