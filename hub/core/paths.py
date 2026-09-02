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


def code_version() -> dict:
    """
    지금 돌고 있는 코드가 어느 시점 것인지.

    ⚠️ 받은 폴더만 보고는 알 수 없다. GitHub ZIP 은 파일 날짜를 내려받은
       시각이 아니라 커밋 시각으로 넣어서 눈으로도 못 가린다.
       2026-09-02 아침에 8/31 11:19 버전으로 마감을 돌았는데, 로그만 봐서는
       아무도 몰랐다 -- 그날 고친 것 5개가 전부 빠진 채였다.

    묶음을 만들 때 make_share 가 심어 둔 hub/data/version.json 을 읽는다.
    개발 폴더에는 그 파일이 없으므로 git 에 직접 물어본다.
    """
    f = DATA_DIR / "version.json"
    if f.is_file():
        try:
            import json
            v = json.loads(f.read_text(encoding="utf-8"))
            if v.get("commit"):
                return v
        except Exception:
            pass
    try:
        import subprocess
        out = subprocess.run(
            ["git", "log", "-1", "--format=%h|%cd|%s", "--date=format:%Y-%m-%d %H:%M"],
            cwd=str(SYSTEM_DIR), text=True, capture_output=True, timeout=10).stdout.strip()
        if out and "|" in out:
            h, at, sub = (out.split("|", 2) + ["", ""])[:3]
            return {"commit": h, "at": at, "subject": sub[:70], "built": "(개발 폴더)"}
    except Exception:
        pass
    return {"commit": "?", "at": "?", "subject": "", "built": ""}


def version_line() -> str:
    v = code_version()
    tail = f" · 묶은 날 {v['built']}" if v.get("built") else ""
    return f"코드 {v.get('commit', '?')} ({v.get('at', '?')}){tail}"


def describe() -> dict:
    ko = klook_open_dir()
    oc = ota_close_dir()
    return {
        "hub": str(HUB_DIR),
        "klook_open": str(ko) if ko else None,
        "ota_close": str(oc) if oc else None,
    }
