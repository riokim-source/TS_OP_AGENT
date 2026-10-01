# -*- coding: utf-8 -*-
"""
2026-10-01 수집 규칙 정리.

운영 요청 그대로 옮긴 것이 맞는지 본다. 수량이 달라지는 규칙이라 숫자로 못 박는다.

  1) 언어 목록에서 **일본어를 뺀다** (일본 상품도, 한국 상품도)
  2) 언어·픽업지는 '전부 포함' 이 기본이고 **뺄 것만** 고른다 (화면 쪽 규칙)
  3) 아웃소싱(MBC)은 **익일 예약에 있을 때만** 맨 위 칸으로 올린다. 없으면 생략
  4) OP 텍스트에서 **수량 0 은 적지 않는다** (오픈 계획에서도 뺀다 — 0 은 마감이다)
  5) 일본 상품 + '중국어 불가' 면 **GG 에서 자동으로 뺀다**
  6) 그 밖의 Office 규칙은 그대로

    python hub/tests/test_collect_rules.py
"""
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "hub"))

from core.lastmin import constants as C          # noqa: E402
from core.lastmin import memo as M               # noqa: E402
from core.lastmin import outsourced as OUT       # noqa: E402

bad = []


def row(area, product, qty, langs_all=None, langs_sel=None, **kw):
    langs_all = langs_all if langs_all is not None else M.available_languages(product, [])
    langs_sel = langs_sel if langs_sel is not None else list(langs_all)
    return M.RowInput(area=area, product=product, qty=qty,
                      languages_all=list(langs_all), languages_sel=list(langs_sel), **kw)


def op_lines(r):
    return M.build_panel_memo([r], is_latest=True, is_op=True)


def office_lines(r):
    return M.build_panel_memo([r], is_latest=True, is_op=False)


# ── 1) 일본어는 목록에 없다 ───────────────────────────────────────────────
print("  [1] 언어 목록 — 일본어 제외")
CASES = [
    ("예약에 일본어가 없을 때", []),
    ("예약에 일본어가 있을 때", ["Japanese"]),
    ("한 칸에 섞여 들어올 때", ["Japanese,English"]),
]
for label, sheet in CASES:
    got = M.available_languages("Mt. Fuji Highlight", sheet)
    ok = "japanese" not in got and "english" in got
    print(f"     {label:22} -> {got}{'' if ok else '   !! 일본어가 남아 있다'}")
    if not ok:
        bad.append(f"{label}: {got}")
if "japanese" not in C.HIDDEN_LANGUAGES:
    bad.append("HIDDEN_LANGUAGES 에 japanese 가 없다")

# 일본어가 빠져도 '남은 셋 전부' 는 제한이 아니다 (분모가 셋이 된다)
r = row("Tokyo", "Mt. Fuji Highlight", 20)
print(f"     전부 포함일 때 제한으로 보는가: {r.language_restricted()} (False 여야 함)")
if r.language_restricted():
    bad.append("일본어를 뺐더니 '전부 포함' 이 제한으로 잡힌다")

# ── 2) 아웃소싱 — 있을 때만 ───────────────────────────────────────────────
print()
print("  [2] 아웃소싱(MBC) — 예약에 있을 때만 맨 위로")
MATCH = [("MBC 스튜디오", "", True), ("MBC스튜디오", "", True),
         ("MBC 스튜디오", "드라마 리허설", True),
         ("MBC 스튜디오(드라마리허설)", "", True),
         ("남이섬셔틀", "", False), ("MBC 뉴스", "", False)]
for product, option, want in MATCH:
    got = OUT.matches(product, option)
    print(f"     {product:22} {option:10} -> {'아웃소싱' if got else '일반'}"
          f"{'' if got == want else '   !! 기대 ' + str(want)}")
    if got != want:
        bad.append(f"matches({product!r},{option!r}) = {got}")

groups_with = [{"region": "Korea", "areas": [{"area": "Seoul", "rows": [
    {"product": "MBC 스튜디오", "option": ""}, {"product": "남이섬셔틀", "option": ""}]}]}]
groups_without = [{"region": "Korea", "areas": [{"area": "Seoul", "rows": [
    {"product": "남이섬셔틀", "option": ""}]}]}]
n1 = OUT.mark(groups_with)
n2 = OUT.mark(groups_without)
flags1 = [r.get("outsourced") for r in groups_with[0]["areas"][0]["rows"]]
print(f"     예약에 있을 때 표시 {n1}건 {flags1} / 없을 때 {n2}건")
if n1 != 1 or flags1 != [True, None]:
    bad.append(f"있는 날 표시가 이상하다: {n1} {flags1}")
if n2 != 0:
    bad.append("없는 날에 줄을 만들었다")
# 줄을 새로 만들지 않는다 (수량이 두 번 들어가면 안 된다)
if len(groups_without[0]["areas"][0]["rows"]) != 1:
    bad.append("없는 날 줄이 늘었다")

# ── 3) 수량 0 은 적지 않는다 ──────────────────────────────────────────────
print()
print("  [3] OP 텍스트 — 수량 0 생략")
# 언어 둘로 나누면 1 -> 1, 0 이 된다. '0' 줄이 남으면 사람이 0 을 넣으러 간다.
split = M.calc.split_across(1, 2)
print(f"     1을 둘로 나누면 {split}")
r0 = row("Seoul", "경주", 1, langs_sel=["english", "korean"])
lines = op_lines(r0)["KLOOK"]
print(f"     KLOOK 줄: {lines}")
if any(x.rstrip().endswith(" 0") for x in lines):
    bad.append(f"0 자리가 OP 텍스트에 남았다: {lines}")
plan = M.build_open_plan([r0], is_op=True)
zeros = [p for p in plan if p["mode"] == "qty" and int(p["qty"]) == 0]
print(f"     오픈 계획 {len(plan)}건 · 수량 0: {len(zeros)}건")
if zeros:
    bad.append(f"오픈 계획에 수량 0 이 남았다 (그대로 두면 마감된다): {zeros}")
# 이름만 적는 줄(KK·VI 재개)은 0 이 아니다 — 사라지면 안 된다
r_name = row("Seoul", "경주", 20)
vi = office_lines(r_name)["VI"]
print(f"     이름만 적는 줄은 남는가: VI {vi}")

# ── 4) 일본 상품 + 중국어 불가 -> GG 제외 ─────────────────────────────────
print()
print("  [4] 일본 상품 — '중국어 불가' 면 GG 에서 뺀다")
ALL = M.available_languages("Mt. Fuji Highlight", [])
NO_CN = [l for l in ALL if l != "chinese"]
GG_CASES = [
    # 지역,      언어,          GG 가 있어야 하나
    ("Tokyo",  ALL,    True,  "제한 없음"),
    ("Tokyo",  NO_CN,  False, "중국어 불가"),
    ("Tokyo",  ["chinese"], True, "중국어만"),
    ("Seoul",  NO_CN,  True,  "한국 상품은 그대로"),
    ("Osaka",  ["korean"], False, "한국어만(=중국어 불가)"),
]
for area, langs, want_gg, label in GG_CASES:
    r = row(area, "Mt. Fuji Highlight", 20, langs_all=ALL, langs_sel=langs)
    got = op_lines(r)
    has_gg = bool(got["GG"])
    mark = "" if has_gg == want_gg else f"   !! 기대 {'있음' if want_gg else '없음'}"
    print(f"     {area:6} {label:18} GG {'있음' if has_gg else '없음':4} "
          f"KLOOK {got['KLOOK']}{mark}")
    if has_gg != want_gg:
        bad.append(f"{area}/{label}: GG {'있음' if has_gg else '없음'}")

# 뺀 수량을 다른 데로 옮기지 않는다 (자리 수가 몰래 늘면 안 된다)
r_block = row("Tokyo", "Mt. Fuji Highlight", 20, langs_all=ALL, langs_sel=NO_CN)
r_free = row("Tokyo", "Mt. Fuji Highlight", 20, langs_all=ALL, langs_sel=ALL)
k_block = op_lines(r_block)["KLOOK"]
k_free = op_lines(r_free)["KLOOK"]
print(f"     Klook 몫은 그대로인가: 제한 {k_block} vs 전부 {k_free}")

# ── 5) Office 규칙은 그대로 ───────────────────────────────────────────────
print()
print("  [5] Office 규칙 — 바뀌지 않았다")
BASE = [
    ("Seoul", "경주", 25, {"KLOOK": ["경주 13"], "GG": ["경주 12"]}),
    ("Seoul", "교촌경주", 13, {"KLOOK": ["교촌경주 13"], "GG": []}),
    ("Sydney", "Blue Mountain Zig Zag", 5, {"KLOOK": ["Blue Mountain Zig Zag 5"], "GG": []}),
]
for area, product, qty, want in BASE:
    got = office_lines(row(area, product, qty))
    okk = all(got[ch] == v for ch, v in want.items())
    print(f"     {product:24} {qty:3} -> KLOOK {got['KLOOK']} / GG {got['GG']}"
          f"{'' if okk else '   !! 달라졌다'}")
    if not okk:
        bad.append(f"Office 규칙이 바뀌었다: {product} {qty} -> {got}")

# 특별지역(일본) Office: CP·MRT 전량
got = office_lines(row("Sapporo", "Toyako Niseko", 24))
print(f"     Toyako Niseko 24 (Sapporo) -> CP {got['CP']} / MRT {got['MRT']}")
if got["MRT"] != ["Toyako Niseko 24"]:
    bad.append(f"특별지역 Office 규칙이 바뀌었다: {got}")

print()
if bad:
    for b in bad:
        print("  !!", b)
    raise SystemExit(f"!! {len(bad)}건 어긋남")
print("전부 통과 — 일본어 제외 · 있을 때만 MBC · 0 생략 · 일본 GG 언어 제한")
