# -*- coding: utf-8 -*-
"""
'Chrome 은 떠 있는데 봇이 못 붙는' 상태를 빨리 잡아내는지 검사.

2026-09-01 팀원 PC 마감 / 2026-09-02 이 PC 오픈에서 같은 일이 났다.

    /json/version   200 OK          -> 화면에는 'Chrome 연결됨'
    connect_over_cdp <ws connected> 뒤 180초 멈춤 -> 워커마다 3분씩 버리고 실패

  09-01 마감: 9522/9523 이 이래서 KKday 4워커 + GG 2지역 전멸 (41분)
  09-02 오픈: 9530 이 이래서 MRT 3건 전멸

브라우저를 띄우지 않고, 코드가 갖춰졌는지와 판정만 확인한다.

    python hub/tests/test_chrome_guard.py
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "hub"))

bad = []

# ── 1) connect_over_cdp 에 전부 타임아웃이 붙었나 ────────────────────────
print("  [1] connect_over_cdp 타임아웃")
missing = []
for p in sorted(list((ROOT / "OTA Close").rglob("*.py"))
                + list((ROOT / "Klook Open").glob("*.py"))):
    if ".venv" in str(p):
        continue
    src = p.read_text(encoding="utf-8", errors="replace")
    for m in re.finditer(r"connect_over_cdp\(([^)]*)\)", src, re.S):
        if "timeout" not in m.group(1):
            missing.append(f"{p.name}: {m.group(1)[:40]}")
print(f"     타임아웃 없는 호출: {missing or '없음'}")
if missing:
    bad.append(f"타임아웃 없는 connect_over_cdp {len(missing)}곳")

for p, name in ((ROOT / "OTA Close" / "mrt.py", "mrt"),
                (ROOT / "OTA Close" / "gg_open.py", "gg_open"),
                (ROOT / "OTA Close" / "klook.py", "klook"),
                (ROOT / "OTA Close" / "shared" / "chrome_setup.py", "chrome_setup"),
                (ROOT / "Klook Open" / "klook_worker.py", "klook_worker")):
    src = p.read_text(encoding="utf-8", errors="replace")
    if "CDP_CONNECT_TIMEOUT_MS = int(" not in src:
        bad.append(f"{name}: 타임아웃 상수 없음")
    if not re.search(r"^import os$", src, re.M):
        bad.append(f"{name}: import os 없음 (상수가 os.environ 을 쓴다)")
print("     상수 선언 + import os: 확인")

# ── 2) 실행 전에 '진짜 CDP' 로 확인하는가 ────────────────────────────────
print()
print("  [2] 실행 전 CDP 확인")
from core.routing import cdp_attach_ok, cdp_ready  # noqa: E402

for p, name in ((ROOT / "hub" / "core" / "close" / "runner.py", "마감"),
                (ROOT / "hub" / "core" / "opens" / "mrt_open.py", "MRT 오픈"),
                (ROOT / "hub" / "core" / "opens" / "gg_open.py", "GG 오픈"),
                (ROOT / "hub" / "core" / "opens" / "klook_open.py", "Klook 오픈")):
    has = "cdp_attach_ok(" in p.read_text(encoding="utf-8", errors="replace")
    print(f"     {name:10} {'확인함' if has else '!! 안 함'}")
    if not has:
        bad.append(f"{name}: cdp_attach_ok 를 안 부른다")

# 꺼진 포트는 빨리 False 여야 한다 (오래 매달리면 의미가 없다)
import time  # noqa: E402

t0 = time.perf_counter()
ok, why = cdp_attach_ok(59999, timeout_ms=8000)     # 아무도 없는 포트
el = time.perf_counter() - t0
print(f"     꺼진 포트 판정: {ok} ({el:.1f}초) {why[:40]}")
if ok:
    bad.append("꺼진 포트를 '된다' 고 한다")
if el > 12:
    bad.append(f"꺼진 포트 판정이 너무 느리다 ({el:.1f}초)")

# ── 3) Klook: <a> 가 늦게 떠도 기다리는가 ────────────────────────────────
print()
print("  [3] Klook 상세 진입 — href 없으면 기다림")
src = (ROOT / "Klook Open" / "klook_worker.py").read_text(encoding="utf-8",
                                                          errors="replace")
has_wait = "<a> 가 늦게 떠서 다시 잡음" in src
print(f"     {'기다린다' if has_wait else '!! 바로 포기한다'}")
if not has_wait:
    bad.append("klook_worker: href 없을 때 재시도가 없다")

# ── 4) 실행 로그에 코드 버전이 찍히는가 ──────────────────────────────────
print()
print("  [4] 코드 버전 표시")
from core.paths import code_version, version_line  # noqa: E402

v = code_version()
print(f"     {version_line()}")
if not v.get("commit") or v["commit"] == "?":
    bad.append("코드 버전을 못 읽는다")
if "version_line()" not in (ROOT / "hub" / "agent.py").read_text(encoding="utf-8",
                                                                errors="replace"):
    bad.append("agent 가 버전을 로그에 안 찍는다")
print("     agent 로그에 포함: 확인")

print()
if bad:
    for b in bad:
        print("  !!", b)
    raise SystemExit(f"!! {len(bad)}건 어긋남")
print("전부 통과 — 못 붙는 Chrome 을 빨리 잡고, 어느 버전인지 남는다")
