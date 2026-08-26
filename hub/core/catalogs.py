# -*- coding: utf-8 -*-
"""
catalogs.py
OTA 별 '우리가 실제로 파는 상품 목록' 을 모은다.

라스트미닛 메모는 내부 투어명("Toyako Niseko")으로 나오는데, 각 OTA 는 자기 상품
식별자와 자기 상품명을 쓴다. 그 사이를 잇는 표(productmap)를 만들려면 먼저
"각 OTA 에 뭐가 있는지" 를 알아야 한다. 이 파일이 그 목록을 공급한다.

출처:
  MRT / VI / KK : OTA Close 가 매일 남기는 logs/discover/*.json 재사용 (추가 크롤링 없음)
  GG            : 카탈로그가 없어서 availability 페이지에서 직접 수집해야 한다 (미구현)
  KLOOK         : packages.py 가 이미 완전한 매핑이라 여기서는 다루지 않는다
"""
from __future__ import annotations

import glob
import json
import os
import re
from pathlib import Path

from .paths import ota_close_dir


def _discover_dir() -> Path | None:
    root = ota_close_dir()
    if root is None:
        return None
    d = root / "logs" / "discover"
    return d if d.is_dir() else None


def _latest_nonempty(pattern: str):
    """가장 최근의 '비어있지 않은' discover 파일을 고른다.
    (마감이 일찍 끝나 0건으로 저장된 날이 섞여 있어서 최신 파일이 항상 유효하진 않다)"""
    d = _discover_dir()
    if d is None:
        return None, None
    files = sorted(glob.glob(str(d / pattern)), key=os.path.getmtime, reverse=True)
    for f in files:
        try:
            data = json.loads(Path(f).read_text(encoding="utf-8"))
        except Exception:
            continue
        payload = data.get("products") if isinstance(data, dict) else data
        if payload:
            return f, payload
    return None, None


def mrt_catalog() -> dict:
    f, rows = _latest_nonempty("mrt_*.json")
    items = [{
        "id": str(r.get("id", "")),
        "name": str(r.get("name", "")),
        "region": str(r.get("region", "")),
        "status": str(r.get("status", "")),
    } for r in (rows or []) if r.get("id")]
    return {"channel": "MRT", "source": os.path.basename(f) if f else None,
            "count": len(items), "items": items}


def vi_catalog() -> dict:
    f, rows = _latest_nonempty("vi_*.json")
    items = []
    for r in (rows or []):
        code = str(r.get("code", ""))
        label = str(r.get("label", ""))
        # 라벨 끝의 "(48881P233)" 은 코드 중복이라 이름에서 뗀다
        name = re.sub(r"\s*\(" + re.escape(code) + r"\)\s*$", "", label).strip() if code else label
        if code:
            items.append({"id": code, "name": name, "region": "", "status": ""})
    return {"channel": "VI", "source": os.path.basename(f) if f else None,
            "count": len(items), "items": items}


def kk_catalog() -> dict:
    """
    KKday discover 는 (product_no, package_id, package_option_id) 만 남기고
    상품명을 저장하지 않는다(label 이 'On-shelf Unlimited' 같은 패키지 옵션명).
    그래서 이름 기반 매핑이 불가능하다 — product_no 목록만 돌려준다.
    """
    d = _discover_dir()
    items: list[dict] = []
    seen: set[str] = set()
    src = []
    if d is not None:
        for region in ("KOREA", "JAPAN", "AUSTRALIA", "UK"):
            f, rows = _latest_nonempty(f"kkday_{region}_*.json")
            if not rows:
                continue
            src.append(os.path.basename(f))
            for r in rows:
                pno = str(r.get("product_no", ""))
                if not pno or pno in seen:
                    continue
                seen.add(pno)
                items.append({"id": pno, "name": "", "region": region,
                              "status": str(r.get("label", ""))})
    return {"channel": "KK", "source": ", ".join(src) or None,
            "count": len(items), "items": items,
            "note": "KKday discover 가 상품명을 저장하지 않아 이름 매칭이 불가능합니다. "
                    "product_no 를 직접 지정하거나, discover 에 상품명 수집을 추가해야 합니다."}


def gg_catalog() -> dict:
    return {"channel": "GG", "source": None, "count": 0, "items": [],
            "note": "GG 는 상품 카탈로그가 없습니다. availability 페이지에서 옵션 제목을 "
                    "수집하는 기능이 필요합니다 (미구현)."}


LOADERS = {"MRT": mrt_catalog, "VI": vi_catalog, "KK": kk_catalog, "GG": gg_catalog}


def catalog(channel: str) -> dict:
    fn = LOADERS.get(str(channel).upper())
    if fn is None:
        return {"channel": channel, "source": None, "count": 0, "items": [],
                "note": "지원하지 않는 채널"}
    return fn()


# ──────────────────────────────────────────────────────────────────────────────
# 이름 유사도 (자동 매칭 후보 추천용)
# ──────────────────────────────────────────────────────────────────────────────
_STOP = re.compile(r"[\[\]()·・,:/&+\-—–|]|day tour|tour|from|shared|1명도출발|한국어가이드", re.I)


def _tokens(text: str) -> set[str]:
    t = _STOP.sub(" ", str(text or "").lower())
    return {w for w in re.split(r"\s+", t) if len(w) > 1}


def score(tour_name: str, candidate_name: str) -> float:
    """
    0~1. 토큰 교집합 기반 + 연속 부분문자열 보너스.
    정확도가 높지 않으므로 '추천' 용도로만 쓰고, 최종 확정은 사람이 한다.
    """
    a, b = _tokens(tour_name), _tokens(candidate_name)
    if not a or not b:
        return 0.0
    jac = len(a & b) / len(a | b)
    lo_t, lo_c = str(tour_name).lower(), str(candidate_name).lower()
    compact_t = re.sub(r"\s+", "", lo_t)
    compact_c = re.sub(r"\s+", "", lo_c)
    bonus = 0.35 if compact_t and compact_t in compact_c else 0.0
    return min(1.0, jac + bonus)


def suggest(tour_name: str, channel: str, top: int = 5) -> list[dict]:
    cat = catalog(channel)
    scored = []
    for item in cat["items"]:
        if not item.get("name"):
            continue
        s = score(tour_name, item["name"])
        if s > 0:
            scored.append({**item, "score": round(s, 3)})
    scored.sort(key=lambda x: -x["score"])
    return scored[:top]
