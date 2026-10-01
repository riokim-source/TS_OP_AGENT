# -*- coding: utf-8 -*-
"""
mobile.py
'클룩 모바일' — 휴대폰에서 Klook 오픈을 실행하는 리모컨 서버를 켜고 끈다.

봇은 반드시 이 PC 에서 돌아야 한다 (Playwright 가 이 PC Chrome 의 CDP 포트에
붙는다). 그래서 휴대폰은 'PC 의 봇을 누르는 리모컨' 이고, 그 리모컨 서버가
`Klook Open/mobile_server.py` 다. 지금까지는 start_mobile.bat 을 따로
더블클릭해야 했는데, 창을 닫으면 조용히 끊기고 사람은 폰에서 '안 열린다' 만
보게 된다. 그래서 화면에서 켜고/끄고/상태를 보게 한다.

⚠️ 이 파일은 '켜고 끄는 것' 만 한다. 오픈 규칙·수량은 klook_core 가 주인이다.
   (화면과 봇이 서로 다른 규칙을 갖게 되면 안 된다)

⚠️ 토큰 없이는 접속이 안 된다. 주소만 알면 누구나 재고를 열 수 있으므로
   링크를 단체 대화방에 붙이지 말 것 — 그 안내도 화면에 같이 띄운다.
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
from pathlib import Path

from .paths import LOG_DIR, klook_open_dir

DEFAULT_PORT = 8500
PID_FILE = LOG_DIR / "_mobile_pid.txt"


def server_file() -> Path | None:
    d = klook_open_dir()
    if d is None:
        return None
    p = d / "mobile_server.py"
    return p if p.is_file() else None


def available() -> tuple[bool, str]:
    p = server_file()
    if p is None:
        return False, "Klook Open/mobile_server.py 를 찾지 못했습니다."
    return True, str(p)


def token() -> str:
    """서버가 처음 켜질 때 만들어 두는 접속 열쇠. 없으면 빈 문자열."""
    d = klook_open_dir()
    if d is None:
        return ""
    try:
        return (d / "logs" / "_mobile_token.txt").read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def port_open(port: int = DEFAULT_PORT, timeout: float = 0.4) -> bool:
    """그 포트에서 누군가 듣고 있는가 (= 서버가 떠 있는가)."""
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=timeout):
            return True
    except Exception:
        return False


def _saved_pid() -> int | None:
    try:
        v = int(PID_FILE.read_text(encoding="utf-8").strip())
        return v if v > 0 else None
    except Exception:
        return None


def _alive(pid: int) -> bool:
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return True
        except Exception:
            return False
    try:
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"],
                             capture_output=True, text=True, errors="replace",
                             timeout=10).stdout
        return str(pid) in out
    except Exception:
        return False


def lan_ip() -> str:
    """같은 Wi-Fi 에서 이 PC 로 들어올 주소. 못 찾으면 빈 문자열."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))          # 보내지 않는다. 경로만 본다
            return s.getsockname()[0]
        finally:
            s.close()
    except Exception:
        return ""


def urls(port: int = DEFAULT_PORT) -> dict:
    """화면에 띄울 주소들. 토큰이 없으면 주소만(아직 한 번도 안 켠 상태)."""
    tok = token()
    q = f"/?k={tok}" if tok else "/"
    ip = lan_ip()
    return {
        "token": tok,
        "this_pc": f"http://localhost:{port}{q}",
        "phone": f"http://{ip}:{port}{q}" if ip else "",
        "ip": ip,
    }


def status(port: int = DEFAULT_PORT) -> dict:
    """
    지금 켜져 있나.

    ⚠️ pid 파일만 보면 안 된다. 사람이 start_mobile.bat 으로 직접 켰거나,
       PC 를 다시 켠 뒤라면 pid 는 남아 있어도 서버는 없다. 반대로 pid 를
       모르지만 서버는 떠 있을 수 있다. **포트가 듣고 있는지**가 기준이다.
    """
    pid = _saved_pid()
    up = port_open(port)
    return {
        "running": up,
        "port": int(port),
        "pid": pid if (pid and _alive(pid)) else None,
        "ours": bool(pid and _alive(pid) and up),
        **urls(port),
    }


def start(port: int = DEFAULT_PORT) -> tuple[bool, str]:
    """서버를 띄운다. 이미 떠 있으면 그대로 둔다."""
    ok, why = available()
    if not ok:
        return False, why
    if port_open(port):
        return True, f"이미 켜져 있습니다 (port {port})."

    flags = 0
    if os.name == "nt":
        # 화면(Streamlit)을 닫아도 리모컨은 살아 있어야 한다. 창은 띄우지 않는다.
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) \
            | getattr(subprocess, "DETACHED_PROCESS", 0)
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    try:
        p = subprocess.Popen(
            [sys.executable, "mobile_server.py", str(int(port))],
            cwd=str(klook_open_dir()), env=env, creationflags=flags,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL)
    except Exception as e:
        return False, f"켜지 못했습니다: {str(e)[:150]}"

    try:
        PID_FILE.parent.mkdir(parents=True, exist_ok=True)
        PID_FILE.write_text(str(p.pid), encoding="utf-8")
    except Exception:
        pass

    # 뜨는 데 1~2초 걸린다. '켰다' 고만 하고 넘어가면 사람이 폰에서 먼저 막힌다.
    import time
    for _ in range(20):
        time.sleep(0.4)
        if port_open(port):
            return True, f"켰습니다 (port {port})."
        if p.poll() is not None:
            return False, (f"바로 꺼졌습니다 (종료 코드 {p.returncode}). "
                           f"포트 {port} 가 이미 쓰이고 있는지 확인하세요.")
    return False, f"{port} 포트가 열리지 않았습니다. 잠시 뒤 다시 보세요."


def stop(port: int = DEFAULT_PORT) -> tuple[bool, str]:
    """우리가 켠 서버를 끈다. 남이 켠 것(pid 모름)은 건드리지 않는다."""
    pid = _saved_pid()
    if not pid or not _alive(pid):
        if port_open(port):
            return False, (f"이 화면에서 켠 것이 아니라 끌 수 없습니다. "
                           f"start_mobile.bat 창을 닫으세요 (port {port}).")
        return True, "이미 꺼져 있습니다."
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                           capture_output=True, timeout=20)
        else:
            os.kill(pid, 15)
    except Exception as e:
        return False, f"끄지 못했습니다: {str(e)[:150]}"
    try:
        PID_FILE.unlink(missing_ok=True)
    except Exception:
        pass
    return True, "껐습니다."
