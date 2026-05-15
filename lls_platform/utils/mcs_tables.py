from __future__ import annotations

"""MCS 表定义。

本文件从 v2.1 开始支持 3GPP NR PDSCH 标准 MCS 表：
- nr_64qam  : 3GPP TS 38.214 Table 5.1.3.1-1，最高 64QAM
- nr_256qam : 3GPP TS 38.214 Table 5.1.3.1-2，最高 256QAM

同时保留 approx_256qam，供旧的 link_abstraction 快速调试使用。

注意：
3GPP MCS 表只定义 MCS index、Qm、目标码率 R×1024 和谱效，
并不定义 required_sinr_db。required_sinr_db 只用于 link_abstraction
抽象 BLER 模型。对标准表，本文件用 Shannon-gap 近似生成一个门限，
以便旧抽象后端仍可运行；真实 bit-level 后端不会使用该字段。
"""

import math
from typing import Iterable, List, Sequence, Tuple, Union

from lls_platform.core.data_structures import MCSEntry


RateValue = Union[int, float]
RawMCS = Tuple[int, str, int, RateValue]


def _required_sinr_from_se(se: float, gap_db: float = 2.5) -> float:
    """由谱效生成 link_abstraction 所需的近似 required_sinr_db。

    这个值不是 3GPP 标准表的一部分，只是为了让抽象后端有一个可用门限：
        SE = log2(1 + SINR / Gamma)
        SINR = Gamma * (2^SE - 1)
    """
    gap_linear = 10.0 ** (gap_db / 10.0)
    sinr_linear = gap_linear * (2.0 ** float(se) - 1.0)
    if sinr_linear <= 0:
        return -99.0
    return 10.0 * math.log10(sinr_linear)


def _build_standard_table(raw: Sequence[RawMCS], gap_db: float = 2.5) -> List[MCSEntry]:
    """把标准表中的 R×1024 转成 MCSEntry。"""
    out: List[MCSEntry] = []
    for index, mod, qm, r_x1024 in raw:
        code_rate = float(r_x1024) / 1024.0
        se = qm * code_rate
        out.append(MCSEntry(
            index=int(index),
            modulation=str(mod),
            Qm=int(qm),
            code_rate=code_rate,
            required_sinr_db=_required_sinr_from_se(se, gap_db=gap_db),
        ))
    return out


def _build_approx_table() -> List[MCSEntry]:
    """旧版近似表，保留用于 link_abstraction 对比和回归测试。"""
    raw = [
        (0, "QPSK",   2, 0.12, -5.5),
        (1, "QPSK",   2, 0.19, -3.5),
        (2, "QPSK",   2, 0.30, -1.5),
        (3, "QPSK",   2, 0.44,  0.5),
        (4, "QPSK",   2, 0.59,  2.5),
        (5, "16QAM",  4, 0.37,  4.0),
        (6, "16QAM",  4, 0.42,  5.0),
        (7, "16QAM",  4, 0.48,  6.0),
        (8, "16QAM",  4, 0.54,  7.0),
        (9, "16QAM",  4, 0.60,  8.5),
        (10, "16QAM", 4, 0.64,  9.5),
        (11, "64QAM", 6, 0.46, 10.5),
        (12, "64QAM", 6, 0.50, 11.5),
        (13, "64QAM", 6, 0.55, 12.5),
        (14, "64QAM", 6, 0.60, 13.5),
        (15, "64QAM", 6, 0.65, 15.0),
        (16, "64QAM", 6, 0.72, 16.0),
        (17, "64QAM", 6, 0.75, 17.0),
        (18, "64QAM", 6, 0.82, 18.0),
        (19, "256QAM", 8, 0.67, 19.0),
        (20, "256QAM", 8, 0.70, 20.0),
        (21, "256QAM", 8, 0.75, 21.0),
        (22, "256QAM", 8, 0.80, 22.0),
        (23, "256QAM", 8, 0.85, 23.5),
        (24, "256QAM", 8, 0.89, 24.5),
        (25, "256QAM", 8, 0.93, 26.0),
        (26, "256QAM", 8, 0.95, 27.5),
        (27, "256QAM", 8, 0.95, 28.0),
    ]
    return [MCSEntry(*x) for x in raw]


# 3GPP TS 38.214 Table 5.1.3.1-1, PDSCH MCS table, 64QAM。
# 字段: (MCS index, modulation name, Qm, target code rate R×1024)
_NR_64QAM_RAW: Sequence[RawMCS] = [
    (0,  "QPSK",  2, 120),
    (1,  "QPSK",  2, 157),
    (2,  "QPSK",  2, 193),
    (3,  "QPSK",  2, 251),
    (4,  "QPSK",  2, 308),
    (5,  "QPSK",  2, 379),
    (6,  "QPSK",  2, 449),
    (7,  "QPSK",  2, 526),
    (8,  "QPSK",  2, 602),
    (9,  "QPSK",  2, 679),
    (10, "16QAM", 4, 340),
    (11, "16QAM", 4, 378),
    (12, "16QAM", 4, 434),
    (13, "16QAM", 4, 490),
    (14, "16QAM", 4, 553),
    (15, "16QAM", 4, 616),
    (16, "16QAM", 4, 658),
    (17, "64QAM", 6, 438),
    (18, "64QAM", 6, 466),
    (19, "64QAM", 6, 517),
    (20, "64QAM", 6, 567),
    (21, "64QAM", 6, 616),
    (22, "64QAM", 6, 666),
    (23, "64QAM", 6, 719),
    (24, "64QAM", 6, 772),
    (25, "64QAM", 6, 822),
    (26, "64QAM", 6, 873),
    (27, "64QAM", 6, 910),
    (28, "64QAM", 6, 948),
]


# 3GPP TS 38.214 Table 5.1.3.1-2, PDSCH MCS table, 256QAM。
# 字段: (MCS index, modulation name, Qm, target code rate R×1024)
# 注意：标准表中 index 20 和 26 的 R×1024 是半整数。
_NR_256QAM_RAW: Sequence[RawMCS] = [
    (0,  "QPSK",   2, 120),
    (1,  "QPSK",   2, 193),
    (2,  "QPSK",   2, 308),
    (3,  "QPSK",   2, 449),
    (4,  "QPSK",   2, 602),
    (5,  "16QAM",  4, 378),
    (6,  "16QAM",  4, 434),
    (7,  "16QAM",  4, 490),
    (8,  "16QAM",  4, 553),
    (9,  "16QAM",  4, 616),
    (10, "16QAM",  4, 658),
    (11, "64QAM",  6, 466),
    (12, "64QAM",  6, 517),
    (13, "64QAM",  6, 567),
    (14, "64QAM",  6, 616),
    (15, "64QAM",  6, 666),
    (16, "64QAM",  6, 719),
    (17, "64QAM",  6, 772),
    (18, "64QAM",  6, 822),
    (19, "64QAM",  6, 873),
    (20, "256QAM", 8, 682.5),
    (21, "256QAM", 8, 711),
    (22, "256QAM", 8, 754),
    (23, "256QAM", 8, 797),
    (24, "256QAM", 8, 841),
    (25, "256QAM", 8, 885),
    (26, "256QAM", 8, 916.5),
    (27, "256QAM", 8, 948),
]


def get_mcs_table(name: str = "nr_64qam") -> List[MCSEntry]:
    """返回 MCS 表。

    参数：
        name:
            - "nr_64qam" / "3gpp_64qam"：3GPP 64QAM 表
            - "nr_256qam" / "3gpp_256qam"：3GPP 256QAM 表
            - "approx_256qam"：旧版近似表，仅用于回归/抽象测试
    """
    key = (name or "nr_64qam").lower()
    if key in ("nr_64qam", "3gpp_64qam", "table1", "qam64"):
        return _build_standard_table(_NR_64QAM_RAW)
    if key in ("nr_256qam", "3gpp_256qam", "table2", "qam256"):
        return _build_standard_table(_NR_256QAM_RAW)
    if key in ("approx_256qam", "approx"):
        return _build_approx_table()
    raise ValueError(
        "暂不支持 MCS 表: {}。可选: nr_64qam, nr_256qam, approx_256qam".format(name)
    )


def get_mcs_by_index(index: int, table: List[MCSEntry]) -> MCSEntry:
    """按 MCS index 查表。"""
    for m in table:
        if m.index == index:
            return m
    valid = [m.index for m in table]
    raise ValueError("MCS index {} 不在表中；当前表有效 index 为 {}".format(index, valid))


# 便于测试和 README 展示。
def available_mcs_tables() -> List[str]:
    return ["nr_64qam", "nr_256qam", "approx_256qam"]
