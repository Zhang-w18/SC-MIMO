from __future__ import annotations

from typing import Dict, Type
from lls_platform.core.mapping_scheme import MappingScheme

_SCHEME_REGISTRY: Dict[str, Type[MappingScheme]] = {}


def register_scheme(name: str):
    """方案注册装饰器。"""
    def deco(cls):
        _SCHEME_REGISTRY[name] = cls
        return cls
    return deco


def create_scheme(name: str, **kwargs) -> MappingScheme:
    if name not in _SCHEME_REGISTRY:
        raise KeyError(f"未知 mapping scheme: {name}. 已注册: {list(_SCHEME_REGISTRY)}")
    return _SCHEME_REGISTRY[name](**kwargs)


def list_schemes():
    return list(_SCHEME_REGISTRY.keys())
