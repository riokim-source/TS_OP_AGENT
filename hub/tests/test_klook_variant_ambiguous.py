# -*- coding: utf-8 -*-
"""
언어 변형이 기본 상품과 번호가 같을 때, 엉뚱한 상품을 열지 않는지 검사.

2026-09-03: 'Osaka Kobe (Night)(한) 6' 을 열라고 했는데 **영어 상품이 6자리
열렸다.** 한국어 상품은 닫힌 채로 남았다.

    Osaka Kobe (Night)     id=216946 activity
    Osaka Kobe (Night)(한) id=216946 activity   <- 글자 하나까지 똑같다

'activity' 방식 상품들이 이 상태다 (19개 이름). 'package' 방식은 언어별로
번호가 따로 있어 정상이다.

    Kyoto & Nara      515200
    Kyoto & Nara(중)  666472   <- 다르다. 정상 동작

번호를 알아내 packages.py 를 고치기 전까지는, 엉뚱한 상품을 여는 대신
열지 않고 사람에게 넘긴다. 잘못 여는 쪽이 훨씬 비싸다.

    python hub/tests/test_klook_variant_ambiguous.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.opens import klook_open as K  # noqa: E402

bad = K.ambiguous_names()
print(f"  구분 불가로 잡힌 이름: {len(bad)}개")

problems = []

# 1) 같은 번호를 쓰는 언어 변형은 반드시 잡혀야 한다
MUST_CATCH = ["Osaka Kobe (Night)(한)", "Sapporo Otaru(한)", "Toyako Niseko(한)",
              "MBC 스튜디오(한)", "Itoshima Marine(한)"]
for n in MUST_CATCH:
    print(f"     {n:28} {'잡음' if n in bad else '!! 못 잡음'}")
    if n not in bad:
        problems.append(f"{n} 을 못 잡는다")

# 2) 번호가 다른 변형과 기본 상품은 건드리면 안 된다
MUST_PASS = ["Kyoto & Nara(중)", "Yufuin Brewery(한)", "Osaka Kobe (Night)",
             "경주", "에버"]
print()
for n in MUST_PASS:
    ok = n not in bad
    print(f"     {n:28} {'통과' if ok else '!! 잘못 막음'}")
    if not ok:
        problems.append(f"{n} 을 잘못 막는다")

# 3) 계획에서 실제로 빠지는가 / 나머지는 그대로 가는가
print()
plan = [
    {"channel": "KLOOK", "mode": "qty", "product": "Osaka Kobe (Night)(한)", "qty": 6},
    {"channel": "KLOOK", "mode": "qty", "product": "Kyoto & Nara(중)", "qty": 4},
    {"channel": "KLOOK", "mode": "qty", "product": "경주", "qty": 10},
]
text = K.plan_to_text(plan)
dropped = K.dropped_for_ambiguity(plan)
print(f"  봇에 넘어가는 것: {text}")
print(f"  빠진 것: {[d['name'] for d in dropped]}")

if "Osaka Kobe (Night)(한)" in text:
    problems.append("구분 안 되는 상품이 그대로 봇에 넘어간다")
for keep in ("Kyoto & Nara(중)", "경주"):
    if keep not in text:
        problems.append(f"{keep} 가 빠졌다 (빠지면 안 된다)")
if not dropped or dropped[0]["name"] != "Osaka Kobe (Night)(한)":
    problems.append("빠진 것을 알려주지 않는다")
if dropped and "216946" not in dropped[0]["why"]:
    problems.append("사유에 번호가 없어 사람이 못 고친다")

print()
if problems:
    for p in problems:
        print("  !!", p)
    raise SystemExit(f"!! {len(problems)}건 어긋남 — 엉뚱한 상품이 열릴 수 있다")
print("전부 통과 — 구분 안 되는 상품은 열지 않고 사람에게 넘긴다")
