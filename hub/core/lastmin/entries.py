# -*- coding: utf-8 -*-
"""
entries.py
운영자가 손으로 넣은 값(수량 / 언어 / 픽업 / OTA 변경)을 파일로 보관한다.

왜 필요한가
    Streamlit 은 브라우저를 새로고침하거나 연결이 끊기면 session_state 가 사라진다.
    투어 30개에 수량을 다 넣어 놓고 새로고침 한 번에 전부 날아가면
    10시 마감 작업에서 치명적이다.

키에 파일명과 투어일자를 넣어 두어서, 다음 날 새 파일을 올리면 저절로 새 판이 된다.

⚠️ 키에 '누가' 도 넣는다.
    예전에는 파일명|날짜 하나로만 저장해서, 두 사람이 같은 파일을 열면
    서로의 수량을 덮어썼다. 화면을 그릴 때마다 저장하므로 상대가 한 번만
    움직여도 내 값이 지워졌고, 새로고침하면 남의 수량이 '직전에 입력한 값'
    으로 되살아났다. 클라우드는 모두가 같은 서버를 쓰기 때문에 두 명째부터
    바로 문제가 된다.
"""
from __future__ import annotations

import json
import re
from datetime import datetime

from ..paths import DATA_DIR

STORE = DATA_DIR / "lm_entries.json"
KEEP = 24                       # 최근 몇 판을 남길지 (사람 수만큼 늘어난다)
ANON = "(이름없음)"


def who_label(who: str) -> str:
    """빈 이름도 하나의 자리로 다룬다 (이름을 안 적어도 동작해야 한다)."""
    return re.sub(r"\s+", " ", str(who or "").strip()) or ANON


def _base(loaded: dict) -> str:
    name = str((loaded or {}).get("filename") or "?")
    dates = ",".join((loaded or {}).get("dates") or [])
    return re.sub(r"\s+", " ", f"{name}|{dates}").strip()


def _key(loaded: dict, who: str = "") -> str:
    return f"{_base(loaded)}|{who_label(who)}"


def _all() -> dict:
    if not STORE.exists():
        return {}
    try:
        return json.loads(STORE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _entries_of(rec) -> dict:
    """저장 형식이 두 가지다 (옛 것은 값이 곧 입력값이었다)."""
    if isinstance(rec, dict) and "entries" in rec:
        return rec.get("entries") or {}
    return rec or {}


def load(loaded: dict, who: str = "") -> dict:
    """
    내가 저장해 둔 입력값. 없으면 빈 dict.

    ⚠️ 사람 구분을 넣기 전에 저장된 것은 키가 다르다 (파일명|날짜 까지만).
       그것까지 못 읽으면, 바꾼 날 작업 중이던 사람의 수량이 새로고침
       한 번에 통째로 날아간다. 그래서 내 것이 없을 때만 옛 자리를 본다.
       (저장은 언제나 새 자리에 한다 — 옛 자리는 점점 사라진다)
    """
    sets = _all().get("sets") or {}
    hit = sets.get(_key(loaded, who))
    if hit is None:
        hit = sets.get(_base(loaded))
        if isinstance(hit, dict) and "entries" in hit:
            hit = None                     # 남의 새 형식 기록이면 쓰지 않는다
    return _entries_of(hit)


def save(loaded: dict, entries: dict, who: str = "") -> None:
    """실패해도 작업을 막지 않는다. 보관은 편의 기능이다."""
    try:
        data = _all()
        sets = data.setdefault("sets", {})
        sets[_key(loaded, who)] = {
            "who": who_label(who),
            "base": _base(loaded),
            "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "count": sum(1 for v in (entries or {}).values()
                         if int((v or {}).get("qty") or 0) > 0),
            "entries": entries,
        }
        # 오래된 판이 무한히 쌓이지 않게 최근 것만 남긴다
        if len(sets) > KEEP:
            for k in list(sets.keys())[:-KEEP]:
                sets.pop(k, None)
        data["updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        STORE.parent.mkdir(parents=True, exist_ok=True)
        STORE.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:
        pass


def clear(loaded: dict, who: str = "") -> None:
    try:
        data = _all()
        (data.get("sets") or {}).pop(_key(loaded, who), None)
        STORE.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:
        pass


def others(loaded: dict, who: str = "") -> list[dict]:
    """
    같은 파일을 만지고 있는 '다른 사람' 목록.

    수량을 두 사람이 따로 넣고 있으면 한쪽이 실행할 때 상대 몫이 빠진다.
    막지는 않는다 — 누가 무엇을 넣었는지 보여주고 사람이 판단한다.
    """
    base, me = _base(loaded), who_label(who)
    out = []
    for k, rec in ((_all().get("sets") or {})).items():
        if not isinstance(rec, dict) or "entries" not in rec:
            continue                       # 옛 형식은 누구 것인지 알 수 없다
        if rec.get("base") != base or rec.get("who") == me:
            continue
        out.append({"who": rec.get("who") or ANON, "at": rec.get("at", ""),
                    "count": int(rec.get("count") or 0)})
    return sorted(out, key=lambda x: x["at"], reverse=True)


def prune(entries: dict, valid_keys: set) -> dict:
    """
    지금 화면에 있는 투어의 값만 남긴다.

    저장해 둔 뒤 예약 파일이 바뀌어 투어가 사라졌을 수 있다.
    없는 투어의 수량을 들고 있으면 메모에 유령 항목이 생긴다.
    """
    return {k: v for k, v in (entries or {}).items() if k in valid_keys}
