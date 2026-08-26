# -*- coding: utf-8 -*-
"""
Office / OP 메모 기준선 회귀 테스트.

아래 두 텍스트는 운영팀이 실제로 쓰는 출력이다. 한 글자라도 달라지면 안 된다.
분배 규칙(15/20 임계값, GG 절반, 특별지역 CP·MRT)이나 메모 포맷을 건드릴 때
반드시 이걸 먼저 돌릴 것.

    python hub/tests/test_memo_baseline.py

검증 대상
  1) 최신 일자 패널  : 수량 분배 규칙
  2) 전일 패널       : 'Last Min 10시 후 예약' 자동 집계
                      (투어일자 전날 10:00 이후 들어온 예약을 채널별 합산)
  3) Office 는 제한 표기 없이 '상품명 수량' 만
  4) OP 는 Klook 언어 변형 상품명 사용
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.lastmin import loader                                   # noqa: E402
from core.lastmin.memo import RowInput, render_memo               # noqa: E402

XLSX = Path(__file__).resolve().parent.parent.parent / "2122.xlsx"

# 최신 일자에 운영자가 넣은 오픈 수량
OPEN_QTY = {
    "BIGBANG 셔틀": 7, "남아": 1, "남이섬셔틀": 3, "다크-남영동": 5, "수원화성": 1,
    "경주": 25, "경주 Express": 1, "교촌경주": 13, "선셋캡슐 East": 3, "자갈치야경": 5,
    "Kamakura Yokohama": 6, "Mt. Fuji Signature": 8, "Amanohashidate": 5,
    "Fukuoka Foodie": 3, "Yufuin Dazaifu": 3, "Biei Furano": 1,
    "Toyako Niseko": 24, "Blue Mountain Zig Zag": 5,
}

WANT_OFFICE = """[투어일자 08/22]
Last Min 오픈:
[KLOOK]: BIGBANG 셔틀 7, 남아 1, 남이섬셔틀 3, 다크-남영동 5, 수원화성 1, 경주 13, 경주 Express 1, 교촌경주 13, 선셋캡슐 East 3, 자갈치야경 5, Kamakura Yokohama 6, Mt. Fuji Signature 8, Amanohashidate 5, Fukuoka Foodie 3, Yufuin Dazaifu 3, Biei Furano 1, Toyako Niseko 12, Blue Mountain Zig Zag 5
[KK]: 경주
[VI]: 경주, Toyako Niseko
[GG]: 경주 12, Toyako Niseko 12
[CP]: Kamakura Yokohama 6, Mt. Fuji Signature 8, Amanohashidate 5, Fukuoka Foodie 3, Yufuin Dazaifu 3, Biei Furano 1, Toyako Niseko 24
[MRT]: Kamakura Yokohama 6, Mt. Fuji Signature 8, Amanohashidate 5, Fukuoka Foodie 3, Yufuin Dazaifu 3, Biei Furano 1, Toyako Niseko 24

[투어일자 08/21]
Last Min 10시 후 예약:
[KLOOK]: 알남레 5, 감천미포 8, Shakotan Otaru 4
[KK]: 
[VI]: 
[GG]: Shakotan Otaru 2
[CP]: 
[MRT]: Kamakura Highlight 2, Amanohashidate 1, Kumamoto Takachiho 2"""

WANT_OP = """[투어일자 08/22]
Last Min 오픈:
[KLOOK]: BIGBANG 셔틀 7, 남아 1, 남이섬셔틀 3, 다크-남영동 5, 수원화성 1, 경주 13, 경주 Express 1, 교촌경주 13, 선셋캡슐 East 3, 자갈치야경 5, Kamakura Yokohama 6, Mt. Fuji Signature 8, Amanohashidate 5, Fukuoka Foodie 3, Yufuin Dazaifu 3, Biei Furano 1, Toyako Niseko 12, Blue Mountain Zig Zag 5
[KK]: 경주
[VI]: 경주, Toyako Niseko
[GG]: 경주 12, Toyako Niseko 12
[CP]: Toyako Niseko 12
[MRT]: Toyako Niseko 12

[투어일자 08/21]
Last Min 10시 후 예약:
[KLOOK]: 알남레 5, 감천미포 8, Shakotan Otaru 4
[KK]: 
[VI]: 
[GG]: Shakotan Otaru 2
[CP]: 
[MRT]: Kamakura Highlight 2, Amanohashidate 1, Kumamoto Takachiho 2"""


def build_panels() -> list[dict]:
    """UI 가 하는 것과 같은 방식으로 예약 파일에서 패널을 만든다."""
    res = loader.load(str(XLSX))
    panels = []
    for p in res["panels"]:
        rows = []
        for group in p["groups"]:
            for area in group["areas"]:
                for r in area["rows"]:
                    # 옵션분리 투어는 '옵션없음' 버킷이 대표 행
                    qty = 0
                    if p["is_latest"]:
                        if not r["option_split"] or r["option"] == "(옵션없음)":
                            qty = OPEN_QTY.get(r["product"], 0)
                    rows.append(RowInput(
                        area=r["area"], product=r["product"], option=r["option"],
                        option_split=r["option_split"], qty=qty,
                        channel_qty=dict(r.get("lastmin") or {}),
                    ))
        panels.append({"date_label": p["date_label"],
                       "is_latest": p["is_latest"], "rows": rows})
    return panels


def main() -> int:
    if not XLSX.exists():
        print(f"기준 예약파일이 없습니다: {XLSX}")
        return 2
    panels = build_panels()
    ok = True
    for label, is_op, want in (("OFFICE", False, WANT_OFFICE), ("OP", True, WANT_OP)):
        got = render_memo(panels, is_op=is_op).strip()
        same = got == want.strip()
        ok &= same
        print(f"{label:7} 일치: {same}")
        if not same:
            got_lines, want_lines = got.split("\n"), want.strip().split("\n")
            for i in range(max(len(got_lines), len(want_lines))):
                g = got_lines[i] if i < len(got_lines) else "(없음)"
                w = want_lines[i] if i < len(want_lines) else "(없음)"
                if g != w:
                    print(f"   줄 {i + 1}")
                    print(f"     나온것: {g}")
                    print(f"     기대값: {w}")
    print()
    print("전체 일치" if ok else "불일치 있음")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
