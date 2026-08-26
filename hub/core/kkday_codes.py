# -*- coding: utf-8 -*-
"""
kkday_codes.py
매일 마감해야 하는 KKday 상품 번호를 '읽기만' 한다.

목록의 주인은 OTA Close/kkday.py 의 DEFAULT_PRODUCT_CODES 하나다.
새 상품이 생기면 거기에 번호를 추가한다.

여기서 굳이 다시 읽는 이유는 마감 실행 로그에 '오늘 무엇을 도는지' 를
이름과 함께 먼저 찍어두기 위해서다. 번호만 남으면 나중에 로그를 봐도
무엇이 빠졌는지 알 수 없다. 값을 바꾸지는 않는다.

⚠️ 여기에 목록을 복사해 두면 안 된다. 두 곳이 어긋나면
   화면에는 6개라고 적혀 있는데 실제로는 다른 걸 도는 상황이 된다.
"""
from __future__ import annotations

import re

from .paths import ota_close_dir

_LIST_RE = re.compile(
    r"DEFAULT_PRODUCT_CODES\s*:\s*List\[str\]\s*=\s*\[(?P<body>.*?)\]", re.S)


def load() -> list[str]:
    """kkday.py 의 DEFAULT_PRODUCT_CODES 를 그대로 읽는다. 못 읽으면 빈 목록."""
    root = ota_close_dir()
    if root is None:
        return []
    f = root / "kkday.py"
    if not f.exists():
        return []
    try:
        m = _LIST_RE.search(f.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not m:
        return []
    out: list[str] = []
    for tok in re.findall(r'"(\d+)"|\'(\d+)\'', m.group("body")):
        code = tok[0] or tok[1]
        if code and code not in out:
            out.append(code)
    return out


def names_of(codes) -> dict[str, list[str]]:
    """상품번호 -> 맵핑리스트에 있는 이름들. 번호만 보면 뭔지 모른다."""
    out: dict[str, list[str]] = {c: [] for c in codes}
    try:
        from .productmap import get_map
        kk = (get_map().data or {}).get("KK") or {}
    except Exception:
        return out
    for tour, v in kk.items():
        pno = str((v or {}).get("product_no") or "")
        if pno in out and tour not in out[pno]:
            out[pno].append(tour)
    return out
