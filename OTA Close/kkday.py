"""
KKDAY 마감 봇 v3 (V14 도메인 지식 + 운영 가시성 강화)
======================================================

v3 추가:
1. Post-close verification - "Ceased selling" 클릭 후 'Updated' 토스트 + 재Search 양방향 검증
2. product_no 단위 결과 추적 - 화이트리스트 6개 코드별 성공/실패 명확히
3. 실패 사유 세분화 - PackageStatus enum 으로 어디서 왜 실패했는지 구분
4. 마지막 요약 표 - 상품 코드별 결과 표를 stdout + logs/kkday_summary_YYYY-MM-DD.txt 양쪽에

흐름:
1) 지역 Chrome(9522~9525) 에 attach
2) /productlist 진입 → Manage date or session 링크 전부 수집 (페이지네이션 포함)
3) env KKDAY_PRODUCT_CODES > DEFAULT_PRODUCT_CODES 화이트리스트로 필터
4) 각 (product, package, option) dateToggle URL 방문:
   - Product No. 진입 검증
   - Departure Date = target_date, Status = Open, Search
   - 결과 행 수 기록 (rows_before)
   - All 체크 → Ceased selling → Confirm → 'Updated' 토스트 확인
   - 재Search 해서 rows_after == 0 인지 검증
5) 상품 코드별로 집계, 마지막에 요약 표

사용:
    from kkday import run_close
    result = run_close(target_date)  # None 이면 내일

환경 변수:
    KKDAY_PRODUCT_CODES   쉼표 구분 화이트리스트. 빈 문자열이면 전체. 미설정이면 DEFAULT_PRODUCT_CODES.
"""

from __future__ import annotations

import sys as _sys_init
try:
    _sys_init.stdout.reconfigure(encoding="utf-8", errors="replace")
    _sys_init.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from playwright.sync_api import Page, TimeoutError as PWTimeoutError

from shared.chrome_setup import connect_and_setup, close_worker_page
from shared.health import ensure_chrome
from shared.logger import get_agency_logger
from shared.types import Result

def _routed_port(region: str, channel: str, fallback: int) -> int:
    """hub 라우팅이 있으면 그 포트를, 없으면 기존 값을 쓴다."""
    try:
        from shared.hub_bridge import routing as _hub_routing
        r = _hub_routing()
        if r is not None:
            key = r.route(region, channel)
            if key:
                return int(r.profile_port(key))
    except Exception:
        pass
    return fallback



# ── Departure Date 입력 타임아웃 ──────────────────────────────────────────────
# goto 는 domcontentloaded 까지만 기다리는데, KKDAY 상품 페이지는 SPA 라서
# #searchDate 가 그 뒤에 렌더된다. 예전에는 곧바로 click(timeout=3000) 을 때려서
# PC/네트워크가 조금만 느려지면 DATE_INPUT_FAILED 가 무더기로 났다.
#   (2026-08-23 실행: 1차 패스에서 worker 당 ~30건 실패 -> 재시도로 대부분 회복,
#    최종 34건 실패. 재시도로 회복된다는 건 '진짜 실패'가 아니라 '기다리면 됐다'는 뜻)
# 그래서 클릭 전에 요소가 보일 때까지 넉넉히 기다리고, 클릭 타임아웃도 늘린다.
DATE_INPUT_WAIT_MS = 15_000     # #searchDate 가 렌더될 때까지 대기
DATE_INPUT_CLICK_MS = 8_000     # 클릭 자체 타임아웃
DATE_INPUT_RETRY = 2            # 실패 시 페이지 새로고침 후 재시도 횟수

LOG = get_agency_logger("KKDAY")
LOGS_DIR = ROOT / "logs"
LOGS_DIR.mkdir(exist_ok=True)

# 지역별 Chrome 포트 (shared/health.py 의 REGION_CHROME_MAP 과 일치)
# 한 Chrome 에 KLOOK + GG + KKDAY 가 같이 들어있고, 봇이 자기 사이트(scm.kkday.com) 탭을 찾아 동작.
REGIONS: List[tuple] = [
    ("KOREA",     _routed_port("KOREA", "KK", 9522), "start_chrome_korea.bat"),
    ("JAPAN",     _routed_port("JAPAN", "KK", 9523), "start_chrome_japan.bat"),
    ("AUSTRALIA", _routed_port("AUSTRALIA", "KK", 9524), "start_chrome_australia.bat"),
    ("UK",        _routed_port("UK", "KK", 9525), "start_chrome_uk.bat"),
]
# 하위 호환 (예전 단일 포트 참조용 - 일부 메시지에서 사용)
PORT = REGIONS[0][1]
LAUNCHER_BAT = REGIONS[0][2]
BASE = "https://scm.kkday.com"
PRODUCT_LIST = f"{BASE}/v1/en/product/productlist"
DATE_TOGGLE_RE = re.compile(r"/v1/en/product/dateToggle/(\d+)/(\d+)/(\d+)")

# 전수조사 모드: 화이트리스트 비어있음 → productlist 에서 "Managed by you"
# 필터가 적용된 전체 패키지를 자동 수집해서 처리.
# 특정 상품만 처리하려면 env KKDAY_PRODUCT_CODES="9367,8974,..." 설정.
# 매일 마감이 필요한 상품만 돈다. 나머지는 스킵.
# 허브에서 실행하면 KKDAY_PRODUCT_CODES 로 덮어쓰므로 hub/data/kkday_codes.json 이 기준이고,
# 이 값은 kkday.py 를 단독 실행할 때의 기본값이다.
#
# 왜 전체를 안 도나: '내 상품' 은 88개 상품 205개 패키지인데 매일 마감이 필요한 건 한 줌이다.
# 전부 도는 동안 worker 4개가 KKday 를 계속 두드려 페이지가 느려지고,
# 그 느려짐이 그대로 DATE_INPUT_FAILED 가 된다 (2026-08-23 아침 34건).
DEFAULT_PRODUCT_CODES: List[str] = [
    "9367",     # 에버
    "8974",     # 남이섬 레귤러
    "11186",    # 부산시티
    "17654",    # 경주
    "18613",    # 남이섬셔틀
    "167055",   # MBC
]


# ============================================================
# 결과 상태 분류 (v3: 사유 세분화)
# ============================================================
class PackageStatus(str, Enum):
    SUCCESS = "SUCCESS"                  # 마감 클릭 + 검증 통과
    DRY_RUN = "DRY_RUN"                  # dry-run 모드, 클릭 안 함
    ALREADY_CLOSED = "ALREADY_CLOSED"    # Search 했더니 Open 행 0개 (이미 닫힘)
    NO_SLOTS = "NO_SLOTS"                # 해당 날짜에 슬롯 자체가 없음
    NAVIGATION_FAILED = "NAV_FAILED"     # dateToggle URL 진입 실패
    PRODUCT_MISMATCH = "PRODUCT_MISMATCH"  # 진입은 했는데 Product No. 불일치 (잘못된 페이지)
    DATE_INPUT_FAILED = "DATE_INPUT_FAILED"  # Departure Date 입력 실패
    SEARCH_FAILED = "SEARCH_FAILED"      # Search 버튼 클릭 실패
    SELECT_ALL_FAILED = "SELECT_ALL_FAILED"  # All 체크박스 실패
    CEASED_BUTTON_FAILED = "CEASED_BUTTON_FAILED"  # Ceased selling 클릭 실패
    CONFIRM_FAILED = "CONFIRM_FAILED"    # 'Updated' Confirm 못 누름
    VERIFY_FAILED = "VERIFY_FAILED"      # 클릭 후 재Search 했는데 행이 안 사라짐
    EXCEPTION = "EXCEPTION"              # 예상 못 한 예외

    @property
    def is_success(self) -> bool:
        return self in (PackageStatus.SUCCESS, PackageStatus.DRY_RUN)

    @property
    def is_skip(self) -> bool:
        return self in (PackageStatus.ALREADY_CLOSED, PackageStatus.NO_SLOTS)

    @property
    def is_failure(self) -> bool:
        return not (self.is_success or self.is_skip)


@dataclass
class PackageResult:
    product_no: str
    package_id: str
    package_option_id: str
    label: str
    status: PackageStatus
    region: str = ""             # 어느 지역 Chrome 에서 처리했는지
    rows_before: int = 0
    rows_after: int = -1  # -1 = 검증 안 함
    elapsed_sec: float = 0.0
    detail: str = ""

    @property
    def url(self) -> str:
        return f"{BASE}/v1/en/product/dateToggle/{self.product_no}/{self.package_id}/{self.package_option_id}"


@dataclass
class PackagePage:
    product_no: str
    package_id: str
    package_option_id: str
    label: str

    @property
    def url(self) -> str:
        return f"{BASE}/v1/en/product/dateToggle/{self.product_no}/{self.package_id}/{self.package_option_id}"


# ============================================================
# 헬퍼
# ============================================================
def _normalize_text(text: str) -> str:
    return " ".join(str(text).replace("\xa0", " ").split())


def _parse_codes(value: str) -> List[str]:
    return [c.strip() for c in re.split(r"[\s,;/]+", value or "") if c.strip()]


def get_target_codes() -> Set[str]:
    env_val = os.environ.get("KKDAY_PRODUCT_CODES", None)
    if env_val is not None:
        return set(_parse_codes(env_val))
    return set(str(c).strip() for c in DEFAULT_PRODUCT_CODES if str(c).strip())


def inventory_result_matches_product_code(body_text: str, product_code: str) -> bool:
    """V14: body text 가 정확히 요청 Product No. 인지 검증."""
    text = _normalize_text(body_text)
    code = str(product_code).strip()
    patterns = [
        rf"Product\s*No\.?\s*:?\s*{re.escape(code)}\b",
        rf"Product\s*No\s*{re.escape(code)}\b",
    ]
    for p in patterns:
        if re.search(p, text, flags=re.IGNORECASE):
            return True
    lower = text.lower()
    pos = lower.find("product no")
    while pos != -1:
        near = text[pos:pos + 120]
        if re.search(rf"\b{re.escape(code)}\b", near):
            return True
        pos = lower.find("product no", pos + 1)
    return False


def count_table_rows(page: Page) -> int:
    """결과 테이블의 실제 데이터 행 수. 'No results' placeholder 면 0."""
    try:
        if page.get_by_text("No results", exact=False).count() > 0:
            return 0
        rows = page.locator("table tbody tr").count()
        return rows
    except Exception:
        return 0


def count_open_rows(page: Page) -> int:
    """
    Open 상태 행만 카운트 (Ceased 행은 제외).
    forward/backward 가 같은 패키지에 도착했을 때 이미 마감된 행을 다시 처리하지 않도록.

    [v2 - 엄격한 status 컬럼 매칭]
    KKDAY 테이블 status 컬럼 (3번째 td) 이 정확히 "Open" 인 행만 카운트.
    이전: 행 텍스트에 "ceased" 만 없으면 무조건 Open 처리 → 화면 갱신 중 빈 status
    셀이 일시적으로 비어있을 때 false positive 발생 → round 2 가 잘못 진입해서
    이미 닫힌 패키지를 CEASED_BUTTON_FAILED 로 잘못 기록하는 만성 이슈 원인.
    """
    try:
        if page.get_by_text("No results", exact=False).count() > 0:
            return 0
        return page.evaluate(
            """() => {
                const trs = Array.from(document.querySelectorAll('table tbody tr'));
                return trs.filter(tr => {
                    // status 컬럼 (3번째 td, index=2) 정확 매칭
                    const cells = tr.querySelectorAll('td');
                    if (cells.length < 3) return false;
                    const status = (cells[2].innerText || '').trim();
                    // 빈 status (로딩/갱신 중) 도 Open 으로 카운트하지 않음 → false positive 방지
                    return status === 'Open';
                }).length;
            }"""
        )
    except Exception:
        return 0


def wait_search_results(page: Page, timeout_ms: int = 6000) -> None:
    """Search 클릭 후 결과 테이블이 '실제로 렌더' 될 때까지 대기.

    고정 대기(0.8s)만 하면 병렬 부하로 렌더가 늦을 때 count_open_rows 가
    0을 읽어 열린 상품을 ALREADY_CLOSED 로 오보고함 (9367/1994043 케이스).
    - status 셀이 채워진 행이 하나라도 나타나거나
    - 'No results' 가 뜨면
    렌더 완료로 보고 진행. 이미 닫힌/슬롯없는 상품은 각각 Ceased행/No results 가
    곧 렌더되므로 이 대기는 정상 케이스 속도에 거의 영향 없음.
    """
    try:
        page.wait_for_function(
            """() => {
                const norm = s => (s || '').trim();
                const body = document.body ? (document.body.innerText || '') : '';
                if (body.includes('No results') || body.includes('No Result')) return true;
                const trs = Array.from(document.querySelectorAll('table tbody tr'));
                if (trs.length === 0) return false;
                // status 컬럼(3번째 td)이 채워진 행이 하나라도 있으면 렌더 완료
                return trs.some(tr => {
                    const c = tr.querySelectorAll('td');
                    return c.length >= 3 && norm(c[2].innerText) !== '';
                });
            }""",
            timeout=timeout_ms,
        )
    except Exception:
        pass
    page.wait_for_timeout(300)


def search_confirmed_closed(page: Page) -> bool:
    """rows_before==0 일 때 '진짜 마감'(빈 테이블 오독이 아님)인지 확정.

    True(마감 확정) 조건:
      - 'No results' 문구가 보이거나
      - 상태셀(3번째 td)이 채워진 행이 하나라도 있음 (전부 'Ceased' 여도 결과가 실제 렌더된 것)
    False(미확정) 조건:
      - tbody 행이 0개이고 'No results' 도 없음 → 검색 결과가 아직 안 뜬 상태
        (로딩 지연 / Search 클릭 미등록). 이때 0행을 마감으로 오인하면 열린 상품을 스킵함.
    """
    try:
        return bool(page.evaluate(
            r"""() => {
                const norm = s => (s || '').trim();
                const body = document.body ? (document.body.innerText || '') : '';
                if (body.includes('No results') || body.includes('No Result')) return true;
                const trs = Array.from(document.querySelectorAll('table tbody tr'));
                return trs.some(tr => {
                    const c = tr.querySelectorAll('td');
                    return c.length >= 3 && norm(c[2].innerText) !== '';
                });
            }"""
        ))
    except Exception:
        # 판정 자체가 실패하면 '확정'으로 보지 않음 → 재시도 쪽이 안전
        return False


# ============================================================
# 1) 상품 목록 수집
# ============================================================
def _apply_managed_by_you_filter(page: Page) -> bool:
    """
    productlist 페이지에서 "Managed by you" 필터를 적용 + Search 클릭.
    이 필터를 적용하면 우리 권한 패키지만 남아서 207 -> ~174 로 줄어들고
    권한 없는 패키지에서 헛수고하는 일이 없어진다.
    """
    try:
        # Permission select 는 id/name 이 비어있어서 옵션 텍스트로 찾는다.
        ok = page.evaluate(
            """
            (() => {
              const sel = Array.from(document.querySelectorAll('select')).find(
                s => Array.from(s.options).some(o => o.text === 'Managed by you')
              );
              if (!sel) return false;
              sel.value = '1';
              sel.dispatchEvent(new Event('change', {bubbles: true}));
              return true;
            })()
            """
        )
        if not ok:
            LOG.warning("Permission select 못 찾음 - 필터 적용 skip")
            return False

        # Search 버튼
        try:
            page.locator("button.searchBtn, button:has-text('Search')").first.click(timeout=3000)
        except Exception:
            page.get_by_role("button", name="Search").first.click(timeout=3000)
        page.wait_for_timeout(800)
        try:
            page.wait_for_load_state("networkidle", timeout=10_000)
        except PWTimeoutError:
            pass
        LOG.info("'Managed by you' 필터 적용 완료")
        return True
    except Exception as e:
        LOG.warning("'Managed by you' 필터 적용 실패: %s", e)
        return False


def _pagination_info(page: Page) -> Tuple[int, int, int]:
    """
    pagination-num div 에서 (current_page, total_pages, total_items) 파싱.
    실패 시 (0, 0, 0).
    예: '1/21 Total:207' → (1, 21, 207)
    """
    try:
        text = page.locator("div.pagination-num").first.inner_text(timeout=2_000)
        m_page = re.search(r"(\d+)\s*/\s*(\d+)", text)
        m_total = re.search(r"Total:\s*(\d+)", text)
        cur = int(m_page.group(1)) if m_page else 0
        tot = int(m_page.group(2)) if m_page else 0
        items = int(m_total.group(1)) if m_total else 0
        return cur, tot, items
    except Exception:
        return 0, 0, 0


def _wait_links_stable(page: Page, timeout_ms: int = 8000) -> int:
    """현재 페이지의 'Manage date or session' 링크 개수가 안정될 때까지 대기.
    lazy 렌더로 뒤쪽 행이 늦게 붙어 collect 에서 통째로 누락되는 것을 방지한다.
    연속 2회 동일 개수(>0)면 안정으로 판단하고 그 개수를 반환."""
    deadline = time.time() + timeout_ms / 1000.0
    last = -1
    stable = 0
    count = 0
    while time.time() < deadline:
        try:
            count = page.locator("a", has_text="Manage date or session").count()
        except Exception:
            count = 0
        if count > 0 and count == last:
            stable += 1
            if stable >= 2:
                return count
        else:
            stable = 0
        last = count
        page.wait_for_timeout(300)
    return count


def discover_packages(page: Page) -> List[PackagePage]:
    LOG.info("상품 목록 수집 시작")

    seen: set[tuple[str, str, str]] = set()
    results: List[PackagePage] = []

    def collect() -> int:
        added = 0
        links = page.locator("a", has_text="Manage date or session").all()
        for a in links:
            href = a.get_attribute("href") or ""
            m = DATE_TOGGLE_RE.search(href)
            if not m:
                continue
            key = m.groups()
            if key in seen:
                continue
            seen.add(key)
            try:
                card = a.locator(
                    "xpath=ancestor::*[contains(@class,'card') or contains(@class,'list') or self::tr or self::li][1]"
                )
                label = card.inner_text(timeout=500).split("\n")[0][:80]
            except Exception:
                label = f"product_{key[0]}"
            results.append(PackagePage(*key, label=label))
            added += 1
        return added

    def run_pass() -> int:
        """productlist 진입 → 'Managed by you' 필터 → 전 페이지 순회하며 collect.
        반환값: pagination 에서 파싱한 Total items (검증용). seen 기반이라
        여러 번 호출해도 이미 담은 패키지는 중복 수집되지 않는다."""
        page.goto(PRODUCT_LIST, wait_until="domcontentloaded")
        try:
            page.wait_for_load_state("networkidle", timeout=15_000)
        except PWTimeoutError:
            pass

        # "Managed by you" 필터 적용 (우리 권한 패키지만)
        _apply_managed_by_you_filter(page)

        cur, tot, items = _pagination_info(page)
        if items:
            LOG.info("필터 적용 후 페이지네이션: page %d/%d, total items=%d", cur, tot, items)

        # 링크 개수가 안정될 때까지 대기 후 수집 (부분 렌더 누락 방지)
        _wait_links_stable(page)
        collect()

        # 페이지네이션: KKDAY 는 <ul class="pagination"> 안에 <li class="active">
        # 가 현재 페이지. Next 는 <a aria-label="Next"> 로 식별.
        max_pages = max(tot if tot > 0 else 50, 50)
        consecutive_no_new = 0
        for page_iter in range(max_pages):
            before = len(results)
            # 현재 active 페이지 번호 (이걸로 클릭 후 페이지 바뀐지 확인)
            try:
                active_before = page.evaluate(
                    "() => { const el = document.querySelector('ul.pagination li.active a'); return el ? el.innerText.trim() : ''; }"
                )
            except Exception:
                active_before = ""

            # 마지막 페이지에 도달했으면 종료 (active >= total_pages)
            if tot and active_before:
                try:
                    if int(active_before) >= tot:
                        break
                except (ValueError, TypeError):
                    pass

            try:
                nxt = page.locator(
                    "ul.pagination a[aria-label='Next'], a[aria-label='Next']"
                ).first
                if nxt.count() == 0:
                    break
                parent_class = page.evaluate(
                    "(el) => el.closest('li')?.className || ''", nxt.element_handle()
                ) if nxt.count() else ""
                if "disabled" in (parent_class or "").lower():
                    break
                try:
                    if nxt.is_disabled():
                        break
                except Exception:
                    pass
                nxt.click()
            except Exception as e:
                LOG.warning("페이지네이션 Next 클릭 실패: %s", e)
                break

            # 페이지 바뀌었는지 active 페이지 번호로 검증 + 재시도
            page_changed = False
            for _try in range(10):
                try:
                    page.wait_for_timeout(400)
                    active_after = page.evaluate(
                        "() => { const el = document.querySelector('ul.pagination li.active a'); return el ? el.innerText.trim() : ''; }"
                    )
                    if active_after and active_after != active_before:
                        page_changed = True
                        break
                except Exception:
                    pass
            if not page_changed:
                LOG.warning("Next 클릭 후 페이지가 안 바뀜 (active=%s, iter=%d)", active_before, page_iter)
                # 한 번 더 클릭 재시도
                try:
                    page.locator("ul.pagination a[aria-label='Next']").first.click(timeout=3000)
                    page.wait_for_timeout(1000)
                except Exception:
                    break

            try:
                page.wait_for_load_state("networkidle", timeout=10_000)
            except PWTimeoutError:
                pass
            # 링크 개수가 안정될 때까지 대기 (렌더 레이스로 뒤쪽 행 누락 방지)
            _wait_links_stable(page)
            collect()

            # '새 항목 0개면 즉시 중단'은 렌더 레이스로 뒤쪽 페이지를 통째로 버릴 수 있어 위험.
            # → 연속 3회 0개일 때만 종료 (마지막 페이지 도달은 위 active>=tot 검증에서 처리).
            if len(results) == before:
                consecutive_no_new += 1
                if consecutive_no_new >= 3:
                    break
            else:
                consecutive_no_new = 0
        return items

    items = run_pass()

    # 수집 < Total 이면 렌더 레이스로 일부 행을 놓쳤을 수 있음 → 전체 재순회로 보강.
    # seen 이 이미 수집한 패키지를 중복 제거하므로 누락분만 새로 담긴다.
    if items and len(results) < items:
        LOG.warning(
            "1차 수집 %d < Total %d → 누락 의심, 전체 재순회 재시도",
            len(results), items,
        )
        run_pass()

    LOG.info("총 %d 개 (productNo, packageId, optionId) 수집", len(results))
    if items and len(results) != items:
        LOG.warning(
            "⚠️ 수집 누락 확정: 수집 %d / Total %d (누락 %d개) - "
            "일부 패키지가 마감 시도조차 안 됨(수동 확인 필요)",
            len(results), items, items - len(results),
        )
    return results


def filter_by_target_codes(packages: List[PackagePage], target_codes: Set[str]) -> List[PackagePage]:
    if not target_codes:
        LOG.info("화이트리스트 비어 있음 → 발견된 전체 처리")
        return packages
    filtered = [p for p in packages if p.product_no in target_codes]
    LOG.info("화이트리스트 적용: %d → %d", len(packages), len(filtered))
    found_codes = {p.product_no for p in packages}
    missing = target_codes - found_codes
    if missing:
        LOG.warning("화이트리스트에 있지만 productlist 에서 못 찾은 코드: %s", sorted(missing))
    return filtered


# ============================================================
# 2) 단일 패키지 마감 (v3: 단계별 사유 분류 + post-close 검증)
# ============================================================
def close_one_package(
    page: Page,
    pkg: PackagePage,
    target_date_str: str,
    dry_run: bool,
) -> PackageResult:
    """각 단계별로 실패 사유를 구분해서 PackageResult 반환."""
    t0 = time.perf_counter()
    pr = PackageResult(
        product_no=pkg.product_no,
        package_id=pkg.package_id,
        package_option_id=pkg.package_option_id,
        label=pkg.label,
        status=PackageStatus.EXCEPTION,
    )

    # --- (A) 페이지 진입 (빠르게 - networkidle 안 기다림) ---
    try:
        page.goto(pkg.url, wait_until="domcontentloaded", timeout=10_000)
    except Exception as e:
        pr.status = PackageStatus.NAVIGATION_FAILED
        pr.detail = f"goto 실패: {e}"
        pr.elapsed_sec = time.perf_counter() - t0
        return pr

    # --- (B) Product No. 검증 (URL 기반) ---
    # 페이지에 "Product No" 텍스트가 없는 경우가 있어 (Product Name 만 표시) URL 로 검증.
    # navigate 후 KKDAY 가 다른 URL 로 redirect 시키면 잡힘.
    try:
        cur_url = page.url or ""
        if f"/dateToggle/{pkg.product_no}/" not in cur_url:
            pr.status = PackageStatus.PRODUCT_MISMATCH
            pr.detail = f"URL 불일치: 기대 product_no={pkg.product_no}, 실제 URL={cur_url[:120]}"
            pr.elapsed_sec = time.perf_counter() - t0
            return pr
    except Exception:
        pass

    # --- (C) Departure Date 입력 ---
    # KKDAY 는 bootstrap daterangepicker 사용. 가장 안정적인 방식은
    # 캘린더 UI 를 사람이 하는 것처럼 직접 클릭:
    # 1) #searchDate 클릭 → picker 열림
    # 2) 월/년 헤더 확인, prev/next 화살표로 목표 월 이동
    # 3) 목표 day 셀 클릭 (시작 + 끝 같은 날 두 번)
    # 4) Apply 버튼 클릭
    try:
        y, m, d = target_date_str.split("/")
        target_year, target_month, target_day = int(y), int(m), int(d)
    except Exception as e:
        pr.status = PackageStatus.DATE_INPUT_FAILED
        pr.detail = f"target_date 파싱 실패: {target_date_str} ({e})"
        pr.elapsed_sec = time.perf_counter() - t0
        return pr

    try:
        di = page.locator("#searchDate, input[placeholder='Please select date']").first

        # SPA 가 늦게 렌더되는 날이 있다. 곧바로 클릭하지 말고 보일 때까지 기다린다.
        # 안 기다리면 느린 날 DATE_INPUT_FAILED 가 무더기로 난다.
        last_err = None
        for attempt in range(DATE_INPUT_RETRY + 1):
            try:
                di.wait_for(state="visible", timeout=DATE_INPUT_WAIT_MS)
                di.click(timeout=DATE_INPUT_CLICK_MS)
                last_err = None
                break
            except Exception as _e:
                last_err = _e
                if attempt >= DATE_INPUT_RETRY:
                    break
                LOG.info("[%s] 날짜 입력칸 대기 실패(%d/%d) - 새로고침 후 재시도",
                         pkg.product_no, attempt + 1, DATE_INPUT_RETRY)
                try:
                    page.reload(wait_until="domcontentloaded", timeout=15_000)
                    page.wait_for_timeout(800)
                except Exception:
                    pass
        if last_err is not None:
            raise last_err

        page.wait_for_timeout(250)

        # picker 가 열린 것 확인
        picker = page.locator(".daterangepicker").first
        try:
            picker.wait_for(state="visible", timeout=3000)
        except Exception:
            di.click(timeout=DATE_INPUT_CLICK_MS)
            page.wait_for_timeout(400)

        # 월/년 네비게이션 (left calendar 기준)
        # 영어 + 한국어 + 축약형 모두 지원
        month_names = {
            "January":1, "February":2, "March":3, "April":4, "May":5, "June":6,
            "July":7, "August":8, "September":9, "October":10, "November":11, "December":12,
            "Jan":1, "Feb":2, "Mar":3, "Apr":4, "Jun":6, "Jul":7, "Aug":8,
            "Sep":9, "Sept":9, "Oct":10, "Nov":11, "Dec":12,
        }

        def _parse_header(text):
            """헤더 텍스트에서 (year, month) 추출. 영어/한국어/숫자만 형식 모두 지원."""
            import re as _re
            t = (text or "").strip()
            if not t:
                return None
            # 1) 한국어: "6월 2026" 또는 "2026년 6월"
            m = _re.search(r"(\d{1,2})\s*월\s*(\d{4})", t)
            if m:
                return int(m.group(2)), int(m.group(1))
            m = _re.search(r"(\d{4})\s*년\s*(\d{1,2})\s*월", t)
            if m:
                return int(m.group(1)), int(m.group(2))
            # 2) 숫자만: "2026/06" "2026-06" "2026.06" "06/2026"
            m = _re.search(r"(\d{4})[./\-](\d{1,2})\b", t)
            if m:
                return int(m.group(1)), int(m.group(2))
            m = _re.search(r"\b(\d{1,2})[./\-](\d{4})\b", t)
            if m:
                return int(m.group(2)), int(m.group(1))
            # 3) 영어: "May 2026" 또는 "2026 May" 또는 "May, 2026"
            for token in _re.findall(r"[A-Za-z]+", t):
                mm = month_names.get(token) or month_names.get(token.capitalize())
                if mm:
                    yrs = _re.findall(r"\d{4}", t)
                    if yrs:
                        return int(yrs[0]), mm
            return None

        nav_ok = False
        last_header_text = None
        for _ in range(36):
            header = page.locator(".daterangepicker .calendar.left .month, .daterangepicker .month").first
            try:
                header_text = header.inner_text(timeout=1500).strip()
            except Exception:
                break
            last_header_text = header_text
            parsed = _parse_header(header_text)
            if parsed is None:
                break
            cur_y, cur_m = parsed
            if cur_m == target_month and cur_y == target_year:
                nav_ok = True
                break
            delta = (target_year - cur_y) * 12 + (target_month - cur_m)
            if delta > 0:
                page.locator(".daterangepicker .calendar.left .next, .daterangepicker .next").first.click()
            else:
                page.locator(".daterangepicker .calendar.left .prev, .daterangepicker .prev").first.click()
            page.wait_for_timeout(100)

        if not nav_ok:
            pr.status = PackageStatus.DATE_INPUT_FAILED
            pr.detail = (f"picker 월 네비게이션 실패 ({target_year}/{target_month:02d}) "
                         f"| 헤더 raw text={last_header_text!r}")
            pr.elapsed_sec = time.perf_counter() - t0
            return pr

        # 목표 day 셀 클릭 (off/disabled 제외, current month 만)
        day_cells = page.locator(
            f".daterangepicker .calendar.left tbody td:not(.off):not(.disabled)"
        )
        n = day_cells.count()
        target_cell = None
        for i in range(n):
            try:
                txt = day_cells.nth(i).inner_text(timeout=500).strip()
                if txt == str(target_day):
                    target_cell = day_cells.nth(i)
                    break
            except Exception:
                continue

        if target_cell is None:
            pr.status = PackageStatus.DATE_INPUT_FAILED
            pr.detail = f"picker 에서 day {target_day} 셀 못 찾음"
            pr.elapsed_sec = time.perf_counter() - t0
            return pr

        target_cell.click()  # start
        page.wait_for_timeout(100)
        target_cell.click()  # end (same day = single day range)
        page.wait_for_timeout(100)

        # Apply 버튼
        try:
            page.locator(".daterangepicker button.applyBtn, button.applyBtn").first.click(timeout=1500)
        except Exception:
            pass
        page.wait_for_timeout(250)

        # 입력값 검증
        try:
            actual = page.locator("#searchDate").input_value(timeout=2000)
            if target_date_str not in (actual or ""):
                pr.status = PackageStatus.DATE_INPUT_FAILED
                pr.detail = f"date input 검증 실패: target={target_date_str}, actual={actual!r}"
                pr.elapsed_sec = time.perf_counter() - t0
                return pr
            LOG.info("[%s] date input OK: %s", pkg.product_no, actual)
        except Exception:
            pass
    except Exception as e:
        pr.status = PackageStatus.DATE_INPUT_FAILED
        pr.detail = f"Departure Date 입력 실패: {e}"
        pr.elapsed_sec = time.perf_counter() - t0
        return pr

    # (Selling Status 라디오 단계 제거 - All 기본값 그대로 사용,
    #  어차피 Ceased selling 누르고 안 되면 다음으로 넘김)

    # --- (E) Search ---
    try:
        # ★ stale 결과 방지: Search 직전에 기존 결과 테이블 행을 비운다.
        #   Search 가 실제로 재조회하면 새 행이 채워지고, no-op/지연이면 빈 채로 남는다.
        #   → 이전 패키지/이전 상태의 'Ceased 행'을 이 패키지 결과로 오독해서
        #     '열린 상품'을 rows_before=0(ALREADY_CLOSED)으로 잘못 스킵하는 것을 원천 차단.
        #     (열린 채였는데 스킵된 8974/440723, 9367/2005210 사례)
        #   비운 뒤에도 안 채워지면 search_confirmed_closed=False → SEARCH_FAILED 로 재시도됨.
        try:
            page.evaluate("() => { document.querySelectorAll('table tbody tr').forEach(tr => tr.remove()); }")
        except Exception:
            pass
        page.get_by_role("button", name="Search").click(timeout=3000)
        # 결과 테이블이 실제 렌더될 때까지 대기 (고정 0.8s 대기만 하면 로딩 지연 시
        # 0행으로 오판 → ALREADY_CLOSED 오보고. 9367/1994043 이 그 케이스였음)
        wait_search_results(page)
    except Exception as e:
        pr.status = PackageStatus.SEARCH_FAILED
        pr.detail = f"Search 클릭 실패: {e}"
        pr.elapsed_sec = time.perf_counter() - t0
        return pr

    # --- (F) 결과 행 수 측정 ---
    # Open 상태 행만 카운트 (Ceased 는 이미 마감이므로 제외)
    rows_before = count_open_rows(page)
    pr.rows_before = rows_before

    if rows_before == 0:
        # ★ rows_before==0 은 두 가지 의미가 섞여 있음:
        #   (1) 진짜 마감/슬롯없음  (2) 검색 결과가 아직 안 뜬 '빈 테이블'(로딩 지연·Search 미등록)
        # (2)를 ALREADY_CLOSED 로 처리하면 '열려 있는 상품을 조용히 스킵'하게 됨
        #   (실측: 8974/442767, 17654/461775 가 41행 Open 인데 0으로 오독되어 스킵됨).
        # → 'No results' 문구가 있거나, 상태셀이 채워진 행(전부 Ceased 여도)이 실제로 보일 때만
        #   '마감 확정'으로 보고 ALREADY_CLOSED. 그 외(빈 테이블)는 SEARCH_FAILED 로 재시도시킨다.
        if search_confirmed_closed(page):
            pr.status = PackageStatus.ALREADY_CLOSED
            pr.detail = "Open 행 0개 (이미 마감 또는 슬롯 없음) - 다음으로"
        else:
            pr.status = PackageStatus.SEARCH_FAILED
            pr.detail = "검색 결과 미렌더(빈 테이블·No results 없음) → 0행 오독 방지, 실패로 재시도"
        pr.rows_after = 0
        pr.elapsed_sec = time.perf_counter() - t0
        return pr

    # --- (G) DRY-RUN ---
    if dry_run:
        pr.status = PackageStatus.DRY_RUN
        pr.detail = f"DRY: {rows_before} 행 마감 대상"
        pr.elapsed_sec = time.perf_counter() - t0
        return pr

    # --- (H) All 체크 → Ceased selling → Confirm (최대 5라운드, 페이지 분할 대응) ---
    total_closed = 0
    rounds = 0
    confirm_clicked_at_least_once = False
    updated_toast_seen = False

    for round_idx in range(5):
        rounds = round_idx + 1
        current_rows = count_open_rows(page)  # Open 행만
        if current_rows == 0:
            break

        # All 체크박스
        try:
            page.locator("input[type=checkbox]").first.check(timeout=3000)
        except Exception as e:
            pr.status = PackageStatus.SELECT_ALL_FAILED
            pr.detail = f"All 체크박스 실패 (round={rounds}): {e}"
            pr.rows_after = current_rows
            pr.elapsed_sec = time.perf_counter() - t0
            return pr

        # Ceased selling 버튼
        ceased_btn = page.get_by_role("button", name="Ceased selling")
        # 버튼은 '테이블 행이 실제 선택돼야' 활성화됨. .click() 은 활성화까지 5초 기다리므로
        # 누르기 전에 즉시 활성 여부를 판단해 시간 낭비를 막는다.
        try:
            btn_enabled = ceased_btn.is_enabled(timeout=1500)
        except Exception:
            btn_enabled = False

        # 비활성이면: 위 전체선택(.first)이 엉뚱한 체크박스를 눌러 행이 실제로 선택 안 됐을 수 있음.
        # → 테이블 행 체크박스를 직접 모두 클릭해서 선택 후 재확인 (예약 유무와 무관, 신규 예약만 차단).
        if not btn_enabled:
            try:
                page.eval_on_selector_all(
                    "table tbody tr input[type=checkbox]",
                    "els => els.forEach(e => { if (!e.disabled && !e.checked) e.click(); })",
                )
                page.wait_for_timeout(300)
                ceased_btn = page.get_by_role("button", name="Ceased selling")
                btn_enabled = ceased_btn.is_enabled(timeout=1500)
            except Exception:
                pass

        if not btn_enabled:
            # 행을 직접 다 선택해도 비활성 = 이 날짜에 닫을 수 있는 세션이 없거나(미운영)
            # KKDAY UI 구조가 바뀐 경우. 닫힘으로 위장하지 말고 진단 남기고 실패로 표시.
            try:
                diag = page.evaluate("""() => {
                    const all = Array.from(document.querySelectorAll('input[type=checkbox]'));
                    const rows = Array.from(document.querySelectorAll('table tbody tr input[type=checkbox]'));
                    return {
                        cb_total: all.length,
                        cb_checked: all.filter(c => c.checked).length,
                        row_cb_total: rows.length,
                        row_cb_checked: rows.filter(c => c.checked).length,
                        row_cb_disabled: rows.filter(c => c.disabled).length,
                    };
                }""")
            except Exception:
                diag = {}
            pr.status = PackageStatus.CEASED_BUTTON_FAILED
            pr.detail = (f"Ceased selling 버튼 비활성 → 안 닫힘 (round={rounds}, "
                         f"open_rows={current_rows}, closed_so_far={total_closed}) "
                         f"진단={diag}")
            pr.rows_after = current_rows
            pr.elapsed_sec = time.perf_counter() - t0
            return pr
        try:
            ceased_btn.click(timeout=5000)
        except Exception as e:
            pr.status = PackageStatus.CEASED_BUTTON_FAILED
            pr.detail = f"Ceased selling 클릭 실패 (round={rounds}): {e}"
            pr.rows_after = current_rows
            pr.elapsed_sec = time.perf_counter() - t0
            return pr

        # 'Updated' 토스트 (있으면 좋음) + Confirm 버튼 (다중 selector 빠르게)
        try:
            # 'Updated' 토스트 텍스트 빠르게 체크
            try:
                page.get_by_text("Updated", exact=False).first.wait_for(state="visible", timeout=1500)
                updated_toast_seen = True
            except PWTimeoutError:
                pass

            # Confirm 모달이 렌더될 때까지 대기 (렌더 레이스 방지):
            # "Ceased selling" 클릭 직후 모달이 늦게 뜨는데 셀렉터들을 먼저 다 시도해버려
            # CONFIRM_FAILED 나던 문제 → 보이는 'Confirm' 버튼이 나타날 때까지 최대 6초 폴링.
            # offsetParent 는 position:fixed 모달에서 null 이라 오탐 → getClientRects 로 가시성 판정.
            # 라벨도 Confirm 한정하지 않고 확정 계열(OK/Yes/확정/확인)까지 폴링.
            try:
                page.wait_for_function(
                    """() => {
                        const labels = ['confirm','ok','yes','확정','확인'];
                        const isVis = (el) => el && el.getClientRects().length > 0 && !el.disabled;
                        const btns = Array.from(document.querySelectorAll('button'));
                        return btns.some(b => {
                            if (!isVis(b)) return false;
                            const t = (b.innerText || '').trim().toLowerCase();
                            return labels.some(l => t === l || t.includes(l));
                        });
                    }""",
                    timeout=8000,
                )
            except PWTimeoutError:
                pass

            # Confirm 버튼: 여러 selector 빠르게 시도 (각 1.5초). 라벨/모달 클래스 확대.
            confirm_selectors = [
                "button.scm-btn-primary:has-text('Confirm')",
                "button:has-text('Confirm'):visible",
                "[role=dialog] button:has-text('Confirm')",
                ".modal button:has-text('Confirm')",
                ".scm-modal button:has-text('Confirm')",
                ".ant-modal button:has-text('Confirm')",
                "button.btn-primary:has-text('Confirm')",
                # 라벨이 Confirm 이 아닌 케이스 대비 (모달 내부로 한정해 오클릭 방지)
                "[role=dialog] button.scm-btn-primary:visible",
                ".ant-modal button.ant-btn-primary:visible",
                ".scm-modal button:has-text('OK'):visible",
                "[role=dialog] button:has-text('확정'):visible",
                "[role=dialog] button:has-text('확인'):visible",
            ]
            clicked = False
            for sel in confirm_selectors:
                try:
                    page.locator(sel).first.click(timeout=1500)
                    clicked = True
                    confirm_clicked_at_least_once = True
                    break
                except Exception:
                    continue

            # 그래도 안 되면 JS 직접 클릭 + 진단 수집 (fixed 모달: getClientRects 로 가시성 판정)
            _confirm_diag = ""
            if not clicked:
                try:
                    res = page.evaluate("""
                    () => {
                      const labels = ['confirm','ok','yes','확정','확인'];
                      const isVis = (el) => el && el.getClientRects().length > 0 && !el.disabled;
                      const inModal = (b) => b.closest('[role=dialog],.modal,.scm-modal,.ant-modal') != null;
                      const match = (b) => { const t=(b.innerText||'').trim().toLowerCase();
                        return labels.some(l => t===l || t.includes(l)); };
                      const btns = Array.from(document.querySelectorAll('button'));
                      // 진단: 화면에 보이는 버튼 텍스트 + iframe 안 버튼 수
                      const visTexts = btns.filter(isVis).map(b => (b.innerText||'').trim()).filter(Boolean);
                      let iframeBtns = 0;
                      for (const f of Array.from(document.querySelectorAll('iframe'))) {
                        try { iframeBtns += f.contentDocument.querySelectorAll('button').length; } catch(e) {}
                      }
                      const diag = 'visBtns=[' + visTexts.slice(0,15).join(' | ') + '] iframeBtns=' + iframeBtns;
                      // 우선순위: 모달 내부 primary → 모달 내부 label 매칭 → 아무 곳 label 매칭
                      let target = btns.find(b => isVis(b) && match(b) && inModal(b) && /primary/i.test(b.className));
                      if (!target) target = btns.find(b => isVis(b) && match(b) && inModal(b));
                      if (!target) target = btns.find(b => isVis(b) && match(b));
                      if (target) { target.click(); return {clicked:true, diag}; }
                      return {clicked:false, diag};
                    }
                    """)
                    if res and res.get("clicked"):
                        clicked = True
                        confirm_clicked_at_least_once = True
                    else:
                        _confirm_diag = (res or {}).get("diag", "")
                except Exception as _e:
                    _confirm_diag = f"진단 evaluate 예외: {_e}"

            if not clicked:
                # Confirm 모달이 정말 안 뜬 경우 (이미 자동 닫혔거나)
                # 첫 라운드부터 실패면 진짜 실패, 아니면 계속.
                # 실패 시 실제로 보이는 버튼 텍스트를 detail 에 남겨 다음 run 에서 원인 특정.
                if round_idx == 0:
                    pr.status = PackageStatus.CONFIRM_FAILED
                    pr.detail = f"Confirm 버튼 못 찾음 (selector+JS 실패) | {_confirm_diag}"
                    pr.rows_after = current_rows
                    pr.elapsed_sec = time.perf_counter() - t0
                    return pr
        except Exception as e:
            pr.status = PackageStatus.CONFIRM_FAILED
            pr.detail = f"Confirm 클릭 처리 중 예외 (round={rounds}): {e}"
            pr.rows_after = current_rows
            pr.elapsed_sec = time.perf_counter() - t0
            return pr

        page.wait_for_timeout(800)
        total_closed += current_rows

        # 재Search 해서 행이 줄었는지 확인 (고정 대기 대신 렌더 완료 대기 → 0행 오독으로 성공 오보고 방지)
        try:
            page.get_by_role("button", name="Search").click(timeout=3000)
            wait_search_results(page)
        except Exception:
            pass

        new_rows = count_open_rows(page)  # Open 행만 카운트
        if new_rows == 0:
            break
        if new_rows >= current_rows:
            # 처리 안 됨 - 같거나 더 늘었음
            pr.status = PackageStatus.VERIFY_FAILED
            pr.detail = (f"마감 클릭 후에도 Open 행이 줄지 않음 "
                         f"(before={current_rows} after={new_rows}, round={rounds}, "
                         f"updated_toast={updated_toast_seen}, confirm_clicked={confirm_clicked_at_least_once})")
            pr.rows_after = new_rows
            pr.elapsed_sec = time.perf_counter() - t0
            return pr

    # --- (I) Post-close 최종 검증 ---
    # 열린 행만 카운트: 마감된(Ceased) 행이 테이블에 남아 보여도 '성공'을 실패로 오보고하지 않도록.
    final_rows = count_open_rows(page)
    pr.rows_after = final_rows

    if final_rows == 0:
        pr.status = PackageStatus.SUCCESS
        pr.detail = (f"{total_closed} 행 마감 완료 (rounds={rounds}, "
                     f"updated_toast={updated_toast_seen})")
    else:
        # 행이 남아있음 = 일부만 처리됐거나 검증 실패
        pr.status = PackageStatus.VERIFY_FAILED
        pr.detail = (f"부분 처리만 됨: 처리={total_closed} / 잔여 Open 행={final_rows} "
                     f"(rounds={rounds}, updated_toast={updated_toast_seen})")

    pr.elapsed_sec = time.perf_counter() - t0
    return pr


# ============================================================
# 3) 상품 코드별 요약 생성 (v3 신규)
# ============================================================
def build_summary_by_product(results: List[PackageResult], target_codes: Set[str]) -> Dict[str, Dict]:
    """product_no 별로 묶어서 상태 집계."""
    summary: Dict[str, Dict] = {}
    # 화이트리스트의 코드 먼저 키로 추가 (찾지 못해도 표에 나오도록)
    for code in (target_codes or set()):
        summary[code] = {"total": 0, "success": 0, "skipped": 0, "failed": 0,
                         "packages": [], "statuses": []}

    for pr in results:
        code = pr.product_no
        entry = summary.setdefault(code, {
            "total": 0, "success": 0, "skipped": 0, "failed": 0,
            "packages": [], "statuses": []
        })
        entry["total"] += 1
        entry["packages"].append(pr)
        entry["statuses"].append(pr.status.value)
        if pr.status.is_success:
            entry["success"] += 1
        elif pr.status.is_skip:
            entry["skipped"] += 1
        else:
            entry["failed"] += 1
    return summary


def render_summary_table(
    summary: Dict[str, Dict],
    target_codes: Set[str],
    target_date_str: str,
) -> str:
    """사람이 한눈에 볼 수 있는 표 (stdout + 파일 저장용)."""
    lines: List[str] = []
    lines.append("=" * 78)
    lines.append(f" KKDAY 마감 결과 ({target_date_str})")
    lines.append("=" * 78)
    lines.append(f"{'상품 코드':<10} {'결과':<10} {'패키지':<8} {'성공':<5} {'실패':<5} {'스킵':<5}  사유")
    lines.append("-" * 78)

    # 화이트리스트 순서 우선, 그 외 코드는 뒤에
    ordered_keys = []
    for code in DEFAULT_PRODUCT_CODES:
        if code in summary:
            ordered_keys.append(code)
    for code in sorted(summary.keys()):
        if code not in ordered_keys:
            ordered_keys.append(code)

    overall_success = overall_failed = overall_skipped = overall_total = 0

    for code in ordered_keys:
        entry = summary[code]
        total = entry["total"]
        succ = entry["success"]
        fail = entry["failed"]
        skip = entry["skipped"]
        overall_total += total
        overall_success += succ
        overall_failed += fail
        overall_skipped += skip

        if total == 0:
            verdict = "MISSING"
            reason = "productlist 에서 패키지 못 찾음 (등록 해제됐거나 권한 변경?)"
        elif fail > 0:
            verdict = "FAIL"
            # 실패 사유 모으기
            fail_reasons = [
                f"{pr.status.value}: {pr.detail[:60]}"
                for pr in entry["packages"]
                if pr.status.is_failure
            ]
            reason = " | ".join(fail_reasons)[:120]
        elif succ > 0:
            verdict = "OK"
            reason = "마감 완료" if any(pr.status == PackageStatus.SUCCESS for pr in entry["packages"]) else "DRY-RUN"
        else:
            verdict = "SKIP"
            reason = "이미 마감 또는 슬롯 없음"

        lines.append(f"{code:<10} {verdict:<10} {total:<8} {succ:<5} {fail:<5} {skip:<5}  {reason}")

    lines.append("-" * 78)
    lines.append(f"합계: 패키지 {overall_total} / 성공 {overall_success} / 실패 {overall_failed} / 스킵 {overall_skipped}")
    lines.append("=" * 78)

    # 실패 상세
    failures = [pr for code in summary for pr in summary[code]["packages"] if pr.status.is_failure]
    if failures:
        lines.append("")
        lines.append("[실패 상세]")
        for pr in failures:
            lines.append(f"  - {pr.product_no} ({pr.label[:40]}) → {pr.status.value}")
            lines.append(f"      {pr.detail}")
            lines.append(f"      URL: {pr.url}")

    # 화이트리스트인데 packages 0인 코드도 명시
    missing_codes = [c for c in target_codes if summary.get(c, {}).get("total", 0) == 0]
    if missing_codes:
        lines.append("")
        lines.append(f"[화이트리스트 미발견] {sorted(missing_codes)}")
        lines.append("  → productlist 에서 Manage date or session 링크를 못 찾았습니다.")
        lines.append("  → 상품이 비활성화됐거나 권한이 변경됐을 수 있어요.")

    return "\n".join(lines)


# ============================================================
# 4) 메인 진입
# ============================================================
def _close_one_region(
    region_name: str,
    port: int,
    launcher_bat: str,
    target_str: str,
    target_codes: Set[str],
    dry_run: bool,
) -> List[PackageResult]:
    """한 지역 Chrome 에서 KKDAY 마감 처리. region 정보 부착해 PackageResult 리스트 반환."""
    region_results: List[PackageResult] = []

    if not ensure_chrome(port, launcher_bat, wait_sec=10):
        LOG.warning("[%s] Chrome port %d 죽음 - skip", region_name, port)
        region_results.append(PackageResult(
            product_no="*", package_id="*", package_option_id="*",
            label=f"(region {region_name})",
            region=region_name,
            status=PackageStatus.EXCEPTION,
            detail=f"Chrome port {port} 살아있지 않음 ({launcher_bat} 먼저 실행)",
        ))
        return region_results

    try:
        browser, context, page = connect_and_setup(port)
    except Exception as e:
        region_results.append(PackageResult(
            product_no="*", package_id="*", package_option_id="*",
            label=f"(region {region_name})",
            region=region_name,
            status=PackageStatus.EXCEPTION,
            detail=f"connect 실패 (port {port}): {e}",
        ))
        return region_results

    # 같은 Chrome 에 KLOOK / GG 탭도 있을 수 있어 KKDAY 탭 찾거나 새로 만든다.
    # 분할 모드 (forward/backward 또는 quarter 1~4) 면 같은 Chrome 안에서
    # 여러 subprocess 가 동시 작업하므로 자기 전용 새 탭을 강제로 만들어야 함.
    direction = os.environ.get("KKDAY_DIRECTION", "").strip().lower()
    quarter = os.environ.get("KKDAY_QUARTER", "").strip()
    is_split = direction in ("forward", "backward") or quarter in ("1", "2", "3", "4")
    if is_split:
        kkday_page = context.new_page()
        worker_label = f"q{quarter}" if quarter else direction
        LOG.info("[%s/%s] 전용 새 탭 생성 (parallel worker)", region_name, worker_label)
        # 동시 productlist 페이지네이션 race 방지: q 번호가 클수록 더 늦게 시작
        import time as _t
        if quarter:
            _t.sleep((int(quarter) - 1) * 1.0)  # q1=0s, q2=1s, q3=2s, q4=3s stagger
        elif direction == "backward":
            _t.sleep(2.0)
    else:
        kkday_page = None
        for pg in context.pages:
            try:
                if "scm.kkday.com" in (pg.url or "") or "kkday.com" in (pg.url or ""):
                    kkday_page = pg
                    break
            except Exception:
                pass
        if kkday_page is None:
            kkday_page = context.new_page()
    page = kkday_page
    try:
        page.bring_to_front()
    except Exception:
        pass

    try:
        # discover-once-share: 환경변수로 미리 수집된 파일이 있으면 거기서 읽기
        # → 4-8개 worker 가 동시에 productlist 페이지네이션 race 하는 문제 제거
        import json as _json
        discover_file = os.environ.get("KKDAY_DISCOVER_FILE", "").strip()
        if discover_file and os.path.exists(discover_file):
            with open(discover_file, "r", encoding="utf-8") as _f:
                _data = _json.load(_f)
            all_packages = [PackagePage(
                product_no=d["product_no"],
                package_id=d["package_id"],
                package_option_id=d["package_option_id"],
                label=d.get("label", f"product_{d['product_no']}"),
            ) for d in _data]
            LOG.info("[%s] Discover 파일에서 %d 패키지 로드 (race 없음): %s",
                     region_name, len(all_packages), discover_file)
        else:
            all_packages = discover_packages(page)
        packages = filter_by_target_codes(all_packages, target_codes)

        if not packages:
            LOG.info("[%s] 처리 대상 패키지 0개", region_name)
            return region_results

        # 분할: KKDAY_QUARTER=1/2/3/4 (4분할) 우선, 없으면 KKDAY_DIRECTION (2분할)
        # 4분할: q1=앞→1/4 forward, q2=반→1/4 reverse, q3=반→3/4 forward, q4=끝→3/4 reverse
        # [v2] 진행률 표시를 글로벌 인덱스 기준으로 (각 worker 가 (1/183), (2/183)... 처럼 표시)
        total = len(packages)
        # (global_idx, pkg) 튜플 리스트 만들어서 슬라이싱
        indexed = list(enumerate(packages))
        quarter = os.environ.get("KKDAY_QUARTER", "").strip()
        direction = os.environ.get("KKDAY_DIRECTION", "").strip().lower()
        if quarter in ("1", "2", "3", "4"):
            q1 = total // 4
            q2 = total // 2
            q3 = (3 * total) // 4
            if quarter == "1":
                my_indexed = indexed[:q1]
                LOG.info("[%s] quarter=1 → 앞→1/4 forward %d/%d 처리", region_name, len(my_indexed), total)
            elif quarter == "2":
                my_indexed = list(reversed(indexed[q1:q2]))
                LOG.info("[%s] quarter=2 → 반→1/4 reverse %d/%d 처리", region_name, len(my_indexed), total)
            elif quarter == "3":
                my_indexed = indexed[q2:q3]
                LOG.info("[%s] quarter=3 → 반→3/4 forward %d/%d 처리", region_name, len(my_indexed), total)
            else:  # "4"
                my_indexed = list(reversed(indexed[q3:]))
                LOG.info("[%s] quarter=4 → 끝→3/4 reverse %d/%d 처리", region_name, len(my_indexed), total)
        elif direction == "forward":
            half = total // 2
            my_indexed = indexed[:half]
            LOG.info("[%s] direction=forward → 앞 %d/%d 처리", region_name, len(my_indexed), total)
        elif direction == "backward":
            half = total // 2
            my_indexed = list(reversed(indexed[half:]))
            LOG.info("[%s] direction=backward → 뒤 %d/%d (역순) 처리", region_name, len(my_indexed), total)
        else:
            my_indexed = indexed

        # ==============================================================
        # Work stealing 지원: KKDAY_CLAIM_DIR 환경변수가 디렉토리 가리키면
        # 정적 slice 대신 원자적 claim 으로 동적 부하 분산.
        # quarter 는 "starting index hint" 로만 사용 (초기 부하 분산).
        # 자기 quarter 끝나면 다른 worker 슬라이스 자동으로 도와줌.
        # 원자성: os.open(O_CREAT|O_EXCL) - filesystem race 안전.
        # ==============================================================
        claim_dir = os.environ.get("KKDAY_CLAIM_DIR", "").strip()
        if claim_dir and os.path.isdir(claim_dir):
            LOG.info("[%s] Work-stealing 모드: claim_dir=%s", region_name, claim_dir)
            # quarter 기반 starting offset (초기 분산)
            try:
                _q_int = int(quarter) if quarter in ("1", "2", "3", "4") else 1
            except Exception:
                _q_int = 1
            start_idx = ((_q_int - 1) * total) // 4
            processed_count = 0
            stolen_count = 0  # 내 quarter 밖에서 가져온 것
            # 내 quarter 범위
            my_q_start = ((_q_int - 1) * total) // 4
            my_q_end = (_q_int * total) // 4 if _q_int < 4 else total

            for offset in range(total):
                idx = (start_idx + offset) % total
                pkg = packages[idx]
                claim_path = os.path.join(claim_dir, f"claim_{idx:04d}.marker")
                # 원자적 claim 시도
                try:
                    _fd = os.open(claim_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                    os.write(_fd, f"q{_q_int}".encode())
                    os.close(_fd)
                except FileExistsError:
                    continue  # 다른 worker 가 이미 가져감
                except Exception as _ce:
                    LOG.warning("[%s] claim 파일 생성 실패 idx=%d: %s", region_name, idx, _ce)
                    continue

                # 클레임 성공 → 처리
                processed_count += 1
                is_stolen = not (my_q_start <= idx < my_q_end)
                if is_stolen:
                    stolen_count += 1

                try:
                    pr = close_one_package(page, pkg, target_str, dry_run)
                except Exception as e:
                    pr = PackageResult(
                        product_no=pkg.product_no,
                        package_id=pkg.package_id,
                        package_option_id=pkg.package_option_id,
                        label=pkg.label,
                        status=PackageStatus.EXCEPTION,
                        detail=f"미처리 예외: {str(e)[:140]}",
                    )
                pr.region = region_name
                region_results.append(pr)
                LOG.info("[%s] (%d/%d) [%s] [%s] %s | %s | rows_before=%d rows_after=%d | %s | %.1fs",
                         region_name, processed_count, total,
                         "STEAL" if is_stolen else "OWN",
                         f"{pr.product_no}/{pr.package_id}/{pr.package_option_id}", pr.label[:30],
                         pr.status.value,
                         pr.rows_before, pr.rows_after,
                         pr.detail[:80],
                         pr.elapsed_sec)
                # 완료 표시: claim 파일을 그대로 두면 영구 마커가 됨 (재처리 방지).
                # 모니터링용으로 done 파일을 별도 생성 (rename 아님 - rename 시 claim 사라져서 다른 워커가 재claim 가능).
                try:
                    with open(os.path.join(claim_dir, f"done_{idx:04d}.marker"), "w") as _df:
                        _df.write(f"q{_q_int}")
                except Exception:
                    pass
            LOG.info("[%s] Work-stealing 완료: 처리=%d (own=%d, stolen=%d)",
                     region_name, processed_count, processed_count - stolen_count, stolen_count)
        else:
            # 기존 정적 slice 흐름 (claim_dir 없으면 fallback)
            for local_i, (global_idx, pkg) in enumerate(my_indexed, 1):
                try:
                    pr = close_one_package(page, pkg, target_str, dry_run)
                except Exception as e:
                    pr = PackageResult(
                        product_no=pkg.product_no,
                        package_id=pkg.package_id,
                        package_option_id=pkg.package_option_id,
                        label=pkg.label,
                        status=PackageStatus.EXCEPTION,
                        detail=f"미처리 예외: {str(e)[:140]}",
                    )
                pr.region = region_name
                region_results.append(pr)
                LOG.info("[%s] (%d/%d) [%s] %s | %s | rows_before=%d rows_after=%d | %s | %.1fs",
                         region_name, local_i, total,
                         f"{pr.product_no}/{pr.package_id}/{pr.package_option_id}", pr.label[:30],
                         pr.status.value,
                         pr.rows_before, pr.rows_after,
                         pr.detail[:80],
                         pr.elapsed_sec)

        # ==============================================================
        # End-of-run 재시도: 실패한 패키지 모아서 fresh page 로 한번 더 시도
        # 일시적인 UI race / 페이지 로딩 지연으로 인한 실패가 1회 재시도면
        # 회복되는 경우가 많음. SUCCESS/ALREADY_CLOSED/SKIP 은 재시도 안 함.
        # ==============================================================
        RETRY_STATUSES = {
            PackageStatus.CEASED_BUTTON_FAILED,
            PackageStatus.SELECT_ALL_FAILED,
            PackageStatus.CONFIRM_FAILED,
            PackageStatus.VERIFY_FAILED,
            PackageStatus.NAVIGATION_FAILED,
            PackageStatus.DATE_INPUT_FAILED,  # 날짜입력 클릭 일시 지연 → 재시도로 회복
            PackageStatus.SEARCH_FAILED,      # Search 클릭 일시 지연 → 재시도로 회복
        }
        retry_targets = [(i, pr) for i, pr in enumerate(region_results) if pr.status in RETRY_STATUSES]
        if retry_targets and not dry_run:
            LOG.info("[%s] End-of-run 재시도: %d개 실패 패키지", region_name, len(retry_targets))
            # fresh page 생성 → 기존 page 상태 오염 회피
            try:
                retry_page = context.new_page()
                try:
                    retry_page.bring_to_front()
                except Exception:
                    pass
            except Exception as e:
                LOG.warning("[%s] 재시도 page 생성 실패 → skip: %s", region_name, e)
                retry_page = None

            if retry_page:
                recovered = 0
                for retry_idx, (orig_idx, old_pr) in enumerate(retry_targets, 1):
                    # 동일한 PackagePage 재구성 → close_one_package 재호출
                    retry_pkg = PackagePage(
                        product_no=old_pr.product_no,
                        package_id=old_pr.package_id,
                        package_option_id=old_pr.package_option_id,
                        label=old_pr.label,
                    )
                    try:
                        new_pr = close_one_package(retry_page, retry_pkg, target_str, dry_run)
                    except Exception as e:
                        new_pr = PackageResult(
                            product_no=retry_pkg.product_no,
                            package_id=retry_pkg.package_id,
                            package_option_id=retry_pkg.package_option_id,
                            label=retry_pkg.label,
                            status=PackageStatus.EXCEPTION,
                            detail=f"재시도 예외: {str(e)[:140]}",
                        )
                    new_pr.region = region_name
                    # 재시도가 더 좋아진 경우만 갱신.
                    # ★ 성공 위장 방지: 직전 실패가 '열린 행을 봤는데 못 닫음' 계열이면
                    #   (CONFIRM/VERIFY/SELECT_ALL/CEASED), 재시도에서 ALREADY_CLOSED(=0행)로
                    #   나와도 '실제로 닫아서'가 아니라 렌더 지연으로 0행을 오독했을 수 있음.
                    #   work-stealing 상 다른 워커가 대신 닫아줄 일도 없으므로,
                    #   이 경우엔 실제 마감을 수행한 SUCCESS 만 회복으로 인정하고
                    #   ALREADY_CLOSED 는 원래 실패 상태로 유지한다(열린 채 '성공' 위장 차단).
                    SAW_OPEN_ROWS = {
                        PackageStatus.CONFIRM_FAILED,
                        PackageStatus.VERIFY_FAILED,
                        PackageStatus.SELECT_ALL_FAILED,
                        PackageStatus.CEASED_BUTTON_FAILED,
                    }
                    accept = (new_pr.status == PackageStatus.SUCCESS) or (
                        new_pr.status == PackageStatus.ALREADY_CLOSED
                        and old_pr.status not in SAW_OPEN_ROWS
                    )
                    if accept:
                        region_results[orig_idx] = new_pr
                        recovered += 1
                        LOG.info("[%s] (재시도 %d/%d) [%s] %s | 회복 → %s",
                                 region_name, retry_idx, len(retry_targets),
                                 new_pr.product_no, old_pr.status.value, new_pr.status.value)
                    elif new_pr.status == PackageStatus.ALREADY_CLOSED and old_pr.status in SAW_OPEN_ROWS:
                        LOG.warning("[%s] (재시도 %d/%d) [%s] %s | 재시도서 0행(ALREADY_CLOSED)이나 "
                                    "'열린행 못닫음' 이력 → 실제 마감 아님으로 판단, 실패 유지(수동 확인 필요)",
                                    region_name, retry_idx, len(retry_targets),
                                    new_pr.product_no, old_pr.status.value)
                    else:
                        LOG.info("[%s] (재시도 %d/%d) [%s] %s | 여전히 %s",
                                 region_name, retry_idx, len(retry_targets),
                                 new_pr.product_no, old_pr.status.value, new_pr.status.value)
                LOG.info("[%s] End-of-run 재시도 완료: %d/%d 회복",
                         region_name, recovered, len(retry_targets))
                try:
                    retry_page.close()
                except Exception:
                    pass
    except Exception as e:
        LOG.exception("[%s] 치명 오류: %s", region_name, e)
        region_results.append(PackageResult(
            product_no="*", package_id="*", package_option_id="*",
            label=f"(region {region_name} 전역)",
            region=region_name,
            status=PackageStatus.EXCEPTION,
            detail=f"치명: {str(e)[:140]}",
        ))
    finally:
        # P0-2: 분할 worker 가 만든 전용 탭만 닫는다 (공용 KKDAY 탭은 그대로 둔다)
        if is_split:
            close_worker_page(page, f"KKDAY/{region_name}/{quarter or direction}")

    return region_results


def run_close(target_date: Optional[date] = None, dry_run: bool = False) -> Result:
    target = target_date or (datetime.now().date() + timedelta(days=1))
    target_str = f"{target.year:04d}/{target.month:02d}/{target.day:02d}"
    target_codes = get_target_codes()

    # 지역 필터 (환경변수 KKDAY_REGIONS 로 일부 지역만 돌릴 수 있음)
    region_env = os.environ.get("KKDAY_REGIONS", "").strip()
    if region_env:
        wanted = {r.strip().upper() for r in region_env.split(",") if r.strip()}
        regions_to_run = [r for r in REGIONS if r[0] in wanted]
        LOG.info("KKDAY_REGIONS 필터: %s", sorted(wanted))
    else:
        regions_to_run = list(REGIONS)

    LOG.info("KKDAY 마감 시작 | target=%s | dry_run=%s | whitelist=%d개 | regions=%s",
             target_str, dry_run, len(target_codes),
             [r[0] for r in regions_to_run])

    package_results: List[PackageResult] = []

    # 각 지역 순차 처리
    for region_name, port, launcher_bat in regions_to_run:
        try:
            rs = _close_one_region(
                region_name, port, launcher_bat,
                target_str, target_codes, dry_run,
            )
        except Exception as e:
            LOG.exception("[%s] region 처리 미처리 예외: %s", region_name, e)
            rs = [PackageResult(
                product_no="*", package_id="*", package_option_id="*",
                label=f"(region {region_name})",
                region=region_name,
                status=PackageStatus.EXCEPTION,
                detail=f"region 미처리 예외: {str(e)[:140]}",
            )]
        package_results.extend(rs)

    # === 상품 코드별 요약 ===
    summary = build_summary_by_product(package_results, target_codes)
    table_text = render_summary_table(summary, target_codes, target_str)

    # 콘솔/로거 출력
    print()
    print(table_text)
    LOG.info("\n%s", table_text)

    # 파일 저장
    today_str = datetime.now().strftime("%Y-%m-%d")
    try:
        # P0-3: 4지역 x 4분할 = 16 worker 가 같은 파일에 쓰면 마지막 하나만 남는다.
        #   (2026-08-21 요약파일은 55패키지/성공 10인데 전체 집계는 성공 42 였다 =
        #    로그가 실제와 안 맞는 상태) worker 단위로 파일을 쪼갠다.
        _wsuffix = "_".join(x for x in [
            os.environ.get("KKDAY_REGIONS", "").strip().replace(",", "-"),
            (f"q{os.environ.get('KKDAY_QUARTER','').strip()}"
             if os.environ.get("KKDAY_QUARTER", "").strip() else
             os.environ.get("KKDAY_DIRECTION", "").strip()),
        ] if x)
        summary_path = LOGS_DIR / (
            f"kkday_summary_{today_str}{('_' + _wsuffix) if _wsuffix else ''}.txt")
        summary_path.write_text(table_text, encoding="utf-8")
        LOG.info("요약 파일 저장: %s", summary_path)
    except Exception as e:
        LOG.warning("요약 파일 저장 실패: %s", e)

    # Result 변환
    success = sum(1 for pr in package_results if pr.status.is_success)
    skipped = sum(1 for pr in package_results if pr.status.is_skip)
    failed = sum(1 for pr in package_results if pr.status.is_failure)

    # Result 변환 (run_close 결말 — 어떤 errors 를 모아서 Result 반환)
    errors_list: List[str] = []
    for code, entry in summary.items():
        if entry["failed"] > 0:
            # productNo 만으론 어느 패키지인지 알 수 없어 packageId/optionId + URL 까지 포함.
            for pr in entry["packages"]:
                if pr.status.is_failure:
                    errors_list.append(
                        f"[{pr.product_no}/{pr.package_id}/{pr.package_option_id}] "
                        f"{pr.status.value}: {pr.detail[:80]} | {pr.url}"
                    )
    missing = [c for c in target_codes if summary.get(c, {}).get("total", 0) == 0]
    if missing:
        errors_list.append(f"화이트리스트 미발견: {sorted(missing)}")

    return Result(
        agency="KKDAY",
        success=success, failed=failed, skipped=skipped,
        errors=errors_list[:20],
    )


# ============================================================
# CLI (subprocess 실행용)
# ============================================================
if __name__ == "__main__":
    import argparse
    import json as _json
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--date", default=None, help="YYYY-MM-DD")
    ap.add_argument("--region", default=None, help="KOREA/JAPAN/AUSTRALIA/UK")
    ap.add_argument("--direction", default=None, help="forward|backward (2분할 사용 시)")
    ap.add_argument("--quarter", default=None, help="1|2|3|4 (4분할 사용 시, direction 보다 우선)")
    ap.add_argument("--mode", default="auto", choices=["auto", "discover"],
                    help="auto=원래 흐름 (discover+process), discover=수집만 하고 JSON 으로 저장 후 종료")
    ap.add_argument("--output", default=None,
                    help="--mode discover 일 때 결과 JSON 파일 경로")
    ap.add_argument("--discover-file", default=None,
                    help="미리 수집한 패키지 JSON 파일 경로. 지정 시 worker 들이 discover skip")
    ap.add_argument("--claim-dir", default=None,
                    help="Work-stealing claim 디렉토리. 지정 시 정적 slice 대신 원자적 claim 사용")
    args = ap.parse_args()

    if args.region:
        os.environ["KKDAY_REGIONS"] = args.region.upper()
    if args.direction:
        os.environ["KKDAY_DIRECTION"] = args.direction.lower()
    if args.quarter:
        os.environ["KKDAY_QUARTER"] = str(args.quarter).strip()
    if args.discover_file:
        os.environ["KKDAY_DISCOVER_FILE"] = args.discover_file
    if args.claim_dir:
        os.environ["KKDAY_CLAIM_DIR"] = args.claim_dir

    tgt = None
    if args.date:
        tgt = datetime.strptime(args.date, "%Y-%m-%d").date()

    # ==========================================================
    # MODE: discover (수집만 + JSON 저장 후 종료)
    # ==========================================================
    if args.mode == "discover":
        if not args.region:
            LOG.error("--mode discover requires --region")
            sys.exit(2)
        if not args.output:
            LOG.error("--mode discover requires --output")
            sys.exit(2)

        port_map = {r[0]: (r[1], r[2]) for r in REGIONS}
        if args.region.upper() not in port_map:
            LOG.error("Unknown region: %s", args.region)
            sys.exit(2)
        port, launcher_bat = port_map[args.region.upper()]

        if not ensure_chrome(port, launcher_bat, wait_sec=10):
            LOG.error("Chrome port %d not alive", port)
            sys.exit(3)

        try:
            browser, context, page = connect_and_setup(port)
        except Exception as e:
            LOG.error("connect failed (port %d): %s", port, e)
            sys.exit(3)

        discover_page = context.new_page()
        try:
            discover_page.bring_to_front()
        except Exception:
            pass

        try:
            packages = discover_packages(discover_page)
            data = [{
                "product_no": p.product_no,
                "package_id": p.package_id,
                "package_option_id": p.package_option_id,
                "label": p.label,
            } for p in packages]
            out_path = args.output
            os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
            with open(out_path, "w", encoding="utf-8") as f:
                _json.dump(data, f, ensure_ascii=False, indent=2)
            LOG.info("[%s] Discover done: %d packages -> %s",
                     args.region.upper(), len(packages), out_path)
            print(f"[KKDAY/{args.region.upper()}] discover_success={len(packages)}")
            sys.exit(0)
        except Exception as e:
            LOG.exception("Discover failed: %s", e)
            print(f"[KKDAY/{args.region.upper()}] discover_failed: {e}")
            sys.exit(4)
        finally:
            try:
                discover_page.close()
            except Exception:
                pass

    # ==========================================================
    # MODE: auto (original flow - discover + process)
    # ==========================================================
    r = run_close(target_date=tgt, dry_run=args.dry_run)
    suffix = ""
    if args.region:
        suffix += "/" + args.region
    if args.quarter:
        suffix += "/q" + str(args.quarter)
    elif args.direction:
        suffix += "/" + args.direction
    print("\n[KKDAY" + suffix + "] success=" + str(r["success"]) + " failed=" + str(r["failed"]) + " skipped=" + str(r["skipped"]))
    if r["errors"]:
        print("Errors:")
        for e in r["errors"][:20]:
            print("  -", e)