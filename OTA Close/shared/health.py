"""
Chrome 헬스체크 - 지역 통합 구조 (v2).

구조:
- 한 지역 = 한 Chrome 인스턴스 = 여러 에이전시 사이트가 그 안에 탭으로 존재
- 5개 지역 Chrome: KOREA / JAPAN / AUSTRALIA / UK / GLOBAL
- 각 에이전시 봇은 자기가 동작하는 지역들의 포트만 attach

각 포트에 살아있는 Chrome 이 있는지 확인. 없으면 해당 .bat 자동 실행.
"""

from __future__ import annotations

import os
import socket
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Dict, List, Tuple

LAUNCHERS_DIR = Path(__file__).resolve().parent.parent / "chrome_launchers"

# 부팅 락 파일 위치 / 유효시간
#   워커 18개가 동시에 ensure_chrome() 을 부르면 각자 .bat 을 실행해서
#   크롬이 여러 개 뜨고(프로필 락 충돌) 이미 attach 한 워커가
#   TargetClosedError / "Connection closed while reading from the driver" 로
#   즉사하던 문제 → 포트별 파일 락으로 '한 프로세스만 부팅' 하도록 직렬화.
_LOCK_DIR = Path(tempfile.gettempdir())
_LOCK_STALE_SEC = 90.0


def is_port_alive(port: int, host: str = "127.0.0.1", timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def is_cdp_ready(port: int, timeout: float = 1.5) -> bool:
    """
    포트 소켓만 열린 '반쯤 뜬 크롬' 방어.

    크롬은 디버깅 포트를 먼저 열고 내부 준비를 나중에 끝낸다. 그 사이에
    connect_over_cdp 로 붙으면 contexts 가 비어 있거나 곧 연결이 끊겨
    작업 중 봇이 죽는다. /json/version 이 응답할 때만 '준비됨' 으로 본다.
    """
    if not is_port_alive(port, timeout=timeout):
        return False
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/json/version", timeout=timeout
        ) as resp:
            return resp.status == 200
    except Exception:
        return False


# ============================================================
# P0-1: 포트 소유권 검증
# ============================================================
# 여태 is_port_alive(9222)==True 면 '우리 Chrome' 이라고 믿었다. 그런데
# Klook Open 의 런처는 같은 9222 에 다른 프로필(%LOCALAPPDATA%\KlookBot\chrome_korea)
# 을 물린다. 먼저 뜬 쪽이 포트를 잡으면 나중 쪽은 디버그 포트를 못 열지만
# 포트 자체는 살아 있으므로 봇이 '정상' 으로 오판하고, 로그인 안 된 Chrome 에
# attach 해서 그 지역 전체가 실패했다.
#
# Chrome 은 뜰 때 <user-data-dir>/DevToolsActivePort 첫 줄에 실제 포트를 쓴다.
# 그 값을 대조하면 "이 포트의 Chrome 이 정말 이 프로필인지" 를 확실히 알 수 있다.
_LOCALAPPDATA = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")

REGION_PROFILE_DIR: Dict[str, Path] = {
    "KOREA":     Path(_LOCALAPPDATA) / "OTABot" / "chrome_korea",
    "JAPAN":     Path(_LOCALAPPDATA) / "OTABot" / "chrome_japan",
    "AUSTRALIA": Path(_LOCALAPPDATA) / "OTABot" / "chrome_australia",
    "UK":        Path(_LOCALAPPDATA) / "OTABot" / "chrome_uk",
    "GLOBAL":    Path(_LOCALAPPDATA) / "OTABot" / "chrome_global",
}


def devtools_active_port(profile_dir: Path) -> int | None:
    try:
        first = Path(profile_dir).joinpath("DevToolsActivePort").read_text(
            encoding="utf-8", errors="replace").splitlines()[0].strip()
        return int(first)
    except Exception:
        return None


def _port_owner_dir(port: int) -> str | None:
    """포트를 LISTEN 중인 Chrome 의 --user-data-dir. hub 가 있으면 그 구현을 쓴다."""
    try:
        from shared.hub_bridge import routing as _hub_routing
        if _hub_routing() is not None:
            from core.routing import port_owner_user_data_dir  # type: ignore
            return port_owner_user_data_dir(port)
    except Exception:
        pass
    return None


def profile_owns_port(region: str, port: int) -> bool:
    """
    이 포트에 떠 있는 Chrome 이 정말 이 지역의 프로필인가.

    판정 못 하면 True(=예전 동작)로 폴백한다. 여기서 False 를 주면
    멀쩡한 Chrome 도 못 쓰게 되어 오히려 운영이 멈춘다.
    """
    pdir = REGION_PROFILE_DIR.get(str(region).upper())
    if pdir is None:
        return is_cdp_ready(port)          # 모르는 지역은 예전 동작 유지
    if not is_cdp_ready(port):
        return False

    owner = _port_owner_dir(port)
    if owner:
        try:
            return Path(owner).resolve() == Path(pdir).resolve()
        except Exception:
            return str(owner).rstrip("\\/").casefold() == str(pdir).rstrip("\\/").casefold()

    active = devtools_active_port(pdir)
    if active is not None:
        return active == int(port)
    return True


def check_port_conflict(region: str, port: int) -> str | None:
    """
    포트는 살아있는데 내 프로필이 아니면 사유 문자열을 돌려준다.
    (봇이 조용히 엉뚱한 Chrome 에 붙지 않게 하려는 용도)
    """
    if not is_cdp_ready(port):
        return None
    if profile_owns_port(region, port):
        return None
    pdir = REGION_PROFILE_DIR.get(str(region).upper())
    return (f"port {port} 를 다른 프로필의 Chrome 이 점유하고 있습니다 "
            f"(기대 프로필: {pdir}). 그 Chrome 을 끄고 다시 실행하세요.")


def _acquire_boot_lock(port: int) -> bool:
    """포트별 부팅 락 획득 (성공=내가 부팅 담당). 오래된 락은 stale 로 보고 회수."""
    lock_path = _LOCK_DIR / f"otabot_chrome_boot_{port}.lock"
    try:
        if lock_path.exists():
            age = time.time() - lock_path.stat().st_mtime
            if age > _LOCK_STALE_SEC:
                try:
                    lock_path.unlink()
                except OSError:
                    pass
    except OSError:
        pass
    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False
    except OSError:
        return False
    try:
        os.write(fd, str(os.getpid()).encode())
    except OSError:
        pass
    finally:
        os.close(fd)
    return True


def _release_boot_lock(port: int) -> None:
    try:
        (_LOCK_DIR / f"otabot_chrome_boot_{port}.lock").unlink()
    except OSError:
        pass


def _region_of_bat(bat_name: str) -> str | None:
    for region, (_port, bat) in REGION_CHROME_MAP.items():
        if bat == bat_name:
            return region
    return None


def _wait_cdp(port: int, wait_sec: float, region: str | None = None) -> bool:
    def ready() -> bool:
        if not is_cdp_ready(port):
            return False
        return profile_owns_port(region, port) if region else True

    deadline = time.time() + wait_sec
    while time.time() < deadline:
        if ready():
            return True
        time.sleep(0.5)
    return ready()


def ensure_chrome(port: int, bat_name: str, wait_sec: int = 8) -> bool:
    """
    포트가 죽어있으면 .bat 실행해서 띄움. 띄워졌는지 확인 후 반환.

    중복 부팅 방지: 포트별 파일 락을 먼저 잡은 프로세스만 .bat 을 실행하고,
    락을 못 잡은 프로세스는 부팅이 끝나기를 기다리기만 한다(크롬 중복 실행 X).
    준비 판정은 소켓이 아니라 CDP(/json/version) 응답 기준.
    """
    region = _region_of_bat(bat_name)

    if is_cdp_ready(port):
        # P0-1: 포트가 살아있어도 '내 프로필' 인지 확인한다.
        #   남의 Chrome 이 점유 중이면 True 를 돌려주면 안 된다.
        #   여기서 True 를 주면 봇이 로그인 안 된 Chrome 에 붙어서 전부 실패한다.
        if region and not profile_owns_port(region, port):
            print(f"[health] {check_port_conflict(region, port)}", flush=True)
            return False
        return True

    bat_path = LAUNCHERS_DIR / bat_name
    if not bat_path.exists():
        return False

    if not _acquire_boot_lock(port):
        # 다른 프로세스가 부팅 중 → 실행하지 말고 준비될 때까지 대기만.
        # 부팅 주체보다 넉넉히 기다린다(자기 몫 wait_sec + 여유).
        return _wait_cdp(port, wait_sec + 10, region)

    try:
        # 락 잡은 뒤 재확인 (내가 기다리는 동안 이미 떴을 수 있음)
        if is_cdp_ready(port) and (not region or profile_owns_port(region, port)):
            return True
        try:
            subprocess.Popen(
                ["cmd.exe", "/c", "start", "", str(bat_path)],
                cwd=str(LAUNCHERS_DIR),
                shell=False,
                creationflags=0x00000008,  # DETACHED_PROCESS
            )
        except Exception:
            return False
        return _wait_cdp(port, wait_sec, region)
    finally:
        _release_boot_lock(port)


# ============================================================
# 지역 → Chrome 인스턴스 매핑 (단일 source of truth)
# ============================================================
# ⚠️ 라스트미닛 전용 대역(95xx). 9222~9230 은 다른 도구들이 이미 쓴다.
#    실제 값은 hub/data/routing.json 이 단일 기준이며, 아래는 hub 가 없을 때 폴백.
REGION_CHROME_MAP: Dict[str, Tuple[int, str]] = {
    "KOREA":     (9522, "start_chrome_korea.bat"),
    "JAPAN":     (9523, "start_chrome_japan.bat"),
    "AUSTRALIA": (9524, "start_chrome_australia.bat"),
    "UK":        (9525, "start_chrome_uk.bat"),
    "GLOBAL":    (9530, "start_chrome_global.bat"),
}


# ============================================================
# 에이전시 → 사용 지역 매핑
# ============================================================
AGENCY_REGIONS: Dict[str, List[str]] = {
    "klook": ["KOREA", "JAPAN", "AUSTRALIA", "UK"],
    "gg":    ["KOREA", "JAPAN", "AUSTRALIA", "UK"],
    "kkday": ["KOREA", "JAPAN", "AUSTRALIA", "UK"],
    "vi":    ["GLOBAL"],
    "mrt":   ["GLOBAL"],
}


def get_agency_chrome_targets(agency: str) -> List[Tuple[str, int, str]]:
    """
    에이전시가 사용해야 할 (region_name, port, bat_name) 리스트 반환.
    예: get_agency_chrome_targets("klook")
        → [("KOREA", 9522, "start_chrome_korea.bat"),
           ("JAPAN", 9223, "start_chrome_japan.bat"), ...]
    """
    out: List[Tuple[str, int, str]] = []
    regions = AGENCY_REGIONS.get(agency.lower(), [])
    for r in regions:
        if r in REGION_CHROME_MAP:
            port, bat = REGION_CHROME_MAP[r]
            out.append((r, port, bat))
    return out


# ============================================================
# 하위 호환: 봇 → (포트, .bat) 리스트 (예전 코드가 부르는 형식)
# ============================================================
def _build_legacy_chrome_map() -> Dict[str, List[Tuple[int, str]]]:
    out: Dict[str, List[Tuple[int, str]]] = {}
    for agency in AGENCY_REGIONS:
        out[agency] = [(p, b) for _r, p, b in get_agency_chrome_targets(agency)]
    return out


CHROME_MAP: Dict[str, List[Tuple[int, str]]] = _build_legacy_chrome_map()


# ============================================================
# 헬스체크
# ============================================================
def healthcheck_regions() -> Dict[str, Tuple[int, bool]]:
    """지역별 Chrome 상태 (region -> (port, alive))."""
    return {r: (p, is_port_alive(p)) for r, (p, _bat) in REGION_CHROME_MAP.items()}


def healthcheck_all() -> Dict[str, List[Tuple[int, bool]]]:
    """봇별 모든 Chrome 포트의 상태 반환 (예전 형식 유지)."""
    out: Dict[str, List[Tuple[int, bool]]] = {}
    for bot, items in CHROME_MAP.items():
        out[bot] = [(port, is_port_alive(port)) for port, _ in items]
    return out


def boot_all_missing() -> Dict[str, Tuple[int, bool]]:
    """죽어있는 지역 Chrome 모두 띄우기 시도. 지역별 상태 반환."""
    for region, (port, bat) in REGION_CHROME_MAP.items():
        ensure_chrome(port, bat)
    return healthcheck_regions()
