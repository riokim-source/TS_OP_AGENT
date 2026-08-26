# -*- coding: utf-8 -*-
"""
queue_file.py
파일로 구현한 작업 큐. 중앙과 Agent 가 같은 PC/같은 네트워크 폴더를 볼 때 쓴다.

Streamlit Cloud 에서는 파일이 남지 않으므로 queue_firebase 를 쓴다.
어느 쪽을 쓸지는 core/queue.py 가 정한다.

왜 이런 구조인가
    웹페이지 코드는 중앙 서버에서 돈다. 그 서버는 자기 Chrome 만 만질 수 있다.
    OTA 로그인은 각자 PC 의 Chrome 프로필에 있으므로, 실제 봇은 그 PC 에서 돌아야 한다.
    그래서 중앙은 '무엇을 할지' 만 정하고, 개인 PC 의 Agent 가 가져가 실행한다.

파일 하나 = 작업 하나. DB 를 두지 않는 이유는 사람이 눈으로 열어볼 수 있어야
아침에 문제가 났을 때 바로 확인되기 때문이다.

    jobs/<id>.pending    아직 아무도 안 가져감
    jobs/<id>.running    누가 가져가서 도는 중
    jobs/<id>.done       끝남 (성공/실패 모두)

가져가기는 os.rename 으로 한다. Windows 에서 대상 파일이 이미 있으면 예외가 나므로,
Agent 가 여럿이어도 한 명만 이긴다. 락 파일이 따로 필요 없다.
"""
from __future__ import annotations

import json
import os
import secrets
import time
from datetime import datetime
from pathlib import Path

from .paths import DATA_DIR

JOBS_DIR = DATA_DIR / "jobs"
AGENTS_DIR = DATA_DIR / "agents"

# 이 시간 동안 소식이 없으면 그 Agent 는 꺼진 것으로 본다
AGENT_STALE_SEC = 30
# 가져간 뒤 이 시간 동안 진행 보고가 없으면 죽은 작업으로 본다
JOB_STALE_SEC = 30 * 60
KEEP_DONE = 200


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _write_atomic(path: Path, data: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def _read(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _ensure() -> None:
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    AGENTS_DIR.mkdir(parents=True, exist_ok=True)


# ── Agent 등록 / 상태 ─────────────────────────────────────────────────────
def heartbeat(agent: str, info: dict | None = None) -> None:
    """Agent 가 살아있다고 알린다. 화면의 'PC 목록' 이 이걸 본다."""
    _ensure()
    name = safe_name(agent)
    if not name:
        return
    d = _read(AGENTS_DIR / f"{name}.json") or {}
    d.update({"agent": agent, "at": time.time(), "at_text": _now()})
    if info:
        d.update({k: v for k, v in info.items() if k not in ("agent", "at", "at_text")})
    _write_atomic(AGENTS_DIR / f"{name}.json", d)


def agents() -> list[dict]:
    """등록된 PC 목록. online=최근에 소식이 있었나."""
    _ensure()
    out = []
    for f in sorted(AGENTS_DIR.glob("*.json")):
        d = _read(f)
        if not d:
            continue
        d["online"] = (time.time() - float(d.get("at") or 0)) < AGENT_STALE_SEC
        d["idle_sec"] = int(time.time() - float(d.get("at") or 0))
        out.append(d)
    return out


def safe_name(agent: str) -> str:
    keep = "".join(c if (c.isalnum() or c in "-_") else "_" for c in str(agent or ""))
    return keep.strip("_")[:60]


# ── 작업 만들기 ───────────────────────────────────────────────────────────
def create(kind: str, agent: str, title: str, params: dict,
           created_by: str = "") -> dict:
    """
    kind: "close" | "open"
    agent: 실행할 PC 이름 (agents() 의 agent 값)
    params: 그 실행기가 그대로 쓰는 인자
    """
    _ensure()
    job = {
        "id": datetime.now().strftime("%Y%m%d_%H%M%S_") + secrets.token_hex(3),
        "kind": kind,
        "agent": agent,
        "title": title,
        "params": params,
        "created": _now(),
        "created_by": created_by or "",
        "status": "pending",
        "logs": [],
        "results": [],
        "summary": None,
        "error": None,
        "claimed_at": None,
        "finished_at": None,
        "beat": time.time(),
    }
    _write_atomic(JOBS_DIR / f"{job['id']}.pending", job)
    return job


def path_of(job_id: str) -> Path | None:
    for ext in ("running", "pending", "done"):
        p = JOBS_DIR / f"{job_id}.{ext}"
        if p.exists():
            return p
    return None


def get(job_id: str) -> dict | None:
    p = path_of(job_id)
    return _read(p) if p else None


def pending_for(agent: str) -> list[dict]:
    _ensure()
    out = []
    for f in sorted(JOBS_DIR.glob("*.pending")):
        d = _read(f)
        if d and d.get("agent") == agent:
            out.append(d)
    return out


def claim(agent: str) -> dict | None:
    """
    이 Agent 앞으로 온 작업 하나를 가져간다. 없으면 None.

    os.rename 은 Windows 에서 대상이 있으면 실패한다. 그래서 두 Agent 가
    같은 작업을 동시에 집어도 한 명만 성공한다.
    """
    _ensure()
    for f in sorted(JOBS_DIR.glob("*.pending")):
        d = _read(f)
        if not d or d.get("agent") != agent:
            continue
        target = f.with_suffix(".running")
        try:
            os.rename(f, target)
        except OSError:
            continue        # 남이 먼저 가져갔다
        d["status"] = "running"
        d["claimed_at"] = _now()
        d["beat"] = time.time()
        _write_atomic(target, d)
        return d
    return None


# ── 진행 보고 ─────────────────────────────────────────────────────────────
def append(job_id: str, logs: list | None = None, results: list | None = None) -> bool:
    p = JOBS_DIR / f"{job_id}.running"
    d = _read(p)
    if not d:
        return False
    if logs:
        d["logs"].extend(logs)
        if len(d["logs"]) > 6000:
            del d["logs"][: len(d["logs"]) - 6000]
    if results:
        d["results"].extend(results)
    d["beat"] = time.time()
    _write_atomic(p, d)
    return True


def finish(job_id: str, summary: dict | None = None, error: str | None = None) -> bool:
    p = JOBS_DIR / f"{job_id}.running"
    d = _read(p)
    if not d:
        return False
    d["status"] = "error" if error else "done"
    d["summary"] = summary
    d["error"] = error
    d["finished_at"] = _now()
    d["beat"] = time.time()
    _write_atomic(p, d)
    os.replace(p, JOBS_DIR / f"{job_id}.done")
    _prune()
    return True


def cancel(job_id: str, reason: str = "사용자 중단") -> bool:
    """대기 중인 작업만 취소한다. 이미 도는 것은 Agent 가 멈춰야 한다."""
    p = JOBS_DIR / f"{job_id}.pending"
    d = _read(p)
    if not d:
        return False
    d["status"] = "error"
    d["error"] = reason
    d["finished_at"] = _now()
    _write_atomic(p, d)
    os.replace(p, JOBS_DIR / f"{job_id}.done")
    return True


def request_stop(job_id: str) -> bool:
    """실행 중인 작업에 '멈춰달라' 고 표시한다. Agent 가 보고 멈춘다."""
    p = JOBS_DIR / f"{job_id}.running"
    d = _read(p)
    if not d:
        return False
    d["stop"] = True
    _write_atomic(p, d)
    return True


def stop_requested(job_id: str) -> bool:
    d = _read(JOBS_DIR / f"{job_id}.running")
    return bool(d and d.get("stop"))


# ── 조회 ─────────────────────────────────────────────────────────────────
def active() -> list[dict]:
    """지금 대기/실행 중인 작업. 오래 소식 없는 실행은 죽은 것으로 표시."""
    _ensure()
    out = []
    for f in sorted(JOBS_DIR.glob("*.pending")) + sorted(JOBS_DIR.glob("*.running")):
        d = _read(f)
        if not d:
            continue
        if d.get("status") == "running":
            d["stale"] = (time.time() - float(d.get("beat") or 0)) > JOB_STALE_SEC
        out.append(d)
    return out


def recent(limit: int = 30) -> list[dict]:
    _ensure()
    files = sorted(JOBS_DIR.glob("*.done"), reverse=True)[:limit]
    return [d for d in (_read(f) for f in files) if d]


def _prune() -> None:
    try:
        files = sorted(JOBS_DIR.glob("*.done"), reverse=True)
        for f in files[KEEP_DONE:]:
            f.unlink(missing_ok=True)
    except Exception:
        pass


# ── 팀이 같이 보는 작은 메모 (파일 백엔드) ─────────────────────────────────
SHARED_DIR = DATA_DIR / "shared"


def _shared_path(key: str):
    name = "".join(c if (c.isalnum() or c in "-_") else "_" for c in str(key or ""))
    return SHARED_DIR / f"{name.strip('_')[:60]}.json"


def shared_get(key: str):
    p = _shared_path(key)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def shared_set(key: str, value) -> bool:
    try:
        SHARED_DIR.mkdir(parents=True, exist_ok=True)
        _shared_path(key).write_text(
            json.dumps(value, ensure_ascii=False, indent=1), encoding="utf-8")
        return True
    except Exception:
        return False
