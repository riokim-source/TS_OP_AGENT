# -*- coding: utf-8 -*-
"""
dispatch.py
'어디서 봇을 돌릴 것인가' 를 한 곳에서 정한다.

두 가지 모드가 있다. 화면 코드는 이 파일만 부르고 둘을 구분하지 않는다.

  local    (기본)  이 PC 에서 바로 실행. 지금까지 쓰던 방식.
  central          중앙 웹페이지가 Job 을 만들고, 개인 PC 의 Agent 가 가져가 실행.

왜 모드를 남겨 두나
    매일 아침 10시 마감이 걸린 작업이다. 중앙 방식이 며칠 돌아가는 걸 확인하기
    전까지 물러설 곳이 있어야 한다. 환경변수 하나로 되돌릴 수 있게 둔다.

    set LMHUB_MODE=central     중앙 + Agent
    (없으면 local)
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import streamlit as st

# ⚠️ 이 모듈이 common 보다 먼저 import 될 수 있다 (close_tab 의 import 순서).
#    그래서 core 경로를 스스로 잡는다. common 에만 맡기면 순서에 따라 깨진다.
_HUB = Path(__file__).resolve().parent.parent
if str(_HUB) not in sys.path:
    sys.path.insert(0, str(_HUB))

from core import queue as Q
from core.close import runner as close_runner
from core.jobs import MANAGER, Job
from core.opens import gg_open, klook_open, mrt_open


def mode() -> str:
    return "central" if os.environ.get("LMHUB_MODE") == "central" else "local"


def is_central() -> bool:
    return mode() == "central"


# ── 연결된 PC ─────────────────────────────────────────────────────────────
AGENT_KEY = "agent_pick"


def agent_rows() -> list[dict]:
    """
    연결된 PC 목록. 한 번 그리는 동안 여러 번 불려도 왕복은 1회로 묶는다.
    (첫 화면만 해도 Chrome 칸 + PC 고르는 칸 + 배너가 각각 부른다)
    """
    hit = st.session_state.get("_agent_rows")
    if hit and (time.time() - hit[0]) < 3.0:
        return hit[1]
    try:
        rows = Q.agents()
    except Exception:
        rows = []
    st.session_state["_agent_rows"] = (time.time(), rows)
    return rows


def current_agent() -> str | None:
    """
    지금 대상 PC. 고른 적이 없으면 켜져 있는 첫 PC 를 쓴다.

    혼자 쓸 때 매번 고르게 하면 번거롭기만 하다. 여러 대가 켜져 있으면
    agent_picker() 가 사람에게 묻는다.
    """
    picked = st.session_state.get(AGENT_KEY)
    rows = agent_rows()
    online = [a["agent"] for a in rows if a.get("online")]
    if picked in online:
        return picked
    return online[0] if online else None


# ── 실행할 PC 고르기 (central 전용) ───────────────────────────────────────
def agent_picker(key: str = AGENT_KEY) -> str | None:
    """
    어느 PC 에서 돌릴지 고른다.

    Agent 가 살아 있는 PC 만 보여준다. 꺼진 PC 를 고르면 Job 이 큐에 쌓인 채
    아무도 안 가져가고, 사람은 '실행이 안 된다' 고만 느낀다.
    """
    rows = agent_rows()
    online = [a for a in rows if a.get("online")]
    if not rows:
        st.warning("연결된 PC 가 없습니다. 각자 PC 에서 Agent 를 실행하세요.", icon="🔌")
        return None
    if not online:
        names = ", ".join(a["agent"] for a in rows)
        st.warning(f"켜져 있는 PC 가 없습니다. (마지막 접속: {names})", icon="🔌")
        return None

    labels = {}
    for a in online:
        c = f" · Chrome {a.get('chromes_alive')}/{a.get('chromes_total')}" \
            if a.get("chromes_total") else ""
        labels[a["agent"]] = f"{a['agent']}{c}"

    names = list(labels)
    prev = st.session_state.get(key)
    idx = names.index(prev) if prev in names else 0
    picked = st.selectbox("실행할 PC", names, index=idx, key=key,
                          format_func=lambda x: labels.get(x, x))
    return picked


def agent_status_line() -> str:
    rows = Q.agents()
    on = [a for a in rows if a.get("online")]
    if not rows:
        return "연결된 PC 없음"
    return f"PC {len(on)}/{len(rows)} 켜짐 — " + ", ".join(a["agent"] for a in on)


# ── 실행 요청 ─────────────────────────────────────────────────────────────
# ── Chrome (짧은 작업, 끝날 때까지 기다린다) ──────────────────────────────
CHROME_WAIT = 90.0            # 이보다 오래 걸리면 무언가 잘못된 것이다


def chrome_call(action: str, params: dict, agent: str | None = None,
                timeout: float = CHROME_WAIT) -> dict:
    """
    Chrome 프로필 실행 / 로그인 확인.

    local  : 이 PC 에서 바로 한다.
    central: 화면은 클라우드에서 돈다. 거기서 Chrome 을 켜 봐야 그 서버의
             Chrome 이 켜질 뿐이다. 그래서 Agent 에게 시키고 끝날 때까지
             기다린다. 마감·오픈과 달리 몇십 초짜리라 기다려도 된다.

    반환 {"ok": bool, "message": str, "summary": dict}
    """
    body = dict(params or {})
    body["action"] = action

    if not is_central():
        from core.routing import get_routing
        r = get_routing()
        try:
            if action == "ensure":
                res = r.ensure(str(body.get("key") or ""),
                               wait_seconds=float(body.get("wait") or 35))
                return {"ok": bool(res.get("ready")),
                        "message": str(res.get("message") or ""),
                        "summary": {"ready": bool(res.get("ready"))}}
            if action == "check_login":
                out = {}
                for item in (body.get("targets") or []):
                    k = str(item.get("key") or "")
                    got = r.check_login(k, list(item.get("channels") or []),
                                        timeout=45).get("results", [])
                    out[k] = [{"channel": x.get("channel"), "state": x.get("state")}
                              for x in got]
                return {"ok": True, "message": "", "summary": {"login": out}}
        except Exception as e:
            return {"ok": False, "message": str(e)[:200], "summary": {}}
        return {"ok": False, "message": f"알 수 없는 작업: {action}", "summary": {}}

    agent = agent or current_agent()
    if not agent:
        return {"ok": False, "summary": {},
                "message": "연결된 PC 가 없습니다. 그 PC 에서 'Agent 켜기' 를 실행하세요."}

    title = {"ensure": f"Chrome 실행 {body.get('key','')}",
             "check_login": "로그인 확인"}.get(action, f"Chrome {action}")
    try:
        job = Q.create("chrome", agent, title, body)
    except Exception as e:
        return {"ok": False, "message": f"작업을 보내지 못했습니다: {str(e)[:150]}",
                "summary": {}}

    jid = job["id"]
    t0 = time.time()
    last = ""
    while time.time() - t0 < timeout:
        time.sleep(1.5)
        d = Q.get(jid) or {}
        logs = d.get("logs") or []
        if logs:
            last = str(logs[-1].get("line") or "")
        if d.get("status") in ("done", "error"):
            summary = d.get("summary") or {}
            err = d.get("error")
            try:
                Q.remove(jid)
            except Exception:
                pass
            if err:
                return {"ok": False, "message": str(err)[:200], "summary": summary}
            msg = str(summary.get("message") or last or "완료")
            ok = summary.get("ready", True) if action == "ensure" else True
            return {"ok": bool(ok), "message": msg, "summary": summary}

    try:
        Q.cancel(jid, "시간 초과")
    except Exception:
        pass
    return {"ok": False, "summary": {},
            "message": (f"{int(timeout)}초 안에 끝나지 않았습니다. "
                        f"{agent} 의 Agent 창이 켜져 있는지 확인하세요.")}


def start(kind: str, title: str, params: dict, total: int = 0,
          agent: str | None = None, pre_results: list | None = None) -> tuple[bool, str]:
    """
    kind: "close" | "open"
    반환 (시작됨, 오류메시지)
    """
    if is_central():
        if not agent:
            return False, "실행할 PC 를 고르세요."
        job = Q.create(kind, agent, title, params)
        if pre_results:
            Q.append(job["id"], results=pre_results)   # 미구현 스킵 같은 것
        st.session_state["watch_job"] = job["id"]
        return True, ""

    # local
    job = Job(kind, title, total=total)
    for r in (pre_results or []):
        job.result(r)

    def fn(j):
        if kind == "close":
            close_runner.run(j, params.get("date"), params.get("agencies") or [],
                             params.get("regions") or [],
                             dry_run=bool(params.get("dry_run")))
        else:
            _run_open_local(j, params)

    return MANAGER.start(job, fn)


def _run_open_local(job, p: dict) -> None:
    """local 모드의 오픈. Agent 쪽 _run_open 과 같은 순서를 유지한다."""
    plan = p.get("plan") or []
    target = p.get("date")
    channels = p.get("channels") or []
    dry = bool(p.get("dry_run"))
    mine = [x for x in plan if x.get("channel") in channels]

    runners = []
    if "KLOOK" in channels and any(x["channel"] == "KLOOK" for x in mine):
        runners.append(("KLOOK", lambda: klook_open.run(job, mine, target)))
    if "MRT" in channels and any(x["channel"] == "MRT" for x in mine):
        runners.append(("MRT", lambda: mrt_open.run(job, mine, target, dry_run=dry)))
    if "GG" in channels and any(x["channel"] == "GG" for x in mine):
        runners.append(("GG", lambda: gg_open.run(job, mine, target, dry_run=dry)))

    for name, fn in runners:
        if job.stopping:
            job.log("SYS", f"[중단] {name} 실행 안 함")
            break
        job.log("SYS", f"===== {name} 오픈 시작 =====")
        try:
            fn()
        except Exception as e:
            job.log("SYS", f"[오류] {name}: {e}")
        # 각 러너가 job.done() 을 부르므로 다음 러너를 위해 되돌린다
        job.finished = False
    job.done(summary={"channels": [n for n, _ in runners], "dry_run": dry})


# ── 진행 상황 보기 ────────────────────────────────────────────────────────
def snapshot(kind: str | None = None) -> dict:
    """
    화면이 그릴 실행 상태. 두 모드가 같은 모양으로 돌려준다.
        {kind, title, running, started, elapsed, logs, results, summary, error}
    """
    if not is_central():
        snap = MANAGER.snapshot(0)
        if kind and snap.get("kind") not in (None, kind):
            return {}
        return snap

    jid = st.session_state.get("watch_job")
    d = Q.get(jid) if jid else None
    if not d:
        # 이 종류의 가장 최근 것을 보여준다
        cand = [x for x in Q.active() if not kind or x.get("kind") == kind]
        if not cand:
            cand = [x for x in Q.recent(5) if not kind or x.get("kind") == kind][:1]
        d = cand[-1] if cand else None
    if not d:
        return {}
    if kind and d.get("kind") != kind:
        return {}
    return {
        "kind": d.get("kind"),
        "title": d.get("title"),
        "running": d.get("status") in ("pending", "running"),
        "pending": d.get("status") == "pending",
        "agent": d.get("agent"),
        "started": d.get("claimed_at") or d.get("created"),
        "elapsed": 0,
        "logs": d.get("logs") or [],
        "results": d.get("results") or [],
        "summary": d.get("summary"),
        "error": d.get("error"),
        "job_id": d.get("id"),
        "stale": d.get("stale"),
    }


def stop(snap: dict) -> None:
    if is_central():
        jid = snap.get("job_id")
        if jid:
            if snap.get("pending"):
                Q.cancel(jid)
            else:
                Q.request_stop(jid)
    else:
        MANAGER.stop()


def busy(kind: str | None = None) -> bool:
    """지금 뭔가 돌고 있나. 돌고 있으면 실행 버튼을 잠근다."""
    if is_central():
        return any(True for _ in Q.active())
    from core.jobs import read_lock
    return bool(read_lock()) or bool(MANAGER.snapshot(0).get("running"))
