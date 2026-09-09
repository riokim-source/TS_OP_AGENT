# -*- coding: utf-8 -*-
"""
팀원에게 넘기는 묶음에 빠진 파일이 없는지 검사.

hub/make_share.py 는 넘길 파일을 하나씩 적어 둔다. 새 파일을 만들고 그 목록에
넣는 것을 잊으면, 이 PC 에서는 잘 되는데 **팀원 것만 죽는다.**

실제로 두 번 났다 (2026-09-09, 둘 다 같은 날 배포에서).

    vi.py       -> import vi_targets     ModuleNotFoundError
    tpc.py / tpc_dom.py / tpc_targets.py 가 통째로 안 들어감
    start_gui.bat 은 들어갔는데 그것이 부르는 gui.py 가 안 들어감

봇은 아침 10시에 도는데, 그때 죽으면 그날 마감을 못 한다.

여기서 보는 것
  1) OTA Close / Klook Open 의 .py 가 전부 목록에 있는가
  2) 넘기는 .py 가 import 하는 같은 폴더 파일이 전부 같이 가는가
  3) 넘기는 .bat 이 부르는 .py 가 전부 같이 가는가

    python hub/tests/test_share_complete.py
"""
import re
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "hub"))

import make_share as ms  # noqa: E402

LISTED = {p for p, _ in ms.INCLUDE_FILES}
DIRS = [p for p, _ in ms.INCLUDE_DIRS]
bad = []


def shipped(rel: str) -> bool:
    if rel in LISTED:
        return True
    return any(rel.startswith(d + "/") for d in DIRS)


# ── 1) 폴더의 .py 가 전부 목록에 있는가 ─────────────────────────────────
print("  [1] 봇 폴더의 파일이 전부 목록에 있는가")
for folder in ("OTA Close", "Klook Open"):
    have = sorted(f"{folder}/{p.name}" for p in (ROOT / folder).glob("*.py"))
    missing = [h for h in have if not shipped(h)]
    print(f"     {folder:12} {len(have)}개 중 빠진 것 {len(missing)}개"
          + ("" if not missing else "  !! " + ", ".join(Path(m).name for m in missing)))
    for m in missing:
        bad.append(f"{m} 가 배포 목록에 없다")

# ── 2) 넘기는 파일이 부르는 같은 폴더 파일도 같이 가는가 ────────────────
print()
print("  [2] import 하는 파일도 같이 가는가")
_IMP = re.compile(r"^\s*(?:import\s+([A-Za-z_][\w]*)|from\s+([A-Za-z_][\w]*)\s+import)",
                  re.M)
for folder in ("OTA Close", "Klook Open"):
    local = {p.stem for p in (ROOT / folder).glob("*.py")}
    for rel in sorted(h for h in LISTED if h.startswith(folder + "/") and h.endswith(".py")):
        f = ROOT / rel
        if not f.exists():
            bad.append(f"{rel} 가 목록에 있는데 파일이 없다")
            continue
        src = f.read_text(encoding="utf-8", errors="replace")
        for m in _IMP.finditer(src):
            mod = m.group(1) or m.group(2)
            if mod not in local:
                continue                       # 표준/외부 라이브러리
            dep = f"{folder}/{mod}.py"
            if not shipped(dep):
                print(f"     !! {rel} 가 {mod} 를 부르는데 {dep} 가 안 간다")
                bad.append(f"{rel} 의 의존 {dep} 가 배포 목록에 없다")
print("     확인 완료" if not bad else "")

# ── 3) .bat 이 부르는 .py 도 같이 가는가 ────────────────────────────────
print()
print("  [3] .bat 이 부르는 파일도 같이 가는가")
_PY = re.compile(r"python[a-z0-9.]*\s+(?:-\S+\s+)*([\w./\\-]+\.py)", re.I)
for rel in sorted(h for h in LISTED if h.endswith(".bat")):
    f = ROOT / rel
    if not f.exists():
        bad.append(f"{rel} 가 목록에 있는데 파일이 없다")
        continue
    folder = str(Path(rel).parent).replace("\\", "/")
    for m in _PY.finditer(f.read_text(encoding="utf-8", errors="replace")):
        name = m.group(1).replace("\\", "/").lstrip("./")
        dep = name if "/" in name else (f"{folder}/{name}" if folder != "." else name)
        if not (ROOT / dep).exists():
            continue                            # 그 .bat 이 쓰는 다른 경로
        if not shipped(dep):
            print(f"     !! {rel} 가 {name} 를 부르는데 안 간다")
            bad.append(f"{rel} 가 부르는 {dep} 가 배포 목록에 없다")
print("     확인 완료" if not bad else "")

# ── 4) 목록에 적힌 파일이 실제로 있는가 ─────────────────────────────────
print()
print("  [4] 목록에 적힌 것이 실제로 있는가")
gone = [p for p in sorted(LISTED) if not (ROOT / p).exists()]
print(f"     {len(LISTED)}개 중 없는 것 {len(gone)}개"
      + ("" if not gone else "  !! " + ", ".join(gone)))
for g in gone:
    bad.append(f"목록의 {g} 가 없다")

print()
if bad:
    for b in bad:
        print("  !!", b)
    raise SystemExit(f"!! {len(bad)}건 어긋남")
print("전부 통과 — 팀원이 받는 묶음에 빠진 파일이 없다")
