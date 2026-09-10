# -*- coding: utf-8 -*-
"""
라스트미닛 메모의 [TPC] 줄 검사.

2026-09-10 에 [TPC] 를 새로 넣었다. 지켜야 할 것:

  1) [CP] 와 **다른 채널이다.** 이름이 비슷해서 합치기 쉬운데, 합치면 한쪽
     규칙이 다른 쪽 재고를 건드린다.
       TPC : 이 봇이 여닫는 Trip.com. tpc_targets.py 의 상품만.
       CP  : 봇 없음. 메모에 수량만 적힌다. 규칙도 예전 그대로.

  2) 줄 순서는 [GG] 다음, [CP] 앞.

  3) 수량은 **GG 와 같다** (특별지역 규칙을 쓰지 않는다).
       Office : q >= 15 -> 절반
       OP     : q >= 20 -> 절반
     일본이라고 전량으로 가지 않는다.

  4) tpc_targets.py 에 없는 상품은 Office/OP 둘 다 건너뛴다.

  5) 언어 표기는 OP 에만 붙인다 (Office 메모 형식은 한 글자도 안 바꾼다).
       한국 상품 -> (중)
       일본 상품 -> (한), 그날 한국어를 받을 수 있을 때만 적는다

  6) 메모의 (한)/(중) 은 사람에게 알려주는 표시다. 봇에게 가는 계획에는
     표시 없는 이름이 간다 (tpc_targets 가 정확히 같은 이름만 받는다).

    python hub/tests/test_tpc_memo.py
"""
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "hub"))

from core.lastmin import calc, constants as C                    # noqa: E402
from core.lastmin.memo import RowInput, build_open_plan, render_memo  # noqa: E402

bad = []


def memo_line(rows, is_op: bool, ch: str) -> str:
    text = render_memo([{"date_label": "09/11", "is_latest": True, "rows": rows}],
                       is_op=is_op)
    for line in text.splitlines():
        if line.startswith(f"[{ch}]:"):
            return line.split(":", 1)[1].strip()
    return "(줄 없음)"


# ── 1) 줄 순서 ──────────────────────────────────────────────────────────
print("  [1] 줄 순서")
print(f"     {C.CHANNELS}")
if "TPC" not in C.CHANNELS:
    bad.append("[TPC] 줄이 없다")
else:
    i, j = C.CHANNELS.index("TPC"), C.CHANNELS.index("CP")
    g = C.CHANNELS.index("GG")
    ok = g < i < j
    print(f"     GG({g}) < TPC({i}) < CP({j}) : {'예' if ok else '!! 아니오'}")
    if not ok:
        bad.append("[TPC] 가 [GG] 와 [CP] 사이가 아니다")

# ── 2) 지정 목록 밖은 건너뛴다 ──────────────────────────────────────────
print()
print("  [2] tpc_targets 에 있는 것만")
for name, want in (("경주", True), ("감천미포", True), ("Mt. Fuji Highlight", True),
                   ("Toyako Niseko", False), ("Kamakura Yokohama", False),
                   ("없는투어", False)):
    got = calc.in_tpc_list(name)
    print(f"     {name:22} {'대상' if got else '건너뜀'}")
    if got != want:
        bad.append(f"'{name}' 판정이 {got} (기대 {want})")

# ── 3) 수량 = GG 규칙 ───────────────────────────────────────────────────
print()
print("  [3] 수량 (GG 와 같아야 한다)")
print(f"     {'상품':20} {'지역':8} {'q':>3}  {'Office':>7} {'OP':>4}   GG(Office/OP)")
CASES = [
    ("경주", "Busan", 25, 12, 12),
    ("경주", "Busan", 15, 7, 0),
    ("경주", "Busan", 14, 0, 0),
    ("Mt. Fuji Highlight", "Tokyo", 30, 15, 15),
    ("Mt. Fuji Highlight", "Tokyo", 19, 9, 0),
    ("Mt. Fuji Highlight", "Tokyo", 1, 0, 0),     # 일본이라고 전량 아님
]
for name, area, q, w_off, w_op in CASES:
    off = calc.tpc_share(name, q, False)
    op = calc.tpc_share(name, q, True)
    # 같은 조건에서 GG 가 받는 몫
    gg_off = calc.distribute(area, name, q, False)["GG"]
    gg_op = calc.distribute(area, name, q, True)["GG"]
    def _n(lst):
        return int(lst[0].rsplit(" ", 1)[1]) if lst else 0
    mark = "" if (off, op) == (w_off, w_op) else f"  !! 기대 {w_off}/{w_op}"
    print(f"     {name:20} {area:8} {q:3}  {off:7} {op:4}   {_n(gg_off)}/{_n(gg_op)}{mark}")
    if (off, op) != (w_off, w_op):
        bad.append(f"{name} {q}: {off}/{op} (기대 {w_off}/{w_op})")
    if off != _n(gg_off) or op != _n(gg_op):
        bad.append(f"{name} {q}: GG 와 다르다 (TPC {off}/{op} vs GG {_n(gg_off)}/{_n(gg_op)})")

# ── 4) 언어 표기 ────────────────────────────────────────────────────────
print()
print("  [4] 언어 표기 (Office 엔 안 붙는다)")
KR = RowInput(area="Busan", product="경주", qty=25)
JP = RowInput(area="Tokyo", product="Mt. Fuji Highlight", qty=30)
for label, rows, is_op, want in (
        ("Office 한국", [KR], False, "경주 12"),
        ("OP     한국", [KR], True, "경주(중) 12"),
        ("Office 일본", [JP], False, "Mt. Fuji Highlight 15"),
        ("OP     일본", [JP], True, "Mt. Fuji Highlight(한) 15")):
    got = memo_line(rows, is_op, "TPC")
    mark = "" if got == want else f"   !! 기대 '{want}'"
    print(f"     {label} -> [TPC]: {got}{mark}")
    if got != want:
        bad.append(f"{label}: '{got}' (기대 '{want}')")

# ── 5) OP 일본은 한국어가 되는 날만 ─────────────────────────────────────
print()
print("  [5] OP 일본 — 한국어를 못 받는 날은 안 적는다")
LANGS = ["english", "korean", "chinese", "japanese"]
for label, sel, want in (
        ("제한 없음", [], "Mt. Fuji Highlight(한) 15"),
        ("한국어 포함", ["korean", "english"], "Mt. Fuji Highlight(한) 15"),
        ("한국어 빠짐", ["english", "chinese"], "")):
    r = RowInput(area="Tokyo", product="Mt. Fuji Highlight", qty=30,
                 languages_all=LANGS if sel else [], languages_sel=sel)
    got = memo_line([r], True, "TPC")
    mark = "" if got == want else f"   !! 기대 '{want}'"
    print(f"     {label:12} -> [TPC]: '{got}'{mark}")
    if got != want:
        bad.append(f"OP 일본 {label}: '{got}' (기대 '{want}')")
# 한국 상품은 언어 제한과 무관하다
r = RowInput(area="Busan", product="경주", qty=25,
             languages_all=LANGS, languages_sel=["english", "chinese"])
got = memo_line([r], True, "TPC")
print(f"     한국 상품(한국어 빠짐) -> [TPC]: '{got}'")
if got != "경주(중) 12":
    bad.append(f"한국 상품이 언어 제한에 걸렸다: '{got}'")

# ── 6) 계획에는 표시 없는 이름이 간다 ───────────────────────────────────
print()
print("  [6] 봇에게 가는 이름")
plan = [p for p in build_open_plan([KR, JP], is_op=True) if p["channel"] == "TPC"]
for p in plan:
    print(f"     {p['product']:24} qty={p['qty']}")
names = sorted(p["product"] for p in plan)
if names != ["Mt. Fuji Highlight", "경주"]:
    bad.append(f"계획의 이름이 {names} (표시가 붙으면 봇이 못 찾는다)")

# ── 7) CP 는 건드리지 않았다 ────────────────────────────────────────────
print()
print("  [7] CP 는 예전 그대로")
cp_off = memo_line([JP], False, "CP")
cp_op = memo_line([JP], True, "CP")
print(f"     Office [CP]: {cp_off}")
print(f"     OP     [CP]: {cp_op}")
if cp_off != "Mt. Fuji Highlight 30":      # 특별지역 Office: q>=1 전량
    bad.append(f"CP Office 규칙이 바뀌었다: '{cp_off}'")
if cp_op != "Mt. Fuji Highlight 15":       # 특별지역 OP: q>=20 반반
    bad.append(f"CP OP 규칙이 바뀌었다: '{cp_op}'")

from core import opens  # noqa: E402
if "CP" in opens.IMPLEMENTED:
    bad.append("CP 에 오픈 봇이 붙어 있다 — CP 는 봇이 없다")
if "TPC" not in opens.IMPLEMENTED:
    bad.append("TPC 에 오픈 봇이 안 붙어 있다")
print(f"     오픈 봇: TPC {'있음' if 'TPC' in opens.IMPLEMENTED else '없음'} / "
      f"CP {'있음' if 'CP' in opens.IMPLEMENTED else '없음'}")

print()
if bad:
    for b in bad:
        print("  !!", b)
    raise SystemExit(f"!! {len(bad)}건 어긋남")
print("전부 통과 — [TPC] 는 GG 규칙 · 지정 목록만 · OP 에만 언어 표기, CP 는 그대로")
