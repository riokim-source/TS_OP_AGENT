# -*- coding: utf-8 -*-
"""
직접 오픈 탭.

수량 수집을 거치지 않고 상품을 골라 바로 여는 화면이다.
휴대폰용 mobile_server.py 가 하던 일을 여기로 옮겼다 — PC 에서 서버를
따로 켤 필요도, 토큰·방화벽도 없어진다.

왜 따로 두나
    수집 탭의 오픈은 '오늘 올라온 예약 파일' 에 묶여 있다. 그런데 실제로는
    한 사람이 수집·오픈을 끝낸 뒤에 다른 사람이 특정 상품만 더 열거나
    수량을 고쳐야 하는 일이 자주 생긴다. 그때마다 파일을 다시 올릴 수는 없다.

⚠️ 한 번에 한 OTA 만 다룬다. 채널마다 상품을 부르는 이름이 다르기 때문이다
   (Klook 은 패키지 이름, 나머지는 내부 투어명). 섞으면 어느 쪽 이름인지
   알 수 없어 조용히 빗나간다.
"""
from __future__ import annotations

from datetime import date

import streamlit as st

import dispatch
from common import date_picker, now_kst
from core import queue as Q
from core.opens import direct

TEXT_KEY = "open_direct_text"
PENDING_KEY = "open_direct_pending"
CH_KEY = "open_direct_ch"
SHARED_PREFIX = "klook-open"          # 수집 결과를 남기는 키 (Klook 기준)


def _shared_key(d: date) -> str:
    return f"{SHARED_PREFIX}-{d.isoformat()}"


def save_collected(target: date, plan: list[dict], who: str = "") -> None:
    """수집 탭이 계획을 만들면 여기로 남긴다 (팀이 같이 본다)."""
    lines = direct.plan_to_lines([p for p in plan if p.get("channel") == "KLOOK"])
    if not lines:
        return
    Q.shared_set(_shared_key(target), {
        "text": lines,
        "at": now_kst().strftime("%Y-%m-%d %H:%M"),
        "by": who or "",
        "count": len(lines.splitlines()),
    })


def _set_text_later(value: str) -> None:
    """
    입력칸 값을 바꾼다.

    ⚠️ Streamlit 은 입력칸이 그려진 뒤에 그 값을 코드로 바꾸는 것을 막는다.
       [추가]·[지우기] 는 입력칸 아래에 있어서 그대로 넣으면 예외가 난다.
       그래서 '무엇으로 바꿀지' 만 적어 두고 화면을 다시 그린다.
    """
    st.session_state[PENDING_KEY] = value
    st.rerun()


def _append(name: str, qty: int) -> None:
    cur = str(st.session_state.get(TEXT_KEY, "")).rstrip()
    _set_text_later((cur + "\n" if cur else "") + f"{name} {qty}")


# ── 화면 ──────────────────────────────────────────────────────────────────
def render(lock) -> None:
    if PENDING_KEY in st.session_state:
        st.session_state[TEXT_KEY] = st.session_state.pop(PENDING_KEY)

    st.caption("수량 수집을 거치지 않고 상품만 골라 바로 엽니다. "
               "수집한 뒤 특정 상품을 더 열거나 수량을 고칠 때 쓰세요.")

    chans = direct.channels()
    ready = [c for c in chans if c["ready"]]
    names = [c["channel"] for c in ready]

    c1, c2 = st.columns([2, 3])
    with c1:
        ch = st.selectbox(
            "OTA", names, key=CH_KEY,
            format_func=lambda x: next(
                (f"{c['label']} ({c['count']}개)" if c["count"] else c["label"])
                for c in ready if c["channel"] == x))
    info = next(c for c in chans if c["channel"] == ch)
    with c2:
        target = date_picker("오픈할 날짜", key="open_direct_date")

    notready = [c for c in chans if not c["ready"]]
    if notready:
        with st.expander("아직 안 되는 OTA"):
            for c in notready:
                st.caption(f"**{c['label']}** — {c['reason']}")

    # GG 는 맵핑표를 쓰지 않고 화면에서 이름으로 찾는다.
    # 어느 Chrome 으로 들어갈지 알 수 없으므로 지역을 사람이 골라야 한다.
    gg_region = ""
    if info.get("needs_region"):
        gg_region = st.selectbox("지역", info.get("regions") or [],
                                 key=f"open-need-rg-{ch}",
                                 help="이 지역의 Chrome 으로 들어갑니다.")

    # 채널마다 '닫는다' 의 실제 동작이 달라서 무엇이 일어나는지 밝힌다.
    ZERO_MEANS = {"KLOOK": "Inventory 0 + Activate OFF",
                  "MRT": "잔여 인원 0", "GG": "Block 켜기"}
    if info["can_close"]:
        st.caption(f"수량 **0** 을 넣으면 마감됩니다 — {info['label']} 은 "
                   f"**{ZERO_MEANS.get(ch, '마감')}**.")
    else:
        st.caption(f"{info['label']} 은 수량 0 을 건너뜁니다. "
                   f"닫으려면 **Inventory 마감** 을 쓰세요.")

    # ── 수집 결과 불러오기 (Klook 기준으로 남는다) ────────────────────────
    if ch == "KLOOK":
        got = Q.shared_get(_shared_key(target)) or {}
        if got.get("text"):
            d1, d2 = st.columns([2, 3])
            with d1:
                if st.button(f"📥 수집한 것 불러오기 ({got.get('count', 0)}건)",
                             width="stretch", key="btn-load-collected"):
                    _set_text_later(got["text"])
            with d2:
                who = f" · {got['by']}" if got.get("by") else ""
                st.caption(f"{got.get('at', '')}{who} 에 수집됨. "
                           "불러온 뒤 고치면 그 내용으로 실행됩니다.")

    # ── 작업 목록 ─────────────────────────────────────────────────────────
    st.subheader("작업 목록")
    st.text_area(
        f"{info['label']} 상품명 수량 — 한 줄에 하나, 또는 쉼표로 구분",
        key=TEXT_KEY, height=170,
        placeholder=("에버 12\n경주 16" if ch == "KLOOK" else "Amanohashidate 5"),
        help=(f"수량 0 을 넣으면 마감됩니다 ({ZERO_MEANS.get(ch, '')})."
              if info["can_close"] else "수량 0 은 건너뜁니다."))

    text = st.session_state.get(TEXT_KEY, "")
    plan, bad = direct.text_to_plan(ch, text, region=gg_region)
    if bad:
        st.warning("형식을 읽지 못한 줄 — `상품명 수량` 이어야 합니다: "
                   + ", ".join(f"`{b}`" for b in bad[:5]), icon="✏️")

    b1, _ = st.columns([1, 4])
    with b1:
        if st.button("지우기", width="stretch", key="btn-open-clear"):
            _set_text_later("")

    # ── 상품 찾기 ─────────────────────────────────────────────────────────
    with st.expander(f"상품 찾기 — {info['label']}"):
        cat = direct.catalog(ch)
        if not cat:
            st.info(f"{info['label']} 은 상품 목록이 아직 없습니다 "
                    f"(맵핑표가 비어 있습니다). 위에 이름을 직접 적으세요.")
        else:
            f1, f2, f3 = st.columns([3, 2, 1])
            with f1:
                q = st.text_input("이름으로 찾기", key=f"open-search-{ch}",
                                  placeholder="에버, Biei, 남이섬 ...")
            with f2:
                regions = sorted({c.get("region", "") for c in cat if c.get("region")})
                rg = st.selectbox("지역", ["전체"] + regions, key=f"open-rg-{ch}")
            with f3:
                qty = st.number_input("수량", min_value=0, value=1, step=1,
                                      key=f"open-qty-{ch}")

            hits = [c for c in cat
                    if (not q or q.lower() in str(c.get("name", "")).lower())
                    and (rg == "전체" or c.get("region") == rg)]
            st.caption(f"{len(hits)}개" + (" — 앞 30개만 보입니다" if len(hits) > 30 else ""))
            for c in hits[:30]:
                cc1, cc2 = st.columns([5, 1])
                with cc1:
                    bits = [c.get("region", ""), c.get("workflow", ""),
                            f"ID {c.get('id', '')}" if c.get("id") else ""]
                    st.write(f"**{c['name']}**  ·  "
                             + " · ".join(x for x in bits if x))
                with cc2:
                    if st.button("추가", key=f"add-{ch}-{c.get('id')}-{c['name']}",
                                 width="stretch"):
                        _append(c["name"], int(qty))

    if not plan:
        st.info("열 상품을 적거나 [상품 찾기] 에서 추가하세요.", icon="👆")
        return

    # ── 미리보기 ─────────────────────────────────────────────────────────
    st.divider()
    try:
        pv = direct.preview(ch, plan, target.isoformat())
    except Exception as e:
        st.error(f"미리보기를 만들지 못했습니다: {e}")
        return

    rows = pv["rows"]
    st.subheader(f"미리보기 — {len(rows)}건")
    st.caption(f"대상 날짜: **{pv.get('date_text', target.isoformat())}**")
    if pv["unknown"]:
        st.error(f"{info['label']} 맵핑에 없는 상품 — 이대로 실행하면 빠집니다: "
                 + ", ".join(f"`{u}`" for u in pv["unknown"]), icon="❓")
    for w in pv["warnings"]:
        st.warning(w, icon="⚠️")
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

    # 미리보기에서 빠진 것은 실제로도 보내지 않는다.
    # 화면에 보인 것과 실행되는 것이 어긋나면 안 된다.
    keep = {str(r["상품"]) for r in rows}
    send = [p for p in plan if str(p.get("product")) in keep]

    def _go(dry: bool) -> None:
        title = (f"직접 오픈 DRY-RUN {info['label']} " if dry
                 else f"직접 오픈 {info['label']} ") + f"{len(rows)}건"
        started, msg = dispatch.start(
            "open", title,
            {"plan": send, "date": target.isoformat(),
             "channels": [ch], "dry_run": dry},
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
            f"**{info['label']}** 에 **{target.isoformat()}** 날짜로 "
            f"**{len(rows)}건** 을 실제로 엽니다."
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
