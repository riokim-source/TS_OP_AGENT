# -*- coding: utf-8 -*-
"""
예약에 없는 언어·픽업지도 고를 수 있는지 검사.

⚠️ '홍대 제외' 는 홍대 예약이 **없을 때** 하는 지시다. 후보를 예약 기준으로
   만들면 정작 필요한 순간에 목록에 없어 뺄 수가 없고, 그대로 '모든 픽업'
   으로 나가 열려 버린다.

2026-08-31 실제 사고:
   Seasonal BTS 가 명동·동대문 예약만 있어 홍대를 뺄 수 없었고,
   '모든 픽업' 으로 나가 홍대에 2자리가 열렸다.
     [GG] KOREA Seasonal BTS +4 (모든 픽업)
     [분할] 4명 -> Hongik Univ Station=2, Myeongdong=1, Dongdaemun=1

    python hub/tests/test_options_not_lost.py
"""
import sys, io, warnings
warnings.simplefilter("ignore")
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pandas as pd
from core.lastmin.panels import build_panels
from core.lastmin.memo import RowInput, build_open_plan

COLS = ["Date","Reservation Date","Area","Product","Agency","People",
        "Option","Language","Pickup"]
rows = [
    # Seasonal BTS: 명동·동대문 예약만 (홍대 없음) — 오늘 실제 상황
    ["2026-09-01","2026-08-31 09:00","Seoul","Seasonal BTS","GG",2,None,"english","Myungdong"],
    ["2026-09-01","2026-08-31 09:00","Seoul","Seasonal BTS","GG",2,None,"english","Dongdaemoon"],
    # 같은 지역 다른 투어에는 홍대가 있다
    ["2026-09-01","2026-08-31 09:00","Seoul","레남아","GG",3,None,"english","Hongdae"],
    ["2026-09-01","2026-08-31 09:00","Seoul","레남아","GG",3,None,"korean","Myungdong"],
    ["2026-08-31","2026-08-30 09:00","Seoul","Seasonal BTS","GG",1,None,"english","Myungdong"],
]
b = io.BytesIO(); pd.DataFrame(rows, columns=COLS).to_excel(b, index=False)
r = build_panels(b.getvalue(), "t.xlsx")

tgt = {}
for g in r["panels"][0]["groups"]:
    for a in g["areas"]:
        for row in a["rows"]:
            tgt[row["product"]] = row

print("=== 화면에 뜨는 후보 ===")
for name in ("Seasonal BTS", "레남아"):
    row = tgt[name]
    print(f"  {name}")
    print(f"     픽업 : {row['pickups']}")
    print(f"       (GG 확인됨 {row.get('pickups_known') or '없음'})")
    print(f"     언어 : {row['languages']}")

bts = tgt["Seasonal BTS"]
ok1 = "Hongdae" in bts["pickups"]
ok2 = "chinese" in bts["languages"]
print()
print(f"  홍대를 고를 수 있나  : {'예 ✅' if ok1 else '아니오 ❌'}")
print(f"  중국어를 고를 수 있나: {'예 ✅' if ok2 else '아니오 ❌'}")

print()
print("=== 홍대를 빼고 계획을 만들면 ===")
sel = [p for p in bts["pickups"] if p != "Hongdae"]
ri = RowInput(area="Seoul", product="Seasonal BTS", qty=4,
              languages_all=list(bts["languages"]), languages_sel=list(bts["languages"]),
              pickups_all=list(bts["pickups"]), pickups_sel=sel)
print(f"  고른 픽업 : {sel}")
print(f"  제한 걸림 : {ri.pickup_restricted()}")
for it in build_open_plan([ri], is_op=False):
    if it["channel"] == "GG":
        print(f"  GG 로 나가는 것: {it['product']} {it['qty']} / 픽업 {it['pickups_allowed'] or '(모든 픽업)'}")

print()
print("=== 아무것도 안 빼면 (기존과 같아야 함) ===")
ri2 = RowInput(area="Seoul", product="Seasonal BTS", qty=4,
               languages_all=list(bts["languages"]), languages_sel=list(bts["languages"]),
               pickups_all=list(bts["pickups"]), pickups_sel=list(bts["pickups"]))
for it in build_open_plan([ri2], is_op=False):
    if it["channel"] == "GG":
        print(f"  GG: {it['product']} {it['qty']} / 픽업 {it['pickups_allowed'] or '(모든 픽업)'}")
        print(f"  메모 주석: {it['note'] or '(없음)'}")

# ── 한 칸에 언어가 둘 적힌 경우 ──────────────────────────────────────────
# 'Chinese,English' 가 통째로 후보 하나가 되면, 그 뜻 없는 항목만 빼도
# '언어 제한' 이 걸려 오픈이 통째로 달라진다 (10 -> 5 + (중) 5).
from core.lastmin.memo import available_languages  # noqa: E402

print()
print("=== 한 칸에 'Chinese,English' 로 들어온 경우 ===")
langs = available_languages("Mt. Fuji Highlight",
                            ["English", "Chinese", "Korean", "Japanese",
                             "Chinese,English"])
print(f"  후보: {langs}")

mixed = [x for x in langs if "," in x or "/" in x or "&" in x]
plain = RowInput(area="Tokyo", product="Mt. Fuji Highlight", qty=20,
                 languages_all=langs, languages_sel=list(langs))
print(f"  섞인 값 남아있나: {mixed or '없음'}")
print(f"  전부 선택 시 제한: {plain.language_restricted()}")

print()
bad = []
if mixed:
    bad.append(f"한 칸에 둘 적힌 값이 후보로 남았다: {mixed}")
if plain.language_restricted():
    bad.append("전부 골랐는데 언어 제한이 걸린다")
for need in ("english", "chinese"):
    if need not in langs:
        bad.append(f"{need} 가 후보에 없다")
if "Hongdae" not in bts["pickups"]:
    bad.append("예약에 없는 픽업지(홍대)가 후보에 없다")
if "chinese" not in bts["languages"]:
    bad.append("예약에 없는 언어(중국어)가 후보에 없다")
if not ri.pickup_restricted():
    bad.append("홍대를 뺐는데 제한이 안 걸린다")
gg = [i for i in build_open_plan([ri], is_op=False) if i["channel"] == "GG"]
if not gg or sorted(gg[0]["pickups_allowed"]) != ["Dongdaemoon", "Myungdong"]:
    bad.append("GG 로 나가는 픽업 목록이 다르다")
# 아무것도 안 빼면 예전과 같아야 한다 (제한 없음 = 모든 픽업)
if ri2.pickup_restricted():
    bad.append("아무것도 안 뺐는데 제한이 걸린다")

if bad:
    for b in bad:
        print("  !!", b)
    raise SystemExit(f"!! {len(bad)}건 어긋남 — 필요한 옵션을 못 고른다")
print("전부 통과 — 예약에 없는 언어·픽업지도 고를 수 있다")
