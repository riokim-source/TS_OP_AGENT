# -*- coding: utf-8 -*-
"""
리뷰 수집·분석.

원본 도구(TOURSTORY Review Analyzer v1.3)의 엔진을 그대로 옮겨 왔다.
바뀐 것은 **Chrome 뿐**이다 — 자기만의 9222~9225 대신 라스트미닛이 매일 쓰는
Chrome(KR 9522 / JP 9523 / AU 9524 / UK 9525 / GLOBAL 9530)에 붙는다.
어느 Chrome 으로 갈지는 [Settings] 의 Region x OTA 표 하나가 정한다.

⚠️ 수집은 **로그인된 Chrome 이 있는 PC** 에서만 된다. 클라우드에는 그 Chrome 이
   없다. 그래서 웹으로 볼 때는 이 화면이 작업을 Agent 로 보내고, 결과 엑셀도
   그 PC 에 저장된다.
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import streamlit as st

import dispatch
from common import page, render_logs, render_results, running_banner, who_input
from core.review import runner as review

page("리뷰 분석", "⭐")
running_banner()

ok, why = review.available()
if not ok:
    st.error(why, icon="🚫")
    st.stop()

st.caption("예약 리포트를 기준으로 OTA 리뷰를 모아서 나쁜 리뷰 비율을 냅니다. "
           "Chrome 은 라스트미닛이 쓰는 것을 그대로 씁니다.")
who_input("rv_who")

agent = dispatch.agent_picker("agent_review") if dispatch.is_central() else None

# ── 무엇을 볼 것인가 ─────────────────────────────────────────────────────
st.subheader("대상")
if dispatch.is_central():
    st.info("수집은 그 PC 에서 돕니다. **그 PC 안의 파일 경로**를 적어 주세요. "
            "결과 엑셀도 그 PC 에 저장됩니다.", icon="💻")
    path = st.text_input("예약 리포트 엑셀 (그 PC 경로)",
                         placeholder=r"C:\Users\...\Desktop\예약리포트.xlsx",
                         key="rv_path")
else:
    up = st.file_uploader("예약 리포트 엑셀", type=["xlsx", "xls"], key="rv_file")
    path = ""
    if up is not None:
        # 이 PC 에서 도는 중이므로 임시로 저장해서 경로를 넘긴다.
        from core import paths as _paths
        tmp = _paths.DATA_DIR / "review_input.xlsx"
        tmp.write_bytes(up.getvalue())
        path = str(tmp)
        st.caption(f"읽을 파일: {up.name}")

c1, c2 = st.columns(2)
today = date.today()
start = c1.date_input("시작일", value=today - timedelta(days=60), key="rv_start")
end = c2.date_input("종료일", value=today, key="rv_end")
st.caption("비워 둘 수 없습니다. 파일 전체를 보려면 넉넉히 잡으세요.")

chans = review.channels()
picked = st.multiselect(
    "OTA", chans, default=chans, key="rv_ch",
    format_func=lambda c: f"{c} · {review.channel_label(c)}")
areas_text = st.text_input("Area (비우면 전부)", key="rv_areas",
                           placeholder="예: Busan, Sapporo")
areas = [a.strip() for a in areas_text.split(",") if a.strip()]

# ── 실행 ─────────────────────────────────────────────────────────────────
busy = dispatch.busy()
blocked = busy or not path or not picked or (dispatch.is_central() and not agent)
if st.button("리뷰 수집 시작", type="primary", width="stretch", disabled=blocked):
    started, msg = dispatch.start(
        "review",
        f"리뷰 {start:%m/%d}~{end:%m/%d}",
        {
            "path": path,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "areas": areas,
            "channels": picked,
        },
        agent=agent,
    )
    if started:
        st.rerun()
    else:
        st.error(msg)

if not path:
    st.caption("엑셀을 먼저 지정하세요.")
elif busy:
    st.caption("다른 작업이 도는 중입니다. 끝나면 실행할 수 있습니다.")

# ── 진행 / 결과 ──────────────────────────────────────────────────────────
snap = dispatch.snapshot("review")
if snap.get("exists"):
    st.divider()
    st.subheader("진행")
    render_results(snap)
    with st.expander("로그", expanded=bool(snap.get("running"))):
        render_logs(snap)
    summary = snap.get("summary") or {}
    if summary.get("output"):
        st.success(f"리뷰 {summary.get('rows', 0)}건 · 엑셀: "
                   f"`{summary['output']}`", icon="✅")
        out = Path(str(summary["output"]))
        if out.exists():
            st.download_button("결과 엑셀 내려받기", out.read_bytes(), out.name,
                               width="stretch")
    if snap.get("running") and st.button("중단", width="stretch"):
        dispatch.stop(snap)
        st.rerun()
