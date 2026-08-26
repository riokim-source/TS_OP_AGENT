# -*- coding: utf-8 -*-
"""
hub_bridge.py
OTA Close 의 봇들이 hub 의 Chrome 라우팅(단일 source of truth)을 쓰게 해주는 다리.

hub 가 없으면(구버전 단독 실행) 조용히 None 을 돌려주고, 호출 측은 기존 방식으로 돌아간다.
따라서 이 파일이 없어도, hub 폴더를 지워도 기존 운영 경로는 그대로 산다.
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent          # OTA Close/
_CACHE: dict = {}


def _hub_dir() -> Path | None:
    for parent in (_ROOT.parent, _ROOT.parent.parent, Path.home() / "Desktop"):
        cand = parent / "hub"
        if (cand / "core" / "routing.py").exists():
            return cand
    return None


def routing():
    """hub 의 Routing 인스턴스. 없으면 None."""
    if "r" in _CACHE:
        return _CACHE["r"]
    _CACHE["r"] = None
    hub = _hub_dir()
    if hub is not None:
        try:
            if str(hub) not in sys.path:
                sys.path.insert(0, str(hub))
            from core.routing import get_routing  # type: ignore
            _CACHE["r"] = get_routing()
        except Exception:
            _CACHE["r"] = None
    return _CACHE["r"]


def resolve(region: str, channel: str) -> dict | None:
    """
    (region, OTA) -> {profile, port, ok, message}

    ok=False 면 그 조합은 실행하면 안 된다 (미설정이거나 포트를 남이 점유 중).
    조용히 엉뚱한 Chrome 에 붙는 것보다 명시적으로 스킵하는 게 낫다.
    """
    r = routing()
    if r is None:
        return None
    try:
        key = r.route(region, channel)
        if key is None:
            return {"profile": None, "port": None, "ok": False,
                    "message": f"{region}/{channel} 은 Chrome 연결이 미설정입니다."}
        port = r.profile_port(key)
        if r.profile_owns_port(key):
            return {"profile": key, "port": port, "ok": True, "message": ""}
        if r.port_conflict(key):
            return {"profile": key, "port": port, "ok": False,
                    "message": (f"port {port} 를 다른 프로필의 Chrome 이 점유 중입니다 "
                                f"(기대: {r.profile_dir(key)}).")}
        res = r.ensure(key)
        ok = bool(res.get("ok")) and bool(res.get("ready", r.profile_owns_port(key)))
        return {"profile": key, "port": port, "ok": ok, "message": res.get("message", "")}
    except Exception as e:
        return {"profile": None, "port": None, "ok": False, "message": f"routing 실패: {e}"}
