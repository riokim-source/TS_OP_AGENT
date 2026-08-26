# -*- coding: utf-8 -*-
"""
직접 오픈 탭.

수량 수집을 거치지 않고 Klook 상품을 골라 바로 여는 화면이다.
휴대폰용 mobile_server.py 가 하던 일을 여기로 옮겼다 — PC 에서 서버를
따로 켤 필요도, 토큰·방화벽도 없어진다.

왜 따로 두나
    수집 탭의 오픈은 '오늘 올라온 예약 파일' 에 묶여 있다. 그런데 실제로는
    한 사람이 수집·오픈을 끝낸 뒤에 다른 사람이 특정 상품만 더 열거나
    수량을 고쳐야 하는 일이 자주 생긴다. 그때마다 파일을 다시 올릴 수는 없다.

    수집 결과는 [오늘 수집한 것 불러오기] 로 가져온다. 그래서 두 화면이
    같은 목록을 본다.
"""
from __future__ import annotations

from datetime import date

import streamlit as st

import dispatch
from common import date_picker, now_kst
from core import queue as Q
from core.opens import klook_open

TEXT_KEY = "open_direct_text"
SHARED_PREFIX = "klook-open"          # shared 저장 키 앞부분


def _shared_key(d: date) -> str:
    return f"{SHARED_PREFIX}-{d.isoformat()}"


def save_collected(target: date, plan: list[dict], who: str = "") -> None:
    """
    수집 탭이 계획을 만들면 여기로 남긴다.

    각자 브라우저 안에만 두면, 수집한 사람과 나중에 고치는 사람이 다를 때
    아무것도 안 보인다. 그래서 팀이 같이 보는 곳에 둔다.
    """
    lines = klook_open.plan_to_lines(plan)
    if not lines:
        return
    Q.shared_set(_shared_key(target), {
        "text": lines,
        "at": now_kst().strftime("%Y-%m-%d %H:%M"),
        "by": who or "",
        "count": len(lines.splitlines()),
    })


def _append(name: str, qty: int) -> None:
    cur = st.session_state.get(TEXT_KEY, "").rstrip()
    st.session_state[TEXT_KEY] = (cur + "\n" if cur else "") + f"{name} {qty}"


# ── 화면 ──────────────────────────────────────────────────────────────────
def render(lock) -> None:
    st.caption("수량 수집을 거치지 않고 Klook 상품만 골라 바로 엽니다. "
               "수집한 뒤 특정 상품을 더 열거나 수량을 고칠 때 쓰세요.")

    target = date_picker("오픈할 날짜", key="open_direct_date")

    # ── 수집 결과 불러오기 ────────────────────────────────────────────────
    got = Q.shared_get(_shared_key(target)) or {}
    if got.get("text"):
        c1, c2 = st.columns([2, 3])
        with c1:
            if st.button(f"📥 수집한 것 불러오기 ({got.get('count', 0)}건)",
                         width="stretch", key="btn-load-collected"):
                st.session_state[TEXT_KEY] = got["text"]
                st.rerun()
        with c2:
            who = f" · {got['by']}" if got.get("by") else ""
            st.caption(f"{got.get('at', '')}{who} 에 수집됨. "
                       "불러온 뒤 고치면 그 내용으로 실행됩니다.")
    else:
        st.caption(f"{target.isoformat()} 로 수집된 목록이 아직 없습니다. "
                   "아래에 직접 적으셔도 됩니다.")

    # ── 작업 목록 ─────────────────────────────────────────────────────────
    st.subheader("작업 목록")
    st.text_area(
        "상품명 수량 — 한 줄에 하나, 또는 쉼표로 구분",
        key=TEXT_KEY, height=170,
        placeholder="에버 12\n경주 16\n남이섬셔틀 3",
        help="수량 0 을 넣으면 Inventory 0 + Activate OFF (= 마감) 이 됩니다.")

    text = st.session_state.get(TEXT_KEY, "")
    plan, bad = klook_open.text_to_plan(text)
    if bad:
        st.warning("형식을 읽지 못한 줄 — `상품명 수량` 이어야 합니다: "
                   + ", ".join(f"`{b}`" for b in bad[:5]), icon="✏️")

    b1, b2 = st.columns([1, 4])
    with b1:
        if st.button("지우기", width="stretch", key="btn-open-clear"):
            st.session_state[TEXT_KEY] = ""
            st.rerun()

    # ── 상품 찾기 ─────────────────────────────────────────────────────────
    with st.expander("상품 찾기"):
        cat = klook_open.catalog()
        if not cat:
            st.info("상품 목록을 읽지 못했습니다. Klook Open 폴더를 확인하세요.")
        else:
            f1, f2, f3 = st.columns([3, 2, 1])
            with f1:
                q = st.text_input("이름으로 찾기", key="open-search",
                                  placeholder="에버, Biei, 남이섬 ...")
            with f2:
                regions = sorted({c.get("region", "") for c in cat if c.get("region")})
                rg = st.selectbox("지역", ["전체"] + regions, key="open-search-rg")
            with f3:
                qty = st.number_input("수량", min_value=0, value=1, step=1,
                                      key="open-search-qty")

            hits = [c for c in cat
                    if (not q or q.lower() in str(c.get("name", "")).lower())
                    and (rg == "전체" or c.get("region") == rg)]
            st.caption(f"{len(hits)}개" + (" — 앞 30개만 보입니다" if len(hits) > 30 else ""))
            for c in hits[:30]:
                cc1, cc2 = st.columns([5, 1])
                with cc1:
                    st.write(f"**{c['name']}**  ·  {c.get('region', '')} · "
                             f"{c.get('workflow', '')} · ID {c.get('id', '')}")
                with cc2:
                    if st.button("추가", key=f"add-{c.get('id')}-{c['name']}",
                                 width="stretch"):
                        _append(c["name"], int(qty))
                        st.rerun()

    if not plan:
        st.info("열 상품을 적거나 [상품 찾기] 에서 추가하세요.", icon="👆")
        return

    # ── 미리보기 ─────────────────────────────────────────────────────────
    st.divider()
    st.subheader(f"미리보기 — {len(plan)}건")
    try:
        pv = klook_open.preview(plan, target.isoformat())
    except Exception as e:
        st.error(f"미리보기를 만들지 못했습니다: {e}")
        return

    st.caption(f"대상 날짜: **{pv.get('date_text', target.isoformat())}**")
    if pv.get("unknown"):
        st.error("맵핑에 없는 상품 — 이대로 실행하면 빠집니다: "
                 + ", ".join(f"`{u['text']}`" for u in pv["unknown"]), icon="❓")
    for w in (pv.get("warnings") or []):
        st.warning(w, icon="⚠️")

    rows = []
    for region, tasks in (pv.get("regions") or {}).items():
        for t in tasks:
            rows.append({"지역": region, "상품": t["name"], "수량": t["qty"],
                         "방식": t["workflow"],
                         "": "마감(0)" if int(t["qty"] or 0) == 0 else ""})
    if not rows:
        st.error("실행할 것이 없습니다.")
        return
    st.dataframe(rows, width="stretch", hide_index=True)

    # ── 실행 ─────────────────────────────────────────────────────────────
    st.divider()
    agent = dispatch.agent_picker("agent_open_direct") if dispatch.is_central() else None
    busy = bool(lock) or dispatch.busy()
    blocked = busy or (dispatch.is_central() and not agent)
    if busy:
        st.info("다른 작업이 돌고 있습니다. 끝난 뒤에 실행하세요.", icon="⏳")

    def _go(dry: bool) -> None:
        title = ("직접 오픈 DRY-RUN " if dry else "직접 오픈 ") + f"KLOOK {len(rows)}건"
        started, msg = dispatch.start(
            "open", title,
            {"plan": plan, "date": target.isoformat(),
             "channels": ["KLOOK"], "dry_run": dry},
            total=len(rows), agent=agent)
        if started:
            st.rerun()
        st.error(msg)

    g1, g2 = st.columns(2)
    with g1:
        if st.button("DRY-RUN (입력 없이 대상만)", width="stretch",
                     disabled=blocked, key="btn-direct-dry"):
            _go(True)
    with g2:
        if st.button("오픈 실행", type="primary", width="stretch",
                     disabled=blocked, key="btn-direct-run"):
            st.session_state["confirm_direct"] = True

    if st.session_state.get("confirm_direct"):
        zero = [r for r in rows if int(r["수량"] or 0) == 0]
        st.warning(
            f"**{target.isoformat()}** 날짜로 **{len(rows)}건** 을 실제로 엽니다."
            + (f"  이 중 **{len(zero)}건은 수량 0** 이라 마감됩니다." if zero else ""),
            icon="⚠️")
        y1, y2 = st.columns(2)
        with y1:
            if st.button("네, 실행합니다", type="primary", width="stretch",
                         key="btn-direct-yes"):
                st.session_state["confirm_direct"] = False
                _go(False)
        with y2:
            if st.button("취소", width="stretch", key="btn-direct-no"):
                st.session_state["confirm_direct"] = False
                st.rerun()
