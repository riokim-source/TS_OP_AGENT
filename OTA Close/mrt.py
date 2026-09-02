# -*- coding: utf-8 -*-
"""
MRT / MyRealTrip 예약인원 마감 봇 V10 - 주차 이동 후 저장 반영 안정화

실행 전 준비:
1) Chrome을 디버그 모드로 실행
   chrome.exe --remote-debugging-port=9530 --user-data-dir="C:\\chrome-global"

2) 마이리얼트립 파트너 페이지에 로그인해 둔 상태에서 실행

실행:
python close_mrt.py

동작:
- 상품 > 투어·티켓 상품(신규) 이동
- 상품 ID 기준 검색
- 검색 결과의 수정 버튼 클릭
- 예약 인원 관리 진입
- 오늘 기준 내일 날짜의 잔여 인원을 0으로 수정
- 우측 하단 예약인원 수정 클릭
- 확인 팝업의 파란색 수정하기 클릭
- 팝업 저장 클릭 대기시간 단축
- 왼쪽 상단 나가기 클릭
- 다음 상품번호 작업 시 투어·티켓 상품 목록 복귀 후 빠르게 다음 검색 진행
- 요일별 기본 상품 ID 목록 사용 가능
- 전체 실행 런닝타임 출력 및 결과 로그 저장
- V8: 여러 탭 중 MRT 파트너 탭 자동 탐색, MRT_CDP_URL/MRT_PRODUCT_IDS 환경변수 지원
- V10: 인원 0 입력 후 실제 다음 주 이동 → 원래 주차 복귀 → 예약인원 수정 저장 순서로 반영 안정화
- 나가기 버튼 클릭 실패 시 JS 직접 클릭, 좌표 클릭, 캐시 URL 직접 이동으로 복구
"""

from __future__ import annotations

import sys as _sys_init
try:
    # Windows cp949 에서 이모지 print 시 UnicodeEncodeError 방지
    _sys_init.stdout.reconfigure(encoding="utf-8", errors="replace")
    _sys_init.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import os
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List

import pandas as pd
from playwright.sync_api import sync_playwright, Page

# Chrome 에 붙을 때 기다리는 시간 (ms).
# ⚠️ playwright 기본값은 180초다. Chrome 이 "반쯤 죽은" 상태 -- /json/version 은
#    200 을 주는데 CDP 핸드셰이크(<ws connected> 이후)가 안 끝나는 상태 -- 면
#    워커마다 3분씩 버리고 그제서야 실패한다.
#    2026-09-01 팀원 PC: 마감에서 25번, 2026-09-02: MRT 오픈 3건 전멸.
#    빨리 실패해야 사람이 그 Chrome 을 다시 켤 시간이 있다.
CDP_CONNECT_TIMEOUT_MS = int(os.environ.get("CDP_CONNECT_TIMEOUT_MS") or 30000)


RESULT_LOG_FILE = os.environ.get("MRT_RESULT_LOG_FILE", "mrt_close_result_log_v10_week_refresh_commit.xlsx")
# 스크린샷 기능 제거 - 폴더 자동 생성 안 함

def _mrt_default_cdp() -> str:
    """hub 라우팅의 MRT 포트. 없으면 9530 (라스트미닛 전용 대역)."""
    try:
        import sys as _s
        from pathlib import Path as _P
        _root = _P(__file__).resolve().parent
        if str(_root) not in _s.path:
            _s.path.insert(0, str(_root))
        from shared.hub_bridge import routing as _hub_routing
        r = _hub_routing()
        if r is not None:
            key = r.route("KOREA", "MRT") or r.route("JAPAN", "MRT")
            if key:
                return f"http://localhost:{r.profile_port(key)}"
    except Exception:
        pass
    return "http://localhost:9530"


CDP_URL = os.environ.get("MRT_CDP_URL") or os.environ.get("CDP_URL") or _mrt_default_cdp()

# 상품 목록 URL 캐시
# - 나가기 클릭이 간헐적으로 먹히지 않을 때 마지막으로 확인된 상품 목록 URL로 직접 복귀합니다.
PRODUCT_LIST_URL_CACHE = None

# ============================================================
# 요일별 기본 상품 ID 설정
# - 기준: 실제 마감할 날짜, 즉 "내일 날짜"의 요일 기준입니다.
# - 사용자가 제공한 목록은 전날 작업 기준입니다.
#   예: 월요일 목록은 일요일에 실행해서 월요일 재고를 0으로 만들 때 사용됩니다.
# - 실행 후 바로 Enter를 누르면 내일 요일 기준 목록이 자동 사용됩니다.
# ============================================================
DEFAULT_PRODUCT_IDS_BY_WEEKDAY = {
    "MON": [
        "3887808",
        "5724343",
        "4600233",
        "4397040",
        "4956172",
        "4700281",
        "4973348",
        "5624093",
        "5885335",
        "5728538",
    ],
    "TUE": [
        "3887808",
        "5724343",
        "4396387",
        "4397040",
        "4700281",
        "5624093",
        "5889847",
        "5728538",
    ],
    "WED": [
        "3887808",
        "5724343",
        "4397040",
        "4956172",
        "4700281",
        "5518461",
        "5632837",
        "5624093",
        "5885335",
        "5728538",
    ],
    "THU": [
        "3887808",
        "5724343",
        "4396387",
        "4397040",
        "4700281",
        "5624093",
        "5889847",
        "5728538",
    ],
    "FRI": [
        "3887808",
        "4600233",
        "5724343",
        "4397040",
        "4396387",
        "4700281",
        "4797890",
        "4973348",
        "5632837",
        "5624093",
        "5885335",
    ],
    "SAT": [
        "3887808",
        "4600233",
        "5724343",
        "4396387",
        "4397040",
        "4700281",
        "4797890",
        "5624093",
        "5889847",
    ],
    "SUN": [
        "3887808",
        "4956172",
        "4973348",
        "5728538",
    ],
}

WEEKDAY_KO = {
    "MON": "월요일",
    "TUE": "화요일",
    "WED": "수요일",
    "THU": "목요일",
    "FRI": "금요일",
    "SAT": "토요일",
    "SUN": "일요일",
}

WEEKDAY_ALIASES = {
    "MON": "MON", "월": "MON", "월요일": "MON",
    "TUE": "TUE", "화": "TUE", "화요일": "TUE",
    "WED": "WED", "수": "WED", "수요일": "WED",
    "THU": "THU", "목": "THU", "목요일": "THU",
    "FRI": "FRI", "금": "FRI", "금요일": "FRI",
    "SAT": "SAT", "토": "SAT", "토요일": "SAT",
    "SUN": "SUN", "일": "SUN", "일요일": "SUN",
}


def target_date() -> datetime:
    return datetime.now() + timedelta(days=1)


def target_day_number() -> str:
    return str(target_date().day)


def target_date_label() -> str:
    return target_date().strftime("%Y-%m-%d")


def target_weekday_key() -> str:
    return target_date().strftime("%a").upper()


def target_weekday_label() -> str:
    return WEEKDAY_KO.get(target_weekday_key(), target_weekday_key())


def format_duration(seconds: float) -> str:
    """초 단위 시간을 HH:MM:SS 형식으로 변환합니다."""
    seconds = int(round(seconds))
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


def get_default_product_ids_for_weekday(weekday_key: str) -> List[str]:
    return [str(x).strip() for x in DEFAULT_PRODUCT_IDS_BY_WEEKDAY.get(weekday_key, []) if str(x).strip()]


def normalize_text(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def wait_ready(page: Page, ms: int = 800):
    try:
        page.wait_for_load_state("networkidle", timeout=10000)
    except Exception:
        pass
    page.wait_for_timeout(ms)


def wait_light(page: Page, ms: int = 300):
    """
    V4 속도 개선용 짧은 대기.
    networkidle을 매번 기다리면 페이지 내부 요청 때문에 10초 가까이 지연될 수 있어,
    다음 화면 확인이 명확한 구간에서는 이 함수를 사용한다.
    """
    page.wait_for_timeout(ms)


def is_product_list_screen(page: Page) -> bool:
    try:
        return bool(page.evaluate(
            """() => {
                const text = (document.body.innerText || '').replace(/\\s+/g, ' ').trim();
                return text.includes('투어·티켓 상품') &&
                       (text.includes('검색어') || text.includes('상품명') || text.includes('상품 ID')) &&
                       !text.includes('상품 정보 등록') &&
                       !text.includes('예약 인원 관리 *');
            }"""
        ))
    except Exception:
        return False


def is_product_edit_screen(page: Page) -> bool:
    """상품 수정/예약 인원 관리 화면에 남아있는지 확인합니다."""
    try:
        return bool(page.evaluate(
            """() => {
                const text = (document.body.innerText || '').replace(/\\s+/g, ' ').trim();
                return text.includes('상품 정보 등록') ||
                       text.includes('예약 인원 관리') ||
                       text.includes('상품 속성 정보 등록') ||
                       text.includes('옵션 등록');
            }"""
        ))
    except Exception:
        return False


def record_product_list_url(page: Page):
    """상품 목록 화면 URL을 저장해 두었다가 나가기 실패 시 직접 복귀에 사용합니다."""
    global PRODUCT_LIST_URL_CACHE
    try:
        if is_product_list_screen(page):
            url = page.url
            if url and url.startswith('http'):
                PRODUCT_LIST_URL_CACHE = url
    except Exception:
        pass


def wait_product_list_ready(page: Page, timeout: int = 7000) -> bool:
    """투어·티켓 상품 목록 복귀를 networkidle 대신 화면 텍스트로 빠르게 확인."""
    try:
        page.wait_for_function(
            """() => {
                const text = (document.body.innerText || '').replace(/\\s+/g, ' ').trim();
                return text.includes('투어·티켓 상품') &&
                       (text.includes('검색어') || text.includes('상품명') || text.includes('상품 ID')) &&
                       !text.includes('상품 정보 등록') &&
                       !text.includes('예약 인원 관리 *');
            }""",
            timeout=timeout,
        )
        wait_light(page, 180)
        record_product_list_url(page)
        return True
    except Exception:
        ok = is_product_list_screen(page)
        if ok:
            record_product_list_url(page)
        return ok

def wait_edit_page_ready(page: Page, timeout: int = 8000) -> bool:
    try:
        page.wait_for_function(
            """() => {
                const text = (document.body.innerText || '').replace(/\\s+/g, ' ').trim();
                return text.includes('예약 인원 관리') || text.includes('상품 정보 등록');
            }""",
            timeout=timeout,
        )
        wait_light(page, 400)
        return True
    except Exception:
        return False


def wait_inventory_page_ready(page: Page, timeout: int = 7000) -> bool:
    try:
        page.wait_for_function(
            """() => {
                const text = (document.body.innerText || '').replace(/\\s+/g, ' ').trim();
                return text.includes('예약 인원 관리');
            }""",
            timeout=timeout,
        )
        wait_light(page, 400)
        return True
    except Exception:
        return False




def scroll_calendar_week_title_into_view(page: Page) -> bool:
    """
    예약 인원 관리 달력 상단의 'YYYY년 M월 N주차' 제목을 DOM 텍스트 기준으로 찾아 화면 중앙으로 이동합니다.
    상품/옵션마다 테이블 위치가 달라지므로 고정 좌표는 사용하지 않습니다.
    """
    try:
        moved = page.evaluate(
            r"""() => {
                function norm(s) { return (s || '').replace(/\s+/g, ' ').trim(); }
                function visible(el) {
                    if (!el) return false;
                    const r = el.getBoundingClientRect();
                    const st = window.getComputedStyle(el);
                    return st.display !== 'none' &&
                           st.visibility !== 'hidden' &&
                           st.opacity !== '0' &&
                           r.width > 0 &&
                           r.height > 0;
                }
                function rect(el) {
                    const r = el.getBoundingClientRect();
                    const text = norm(el.innerText || el.textContent || '');
                    const m = text.match(/\d{4}\s*년\s*\d{1,2}\s*월\s*\d+\s*주차/);
                    const childHasTitle = Array.from(el.children || []).some(ch => visible(ch) && /\d{4}\s*년\s*\d{1,2}\s*월\s*\d+\s*주차/.test(norm(ch.innerText || ch.textContent || '')));
                    return {el, text, match:m ? m[0] : '', x:r.x, y:r.y, width:r.width, height:r.height,
                            centerX:r.x + r.width/2, centerY:r.y + r.height/2, area:r.width*r.height,
                            childHasTitle};
                }
                const titleRe = /\d{4}\s*년\s*\d{1,2}\s*월\s*\d+\s*주차/;
                const nodes = Array.from(document.querySelectorAll('button,[role="button"],span,div,p,strong,b,h1,h2,h3,h4,a'))
                    .filter(visible)
                    .map(rect)
                    .filter(o => titleRe.test(o.text))
                    .sort((a,b) => {
                        // 실제 제목만 담은 leaf 노드 우선 → 작은 면적 → 화면 중앙 근처
                        const mid = (window.innerHeight || 1080) / 2;
                        return Number(a.childHasTitle) - Number(b.childHasTitle) ||
                               a.area - b.area ||
                               Math.abs(a.centerY - mid) - Math.abs(b.centerY - mid);
                    });
                if (!nodes.length) return false;
                try { nodes[0].el.scrollIntoView({block:'center', inline:'center'}); } catch(e) {}
                return true;
            }"""
        )
        if moved:
            page.wait_for_timeout(250)
            return True
    except Exception:
        pass
    return False


def get_calendar_week_title(page: Page):
    """예약 인원 관리 달력 상단의 '2026년 5월 3주차' 같은 주차 제목을 DOM 기준으로 찾습니다."""
    try:
        return page.evaluate(
            r"""() => {
                function norm(s) { return (s || '').replace(/\s+/g, ' ').trim(); }
                function visible(el) {
                    if (!el) return false;
                    const r = el.getBoundingClientRect();
                    const st = window.getComputedStyle(el);
                    const vh = window.innerHeight || 1080;
                    const vw = window.innerWidth || 1920;
                    return st.display !== 'none' &&
                           st.visibility !== 'hidden' &&
                           st.opacity !== '0' &&
                           r.width > 0 &&
                           r.height > 0 &&
                           r.bottom >= 0 &&
                           r.top <= vh &&
                           r.right >= 0 &&
                           r.left <= vw;
                }
                function info(el) {
                    const r = el.getBoundingClientRect();
                    const text = norm(el.innerText || el.textContent || '');
                    const m = text.match(/\d{4}\s*년\s*\d{1,2}\s*월\s*\d+\s*주차/);
                    const childHasTitle = Array.from(el.children || []).some(ch => visible(ch) && /\d{4}\s*년\s*\d{1,2}\s*월\s*\d+\s*주차/.test(norm(ch.innerText || ch.textContent || '')));
                    return {el, text, match:m ? m[0] : '', x:r.x, y:r.y, width:r.width, height:r.height,
                            centerX:r.x + r.width/2, centerY:r.y + r.height/2,
                            area:r.width*r.height, childHasTitle};
                }
                const titleRe = /\d{4}\s*년\s*\d{1,2}\s*월\s*\d+\s*주차/;
                const nodes = Array.from(document.querySelectorAll('button,[role="button"],span,div,p,strong,b,h1,h2,h3,h4,a'))
                    .filter(visible)
                    .map(info)
                    .filter(o => titleRe.test(o.text))
                    .sort((a,b) => {
                        const mid = (window.innerHeight || 1080) / 2;
                        return Number(a.childHasTitle) - Number(b.childHasTitle) ||
                               a.area - b.area ||
                               Math.abs(a.centerY - mid) - Math.abs(b.centerY - mid);
                    });
                if (!nodes.length) return null;
                const n = nodes[0];
                return {text:n.match, x:n.x, y:n.y, width:n.width, height:n.height,
                        centerX:n.centerX, centerY:n.centerY};
            }"""
        )
    except Exception:
        return None


def is_calendar_day_visible(page: Page, day_number: str) -> bool:
    """현재 보이는 주차 안에 목표 날짜 숫자가 있는지 확인합니다."""
    try:
        return bool(page.evaluate(
            r"""(dayNumber) => {
                function norm(s) { return (s || '').replace(/\s+/g, ' ').trim(); }
                const vh = window.innerHeight || 1080;
                return Array.from(document.querySelectorAll('th,td,div,span'))
                  .some(el => {
                    const text = norm(el.innerText || el.textContent || '');
                    const r = el.getBoundingClientRect();
                    const st = window.getComputedStyle(el);
                    return st.display !== 'none' &&
                           st.visibility !== 'hidden' &&
                           st.opacity !== '0' &&
                           text === String(dayNumber) &&
                           r.width > 20 &&
                           r.height > 12 &&
                           r.bottom >= 0 &&
                           r.top <= vh;
                  });
            }""",
            str(day_number),
        ))
    except Exception:
        return False




def _js_date_obj(dt: datetime) -> Dict[str, int]:
    """JS date picker 선택용 날짜 dict."""
    return {"year": dt.year, "month": dt.month, "day": dt.day}



def click_calendar_week_title(page: Page, reason: str = "") -> bool:
    """
    'YYYY년 M월 N주차' 제목을 클릭해 날짜 선택 팝업을 엽니다.
    고정 좌표 fallback은 사용하지 않고, 실제 주차 제목 DOM의 위치를 읽어 클릭합니다.
    """
    scroll_calendar_week_title_into_view(page)
    label = f"{reason}: " if reason else ""
    print(f"[진행] {label}주차 제목 클릭 → 날짜 선택 팝업 열기")

    def picker_open() -> bool:
        try:
            return get_datepicker_month(page) is not None
        except Exception:
            return False

    # 이미 열려 있으면 그대로 진행
    if picker_open():
        print("[완료] 날짜 선택 팝업이 이미 열려 있습니다.")
        return True

    for attempt in range(1, 4):
        try:
            title = page.evaluate(
                r"""() => {
                    function norm(s) { return (s || '').replace(/\s+/g, ' ').trim(); }
                    function visible(el) {
                        if (!el) return false;
                        const r = el.getBoundingClientRect();
                        const st = window.getComputedStyle(el);
                        const vh = window.innerHeight || 1080;
                        const vw = window.innerWidth || 1920;
                        return st.display !== 'none' && st.visibility !== 'hidden' && st.opacity !== '0' &&
                               r.width > 0 && r.height > 0 && r.bottom >= 0 && r.top <= vh && r.right >= 0 && r.left <= vw;
                    }
                    function info(el) {
                        const r = el.getBoundingClientRect();
                        const text = norm(el.innerText || el.textContent || '');
                        const m = text.match(/\d{4}\s*년\s*\d{1,2}\s*월\s*\d+\s*주차/);
                        const childHasTitle = Array.from(el.children || []).some(ch => visible(ch) && /\d{4}\s*년\s*\d{1,2}\s*월\s*\d+\s*주차/.test(norm(ch.innerText || ch.textContent || '')));
                        return {el, text, match:m ? m[0] : '', x:r.x, y:r.y, width:r.width, height:r.height,
                                centerX:r.x + r.width/2, centerY:r.y + r.height/2,
                                area:r.width*r.height, childHasTitle};
                    }
                    const titleRe = /\d{4}\s*년\s*\d{1,2}\s*월\s*\d+\s*주차/;
                    const nodes = Array.from(document.querySelectorAll('button,[role="button"],span,div,p,strong,b,h1,h2,h3,h4,a'))
                        .filter(visible)
                        .map(info)
                        .filter(o => titleRe.test(o.text))
                        .sort((a,b) => {
                            const mid = (window.innerHeight || 1080) / 2;
                            return Number(a.childHasTitle) - Number(b.childHasTitle) ||
                                   a.area - b.area ||
                                   Math.abs(a.centerY - mid) - Math.abs(b.centerY - mid);
                        });
                    if (!nodes.length) return null;
                    const n = nodes[0];
                    try { n.el.scrollIntoView({block:'center', inline:'center'}); } catch(e) {}
                    const r = n.el.getBoundingClientRect();
                    return {text:n.match, x:r.x + r.width/2, y:r.y + r.height/2, width:r.width, height:r.height};
                }"""
            )
            if title:
                # 좌표 대신 텍스트로 찾아서 클릭 (xy 좌표 사용 안 함)
                try:
                    page.get_by_text(title.get("text", ""), exact=False).first.click(timeout=2000, force=True)
                except Exception:
                    pass
                page.wait_for_timeout(450)
                if picker_open():
                    print(f"[완료] 주차 제목 클릭으로 날짜 팝업 열림: {title.get('text')} / attempt={attempt}")
                    return True
                # 첫 클릭이 포커스만 잡히는 경우 한 번 더
                try:
                    page.get_by_text(title.get("text", ""), exact=False).first.click(timeout=2000, force=True)
                except Exception:
                    pass
                page.wait_for_timeout(450)
                if picker_open():
                    print(f"[완료] 주차 제목 재클릭으로 날짜 팝업 열림: {title.get('text')} / attempt={attempt}")
                    return True
        except Exception as e:
            print(f"[주의] 주차 제목 DOM 클릭 시도 {attempt}/3 실패: {e}")

        # Playwright text locator 보조: 정확한 텍스트 DOM을 클릭
        try:
            loc = page.get_by_text(re.compile(r"\d{4}\s*년\s*\d{1,2}\s*월\s*\d+\s*주차")).first
            loc.scroll_into_view_if_needed(timeout=1200)
            loc.click(timeout=2000)
            page.wait_for_timeout(450)
            if picker_open():
                print(f"[완료] 주차 제목 locator 클릭으로 날짜 팝업 열림 / attempt={attempt}")
                return True
        except Exception:
            pass

    print("[오류] 주차 제목 클릭 후 날짜 선택 팝업이 열리지 않았습니다.")
    return False



def _get_datepicker_panel_info(page: Page):
    """열린 날짜 선택 팝업 패널과 월 제목 정보를 찾습니다."""
    try:
        return page.evaluate(
            r"""() => {
                function norm(s) { return (s || '').replace(/\s+/g, ' ').trim(); }
                function visible(el) {
                    if (!el) return false;
                    const r = el.getBoundingClientRect();
                    const st = window.getComputedStyle(el);
                    const vh = window.innerHeight || 1080;
                    const vw = window.innerWidth || 1920;
                    return st.display !== 'none' && st.visibility !== 'hidden' && st.opacity !== '0' &&
                           r.width > 0 && r.height > 0 && r.bottom >= 0 && r.top <= vh && r.right >= 0 && r.left <= vw;
                }
                function rect(el) {
                    const r = el.getBoundingClientRect();
                    return {el, text:norm(el.innerText || el.textContent || ''), x:r.x, y:r.y,
                            width:r.width, height:r.height, centerX:r.x+r.width/2, centerY:r.y+r.height/2,
                            area:r.width*r.height};
                }
                const monthRe = /(\d{4})\s*년\s*(\d{1,2})\s*월/;
                const headers = Array.from(document.querySelectorAll('button,[role="button"],span,div,strong,b,p'))
                    .filter(visible)
                    .map(rect)
                    .filter(o => monthRe.test(o.text) && !/주차/.test(o.text) && o.width >= 40 && o.width <= 500 && o.height >= 12 && o.height <= 90)
                    .sort((a,b) => a.area - b.area || a.y - b.y);
                if (!headers.length) return null;
                const h = headers[0];
                const m = h.text.match(monthRe);

                const containers = [];
                let cur = h.el.parentElement;
                for (let depth = 0; cur && depth < 10; depth++, cur = cur.parentElement) {
                    if (!visible(cur)) continue;
                    const c = rect(cur);
                    const dayCount = Array.from(cur.querySelectorAll('button,[role="button"],td,div,span'))
                        .filter(visible)
                        .map(el => norm(el.innerText || el.textContent || ''))
                        .filter(t => /^(?:[1-9]|[12]\d|3[01])$/.test(t)).length;
                    if (c.width >= 180 && c.width <= 620 && c.height >= 160 && c.height <= 620 && dayCount >= 20) {
                        containers.push({...c, dayCount});
                    }
                }
                containers.sort((a,b) => a.area - b.area);
                const panel = containers[0] || h;
                return {
                    text:m[0], year:Number(m[1]), month:Number(m[2]),
                    header:{x:h.x, y:h.y, width:h.width, height:h.height, centerX:h.centerX, centerY:h.centerY},
                    panel:{x:panel.x, y:panel.y, width:panel.width, height:panel.height, centerX:panel.centerX, centerY:panel.centerY}
                };
            }"""
        )
    except Exception:
        return None


def get_datepicker_month(page: Page):
    """열린 날짜 선택 팝업의 월 제목(예: 2026년 05월)을 읽습니다."""
    info = _get_datepicker_panel_info(page)
    if not info:
        return None
    return {"text": info["text"], "year": info["year"], "month": info["month"],
            "x": info["header"]["x"], "y": info["header"]["y"],
            "width": info["header"]["width"], "height": info["header"]["height"]}


def click_datepicker_month_nav(page: Page, direction: str) -> bool:
    """날짜 선택 팝업 내부 월 이동 버튼을 클릭합니다."""
    label = "다음 달" if direction == "next" else "지난 달"
    print(f"[진행] 날짜 팝업 {label} 클릭")
    try:
        result = page.evaluate(
            r"""(direction) => {
                function norm(s) { return (s || '').replace(/\s+/g, ' ').trim(); }
                function visible(el) {
                    if (!el) return false;
                    const r = el.getBoundingClientRect();
                    const st = window.getComputedStyle(el);
                    const vh = window.innerHeight || 1080;
                    const vw = window.innerWidth || 1920;
                    return st.display !== 'none' && st.visibility !== 'hidden' && st.opacity !== '0' &&
                           r.width > 0 && r.height > 0 && r.bottom >= 0 && r.top <= vh && r.right >= 0 && r.left <= vw;
                }
                function info(el) {
                    const r = el.getBoundingClientRect();
                    return {el, x:r.x, y:r.y, width:r.width, height:r.height,
                            centerX:r.x + r.width/2, centerY:r.y + r.height/2,
                            text:norm(el.innerText || el.textContent || ''),
                            label:norm(el.getAttribute('aria-label') || el.getAttribute('title') || '')};
                }
                const monthRe = /\d{4}\s*년\s*\d{1,2}\s*월/;
                const headers = Array.from(document.querySelectorAll('div,span,button,strong,b'))
                    .filter(visible).map(info)
                    .filter(o => monthRe.test(o.text) && !/주차/.test(o.text) && o.width >= 60 && o.width <= 360 && o.height >= 14 && o.height <= 80)
                    .sort((a,b) => (a.width*a.height) - (b.width*b.height) || a.y - b.y);
                if (!headers.length) return {ok:false, reason:'month header not found'};
                const h = headers[0];

                const buttons = Array.from(document.querySelectorAll('button,[role="button"],span,div'))
                    .filter(visible).map(info)
                    .filter(b =>
                        b.width >= 12 && b.width <= 60 && b.height >= 12 && b.height <= 60 &&
                        Math.abs(b.centerY - h.centerY) <= 45 &&
                        (direction === 'next' ? b.centerX > h.centerX : b.centerX < h.centerX)
                    );
                if (!buttons.length) return {ok:false, reason:'month nav button not found'};

                // 월 이동은 보통 헤더에 가장 가까운 단일 화살표입니다. <<, >>는 연도 이동일 수 있어 텍스트 길이가 짧은 버튼 우선.
                buttons.sort((a,b) => {
                    const at = (a.text || a.label || '').length;
                    const bt = (b.text || b.label || '').length;
                    return at - bt || Math.abs(a.centerX - h.centerX) - Math.abs(b.centerX - h.centerX);
                });
                const target = buttons[0];
                const clickable = target.el.closest('button,[role="button"]') || target.el;
                clickable.click();
                return {ok:true, x:target.centerX, y:target.centerY, text:target.text || target.label};
            }""",
            direction,
        )
    except Exception as e:
        print(f"[주의] 날짜 팝업 {label} 클릭 오류: {e}")
        return False

    if not result or not result.get("ok"):
        reason_msg = result.get("reason") if isinstance(result, dict) else "unknown"
        print(f"[주의] 날짜 팝업 {label} 버튼 탐색 실패: {reason_msg}")
        return False

    page.wait_for_timeout(350)
    return True


def ensure_datepicker_month(page: Page, dt: datetime) -> bool:
    """날짜 선택 팝업의 표시 월을 목표 날짜 월로 맞춥니다."""
    for _ in range(14):
        month = get_datepicker_month(page)
        if not month:
            page.wait_for_timeout(250)
            continue
        cur_index = month["year"] * 12 + month["month"]
        target_index = dt.year * 12 + dt.month
        if cur_index == target_index:
            return True
        direction = "next" if target_index > cur_index else "prev"
        if not click_datepicker_month_nav(page, direction):
            return False
    return False



def click_datepicker_day(page: Page, dt: datetime, reason: str = "") -> bool:
    """열린 날짜 선택 팝업에서 목표 날짜의 일(day)을 클릭합니다."""
    if reason:
        print(f"[진행] {reason}: 날짜 팝업에서 {dt.strftime('%Y-%m-%d')} 선택")
    else:
        print(f"[진행] 날짜 팝업에서 {dt.strftime('%Y-%m-%d')} 선택")

    if not ensure_datepicker_month(page, dt):
        print(f"[오류] 날짜 팝업 월 이동 실패: {dt.strftime('%Y-%m')}")
        return False

    try:
        result = page.evaluate(
            r"""(target) => {
                function norm(s) { return (s || '').replace(/\s+/g, ' ').trim(); }
                function visible(el) {
                    if (!el) return false;
                    const r = el.getBoundingClientRect();
                    const st = window.getComputedStyle(el);
                    const vh = window.innerHeight || 1080;
                    const vw = window.innerWidth || 1920;
                    const disabled = el.disabled || el.getAttribute('aria-disabled') === 'true';
                    return !disabled && st.display !== 'none' && st.visibility !== 'hidden' && st.opacity !== '0' &&
                           r.width > 0 && r.height > 0 && r.bottom >= 0 && r.top <= vh && r.right >= 0 && r.left <= vw;
                }
                function rect(el) {
                    const r = el.getBoundingClientRect();
                    return {el, text:norm(el.innerText || el.textContent || ''), x:r.x, y:r.y,
                            width:r.width, height:r.height, centerX:r.x+r.width/2, centerY:r.y+r.height/2,
                            area:r.width*r.height, cls:String(el.className || ''),
                            aria:norm(el.getAttribute('aria-label') || el.getAttribute('title') || '')};
                }
                const monthRe = /(\d{4})\s*년\s*(\d{1,2})\s*월/;
                const headers = Array.from(document.querySelectorAll('button,[role="button"],span,div,strong,b,p'))
                    .filter(visible)
                    .map(rect)
                    .filter(o => monthRe.test(o.text) && !/주차/.test(o.text) && o.width >= 40 && o.width <= 500 && o.height >= 12 && o.height <= 90)
                    .sort((a,b) => a.area - b.area || a.y - b.y);
                if (!headers.length) return {ok:false, reason:'month header not found'};
                const h = headers[0];

                const containers = [];
                let cur = h.el.parentElement;
                for (let depth = 0; cur && depth < 10; depth++, cur = cur.parentElement) {
                    if (!visible(cur)) continue;
                    const c = rect(cur);
                    const dayCount = Array.from(cur.querySelectorAll('button,[role="button"],td,div,span'))
                        .filter(visible)
                        .map(el => norm(el.innerText || el.textContent || ''))
                        .filter(t => /^(?:[1-9]|[12]\d|3[01])$/.test(t)).length;
                    if (c.width >= 180 && c.width <= 620 && c.height >= 160 && c.height <= 620 && dayCount >= 20) {
                        containers.push({...c, dayCount});
                    }
                }
                containers.sort((a,b) => a.area - b.area);
                const panel = containers[0];
                if (!panel) return {ok:false, reason:'datepicker panel not found'};

                const dayText = String(target.day);
                const targetIso = `${target.year}-${String(target.month).padStart(2,'0')}-${String(target.day).padStart(2,'0')}`;
                const targetSlash = `${target.year}/${String(target.month).padStart(2,'0')}/${String(target.day).padStart(2,'0')}`;
                const targetKo = `${target.year}년 ${target.month}월 ${target.day}일`;

                const seen = new Set();
                const candidates = Array.from(panel.el.querySelectorAll('button,[role="button"],td,[role="gridcell"],div,span'))
                    .filter(visible)
                    .map(el => {
                        const clickable = el.closest('button,[role="button"],td,[role="gridcell"]') || el;
                        return rect(clickable);
                    })
                    .filter(o => {
                        const key = `${Math.round(o.x)}:${Math.round(o.y)}:${Math.round(o.width)}:${Math.round(o.height)}`;
                        if (seen.has(key)) return false;
                        seen.add(key);
                        const cls = o.cls.toLowerCase();
                        const disabledLike = /disabled/.test(cls) || /outside/.test(cls) || /prev-month/.test(cls) || /next-month/.test(cls);
                        const inPanel = o.centerX >= panel.x && o.centerX <= panel.x + panel.width && o.centerY >= panel.y && o.centerY <= panel.y + panel.height;
                        const belowHeader = o.centerY > h.centerY + 25;
                        const dayMatch = o.text === dayText || o.aria.includes(targetIso) || o.aria.includes(targetSlash) || o.aria.includes(targetKo);
                        return !disabledLike && inPanel && belowHeader && dayMatch && o.width >= 14 && o.width <= 90 && o.height >= 14 && o.height <= 90;
                    })
                    .sort((a,b) => a.y - b.y || a.x - b.x || a.area - b.area);

                if (!candidates.length) return {ok:false, reason:'day cell not found', day:dayText};
                const c = candidates[0];
                return {ok:true, text:c.text, x:c.centerX, y:c.centerY, width:c.width, height:c.height};
            }""",
            _js_date_obj(dt),
        )
    except Exception as e:
        print(f"[오류] 날짜 팝업 일자 탐색 중 오류: {e}")
        return False

    if not result or not result.get("ok"):
        reason_msg = result.get("reason") if isinstance(result, dict) else "unknown"
        print(f"[오류] 날짜 팝업에서 날짜 선택 실패: {reason_msg}")
        return False

    print(f"[진행] 날짜 팝업 일자 클릭: {dt.strftime('%Y-%m-%d')}")
    try:
        # 좌표 대신 JS 내에서 직접 element.click() 처리
        clicked = page.evaluate(
            """(targetText) => {
                const cells = Array.from(document.querySelectorAll('.ant-picker-cell, [class*=picker-cell]'));
                const found = cells.find(c => (c.getAttribute('title') || c.textContent || '').trim() === targetText);
                if (found) { found.click(); return true; }
                return false;
            }""",
            dt.strftime("%Y-%m-%d")
        )
        if not clicked:
            # 폴백: aria-label 매칭
            try:
                page.locator(f"[aria-label*=\"{dt.strftime('%Y-%m-%d')}\"]").first.click(timeout=2000, force=True)
            except Exception:
                pass
        page.wait_for_timeout(800)
    except Exception as e:
        print(f"[오류] 날짜 팝업 일자 클릭 실패: {e}")
        return False

    print(f"[완료] 날짜 팝업 일자 클릭: {dt.strftime('%Y-%m-%d')}")
    return True


def change_calendar_week_by_datepicker(page: Page, dt: datetime, reason: str = "") -> bool:
    """
    주차 좌우 버튼 대신 'YYYY년 M월 N주차' 제목 클릭 → 날짜 선택 팝업 → 원하는 날짜 클릭 방식으로 주차를 변경합니다.
    """
    before = get_calendar_week_title(page)
    before_text = before.get("text") if before else ""
    if not click_calendar_week_title(page, reason=reason):
        return False
    if not click_datepicker_day(page, dt, reason=reason):
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass
        return False
    after = get_calendar_week_title(page)
    after_text = after.get("text") if after else ""
    if after_text:
        print(f"[완료] 현재 주차: {after_text}")
    elif before_text:
        print("[주의] 날짜 선택 후 현재 주차 제목을 다시 읽지 못했습니다.")
    return True

def click_calendar_week_nav(page: Page, direction: str, reason: str = "") -> bool:
    """
    달력의 다음 주/지난 주 버튼을 DOM 기준으로 클릭합니다.

    중요:
    - 상품마다 예약 인원 관리 테이블 위치가 달라질 수 있으므로 고정 좌표를 사용하지 않습니다.
    - '2026년 5월 3주차' 형태의 주차 제목 DOM을 먼저 찾고,
      같은 컨테이너 안에서 제목 좌우에 있는 실제 button 요소를 클릭합니다.
    """
    label = "다음 주" if direction == "next" else "지난 주"

    scroll_calendar_week_title_into_view(page)
    before = get_calendar_week_title(page)
    before_text = before.get("text") if before else ""

    if reason:
        print(f"[진행] {reason}: {label} 클릭")
    else:
        print(f"[진행] {label} 클릭")

    try:
        result = page.evaluate(
            r"""(direction) => {
                function norm(s) { return (s || '').replace(/\s+/g, ' ').trim(); }
                function visible(el) {
                    if (!el) return false;
                    const r = el.getBoundingClientRect();
                    const st = window.getComputedStyle(el);
                    const vh = window.innerHeight || 1080;
                    const vw = window.innerWidth || 1920;
                    return st.display !== 'none' &&
                           st.visibility !== 'hidden' &&
                           st.opacity !== '0' &&
                           r.width > 0 &&
                           r.height > 0 &&
                           r.bottom >= 0 &&
                           r.top <= vh &&
                           r.right >= 0 &&
                           r.left <= vw;
                }
                function rectInfo(el) {
                    const r = el.getBoundingClientRect();
                    return {
                        el,
                        x: r.x,
                        y: r.y,
                        width: r.width,
                        height: r.height,
                        centerX: r.x + r.width / 2,
                        centerY: r.y + r.height / 2,
                        area: r.width * r.height,
                        text: norm(el.innerText || el.textContent || ''),
                        label: norm(el.getAttribute('aria-label') || el.getAttribute('title') || '')
                    };
                }

                const titleRe = /\d{4}\s*년\s*\d{1,2}\s*월\s*\d+\s*주차/;

                // 1) 실제 주차 제목 노드 찾기: 가장 작은 visible 텍스트 노드를 우선 선택
                const titleCandidates = Array.from(document.querySelectorAll('h1,h2,h3,h4,button,[role="button"],div,span,p,strong,b'))
                    .filter(visible)
                    .map(rectInfo)
                    .filter(o =>
                        titleRe.test(o.text) &&
                        o.width >= 80 && o.width <= 520 &&
                        o.height >= 14 && o.height <= 100
                    )
                    .sort((a, b) => {
                        const mid = (window.innerHeight || 1080) / 2;
                        return a.area - b.area || Math.abs(a.centerY - mid) - Math.abs(b.centerY - mid);
                    });

                if (!titleCandidates.length) {
                    return {ok:false, reason:'week title not found'};
                }

                const title = titleCandidates[0];
                try { title.el.scrollIntoView({block:'center', inline:'center'}); } catch(e) {}

                // 2) 주차 제목의 조상 중, 제목 좌우 버튼을 함께 포함하는 컨테이너 찾기
                const containers = [];
                let cur = title.el.parentElement;
                for (let depth = 0; cur && depth < 8; depth++, cur = cur.parentElement) {
                    if (!visible(cur)) continue;
                    const cr = cur.getBoundingClientRect();
                    // 너무 큰 전체 페이지 컨테이너는 제외
                    if (cr.width > 900 || cr.height > 220) continue;
                    const buttons = Array.from(cur.querySelectorAll('button,[role="button"]'))
                        .filter(visible)
                        .map(rectInfo)
                        .filter(b =>
                            b.width >= 20 && b.width <= 90 &&
                            b.height >= 20 && b.height <= 90 &&
                            Math.abs(b.centerY - title.centerY) <= 80
                        );
                    const lefts = buttons.filter(b => b.centerX < title.centerX);
                    const rights = buttons.filter(b => b.centerX > title.centerX);
                    if (lefts.length || rights.length) {
                        containers.push({el:cur, buttons, lefts, rights, width:cr.width, height:cr.height});
                    }
                }

                let pool = [];
                if (containers.length) {
                    // 가장 작은 컨테이너가 보통 <지난주 버튼 + 주차 제목 + 다음주 버튼> 영역입니다.
                    containers.sort((a,b) => (a.width * a.height) - (b.width * b.height));
                    const c = containers[0];
                    pool = direction === 'next' ? c.rights : c.lefts;
                }

                // 3) 컨테이너 탐색이 실패하면, 제목과 같은 라인에 있는 모든 실제 버튼 중 좌우 버튼 선택
                if (!pool.length) {
                    pool = Array.from(document.querySelectorAll('button,[role="button"]'))
                        .filter(visible)
                        .map(rectInfo)
                        .filter(b =>
                            b.width >= 20 && b.width <= 90 &&
                            b.height >= 20 && b.height <= 90 &&
                            Math.abs(b.centerY - title.centerY) <= 80 &&
                            (direction === 'next'
                                ? b.centerX > title.centerX && b.centerX <= title.centerX + 280
                                : b.centerX < title.centerX && b.centerX >= title.centerX - 280)
                        );
                }

                if (!pool.length) {
                    return {ok:false, reason:'week nav button not found near title', title:title.text};
                }

                pool.sort((a,b) => Math.abs(a.centerX - title.centerX) - Math.abs(b.centerX - title.centerX));
                const target = pool[0];

                try { target.el.scrollIntoView({block:'center', inline:'center'}); } catch(e) {}
                target.el.click();

                return {
                    ok:true,
                    title:title.text.match(titleRe)?.[0] || title.text,
                    buttonText:target.text || target.label || direction,
                    x:target.centerX,
                    y:target.centerY,
                    method:'dom-near-week-title'
                };
            }""",
            direction,
        )
    except Exception as e:
        print(f"[주의] {label} DOM 클릭 중 오류: {e}")
        return False

    if not result or not result.get("ok"):
        reason_msg = result.get("reason") if isinstance(result, dict) else "unknown"
        print(f"[오류] {label} 버튼을 DOM 기준으로 찾지 못했습니다: {reason_msg}")
        return False

    print(f"[진행] {label} 버튼 DOM 클릭 완료: method={result.get('method')}, title={result.get('title')}")

    try:
        if before_text:
            page.wait_for_function(
                r"""(oldTitle) => {
                    const text = (document.body.innerText || '').replace(/\s+/g, ' ').trim();
                    const titles = text.match(/\d{4}\s*년\s*\d{1,2}\s*월\s*\d+\s*주차/g) || [];
                    return titles.some(t => t !== oldTitle);
                }""",
                arg=before_text,
                timeout=4500,
            )
    except Exception:
        pass

    wait_light(page, 700)
    after = get_calendar_week_title(page)
    after_text = after.get("text") if after else ""
    if after_text:
        print(f"[완료] 현재 주차: {after_text}")
    if before_text and after_text and before_text == after_text:
        print(f"[주의] {label} 클릭 후 주차 제목이 바뀌지 않았습니다: {before_text}")
        return False
    return True


def ensure_target_week_visible_for_date_selection(page: Page, day_number: str) -> bool:
    """
    MRT 달력은 일~토 기준으로 한 주만 보여줍니다.
    target_date 가 일요일이면 (= 오늘이 토요일이거나 --date 인자로 일요일 지정),
    현재 주차 화면은 보통 "지난 일요일" 까지만 보여서 target 일요일이 안 보입니다.
    이 경우 주차 좌우 버튼 대신 주차 제목 클릭 → 날짜 선택 팝업에서 target 일요일을 선택합니다.

    주의: datetime.now() 가 아니라 target_date() 를 기준으로 판정해야 --date 로 다른 날짜
    지정 시에도 올바르게 동작.
    """
    scroll_calendar_week_title_into_view(page)
    tgt_dt = target_date()
    # 일요일(weekday=6)이 target 이면 → 주차 boundary 넘는 케이스
    if tgt_dt.weekday() == 6:
        if is_calendar_day_visible(page, day_number):
            print("[안내] 목표 일요일 날짜가 이미 현재 화면에 보여 주차 변경을 생략합니다.")
            return False
        print(f"[안내] target={tgt_dt.strftime('%Y-%m-%d')} 가 일요일이고 현재 주차에 없음 → 다음 주 이동")
        # 1) 매 런 검증된 '다음 주' 화살표를 우선 사용 (날짜팝업 datepicker 는 이 페이지에서 불안정 → 토요일 25건 전부 실패한 원인)
        if click_calendar_week_nav(page, "next", "일요일 날짜 선택 전"):
            wait_light(page, 500)
            if is_calendar_day_visible(page, day_number):
                print("[완료] 다음 주 이동으로 목표 일요일 노출 확인")
                return True
            print("[주의] 다음 주 이동했으나 목표 일요일 미노출 → 날짜 팝업 폴백")
        else:
            print("[주의] 다음 주 화살표 이동 실패 → 날짜 팝업 폴백")
        # 2) 폴백: 날짜 팝업(datepicker)
        if change_calendar_week_by_datepicker(page, tgt_dt, "일요일 날짜 선택 전"):
            wait_light(page, 400)
            return True
        raise Exception("일요일 날짜 선택 전 다음 주 이동에 실패했습니다 (화살표+날짜팝업 모두 실패).")
    return False


def refresh_calendar_next_prev_before_save(page: Page) -> bool:
    """
    예약인원 수정 버튼 클릭 전, 입력한 0명 값을 MRT 화면 내부 상태에 확실히 반영하기 위해
    실제 달력 주차 버튼으로 다음 주로 이동한 뒤 다시 원래 주차로 돌아옵니다.

    기존 날짜 팝업 방식은 주차 제목/날짜만 클릭되고 화면 내부 수정 상태가 저장 버튼에
    반영되지 않는 케이스가 있어, 기본 동작을 <다음 주 버튼 → 지난 주 버튼>으로 변경했습니다.
    버튼 이동이 실패할 때만 날짜 팝업 방식으로 fallback 합니다.
    """
    print("[진행] 저장 전 주차 새로고침 V10: 실제 다음 주 이동 → 원래 주차 복귀")
    scroll_calendar_week_title_into_view(page)

    original = get_calendar_week_title(page)
    original_text = original.get("text") if original else ""
    if original_text:
        print(f"[안내] 저장 전 원래 주차: {original_text}")

    # 혹시 마지막 셀 input focus가 남아 있으면 blur/change가 발생하도록 먼저 포커스를 해제합니다.
    try:
        page.keyboard.press("Escape")
        page.wait_for_timeout(150)
        # blur: 페이지 body 좌상단 클릭 (좌표가 아닌 element 기준)
        try:
            page.locator("body").click(position={"x": 5, "y": 5}, timeout=500)
        except Exception:
            pass
        page.wait_for_timeout(250)
    except Exception:
        pass

    # 1) 우선 실제 주차 이동 버튼을 클릭합니다.
    moved_next = click_calendar_week_nav(page, "next", "저장 전 실제 다음 주 이동")
    if moved_next:
        page.wait_for_timeout(900)
        moved_back = click_calendar_week_nav(page, "prev", "저장 전 원래 주차 복귀")
        if moved_back:
            page.wait_for_timeout(900)
            after = get_calendar_week_title(page)
            after_text = after.get("text") if after else ""
            if original_text and after_text and after_text != original_text:
                print(f"[주의] 원래 주차 복귀 확인 불일치: 원래={original_text} / 현재={after_text}")
            else:
                print(f"[완료] 저장 전 실제 주차 이동/복귀 완료: {after_text or original_text}")
            return True
        print("[주의] 다음 주 이동은 성공했지만 원래 주차 복귀 실패 → 날짜 팝업 fallback 시도")
    else:
        print("[주의] 실제 다음 주 버튼 클릭 실패 → 날짜 팝업 fallback 시도")

    # 2) fallback: 날짜 팝업으로 차주 target date 선택 후 다시 target date 주차로 복귀합니다.
    #    today 기준 복귀는 토요일→일요일 작업 시 다른 주차로 돌아갈 수 있으므로 target_date()를 기준으로 합니다.
    base_dt = target_date()
    next_week_dt = base_dt + timedelta(days=7)

    moved_next_popup = change_calendar_week_by_datepicker(page, next_week_dt, "저장 전 차주 선택 fallback")
    if not moved_next_popup:
        print("[주의] 저장 전 날짜 팝업 차주 선택도 실패 → 그래도 저장 버튼 클릭은 계속 진행")
        return False

    moved_back_popup = change_calendar_week_by_datepicker(page, base_dt, "저장 전 원래 주차 복귀 fallback")
    if not moved_back_popup:
        print("[주의] 저장 전 날짜 팝업 원래 주차 복귀 실패")
        return False

    print("[완료] 저장 전 날짜 팝업 fallback 주차 새로고침 완료")
    return True


def save_screenshot(page: Page, name: str) -> str:
    """No-op: 스크린샷 저장 비활성화 (호출처 호환을 위해 함수만 유지)."""
    return ""


def safe_click_text(page: Page, texts, timeout=5000) -> bool:
    if isinstance(texts, str):
        texts = [texts]

    for text in texts:
        for exact in [True, False]:
            try:
                loc = page.get_by_text(text, exact=exact)
                if loc.count() > 0:
                    loc.first.wait_for(state="visible", timeout=timeout)
                    loc.first.click(timeout=timeout)
                    page.wait_for_timeout(700)
                    return True
            except Exception:
                continue
    return False


def click_confirm_if_exists(page: Page) -> bool:
    for text in ["확인", "저장", "예", "네", "Confirm", "OK"]:
        try:
            loc = page.get_by_text(text, exact=True)
            if loc.count() > 0 and loc.first.is_visible():
                loc.first.click(timeout=2500)
                page.wait_for_timeout(400)
                return True
        except Exception:
            pass
    return False


def goto_tour_ticket_product_list(page: Page):
    print("[진행] 투어·티켓 상품 목록 확인")

    # 이미 목록 화면이면 메뉴를 다시 타지 않고 즉시 다음 검색으로 진행
    if is_product_list_screen(page):
        record_product_list_url(page)
        print("[완료] 이미 투어·티켓 상품 목록 화면")
        return

    # 이전 상품의 수정 화면에 남아 있으면 먼저 나가기를 다시 시도
    if is_product_edit_screen(page):
        print("[주의] 아직 상품 수정 화면에 남아 있습니다 → 나가기 재시도")
        click_exit(page)
        if is_product_list_screen(page):
            record_product_list_url(page)
            print("[완료] 나가기 재시도 후 상품 목록 화면 복귀")
            return

    # 캐시된 상품 목록 URL이 있으면 직접 이동이 가장 안정적임
    global PRODUCT_LIST_URL_CACHE
    if PRODUCT_LIST_URL_CACHE:
        try:
            print("[진행] 캐시된 상품 목록 URL로 직접 이동")
            page.goto(PRODUCT_LIST_URL_CACHE, wait_until="domcontentloaded", timeout=10000)
            if wait_product_list_ready(page, timeout=5000):
                print("[완료] 상품 목록 URL 직접 이동 완료")
                return
        except Exception:
            pass

    print("[진행] 투어·티켓 상품 목록으로 이동")

    safe_click_text(page, ["상품"], timeout=1800)
    wait_light(page, 300)

    if not safe_click_text(page, ["투어·티켓 상품 (신규)", "투어·티켓 상품", "투어 티켓 상품"], timeout=2500):
        # 메뉴 텍스트 클릭 실패: href 셀렉터 → URL 직접 이동 순으로 폴백
        print("[주의] 메뉴 텍스트 클릭 실패 → href 셀렉터 재시도")
        try:
            page.locator("a[href*='/products/experiences']").first.click(timeout=2500, force=True)
        except Exception:
            print("[주의] href 클릭도 실패 → URL 직접 이동")
            try:
                page.goto("https://partner.myrealtrip.com/products/experiences",
                          wait_until="domcontentloaded", timeout=10000)
            except Exception:
                pass
        wait_light(page, 800)

    if not wait_product_list_ready(page, timeout=7000):
        raise Exception("투어·티켓 상품 목록 화면으로 이동하지 못했습니다.")

    record_product_list_url(page)
    print("[완료] 투어·티켓 상품 목록 화면 진입")

def ensure_search_type_product_id(page: Page):
    """
    검색 기준을 상품 ID로 맞춤 V2.

    문제:
    - '상품명' 드롭다운 클릭 후, 드롭다운 안의 '상품 ID'를 제대로 클릭하지 못함.

    V2:
    - 검색창 왼쪽 드롭다운 버튼의 좌표를 먼저 찾음
    - 드롭다운을 열고, 열린 메뉴 안의 '상품 ID' 위치를 JS로 찾아 실제 좌표 클릭
    - 실패 시 캡처 기준 fallback 좌표 클릭
    """
    print("[진행] 검색 기준 상품 ID 설정")

    # 이미 상품 ID로 보이면 그대로 통과
    try:
        selected = page.evaluate(
            """() => {
                function norm(s) { return (s || '').replace(/\\s+/g, ' ').trim(); }
                const nodes = Array.from(document.querySelectorAll('button,div,span'))
                  .map(el => {
                    const text = norm(el.innerText || el.textContent || '');
                    const r = el.getBoundingClientRect();
                    const st = window.getComputedStyle(el);
                    return {text, x:r.x, y:r.y, width:r.width, height:r.height,
                            display:st.display, visibility:st.visibility};
                  })
                  .filter(o =>
                    o.display !== 'none' &&
                    o.visibility !== 'hidden' &&
                    o.text === '상품 ID' &&
                    o.width >= 70 &&
                    o.width <= 220 &&
                    o.height >= 25 &&
                    o.height <= 70 &&
                    o.y >= 150 &&
                    o.y <= 270
                  )
                  .sort((a,b) => a.y - b.y || a.x - b.x);
                return nodes[0] || null;
            }"""
        )
        if selected:
            print("[완료] 검색 기준이 이미 상품 ID로 보입니다.")
            return
    except Exception:
        pass

    # 1) 검색 기준 드롭다운 버튼 찾기: 상품명/상품 ID가 보이는 좌측 버튼
    dropdown_btn = page.evaluate(
        """() => {
            function norm(s) { return (s || '').replace(/\\s+/g, ' ').trim(); }

            const candidates = Array.from(document.querySelectorAll('button,div,span,[role="button"]'))
              .map(el => {
                const text = norm(el.innerText || el.textContent || '');
                const r = el.getBoundingClientRect();
                const st = window.getComputedStyle(el);
                return {text, x:r.x, y:r.y, width:r.width, height:r.height,
                        display:st.display, visibility:st.visibility};
              })
              .filter(o =>
                o.display !== 'none' &&
                o.visibility !== 'hidden' &&
                (o.text === '상품명' || o.text === '상품 ID') &&
                o.width >= 70 &&
                o.width <= 230 &&
                o.height >= 25 &&
                o.height <= 80 &&
                o.y >= 150 &&
                o.y <= 280
              )
              .sort((a,b) => a.y - b.y || a.x - b.x);

            return candidates[0] || null;
        }"""
    )

    if dropdown_btn:
        print(f"[진행] 검색 기준 드롭다운 클릭 (JS 직접): '{dropdown_btn.get('text')}'")
        try:
            page.get_by_role("combobox").first.click(timeout=2000, force=True)
        except Exception:
            try:
                page.locator("[role='combobox'], [class*=Select]").first.click(timeout=2000, force=True)
            except Exception:
                pass
        page.wait_for_timeout(600)
    else:
        print("[주의] 검색 기준 드롭다운 탐색 실패 → 셀렉터 fallback")
        try:
            page.get_by_role("combobox", name="상품명").first.click(timeout=2000)
        except Exception:
            try:
                page.locator("[role='combobox']").first.click(timeout=2000, force=True)
            except Exception:
                pass
        page.wait_for_timeout(600)

    # 2) 열린 메뉴 안의 상품 ID 클릭
    item = page.evaluate(
        """() => {
            function norm(s) { return (s || '').replace(/\\s+/g, ' ').trim(); }

            const candidates = Array.from(document.querySelectorAll('button,div,span,li,[role="option"],[role="menuitem"]'))
              .map(el => {
                const text = norm(el.innerText || el.textContent || '');
                const r = el.getBoundingClientRect();
                const st = window.getComputedStyle(el);
                return {text, x:r.x, y:r.y, width:r.width, height:r.height,
                        display:st.display, visibility:st.visibility};
              })
              .filter(o =>
                o.display !== 'none' &&
                o.visibility !== 'hidden' &&
                o.text === '상품 ID' &&
                o.width >= 50 &&
                o.width <= 260 &&
                o.height >= 20 &&
                o.height <= 80 &&
                o.y >= 180 &&
                o.y <= 380
              )
              .sort((a,b) => {
                // 드롭다운 내부 항목은 보통 버튼 아래쪽에 위치
                return a.y - b.y || a.x - b.x;
              });

            return candidates[candidates.length - 1] || candidates[0] || null;
        }"""
    )

    if item:
        print(f"[진행] 드롭다운 항목 상품 ID 클릭 (셀렉터)")
        try:
            page.get_by_role("option", name="상품 ID").first.click(timeout=2000, force=True)
        except Exception:
            try:
                page.get_by_text("상품 ID", exact=True).first.click(timeout=2000, force=True)
            except Exception:
                pass
        page.wait_for_timeout(700)
    else:
        print("[주의] 상품 ID 항목 탐색 실패 → 셀렉터 fallback")
        try:
            page.get_by_role("option", name="상품 ID").first.click(timeout=2000)
        except Exception:
            try:
                page.get_by_text("상품 ID", exact=True).first.click(timeout=2000, force=True)
            except Exception:
                pass
        page.wait_for_timeout(700)

    # 3) 확인
    try:
        body = normalize_text(page.locator("body").inner_text(timeout=2000))
        print("[완료] 검색 기준 상품 ID 설정 시도 완료")
    except Exception:
        pass


def fill_product_id_and_search(page: Page, product_id: str):
    print(f"[진행] 상품 ID 검색: {product_id}")

    ensure_search_type_product_id(page)

    search_input = None
    for selector in [
        "xpath=//input[contains(@placeholder, '검색어')]",
        "xpath=//input[contains(@placeholder, '입력')]",
        "xpath=//input[@type='text']",
        "xpath=//input",
    ]:
        try:
            locs = page.locator(selector)
            for i in range(locs.count()):
                inp = locs.nth(i)
                if not inp.is_visible():
                    continue
                box = inp.bounding_box(timeout=1000)
                if not box:
                    continue
                if box["y"] < 120 or box["y"] > 340 or box["width"] < 150:
                    continue
                search_input = inp
                break
            if search_input:
                break
        except Exception:
            continue

    if not search_input:
        raise Exception("상품 ID 검색 입력창을 찾지 못했습니다.")

    search_input.click(timeout=3000)
    page.keyboard.press("Control+A")
    page.keyboard.press("Backspace")
    page.keyboard.type(str(product_id), delay=20)
    page.keyboard.press("Enter")

    # V4: networkidle 대신 검색 결과에 상품 ID가 보일 때까지만 대기
    try:
        page.wait_for_function(
            """(pid) => {
                const text = (document.body.innerText || '').replace(/\\s+/g, ' ').trim();
                return text.includes(String(pid));
            }""",
            arg=str(product_id),
            timeout=7000,
        )
        wait_light(page, 400)
    except Exception:
        wait_light(page, 1000)

    body = normalize_text(page.locator("body").inner_text(timeout=4000))
    if str(product_id) not in body:
        raise Exception(f"상품 ID {product_id} 검색 결과를 찾지 못했습니다.")

    print(f"[완료] 상품 ID 검색 결과 확인: {product_id}")


def click_edit_button_for_product(page: Page, product_id: str):
    print(f"[진행] 상품 수정 버튼 클릭: {product_id}")

    result = page.evaluate(
        """(pid) => {
            function norm(s) { return (s || '').replace(/\\s+/g, ' ').trim(); }

            const rows = Array.from(document.querySelectorAll('tr, div'))
              .map(el => {
                const text = norm(el.innerText || el.textContent || '');
                const r = el.getBoundingClientRect();
                const st = window.getComputedStyle(el);
                return {el, text, x:r.x, y:r.y, width:r.width, height:r.height,
                        display:st.display, visibility:st.visibility, area:r.width*r.height};
              })
              .filter(o =>
                o.display !== 'none' &&
                o.visibility !== 'hidden' &&
                o.width > 300 &&
                o.height > 30 &&
                o.text.includes(String(pid)) &&
                o.text.includes('수정')
              )
              .sort((a,b) => a.area - b.area);

            if (!rows.length) return null;

            const row = rows[0];

            const btns = Array.from(document.querySelectorAll('button, a, span, div'))
              .map(el => {
                const text = norm(el.innerText || el.textContent || '');
                const r = el.getBoundingClientRect();
                const st = window.getComputedStyle(el);
                return {text, x:r.x, y:r.y, width:r.width, height:r.height,
                        display:st.display, visibility:st.visibility};
              })
              .filter(o =>
                o.display !== 'none' &&
                o.visibility !== 'hidden' &&
                o.text === '수정' &&
                o.width > 20 &&
                o.height > 15 &&
                o.y >= row.y - 5 &&
                o.y <= row.y + row.height + 5
              )
              .sort((a,b) => b.x - a.x);

            if (!btns.length) return null;

            const b = btns[0];
            return {x:b.x + b.width/2, y:b.y + b.height/2};
        }""",
        str(product_id),
    )

    if result:
        # JS 안에서 직접 클릭 (좌표 없음)
        page.evaluate(
            """(pid) => {
                const rows = Array.from(document.querySelectorAll("table tr, tbody tr, [role='row']"));
                for (const r of rows) {
                    if ((r.textContent || "").includes(pid)) {
                        const btn = Array.from(r.querySelectorAll("button, a"))
                            .find(el => (el.textContent || "").trim() === "수정");
                        if (btn) { btn.click(); return true; }
                    }
                }
                return false;
            }""",
            str(product_id),
        )
        wait_edit_page_ready(page, timeout=8000)
    else:
        print("[주의] 행 기준 수정 버튼 탐색 실패 → 우측 수정 버튼 fallback")
        try:
            loc = page.get_by_text("수정", exact=True)
            if loc.count() > 0:
                loc.last.click(timeout=4000)
                wait_edit_page_ready(page, timeout=8000)
            else:
                raise Exception()
        except Exception:
            # 수정 버튼: 첫 번째 행의 우측 '수정' 링크/버튼
            try:
                page.get_by_role("link", name="수정", exact=True).first.click(timeout=4000, force=True)
            except Exception:
                try:
                    page.get_by_role("button", name="수정", exact=True).first.click(timeout=4000, force=True)
                except Exception:
                    try:
                        page.locator("table tr").nth(1).get_by_text("수정", exact=True).click(timeout=4000, force=True)
                    except Exception:
                        pass
            wait_edit_page_ready(page, timeout=8000)

    body = normalize_text(page.locator("body").inner_text(timeout=8000))
    if "예약 인원 관리" not in body and "상품 정보 등록" not in body:
        raise Exception("상품 수정 페이지로 진입하지 못했습니다.")

    print("[완료] 상품 수정 페이지 진입")


def goto_inventory_management(page: Page):
    print("[진행] 예약 인원 관리 클릭")

    if not safe_click_text(page, ["예약 인원 관리"], timeout=5000):
        # 텍스트 클릭 실패: 더 광범위한 텍스트 매칭 시도
        print("[주의] 예약 인원 관리 메뉴 텍스트 클릭 실패 → 셀렉터 fallback")
        try:
            page.get_by_text(re.compile(r"예약\s*인원\s*관리")).first.click(timeout=3000, force=True)
        except Exception:
            try:
                page.locator("a, button, span, div").filter(has_text="예약 인원 관리").first.click(timeout=3000, force=True)
            except Exception:
                pass
        page.wait_for_timeout(1200)

    if not wait_inventory_page_ready(page, timeout=7000):
        body = normalize_text(page.locator("body").inner_text(timeout=4000))
        if "예약 인원 관리" not in body:
            raise Exception("예약 인원 관리 화면으로 이동하지 못했습니다.")

    print("[완료] 예약 인원 관리 화면 진입")




def get_calendar_date_column(page: Page, day_number: str):
    print(f"[진행] 캘린더 날짜 컬럼 찾기: {day_number}")

    scroll_calendar_week_title_into_view(page)

    info = page.evaluate(
        r"""(dayNumber) => {
            function norm(s) { return (s || '').replace(/\s+/g, ' ').trim(); }
            function visible(el) {
                if (!el) return false;
                const r = el.getBoundingClientRect();
                const st = window.getComputedStyle(el);
                const vh = window.innerHeight || 1080;
                const vw = window.innerWidth || 1920;
                return st.display !== 'none' &&
                       st.visibility !== 'hidden' &&
                       st.opacity !== '0' &&
                       r.width > 0 &&
                       r.height > 0 &&
                       r.bottom >= 0 &&
                       r.top <= vh &&
                       r.right >= 0 &&
                       r.left <= vw;
            }
            function rect(el) {
                const r = el.getBoundingClientRect();
                return {el, text:norm(el.innerText || el.textContent || ''), x:r.x, y:r.y, width:r.width, height:r.height,
                        centerX:r.x + r.width/2, centerY:r.y + r.height/2,
                        x1:r.x, x2:r.x + r.width, area:r.width*r.height};
            }
            const titleRe = /\d{4}\s*년\s*\d{1,2}\s*월\s*\d+\s*주차/;
            const titles = Array.from(document.querySelectorAll('button,[role="button"],span,div,p,strong,b,h1,h2,h3,h4,a'))
                .filter(visible)
                .map(rect)
                .filter(o => titleRe.test(o.text))
                .sort((a,b) => a.area - b.area || a.y - b.y);
            const title = titles[0] || null;
            const minY = title ? title.centerY : 0;

            const raw = Array.from(document.querySelectorAll('th,td,[role="columnheader"],[role="cell"],div,span,button'))
                .filter(visible)
                .map(el => ({source:el, text:norm(el.innerText || el.textContent || ''), ...rect(el)}))
                .filter(o => o.text === String(dayNumber) && o.centerY > minY + 10);

            const cells = [];
            for (const o of raw) {
                let cell = o.source.closest('th,td,[role="columnheader"],[role="cell"]') || o.source;
                if (!visible(cell)) continue;
                const c = rect(cell);
                if (c.width < 30 || c.height < 18) continue;
                if (title && c.centerY < title.centerY + 20) continue;
                if (c.text !== String(dayNumber) && !c.text.split(' ').includes(String(dayNumber))) continue;
                // 팝업 달력 셀은 테이블 헤더보다 작으므로 제외
                if (c.width < 45 && c.height < 45) continue;
                if (!cells.some(x => Math.abs(x.x - c.x) < 2 && Math.abs(x.y - c.y) < 2)) cells.push(c);
            }

            cells.sort((a,b) => {
                const targetY = title ? title.centerY + 85 : a.y;
                return Math.abs(a.centerY - targetY) - Math.abs(b.centerY - targetY) || b.width - a.width || a.x - b.x;
            });

            if (!cells.length) return null;
            const c = cells[0];
            return {x1:c.x1, x2:c.x2, centerX:c.centerX, y:c.y, text:c.text, width:c.width, height:c.height};
        }""",
        str(day_number),
    )

    if not info:
        raise Exception(f"캘린더에서 날짜 {day_number} 컬럼을 찾지 못했습니다.")

    print(f"[완료] 날짜 컬럼 확인: {day_number} / x={int(info['centerX'])}, y={int(info['y'])}")
    return info


def enable_inventory_edit_mode(page: Page) -> str:
    """
    페이지 처음 진입 시 모든 인원 셀 input 이 disabled 상태.
    "예약 인원 수정" 버튼을 한 번 누르면 편집 모드 활성화되고 disabled 가 풀림.

    반환값 (기존 bool → 3-상태 문자열):
      "ok"              : 편집 모드 활성화됨 (또는 이미 활성화)
      "no_button"       : '예약 인원 수정' 버튼이 DOM 에 없음 → 판매중 아님 (SKIP 대상)
      "activate_failed" : 버튼은 있으나(=판매중) 활성화 실패 → 실패(수동확인) + 재시도 대상

    ⚠️ 토요일(일요일 타깃) 콜드로드에서 편집버튼 클릭이 3초 타임아웃 나면
       예전엔 무조건 '버튼 없음 → 판매중 아님 SKIP' 으로 오판 → 판매중 상품이
       마감 안 되고 조용히 넘어감(성공/스킵 위장). 그래서 버튼 DOM 존재를 명시적으로
       구분하고, 버튼 렌더/활성화를 넉넉히 폴링·재시도한다.
    """
    import time as _t

    def _any_enabled():
        try:
            return page.evaluate(
                """() => {
                    const inputs = document.querySelectorAll('input[name^="stockBundles."][name$=".remainQuantity"]');
                    return Array.from(inputs).some(i => !i.disabled);
                }"""
            )
        except Exception:
            return False

    def _button_exists():
        # ⚠️ offsetParent 는 position:fixed 요소에서 null → MRT 하단 고정 버튼바가
        #    '없음' 으로 오판됨(3887808/5724343/4700281 사례). getClientRects 로 판정.
        try:
            return page.evaluate(
                """() => {
                    const btns = Array.from(document.querySelectorAll('button'));
                    return btns.some(b => (b.innerText || '').trim() === '예약 인원 수정'
                                          && b.getClientRects().length > 0);
                }"""
            )
        except Exception:
            return False

    def _page_ready():
        """
        재고 관리 화면이 실제로 그려졌는지(=판정할 자격이 있는지) 확인.
        이 앵커들이 없으면 아직 로딩 중/로그인 리다이렉트 등 → '판매중 아님' 단정 금지.
        """
        try:
            return page.evaluate(
                """() => {
                    const t = document.body ? (document.body.innerText || '') : '';
                    const hasAnchor = t.includes('예약 인원 관리') || t.includes('여행자 상품 보기')
                                      || t.includes('판매 중지') || t.includes('판매 재개');
                    const hasCells = document.querySelectorAll('input[name^="stockBundles."]').length > 0;
                    return hasAnchor || hasCells;
                }"""
            )
        except Exception:
            return False

    # 0) 이미 활성화 상태면 바로 통과 (여기서 다시 버튼 누르면 저장 팝업 뜸 → 절대 금지)
    if _any_enabled():
        print("[안내] 이미 편집 모드. 활성화 단계 skip")
        return "ok"

    # 1) 버튼 렌더 대기 (콜드 로드 방어): 최대 12초 폴링
    #    워커의 '첫 상품' 은 SPA 번들/인증 로딩 때문에 늦게 그려짐 → 넉넉히.
    deadline_btn = _t.time() + 12.0
    btn_seen = False
    ready_seen = False
    while _t.time() < deadline_btn:
        if _any_enabled():   # 폴링 중 활성화되면 통과
            print("[완료] 편집 모드 활성화됨 (input.disabled 해제)")
            return "ok"
        if _button_exists():
            btn_seen = True
            break
        if _page_ready():
            ready_seen = True
        page.wait_for_timeout(300)

    if not btn_seen:
        if not ready_seen and not _page_ready():
            # 화면 자체가 안 그려짐 → 판매중 여부를 판정할 수 없음.
            # SKIP(판매중 아님) 으로 단정하면 열린 상품을 조용히 놓침 → 실패 처리.
            print("[주의] 재고 화면 미로드(12초) → 판매중 여부 판정 불가 → 실패 처리")
            return "not_loaded"
        # 화면은 떴는데 버튼이 없음 = 진짜 판매중 아님
        print("[안내] 화면 로드됨 + '예약 인원 수정' 버튼 없음 → 판매중 아님")
        return "no_button"

    # 2) 버튼이 있으니 클릭 → 활성화. 최대 3회 클릭, 회당 활성화 폴링(3초).
    print("[진행] '예약 인원 수정' 버튼 클릭 - 편집 모드 활성화")
    for click_try in range(3):
        # 클릭 직전 이미 활성화됐으면 중단 (재클릭=저장 팝업 방지)
        if _any_enabled():
            print("[완료] 편집 모드 활성화됨 (input.disabled 해제)")
            return "ok"
        try:
            page.get_by_role("button", name="예약 인원 수정", exact=True).first.click(timeout=3000, force=True)
        except Exception:
            try:
                page.locator("button").filter(has_text="예약 인원 수정").first.click(timeout=3000, force=True)
            except Exception as e:
                print(f"[주의] '예약 인원 수정' 버튼 클릭 실패({click_try + 1}/3): {e}")
        # 클릭 후 활성화 폴링 (회당 최대 3초)
        deadline = _t.time() + 3.0
        while _t.time() < deadline:
            if _any_enabled():
                print("[완료] 편집 모드 활성화됨 (input.disabled 해제)")
                return "ok"
            page.wait_for_timeout(250)
        # 혹시 클릭이 저장 확인 팝업을 띄웠으면 '취소' 로 닫아 커밋 위장 방지
        try:
            cancel = page.get_by_role("button", name="취소", exact=True)
            if cancel.count() > 0 and cancel.first.is_visible():
                cancel.first.click(timeout=1500)
                page.wait_for_timeout(300)
        except Exception:
            pass

    print("[주의] 버튼은 있으나 편집 모드 활성화 실패 → 실패 처리(수동확인)")
    return "activate_failed"


# 캘린더 첫 컬럼이 '일'요일. Python weekday: 월=0..일=6 → cal_idx: 일=0..토=6
# 매핑: cal_idx = (python_weekday + 1) % 7
def _weekday_to_calendar_idx(d) -> int:
    """datetime.date 또는 datetime.datetime 의 weekday() → 캘린더 컬럼 인덱스 (일=0..토=6)."""
    return (d.weekday() + 1) % 7


def dismiss_notice_popup(page) -> bool:
    """MRT 파트너 포털 공지 팝업 닫기.
    '7일간 보지 않기'(있으면 우선 — 7일간 재출현 억제) → 없으면 '닫기'.
    팝업이 재고 화면/버튼을 가려 클릭을 방해하는 것을 막는다.
    """
    try:
        clicked = page.evaluate(
            """() => {
                const btns = Array.from(document.querySelectorAll('button, [role="button"]'));
                const vis = b => b && b.offsetParent && b.getBoundingClientRect().width > 0;
                const find = txt => btns.find(b => (b.innerText || '').trim() === txt && vis(b));
                const b = find('7일간 보지 않기') || find('닫기');
                if (b) { b.click(); return (b.innerText || '').trim(); }
                return '';
            }"""
        )
        if clicked:
            print(f"[안내] 공지 팝업 닫음: '{clicked}'")
            page.wait_for_timeout(300)
            return True
    except Exception as e:
        print(f"[안내] 공지 팝업 닫기 skip: {str(e)[:80]}")
    return False


def split_across(total: int, n: int) -> list:
    """
    수량을 셀(=픽업 옵션) 개수만큼 나눈다. 나머지는 앞쪽 셀부터 1씩.
        12, 2개 -> [6, 6]      (도쿄 6 / 신주쿠 6)
        13, 2개 -> [7, 6]
         5, 3개 -> [2, 2, 1]
    """
    if n <= 0:
        return []
    base, rem = divmod(max(0, int(total)), n)
    return [base + (1 if i < rem else 0) for i in range(n)]


def set_target_date_inventory_zero(page: Page):
    """마감용 하위호환 래퍼. 모든 셀에 0."""
    return set_target_date_inventory(page, total=0, split=False)


def _row_person(text: str) -> str:
    """줄 맨 앞의 인원구분. '성인 [코스 A] ... ' -> '성인'."""
    m = re.match(r"\s*(성인|소인|유아|어른|아동)", text or "")
    return m.group(1) if m else ""


def _pick_open_cells(cells: list, course=None) -> tuple:
    """
    오픈할 셀만 고른다. 반환 (고른셀, 사람이 읽을 설명).

    두 가지를 반드시 걸러야 한다.
      1) 인원구분 : 성인 줄에만 넣는다. 2026-08-23 운영팀 확인 사항이다.
                    소인이 별도 재고를 가진 상품(4397040 교토, 5616354 블루마운틴)은
                    소인이 0 으로 남는다 -- 알고 그렇게 두는 것이다.
                    (그 전에는 성인/소인에 수량을 나눠 넣고 있었다)
      2) 코스     : 상품ID 하나에 코스가 여러 개 있다.
                    5624093 = 비에이 하이라이트 / 비에이 시그니처 / 비에이 & 후라노
                    course 키워드로 한 코스만 남긴다.

    남은 셀 = 그 코스의 '출발(픽업)' 개수이고, 여기서만 수량을 나눈다.
    이게 원래 의도했던 'Mt. Fuji Highlight 12 -> 도쿄 6 / 신주쿠 6' 이다.
    """
    notes: list[str] = []

    adults = [c for c in cells if _row_person(c.get("row_text", "")) not in ("소인", "아동", "유아")]
    dropped = len(cells) - len(adults)
    if adults and dropped:
        notes.append(f"소인/유아 {dropped}줄 제외(성인 줄에만 입력)")
        cells = adults
    elif not adults:
        notes.append("인원구분을 못 읽어 전체 줄 대상")

    if course:
        # 조각이 여러 개면 전부 들어있는 줄만 고른다.
        #   Itoshima Marine = ['여름코스', '하루 종일']
        #   ('여름코스' 만으로는 '조기 하차' 줄까지 걸린다)
        keys = [re.sub(r"\s+", "", str(k)) for k in
                (course if isinstance(course, (list, tuple)) else [course]) if str(k).strip()]
        # 줄 텍스트를 못 읽은 칸이 있으면 코스를 가를 수 없다. 찍어서 넣으면 안 된다.
        blank = [c for c in cells if not c.get("row_text")]
        if blank:
            raise Exception(
                f"줄 이름을 읽지 못한 입력칸이 {len(blank)}개 있어 코스를 가릴 수 없습니다 "
                f"({[c.get('name') for c in blank]}). 화면을 다시 불러온 뒤 재시도하세요."
            )
        matched = [c for c in cells
                   if all(k in re.sub(r"\s+", "", c.get("row_text", "")) for k in keys)]
        if not matched:
            seen = sorted({c.get("row_text", "") for c in cells})
            raise Exception(
                f"코스 {course!r} 에 해당하는 줄을 찾지 못했습니다. 화면에 있는 줄: {seen}"
            )
        notes.append(f"코스 {course!r} {len(matched)}줄")
        cells = matched

    return cells, " / ".join(notes)


def set_target_date_inventory(page: Page, total: int = 0, split: bool = False,
                              course=None):
    """
    target 날짜의 인원 셀(들)에 수량 입력.
    selector: input[name='stockBundles.X.stocks.N.remainQuantity']
        - X = stockBundle 인덱스 (옵션 여러개면 0,1,2,...)
        - N = 요일 인덱스 (일=0..토=6) = (target weekday + 1) % 7

    total / split
        마감 : total=0, split=False  -> 모든 셀 0
        오픈 : total=N, split=True   -> N 을 셀 개수로 나눠서 배분
               한 상품에 픽업지가 둘이면 stockBundles 가 둘로 잡히므로
               'Mt. Fuji Highlight 12' 는 도쿄 6 / 신주쿠 6 이 된다.
    """
    day = target_day_number()
    moved_for_sunday = ensure_target_week_visible_for_date_selection(page, day)
    target_cal_idx = _weekday_to_calendar_idx(target_date())
    print(f"[진행] target weekday={target_weekday_key()} → 캘린더 컬럼 idx={target_cal_idx}")

    try:
        col = get_calendar_date_column(page, day)
    except Exception:
        # 토요일 실행 시 일요일 날짜가 현재 주차에 없어 발생하는 케이스 보완
        if target_weekday_key() == "SUN" and not moved_for_sunday:
            print("[주의] 목표 일요일 날짜를 찾지 못해 날짜 팝업으로 내일 날짜 선택 후 재시도합니다.")
            change_calendar_week_by_datepicker(page, target_date(), "일요일 날짜 재탐색")
            col = get_calendar_date_column(page, day)
        else:
            raise

    print(f"[진행] {target_date_label()} 날짜 잔여 인원 0명 처리")

    # virtual scroll 대응: 모든 table row 를 한번씩 scrollIntoView 해서
    # 화면 밖 옵션 입력 input 도 DOM 에 렌더링되도록 강제.
    try:
        page.evaluate(
            """() => {
                const rows = document.querySelectorAll('table tbody tr');
                for (const row of rows) {
                    try { row.scrollIntoView({block: 'nearest', behavior: 'instant'}); } catch(e) {}
                }
                // 다시 맨 위로
                window.scrollTo(0, 0);
            }"""
        )
        page.wait_for_timeout(400)
    except Exception:
        pass

    # 인원 셀 = <input name="stockBundles.X.stocks.N.remainQuantity">.
    # N = 요일 인덱스 (일=0..토=6). 한 컬럼에 여러 옵션 (X=0,1,2,...) 있을 수도.
    # target weekday 와 일치하는 N 의 input 들만 정확히 식별.
    # === 셀 선택: '요일 인덱스(stocks.N)' 대신 '화면의 날짜 열 x좌표' 기준 ===
    # MRT 는 stocks.N 인덱스가 렌더마다 바뀌고(0~6 ↔ 7~13) 두 주가 겹쳐 뜨기도 해서
    # 하드코딩 인덱스는 엉뚱한 날을 가리킬 수 있음. 날짜 숫자 헤더("3" 등)의 x좌표를
    # 앵커로, 그 열 아래 '보이는' 입력칸만 고른다 (WYSIWYG). day_number 로 타깃 날짜 전달.
    day_number = target_day_number()
    cells_and_diag = page.evaluate(
        """(dayNumber) => {
            document.querySelectorAll('[data-bot-cell]').forEach(el => el.removeAttribute('data-bot-cell'));
            const norm = s => (s || '').replace(/\\s+/g, ' ').trim();
            const all = Array.from(document.querySelectorAll(
                'input[name^="stockBundles."][name$=".remainQuantity"]'
            ));
            // 1) 날짜 숫자 헤더에서 타깃 날짜 열의 x중심 찾기 (캘린더 컬럼 폭 ~150~320px)
            const dayCells = Array.from(document.querySelectorAll('th,td,div,span,button'))
                .map(el => { const r = el.getBoundingClientRect();
                    return {t: norm(el.innerText || el.textContent || ''),
                            x: r.x + r.width/2, y: r.y, w: r.width, h: r.height}; })
                .filter(o => o.t === String(dayNumber) && o.w > 120 && o.w < 340
                             && o.h >= 26 && o.h <= 70 && o.y < 900)
                .sort((a, b) => a.y - b.y);
            if (dayCells.length === 0) {
                return {cells: [], diag: {error: 'date-header-not-found', total_inputs: all.length}};
            }
            const targetX = dayCells[0].x;
            const headerY = dayCells[0].y;
            // 2) 타깃 날짜 열 아래(headerY 이하) '보이는' remainQuantity 입력만 선택
            const out = [];
            all.forEach((el, i) => {
                if (!el.offsetParent) return;                 // 안 보이면 제외
                const r = el.getBoundingClientRect();
                if (r.width <= 0 || r.height <= 0) return;
                const cx = r.x + r.width/2;
                if (r.y < headerY - 5) return;                // 헤더 위 영역 제외
                if (Math.abs(cx - targetX) > 95) return;      // 타깃 날짜 열이 아니면 제외
                const tag = '__bot_cell_' + i + '_' + Date.now();
                el.setAttribute('data-bot-cell', tag);
                // 이 입력칸이 어느 '줄' 인지 = 인원구분(성인/소인) + 코스 + 출발.
                // MRT 는 상품ID 하나에 코스가 여러 개 들어있어서
                // ('비에이 시그니처' 와 '비에이 & 후라노' 가 같은 페이지)
                // 줄을 안 보고 날짜열 셀에 전부 나눠 넣으면 엉뚱한 코스가 열린다.
                const tr = el.closest('tr');
                const rowText = tr ? norm(tr.innerText || '').replace(/(\\s*명)+$/, '').trim() : '';
                out.push({tag, name: el.name, value: el.value,
                          placeholder: el.placeholder, disabled: el.disabled,
                          row_text: rowText,
                          text: el.value || el.placeholder || '0'});
            });
            return {cells: out, diag: {total_inputs: all.length,
                    targetX: Math.round(targetX), headerY: Math.round(headerY),
                    matched: out.length}};
        }""",
        day_number,
    )
    cells = cells_and_diag.get("cells", [])
    diag = cells_and_diag.get("diag", {})
    if diag.get("error"):
        print(f"[진단] 날짜열 헤더('{day_number}') 못 찾음 → 전체 input={diag.get('total_inputs')} "
              f"(주차 표시/렌더 확인 필요)")
    else:
        print(f"[진단] 날짜열 앵커: day={day_number} targetX={diag.get('targetX')} "
              f"headerY={diag.get('headerY')} 전체 input={diag.get('total_inputs')} "
              f"매칭={len(cells)}개")

    if not cells:
        raise Exception(f"{target_date_label()} 날짜 컬럼에서 수정할 인원 셀을 찾지 못했습니다.")

    # 가상 스크롤 때문에 화면 밖 줄은 innerText 가 빈 채로 읽힌다.
    # 그 상태로 코스를 가르면 맞는 줄을 놓친다 (5912702 성인 [코스 A] 가 실제로 그랬다).
    # 오픈일 때만, 빈 줄이 있는 칸을 하나씩 보이게 해서 다시 읽는다.
    if split:
        for c in cells:
            if c.get("row_text"):
                continue
            try:
                page.locator(f"input[name='{c['name']}']").first.scroll_into_view_if_needed(timeout=1500)
                page.wait_for_timeout(250)
                c["row_text"] = page.evaluate(
                    r"""(nm) => {
                        const el = document.querySelector('input[name="' + nm + '"]');
                        const tr = el && el.closest('tr');
                        if (!tr) return '';
                        return (tr.innerText || '').replace(/\s+/g, ' ')
                                                  .replace(/(\s*명)+$/, '').trim();
                    }""",
                    c["name"],
                )
                if c["row_text"]:
                    print(f"[진단] 줄 이름 재확인: {c['row_text']}")
            except Exception:
                pass

    # 마감은 '이 날짜 전부 0' 이라 모든 줄이 대상이다. 오픈만 줄을 골라야 한다.
    if split:
        cells, why = _pick_open_cells(cells, course)
        if why:
            print(f"[진행] 대상 줄 선별: {why}")
        for c in cells:
            print(f"        · {c.get('row_text') or c.get('name')}")

    changed = 0
    total_cells = len(cells)

    # 셀별 목표값. 마감(total=0)이면 전부 0, 오픈이면 split 여부에 따라 배분.
    if split and total_cells > 0:
        targets = split_across(total, total_cells)
    else:
        targets = [int(total)] * total_cells
    print(f"[진행] 총 {total_cells} 개 셀 입력 시작 (target weekday idx={target_cal_idx}, "
          f"목표={targets})")

    for idx, cell in enumerate(cells, start=1):
        want = str(targets[idx - 1])
        tag = cell.get("tag")
        cell_name = cell.get("name", "?")
        cur_val = cell.get("value") or cell.get("placeholder") or "0"
        was_disabled = cell.get("disabled")
        print(f"[진행] 셀 {idx}/{total_cells} ({cell_name}) value='{cur_val}' "
              f"disabled={was_disabled} → {want} 입력")

        sel = f"input[name='{cell_name}']"  # data-bot-cell 은 React 재렌더로 지워짐 → 안정적인 name 으로 지정
        success = False
        # data-bot-cell 이 React re-render 로 사라진 케이스: locator timeout 길게 잡으면 90초 낭비.
        # 짧은 timeout (3초) × 2회 retry 로 빠르게 다음 셀 진행.
        CELL_OP_TIMEOUT_MS = 3000
        for attempt in range(3):
            try:
                el = page.locator(sel).first
                # scroll: 실패해도 무시
                try:
                    el.scroll_into_view_if_needed(timeout=1500)
                except Exception:
                    pass
                # disabled 면 한 번 더 enable mode 시도 (전역적으로)
                is_disabled = el.evaluate("el => el.disabled", timeout=CELL_OP_TIMEOUT_MS)
                if is_disabled:
                    print(f"  [재시도-{attempt+1}] 셀 #{idx} 아직 disabled, enable_mode 재시도")
                    enable_inventory_edit_mode(page)
                    page.wait_for_timeout(300)
                    continue

                el.evaluate(
                    """(el, v) => {
                        el.focus();
                        const proto = Object.getPrototypeOf(el);
                        const setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
                        setter.call(el, v);
                        el.dispatchEvent(new Event('input', {bubbles: true}));
                        el.dispatchEvent(new Event('change', {bubbles: true}));
                        el.dispatchEvent(new Event('blur', {bubbles: true}));
                    }""",
                    want,
                    timeout=CELL_OP_TIMEOUT_MS,
                )
                page.wait_for_timeout(150)
                final_val = el.evaluate("el => el.value", timeout=CELL_OP_TIMEOUT_MS)
                if str(final_val) == want:
                    success = True
                    break
                # 폴백: fill
                try:
                    el.fill(want, timeout=1500, force=True)
                    final_val = el.evaluate("el => el.value", timeout=CELL_OP_TIMEOUT_MS)
                    if str(final_val) == want:
                        success = True
                        break
                except Exception:
                    pass
            except Exception as e:
                # 메시지 줄이기: full traceback 대신 핵심만
                msg = str(e).split("\n")[0][:120]
                print(f"  [재시도-{attempt+1}] 셀 #{idx} 실패: {msg}")
                page.wait_for_timeout(200)

        if success:
            changed += 1
            print(f"  [완료] 셀 #{idx} value='{want}' 적용")
        else:
            print(f"  [실패] 셀 #{idx} 2회 시도 모두 실패 (다음 셀로 진행)")

        page.wait_for_timeout(100)

    # 마지막 입력칸 focus가 남으면 저장 버튼이 비활성/미반영되는 경우가 있어 한 번 더 blur 처리합니다.
    try:
        page.keyboard.press("Escape")
        try:
            page.locator("body").click(position={"x": 5, "y": 5}, timeout=500)
        except Exception:
            pass
        page.wait_for_timeout(250)
    except Exception:
        pass

    # === 저장 직전 검증 + 미완 셀 재시도 (성공 위장 방지) ===
    # 입력 시점 changed 카운트만 믿지 않고, 타깃 셀 값을 fresh 로 재확인한다.
    # 아직 0 이 아닌 셀은 페이지가 진정된 뒤 한 번 더 재시도. 정상 케이스는
    # 셀 값 한 번씩만 읽고 끝나 거의 시간이 안 든다.
    # data-bot-cell 태그는 React 재렌더로 지워지므로, React 가 유지하는 name 속성으로 셀을 지정한다.
    def _name_sel(nm):
        return f"input[name='{nm}']"

    def _cell_val(nm):
        try:
            return str(page.locator(_name_sel(nm)).first.evaluate(
                "el => el.value", timeout=3000))
        except Exception:
            return None  # 못 읽으면 미확인 → 실패로 간주

    want_of = {c.get("name"): str(targets[i]) for i, c in enumerate(cells)}

    def _force_value(nm, v):
        try:
            loc = page.locator(_name_sel(nm)).first
            try:
                loc.scroll_into_view_if_needed(timeout=1500)
            except Exception:
                pass
            loc.evaluate(
                """(el, v) => {
                    el.focus();
                    const proto = Object.getPrototypeOf(el);
                    const setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
                    setter.call(el, v);
                    el.dispatchEvent(new Event('input', {bubbles: true}));
                    el.dispatchEvent(new Event('change', {bubbles: true}));
                    el.dispatchEvent(new Event('blur', {bubbles: true}));
                }""",
                v,
                timeout=3000)
            return True
        except Exception:
            return False

    def _wrong(c):
        nm = c.get("name")
        return _cell_val(nm) != want_of.get(nm)

    for verify_round in range(2):
        remaining = [c for c in cells if _wrong(c)]
        if not remaining:
            break
        print(f"[검증] 목표값과 다른 셀 {len(remaining)}개 → 페이지 진정 후 재시도 "
              f"(round {verify_round + 1})")
        page.wait_for_timeout(800)
        for c in remaining:
            _force_value(c.get("name"), want_of.get(c.get("name"), "0"))
            page.wait_for_timeout(120)

    remaining_final = [c for c in cells if _wrong(c)]
    ok_cells = total_cells - len(remaining_final)
    if remaining_final:
        print(f"[완료] 인원 입력: {ok_cells}/{total_cells}개 (미완 {len(remaining_final)}개)")
    else:
        print(f"[완료] 인원 입력 완료: {ok_cells}/{total_cells}개 목표={targets}")
    return ok_cells, total_cells


def get_viewport_size(page: Page) -> Dict[str, int]:
    try:
        size = page.viewport_size
        if size and size.get("width") and size.get("height"):
            return {"width": int(size["width"]), "height": int(size["height"])}
    except Exception:
        pass

    try:
        return page.evaluate(
            """() => ({width: window.innerWidth || 1920, height: window.innerHeight || 1080})"""
        )
    except Exception:
        return {"width": 1920, "height": 1080}


def click_inventory_modify_confirm_popup(page: Page) -> bool:
    """
    V5:
    예약인원 수정 버튼 클릭 후 뜨는 확인 팝업에서
    파란색 '수정하기' 버튼이 보이는 즉시 클릭한다.
    기존 고정 대기시간을 줄여 저장 단계 속도를 개선했다.
    """
    print("[진행] 예약 인원 수정 확인 팝업 대기")

    # 0) 고정 대기 대신 팝업의 '수정하기' 버튼이 보일 때까지만 짧게 대기
    try:
        page.wait_for_function(
            """() => {
                function norm(s) { return (s || '').replace(/\\s+/g, ' ').trim(); }
                const vw = window.innerWidth || 1920;
                const vh = window.innerHeight || 1080;
                return Array.from(document.querySelectorAll('button,[role="button"],div,span'))
                  .some(el => {
                    const text = norm(el.innerText || el.textContent || '');
                    const r = el.getBoundingClientRect();
                    const st = window.getComputedStyle(el);
                    return text === '수정하기' &&
                           st.display !== 'none' &&
                           st.visibility !== 'hidden' &&
                           Number(st.opacity) !== 0 &&
                           r.width >= 45 &&
                           r.height >= 25 &&
                           r.x >= 0 &&
                           r.y >= 0 &&
                           r.x <= vw &&
                           r.y <= vh;
                  });
            }""",
            timeout=2200,
        )
    except Exception:
        # 팝업 표시가 늦는 경우를 대비한 최소 fallback 대기
        page.wait_for_timeout(250)

    # 1) JS로 버튼 좌표를 바로 찾아 클릭: locator 탐색보다 빠름
    result = page.evaluate(
        """() => {
            function norm(s) { return (s || '').replace(/\\s+/g, ' ').trim(); }

            const vw = window.innerWidth || 1920;
            const vh = window.innerHeight || 1080;
            const cx = vw / 2;
            const cy = vh / 2;

            const candidates = Array.from(document.querySelectorAll('button,[role="button"],div,span'))
              .map(el => {
                const text = norm(el.innerText || el.textContent || '');
                const r = el.getBoundingClientRect();
                const st = window.getComputedStyle(el);
                return {text, x:r.x, y:r.y, width:r.width, height:r.height,
                        display:st.display, visibility:st.visibility, opacity:st.opacity};
              })
              .filter(o =>
                o.display !== 'none' &&
                o.visibility !== 'hidden' &&
                Number(o.opacity) !== 0 &&
                o.text === '수정하기' &&
                o.width >= 45 &&
                o.height >= 25 &&
                o.x >= 0 &&
                o.y >= 0 &&
                o.x <= vw &&
                o.y <= vh
              )
              .sort((a,b) => {
                const acx = a.x + a.width / 2;
                const acy = a.y + a.height / 2;
                const bcx = b.x + b.width / 2;
                const bcy = b.y + b.height / 2;
                return (Math.abs(acx - cx) + Math.abs(acy - cy)) -
                       (Math.abs(bcx - cx) + Math.abs(bcy - cy));
              });

            if (!candidates.length) return null;
            const b = candidates[0];
            return {x:b.x + b.width / 2, y:b.y + b.height / 2};
        }"""
    )

    if result:
        print(f"[진행] 팝업 수정하기 버튼 즉시 클릭 (셀렉터)")
        try:
            page.get_by_role("button", name="수정하기", exact=True).last.click(timeout=2500, force=True)
        except Exception:
            try:
                page.locator("[role='dialog'] button").filter(has_text="수정하기").last.click(timeout=2500, force=True)
            except Exception:
                pass

        # 팝업이 닫히면 바로 다음 단계로 진행. 닫힘 확인 실패 시에도 오래 기다리지 않음.
        try:
            page.wait_for_function(
                """() => {
                    function norm(s) { return (s || '').replace(/\\s+/g, ' ').trim(); }
                    const visibleModifyButtons = Array.from(document.querySelectorAll('button,[role="button"],div,span'))
                      .filter(el => {
                        const text = norm(el.innerText || el.textContent || '');
                        const r = el.getBoundingClientRect();
                        const st = window.getComputedStyle(el);
                        return text === '수정하기' &&
                               st.display !== 'none' &&
                               st.visibility !== 'hidden' &&
                               Number(st.opacity) !== 0 &&
                               r.width >= 45 &&
                               r.height >= 25 &&
                               r.x >= 0 &&
                               r.y >= 0;
                      });
                    return visibleModifyButtons.length === 0;
                }""",
                timeout=1800,
            )
        except Exception:
            page.wait_for_timeout(450)

        print("[완료] 팝업 수정하기 버튼 클릭")
        return True

    # 2) JS 탐색 실패 시 role=dialog 안의 수정하기 버튼 fallback
    try:
        dialog = page.locator('[role="dialog"], [aria-modal="true"]')
        if dialog.count() > 0:
            btn = dialog.last.get_by_role("button", name="수정하기")
            if btn.count() > 0 and btn.first.is_visible():
                btn.first.click(timeout=1500)
                page.wait_for_timeout(500)
                print("[완료] 팝업 수정하기 버튼 클릭")
                return True
    except Exception:
        pass

    # 3) 최종 fallback: 팝업 안의 파란색 '수정하기' 버튼을 셀렉터로 탐색
    print("[주의] 팝업 수정하기 버튼 JS/role 탐색 실패 → 셀렉터 fallback")
    try:
        page.get_by_role("button", name="수정하기", exact=True).last.click(timeout=3000, force=True)
        page.wait_for_timeout(600)
        print("[완료] 팝업 수정하기 버튼 클릭 (셀렉터 fallback)")
        return True
    except Exception:
        try:
            # 마지막 안전망: dialog 내부의 primary 버튼
            page.locator("[role='dialog'] button").filter(has_text="수정하기").last.click(timeout=3000, force=True)
            page.wait_for_timeout(600)
            print("[완료] 팝업 수정하기 버튼 클릭 (dialog fallback)")
            return True
        except Exception:
            # 여기까지 오면 '수정하기' 확정을 못 누른 것 → 저장 미커밋. False 로 알린다.
            print("[오류] 팝업 수정하기 버튼 셀렉터 모두 실패")
            page.wait_for_timeout(300)
            return False


def click_save_inventory(page: Page) -> bool:
    print("[진행] 예약인원 수정 저장 클릭")

    # 저장 전 차주로 갔다가 다시 현재 주차로 돌아와 입력값 반영을 안정화합니다.
    refresh_calendar_next_prev_before_save(page)

    # 1) 우측 하단 '예약인원 수정' 버튼 클릭
    clicked_save = False

    try:
        loc = page.get_by_text("예약인원 수정", exact=True)
        if loc.count() > 0:
            loc.last.wait_for(state="visible", timeout=4000)
            loc.last.click(timeout=4000)
            clicked_save = True
    except Exception:
        pass

    if not clicked_save:
        try:
            loc = page.get_by_text("예약 인원 수정", exact=True)
            if loc.count() > 0:
                loc.last.wait_for(state="visible", timeout=4000)
                loc.last.click(timeout=4000)
                clicked_save = True
        except Exception:
            pass

    if not clicked_save:
        # ★ 실제 저장버튼 text 는 '예약 인원 수정'(띄어쓰기 있음). 아래에서 JS 로 버튼을 찾아
        #   '그 요소를 직접 click()' 한다 (문자열 재탐색 시 no-space/space 불일치로 실패했던 6107106 버그 방지).
        clicked_via_js = False
        try:
            clicked_via_js = bool(page.evaluate(
                """() => {
                    const norm = s => (s || '').replace(/\\s+/g, ' ').trim();
                    const vh = window.innerHeight || 1080;
                    const cands = Array.from(document.querySelectorAll('button,[role="button"]'))
                      .filter(el => {
                        const t = norm(el.innerText || el.textContent || '');
                        const r = el.getBoundingClientRect();
                        const st = window.getComputedStyle(el);
                        const disabled = el.disabled === true || el.getAttribute('aria-disabled') === 'true';
                        return !disabled &&
                               st.display !== 'none' && st.visibility !== 'hidden' &&
                               (t === '예약인원 수정' || t === '예약 인원 수정') &&
                               r.width >= 70 && r.height >= 25 && r.y >= vh * 0.45;
                      })
                      .sort((a,b) => {
                        const ra = a.getBoundingClientRect(), rb = b.getBoundingClientRect();
                        return rb.y - ra.y || rb.x - ra.x;
                      });
                    if (!cands.length) return false;
                    cands[0].click();
                    return true;
                }"""
            ))
        except Exception:
            clicked_via_js = False

        if clicked_via_js:
            print("[진행] 예약인원 수정 버튼 클릭 (JS element.click)")
            clicked_save = True

    if not clicked_save:
        print("[주의] 예약인원 수정 버튼 JS 탐색 실패 → 셀렉터 fallback")
        # 실제 버튼 text 는 '예약 인원 수정'(띄어쓰기) → 띄어쓰기 버전을 먼저, no-space 는 예비로 시도.
        for _name in ("예약 인원 수정", "예약인원 수정"):
            try:
                page.get_by_role("button", name=_name, exact=True).last.click(timeout=2500, force=True)
                clicked_save = True
                break
            except Exception:
                continue
        if not clicked_save:
            try:
                page.get_by_text("예약 인원 수정", exact=True).last.click(timeout=2500, force=True)
                clicked_save = True
            except Exception:
                print("[오류] 예약인원 수정 버튼 셀렉터 모두 실패")

    wait_light(page, 250)
    print("[완료] 예약인원 수정 버튼 클릭")

    # 2) 팝업창의 파란색 '수정하기' 버튼 클릭
    confirm_ok = click_inventory_modify_confirm_popup(page)

    # 3) 혹시 추가 확인창이 뜨면 확인 클릭
    click_confirm_if_exists(page)
    wait_light(page, 300)

    # ★ 저장 커밋 판정: '예약인원 수정' 저장버튼 클릭 + '수정하기' 확정 팝업 클릭 이 둘 다 돼야 커밋됨.
    #   둘 중 하나라도 실패면 변경이 커밋 안 된 것(6107106 사례) → 성공 위장 말고 False 반환.
    saved_ok = bool(clicked_save and confirm_ok)
    if saved_ok:
        print("[완료] 예약인원 수정 저장 완료")
    else:
        print(f"[오류] 예약인원 수정 저장 미완료 (저장버튼클릭={clicked_save}, 확인팝업={confirm_ok}) → 변경 미커밋 가능")
    return saved_ok


def click_exit(page: Page):
    print("[진행] 왼쪽 상단 나가기 클릭")

    # 저장 직후 버튼이 잠깐 비활성/오버레이 상태일 수 있어 아주 짧게만 안정화 대기
    wait_light(page, 250)

    def check_back_to_list(wait_ms: int = 1200) -> bool:
        return wait_product_list_ready(page, timeout=wait_ms)

    # 1) JS로 실제 클릭 가능한 부모 button/a/[role=button]를 찾아 element.click() 실행
    try:
        result = page.evaluate(
            """() => {
                function norm(s) { return (s || '').replace(/\\s+/g, ' ').trim(); }
                const nodes = Array.from(document.querySelectorAll('button,a,[role="button"],div,span'))
                  .map(el => {
                    const text = norm(el.innerText || el.textContent || el.getAttribute('aria-label') || '');
                    const r = el.getBoundingClientRect();
                    const st = window.getComputedStyle(el);
                    return {el, text, x:r.x, y:r.y, width:r.width, height:r.height,
                            display:st.display, visibility:st.visibility, opacity:st.opacity};
                  })
                  .filter(o =>
                    o.display !== 'none' &&
                    o.visibility !== 'hidden' &&
                    Number(o.opacity) !== 0 &&
                    o.text.includes('나가기') &&
                    o.x >= 0 && o.x <= 280 &&
                    o.y >= 0 && o.y <= 120 &&
                    o.width >= 20 && o.height >= 15
                  )
                  .sort((a,b) => a.y - b.y || a.x - b.x);

                if (!nodes.length) return null;

                const item = nodes[0];
                const target = item.el.closest('button,a,[role="button"]') || item.el;
                const tr = target.getBoundingClientRect();
                target.click();
                return {
                    method: 'js_element_click',
                    text: item.text,
                    x: tr.x + tr.width / 2,
                    y: tr.y + tr.height / 2
                };
            }"""
        )
        if result:
            print(f"[진행] 나가기 JS element.click: x={int(result['x'])}, y={int(result['y'])}, text='{result.get('text')}'")
            if check_back_to_list(1800):
                print("[완료] 나가기 클릭 및 상품 목록 복귀 확인")
                return
    except Exception:
        pass

    # 2) Playwright 텍스트 클릭 force 시도
    for exact in [True, False]:
        try:
            loc = page.get_by_text("나가기", exact=exact)
            if loc.count() > 0:
                print(f"[진행] 나가기 텍스트 클릭 재시도: exact={exact}")
                loc.first.click(timeout=1200, force=True)
                if check_back_to_list(1600):
                    print("[완료] 나가기 클릭 및 상품 목록 복귀 확인")
                    return
        except Exception:
            pass

    # 3) 좌상단 버튼 좌표 다중 클릭 fallback
    # 캡처 기준 나가기 버튼을 셀렉터로 다양하게 시도
    exit_selectors = [
        lambda: page.get_by_role("button", name="나가기", exact=True),
        lambda: page.get_by_role("link", name="나가기", exact=True),
        lambda: page.get_by_text("나가기", exact=True),
        lambda: page.locator("[aria-label='나가기']"),
        lambda: page.locator("button, a").filter(has_text="나가기").first,
    ]
    for i, sel_fn in enumerate(exit_selectors, start=1):
        try:
            print(f"[진행] 나가기 셀렉터 재시도 #{i}")
            sel_fn().click(timeout=2000, force=True)
            if check_back_to_list(1500):
                print("[완료] 나가기 클릭 및 상품 목록 복귀 확인")
                return
        except Exception:
            pass

    # 4) 브라우저 뒤로가기 fallback
    try:
        print("[주의] 나가기 클릭 확인 실패 → 브라우저 뒤로가기 재시도")
        page.go_back(wait_until="domcontentloaded", timeout=8000)
        if check_back_to_list(2500):
            print("[완료] 뒤로가기 후 상품 목록 복귀 확인")
            return
    except Exception:
        pass

    # 5) 마지막 복구: 캐시된 상품 목록 URL로 직접 이동
    global PRODUCT_LIST_URL_CACHE
    if PRODUCT_LIST_URL_CACHE:
        try:
            print("[주의] 나가기 복귀 실패 → 캐시된 상품 목록 URL로 직접 이동")
            page.goto(PRODUCT_LIST_URL_CACHE, wait_until="domcontentloaded", timeout=10000)
            if check_back_to_list(5000):
                print("[완료] 상품 목록 URL 직접 이동 완료")
                return
        except Exception:
            pass

    print("[주의] 나가기 후 상품 목록 확인이 늦습니다. 다음 상품 시작 단계에서 다시 복구합니다.")



def get_page_brief(page: Page, body_timeout: int = 900) -> Dict[str, str]:
    """열려 있는 Chrome 탭의 URL/타이틀/본문 일부를 안전하게 가져옵니다."""
    info = {"url": "", "title": "", "body": ""}
    try:
        info["url"] = str(page.url or "")
    except Exception:
        pass
    try:
        info["title"] = normalize_text(page.title(timeout=1200))
    except Exception:
        pass
    try:
        info["body"] = normalize_text(page.locator("body").inner_text(timeout=body_timeout))
    except Exception:
        pass
    return info


def score_mrt_partner_page(info: Dict[str, str]) -> int:
    """현재 탭이 MyRealTrip 파트너/상품관리 탭인지 점수화합니다."""
    url = (info.get("url") or "").lower()
    title = (info.get("title") or "").lower()
    body = info.get("body") or ""
    body_l = body.lower()
    joined_l = f"{url} {title} {body_l}"

    if url.startswith(("chrome://", "edge://", "devtools://", "chrome-extension://", "about:")):
        return -100

    score = 0
    if "myrealtrip" in joined_l or "mrt" in joined_l:
        score += 25
    if "partner" in joined_l or "파트너" in body:
        score += 12
    if "투어·티켓 상품" in body or "투어 티켓 상품" in body:
        score += 40
    if "상품 ID" in body and ("검색어" in body or "상품명" in body):
        score += 25
    if "상품 정보 등록" in body or "상품 속성 정보 등록" in body:
        score += 25
    if "예약 인원 관리" in body:
        score += 35
    if "예약인원 수정" in body or "예약 인원 수정" in body:
        score += 15
    if "수정" in body and "상품" in body:
        score += 8
    return score


def pick_mrt_partner_page(browser) -> Page:
    """CDP로 연결된 Chrome 안의 모든 탭 중 MRT 파트너 작업 탭을 찾아 반환합니다."""
    candidates = []

    for context in browser.contexts:
        for page in context.pages:
            try:
                if page.is_closed():
                    continue
            except Exception:
                continue
            info = get_page_brief(page)
            score = score_mrt_partner_page(info)
            candidates.append((score, page, info))

    candidates.sort(key=lambda x: x[0], reverse=True)

    print("[안내] 연결된 Chrome 탭 확인:")
    for idx, (score, _page, info) in enumerate(candidates, start=1):
        title = info.get("title") or "제목 없음"
        url = info.get("url") or "URL 없음"
        print(f"  - 탭 {idx}: score={score} / title={title[:70]} / url={url[:120]}")

    if candidates and candidates[0][0] > 0:
        selected = candidates[0][1]
        try:
            selected.bring_to_front()
        except Exception:
            pass
        print(f"[완료] MRT 파트너 작업 탭 선택: {candidates[0][2].get('title') or candidates[0][2].get('url')}")
        return selected

    raise Exception(
        "MRT/MyRealTrip 파트너 탭을 찾지 못했습니다. "
        "디버그 모드 Chrome에서 마이리얼트립 파트너 페이지에 로그인한 뒤, "
        "투어·티켓 상품 목록 또는 상품 수정 화면을 열어둔 상태로 다시 실행해주세요. "
        f"현재 연결 URL: {CDP_URL}"
    )


def wait_for_any_mrt_screen(page: Page, timeout_ms: int = 7000) -> bool:
    """선택된 탭이 MRT 파트너 화면으로 읽히는지 짧게 확인합니다."""
    deadline = time.perf_counter() + timeout_ms / 1000
    while time.perf_counter() < deadline:
        info = get_page_brief(page, body_timeout=800)
        if score_mrt_partner_page(info) > 0:
            return True
        page.wait_for_timeout(250)
    return False

def process_product(page: Page, product_id: str) -> Dict[str, object]:
    product_started_at = time.perf_counter()

    try:
        goto_tour_ticket_product_list(page)
        fill_product_id_and_search(page, product_id)
        click_edit_button_for_product(page, product_id)
        goto_inventory_management(page)
        # 편집 모드 활성화: "예약 인원 수정" 버튼을 누르면 input disabled 해제됨
        enable_inventory_edit_mode(page)
        changed, _total_cells = set_target_date_inventory_zero(page)
        saved_ok = click_save_inventory(page)
        click_exit(page)

        elapsed_seconds = time.perf_counter() - product_started_at
        elapsed_time = format_duration(elapsed_seconds)
        print(f"[완료] 상품 ID {product_id} 작업 소요시간: {elapsed_time}")

        if not saved_ok:
            # 저장 확정(예약인원 수정 → 수정하기)이 안 눌려 변경이 커밋 안 됨 → 성공 위장 방지
            return {
                "product_id": product_id,
                "target_date": target_date_label(),
                "result": "실패",
                "memo": f"저장 미커밋(저장확정 버튼 실패): {changed}개 셀 입력했으나 커밋 안 됨",
                "elapsed_seconds": round(elapsed_seconds, 2),
                "elapsed_time": elapsed_time,
            }

        return {
            "product_id": product_id,
            "target_date": target_date_label(),
            "result": "성공",
            "memo": f"{changed}개 셀 0 입력 시도",
            "elapsed_seconds": round(elapsed_seconds, 2),
            "elapsed_time": elapsed_time,
        }

    except Exception as e:
        elapsed_seconds = time.perf_counter() - product_started_at
        elapsed_time = format_duration(elapsed_seconds)
        print(f"[오류] {product_id}: {e}")
        print(f"[완료] 상품 ID {product_id} 작업 소요시간: {elapsed_time}")
        return {
            "product_id": product_id,
            "target_date": target_date_label(),
            "result": "실패",
            "memo": f"{e}",
            "elapsed_seconds": round(elapsed_seconds, 2),
            "elapsed_time": elapsed_time,
        }


def parse_product_id_list(value: str) -> List[str]:
    return [x.strip() for x in re.split(r"[\s,;/]+", value or "") if x.strip()]


def ask_product_ids() -> List[str]:
    env_ids = os.environ.get("MRT_PRODUCT_IDS", "").strip()
    if env_ids:
        default_key = target_weekday_key()
        env_upper = env_ids.upper()
        weekday_key = WEEKDAY_ALIASES.get(env_upper) or WEEKDAY_ALIASES.get(env_ids)
        if env_upper in {"DEFAULT", "AUTO", "__DEFAULT__"}:
            ids = get_default_product_ids_for_weekday(default_key)
            print(f"[안내] MRT_PRODUCT_IDS=DEFAULT → {WEEKDAY_KO.get(default_key, default_key)} 기본 상품 ID {len(ids)}개 사용")
            return ids
        if weekday_key:
            ids = get_default_product_ids_for_weekday(weekday_key)
            print(f"[안내] MRT_PRODUCT_IDS={env_ids} → {WEEKDAY_KO.get(weekday_key, weekday_key)} 기본 상품 ID {len(ids)}개 사용")
            return ids
        ids = parse_product_id_list(env_ids)
        print(f"[안내] MRT_PRODUCT_IDS 지정 상품 ID 사용: {' / '.join(ids)}")
        return ids

    print()
    print("[입력] MRT 마감 처리할 상품 ID를 입력하세요.")
    print(f"[기준] 오늘 기준 내일 날짜: {target_date_label()} / {target_weekday_label()}")
    print("[기본값] 바로 Enter를 누르면 내일 요일 기준 기본 상품 ID 목록을 사용합니다.")
    print("[직접입력] 상품 ID를 한 줄씩 입력하고, 입력이 끝나면 END 입력 후 Enter")
    print("[요일선택] MON/TUE/WED/THU/FRI/SAT/SUN 또는 월/화/수/목/금/토/일 입력 시 해당 요일 기본값 사용")
    print()

    default_key = target_weekday_key()
    default_ids = get_default_product_ids_for_weekday(default_key)
    print(f"[내일 기본 상품 ID] {WEEKDAY_KO.get(default_key, default_key)}: {len(default_ids)}개")
    if default_ids:
        print(" / ".join(default_ids))
    else:
        print("아직 기본 상품 ID가 설정되어 있지 않습니다. 코드 상단 DEFAULT_PRODUCT_IDS_BY_WEEKDAY에 입력해주세요.")
    print()

    ids = []
    first = input("> ").strip()

    if not first:
        return default_ids

    first_upper = first.upper()
    weekday_key = WEEKDAY_ALIASES.get(first_upper) or WEEKDAY_ALIASES.get(first)
    if weekday_key:
        selected_ids = get_default_product_ids_for_weekday(weekday_key)
        print(f"[선택] {WEEKDAY_KO.get(weekday_key, weekday_key)} 기본 상품 ID {len(selected_ids)}개 사용")
        return selected_ids

    if first_upper != "END":
        ids.append(first)

    while True:
        line = input("> ").strip()
        if not line:
            continue
        if line.upper() == "END":
            break
        ids.append(line)

    return ids



# ============================================================
# OTA Close 프레임워크 어댑터
# - main.py 가 from mrt import run_close 로 호출
# - target_date 기본: 내일 (PC 시간 기준). 이미 모듈 내부의 target_date() 함수가 내일을 반환.
# - 상품 ID: 환경변수 MRT_PRODUCT_IDS 또는 요일별 기본 목록
# - dry_run: 실제 클릭 없이 대상 ID 만 출력
# ============================================================
def run_close(target_date=None, dry_run: bool = False):
    """OTA Close 프레임워크용 진입점. shared.types.Result 호환 dict 반환."""
    result = {
        "agency": "MRT",
        "success": 0,
        "failed": 0,
        "skipped": 0,
        "errors": [],
    }

    # 핵심: target_date 가 들어오면 모듈 전역의 target_date() 함수를 덮어쓴다.
    # mrt.py 내부에서 target_date()/target_date_label()/target_weekday_label() 등이
    # 여러 곳에서 호출되므로 monkey-patch 가 필요.
    if target_date is not None:
        try:
            import sys as _sys
            _self_mod = _sys.modules[__name__]
            _tgt = target_date
            # date 객체이면 datetime 으로 변환 (기존 함수가 datetime 반환)
            from datetime import datetime as _dt_cls, date as _date_cls
            if isinstance(_tgt, _date_cls) and not isinstance(_tgt, _dt_cls):
                _tgt_dt = _dt_cls(_tgt.year, _tgt.month, _tgt.day)
            else:
                _tgt_dt = _tgt
            def _patched_target_date():
                return _tgt_dt
            _self_mod.target_date = _patched_target_date
            print(f"[MRT] target_date monkey-patch 완료 → {_tgt_dt.strftime('%Y-%m-%d')}")
        except Exception as _e:
            print(f"[MRT] target_date monkey-patch 실패 (내일 처리): {_e}")

    # 전수조사 모드 (사용자 요청 - 화이트리스트/요일별 product_id 모두 제거)
    direction = os.environ.get("MRT_DIRECTION", "").strip().lower()
    quarter = os.environ.get("MRT_QUARTER", "").strip()

    # Chrome 보장
    try:
        from shared.health import ensure_chrome as _ensure_chrome_disc
        _ensure_chrome_disc(int(CDP_URL.rsplit(":", 1)[-1]), "start_chrome_global.bat", wait_sec=10)
    except Exception as e:
        print(f"[MRT] ensure_chrome 실패 (계속): {e}")

    try:
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(CDP_URL, timeout=CDP_CONNECT_TIMEOUT_MS)
            if not browser.contexts:
                result["errors"].append("Chrome context 없음")
                return result
            context = browser.contexts[0]

            # quarter (1~4) 또는 forward/backward 분할이면 자기 전용 새 탭 (worker 충돌 방지)
            is_split = quarter in ("1", "2", "3", "4") or direction in ("forward", "backward")
            if is_split:
                page = context.new_page()
                worker_label = f"q{quarter}" if quarter else direction
                print(f"[MRT/{worker_label}] 전용 새 탭 생성 (parallel worker)")
                # stagger: 동시 productlist/페이지네이션 race 방지
                if quarter:
                    time.sleep((int(quarter) - 1) * 1.0)  # q1=0s, q2=1s, q3=2s, q4=3s
                elif direction == "backward":
                    time.sleep(2.0)
            else:
                try:
                    page = pick_mrt_partner_page(browser)
                except Exception:
                    page = context.new_page()

            # quarter 가 있으면 quarter 전달, 없으면 direction 전달
            try:
                summary = _mrt_discover_and_close(page, dry_run, direction, quarter)
                result["success"] = summary["success"]
                result["failed"] = summary["failed"]
                result["skipped"] = summary["skipped"]
                result["errors"] = summary["errors"][:20]
                print(f"[MRT] discover 완료: 처리={summary['products_processed']}, "
                      f"지역제외={summary['products_skipped_region']}")
            finally:
                # P0-2: 분할 worker 가 만든 전용 탭을 닫는다 (탭 누수 방지).
                #   VI 와 같은 GLOBAL Chrome(9530) 을 공유하므로, 여기서 안 닫으면
                #   Viator 쪽까지 같이 느려지다 net::ERR_ABORTED 로 죽는다.
                if is_split:
                    try:
                        from shared.chrome_setup import close_worker_page
                        close_worker_page(page, f"MRT/{quarter or direction}")
                    except Exception as _ce:
                        print(f"[MRT] 탭 정리 실패: {_ce}")
    except Exception as e:
        result["errors"].append(f"discover 모드 치명: {e}")

    result["errors"] = result["errors"][:20]
    return result




# ============================================================
# 전수조사 모드 (discover-and-close)
# - /products/experiences 진입 -> "판매중" 탭
# - 페이지 순회 (forward/backward 분할 지원)
# - 각 행의 지역 검사 -> 일본 6개 지역만 처리
# - 상품 ID 로 stock-for-use 페이지 직접 navigate
# - 기존 set_target_date_inventory_zero 로 수량 0 처리
# ============================================================

EXPERIENCES_URL = "https://partner.myrealtrip.com/products/experiences"

# 일본 6개 타겟 지역만 마감
TARGET_REGIONS_JP = {"후쿠오카", "삿포로", "오타루", "나고야", "도쿄", "오사카"}


def _mrt_get_total_count(page) -> int:
    """본문에서 '총 N개의 상품' 파싱."""
    try:
        txt = page.locator("body").inner_text(timeout=2000)
        import re
        m = re.search(r"총\s*(\d+)\s*개의\s*상품", txt)
        return int(m.group(1)) if m else 0
    except Exception:
        return 0


def _mrt_click_sale_tab(page) -> bool:
    """
    '판매중' 탭 클릭.
    검증은 _mrt_collect_all_products 의 count match (total == collected) 에 위임.
    여기서는 클릭 + wait 만.
    """
    try:
        clicked = page.evaluate("""
        () => {
          const spans = Array.from(document.querySelectorAll('span'));
          const tab = spans.find(s =>
            s.children.length === 0 &&
            (s.innerText||'').trim() === '판매중' &&
            (s.className||'').indexOf('TabList') >= 0
          );
          if (tab) { tab.click(); return true; }
          return false;
        }
        """)
        if not clicked:
            print(f"[MRT] 판매중 탭 span 못 찾음 → 재시도")
            page.wait_for_timeout(1500)
            # 1회 재시도
            page.evaluate("""
            () => {
              const spans = Array.from(document.querySelectorAll('span'));
              const tab = spans.find(s =>
                s.children.length === 0 &&
                (s.innerText||'').trim() === '판매중' &&
                (s.className||'').indexOf('TabList') >= 0
              );
              if (tab) tab.click();
            }
            """)
        # 필터 적용 wait
        page.wait_for_timeout(3000)
        return True
    except Exception as e:
        print(f"[MRT] 판매중 탭 클릭 오류: {e}")
        return False


def _mrt_get_products_on_page(page):
    """현재 페이지의 (product_id, region) 리스트 반환."""
    try:
        rows = page.evaluate("""
        () => {
          const rows = Array.from(document.querySelectorAll('table tbody tr'));
          return rows.map(r => {
            const cells = r.querySelectorAll('td');
            return {
              id: cells[0]?.innerText?.trim() || '',
              status: cells[1]?.innerText?.trim() || '',
              name: (cells[2]?.innerText||'').trim().slice(0,80),
              region: cells[3]?.innerText?.trim() || '',
            };
          }).filter(r => r.id);
        }
        """)
        return rows or []
    except Exception:
        return []


def _mrt_get_pagination(page):
    """(current_page, total_pages) 반환. 못 찾으면 (1, 1)."""
    try:
        info = page.evaluate("""
        () => {
          const buttons = Array.from(document.querySelectorAll('button'))
            .filter(b => /^\\d+$/.test((b.innerText||'').trim()));
          const nums = buttons.map(b => parseInt(b.innerText.trim()));
          const cur_btn = buttons.find(b => b.className.includes('iq4752'));
          const cur = cur_btn ? parseInt(cur_btn.innerText.trim()) : (nums[0] || 1);
          const total = nums.length > 0 ? Math.max(...nums) : 1;
          return {cur, total};
        }
        """)
        return (info.get("cur", 1), info.get("total", 1))
    except Exception:
        return (1, 1)


def _mrt_goto_page(page, target_page: int) -> bool:
    """페이지 번호 버튼 클릭으로 이동."""
    try:
        ok = page.evaluate(f"""
        () => {{
          const btn = Array.from(document.querySelectorAll('button'))
            .filter(b => /^\\d+$/.test((b.innerText||'').trim()))
            .find(b => parseInt(b.innerText.trim()) === {target_page});
          if (btn) {{ btn.click(); return true; }}
          return false;
        }}
        """)
        if ok:
            page.wait_for_timeout(1200)
        return bool(ok)
    except Exception:
        return False


def _mrt_close_one_product(page, product_id: str, dry_run: bool):
    """
    /products/experiences/{id}/stock-for-use 직접 진입 후 수량 0 처리.
    기존 set_target_date_inventory_zero + click_save_inventory + click_exit 재사용.
    """
    started_at = time.perf_counter()
    stock_url = f"https://partner.myrealtrip.com/products/experiences/{product_id}/stock-for-use"
    try:
        page.goto(stock_url, wait_until="domcontentloaded", timeout=15_000)
        page.wait_for_timeout(800)
        dismiss_notice_popup(page)  # 공지 팝업이 떠 있으면 닫기 (클릭 방해 방지)
    except Exception as e:
        return {"product_id": product_id, "result": "실패",
                "memo": f"goto 실패: {e}",
                "elapsed_seconds": round(time.perf_counter() - started_at, 2)}

    if dry_run:
        return {"product_id": product_id, "result": "DRY_RUN",
                "memo": "URL 진입 OK (dry-run)",
                "elapsed_seconds": round(time.perf_counter() - started_at, 2)}

    try:
        edit_state = enable_inventory_edit_mode(page)
        if edit_state == "no_button":
            # 버튼이 DOM 에 진짜 없음 = 판매중 아님 (마감/운영X 상품이 list 에 잘못 섞임) → SKIP
            print(f"[MRT/{product_id}] '예약 인원 수정' 버튼 없음(판매중 아님) → SKIP")
            return {"product_id": product_id, "result": "스킵",
                    "memo": "예약 인원 수정 버튼 없음 (판매중 아님)",
                    "elapsed_seconds": round(time.perf_counter() - started_at, 2)}
        if edit_state == "not_loaded":
            # 재고 화면이 안 떠서 판매중 여부 자체를 알 수 없음 → SKIP 금지, 실패(재시도 대상)
            print(f"[MRT/{product_id}] 재고 화면 미로드 → 실패(재시도/수동확인)")
            return {"product_id": product_id, "result": "실패",
                    "memo": "재고 화면 미로드(12초) - 판매중 여부 판정 불가, 수동 확인 필요",
                    "elapsed_seconds": round(time.perf_counter() - started_at, 2)}
        if edit_state == "activate_failed":
            # 버튼은 있는데(=판매중) 편집모드 진입 실패 → 성공/스킵 위장 금지.
            # 실패로 표시하면 End-of-run 재시도 대상에 포함됨(수동확인).
            print(f"[MRT/{product_id}] 판매중인데 편집모드 진입 실패 → 실패(수동확인), 재시도 대상")
            return {"product_id": product_id, "result": "실패",
                    "memo": "판매중 상품 편집모드 진입 실패(버튼 있음, 활성화 실패) - 수동 확인 필요",
                    "elapsed_seconds": round(time.perf_counter() - started_at, 2)}
        ok_cells, total_cells = set_target_date_inventory_zero(page)
        saved_ok = click_save_inventory(page)
        click_exit(page)
        if not saved_ok:
            # 저장 확정(예약인원 수정 → 수정하기)이 안 눌려 변경 미커밋 (6107106 사례) → 성공 위장 방지
            return {"product_id": product_id, "result": "실패",
                    "memo": f"저장 미커밋(저장확정 실패): {ok_cells}/{total_cells}개 입력됐으나 커밋 안 됨",
                    "elapsed_seconds": round(time.perf_counter() - started_at, 2)}
        if ok_cells < total_cells:
            # 일부 셀만 0 입력됨 = 부분 마감. 성공으로 위장하지 말고 실패로 표시.
            return {"product_id": product_id, "result": "실패",
                    "memo": f"부분 마감: {ok_cells}/{total_cells}개만 0 ({total_cells - ok_cells}개 미완)",
                    "elapsed_seconds": round(time.perf_counter() - started_at, 2)}
        return {"product_id": product_id, "result": "성공",
                "memo": f"{ok_cells}개 셀 0 입력",
                "elapsed_seconds": round(time.perf_counter() - started_at, 2)}
    except Exception as e:
        return {"product_id": product_id, "result": "실패",
                "memo": f"{e}"[:120],
                "elapsed_seconds": round(time.perf_counter() - started_at, 2)}


def _mrt_open_one_product(page, product_id: str, qty: int, dry_run: bool, course=None):
    """
    /products/experiences/{id}/stock-for-use 진입 후 잔여 인원을 qty 로 설정.

    마감(_mrt_close_one_product)과 완전히 같은 흐름이고 입력값만 0 -> qty 다.
    한 상품에 픽업지가 여러 개면 stockBundles 가 그만큼 잡히므로 수량을 나눠 넣는다.
        Mt. Fuji Highlight 12 + 픽업 2곳 -> 도쿄 6 / 신주쿠 6
    """
    started_at = time.perf_counter()
    stock_url = f"https://partner.myrealtrip.com/products/experiences/{product_id}/stock-for-use"
    try:
        page.goto(stock_url, wait_until="domcontentloaded", timeout=15_000)
        page.wait_for_timeout(800)
        dismiss_notice_popup(page)
    except Exception as e:
        return {"product_id": product_id, "result": "실패",
                "memo": f"goto 실패: {e}",
                "elapsed_seconds": round(time.perf_counter() - started_at, 2)}

    if dry_run:
        return {"product_id": product_id, "result": "DRY_RUN",
                "memo": f"DRY: {qty} 명 오픈 예정 (URL 진입 OK)"
                        + (f" / 코스 '{course}'" if course else ""),
                "elapsed_seconds": round(time.perf_counter() - started_at, 2)}

    try:
        edit_state = enable_inventory_edit_mode(page)
        if edit_state == "no_button":
            print(f"[MRT/{product_id}] '예약 인원 수정' 버튼 없음(판매중 아님) → SKIP")
            return {"product_id": product_id, "result": "스킵",
                    "memo": "예약 인원 수정 버튼 없음 (판매중 아님)",
                    "elapsed_seconds": round(time.perf_counter() - started_at, 2)}
        if edit_state == "not_loaded":
            return {"product_id": product_id, "result": "실패",
                    "memo": "재고 화면 미로드(12초) - 수동 확인 필요",
                    "elapsed_seconds": round(time.perf_counter() - started_at, 2)}
        if edit_state == "activate_failed":
            return {"product_id": product_id, "result": "실패",
                    "memo": "판매중인데 편집모드 진입 실패 - 수동 확인 필요",
                    "elapsed_seconds": round(time.perf_counter() - started_at, 2)}

        ok_cells, total_cells = set_target_date_inventory(page, total=qty, split=True,
                                                          course=course)
        saved_ok = click_save_inventory(page)
        click_exit(page)

        if not saved_ok:
            return {"product_id": product_id, "result": "실패",
                    "memo": f"저장 미커밋: {ok_cells}/{total_cells}개 입력됐으나 커밋 안 됨",
                    "elapsed_seconds": round(time.perf_counter() - started_at, 2)}
        if ok_cells < total_cells:
            return {"product_id": product_id, "result": "실패",
                    "memo": f"부분 적용: {ok_cells}/{total_cells}개만 입력됨",
                    "elapsed_seconds": round(time.perf_counter() - started_at, 2)}
        return {"product_id": product_id, "result": "성공",
                "memo": f"{total_cells}개 셀에 총 {qty}명 배분"
                        + (f" (코스 '{course}')" if course else ""),
                "elapsed_seconds": round(time.perf_counter() - started_at, 2)}
    except Exception as e:
        return {"product_id": product_id, "result": "실패",
                "memo": f"{e}"[:120],
                "elapsed_seconds": round(time.perf_counter() - started_at, 2)}


def run_open(items, target_date_str=None, dry_run: bool = False) -> dict:
    """
    items: [(product_id, qty), ...] 또는 [{"id","qty","course"}, ...]

    course 는 '상품ID 하나에 코스가 여러 개' 인 페이지에서 어느 코스를 열지 정한다.
    (5624093 = 비에이 하이라이트 / 비에이 시그니처 / 비에이 & 후라노)
    없으면 그 날짜 열의 성인 줄 전체에 나눠 넣는다 -- 코스가 하나인 상품만 안전하다.
    """
    import json as _json
    from playwright.sync_api import sync_playwright as _spw

    if target_date_str:
        try:
            _tgt = datetime.strptime(target_date_str, "%Y-%m-%d")
            import sys as _s
            _mod = _s.modules[__name__]
            _mod.target_date = lambda: _tgt
            print(f"[MRT] target_date -> {target_date_str}")
        except Exception as e:
            print(f"[MRT] 날짜 지정 실패: {e}")

    results = []
    print(f"[MRT] 오픈 시작 | {len(items)}건 | dry_run={dry_run}")
    with _spw() as p:
        browser = p.chromium.connect_over_cdp(CDP_URL, timeout=CDP_CONNECT_TIMEOUT_MS)
        if not browser.contexts:
            print("[MRT] Chrome context 없음")
            return {"results": [], "error": "Chrome context 없음"}
        context = browser.contexts[0]
        page = context.new_page()
        try:
            try:
                page.bring_to_front()
            except Exception:
                pass
            for i, raw in enumerate(items, 1):
                if isinstance(raw, dict):
                    pid, qty, course = raw.get("id"), raw.get("qty"), raw.get("course")
                else:
                    (pid, qty), course = raw, None
                label = f" / 코스 '{course}'" if course else ""
                print(f"[MRT] ({i}/{len(items)}) 상품 {pid} → {qty}명{label}")
                r = _mrt_open_one_product(page, str(pid), int(qty), dry_run, course=course)
                r["qty"] = int(qty)
                if course:
                    r["course"] = course
                results.append(r)
                print(f"##MRT_RESULT## {_json.dumps(r, ensure_ascii=False)}")
        finally:
            # P0-2: 이 실행이 만든 탭은 반드시 닫는다
            try:
                from shared.chrome_setup import close_worker_page
                close_worker_page(page, "MRT/open")
            except Exception:
                pass

    ok = sum(1 for r in results if r["result"] == "성공")
    dry = sum(1 for r in results if r["result"] == "DRY_RUN")
    skip = sum(1 for r in results if r["result"] == "스킵")
    fail = sum(1 for r in results if r["result"] == "실패")
    print(f"\n[MRT/open] success={ok + dry} failed={fail} skipped={skip}")
    return {"results": results, "success": ok + dry, "failed": fail, "skipped": skip}


def _mrt_collect_all_products(page, label: str = "") -> list:
    """
    모든 페이지를 read-only 순회하면서 (id, region, name) 수집.

    안정화 보장 (race / 부분 로드 / 워커간 list size 불일치 방지):
      1) 시작 전 STABILIZE_WAIT_MS 안정화 wait (필터 적용 직후 react state 안정화)
      2) total_count == 0 / total_pages == 0 인 동안 재시도 (필터 적용 전 race)
      3) page 1 부터 모든 페이지 read-only 순회 후, "수집 행 수 == total" 검증
         - 일치하면 확정
         - 불일치하면 page 1로 복귀해 재수집 (최대 MAX_OUTER_ATTEMPTS 회)
      4) 일치 안 돼도 "연속 2회 동일 snapshot (productId set)" 이면 확정
         (페이지네이션 badge 가 잘못 표시되는 케이스 — 실제 행이 stable 하면 신뢰)

    반환: [{"id", "region", "name"}, ...] - productId desc 정렬
    """
    MAX_OUTER_ATTEMPTS = 4
    STABILIZE_WAIT_MS = 2000  # 시작 전 안정화 대기

    page.wait_for_timeout(STABILIZE_WAIT_MS)

    prev_ids: list = []
    final_products: list = []
    final_total = 0
    final_total_pages = 0

    for outer in range(MAX_OUTER_ATTEMPTS):
        # 1) total / total_pages 안정화 (0 인 동안 재시도)
        total, total_pages = 0, 0
        for attempt in range(5):
            total = _mrt_get_total_count(page)
            _cur, total_pages = _mrt_get_pagination(page)
            if total > 0 and total_pages > 0:
                break
            print(f"[MRT/{label}] total=0 감지 (attempt {attempt+1}/5) → 1초 대기 후 재시도")
            page.wait_for_timeout(1000)

        if total_pages == 0:
            print(f"[MRT/{label}] total_pages=0, 빈 list 반환")
            return []

        # 2) 재시도 시에는 page 1 로 복귀
        if outer > 0:
            _mrt_goto_page(page, 1)
            page.wait_for_timeout(700)

        # 3) 모든 페이지 read-only 순회
        all_products: list = []
        seen_ids: set = set()
        for pnum in range(1, total_pages + 1):
            if pnum != 1:
                ok = _mrt_goto_page(page, pnum)
                if not ok:
                    print(f"[MRT/{label}] 페이지 {pnum} 이동 실패")
                    break
                page.wait_for_timeout(500)  # 페이지 로드 완료 추가 대기
            products = _mrt_get_products_on_page(page)
            for prod in products:
                pid = prod["id"]
                if pid in seen_ids:
                    continue
                seen_ids.add(pid)
                all_products.append(prod)
            print(f"[MRT/{label}] page {pnum}: {len(products)} 개 행 (누적 {len(all_products)})")

        collected = len(all_products)
        ids_now = sorted(p["id"] for p in all_products)

        # 4) 검증
        count_match = (collected == total)
        snapshot_stable = (collected > 0 and ids_now == prev_ids)

        final_products = all_products
        final_total = total
        final_total_pages = total_pages

        if count_match:
            print(f"[MRT/{label}] ✓ count match: total={total} == collected={collected}, pages={total_pages}")
            break
        if snapshot_stable:
            print(f"[MRT/{label}] ✓ snapshot stable: 2회 연속 동일 {collected}개 (badge total={total} 불일치이나 list 안정 → 확정)")
            break

        print(f"[MRT/{label}] ⚠ 불일치: total={total} vs collected={collected} → page 1 복귀 후 재수집 (attempt {outer+1}/{MAX_OUTER_ATTEMPTS})")
        prev_ids = ids_now
        page.wait_for_timeout(2000)
    else:
        print(f"[MRT/{label}] WARNING: {MAX_OUTER_ATTEMPTS}회 시도 후에도 불일치, 마지막 결과 사용 "
              f"(total={final_total}, collected={len(final_products)}, pages={final_total_pages})")

    # 정렬 (모든 워커가 동일 순서 보장)
    try:
        final_products.sort(key=lambda p: int(p["id"]), reverse=True)
    except Exception:
        final_products.sort(key=lambda p: str(p["id"]), reverse=True)

    print(f"[MRT/{label}] 판매중 총 {len(final_products)}개 상품 확정 (badge total={final_total}, pages={final_total_pages})")
    return final_products


def _mrt_quarter_slice(items: list, quarter: str, direction: str) -> list:
    """
    items 를 quarter 또는 direction 으로 잘라서 (global_idx, item) 튜플 리스트로 반환.
    quarter='1'/'2'/'3'/'4': 4등분 (인덱스 기반, 페이지 카운트 무관)
    direction='forward'/'backward': 2등분
    그 외: 전체.

    [v2] 글로벌 인덱스를 같이 반환 → 호출자가 (global_idx+1 / total) 형식으로 진행률 표시 가능.
    """
    n = len(items)
    if n == 0:
        return []
    indexed = list(enumerate(items))  # [(0, item0), (1, item1), ...]

    if quarter in ("1", "2", "3", "4"):
        q1 = n // 4
        q2 = n // 2
        q3 = (3 * n) // 4
        if quarter == "1":
            return indexed[0:q1]                  # 앞 1/4 (forward)
        elif quarter == "2":
            return indexed[q1:q2][::-1]           # 1/4 ~ 2/4 (reverse, 가운데로 수렴)
        elif quarter == "3":
            return indexed[q2:q3]                 # 2/4 ~ 3/4 (forward)
        else:  # "4"
            return indexed[q3:n][::-1]            # 3/4 ~ 끝 (reverse)
    if direction == "forward":
        return indexed[0 : (n + 1) // 2]
    if direction == "backward":
        return indexed[(n + 1) // 2 :][::-1]
    return indexed


def _mrt_discover_and_close(page, dry_run: bool, direction: str = "", quarter: str = ""):
    """
    /products/experiences 진입 -> 판매중 탭 -> 전체 product list 수집 -> quarter/direction 분할 -> 타겟 지역만 마감.
    quarter = '1'/'2'/'3'/'4' 우선 (4분할), 없으면 direction = 'forward'/'backward' (2분할), 둘 다 없으면 전체.
    핵심: 모든 워커가 동일한 전체 list 를 수집한 뒤 인덱스 기반으로 분할 → 페이지 카운트 race 로 빵꾸 없음.
    반환: dict (success, failed, skipped, errors, products_processed)
    """
    summary = {"success": 0, "failed": 0, "skipped": 0, "errors": [],
               "products_processed": 0, "products_skipped_region": 0}

    label = f"q{quarter}" if quarter else (direction or "all")
    print(f"[MRT] discover 모드 시작 (worker={label})")

    # discover-once-share: 환경변수에 미리 수집된 파일 경로 있으면 거기서 읽기
    # → 4개 worker 동시 페이지네이션 race 제거
    import json as _json
    discover_file = os.environ.get("MRT_DISCOVER_FILE", "").strip()
    if discover_file and os.path.exists(discover_file):
        try:
            with open(discover_file, "r", encoding="utf-8") as _f:
                all_products = _json.load(_f)
            print(f"[MRT/{label}] Discover 파일에서 {len(all_products)}개 로드 (race 없음): {discover_file}")
        except Exception as e:
            summary["errors"].append(f"discover_file 읽기 실패: {e}")
            return summary
    else:
        # 1) /products/experiences 진입
        try:
            page.goto(EXPERIENCES_URL, wait_until="domcontentloaded", timeout=20_000)
            page.wait_for_timeout(2000)
        except Exception as e:
            summary["errors"].append(f"experiences 페이지 진입 실패: {e}")
            return summary

        # 2) 판매중 탭 클릭
        if not _mrt_click_sale_tab(page):
            summary["errors"].append("'판매중' 탭 클릭 실패")
            return summary
        page.wait_for_timeout(1500)

        # 3) 전체 product list 수집 (모든 워커 공통)
        all_products = _mrt_collect_all_products(page, label=label)
        if not all_products:
            summary["errors"].append(f"[{label}] 상품 수집 실패 또는 0개")
            return summary

    # 4) 자기 quarter/direction slice (global_idx 포함 튜플 리스트)
    total_products = len(all_products)
    my_indexed = _mrt_quarter_slice(all_products, quarter, direction)
    print(f"[MRT/{label}] 전체 {total_products}개 → 내 몫 {len(my_indexed)}개 (인덱스 기반 분할)")

    # Work stealing 지원: MRT_CLAIM_DIR 환경변수 있으면 정적 slice 대신 원자적 claim 사용
    claim_dir = os.environ.get("MRT_CLAIM_DIR", "").strip()
    use_work_stealing = bool(claim_dir and os.path.isdir(claim_dir))
    if use_work_stealing:
        print(f"[MRT/{label}] Work-stealing 모드: claim_dir={claim_dir}")
        try:
            _q_int = int(quarter) if quarter in ("1", "2", "3", "4") else 1
        except Exception:
            _q_int = 1
        start_idx = ((_q_int - 1) * total_products) // 4
        my_q_start = ((_q_int - 1) * total_products) // 4
        my_q_end = (_q_int * total_products) // 4 if _q_int < 4 else total_products

    # 5) 마감 처리
    from collections import Counter
    regions_seen: Counter = Counter()
    local_i = 0
    stolen_count = 0

    # 처리할 항목 시퀀스 결정
    if use_work_stealing:
        # 모든 인덱스 시도 (claim 으로 필터링)
        process_seq = [(i, all_products[(start_idx + i) % total_products],
                        (start_idx + i) % total_products) for i in range(total_products)]
    else:
        process_seq = [(local_pos, prod, global_idx) for local_pos, (global_idx, prod) in enumerate(my_indexed)]

    for _seq_i, prod, idx in process_seq:
        # work-stealing: claim 시도
        if use_work_stealing:
            claim_path = os.path.join(claim_dir, f"claim_{idx:04d}.marker")
            try:
                _fd = os.open(claim_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(_fd, f"q{_q_int}".encode())
                os.close(_fd)
            except FileExistsError:
                continue  # 다른 worker 가 가져감
            except Exception as _ce:
                print(f"[MRT/{label}] claim 파일 생성 실패 idx={idx}: {_ce}")
                continue

        local_i += 1
        pid = prod["id"]
        region = prod["region"]
        regions_seen[region] += 1
        is_stolen = use_work_stealing and not (my_q_start <= idx < my_q_end)
        if is_stolen:
            stolen_count += 1
        tag = (" [STEAL]" if is_stolen else (" [OWN]" if use_work_stealing else ""))

        if region not in TARGET_REGIONS_JP:
            summary["products_skipped_region"] += 1
            if use_work_stealing:
                # claim 파일을 그대로 둬서 다른 워커가 재claim 못하게 함 (rename 시 사라져서 재처리 위험).
                # done 파일은 별도 생성 (모니터링용).
                try:
                    with open(os.path.join(claim_dir, f"done_{idx:04d}.marker"), "w") as _df:
                        _df.write(f"skip_region")
                except Exception:
                    pass
            continue

        print(f"  -> ({local_i}/{total_products}){tag} [{pid}] {region} | {prod['name']}")
        log = _mrt_close_one_product(page, pid, dry_run)
        summary["products_processed"] += 1
        if log["result"] == "성공":
            summary["success"] += 1
        elif log["result"] == "DRY_RUN":
            summary["skipped"] += 1
        elif log["result"] == "스킵":
            # 판매중 아닌 상품 (list 에 잘못 섞임) → skip, 실패 아님
            summary["skipped"] += 1
        else:
            summary["failed"] += 1
            summary["errors"].append(f"{pid}: {log['memo']}")

        if use_work_stealing:
            # claim 파일을 그대로 둬서 영구 마커로 사용 (재처리 방지).
            # done 파일은 별도 생성 (모니터링용).
            try:
                with open(os.path.join(claim_dir, f"done_{idx:04d}.marker"), "w") as _df:
                    _df.write(f"done")
            except Exception:
                pass

        # 다음 상품은 _mrt_close_one_product 가 stock-for-use URL 로 직접 진입.
        # → list 페이지 복귀 + 판매중 탭 재클릭 불필요 (KKDAY 와 동일 패턴).
        # 상품당 ~3-5초 절약.

    # ==============================================================
    # End-of-run 재시도: 실패한 상품 모아서 한번 더 시도
    # 일시적인 UI race / 페이지 race 로 실패한 경우 회복.
    # ==============================================================
    if summary["errors"] and not dry_run:
        # errors 에서 product_id 만 추출 (포맷: "{pid}: {memo}")
        retry_pids = []
        for err in summary["errors"]:
            if ":" in err and not err.startswith("["):  # warn 메시지 제외
                pid_part = err.split(":", 1)[0].strip()
                if pid_part.isdigit():
                    retry_pids.append(pid_part)
        if retry_pids:
            print(f"[MRT/{label}] End-of-run 재시도: {len(retry_pids)}개 실패 상품")
            recovered_pids = set()
            for retry_i, pid in enumerate(retry_pids, 1):
                try:
                    log = _mrt_close_one_product(page, pid, dry_run)
                except Exception as e:
                    log = {"result": "실패", "memo": f"재시도 예외: {e}"}
                if log["result"] == "성공":
                    recovered_pids.add(pid)
                    summary["failed"] -= 1
                    summary["success"] += 1
                    print(f"[MRT/{label}] (재시도 {retry_i}/{len(retry_pids)}) [{pid}] 회복 → 성공")
                else:
                    print(f"[MRT/{label}] (재시도 {retry_i}/{len(retry_pids)}) [{pid}] 여전히 {log['result']}: {log.get('memo','')[:60]}")
            # 회복된 pid 의 error 메시지 제거
            if recovered_pids:
                summary["errors"] = [e for e in summary["errors"]
                                     if not any(e.startswith(f"{p}:") for p in recovered_pids)]
            print(f"[MRT/{label}] End-of-run 재시도 완료: {len(recovered_pids)}/{len(retry_pids)} 회복")

    # 안전장치: 내 몫이 있었는데 타겟 지역 매칭이 0이면 지역 라벨 변경 의심
    if summary["products_processed"] == 0 and len(my_indexed) >= 5:
        top = ", ".join(f"{r}({c})" for r, c in regions_seen.most_common(8))
        warn = (f"[{label}] 내 몫 {len(my_indexed)}개 발견했으나 타겟지역 매칭 0건 "
                f"— 지역 라벨 변경 의심 (TARGET_REGIONS_JP 확인 필요). 발견한 지역: {top}")
        print(f"[MRT] ⚠️ {warn}")
        summary["errors"].append(warn)

    return summary


# ============================================================
# CLI (subprocess 실행용)
# ============================================================
if __name__ == "__main__":
    import argparse
    import json as _json
    import sys as _sys_cli

    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--date", default=None, help="YYYY-MM-DD")
    ap.add_argument("--direction", default=None, help="forward|backward (2분할)")
    ap.add_argument("--quarter", default=None, help="1|2|3|4 (4분할, direction 보다 우선)")
    ap.add_argument("--items", default=None,
                    help="오픈 모드: '상품ID=수량' 을 콤마로 (예: 5889847=12,4700281=6)")
    ap.add_argument("--items-file", default=None,
                    help='오픈 모드: [{"id","qty","course"}] JSON. 코스명에 콤마가 들어가서 --items 로는 못 넘긴다')
    ap.add_argument("--mode", default="auto", choices=["auto", "discover", "open"],
                    help="auto=원래 흐름 (discover+process), discover=수집만 하고 JSON 저장 후 종료")
    ap.add_argument("--output", default=None,
                    help="--mode discover 일 때 결과 JSON 파일 경로")
    ap.add_argument("--discover-file", default=None,
                    help="미리 수집한 product list JSON 파일 경로 (worker 가 discover skip)")
    ap.add_argument("--claim-dir", default=None,
                    help="Work-stealing claim 디렉토리. 지정 시 정적 slice 대신 원자적 claim 사용")
    args = ap.parse_args()

    if args.direction:
        os.environ["MRT_DIRECTION"] = args.direction.lower()
    if args.quarter:
        os.environ["MRT_QUARTER"] = str(args.quarter).strip()
    if args.discover_file:
        os.environ["MRT_DISCOVER_FILE"] = args.discover_file
    if args.claim_dir:
        os.environ["MRT_CLAIM_DIR"] = args.claim_dir

    tgt = None
    if args.date:
        from datetime import datetime as _dt
        tgt = _dt.strptime(args.date, "%Y-%m-%d").date()

    # ==========================================================
    # MODE: discover (수집만 + JSON 저장 후 종료)
    # ==========================================================
    if args.mode == "discover":
        if not args.output:
            print("[MRT] --mode discover 는 --output 필수")
            _sys_cli.exit(2)

        # target_date 적용 (필요 시) - 인라인 monkey-patch
        if tgt:
            try:
                _self_mod = _sys_cli.modules[__name__]
                _tgt_dt = datetime(tgt.year, tgt.month, tgt.day)
                def _patched_target_date():
                    return _tgt_dt
                _self_mod.target_date = _patched_target_date
                print(f"[MRT/discover] target_date monkey-patch 완료 → {_tgt_dt.strftime('%Y-%m-%d')}")
            except Exception as _e:
                print(f"[MRT/discover] target_date monkey-patch 실패: {_e}")

        # Chrome 보장
        try:
            from shared.health import ensure_chrome as _ensure_chrome_disc
            _ensure_chrome_disc(int(CDP_URL.rsplit(":", 1)[-1]), "start_chrome_global.bat", wait_sec=10)
        except Exception as e:
            print(f"[MRT/discover] ensure_chrome 실패 (계속): {e}")

        try:
            with sync_playwright() as p:
                browser = p.chromium.connect_over_cdp(CDP_URL, timeout=CDP_CONNECT_TIMEOUT_MS)
                if not browser.contexts:
                    print("[MRT/discover] Chrome context 없음")
                    _sys_cli.exit(3)
                context = browser.contexts[0]
                page = context.new_page()
                try:
                    page.bring_to_front()
                except Exception:
                    pass

                page.goto(EXPERIENCES_URL, wait_until="domcontentloaded", timeout=20_000)
                page.wait_for_timeout(2000)

                if not _mrt_click_sale_tab(page):
                    print("[MRT/discover] '판매중' tab click failed")
                    _sys_cli.exit(4)
                page.wait_for_timeout(1500)

                all_products = _mrt_collect_all_products(page, label="discover")
                if not all_products:
                    print("[MRT/discover] product collect failed or 0")
                    _sys_cli.exit(5)

                out_path = args.output
                os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
                with open(out_path, "w", encoding="utf-8") as f:
                    _json.dump(all_products, f, ensure_ascii=False, indent=2)
                print(f"[MRT/discover] done: {len(all_products)} products -> {out_path}")
                try:
                    from shared.chrome_setup import close_worker_page
                    close_worker_page(page, "MRT/discover")
                except Exception:
                    pass
                _sys_cli.exit(0)
        except Exception as e:
            print(f"[MRT/discover] 치명 오류: {e}")
            _sys_cli.exit(6)

    # ==========================================================
    # MODE: open - 라스트미닛 오픈 (상품별 수량 입력)
    # ==========================================================
    if args.mode == "open":
        items = []
        if args.items_file:
            import json as _j
            with open(args.items_file, encoding="utf-8") as fh:
                items = _j.load(fh)
            print(f"[MRT/open] items-file 에서 {len(items)}건")
        for chunk in (args.items or "").split(","):
            chunk = chunk.strip()
            if not chunk:
                continue
            if "=" not in chunk:
                print(f"[MRT/open] 형식 오류(무시): {chunk}")
                continue
            pid, qty = chunk.split("=", 1)
            try:
                items.append((pid.strip(), int(qty.strip())))
            except ValueError:
                print(f"[MRT/open] 수량 오류(무시): {chunk}")
        if not items:
            print("[MRT/open] 처리할 항목이 없습니다. --items 확인")
            _sys_cli.exit(2)
        res = run_open(items, target_date_str=args.date, dry_run=args.dry_run)
        _sys_cli.exit(0 if res.get("failed", 0) == 0 else 1)

    # ==========================================================
    # MODE: auto (기본) - 기존 run_close 흐름
    # ==========================================================
    r = run_close(target_date=tgt, dry_run=args.dry_run)
    print(f"\n[MRT] success={r['success']} failed={r['failed']} skipped={r['skipped']}")
    if r["errors"]:
        print("Errors:")
        for e in r["errors"][:20]:
            print("  -", e)
