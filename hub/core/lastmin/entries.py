# -*- coding: utf-8 -*-
"""
entries.py
운영자가 손으로 넣은 값(수량 / 언어 / 픽업 / OTA 변경)을 파일로 보관한다.

왜 필요한가
    Streamlit 은 브라우저를 새로고침하거나 연결이 끊기면 session_state 가 사라진다.
    투어 30개에 수량을 다 넣어 놓고 새로고침 한 번에 전부 날아가면
    10시 마감 작업에서 치명적이다.

키에 파일명과 투어일자를 넣어 두어서, 다음 날 새 파일을 올리면 저절로 새 판이 된다.
"""
from __future__ import annotations

import json
import re
from datetime import datetime

from ..paths import DATA_DIR

STORE = DATA_DIR / "lm_entries.json"


def _key(loaded: dict) -> str:
    name = str((loaded or {}).get("filename") or "?")
    dates = ",".join((loaded or {}).get("dates") or [])
    return re.sub(r"\s+", " ", f"{name}|{dates}").strip()


def _all() -> dict:
    if not STORE.exists():
        return {}
    try:
        return json.loads(STORE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def load(loaded: dict) -> dict:
    """이 파일/날짜 조합으로 저장해 둔 입력값. 없으면 빈 dict."""
    return (_all().get("sets") or {}).get(_key(loaded)) or {}


def save(loaded: dict, entries: dict) -> None:
    """실패해도 작업을 막지 않는다. 보관은 편의 기능이다."""
    try:
        data = _all()
        sets = data.setdefault("sets", {})
        sets[_key(loaded)] = entries
        # 오래된 판이 무한히 쌓이지 않게 최근 8개만 남긴다
        if len(sets) > 8:
            for k in list(sets.keys())[:-8]:
                sets.pop(k, None)
        data["updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        STORE.parent.mkdir(parents=True, exist_ok=True)
        STORE.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:
        pass


def clear(loaded: dict) -> None:
    try:
        data = _all()
        (data.get("sets") or {}).pop(_key(loaded), None)
        STORE.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:
        pass


def prune(entries: dict, valid_keys: set) -> dict:
    """
    지금 화면에 있는 투어의 값만 남긴다.

    저장해 둔 뒤 예약 파일이 바뀌어 투어가 사라졌을 수 있다.
    없는 투어의 수량을 들고 있으면 메모에 유령 항목이 생긴다.
    """
    return {k: v for k, v in (entries or {}).items() if k in valid_keys}
