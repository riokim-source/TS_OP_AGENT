# -*- coding: utf-8 -*-
"""
klook_core.py
GUI(gui.py) / 모바일 웹(mobile_server.py) 프론트엔드가 공유하는 실행 코어.

⚠️ 실행방식은 main.py(CLI) 와 100% 동일하다.
   - 파싱/로그 로직은 main.py 의 함수를 그대로 import 해서 사용 (중복 구현 없음)
   - region 별 klook_worker.py 를 병렬 subprocess 로 실행
   - stdout 의 '##RESULT##' 마커를 수집
   - logs/booking_log_KLOOK_*.txt 동일 포맷 기록
   달라진 것은 "입력을 stdin 대신 GUI 에서 받고, 출력을 콜백으로 흘려보낸다" 뿐이다.

main.py 는 전혀 수정하지 않으므로 기존 `python main.py` 운영 경로는 그대로 살아 있다.
"""
from __future__ import annotations

import io
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from contextlib import redirect_stdout
from datetime import datetime, timedelta
from pathlib import Path

# ── main.py 재사용 (파싱 / 로그 / 상수) ──────────────────────────────────────
import main as cli
from packages import PACKAGES, get_package

BASE_DIR          = cli.BASE_DIR
LOG_DIR           = cli.LOG_DIR
WORKER            = cli.WORKER
SUPPORTED_REGIONS = cli.SUPPORTED_REGIONS
REGION_CDP_URLS   = cli.REGION_CDP_URLS
RESULT_MARKER     = cli.RESULT_MARKER

REGION_DISPLAY = {
    'KOREA':     'Korea',
    'JAPAN':     'Japan',
    'AUSTRALIA': 'Australia',
    'UK':        'UK',
}

CHROME_BAT = {
    'KOREA':     'start_chrome_korea.bat',
    'JAPAN':     'start_chrome_japan.bat',
    'AUSTRALIA': 'start_chrome_australia.bat',
    'UK':        'start_chrome_uk.bat',
}

RESULT_LABELS = ["성공", "실패", "찾을 수 없음", "새버전 실패"]


# ──────────────────────────────────────────────────────────────────────────────
# hub 라우팅 연동 (단일 source of truth)
# ──────────────────────────────────────────────────────────────────────────────
# 여태 Klook Open 은 자기 .bat 과 자기 포트를 들고 있었고, OTA Close 는 따로 들고 있었다.
# 같은 포트에 다른 프로필을 물려서, 먼저 뜬 쪽이 포트를 잡으면 나머지 봇은 로그인 안 된
# Chrome 에 붙어 그 지역 전체가 실패했다.
#
# 이제 hub/data/routing.json 이 (지역 x OTA) -> Chrome 프로필을 결정한다.
# hub 가 있으면 그 값으로 CDP 주소를 덮어쓰고, 없으면 기존 동작 그대로 간다.
# 이 함수 덕분에 gui.py / mobile_server.py / hub 세 프론트엔드가 항상 같은 Chrome 을 본다.
def _hub_routing():
    try:
        import sys as _sys
        for parent in (BASE_DIR.parent, BASE_DIR.parent.parent):
            hub = parent / "hub"
            if (hub / "core" / "routing.py").exists():
                if str(hub) not in _sys.path:
                    _sys.path.insert(0, str(hub))
                from core.routing import get_routing  # type: ignore
                return get_routing()
    except Exception:
        pass
    return None


def apply_hub_routing() -> dict:
    """hub 라우팅의 KLOOK 프로필 포트를 REGION_CDP_URLS 에 반영. 반환: {region: profile}"""
    r = _hub_routing()
    if r is None:
        return {}
    applied = {}
    for region in SUPPORTED_REGIONS:
        try:
            key = r.route(region, "KLOOK")
            if key is None:
                continue
            url = f"http://localhost:{r.profile_port(key)}"
            cli.REGION_CDP_URLS[region] = url
            REGION_CDP_URLS[region] = url
            applied[region] = key
        except Exception:
            continue
    return applied


HUB_PROFILES = apply_hub_routing()


# Windows 에서 GUI 로 띄웠을 때 worker 콘솔창이 튀어나오지 않게
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


# ──────────────────────────────────────────────────────────────────────────────
# 상품 카탈로그 / 매핑 조회
# ──────────────────────────────────────────────────────────────────────────────

def product_catalog() -> list[dict]:
    """packages.py 전체 상품 목록. GUI 검색 패널용."""
    rows = []
    for name, info in PACKAGES.items():
        acc = info.get('accept_until') or {}
        rows.append({
            'name':     name,
            'id':       info['id'],
            'region':   info['region'],
            'workflow': info.get('workflow', 'package'),
            'accept':   f"{'당일' if acc.get('when') == 'same_day' else '전일'} {acc.get('time', '')}".strip(),
        })
    rows.sort(key=lambda r: (r['region'], r['name']))
    return rows


def variant_groups() -> dict[str, list[str]]:
    """(한)/(중)/(일) 다국어 변형 상품 그룹. main.py 의 함수 그대로 사용."""
    try:
        return cli._multi_lang_variant_names()
    except Exception:
        return {}


def catalog_stats() -> dict[str, int]:
    counts = {r: 0 for r in SUPPORTED_REGIONS}
    for info in PACKAGES.values():
        if info['region'] in counts:
            counts[info['region']] += 1
    return counts


# ──────────────────────────────────────────────────────────────────────────────
# 입력 파싱 (main.parse_tasks 재사용 + stdout 캡처)
# ──────────────────────────────────────────────────────────────────────────────

def parse_input(text: str) -> dict:
    """
    GUI 입력 텍스트 → region 별 task / 매핑 실패 / 경고 메시지.
    main.parse_tasks() 를 그대로 호출하고, 그 안의 print 출력만 캡처한다.
    """
    buf = io.StringIO()
    with redirect_stdout(buf):
        region_tasks, unknown = cli.parse_tasks(text)
    warnings = [ln for ln in buf.getvalue().splitlines() if ln.strip()]
    return {
        'region_tasks': region_tasks,
        'unknown': unknown,
        'warnings': warnings,
        'total': sum(len(v) for v in region_tasks.values()),
    }


def normalize_date(raw: str) -> str | None:
    """
    GUI 날짜 입력 → 'YYYY-MM-DD' 또는 None(익일 자동).
    main.py 의 _prompt_target_date 와 동일한 규칙 (MM/DD 는 과거면 내년).
    """
    raw = (raw or '').strip()
    if not raw:
        return None
    import re
    today = datetime.today()
    m = re.match(r'^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})$', raw)
    if m:
        return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))).strftime('%Y-%m-%d')
    m = re.match(r'^(\d{1,2})[-/.](\d{1,2})$', raw)
    if m:
        mm, dd = int(m.group(1)), int(m.group(2))
        dt = datetime(today.year, mm, dd)
        if dt.date() < today.date():
            dt = datetime(today.year + 1, mm, dd)
        return dt.strftime('%Y-%m-%d')
    raise ValueError("날짜 형식 오류. MM/DD (예: 6/15) 또는 YYYY-MM-DD (예: 2026-06-15)")


def describe_date(target_date: str | None) -> str:
    if not target_date:
        d = datetime.today() + timedelta(days=1)
        return f"내일 {d.strftime('%Y-%m-%d')} ({'월화수목금토일'[d.weekday()]})"
    d = datetime.strptime(target_date, '%Y-%m-%d')
    return f"{target_date} ({'월화수목금토일'[d.weekday()]})"


# ──────────────────────────────────────────────────────────────────────────────
# Chrome / CDP 상태
# ──────────────────────────────────────────────────────────────────────────────

def cdp_port(region: str) -> str:
    return REGION_CDP_URLS[region].rsplit(':', 1)[-1].strip('/')


def cdp_status(region: str, timeout: float = 0.8) -> dict:
    """해당 region Chrome 이 remote-debugging 포트로 떠 있는지 확인."""
    url = REGION_CDP_URLS[region].rstrip('/') + '/json/version'
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            data = json.loads(resp.read().decode('utf-8', 'replace'))
        return {'region': region, 'ok': True, 'browser': data.get('Browser', ''), 'port': cdp_port(region)}
    except Exception as e:
        return {'region': region, 'ok': False, 'browser': f'{type(e).__name__}', 'port': cdp_port(region)}


def cdp_status_all(timeout: float = 0.8) -> dict[str, dict]:
    out: dict[str, dict] = {}
    threads = []

    def _probe(r):
        out[r] = cdp_status(r, timeout)

    for r in SUPPORTED_REGIONS:
        th = threading.Thread(target=_probe, args=(r,), daemon=True)
        th.start()
        threads.append(th)
    for th in threads:
        th.join(timeout=timeout + 0.5)
    for r in SUPPORTED_REGIONS:
        out.setdefault(r, {'region': r, 'ok': False, 'browser': 'timeout', 'port': cdp_port(r)})
    return out


def launch_chrome(region: str) -> str:
    """
    region 용 Chrome 실행.

    hub 라우팅이 있으면 hub 가 띄운다 (프로필/포트/열 사이트가 한 곳에서 결정되도록).
    hub 가 없을 때만 예전 .bat 경로로 폴백한다.
    """
    r = _hub_routing()
    if r is not None:
        key = r.route(region, "KLOOK")
        if key is None:
            raise RuntimeError(f"{region}/KLOOK 은 Chrome 연결이 미설정입니다. "
                               f"hub 콘솔의 [Region x OTA 연결] 에서 지정하세요.")
        if r.port_conflict(key):
            raise RuntimeError(f"port {r.profile_port(key)} 를 다른 프로필의 Chrome 이 "
                               f"점유하고 있습니다. 그 창을 닫고 다시 시도하세요.")
        res = r.ensure(key, wait_seconds=20)
        if not res.get("ok"):
            raise RuntimeError(res.get("message", f"{key} Chrome 실행 실패"))
        return (f"{REGION_DISPLAY[region]} Chrome ({key}, port {r.profile_port(key)}) "
                f"준비됨 — Klook 로그인 상태를 확인하세요.")

    bat = BASE_DIR / CHROME_BAT[region]
    if not bat.exists():
        raise FileNotFoundError(f"{bat.name} 을 찾을 수 없습니다.")
    subprocess.Popen(
        ['cmd', '/c', 'start', '', str(bat)],
        cwd=str(BASE_DIR),
        shell=False,
    )
    return f"{REGION_DISPLAY[region]} Chrome 실행 요청 (port {cdp_port(region)}) — 로그인 상태 확인 후 실행하세요."


def local_ips() -> list[str]:
    """모바일 접속 안내용 LAN IP 목록."""
    ips = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except Exception:
        pass
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        ips.add(s.getsockname()[0])
        s.close()
    except Exception:
        pass
    return sorted(ip for ip in ips if not ip.startswith('127.'))


# ──────────────────────────────────────────────────────────────────────────────
# 결과 집계 (main.print_site_summary 와 동일한 분류 규칙)
# ──────────────────────────────────────────────────────────────────────────────

def summarize(rows: list[dict]) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = {label: [] for label in RESULT_LABELS}
    for row in rows:
        result = str(row.get('result', '')).strip()
        workflow = str(row.get('workflow', '')).strip().lower()
        if workflow == 'activity' and result == '실패':
            result = '새버전 실패'
        if result not in groups:
            result = '실패'
        groups[result].append(cli.item_text_of(row))
    return groups


# ──────────────────────────────────────────────────────────────────────────────
# Runner — main.main() 의 실행부를 콜백 기반으로 감싼 것
# ──────────────────────────────────────────────────────────────────────────────

class Runner:
    """
    region 별 klook_worker.py 를 병렬 실행하고 진행 상황을 콜백으로 전달.

    콜백 (모두 worker 스레드에서 호출됨 — Tk 는 after() 로 넘길 것):
        on_log(region, line)      : 콘솔 한 줄  (region='*' 는 시스템 메시지)
        on_result(region, result) : task 1건 완료 (##RESULT## 파싱 결과)
        on_done(summary)          : 전체 종료
    """

    def __init__(self, region_tasks, unknown, target_date=None,
                 on_log=None, on_result=None, on_done=None):
        self.region_tasks = {r: list(region_tasks.get(r, [])) for r in SUPPORTED_REGIONS}
        self.unknown = list(unknown or [])
        self.target_date = target_date
        self.on_log = on_log or (lambda region, line: None)
        self.on_result = on_result or (lambda region, result: None)
        self.on_done = on_done or (lambda summary: None)

        self.running = False
        self.stopped = False
        self._procs: dict[str, subprocess.Popen] = {}
        self._thread: threading.Thread | None = None
        self.started_at: float | None = None
        self.summary: dict | None = None

    # ── public ────────────────────────────────────────────────────────────
    def start(self):
        if self.running:
            raise RuntimeError("이미 실행 중입니다.")
        if not WORKER.exists():
            raise FileNotFoundError(f"worker 파일을 찾을 수 없음: {WORKER}")
        self.running = True
        self.stopped = False
        self.summary = None
        self.started_at = time.perf_counter()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        """실행 중인 worker 프로세스 종료. (Chrome 은 건드리지 않음)"""
        self.stopped = True
        for region, proc in list(self._procs.items()):
            if proc.poll() is None:
                try:
                    proc.terminate()
                    self._log('*', f"[중단] {REGION_DISPLAY.get(region, region)} worker 종료 요청")
                except Exception as e:
                    self._log('*', f"[주의] {region} worker 종료 실패: {e}")

    def active_regions(self) -> list[str]:
        return [r for r in SUPPORTED_REGIONS if self.region_tasks.get(r)]

    # ── internal ──────────────────────────────────────────────────────────
    def _log(self, region, line):
        try:
            self.on_log(region, line)
        except Exception:
            pass

    def _spawn(self, region: str, tasks: list[dict]) -> dict:
        tasks_file = cli.write_tasks_file(region, tasks)
        cmd = [
            sys.executable, str(WORKER),
            "--site", region,
            "--cdp-url", REGION_CDP_URLS[region],
            "--tasks-file", str(tasks_file),
            "--suppress-summary",
        ]
        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONWARNINGS"] = "ignore::SyntaxWarning"

        proc = subprocess.Popen(
            cmd,
            cwd=str(BASE_DIR),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            creationflags=_NO_WINDOW,
        )
        self._procs[region] = proc
        return {'region': region, 'proc': proc, 'tasks_file': tasks_file,
                'tasks': tasks, 'results': []}

    def _stream(self, runner: dict):
        """worker stdout 스트리밍. main.stream_output 과 동일한 마커 규칙."""
        region = runner['region']
        proc = runner['proc']
        assert proc.stdout is not None
        for line in proc.stdout:
            stripped = line.rstrip()
            if stripped.startswith(RESULT_MARKER):
                payload = stripped[len(RESULT_MARKER):].strip()
                try:
                    result = json.loads(payload)
                    runner['results'].append(result)
                    try:
                        self.on_result(region, result)
                    except Exception:
                        pass
                except Exception as e:
                    self._log(region, f"[주의] 결과 마커 파싱 실패: {e}")
                continue
            if stripped:
                self._log(region, stripped)

    def _run(self):
        # 특정 날짜 지정 시 모든 task 에 target_date 주입 (main.main 과 동일)
        if self.target_date:
            for region in SUPPORTED_REGIONS:
                for t in self.region_tasks.get(region, []):
                    t['target_date'] = self.target_date

        self._log('*', f"[실행] 대상 날짜: {describe_date(self.target_date)}")
        for region in SUPPORTED_REGIONS:
            tasks = self.region_tasks.get(region, [])
            if tasks:
                self._log('*', f"[실행] {REGION_DISPLAY[region]} {len(tasks)}건 / CDP {REGION_CDP_URLS[region]}")

        runners = []
        for region in SUPPORTED_REGIONS:
            tasks = self.region_tasks.get(region, [])
            if not tasks:
                continue
            try:
                runners.append(self._spawn(region, tasks))
            except Exception as e:
                self._log('*', f"[오류] {REGION_DISPLAY[region]} worker 실행 실패: {e}")

        threads = []
        for r in runners:
            th = threading.Thread(target=self._stream, args=(r,), daemon=True)
            th.start()
            threads.append((th, r))

        # 결과 수집 (main.main 과 동일: 매핑 실패 항목 먼저 site_rows 에 반영)
        failed = False
        site_rows: dict[str, list[dict]] = {r: [] for r in SUPPORTED_REGIONS}
        for record in self.unknown:
            site = record.get('site', 'KOREA')
            if site not in site_rows:
                site = 'KOREA'
            site_rows[site].append(record)

        for th, r in threads:
            code = r['proc'].wait()
            th.join(timeout=2)
            loaded = list(r['results'])
            loaded_keys = {str(row.get('input_text', '')).strip() for row in loaded}

            # 결과 마커가 없는 task 는 무조건 실패로 채운다.
            #
            # ⚠️ main.py 는 `if code != 0` 일 때만 이 fallback 을 돌리는데,
            #    klook_worker.py 는 [치명적 오류] 를 print 만 하고 exit code 0 으로 끝난다.
            #    (예: Chrome CDP 미연결) 그래서 CLI 에서는 해당 task 가 결과에도
            #    booking_log 에도 안 남고 조용히 사라진다 → 재시도 대상 판별 불가.
            #    GUI 에서는 종료 코드와 무관하게 누락분을 실패로 기록한다.
            missing = [t for t in r.get('tasks', [])
                       if str(t.get('input_text', '')).strip()
                       and str(t.get('input_text', '')).strip() not in loaded_keys]
            for task in missing:
                fb = dict(task)
                fb['result'] = ('새버전 실패'
                                if str(task.get('workflow', '')).lower() == 'activity'
                                else '실패')
                if self.stopped:
                    fb['memo'] = f"사용자 중단 (종료 코드 {code})"
                elif code != 0:
                    fb['memo'] = f"worker 프로세스 종료 코드 {code} / 결과 마커 누락"
                else:
                    fb['memo'] = "worker 가 결과를 남기지 않음 (Chrome 미연결/치명적 오류 확인 필요)"
                loaded.append(fb)
                try:
                    self.on_result(r['region'], fb)
                except Exception:
                    pass

            if code != 0:
                self._log(r['region'], f"[오류] worker 종료 코드: {code}")
            if code != 0 or missing:
                if missing:
                    self._log(r['region'],
                              f"[오류] 결과 누락 {len(missing)}건 → 실패 처리: "
                              + ", ".join(str(t.get('input_text', '')) for t in missing))
                failed = True

            site_rows.setdefault(r['region'], []).extend(loaded)

            try:
                tf = r.get('tasks_file')
                if tf and Path(tf).exists():
                    Path(tf).unlink()
            except Exception as e:
                self._log(r['region'], f"[주의] 임시 task 파일 정리 실패: {e}")

        # 텍스트 로그 기록 (AutoBots 호환) — main.main 과 동일
        for region in SUPPORTED_REGIONS:
            rows = site_rows.get(region, [])
            if rows:
                try:
                    cli.write_text_logs(region.lower(), rows)
                except Exception as e:
                    self._log('*', f"[주의] {region} 로그 기록 실패: {e}")

        duration = time.perf_counter() - (self.started_at or time.perf_counter())
        summary = {
            'regions': {
                region: {
                    'display': REGION_DISPLAY[region],
                    'rows': site_rows.get(region, []),
                    'groups': summarize(site_rows.get(region, [])),
                }
                for region in SUPPORTED_REGIONS
                if site_rows.get(region)
            },
            'duration': duration,
            'duration_text': cli.format_duration(duration),
            'failed': failed,
            'stopped': self.stopped,
            'log_dir': str(LOG_DIR),
        }
        self.summary = summary
        self.running = False
        self._procs.clear()
        self._log('*', f"[완료] 총 런닝타임 {summary['duration_text']}")
        try:
            self.on_done(summary)
        except Exception:
            pass


# ──────────────────────────────────────────────────────────────────────────────
# 최근 로그 조회 (재시도 대상 확인용)
# ──────────────────────────────────────────────────────────────────────────────

def tail_log(region: str, lines: int = 60) -> list[str]:
    path = LOG_DIR / f"booking_log_KLOOK_{region.lower()}.txt"
    if not path.exists():
        return []
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            return [ln.rstrip() for ln in f.readlines()[-lines:]]
    except Exception as e:
        return [f"[로그 읽기 실패] {e}"]
