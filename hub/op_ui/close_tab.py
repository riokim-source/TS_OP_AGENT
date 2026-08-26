# -*- coding: utf-8 -*-
"""Inventory 마감 탭. Last Minute 페이지 안에서 쓴다."""
from __future__ import annotations

import time

import streamlit as st

import dispatch
from common import CHANNEL_LABEL, date_picker, render_logs, render_results
from core import kkday_codes
from core.close import runner as close_runner
from core.routing import get_routing

AGENCY_LABEL = {"klook": "Klook", "kkday": "KKday", "gg": "GetYourGuide",
                "vi": "Viator", "mrt": "MyRealTrip"}
REGIONS = ["KOREA", "JAPAN", "AUSTRALIA", "UK"]


def render(lock) -> None:
    ok, detail = close_runner.available()
    if not ok:
        st.error(detail)
        return

    target = date_picker("마감할 날짜", key="close_date")

    st.subheader("에이전시")
    st.caption("오늘 마감이 필요한 것만 고르세요. 안 고른 OTA 는 건드리지 않습니다.")
    cols = st.columns(len(close_runner.AGENCIES))
    agencies = []
    for col, a in zip(cols, close_runner.AGENCIES):
        with col:
            if st.checkbox(AGENCY_LABEL[a], value=True, key=f"ag-{a}"):
                agencies.append(a)

    with st.expander("지역 지정 (비우면 전체)"):
        st.caption("Chrome 연결이 미설정인 조합은 여기서 골라도 실행되지 않습니다.")
        rcols = st.columns(len(REGIONS))
        regions = [rg for col, rg in zip(rcols, REGIONS)
                   if col.checkbox(rg, value=False, key=f"rg-{rg}")]

    if "kkday" in agencies:
        codes = kkday_codes.load()
        with st.expander(f"KKday 마감 대상 {len(codes)}개 상품 (그 외 스킵)"):
            if not codes:
                st.error("대상 목록을 읽지 못했습니다 — 전체 상품을 돌 수 있습니다.")
            else:
                names = kkday_codes.names_of(codes)
                st.dataframe(
                    [{"상품번호": c, "이름": ", ".join(names.get(c, [])[:6]) or "(맵핑리스트에 없음)"}
                     for c in codes], width="stretch", hide_index=True)
                st.caption("목록은 `OTA Close/kkday.py` 의 DEFAULT_PRODUCT_CODES 입니다.")

    # ── 필요한 Chrome ────────────────────────────────────────────────────
    # central 모드에서는 이 화면(중앙 PC)의 Chrome 을 보여줘도 의미가 없다.
    # 실제로 도는 곳은 고른 PC 의 Agent 다. 그쪽 상태를 보여준다.
    st.subheader("필요한 Chrome")
    ready_all = True
    if not agencies:
        st.info("에이전시를 1개 이상 고르세요.")
        ready_all = False
    elif dispatch.is_central():
        req = close_runner.required_chromes(agencies, regions)
        need = ", ".join(req["profiles"].keys()) or "없음"
        st.caption(f"이 조합에 필요한 프로필: **{need}** — 실행할 PC 에 그 Chrome 이 "
                   f"켜져 있고 로그인돼 있어야 합니다.")
        if req.get("unconfigured"):
            st.caption("미설정(실행 안 함): " + ", ".join(
                f"{u['region']}/{CHANNEL_LABEL.get(u['channel'], u['channel'])}"
                for u in req["unconfigured"]))
        if not req["profiles"]:
            st.warning("고른 조합에 연결된 Chrome 이 없습니다 — 실행되지 않습니다.", icon="⚠️")
            ready_all = False
    else:
        r = get_routing()
        req = close_runner.required_chromes(agencies, regions)
        for key, v in req["profiles"].items():
            ready = r.profile_owns_port(key)
            conflict = r.port_conflict(key)
            ready_all = ready_all and ready and not conflict
            icon = "🚫" if conflict else ("🟢" if ready else "⚪")
            st.write(f"{icon} **{key}** (port {r.profile_port(key)}) — "
                     f"{', '.join(CHANNEL_LABEL.get(c, c) for c in v['channels'])} / "
                     f"{', '.join(v['regions'])}")
        if not req["profiles"]:
            st.warning("고른 조합에 연결된 Chrome 이 없습니다 — 실행되지 않습니다.", icon="⚠️")
            ready_all = False
        if req.get("unconfigured"):
            st.caption("미설정(실행 안 함): " + ", ".join(
                f"{u['region']}/{CHANNEL_LABEL.get(u['channel'], u['channel'])}"
                for u in req["unconfigured"]))
        if not ready_all and req["profiles"]:
            st.warning("꺼져 있거나 충돌 중인 Chrome 이 있습니다. "
                       "[Chrome / 로그인] 에서 실행하고 로그인을 확인하세요.", icon="⚠️")

    st.divider()

    agent = dispatch.agent_picker("agent_close") if dispatch.is_central() else None
    busy = bool(lock) or dispatch.busy()
    blocked = busy or not agencies or (dispatch.is_central() and not agent)

    def start(dry: bool) -> None:
        title = ("마감 DRY-RUN " if dry else "마감 ") + \
                f"{', '.join(a.upper() for a in agencies)} ({target.isoformat()})"
        started, msg = dispatch.start(
            "close", title,
            {"date": target.isoformat(), "agencies": agencies,
             "regions": regions, "dry_run": dry},
            agent=agent)
        if started:
            st.rerun()
        else:
            st.error(msg)

    c1, c2 = st.columns(2)
    with c1:
        if st.button("DRY-RUN (클릭 없이 대상만)", width="stretch",
                     disabled=blocked, key="btn-close-dry"):
            start(True)
    with c2:
        if st.button("실제 마감 실행", type="primary", width="stretch",
                     disabled=blocked, key="btn-close-run"):
            st.session_state["confirm_close"] = True

    if st.session_state.get("confirm_close"):
        lines = [
            f"**{target.isoformat()}** 날짜의 재고를 실제로 0 으로 만듭니다.",
            "",
            "대상: " + ", ".join(AGENCY_LABEL[a] for a in agencies)
            + (f" / {', '.join(regions)}" if regions else " / 전체 지역"),
        ]
        if agent:
            lines.append(f"실행 PC: {agent}")
        st.warning("\n\n".join(lines), icon="🔒")
        cc1, cc2 = st.columns(2)
        if cc1.button("진행", type="primary", width="stretch", key="cc-yes"):
            st.session_state["confirm_close"] = False
            start(False)
        if cc2.button("취소", width="stretch", key="cc-no"):
            st.session_state["confirm_close"] = False
            st.rerun()

    # ── 진행 상황 ────────────────────────────────────────────────────────
    snap = dispatch.snapshot("close")
    if snap and (snap.get("logs") or snap.get("results") or snap.get("running")):
        st.divider()
        st.subheader(snap.get("title") or "실행")
        if snap.get("pending"):
            st.info(f"{snap.get('agent')} 의 Agent 가 가져가기를 기다리는 중...", icon="📨")
        elif snap.get("running"):
            extra = f" · {snap.get('agent')}" if snap.get("agent") \
                    else f" · {snap.get('elapsed', 0)}초 경과"
            st.info(f"{snap.get('started')} 시작{extra}", icon="⏳")
        elif snap.get("error"):
            st.error(snap["error"])
        elif snap.get("summary") is not None:
            st.success("완료")
        if snap.get("stale"):
            st.warning("Agent 에서 오래 소식이 없습니다. 그 PC 를 확인하세요.", icon="⚠️")
        if snap.get("running") and st.button("중단", key="btn-close-stop"):
            dispatch.stop(snap)
            st.rerun()
        render_results(snap)
        with st.expander("로그", expanded=bool(snap.get("running"))):
            render_logs(snap)
        if snap.get("running"):
            time.sleep(3)
            st.rerun()
