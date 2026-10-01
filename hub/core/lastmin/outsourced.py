# -*- coding: utf-8 -*-
"""
outsourced.py
아웃소싱으로 파는 상품 — 화면 맨 위에서 따로 받는 줄.

지금 규칙 (2026-10-01)
    예약 파일의 **익일(오픈 대상) 날짜에 그 상품이 있을 때만** 맨 위에 올린다.
    없으면 아예 그리지 않는다.

왜 바뀌었나
    2026-09-27 에는 '예약에 안 잡히는 상품이라 줄 자체가 없다' 가 문제였고,
    그래서 예약이 없어도 줄을 만들어 두었다. 그런데 그러면 MBC 를 안 파는
    날에도 빈 칸이 매일 맨 위에 떠서, 넣을 것이 없는데 넣어야 하나 싶은
    자리가 된다. 운영에서 '있는 날만 올려 달라' 로 정리했다.

⚠️ 줄을 **새로 만들지 않는다.** 예약에 있는 그 줄에 표시만 단다
   (`outsourced: True`). 새로 만들면 같은 상품이 두 줄이 되어 수량이 두 번
   들어가고, 메모의 상품 순서도 흔들린다.

⚠️ 표시가 붙은 줄은 빠른 입력(텍스트) 대상이 아니다. 화면 맨 위에서 예전처럼
   수량·언어·픽업을 직접 고른다 (2026-09-27 요청 그대로).
"""
from __future__ import annotations

import re

# 아웃소싱 상품 이름. 띄어쓰기·대소문자는 무시하고 '앞부분이 같으면' 같은 상품으로 본다.
#   'MBC 스튜디오'            -> MBC 스튜디오
#   'MBC스튜디오(드라마 리허설)' -> MBC 스튜디오 드라마리허설
OUTSOURCED_PRODUCTS: list[str] = [
    "MBC 스튜디오",
    "MBC 스튜디오 드라마리허설",
]


def _squash(text: str) -> str:
    """띄어쓰기·괄호·대소문자를 지운 비교용 글자.

    ⚠️ 이름이 _norm 이면 안 된다. core/pickups.py 에도 _norm 이 있는데 하는 일이
       다르고(공백만 정리), 수집 전용 파일 한 개로 이어 붙일 때 뒤의 것이 앞의
       것을 덮어 픽업 목록 조회가 통째로 어긋난다.
    """
    return re.sub(r"[\s()\[\]/·\-_]+", "", str(text or "")).casefold()


_NORMED = [_squash(p) for p in OUTSOURCED_PRODUCTS]


def matches(product: str, option: str = "") -> bool:
    """
    이 줄이 아웃소싱 상품인가.

    예약 파일이 'MBC 스튜디오' + 옵션 '드라마 리허설' 로 올 수도 있고,
    상품명 하나로 'MBC스튜디오(드라마리허설)' 로 올 수도 있다. 둘 다 잡는다.
    """
    p = _squash(product)
    both = _squash(f"{product}{option}")
    for n in _NORMED:
        if not n:
            continue
        if p == n or both == n or p.startswith(n) or both.startswith(n):
            return True
    return False


def mark(groups: list) -> int:
    """
    패널 안에서 아웃소싱 줄에 표시를 단다. 단 개수를 돌려준다.

    표시만 달고 자리는 그대로 둔다 — 메모의 상품 순서를 흔들지 않기 위해서다
    (화면에서 맨 위로 올려 그리는 것은 화면 쪽 일이다).
    """
    n = 0
    for g in groups or []:
        for a in g.get("areas") or []:
            for row in a.get("rows") or []:
                if matches(row.get("product", ""), row.get("option", "")):
                    row["outsourced"] = True
                    n += 1
    return n


def names() -> list[str]:
    return list(OUTSOURCED_PRODUCTS)
