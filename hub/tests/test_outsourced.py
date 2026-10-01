# -*- coding: utf-8 -*-
"""
아웃소싱 상품(MBC 스튜디오) 줄 검사.

규칙이 두 번 바뀌었다.

  2026-09-27  빠른 입력 칸에 'MBC 스튜디오' 를 적으면 '이 지역 목록에 없는
              이름입니다' 만 나왔다. 화면의 줄은 그날 예약이 있는 상품으로
              만드는데 아웃소싱 상품은 예약 파일에 안 들어오기 때문이다.
              -> 예약이 없어도 줄을 만들고, 맨 위에서 직접 넣게 했다.

  2026-10-01  그랬더니 MBC 를 안 파는 날에도 빈 칸이 매일 맨 위에 떴다.
              -> **익일 예약에 있을 때만** 맨 위로 올린다. 없으면 생략.

지금 보는 것
  1) 이름을 알아보는가 (띄어쓰기·괄호·옵션 표기가 제각각이다)
  2) 예약에 있으면 그 줄에 표시만 단다 — **새 줄을 만들지 않는다**
     (같은 줄이 둘이면 수량이 두 번 들어간다)
  3) 예약에 없으면 아무 일도 하지 않는다
  4) 화면이 그 표시를 보고 맨 위에 그리고, 빠른 입력에서는 뺀다

    python hub/tests/test_outsourced.py
"""
import os
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[2]
UI = ROOT / "hub" / "op_ui"
os.environ["LMHUB_LOCAL_ONLY"] = "1"
sys.path.insert(0, str(ROOT / "hub"))
sys.path.insert(0, str(UI))

from core.lastmin import outsourced as O            # noqa: E402

bad = []

print("  [1] 목록")
print(f"     {O.names()}")
if not O.names():
    bad.append("아웃소싱 목록이 비어 있다")
if not any("MBC" in p for p in O.names()):
    bad.append("MBC 스튜디오가 목록에 없다")
if not any("리허설" in p.replace(" ", "") for p in O.names()):
    bad.append("드라마리허설이 목록에 없다 (2026-10-01 요청)")

print()
print("  [2] 예약 줄에 표시만 단다")
groups = [{"region": "Korea", "areas": [{"area": "Seoul", "rows": [
    {"product": "경주", "option": ""},
    {"product": "MBC 스튜디오", "option": ""},
    {"product": "MBC 스튜디오", "option": "드라마 리허설"},
]}]}]
before = len(groups[0]["areas"][0]["rows"])
n = O.mark(groups)
rows = groups[0]["areas"][0]["rows"]
print(f"     표시 {n}건 / 줄 수 {before} -> {len(rows)}")
for r in rows:
    print(f"       {r['product']:16} {r.get('option',''):10} "
          f"{'아웃소싱' if r.get('outsourced') else '일반'}")
if n != 2:
    bad.append(f"MBC 두 줄에 표시가 안 붙었다 ({n}건)")
if len(rows) != before:
    bad.append("줄을 새로 만들었다 — 수량이 두 번 들어간다")
if rows[0].get("outsourced"):
    bad.append("일반 상품에 표시가 붙었다")

print()
print("  [3] 예약에 없으면 아무 일도 안 한다")
plain = [{"region": "Korea", "areas": [{"area": "Seoul", "rows": [
    {"product": "경주", "option": ""}]}]}]
n2 = O.mark(plain)
rows2 = plain[0]["areas"][0]["rows"]
print(f"     표시 {n2}건 / 줄 {len(rows2)}개 {[r['product'] for r in rows2]}")
if n2 or len(rows2) != 1:
    bad.append("예약에 없는데 줄을 만들거나 표시를 달았다")

print()
print("  [4] 화면 연결")
SRC = (UI / "lastmin_tab.py").read_text(encoding="utf-8")
PAN = (ROOT / "hub" / "core" / "lastmin" / "panels.py").read_text(encoding="utf-8")
CASES = [
    ("패널이 표시를 단다", "lmoutsourced.mark(groups)" in PAN),
    ("더 이상 줄을 만들지 않는다", "_outsourced_group" not in PAN),
    ("맨 위에 그린다", "_outsourced_block(latest, panels[latest])" in SRC),
    ("표시를 보고 고른다", 'r.get("outsourced")' in SRC),
    ("빠른 입력에서 뺀다", 'qrows = [r for r in a["rows"] if not r.get("outsourced")]' in SRC),
    ("두 번 그리지 않는다", 'if p["is_latest"] and row.get("outsourced"):' in SRC),
]
for label, cond in CASES:
    print(f"     {label:24} {'예' if cond else '!! 아니오'}")
    if not cond:
        bad.append(f"{label} — 아니다")

print()
if bad:
    for b in bad:
        print("  !!", b)
    raise SystemExit(f"!! {len(bad)}건 어긋남")
print("전부 통과 — 있는 날만 맨 위로, 줄은 하나, 빠른 입력에서는 뺀다")
