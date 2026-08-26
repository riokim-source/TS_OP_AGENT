"""
KLOOK 마감 봇.

핵심 아이디어:
- 사용자가 이미 만들어둔 klook_worker.py 의 process_task() 가 inventory=0 일 때
  자동으로 Activate OFF 까지 처리하도록 되어 있음 (line ~4341).
- packages.py 에 4개 시장(KOREA/JAPAN/AUSTRALIA/UK) × 2워크플로우(package/activity)
  × 2 accept_until 의 모든 상품 매핑이 들어있음.
- 따라서 "각 시장 Chrome 에 attach → packages.py 의 그 시장 상품 전부 →
  task=(name, inventory=0, ...) 만들어서 process_task 호출" 하면 close 봇 완성.

전제:
- 같은 폴더(OTA Close/)에 klook_worker.py / packages.py 가 있어야 함.
- 4개 시장 Chrome 이 9522~9525 포트로 띄워져 있고 각각 로그인 끝난 상태여야 함.
- (없는 시장은 자동으로 skip → 결과 표에 명시)

사용:
    from klook import run_close
    result = run_close(target_date)  # None 이면 내일 (worker 가 자동으로 내일 잡음)

환경 변수:
    KLOOK_REGIONS  쉼표 구분 시장 필터 (예: "KOREA,JAPAN") - 미설정 시 4개 전부
"""

from __future__ import annotations

import sys as _sys_init
try:
    _sys_init.stdout.reconfigure(encoding="utf-8", errors="replace")
    _sys_init.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import os
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared.health import ensure_chrome, is_port_alive
from shared.logger import get_agency_logger
from shared.types import Result

LOG = get_agency_logger("KLOOK")
LOGS_DIR = ROOT / "logs"
LOGS_DIR.mkdir(exist_ok=True)


# ============================================================
# 시장 → (포트, bat, packages.py 의 all_<region>_names 함수명)
# ============================================================
# Klook 은 지역 Chrome(9522~9525) 을 KKday/GG/Trip.com 과 함께 쓴다.
# 실제 포트는 hub 라우팅이 결정하며(_close_one_region 참고), 아래 값은 hub 가 없을 때의 폴백이다.
REGIONS: List[Tuple[str, int, str, str]] = [
    ("KOREA",     9522, "start_chrome_korea.bat",     "all_korea_names"),
    ("JAPAN",     9523, "start_chrome_japan.bat",     "all_japan_names"),
    ("AUSTRALIA", 9524, "start_chrome_australia.bat", "all_australia_names"),
    ("UK",        9525, "start_chrome_uk.bat",        "all_uk_names"),
]


# ============================================================
# 결과 상태 enum (kkday.py v3 와 같은 패턴)
# ============================================================
class ProductStatus(str, Enum):
    SUCCESS = "SUCCESS"                # worker 가 "성공" 반환
    DRY_RUN = "DRY_RUN"                # dry-run, 실제 처리 안 함
    NOT_FOUND = "NOT_FOUND"            # worker 가 "찾을 수 없음" 반환
    NO_SCHEDULE = "NO_SCHEDULE"        # 내일이 운영일이 아님 (캘린더에 빈 셀)
    NEW_WORKFLOW_FAIL = "NEW_WF_FAIL"  # activity 워크플로우 실패
    GENERIC_FAIL = "GENERIC_FAIL"      # worker 가 "실패" 반환
    REGION_SKIPPED = "REGION_SKIPPED"  # Chrome 포트 죽었거나 region 필터로 skip
    EXCEPTION = "EXCEPTION"            # 예외

    @property
    def is_success(self) -> bool:
        return self in (ProductStatus.SUCCESS, ProductStatus.DRY_RUN)

    @property
    def is_skip(self) -> bool:
        # 운영 안 하는 날도 정상적인 skip 으로 처리 (실패 아님)
        return self in (ProductStatus.REGION_SKIPPED, ProductStatus.NO_SCHEDULE)

    @property
    def is_failure(self) -> bool:
        return not (self.is_success or self.is_skip)


# 워커 memo 텍스트가 "내일 운영일 아님" 패턴이면 NO_SCHEDULE 로 재분류
_NO_SCHEDULE_PATTERNS = [
    "익일 날짜 카드",
    "익일 날짜",
    "날짜 카드",
    "Edit schedule 팝업을 확인하지",
    "Price & inventory 영역에서 익일",
]


def _classify_close_failure(memo):
    """워커가 '실패' 로 분류한 메모를 close 봇 관점에서 재분류.
    매칭 안 되면 None (= 그대로 GENERIC_FAIL 유지)."""
    if not memo:
        return None
    text = str(memo)
    for pat in _NO_SCHEDULE_PATTERNS:
        if pat in text:
            return ProductStatus.NO_SCHEDULE
    return None


@dataclass
class ProductResult:
    region: str
    name: str
    package_id: str
    workflow: str
    status: ProductStatus
    detail: str = ""
    elapsed_sec: float = 0.0


# ============================================================
# Klook open 봇 폴더 자동 탐색 + import
# ============================================================
# 폴더 이름 후보들 (대소문자/공백/하이픈 변형 다 시도)
_OPEN_DIR_NAMES = [
    "Klook Open",
    "Klook open",
    "klook open",
    "KLOOK Open",
    "KLOOK_OPEN",
    "klook_open",
    "klook_open_bot",
    "KlookOpen",
    "klook-open",
]

# 검색할 부모 경로들 (Windows 일반 위치 + 홈/현재 폴더 형제)
def _candidate_open_dirs():
    """KLOOK open 봇이 있을 만한 폴더 경로들 yield."""
    import os
    home = Path.home()
    # ⚠️ 순서가 중요하다. ROOT.parent(= 나와 같은 폴더에 있는 Klook Open)를 맨 앞에 둔다.
    #   예전에는 ~/Desktop 을 먼저 뒤져서, 이 봇을 "Last minute system" 안으로 옮긴 뒤에도
    #   바깥 데스크톱의 옛 Klook Open 에서 packages.py / klook_worker.py 를 import 했다.
    #   두 폴더 내용이 어긋나는 순간 조용히 옛 상품 매핑으로 마감하게 된다.
    parents = [
        ROOT.parent,  # OTA Close 의 부모 (같은 레벨에 있는 경우) - 최우선
        home / "OneDrive" / "Desktop",
        home / "Desktop",
        home / "OneDrive" / "Documents",
        home / "Documents",
        home / "OneDrive",
        home,
    ]
    # USERPROFILE 같은 환경변수 경로 추가 (Windows)
    user_profile = os.environ.get("USERPROFILE")
    if user_profile:
        up = Path(user_profile)
        for p in [up / "OneDrive" / "Desktop", up / "Desktop", up]:
            if p not in parents:
                parents.append(p)

    for parent in parents:
        if not parent.exists() or not parent.is_dir():
            continue
        for name in _OPEN_DIR_NAMES:
            candidate = parent / name
            if candidate.is_dir() and (candidate / "packages.py").exists():
                yield candidate


def _find_klook_open_dir():
    """KLOOK open 봇 폴더 찾기.
    우선순위:
      1) 환경변수 KLOOK_OPEN_DIR (전체 경로)
      2) 폴더 이름 자동 탐색 (Klook Open 등)
      3) None (OTA Close 폴더 자체에서 import 시도)
    """
    import os
    env_dir = os.environ.get("KLOOK_OPEN_DIR", "").strip()
    if env_dir:
        p = Path(env_dir)
        if p.is_dir() and (p / "packages.py").exists():
            return p
        else:
            LOG.warning("KLOOK_OPEN_DIR=%s 가 유효하지 않음 (폴더 없거나 packages.py 없음)", env_dir)

    for candidate in _candidate_open_dirs():
        return candidate
    return None


# ============================================================
# packages.py / klook_worker.py 안전 import
# ============================================================
def _import_klook_modules():
    """klook_worker / packages 모듈을 import. 실패 시 None 반환.
    Klook open 봇 폴더를 자동 탐색하여 sys.path 에 추가."""
    import sys as _sys

    open_dir = _find_klook_open_dir()
    if open_dir:
        if str(open_dir) not in _sys.path:
            _sys.path.insert(0, str(open_dir))
        LOG.info("Klook open 봇 폴더 사용: %s", open_dir)
    else:
        LOG.info("Klook open 봇 폴더 자동 탐색 실패 - OTA Close 폴더 fallback 사용")

    try:
        import packages as _packages_mod
        import klook_worker as _worker_mod
        # 어느 파일을 import 했는지 확인
        pkg_file = getattr(_packages_mod, "__file__", "unknown")
        worker_file = getattr(_worker_mod, "__file__", "unknown")
        LOG.info("packages.py = %s", pkg_file)
        LOG.info("klook_worker.py = %s", worker_file)
        return _worker_mod, _packages_mod
    except Exception as e:
        LOG.error("klook_worker.py 또는 packages.py import 실패: %s", e)
        LOG.error("환경변수 KLOOK_OPEN_DIR 로 경로 지정하거나 "
                  "Klook open 봇 폴더 이름을 '%s' 중 하나로 맞춰주세요.",
                  ", ".join(_OPEN_DIR_NAMES[:3]))
        return None, None


# ============================================================
# 한 시장 처리
# ============================================================
def _close_one_region(
    worker_mod,
    packages_mod,
    region: str,
    port: int,
    launcher_bat: str,
    name_fn: str,
    dry_run: bool,
) -> List[ProductResult]:
    """한 시장의 모든 상품을 close. 반환: ProductResult 리스트."""
    results: List[ProductResult] = []

    # 1) 시장 Chrome 살아있는지 확인
    #    hub 라우팅이 있으면 그쪽이 단일 기준 (Klook 은 지역 Chrome / 9522~9525).
    #    없으면 예전 방식으로 폴백한다.
    try:
        from shared.hub_bridge import resolve as _hub_resolve
    except Exception:
        _hub_resolve = None

    resolved = _hub_resolve(region, "KLOOK") if _hub_resolve else None
    if resolved is not None:
        if not resolved["ok"]:
            LOG.warning("[%s] %s", region, resolved["message"])
            results.append(ProductResult(
                region=region, name="*", package_id="*", workflow="*",
                status=ProductStatus.REGION_SKIPPED,
                detail=resolved["message"],
            ))
            return results
        port = int(resolved["port"])
        LOG.info("[%s] hub 라우팅 사용: profile=%s port=%d", region, resolved["profile"], port)
    elif not is_port_alive(port):
        LOG.warning("[%s] Chrome port %d 죽음 - 자동 부팅 시도", region, port)
        if not ensure_chrome(port, launcher_bat, wait_sec=10):
            LOG.warning("[%s] Chrome 부팅 실패 - 이 시장은 skip", region)
            results.append(ProductResult(
                region=region, name="*", package_id="*", workflow="*",
                status=ProductStatus.REGION_SKIPPED,
                detail=f"Chrome port {port} 죽어있고 {launcher_bat} 도 안 됨",
            ))
            return results

    # 2) 이 시장의 모든 상품 이름 가져오기
    get_names = getattr(packages_mod, name_fn, None)
    if not get_names:
        results.append(ProductResult(
            region=region, name="*", package_id="*", workflow="*",
            status=ProductStatus.REGION_SKIPPED,
            detail=f"packages.{name_fn} 함수 없음",
        ))
        return results

    names = list(get_names())
    if not names:
        LOG.info("[%s] 등록된 상품 0개 - skip", region)
        return results

    # forward/backward 분할 (KLOOK_DIRECTION 환경변수)
    direction = os.environ.get("KLOOK_DIRECTION", "").strip().lower()
    if direction == "forward":
        half = len(names) // 2
        names = names[:half] if half > 0 else names
        LOG.info("[%s/forward] 앞 %d 개 처리", region, len(names))
    elif direction == "backward":
        half = len(names) // 2
        names = list(reversed(names[half:])) if half > 0 else []
        LOG.info("[%s/backward] 뒤 %d 개 역순 처리", region, len(names))

    LOG.info("[%s] 총 %d 개 상품 마감 시작", region, len(names))

    # 3) DRY-RUN 이면 process_task 호출 없이 카운트만
    if dry_run:
        for nm in names:
            info = packages_mod.get_package(nm) or {}
            results.append(ProductResult(
                region=region,
                name=nm,
                package_id=str(info.get("id", "?")),
                workflow=str(info.get("workflow", "package")),
                status=ProductStatus.DRY_RUN,
                detail="DRY: inventory=0, activate=OFF 예정",
            ))
        return results

    # 4) Chrome 에 attach
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        results.append(ProductResult(
            region=region, name="*", package_id="*", workflow="*",
            status=ProductStatus.REGION_SKIPPED,
            detail=f"playwright import 실패: {e}",
        ))
        return results

    cdp_url = f"http://127.0.0.1:{port}"
    try:
        p = sync_playwright().start()
    except Exception as e:
        results.append(ProductResult(
            region=region, name="*", package_id="*", workflow="*",
            status=ProductStatus.EXCEPTION,
            detail=f"sync_playwright().start() 실패: {e}",
        ))
        return results

    try:
        browser = p.chromium.connect_over_cdp(cdp_url)
    except Exception as e:
        results.append(ProductResult(
            region=region, name="*", package_id="*", workflow="*",
            status=ProductStatus.REGION_SKIPPED,
            detail=f"CDP attach 실패 ({cdp_url}): {e}",
        ))
        try:
            p.stop()
        except Exception:
            pass
        return results

    if not browser.contexts:
        results.append(ProductResult(
            region=region, name="*", package_id="*", workflow="*",
            status=ProductStatus.REGION_SKIPPED,
            detail="Chrome context 없음 (창이 닫혀있거나 로그인 안 됨)",
        ))
        try:
            p.stop()
        except Exception:
            pass
        return results

    context = browser.contexts[0]

    # 한 Chrome 에 KLOOK + GG + KKDAY 탭이 같이 있을 수 있어 KLOOK 탭 찾거나 새로 만든다.
    # forward/backward 분할 모드면 두 worker 가 같은 Chrome 안에서 동시 작업하므로
    # 자기 전용 새 탭 강제 생성 (탭 공유하면 충돌).
    _direction = os.environ.get("KLOOK_DIRECTION", "").strip().lower()

    # 기존 KLOOK 탭 찾기 (origin URL 추출용)
    _existing_klook_url = None
    for pg in context.pages:
        try:
            url = pg.url or ""
            if "klook.com" in url:
                _existing_klook_url = url
                break
        except Exception:
            pass

    if _direction in ("forward", "backward"):
        # parallel worker: 자기 전용 새 탭 강제 생성 (탭 공유하면 충돌)
        klook_page = context.new_page()
        LOG.info("[%s/%s] 전용 새 탭 생성 (parallel worker)", region, _direction)
        # about:blank 에서 시작하면 worker 의 origin 추출이 실패하므로
        # 기존 KLOOK 탭의 URL 로 먼저 이동시켜 origin 을 박아둔다
        if _existing_klook_url:
            try:
                LOG.info("[%s/%s] 새 탭을 KLOOK 으로 navigate: %s",
                         region, _direction, _existing_klook_url[:80])
                klook_page.goto(_existing_klook_url, wait_until="domcontentloaded", timeout=20000)
                klook_page.wait_for_timeout(1500)
            except Exception as e:
                LOG.warning("[%s/%s] 새 탭 초기 navigate 실패: %s", region, _direction, e)
        else:
            LOG.warning("[%s/%s] 기존 KLOOK 탭을 못 찾음 - worker 가 origin 추출 못 할 수 있음",
                        region, _direction)
        if _direction == "backward":
            import time as _t
            _t.sleep(2.0)  # forward 가 먼저 진행하도록 stagger
    else:
        # 단일 worker: 기존 KLOOK 탭 재사용
        klook_page = None
        for pg in context.pages:
            try:
                url = pg.url or ""
                if "klook.com" in url:
                    klook_page = pg
                    break
            except Exception:
                pass
        if klook_page is None:
            klook_page = context.new_page()
            if _existing_klook_url:
                try:
                    klook_page.goto(_existing_klook_url, wait_until="domcontentloaded", timeout=20000)
                except Exception:
                    pass
    page = klook_page
    try:
        page.bring_to_front()
    except Exception:
        pass

    # viewport 강제 (worker 와 일관)
    try:
        page.set_viewport_size({"width": 2560, "height": 1440})
    except Exception:
        pass

    # 5) 각 상품에 task=inventory=0 으로 process_task 호출
    for i, nm in enumerate(names, 1):
        t0 = time.perf_counter()
        info = packages_mod.get_package(nm)
        if not info:
            results.append(ProductResult(
                region=region, name=nm, package_id="?", workflow="?",
                status=ProductStatus.NOT_FOUND,
                detail="packages.get_package 결과 None",
                elapsed_sec=time.perf_counter() - t0,
            ))
            continue

        task = {
            "name": info["canonical_name"],
            "package_id": info["id"],
            "search_key": info["id"],
            "inventory": 0,  # ← 핵심: 0 이면 worker 가 자동으로 Activate OFF
            "workflow": info["workflow"],
            "accept_until": info.get("accept_until", {"when": "same_day", "time": "06:00"}),
            "input_text": f"{nm} 0",
        }

        try:
            # 시작 전 검색 화면으로 보내기 (worker main() 이 하던 동작 재현)
            try:
                if task["workflow"] == "activity":
                    worker_mod.go_activity_search_hard(page, reason="다음 상품 시작 전 정리")
                else:
                    worker_mod.go_package_search_hard(page, reason="다음 상품 시작 전 정리")
            except Exception:
                pass

            r = worker_mod.process_task(page, task)
            result_label = str(r.get("result", "")).strip()
            memo = str(r.get("memo", ""))[:200]
            elapsed = time.perf_counter() - t0

            if result_label == "성공":
                status = ProductStatus.SUCCESS
            elif result_label == "찾을 수 없음":
                status = ProductStatus.NOT_FOUND
            elif result_label == "새버전 실패":
                # activity 워크플로우도 '날짜 카드 없음' 패턴이면 NO_SCHEDULE 로 재분류
                reclass = _classify_close_failure(memo)
                status = reclass if reclass else ProductStatus.NEW_WORKFLOW_FAIL
            else:
                # 일반 '실패': 내일이 운영일 아닌 경우(NO_SCHEDULE)와 진짜 실패 구분
                reclass = _classify_close_failure(memo)
                status = reclass if reclass else ProductStatus.GENERIC_FAIL

            results.append(ProductResult(
                region=region,
                name=task["name"],
                package_id=task["package_id"],
                workflow=task["workflow"],
                status=status,
                detail=memo,
                elapsed_sec=elapsed,
            ))
            LOG.info("[%s] (%d/%d) %s | %s | %.1fs",
                     region, i, len(names), task["name"][:40],
                     status.value, elapsed)
        except Exception as e:
            elapsed = time.perf_counter() - t0
            results.append(ProductResult(
                region=region,
                name=task["name"],
                package_id=task["package_id"],
                workflow=task["workflow"],
                status=ProductStatus.EXCEPTION,
                detail=str(e)[:200],
                elapsed_sec=elapsed,
            ))
            LOG.warning("[%s] (%d/%d) %s | EXCEPTION: %s",
                        region, i, len(names), task["name"][:40], str(e)[:80])

    # P0-2: 분할 worker 가 만든 전용 탭만 닫는다 (공용 KLOOK 탭은 그대로 둔다)
    if _direction in ("forward", "backward"):
        try:
            from shared.chrome_setup import close_worker_page
            close_worker_page(page, f"KLOOK/{region}/{_direction}")
        except Exception as e:
            LOG.warning("[%s/%s] 탭 정리 실패: %s", region, _direction, e)

    try:
        p.stop()
    except Exception:
        pass

    return results


# ============================================================
# 요약 표
# ============================================================
def _build_summary(results: List[ProductResult]) -> Dict[str, Dict]:
    """region -> {total, success, failed, skipped, no_schedule, products: [...]}"""
    summary: Dict[str, Dict] = {}
    for r in results:
        entry = summary.setdefault(r.region, {
            "total": 0, "success": 0, "failed": 0, "skipped": 0,
            "no_schedule": 0, "products": [],
        })
        # REGION_SKIPPED 의 가짜 "*" 행은 total 에 포함 안 함
        if r.name != "*":
            entry["total"] += 1
        if r.status.is_success:
            entry["success"] += 1
        elif r.status == ProductStatus.NO_SCHEDULE:
            # 운영 안 하는 날 - 스킵에 포함되지만 별도 카운트
            entry["skipped"] += 1
            entry["no_schedule"] += 1
        elif r.status.is_skip:
            entry["skipped"] += 1
        else:
            entry["failed"] += 1
        entry["products"].append(r)
    return summary


def _render_summary_table(summary: Dict[str, Dict], target_date_str: str) -> str:
    lines: List[str] = []
    lines.append("=" * 78)
    lines.append(f" KLOOK 마감 결과 ({target_date_str})")
    lines.append("=" * 78)
    lines.append(f"{'시장':<10} {'전체':<5} {'성공':<5} {'실패':<5} {'운영X':<6} {'스킵':<5}  비고")
    lines.append("-" * 78)

    overall = {"total": 0, "success": 0, "failed": 0, "skipped": 0, "no_schedule": 0}
    region_order = [r[0] for r in REGIONS]
    for region in region_order:
        entry = summary.get(region)
        if not entry:
            lines.append(f"{region:<10} {'-':<5} {'-':<5} {'-':<5} {'-':<6} {'-':<5}  (필터로 제외 또는 매핑 없음)")
            continue
        total = entry["total"]
        succ = entry["success"]
        fail = entry["failed"]
        skip = entry["skipped"]
        no_sched = entry.get("no_schedule", 0)
        # 시장 자체 스킵(REGION_SKIPPED) 만 separately 표시
        real_skip = skip - no_sched
        overall["total"] += total
        overall["success"] += succ
        overall["failed"] += fail
        overall["skipped"] += real_skip
        overall["no_schedule"] += no_sched

        if any(p.name == "*" and p.status == ProductStatus.REGION_SKIPPED for p in entry["products"]):
            note = "Chrome 죽음 또는 attach 실패"
        elif fail > 0:
            note = f"{fail}건 실패 (아래 상세)"
        elif succ > 0:
            note = f"성공 {succ}/{total}" + (f", 운영 안 함 {no_sched}건" if no_sched else "")
        elif no_sched > 0 and no_sched == total:
            note = "내일 운영하는 상품 없음"
        else:
            note = "상품 없음"

        lines.append(f"{region:<10} {total:<5} {succ:<5} {fail:<5} {no_sched:<6} {real_skip:<5}  {note}")

    lines.append("-" * 78)
    lines.append(f"합계: {overall['total']} / 성공 {overall['success']} / "
                 f"실패 {overall['failed']} / 운영X {overall['no_schedule']} / 시장스킵 {overall['skipped']}")
    lines.append("=" * 78)

    # 운영 안 하는 날 상품 (정보용)
    no_sched_products = [p for entry in summary.values() for p in entry["products"]
                          if p.status == ProductStatus.NO_SCHEDULE]
    if no_sched_products:
        lines.append("")
        lines.append(f"[내일 운영 안 함 - {len(no_sched_products)}개]")
        for p in no_sched_products:
            lines.append(f"  - [{p.region}] {p.name} (id={p.package_id})")

    # 실패 상세
    failures = [p for entry in summary.values() for p in entry["products"]
                if p.status.is_failure and p.name != "*"]
    if failures:
        lines.append("")
        lines.append("[실패 상세]")
        for p in failures:
            lines.append(f"  - [{p.region}] {p.name} (id={p.package_id}, "
                         f"workflow={p.workflow}) → {p.status.value}")
            lines.append(f"      {p.detail}")

    # Region skipped 상세
    skipped_regions = [p for entry in summary.values() for p in entry["products"]
                       if p.status == ProductStatus.REGION_SKIPPED]
    if skipped_regions:
        lines.append("")
        lines.append("[시장 스킵 상세]")
        for p in skipped_regions:
            lines.append(f"  - [{p.region}] {p.detail}")

    return "\n".join(lines)


# ============================================================
# 메인 진입
# ============================================================
def run_close(target_date: Optional[date] = None, dry_run: bool = False) -> Result:
    """4개 시장 모든 상품을 inventory=0 + Activate=OFF 처리."""
    from datetime import timedelta as _td, datetime as _dt
    if target_date:
        target_str = target_date.strftime("%Y-%m-%d")
    else:
        target_date = datetime.now().date() + _td(days=1)
        target_str = target_date.strftime("%Y-%m-%d")

    LOG.info("KLOOK 마감 시작 | target=%s | dry_run=%s", target_str, dry_run)

    # 모듈 import
    worker_mod, packages_mod = _import_klook_modules()
    if worker_mod is None or packages_mod is None:
        return Result(
            agency="KLOOK", success=0, failed=0, skipped=0,
            errors=["klook_worker.py / packages.py import 실패 - 파일이 OTA Close 폴더에 있는지 확인"],
        )

    # 핵심: klook_worker 의 tomorrow_* 함수들을 target_date 반환하도록 monkey-patch
    # (worker 내부에서 tomorrow_iso() / tomorrow_day_number() 가 여러 곳에서 호출됨)
    try:
        _tgt = target_date  # closure 캡쳐용
        def _patched_tomorrow_date_obj():
            return _dt(_tgt.year, _tgt.month, _tgt.day)
        def _patched_tomorrow_iso():
            return _tgt.strftime("%Y-%m-%d")
        def _patched_tomorrow_day_number():
            return str(_tgt.day)
        worker_mod.tomorrow_date_obj = _patched_tomorrow_date_obj
        worker_mod.tomorrow_iso = _patched_tomorrow_iso
        worker_mod.tomorrow_day_number = _patched_tomorrow_day_number
        LOG.info("klook_worker.tomorrow_* monkey-patch 완료 → target=%s", target_str)
    except Exception as e:
        LOG.warning("tomorrow_* monkey-patch 실패: %s (worker 가 실제 내일을 처리할 수 있음)", e)

    # 시장 필터
    region_env = os.environ.get("KLOOK_REGIONS", "")
    if region_env.strip():
        wanted = {r.strip().upper() for r in region_env.split(",") if r.strip()}
        regions_to_run = [r for r in REGIONS if r[0] in wanted]
        LOG.info("KLOOK_REGIONS 필터 적용: %s", sorted(wanted))
    else:
        regions_to_run = list(REGIONS)

    all_results: List[ProductResult] = []
    for region, port, launcher_bat, name_fn in regions_to_run:
        try:
            rs = _close_one_region(
                worker_mod, packages_mod,
                region, port, launcher_bat, name_fn,
                dry_run,
            )
        except Exception as e:
            LOG.exception("[%s] 시장 처리 중 예외: %s", region, e)
            rs = [ProductResult(
                region=region, name="*", package_id="*", workflow="*",
                status=ProductStatus.EXCEPTION,
                detail=f"region 처리 예외: {str(e)[:150]}",
            )]
        all_results.extend(rs)

    # 요약 표
    summary = _build_summary(all_results)
    table_text = _render_summary_table(summary, target_str)
    print()
    print(table_text)
    LOG.info("\n%s", table_text)

    # 요약 파일 저장
    today_str = datetime.now().strftime("%Y-%m-%d")
    # P0-3: 지역 x forward/backward worker 가 같은 파일을 덮어쓰지 않도록 분리
    _wsuffix = "_".join(x for x in [
        os.environ.get("KLOOK_REGIONS", "").strip().replace(",", "-"),
        os.environ.get("KLOOK_DIRECTION", "").strip(),
    ] if x)
    summary_path = LOGS_DIR / (
        f"klook_summary_{today_str}{('_' + _wsuffix) if _wsuffix else ''}.txt")
    try:
        summary_path.write_text(table_text + "\n", encoding="utf-8")
        LOG.info("요약 저장: %s", summary_path)
    except Exception as e:
        LOG.warning("요약 파일 저장 실패: %s", e)

    # Result 변환
    success = sum(1 for r in all_results if r.status.is_success and r.name != "*")
    skipped = sum(1 for r in all_results if r.status.is_skip)
    failed = sum(1 for r in all_results if r.status.is_failure and r.name != "*")

    errors: List[str] = []
    for region, entry in summary.items():
        # 시장 자체 skip
        skipped_in_region = [p for p in entry["products"]
                             if p.status == ProductStatus.REGION_SKIPPED]
        if skipped_in_region:
            errors.append(f"[{region}] 시장 스킵: {skipped_in_region[0].detail[:80]}")
        # 상품별 실패
        for p in entry["products"]:
            if p.status.is_failure and p.name != "*":
                errors.append(f"[{region}] {p.name}: {p.status.value} - {p.detail[:80]}")

    return Result(
        agency="KLOOK",
        success=success, failed=failed, skipped=skipped,
        errors=errors[:20],
    )


# ============================================================
# CLI (subprocess 실행용)
# ============================================================
if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--date", default=None, help="YYYY-MM-DD")
    ap.add_argument("--region", default=None, help="KOREA/JAPAN/AUSTRALIA/UK")
    ap.add_argument("--direction", default=None, help="forward|backward")
    args = ap.parse_args()

    if args.region:
        os.environ["KLOOK_REGIONS"] = args.region.upper()
    if args.direction:
        os.environ["KLOOK_DIRECTION"] = args.direction.lower()

    tgt = None
    if args.date:
        tgt = datetime.strptime(args.date, "%Y-%m-%d").date()

    r = run_close(target_date=tgt, dry_run=args.dry_run)
    suffix = ""
    if args.region:
        suffix += "/" + args.region
    if args.direction:
        suffix += "/" + args.direction
    print("\n[KLOOK" + suffix + "] success=" + str(r["success"]) + " failed=" + str(r["failed"]) + " skipped=" + str(r["skipped"]))
    if r["errors"]:
        print("Errors:")
        for e in r["errors"][:20]:
            print("  -", e)
