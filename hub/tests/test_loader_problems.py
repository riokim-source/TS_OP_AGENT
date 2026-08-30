# -*- coding: utf-8 -*-
"""
엑셀에서 빠진 행을 이유와 함께 알리는지 검사.

Area 나 Product 가 비면 groupby 가 그 행을 버려서 투어가 화면에 아예
안 나온다. 안 보이니 수량도 못 넣고 그날 안 열린다. 예전에는 아무 경고가
없어서 끝까지 모르고 지나갔다.

날짜가 3개 이상인 경우도 함께 본다. '가장 나중 2개' 가 내일이 아닐 수 있다.

    python hub/tests/test_loader_problems.py
"""
import io
import sys
import warnings
from datetime import date
from pathlib import Path

warnings.simplefilter("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402

from core.lastmin.panels import build_panels  # noqa: E402

COLS = ["Date", "Reservation Date", "Area", "Product", "Agency", "People",
        "Option", "Language", "Pickup"]


def xl(rows):
    b = io.BytesIO()
    pd.DataFrame(rows, columns=COLS).to_excel(b, index=False)
    return b.getvalue()


BASE = [
    ["2026-08-31", "2026-08-30 11:00", "Seoul", "남이섬", "L", 4, None, "english", "H"],
    ["2026-08-30", "2026-08-29 11:00", "Seoul", "남이섬", "L", 3, None, "english", "H"],
]


def row(date_v="2026-08-31", area="Seoul", product="남이섬", people=9):
    return [date_v, "2026-08-30 11:00", area, product, "L", people, None, "e", "H"]


CASES = [
    # 이름,                추가 행,                       이유에 들어가야 할 말
    ("정상",                None,                          None),
    ("Area 빈 칸",          row(area=None),                "Area"),
    ("Product 공백만",      row(product="  "),             "Product"),
    ("날짜 31/08/2026",     row(date_v="31/08/2026"),      "날짜를 읽지 못함"),
    ("날짜 빈 칸",          row(date_v=None),              "날짜 칸이 비어 있음"),
    ("인원이 '다섯'",       row(people="다섯"),            "숫자가 아님"),
    ("인원 음수",           row(people=-5),                "음수"),
]

bad = 0
for label, extra, want in CASES:
    rows = BASE + ([extra] if extra else [])
    L = build_panels(xl(rows), "t.xlsx").get("loaded") or {}
    probs = L.get("problems") or []
    hit = [p for p in probs if want and want in p["reason"]]
    ok = (not probs) if want is None else bool(hit)
    print(f"  [{label}]")
    for p in probs:
        print(f"     {p['reason']} — {p['rows']}행 {p['people']}명 · {p['fix']}")
    if not probs:
        print("     (빠진 행 없음)")
    if not ok:
        bad += 1
        print(f"     !! '{want}' 를 알리지 않았다")

print()
print("  [투어일자가 3개일 때]")
rows = BASE + [["2026-09-15", "2026-08-30 11:00", "Seoul", "에버", "L", 99,
                None, "e", "H"]]
L = build_panels(xl(rows), "t.xlsx")["loaded"]
print(f"     기본 대상  : {L['dates']}   파일 전체 {L['all_dates']}")
print(f"     고르라고 함: {L.get('date_choice')}")
if not L.get("date_choice"):
    bad += 1
    print("     !! 날짜가 3개인데 알리지 않았다")

picked = build_panels(xl(rows), "t.xlsx",
                      pick_dates=[date(2026, 8, 31), date(2026, 8, 30)])["loaded"]
print(f"     직접 고르면: {picked['dates']}")
if picked["dates"] != ["2026-08-31", "2026-08-30"]:
    bad += 1
    print("     !! 고른 날짜가 반영되지 않았다")

print()
if bad:
    raise SystemExit(f"!! {bad}건 어긋남 — 빠진 행이 조용히 사라진다")
print("전부 통과 — 빠진 행과 날짜 문제는 사람에게 보인다")
