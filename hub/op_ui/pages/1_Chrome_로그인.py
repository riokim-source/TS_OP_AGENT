# -*- coding: utf-8 -*-
"""
Chrome 프로필 실행 / 로그인 확인.

⚠️ 이 화면은 클라우드에서 돈다. 여기서 Chrome 을 켜 봐야 그 서버의 Chrome 이
   켜질 뿐이다. 실제로 필요한 것은 사람이 앉아 있는 PC 의 Chrome 이므로,
   실행도 로그인 확인도 그 PC 의 Agent 를 거친다 (dispatch.chrome_call).
   (2026-08-26: [실행] 을 눌러도 아무 일도 일어나지 않았다)
"""
from __future__ import annotations

import streamlit as st

import dispatch
from common import (CHANNEL_LABEL, chrome_rows, chrome_source_note, page,
                    running_banner)

page("Chrome / 로그인", "🌐")
running_banner()

st.caption("OTA 로그인은 각 Chrome 프로필에 저장됩니다. 중앙 서버는 계정을 갖고 있지 않습니다.")

# ── 어느 PC 를 볼 것인가 ──────────────────────────────────────────────────
agent = None
if dispatch.is_central():
    agent = dispatch.agent_picker("agent_chrome")
    if not agent:
        st.info("각자 PC 에서 **`Agent 켜기.bat`** 을 실행하면 여기에 나타납니다.",
                icon="🔌")
        st.stop()
    st.caption(f"대상 PC: **{chrome_source_note(agent)}** — 아래 버튼은 그 PC 에서 실행됩니다.")

try:
    rows = chrome_rows(agent)
except Exception as e:
    st.error(f"상태를 읽지 못했습니다: {e}")
    st.stop()

if not rows:
    st.warning("Chrome 상태를 아직 받지 못했습니다. Agent 를 켠 직후라면 "
               "10초 뒤 새로고침하세요.", icon="⏳")
    st.stop()

conflicts = [x for x in rows if x.get("conflict")]
if conflicts:
    st.error(
        "포트를 다른 프로필의 Chrome 이 점유하고 있습니다: "
        + ", ".join(f"{c['key']}({c['port']})" for c in conflicts)
        + " — 그 Chrome 을 닫거나 Settings 에서 포트를 바꾸세요.", icon="🚫")

if dispatch.is_central():
    st.caption("상태는 그 PC 가 10초마다 보내옵니다. 켠 직후에는 잠깐 '꺼짐' 으로 보일 수 있습니다.")

# ── 프로필 카드 ───────────────────────────────────────────────────────────
for row in rows:
    key = row["key"]
    with st.container(border=True):
        c1, c2, c3 = st.columns([3, 4, 2])
        with c1:
            state = ("🚫 포트 충돌" if row.get("conflict")
                     else "🟢 실행 중" if row.get("alive") else "⚪ 꺼짐")
            st.markdown(f"**{key}** · {row.get('name','')}")
            st.caption(f"{state} · port {row.get('port')} · 탭 {row.get('tabs',0)}")
        with c2:
            st.caption("담당 OTA: " + (", ".join(
                CHANNEL_LABEL.get(c, c) for c in (row.get("routed_channels") or [])) or "없음"))
            st.caption("열린 탭: " + (", ".join(
                CHANNEL_LABEL.get(c, c) for c in (row.get("open_channels") or [])) or "없음"))
        with c3:
            if st.button("실행", key=f"boot-{key}", width="stretch",
                         disabled=bool(row.get("alive"))):
                where = f" ({agent})" if agent else ""
                with st.spinner(f"{key} Chrome 실행 중{where}..."):
                    res = dispatch.chrome_call("ensure", {"key": key, "wait": 35},
                                               agent=agent)
                if res.get("ok"):
                    st.success(res.get("message") or "실행됨")
                else:
                    st.error(res.get("message") or "실행 실패")
                st.rerun()

        got = st.session_state.get(f"login-{key}")
        if got:
            for ch, state in got:
                if state == "logged_in":
                    st.success(f"{CHANNEL_LABEL.get(ch, ch)} — 로그인됨", icon="✅")
                else:
                    st.error(f"{CHANNEL_LABEL.get(ch, ch)} — **로그인 필요** "
                             f"(그 PC 의 Chrome 창에서 직접 로그인하세요)", icon="🔑")

st.divider()

# ── 로그인 확인 ───────────────────────────────────────────────────────────
st.subheader("로그인 확인")
st.caption("각 프로필이 담당하는 OTA 에 실제로 접속해 보고 로그인 페이지로 튕기는지 봅니다. "
           "Chrome 을 방금 켰다면 몇 초 뒤에 확인하세요.")

if st.button("전체 확인", type="primary"):
    targets = [{"key": x["key"], "channels": x.get("routed_channels") or []}
               for x in rows if x.get("alive") and x.get("routed_channels")]
    if not targets:
        st.warning("실행 중인 Chrome 이 없습니다. 먼저 [실행] 을 누르세요.")
    else:
        with st.spinner("확인 중... (OTA 마다 페이지를 한 번씩 열어봅니다)"):
            res = dispatch.chrome_call("check_login", {"targets": targets},
                                       agent=agent, timeout=180)
        if not res.get("ok"):
            st.error(res.get("message") or "확인 실패")
        else:
            for k, items in (res.get("summary", {}).get("login") or {}).items():
                st.session_state[f"login-{k}"] = [
                    (x.get("channel"), x.get("state")) for x in items]
            st.rerun()
