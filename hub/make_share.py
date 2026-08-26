# -*- coding: utf-8 -*-
"""
make_share.py
팀원에게 넘길 '최소 파일 묶음' 을 만든다.

    python hub/make_share.py              → 바탕화면에 폴더로
    python hub/make_share.py --zip        → 압축 파일 하나로
    python hub/make_share.py --with-key   → 중계 열쇠까지 포함 (USB/사내공유 전용)

왜 필요한가
    지금 폴더는 169MB 다. 대부분이 실행 로그·백업본·가상환경이라 팀원에게는
    쓸모가 없고, 예약 엑셀에는 손님 정보가 들어 있다. 통째로 건네면
    안 될 것까지 같이 간다.

    그래서 '무엇을 넘길지' 를 사람 기억이 아니라 이 파일이 정한다.
    화면의 [필요 파일 다운로드] 도 같은 목록을 쓴다 (build_manifest).

⚠️ 열쇠(firebase_service_account.json)는 기본으로 넣지 않는다.
   넣으려면 --with-key 를 명시해야 하고, 그렇게 만든 폴더는 사내
   공유 폴더나 USB 로만 건넨다. 메일·메신저로 보내지 않는다.
"""
from __future__ import annotations

import argparse
import io
import os
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KEY_REL = "hub/data/firebase_service_account.json"

# 팀원이 받아 갈 저장소. 화면의 [필요 파일 다운로드] 가 이 주소로 링크한다.
# 여기를 바꾸면 화면도 같이 바뀐다 (주소를 두 곳에 적지 않는다).
SHARE_REPO = "https://github.com/riokim-source/TS_OP_AGENT"
SHARE_BRANCH = "main"


def repo_url() -> str:
    """설정된 저장소 주소. 환경변수/Secrets 로 덮어쓸 수 있다."""
    import os
    v = str(os.environ.get("LMHUB_SHARE_REPO") or "").strip()
    if v:
        return v.rstrip("/")
    try:
        import streamlit as st
        v = str(st.secrets.get("SHARE_REPO_URL") or "").strip()   # type: ignore[attr-defined]
        if v:
            return v.rstrip("/")
    except Exception:
        pass
    return SHARE_REPO.rstrip("/")


def zip_url() -> str:
    """GitHub 의 'Download ZIP' 주소. Git 을 몰라도 받을 수 있다."""
    return f"{repo_url()}/archive/refs/heads/{SHARE_BRANCH}.zip"

# ── 무엇을 넘기나 ────────────────────────────────────────────────────────
# (경로, 설명) — 폴더면 아래 규칙으로 걸러서 통째로 넣는다.
INCLUDE_DIRS = [
    ("hub/core", "업무 로직 (배분 규칙, 메모 생성, 오픈 계획)"),
    ("hub/op_ui", "화면 (중앙에서 돌지만, 비상시 이 PC 에서도 열 수 있다)"),
    ("hub/tests", "회귀 테스트 — 고친 뒤 확인용"),
    ("OTA Close/shared", "마감 봇 공통 코드"),
    ("OTA Close/chrome_launchers", "Chrome 프로필 실행"),
]

INCLUDE_FILES = [
    ("hub/agent.py", "이 PC 에서 작업을 받아 실행한다 (매일 켤 것)"),
    ("hub/launch_chrome.py", "Chrome 프로필 실행"),
    ("hub/install.ps1", "설치 확인"),
    ("hub/banner.py", "실행 창 한글 안내 (.bat 은 ASCII 로만 둔다)"),
    ("hub/make_share.py", "이 묶음을 다시 만드는 스크립트"),
    ("hub/data/routing.json", "지역 x OTA 연결"),
    ("hub/data/productmap.json", "상품 맵핑"),
    ("hub/data/gg_pickups.json", "GG 픽업 구분"),
    ("OTA Close/main.py", "마감 봇 본체"),
    ("OTA Close/klook.py", "Klook 마감"),
    ("OTA Close/kkday.py", "KKday 마감"),
    ("OTA Close/gg.py", "GetYourGuide 마감"),
    ("OTA Close/gg_open.py", "GetYourGuide 오픈"),
    ("OTA Close/vi.py", "Viator 마감"),
    ("OTA Close/mrt.py", "MyRealTrip 마감/오픈"),
    ("OTA Close/requirements.txt", "마감 봇 패키지"),
    ("Klook Open/main.py", "Klook 오픈 본체"),
    ("Klook Open/klook_core.py", "Klook 오픈 공통"),
    ("Klook Open/klook_worker.py", "Klook 오픈 실제 조작"),
    ("Klook Open/packages.py", "Klook 상품/패키지 목록"),
    ("Klook Open/mobile_server.py", "휴대폰에서 오픈 실행 (이 PC 의 봇을 원격 조작)"),
    ("Klook Open/gui.py", "PC 창으로 오픈 실행"),
    ("Klook Open/README_MOBILE.md", "휴대폰 접속 방법"),
    ("Klook Open/start_mobile.bat", "휴대폰용 서버 켜기"),
    ("Klook Open/start_gui.bat", "PC 창 켜기"),
    ("Klook Open/start_main.bat", "명령줄로 실행"),
    ("Klook Open/start_chrome_korea.bat", "한국 Chrome"),
    ("Klook Open/start_chrome_japan.bat", "일본 Chrome"),
    ("Klook Open/start_chrome_australia.bat", "호주 Chrome"),
    ("Klook Open/start_chrome_uk.bat", "영국 Chrome"),
    ("OTA Close/start_all_chromes.bat", "Chrome 전부 켜기"),
    ("OTA Close/start_main.bat", "마감 명령줄 실행"),
    ("OTA Close/start_gui.bat", "마감 PC 창"),
    ("OTA Close/README.md", "마감 봇 설명"),
    ("requirements.txt", "필요한 패키지"),
    ("설치.bat", "① 설치 — 처음 한 번"),
    ("Agent 켜기.bat", "② 매일 아침 이것만 켜면 된다"),
    ("OP System 열기.bat", "비상용 — 중앙이 멈췄을 때 이 PC 에서 직접"),
    ("설치안내.md", "읽어 보세요"),
    ("- MRT 맵핑리스트.txt", "MRT 상품 맵핑 (참고)"),
    ("- kkday 맵핑리스트.txt", "KKday 상품 맵핑 (참고)"),
]

# 폴더를 넣을 때 걸러내는 것
SKIP_DIRS = {"__pycache__", ".venv", "venv", "logs", "discover", "queue",
             ".git", ".idea", ".vscode"}
SKIP_SUFFIX = (".pyc", ".xlsx", ".xls", ".csv", ".exe", ".log")


def _skip(p: Path) -> bool:
    if any(part in SKIP_DIRS for part in p.parts):
        return True
    if p.suffix.lower() in SKIP_SUFFIX:
        return True
    name = p.name
    return ".bak" in name or name.endswith("~")


def build_manifest(with_key: bool = False) -> list[tuple[str, str]]:
    """
    넘길 파일 목록을 (저장소 기준 경로, 설명) 으로 돌려준다.
    화면의 [필요 파일 다운로드] 도 이 함수를 쓴다 — 목록이 두 곳에 생기면 어긋난다.
    """
    out: list[tuple[str, str]] = []
    for rel, why in INCLUDE_FILES:
        if (ROOT / rel).is_file():
            out.append((rel, why))
    for rel, why in INCLUDE_DIRS:
        base = ROOT / rel
        if not base.is_dir():
            continue
        for p in sorted(base.rglob("*")):
            if p.is_file() and not _skip(p.relative_to(ROOT)):
                out.append((p.relative_to(ROOT).as_posix(), why))
    if with_key and (ROOT / KEY_REL).is_file():
        out.append((KEY_REL, "중계 열쇠 — 이 파일만은 따로 관리하세요"))
    return out


def write_readme(dest: Path, with_key: bool) -> None:
    lines = [
        "TOURSTORY OP SYSTEM — 설치용 묶음",
        "=" * 50,
        "",
        "1. 이 폴더를 통째로 원하는 곳에 둡니다 (바탕화면 추천).",
        "2. '설치.bat' 을 더블클릭합니다.",
        "3. 'Agent 켜기.bat' 을 더블클릭하고, 그 창은 켜 둡니다.",
        "4. 화면 주소를 열고 안내대로 진행합니다.",
        "",
        "자세한 내용은 '설치안내.md' 를 보세요.",
        "",
    ]
    if with_key:
        lines += [
            "!! 이 묶음에는 중계 열쇠가 들어 있습니다.",
            "   메일·메신저로 보내지 마세요. 사내 공유 폴더나 USB 로만 건넵니다.",
            "",
        ]
    else:
        lines += [
            "* 중계 열쇠(hub/data/firebase_service_account.json)는 빠져 있습니다.",
            "  관리자에게 따로 받아 그 경로에 넣으세요.",
            "",
        ]
    (dest / "먼저 읽어보세요.txt").write_text(
        "\n".join(lines).replace("\n", "\r\n"), encoding="utf-8")


KEEP = {".git", ".gitignore"}          # 저장소로 쓸 때 지우면 안 되는 것


def make_folder(dest: Path, with_key: bool, keep_git: bool = False) -> tuple[int, int]:
    """
    공유 폴더를 다시 만든다.

    keep_git=True 면 .git 을 남기고 그 안의 파일만 갈아 끼운다.
    통째로 지우면 저장소 기록이 날아가고, Windows 에서는 읽기전용인
    .git/objects 때문에 삭제 자체가 실패한다.
    """
    if dest.exists():
        if keep_git:
            for p in sorted(dest.iterdir()):
                if p.name in KEEP:
                    continue
                shutil.rmtree(p, ignore_errors=True) if p.is_dir() else p.unlink(missing_ok=True)
        else:
            shutil.rmtree(dest)
    n = size = 0
    for rel, _why in build_manifest(with_key):
        src, dst = ROOT / rel, dest / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        n += 1
        size += src.stat().st_size
    write_readme(dest, with_key)
    return n, size


def make_zip_bytes(with_key: bool = False) -> bytes:
    """화면에서 내려받을 때 쓴다 (디스크에 쓰지 않는다)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for rel, _why in build_manifest(with_key):
            z.write(ROOT / rel, f"Last minute system/{rel}")
        note = ("TOURSTORY OP SYSTEM\r\n\r\n"
                "1. 압축을 풀고 '설치.bat' 을 더블클릭\r\n"
                "2. 'Agent 켜기.bat' 을 켜 두기\r\n"
                "3. 자세한 내용은 '설치안내.md'\r\n")
        if not with_key:
            note += ("\r\n* 중계 열쇠는 빠져 있습니다. 관리자에게 따로 받아\r\n"
                     "  hub/data/firebase_service_account.json 에 두세요.\r\n")
        z.writestr("Last minute system/먼저 읽어보세요.txt", note.encode("utf-8"))
    return buf.getvalue()


SHARE_GITIGNORE = """# 이 저장소에는 비밀이 들어가지 않는다
hub/data/firebase_service_account.json
hub/data/agent_config.json
hub/data/agent.lock
hub/data/jobs/
hub/data/shared/
hub/logs/
*.xlsx
*.xls
*.csv
__pycache__/
*.pyc
.venv/
*.bak_*
*.exe
"""


def repo_dir() -> Path:
    """
    올릴 때 쓰는 원본 저장소의 자리.

    바탕화면에 두면 'TS_OP_AGENT-main'(받은 폴더)과 이름이 비슷해서
    정리하다 같이 지워진다. 지워지면 올릴 대상이 없어지는데 아무 말도
    안 나오고, GitHub 은 옛날 것에 멈춘 채로 남는다. 그래서 치워 둔다.
    """
    import tempfile
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("HOME") or tempfile.gettempdir()
    return Path(base) / "OTABot" / "share_repo"


def git_push(dest: Path) -> tuple[bool, str]:
    """GitHub 으로 올린다. 로그인은 Windows 가 기억하고 있는 것을 쓴다."""
    import subprocess

    def git(*a, timeout=180):
        return subprocess.run(["git", "-C", str(dest), *a], capture_output=True,
                              text=True, errors="replace", timeout=timeout,
                              env=dict(os.environ, GIT_TERMINAL_PROMPT="0"))

    url = repo_url() + (".git" if not repo_url().endswith(".git") else "")
    if git("remote", "get-url", "origin").returncode != 0:
        git("remote", "add", "origin", url)
    else:
        git("remote", "set-url", "origin", url)

    r = git("push", "-u", "origin", SHARE_BRANCH, "--force")
    out = (r.stdout + r.stderr).strip()
    if r.returncode == 0:
        return True, out.splitlines()[-1] if out else "올림"
    return False, out[-400:] or "알 수 없는 실패"


def sync_git(dest: Path, with_key: bool) -> tuple[int, str]:
    """
    공유 폴더를 그대로 Git 저장소로 만든다 (없으면 새로, 있으면 갱신).

    왜 저장소로 두나
        팀원이 늘어날 때마다 폴더를 복사해 돌리면 누가 어느 버전을 갖고 있는지
        알 수 없다. 저장소면 'Download ZIP' 링크 하나로 끝나고, 무엇이 언제
        바뀌었는지도 남는다.

    ⚠️ 열쇠는 넣지 않는다. 이 저장소는 사람이 여럿 받아 가는 곳이다.
    """
    import subprocess
    if with_key:
        raise SystemExit("열쇠가 든 폴더는 저장소로 만들지 않습니다. --with-key 를 빼세요.")

    n, _size = make_folder(dest, with_key=False, keep_git=True)
    (dest / ".gitignore").write_text(SHARE_GITIGNORE, encoding="utf-8")

    def git(*a):
        return subprocess.run(["git", "-C", str(dest), *a],
                              capture_output=True, text=True, errors="replace")

    if not (dest / ".git").exists():
        git("init", "-q")
        git("branch", "-M", SHARE_BRANCH)
    git("add", "-A")
    st = git("status", "--porcelain").stdout.strip()
    if not st:
        return n, "바뀐 것 없음"
    git("-c", "user.name=ktourstory", "-c", "user.email=ktourstory@gmail.com",
        "commit", "-q", "-m", "설치용 파일 갱신")
    return n, "커밋함"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--git", action="store_true",
                    help="공유 폴더를 Git 저장소로 만들고 커밋한다")
    ap.add_argument("--push", action="store_true",
                    help="만들고 커밋한 뒤 GitHub 으로 올린다 (--git 포함)")
    ap.add_argument("--zip", action="store_true", help="폴더 대신 압축 파일로")
    ap.add_argument("--with-key", action="store_true",
                    help="중계 열쇠 포함 (USB/사내공유 전용)")
    ap.add_argument("--out", default="", help="만들 위치 (기본: 바탕화면)")
    a = ap.parse_args()

    base = Path(a.out) if a.out else Path.home() / "Desktop"
    # ⚠️ 폴더 이름은 영문으로 둔다. 한글이면 명령창마다 인코딩이 달라
    #    cd 가 조용히 실패한다 (2026-08-26: git remote add 가 안 먹었다).
    #    저장소 이름과 같게 맞춰 헷갈리지 않게 한다.
    name = "TS_OP_AGENT"

    if a.git or a.push:
        target = Path(a.out) if a.out else repo_dir()
        n, what = sync_git(target, a.with_key)
        print("=" * 62)
        print(" 설치용 파일 올리기")
        print("=" * 62)
        print(f"  파일    : {n}개")
        print(f"  상태    : {what}")
        print(f"  올릴 곳 : {repo_url()}")
        if not a.push:
            print()
            print("  (올리려면 --push 를 붙이세요)")
            return
        print()
        ok, msg = git_push(target)
        print(f"  결과    : {'성공' if ok else '실패'}")
        for line in str(msg).splitlines()[-6:]:
            print(f"            {line}")
        print("=" * 62)
        if ok:
            print("  팀원이 받는 파일이 방금 것으로 바뀌었습니다.")
        else:
            print("  올리지 못했습니다. 위 메시지를 확인하세요.")
        return

    if a.zip:
        data = make_zip_bytes(a.with_key)
        target = base / f"{name}.zip"
        target.write_bytes(data)
        n = len(build_manifest(a.with_key))
        print(f"만들었습니다: {target}")
        print(f"  파일 {n}개 / {len(data)/1024/1024:.1f} MB (압축)")
    else:
        target = base / name
        n, size = make_folder(target, a.with_key)
        print(f"만들었습니다: {target}")
        print(f"  파일 {n}개 / {size/1024/1024:.1f} MB")

    print(f"  중계 열쇠: {'포함 (취급 주의)' if a.with_key else '미포함'}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
