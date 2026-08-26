# -*- coding: utf-8 -*-
"""
queue_firebase.py
Firebase Realtime Database 를 '만나는 지점' 으로 쓰는 작업 큐.

왜 필요한가
    화면은 Streamlit Cloud 에서 돌고, 봇은 개인 PC 에서 돈다.
    Streamlit 은 외부에서 부를 창구를 만들 수 없고, 클라우드는 사내 PC 안으로
    들어올 수 없다. 그래서 둘 다 바깥에서 닿는 곳이 하나 필요하다.

⚠️ 여기는 '저장소' 가 아니라 '중계 지점' 이다.
    끝난 작업은 지운다. 영구 기록(30일)은 실행한 그 PC 에 남는다.
    바깥에 쌓이는 것이 없어야 한다.

인증
    서비스 계정 JSON 으로 액세스 토큰을 만들어 REST 로 호출한다.
      개인 PC       : hub/data/firebase_service_account.json
      Streamlit Cloud: st.secrets["firebase"] (앱 설정에 붙여넣기)
    이 파일은 저장소에 올리지 않는다 (.gitignore).

데이터 모양
    /agents/<이름>         살아있다는 표시 + Chrome 상태
    /jobs/<작업id>         작업 본체 (logs 는 push 키로 append)
    /jobs/<작업id>/logs/<pushkey>
"""
from __future__ import annotations

import json
import secrets as _secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

from .paths import DATA_DIR

SA_FILE = DATA_DIR / "firebase_service_account.json"
CONFIG = DATA_DIR / "firebase.json"          # {"database_url": "https://xxx.firebaseio.com"}

AGENT_STALE_SEC = 30
JOB_STALE_SEC = 30 * 60
SCOPES = ["https://www.googleapis.com/auth/userinfo.email",
          "https://www.googleapis.com/auth/firebase.database"]

_lock = threading.Lock()
_token: dict = {"value": None, "exp": 0.0}
_seq: dict = {"n": 0}


# ── 설정 / 인증 ───────────────────────────────────────────────────────────
def _secrets_blob() -> dict | None:
    """Streamlit Cloud 면 st.secrets, 아니면 로컬 파일."""
    try:
        import streamlit as st
        blob = st.secrets.get("firebase")          # type: ignore[attr-defined]
        if blob:
            return dict(blob)
    except Exception:
        pass
    if SA_FILE.exists():
        try:
            return json.loads(SA_FILE.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None


def database_url() -> str:
    blob = _secrets_blob() or {}
    url = str(blob.get("database_url") or "").strip()
    if not url and CONFIG.exists():
        try:
            url = str(json.loads(CONFIG.read_text(encoding="utf-8")).get("database_url") or "")
        except Exception:
            url = ""
    return url.rstrip("/")


def available() -> tuple[bool, str]:
    if not _secrets_blob():
        return False, ("Firebase 서비스 계정이 없습니다. "
                       f"{SA_FILE} 를 두거나 Streamlit secrets 에 [firebase] 를 넣으세요.")
    if not database_url():
        return False, "Firebase database_url 이 설정돼 있지 않습니다."
    try:
        _access_token()
    except Exception as e:
        return False, f"Firebase 인증 실패: {str(e)[:120]}"
    return True, database_url()


def _access_token() -> str:
    """서비스 계정으로 OAuth2 토큰을 받는다. 만료 5분 전에 갱신."""
    with _lock:
        if _token["value"] and time.time() < _token["exp"] - 300:
            return _token["value"]
        blob = _secrets_blob()
        if not blob:
            raise RuntimeError("Firebase 서비스 계정이 없습니다.")
        try:
            from google.oauth2 import service_account
            import google.auth.transport.requests as greq
        except ImportError as e:
            raise RuntimeError(
                "google-auth 가 필요합니다. pip install google-auth") from e
        info = {k: v for k, v in blob.items() if k != "database_url"}
        cred = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
        cred.refresh(greq.Request())
        _token["value"] = cred.token
        _token["exp"] = cred.expiry.timestamp() if cred.expiry else time.time() + 3000
        return _token["value"]


# ── REST 호출 ─────────────────────────────────────────────────────────────
def _call(method: str, path: str, body=None, params: dict | None = None,
          etag: bool = False, if_match: str | None = None, timeout: float = 15.0):
    url = f"{database_url()}/{path.lstrip('/')}.json"
    q = dict(params or {})
    if q:
        url += "?" + urllib.parse.urlencode(q)
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    headers = {"Authorization": f"Bearer {_access_token()}",
               "Content-Type": "application/json"}
    if etag:
        headers["X-Firebase-ETag"] = "true"
    if if_match:
        headers["if-match"] = if_match
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            out = json.loads(raw) if raw else None
            return (out, r.headers.get("ETag")) if etag else out
    except urllib.error.HTTPError as e:
        if if_match and e.code == 412:          # 남이 먼저 바꿨다
            return (None, None) if etag else None
        raise


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def safe_name(agent: str) -> str:
    keep = "".join(c if (c.isalnum() or c in "-_") else "_" for c in str(agent or ""))
    return keep.strip("_")[:60]


# ── Agent 등록 / 상태 ─────────────────────────────────────────────────────
def heartbeat(agent: str, info: dict | None = None) -> None:
    name = safe_name(agent)
    if not name:
        return
    body = {"agent": agent, "at": time.time(), "at_text": _now()}
    if info:
        body.update({k: v for k, v in info.items()
                     if k not in ("agent", "at", "at_text")})
    try:
        _call("PATCH", f"agents/{name}", body)
    except Exception:
        pass        # 심장박동 실패로 작업을 막지 않는다


def agents() -> list[dict]:
    try:
        got = _call("GET", "agents") or {}
    except Exception:
        return []
    out = []
    for _k, d in (got.items() if isinstance(got, dict) else []):
        if not isinstance(d, dict):
            continue
        d = dict(d)
        d["online"] = (time.time() - float(d.get("at") or 0)) < AGENT_STALE_SEC
        d["idle_sec"] = int(time.time() - float(d.get("at") or 0))
        out.append(d)
    return sorted(out, key=lambda x: x.get("agent") or "")


# ── 작업 ─────────────────────────────────────────────────────────────────
def create(kind: str, agent: str, title: str, params: dict,
           created_by: str = "") -> dict:
    job = {
        "id": datetime.now().strftime("%Y%m%d_%H%M%S_") + _secrets.token_hex(3),
        "kind": kind, "agent": agent, "title": title, "params": params,
        "created": _now(), "created_by": created_by or "",
        "status": "pending", "summary": None, "error": None,
        "claimed_at": None, "finished_at": None, "beat": time.time(),
    }
    _call("PUT", f"jobs/{job['id']}", job)
    return {**job, "logs": [], "results": []}


def _logs_of(job_id: str, since: str | None = None) -> list:
    params = {"orderBy": '"$key"'}
    if since:
        params["startAt"] = json.dumps(since)
    try:
        got = _call("GET", f"jobs/{job_id}/logs", params=params) or {}
    except Exception:
        return []
    if not isinstance(got, dict):
        return []
    out = []
    for k in sorted(got):
        if since and k <= since:
            continue
        v = got[k]
        if isinstance(v, dict):
            out.append({**v, "_k": k})
    return out


def get(job_id: str, with_logs: bool = True, since: str | None = None) -> dict | None:
    if not job_id:
        return None
    try:
        d = _call("GET", f"jobs/{job_id}", params={"shallow": "false"})
    except Exception:
        return None
    if not isinstance(d, dict):
        return None
    d.pop("logs", None)
    res = d.get("results")
    d["results"] = [v for _k, v in sorted((res or {}).items())] if isinstance(res, dict) \
        else (res or [])
    d["logs"] = _logs_of(job_id, since) if with_logs else []
    if d.get("status") == "running":
        d["stale"] = (time.time() - float(d.get("beat") or 0)) > JOB_STALE_SEC
    return d


def claim(agent: str) -> dict | None:
    """
    이 Agent 앞으로 온 대기 작업 하나를 가져간다.

    ETag 비교 교환으로 잡는다. 두 Agent 가 같은 작업을 동시에 집어도
    한 명만 성공한다 (412 가 오면 남이 먼저 가져간 것).
    """
    try:
        jobs = _call("GET", "jobs", params={"shallow": "true"}) or {}
    except Exception:
        return None
    for jid in sorted(jobs if isinstance(jobs, dict) else {}):
        try:
            cur, tag = _call("GET", f"jobs/{jid}/status", etag=True)
        except Exception:
            continue
        if cur != "pending":
            continue
        try:
            who = _call("GET", f"jobs/{jid}/agent")
        except Exception:
            continue
        if who != agent:
            continue
        got, _ = _call("PUT", f"jobs/{jid}/status", "running", etag=True, if_match=tag)
        if got != "running":
            continue        # 남이 먼저 가져갔다
        _call("PATCH", f"jobs/{jid}", {"claimed_at": _now(), "beat": time.time()})
        return get(jid)
    return None


def _keys(n: int) -> list:
    """
    시간순으로 정렬되는 키를 직접 만든다.

    Firebase 의 push 키를 쓰려면 항목마다 POST 를 한 번씩 해야 하는데,
    로그가 수천 줄이면 그만큼 왕복이 생겨 실행이 끝나지 않는다.
    키를 직접 만들면 여러 줄을 PATCH 한 번으로 보낼 수 있다.
    """
    with _lock:
        base = _seq["n"]
        _seq["n"] = base + n
    ms = int(time.time() * 1000)
    return [f"{ms:013d}_{base + i:06d}" for i in range(n)]


def append(job_id: str, logs: list | None = None, results: list | None = None) -> bool:
    """
    로그·결과·심장박동을 PATCH 한 번으로 보낸다.

    Firebase REST 는 키에 '/' 를 넣으면 하위 경로를 한꺼번에 갱신한다.
    그래서 왕복이 항목 수와 무관하게 1회로 고정된다.
    """
    if not job_id:
        return False
    body: dict = {"beat": time.time()}
    logs = list(logs or [])
    results = list(results or [])
    for k, v in zip(_keys(len(logs)), logs):
        body[f"logs/{k}"] = v
    for k, v in zip(_keys(len(results)), results):
        body[f"results/{k}"] = v
    try:
        _call("PATCH", f"jobs/{job_id}", body, timeout=25.0)
        return True
    except Exception:
        return False


def finish(job_id: str, summary: dict | None = None, error: str | None = None) -> bool:
    if not job_id:
        return False
    try:
        _call("PATCH", f"jobs/{job_id}", {
            "status": "error" if error else "done",
            "summary": summary, "error": error,
            "finished_at": _now(), "beat": time.time()})
        return True
    except Exception:
        return False


def remove(job_id: str) -> None:
    """중계 지점에서 지운다. 영구 기록은 실행한 PC 에 남는다."""
    try:
        _call("DELETE", f"jobs/{job_id}")
    except Exception:
        pass


def cancel(job_id: str, reason: str = "사용자 중단") -> bool:
    d = get(job_id, with_logs=False)
    if not d or d.get("status") != "pending":
        return False
    return finish(job_id, error=reason)


def request_stop(job_id: str) -> bool:
    try:
        _call("PATCH", f"jobs/{job_id}", {"stop": True})
        return True
    except Exception:
        return False


_stop_cache: dict = {"at": 0.0, "id": None, "val": False}
STOP_CHECK_SEC = 8.0


def stop_requested(job_id: str) -> bool:
    """
    중단 요청 확인. 매 로그마다 물어보면 왕복만 늘어난다.
    몇 초 캐시해도 사람이 느끼는 차이는 없다.
    """
    now = time.time()
    if _stop_cache["id"] == job_id and (now - _stop_cache["at"]) < STOP_CHECK_SEC:
        return _stop_cache["val"]
    try:
        val = bool(_call("GET", f"jobs/{job_id}/stop", timeout=8.0))
    except Exception:
        val = _stop_cache["val"] if _stop_cache["id"] == job_id else False
    _stop_cache.update({"at": now, "id": job_id, "val": val})
    return val


def active() -> list[dict]:
    try:
        jobs = _call("GET", "jobs", params={"shallow": "true"}) or {}
    except Exception:
        return []
    out = []
    for jid in sorted(jobs if isinstance(jobs, dict) else {}):
        d = get(jid, with_logs=False)
        if d and d.get("status") in ("pending", "running"):
            out.append(d)
    return out


def recent(limit: int = 30) -> list[dict]:
    """
    중계 지점에는 끝난 작업을 남기지 않는다. 지워지기 전 잠깐 보이는 것만 돌려준다.
    지난 기록은 실행한 PC 의 hub/logs/runs 에서 본다.
    """
    try:
        jobs = _call("GET", "jobs", params={"shallow": "true"}) or {}
    except Exception:
        return []
    out = []
    for jid in sorted(jobs if isinstance(jobs, dict) else {}, reverse=True)[:limit]:
        d = get(jid, with_logs=False)
        if d and d.get("status") in ("done", "error"):
            out.append(d)
    return out

# ── 팀이 같이 보는 작은 메모 (오픈 목록 등) ────────────────────────────────
def shared_get(key: str):
    """없으면 None. 실패해도 화면을 막지 않는다."""
    try:
        return _call("GET", f"shared/{safe_name(key)}")
    except Exception:
        return None


def shared_set(key: str, value) -> bool:
    """value 가 None 이면 지운다. (PUT null 은 REST 에서 거절된다)"""
    try:
        if value is None:
            _call("DELETE", f"shared/{safe_name(key)}")
        else:
            _call("PUT", f"shared/{safe_name(key)}", value)
        return True
    except Exception:
        return False
