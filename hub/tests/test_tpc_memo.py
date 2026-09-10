# -*- coding: utf-8 -*-
"""
라스트미닛 메모의 [TPC] 줄 검사.

2026-09-10 에 [TPC] 를 새로 넣었다. 지켜야 할 것:

  1) [CP] 와 **다른 채널이다.** 이름이 비슷해서 합치기 쉬운데, 합치면 한쪽
     규칙이 다른 쪽 재고를 건드린다.
       TPC : 이 봇이 여닫는 Trip.com. tpc_targets.py 의 상품만.
       CP  : 봇 없음. 메모에 수량만 적힌다. 규칙도 예전 그대로.

  2) 줄 순서는 [GG] 다음, [CP] 앞.

  3) 수량 (2026-09-11 에 CP 와 서로 바꾼 것)
       TPC : Office q >= 1  -> 전량 / OP q >= 20 -> 절반   (지역 무관)
       CP  : Office q >= 15 -> 절반 / OP q >= 20 -> 절반   (일본만)
       MRT : 예전 그대로 — 이 교체에 안 딸려갔다

  4) tpc_targets.py 에 없는 상품은 Office/OP 둘 다 건너뛴다.

  5) 언어 표기는 OP 에만 붙인다 (Office 메모 형식은 한 글자도 안 바꾼다).
       한국 상품 -> (중)   Trip.com 에서 파는 것은 중국어 가이드 패키지다
       일본 상품 -> (한)   한국어 가이드 패키지를 연다

  6) OP 에서 그 언어가 빠진 날은 **수량이 넘어도 생략한다.**
       한국 상품에 '중국어 불가' / 일본 상품에 '한국어 불가' / '영어만'
     열 것이 없는데 적어 두면 사람이 찾아 헤매게 된다.
     Office 는 언어를 안 보므로 그대로 적는다.

  7) 메모의 (한)/(중) 은 사람에게 알려주는 표시다. 봇에게 가는 계획에는
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

# ── 3) 수량 (CP 와 서로 바꾼 규칙) ──────────────────────────────────────
print()
print("  [3] 수량 — TPC 는 전량/절반, CP 는 GG 와 같다")
print(f"     {'지역':7} {'q':>3} | {'TPC':>12} | {'CP':>10} | {'MRT':>10}")
CASES = [
    # 지역,    q,   TPC(Office/OP),  CP(Office/OP),  MRT(Office/OP)
    ("Tokyo", 1, (1, 0), (0, 0), (1, 0)),
    ("Tokyo", 14, (14, 0), (0, 0), (14, 14)),
    ("Tokyo", 15, (15, 0), (7, 0), (15, 15)),
    ("Tokyo", 19, (19, 0), (9, 0), (19, 19)),
    ("Tokyo", 20, (20, 10), (10, 10), (20, 10)),
    ("Tokyo", 30, (30, 15), (15, 15), (30, 15)),
    # 한국은 CP·MRT 가 아예 없다 (특별지역이 아니다). TPC 는 지역을 안 가린다.
    ("Busan", 1, (1, 0), (0, 0), (0, 0)),
    ("Busan", 20, (20, 10), (0, 0), (0, 0)),
    ("Busan", 30, (30, 15), (0, 0), (0, 0)),
]
NAME = {"Tokyo": "Mt. Fuji Highlight", "Busan": "경주"}


def _n(lst):
    return int(lst[0].rsplit(" ", 1)[1]) if lst else 0


for area, q, w_tpc, w_cp, w_mrt in CASES:
    name = NAME[area]
    off = calc.distribute(area, name, q, False)
    op = calc.distribute(area, name, q, True)
    got = {ch: (_n(off[ch]), _n(op[ch])) for ch in ("TPC", "CP", "MRT")}
    want = {"TPC": w_tpc, "CP": w_cp, "MRT": w_mrt}
    marks = "".join("" if got[c] == want[c] else f"  !! {c} 기대 {want[c]}"
                    for c in ("TPC", "CP", "MRT"))
    print(f"     {area:7} {q:3} | {str(got['TPC']):>12} | {str(got['CP']):>10}"
          f" | {str(got['MRT']):>10}{marks}")
    for c in ("TPC", "CP", "MRT"):
        if got[c] != want[c]:
            bad.append(f"{area} {q} {c}: {got[c]} (기대 {want[c]})")

# CP 는 GG 와 같은 몫이어야 한다 (일본에서)
print()
print("  [3-2] CP 가 GG 와 같은 몫인가 (일본)")
for q in (15, 19, 20, 30):
    d_off = calc.distribute("Tokyo", "Mt. Fuji Highlight", q, False)
    d_op = calc.distribute("Tokyo", "Mt. Fuji Highlight", q, True)
    same = (_n(d_off["CP"]), _n(d_op["CP"])) == (_n(d_off["GG"]), _n(d_op["GG"]))
    print(f"     q={q:3}  CP {_n(d_off['CP'])}/{_n(d_op['CP'])}"
          f"  GG {_n(d_off['GG'])}/{_n(d_op['GG'])}  {'같음' if same else '!! 다름'}")
    if not same:
        bad.append(f"CP 가 GG 와 다르다 (q={q})")

# ── 4) 언어 표기 ────────────────────────────────────────────────────────
print()
print("  [4] 언어 표기 (Office 엔 안 붙는다)")
KR = RowInput(area="Busan", product="경주", qty=25)
JP = RowInput(area="Tokyo", product="Mt. Fuji Highlight", qty=30)
for label, rows, is_op, want in (
        ("Office 한국", [KR], False, "경주 25"),
        ("OP     한국", [KR], True, "경주(중) 12"),
        ("Office 일본", [JP], False, "Mt. Fuji Highlight 30"),
        ("OP     일본", [JP], True, "Mt. Fuji Highlight(한) 15")):
    got = memo_line(rows, is_op, "TPC")
    mark = "" if got == want else f"   !! 기대 '{want}'"
    print(f"     {label} -> [TPC]: {got}{mark}")
    if got != want:
        bad.append(f"{label}: '{got}' (기대 '{want}')")

# ── 5) 언어가 빠지면 수량이 넘어도 뺀다 ────────────────────────────────
print()
print("  [5] OP — 그 지역이 여는 언어가 빠지면 생략")
print("     (한국=중국어 / 일본=한국어. 수량은 셋 다 임계값을 넘는다)")
LANGS = ["english", "korean", "chinese", "japanese"]
CASES5 = [
    # 지역,     상품,                 고른 언어,                  기대
    ("Busan", "경주", 25, [], "경주(중) 12"),
    ("Busan", "경주", 25, ["chinese", "english"], "경주(중) 12"),
    ("Busan", "경주", 25, ["korean", "english"], ""),          # 중국어 불가
    ("Busan", "경주", 25, ["english"], ""),                    # 영어만
    ("Tokyo", "Mt. Fuji Highlight", 30, [], "Mt. Fuji Highlight(한) 15"),
    ("Tokyo", "Mt. Fuji Highlight", 30, ["korean", "chinese"],
     "Mt. Fuji Highlight(한) 15"),
    ("Tokyo", "Mt. Fuji Highlight", 30, ["chinese", "english"], ""),   # 한국어 불가
    ("Tokyo", "Mt. Fuji Highlight", 30, ["english"], ""),              # 영어만
]
for area, name, q, sel, want in CASES5:
    r = RowInput(area=area, product=name, qty=q,
                 languages_all=LANGS if sel else [], languages_sel=sel)
    got = memo_line([r], True, "TPC")
    mark = "" if got == want else f"   !! 기대 '{want}'"
    label = ", ".join(sel) if sel else "(제한 없음)"
    print(f"     {area:6} {label:24} -> '{got}'{mark}")
    if got != want:
        bad.append(f"{area} {label}: '{got}' (기대 '{want}')")

# 빠지는 날이라도 Office 는 그대로 적는다 (Office 는 언어를 안 본다)
r = RowInput(area="Busan", product="경주", qty=25,
             languages_all=LANGS, languages_sel=["english"])
off = memo_line([r], False, "TPC")
print(f"     Office (영어만)                     -> '{off}'")
if off != "경주 25":
    bad.append(f"Office 가 언어 제한에 걸렸다: '{off}'")

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
print("  [7] CP / MRT")
cp_off = memo_line([JP], False, "CP")
cp_op = memo_line([JP], True, "CP")
print(f"     Office [CP]: {cp_off}")
print(f"     OP     [CP]: {cp_op}")
# CP 는 이제 GG 와 같은 규칙이다 (2026-09-11 교체)
if cp_off != "Mt. Fuji Highlight 15":      # Office 30 -> 절반
    bad.append(f"CP Office 규칙이 다르다: '{cp_off}'")
if cp_op != "Mt. Fuji Highlight 15":       # OP 30 -> 절반
    bad.append(f"CP OP 규칙이 다르다: '{cp_op}'")
mrt_off = memo_line([JP], False, "MRT")
print(f"     Office [MRT]: {mrt_off}   (교체에 안 딸려갔는지)")
if mrt_off != "Mt. Fuji Highlight 30":
    bad.append(f"MRT 가 교체에 딸려갔다: '{mrt_off}'")

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
print("전부 통과 — TPC 전량/절반 · CP 는 GG 와 같음 · MRT 그대로 · 지정 목록/언어 조건은 TPC 에만")
