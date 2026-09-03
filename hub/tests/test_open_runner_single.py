# -*- coding: utf-8 -*-
"""
오픈 실행 코드가 한 벌인지, GG 일본 이름이 맞는지 검사.

2026-09-03 오픈에서 둘 다 터졌다.

  1) 오픈 실행 코드가 두 벌이었다
        agent._run_open           Agent 로 실행 (클라우드 화면)
        dispatch._run_open_local  이 PC 에서 바로 실행 (로컬 콘솔)
     08-31 에 '실패한 OTA 를 결과에 남긴다' 를 고쳤는데 Agent 쪽만 고쳤다.
     정작 매일 쓰는 로컬 콘솔은 옛 코드 그대로였고, 그래서 MRT 가 22초 동안
     아무것도 못 하고 끝났는데 화면에 실패가 한 줄도 안 남았다.

  2) GG 일본 상품이 한글로 올라가 있다
        Mt. Fuji Highlight 18명  -> 못 찾음 (화면에는 '후지하이라이트')
        Mt. Fuji Signature 16명  -> 못 찾음 (화면에는 '후지시그니처')

    python hub/tests/test_open_runner_single.py
"""
import sys
from pathlib import Path
ROOT = str(Path(__file__).resolve().parents[2])
sys.path.insert(0, ROOT + "/OTA Close")
sys.path.insert(0, ROOT + "/hub")

print("=== 1) GG 일본: 화면에 한글로 올라간 상품 ===")
import gg_open
SCREEN = ["후지하이라이트 - Meet at Shinjuku", "후지시그니처 - Meet at Tokyo Station",
          "Kamakura Highlights - Meet at Tokyo Station", "교토 & 나라 - Meet at Osaka",
          "아마노하시다테 - Meet at Osaka", "Yufuin Brewery - Meet at Hakata"]
cards = [{"title": t, "testid": f"t{i}", "count": "0/20", "blocked": True}
         for i, t in enumerate(SCREEN)]
for tour, qty in [("Mt. Fuji Highlight", 18), ("Mt. Fuji Signature", 16),
                  ("Kyoto & Nara", 5), ("Amanohashidate", 4)]:
    pairs, missed = gg_open.match_targets(cards, [{"tour": tour, "qty": qty, "pickups": []}])
    got = [gg_open.parse_title(c["title"])[0] for c, _q, _i in pairs]
    print(f"  {tour:22} → {got or '못 찾음 !!'}")

print()
print("=== 2) 오픈 실행이 한 곳에서만 도는가 ===")
import inspect
from core.opens import run_all
import importlib
ag = open(ROOT + "/hub/agent.py", encoding="utf-8").read()
dp = open(ROOT + "/hub/op_ui/dispatch.py", encoding="utf-8").read()
print(f"  agent.py    run_all 위임: {'run_all.run_open(job, p)' in ag}")
print(f"  dispatch.py run_all 위임: {'run_all.run_open(job, p)' in dp}")
print(f"  복사본 남아있나 (runners.append 개수): "
      f"agent {ag.count('runners.append')} / dispatch {dp.count('runners.append')} "
      f"/ run_all {inspect.getsource(run_all).count('runners.append')}")

print()
print("=== 3) MRT 가 결과 0건일 때 실패로 잡히는가 ===")
class FakeJob:
    stopping=False
    def __init__(s): s.logs=[]; s.results=[]; s.summary=None; s.error=None; s.finished=False
    def log(s,k,m): s.logs.append(str(m))
    def result(s,r): s.results.append(r)
    def done(s,summary=None,error=None): s.summary=summary; s.error=error; s.finished=True
job = FakeJob()
run_all.klook_open.run = lambda j,m,t: j.result({"channel":"KLOOK","result":"성공"}) or j.done(summary={})
run_all.mrt_open.run   = lambda j,m,t,dry_run=False: j.done(summary={"channel":"MRT","total":0})
run_all.gg_open.run    = lambda j,m,t,dry_run=False: j.result({"channel":"GG","result":"성공"}) or j.done(summary={})
plan=[{"channel":c,"mode":"qty","product":"X","qty":5} for c in ("KLOOK","MRT","GG")]
run_all.run_open(job, {"plan":plan,"date":"2026-09-04","channels":["KLOOK","MRT","GG"]})
print(f"  화면 맨 위 : {job.error or '완료'}")
for r in job.results:
    print(f"     {r.get('channel'):6} {r.get('item',''):12} {r.get('result')}  {str(r.get('memo',''))[:40]}")
print(f"  요약: {job.summary}")

print()
bad = []
for tour, want in (("Mt. Fuji Highlight", "후지하이라이트"),
                   ("Mt. Fuji Signature", "후지시그니처"),
                   ("Kyoto & Nara", "교토 & 나라"),
                   ("Amanohashidate", "아마노하시다테")):
    pairs, _m = gg_open.match_targets(cards, [{"tour": tour, "qty": 1, "pickups": []}])
    got = [gg_open.parse_title(c["title"])[0] for c, _q, _i in pairs]
    if want not in got:
        bad.append(f"GG: {tour} → {want} 를 못 찾는다")

if "run_all.run_open(job, p)" not in ag:
    bad.append("agent.py 가 run_all 을 안 쓴다")
if "run_all.run_open(job, p)" not in dp:
    bad.append("dispatch.py 가 run_all 을 안 쓴다")
if ag.count("runners.append") or dp.count("runners.append"):
    bad.append("오픈 실행 코드가 아직 두 벌이다")

fail_rows = [r for r in job.results if r.get("result") == "실패"]
if not any(r.get("channel") == "MRT" for r in fail_rows):
    bad.append("결과 0건인 MRT 를 실패로 안 잡는다")
if not job.error:
    bad.append("실패가 있는데 job.error 가 비었다")

if bad:
    for b in bad:
        print("  !!", b)
    raise SystemExit(f"!! {len(bad)}건 어긋남")
print("전부 통과 — 오픈 실행은 한 곳, GG 일본 이름도 맞는다")
