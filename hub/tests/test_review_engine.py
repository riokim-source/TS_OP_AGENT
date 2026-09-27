# -*- coding: utf-8 -*-
"""
리뷰 분석 엔진이 OP System 안에서 제대로 붙었는지 검사.

원본(TOURSTORY_Review_Analyzer_v1.3)의 엔진을 옮겨 왔다. 원본 폴더는 손대지
않았고, 여기(Review Analyzer/)로 복사한 사본만 고쳤다. 고친 것은 두 가지다.

  1) Chrome 을 라스트미닛 것으로 바꿨다
        원본  KR 9222 / JP 9223 / AU 9224 / UK 9225 (앱 안의 Chrome_debug*)
        여기  KR 9522 / JP 9523 / AU 9524 / UK 9525 / GLOBAL 9530
     ⚠️ 포트를 엔진에 다시 적지 않는다. hub/core/routing.py 하나가 주인이고,
        [Settings] 의 표를 고치면 리뷰 수집도 같이 따라간다. 두 벌로 두면
        한쪽만 바뀌어, 로그인 안 된 Chrome 에서 0건을 긁고도 '리뷰가 없다'
        고 보고하게 된다.

  2) 패키지 이름 core -> rcore
     hub 에도 core 가 있다. 같은 이름이면 한쪽이 다른 쪽을 가린다. 이름을
     바꾸는 대신 sys.modules 를 손대면, 같은 프로세스에서 도는 화면의
     hub.core 까지 망가진다.

    python hub/tests/test_review_engine.py
"""
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "hub"))

import pandas as pd                                   # noqa: E402
from core.review import runner                        # noqa: E402
from core import routing as hub_routing               # noqa: E402

bad = []

print("  [1] 엔진이 있는가")
ok, why = runner.available()
print(f"     {ok} · {why}")
if not ok:
    bad.append(f"엔진을 못 쓴다: {why}")
    for b in bad:
        print("  !!", b)
    raise SystemExit("!! 1건 어긋남")

print(f"     채널 {runner.channels()}")
if runner.channels() != ["L", "KK", "GG", "TPC", "MRT"]:
    bad.append(f"채널이 바뀌었다: {runner.channels()}")

# ── 2) hub 쪽이 안 망가졌는가 (이름 충돌) ───────────────────────────────
print()
print("  [2] 엔진을 불러도 hub 의 core 가 그대로인가")
_p, _c, profiles = runner._engine()
import core.paths as hub_paths                        # noqa: E402
print(f"     hub.core.paths -> {hub_paths.__file__[-40:]}")
print(f"     엔진 rcore     -> {profiles.__file__[-40:]}")
if "hub" not in str(hub_paths.__file__):
    bad.append("hub 의 core 가 엔진 것으로 바뀌었다 — 화면이 망가진다")
if "Review Analyzer" not in str(profiles.__file__):
    bad.append("엔진을 엉뚱한 곳에서 불러왔다")

# ── 3) 포트가 라스트미닛 것인가 ─────────────────────────────────────────
print()
print("  [3] Chrome 포트 — 라스트미닛 것을 쓴다")
m = profiles.BrowserProfileManager()
r = hub_routing.Routing()
CASES = [("Seoul", "L", "KLOOK"), ("Tokyo", "L", "KLOOK"), ("Sydney", "GG", "GG"),
         ("Busan", "MRT", "MRT"), ("Tokyo", "TPC", "CP"), ("Seoul", "KK", "KK")]
for area, ch, op_ch in CASES:
    got = m.route_profile(area, ch)
    want = r.route(area, op_ch)
    port = m.profile_port(got) if got else None
    mark = "" if got == want else f"   !! 라우팅과 다름 ({want})"
    print(f"     {area:7} {ch:4} -> {str(got):7} port {port}{mark}")
    if got != want:
        bad.append(f"{area}/{ch}: 엔진 {got} vs 라우팅 {want}")
    if got and port != r.profile_port(got):
        bad.append(f"{area}/{ch}: 포트가 {port} (라우팅은 {r.profile_port(got)})")

# 원본 포트(9222~9225)가 남아 있으면 안 된다
for key in ("KR", "JP", "AU", "UK"):
    p = m.profile_port(key)
    if 9222 <= p <= 9225:
        bad.append(f"{key} 가 아직 원본 포트 {p} 를 쓴다")
print(f"     KR/JP/AU/UK/GLOBAL = "
      f"{[m.profile_port(k) for k in ('KR', 'JP', 'AU', 'UK', 'GLOBAL')]}")

# ── 4) 수집 계획 ────────────────────────────────────────────────────────
print()
print("  [4] 예약 줄 -> (OTA, Chrome) 묶음")
df = pd.DataFrame([
    {"Agency": "L", "Area": "Seoul", "Date": pd.Timestamp("2026-09-01")},
    {"Agency": "L", "Area": "Tokyo", "Date": pd.Timestamp("2026-09-03")},
    {"Agency": "MRT", "Area": "Busan", "Date": pd.Timestamp("2026-09-02")},
    {"Agency": "KK", "Area": "Tokyo", "Date": pd.Timestamp("2026-09-02")},   # 미설정
    {"Agency": "L", "Area": "London", "Date": pd.NaT},                       # 미설정 + 날짜 없음
])
plan = m.build_collection_plan(df, ["L", "KK", "GG", "TPC", "MRT"])
for ch, entries in sorted(plan.items()):
    for e in entries:
        print(f"     {ch:4} {str(e['profile']):7} {e['areas']} "
              f"{e['start_date']} ~ {e['end_date']}")
l_profiles = {e["profile"] for e in plan.get("L", [])}
if l_profiles != {"KR", "JP", None}:
    bad.append(f"Klook 묶음이 {l_profiles} (KR/JP/미설정 이어야 한다)")
mrt = [e for e in plan.get("MRT", [])]
if not mrt or mrt[0]["profile"] != "GLOBAL":
    bad.append(f"MRT 가 GLOBAL(9530) 로 안 간다: {mrt}")

need = m.required_profiles(df, ["L", "KK", "GG", "TPC", "MRT"])
print(f"     켜야 할 Chrome: {sorted(need)}")
if "GLOBAL" not in need or "KR" not in need:
    bad.append(f"켜야 할 Chrome 이 빠졌다: {sorted(need)}")

un = m.unconfigured_routes(df, ["L", "KK", "GG", "TPC", "MRT"])
print(f"     미설정: {[(u['area'], u['channel']) for u in un]}")
if not any(u["channel"] == "KK" for u in un):
    bad.append("설정 안 된 (Tokyo, KKday) 를 조용히 넘겼다")
if any(u["profile"] if "profile" in u else False for u in un):
    bad.append("미설정인데 Chrome 이 붙었다")

# ── 5) 화면·Agent 가 같은 함수를 부르는가 ───────────────────────────────
print()
print("  [5] 화면과 Agent 가 같은 곳을 부르는가")
AG = (ROOT / "hub" / "agent.py").read_text(encoding="utf-8")
DP = (ROOT / "hub" / "op_ui" / "dispatch.py").read_text(encoding="utf-8")
for label, cond in (
        ("Agent 가 review 를 안다", 'kind == "review"' in AG),
        ("Agent 가 runner 를 부른다", "review_runner.run(job, p)" in AG),
        ("로컬도 같은 runner", "review_runner.run(j, params)" in DP)):
    print(f"     {label:26} {'예' if cond else '!! 아니오'}")
    if not cond:
        bad.append(f"{label} — 아니다")

# 묶음에 엔진이 들어가는가 (안 들어가면 팀원 PC 에서 '엔진이 없다')
MS = (ROOT / "hub" / "make_share.py").read_text(encoding="utf-8")
for d in ("Review Analyzer/rcore", "Review Analyzer/collectors"):
    if d not in MS:
        bad.append(f"묶음에 {d} 가 안 들어간다")
print(f"     묶음에 엔진 포함: {'예' if 'Review Analyzer/rcore' in MS else '!! 아니오'}")

print()
if bad:
    for b in bad:
        print("  !!", b)
    raise SystemExit(f"!! {len(bad)}건 어긋남")
print("전부 통과 — 엔진은 그대로, Chrome 만 라스트미닛 것으로 바뀌었다")
