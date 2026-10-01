# -*- coding: utf-8 -*-
"""
두 봇 폴더에 같은 이름의 파일이 있어서 엉뚱한 것이 불려 오는 것.

2026-10-01 로컬판 오픈:

    열지 못한 OTA — KLOOK: AttributeError: module 'main' has no attribute 'BASE_DIR'

상품도 Chrome 도 멀쩡했다. main.py 가 두 개라서 생긴 일이다.

    Klook Open/main.py    BASE_DIR, WORKER, parse_tasks …   <- 쓰려던 것
    OTA Close/main.py     마감 봇 진입점

통합 화면은 한 프로세스 안에서 두 폴더를 모두 sys.path 에 올린다. 어느 쪽이
앞에 서는지는 '그날 어느 화면을 먼저 열었나' 로 정해진다 — 마감·수량 계산을
먼저 건드린 날에는 OTA Close 가 앞서고, 그 뒤 Klook 오픈이 통째로 죽는다.
즉 **어떤 날은 되고 어떤 날은 안 되는** 모양으로 나타난다.

고친 것: klook_core 는 이름(`import main`)이 아니라 **옆에 있는 파일**을
직접 읽는다. sys.path 순서가 어떻든 같은 것이 온다.

여기서는 그 상황을 실제로 만들어서 본다 (sys.modules 가 깨끗해야 하므로
각각 별도 프로세스로 돌린다).

    python hub/tests/test_module_name_clash.py
"""
import os
import subprocess
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[2]
KLOOK = ROOT / "Klook Open"
CLOSE = ROOT / "OTA Close"

bad = []


def run(code: str) -> tuple[int, str]:
    """
    따로 띄워서 돌린다 (sys.modules 가 깨끗해야 순서를 제대로 볼 수 있다).

    ⚠️ 환경변수를 몇 개만 추려서 넘기면 안 된다. Windows 는 빠진 값을 글자
       그대로 두고 폴더를 만들어 버린다 — 실제로 저장소에 '%SystemDrive%'
       라는 폴더가 생겨 커밋까지 따라갔다. 지금 환경을 그대로 물려준다.
    """
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True,
                       text=True, encoding="utf-8", errors="replace",
                       timeout=180, env=env, cwd=str(ROOT))
    return r.returncode, (r.stdout + r.stderr).strip()


# ── 1) 같은 이름이 무엇무엇인가 (알고는 있어야 한다) ──────────────────────
print("  [1] 두 폴더에 같은 이름으로 있는 파일")
dup = sorted({p.name for p in KLOOK.glob("*.py")} & {p.name for p in CLOSE.glob("*.py")})
print(f"     {dup}")
if "main.py" not in dup:
    print("     (main.py 가 더 이상 겹치지 않는다 — 이 검사는 그래도 둔다)")

# ── 2) 마감 폴더가 앞에 있어도 Klook 오픈이 제 것을 찾는가 ────────────────
print()
print("  [2] sys.path 순서를 뒤집어 본다")
CASES = [
    ("마감 폴더가 앞", [str(KLOOK), str(CLOSE)]),      # 나중 insert 가 앞선다
    ("Klook 폴더가 앞", [str(CLOSE), str(KLOOK)]),
]
for label, order in CASES:
    code = (
        "import sys\n"
        + "".join(f"sys.path.insert(0, r'{d}')\n" for d in order)
        + "import klook_core as k\n"
        "print('cli=' + k.cli.__file__)\n"
        "print('base=' + str(k.BASE_DIR))\n"
        "assert k.WORKER.name == 'klook_worker.py', k.WORKER\n"
        "assert k.SUPPORTED_REGIONS[0] == 'KOREA'\n"
    )
    rc, out = run(code)
    first = out.splitlines()[0] if out else "(출력 없음)"
    ok = rc == 0 and "Klook Open" in out
    print(f"     {label:14} {'OK' if ok else '!! 실패'}  {first[:90]}")
    if not ok:
        bad.append(f"{label}: {out[-200:]}")

# ── 3) 반대쪽도 멀쩡한가 (마감·수량 계산이 OTA Close 것을 본다) ───────────
print()
print("  [3] Klook 폴더가 앞서도 마감 쪽 목록은 제 것을 보는가")
code = (
    "import sys\n"
    f"sys.path.insert(0, r'{CLOSE}')\n"
    f"sys.path.insert(0, r'{KLOOK}')\n"
    f"sys.path.insert(0, r'{ROOT / 'hub'}')\n"
    "from core.lastmin import calc\n"
    "import vi_targets, tpc_targets\n"
    "print('vi=' + vi_targets.__file__)\n"
    "print('tpc=' + tpc_targets.__file__)\n"
)
rc, out = run(code)
ok = rc == 0 and out.count("OTA Close") >= 2
print(f"     {'OK' if ok else '!! 실패'}  {out.splitlines()[0][:90] if out else ''}")
if not ok:
    bad.append(f"마감 쪽 목록: {out[-200:]}")

# ── 4) 이름으로 부르는 자리가 남아 있지 않은가 ────────────────────────────
print()
print("  [4] 화면·봇이 한 프로세스에서 'import main' 을 하지 않는가")
src = (KLOOK / "klook_core.py").read_text(encoding="utf-8")
by_name = "\nimport main" in src or "\nfrom main import" in src
by_path = "spec_from_file_location" in src and '"klook_cli"' in src
print(f"     klook_core: 이름으로 부름 {by_name} · 파일로 읽음 {by_path}")
if by_name:
    bad.append("klook_core 가 아직 'import main' 을 한다")
if not by_path:
    bad.append("klook_core 가 옆 파일을 직접 읽지 않는다")

print()
if bad:
    for b in bad:
        print("  !!", b)
    raise SystemExit(f"!! {len(bad)}건 어긋남")
print("전부 통과 — 폴더 순서가 어떻든 각자 제 main.py 를 쓴다")
