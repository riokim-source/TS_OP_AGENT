"""
Viator (VI) 마감 봇.

!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
!! 절대 안전 규칙: "Not operating" 은 절대 누르지 않는다.   !!
!! - "Not operating" = cancellation 의미                     !!
!! - 기존 예약까지 취소될 위험이 있어 사용 금지              !!
!! - 이 봇은 오직 "Sold out" 만 클릭한다                     !!
!! - 클릭 직전 텍스트 검증 가드 + 셀렉터 가드                !!
!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!

흐름:
1) start_chrome_global.bat 가 띄운 port=9230 Chrome 에 attach (MRT 와 같은 Chrome 공유)
2) https://supplier.viator.com/availability/ 진입
3) target 날짜 (기본: 내일) 를 picker 에서 선택
4) Select products 드롭다운에서 모든 product 코드 수집
5) 각 product 별:
   - Clear all → 해당 상품만 체크 → Apply
   - target 섹션의 'Sold out' 링크 있으면 → 안전가드 검증 → 클릭
   - 없으면 skip (이미 마감 또는 슬롯 없음)
6) Result 반환

사용:
    from vi import run_close
    result = run_close(target_date)  # datetime.date 또는 None(=내일)
"""

from __future__ import annotations

import sys as _sys_init
try:
    _sys_init.stdout.reconfigure(encoding="utf-8", errors="replace")
    _sys_init.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

# 패키지 외부에서도 실행되도록 sys.path 보강
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



LOG = get_agency_logger("VI")

# 포트는 hub 라우팅이 단일 기준. 없으면 9530 (라스트미닛 전용 대역).
PORT = _routed_port("KOREA", "VI", 9530)
LAUNCHER_BAT = "start_chrome_global.bat"
AVAILABILITY_URL = "https://supplier.viator.com/availability/"

WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def header_text_for(d: date) -> str:
    """예: 2026-05-21 -> 'Thu, May 21'"""
    return f"{WEEKDAYS[d.weekday()]}, {MONTHS[d.month - 1]} {d.day}"


def aria_label_for(d: date) -> str:
    """캘린더 gridcell aria-label 형식. 예: 'Thu May 21 2026'"""
    return datetime(d.year, d.month, d.day).strftime("%a %b %d %Y")


# ============================================================
# 1. 날짜 picker
# ============================================================
def open_date_picker(page: Page) -> None:
    page.evaluate("window.scrollTo(0, 0)")
    picker = page.evaluate_handle(
        """() => Array.from(document.querySelectorAll('input'))
            .find(i => {
                const r = i.getBoundingClientRect();
                return r.width > 0 && r.height > 0 &&
                       /\\b\\w+ \\d+, \\d{4}\\b/.test(i.value || '');
            })"""
    )
    el = picker.as_element()
    if el is None:
        raise RuntimeError("Date picker input 을 찾지 못했습니다")
    el.click()
    page.wait_for_timeout(700)


def pick_date(page: Page, target: date) -> None:
    """target 날짜 셀(aria-label='Thu May 21 2026') 클릭. 필요시 다음달 이동.

    [v2] 월말~월초 케이스 (예: 06-30 → 07-01) 안정성 보강:
      1. 셀 visible 못 봐도 enabled 면 그냥 클릭 시도 (옆달 grayed-out 케이스)
      2. Next 버튼 셀렉터 다양화 (Viator UI 가끔 바뀜)
      3. Next 클릭 후 wait 300ms → 700ms (월 전환 애니메이션 대기)
      4. tabindex/aria-disabled 도 체크
    """
    open_date_picker(page)
    label = aria_label_for(target)

    # Next 버튼 후보 셀렉터들 (위에서 아래로 시도)
    next_selectors = [
        "button[aria-label*='Next month' i]",
        "button[aria-label*='next month' i]",
        "button[aria-label*='Next' i]",
        "[role='button'][aria-label*='Next' i]",
        "button[data-testid*='next' i]",
        "button[class*='next' i]:not([aria-disabled='true'])",
        "svg[aria-label*='Next' i]",
    ]

    def _click_next() -> bool:
        for sel in next_selectors:
            loc = page.locator(sel).first
            try:
                if loc.count() == 0:
                    continue
                # aria-disabled 체크 (월 끝까지 가면 disabled)
                if loc.get_attribute("aria-disabled") == "true":
                    continue
                loc.click(timeout=2000)
                return True
            except Exception:
                continue
        return False

    for attempt in range(15):  # 12 → 15 (월 경계 여유)
        # Viator 가 react-day-picker(rdp) 로 변경됨: aria-label 이 gridcell(td) 이 아니라
        # 그 안의 button.rdp-day_button 에 붙음. 신규 UI(button) 우선 + 구 UI(gridcell) fallback.
        cell = page.locator(
            f"button[aria-label='{label}'], [role='gridcell'][aria-label='{label}']"
        )
        if cell.count() > 0:
            try:
                first_cell = cell.first
                # is_visible 못 봐도 일단 click 시도 — Viator 가 grayed-out 옆달 셀도 클릭 가능한 경우 있음
                aria_dis = first_cell.get_attribute("aria-disabled")
                # rdp 버튼은 disabled 속성으로 비활성 표시 → aria-disabled 와 함께 체크
                is_disabled = (aria_dis == "true") or (first_cell.get_attribute("disabled") is not None)
                if not is_disabled:
                    try:
                        first_cell.click(timeout=2000)
                        page.wait_for_timeout(900)
                        return
                    except Exception as _ce:
                        # 클릭 실패 → 일반 fallback (JS click)
                        try:
                            first_cell.evaluate("el => el.click()")
                            page.wait_for_timeout(900)
                            return
                        except Exception:
                            pass
            except Exception:
                pass

        # 셀 못 찾음 → 다음 달로 이동
        if not _click_next():
            break
        page.wait_for_timeout(700)  # 월 전환 애니메이션 + 렌더 대기

    raise RuntimeError(f"캘린더에서 {label} 셀을 찾지 못함")


def verify_picker_value(page: Page, target: date) -> bool:
    expected = f"{MONTHS[target.month - 1]} {target.day}, {target.year}"
    val = page.evaluate(
        """() => Array.from(document.querySelectorAll('input'))
            .map(i => i.value)
            .find(v => /\\b\\w+ \\d+, \\d{4}\\b/.test(v || '')) || ''"""
    )
    return (val or "").strip() == expected


# ============================================================
# 2. 적응형 대기
# ============================================================
def wait_for_target_section(page: Page, target: date, max_wait_s: float = 6.0) -> dict:
    """
    target 헤더 + bulk 옵션 ≥2개 보일 때까지 폴링.

    중요: 로딩 race 방지.
    - 다음날 헤더 하나만으로 no_slots 종료 X (페이지 부분 렌더링 중일 가능성)
    - 다음날 + 다다음날 둘 다 보이는데 target 만 없으면 그때 "확실히 운영X" 로 빠른 종료
    - 그 외 케이스는 max_wait_s 까지 폴링

    반환:
      has_target=True : target 슬롯 발견 + 옵션 수집됨
      has_target=False + reason='no_slots'        : 다음날+다다음날 보이는데 target 없음 (운영X 확정)
      has_target=False + reason='no_section'      : 어떤 헤더도 안 그려짐 (페이지 로딩 실패 등)
      has_target=True  + reason='timeout_with_target' : 헤더는 있지만 옵션 미완성
    """
    target_text = header_text_for(target)
    next_text = header_text_for(target + timedelta(days=1))
    next2_text = header_text_for(target + timedelta(days=2))
    start = time.time()
    target_seen_at = None
    no_slot_seen_at = None  # no_target+hasNext+hasNext2 조건 처음 본 시각 (안정화 대기용)
    last_snap = {"hasTarget": False, "hasNext": False, "hasNext2": False, "opts": []}

    while time.time() - start < max_wait_s:
        snap = page.evaluate(
            """({tt, nt, nt2}) => {
                const leafs = Array.from(document.querySelectorAll('*')).filter(el => {
                    const t = (el.textContent || '').trim();
                    return el.children.length === 0 &&
                           /^(Mon|Tue|Wed|Thu|Fri|Sat|Sun),\\s+\\w+\\s+\\d+$/.test(t);
                });
                const headers = leafs.map(h => h.textContent.trim());
                const hasTarget = headers.includes(tt);
                const hasNext = headers.includes(nt);
                const hasNext2 = headers.includes(nt2);
                let opts = [];
                if (hasTarget) {
                    const el = leafs.find(h => h.textContent.trim() === tt);
                    const heading = el.closest('[class*="heading"]') || el.parentElement;
                    opts = Array.from(heading.querySelectorAll('a, button'))
                        .map(a => (a.textContent||'').trim()).filter(Boolean);
                }
                return {hasTarget, hasNext, hasNext2, opts};
            }""",
            {"tt": target_text, "nt": next_text, "nt2": next2_text},
        )
        last_snap = snap

        if snap["hasTarget"] and target_seen_at is None:
            target_seen_at = time.time()

        # 1) target + Sold out → 즉시 반환 (마감 가능)
        if snap["hasTarget"] and "Sold out" in snap["opts"]:
            return {"has_target": True, "opts": snap["opts"], "elapsed": time.time() - start}

        # 2) target + 옵션 ≥ 2, 0.5초 stabilize 후 반환
        if snap["hasTarget"] and len(snap["opts"]) >= 2:
            if target_seen_at and time.time() - target_seen_at >= 0.5:
                return {"has_target": True, "opts": snap["opts"], "elapsed": time.time() - start}

        # 3) target 없는데 다음날 + 다다음날 둘 다 보임 → 운영X 후보
        # 단, 1.5초 이상 지속되어야 확정 (페이지 부분 렌더링 race 방지).
        # 그 사이에 target 이 나타나면 reset.
        if not snap["hasTarget"] and snap["hasNext"] and snap["hasNext2"]:
            if no_slot_seen_at is None:
                no_slot_seen_at = time.time()
            elif time.time() - no_slot_seen_at >= 1.5:
                return {
                    "has_target": False,
                    "opts": [],
                    "elapsed": time.time() - start,
                    "reason": "no_slots",
                }
        else:
            # 조건이 깨졌으면 (target 나타남 / 다음날 사라짐 / 다다음날 사라짐) reset
            no_slot_seen_at = None

        time.sleep(0.4)

    # max_wait 초과 후 최종 판정
    if last_snap["hasTarget"]:
        return {
            "has_target": True,
            "opts": last_snap["opts"],
            "elapsed": time.time() - start,
            "reason": "timeout_with_target",
        }
    return {
        "has_target": False,
        "opts": [],
        "elapsed": time.time() - start,
        "reason": "no_slots" if last_snap["hasNext"] else "no_section",
    }


# ============================================================
# 3. Sold out 클릭 + 3중 안전 가드
# ============================================================
def find_and_click_sold_out(page: Page, target: date, dry_run: bool, _retry: int = 0) -> str:
    target_text = header_text_for(target)

    handle = page.evaluate_handle(
        f"""() => {{
            const els = Array.from(document.querySelectorAll('*')).filter(el =>
                el.children.length === 0 && (el.textContent||'').trim() === '{target_text}'
            );
            if (!els.length) return null;
            const heading = els[0].closest('[class*="heading"]') || els[0].parentElement;
            const soldOut = Array.from(heading.querySelectorAll('a, button')).find(el =>
                (el.textContent||'').trim() === 'Sold out'
            );
            return soldOut || null;
        }}"""
    )
    elem = handle.as_element()
    if elem is None:
        has = page.evaluate(
            f"""() => Array.from(document.querySelectorAll('*')).some(el =>
                el.children.length === 0 && (el.textContent||'').trim() === '{target_text}')"""
        )
        return "skip:closed" if has else "skip:no_target"

    try:
        text = elem.evaluate("el => (el.textContent || '').trim()")
    except Exception as e:
        # evaluate 시점에 이미 detach 됐다면 짧게 대기 후 재조회
        if _retry < 2 and "not attached" in str(e).lower():
            page.wait_for_timeout(700)
            return find_and_click_sold_out(page, target, dry_run, _retry=_retry + 1)
        raise
    # ==== 안전 가드 1 ====
    if text != "Sold out":
        raise RuntimeError(f"안전 가드 발동: textContent='{text}' (expected 'Sold out')")
    # ==== 안전 가드 2 ====
    if "operating" in text.lower():
        raise RuntimeError(f"안전 가드 발동: 'operating' 감지 → '{text}'")
    # ==== 안전 가드 3 ====
    try:
        in_target_section = elem.evaluate(
            f"""el => {{
                const heading = el.closest('[class*="heading"]');
                if (!heading) return false;
                return Array.from(heading.querySelectorAll('*')).some(e =>
                    e.children.length === 0 &&
                    (e.textContent||'').trim() === '{target_text}'
                );
            }}"""
        )
    except Exception as e:
        if _retry < 2 and "not attached" in str(e).lower():
            page.wait_for_timeout(700)
            return find_and_click_sold_out(page, target, dry_run, _retry=_retry + 1)
        raise
    if not in_target_section:
        raise RuntimeError(f"안전 가드 발동: 클릭 대상이 '{target_text}' 섹션 외부")

    if dry_run:
        return "DRY"

    # click 시 stale element 발생 가능 → 재조회 + 재검증 후 재클릭
    try:
        elem.click()
    except Exception as e:
        if _retry < 2 and "not attached" in str(e).lower():
            LOG.info("[VI] stale element 감지 → 재조회 재시도 (retry=%d)", _retry + 1)
            page.wait_for_timeout(800)
            return find_and_click_sold_out(page, target, dry_run, _retry=_retry + 1)
        raise
    page.wait_for_timeout(1500)
    return "CLICKED"


# ============================================================
# 4. 드롭다운 필터
# ============================================================
TRIGGER_SEL = "[class*='AvailabilityProductFilter__productAndTourGradeFilterTrigger']"


def _is_dropdown_open(page: Page) -> bool:
    """Apply 버튼이 보이면 dropdown 열린 상태로 판단."""
    try:
        btn = page.get_by_role("button", name="Apply", exact=True).first
        return btn.is_visible(timeout=300)
    except Exception:
        return False


def open_product_dropdown(page: Page, max_attempts: int = 3) -> bool:
    """드롭다운을 확실히 연다. 최대 3번 시도."""
    page.evaluate("window.scrollTo(0, 0)")
    for attempt in range(max_attempts):
        if _is_dropdown_open(page):
            return True
        try:
            page.locator(TRIGGER_SEL).first.click(timeout=2000, force=True)
        except Exception:
            # trigger 못 찾으면 JS 로 클릭 시도
            try:
                page.evaluate(
                    """() => {
                        const el = document.querySelector(
                            "[class*='AvailabilityProductFilter__productAndTourGradeFilterTrigger']"
                        );
                        if (el) el.click();
                    }"""
                )
            except Exception:
                pass
        page.wait_for_timeout(700 + attempt * 300)
        if _is_dropdown_open(page):
            return True
    return False


def clear_all_filter(page: Page) -> None:
    """dropdown 안의 'Clear all' 클릭. 드롭다운이 닫혀있으면 먼저 열고 실행."""
    if not _is_dropdown_open(page):
        open_product_dropdown(page)
    try:
        page.get_by_role("button", name="Clear all", exact=True).click(timeout=1500)
    except PWTimeoutError:
        try:
            page.get_by_text("Clear all", exact=True).click(timeout=1500)
        except PWTimeoutError:
            pass
    page.wait_for_timeout(300)


def apply_filter(page: Page) -> bool:
    """드롭다운 안 Apply 버튼 클릭. 성공 여부 반환.
    Apply 버튼은 드롭다운 안에 있어야 하므로 .dropdown--active 안에서 찾는다.
    """
    try:
        # 1차: dropdown--active 안의 Apply 버튼 (정확한 위치)
        btn_in_dd = page.locator(".dropdown--active button:has-text('Apply')").first
        btn_in_dd.wait_for(state="visible", timeout=2000)
        btn_in_dd.click(timeout=2000)
        page.wait_for_timeout(300)
        # 클릭 후 드롭다운 닫혔는지 검증
        try:
            still_open = page.locator(".dropdown--active").count() > 0
        except Exception:
            still_open = False
        if not still_open:
            LOG.info("Apply 클릭 → 드롭다운 닫힘 (정상)")
            return True
        # 드롭다운이 안 닫혔으면 한 번 더 시도
        LOG.warning("Apply 클릭 후에도 드롭다운 열려있음 - 재시도")
        btn_in_dd.click(timeout=2000, force=True)
        page.wait_for_timeout(400)
        return True
    except Exception as e:
        LOG.warning("dropdown--active 안 Apply 못 찾음: %s - role 기반 fallback", e)
        # 2차: role 기반 (기존 방식)
        try:
            page.get_by_role("button", name="Apply", exact=True).click(timeout=2000)
            page.wait_for_timeout(300)
            return True
        except Exception as e2:
            LOG.error("Apply 클릭 최종 실패: %s", e2)
            return False


def check_select_all(page: Page) -> bool:
    """드롭다운 안의 'Select all' 라벨/체크박스 클릭. 드롭다운이 닫혀있으면 먼저 열고 실행."""
    if not _is_dropdown_open(page):
        open_product_dropdown(page)
    try:
        # label[text="Select all"] 클릭 (체크박스 wrapper 라벨)
        label = page.locator("label").filter(has_text="Select all").first
        label.wait_for(state="visible", timeout=2000)
        label.click(timeout=2000, force=True)
        page.wait_for_timeout(400)
        return True
    except Exception:
        # 대체: get_by_text 로 시도
        try:
            page.get_by_text("Select all", exact=True).click(timeout=2000)
            page.wait_for_timeout(400)
            return True
        except Exception:
            return False


def get_all_product_codes(page: Page) -> list[dict]:
    return page.evaluate(
        """() => Array.from(document.querySelectorAll('input[data-automation^="product-filter-"]'))
            .map(i => ({code: i.name, label: (i.closest('label')?.textContent||'').trim().slice(0,120)}))"""
    )


def check_one_product(page: Page, code: str) -> bool:
    """드롭다운 안의 product checkbox 체크. 안 보이면 dropdown 다시 열어서 재시도."""
    sel = f"input[data-automation='product-filter-{code}']"
    cb = page.locator(sel)
    if cb.count() == 0:
        return False
    # checkbox 가 hidden 일 수 있어 (Ant Design 패턴) 라벨을 클릭하거나 force 옵션 활용
    try:
        if not cb.first.is_visible(timeout=500):
            # dropdown 안 열려있으면 재시도
            if not _is_dropdown_open(page):
                open_product_dropdown(page)
            # 그래도 invisible 이면 라벨로 클릭 시도
            label = page.locator(f"label:has({sel})").first
            if label.count() > 0:
                label.click(timeout=2000, force=True)
                page.wait_for_timeout(200)
                return True
        if not cb.first.is_checked():
            cb.first.check(timeout=2000, force=True)
        page.wait_for_timeout(150)
        return True
    except Exception:
        # 마지막 방법: JS 로 클릭
        try:
            page.evaluate(
                f"""() => {{
                    const inp = document.querySelector("{sel}");
                    if (!inp) return false;
                    inp.click();
                    inp.dispatchEvent(new Event('change', {{bubbles:true}}));
                    return inp.checked;
                }}"""
            )
            page.wait_for_timeout(150)
            return True
        except Exception:
            return False


# ============================================================
# 메인 진입
# ============================================================
# ============================================================
# 병렬화 헬퍼: per-product 처리를 함수로 추출 (worker 모드 + 재시도용)
# ============================================================
def _verify_day_truly_closed(page: Page, target: date, code: str) -> tuple:
    """
    '이미 마감' 확정 전 슬롯 레벨 2차 검증 (bulk 헤더 링크 오판 방지).

    target 날짜 헤더 ~ 다음 날짜 헤더 사이 텍스트 구간에서:
      - 'Sold out' 텍스트 발견 → 슬롯 레벨 'Mark as: ... Sold out' 링크
        (열린 슬롯에만 표시됨) → 진짜 마감 아님
      - product code 미포함 → 이전 필터 잔상(stale render) 의심 → 마감 확정 금지

    반환: (truly_closed: bool, diag: str)
    """
    try:
        tt = header_text_for(target)
        res = page.evaluate(
            """({tt, code}) => {
                const txt = document.body.innerText || '';
                const i = txt.indexOf(tt);
                if (i < 0) return {found: false};
                const rest = txt.slice(i + tt.length);
                const m = rest.search(/(Mon|Tue|Wed|Thu|Fri|Sat|Sun),\\s+\\w+\\s+\\d+/);
                const seg = m >= 0 ? rest.slice(0, m) : rest;
                const openLinks = (seg.match(/Sold out/g) || []).length;
                const codeOk = code ? seg.includes(code) : true;
                return {found: true, openLinks, codeOk};
            }""",
            {"tt": tt, "code": code},
        )
        if not res.get("found"):
            return False, "target 헤더 미발견(2차검증)"
        if not res.get("codeOk"):
            return False, f"섹션에 {code} 미포함(이전 필터 잔상 의심)"
        if res.get("openLinks", 0) > 0:
            return False, f"슬롯 레벨 'Sold out' 링크 {res['openLinks']}개(열린 슬롯 존재)"
        return True, ""
    except Exception as e:
        return False, f"2차검증 예외: {e}"


def _process_one_product(page: Page, target: date, dry_run: bool, p: dict, max_wait_s: float = 8.0) -> dict:
    """
    한 product 처리. 반환:
      {"status": "success"|"closed"|"skip"|"fail", "reason": str, "result": str}
    status:
      success - 클릭 성공 / DRY-RUN
      closed  - 이미 마감
      skip    - target 슬롯 없음 (no_section / no_slots) 또는 checkbox 없음
      fail    - 예외 / find_and_click_sold_out 실패
    """
    code = p["code"]
    try:
        open_product_dropdown(page)
        clear_all_filter(page)
        if not check_one_product(page, code):
            return {"status": "skip", "reason": "checkbox_missing", "result": ""}
        apply_filter(page)
        info = wait_for_target_section(page, target, max_wait_s=max_wait_s)
        if not info["has_target"]:
            return {"status": "skip", "reason": info.get("reason", "?"),
                    "result": "", "elapsed": info.get("elapsed", 0.0)}
        if "Sold out" not in info["opts"]:
            # 'Sold out' 없음 → 마감으로 단정하지 말 것.
            # 진짜 마감된 슬롯은 opts 에 'Available'(다시 열기)이 있음.
            # 'Available' 도 없으면 옵션이 덜 렌더된 것 → 마감 위장 방지 위해
            # closed 로 확정하지 않고 재시도 대상(opts_incomplete)으로 넘김.
            if "Available" in info["opts"]:
                # ★ bulk 헤더 링크만으로 closed 확정 금지 (48881P27 오판 사례):
                #   콜드로드/이전 필터 잔상에서 헤더가 'Available' 로 보여도
                #   슬롯 레벨엔 열린 슬롯이 남아 있을 수 있음 → 슬롯 레벨 2차 검증.
                truly, diag = _verify_day_truly_closed(page, target, code)
                if truly:
                    return {"status": "closed", "reason": "already_closed", "result": ""}
                LOG.warning("[%s] bulk 는 '이미 마감'처럼 보이나 2차검증 실패(%s) → opts_incomplete 재시도", code, diag)
                return {"status": "skip", "reason": "opts_incomplete",
                        "result": "", "elapsed": info.get("elapsed", 0.0)}
            return {"status": "skip", "reason": "opts_incomplete",
                    "result": "", "elapsed": info.get("elapsed", 0.0)}
        result = find_and_click_sold_out(page, target, dry_run)
        if result in ("CLICKED", "DRY"):
            return {"status": "success", "reason": "", "result": result}
        if result == "skip:closed":
            truly, diag = _verify_day_truly_closed(page, target, code)
            if truly:
                return {"status": "closed", "reason": "already_closed", "result": ""}
            LOG.warning("[%s] skip:closed 이나 2차검증 실패(%s) → opts_incomplete 재시도", code, diag)
            return {"status": "skip", "reason": "opts_incomplete", "result": ""}
        if result.startswith("skip:"):
            reason = result.split(":", 1)[1] if ":" in result else "unknown"
            return {"status": "skip", "reason": reason, "result": ""}
        return {"status": "fail", "reason": "click_fail", "result": result}
    except Exception as e:
        return {"status": "fail", "reason": f"exception:{e}", "result": ""}


def _vi_quarter_slice(products: list, quarter: str) -> list:
    """
    KKDAY/MRT 와 동일한 4-way 분할:
      q1 = 앞 → 1/4 forward
      q2 = 반 → 1/4 reverse (중간부터 앞으로)
      q3 = 반 → 3/4 forward (중간부터 뒤로)
      q4 = 끝 → 3/4 reverse (끝부터 앞으로)
    반환: [(global_idx, product), ...]
    """
    indexed = list(enumerate(products))
    total = len(indexed)
    half = total // 2
    if quarter == "1":
        return indexed[: half // 2 + (half % 2)] if False else indexed[: (total + 3) // 4]
    if quarter == "2":
        return list(reversed(indexed[: half]))
    if quarter == "3":
        return indexed[half:]
    if quarter == "4":
        return list(reversed(indexed[half:]))
    return indexed


def _vi_get_or_create_tab(context, force_new: bool = False) -> Page:
    """
    Viator availability 탭 가져오기.
    force_new=True: 항상 새 탭 생성 (병렬 worker 용 - 서로 간섭 방지)
    """
    if not force_new:
        for pg in context.pages:
            try:
                if "supplier.viator.com" in (pg.url or ""):
                    return pg
            except Exception:
                pass
    new_page = context.new_page()
    try:
        new_page.set_viewport_size({"width": 2560, "height": 1440})
    except Exception:
        pass
    return new_page


def _vi_navigate_and_pick(page: Page, target: date) -> None:
    """페이지 로드 + 날짜 선택 (재시도 포함)."""
    page.goto(AVAILABILITY_URL, wait_until="domcontentloaded")
    try:
        page.wait_for_load_state("networkidle", timeout=30_000)
    except PWTimeoutError:
        pass
    page.wait_for_timeout(1500)
    try:
        pick_date(page, target)
    except Exception as e:
        LOG.warning("날짜 선택 1차 실패: %s. 재시도.", e)
        pick_date(page, target)
    if not verify_picker_value(page, target):
        LOG.warning("picker 값 불일치 - 재시도")
        pick_date(page, target)


# ============================================================
# Discover 모드: STEP 1 bulk + STEP 2 SelectAll bulk + product 목록 수집
# 결과를 JSON 파일로 저장 → 4 worker 가 공유
# ============================================================
def run_discover(target_date: Optional[date], dry_run: bool, output_file: str) -> Result:
    import json as _json
    target = target_date or (datetime.now().date() + timedelta(days=1))
    LOG.info("Viator DISCOVER 시작 | target=%s | dry_run=%s | output=%s", target, dry_run, output_file)

    if not ensure_chrome(PORT, LAUNCHER_BAT, wait_sec=10):
        msg = f"Chrome on port {PORT} 가 살아있지 않습니다."
        LOG.error(msg)
        return Result(agency="VI", success=0, failed=0, skipped=0, errors=[msg])

    try:
        browser, context, page = connect_and_setup(PORT)
    except Exception as e:
        msg = f"Playwright connect 실패: {e}"
        LOG.error(msg)
        return Result(agency="VI", success=0, failed=0, skipped=0, errors=[msg])

    page = _vi_get_or_create_tab(context, force_new=False)
    try:
        page.bring_to_front()
    except Exception:
        pass

    success = closed = 0
    errors: list[str] = []
    products: list[dict] = []
    try:
        _vi_navigate_and_pick(page, target)
        info = wait_for_target_section(page, target, max_wait_s=8.0)
        LOG.info("target 섹션 로드: %s", info)

        # STEP 1 bulk
        result = find_and_click_sold_out(page, target, dry_run)
        if result == "CLICKED":
            LOG.info("[STEP 1] 기본화면 bulk 'Sold out' 클릭 완료")
            success += 1
            page.wait_for_timeout(2000)
        elif result == "DRY":
            LOG.info("[STEP 1 / DRY] 기본화면 bulk 'Sold out' 클릭 예정")
        elif result == "skip:closed":
            LOG.info("[STEP 1] 기본화면 bulk 옵션에 'Sold out' 없음 (이미 닫힘)")
            closed += 1

        # STEP 2 SelectAll + bulk
        try:
            open_product_dropdown(page)
            page.wait_for_timeout(300)
            if check_select_all(page):
                LOG.info("[STEP 2] Select all 체크 완료 → Apply 클릭")
                apply_ok = apply_filter(page)
                if apply_ok:
                    LOG.info("[STEP 2] Apply 클릭 완료, 필터 반영 대기")
                    page.wait_for_timeout(2500)
                    info2 = wait_for_target_section(page, target, max_wait_s=8.0)
                    LOG.info("[STEP 2] target 섹션 로드: %s", info2)
                    result2 = find_and_click_sold_out(page, target, dry_run)
                    if result2 == "CLICKED":
                        LOG.info("[STEP 2] Select All 후 bulk 'Sold out' 클릭 완료")
                        success += 1
                        page.wait_for_timeout(2000)
                    elif result2 == "DRY":
                        LOG.info("[STEP 2 / DRY] bulk 'Sold out' 클릭 예정")
                    elif result2 == "skip:closed":
                        LOG.info("[STEP 2] bulk 옵션에 'Sold out' 없음 (이미 닫힘)")
                        closed += 1
        except Exception as e:
            LOG.warning("[STEP 2] 예외: %s", e)

        # product 목록 수집
        open_product_dropdown(page)
        try:
            clear_all_filter(page)
        except Exception:
            pass
        products = get_all_product_codes(page)
        LOG.info("[DISCOVER] product %d 개 수집", len(products))

        # JSON 으로 저장
        try:
            with open(output_file, "w", encoding="utf-8") as f:
                _json.dump({"target": str(target), "products": products}, f, ensure_ascii=False, indent=2)
            LOG.info("[DISCOVER] %d 개 product 저장: %s", len(products), output_file)
        except Exception as e:
            errors.append(f"output 파일 저장 실패: {e}")
            LOG.error("output 파일 저장 실패: %s", e)
    except Exception as e:
        LOG.exception("[DISCOVER] 치명 오류: %s", e)
        errors.append(f"discover 치명: {e}")

    return Result(agency="VI", success=success + closed,
                  failed=0, skipped=0, errors=errors[:30])


# ============================================================
# Close 모드 (worker): JSON 읽고 자기 quarter slice 만 STEP 3 처리
# work-stealing + end-of-run 재시도 포함
# ============================================================
def run_close_workers(target_date: Optional[date], dry_run: bool,
                      discover_file: str, quarter: str = "1",
                      claim_dir: Optional[str] = None) -> Result:
    import json as _json
    import os as _os
    target = target_date or (datetime.now().date() + timedelta(days=1))
    LOG.info("Viator CLOSE-WORKER 시작 | target=%s | quarter=%s | discover=%s | claim=%s",
             target, quarter, discover_file, claim_dir)

    if not ensure_chrome(PORT, LAUNCHER_BAT, wait_sec=10):
        msg = f"Chrome on port {PORT} 가 살아있지 않습니다."
        LOG.error(msg)
        return Result(agency="VI", success=0, failed=0, skipped=0, errors=[msg])

    # discover JSON 읽기
    try:
        with open(discover_file, "r", encoding="utf-8") as f:
            data = _json.load(f)
        products = data.get("products", [])
    except Exception as e:
        msg = f"discover_file 읽기 실패: {e}"
        LOG.error(msg)
        return Result(agency="VI", success=0, failed=0, skipped=0, errors=[msg])

    total = len(products)
    LOG.info("[q%s] discover JSON 에서 %d 개 product 로드", quarter, total)
    if total == 0:
        return Result(agency="VI", success=0, failed=0, skipped=0, errors=[])

    try:
        browser, context, page = connect_and_setup(PORT)
    except Exception as e:
        msg = f"Playwright connect 실패: {e}"
        LOG.error(msg)
        return Result(agency="VI", success=0, failed=0, skipped=0, errors=[msg])

    # 각 worker 는 자기만의 새 탭 (다른 worker 와 간섭 방지)
    page = _vi_get_or_create_tab(context, force_new=True)
    try:
        page.bring_to_front()
    except Exception:
        pass

    success = failed = skipped = closed = 0
    errors: list[str] = []
    retry_targets: list = []  # [(idx, product, prev_reason), ...]

    try:
        _vi_navigate_and_pick(page, target)

        use_steal = bool(claim_dir and _os.path.isdir(claim_dir))
        if use_steal:
            LOG.info("[q%s] Work-stealing 모드: claim_dir=%s", quarter, claim_dir)
            try:
                _q_int = int(quarter) if quarter in ("1", "2", "3", "4") else 1
            except Exception:
                _q_int = 1
            start_idx = ((_q_int - 1) * total) // 4
            my_q_start = ((_q_int - 1) * total) // 4
            my_q_end = (_q_int * total) // 4 if _q_int < 4 else total

            processed = 0
            stolen = 0
            for offset in range(total):
                idx = (start_idx + offset) % total
                p = products[idx]
                claim_path = _os.path.join(claim_dir, f"claim_{idx:04d}.marker")
                try:
                    _fd = _os.open(claim_path, _os.O_CREAT | _os.O_EXCL | _os.O_WRONLY)
                    _os.write(_fd, f"q{_q_int}".encode())
                    _os.close(_fd)
                except FileExistsError:
                    continue
                except Exception as _ce:
                    LOG.warning("[q%s] claim 실패 idx=%d: %s", quarter, idx, _ce)
                    continue

                processed += 1
                is_stolen = not (my_q_start <= idx < my_q_end)
                if is_stolen:
                    stolen += 1
                tag = "STEAL" if is_stolen else "OWN"

                res = _process_one_product(page, target, dry_run, p, max_wait_s=8.0)
                _label_short = (p.get("label") or "")[:60]
                if res["status"] == "success":
                    success += 1
                    LOG.info("(%d/%d) [%s] %s | %s | %s",
                             processed, total, tag, p["code"], _label_short,
                             "CLICKED" if res["result"] == "CLICKED" else "DRY-RUN")
                elif res["status"] == "closed":
                    closed += 1
                    LOG.info("(%d/%d) [%s] %s | %s | CLOSE (이미 마감)",
                             processed, total, tag, p["code"], _label_short)
                elif res["status"] == "skip":
                    skipped += 1
                    LOG.info("(%d/%d) [%s] %s | %s | skip: target 슬롯 없음 (reason=%s, elapsed=%.1fs)",
                             processed, total, tag, p["code"], _label_short,
                             res.get("reason", "?"), res.get("elapsed", 0.0))
                    if res.get("reason") in ("no_section", "no_slots", "timeout_with_target", "opts_incomplete", "?"):
                        retry_targets.append((idx, p, res.get("reason", "?")))
                else:
                    failed += 1
                    errors.append(f"{p['code']}: {res.get('reason', '')}")
                    LOG.warning("(%d/%d) [%s] %s | %s | FAIL: %s",
                                processed, total, tag, p["code"], _label_short,
                                res.get("reason", ""))
                    retry_targets.append((idx, p, res.get("reason", "?")))

                # done 마커 (claim 그대로 둠 - 재처리 방지)
                try:
                    with open(_os.path.join(claim_dir, f"done_{idx:04d}.marker"), "w") as _df:
                        _df.write(f"q{_q_int}")
                except Exception:
                    pass

            LOG.info("[q%s] Work-stealing 완료: 처리=%d (own=%d, stolen=%d)",
                     quarter, processed, processed - stolen, stolen)
        else:
            my_indexed = _vi_quarter_slice(products, quarter)
            LOG.info("[q%s] 정적 slice %d/%d 처리", quarter, len(my_indexed), total)
            for local_i, (gidx, p) in enumerate(my_indexed, 1):
                res = _process_one_product(page, target, dry_run, p, max_wait_s=8.0)
                _label_short = (p.get("label") or "")[:60]
                if res["status"] == "success":
                    success += 1
                    LOG.info("(%d/%d) %s | %s | %s",
                             local_i, len(my_indexed), p["code"], _label_short,
                             "CLICKED" if res["result"] == "CLICKED" else "DRY-RUN")
                elif res["status"] == "closed":
                    closed += 1
                    LOG.info("(%d/%d) %s | %s | CLOSE (이미 마감)",
                             local_i, len(my_indexed), p["code"], _label_short)
                elif res["status"] == "skip":
                    skipped += 1
                    LOG.info("(%d/%d) %s | %s | skip: target 슬롯 없음 (reason=%s, elapsed=%.1fs)",
                             local_i, len(my_indexed), p["code"], _label_short,
                             res.get("reason", "?"), res.get("elapsed", 0.0))
                    if res.get("reason") in ("no_section", "no_slots", "timeout_with_target", "opts_incomplete", "?"):
                        retry_targets.append((gidx, p, res.get("reason", "?")))
                else:
                    failed += 1
                    errors.append(f"{p['code']}: {res.get('reason', '')}")
                    LOG.warning("(%d/%d) %s | %s | FAIL: %s",
                                local_i, len(my_indexed), p["code"], _label_short,
                                res.get("reason", ""))
                    retry_targets.append((gidx, p, res.get("reason", "?")))

        # End-of-run 재시도 (페이지 로딩 race / 타임아웃 회복용)
        if retry_targets and not dry_run:
            LOG.info("[q%s] End-of-run 재시도: %d 개 (no_section/no_slots/fail)",
                     quarter, len(retry_targets))
            retry_page = None
            try:
                retry_page = context.new_page()
                try:
                    retry_page.set_viewport_size({"width": 2560, "height": 1440})
                    retry_page.bring_to_front()
                except Exception:
                    pass
                _vi_navigate_and_pick(retry_page, target)
            except Exception as e:
                LOG.warning("[q%s] 재시도 page 생성 실패: %s", quarter, e)
                retry_page = None

            if retry_page:
                recovered = 0
                for ri, (orig_idx, p, prev_reason) in enumerate(retry_targets, 1):
                    res = _process_one_product(retry_page, target, dry_run, p, max_wait_s=15.0)
                    _label_short = (p.get("label") or "")[:50]
                    if res["status"] == "success":
                        success += 1
                        if prev_reason in ("no_section", "no_slots", "timeout_with_target", "opts_incomplete", "?"):
                            skipped = max(0, skipped - 1)
                        recovered += 1
                        LOG.info("[q%s] (재시도 %d/%d) %s | %s | 회복 → %s",
                                 quarter, ri, len(retry_targets), p["code"], _label_short,
                                 res["result"])
                    elif res["status"] == "closed":
                        closed += 1
                        if prev_reason in ("no_section", "no_slots", "timeout_with_target", "opts_incomplete", "?"):
                            skipped = max(0, skipped - 1)
                        recovered += 1
                        LOG.info("[q%s] (재시도 %d/%d) %s | %s | 회복 → CLOSE (이미 마감)",
                                 quarter, ri, len(retry_targets), p["code"], _label_short)
                    elif res.get("reason") == "opts_incomplete":
                        # 재시도(15초 대기) 후에도 옵션 미완성 → 마감 확정 불가.
                        # 조용히 넘기지 말고 '실패'로 표면화(열려있는데 위장 마감 방지).
                        failed += 1
                        errors.append(f"{p['code']}: opts_incomplete (마감 확정 실패, 수동 확인 필요)")
                        if prev_reason in ("no_section", "no_slots", "timeout_with_target", "opts_incomplete", "?"):
                            skipped = max(0, skipped - 1)
                        LOG.warning("[q%s] (재시도 %d/%d) %s | %s | 마감 확정 실패(옵션 미완성) → 실패 처리, 수동 확인 필요",
                                    quarter, ri, len(retry_targets), p["code"], _label_short)
                    else:
                        LOG.info("[q%s] (재시도 %d/%d) %s | %s | 여전히 %s (reason=%s)",
                                 quarter, ri, len(retry_targets), p["code"], _label_short,
                                 res["status"], res.get("reason", "?"))
                LOG.info("[q%s] End-of-run 재시도 완료: %d/%d 회복",
                         quarter, recovered, len(retry_targets))
                try:
                    retry_page.close()
                except Exception:
                    pass

        LOG.info("[q%s] Viator 결과 | 성공 %d / 마감됨 %d / 운영X·기타 %d / 실패 %d",
                 quarter, success, closed, skipped, failed)
    except Exception as e:
        LOG.exception("[q%s] 치명 오류: %s", quarter, e)
        errors.append(f"worker 치명: {e}")
    finally:
        # P0-2: 이 worker 가 만든 전용 탭을 반드시 닫는다 (탭 누수 방지)
        close_worker_page(page, f"VI/q{quarter}")

    return Result(agency="VI", success=success + closed,
                  failed=failed, skipped=skipped, errors=errors[:30])


def run_close(target_date: Optional[date] = None, dry_run: bool = False) -> Result:
    target = target_date or (datetime.now().date() + timedelta(days=1))
    LOG.info("Viator 마감 시작 | target=%s | dry_run=%s", target, dry_run)

    if not ensure_chrome(PORT, LAUNCHER_BAT, wait_sec=10):
        msg = f"Chrome on port {PORT} 가 살아있지 않습니다. {LAUNCHER_BAT} 를 먼저 실행하세요."
        LOG.error(msg)
        return Result(agency="VI", success=0, failed=0, skipped=0, errors=[msg])

    success = failed = skipped = closed = 0
    errors: list = []

    try:
        browser, context, page = connect_and_setup(PORT)
    except Exception as e:
        msg = f"Playwright connect 실패: {e}"
        LOG.error(msg)
        return Result(agency="VI", success=0, failed=0, skipped=0, errors=[msg])

    page = _vi_get_or_create_tab(context, force_new=False)
    try:
        page.bring_to_front()
    except Exception:
        pass

    try:
        _vi_navigate_and_pick(page, target)
        info = wait_for_target_section(page, target, max_wait_s=8.0)
        LOG.info("target 섹션 로드: %s", info)

        # STEP 1
        result = find_and_click_sold_out(page, target, dry_run)
        if result == "CLICKED":
            LOG.info("[STEP 1] 기본화면 bulk 'Sold out' 클릭 완료")
            success += 1
            page.wait_for_timeout(2000)
        elif result == "DRY":
            LOG.info("[STEP 1 / DRY] 기본화면 bulk 'Sold out' 클릭 예정")
        elif result == "skip:closed":
            LOG.info("[STEP 1] 기본화면 bulk 옵션에 'Sold out' 없음 (이미 닫힘)")
            closed += 1

        # STEP 2
        try:
            open_product_dropdown(page)
            page.wait_for_timeout(300)
            if check_select_all(page):
                LOG.info("[STEP 2] Select all 체크 완료 → Apply 클릭")
                apply_ok = apply_filter(page)
                if not apply_ok:
                    LOG.warning("[STEP 2] Apply 클릭 실패 - 이 단계 skip")
                else:
                    LOG.info("[STEP 2] Apply 클릭 완료, 필터 반영 대기")
                    page.wait_for_timeout(2500)
                    info2 = wait_for_target_section(page, target, max_wait_s=8.0)
                    LOG.info("[STEP 2] target 섹션 로드: %s", info2)
                    result2 = find_and_click_sold_out(page, target, dry_run)
                    if result2 == "CLICKED":
                        LOG.info("[STEP 2] Select All 후 bulk 'Sold out' 클릭 완료")
                        success += 1
                        page.wait_for_timeout(2000)
                    elif result2 == "DRY":
                        LOG.info("[STEP 2 / DRY] bulk 'Sold out' 클릭 예정")
                    elif result2 == "skip:closed":
                        LOG.info("[STEP 2] bulk 옵션에 'Sold out' 없음 (이미 닫힘)")
                        closed += 1
            else:
                LOG.warning("[STEP 2] Select all 클릭 실패 - skip")
        except Exception as e:
            LOG.warning("[STEP 2] Select All + bulk 처리 중 예외: %s", e)

        # STEP 3
        open_product_dropdown(page)
        try:
            clear_all_filter(page)
        except Exception:
            pass
        products = get_all_product_codes(page)
        LOG.info("[STEP 3] 드롭다운에서 product %d 개 발견", len(products))

        for i, p in enumerate(products, 1):
            code = p["code"]
            label = (p["label"] or "")[:60]
            try:
                res = _process_one_product(page, target, dry_run, p, max_wait_s=8.0)
                if res["status"] == "success":
                    success += 1
                    LOG.info("(%d/%d) %s | %s | %s",
                             i, len(products), code, label,
                             "CLICKED" if res["result"] == "CLICKED" else "DRY-RUN")
                elif res["status"] == "closed":
                    closed += 1
                    LOG.info("(%d/%d) %s | %s | CLOSE (이미 마감)",
                             i, len(products), code, label)
                elif res["status"] == "skip":
                    skipped += 1
                    LOG.info("(%d/%d) %s | %s | skip: target 슬롯 없음 (reason=%s, elapsed=%.1fs)",
                             i, len(products), code, label,
                             res.get("reason", "?"), res.get("elapsed", 0.0))
                else:
                    failed += 1
                    errors.append(f"{code} {label}: {res.get('reason', '')}")
                    LOG.warning("(%d/%d) %s | %s | FAIL: %s",
                                i, len(products), code, label, res.get("reason", ""))
            except Exception as _pe:
                failed += 1
                errors.append(f"{code} {label}: 예외 {_pe}")
                LOG.exception("(%d/%d) %s | %s | 예외", i, len(products), code, label)

        LOG.info("Viator 결과 | 성공 %d / 마감됨 %d / 운영X·기타 %d / 실패 %d",
                 success, closed, skipped, failed)
    except Exception as e:
        msg = f"치명 오류: {e}"
        LOG.exception(msg)
        errors.append(msg)

    return Result(agency="VI", success=success + closed,
                  failed=failed, skipped=skipped, errors=errors[:30])


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--date", default=None, help="YYYY-MM-DD")
    ap.add_argument("--mode", choices=["all", "discover", "close", "auto"], default="auto",
                    help="auto=플래그로 자동판정 / all=레거시 단일프로세스 / discover=STEP1+2+JSON / close=STEP3만 + work-stealing")
    ap.add_argument("--output", default=None, help="discover 모드: product 목록 JSON 저장 경로")
    ap.add_argument("--discover-file", default=None, help="close 모드: discover JSON 파일 경로")
    ap.add_argument("--quarter", default=None, choices=["1", "2", "3", "4"],
                    help="close 모드: 4-way 분할")
    ap.add_argument("--claim-dir", default=None,
                    help="close 모드: work-stealing claim 디렉토리")
    args = ap.parse_args()

    tgt = None
    if args.date:
        tgt = datetime.strptime(args.date, "%Y-%m-%d").date()

    # auto 모드: 플래그로 자동 판정
    effective_mode = args.mode
    if effective_mode == "auto":
        if args.output and not args.discover_file:
            effective_mode = "discover"
        elif args.discover_file:
            effective_mode = "close"
        else:
            effective_mode = "all"

    if effective_mode == "discover":
        if not args.output:
            print("[VI] discover 모드는 --output 필요", file=sys.stderr)
            sys.exit(2)
        r = run_discover(tgt, dry_run=args.dry_run, output_file=args.output)
    elif effective_mode == "close":
        if not args.discover_file:
            print("[VI] close 모드는 --discover-file 필요", file=sys.stderr)
            sys.exit(2)
        r = run_close_workers(tgt, dry_run=args.dry_run,
                              discover_file=args.discover_file,
                              quarter=args.quarter or "1",
                              claim_dir=args.claim_dir)
    else:
        r = run_close(tgt, dry_run=args.dry_run)

    print(f"\n[VI] success={r['success']} failed={r['failed']} skipped={r['skipped']}")
    if r["errors"]:
        print("Errors:")
        for e in r["errors"][:20]:
            print("  -", e)
