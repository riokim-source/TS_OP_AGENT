# -*- coding: utf-8 -*-
"""
vi_targets.py
Viator 에서 매일 다루는 상품 목록. **이 파일 하나가 주인이다.**

예전에는 화면의 드롭다운에 있는 상품을 전부(99개) 돌았다. 실제로 우리가
매일 여닫는 것은 14개뿐인데, 나머지 85개를 도느라 마감이 20분씩 걸렸고
'안 파는 상품' 스킵이 63건씩 쌓여서 진짜 실패가 그 속에 묻혔다.
(2026-09-09 마감: 스킵 63건 / 그 안에 실제 실패 1건)

새 상품이 생기면 여기에만 추가한다. 코드 여러 곳에 복사해 두지 않는다.
번호는 Viator 공급자 화면의 product code (48881P○○) 다.

⚠️ 한 번호에 투어가 여러 개 묶여 있다. Viator 는 상품 하나에 옵션이 여럿이라
   '경주' 와 '경주Express' 가 같은 48881P43 안에 들어 있다. 그래서 여닫는
   단위는 '투어' 가 아니라 '이 번호' 다.
"""
from __future__ import annotations

# 번호 -> (지역, 그 번호에 묶인 투어들)
VI_TARGETS: dict[str, tuple[str, tuple[str, ...]]] = {
    # ── 한국 ────────────────────────────────────────────────────────────
    "48881P43":  ("KOREA", ("경주", "경주Express")),
    "48881P170": ("KOREA", ("감천미포", "감천미포 Sightseeing", "감천미포 Early Bird")),
    "48881P2":   ("KOREA", ("청해자감", "아해자감")),
    "48881P202": ("KOREA", ("선셋캡슐",)),
    "48881P11":  ("KOREA", ("레남아",)),
    "48881P13":  ("KOREA", ("설낙",)),
    "48881P93":  ("KOREA", ("포천",)),
    # ── 일본 ────────────────────────────────────────────────────────────
    "48881P233": ("JAPAN", ("Biei Highlights", "Biei Signature", "Biei Furano")),
    "48881P183": ("JAPAN", ("Mt. Fuji Highlight", "Mt. Fuji Signature")),
    "48881P238": ("JAPAN", ("Kamakura Highlight", "Kamakura Yokohama")),
    "48881P211": ("JAPAN", ("Yufuin Brewery", "Yufuin Dazaifu")),
    "48881P206": ("JAPAN", ("Kyoto Nara", "Arashiyama & Nishiki")),
    "48881P237": ("JAPAN", ("Shirakawago Regular",)),
    # ── 호주 ────────────────────────────────────────────────────────────
    "48881P232": ("AUSTRALIA", ("Blue Mountains Zig Zag",)),
}


def codes() -> list[str]:
    """다룰 상품 번호 (적어 둔 순서 그대로)."""
    return list(VI_TARGETS)


def region_of(code: str) -> str:
    return VI_TARGETS.get(code, ("", ()))[0]


def tours_of(code: str) -> tuple[str, ...]:
    return VI_TARGETS.get(code, ("", ()))[1]


def label_of(code: str) -> str:
    """로그에 쓸 이름. 번호만 남으면 나중에 무엇이 빠졌는지 알 수 없다."""
    region, tours = VI_TARGETS.get(code, ("", ()))
    return f"[{region}] {' / '.join(tours)}" if tours else code


def by_region() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for code, (region, _) in VI_TARGETS.items():
        out.setdefault(region, []).append(code)
    return out


def code_for_tour(name: str) -> str | None:
    """
    투어 이름 -> 상품 번호. 못 찾으면 None (추측하지 않는다).

    이름 표기가 조금씩 달라서 (공백/대소문자/'&' 앞뒤) 그 정도만 눌러서 맞춘다.
    그 이상은 맞추지 않는다 — 비슷하다고 열면 엉뚱한 상품이 열린다.
    """
    key = _norm(name)
    if not key:
        return None
    for code, (_, tours) in VI_TARGETS.items():
        for t in tours:
            if _norm(t) == key:
                return code
    return None


def _norm(s: str) -> str:
    return "".join(str(s or "").lower().split()).replace("&", "").replace(".", "")
