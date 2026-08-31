"""
GetYourGuide (GG) 마감 봇.

지역별 Chrome 통합 구조:
- 4개 지역 (KOREA/JAPAN/AUSTRALIA/UK) Chrome 에 KLOOK+GG+KKDAY 가 같이 들어있음
- GG 봇은 자기 사이트 탭(supplier.getyourguide.com) 을 찾아 동작
- TOTP 첫 로그인은 수동, 이후 세션 쿠키로 자동

동작 흐름:
  1) /manage/availability 로 이동
  2) 날짜 input 에 [target, target] 범위 입력 (반드시 from + to 둘 다)
  3) page size = 15 로 한 바퀴: select all -> update selected -> Block -> Apply -> Next
  4) Next disabled 되면 page size 를 50 으로 바꾸고 1페이지부터 동일하게 한 바퀴 더
  5) 두 바퀴 완료 후 다음 지역으로

단일 날짜만 입력하면 "그 날부터 미래 전체" 가 선택되는 위험이 있어서,
반드시 from + to 두 번 다 동일 날짜로 클릭한다.
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
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import List, Optional, Tuple

from playwright.sync_api import Page, TimeoutError as PWTimeoutError

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared.chrome_setup import connect_and_setup
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



LOG = get_agency_logger("GG")

_REGION_FALLBACK: List[Tuple[str, int, str]] = [
    ("KOREA",     9522, "start_chrome_korea.bat"),
    ("JAPAN",     9523, "start_chrome_japan.bat"),
    ("AUSTRALIA", 9524, "start_chrome_australia.bat"),
    ("UK",        9525, "start_chrome_uk.bat"),
]
# 포트는 hub 라우팅(hub/data/routing.json)이 단일 기준. 없으면 위 폴백.
REGIONS: List[Tuple[str, int, str]] = [
    (rg, _routed_port(rg, "GG", port), bat) for rg, port, bat in _REGION_FALLBACK
]
PORT = REGIONS[0][1]
LAUNCHER_BAT = REGIONS[0][2]

AVAIL_URL = "https://supplier.getyourguide.com/manage/availability"

MONTHS = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]


# ============================================================
# 탭 찾기 / 이동
# ============================================================
def find_or_open_availability_tab(context, page) -> Page:
    """supplier.getyourguide.com 탭을 찾거나 새로 열어 /manage/availability 로 이동."""
    target = None
    for pg in context.pages:
        try:
            if "supplier.getyourguide.com" in (pg.url or ""):
                target = pg
                break
        except Exception:
            continue

    if target is None:
        target = context.new_page()

    if "/manage/availability" not in (target.url or ""):
        target.goto(AVAIL_URL, wait_until="domcontentloaded", timeout=45_000)

    try:
        target.wait_for_selector('input[placeholder="From - to"]', timeout=20_000)
    except PWTimeoutError:
        LOG.warning("GG availability 페이지 로드 실패 (date input 안 보임)")

    try:
        target.bring_to_front()
    except Exception:
        pass
    return target


# ============================================================
# 날짜 picker
# ============================================================
def _open_date_picker(page: Page) -> None:
    page.locator('input[placeholder="From - to"]').first.click()
    page.wait_for_selector("#date-range_panel", timeout=5_000)


def _close_date_picker(page: Page) -> None:
    try:
        page.locator("body").click(position={"x": 10, "y": 10}, timeout=1_500)
    except Exception:
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass


def _picker_current_my(page: Page) -> Tuple[int, int]:
    """현재 패널이 가리키는 (year, month) 반환. month=1..12"""
    title = page.locator("#date-range_panel .p-datepicker-title").first.inner_text(timeout=2_000)
    parts = title.replace("\n", " ").split()
    mon_name, year_str = parts[0], parts[-1]
    month = MONTHS.index(mon_name) + 1
    year = int(year_str)
    return year, month


def _picker_navigate_to(page: Page, year: int, month: int, max_clicks: int = 36) -> None:
    for _ in range(max_clicks):
        cy, cm = _picker_current_my(page)
        if cy == year and cm == month:
            return
        delta = (year - cy) * 12 + (month - cm)
        if delta > 0:
            page.locator('#date-range_panel button[aria-label="Next Month"]').first.click()
        else:
            page.locator('#date-range_panel button[aria-label="Previous Month"]').first.click()
        page.wait_for_timeout(150)
    raise RuntimeError(f"date picker navigation failed to {year}-{month:02d}")


def _panel_is_open(page: Page) -> bool:
    """picker panel 이 현재 열려있는지."""
    try:
        return page.locator("#date-range_panel").first.is_visible(timeout=500)
    except Exception:
        return False


def _ensure_panel_open(page: Page) -> None:
    """panel 닫혔으면 다시 연다."""
    if _panel_is_open(page):
        return
    try:
        page.locator('input[placeholder="From - to"]').first.click()
        page.wait_for_selector("#date-range_panel", state="visible", timeout=4_000)
    except Exception:
        pass


def _click_day_cell(page: Page, target_d: date, *, as_range_end: bool = False) -> None:
    """target_d 에 해당하는 셀 클릭.
    as_range_end=True 면 range 종료점으로 인식되도록 hover → click 패턴 사용.
    같은 날 두 번 연속 클릭 시 PrimeReact 가 종종 deselect 로 처리하는 문제 회피.
    """
    day = target_d.day
    cells = page.locator(
        f'#date-range_panel td[aria-label="{day}"]'
        f':not(.p-datepicker-other-month):not(.p-disabled)'
    )
    n = cells.count()
    if n == 0:
        raise RuntimeError(f"day {day} cell not found in picker")
    target_cell = None
    for i in range(n):
        c = cells.nth(i)
        try:
            if c.is_visible(timeout=300):
                target_cell = c
                break
        except Exception:
            continue
    if target_cell is None:
        target_cell = cells.first

    if as_range_end:
        # range end 로 같은 셀 클릭 → hover 먼저 (PrimeReact range building 인식 유도)
        try:
            target_cell.hover()
            page.wait_for_timeout(150)
        except Exception:
            pass

    target_cell.click()
    page.wait_for_timeout(350)


def _read_date_input(page: Page) -> str:
    try:
        return page.locator('input[placeholder="From - to"]').first.input_value().strip()
    except Exception:
        return ""


def _month_variants(month: int) -> list:
    """월의 약자('Jun') + 풀네임('June') 둘 다 반환."""
    full = MONTHS[month - 1]
    return [full[:3], full]


def _date_variants(d: date) -> list:
    """한 날짜에 대해 PrimeReact 가 쓸 수 있는 포맷 후보들."""
    return [f"{m} {d.day}, {d.year}" for m in _month_variants(d.month)]


def _expected_input_value(d_from: date, d_to: date) -> str:
    """표시용. 약자 우선 (사이트가 실제로 약자를 씀)."""
    short_from = MONTHS[d_from.month - 1][:3]
    short_to = MONTHS[d_to.month - 1][:3]
    return f"{short_from} {d_from.day}, {d_from.year} - {short_to} {d_to.day}, {d_to.year}"


def _input_matches(val: str, d_from: date, d_to: date) -> bool:
    """input 값이 d_from 과 d_to 정확히 둘 다 포함하는지 검증.
    약자(Jun) 와 풀네임(June) 둘 다 인정. range 구분자 ' - ' 또는 ' – '."""
    if " - " not in val and " – " not in val:
        return False
    from_ok = any(f in val for f in _date_variants(d_from))
    to_ok = any(t in val for t in _date_variants(d_to))
    return from_ok and to_ok


def _try_fill_input(page: Page, d_from: date, d_to: date) -> bool:
    """PrimeReact input 에 'Jun 3, 2026 - Jun 3, 2026' 형태로 직접 fill 시도.
    성공 시 True, input 이 readonly 또는 fill 거부하면 False."""
    short_from = MONTHS[d_from.month - 1][:3]
    short_to = MONTHS[d_to.month - 1][:3]
    range_text = f"{short_from} {d_from.day}, {d_from.year} - {short_to} {d_to.day}, {d_to.year}"
    try:
        inp = page.locator('input[placeholder="From - to"]').first
        inp.click()
        page.wait_for_timeout(150)
        # native value setter + React state 업데이트 이벤트 발사
        ok = page.evaluate(
            """({selector, text}) => {
                const inp = document.querySelector(selector);
                if (!inp) return false;
                if (inp.readOnly || inp.disabled) return false;
                const setter = Object.getOwnPropertyDescriptor(
                    window.HTMLInputElement.prototype, 'value'
                ).set;
                setter.call(inp, text);
                inp.dispatchEvent(new Event('input', {bubbles: true}));
                inp.dispatchEvent(new Event('change', {bubbles: true}));
                return true;
            }""",
            {"selector": 'input[placeholder="From - to"]', "text": range_text},
        )
        if not ok:
            return False
        page.wait_for_timeout(200)
        # blur 로 commit
        try:
            inp.press("Tab")
        except Exception:
            try:
                page.locator("body").click(position={"x": 5, "y": 5}, timeout=1_000)
            except Exception:
                pass
        page.wait_for_timeout(400)
        return _input_matches(_read_date_input(page), d_from, d_to)
    except Exception as e:
        LOG.warning("input fill 실패: %s", e)
        return False


def _click_only_strategy(page: Page, d_from: date, d_to: date) -> None:
    """캘린더 두 번 클릭 방식. fill 이 실패한 경우의 fallback.
    같은 달이면 JS 안에서 빠르게 두 번 클릭 (panel 닫히기 전).
    """
    # input 비우고 picker 열기
    try:
        inp = page.locator('input[placeholder="From - to"]').first
        try:
            inp.fill("")
            page.wait_for_timeout(100)
        except Exception:
            pass
    except Exception:
        pass

    _open_date_picker(page)
    _picker_navigate_to(page, d_from.year, d_from.month)

    same_month = (d_from.year == d_to.year and d_from.month == d_to.month)
    if same_month:
        # JS evaluate 안에서 setTimeout 으로 빠르게 두 번 클릭 → React 가 batch 처리할 가능성 높음
        # panel 이 닫혀도 JS 안에서 즉시 재오픈 + 두번째 클릭
        result = page.evaluate(
            """async ({day_from, day_to}) => {
                const sleep = ms => new Promise(r => setTimeout(r, ms));
                const findCell = (day) => {
                    const all = document.querySelectorAll(
                        `#date-range_panel td[aria-label="${day}"]`
                    );
                    return Array.from(all).find(c =>
                        !c.classList.contains('p-datepicker-other-month') &&
                        !c.classList.contains('p-disabled') &&
                        c.offsetParent !== null
                    );
                };
                const fireClick = (cell) => {
                    // td 안의 span 이 진짜 클릭 핸들러 가지는 경우 많음
                    const target = cell.querySelector('span') || cell;
                    target.click();
                };

                const c1 = findCell(day_from);
                if (!c1) return {ok: false, msg: 'cell1 not found'};
                fireClick(c1);

                // React state 업데이트 대기. 너무 짧으면 1번 클릭 무시, 너무 길면 panel 닫힘.
                await sleep(140);

                let c2 = findCell(day_to);
                if (!c2) {
                    // panel 닫혔을 가능성 → 다시 열기
                    const inp = document.querySelector('input[placeholder="From - to"]');
                    if (inp) inp.click();
                    await sleep(250);
                    c2 = findCell(day_to);
                    if (!c2) return {ok: false, msg: 'cell2 not found after reopen'};
                }
                fireClick(c2);
                await sleep(200);
                return {ok: true, msg: 'two clicks fired'};
            }""",
            {"day_from": d_from.day, "day_to": d_to.day}
        )
        LOG.info("rapid click 결과: %s", result)
    else:
        # 다른 달: navigate 가 필요해서 빠른 클릭 불가능 → 일반 방식
        _click_day_cell(page, d_from)
        if not _panel_is_open(page):
            LOG.info("1차 클릭 후 panel 닫힘 - 재오픈")
            _open_date_picker(page)
        _picker_navigate_to(page, d_to.year, d_to.month)
        _click_day_cell(page, d_to, as_range_end=False)

    _close_date_picker(page)
    page.wait_for_timeout(350)


def select_date_range(page: Page, d_from: date, d_to: date) -> None:
    """
    date input 에 [d_from, d_to] 범위 설정.

    GG 사이트는 URL query string ?filter_date_from=YYYY-MM-DD&filter_date_to=YYYY-MM-DD
    을 그대로 picker 에 반영. picker 자체는 readonly input + controlled state 라서
    클릭 방식보다 URL navigate 가 훨씬 빠르고 정확함.

    Claude in Chrome 으로 직접 검증한 결과:
      URL navigate 만으로 input value = "Jun 3, 2026 - Jun 3, 2026" 자동 적용됨.
    """
    if d_from > d_to:
        raise ValueError(f"d_from({d_from}) > d_to({d_to})")

    from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
    expected = _expected_input_value(d_from, d_to)

    # 1) 현재 URL 에서 path/origin 만 유지하고 filter_date_from/to 갈아끼우기
    cur_url = page.url or AVAIL_URL
    parsed = urlparse(cur_url)
    qs = parse_qs(parsed.query, keep_blank_values=True)
    qs["filter_date_from"] = [d_from.strftime("%Y-%m-%d")]
    qs["filter_date_to"] = [d_to.strftime("%Y-%m-%d")]
    qs["page"] = ["1"]  # 첫 페이지부터
    new_query = urlencode(qs, doseq=True)
    new_url = urlunparse((
        parsed.scheme or "https",
        parsed.netloc or "supplier.getyourguide.com",
        parsed.path or "/manage/availability",
        parsed.params,
        new_query,
        parsed.fragment,
    ))

    LOG.info("GG date range URL navigate: %s", new_url)
    page.goto(new_url, wait_until="domcontentloaded", timeout=30_000)

    # 2) input 에 정확한 값 들어왔는지 검증 (최대 5초 폴링)
    try:
        # 같은 Chrome 에서 다른 봇이 동시에 돌면 이 페이지가 10초 안에 안 뜬다.
        # (2026-08-23: KR Chrome 에 KKday worker 4개가 붙어 있어 GG KOREA 가 통째로 실패)
        # 넉넉히 기다리고, 그래도 안 되면 새로고침 후 한 번 더.
        try:
            page.wait_for_selector('input[placeholder="From - to"]', timeout=30_000)
        except PWTimeoutError:
            LOG.warning("date input 30초 대기 실패 - 새로고침 후 재시도")
            page.reload(wait_until="domcontentloaded", timeout=45_000)
            page.wait_for_timeout(2_000)
            page.wait_for_selector('input[placeholder="From - to"]', timeout=30_000)
    except PWTimeoutError:
        raise RuntimeError("GG availability 페이지 로드 실패 - date input 안 보임")

    import time as _time
    start = _time.time()
    val = ""
    while _time.time() - start < 5.0:
        val = _read_date_input(page)
        if _input_matches(val, d_from, d_to):
            LOG.info("✓ date range 적용 확인: %r", val)
            return
        page.wait_for_timeout(200)

    raise RuntimeError(
        f"GG date range URL 반영 실패: input='{val}' (기대='{expected}'). "
        "URL navigate 후에도 input 이 기대값으로 안 바뀜. "
        "잘못된 마감을 방지하기 위해 close 작업을 중단합니다."
    )


# ============================================================
# Page size 변경 (15 -> 50)
# ============================================================
# 페이지당 표시 개수 셀렉터는 반드시 페이지네이션 푸터 안에서 찾는다.
# ⚠️ 예전에는 .p-select-label[aria-haspopup='listbox'] 의 .first 를 그냥 썼는데,
#    화면 위쪽 필터에도 같은 클래스의 콤보박스가 있어서 그쪽이 먼저 잡혔다.
#    그러면 엉뚱한 콤보박스를 열었다 닫을 뿐 표시 개수는 그대로라, 뒷 페이지 상품이
#    화면에 안 나오고 그 상품들이 마감되지 않는다.
#    (오픈 봇에서 53개 옵션 중 15개만 읽던 것과 같은 원인)
PAGE_FOOTER = "footer.pagination-footer"


def change_page_size(page: Page, target_size: int) -> bool:
    label = page.locator(f"{PAGE_FOOTER} .p-select-label").first
    try:
        label.scroll_into_view_if_needed(timeout=4_000)
        current = label.inner_text(timeout=3_000).strip()
    except Exception as e:
        LOG.warning("page size 셀렉터를 찾지 못함 (%s): %s", PAGE_FOOTER, str(e)[:100])
        return False
    if current == str(target_size):
        return True

    try:
        label.click(timeout=5_000)
        page.wait_for_timeout(500)
        opt = page.locator(f"li[role='option']:has-text('{target_size}')").first
        opt.wait_for(state="visible", timeout=5_000)
        opt.click(timeout=5_000)
        page.wait_for_timeout(2_000)
        # 실제로 바뀌었는지 확인한다. 바뀐 척하고 넘어가면 뒷 상품이 통째로 안 닫힌다.
        now = label.inner_text(timeout=3_000).strip()
        if now != str(target_size):
            LOG.warning("page size %s 로 안 바뀜 (현재 %s)", target_size, now)
            return False
        LOG.info("page size %s → %s", current, now)
        return True
    except Exception as e:
        LOG.warning("page size 변경 실패: %s", str(e)[:120])
        return False


# ============================================================
# 단일 페이지 처리
# ============================================================
def process_current_page(page: Page, dry_run: bool = False) -> Tuple[int, str]:
    """
    select all → Update selected → Block (= 최종 액션, Apply 단계 없음).
    반환: (선택된 행 수, 상태문자열)
    """
    bulk = page.locator("#bulk-checkbox").first
    try:
        bulk.wait_for(state="visible", timeout=5_000)
    except PWTimeoutError:
        return 0, "no_rows"

    # 이미 체크된 상태면 풀고 다시 체크 (clean state)
    try:
        if bulk.is_checked():
            bulk.click()
            page.wait_for_timeout(200)
    except Exception:
        pass
    bulk.click()
    page.wait_for_timeout(400)

    # bulk selector 의 "N selected" 텍스트에서 선택된 행 수 추출
    # 예전: "(15)" → 새: "15 selected"
    selected_n = 0
    try:
        bulk_label = page.locator("[data-testid='agenda-bulk-selector']").first.inner_text(timeout=2_000)
        # 둘 다 지원 (N) 또는 'N selected'
        m = re.search(r"\((\d+)\)", bulk_label) or re.search(r"(\d+)\s+selected", bulk_label, re.IGNORECASE)
        if m:
            selected_n = int(m.group(1))
    except Exception:
        pass

    if selected_n == 0:
        # 행이 진짜 없으면 종료
        return 0, "no_rows"

    if dry_run:
        LOG.info("[DRY-RUN] would block %d rows on this page", selected_n)
        try:
            bulk.click()
        except Exception:
            pass
        return selected_n, "dry_run"

    # Update selected 클릭 → 드롭다운/메뉴 등장
    # [v2] 페이지 전환 직후 React 렌더 race 해결:
    #   - 타임아웃 6s → 12s (페이지 4, page 2 처럼 가끔 느린 페이지 대비)
    #   - 클릭 전 명시적 wait_for(visible) + 짧은 안정화 대기
    #   - Playwright click 실패 시 JS fallback 시도 (overlay/state race 회피)
    #   - 재시도 3번 (총 6초 안정화 시간 확보)
    update_btn_sel = "[data-testid='update-selected-button']"
    clicked_update = False
    for attempt in range(3):
        try:
            btn = page.locator(update_btn_sel).first
            # 명시적 visible 대기 (React mount 가 늦으면 여기서 잡힘)
            btn.wait_for(state="visible", timeout=12_000)
            # DOM 안정화 (React state update 완료 보장)
            page.wait_for_timeout(500)
            try:
                btn.click(timeout=8_000)
                clicked_update = True
                break
            except Exception as _click_e:
                # Playwright click 실패 시 JS dispatch fallback
                LOG.info("Update selected click 실패 → JS fallback: %s", str(_click_e)[:80])
                ok = page.evaluate(
                    f"() => {{ const el = document.querySelector(\"{update_btn_sel}\"); if (!el) return false; el.click(); return true; }}"
                )
                if ok:
                    clicked_update = True
                    break
                raise
        except Exception as e:
            if attempt < 2:
                LOG.info("Update selected attempt %d/3 실패 - bulk 재선택 후 재시도: %s",
                         attempt + 1, str(e)[:80])
                # bulk state 리셋 + 점진적으로 더 긴 안정화 대기
                try:
                    bulk.click()
                    page.wait_for_timeout(400 + attempt * 300)
                    bulk.click()
                    page.wait_for_timeout(800 + attempt * 400)
                except Exception:
                    pass
            else:
                LOG.warning("Update selected 3회 재시도 후에도 실패: %s", str(e)[:80])
    if not clicked_update:
        return selected_n, "update_failed"
    page.wait_for_timeout(700)

    # Block bookings 클릭 = 최종 액션 (Apply 단계 없음, 즉시 적용)
    # [v2] visibility 타임아웃 5s → 10s, JS fallback 추가
    block_btn_sel = "[data-testid='block-bookings-button']"
    try:
        block_btn = page.locator(block_btn_sel).first
        block_btn.wait_for(state="visible", timeout=10_000)
        page.wait_for_timeout(300)
        try:
            block_btn.click(timeout=6_000)
        except Exception as _click_e:
            LOG.info("Block bookings click 실패 → JS fallback: %s", str(_click_e)[:80])
            ok = page.evaluate(
                f"() => {{ const el = document.querySelector(\"{block_btn_sel}\"); if (!el) return false; el.click(); return true; }}"
            )
            if not ok:
                raise
    except Exception as e:
        LOG.warning("Block bookings 버튼 클릭 실패: %s", e)
        return selected_n, "block_failed"

    # 적용 반영 대기
    page.wait_for_timeout(1_500)
    return selected_n, "blocked"


def is_next_enabled(page: Page) -> bool:
    try:
        nxt = page.locator("button:has-text('Next')").last
        if nxt.count() == 0:
            return False
        return not nxt.is_disabled()
    except Exception:
        return False


def click_next(page: Page) -> None:
    page.locator("button:has-text('Next')").last.click()
    page.wait_for_timeout(900)


def click_first_page(page: Page) -> None:
    try:
        first_page_btn = page.locator("button:has-text('1')").first
        if first_page_btn.is_visible(timeout=1_000):
            first_page_btn.click()
            page.wait_for_timeout(900)
            return
    except Exception:
        pass
    for _ in range(50):
        try:
            prev = page.locator("button:has-text('Previous')").last
            if prev.is_disabled():
                return
            prev.click()
            page.wait_for_timeout(500)
        except Exception:
            return


# ============================================================
# 한 바퀴
# ============================================================
def run_one_pass(page: Page, dry_run: bool, label: str) -> Tuple[int, int, List[str]]:
    blocked_total = 0
    page_count = 0
    fails: List[str] = []
    max_pages = 200

    while page_count < max_pages:
        page_count += 1
        n, status = process_current_page(page, dry_run=dry_run)
        LOG.info("[%s] page %d: %d rows, status=%s", label, page_count, n, status)
        if status == "no_rows":
            break
        if status in ("blocked", "dry_run"):
            blocked_total += n
        elif status in ("update_failed", "block_failed"):
            msg = f"{label} page{page_count}: {status} ({n}행 미마감)"
            LOG.warning("[%s] page %d: %s - 이 페이지 마감 실패 (%d행)", label, page_count, status, n)
            fails.append(msg)
        else:
            msg = f"{label} page{page_count}: unknown status={status}"
            LOG.warning("[%s] page %d: 알 수 없는 status=%s", label, page_count, status)
            fails.append(msg)

        if not is_next_enabled(page):
            break
        click_next(page)

    return blocked_total, page_count, fails


# ============================================================
# 지역 1개
# ============================================================
def run_region(
    region: str, port: int, bat: str,
    d_from: date, d_to: date, dry_run: bool,
) -> Tuple[int, List[str]]:
    errors: List[str] = []
    LOG.info("=== GG %s (port %d) 시작 ===", region, port)
    if not ensure_chrome(port, bat):
        errors.append(f"{region}: Chrome 부팅 실패")
        return 0, errors

    try:
        browser, context, _first = connect_and_setup(port)
        page = find_or_open_availability_tab(context, _first)

        # 1) date range URL navigate (page=1)
        select_date_range(page, d_from, d_to)

        # 2) size 15 패스: 기본 15 로 page 1 → 마지막 페이지까지
        change_page_size(page, 15)
        page.wait_for_timeout(700)
        click_first_page(page)
        page.wait_for_timeout(400)
        n1, p1, f1 = run_one_pass(page, dry_run, f"{region}/size15")

        # 3) size 50 패스
        # size 15 8페이지 처리 후 그 자리에서 size 50 변경 + page 1 클릭하면
        # React state transition 이 길어서 Update selected 가 안 뜸.
        # → URL navigate 으로 fresh page 1 + size 50 처음부터 시작.
        select_date_range(page, d_from, d_to)  # ?filter_date_from=...&filter_date_to=...&page=1 로 다시 진입
        page.wait_for_timeout(800)
        if change_page_size(page, 50):
            page.wait_for_timeout(1_500)
            # change_page_size 가 page 를 1로 reset 하지 않는 경우 대비
            click_first_page(page)
            page.wait_for_timeout(1_200)
            n2, p2, f2 = run_one_pass(page, dry_run, f"{region}/size50")
        else:
            n2, p2, f2 = 0, 0, []
            errors.append(f"{region}: page size 50 변경 실패")

        # 페이지 단위 마감 실패(update_failed/block_failed)를 region errors 로 끌어올림
        errors.extend(f1)
        errors.extend(f2)

        # ⚠️ 두 패스는 '같은 옵션들' 을 두 번 훑는다 (size15 로 한 번, size50 으로
        #    다시 한 번 — 페이지 넘김 중에 빠지는 것을 잡으려는 것이다).
        #    그래서 n1 + n2 는 실제 마감한 옵션 수가 아니라 그 두 배다.
        #    (2026-08-31: KOREA 106행 x 2 = 'success=212' 로 보고됐다.
        #     전체 합계 '성공 346' 도 그만큼 부풀어 있었다)
        #    실제로 다룬 옵션 수는 두 패스 중 많은 쪽이다.
        done = max(n1, n2)
        LOG.info("=== GG %s 완료: size15=%d행/%d페이지, size50=%d행/%d페이지, "
                 "실패페이지=%d → 옵션 %d개 ===",
                 region, n1, p1, n2, p2, len(f1) + len(f2), done)
        return done, errors
    except Exception as e:
        LOG.exception("GG %s 처리 중 예외: %s", region, e)
        errors.append(f"{region}: {e}")
        return 0, errors


# ============================================================
# 엔트리
# ============================================================
def run_close(target_date: Optional[date] = None, dry_run: bool = False) -> Result:
    target = target_date or (datetime.now().date() + timedelta(days=1))
    LOG.info("GG 마감 시작 target=%s dry_run=%s", target, dry_run)

    region_env = os.environ.get("GG_REGIONS", "").strip()
    if region_env:
        wanted = {r.strip().upper() for r in region_env.split(",") if r.strip()}
        regions_to_run = [r for r in REGIONS if r[0] in wanted]
    else:
        regions_to_run = list(REGIONS)

    total_blocked = 0
    all_errors: List[str] = []
    for region, port, bat in regions_to_run:
        blocked, errs = run_region(region, port, bat, target, target, dry_run)
        total_blocked += blocked
        all_errors.extend(errs)

    return Result(
        agency="GG",
        success=total_blocked,
        failed=len(all_errors),
        skipped=0,
        errors=all_errors,
    )


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--date", help="YYYY-MM-DD (기본: 내일)")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--region", help="단일 지역 (KOREA/JAPAN/AUSTRALIA/UK)")
    args = p.parse_args()

    if args.region:
        os.environ["GG_REGIONS"] = args.region.upper()

    target = None
    if args.date:
        target = datetime.strptime(args.date, "%Y-%m-%d").date()

    r = run_close(target_date=target, dry_run=args.dry_run)
    region_suffix = f"/{args.region}" if args.region else ""
    print(f"\n[GG{region_suffix}] success={r['success']} failed={r['failed']} skipped={r['skipped']}")
    if r.get("errors"):
        print("Errors:")
        for e in r["errors"][:20]:
            print("  -", e)