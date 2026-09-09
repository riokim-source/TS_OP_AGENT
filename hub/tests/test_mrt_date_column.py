# -*- coding: utf-8 -*-
"""
MRT 날짜 열을 '고정 픽셀 숫자' 로 찾지 않는지 검사.

2026-09-09 오픈에서 MRT 4건이 전멸했다.

    [완료] 날짜 컬럼 확인: 10 / x=1060, y=676     <- 찾았다고 하고
    [진단] 날짜열 헤더('10') 못 찾음               <- 바로 뒤에 못 찾았다고 한다

같은 열을 두 벌의 다른 잣대로 찾고 있었다. 앞의 것은 '너비 30 이상' 이면 받고,
뒤의 것은 '너비 120~340' 만 받았다. 그날 실측한 열 너비는 102/103/115px —
셋 다 120 미만이라 전부 탈락했다. 창 크기와 확대율에 따라 매일 달라지는 값에
고정 숫자를 걸어 둔 것이 원인이다 (실측 targetX 가 날마다 1064~2404).

고친 방법: 앞에서 찾아 둔 열(col)의 실제 좌우 경계를 그대로 쓴다.

⚠️ 범위도 '중심에서 ±95px' 이 아니라 열 경계로 바꿨다. 열이 102px 로 좁게
   뜨면 ±95 는 옆 날짜까지 삼킨다 — 엉뚱한 날에 재고가 열린다.

브라우저를 띄우지 않고, 코드가 갖춰졌는지와 고르는 규칙만 확인한다.

    python hub/tests/test_mrt_date_column.py
"""
import re
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[2]
SRC = (ROOT / "OTA Close" / "mrt.py").read_text(encoding="utf-8", errors="replace")

bad = []

# ── 1) 찾아 둔 열을 실제로 쓰는가 ────────────────────────────────────────
print("  [1] 찾아 둔 열(col)을 쓰는가")
body = SRC[SRC.index("def set_target_date_inventory("):]
body = body[:body.index("\ndef ", 10)]

uses_col = 'col["x1"]' in body and 'col["x2"]' in body
print(f"     col 의 좌우 경계 사용: {'예' if uses_col else '!! 아니오'}")
if not uses_col:
    bad.append("찾아 둔 열(col)을 안 쓰고 어딘가에서 다시 찾는다")

# ── 2) 고정 픽셀 잣대가 남아 있지 않은가 ─────────────────────────────────
print()
print("  [2] 고정 픽셀 숫자")
# 'w > 120 && w < 340' 같은 너비 문지기
width_gate = re.search(r"\.w\s*[<>]=?\s*\d{2,4}\s*&&\s*o?\.?w\s*[<>]=?\s*\d{2,4}", body)
print(f"     너비 문지기(w>120&&w<340): {'!! 남아 있음' if width_gate else '없음'}")
if width_gate:
    bad.append(f"고정 너비 조건이 남아 있다: {width_gate.group(0)}")

# 'Math.abs(cx - targetX) > 95' 같은 중심 기준 허용오차
tol = re.search(r"Math\.abs\(\s*cx\s*-\s*\w+\s*\)\s*>\s*(\d+)", body)
print(f"     중심 ±픽셀 허용오차: {'!! 남아 있음 (±' + tol.group(1) + ')' if tol else '없음'}")
if tol:
    bad.append(f"중심 기준 ±{tol.group(1)}px 이 남아 있다 (옆 날짜를 삼킬 수 있다)")

# ── 3) 고르는 규칙이 실제로 맞는가 (그날의 실측 좌표로) ──────────────────
print()
print("  [3] 2026-09-09 실측 좌표로 고르기")


def pick(col_x1, col_x2, inputs, tolerance=None, center=None):
    """새 방식(열 경계) / 옛 방식(중심 ±tolerance) 를 흉내낸다."""
    out = []
    for name, cx in inputs:
        if tolerance is not None:
            if abs(cx - center) <= tolerance:
                out.append(name)
        elif col_x1 - 4 <= cx <= col_x2 + 4:
            out.append(name)
    return out


# 실제 화면에서 잰 값 (상품 5724343, 2026-09-10 목요일)
COLS = {"6": (602, 704), "7": (704, 806), "8": (806, 908), "9": (908, 1010),
        "10": (1010, 1111), "11": (1111, 1213), "12": (1213, 1315)}
# 각 날짜 열의 입력칸 (열 중심에서 살짝 왼쪽에 그려진다)
INPUTS = [(f"{d}일칸", (a + b) // 2 - 2) for d, (a, b) in COLS.items()]

x1, x2 = COLS["10"]
got = pick(x1, x2, INPUTS)
print(f"     새 방식: {got}")
if got != ["10일칸"]:
    bad.append(f"새 방식이 10일 말고 다른 것도 골랐다: {got}")

# 옛 방식은 좁은 열에서 옆 날짜까지 삼킨다
center = (x1 + x2) // 2
leak = pick(x1, x2, INPUTS, tolerance=95, center=center)
print(f"     옛 방식(중심 ±95px): {leak}")
if len(leak) > 1:
    print(f"     -> 좁은 열에서는 옆 날짜까지 삼켰다 ({len(leak)}개)")

# 열이 조금만 더 좁아지면(예: 80px) 옛 방식은 확실히 샌다
NARROW = {str(d): (600 + (d - 6) * 80, 600 + (d - 5) * 80) for d in range(6, 13)}
n_inputs = [(f"{d}일칸", (a + b) // 2 - 2) for d, (a, b) in NARROW.items()]
nx1, nx2 = NARROW["10"]
new_narrow = pick(nx1, nx2, n_inputs)
old_narrow = pick(nx1, nx2, n_inputs, tolerance=95, center=(nx1 + nx2) // 2)
print(f"     열 80px 일 때 — 새 방식 {new_narrow} / 옛 방식 {len(old_narrow)}개 {old_narrow}")
if new_narrow != ["10일칸"]:
    bad.append("열이 좁아지면 새 방식도 어긋난다")
if len(old_narrow) <= 1:
    bad.append("옛 방식이 새는 것을 못 보여줬다 — 이 테스트가 의미가 없다")

# ── 4) 실측 너비가 옛 조건에 걸렸다는 사실 ───────────────────────────────
print()
print("  [4] 2026-09-09 실측 열 너비")
for pid, w in (("5724343", 102), ("3887808", 102), ("5728538", 103), ("5889847", 115)):
    print(f"     {pid}  너비 {w}px  옛 조건(120 초과) {'통과' if w > 120 else '탈락'}")

print()
if bad:
    for b in bad:
        print("  !!", b)
    raise SystemExit(f"!! {len(bad)}건 어긋남")
print("전부 통과 — 날짜 열은 찾아 둔 경계로 고르고, 옆 날짜를 삼키지 않는다")
