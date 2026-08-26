# -*- coding: utf-8 -*-
"""
paths.py
hub 가 의존하는 기존 봇 폴더(Klook Open / OTA Close)의 위치를 한 곳에서 결정한다.

⚠️ 기존 klook.py 의 _find_klook_open_dir() 는 ~/Desktop 을 ROOT.parent 보다 먼저
   뒤져서, "Last minute system" 안으로 옮긴 뒤에도 바깥의 옛 폴더를 import 했다.
   여기서는 "내 부모 폴더 우선" 으로 순서를 뒤집는다.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

HUB_DIR = Path(__file__).resolve().parent.parent
SYSTEM_DIR = HUB_DIR.parent            # "Last minute system"
DATA_DIR = HUB_DIR / "data"
LOG_DIR = HUB_DIR / "logs"
UI_DIR = HUB_DIR / "ui"

DATA_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

_KLOOK_OPEN_NAMES = ["Klook Open", "Klook open", "klook open", "KLOOK Open", "klook_open"]
_OTA_CLOSE_NAMES = ["OTA Close", "OTA close", "ota close", "OTA_Close", "ota_close"]


def _search(names: list[str], marker: str, env_var: str) -> Path | None:
    env = os.environ.get(env_var, "").strip()
    if env:
        p = Path(env)
        if p.is_dir() and (p / marker).exists():
            return p

    # 순서가 중요하다: 형제 폴더 → 상위 → 데스크톱 순.
    parents = [SYSTEM_DIR, SYSTEM_DIR.parent, Path.home() / "Desktop",
               Path.home() / "OneDrive" / "Desktop", Path.home()]
    seen: set[Path] = set()
    for parent in parents:
        try:
            parent = parent.resolve()
        except OSError:
            continue
        if parent in seen or not parent.is_dir():
            continue
        seen.add(parent)
        for name in names:
            cand = parent / name
            if cand.is_dir() and (cand / marker).exists():
                return cand
    return None


def klook_open_dir() -> Path | None:
    """Klook Open 봇 폴더 (packages.py / klook_worker.py 보유)."""
    return _search(_KLOOK_OPEN_NAMES, "packages.py", "KLOOK_OPEN_DIR")


def ota_close_dir() -> Path | None:
    """OTA Close 봇 폴더 (main.py / kkday.py 보유)."""
    return _search(_OTA_CLOSE_NAMES, "kkday.py", "OTA_CLOSE_DIR")


def ensure_on_syspath(directory: Path | None) -> bool:
    if directory is None:
        return False
    s = str(directory)
    if s not in sys.path:
        sys.path.insert(0, s)
    return True


def describe() -> dict:
    ko = klook_open_dir()
    oc = ota_close_dir()
    return {
        "hub": str(HUB_DIR),
        "klook_open": str(ko) if ko else None,
        "ota_close": str(oc) if oc else None,
    }
