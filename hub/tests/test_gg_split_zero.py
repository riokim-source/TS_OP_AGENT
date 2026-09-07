# -*- coding: utf-8 -*-
"""
GG: 몫이 0인 픽업지를 '스킵' 으로 보고하지 않는지 검사.

2026-09-07: Seasonal BTS 1명이 **정상으로 다 열렸는데** 화면에는 스킵 1건이
떴다. 실행 기록은 스킵을 '실패/스킵' 으로 세기 때문에, 멀쩡한 건이 문제처럼
보인다.

    [분할] Seasonal BTS 1명 → 픽업 2곳: Myeongdong=1, Dongdaemun=0
      성공 | ... Meet at Myeongdong  | 1/120 → 1/2 (Block 해제됨)
      스킵 | ... Meet at Dongdaemun  | 분할 결과 0명      <- 이게 잘못

1명을 두 곳에 나누면 [1, 0] 이 된다. 뒤쪽이 0인 것은 '못 한 것' 이 아니라
앞쪽에서 이미 다 나눠 가진 것이다. 요청한 1명은 전부 열렸다.

⚠️ 수량 0(=마감)은 다른 경우다. 그건 Block 을 걸어야 하므로 빠지면 안 된다.

    python hub/tests/test_gg_split_zero.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "OTA Close"))

import gg_open  # noqa: E402

bad = []

print("  [나눗셈 자체]")
for total, n, want in ((1, 2, [1, 0]), (5, 2, [3, 2]), (2, 3, [1, 1, 0])):
    got = gg_open.split_across(total, n)
    print(f"     {total}명 → {n}곳 = {got}")
    if got != want:
        bad.append(f"split_across({total},{n}) = {got}, 기대 {want}")

# 오늘 상황을 그대로: 1명, 픽업 2곳
SCREEN = ["Seasonal BTS - Gangneung Special Shared Tour, Meet at Myeongdong",
          "Seasonal BTS - Gangneung Special Shared Tour, Meet at Dongdaemun"]
cards = [{"title": t, "testid": f"agenda-item-{i}-x", "count": "1 / 120",
          "status": "", "blocked": True, "canEdit": True}
         for i, t in enumerate(SCREEN)]


def build_plan(qty: int) -> dict:
    """gg_open 의 '매칭 + 분할' 부분과 같은 규칙으로 계획을 만든다."""
    it = {"tour": "Seasonal BTS", "qty": qty, "pickups": []}
    pairs, _miss = gg_open.match_targets(cards, [it])
    hit = [c for c, _q, _i in pairs]
    shares = gg_open.split_across(int(qty), len(hit))
    plan = {}
    for c, share in zip(hit, shares):
        if share <= 0 and int(qty) > 0:
            continue                      # 고친 규칙
        plan[c["testid"]] = (it, share, c)
    return plan


print()
print("  [1명 → 픽업 2곳 (오늘 상황)]")
plan = build_plan(1)
shares = sorted(s for _i, s, _c in plan.values())
print(f"     실제로 다룰 옵션 {len(plan)}개, 몫 {shares}")
print(f"     열리는 총 인원 {sum(shares)}명  (요청 1명)")
if len(plan) != 1:
    bad.append(f"옵션 {len(plan)}개를 다룬다 (1개여야 한다 — 0명짜리는 빼야 함)")
if sum(shares) != 1:
    bad.append(f"열리는 인원이 {sum(shares)}명 (요청 1명과 달라졌다)")

print()
print("  [5명 → 픽업 2곳 (둘 다 몫이 있는 경우)]")
plan5 = build_plan(5)
shares5 = sorted((s for _i, s, _c in plan5.values()), reverse=True)
print(f"     옵션 {len(plan5)}개, 몫 {shares5}, 합계 {sum(shares5)}명")
if len(plan5) != 2 or sum(shares5) != 5:
    bad.append("몫이 있는 픽업지가 빠졌다")

print()
print("  [수량 0 = 마감 (빠지면 안 된다)]")
plan0 = build_plan(0)
print(f"     옵션 {len(plan0)}개  (마감이므로 둘 다 다뤄야 한다)")
if len(plan0) != 2:
    bad.append(f"마감인데 옵션 {len(plan0)}개만 다룬다 — Block 이 안 걸린다")

src = (ROOT / "OTA Close" / "gg_open.py").read_text(encoding="utf-8")
if "if share <= 0 and int(it.get(\"qty\") or 0) > 0:" not in src:
    bad.append("gg_open 에 '몫 0은 계획에서 뺀다' 가 없다")

print()
if bad:
    for b in bad:
        print("  !!", b)
    raise SystemExit(f"!! {len(bad)}건 어긋남")
print("전부 통과 — 다 열렸으면 스킵으로 세지 않고, 마감은 그대로 걸린다")
