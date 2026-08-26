# -*- coding: utf-8 -*-
"""
지난 실행 기록.

기록은 두 곳에 있다.
  실행한 PC   hub/logs/runs — 전체 로그까지. 30일.
  중계 지점   요약(무엇이 언제 몇 건 실패했는지) + 마지막 300줄. 최근 120건.

중앙 화면은 클라우드에서 돌기 때문에 PC 의 파일을 볼 수 없다. 그대로 두면
'아직 기록이 없습니다' 만 뜬다. 아침에 무엇이 실패했는지는 팀 누구나
봐야 하므로, central 에서는 중계 지점의 요약을 읽는다.
"""
from __future__ import annotations

import json

import streamlit as st

import dispatch
from common import job_snapshot, page, render_logs, render_results, running_banner
from core import queue as Q
from core.jobs import LOG_DIR

page("실행 기록", "📄")
running_banner()

# ── 지금 도는 것 (이 화면에서 시작한 것) ──────────────────────────────────
snap = job_snapshot()
if snap.get("kind") or snap.get("logs"):
    st.subheader("현재 / 마지막 실행 — " + (snap.get("title") or ""))
    if snap.get("running"):
        st.info(f"{snap.get('started')} 시작 · {snap.get('elapsed', 0)}초 경과", icon="⏳")
    render_results(snap)
    with st.expander("로그", expanded=bool(snap.get("running"))):
        render_logs(snap)
    st.divider()


def _show(data: dict, full_log: str = "") -> None:
    """기록 하나를 보여준다. 두 방식(파일/중계)이 같은 모양이라 함께 쓴다."""
    results = data.get("results") or []
    fail = [r for r in results
            if "성공" not in str(r.get("result", ""))
            and "집계" not in str(r.get("result", ""))]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("항목", len(results))
    c2.metric("실패/스킵", len(fail))
    c3.metric("시작", data.get("started", "-"))
    c4.metric("걸린 시간", f"{data.get('seconds', 0)}초" if data.get("seconds") else "-")

    if data.get("agent"):
        st.caption(f"실행한 PC: **{data['agent']}**")
    if data.get("error"):
        st.error(data["error"])
    if data.get("summary"):
        st.caption(json.dumps(data["summary"], ensure_ascii=False))

    if results:
        only_fail = st.checkbox("실패/스킵만 보기", value=bool(fail), key="onlyfail")
        rows = fail if only_fail else results
        st.dataframe(
            [{"OTA": r.get("channel", ""), "지역": r.get("region", ""),
              "항목": r.get("item", ""), "결과": r.get("result", ""),
              "메모": r.get("memo", "")} for r in rows],
            width="stretch", hide_index=True)

    logs = data.get("logs") or []
    if full_log:
        with st.expander("전체 로그"):
            st.code(full_log[-60000:], language=None, height=420)
    elif logs:
        with st.expander("로그 (마지막 300줄)"):
            st.code("\n".join(str(x) for x in logs), language=None, height=420)
        st.caption("전체 로그는 실행한 PC 의 `hub/logs/runs` 에 있습니다. "
                   "로그인 정보가 섞일 수 있어 통째로 내보내지 않습니다.")


st.subheader("지난 기록")

# ── central : 중계 지점의 요약 ────────────────────────────────────────────
if dispatch.is_central():
    st.caption("팀 전체 기록입니다. 어느 PC 에서 돌렸는지도 함께 보입니다. 최근 120건.")
    try:
        runs = Q.run_list(60)
    except Exception as e:
        st.error(f"기록을 읽지 못했습니다: {e}")
        runs = []
    if not runs:
        st.info("아직 기록이 없습니다. 마감이나 오픈을 한 번 실행하면 여기에 쌓입니다.\n\n"
                "이미 실행하셨는데 비어 있다면, 그때 돌린 Agent 가 옛 버전입니다. "
                "**필요 파일 다운로드**에서 다시 받아 주세요.", icon="ℹ️")
        st.stop()

    def _label(r: dict) -> str:
        bad = f" · 실패 {r.get('failed')}" if r.get("failed") else ""
        return (f"{r.get('date','')} {r.get('started','')} · {r.get('title','?')}"
                f" · {r.get('agent','')}{bad}")

    pick = st.selectbox("기록 선택", runs, format_func=_label)
    if pick:
        _show(pick)
    st.stop()

# ── local : 이 PC 의 파일 ─────────────────────────────────────────────────
st.caption(f"{LOG_DIR} · 30일 보관. 아침에 무엇이 실패했는지 나중에 되짚기 위한 것입니다.")

files = sorted(LOG_DIR.glob("*.json"), reverse=True) if LOG_DIR.exists() else []
if not files:
    st.info("아직 기록이 없습니다. 마감이나 오픈을 한 번 실행하면 여기에 쌓입니다.")
    st.stop()

labels = []
for f in files[:60]:
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        d = {}
    labels.append((f, f"{f.stem.replace('_', ' ')} — {d.get('title', '?')}"))

pick = st.selectbox("기록 선택", labels, format_func=lambda x: x[1])
if not pick:
    st.stop()

path = pick[0]
try:
    data = json.loads(path.read_text(encoding="utf-8"))
except Exception as e:
    st.error(f"읽지 못했습니다: {e}")
    st.stop()

log_path = path.with_suffix(".log")
_show(data, log_path.read_text(encoding="utf-8") if log_path.exists() else "")
