# -*- coding: utf-8 -*-
"""
언어 변형('(한)','(중)')을 어떻게 가르는지 검사.

2026-09-03: 'Osaka Kobe (Night)(한) 6' 을 열라고 했는데 **영어 상품이 6자리
열렸다.** 한국어 상품은 닫힌 채로 남았다. 그때는 이름이 같은 번호를 쓰면
아예 열지 않고 사람에게 넘겼다.

2026-09-27: 화면을 보고 방식을 바꿨다. Klook 새 UI(activity)는 한 Activity
안에 **언어별 줄(unit)** 이 따로 있다.

    1 English · Adult   ID 828952387910
    2 Korean  · Adult   ID 828952387912   <- '(한)' 은 여기로 들어간다
    3 English · Child   / 4 Korean · Child

그래서 activity 는 봇이 그 줄을 골라서 연다. 대신 **그 언어 줄을 못 찾으면
첫 줄(영어)로 흘러가지 않고 실패**해야 한다. 그게 09-03 사고의 진짜 원인이었다.

  - activity  : 막지 않는다 (화면에서 언어 줄로 가른다)
  - package   : 번호가 언어별로 따로 있어야 한다. 같으면 맵핑이 잘못된 것이라
                그대로 막는다 — 구버전 화면에는 가를 근거가 없다.

    python hub/tests/test_klook_variant_ambiguous.py
"""
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "hub"))
sys.path.insert(0, str(ROOT / "Klook Open"))

from core.opens import klook_open as K   # noqa: E402
import klook_worker as W                 # noqa: E402

problems = []
bad = K.ambiguous_names()
print(f"  막히는 이름: {len(bad)}개  {list(bad)[:3]}")

# ── 1) activity 언어 변형은 이제 봇이 연다 ──────────────────────────────
print()
print("  [1] activity 변형은 막지 않는다 (화면에서 언어 줄로 가른다)")
FREED = ["Osaka Kobe (Night)(한)", "Sapporo Otaru(한)", "Toyako Niseko(한)",
         "MBC 스튜디오(한)", "Itoshima Marine(한)", "Hunter Valley(WINERY)(중)"]
for n in FREED:
    ok = n not in bad
    print(f"     {n:30} {'봇이 연다' if ok else '!! 아직 막힘'}")
    if not ok:
        problems.append(f"{n} 이 아직 막혀 있다")

# ── 2) 못 찾으면 영어로 흘러가면 안 된다 (09-03 사고의 진짜 원인) ────────
print()
print("  [2] 언어를 못 찾았을 때 첫 줄(영어)로 흘러가지 않는가")
for name, want in (("Osaka Kobe (Night)(한)", True), ("Toyako Niseko(중)", True),
                   ("Osaka Kobe (Night)", False), ("에버", False)):
    got = bool(W._parse_language_target(name).get("explicit"))
    print(f"     {name:26} 언어 명시={got}")
    if got != want:
        problems.append(f"{name}: explicit={got} (기대 {want})")

WSRC = (ROOT / "Klook Open" / "klook_worker.py").read_text(encoding="utf-8")
guards = WSRC.count("if (langExplicit) return {ok:false, reason:'lang_not_found'}")
print(f"     'See schedule 첫 줄 클릭' 앞 가드: {guards}곳")
if guards < 2:
    problems.append(f"첫 줄로 흘러가는 자리에 가드가 {guards}곳뿐이다")
if "} else if (window.__targetLangExplicit) {" not in WSRC:
    problems.append("Adult 줄 매칭 실패 때 영어로 흘러가는 자리에 가드가 없다")
if "window.__targetLangExplicit = !!args.explicit" not in WSRC:
    problems.append("화면에 '언어를 명시했다' 를 안 넘긴다 — 가드가 늘 꺼져 있다")

# ── 3) package 방식에서 번호가 같으면 그대로 막는다 ─────────────────────
print()
print("  [3] package 방식은 번호가 같으면 막는다 (가를 근거가 없다)")
real = K._packages_map()
fake = dict(real)
fake["테스트투어"] = {"id": "999999", "workflow": "package", "region": "KOREA"}
fake["테스트투어(한)"] = {"id": "999999", "workflow": "package", "region": "KOREA"}
fake["테스트액티비티"] = {"id": "888888", "workflow": "activity", "region": "KOREA"}
fake["테스트액티비티(한)"] = {"id": "888888", "workflow": "activity", "region": "KOREA"}
_orig = K._packages_map
K._packages_map = lambda: fake
try:
    got = K.ambiguous_names()
finally:
    K._packages_map = _orig
print(f"     package 중복 -> {'막음' if '테스트투어(한)' in got else '!! 안 막음'}")
print(f"     activity 중복 -> {'!! 막음' if '테스트액티비티(한)' in got else '안 막음(정상)'}")
if "테스트투어(한)" not in got:
    problems.append("package 번호가 같은데 안 막는다 — 엉뚱한 상품이 열린다")
if "테스트액티비티(한)" in got:
    problems.append("activity 를 막는다 — 봇이 열 수 있는데 사람에게 넘긴다")

# ── 4) 계획이 그대로 봇에 넘어가는가 ────────────────────────────────────
print()
print("  [4] 오픈 계획")
plan = [
    {"channel": "KLOOK", "mode": "qty", "product": "Osaka Kobe (Night)(한)", "qty": 6},
    {"channel": "KLOOK", "mode": "qty", "product": "Kyoto & Nara(중)", "qty": 4},
    {"channel": "KLOOK", "mode": "qty", "product": "경주", "qty": 10},
]
text = K.plan_to_text(plan)
dropped = K.dropped_for_ambiguity(plan)
print(f"     봇에 넘어가는 것: {text}")
print(f"     빠진 것: {[d['name'] for d in dropped]}")
for keep in ("Osaka Kobe (Night)(한)", "Kyoto & Nara(중)", "경주"):
    if keep not in text:
        problems.append(f"{keep} 가 빠졌다")
if dropped:
    problems.append(f"빠진 것이 있다: {dropped}")

# ── 5) 2026-09-27 에 넣은 맵핑이 다 있는가 ──────────────────────────────
print()
print("  [5] 새로 넣은 맵핑")
import packages as PK                                # noqa: E402
NEW = {
    "Canberra Floriade": ("221170", "AUSTRALIA", "activity"),
    "Sydney Jacaranda": ("221120", "AUSTRALIA", "activity"),
    "Attack on Titan": ("232801", "JAPAN", "activity"),
    "Autumn Asahidake": ("225351", "JAPAN", "activity"),
    "Autumn Kumamoto": ("225564", "JAPAN", "activity"),
    "Autumn Shikotsu": ("221815", "JAPAN", "activity"),
    "Autumn Hitachi": ("145488", "JAPAN", "activity"),
    "Autumn Takao": ("226107", "JAPAN", "activity"),
    "Autumn Nara": ("223971", "JAPAN", "activity"),
    "Autumn Shiga": ("226773", "JAPAN", "activity"),
    "Autumn Nagoya": ("227081", "JAPAN", "activity"),
    "Autumn Nikko": ("571257", "JAPAN", "package"),
    "Autumn Nikko(한)": ("722543", "JAPAN", "package"),
    "Autumn Nikko(중)": ("722665", "JAPAN", "package"),
    "Autumn Usa": ("571296", "JAPAN", "package"),
    "Autumn Usa(한)": ("716818", "JAPAN", "package"),
    "Autumn Usa(중)": ("716830", "JAPAN", "package"),
    "아미천마": ("224469", "KOREA", "activity"),
    "설악산등산": ("233675", "KOREA", "activity"),
}
miss = 0
for name, (pid, region, wf) in NEW.items():
    v = PK.PACKAGES.get(name)
    ok = v and str(v["id"]).strip() == pid and v["region"] == region and v["workflow"] == wf
    if not ok:
        miss += 1
        problems.append(f"{name}: {v} (기대 {pid}/{region}/{wf})")
print(f"     {len(NEW) - miss}/{len(NEW)}개 정상")
# 번호가 같은 package 변형이 새로 생기지 않았는지도 같이 본다
if any(n in K.ambiguous_names() for n in NEW):
    problems.append("새로 넣은 맵핑 중에 번호가 겹치는 것이 있다")

print()
if problems:
    for p in problems:
        print("  !!", p)
    raise SystemExit(f"!! {len(problems)}건 어긋남 — 엉뚱한 상품이 열릴 수 있다")
print("전부 통과 — activity 는 언어 줄로 가르고, 못 찾으면 열지 않는다")
