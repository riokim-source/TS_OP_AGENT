# -*- coding: utf-8 -*-
"""
pickups.py
투어별 '실제로 파는 픽업지' 목록.

예약 파일만 보면 그날 예약이 들어온 픽업지만 후보로 나온다.
남이섬셔틀에 명동 예약만 있으면 명동 하나만 뜨는데, 정작 필요한 지시는
'홍대 제외' 라서 홍대가 후보에 없으면 지시를 만들 수가 없다.

그래서 GG availability 페이지에서 수집한 (투어 -> 픽업지) 카탈로그를 합친다.
GG 옵션이 픽업지별로 갈라져 있어서 '실제로 파는 픽업지' 의 사실상 유일한 출처다.
    남이섬셔틀 - Nami Island Roundtrip Transfer, Meet at Hongik Univ Station
                                                Meet at Myeongdong
                                                Meet at Dongdaemun

수집: hub UI 의 [픽업 목록 새로고침] 또는
      python "OTA Close/gg_open.py" --mode catalog --date YYYY-MM-DD --port 9522 \
             --output hub/data/gg_pickups.json
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

from .paths import DATA_DIR, ota_close_dir
from .routing import get_routing

CATALOG_PATH = DATA_DIR / "gg_pickups.json"

# GG 투어 코드 -> 예약 파일 투어명 (다른 것만)
TOUR_ALIASES: dict[str, str] = {
    "에버셔틀": "에버",
    "mbc": "MBC 스튜디오",
}


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", str(s or "").replace("\xa0", " ").strip()).casefold()


def load() -> dict:
    """{투어명(정규화): [픽업지...]}"""
    if not CATALOG_PATH.exists():
        return {}
    try:
        raw = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    out: dict[str, list[str]] = {}
    for tour, picks in (raw.get("catalog") or {}).items():
        keys = {_norm(tour)}
        alias = TOUR_ALIASES.get(_norm(tour))
        if alias:
            keys.add(_norm(alias))
        for k in keys:
            cur = out.setdefault(k, [])
            for p in picks:
                if p not in cur:
                    cur.append(p)
    return out


def info() -> dict:
    if not CATALOG_PATH.exists():
        return {"exists": False, "date": None, "tours": 0}
    try:
        raw = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
        return {"exists": True, "date": raw.get("date"),
                "tours": len(raw.get("catalog") or {})}
    except Exception:
        return {"exists": False, "date": None, "tours": 0}


def merge(tour: str, sheet_pickups: list[str], catalog: dict | None = None) -> list[str]:
    """
    화면 드롭다운에 띄울 픽업 후보 = 예약 파일에 나온 것 U GG 가 파는 것.

    예약이 없어도 '파는 픽업지' 는 고를 수 있어야 '홍대 제외' 같은 지시가 나온다.
    """
    cat = load() if catalog is None else catalog
    out = list(sheet_pickups or [])
    for p in cat.get(_norm(tour), []):
        if p not in out:
            out.append(p)
    return sorted(out)


def refresh(date_str: str, region: str = "KOREA") -> dict:
    """GG 에서 카탈로그를 다시 수집한다. Chrome 이 준비돼 있어야 한다."""
    root = ota_close_dir()
    if root is None or not (root / "gg_open.py").exists():
        return {"ok": False, "error": "gg_open.py 를 찾을 수 없습니다."}

    r = get_routing()
    key = r.route(region, "GG")
    if key is None:
        return {"ok": False, "error": f"{region}/GG Chrome 연결이 미설정입니다."}
    if r.port_conflict(key):
        return {"ok": False,
                "error": f"port {r.profile_port(key)} 를 다른 프로필의 Chrome 이 점유 중입니다."}
    if not r.profile_owns_port(key):
        boot = r.ensure(key, wait_seconds=25)
        if not (boot.get("ok") and boot.get("ready")):
            return {"ok": False, "error": boot.get("message", "Chrome 준비 실패")}

    try:
        chk = r.check_login(key, ["GG"], timeout=20)
        st = (chk.get("results") or [{}])[0].get("state")
        if st == "logged_out":
            return {"ok": False,
                    "error": "GG 로그인이 필요합니다. 해당 Chrome 창에서 로그인(2단계 인증 포함) 후 다시 시도하세요."}
    except Exception:
        pass

    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    cmd = [sys.executable, str(root / "gg_open.py"), "--mode", "catalog",
           "--date", date_str, "--port", str(r.profile_port(key)),
           "--output", str(CATALOG_PATH)]
    try:
        proc = subprocess.run(cmd, cwd=str(root), capture_output=True, text=True,
                              encoding="utf-8", errors="replace", env=env, timeout=600,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception as e:
        return {"ok": False, "error": f"실행 실패: {e}"}
    if proc.returncode != 0:
        tail = (proc.stdout or "")[-400:]
        return {"ok": False, "error": f"수집 실패 (rc={proc.returncode}) {tail}"}
    return {"ok": True, **info()}
