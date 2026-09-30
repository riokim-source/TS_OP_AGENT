# -*- coding: utf-8 -*-
"""
Chrome 에 못 붙어서 한 지역이 통째로 날아가는 것.

2026-09-30 오픈 기록:
    10:30:15  [로그인] KOREA (KR) Klook 로그인됨      <- 붙는 데 문제 없었다
    10:30:43  [실행] Korea 15건 / CDP localhost:9522
    10:31:17  connect_over_cdp: Timeout 30000ms exceeded  (<ws connected> 까지 감)
    10:31:17  [오류] 결과 누락 15건 → 실패 처리
    10:36:19  [Chrome] GLOBAL 다시 켜서 붙었습니다     <- 다시 켜니 멀쩡
    10:37:22  [Chrome] KR 다시 켜서 붙었습니다

같은 날 마감에서도 09:25 에 '연결됨' 이던 KR 이 GG KOREA·KKday 차례에 안 붙어
그 채널이 전멸했다. 이번 주 실패 71건 중 15건이 이 한 가지였다.

원인: 로그인 확인이 탭을 열고 /json/close 로 닫는데, 그건 '닫으라고 시키는'
것일 뿐이다. 무거운 SPA 는 늦게 사라지고 드물게 남는다. 반쯤 닫힌 탭 하나가
있으면 Playwright 의 connect_over_cdp 가 거기서 멈춘다.

고친 것 세 가지:
  1) 열었던 탭은 '사라진 것까지' 확인한다 (routing.close_tab)
  2) 로그인 확인 **뒤에** CDP 를 한 번 더 본다 (막혀 있으면 그 자리에서 다시 켠다)
  3) 그래도 지역이 통째로 비면, Chrome 을 다시 켜고 **한 번 더** 돌린다.
     결과 줄은 재시도 뒤에 한 번만 적는다 (한 상품에 두 줄이 남으면 안 된다)

    python hub/tests/test_chrome_lost_region.py
"""
import json
import sys
import types
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "hub"))

from core import routing                                  # noqa: E402
from core.opens import klook_open                         # noqa: E402

bad = []


# ── 1) 탭은 '사라진 것까지' 확인한다 ───────────────────────────────────────
print("  [1] /json/close 를 부르고 끝내지 않는가")


class FakeResp:
    def __init__(self, body=b"{}"):
        self.body = body

    def read(self):
        return self.body

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def fake_close_world(gone_after: int | None):
    """
    gone_after: 탭 목록을 몇 번 본 뒤에 탭이 사라지는가 (None = 안 사라짐)
    반환: (urlopen 대역, cdp_tabs 대역, 호출 기록)
    """
    calls = {"close": 0, "list": 0}

    def urlopen(url, *a, **k):
        calls["close"] += 1
        return FakeResp()

    def cdp_tabs(port, timeout=2.0):
        calls["list"] += 1
        if gone_after is not None and calls["list"] > gone_after:
            return []
        return [{"id": "T1", "url": "https://merchant.klook.com/"}]

    return urlopen, cdp_tabs, calls


CASES = [
    ("바로 사라짐",      0,    True),
    ("몇 초 뒤 사라짐",   3,    True),
    ("끝까지 안 사라짐",  None, False),
]
for label, gone_after, want in CASES:
    uo, tabs, calls = fake_close_world(gone_after)
    old_u, old_t = routing.urllib.request.urlopen, routing.cdp_tabs
    routing.urllib.request.urlopen, routing.cdp_tabs = uo, tabs
    try:
        got = routing.close_tab(9522, "T1", wait=2.0)
    finally:
        routing.urllib.request.urlopen, routing.cdp_tabs = old_u, old_t
    mark = "" if got == want else f"   !! 기대 {want}"
    print(f"     {label:16} -> 닫힘 {got} / 목록 확인 {calls['list']}회 / "
          f"close {calls['close']}회{mark}")
    if got != want:
        bad.append(f"close_tab({label}) = {got}")
    if calls["list"] == 0:
        bad.append(f"{label}: 닫으라고만 하고 확인을 안 했다")
    if want is False and calls["close"] < 2:
        bad.append(f"{label}: 안 사라지는데 한 번만 닫으라고 했다")


# ── 2) 로그인 확인이 남긴 탭을 알아채는가 ──────────────────────────────────
print()
print("  [2] 로그인 확인 탭이 남으면 tab_left 로 알린다")


def probe_with(tab_stays: bool) -> dict:
    def urlopen(url, *a, **k):
        u = url if isinstance(url, str) else getattr(url, "full_url", "")
        if "/json/new" in str(u):
            return FakeResp(json.dumps({"id": "T9"}).encode())
        return FakeResp()

    def cdp_tabs(port, timeout=2.0):
        if tab_stays:
            return [{"id": "T9", "url": "https://merchant.klook.com/home"}]
        return []

    old_u, old_t = routing.urllib.request.urlopen, routing.cdp_tabs
    routing.urllib.request.urlopen, routing.cdp_tabs = urlopen, cdp_tabs
    try:
        return routing.Routing._probe_login(9522, "KLOOK", 1.0)
    finally:
        routing.urllib.request.urlopen, routing.cdp_tabs = old_u, old_t


for label, stays, want in (("탭이 남음", True, True), ("탭이 닫힘", False, False)):
    res = probe_with(stays)
    got = bool(res.get("tab_left"))
    mark = "" if got == want else f"   !! 기대 {want}"
    print(f"     {label:10} -> tab_left {got} / state {res.get('state')}{mark}")
    if got != want:
        bad.append(f"_probe_login({label}) tab_left={got}")


# ── 3) 붙기 직전에 한 번 더 보는가 (소스 순서) ─────────────────────────────
print()
print("  [3] 로그인 확인 '뒤에' CDP 를 다시 보는가")
for path, label in ((ROOT / "hub" / "core" / "opens" / "klook_open.py", "Klook 오픈"),
                    (ROOT / "hub" / "core" / "close" / "runner.py", "마감")):
    src = path.read_text(encoding="utf-8")
    i_login = src.find("check_login")
    i_after = src.find("cdp_ready_or_restart", i_login)
    ok = i_login > 0 and i_after > i_login
    print(f"     {label:10} {'예' if ok else '!! 아니오'}")
    if not ok:
        bad.append(f"{label}: 로그인 확인 뒤에 CDP 를 다시 보지 않는다")


# ── 4) 지역이 통째로 비면 다시 켜고 한 번 더 ───────────────────────────────
print()
print("  [4] 통째로 빈 지역 — 다시 켜고 한 번 더, 줄은 한 번만")

LOST = klook_open.LOST_MEMO + " (Chrome 미연결/치명적 오류 확인 필요)"
TASKS = [
    {"input_text": "남레 18", "name": "남레", "package_id": "356595", "inventory": 18},
    {"input_text": "남아 4", "name": "남아", "package_id": "486796", "inventory": 4},
]


class FakeJob:
    def __init__(self, stopping=False):
        self.logs = []
        self.results = []
        self.summary = None
        self.error = None
        self.stopping = stopping
        self.total = 0

    def log(self, src, line):
        self.logs.append(str(line))

    def result(self, item):
        self.results.append(item)

    def done(self, summary=None, error=None):
        self.summary, self.error = summary, error

    def set_stopper(self, fn):
        pass


def fake_core(scripts):
    """scripts: 시도마다 '각 task 를 무엇으로 돌려줄지' 정하는 함수 목록."""
    seen = {"n": 0}

    class Runner:
        def __init__(self, region_tasks, unknown, target_date,
                     on_log=None, on_result=None, on_done=None):
            self.region_tasks = region_tasks
            self.on_result = on_result or (lambda *a: None)
            self.on_done = on_done or (lambda s: None)
            self.running = False

        def start(self):
            n = seen["n"]
            seen["n"] = n + 1
            shape = scripts[min(n, len(scripts) - 1)]
            for region, tasks in self.region_tasks.items():
                for t in tasks:
                    self.on_result(region, shape(dict(t)))
            self.on_done({"duration_text": "00:00:01", "failed": 0})

        def stop(self):
            pass

    core = types.SimpleNamespace(
        SUPPORTED_REGIONS=["KOREA", "JAPAN", "AUSTRALIA"],
        REGION_DISPLAY={"KOREA": "Korea", "JAPAN": "Japan", "AUSTRALIA": "Australia"},
        cli=types.SimpleNamespace(item_text_of=lambda r: str(r.get("input_text", ""))),
        parse_input=lambda text: {"region_tasks": {"KOREA": [dict(t) for t in TASKS]},
                                  "unknown": [], "warnings": [], "total": len(TASKS)},
        describe_date=lambda d: str(d),
        Runner=Runner,
        attempts=seen,
    )
    return core


def lost(task):
    task["result"] = "실패"
    task["memo"] = LOST
    return task


def okrow(task):
    task["result"] = "성공"
    task["memo"] = ""
    return task


def run_case(scripts, restart_ok=True, stopping=False):
    core = fake_core(scripts)
    job = FakeJob(stopping=stopping)
    r = types.SimpleNamespace(route=lambda region, ch: "KR",
                              profile_port=lambda k: 9522)
    calls = {"restart": 0}

    def restart(rr, key, log=None, timeout_ms=15000):
        calls["restart"] += 1
        return (True, "탭 2개") if restart_ok else (False, "다시 켰는데도 붙지 못합니다")

    old = (klook_open._core, klook_open.get_routing, klook_open.cdp_ready_or_restart,
           klook_open.apply_routing_to_cdp, klook_open.dropped_for_ambiguity,
           klook_open.preflight)
    klook_open._core = lambda: core
    klook_open.get_routing = lambda: r
    klook_open.cdp_ready_or_restart = restart
    klook_open.apply_routing_to_cdp = lambda log=None: None
    klook_open.dropped_for_ambiguity = lambda plan: []
    klook_open.preflight = lambda job, regions: []
    try:
        klook_open.run(job, [{"channel": "KLOOK", "mode": "qty",
                              "product": "남레", "qty": 18}], "2026-10-01")
    finally:
        (klook_open._core, klook_open.get_routing, klook_open.cdp_ready_or_restart,
         klook_open.apply_routing_to_cdp, klook_open.dropped_for_ambiguity,
         klook_open.preflight) = old
    return job, calls, core.attempts["n"]


SCENARIOS = [
    # 이름,                   시도별 결과,        다시켜기, 중단,  기대 (줄수, 성공수, 시도수, 다시켜기)
    ("재시도해서 살아남",        [lost, okrow],     True,  False, (2, 2, 2, 1)),
    ("재시도에서도 빈 결과",      [lost, lost],      True,  False, (2, 0, 2, 1)),
    ("Chrome 을 다시 못 켬",    [lost],            False, False, (2, 0, 1, 1)),
    ("처음부터 잘 됨",          [okrow],           True,  False, (2, 2, 1, 0)),
    ("사용자 중단",            [lost],            True,  True,  (2, 0, 1, 0)),
]
for label, scripts, restart_ok, stopping, want in SCENARIOS:
    job, calls, attempts = run_case(scripts, restart_ok, stopping)
    rows = [x for x in job.results if x.get("channel") == "KLOOK"]
    okn = sum(1 for x in rows if "성공" in str(x.get("result")))
    got = (len(rows), okn, attempts, calls["restart"])
    mark = "" if got == want else f"   !! 기대 {want}"
    print(f"     {label:20} 줄 {got[0]} · 성공 {got[1]} · 실행 {got[2]}회 · "
          f"다시켜기 {got[3]}회{mark}")
    if got != want:
        bad.append(f"{label}: {got} (기대 {want})")
    items = [x.get("item") for x in rows]
    if len(items) != len(set(items)):
        bad.append(f"{label}: 같은 상품이 두 줄로 남았다 — {items}")
    if job.summary is None:
        bad.append(f"{label}: job.done 이 안 불렸다")
    elif job.summary.get("failed") != len(rows) - okn:
        bad.append(f"{label}: 요약의 실패 수가 {job.summary.get('failed')} "
                   f"(줄에는 {len(rows) - okn}건)")

# 재시도로 살아난 줄은 메모로 알 수 있어야 한다
job, _c, _n = run_case([lost, okrow])
memos = [str(x.get("memo", "")) for x in job.results]
print(f"     재시도 메모: {memos}")
if not all("재시도" in m for m in memos):
    bad.append(f"재시도로 살아난 줄에 표시가 없다: {memos}")

print()
if bad:
    for b in bad:
        print("  !!", b)
    raise SystemExit(f"!! {len(bad)}건 어긋남")
print("전부 통과 — 탭을 확인하고, 붙기 직전에 다시 보고, 빈 지역은 한 번 더 돌린다")
