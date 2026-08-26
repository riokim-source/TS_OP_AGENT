# -*- coding: utf-8 -*-
"""
queue.py
중앙이 만든 작업을 개인 PC 의 Agent 가 가져가 실행하는 큐.

이 파일은 '어느 방식으로 주고받을지' 만 정한다. 실제 구현은 둘 중 하나다.

    file      queue_file.py       같은 PC / 같은 네트워크 폴더를 볼 때
    firebase  queue_firebase.py   화면이 Streamlit Cloud 에 있을 때

  set LMHUB_QUEUE=firebase        (없으면 file)

부르는 쪽(화면·Agent)은 이 파일만 import 하고 어느 방식인지 신경 쓰지 않는다.
그래야 나중에 방식을 바꿔도 화면 코드를 안 고친다.

⚠️ 어느 방식이든 큐는 '저장소' 가 아니라 '중계 지점' 이다.
   영구 기록(30일)은 실행한 PC 의 hub/logs/runs 에 남는다.
"""
from __future__ import annotations

import os


def backend_name() -> str:
    return "firebase" if os.environ.get("LMHUB_QUEUE") == "firebase" else "file"


def _be():
    if backend_name() == "firebase":
        from . import queue_firebase as impl
    else:
        from . import queue_file as impl
    return impl


def available() -> tuple[bool, str]:
    """이 방식이 지금 쓸 수 있는 상태인가. 화면에서 미리 알려주기 위한 것."""
    impl = _be()
    fn = getattr(impl, "available", None)
    if fn:
        return fn()
    return True, str(getattr(impl, "JOBS_DIR", ""))


# ── Agent ────────────────────────────────────────────────────────────────
def heartbeat(agent: str, info: dict | None = None) -> None:
    return _be().heartbeat(agent, info)


def agents() -> list[dict]:
    return _be().agents()


def safe_name(agent: str) -> str:
    return _be().safe_name(agent)


# ── 작업 ─────────────────────────────────────────────────────────────────
def create(kind: str, agent: str, title: str, params: dict,
           created_by: str = "") -> dict:
    return _be().create(kind, agent, title, params, created_by)


def get(job_id: str, **kw) -> dict | None:
    impl = _be()
    try:
        return impl.get(job_id, **kw)
    except TypeError:
        return impl.get(job_id)      # file 백엔드는 추가 인자를 안 받는다


def claim(agent: str) -> dict | None:
    return _be().claim(agent)


def append(job_id: str, logs: list | None = None, results: list | None = None) -> bool:
    return _be().append(job_id, logs, results)


def finish(job_id: str, summary: dict | None = None, error: str | None = None) -> bool:
    return _be().finish(job_id, summary, error)


def remove(job_id: str) -> None:
    fn = getattr(_be(), "remove", None)
    if fn:
        fn(job_id)


def cancel(job_id: str, reason: str = "사용자 중단") -> bool:
    return _be().cancel(job_id, reason)


def request_stop(job_id: str) -> bool:
    return _be().request_stop(job_id)


def stop_requested(job_id: str) -> bool:
    return _be().stop_requested(job_id)


def active() -> list[dict]:
    return _be().active()


def recent(limit: int = 30) -> list[dict]:
    return _be().recent(limit)


def shared_get(key: str):
    """팀이 같이 보는 메모 읽기 (오픈 목록 등)."""
    fn = getattr(_be(), "shared_get", None)
    return fn(key) if fn else None


def shared_set(key: str, value) -> bool:
    fn = getattr(_be(), "shared_set", None)
    return bool(fn(key, value)) if fn else False
