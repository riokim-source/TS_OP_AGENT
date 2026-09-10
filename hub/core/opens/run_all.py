# -*- coding: utf-8 -*-
"""
run_all.py
오픈 한 번에 여러 OTA 를 순서대로 돌린다. **여기 한 곳만 있다.**

⚠️ 예전에는 같은 코드가 두 벌 있었다.
       agent._run_open           Agent 로 실행할 때 (클라우드 화면)
       dispatch._run_open_local  이 PC 에서 바로 실행할 때 (로컬 콘솔)
   "같은 순서를 유지한다" 는 주석만 달아 두고 실제로는 어긋났다.
   2026-08-31 에 '실패한 OTA 를 결과에 남긴다' 를 고쳤는데 Agent 쪽만
   고쳤고, 정작 매일 쓰는 로컬 콘솔은 옛 코드 그대로였다.
   그래서 09-03 오픈에서 MRT 가 22초 동안 아무것도 못 하고 끝났는데
   화면에는 실패가 한 줄도 안 남았다.

   두 벌이면 반드시 어긋난다. 한 벌만 둔다.
"""
from __future__ import annotations

from . import gg_open, klook_open, mrt_open, tpc_open, vi_open


class _Counter:
    """
    러너가 결과를 몇 건 남겼는지 센다.

    ⚠️ 예전에는 len(job.results) 로 셌다. 이 PC 에서 바로 돌릴 때 쓰는 Job 에는
       그 목록이 있지만, Agent 로 돌릴 때 쓰는 RemoteJob 에는 없다.
       그래서 휴대폰에서 시킨 오픈이 시작하자마자 죽었다.
           AttributeError: 'RemoteJob' object has no attribute 'results'
       (2026-09-09 10:54 · MRT 4건)

       job 이 무엇이든 상관없도록 job.result() 를 몇 번 불렀는지만 센다.
       run_open() 은 log/result/done/stopping 만 쓴다고 적어 놨으면
       실제로도 그것만 써야 한다.
    """

    def __init__(self, job):
        self.job = job
        self.n = 0
        self._orig = job.result
        # 원래 인스턴스에 붙어 있던 것인지, 클래스의 메서드인지 기억해 둔다.
        # 끝나고 되돌릴 때 없던 것을 남겨 두지 않기 위해서다.
        self._was_own = "result" in vars(job)

    def __enter__(self):
        self.job.result = self._count
        return self

    def _count(self, item):
        self.n += 1
        self._orig(item)

    def __exit__(self, *exc):
        if self._was_own:
            self.job.result = self._orig
        else:
            vars(self.job).pop("result", None)
        return False


def run_open(job, p: dict) -> None:
    """
    오픈 실행. job 은 log/result/done/stopping 만 있으면 된다.

    ⚠️ 실패한 OTA 는 반드시 결과에 남겨야 한다.

       예전에는 두 가지가 겹쳐서 실패가 통째로 사라졌다.
         1) 채널별 예외를 잡아 로그 한 줄만 남기고 넘어갔다
         2) MRT/GG 는 스스로 job.done(error=...) 로 실패를 보고하는데
            (Chrome 미설정 / 포트 충돌 / 부팅 실패 / 맵핑 없음),
            맨 끝의 job.done(summary=...) 이 error 를 None 으로 덮었다
       그래서 Klook 이 통째로 안 열렸는데도 화면은 '실패 0 · 완료' 였다.
       직원은 열렸다고 믿고 넘어가고, 그날 재고는 0인 채로 남는다.
    """
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
    if "VI" in channels and any(x["channel"] == "VI" for x in mine):
        runners.append(("VI", lambda: vi_open.run(job, mine, target, dry_run=dry)))
    # ⚠️ [TPC] 와 [CP] 는 다른 채널이다. 이 봇은 [TPC] 줄만 본다.
    #    CP 는 아직 봇이 없고 메모에 수량만 적힌다.
    if "TPC" in channels and any(x["channel"] == "TPC" for x in mine):
        runners.append(("TPC", lambda: tpc_open.run(job, mine, target, dry_run=dry)))

    failed: list[tuple[str, str]] = []
    ran: list[str] = []
    skipped: list[str] = []

    for name, fn in runners:
        if job.stopping:
            job.log("SYS", f"[중단] {name} 실행 안 함")
            skipped.append(name)
            job.result({"channel": name, "region": "", "item": "(채널 전체)",
                        "result": "중단", "memo": "사용자가 중단해서 실행하지 않음"})
            continue
        job.log("SYS", f"===== {name} 오픈 시작 =====")
        job.error = None                  # 앞 채널의 오류를 물려받지 않는다
        why = ""
        with _Counter(job) as cnt:
            try:
                fn()
                why = str(job.error or "")    # 러너가 스스로 보고한 실패
            except Exception as e:
                why = f"{type(e).__name__}: {e}"
                job.log("SYS", f"[오류] {name}: {e}")

        made = cnt.n
        if not why and made == 0:
            # 열 것이 있어서 러너를 돌렸는데 아무것도 안 나왔다.
            # 조용히 넘어가면 '했다' 로 보이므로 눈에 띄게 남긴다.
            why = "결과가 하나도 없습니다 (로그를 확인하세요)"

        if why:
            failed.append((name, why))
            job.log("SYS", f"[실패] {name}: {why}")
            job.result({"channel": name, "region": "", "item": "(채널 전체)",
                        "result": "실패", "memo": why[:300]})
        else:
            ran.append(name)
            job.log("SYS", f"[완료] {name} {made}건")

        # 각 러너가 job.done() 을 부르므로 다음 러너를 위해 되돌린다
        job.finished = False

    summary = {"channels": [n for n, _ in runners], "dry_run": dry,
               "성공": ran, "실패": [n for n, _ in failed], "중단": skipped}
    if failed:
        job.done(summary=summary,
                 error="열지 못한 OTA — " + " / ".join(f"{n}: {w[:120]}"
                                                       for n, w in failed))
    else:
        job.done(summary=summary)
