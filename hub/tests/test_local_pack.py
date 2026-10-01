# -*- coding: utf-8 -*-
"""
'last minute local' 묶음이 그 PC 에서 혼자 돌 수 있는지 검사.

팀원 PC 에는 저장소도, Agent 도, 중계 열쇠도 없다. 폴더 하나만 받아서
더블클릭한다. 그래서 빠진 파일 하나가 곧 '그날 마감 못 함' 이다
(2026-09-09 에 vi_targets.py 와 tpc 묶음이 빠져서 실제로 그랬다).

여기서 보는 것
  1) 임시 폴더에 진짜로 만들어 보고, 로컬 화면이 import 하는 것이 전부 있는가
  2) Agent·중계용 파일이 안 들어갔는가 (들어가면 '열쇠 달라' 로 번진다)
  3) 실행 파일(.bat)이 ASCII + CRLF 인가  ([[windows-bat-crlf]])
  4) 중계 없이 도는 설정이 박혀 있는가
  5) 클룩 모바일(리모컨) 이 같이 가는가

    python hub/tests/test_local_pack.py
"""
import ast
import os
import re
import sys
import tempfile
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "hub"))

import make_local as M                                    # noqa: E402

bad = []

print("  [1] 임시 폴더에 만들어 본다")
tmp = Path(tempfile.mkdtemp(prefix="lmlocal_"))
dest = tmp / M.DEST_NAME
n, size, _gone = M.make_folder(dest)
print(f"     파일 {n}개 · {size / 1024 / 1024:.1f} MB · {dest.name}")
if n < 100:
    bad.append(f"파일이 너무 적다 ({n}개)")

# ── 2) 로컬 화면이 쓰는 것이 전부 들어갔는가 ──────────────────────────────
print()
print("  [2] 로컬 화면이 부르는 파일이 전부 있는가")
UI = dest / "hub" / "local_ui"
need = ["홈.py", "run.py", "_boot.py"]
for f in need:
    ok = (UI / f).is_file()
    print(f"     {f:12} {'있음' if ok else '!! 없음'}")
    if not ok:
        bad.append(f"로컬 화면 파일이 없다: {f}")

pages = sorted((UI / "pages").glob("*.py"))
print(f"     pages {len(pages)}개: {[p.name for p in pages]}")
if len(pages) < 9:
    bad.append(f"탭이 {len(pages)}개뿐이다")

# 로컬 화면이 run_shared 로 부르는 중앙판 화면이 같이 왔는가
for p in pages:
    for name in re.findall(r'run_shared\("([^"]+)"\)', p.read_text(encoding="utf-8")):
        ok = (dest / "hub" / "op_ui" / "pages" / name).is_file()
        print(f"     {p.name} -> {name} {'' if ok else '  !! 빠짐'}")
        if not ok:
            bad.append(f"{p.name} 이 부르는 {name} 이 묶음에 없다")

# 로컬 화면이 import 하는 모듈이 hub 안에 있는가 (streamlit 같은 외부는 제외)
HUB_MODULES = {p.stem for p in (dest / "hub" / "op_ui").glob("*.py")}
for p in [UI / "홈.py"] + pages:
    tree = ast.parse(p.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        names = []
        if isinstance(node, ast.Import):
            names = [a.name.split(".")[0] for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names = [node.module.split(".")[0]]
        for mod in names:
            if mod in HUB_MODULES and not (dest / "hub" / "op_ui" / f"{mod}.py").is_file():
                bad.append(f"{p.name} 이 쓰는 {mod}.py 가 없다")

# ── 3) Agent·중계 것이 안 들어갔는가 ──────────────────────────────────────
print()
print("  [3] Agent·중계용은 빠졌는가")
for f in ["Agent 켜기.bat", "OP System 열기.bat", "설치안내.md",
          "hub/data/firebase_service_account.json", "hub/data/firebase.json"]:
    gone = not (dest / f).exists()
    print(f"     {f:42} {'빠짐' if gone else '!! 들어감'}")
    if not gone:
        bad.append(f"로컬판에 {f} 가 들어갔다")

# ── 4) 실행 파일 ──────────────────────────────────────────────────────────
print()
print("  [4] 실행 파일 (.bat)")
raw = (dest / M.LAUNCHER).read_bytes()
is_ascii = all(b < 128 for b in raw)
crlf = raw.count(b"\r\n")
lone_cr = len(re.findall(rb"\r(?!\n)", raw))
print(f"     {M.LAUNCHER} · ASCII {is_ascii} · CRLF {crlf}줄 · 외톨이 CR {lone_cr}개")
if not is_ascii:
    bad.append("실행 파일에 한글이 들어갔다 (cmd 가 줄을 잘못 읽는다)")
if crlf == 0:
    bad.append("실행 파일이 CRLF 가 아니다")
if lone_cr:
    bad.append(f"실행 파일에 CR 이 겹쳐 있다 ({lone_cr}개) — cmd 가 줄 끝을 명령으로 읽는다")
body = raw.decode("ascii")
for must in ["hub\\local_ui\\run.py", "pause"]:
    if must not in body:
        bad.append(f"실행 파일에 '{must}' 가 없다")
if "streamlit" in body:
    bad.append("실행 파일이 streamlit 을 직접 부른다 (한글 파일명은 run.py 가 맡는다)")

# ── 5) 중계 없이 도는 설정 ────────────────────────────────────────────────
print()
print("  [5] 중계 없이 이 PC 에서만")
boot = (UI / "_boot.py").read_text(encoding="utf-8")
runpy_src = (UI / "run.py").read_text(encoding="utf-8")
CHECKS = [
    ("_boot 가 local 모드로 못 박음", 'os.environ["LMHUB_MODE"] = "local"' in boot),
    ("_boot 가 file 큐로 못 박음", 'os.environ["LMHUB_QUEUE"] = "file"' in boot),
    ("암호 안 물음", 'os.environ["LMHUB_LOCAL_ONLY"] = "1"' in boot),
    ("run.py 도 같은 값", '"LMHUB_MODE": "local"' in runpy_src),
    ("포트가 중앙판과 다름", str(M.PORT) in runpy_src and M.PORT != 8610),
]
for label, ok in CHECKS:
    print(f"     {label:26} {'예' if ok else '!! 아니오'}")
    if not ok:
        bad.append(f"{label} — 아니다")

req = (dest / "requirements.txt").read_text(encoding="utf-8")
# 설명(#)은 빼고 '실제로 설치하는 줄' 만 본다
pkgs = [ln.strip() for ln in req.splitlines()
        if ln.strip() and not ln.strip().startswith("#")]
has_auth = any(p.lower().startswith("google-auth") for p in pkgs)
print(f"     설치 줄 {len(pkgs)}개 · google-auth {'!! 있음' if has_auth else '없음'}")
if has_auth:
    bad.append("로컬판 requirements 에 중계 인증 패키지가 남아 있다")
req = "\n".join(pkgs)
for must in ("streamlit", "playwright", "pandas"):
    if must not in req:
        bad.append(f"requirements 에 {must} 가 없다")

import json                                               # noqa: E402
pack = json.loads((dest / "hub" / "data" / "pack.json").read_text(encoding="utf-8"))
print(f"     묶음 표시: {pack}")
if pack.get("kind") != "local":
    bad.append("pack.json 이 local 이 아니다 (설치할 때 열쇠를 찾는다)")
ps1 = (dest / "hub" / "install.ps1").read_text(encoding="utf-8", errors="replace")
if "pack.json" not in ps1:
    bad.append("설치 스크립트가 묶음 종류를 안 본다 — 로컬판인데 '열쇠 없음' 을 띄운다")

# ── 6) 클룩 모바일 ────────────────────────────────────────────────────────
print()
print("  [6] 클룩 모바일 (휴대폰 리모컨)")
for f in ["Klook Open/mobile_server.py", "Klook Open/klook_core.py",
          "Klook Open/klook_worker.py", "Klook Open/packages.py",
          "hub/core/mobile.py"]:
    ok = (dest / f).is_file()
    print(f"     {f:32} {'있음' if ok else '!! 없음'}")
    if not ok:
        bad.append(f"묶음에 {f} 가 없다")

# ── 7) 손님 정보가 안 들어갔는가 ──────────────────────────────────────────
print()
print("  [7] 손님 정보·개인 파일")
leaked = [p.relative_to(dest).as_posix() for p in dest.rglob("*")
          if p.is_file() and (p.suffix.lower() in (".xlsx", ".xls", ".csv")
                              or p.name in ("lm_entries.json", "last_upload.bin",
                                            "last_upload.json", "agent_config.json"))]
print(f"     {len(leaked)}개 {leaked[:5]}")
if leaked:
    bad.append(f"묶음에 개인/예약 파일이 들어갔다: {leaked[:3]}")

import shutil                                             # noqa: E402
shutil.rmtree(tmp, ignore_errors=True)

print()
if bad:
    for b in bad:
        print("  !!", b)
    raise SystemExit(f"!! {len(bad)}건 어긋남")
print("전부 통과 — 폴더 하나로 그 PC 에서 혼자 돈다")
