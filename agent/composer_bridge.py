#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""agent/composer_bridge.py — 노드가 조립기를 고르는 한 곳

노드가 조립기 종류를 직접 알면, 프리즘을 붙일 때 노드를 고쳐야 한다. 선택은 여기 한 곳이다.
상태에 `composer` 가 있으면 그것을 쓰고, 없으면 `ORCH_COMPOSER`(기본 skeleton)를 쓴다.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import composer as CP  # noqa: E402

_CACHE: dict = {}


def composer_for(state: dict):
    spec = state.get("composer")
    if hasattr(spec, "compose"):
        return spec
    kind = spec if isinstance(spec, str) else None
    fault = state.get("composer_fault")
    key = (kind, fault)
    if key not in _CACHE:
        _CACHE[key] = (CP.get_composer(kind, fault=fault) if (kind or "skeleton") == "skeleton"
                       else CP.get_composer(kind))
    return _CACHE[key]


def clear_cache():
    _CACHE.clear()
