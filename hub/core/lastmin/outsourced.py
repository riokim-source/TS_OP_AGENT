# -*- coding: utf-8 -*-
"""
outsourced.py
예약 파일에 안 나오지만 **매일 수량을 넣어야 하는 상품**.

왜 필요한가
    화면의 줄은 그날 예약이 있는 상품으로 만든다. 그런데 아웃소싱으로 파는
    상품은 우리 예약 파일에 안 들어온다. 그러면 줄 자체가 없어서 수량을 넣을
    자리도 없다. 2026-09-27 에 MBC 스튜디오가 그랬다 — 빠른 입력 칸에 적어도
    '이 지역 목록에 없는 이름입니다' 만 나왔다.

    그래서 이 목록의 상품은 **예약이 없어도 줄을 만든다.**

⚠️ 여기에 적은 상품은 빠른 입력(텍스트) 대상이 아니다. 화면 맨 위에 따로
   두고 예전처럼 수량·언어·픽업을 직접 고른다 (2026-09-27 요청).

⚠️ 예약이 있는 날에는 원래 줄이 이미 있다. 그때는 만들지 않는다 (같은 줄이
   둘이 되면 수량이 두 번 들어간다).
"""
from __future__ import annotations

# (지역 Area, 상품명, 옵션) — 옵션이 없으면 "".
# 상품명은 **메모·오픈에 그대로 쓰는 이름**이다. 맵핑표(productmap.json)와
# 같은 표기를 쓴다.
OUTSOURCED: list[tuple[str, str, str]] = [
    ("Seoul", "MBC 스튜디오", ""),
]


def rows() -> list[dict]:
    """화면 줄로 쓸 수 있는 모양으로."""
    out = []
    for area, product, option in OUTSOURCED:
        out.append({"area": area, "product": product, "option": option})
    return out


def keys() -> set[str]:
    """이미 있는 줄인지 볼 때 쓰는 열쇠 (loader 의 _row_key 와 같은 모양)."""
    return {f"{a}|{p}|{o}" for a, p, o in OUTSOURCED}


def has(area: str, product: str, option: str = "") -> bool:
    return (str(area), str(product), str(option or "")) in [
        (a, p, o) for a, p, o in OUTSOURCED]
