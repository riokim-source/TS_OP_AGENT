# -*- coding: utf-8 -*-
"""
Agent 이름표가 다른 PC 에서 따라오는 것.

2026-09-27: 팀원이 자기 PC 에서 Agent 를 켰는데, 웹의 '실행할 PC' 에는
'RIO/ktour'(운영자 PC) 한 대만 떴다. 중계 지점을 보니 등록된 PC 가 하나뿐인데
바로 몇 초 전 신호가 있었다 — 운영자 PC 에는 Agent 가 안 돌고 있었으니,
그 신호는 팀원 PC 가 **남의 이름표를 달고** 보낸 것이었다.

원인: 폴더를 통째로 복사해 가면 hub/data/agent_config.json 까지 딸려간다.
(GitHub 묶음에는 안 들어간다. 사람이 손으로 복사할 때만 생긴다)

이름이 같으면 중계 지점에서 서로의 신호를 덮어쓴다. 화면에는 한 대만 보이고,
작업을 보내면 엉뚱한 PC 가 가져갈 수도 있다.

고친 것: 이름표에 '이 이름표를 만든 PC' 를 같이 적는다. 다른 PC 에서 켜지면
자동 이름을 다시 만든다. 단, 사람이 직접 지은 이름은 건드리지 않는다.

    python hub/tests/test_agent_name.py
"""
import importlib
import json
import sys
import tempfile
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "hub"))

import agent as A                                    # noqa: E402

bad = []
THIS = A.default_agent_name()
print(f"  이 PC 의 자동 이름: {THIS}")
if "/" not in THIS:
    bad.append(f"자동 이름 모양이 이상하다: {THIS}")


def run_with(config: dict | None):
    """임시 폴더에 이름표를 두고 load_config() 를 돌린다."""
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "agent_config.json"
        if config is not None:
            path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
        old = A.CONFIG
        A.CONFIG = path
        try:
            got = A.load_config()
        finally:
            A.CONFIG = old
        return got, json.loads(path.read_text(encoding="utf-8"))


print()
print("  [1] 남의 PC 에서 복사해 온 이름표")
CASES = [
    ("made_on 이 남의 PC", {"agent": "RIO/ktour", "made_on": "RIO/ktour"}, THIS),
    ("made_on 없고 자동이름 모양", {"agent": "OTHERPC/hee"}, THIS),
    ("내 PC 이름 그대로", {"agent": THIS, "made_on": THIS}, THIS),
    ("사람이 지은 이름", {"agent": "부산PC", "made_on": THIS}, "부산PC"),
    ("사람이 지은 이름 (made_on 없음)", {"agent": "부산PC"}, "부산PC"),
    ("이름표가 아예 없음", None, THIS),
    ("빈 이름", {"agent": ""}, THIS),
]
for label, cfg, want in CASES:
    got, saved = run_with(cfg)
    ok = got["agent"] == want
    print(f"     {label:28} -> '{got['agent']}'{'' if ok else f'   !! 기대 {want}'}")
    if not ok:
        bad.append(f"{label}: '{got['agent']}' (기대 '{want}')")
    if saved.get("made_on") != THIS:
        bad.append(f"{label}: made_on 이 '{saved.get('made_on')}' 로 저장됐다")

print()
print("  [2] 사람이 지은 이름을 자동 이름으로 착각하지 않는가")
for name, want_auto in (("RIO/ktour", True), ("OTHERPC/hee", True),
                        ("부산PC", False), ("", False), ("a/b/c", False),
                        ("/ktour", False), ("RIO/", False)):
    got = A.looks_auto_name(name)
    mark = "" if got == want_auto else f"   !! 기대 {want_auto}"
    print(f"     '{name}' -> {'자동' if got else '사람이 지음'}{mark}")
    if got != want_auto:
        bad.append(f"'{name}' 판정이 {got}")

print()
print("  [3] 묶음에 이름표가 안 들어가는가")
MS = (ROOT / "hub" / "make_share.py").read_text(encoding="utf-8")
# INCLUDE 목록에 있으면 안 된다 (SHARE_GITIGNORE 에 적힌 것은 '빼라'는 뜻)
include_part = MS.split("SHARE_GITIGNORE")[0]
in_bundle = "agent_config.json" in include_part
print(f"     묶음 목록에 있음: {'!! 예' if in_bundle else '아니오'}")
if in_bundle:
    bad.append("묶음에 남의 이름표가 딸려간다")

print()
if bad:
    for b in bad:
        print("  !!", b)
    raise SystemExit(f"!! {len(bad)}건 어긋남")
print("전부 통과 — 복사해 온 이름표는 이 PC 이름으로 바뀌고, 지은 이름은 남는다")
