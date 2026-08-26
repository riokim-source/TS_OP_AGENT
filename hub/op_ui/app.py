# -*- coding: utf-8 -*-
"""
TOURSTORY OP SYSTEM — Streamlit
실행:  python -m streamlit run hub/op_ui/app.py

v1.1 설계의 Streamlit OP Shell. 업무 로직은 hub/core 를 그대로 쓴다.
"""
from __future__ import annotations

from datetime import datetime

import streamlit as st

import dispatch
from common import (CHANNEL_LABEL, chrome_rows, chrome_source_note,
                    job_snapshot, now_kst, page, paths_line, running_banner)

page("TOURSTORY OP SYSTEM", "🧭")

st.caption(f"{now_kst():%Y-%m-%d %H:%M} (한국) · {paths_line()}")
running_banner()

# ── 오늘 해야 할 일 ───────────────────────────────────────────────────────
now = now_kst()
cut = now.replace(hour=10, minute=0, second=0, microsecond=0)
left = (cut - now).total_seconds() / 60
c1, c2, c3 = st.columns(3)
with c1:
    if left > 0:
        st.metric("10시까지", f"{int(left // 60)}시간 {int(left % 60)}분")
    else:
        st.metric("10시까지", "지남", delta=f"{int(-left)}분 초과", delta_color="inverse")
with c2:
    snap = job_snapshot()
    st.metric("실행 중", snap.get("title") or "없음")
with c3:
    st.metric("오늘", f"{now:%m/%d} ({'월화수목금토일'[now.weekday()]})")

st.divider()

# ── Chrome / 로그인 요약 ──────────────────────────────────────────────────
st.subheader("Chrome 연결")
st.caption("마감·오픈은 이 Chrome 들에 붙어서 돕니다. 꺼져 있거나 로그인이 풀려 있으면 실행할 수 없습니다.")

# ⚠️ 중앙 화면은 클라우드에서 돈다. 자기 Chrome 을 보면 늘 '꺼짐' 이라 쓸모가 없다.
#    그래서 각 PC 의 Agent 가 보내온 상태를 보여주고, 누구 것인지 밝힌다.
_who = chrome_source_note()
if dispatch.is_central():
    if not dispatch.current_agent():
        st.warning("연결된 PC 가 없습니다. 각자 PC 에서 **Agent 켜기** 를 실행하세요.", icon="🔌")
    else:
        st.caption(f"보고 있는 PC: **{_who}**")

try:
    rows = chrome_rows()
except Exception as e:
    st.error(f"Chrome 상태를 읽지 못했습니다: {e}")
    rows = []

if rows:
    live = sum(1 for r in rows if r.get("alive"))
    st.write(f"실행 중 **{live}/{len(rows)}**  ·  {_who}")
    st.dataframe(
        [{"Profile": r["key"], "이름": r.get("name", ""), "Port": r.get("port"),
          "상태": ("포트 충돌" if r.get("conflict") else
                   "실행 중" if r.get("alive") else "꺼짐"),
          "탭": r.get("tabs", 0),
          "담당 OTA": ", ".join(CHANNEL_LABEL.get(c, c) for c in (r.get("routed_channels") or [])) or "-"}
         for r in rows],
        width="stretch", hide_index=True)

st.divider()
st.subheader("오늘 순서")
st.markdown("""
0. **내 PC 에서 `Agent 켜기` 실행** — 이 창을 닫으면 내 PC 로 오는 작업이 안 돕니다
1. **Chrome / 로그인** — 프로필을 켜고 전부 `로그인됨` 인지 확인
2. **Last Minute › Inventory 마감** — 내일 날짜로 마감 (10시 전)
3. **Last Minute › 수량 수집 · 오픈** — 예약 파일 → 수량 → Office/OP 텍스트 → 자동 오픈
4. **실행 기록** — 실패한 건이 있으면 확인
""")
