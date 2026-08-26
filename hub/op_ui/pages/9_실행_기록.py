# -*- coding: utf-8 -*-
"""지난 실행 기록. 30일 보관."""
from __future__ import annotations

import json

import streamlit as st

from common import job_snapshot, page, render_logs, render_results, running_banner
from core.jobs import LOG_DIR

page("실행 기록", "📄")
running_banner()

# ── 지금 도는 것 ──────────────────────────────────────────────────────────
snap = job_snapshot()
if snap.get("kind") or snap.get("logs"):
    st.subheader("현재 / 마지막 실행 — " + (snap.get("title") or ""))
    if snap.get("running"):
        st.info(f"{snap.get('started')} 시작 · {snap.get('elapsed', 0)}초 경과", icon="⏳")
    render_results(snap)
    with st.expander("로그", expanded=bool(snap.get("running"))):
        render_logs(snap)
    st.divider()

# ── 지난 기록 ─────────────────────────────────────────────────────────────
st.subheader("지난 기록")
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
    stamp = f.stem.replace("_", " ")
    labels.append((f, f"{stamp} — {d.get('title', '?')}"))

pick = st.selectbox("기록 선택", labels, format_func=lambda x: x[1])
if not pick:
    st.stop()

path = pick[0]
try:
    data = json.loads(path.read_text(encoding="utf-8"))
except Exception as e:
    st.error(f"읽지 못했습니다: {e}")
    st.stop()

results = data.get("results") or []
fail = [r for r in results
        if "성공" not in str(r.get("result", "")) and "집계" not in str(r.get("result", ""))]
c1, c2, c3 = st.columns(3)
c1.metric("항목", len(results))
c2.metric("실패/스킵", len(fail))
c3.metric("시작", data.get("started", "-"))

if data.get("error"):
    st.error(data["error"])
if data.get("summary"):
    st.caption(json.dumps(data["summary"], ensure_ascii=False))

if results:
    only_fail = st.checkbox("실패/스킵만 보기", value=bool(fail))
    rows = fail if only_fail else results
    st.dataframe(
        [{"OTA": r.get("channel", ""), "지역": r.get("region", ""),
          "항목": r.get("item", ""), "결과": r.get("result", ""),
          "메모": r.get("memo", "")} for r in rows],
        width="stretch", hide_index=True)

log_path = path.with_suffix(".log")
if log_path.exists():
    with st.expander("전체 로그"):
        st.code(log_path.read_text(encoding="utf-8")[-60000:], language=None, height=420)
