# -*- coding: utf-8 -*-
"""
MRT 날짜 열을 화면 좌표가 아니라 '표의 구조' 로 찾는지 검사.

2026-09-09 오픈에서 MRT 4건이 전멸했다. 로그가 앞뒤로 모순됐다.

    [완료] 날짜 컬럼 확인: 10 / x=1060, y=676     <- 찾았다고 하고
    [진단] 날짜열 헤더('10') 못 찾음               <- 바로 뒤에 못 찾았다고 한다

같은 열을 두 벌의 다른 잣대로 찾고 있었고, 뒤의 것이 '너비 120~340px' 이라는
고정 숫자를 썼다. 그날 실측한 열 너비는 102 / 102 / 103 / 115px — 넷 다 탈락.
창 크기와 확대율은 매일 달라진다(그날 로그의 targetX 가 1064~2404).

고친 방법: 좌표를 아예 안 본다. 표의 구조로 센다.

    thead:  인원 | 투어 코스 | 출발지 | 6 | 7 | 8 | 9 | 10 | 11 | 12
    tbody:  성인 | [코스A]   | 도쿄역 | [칸][칸][칸][칸][칸][칸][칸]

    헤더의 n번째 날짜 = 각 줄의 n번째 날짜 칸.

⚠️ 안전장치: 그 n 은 목표 날짜의 요일 번호와 반드시 같아야 한다.
   (일=0 … 토=6. 2026-09-10 은 목요일 → 4)
   다른 주/다른 달이 떠 있으면 여기서 걸린다.

    python hub/tests/test_mrt_date_column.py
"""
import re
import sys
from datetime import date
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[2]
SRC = (ROOT / "OTA Close" / "mrt.py").read_text(encoding="utf-8", errors="replace")

bad = []

# ── 1) 좌표로 찾는 코드가 남아 있지 않은가 ──────────────────────────────
print("  [1] 화면 좌표를 쓰는가")
fn = SRC[SRC.index("def set_target_date_inventory("):]
fn = fn[:fn.index("\ndef ", 10)]

checks = [
    ("고정 너비 조건 (w>120 && w<340)",
     re.search(r"\.w\s*[<>]=?\s*\d{2,4}\s*&&\s*o?\.?w\s*[<>]=?\s*\d{2,4}", fn)),
    ("중심 ±픽셀 허용오차", re.search(r"Math\.abs\(\s*cx\s*-\s*\w+\s*\)\s*>\s*\d+", fn)),
    ("getBoundingClientRect", "getBoundingClientRect" in fn),
    ("x 좌표 비교", re.search(r"col\.x1|col\.x2|targetX", fn)),
]
for label, hit in checks:
    print(f"     {label:30} {'!! 남아 있음' if hit else '없음'}")
    if hit:
        bad.append(f"좌표로 찾는 코드가 남아 있다: {label}")

# 옛 함수는 지웠는가
if "def get_calendar_date_column(" in SRC:
    print("     !! 옛 좌표 함수 get_calendar_date_column 이 남아 있다")
    bad.append("옛 좌표 함수가 남아 있다 — 두 벌이 되면 반드시 어긋난다")
else:
    print("     옛 좌표 함수 get_calendar_date_column: 지워짐")

# ── 2) 구조로 찾는가 ────────────────────────────────────────────────────
print()
print("  [2] 표의 구조로 찾는가")
for need, label in (("thead tr", "thead 에서 날짜 헤더를 찾는다"),
                    ("tbody tr", "tbody 각 줄을 본다"),
                    ("dayCells[want.index]", "헤더의 n번째 = 줄의 n번째 칸")):
    ok = need in fn or need in SRC
    print(f"     {label:34} {'예' if ok else '!! 아니오'}")
    if not ok:
        bad.append(f"구조로 찾지 않는다: {label}")

# 요일 번호 대조를 하는가
if "expect_idx" not in SRC:
    bad.append("요일 번호 대조(expect_idx)를 안 한다")
print(f"     {'요일 번호와 대조한다':34} {'예' if 'expect_idx' in SRC else '!! 아니오'}")

# ── 3) 요일 번호 대조가 실제로 맞는가 ───────────────────────────────────
print()
print("  [3] 요일 번호 계산")
sys.path.insert(0, str(ROOT / "OTA Close"))
import mrt  # noqa: E402

CASES = [(date(2026, 9, 6), 0, "일"), (date(2026, 9, 10), 4, "목"),
         (date(2026, 9, 12), 6, "토"), (date(2026, 10, 1), 4, "목")]
for d, want, kr in CASES:
    got = mrt._weekday_to_calendar_idx(d)
    mark = "" if got == want else f"  !! 기대 {want}"
    print(f"     {d} ({kr}) -> {got}번째 칸{mark}")
    if got != want:
        bad.append(f"{d} 의 요일 번호가 {got} (기대 {want})")

# ── 4) 그날 실측한 표로 골라 보기 ───────────────────────────────────────
print()
print("  [4] 2026-09-09 실측 표로 고르기")
# 실제 화면에서 읽은 것 (상품 5724343). 날짜 칸이 rowSpan 으로 코스를 덮는다.
HEAD = ["6", "7", "8", "9", "10", "11", "12"]
ROWS = [  # (줄 이름, 날짜 칸 개수)
    ("성인 [코스 A] 가마쿠라 하이라이트 도쿄역", 7),
    ("성인 [코스 A] 가마쿠라 하이라이트 신주쿠", 0),   # 위 줄의 rowSpan 이 덮는다
    ("소인 [코스 A] 가마쿠라 하이라이트 도쿄역", 0),
    ("소인 [코스 A] 가마쿠라 하이라이트 신주쿠", 0),
    ("성인 [코스 B] 가마쿠라&요코하마 도쿄역", 7),
    ("성인 [코스 B] 가마쿠라&요코하마 신주쿠", 0),
    ("소인 [코스 B] 가마쿠라&요코하마 도쿄역", 0),
    ("소인 [코스 B] 가마쿠라&요코하마 신주쿠", 0),
]


def pick(head, rows, day):
    idx = head.index(day)
    out = []
    for name, n in rows:
        if not n:
            continue            # 날짜 칸이 없는 줄은 건너뛴다 (이중 입력 방지)
        if n != len(head):
            continue            # 헤더와 개수가 다르면 믿지 않는다
        out.append((name, idx))
    return idx, out


idx, got = pick(HEAD, ROWS, "10")
print(f"     10일 = {idx}번째 칸 / 고른 줄 {len(got)}개")
for name, _ in got:
    print(f"       {name}")
if idx != 4:
    bad.append(f"10일이 {idx}번째 (기대 4)")
if len(got) != 2:
    bad.append(f"고른 줄이 {len(got)}개 (기대 2 — 코스 A / 코스 B)")

# 실측 열 너비: 옛 조건이었다면 전부 탈락했다
print()
print("     그날 실측 열 너비 (옛 조건은 '120 초과' 였다)")
for pid, w in (("5724343", 102), ("3887808", 102), ("5728538", 103), ("5889847", 115)):
    print(f"       {pid}  {w}px  -> 옛 조건 {'통과' if w > 120 else '탈락'}")

print()
if bad:
    for b in bad:
        print("  !!", b)
    raise SystemExit(f"!! {len(bad)}건 어긋남")
print("전부 통과 — 날짜 열은 구조로 찾고, 요일 번호로 한 번 더 대조한다")
