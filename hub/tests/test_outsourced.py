# -*- coding: utf-8 -*-
"""
예약에 안 잡히는 상품(아웃소싱) 줄 검사.

2026-09-27: MBC 스튜디오를 빠른 입력 칸에 적었더니 '이 지역 목록에 없는
이름입니다' 만 나왔다. 화면의 줄은 **그날 예약이 있는 상품**으로 만드는데,
아웃소싱으로 파는 상품은 우리 예약 파일에 안 들어오기 때문이다.
줄이 없으니 수량을 넣을 자리도 없었다.

  - 그런 상품은 예약이 없어도 줄을 만든다 (outsourced.py)
  - 화면 맨 위에 따로 두고 **예전처럼 직접** 수량·언어·픽업을 고른다
    (빠른 입력 대상이 아니다)
  - 예약이 있는 날에는 원래 줄이 이미 있으니 만들지 않는다
    (같은 줄이 둘이면 수량이 두 번 들어간다)

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
from core.lastmin.panels import _outsourced_group   # noqa: E402

bad = []

print("  [1] 목록")
print(f"     {O.OUTSOURCED}")
if not O.OUTSOURCED:
    bad.append("아웃소싱 목록이 비어 있다")
if not any(p == "MBC 스튜디오" for _a, p, _o in O.OUTSOURCED):
    bad.append("MBC 스튜디오가 목록에 없다 (2026-09-27 요청)")

print()
print("  [2] 예약에 없으면 줄을 만든다")
plain = [{"region": "Korea", "areas": [{"area": "Seoul", "rows": [
    {"product": "경주", "option": ""}]}]}]
got = _outsourced_group(plain, {}, {})
rows = [(a["area"], r["product"], r["key"]) for g in got for a in g["areas"] for r in a["rows"]]
print(f"     {rows}")
if not rows:
    bad.append("예약이 없는데 줄을 안 만든다 — 수량을 넣을 자리가 없다")
for g in got:
    if g.get("region") != "아웃소싱":
        bad.append(f"그룹 이름이 {g.get('region')} (화면이 이 이름으로 가른다)")

print()
print("  [3] 예약이 있으면 만들지 않는다 (같은 줄이 둘이 되면 수량이 두 번)")
area, product, option = O.OUTSOURCED[0]
have = [{"region": "Korea", "areas": [{"area": area, "rows": [
    {"product": product, "option": option}]}]}]
got2 = _outsourced_group(have, {}, {})
dup = [r["product"] for g in got2 for a in g["areas"] for r in a["rows"]]
print(f"     이미 있는 '{product}' 를 또 만드나: {dup or '아니오'}")
if product in dup:
    bad.append(f"'{product}' 줄이 둘이 된다")

print()
print("  [4] 화면 연결")
SRC = (UI / "lastmin_tab.py").read_text(encoding="utf-8")
i_block = SRC.find("_outsourced_block(latest, panels[latest])")
i_quick = SRC.find("_quick_fill(latest, panels[latest])")
for label, cond in (
        ("맨 위에 그린다", 0 < i_block < i_quick),
        ("빠른 입력에서 뺀다", 'if g.get("region") == OUTSOURCED_GROUP:\n            continue' in SRC),
        ("상세 박스에서 또 안 그린다", 'if p["is_latest"] and g.get("region") == OUTSOURCED_GROUP:' in SRC),
        ("예전 방식 위젯을 쓴다", "_tour_row(pi, row, True)" in SRC)):
    print(f"     {label:24} {'예' if cond else '!! 아니오'}")
    if not cond:
        bad.append(f"{label} — 아니다")

print()
print("  [5] 실제로 그려지는가 (위젯 키가 겹치지 않는가)")
import streamlit as st                               # noqa: E402
import lastmin_tab as T                              # noqa: E402

st.session_state.clear()
row = {"area": "Seoul", "region": "아웃소싱", "product": "MBC 스튜디오", "option": "",
       "key": "Seoul|MBC 스튜디오|", "option_split": False, "display": "MBC 스튜디오",
       "languages": ["korean"], "pickups": ["상암"], "pickups_known": [], "lastmin": {}}
panel = {"groups": [{"region": "아웃소싱", "areas": [{"area": "Seoul", "rows": [row]}]}]}
try:
    T._outsourced_block(0, panel)
    e = st.session_state["lm_entries"][T._key(0, row)]
    print(f"     기본값 {dict((k, e[k]) for k in ('qty', 'lang', 'pick'))}")
    if e["qty"] != 0 or e["lang"] != ["korean"]:
        bad.append(f"기본값이 이상하다: {e}")
except Exception as ex:
    print(f"     !! {type(ex).__name__}: {ex}")
    bad.append(f"아웃소싱 줄에서 화면이 죽는다: {ex}")

print()
if bad:
    for b in bad:
        print("  !!", b)
    raise SystemExit(f"!! {len(bad)}건 어긋남")
print("전부 통과 — 예약이 없어도 줄이 생기고, 있으면 둘이 되지 않는다")
