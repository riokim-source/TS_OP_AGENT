# -*- coding: utf-8 -*-
"""
로컬판(last minute local) 화면이 전부 열리는지 검사.

중앙판과 같은 이유다 — 화면이 안 열리면 그날 마감을 아예 못 한다
([[test_pages_render]] 의 TPC 사고). 로컬판은 Agent·중계 지점이 없는 판이라,
'연결된 PC 가 없습니다' 같은 중앙판 전용 분기에 걸리면 아무것도 못 누른다.
그래서 중앙 모드 환경변수가 남아 있어도 로컬로 강제되는지까지 같이 본다.

⚠️ 브라우저를 띄우지 않는다. 버튼도 누르지 않는다 (재고를 건드리지 않는다).

    python hub/tests/test_local_pages_render.py
"""
import os
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[2]
UI = ROOT / "hub" / "local_ui"

# 중앙판을 켜 본 PC 에는 이 값이 남아 있다. 로컬판은 그래도 로컬로 돌아야 한다.
os.environ["LMHUB_MODE"] = "central"
os.environ["LMHUB_QUEUE"] = "firebase"

os.chdir(UI)
for d in (str(ROOT / "hub"), str(ROOT / "hub" / "op_ui"), str(UI)):
    sys.path.insert(0, d)

try:
    from streamlit.testing.v1 import AppTest
except Exception as e:                                    # pragma: no cover
    print(f"  streamlit 을 못 불러왔습니다 ({e}) — 이 검사는 건너뜁니다.")
    raise SystemExit(0)

bad = []

# ── 1) 탭 구성 ────────────────────────────────────────────────────────────
print("  [1] 화면 목록 (왼쪽 탭)")
WANT = ["1_크롬_연결.py", "2_마감.py", "3_수집_오픈.py", "4_직접_오픈.py",
        "5_클룩_모바일.py", "6_상품_규칙.py", "7_실행_기록.py", "8_세팅.py",
        "9_리뷰_분석.py"]
got = sorted(p.name for p in (UI / "pages").glob("*.py"))
for name in WANT:
    mark = "" if name in got else "   !! 없음"
    print(f"     {name}{mark}")
    if name not in got:
        bad.append(f"화면이 없다: {name}")
extra = [g for g in got if g not in WANT]
if extra:
    print(f"     (그 외: {extra})")

# ── 2) 전부 열리는가 ──────────────────────────────────────────────────────
print()
print("  [2] 그려지는가")
# 첫 화면은 '홈.py' 다. 'app.py' 로 두면 왼쪽 목록에 그 칸만 'app' 이라고 뜬다.
pages = [UI / "홈.py"] + [UI / "pages" / n for n in WANT if (UI / "pages" / n).is_file()]
for p in pages:
    try:
        at = AppTest.from_file(str(p), default_timeout=120).run()
    except Exception as e:
        print(f"     !! {p.name}  {type(e).__name__}: {str(e)[:120]}")
        bad.append(f"{p.name}: {type(e).__name__}")
        continue
    if at.exception:
        msg = str(at.exception[0].message)[:160]
        print(f"     !! {p.name}\n          {msg}")
        bad.append(f"{p.name}: {msg}")
        continue
    n = (len(at.get("button")) + len(at.get("selectbox")) + len(at.get("multiselect"))
         + len(at.get("checkbox")) + len(at.get("text_input"))
         + len(at.get("dataframe")) + len(at.get("number_input"))
         + len(at.get("tabs")))
    errs = [str(e.value)[:90] for e in at.get("error")]
    print(f"     OK  {p.name:20} 요소 {n:3}개" + (f"  빨간글 {len(errs)}" if errs else ""))
    for e in errs:
        print(f"           {e}")
    if errs:
        bad.append(f"{p.name}: 화면에 오류가 떴다 — {errs[0]}")
    if n == 0 and not at.get("markdown") and not at.get("subheader"):
        bad.append(f"{p.name}: 화면이 비어 있다")

# ── 3) 로컬로 강제되는가 ──────────────────────────────────────────────────
print()
print("  [3] 중앙 모드가 남아 있어도 로컬로 도는가")
import _boot                                               # noqa: E402
import dispatch                                            # noqa: E402
from core import queue as Q                                # noqa: E402

print(f"     LMHUB_MODE={os.environ.get('LMHUB_MODE')} · "
      f"queue={Q.backend_name()} · central={dispatch.is_central()}")
if dispatch.is_central():
    bad.append("로컬판인데 중앙 모드로 돈다 (Agent 를 찾으러 간다)")
if Q.backend_name() != "file":
    bad.append(f"로컬판인데 큐가 {Q.backend_name()} 다 (중계 열쇠를 찾는다)")
if os.environ.get("LMHUB_LOCAL_ONLY") != "1":
    bad.append("접속 암호를 묻는 상태다 (LMHUB_LOCAL_ONLY 가 1 이 아니다)")

# ── 4) 화면 코드를 복사해 두지 않았는가 ───────────────────────────────────
print()
print("  [4] 중앙판 화면을 복사하지 않고 그대로 쓰는가")
for name, shared in (("1_크롬_연결.py", "1_Chrome_로그인.py"),
                     ("7_실행_기록.py", "9_실행_기록.py"),
                     ("8_세팅.py", "8_Settings.py"),
                     ("9_리뷰_분석.py", "3_리뷰_분석.py")):
    src = (UI / "pages" / name).read_text(encoding="utf-8")
    ok = f'run_shared("{shared}")' in src and len(src.splitlines()) < 12
    print(f"     {name:16} -> {shared}  {'예' if ok else '!! 복사본으로 보인다'}")
    if not ok:
        bad.append(f"{name} 이 중앙판 화면을 그대로 쓰지 않는다")

print()
if bad:
    for b in bad:
        print("  !!", b)
    raise SystemExit(f"!! {len(bad)}건 어긋남")
print("전부 통과 — 로컬판 화면 10개가 열리고, 중계 없이 이 PC 에서 돈다")
