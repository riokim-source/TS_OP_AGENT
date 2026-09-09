# -*- coding: utf-8 -*-
"""
화면 6개가 실제로 그려지는지 검사.

2026-09-09: TPC(Trip.com) 를 붙이면서 마감 대상에 'tpc' 를 추가했는데
화면의 이름표(close_tab.AGENCY_LABEL)에는 안 넣었다. 그래서 매일 쓰는
Last Minute 화면이 통째로 안 열렸다.

    close_tab.py:34  AGENCY_LABEL[a]  ->  KeyError: 'tpc'

그때 다른 테스트 22개는 전부 통과했다. 로직은 다 맞았기 때문이다.
**아무도 화면을 그려 보지 않았다.** 그게 이 파일이 있는 이유다.

화면이 안 열리면 그날 마감을 아예 못 한다. 로직 하나 틀린 것보다 나쁘다.

⚠️ 브라우저를 띄우지 않는다. Streamlit 의 AppTest 로 실제 스크립트를 돌려
   예외가 나는지만 본다. 버튼을 누르지 않으므로 재고를 건드리지 않는다.

    python hub/tests/test_pages_render.py
"""
import os
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[2]
UI = ROOT / "hub" / "op_ui"

# 실제 실행과 같은 조건. 이게 없으면 암호 화면에 막혀 아무것도 안 그려진다.
os.environ["LMHUB_LOCAL_ONLY"] = "1"
os.chdir(UI)
sys.path.insert(0, str(ROOT / "hub"))
sys.path.insert(0, str(UI))

try:
    from streamlit.testing.v1 import AppTest
except Exception as e:                                    # pragma: no cover
    print(f"  streamlit 을 못 불러왔습니다 ({e}) — 이 검사는 건너뜁니다.")
    raise SystemExit(0)

bad = []
pages = [UI / "app.py"] + sorted((UI / "pages").glob("*.py"))

for p in pages:
    try:
        at = AppTest.from_file(str(p), default_timeout=90).run()
    except Exception as e:
        print(f"  !! {p.name}  {type(e).__name__}: {str(e)[:120]}")
        bad.append(f"{p.name}: {type(e).__name__}")
        continue

    if at.exception:
        msg = str(at.exception[0].message)[:160]
        print(f"  !! {p.name}\n       {msg}")
        bad.append(f"{p.name}: {msg}")
        continue

    n = (len(at.get("button")) + len(at.get("selectbox"))
         + len(at.get("multiselect")) + len(at.get("checkbox"))
         + len(at.get("text_input")) + len(at.get("dataframe")))
    errs = [str(e.value)[:100] for e in at.get("error")]
    print(f"  OK   {p.name:26} 요소 {n:3}개" + (f"  빨간글 {len(errs)}개" if errs else ""))
    for e in errs:
        print(f"         {e}")
    # 아무것도 안 그려졌으면 뭔가 막힌 것이다 (예: 암호 화면)
    if n == 0 and not at.get("markdown") and not at.get("subheader"):
        bad.append(f"{p.name}: 화면이 비어 있다")

print()
# 마감 화면은 마감 대상 전부에 이름표가 있어야 한다 (이번에 터진 자리)
print("  [마감 대상 이름표]")
from core.close import runner as close_runner  # noqa: E402
import close_tab  # noqa: E402

for a in close_runner.AGENCIES:
    has = a in close_tab.AGENCY_LABEL
    print(f"     {a:8} {close_tab._agency_label(a):18} {'' if has else '(이름표 없음 — 코드로 표시)'}")
    if not has:
        bad.append(f"마감 대상 '{a}' 의 이름표가 없다")

print()
if bad:
    for b in bad:
        print("  !!", b)
    raise SystemExit(f"!! {len(bad)}건 어긋남")
print("전부 통과 — 화면 6개가 모두 열린다")
