# -*- coding: utf-8 -*-
"""
TPC 날짜 선택 검사 — 겉모습이 아니라 실제로 들어갔는지로 판정하는가.

2026-09-10, TPC 마감이 며칠째 '눌렀는데 안 바뀜' 으로 끝났다.
실제 화면에 붙어서 잰 것:

  1) 달력 칸의 'ant-picker-cell-selected' 는 **커서 위치**다. 아무것도 안 눌러도
     오늘 날짜에 이미 붙어 있다.
         누르기 전 : 2026-09-10 -> ...cell-today cell-selected
         누른 뒤   : 2026-09-11 -> ...cell-selected     (커서만 옮겨감)
     봇은 이 클래스를 '선택됨' 의 근거로 삼고 있었다. 늘 통과했다.

  2) 진짜로 고른 날짜는 창 오른쪽 칸(.calendar-right)에 들어간다.
         JS 로 만든 클릭 -> .calendar-right 요소 0개, 글 ''
         진짜 마우스 입력 -> .calendar-right 요소 4개, 글 '2026-09-11'

  3) 그 결과 OK 가 서버로 보내는 본문(53만 자)에 그 날짜가 **아예 없었다**.
         고치기 전: ['2024-05-06','2024-05-15','2026-05-14','2099-12-31']
         고친 뒤  : [..., '2026-09-11', ...]

  4) 창 뒤에 깔린 상품 화면에도 같은 td[title=...] 이 있다. document 에서 찾으면
     창 밖의 칸을 눌러 버린다 (덮개에 막혀 아무 일도 안 난다).

⚠️ 다른 곳(패키지·대상·라디오)은 JS 클릭으로도 잘 들어간다. 달력만 다르다.

    python hub/tests/test_tpc_date_pick.py
"""
import re
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[2]
SRC = (ROOT / "OTA Close" / "tpc_dom.py").read_text(encoding="utf-8", errors="replace")
bad = []


def block(name: str) -> str:
    i = SRC.index(f"def {name}(")
    j = SRC.index("\ndef ", i + 10)
    return SRC[i:j]


# ── 1) 달력은 진짜 마우스 입력으로 누르는가 ─────────────────────────────
print("  [1] 달력 날짜를 진짜 마우스 입력으로 누르는가")
fn = block("dialog_set_by_date")
uses_real = "real_click(" in fn
print(f"     real_click 사용: {'예' if uses_real else '!! 아니오'}")
if not uses_real:
    bad.append("달력을 아직 JS 클릭으로 누른다 — 앱에 안 들어간다")

# 달력 칸을 mclick 으로 누르는 곳이 남아 있으면 안 된다
if re.search(r"mclick\([^)]*picker-cell", fn):
    bad.append("달력 칸을 __tpc.mclick 으로 누르는 코드가 남아 있다")

rc = block("real_click")
if "Input.dispatchMouseEvent" not in rc:
    bad.append("real_click 이 진짜 마우스 입력(Input.dispatchMouseEvent)을 안 쓴다")
print(f"     real_click 이 쓰는 것: "
      f"{'Input.dispatchMouseEvent' if 'Input.dispatchMouseEvent' in rc else '!! 아님'}")
for ty in ("mousePressed", "mouseReleased"):
    if ty not in rc:
        bad.append(f"real_click 에 {ty} 가 없다")

# ── 2) 창 안에서 찾는가 (창 밖 달력을 누르면 안 된다) ───────────────────
print()
print("  [2] 날짜 칸을 창 안에서 찾는가")
scoped = "__tpc.saleModal()" in fn and "m.querySelector('td[title=" in fn
loose = re.search(r"document\.querySelector\(\s*['\"]?td\[title=", fn)
print(f"     창 안으로 좁힘: {'예' if scoped else '!! 아니오'}"
      + ("   !! document 에서 찾는 곳이 있다" if loose else ""))
if not scoped:
    bad.append("날짜 칸을 창 안에서 찾지 않는다")
if loose:
    bad.append("document 에서 날짜 칸을 찾는다 — 창 밖 달력을 누르게 된다")

# ── 3) 판정 근거가 커서 클래스가 아닌가 ─────────────────────────────────
print()
print("  [3] 무엇을 근거로 '골랐다' 고 하는가")
by_panel = "calendar-right" in fn
by_class = re.search(r"className\.includes\(\s*['\"]selected['\"]", fn)
print(f"     오른쪽 칸(.calendar-right) 확인: {'예' if by_panel else '!! 아니오'}")
print(f"     커서 클래스로 판정: {'!! 예' if by_class else '아니오'}")
if not by_panel:
    bad.append("고른 날짜를 .calendar-right 로 확인하지 않는다")
if by_class:
    bad.append("커서 클래스(cell-selected)로 판정한다 — 아무것도 안 눌러도 붙어 있다")

st = block("dialog_state")
m = re.search(r"dates:\s*(.{0,400})", st, re.S)
dates_src = m.group(1) if m else ""
print(f"     dialog_state().dates 가 보는 곳: "
      f"{'.calendar-right' if 'calendar-right' in dates_src else '!! 커서 클래스'}")
if "calendar-right" not in dates_src:
    bad.append("dialog_state().dates 가 아직 커서 클래스를 본다")
if "cursor:" not in st:
    bad.append("커서 값을 따로 남기지 않는다 (로그로 견줄 수 없다)")

# ── 4) 그날 잰 값 (기록) ────────────────────────────────────────────────
print()
print("  [4] 2026-09-10 실측")
rows = [
    ("JS 로 만든 클릭", "cell-selected 붙음", ".calendar-right 0개", "본문에 날짜 없음"),
    ("진짜 마우스 입력", "cell-selected 붙음", ".calendar-right '2026-09-11'", "본문에 날짜 있음"),
]
print(f"     {'방법':16} {'달력 칸':18} {'오른쪽 칸':26} 서버로 간 본문")
for a, b, c, d in rows:
    print(f"     {a:16} {b:18} {c:26} {d}")

print()
if bad:
    for b in bad:
        print("  !!", b)
    raise SystemExit(f"!! {len(bad)}건 어긋남")
print("전부 통과 — 달력은 진짜로 누르고, 오른쪽 칸에 들어간 것으로 판정한다")
