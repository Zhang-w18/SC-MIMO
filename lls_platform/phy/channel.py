from __future__ import annotations

import numpy as np


def make_rng(seed=None):
    """创建随机数生成器，兼容新旧 NumPy。

    你的环境是 NumPy 1.15.1，不支持 np.random.default_rng()。
    因此这里优先使用新版 default_rng；如果没有，就退回旧版 RandomState。
    """
    if hasattr(np.random, "default_rng"):
        return np.random.default_rng(seed)
    return np.random.RandomState(seed)



def rng_uniform01(rng, size=None):
    """生成 [0,1) 均匀随机数，兼容新旧 NumPy。

    NumPy >= 1.17 的 Generator 支持 rng.random(size=...)；
    NumPy 1.15 的 RandomState 在部分环境里没有 rng.random()，
    但支持 rng.random_sample(size=...)。
    """
    if hasattr(rng, "random"):
        return rng.random(size=size)
    return rng.random_sample(size=size)

class RayleighSVDChannel:
    """第一版 link_abstraction backend 的 Rayleigh MIMO 信道。

    作用：
    - 生成 batch 个 trial、每个 trial 多个 RE 样本的 MIMO 信道矩阵；
    - 计算每个 RE 的奇异值；
    - 后续用奇异值计算 post-SINR。

    注意：
    这个 backend 用于验证 Mapping/MCS/TBS/统计闭环。
    论文级链路曲线应替换为 Sionna CDL + LDPC bit-level backend。
    """

    def __init__(
        self,
        n_rx: int,
        n_tx: int,
        n_re_samples: int = 24,
        normalize: bool = True,
        rng=None,
    ):
        self.n_rx = n_rx
        self.n_tx = n_tx
        self.n_re_samples = n_re_samples
        self.normalize = normalize

        # 兼容旧 NumPy：不要直接写 np.random.default_rng()。
        # 如果外部传入 rng，则复用；否则创建一个不固定 seed 的 rng。
        self.rng = rng if rng is not None else make_rng()

    def sample_singular_values(self, batch_size: int, rank: int) -> np.ndarray:
        """返回 shape [batch, n_re_samples, rank] 的前 rank 个奇异值。"""
        h = (
            self.rng.normal(size=(batch_size, self.n_re_samples, self.n_rx, self.n_tx))
            + 1j * self.rng.normal(size=(batch_size, self.n_re_samples, self.n_rx, self.n_tx))
        ) / np.sqrt(2.0)

        # 常见归一化：每个发射天线到每个接收天线平均功率约为 1。
        # 如果希望去掉天线阵列增益，可进一步按 sqrt(n_tx) 归一化。
        if self.normalize:
            h = h / np.sqrt(self.n_tx)

        # NumPy 1.15 已经支持对批量矩阵做 SVD。
        s = np.linalg.svd(h, compute_uv=False)
        return s[..., :rank]
