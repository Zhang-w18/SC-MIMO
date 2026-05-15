from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List
import numpy as np

from lls_platform.core.config import MappingConfig, SimulationConfig
from lls_platform.core.data_structures import MCSEntry, TransmissionConfig


class MappingScheme(ABC):
    """映射方案抽象基类。"""

    @abstractmethod
    def expand_candidates(self, rank: int) -> List["MappingScheme"]:
        """Scheme 7 会展开成多个候选；其他 scheme 返回 [self]。"""
        raise NotImplementedError

    @abstractmethod
    def configure_transmission(
        self,
        layer_sinrs_db: np.ndarray,
        mcs_table: List[MCSEntry],
        sim_cfg: SimulationConfig,
    ) -> TransmissionConfig:
        """根据 post-SINR 和配置生成 TransmissionConfig。"""
        raise NotImplementedError
