# -*- coding: utf-8 -*-
"""
실패한 OTA 가 화면에 보이는지 검사.

한 번 통째로 사라진 적이 있다. Klook 이 안 열렸는데 화면은 '실패 0 · 완료'
였고, 직원은 열렸다고 믿고 넘어갔다. 그날 재고는 0인 채로 남는다.

사라진 이유가 두 가지였다.
  - 채널별 예외를 잡아 로그 한 줄만 남기고 넘어갔다
  - MRT/GG 는 스스로 job.done(error=...) 로 실패를 보고하는데,
    맨 끝의 job.done(summary=...) 이 그 error 를 None 으로 덮었다

    python hub/tests/test_open_failure_visible.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import agent  # noqa: E402


class FakeJob:
    stopping = False

    def __init__(self):
        self.logs = []
        self.results = []
        self.summary = None
        self.error = None
        self.finished = False

    def log(self, src, line):
        self.logs.append(str(line))

    def result(self, item):
        self.results.append(item)

    def done(self, summary=None, error=None):
        self.summary = summary
        self.error = error
        self.finished = True


def run_case(klook, mrt, gg):
    job = FakeJob()
    agent.klook_open.run = lambda j, m, t: klook(j)
    agent.mrt_open.run = lambda j, m, t, dry_run=False: mrt(j)
    agent.gg_open.run = lambda j, m, t, dry_run=False: gg(j)
    plan = [{"channel": c, "mode": "qty", "product": "X", "qty": 5}
            for c in ("KLOOK", "MRT", "GG")]
    agent._run_open(job, {"plan": plan, "date": "2026-08-31",
                          "channels": ["KLOOK", "MRT", "GG"]})
    return job


def failed_rows(job):
    """실행 기록 화면이 '실패/스킵' 으로 세는 것과 같은 기준."""
    return [r for r in job.results
            if "성공" not in str(r.get("result", ""))
            and "집계" not in str(r.get("result", ""))]


OK = lambda j: (j.result({"channel": "?", "item": "상품", "result": "성공"}),
                j.done(summary={}))
BOOM = lambda j: (_ for _ in ()).throw(RuntimeError("Chrome 연결 끊김"))
SELF_REPORT = lambda j: j.done(error="MRT 의 Chrome 연결이 미설정입니다.")
SILENT = lambda j: j.done(summary={"channel": "GG", "total": 0})

CASES = [
    # 이름,                 KLOOK, MRT,        GG,      실패로 잡혀야 하는 채널
    ("전부 성공",            OK,    OK,          OK,     []),
    ("KLOOK 이 예외로 죽음",  BOOM,  OK,          OK,     ["KLOOK"]),
    ("MRT 가 스스로 실패 보고", OK,   SELF_REPORT, OK,     ["MRT"]),
    ("GG 가 조용히 무동작",    OK,    OK,          SILENT, ["GG"]),
    ("셋 다 실패",           BOOM,  SELF_REPORT, SILENT, ["KLOOK", "MRT", "GG"]),
]

bad = 0
for label, k, m, g, want in CASES:
    job = run_case(k, m, g)
    got = sorted(r["channel"] for r in failed_rows(job))
    top = "완료" if not job.error else job.error[:70]
    ok = got == sorted(want) and bool(job.error) == bool(want)
    print(f"  [{label}]")
    print(f"     화면 맨 위 : {top}")
    print(f"     실패 채널  : {got or '없음'}   (기대 {want or '없음'})")
    if not ok:
        bad += 1
        print("     !! 어긋남")

print()
if bad:
    raise SystemExit(f"!! {bad}건 어긋남 — 실패가 화면에 안 보인다")
print("전부 통과 — 실패한 OTA 는 화면과 실행 기록에 남는다")
