from __future__ import annotations

"""纯 NumPy QAM 调制/硬判决解调工具。

这是 bit-level v0 backend 使用的轻量调制器，不依赖 Sionna/TensorFlow。

约定：
- 支持 Qm = 2/4/6/8，即 QPSK/16QAM/64QAM/256QAM。
- 使用方形 QAM 星座，每个实轴/虚轴使用 Gray 标号。
- 星座归一化到平均符号能量 E[|x|^2] = 1。
- 解调为硬判决，适合第一版 bit-level 闭环验证。
"""

import math
from typing import Tuple
import numpy as np


_SUPPORTED_QM = (2, 4, 6, 8)


def _gray_to_binary_int(g: int) -> int:
    """Gray 码整数转普通二进制整数。"""
    b = int(g)
    while g > 0:
        g >>= 1
        b ^= g
    return b


def _int_to_bits(x: int, width: int) -> np.ndarray:
    """整数转固定宽度 bit 数组，高位在前。"""
    return np.asarray([(x >> (width - 1 - i)) & 1 for i in range(width)], dtype=np.int8)


def _bits_to_int(bits: np.ndarray) -> int:
    """bit 数组转整数，高位在前。"""
    out = 0
    for b in bits:
        out = (out << 1) | int(b)
    return int(out)


def qam_levels(qm: int) -> Tuple[np.ndarray, float]:
    """返回未归一化 PAM levels 和复 QAM 归一化因子。"""
    if qm not in _SUPPORTED_QM:
        raise ValueError("当前只支持 Qm=2/4/6/8，收到 Qm={}".format(qm))
    m_axis = 2 ** (qm // 2)
    levels = np.arange(-(m_axis - 1), m_axis, 2, dtype=float)
    # 方形 M-QAM 平均能量 = 2/3 * (M-1)，其中 M=2^Qm。
    avg_energy = (2.0 / 3.0) * (2 ** qm - 1)
    norm = math.sqrt(avg_energy)
    return levels, norm


def qam_modulate(bits: np.ndarray, qm: int) -> np.ndarray:
    """QAM 调制。

    参数：
        bits: 一维 0/1 数组，长度必须能被 Qm 整除。
        qm: 每符号 bit 数。
    返回：
        复数 QAM 符号，一维 complex ndarray。
    """
    bits = np.asarray(bits, dtype=np.int8).reshape(-1)
    if len(bits) % qm != 0:
        raise ValueError("bits 长度 {} 不能被 Qm={} 整除".format(len(bits), qm))

    levels, norm = qam_levels(qm)
    axis_bits = qm // 2
    n_symbols = len(bits) // qm
    syms = np.empty(n_symbols, dtype=np.complex128)

    for i in range(n_symbols):
        chunk = bits[i * qm:(i + 1) * qm]
        i_gray = _bits_to_int(chunk[:axis_bits])
        q_gray = _bits_to_int(chunk[axis_bits:])
        i_idx = _gray_to_binary_int(i_gray)
        q_idx = _gray_to_binary_int(q_gray)
        syms[i] = (levels[i_idx] + 1j * levels[q_idx]) / norm
    return syms


def qam_demodulate_hard(symbols: np.ndarray, qm: int) -> np.ndarray:
    """QAM 硬判决解调，输出 0/1 bit。"""
    symbols = np.asarray(symbols, dtype=np.complex128).reshape(-1)
    levels, norm = qam_levels(qm)
    axis_bits = qm // 2

    # 反归一化后做最近邻判决。
    real_vals = np.real(symbols) * norm
    imag_vals = np.imag(symbols) * norm

    out = []
    for r, im in zip(real_vals, imag_vals):
        i_idx = int(np.argmin(np.abs(levels - r)))
        q_idx = int(np.argmin(np.abs(levels - im)))

        # 调制时 bits 表示 Gray 码；解调时最近邻 index -> Gray 码 bit。
        i_gray = i_idx ^ (i_idx >> 1)
        q_gray = q_idx ^ (q_idx >> 1)
        out.extend(_int_to_bits(i_gray, axis_bits).tolist())
        out.extend(_int_to_bits(q_gray, axis_bits).tolist())
    return np.asarray(out, dtype=np.int8)


def add_awgn(symbols: np.ndarray, snr_db: float, rng) -> np.ndarray:
    """给单位平均功率符号加入复 AWGN。"""
    snr_linear = 10 ** (snr_db / 10.0)
    n0 = 1.0 / snr_linear
    noise = math.sqrt(n0 / 2.0) * (
        rng.normal(size=symbols.shape) + 1j * rng.normal(size=symbols.shape)
    )
    return symbols + noise


def add_flat_rayleigh(symbols: np.ndarray, snr_db: float, rng) -> np.ndarray:
    """通过单抽头 Rayleigh 信道，并用理想 CSI 做一拍均衡。

    这是 rank=1 bit-level v0 的简化信道：
        y = h x + n
        x_hat = y / h
    """
    snr_linear = 10 ** (snr_db / 10.0)
    n0 = 1.0 / snr_linear
    h = (rng.normal() + 1j * rng.normal()) / math.sqrt(2.0)
    if abs(h) < 1e-12:
        h = 1.0 + 0.0j
    noise = math.sqrt(n0 / 2.0) * (
        rng.normal(size=symbols.shape) + 1j * rng.normal(size=symbols.shape)
    )
    y = h * symbols + noise
    return y / h
