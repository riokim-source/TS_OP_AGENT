# -*- coding: utf-8 -*-
"""
'떠 있는데 못 붙는 Chrome' 을 스스로 다시 켜는지 검사.

09-02 / 09-05 / 09-07 모두 GLOBAL(9530) 에서 같은 일이 났다.

    /json/version   200 OK
    connect_over_cdp <ws connected> 뒤 멈춤
    -> MRT 오픈이 통째로 실패. 매번 사람이 창을 닫고 다시 켰다.

봇이 알아채고 스스로 하면 될 일이다. 프로필은 디스크에 있으니 로그인은
유지되고, 열려 있던 탭만 없어진다 (봇은 제 주소로 다시 들어간다).

⚠️ 포트가 아예 안 열려 있으면(=Chrome 이 없음) 죽일 것이 없다. 그때는
   평소대로 ensure() 가 띄운다. 여기서 다루는 건 '떠 있는데 못 쓰는' 경우뿐이다.

브라우저를 띄우지 않고 판정만 확인한다.

    python hub/tests/test_chrome_autorestart.py
"""
import sys
from pathlib import Path

# 명령창이 cp949 면 '—' 같은 글자에서 죽는다. 실패를 알리다가 죽으면 안 된다.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import routing  # noqa: E402


class FakeRouting:
    def __init__(self, ensure_ok=True):
        self.ensured = []
        self._ensure_ok = ensure_ok

    def profile_port(self, key):
        return 9530

    def ensure(self, key, wait_seconds=25):
        self.ensured.append(key)
        return {"ok": self._ensure_ok, "ready": self._ensure_ok,
                "message": "" if self._ensure_ok else "부팅 실패"}


def run_case(label, attach_results, port_is_open, ensure_ok=True):
    """attach_results: cdp_attach_ok 가 순서대로 돌려줄 값들"""
    calls = {"attach": 0, "killed": []}

    def fake_attach(port, timeout_ms=15000):
        i = min(calls["attach"], len(attach_results) - 1)
        calls["attach"] += 1
        return attach_results[i]

    def fake_kill(port):
        calls["killed"].append(port)
        return True, f"PID 1234 를 닫았습니다"

    orig = (routing.cdp_attach_ok, routing.kill_stuck_chrome, routing.port_open)
    routing.cdp_attach_ok = fake_attach
    routing.kill_stuck_chrome = fake_kill
    routing.port_open = lambda port, timeout=0.5: port_is_open
    try:
        r = FakeRouting(ensure_ok=ensure_ok)
        logs = []
        ok, why = routing.cdp_ready_or_restart(
            r, "GLOBAL", log=lambda src, m: logs.append(m))
        return ok, why, calls, r.ensured, logs
    finally:
        (routing.cdp_attach_ok, routing.kill_stuck_chrome,
         routing.port_open) = orig


OKV = (True, "탭 2개")
BAD = (False, "BrowserType.connect_over_cdp: Timeout 15000ms exceeded.")

CASES = [
    # 이름,                        attach 결과,      포트열림, ensure성공, 기대(ok, 죽였나, 다시켰나)
    ("처음부터 정상",               [OKV],            True,  True,  (True,  False, False)),
    ("멈췄다가 다시 켜서 됨",        [BAD, OKV],       True,  True,  (True,  True,  True)),
    ("다시 켜도 안 됨",             [BAD, BAD],       True,  True,  (False, True,  True)),
    ("Chrome 이 아예 없음",         [BAD],            False, True,  (False, False, False)),
    ("다시 켜기 자체가 실패",        [BAD, BAD],       True,  False, (False, True,  True)),
]

bad = []
for label, attach, is_open, ens_ok, want in CASES:
    ok, why, calls, ensured, logs = run_case(label, attach, is_open, ens_ok)
    got = (ok, bool(calls["killed"]), bool(ensured))
    print(f"  [{label}]")
    print(f"     결과={'쓸 수 있음' if ok else '못 씀'}  "
          f"닫음={'예' if got[1] else '아니오'}  다시켬={'예' if got[2] else '아니오'}")
    if logs:
        print(f"     안내: {logs[0][:70]}")
    if got != want:
        bad.append(f"{label}: {got} (기대 {want})")
        print(f"     !! 어긋남 — 기대 {want}")

print()
# 네 곳이 모두 이 함수를 쓰는가
ROOT = Path(__file__).resolve().parents[2]
for p, name in ((ROOT / "hub/core/close/runner.py", "마감"),
                (ROOT / "hub/core/opens/mrt_open.py", "MRT 오픈"),
                (ROOT / "hub/core/opens/gg_open.py", "GG 오픈"),
                (ROOT / "hub/core/opens/klook_open.py", "Klook 오픈")):
    has = "cdp_ready_or_restart(" in p.read_text(encoding="utf-8")
    print(f"  {name:10} {'자동 복구 씀' if has else '!! 안 씀'}")
    if not has:
        bad.append(f"{name} 이 자동 복구를 안 쓴다")

print()
if bad:
    for b in bad:
        print("  !!", b)
    raise SystemExit(f"!! {len(bad)}건 어긋남")
print("전부 통과 — 멈춘 Chrome 은 스스로 다시 켜고, 없는 Chrome 은 건드리지 않는다")
