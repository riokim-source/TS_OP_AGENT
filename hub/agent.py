# -*- coding: utf-8 -*-
"""
agent.py
개인 PC 에서 상주하며, 중앙 화면이 만든 작업을 자기 Chrome 으로 실행한다.

    python hub/agent.py

왜 필요한가
    화면은 Streamlit Cloud 에서 돈다. 그 서버는 자기 Chrome 만 만질 수 있다.
    OTA 로그인은 각자 PC 의 Chrome 프로필에 있으므로, 실제 봇은 그 PC 에서 돌아야 한다.
    이 Agent 가 '그 PC 에서 돌리는 쪽' 이다.

    ⚠️ 로그인 정보는 이 PC 를 떠나지 않는다. 중계 지점에는 '무슨 상품 몇 개' 만 오간다.

설정
    hub/data/firebase.json                  {"database_url": "https://xxx.firebaseio.com"}
    hub/data/firebase_service_account.json  Firebase 서비스 계정 키
    hub/data/agent_config.json              {"agent": "이 PC 이름"}  (비우면 자동)

실행 로직은 새로 짜지 않는다. 지금 쓰는 core.close.runner / core.opens 를 그대로 부른다.
"""
from __future__ import annotations

import json
import os
import socket
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

HUB_DIR = Path(__file__).resolve().parent
if str(HUB_DIR) not in sys.path:
    sys.path.insert(0, str(HUB_DIR))

os.environ.setdefault("LMHUB_QUEUE", "firebase")

# ⚠️ Windows 콘솔은 기본이 cp949 라 한글 일부와 em-dash 에서 죽는다.
#    (2026-08-25: '—' 하나 때문에 Agent 가 배너도 못 찍고 종료했다)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from core import paths                                  # noqa: E402
from core import queue as Q                             # noqa: E402
from core.close import runner as close_runner           # noqa: E402
from core.jobs import Job                               # noqa: E402
from core.opens import gg_open, klook_open, mrt_open    # noqa: E402
from core.routing import get_routing                    # noqa: E402

CONFIG = paths.DATA_DIR / "agent_config.json"
AGENT_NAME = ""          # main() 에서 채운다 (기록에 '어느 PC' 를 남기려고)
POLL_IDLE = 3.0          # 할 일 없을 때 물어보는 간격(초)
FLUSH_SEC = 2.0          # 로그를 중계 지점에 보내는 간격
BEAT_SEC = 10.0          # 살아있다고 알리는 간격


def default_agent_name() -> str:
    return f"{os.environ.get('COMPUTERNAME') or socket.gethostname()}/" \
           f"{os.environ.get('USERNAME') or 'user'}"


def load_config() -> dict:
    c = {}
    if CONFIG.exists():
        try:
            c = json.loads(CONFIG.read_text(encoding="utf-8"))
        except Exception:
            c = {}
    c.setdefault("agent", default_agent_name())
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(json.dumps(c, ensure_ascii=False, indent=1), encoding="utf-8")
    return c


class RemoteJob:
    """
    core.close.runner / core.opens 가 기대하는 Job 인터페이스.

    그쪽 코드는 job.log() / job.result() / job.done() / job.stopping 만 쓴다.
    여기서는 그걸 중계 지점으로 보내고, 동시에 이 PC 에도 기록으로 남긴다.
    (중계 지점은 끝나면 지우므로, 영구 기록은 이 PC 에 있어야 한다)
    """

    def __init__(self, job_id: str, kind: str, title: str):
        self.id = job_id
        self.local = Job(kind, title)      # hub/logs/runs 에 남기는 쪽
        self.kind = kind
        self.title = title
        self.total = 0
        self.finished = False
        self.summary = None
        self.error = None
        self._stop = False
        self._buf_logs: list = []
        self._buf_res: list = []
        self._last = 0.0

    # ── core 가 부르는 것들 ──────────────────────────────────────────────
    def log(self, source: str, line: str) -> None:
        line = str(line).rstrip()
        if not line:
            return
        self.local.log(source, line)
        self._buf_logs.append({"t": datetime.now().strftime("%H:%M:%S"),
                               "src": source, "line": line[:1000]})
        print(f"[{source}] {line}"[:280])
        self._flush()

    def result(self, item: dict) -> None:
        self.local.result(item)
        self._buf_res.append(item)
        self._flush()

    def done(self, summary: dict | None = None, error: str | None = None) -> None:
        self.summary = summary
        self.error = error
        self.finished = True

    def set_stopper(self, fn) -> None:
        self._stopper = fn

    @property
    def stopping(self) -> bool:
        return self._stop

    @property
    def running(self) -> bool:
        return not self.finished

    # ── 전송 ────────────────────────────────────────────────────────────
    def _flush(self, force: bool = False) -> None:
        if not force and (time.time() - self._last) < FLUSH_SEC:
            return
        if not self._buf_logs and not self._buf_res:
            return
        logs, res = self._buf_logs, self._buf_res
        self._buf_logs, self._buf_res = [], []
        self._last = time.time()
        try:
            Q.append(self.id, logs, res)
            if Q.stop_requested(self.id):
                self._stop = True
                stopper = getattr(self, "_stopper", None)
                if stopper:
                    try:
                        stopper()
                    except Exception:
                        pass
        except Exception as e:
            # 중계가 잠깐 끊겨도 작업은 계속한다. 이 PC 기록에는 남는다.
            print(f"[AGENT] 진행 보고 실패(계속 진행): {str(e)[:100]}")

    def finish_up(self) -> None:
        self._flush(force=True)
        self.local.done(summary=self.summary, error=self.error)   # 30일 기록 저장
        try:
            Q.finish(self.id, self.summary, self.error)
        except Exception as e:
            print(f"[AGENT] 완료 보고 실패: {str(e)[:120]}")
        self._save_run_record()

    def _save_run_record(self) -> None:
        """
        중앙 화면에서도 보이도록 '요약' 을 중계 지점에 남긴다.

        전체 로그는 이 PC 의 hub/logs/runs 에 있다. 중계는 저장소가 아니므로
        여기에는 무엇이 언제 몇 건 실패했는지만 둔다. 아침에 실패를 되짚는
        일은 팀 누구나 해야 한다.
        """
        try:
            res = list(self.local.results)
            fail = [r for r in res
                    if "성공" not in str(r.get("result", ""))
                    and "집계" not in str(r.get("result", ""))]
            rec = {
                "id": self.id,
                "agent": AGENT_NAME or "",
                "kind": self.kind,
                "title": self.title,
                "started": self.local.started_text,
                "finished": datetime.now().strftime("%H:%M:%S"),
                "date": datetime.now().strftime("%Y-%m-%d"),
                "seconds": int(time.time() - self.local.started_at),
                "error": self.error or "",
                "summary": self.summary or {},
                "total": len(res),
                "failed": len(fail),
                "results": res[:400],
                "logs": [f"{l.get('t','')} [{l.get('src','')}] {l.get('line','')}"
                         for l in list(self.local.logs)[-300:]],
            }
            Q.run_save(rec)
        except Exception as e:
            print(f"[AGENT] 기록 남기기 실패(무시): {str(e)[:100]}")


# ── 작업 실행 ─────────────────────────────────────────────────────────────
def run_job(agent_name: str, spec: dict) -> None:
    job = RemoteJob(spec["id"], spec.get("kind") or "", spec.get("title") or "")
    p = spec.get("params") or {}
    kind = spec.get("kind")
    job.log("SYS", f"[Agent] {agent_name} 에서 실행")

    try:
        if kind == "close":
            close_runner.run(job, p.get("date"), p.get("agencies") or [],
                             p.get("regions") or [], dry_run=bool(p.get("dry_run")))
        elif kind == "open":
            _run_open(job, p)
        elif kind == "chrome":
            _run_chrome(job, p)
        else:
            job.done(error=f"알 수 없는 작업 종류: {kind}")
    except Exception as e:
        job.log("SYS", f"[오류] {e}")
        for ln in traceback.format_exc().splitlines()[-12:]:
            job.log("SYS", ln)
        job.done(error=str(e))
    finally:
        if not job.finished:
            job.done()
        job.finish_up()


def _run_chrome(job: RemoteJob, p: dict) -> None:
    """
    Chrome 프로필 실행 / 로그인 확인.

    중앙 화면은 자기 서버의 Chrome 밖에 못 만진다. 실제로 필요한 것은
    사람이 앉아 있는 이 PC 의 Chrome 이므로 여기서 대신 한다.
    """
    r = get_routing()
    action = str(p.get("action") or "")

    if action == "ensure":
        key = str(p.get("key") or "")
        job.log("SYS", f"[Chrome] {key} 실행")
        res = r.ensure(key, wait_seconds=float(p.get("wait") or 35))
        job.result({"channel": "CHROME", "region": key,
                    "item": "실행",
                    "result": "성공" if res.get("ready") else "실패",
                    "memo": str(res.get("message") or "")})
        job.done(summary={"action": "ensure", "key": key,
                          "ready": bool(res.get("ready")),
                          "message": res.get("message")})
        return

    if action == "check_login":
        targets = p.get("targets") or []
        out = {}
        for item in targets:
            key = str(item.get("key") or "")
            chans = list(item.get("channels") or [])
            job.log("SYS", f"[Chrome] {key} 로그인 확인 ({', '.join(chans)})")
            try:
                got = r.check_login(key, chans, timeout=45).get("results", [])
                out[key] = [{"channel": x.get("channel"), "state": x.get("state")}
                            for x in got]
            except Exception as e:
                out[key] = [{"channel": "?", "state": f"확인실패: {str(e)[:60]}"}]
            for x in out[key]:
                job.result({"channel": "CHROME", "region": key,
                            "item": str(x.get("channel")),
                            "result": "로그인됨" if x.get("state") == "logged_in"
                                      else "로그인 필요",
                            "memo": str(x.get("state"))})
        job.done(summary={"action": "check_login", "login": out})
        return

    job.done(error=f"알 수 없는 Chrome 작업: {action}")


def _run_open(job: RemoteJob, p: dict) -> None:
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


def chrome_info() -> dict:
    """
    중앙 화면의 'Chrome / 로그인' 칸에 보여줄 정보.

    개수만 보내면 화면에서 '어느 지역이 꺼졌는지' 를 알 수 없다.
    중앙 화면은 자기 PC 의 Chrome 을 볼 수 없으므로, 여기서 보내는 것이 전부다.
    profile_dir 은 각자 PC 의 경로라 굳이 바깥으로 내보내지 않는다.
    """
    try:
        rows = get_routing().status_all()
        slim = [{"key": x.get("key"), "name": x.get("name", ""),
                 "port": x.get("port"), "alive": bool(x.get("alive")),
                 "conflict": bool(x.get("conflict")), "tabs": int(x.get("tabs") or 0),
                 "routed_channels": list(x.get("routed_channels") or []),
                 "open_channels": list(x.get("open_channels") or [])}
                for x in rows]
        return {"chromes_alive": sum(1 for x in slim if x["alive"]),
                "chromes_total": len(slim),
                "chrome_rows": slim,
                "profiles": ",".join(x["key"] for x in slim if x["alive"])}
    except Exception:
        return {}


def _lock_path():
    """
    자물쇠는 PC 한 대에 하나여야 한다.

    폴더 안(hub/data)에 두면 폴더가 다를 때 막히지 않는다. 받은 폴더와
    개발 폴더에서 하나씩 돌면 둘 다 같은 대기줄을 보고, 어느 쪽이 작업을
    가져갈지 알 수 없다. 옛 코드가 든 쪽이 가져가면 조용히 실패한다.
    """
    import tempfile
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("HOME") or tempfile.gettempdir()
    d = Path(base) / "OTABot"
    try:
        d.mkdir(parents=True, exist_ok=True)
        return d / "agent.lock"
    except Exception:
        return Path(tempfile.gettempdir()) / "tourstory_agent.lock"


LOCK = _lock_path()


def _pid_alive(pid: int) -> bool:
    """이 PID 가 아직 살아 있나. (Windows: tasklist 로 확인)"""
    if pid <= 0:
        return False
    try:
        import subprocess
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                             capture_output=True, text=True, errors="replace",
                             timeout=8).stdout
        return str(pid) in out
    except Exception:
        return True          # 확인 못 하면 '살아 있다' 고 본다 (덜 위험한 쪽)


def claim_single_instance() -> bool:
    """
    이 PC 에서 Agent 는 하나만 돈다.

    왜 막나
        두 개가 돌면 둘 다 같은 대기줄에서 작업을 집어 간다. 어느 쪽이
        가져갈지 알 수 없고, 둘이 같은 Chrome 을 동시에 만지면 서로
        페이지를 빼앗는다. 창을 두 번 여는 일은 실제로 잘 일어난다.
        (2026-08-25: 두 개가 떠 있는 걸 발견했다)

    이미 죽은 Agent 가 남긴 자물쇠는 무시하고 새로 잡는다.
    """
    old, old_dir = 0, ""
    try:
        parts = (LOCK.read_text(encoding="utf-8") or "").strip().split("|", 1)
        old = int(parts[0])
        old_dir = parts[1] if len(parts) > 1 else ""
    except Exception:
        old, old_dir = 0, ""
    if old and old != os.getpid() and _pid_alive(old):
        print("=" * 66)
        print(" 이미 이 PC 에서 Agent 가 돌고 있습니다.")
        print("=" * 66)
        print(f"  실행 중인 창: PID {old}")
        if old_dir:
            print(f"  그 창의 폴더 : {old_dir}")
        print(f"  이 창의 폴더 : {HUB_DIR.parent}")
        print()
        print("  창을 두 개 열면 둘이 같은 작업을 서로 가져가려 하고,")
        print("  같은 Chrome 을 동시에 만져 실행이 엉킵니다.")
        print()
        print("  이 창은 닫으셔도 됩니다. 원래 창을 그대로 두세요.")
        print("=" * 66)
        return False
    try:
        LOCK.parent.mkdir(parents=True, exist_ok=True)
        LOCK.write_text(f"{os.getpid()}|{HUB_DIR.parent}", encoding="utf-8")
    except Exception:
        pass
    return True


def release_single_instance() -> None:
    try:
        mine = str(os.getpid())
        if LOCK.exists() and LOCK.read_text(encoding="utf-8").strip().split("|")[0] == mine:
            LOCK.unlink()
    except Exception:
        pass


def main() -> None:
    global AGENT_NAME
    if not claim_single_instance():
        return
    cfg = load_config()
    name = cfg["agent"]
    AGENT_NAME = name

    ok, detail = Q.available()
    print("=" * 66)
    print(" TOURSTORY OP - Agent (이 PC)")
    print("=" * 66)
    print(f"  이 PC   : {name}")
    print(f"  중계    : {Q.backend_name()}  {detail if ok else ''}")
    print(f"  설정    : {CONFIG}")
    if not ok:
        print()
        print(f"  [문제] {detail}")
        print("  설정을 채운 뒤 다시 실행하세요.")
        print("=" * 66)
        return
    print()
    print("  이 창을 닫으면 이 PC 로 오는 작업이 실행되지 않습니다.")
    print("  로그인 정보는 이 PC 를 떠나지 않습니다.")
    print("=" * 66)

    warned = False
    last_beat = 0.0
    while True:
        try:
            if time.time() - last_beat > BEAT_SEC:
                Q.heartbeat(name, chrome_info())
                last_beat = time.time()
            spec = Q.claim(name)
            warned = False
        except Exception as e:
            if not warned:
                print(f"[AGENT] 중계 지점에 연결하지 못했습니다: {str(e)[:120]}")
                print("[AGENT] 계속 재시도합니다...")
                warned = True
            time.sleep(POLL_IDLE)
            continue

        if spec:
            print(f"\n[AGENT] 작업 받음: {spec.get('title')} ({spec.get('id')})")
            run_job(name, spec)
            print(f"[AGENT] 완료: {spec.get('id')}\n")
            # 중계 지점은 저장소가 아니다. 끝난 작업은 잠깐 두었다 지운다.
            # (화면이 결과를 한 번 읽을 시간)
            time.sleep(20)
            Q.remove(spec["id"])
        else:
            time.sleep(POLL_IDLE)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(chr(10) + "[AGENT] 종료합니다.")
    finally:
        # 자물쇠를 놔두면 다음에 켤 때 '이미 돌고 있다' 고 막힌다.
        release_single_instance()
