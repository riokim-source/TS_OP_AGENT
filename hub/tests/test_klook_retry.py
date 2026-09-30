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
print("=== 재시도가 정해진 횟수만 도는가 ===")
# 2026-09-29: 'Klook 이 목록으로 되돌림' 은 1.5초 뒤 재시도가 소용없었다.
# 여섯 상품이 두 번씩 같은 자리에서 끝났다 → 그쪽만 더 기다리고 한 번 더 (총 3회).
has_guard = "attempt < limit" in src and "attempt=attempt + 1" in src
has_limit = "limit = 3 if bounced else 2" in src
slow_retry = "8000 * attempt" in src
print(f"   attempt 로 한도 지킴 : {has_guard}")
print(f"   한도 2회(되돌림 3회) : {has_limit}")
print(f"   되돌림은 더 기다림   : {slow_retry}")

print()
print("=== 실패했을 때 진단이 남는가 ===")
for k, label in [("상세 진입 실패", "href/URL"), ("목록으로 튕김", "튕김 표시"),
                 ("화면글자=", "화면 글자"),
                 ("목록으로 되돌렸습니다", "되돌림 사유"),
                 ("_BOUNCE_MARK", "되돌림 표시 한 벌")]:
    print(f"   {label:16} {'있음' if k in src else '!! 없음'}")

print()
print("=== Edit(연필) 을 못 눌렀을 때 사유가 살아 있는가 ===")
# 이 JS 는 실패를 {ok:false, reason:...} 로 돌려준다. bool() 로 받으면 늘 True 가
# 되어 '눌렀다' 로 착각하고, 진짜 사유(칸이 없다 / 연필이 없다)는 버려졌다.
# (2026-09-29·09-30 수원화성·경주가 3일 연속 이 모양으로 '팝업 확인 못 함')
_fn = src.split("def open_tomorrow_edit_schedule(page):")[1].split("\ndef ")[0]
checks = [
    ("dict 로 받는다", "isinstance(clicked, dict)" in _fn),
    ("bool() 로 덮지 않는다", "clicked = bool(page.evaluate" not in _fn),
    ("칸이 없는 경우 따로", "칸이 없습니다" in _fn),
    ("연필이 없는 경우 따로", "Edit(연필) 이 없습니다" in _fn),
    ("그 외 사유도 적는다", "(사유: {why})" in _fn),
]
for label, ok in checks:
    print(f"   {label:22} {'예' if ok else '!! 아니오'}")

bad2 = []
if not steps: bad2.append("재시도 대상 목록이 없다")
if bad: bad2.append(f"재고를 건드리는 단계가 재시도 대상에 있다: {bad}")
if not has_guard: bad2.append("재시도가 무한 반복될 수 있다")
if not has_limit: bad2.append("재시도 한도가 정해져 있지 않다")
if not slow_retry: bad2.append("되돌림인데 바로 다시 해서 또 실패한다")
if "상세 진입 실패" not in src: bad2.append("실패 진단이 없다")
if "목록으로 되돌렸습니다" not in src: bad2.append("'되돌림' 과 '줄을 못 찾음' 이 안 갈린다")
for label, ok in checks:
    if not ok:
        bad2.append(f"Edit 사유: {label} — 아니다")
print()
if bad2:
    for b in bad2: print("  !!", b)
    raise SystemExit("!! 어긋남")
print("전부 통과 — 안전한 단계만 한도 안에서 재시도하고, 실패하면 이유가 남는다")
