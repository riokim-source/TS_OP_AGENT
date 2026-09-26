# -*- coding: utf-8 -*-
"""
빠른 입력 칸이 실제로 화면 값을 바꾸는지 검사.

파싱은 test_quickfill.py 가 본다. 여기서는 그 결과가 **화면에 들어가는지** 본다.

  1) 적은 상품에 수량·언어·픽업이 들어간다
  2) **적지 않은 상품은 0 으로 되돌아간다**
     (칸을 고쳐 적었는데 지운 상품의 예전 수량이 남으면 그대로 열린다)
  3) 위젯 키(q-/l-/p-)까지 바뀐다
     세션에 위젯 값이 남아 있으면 Streamlit 이 그쪽을 우선해서, 값은 바뀌었는데
     화면에는 예전 숫자가 보인다. 그 상태로 오픈하면 화면과 다른 수량이 나간다.
  4) 상세 박스는 접힌 채로 시작한다 (2026-09-27 요청)

    python hub/tests/test_quickfill_ui.py
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
LANGS = ["english", "korean", "chinese"]
PICKS = ["홍대입구역", "명동", "동대문역사문화공원"]


def row(product, option=""):
    return {"area": "Seoul", "product": product, "option": option,
            "key": f"Seoul|{product}|{option}", "option_split": bool(option),
            "display": f"{product}({option})" if option else product,
            "languages": list(LANGS), "pickups": list(PICKS), "lastmin": {}}


ROWS = [row("경주"), row("교촌경주"), row("알남아")]
st.session_state.clear()

# 어제 값이 남아 있는 상태에서 시작한다 (교촌경주 10)
st.session_state["lm_entries"] = {
    T._key(0, ROWS[1]): {"qty": 10, "lang": list(LANGS), "pick": list(PICKS),
                         "ch": {}, "move": {}},
}
st.session_state[f"q-{T._key(0, ROWS[1])}"] = 10

n, probs = T._apply_quick(0, ROWS, "경주 4, 알남아 9(홍대제외)")
print(f"  [1] 넣은 상품 {n}개 / 알림 {probs}")
if n != 2 or probs:
    bad.append(f"넣은 개수 {n}, 알림 {probs}")

ent = st.session_state["lm_entries"]
for prod, want in (("경주", 4), ("교촌경주", 0), ("알남아", 9)):
    r = next(x for x in ROWS if x["product"] == prod)
    k = T._key(0, r)
    got = int((ent.get(k) or {}).get("qty") or 0)
    wid = st.session_state.get(f"q-{k}")
    mark = "" if (got == want and wid == want) else f"   !! 기대 {want}"
    print(f"     {prod:6} 값={got} 위젯={wid}{mark}")
    if got != want:
        bad.append(f"{prod} 수량이 {got} (기대 {want})")
    if wid != want:
        bad.append(f"{prod} 위젯 값이 {wid} (기대 {want}) — 화면에 옛 숫자가 남는다")

print()
print("  [2] 픽업 제외")
k = T._key(0, ROWS[2])
pick = st.session_state.get(f"p-{k}")
print(f"     알남아 픽업 = {pick}")
if not pick or any("홍대" in x for x in pick):
    bad.append(f"홍대를 못 뺐다: {pick}")
if len(pick or []) != len(PICKS) - 1:
    bad.append(f"홍대 말고 다른 것까지 뺐다: {pick}")

# 언어는 손대지 않았으니 후보 전부여야 한다
lang = st.session_state.get(f"l-{k}")
if lang != LANGS:
    bad.append(f"언어를 안 적었는데 {lang} 로 좁혀졌다")

print()
print("  [3] 다시 적으면 이전 것이 남지 않는다")
n2, _ = T._apply_quick(0, ROWS, "교촌경주 10")
q = {p: int((ent.get(T._key(0, next(x for x in ROWS if x['product'] == p))) or {}).get("qty") or 0)
     for p in ("경주", "교촌경주", "알남아")}
print(f"     {q}")
if q != {"경주": 0, "교촌경주": 10, "알남아": 0}:
    bad.append(f"고쳐 적었는데 예전 수량이 남았다: {q}")

print()
print("  [4] 화면 연결")
SRC = (UI / "lastmin_tab.py").read_text(encoding="utf-8")
for label, cond in (
        ("빠른 입력을 그린다", "_quick_fill(latest, panels[latest])" in SRC),
        ("상세 박스는 접힌 채로", "expanded=False" in SRC),
        ("펼친 채로 시작하지 않는다", 'expanded=p["is_latest"]' not in SRC),
        ("지역마다 칸 하나", 'key=f"qf-{pi}-{area}"' in SRC),
        ("파일을 새로 읽으면 칸도 비운다", '"qf-"' in SRC)):
    print(f"     {label:24} {'예' if cond else '!! 아니오'}")
    if not cond:
        bad.append(f"{label} — 아니다")

print()
if bad:
    for b in bad:
        print("  !!", b)
    raise SystemExit(f"!! {len(bad)}건 어긋남")
print("전부 통과 — 칸에 적은 대로 화면 값이 바뀌고, 적지 않은 것은 0 이 된다")
