# -*- coding: utf-8 -*-
"""
되살린 값 때문에 Last Minute 화면이 통째로 죽던 것.

2026-09-27 에 실제 저장본(lm_entries.json, 3031.xlsx 판)에서 찾았다.

    0::Busan|감천미포|(옵션없음)  lang = [... , 'chinese,english']
    1::Seoul|포천|              lang = [... , 'chinese,english']

언어·픽업 칸은 목록에 없는 이름도 직접 적을 수 있게 열어 두었고(그래야
'홍대 제외' 를 할 수 있다), 적은 값은 그대로 저장된다. 그런데 파일을 다시
불러오면 위젯 상태를 비우고 **저장된 값을 default 로** 다시 그리는데,
Streamlit 은 default 가 options 에 없으면 예외를 던진다.

    StreamlitAPIException: The default value 'chinese,english' is not part of
    the options.

그 순간 Last Minute 탭 전체가 안 열린다. 수량을 넣을 수도, 마감을 돌릴 수도
없다. 로직이 아니라 화면이 죽는 것이라 다른 검사로는 안 잡힌다.

고친 방법: 저장된 값을 버리지 않고 **후보에 얹는다.** 사람이 보고 직접 뺀다.
조용히 지우면 '내가 고른 픽업지가 왜 사라졌지' 가 되고, 그건 그것대로 사고다.

    python hub/tests/test_saved_values_render.py
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
os.environ["LMHUB_LOCAL_ONLY"] = "1"
sys.path.insert(0, str(ROOT / "hub"))
sys.path.insert(0, str(UI))

import streamlit as st                      # noqa: E402

try:
    import lastmin_tab as T                 # noqa: E402
except Exception as e:                      # pragma: no cover
    print(f"  화면 모듈을 못 불러왔습니다 ({e}) — 건너뜁니다.")
    raise SystemExit(0)

bad = []
ROW = {"area": "Busan", "product": "감천미포", "option": "(옵션없음)",
       "key": "Busan|감천미포|(옵션없음)", "option_split": True,
       "display": "감천미포", "languages": ["english", "korean", "chinese", "japanese"],
       "pickups": ["서면", "해운대"], "pickups_known": ["서면"], "lastmin": {}}

# ── 1) 실제로 저장돼 있던 값 그대로 ─────────────────────────────────────
print("  [1] 후보에 없는 언어가 저장돼 있을 때")
st.session_state.clear()
k = T._key(0, ROW)
st.session_state["lm_entries"] = {k: {
    "qty": 4,
    "lang": ["english", "korean", "chinese", "japanese", "chinese,english"],
    "pick": ["서면", "부산역(직접 적음)"],
    "ch": {}, "move": {},
}}
try:
    T._tour_row(0, ROW, True)
    print("     화면이 그려졌다 — OK")
except Exception as e:
    print(f"     !! {type(e).__name__}: {str(e)[:90]}")
    bad.append("저장된 값 때문에 화면이 죽는다 (그날 마감을 못 한다)")

# ── 2) 적어 둔 값이 사라지지 않는다 ─────────────────────────────────────
print()
print("  [2] 직접 적은 값을 버리지 않는가")
opts = T._with_saved(["서면", "해운대"], ["서면", "부산역(직접 적음)"])
print(f"     후보 = {opts}")
if "부산역(직접 적음)" not in opts:
    bad.append("직접 적은 픽업지를 버렸다 — 고른 것이 조용히 사라진다")
if opts[:2] != ["서면", "해운대"]:
    bad.append("원래 후보 순서가 바뀌었다")
if T._with_saved(["a"], None) != ["a"]:
    bad.append("저장값이 없을 때 후보가 망가진다")

# ── 3) 빈 후보 + 저장값만 있어도 그려진다 ───────────────────────────────
print()
print("  [3] 후보가 아예 없을 때")
st.session_state.clear()
BARE = dict(ROW, languages=[], pickups=[], key="Busan|경주|")
st.session_state["lm_entries"] = {T._key(0, BARE): {
    "qty": 3, "lang": ["korean"], "pick": ["서면"], "ch": {}, "move": {}}}
try:
    T._tour_row(0, BARE, True)
    print("     OK")
except Exception as e:
    print(f"     !! {type(e).__name__}: {str(e)[:90]}")
    bad.append("후보가 없는데 저장값이 있으면 화면이 죽는다")

print()
if bad:
    for b in bad:
        print("  !!", b)
    raise SystemExit(f"!! {len(bad)}건 어긋남")
print("전부 통과 — 저장된 값이 후보에 없어도 화면은 열리고, 그 값은 남는다")
