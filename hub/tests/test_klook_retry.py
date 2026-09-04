# -*- coding: utf-8 -*-
"""
Klook: 화면 이동 단계 실패에만 재시도가 붙는지 검사.

같은 상품이 날마다 성공/실패를 오간다. 상품 문제가 아니라 그때그때 화면이
안 뜬 것이다.
    09-03 실패: 486801 488101 486797 277737 284812
    09-04 성공: 위 전부 / 대신 299440 694032 이 실패

VI·MRT 는 재시도가 있는데 Klook 만 없어서, 한 번 미끄러지면 그 상품은
그날 안 열렸다.

⚠️ 재고를 건드린 뒤(Confirm 등)에는 재시도하면 안 된다. 화면 이동 단계만.

    python hub/tests/test_klook_retry.py
"""
import re, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
src = (ROOT / "Klook Open" / "klook_worker.py").read_text(encoding="utf-8")

print("=== 재시도 대상 단계 ===")
m = re.search(r"_RETRYABLE_STEPS = \(([^)]*)\)", src, re.S)
steps = re.findall(r'"([^"]+)"', m.group(1)) if m else []
for s in steps: print("   ", s)

print()
print("=== 재고를 건드리는 단계는 빠져 있나 ===")
DANGER = ["Inventory 수량 입력", "Confirm 저장", "Activate", "익일 날짜 Edit 팝업 열기"]
bad = [d for d in DANGER if any(d in s for s in steps)]
for d in DANGER:
    print(f"   {d:26} {'!! 들어감' if d in bad else '빠짐 (정상)'}")

print()
print("=== 재시도가 한 번만 도는가 ===")
has_guard = "attempt == 1" in src and "attempt=2" in src
print(f"   attempt 로 한 번만: {has_guard}")

print()
print("=== 실패했을 때 진단이 남는가 ===")
for k, label in [("상세 진입 실패", "href/URL"), ("목록으로 튕김", "튕김 표시"),
                 ("화면글자=", "화면 글자")]:
    print(f"   {label:12} {'있음' if k in src else '!! 없음'}")

bad2 = []
if not steps: bad2.append("재시도 대상 목록이 없다")
if bad: bad2.append(f"재고를 건드리는 단계가 재시도 대상에 있다: {bad}")
if not has_guard: bad2.append("재시도가 무한 반복될 수 있다")
if "상세 진입 실패" not in src: bad2.append("실패 진단이 없다")
print()
if bad2:
    for b in bad2: print("  !!", b)
    raise SystemExit("!! 어긋남")
print("전부 통과 — 안전한 단계만 한 번 재시도하고, 실패하면 이유가 남는다")
