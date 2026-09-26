# -*- coding: utf-8 -*-
"""
지역 옆 빈칸에 적은 글을 읽는 검사.

운영자가 매일 쓰는 글을 그대로 넣어서, 수량·언어·픽업 제한이 제대로 나오는지 본다.
아래 예시는 2026-09-27 에 받은 실제 표기다.

    감천미포 4, 감천미포 Early Bird 4, 경주 4, 경주 Express 6, 교촌경주 10
    Mt.Fuji Signature 9(중국어불가)
    Amanohashidate 22 (한, 영)
    Yufuin Brewery 10(영어한국어10 중국어0)
    Wollongong Kiama (En Ko and Ch): 17
    알남아 9(홍대제외)

지켜야 할 것
  1) 비슷한 이름을 맞춰 주지 않는다 ('경주' 와 '교촌경주' 는 다른 상품이다)
  2) 못 읽은 줄은 조용히 버리지 않고 problems 로 올린다
  3) 괄호 안 쉼표로 줄을 쪼개지 않는다 ('(한, 영)')
  4) 빼라고 한 언어·픽업지가 후보에 없으면 그것도 알린다

    python hub/tests/test_quickfill.py
"""
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "hub"))

from core.lastmin import quickfill as Q      # noqa: E402

bad = []
KO = ["english", "korean", "chinese"]
PICKS = ["홍대입구역", "명동", "동대문역사문화공원"]


def row(area, product, option="", langs=None, picks=None):
    split = bool(option)
    return {"area": area, "product": product, "option": option or "",
            "key": f"{area}|{product}|{option or ''}",
            "option_split": split,
            "display": f"{product}({option})" if split else product,
            "languages": list(KO if langs is None else langs),
            "pickups": list(PICKS if picks is None else picks)}


# 실제 파일의 감천미포는 옵션이 '' + 'SIGHTSEEING' 으로 나뉜다.
# '감천미포 4' 는 옵션 없는 줄, '감천미포 Early Bird 4' 는 그 옵션 줄이다.
BUSAN = [
    row("Busan", "감천미포", "(옵션없음)"),
    row("Busan", "감천미포", "Early Bird"),
    row("Busan", "경주"),
    row("Busan", "경주 Express"),
    row("Busan", "교촌경주"),
    row("Busan", "특공대"),
    row("Busan", "선셋캡슐 East"),
]
SEOUL = [row("Seoul", "알남아")]
JAPAN = [
    row("Tokyo", "Mt. Fuji Signature"),
    row("Osaka", "Amanohashidate"),
    row("Osaka", "Arashiyama & Nishiki"),
    row("Fukuoka", "Yufuin Brewery"),
    row("Fukuoka", "Fukuoka Foodie"),
    row("Sapporo", "Biei Furano"),
    row("Sapporo", "Toyako Niseko"),
    row("Sapporo", "Autumn Shikotsu"),
]
SYDNEY = [row("Sydney", "Wollongong Kiama")]


def n(assign, rows, product, option=""):
    key = f"{rows[0]['area']}|{product}|{option}"
    return assign.get(key)


# ── 1) 한 줄에 쉼표로 이어 적기 ─────────────────────────────────────────
print("  [1] 부산 — 쉼표로 이어 적은 7개")
TEXT = ("감천미포 4, 감천미포 Early Bird 4, 경주 4, 경주 Express 6, "
        "교촌경주 10, 특공대 8, 선셋캡슐 East 6")
assign, probs = Q.resolve(Q.parse(TEXT), BUSAN)
want = {"감천미포|(옵션없음)": 4, "감천미포|Early Bird": 4, "경주|": 4,
        "경주 Express|": 6, "교촌경주|": 10, "특공대|": 8, "선셋캡슐 East|": 6}
for tail, q in want.items():
    got = assign.get(f"Busan|{tail}")
    ok = got and got["qty"] == q
    print(f"     {tail:24} {got['qty'] if got else '(없음)'}{'' if ok else f'  !! 기대 {q}'}")
    if not ok:
        bad.append(f"{tail} 수량이 {got['qty'] if got else '없음'} (기대 {q})")
if probs:
    print(f"     !! 문제: {probs}")
    bad.append(f"멀쩡한 줄에서 문제가 났다: {probs}")
# '감천미포' 가 'Early Bird' 줄에 들어가면 안 된다
if assign.get("Busan|감천미포|(옵션없음)", {}).get("qty") != 4:
    bad.append("옵션 있는 상품에서 기본 줄을 못 찾았다")

# ── 2) 언어 제한 ────────────────────────────────────────────────────────
print()
print("  [2] 언어 표기")
CASES = [
    ("Mt.Fuji Signature 9(중국어불가)", "Mt. Fuji Signature", 9, ["english", "korean"]),
    ("Amanohashidate 22 (한, 영)", "Amanohashidate", 22, ["english", "korean"]),
    ("Arashiyama & Nishiki 5 (한국어)", "Arashiyama & Nishiki", 5, ["korean"]),
    ("Yufuin Brewery 10(영어한국어10 중국어0)", "Yufuin Brewery", 10, ["english", "korean"]),
    ("Fukuoka Foodie 7(영어한국어7 중국어0)", "Fukuoka Foodie", 7, ["english", "korean"]),
    ("Biei Furano 2(중국어 불가)", "Biei Furano", 2, ["english", "korean"]),
    ("Toyako Niseko 2", "Toyako Niseko", 2, KO),
    ("Autumn Shikotsu 4(중국어 불가)", "Autumn Shikotsu", 4, ["english", "korean"]),
]
for text, product, qty, langs in CASES:
    rows = [r for r in JAPAN if r["product"] == product]
    a, p = Q.resolve(Q.parse(text), rows)
    got = a.get(rows[0]["key"])
    ok = got and got["qty"] == qty and got["lang"] == langs
    show = f"{got['qty']} / {got['lang']}" if got else "(못 읽음)"
    print(f"     {text:38} -> {show}{'' if ok else f'   !! 기대 {qty} / {langs}'}")
    if not ok:
        bad.append(f"'{text}' -> {show} (기대 {qty} / {langs})")
    if p:
        bad.append(f"'{text}' 에서 문제: {p}")

# 호주 — 영문 표기 + 콜론 뒤 수량
a, p = Q.resolve(Q.parse("Wollongong Kiama (En Ko and Ch): 17"), SYDNEY)
got = a.get(SYDNEY[0]["key"])
ok = got and got["qty"] == 17 and got["lang"] == KO
print(f"     {'Wollongong Kiama (En Ko and Ch): 17':38} -> "
      f"{(str(got['qty']) + ' / ' + str(got['lang'])) if got else '(못 읽음)'}")
if not ok:
    bad.append(f"Wollongong Kiama -> {got} (기대 17 / {KO})")
if p:
    bad.append(f"Wollongong Kiama 에서 문제: {p}")

# ── 3) 픽업 제외 ────────────────────────────────────────────────────────
print()
print("  [3] 픽업 제외")
a, p = Q.resolve(Q.parse("알남아 9(홍대제외)"), SEOUL)
got = a.get(SEOUL[0]["key"])
print(f"     알남아 9(홍대제외) -> {got['qty'] if got else '?'} / {got['pick'] if got else '?'}")
if not got or got["qty"] != 9:
    bad.append("알남아 수량이 안 들어갔다")
elif any("홍대" in x for x in got["pick"]):
    bad.append(f"홍대를 못 뺐다: {got['pick']}")
elif len(got["pick"]) != len(PICKS) - 1:
    bad.append(f"홍대 말고 다른 것까지 뺐다: {got['pick']}")
if p:
    bad.append(f"픽업 제외에서 문제: {p}")

# 후보에 없는 픽업지를 빼라고 하면 반드시 알린다 (모르면 그 자리가 열린 채 남는다)
a2, p2 = Q.resolve(Q.parse("알남아 9(서면제외)"), SEOUL)
print(f"     알남아 9(서면제외) -> 알림 {p2}")
if not any("서면" in x for x in p2):
    bad.append("없는 픽업지를 빼라고 했는데 아무 말이 없다")

# ── 4) 이름을 못 찾으면 알린다 ──────────────────────────────────────────
print()
print("  [4] 모르는 이름 / 수량 없음")
a, p = Q.resolve(Q.parse("없는투어 5, 경주"), BUSAN)
print(f"     {p}")
if not any("없는투어" in x for x in p):
    bad.append("모르는 이름을 조용히 넘겼다")
if not any("수량" in x for x in p):
    bad.append("수량 없는 줄을 조용히 넘겼다")
if a:
    bad.append(f"못 읽은 줄인데 수량이 들어갔다: {a}")

# 부분일치 금지 — '경주' 가 '교촌경주' 에 들어가면 안 된다
a, _ = Q.resolve(Q.parse("경주 4"), BUSAN)
print(f"     '경주 4' 가 건드린 줄: {list(a)}")
if list(a) != ["Busan|경주|"]:
    bad.append(f"'경주' 가 {list(a)} 를 건드렸다 (교촌경주/경주 Express 는 다른 상품이다)")

# 이름 붙은 옵션만 여럿인데 상품명만 적었으면 — 고르지 말고 물어본다
TWO = [row("Busan", "지산", "오전"), row("Busan", "지산", "오후")]
a, p = Q.resolve(Q.parse("지산 6"), TWO)
print(f"     '지산 6' (옵션 2개) -> {p}")
if a or not any("옵션" in x for x in p):
    bad.append(f"옵션이 둘인데 임의로 골랐다: {a} {p}")
a, _ = Q.resolve(Q.parse("지산 오전 6"), TWO)
if list(a) != ["Busan|지산|오전"]:
    bad.append(f"옵션까지 적었는데 못 찾았다: {list(a)}")

# 다른 지역 상품을 적으면 '없다' 고 말한다 (조용히 넣으면 엉뚱한 곳이 열린다)
a, p = Q.resolve(Q.parse("Toyako Niseko 2"), BUSAN)
if not p or a:
    bad.append("다른 지역 상품을 그냥 받아들였다")

# ── 5) 괄호 안 쉼표로 줄을 쪼개지 않는다 ────────────────────────────────
print()
print("  [5] 줄 나누기")
got = Q.split_entries("Amanohashidate 22 (한, 영), 경주 4")
print(f"     {got}")
if got != ["Amanohashidate 22 (한, 영)", "경주 4"]:
    bad.append(f"괄호 속 쉼표에서 줄이 쪼개졌다: {got}")
got2 = Q.split_entries("감천미포 4\n경주 4, 교촌경주 10")
if got2 != ["감천미포 4", "경주 4", "교촌경주 10"]:
    bad.append(f"줄바꿈/쉼표 섞인 것을 못 나눴다: {got2}")

print()
if bad:
    for b in bad:
        print("  !!", b)
    raise SystemExit(f"!! {len(bad)}건 어긋남")
print("전부 통과 — 적은 그대로 수량·언어·픽업이 들어가고, 못 읽은 것은 알린다")
