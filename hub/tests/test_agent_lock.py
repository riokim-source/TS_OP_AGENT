# -*- coding: utf-8 -*-
"""
Agent 자물쇠가 'PID 재사용' 에 속지 않는지 검사.

2026-09-03: Agent 를 켜려는데 계속 '이미 이 PC 에서 Agent 가 돌고 있습니다'.
실제로는 하나도 안 돌고 있었다.

    자물쇠 : 28632|C:\\Users\\ktour\\Desktop\\TS_OP_AGENT-main
    PID 28632 = msedge.exe        <- Windows 가 번호를 재사용했다

_pid_alive() 가 'PID 가 존재하는가' 만 봤기 때문이다. Agent 가 죽은 뒤
(재부팅 등) 그 번호를 다른 프로그램이 물려받으면 영영 못 켠다.

    python hub/tests/test_agent_lock.py
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import agent  # noqa: E402

bad = []


def fake_tasklist(name: str, pid: int):
    """tasklist 출력을 흉내낸다."""
    def run(cmd, **kw):
        class R:
            stdout = (f'"{name}","{pid}","Console","1","12,345 K"\n'
                      if name else "정보: 지정된 조건에 맞는 작업이 실행되고 있지 않습니다.\n")
        return R()
    return run


CASES = [
    # 이름,                    tasklist 가 돌려주는 것,   막아야 하나
    ("진짜 Agent (python)",     "python3.13.exe",          True),
    ("PID 재사용 (Edge)",       "msedge.exe",              False),
    ("PID 재사용 (chrome)",     "chrome.exe",              False),
    ("죽어서 없음",             "",                        False),
]

orig = subprocess.run
try:
    for label, name, want_block in CASES:
        subprocess.run = fake_tasklist(name, 28632)
        got = agent._pid_alive(28632)
        print(f"  [{label}]")
        print(f"     tasklist: {name or '(없음)':16} → "
              f"{'막음' if got else '통과'}   (기대: {'막음' if want_block else '통과'})")
        if got != want_block:
            bad.append(label)
            print("     !! 어긋남")
finally:
    subprocess.run = orig

# 확인 자체가 안 되면 '살아 있다' 로 봐야 한다 (둘이 도는 것이 더 위험)
def boom(cmd, **kw):
    raise OSError("tasklist 없음")


orig = subprocess.run
try:
    subprocess.run = boom
    got = agent._pid_alive(28632)
    print()
    print(f"  [확인 실패] → {'막음' if got else '통과'}   (기대: 막음)")
    if not got:
        bad.append("확인 실패 시 통과시킨다 — 둘이 돌 수 있다")
finally:
    subprocess.run = orig

print()
if bad:
    for b in bad:
        print("  !!", b)
    raise SystemExit(f"!! {len(bad)}건 어긋남 — 자물쇠가 잘못 판단한다")
print("전부 통과 — 남의 PID 를 우리 Agent 로 착각하지 않는다")
