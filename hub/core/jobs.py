# -*- coding: utf-8 -*-
"""
jobs.py
백그라운드 작업(마감 / 오픈) 공통 세션.

웹 UI 는 폴링만 하면 되도록, 로그·결과·진행률을 한 곳에 모아둔다.
동시에 1건만 실행한다 (Chrome 을 여러 작업이 같이 만지면 서로 죽인다).
"""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path

MAX_LOG_LINES = 6000

# 실행 기록을 남길 곳. 매일 아침 10시 전에 도는 작업이라, 끝난 뒤에
# "왜 그 상품이 안 닫혔지" 를 되짚을 수 있어야 한다.
# 2026-08-23 에 GG 한국 마감 실패 원인을 못 찾은 게 로그가 메모리에만 있었기 때문이다.
LOG_DIR = Path(__file__).resolve().parents[1] / "logs" / "runs"
KEEP_DAYS = 30


class Job:
    def __init__(self, kind: str, title: str, total: int = 0):
        self.kind = kind                  # "close" | "open"
        self.title = title
        self.total = total
        self.lock = threading.Lock()
        self.logs: list[dict] = []
        self.results: list[dict] = []
        self.started_at = time.time()
        self.started_text = datetime.now().strftime("%H:%M:%S")
        self.finished = False
        self.summary: dict | None = None
        self.error: str | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._stopper = None              # 실행 측이 등록하는 중단 콜백

    # ── 기록 ──────────────────────────────────────────────────────────────
    def log(self, source: str, line: str) -> None:
        line = str(line).rstrip()
        if not line:
            return
        with self.lock:
            self.logs.append({"t": datetime.now().strftime("%H:%M:%S"),
                              "src": source, "line": line})
            if len(self.logs) > MAX_LOG_LINES:
                del self.logs[: len(self.logs) - MAX_LOG_LINES]

    def result(self, item: dict) -> None:
        with self.lock:
            self.results.append(item)

    def done(self, summary: dict | None = None, error: str | None = None) -> None:
        with self.lock:
            self.summary = summary
            self.error = error
            self.finished = True
        self._save()
        MANAGER.release_lock()

    # ── 파일로 남기기 ─────────────────────────────────────────────────────
    def _save(self) -> None:
        """끝난 실행을 파일로 남긴다. 실패해도 작업 자체를 막지 않는다."""
        try:
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            stamp = datetime.fromtimestamp(self.started_at).strftime("%Y%m%d_%H%M%S")
            base = LOG_DIR / f"{stamp}_{self.kind}"
            with self.lock:
                logs = list(self.logs)
                results = list(self.results)
                summary, error = self.summary, self.error
            lines = [f"# {self.title}",
                     f"# 시작 {self.started_text} / 종료 {datetime.now():%H:%M:%S}"]
            if error:
                lines.append(f"# 오류: {error}")
            if summary:
                lines.append(f"# 요약: {json.dumps(summary, ensure_ascii=False)}")
            lines.append("")
            lines += [f"{l['t']} [{l['src']}] {l['line']}" for l in logs]
            if results:
                lines += ["", "# 결과 ------------------------------------"]
                lines += [f"{r.get('channel','')} | {r.get('region','')} | "
                          f"{r.get('item','')} | {r.get('result','')} | {r.get('memo','')}"
                          for r in results]
            base.with_suffix(".log").write_text(chr(10).join(lines), encoding="utf-8")
            base.with_suffix(".json").write_text(
                json.dumps({"kind": self.kind, "title": self.title,
                            "started": self.started_text, "summary": summary,
                            "error": error, "results": results},
                           ensure_ascii=False, indent=1), encoding="utf-8")
            self._prune()
        except Exception:
            pass

    @staticmethod
    def _prune() -> None:
        try:
            cutoff = time.time() - KEEP_DAYS * 86400
            for f in LOG_DIR.iterdir():
                if f.is_file() and f.stat().st_mtime < cutoff:
                    f.unlink(missing_ok=True)
        except Exception:
            pass

    # ── 제어 ──────────────────────────────────────────────────────────────
    def set_stopper(self, fn) -> None:
        self._stopper = fn

    def stop(self) -> None:
        self._stop.set()
        if self._stopper:
            try:
                self._stopper()
            except Exception as e:
                self.log("SYS", f"[중단] 실패: {e}")

    @property
    def stopping(self) -> bool:
        return self._stop.is_set()

    @property
    def running(self) -> bool:
        return not self.finished

    def start(self, target) -> None:
        self._thread = threading.Thread(target=self._wrap, args=(target,), daemon=True)
        self._thread.start()

    def _wrap(self, target) -> None:
        try:
            target(self)
        except Exception as e:
            import traceback
            self.log("SYS", f"[오류] {e}")
            for ln in traceback.format_exc().splitlines()[-12:]:
                self.log("SYS", ln)
            self.done(error=str(e))
        finally:
            if not self.finished:
                self.done()

    # ── 조회 ──────────────────────────────────────────────────────────────
    def snapshot(self, since: int = 0) -> dict:
        with self.lock:
            elapsed = time.time() - self.started_at
            return {
                "kind": self.kind,
                "title": self.title,
                "running": not self.finished,
                "stopping": self._stop.is_set(),
                "started": self.started_text,
                "elapsed": int(elapsed),
                "total": self.total,
                "since": len(self.logs),
                "logs": self.logs[since:] if since < len(self.logs) else [],
                "results": self.results,
                "summary": self.summary,
                "error": self.error,
            }


# 프로세스를 넘어서는 잠금.
#
# 콘솔(8600)과 Streamlit 은 서로 다른 프로세스라 각자 JobManager 를 갖는다.
# 메모리 안의 잠금만으로는 두 화면에서 동시에 마감을 눌러도 막지 못하고,
# 그러면 워커들이 같은 Chrome 을 같이 만져서 서로 죽는다.
# 파일 하나로 '지금 이 PC 에서 도는 작업' 을 표시한다.
RUN_LOCK = Path(__file__).resolve().parents[1] / "data" / "running.json"
STALE_AFTER = 4 * 3600     # 이 시간이 지난 잠금은 죽은 것으로 본다


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        import ctypes
        h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not h:
            return False
        ctypes.windll.kernel32.CloseHandle(h)
        return True
    except Exception:
        try:
            os.kill(pid, 0)
            return True
        except Exception:
            return False


def read_lock() -> dict | None:
    """살아있는 잠금이면 그 내용을, 아니면 None (죽은 잠금은 지운다)."""
    try:
        if not RUN_LOCK.exists():
            return None
        d = json.loads(RUN_LOCK.read_text(encoding="utf-8"))
    except Exception:
        return None
    stale = (time.time() - float(d.get("at") or 0) > STALE_AFTER
             or not _pid_alive(int(d.get("pid") or 0)))
    if stale:
        try:
            RUN_LOCK.unlink(missing_ok=True)
        except Exception:
            pass
        return None
    return d


class JobManager:
    """동시에 1건만. 이 프로세스 안에서도, 이 PC 안에서도."""

    def __init__(self):
        self.current: Job | None = None
        self._lock = threading.Lock()

    def start(self, job: Job, target) -> tuple[bool, str]:
        with self._lock:
            if self.current is not None and self.current.running:
                return False, f"이미 '{self.current.title}' 실행 중입니다."
            other = read_lock()
            if other:
                return False, (f"다른 화면에서 '{other.get('title')}' 실행 중입니다 "
                               f"({other.get('ui')}, {other.get('started')}). "
                               f"끝난 뒤에 다시 시도하세요.")
            self.current = job
        self._write_lock(job)
        job.start(target)
        return True, ""

    @staticmethod
    def _write_lock(job: Job) -> None:
        try:
            RUN_LOCK.parent.mkdir(parents=True, exist_ok=True)
            RUN_LOCK.write_text(json.dumps({
                "pid": os.getpid(), "title": job.title, "kind": job.kind,
                "ui": os.environ.get("LMHUB_UI", "console"),
                "started": job.started_text, "at": time.time(),
            }, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

    @staticmethod
    def release_lock() -> None:
        try:
            d = json.loads(RUN_LOCK.read_text(encoding="utf-8"))
            if int(d.get("pid") or 0) == os.getpid():
                RUN_LOCK.unlink(missing_ok=True)
        except Exception:
            pass

    def snapshot(self, since: int = 0) -> dict:
        if self.current is None:
            return {"kind": None, "running": False, "logs": [], "results": [],
                    "since": 0, "total": 0, "summary": None, "error": None}
        return self.current.snapshot(since)

    def stop(self) -> tuple[bool, str]:
        if self.current is None or not self.current.running:
            return False, "실행 중이 아닙니다."
        self.current.stop()
        return True, ""


MANAGER = JobManager()
