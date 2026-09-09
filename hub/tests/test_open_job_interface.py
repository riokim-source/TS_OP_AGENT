# -*- coding: utf-8 -*-
"""
오픈 실행기가 Job 의 '있다고 약속한 것' 만 쓰는지 검사.

2026-09-09 10:54, 휴대폰에서 시킨 MRT 오픈이 시작하자마자 죽었다.

    AttributeError: 'RemoteJob' object has no attribute 'results'

run_all 은 'job 은 log/result/done/stopping 만 있으면 된다' 고 적어 놓고
실제로는 len(job.results) 를 썼다. 이 PC 에서 바로 돌릴 때 쓰는 Job 에는
그 목록이 있고 Agent 로 돌릴 때 쓰는 RemoteJob 에는 없다.
그래서 콘솔에서는 멀쩡했고 휴대폰에서만 죽었다.

⚠️ 기존 테스트 둘(test_open_runner_single, test_open_failure_visible)은
   가짜 Job 에 results 를 갖고 있었다. 그래서 이 버그를 못 잡았다.
   여기서는 **진짜 RemoteJob 이 가진 것만** 허용하고, 그 밖을 건드리면 터뜨린다.

    python hub/tests/test_open_job_interface.py
"""
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "hub"))

from core.opens import run_all  # noqa: E402

bad = []

# ── RemoteJob 이 실제로 가진 이름들 ──────────────────────────────────────
# agent.py 를 통째로 import 하면 Firebase 를 건드리므로 소스에서 읽어낸다.
import ast  # noqa: E402

tree = ast.parse((ROOT / "hub" / "agent.py").read_text(encoding="utf-8"))
remote = next(n for n in ast.walk(tree)
              if isinstance(n, ast.ClassDef) and n.name == "RemoteJob")
HAS = set()
for n in ast.walk(remote):
    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
        HAS.add(n.name)
    elif isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) \
            and n.value.id == "self":
        HAS.add(n.attr)
HAS.discard("__init__")
print(f"  RemoteJob 이 가진 것 {len(HAS)}개: {', '.join(sorted(HAS))[:120]}...")
if "results" in HAS:
    bad.append("RemoteJob 에 results 가 생겼다 — 이 테스트를 다시 보라")


class StrictJob:
    """RemoteJob 이 가진 것만 허용한다. 그 밖을 건드리면 그 자리에서 터진다."""

    def __init__(self):
        object.__setattr__(self, "_seen", [])
        object.__setattr__(self, "logs", [])
        object.__setattr__(self, "rows", [])
        object.__setattr__(self, "summary", None)
        object.__setattr__(self, "error", None)
        object.__setattr__(self, "finished", False)
        object.__setattr__(self, "total", 0)

    # RemoteJob 이 주는 것들
    def log(self, src, line):
        self.logs.append(f"{src}|{line}")

    def result(self, item):
        self.rows.append(item)

    def done(self, summary=None, error=None):
        object.__setattr__(self, "summary", summary)
        object.__setattr__(self, "error", error)
        object.__setattr__(self, "finished", True)

    def set_stopper(self, fn):
        pass

    @property
    def stopping(self):
        return False

    # 그 밖의 것을 읽으면 실패
    def __getattr__(self, name):
        if name.startswith("_") or name in HAS:
            raise AttributeError(name)
        raise AssertionError(
            f"run_all 이 RemoteJob 에 없는 job.{name} 을 읽었다 "
            f"— Agent 로 돌릴 때 여기서 죽는다")


# ── 러너를 가짜로 바꿔치기 ──────────────────────────────────────────────
class FakeRunner:
    def __init__(self, rows=0, raise_=None, report=None):
        self.rows, self.raise_, self.report = rows, raise_, report

    def run(self, job, *a, **kw):
        for i in range(self.rows):
            job.result({"channel": "X", "item": f"항목{i}", "result": "성공"})
        if self.report:
            job.done(error=self.report)
        if self.raise_:
            raise RuntimeError(self.raise_)
        job.finished = True


PLAN = [{"channel": "KLOOK", "product": "가", "qty": 1},
        {"channel": "MRT", "product": "나", "qty": 2},
        {"channel": "GG", "product": "다", "qty": 3}]

CASES = [
    ("셋 다 잘 됨", FakeRunner(3), FakeRunner(2), FakeRunner(1), [], ["KLOOK", "MRT", "GG"]),
    ("MRT 가 터짐", FakeRunner(3), FakeRunner(0, raise_="화면이 안 뜸"), FakeRunner(1),
     ["MRT"], ["KLOOK", "GG"]),
    ("GG 가 스스로 실패 보고", FakeRunner(3), FakeRunner(2),
     FakeRunner(0, report="Chrome 미설정"), ["GG"], ["KLOOK", "MRT"]),
    ("MRT 가 조용히 0건", FakeRunner(3), FakeRunner(0), FakeRunner(1),
     ["MRT"], ["KLOOK", "GG"]),
]

print()
print("  [1] RemoteJob 처럼 생긴 job 으로 오픈 실행")
orig = (run_all.klook_open, run_all.mrt_open, run_all.gg_open)
for label, k, m, g, want_fail, want_ok in CASES:
    run_all.klook_open, run_all.mrt_open, run_all.gg_open = k, m, g
    job = StrictJob()
    try:
        run_all.run_open(job, {"plan": PLAN, "date": "2026-09-10",
                               "channels": ["KLOOK", "MRT", "GG"]})
    except AssertionError as e:
        print(f"     [{label}] !! {e}")
        bad.append(f"{label}: {e}")
        continue
    except Exception as e:
        print(f"     [{label}] !! {type(e).__name__}: {e}")
        bad.append(f"{label}: {type(e).__name__}: {e}")
        continue
    s = job.summary or {}
    ok = s.get("성공") == want_ok and s.get("실패") == want_fail
    print(f"     [{label}] 성공={s.get('성공')} 실패={s.get('실패')} "
          f"{'' if ok else '!! 기대 성공=' + str(want_ok) + ' 실패=' + str(want_fail)}")
    if not ok:
        bad.append(f"{label}: 성공/실패 집계가 어긋남")
    # 실패한 채널은 결과표에도 줄이 남아야 한다
    rows = [r for r in job.rows if r.get("result") == "실패"]
    if len(rows) != len(want_fail):
        bad.append(f"{label}: 결과표의 실패 줄이 {len(rows)}개 (기대 {len(want_fail)})")
run_all.klook_open, run_all.mrt_open, run_all.gg_open = orig

# ── 2) 센 뒤에 job.result 를 원래대로 돌려놨는가 ─────────────────────────
print()
print("  [2] 결과 세는 동안 job 을 더럽히지 않는가")
job = StrictJob()
before = "result" in vars(job)
run_all.klook_open, run_all.mrt_open, run_all.gg_open = (
    FakeRunner(1), FakeRunner(1), FakeRunner(1))
run_all.run_open(job, {"plan": PLAN, "date": "2026-09-10",
                       "channels": ["KLOOK", "MRT", "GG"]})
run_all.klook_open, run_all.mrt_open, run_all.gg_open = orig
after = "result" in vars(job)
print(f"     실행 전 인스턴스에 result 있음={before} / 실행 후={after}")
if after != before:
    bad.append("결과를 센 뒤 job.result 를 원래대로 안 돌려놨다")

print()
if bad:
    for b in bad:
        print("  !!", b)
    raise SystemExit(f"!! {len(bad)}건 어긋남")
print("전부 통과 — 오픈 실행기는 약속한 것만 쓴다 (Agent 로도 돈다)")
