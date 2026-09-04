# -*- coding: utf-8 -*-
"""
klook_worker.py
Klook Merchant Center 한국/일본 익일 Inventory Open 자동화 봇 - 통합 worker

(klook_open_worker.py) + (klook_new_worker.py)를 기준으로 통합.
Package 워크플로우(기존 UI) / Activity 워크플로우(새버전 UI) 둘 다 처리.

매핑은 packages.py(단일 source of truth)에서 import. 이 파일 안에 매핑을 두지 않음.

호출 방식:
1) main.py / bulk.py 가 task JSON 파일을 만들어 --tasks-file 로 worker 호출 (운영 경로)
2) python klook_worker.py 단독 실행 시 ask_tasks_from_user() 가 대화형 입력 받음 (디버그 경로)

사용 전:
1) Chrome remote-debugging-port 로 실행 후 Klook Merchant Center 로그인
2) 이 파일을 main.py / bulk.py 통해 실행
"""

from datetime import datetime, timedelta
from pathlib import Path
import traceback
import argparse
import json
import os
import re
import sys

from playwright.sync_api import sync_playwright


# ──────────────────────────────────────────────────────────────────────────────
# 로그 verbosity 설정. VERBOSE=False (기본): [단계]/[오류]/[주의] 만 출력.
# 디버그 시 환경변수 KLOOK_VERBOSE=1 로 모든 [진행]/[완료]/[안내] 메시지 표시.
# ──────────────────────────────────────────────────────────────────────────────
VERBOSE = os.environ.get('KLOOK_VERBOSE', '').strip() in ('1', 'true', 'TRUE', 'yes')

def _v(*args, **kwargs):
    """Verbose-only print. KLOOK_VERBOSE=1 일 때만 출력."""
    if VERBOSE:
        print(*args, **kwargs)


# 매핑은 packages.py 에서만 관리. 여기서는 조회만.
from packages import get_package, PACKAGES as _PACKAGES_MAP

# Chrome 에 붙을 때 기다리는 시간 (ms).
# ⚠️ playwright 기본값은 180초다. Chrome 이 "반쯤 죽은" 상태 -- /json/version 은
#    200 을 주는데 CDP 핸드셰이크(<ws connected> 이후)가 안 끝나는 상태 -- 면
#    워커마다 3분씩 버리고 그제서야 실패한다.
#    2026-09-01 팀원 PC: 마감에서 25번, 2026-09-02: MRT 오픈 3건 전멸.
#    빨리 실패해야 사람이 그 Chrome 을 다시 켤 시간이 있다.
CDP_CONNECT_TIMEOUT_MS = int(os.environ.get("CDP_CONNECT_TIMEOUT_MS") or 30000)


# 하위 호환: 기존 코드에서 PACKAGE_MAP 참조 시 packages.py 데이터를 사용
# (신규 코드는 from packages import get_package 를 직접 사용 권장)
PACKAGE_MAP = {k: v['id'] for k, v in _PACKAGES_MAP.items()}

PACKAGE_SEARCH_URL_CACHE = None
ACTIVITY_SEARCH_URL_CACHE = None

# 특정 날짜 override — process_task 시작 시 task['target_date'] 있으면 설정됨.
# None 이면 기본값(내일) 사용.
# 형식: datetime 객체 (또는 date)
_TARGET_DATE_OVERRIDE = None


def set_target_date_override(dt):
    """process_task 시작 시 특정 날짜 지정 (datetime 객체 또는 None)."""
    global _TARGET_DATE_OVERRIDE
    _TARGET_DATE_OVERRIDE = dt


def _parse_target_date(s: str):
    """날짜 문자열 파싱. YYYY-MM-DD / MM/DD / M/D / MM-DD 지원.

    - YYYY 없으면 오늘 기준 자동 (과거 날짜면 내년으로 자동 조정)
    - 반환: datetime 객체 또는 None (파싱 실패)
    """
    if not s:
        return None
    s = str(s).strip()
    today = datetime.today()
    # YYYY-MM-DD 형식
    m = re.match(r'^(\d{4})[-/](\d{1,2})[-/](\d{1,2})$', s)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except Exception:
            return None
    # MM/DD 또는 MM-DD 또는 M/D 형식
    m = re.match(r'^(\d{1,2})[-/](\d{1,2})$', s)
    if m:
        try:
            mm = int(m.group(1))
            dd = int(m.group(2))
            year = today.year
            candidate = datetime(year, mm, dd)
            # 과거 날짜 (오늘 이전) 면 내년으로
            if candidate.date() < today.date():
                candidate = datetime(year + 1, mm, dd)
            return candidate
        except Exception:
            return None
    return None


def tomorrow_date_obj():
    """기본은 익일. _TARGET_DATE_OVERRIDE 있으면 그걸 반환."""
    if _TARGET_DATE_OVERRIDE is not None:
        return _TARGET_DATE_OVERRIDE
    return datetime.today() + timedelta(days=1)


def tomorrow_iso():
    return tomorrow_date_obj().strftime("%Y-%m-%d")


def tomorrow_day_number():
    return str(tomorrow_date_obj().day)


def normalize_text(text: str) -> str:
    return " ".join(str(text).replace("\xa0", " ").split())


def _click_safe(loc, timeout=2500, force_on_fail=True):
    """
    클릭. 세 단계로 물러선다.

      1) 보통 클릭
      2) force=True  — actionability(보임/활성/안 움직임) 검사 건너뜀
      3) JS element.click()  — 좌표를 아예 안 쓴다

    3번이 필요한 이유: Ant Design 모달/드롭다운은 열릴 때 애니메이션이 있어서
    Playwright 가 'element is not stable' 로 거부한다. force 도 좌표를 잡으려
    하므로 움직이는 요소에서는 같이 실패한다. JS 클릭은 그 영향을 안 받는다.
    (2026-08-26 오픈: MBC 가 Confirm 버튼에서 이것 때문에 저장이 안 됐다)

    호출 전에 visible 확인을 하고 부르는 자리들이라, JS 클릭이 숨은 버튼을
    누를 위험은 없다.
    """
    try:
        loc.click(timeout=timeout)
        return True
    except Exception as first_err:
        if not force_on_fail:
            raise
        try:
            loc.click(timeout=max(800, timeout // 2), force=True)
            return True
        except Exception:
            pass
        try:
            loc.evaluate("el => el.click()")
            return True
        except Exception:
            raise first_err


def _assert_visible(page, selector, label, timeout_ms=5000):
    """단계별 state 검증 ().

 selector 가 보이지 않으면 명확한 에러 메시지로 raise.
 selector 는 Playwright locator 문자열 또는 (locator-instance) 둘 다 허용.
    """
    try:
        if isinstance(selector, str):
            loc = page.locator(selector).first
        else:
            loc = selector
        loc.wait_for(state="visible", timeout=timeout_ms)
        return True
    except Exception as e:
        raise Exception(
            f"[검증 실패] '{label}' 화면 요소가 {timeout_ms}ms 안에 보이지 않습니다. "
            f"(selector={selector if isinstance(selector,str) else '<locator>'}) / 원인: {e}"
        )


def _open_sidebar_section(page, submenu_text, item_text, timeout_ms=8000):
    """사이드바 'submenu_text' submenu 펼치고 그 안의 'item_text' leaf 클릭 ().

 안정성 우선순위:
 1) aria-haspopup / aria-expanded (Ant Design submenu 공식 ARIA)
 2) role="menuitem" (Ant Design leaf 공식 ARIA)
 3) ant-menu-* class (Ant Design)
 4) 텍스트 매칭 (마지막 fallback)

 Ant Design 4 → 5 마이그레이션이나 다국어 라벨 변경에 어느정도 견딤.
    """
    print(f"[사이드바] '{submenu_text}' 펼치기 → '{item_text}' 클릭")

 # 1) submenu (예: 'My Activities') 펼치기
    submenu_selectors = [
 # aria 우선
        f"xpath=//*[@aria-haspopup='true'][normalize-space()='{submenu_text}']",
        f"xpath=//*[@aria-expanded][normalize-space()='{submenu_text}']",
 # role menuitem (일부 Ant 버전)
        f"xpath=//*[@role='menuitem'][normalize-space()='{submenu_text}']",
 # Ant class
        f"xpath=//div[contains(@class,'ant-menu-submenu-title')][normalize-space()='{submenu_text}']",
        f"css=.ant-menu-submenu-title:has-text('{submenu_text}')",
 # 텍스트 매칭 (최후)
        f"text={submenu_text}",
    ]
    opened = False
    for sel in submenu_selectors:
        try:
            loc = page.locator(sel).first
            loc.wait_for(state="visible", timeout=1500)
            _click_safe(loc, timeout=2000)
            page.wait_for_timeout(450)
            opened = True
            break
        except Exception:
            continue

    if not opened:
 # JS fallback: aria-haspopup 우선
        try:
            opened = bool(page.evaluate(
                """(name) => {
 const norm = s => (s || '').replace(/\\s+/g,' ').trim();
 let target = Array.from(document.querySelectorAll('[aria-haspopup]'))
 .find(e => norm(e.innerText || e.textContent || '') === name);
 if (!target) target = Array.from(document.querySelectorAll('.ant-menu-submenu-title, [class*="submenu-title"]'))
 .find(e => norm(e.innerText || e.textContent || '') === name);
 if (!target) return false;
 target.click();
 return true;
 }""",
                submenu_text,
            ))
            if opened:
                page.wait_for_timeout(450)
        except Exception:
            pass

 # 2) leaf item (예: 'Activity management') 클릭
    item_selectors = [
 # role=menuitem 우선
        f"xpath=//*[@role='menuitem'][normalize-space()='{item_text}']",
 # Ant menu item
        f"xpath=//li[contains(@class,'ant-menu-item')][normalize-space()='{item_text}']",
        f"css=li.ant-menu-item:has-text('{item_text}')",
 # 텍스트 매칭 (최후)
        f"xpath=//*[normalize-space()='{item_text}']",
        f"text={item_text}",
    ]
    clicked = False
    for sel in item_selectors:
        try:
            loc = page.locator(sel).last
            loc.wait_for(state="visible", timeout=2500)
            _click_safe(loc, timeout=2500)
            clicked = True
            break
        except Exception:
            continue

    if not clicked:
 # JS fallback: role=menuitem 우선
        try:
            clicked = bool(page.evaluate(
                """(name) => {
 const norm = s => (s || '').replace(/\\s+/g,' ').trim();
 let target = Array.from(document.querySelectorAll('[role="menuitem"]'))
 .find(e => norm(e.innerText || e.textContent || '') === name);
 if (!target) target = Array.from(document.querySelectorAll('li.ant-menu-item, [class*="ant-menu-item"]'))
 .find(e => norm(e.innerText || e.textContent || '') === name);
 if (!target) return false;
 target.click();
 return true;
 }""",
                item_text,
            ))
            if clicked:
                page.wait_for_timeout(700)
        except Exception:
            pass

    if not clicked:
        raise Exception(
            f"[사이드바] '{item_text}' 메뉴를 클릭하지 못했습니다 "
            f"(submenu='{submenu_text}' 펼침={opened}). "
            f"DOM 변경 가능성 → role='menuitem' 또는 라벨 변경 확인 필요."
        )

    return True


def _click_tab(page, tab_text, timeout_ms=4000):
    """Activity/Package 같은 ant-tabs 탭 클릭 ().

 안정성 우선순위:
 1) role='tab' + 텍스트 (Ant Design 공식 ARIA)
 2) ant-tabs-tab class + 텍스트
 3) JS fallback
 클릭 후 aria-selected='true' 검증.
    """
    selectors = [
        f"css=[role='tab']:has-text('{tab_text}')",
        f"xpath=//*[@role='tab'][normalize-space()='{tab_text}']",
        f"xpath=//div[contains(@class,'ant-tabs-tab')][normalize-space()='{tab_text}']",
        f"text={tab_text}",
    ]
    clicked = False
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            loc.wait_for(state="visible", timeout=2000)
            _click_safe(loc, timeout=2500)
            page.wait_for_timeout(500)
            clicked = True
            break
        except Exception:
            continue

    if not clicked:
 # JS fallback
        try:
            clicked = bool(page.evaluate(
                """(name) => {
 const norm = s => (s || '').replace(/\\s+/g,' ').trim();
 const target = Array.from(document.querySelectorAll('[role="tab"], .ant-tabs-tab'))
 .find(e => norm(e.innerText || e.textContent || '') === name);
 if (!target) return false;
 target.click();
 return true;
 }""",
                tab_text,
            ))
            if clicked:
                page.wait_for_timeout(500)
        except Exception:
            pass

    if not clicked:
        raise Exception(f"[탭] '{tab_text}' 탭을 클릭하지 못했습니다. DOM 변경 가능성 → role='tab' 확인.")

 # aria-selected 검증
    try:
        page.locator(
            f"css=[role='tab'][aria-selected='true']:has-text('{tab_text}')"
        ).first.wait_for(state="visible", timeout=timeout_ms)
        print(f"[탭] '{tab_text}' 활성화 확인 (aria-selected=true)")
    except Exception:
 # ant-tabs-tab-active class fallback
        try:
            page.locator(
                f"css=.ant-tabs-tab-active:has-text('{tab_text}')"
            ).first.wait_for(state="visible", timeout=2000)
            print(f"[탭] '{tab_text}' 활성화 확인 (ant-tabs-tab-active)")
        except Exception:
            print(f"[탭] [주의] '{tab_text}' 활성 상태 검증 실패. 진행은 계속.")
    return True





def get_body_text(page, timeout_ms=2500) -> str:
    """Klook 화면은 DOM이 무거워 locator('body').inner_text()가 멈출 때가 있어 JS로 빠르게 본문 텍스트를 읽습니다."""
    old_timeout = None
    try:
        old_timeout = page._timeout_settings.timeout()
    except Exception:
        old_timeout = None
    try:
        page.set_default_timeout(timeout_ms)
    except Exception:
        pass
    try:
        return normalize_text(page.evaluate("""() => document.body ? (document.body.innerText || document.body.textContent || '') : ''"""))
    except Exception:
        try:
            return normalize_text(page.locator('body').text_content(timeout=timeout_ms) or '')
        except Exception:
            return ''
    finally:
        try:
            if old_timeout:
                page.set_default_timeout(old_timeout)
        except Exception:
            pass


def is_package_search_screen(page) -> bool:
    try:
        return bool(page.evaluate("""() => {
 const text = (document.body ? document.body.innerText : '').replace(/\s+/g, ' ').trim();
 return text.includes('Package ID & title') && text.includes('Package status') && text.includes('Search');
 }"""))
    except Exception:
        text = get_body_text(page, 1500)
        return 'Package ID & title' in text and 'Search' in text



def remember_package_search_url(page):
    """현재 화면이 Package 검색 화면이면 URL을 캐시합니다. 다음 상품/오류 복구 때 직접 복귀용으로 사용합니다."""
    global PACKAGE_SEARCH_URL_CACHE
    try:
        if is_package_search_screen(page) and page.url.startswith("http"):
            PACKAGE_SEARCH_URL_CACHE = page.url
    except Exception:
        pass


def close_open_drawers_or_popups(page):
    """다음 상품으로 넘어가기 전 남아 있는 팝업/드롭다운을 최대한 닫습니다."""
    for _ in range(3):
        try:
            page.keyboard.press("Escape")
            page.wait_for_timeout(250)
        except Exception:
            pass


def go_package_search_hard(page, reason=""):
    """
: 여러 상품 연속 처리용 강제 복귀.
 - 성공/실패 후 현재 위치가 상세/Activity 탭/팝업 어디든 Package 검색 화면으로 복구합니다.
 - 캐시 URL이 있으면 가장 먼저 직접 이동합니다.
 - 실패 시 메뉴 클릭 방식으로 복구합니다.
    """
    if reason:
        _v(f"[진행] Package 검색 화면 강제 복귀: {reason}")
    else:
        _v("[진행] Package 검색 화면 강제 복귀")

    close_open_drawers_or_popups(page)

    if is_package_search_screen(page):
        remember_package_search_url(page)
        _v("[완료] 이미 Package 검색 화면")
        return True

    global PACKAGE_SEARCH_URL_CACHE
    if PACKAGE_SEARCH_URL_CACHE:
        try:
            _v("[진행] 캐시된 Package 검색 URL 직접 이동")
            page.goto(PACKAGE_SEARCH_URL_CACHE, wait_until="domcontentloaded", timeout=12000)
            page.wait_for_timeout(900)
 # Activity 탭으로 떨어져도 Package 탭만 누르면 됨
            if not is_package_search_screen(page):
                for selector in ["xpath=//*[normalize-space()='Package']", "text=Package"]:
                    try:
                        loc = page.locator(selector).last
                        loc.wait_for(state="visible", timeout=2000)
                        _click_safe(loc, timeout=2500)
                        page.wait_for_timeout(700)
                        break
                    except Exception:
                        continue
            if is_package_search_screen(page):
                remember_package_search_url(page)
                _v("[완료] Package 검색 URL 직접 복귀 완료")
                return True
        except Exception as e:
            print(f"[주의] 캐시 URL 복귀 실패: {e}")

    try:
        goto_activity_management_package(page)
        remember_package_search_url(page)
        _v("[완료] 메뉴 방식 Package 검색 화면 복귀")
        return True
    except Exception as e:
        print(f"[주의] 메뉴 방식 복귀 실패: {e}")

 # 마지막 fallback: 현재 Klook merchant origin 기준 Activity management URL 후보 이동
    try:
        from urllib.parse import urlparse
        origin = f"{urlparse(page.url).scheme}://{urlparse(page.url).netloc}"
        candidates = [
            origin + "/mspa/experiencesadmincommon/act/activity/list?lang=en_US",
            origin + "/mspa/experiencesadmincommon/act/package/list?lang=en_US",
        ]
        for url in candidates:
            try:
                _v(f"[진행] Package 검색 후보 URL 이동: {url}")
                page.goto(url, wait_until="domcontentloaded", timeout=12000)
                page.wait_for_timeout(1200)
                for selector in ["xpath=//*[normalize-space()='Package']", "text=Package"]:
                    try:
                        loc = page.locator(selector).last
                        loc.wait_for(state="visible", timeout=1800)
                        _click_safe(loc, timeout=2000)
                        page.wait_for_timeout(700)
                        break
                    except Exception:
                        continue
                if is_package_search_screen(page):
                    remember_package_search_url(page)
                    _v("[완료] 후보 URL Package 검색 화면 복귀 완료")
                    return True
            except Exception:
                continue
    except Exception:
        pass

    return False

def ask_tasks_from_user():
    """
 디버그/단독 실행용 대화형 입력. 운영 경로(main.py / bulk.py)는 --tasks-file 사용.
 packages.py 의 get_package() 로 별칭/공백 차이도 정규화해서 조회한다.
    """
    print("\n[입력] 작업할 상품명과 수량을 한 줄씩 입력하세요.")
    print("[예시] 남쁘 3")
    _v("[완료] 입력이 끝나면 END 입력 후 Enter\n")

    tasks = []
    while True:
        line = input("> ").strip()
        if not line:
            continue
        if line.upper() == "END":
            break

        parts = line.split()
        if len(parts) < 2:
            print("[오류] 형식이 잘못되었습니다. 예: 남쁘 3")
            continue

        qty_text = parts[-1]
        name = " ".join(parts[:-1]).strip()

        if not qty_text.isdigit():
            print("[오류] 수량은 숫자여야 합니다. 예: 남쁘 3")
            continue

        info = get_package(name)
        if not info:
            print(f"[오류] 등록되지 않은 상품명입니다: {name}")
            print(f"[참고] packages.py 에서 상품명을 확인하세요.")
            continue

        tasks.append({
            "name": info['canonical_name'],
            "package_id": info['id'],
            "inventory": int(qty_text),
            "workflow": info['workflow'],
            "input_text": f"{name} {qty_text}",
        })
    return tasks


def click_search_button(page):
    """Search 버튼 클릭 ( - data-spm-module 우선).

 안정성 우선순위:
 1) data-spm-module*='FilterSearch' (Klook 추적용 attribute, Package/Activity 양쪽 다)
 2) button.ant-btn-primary + 'Search' 텍스트
 3) 텍스트만 매칭 (최후)

 검증: 클릭 후 800ms 대기 (네트워크 호출 시작). 결과 검증은 search_package() 측에서.
    """
 # 1) data-spm-module 우선 (가장 안정적)
 # Package 화면: data-spm-module="PackageFilterSearch?trg=manual"
 # Activity 화면: data-spm-module="ActivityFilterSearch?trg=manual"
    for selector in [
        "css=button[data-spm-module*='FilterSearch']",
        "css=button.ant-btn-primary[data-spm-module*='Search']",
    ]:
        try:
            btn = page.locator(selector).first
            btn.wait_for(state="visible", timeout=2000)
            _click_safe(btn, timeout=2500)
            page.wait_for_timeout(800)
            print(f"[Search] data-spm-module 매칭 클릭")
            return True
        except Exception:
            continue

 # 2) Ant Design primary button + Search 텍스트
    for selector in [
        "css=button.ant-btn-primary:has-text('Search')",
        "xpath=//button[contains(@class,'ant-btn-primary')][.//span[normalize-space()='Search']]",
    ]:
        try:
            btn = page.locator(selector).first
            btn.wait_for(state="visible", timeout=2000)
            _click_safe(btn, timeout=2500)
            page.wait_for_timeout(800)
            print(f"[Search] ant-btn-primary + 텍스트 매칭")
            return True
        except Exception:
            continue

 # 3) JS fallback (data-spm-module 우선, 텍스트 보조)
    try:
        clicked = bool(page.evaluate("""() => {
 // data-spm-module 우선
 let cand = Array.from(document.querySelectorAll('button[data-spm-module]'))
 .find(el => /FilterSearch/i.test(el.getAttribute('data-spm-module') || '')
 && el.offsetParent !== null);
 if (!cand) {
 const norm = s => (s || '').replace(/\\s+/g,' ').trim();
 cand = Array.from(document.querySelectorAll('button.ant-btn-primary, button, [role="button"]'))
 .find(el => norm(el.innerText || el.textContent || '') === 'Search' && el.offsetParent !== null);
 }
 if (!cand) return false;
 cand.click();
 return true;
 }"""))
        if clicked:
            page.wait_for_timeout(800)
            print(f"[Search] JS fallback 클릭")
            return True
    except Exception:
        pass

 # 4) 최후 fallback: 텍스트만
    for selector in [
        "xpath=//button[normalize-space()='Search']",
        "text=Search",
    ]:
        try:
            btn = page.locator(selector).first
            btn.wait_for(state="visible", timeout=2000)
            _click_safe(btn, timeout=2500)
            page.wait_for_timeout(800)
            print(f"[Search] 텍스트 fallback 클릭")
            return True
        except Exception:
            continue

    raise Exception("[Search 버튼] 모든 셀렉터로 찾지 못함. DOM 변경 가능성 → data-spm-module 또는 라벨 확인.")

def goto_activity_management_package(page):
    """My Activities → Activity management → Package 탭 이동 + URL 직접 이동.
 이미 Package 검색 화면이면 절대 메뉴를 다시 누르지 않고 바로 통과합니다.

: 먼저 act/management URL 로 직접 이동 시도. 실패 시 메뉴 클릭 로직으로 fallback.
    """
    _v("[진행] My Activities > Activity management > Package 이동")

    if is_package_search_screen(page):
        _v("[안내] 현재 Package 검색 화면으로 확인되어 이동 생략")
        remember_package_search_url(page)
        return

 # ──: URL 직접 이동 시도 ──────────────────────────────────────
 # 메뉴 탐색 없이 바로 management 화면으로 가서 Package 탭 클릭만 시도한다.
 # 실패하면 아래 기존 메뉴 클릭 로직이 그대로 동작.
    try:
        origin = re.match(r'^(https?://[^/]+)', page.url).group(1)
        direct_url = origin + "/mspa/experiencesadmincommon/act/management"
        _v(f"[진행] URL 직접 이동 시도: {direct_url}")
        page.goto(direct_url, wait_until='domcontentloaded', timeout=12000)
        page.wait_for_timeout(800)
 # Package 탭 클릭 (Activity 가 기본이므로 Package 로 전환 필요)
        for selector in ["xpath=//*[normalize-space()='Package']", "text=Package"]:
            try:
                loc = page.locator(selector).last
                loc.wait_for(state='visible', timeout=2500)
                _click_safe(loc, timeout=2500)
                page.wait_for_timeout(700)
                break
            except Exception:
                continue
 # 검색 화면 확인
        for _ in range(8):
            if is_package_search_screen(page):
                _v("[완료] URL 직접 이동으로 Package 검색 화면 진입")
                remember_package_search_url(page)
                return
            page.wait_for_timeout(250)
        print("[주의] URL 직접 이동 후 Package 화면 확인 실패. 기존 메뉴 클릭으로 fallback")
    except Exception as e:
        print(f"[주의] URL 직접 이동 실패: {e}. 기존 메뉴 클릭으로 fallback")
 # ── 끝. 실패 시 아래 기존 로직 그대로 진행 ────────────────

 # 페이지가 잠깐 멈춰 있어도 body inner_text timeout으로 종료하지 않도록 JS 기반으로만 판단합니다.
    try:
        page.wait_for_load_state("domcontentloaded", timeout=4000)
    except Exception:
        pass

 #: 사이드바 탐색은 공통 헬퍼로 (aria-haspopup / role=menuitem 우선)
    try:
        _open_sidebar_section(page, "My Activities", "Activity management")
    except Exception as e:
        print(f"[주의] 사이드바 탐색 실패: {e}")

 # 검색 화면 진입 대기
    for _ in range(8):
        if is_package_search_screen(page):
            _v("[완료] Package 검색 화면 진입")
            remember_package_search_url(page)
            return
        page.wait_for_timeout(300)

 #: Package 탭으로 전환 (Activity 가 기본이므로 Package 탭 클릭 필요)
    try:
        _click_tab(page, "Package")
    except Exception as e:
        print(f"[주의] Package 탭 클릭 실패: {e}")

 # 검증: Package 탭 활성 + Package ID 라벨 보임 확인
    for _ in range(12):
        if is_package_search_screen(page):
            _v("[완료] Package 검색 화면 진입")
            remember_package_search_url(page)
            return
        page.wait_for_timeout(300)

 # 마지막 검증: 라벨 텍스트 직접 확인
    try:
        _assert_visible(page,
                        "xpath=//*[contains(normalize-space(),'Package ID & title')]",
                        "Package 검색 라벨", timeout_ms=3000)
        remember_package_search_url(page)
        _v("[완료] Package 검색 화면 진입 (라벨 검증)")
        return
    except Exception as e:
        body = get_body_text(page, 2000)
        raise Exception(
            f"Package 검색 화면 진입을 확인하지 못했습니다. "
            f"DOM 변경 가능성 → _open_sidebar_section / _click_tab 셀렉터 확인. "
            f"현재 화면 일부: {body[:120]}"
        )

def fill_package_id(page, package_id):
    """Package ID & title 입력 ( - .label-box 라벨 anchor).

 DOM 구조: <div class="act-select-box">
 <span class="label-box">Package ID & title:</span>
 <input class="ant-input">
 </div>

 안정성 우선순위:
 1) <span class="label-box">Package ID</span> 텍스트로 라벨 찾고 → 같은 act-select-box 안의 input
 2) act-select-box 텍스트 매칭 (현행)
 3) 첫 번째 visible ant-input (최후)

 Klook 고유 클래스 (.act-select-box, .label-box) 가 변경되면 #2 안내 메시지가 떠서 사용자가
 빨리 어디를 고쳐야 할지 알 수 있음.
    """
    _v(f"[진행] Package ID & title 입력: {package_id}")

 # 1) JS: label-box 텍스트 anchor 기반 ( 1차)
    try:
        ok = bool(page.evaluate("""(pid) => {
 const norm = s => (s || '').replace(/\s+/g,' ').trim();
 // 1차: <span class="label-box"> 안의 텍스트로 라벨 찾기
 let label = Array.from(document.querySelectorAll('.label-box, span.label-box'))
 .find(el => /Package ID/i.test(norm(el.innerText || el.textContent || '')));
 // 1차 실패 시: 모든 span/div 중에서 'Package ID' 포함하고 작은 라벨 같은 요소
 if (!label) {
 label = Array.from(document.querySelectorAll('span, label, div'))
 .find(el => {
 const t = norm(el.innerText || el.textContent || '');
 return t === 'Package ID & title:' || t === 'Package ID & title';
 });
 }
 if (!label) return false;
 // 같은 .act-select-box 안의 input 찾기
 const box = label.closest('.act-select-box') || label.parentElement;
 const input = box ? box.querySelector('input.ant-input, input[type="text"]') : null;
 if (!input) return false;
 input.focus();
 const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
 setter.call(input, '');
 input.dispatchEvent(new Event('input', {bubbles:true}));
 setter.call(input, String(pid));
 input.dispatchEvent(new Event('input', {bubbles:true}));
 input.dispatchEvent(new Event('change', {bubbles:true}));
 return true;
 }""", str(package_id)))
        if ok:
            page.wait_for_timeout(300)
            _v("[완료] Package ID 입력 완료 (label-box anchor)")
            return True
    except Exception as e:
        print(f"[주의] label-box JS 입력 실패: {e}")

 # 2) Playwright locator fallback
    for selector in [
 # label-box 우선
        "xpath=//span[contains(@class,'label-box')][contains(normalize-space(),'Package ID')]/ancestor::div[contains(@class,'act-select-box')][1]//input[contains(@class,'ant-input')]",
 # act-select-box 텍스트
        "xpath=//div[contains(@class,'act-select-box')][contains(., 'Package ID')]//input[contains(@class,'ant-input')]",
        "css=.act-select-box:has-text('Package ID') input.ant-input",
 # 라벨 텍스트 → 인접 input
        "xpath=//*[normalize-space()='Package ID & title:']/following::input[1]",
        "xpath=//*[contains(normalize-space(), 'Package ID & title')]/following::input[1]",
    ]:
        try:
            inp = page.locator(selector).first
            inp.wait_for(state="visible", timeout=2500)
            _click_safe(inp, timeout=2000)
            page.keyboard.press("Control+A")
            page.keyboard.press("Backspace")
            page.keyboard.type(str(package_id), delay=30)
            page.wait_for_timeout(300)
            _v("[완료] Package ID 입력 완료 (locator fallback)")
            return True
        except Exception:
            continue

 # 3) 최후 fallback: 첫 visible ant-input
    print("[주의] Package ID 입력에서 모든 명시 셀렉터 실패. 첫 ant-input 사용. DOM 변경 의심 → .label-box / .act-select-box 확인.")
    try:
        locs = page.locator("input.ant-input")
        for i in range(locs.count()):
            inp = locs.nth(i)
            try:
                if not inp.is_visible():
                    continue
            except Exception:
                continue
            _click_safe(inp, timeout=1500)
            page.keyboard.press("Control+A")
            page.keyboard.press("Backspace")
            page.keyboard.type(str(package_id), delay=30)
            page.wait_for_timeout(300)
            return True
    except Exception:
        pass

    raise Exception(
        "[Package ID 입력] 모든 셀렉터 실패. "
        "DOM 변경 가능성 → fill_package_id() 함수의 .label-box / .act-select-box 셀렉터 확인."
    )

def search_package(page, package_id):
    """Package ID & title에 package_id 입력 후 Search."""
    _v(f"[진행] Package 검색: {package_id}")

    goto_activity_management_package(page)

 # Reset이 있으면 초기화. 없어도 진행.
    try:
        try:
            page.get_by_text("Reset", exact=False).click(timeout=1000)
        except Exception:
            page.get_by_text("Reset", exact=False).click(timeout=800, force=True)
        page.wait_for_timeout(400)
    except Exception:
        pass

    if not fill_package_id(page, package_id):
        raise Exception("Package ID & title 입력칸을 찾지 못했습니다.")

    if not click_search_button(page):
        raise Exception("Package 검색 Search 버튼을 찾지 못했습니다.")

 # 검색 결과 row 또는 href가 생길 때까지 JS로 대기
    try:
        page.wait_for_function(
            """(pid) => {
 const text = (document.body ? document.body.innerText : '').replace(/\s+/g, ' ').trim();
 if (text.includes(String(pid) + ' -')) return true;
 return Array.from(document.querySelectorAll('a[href]')).some(a => {
 const href = a.getAttribute('href') || a.href || '';
 const t = (a.innerText || a.textContent || '').replace(/\s+/g, ' ').trim();
 return href.includes('package_id=' + String(pid)) || t.startsWith(String(pid) + ' -');
 });
 }""",
            arg=str(package_id),
            timeout=9000,
        )
    except Exception:
        page.wait_for_timeout(800)

    body = get_body_text(page, 2500)
    if str(package_id) not in body:
        raise Exception(f"검색 결과에서 Package ID {package_id}를 확인하지 못했습니다.")

    remember_package_search_url(page)
    _v(f"[완료] Package 검색 결과 확인: {package_id}")

def open_package_detail(page, package_id):
    """
 검색 결과의 Package ID 링크 클릭

 핵심:
 - locator.wait_for(visible)를 사용하지 않고 DOM에서 href를 직접 추출합니다.
 - 검색 결과 행 안의 실제 링크가 hidden anchor로 잡혀도 href만 정확하면 직접 page.goto() 합니다.
 - href 직접 이동 실패 시, 화면에 보이는 파란 Package ID 텍스트 좌표를 JS로 찾아 클릭합니다.
    """
    _v(f"[진행] Package 결과 링크 클릭: {package_id}")

    page.wait_for_timeout(700)
    old_url = page.url
    context = page.context
    old_pages = list(context.pages)

    def is_search_page():
        try:
            body = normalize_text(page.locator("body").inner_text(timeout=2500))
            return (
                "Package ID & title" in body
                and "Activity ID & title" in body
                and "Search" in body
                and "Package status" in body
            )
        except Exception:
            return False

    def is_detail_page():
        try:
            body = get_body_text(page, 3500)
            if is_search_page():
                return False
            detail_keywords = [
                "Package info",
                "Package details",
                "Basic Info",
                "Price & inventory",
                "Inventory",
                "Unpublish package",
                "Publish package",
                "Adult",
                "Child",
                "Edit schedule",
            ]
            return any(k in body for k in detail_keywords)
        except Exception:
            return False

    def wait_after_move(timeout_ms=9000):
        deadline = datetime.now().timestamp() + timeout_ms / 1000
        while datetime.now().timestamp() < deadline:
            new_pages = list(context.pages)
            if len(new_pages) > len(old_pages):
                new_page = new_pages[-1]
                try:
                    new_page.bring_to_front()
                except Exception:
                    pass
                try:
                    new_page.wait_for_load_state("domcontentloaded", timeout=6000)
                except Exception:
                    pass
                return new_page
            if page.url != old_url or is_detail_page():
                return page
            page.wait_for_timeout(250)
        return page

    def get_package_result_link_info():
        """locator 없이 DOM에서 검색 결과 Package 링크 정보를 직접 가져옵니다."""
        return page.evaluate(
            """(pid) => {
 function norm(s) { return (s || '').replace(/\s+/g, ' ').trim(); }
 function visibleInfo(el) {
 if (!el) return null;
 const r = el.getBoundingClientRect();
 const st = window.getComputedStyle(el);
 const visible = st.display !== 'none' &&
 st.visibility !== 'hidden' &&
 Number(st.opacity) !== 0 &&
 r.width > 0 && r.height > 0 &&
 r.bottom >= 0 && r.top <= (window.innerHeight || 900) + 300;
 return {visible, x:r.x, y:r.y, width:r.width, height:r.height, area:r.width*r.height};
 }
 function absHref(href) {
 if (!href) return '';
 try { return new URL(href, location.href).href; } catch(e) { return href; }
 }

 const prefix = String(pid) + ' -';

 // 1) 실제 a[href]에서 먼저 href를 수집합니다. hidden anchor라도 href는 사용할 수 있습니다.
 const anchors = Array.from(document.querySelectorAll('a[href]'))
 .map(a => {
 const text = norm(a.innerText || a.textContent || a.getAttribute('title') || '');
 const href = a.getAttribute('href') || a.href || '';
 const v = visibleInfo(a);
 return {
 text,
 href: absHref(href),
 visible: v ? v.visible : false,
 x: v ? v.x : 0,
 y: v ? v.y : 0,
 width: v ? v.width : 0,
 height: v ? v.height : 0,
 area: v ? v.area : 0,
 source: 'anchor'
 };
 })
 .filter(o =>
 (o.text.startsWith(prefix) || (o.href.includes('package_id=' + String(pid)))) &&
 o.href &&
 o.href.includes('package_id=' + String(pid))
 );

 anchors.sort((a,b) => {
 if (a.visible !== b.visible) return a.visible ? -1 : 1;
 if (a.y !== b.y) return a.y - b.y;
 return a.area - b.area;
 });
 if (anchors.length) return anchors[0];

 // 2) a가 없거나 href가 없으면 화면에 보이는 파란 텍스트 좌표를 찾습니다.
 const nodes = Array.from(document.querySelectorAll('td, span, div, button, [role="link"], [role="button"]'))
 .map(el => {
 const text = norm(el.innerText || el.textContent || '');
 const v = visibleInfo(el);
 const clickable = el.closest('a') || el.closest('[role="link"]') || el.closest('button') || el.closest('[role="button"]') || el;
 const cv = visibleInfo(clickable);
 const href = clickable ? (clickable.getAttribute('href') || clickable.href || '') : '';
 return {
 text,
 href: absHref(href),
 visible: v ? v.visible : false,
 x: v ? v.x : 0,
 y: v ? v.y : 0,
 width: v ? v.width : 0,
 height: v ? v.height : 0,
 area: v ? v.area : 999999,
 cx: v ? v.x + Math.min(60, Math.max(18, v.width * 0.22)) : 0,
 cy: v ? v.y + v.height / 2 : 0,
 clickableX: cv ? cv.x : 0,
 clickableY: cv ? cv.y : 0,
 source: 'visibleText'
 };
 })
 .filter(o =>
 o.visible &&
 o.text.startsWith(prefix) &&
 o.y >= 230 &&
 o.width >= 20 &&
 o.height >= 12
 );

 nodes.sort((a,b) => a.area - b.area || a.y - b.y || a.x - b.x);
 return nodes[0] || null;
 }""",
            str(package_id),
        )

    info = get_package_result_link_info()

    # ⚠️ 검색 결과 줄이 <a> 로 감싸이기 전에 먼저 보면 href 가 없다.
    #
    #    그러면 아래 'href 로 바로 이동'(제일 확실한 길)을 통째로 건너뛰고,
    #    JS 클릭 -> //a[...] 셀렉터 3종 순으로 내려가는데 그 셀렉터들은 전부
    #    <a> 를 찾으므로 다 같이 실패한다. 결국 '상세 진입 실패' 로 끝난다.
    #    (2026-09-02: Shirakawago Regular 656584 가 이렇게 안 열렸다.
    #     그 뒤 같은 화면을 재보니 <a> 와 href 가 멀쩡했고 상세는 2.4초에 떴다)
    #
    #    href 가 없으면 몇 초 더 보고 <a> 가 생기는지 기다린다.
    if info and not (info.get("href") or ""):
        for _ in range(12):                     # 최대 6초
            page.wait_for_timeout(500)
            again = get_package_result_link_info()
            if again and (again.get("href") or ""):
                _v("[진행] <a> 가 늦게 떠서 다시 잡음 (href 확보)")
                info = again
                break
        else:
            print(f"[주의] '{package_id} -' 줄에 href 가 없습니다 "
                  f"(source={info.get('source')}). 링크가 아직 안 그려졌을 수 있습니다.")

    if not info:
        raise Exception(f"검색 결과에서 '{package_id} -' Package 링크/href를 찾지 못했습니다.")

    _v(f"[진행] Package 링크 후보: {info.get('text', '')[:120]} / source={info.get('source')} / href={info.get('href', '')[:80]}")

    href = info.get("href") or ""
    if href:
        try:
            _v("[진행] Package href 직접 이동")
            page.goto(href, wait_until="domcontentloaded", timeout=15000)
            page.wait_for_timeout(1200)
            if is_detail_page() or page.url != old_url:
                _v("[완료] Package 상세 화면 진입")
                return
        except Exception as e:
            print(f"[주의] href 직접 이동 실패 → 좌표 클릭으로 재시도: {e}")

 #: href가 없거나 직접 이동이 실패한 경우, anchor 요소를 JS로 직접 click().
 # 기존 좌표 클릭(page.mouse.click) 제거 → 셀렉터/요소 기반 클릭으로 통일.
    try:
        _v("[진행] Package 링크 JS click() 직접 호출")
        clicked = bool(page.evaluate(
            """(pid) => {
 function norm(s){ return (s || '').replace(/\\s+/g, ' ').trim(); }
 const prefix = String(pid) + ' -';
 // anchor 우선
 let target = Array.from(document.querySelectorAll('a[href]'))
 .find(a => {
 const t = norm(a.innerText || a.textContent || '');
 const h = a.getAttribute('href') || '';
 return t.startsWith(prefix) || h.includes('package_id=' + String(pid));
 });
 // visible 텍스트 요소도 fallback
 if (!target) {
 target = Array.from(document.querySelectorAll('td, span, div, button, [role="link"], [role="button"]'))
 .find(el => norm(el.innerText || el.textContent || '').startsWith(prefix));
 }
 if (!target) return false;
 const clickable = target.closest('a') || target.closest('[role="link"]') || target.closest('button') || target.closest('[role="button"]') || target;
 try { clickable.scrollIntoView({block:'center'}); } catch(e) {}
 ['mouseover','mousedown','mouseup','click'].forEach(type => {
 clickable.dispatchEvent(new MouseEvent(type, {bubbles:true, cancelable:true, view:window}));
 });
 try { clickable.click(); } catch(e) {}
 return true;
 }""",
            str(package_id),
        ))
        if clicked:
            new_page = wait_after_move(timeout_ms=7000)
            if new_page is not page:
                page = new_page
            if is_detail_page() or page.url != old_url:
                _v("[완료] Package 상세 화면 진입")
                return
    except Exception as e:
        print(f"[주의] JS click() 실패: {e}")

 # Playwright locator fallback
    for selector in [
        f"xpath=//a[starts-with(normalize-space(), '{package_id} -')]",
        f"xpath=//a[contains(@href, 'package_id={package_id}')]",
        f"text=/^{package_id} -/",
    ]:
        try:
            loc = page.locator(selector).first
            loc.wait_for(state="visible", timeout=2500)
            _click_safe(loc, timeout=2500)
            new_page = wait_after_move(timeout_ms=7000)
            if new_page is not page:
                page = new_page
            if is_detail_page() or page.url != old_url:
                _v("[완료] Package 상세 화면 진입 (locator)")
                return
        except Exception:
            continue

    # ⚠️ 왜 못 갔는지 남긴다. 지금까지는 이 문장 하나뿐이라 며칠을 헤맸다.
    #    href 가 있었는지, 어디로 갔는지, 화면에 뭐가 있는지가 있어야
    #    'Klook 이 튕겨냈다' 와 '아직 안 그려졌다' 를 가를 수 있다.
    try:
        now = page.url
        body = (get_body_text(page, 2000) or "").replace("\n", " ")[:200]
        bounced = "act/management" in now or "activity/list" in now
        print(f"[진단] 상세 진입 실패 {package_id} | href={(href or '(없음)')[:80]}")
        print(f"[진단]   처음 URL={old_url[:80]}")
        print(f"[진단]   지금 URL={now[:80]}{'  <- 목록으로 튕김' if bounced else ''}")
        print(f"[진단]   검색화면인가={is_search_page()} 상세인가={is_detail_page()}")
        print(f"[진단]   화면글자={body}")
    except Exception as diag_error:
        print(f"[진단] 상태를 읽지 못함: {diag_error}")

    raise Exception(f"'{package_id} -' Package 링크 클릭 후에도 상세 화면으로 이동하지 못했습니다.")

def dismiss_unsaved_changes_dialog(page, max_tries: int = 2) -> bool:
    """'If you leave now, you'll lose any unsaved changes' 모달이 떠 있으면
    'Exit without saving' 버튼을 자동 클릭해서 다음 단계로 진행하게 함.

    Adult/Person 클릭 또는 See schedule 클릭 직후 가끔 이 confirm modal 이 떠서
    캘린더 화면으로 못 넘어가는 케이스 처리.

    - 반환: 다이얼로그를 발견하고 닫았으면 True, 다이얼로그 없으면 False
    - 다이얼로그 없을 때는 빠르게 (~100ms) 반환하여 메인 흐름 영향 최소화
    """
    for _ in range(max(1, max_tries)):
        try:
            result = page.evaluate(
                """() => {
 function norm(s){return (s||'').replace(/\\s+/g,' ').trim();}
 function visible(el){
 if(!el) return false;
 const r = el.getBoundingClientRect();
 const st = getComputedStyle(el);
 return st.display !== 'none' && st.visibility !== 'hidden' && Number(st.opacity) !== 0 &&
 r.width > 0 && r.height > 0;
 }
 // 1) 모달 컨테이너 후보: ant-modal / ant-modal-confirm / [role=dialog]
 const modals = Array.from(document.querySelectorAll(
 '.ant-modal, .ant-modal-confirm, .ant-modal-confirm-confirm, [role="dialog"]'
 )).filter(visible);
 for (const modal of modals) {
 const txt = norm(modal.innerText || modal.textContent || '');
 const looksLikeUnsavedDialog =
 /leave now/i.test(txt) ||
 /unsaved changes/i.test(txt) ||
 /Exit without saving/i.test(txt);
 if (!looksLikeUnsavedDialog) continue;
 // 'Exit without saving' 버튼 찾기
 const buttons = Array.from(modal.querySelectorAll('button,[role="button"]')).filter(visible);
 let target = buttons.find(b => /Exit without saving/i.test(norm(b.innerText || b.textContent || '')));
 if (!target) {
 // 텍스트만 일치하는 span/div 도 시도 (button 안의 span 클릭해도 작동)
 const inner = Array.from(modal.querySelectorAll('span,div'))
 .find(el => visible(el) && norm(el.innerText || el.textContent || '') === 'Exit without saving');
 if (inner) target = inner.closest('button') || inner;
 }
 if (target) {
 try { target.scrollIntoView({block:'center'}); } catch(e) {}
 target.click();
 return {found: true, text: norm(target.innerText || target.textContent || '').slice(0, 40)};
 }
 return {found: true, clicked: false, reason: 'no Exit-without-saving button in modal'};
 }
 return {found: false};
 }"""
            )
        except Exception:
            return False
        if not result or not result.get('found'):
            return False
        if result.get('clicked') is False:
            return False
        # 다이얼로그 닫힘 대기
        try:
            page.wait_for_timeout(450)
        except Exception:
            pass
        _v(f"[진행] 'Exit without saving' 자동 클릭 (unsaved-changes 다이얼로그 닫음)")
        # 한 번 더 떠 있을 수 있어서 재시도
    return True


def click_adult_option(page):
    """
 Adult 옵션 클릭.
 - 기본은 Adult 옵션을 우선 선택합니다.
 - Adult 옵션이 보이지 않거나 클릭 후 Price & inventory 영역으로 진입하지 못하면,
 Person 옵션을 Adult와 동일한 옵션으로 보고 선택합니다.
    """
    _v("[진행] Adult/Person 옵션 클릭")
    # 이전 화면에서 남은 'unsaved changes' 다이얼로그가 있으면 먼저 닫기
    try:
        dismiss_unsaved_changes_dialog(page)
    except Exception:
        pass

    def section_ready(timeout_ms=1200) -> bool:
        try:
            body = get_body_text(page, timeout_ms)
            return (
                "Price & inventory" in body
                or "Departure confirmed" in body
                or ("Inventory" in body and "Accept bookings until" in body)
            )
        except Exception:
            return False

 # 이미 Price & inventory가 보이면 옵션 클릭 생략
    if section_ready(1500):
        _v("[안내] 이미 Price & inventory 영역으로 확인되어 옵션 클릭 생략")
        return

    def click_option_by_index(label: str, index: int) -> bool:
        """JS 안에서 후보를 찾아 N번째 요소를 직접 클릭. 좌표 클릭 제거."""
        try:
            return bool(page.evaluate(
                """([label, idx]) => {
 function norm(s) { return (s || '').replace(/\\s+/g, ' ').trim(); }
 function visible(el) {
 const r = el.getBoundingClientRect();
 const st = window.getComputedStyle(el);
 return st.display !== 'none' && st.visibility !== 'hidden' && Number(st.opacity) !== 0 &&
 r.width > 0 && r.height > 0 && r.bottom >= 0 && r.top <= (window.innerHeight || 900) + 300;
 }
 const nodes = Array.from(document.querySelectorAll('a,button,div,span,[role="button"],[role="tab"]'))
 .filter(el => visible(el) && norm(el.innerText || el.textContent || '') === label)
 .filter(el => {
 const r = el.getBoundingClientRect();
 return r.width >= 20 && r.height >= 15;
 })
 .sort((a,b) => {
 const ar = a.getBoundingClientRect();
 const br = b.getBoundingClientRect();
 const as = (ar.y > 120 && ar.y < 700 ? 0 : 50) + (ar.x < 900 ? 0 : 20) + ar.y / 1000;
 const bs = (br.y > 120 && br.y < 700 ? 0 : 50) + (br.x < 900 ? 0 : 20) + br.y / 1000;
 return as - bs;
 });
 if (idx >= nodes.length) return false;
 const el = nodes[idx];
 const target = el.closest('a') || el.closest('button') || el.closest('[role="button"]') || el.closest('[role="tab"]') || el;
 try { target.scrollIntoView({block:'center'}); } catch(e) {}
 target.click();
 return true;
 }""",
                [label, index],
            ))
        except Exception:
            return False

    def count_option_candidates(label: str) -> int:
        try:
            return int(page.evaluate(
                """(label) => {
 function norm(s) { return (s || '').replace(/\\s+/g, ' ').trim(); }
 function visible(el) {
 const r = el.getBoundingClientRect();
 const st = window.getComputedStyle(el);
 return st.display !== 'none' && st.visibility !== 'hidden' && Number(st.opacity) !== 0 &&
 r.width > 0 && r.height > 0;
 }
 return Array.from(document.querySelectorAll('a,button,div,span,[role="button"],[role="tab"]'))
 .filter(el => visible(el) && norm(el.innerText || el.textContent || '') === label)
 .filter(el => {
 const r = el.getBoundingClientRect();
 return r.width >= 20 && r.height >= 15;
 }).length;
 }""",
                label,
            ) or 0)
        except Exception:
            return 0

    def wait_option_candidates(label: str, timeout_ms: int = 8000) -> int:
        """
        옵션이 화면에 나타날 때까지 기다린다.

        검색 결과 줄은 누르면 '펼쳐지는' 패널이다. 펼쳐지기 전에 세면 0 이
        나오는데, 예전에는 한 번만 세고 넘어가 그대로 실패했다.
        (2026-08-30: 일본 3건이 이것 때문에 실패)
        """
        waited = 0
        while waited < timeout_ms:
            n = count_option_candidates(label)
            if n > 0:
                if waited:
                    _v(f"[안내] {label} 옵션이 {waited}ms 뒤에 나타남")
                return n
            page.wait_for_timeout(400)
            waited += 400
        return 0

    def expand_package_panel() -> bool:
        """접힌 패널을 펼친다. 옵션은 그 안에 있다."""
        try:
            return bool(page.evaluate(
                """() => {
 const item = document.querySelector(
 '.ant-collapse-item:not(.ant-collapse-item-active) .ant-collapse-header');
 if (!item) return false;
 item.click();
 return true;
 }"""))
        except Exception:
            return False

    def try_click_option(label: str) -> bool:
        _v(f"[진행] {label} 옵션 탐색")

        total = wait_option_candidates(label)
        if total == 0 and expand_package_panel():
            _v(f"[진행] {label} 옵션이 안 보여 패널을 펼침")
            page.wait_for_timeout(800)
            total = wait_option_candidates(label, timeout_ms=6000)
        if total > 0:
            for idx in range(min(6, total)):
                try:
                    _v(f"[진행] {label} 후보 클릭 {idx+1}/{min(6,total)} (JS click)")
                    if not click_option_by_index(label, idx):
                        continue
                    page.wait_for_timeout(1200)
                    # 클릭 직후 'unsaved changes' 다이얼로그 떠 있으면 닫기
                    try:
                        dismiss_unsaved_changes_dialog(page)
                    except Exception:
                        pass

 # 클릭 직후 섹션이 늦게 뜨는 경우를 대비해 짧게 확인
                    for _ in range(8):
                        if section_ready(1000):
                            _v(f"[완료] {label} 옵션 클릭")
                            return True
                        page.wait_for_timeout(300)
                except Exception:
                    continue
        else:
            _v(f"[안내] {label} 옵션 후보 없음")

 # locator fallback
        for selector in [f"xpath=//*[normalize-space()='{label}']", f"text={label}"]:
            try:
                loc = page.locator(selector).first
                loc.wait_for(state="visible", timeout=2500)
                loc.scroll_into_view_if_needed(timeout=1500)
                loc.click(timeout=3000, force=True)
                page.wait_for_timeout(1500)
                # 클릭 직후 'unsaved changes' 다이얼로그 떠 있으면 닫기
                try:
                    dismiss_unsaved_changes_dialog(page)
                except Exception:
                    pass

                for _ in range(6):
                    if section_ready(1000):
                        _v(f"[완료] {label} 옵션 클릭")
                        return True
                    page.wait_for_timeout(300)
            except Exception:
                continue

        return False

 # 기본은 Adult 우선, Adult가 없거나 실패하면 Person을 Adult와 동일하게 처리
    if try_click_option("Adult"):
        return

    _v("[안내] Adult 옵션을 선택하지 못했습니다 → Person 옵션으로 재시도")
    if try_click_option("Person"):
        return

    # 여기까지 왔으면 화면이 예상과 다르다. 다음에 또 나면 바로 알 수 있게
    # '그때 무엇이 보였는지' 를 남긴다. 메시지만 남기면 재현할 수가 없다.
    hint = ""
    try:
        info = page.evaluate(
            """() => {
 const norm = s => (s||'').replace(/\s+/g,' ').trim();
 const seen = [];
 for (const el of document.querySelectorAll('div,span,button,a')) {
 const t = norm(el.innerText || '');
 if (!t || t.length > 30) continue;
 const r = el.getBoundingClientRect();
 if (r.width < 20 || r.height < 15) continue;
 seen.push(t);
 }
 return {url: location.href,
 texts: [...new Set(seen)].slice(0, 25),
 panels: document.querySelectorAll('.ant-collapse-item').length,
 open: document.querySelectorAll('.ant-collapse-item-active').length};
 }""")
        hint = (f" | URL={str(info.get('url'))[:110]}"
                f" | 패널 {info.get('panels')}개(펼침 {info.get('open')})"
                f" | 화면글자={info.get('texts')}")
    except Exception:
        pass
    raise Exception("Adult 또는 Person 옵션을 찾지 못했습니다." + hint)


def ensure_price_inventory_section(page):
    """
 Price & inventory 섹션 확인.
 단순 mouse wheel로 안 내려가는 Klook 상세 화면에 대비해서
 window와 모든 scrollable container를 직접 스크롤합니다.
    """
    _v("[진행] Price & inventory 섹션 확인")

    def find_and_scroll_to_section():
        try:
            return page.evaluate(
                """() => {
 function norm(s) { return (s || '').replace(/\s+/g, ' ').trim(); }
 function visible(el) {
 const r = el.getBoundingClientRect();
 const st = window.getComputedStyle(el);
 return st.display !== 'none' && st.visibility !== 'hidden' && Number(st.opacity) !== 0 &&
 r.width > 0 && r.height > 0;
 }
 const nodes = Array.from(document.querySelectorAll('h1,h2,h3,h4,div,span,section'))
 .map(el => ({el, text:norm(el.innerText || el.textContent || ''), r:el.getBoundingClientRect()}))
 .filter(o => o.text.includes('Price & inventory'))
 .sort((a,b) => a.r.y - b.r.y);
 if (nodes.length) {
 const el = nodes[0].el;
 try { el.scrollIntoView({block:'center', inline:'nearest'}); } catch(e) {}
 const r = el.getBoundingClientRect();
 return {found:true, visible:visible(el), x:r.x, y:r.y, text:nodes[0].text.slice(0,120)};
 }
 return {found:false};
 }"""
            )
        except Exception:
            return {"found": False}

 # 1) 현재 DOM에 섹션 텍스트가 있으면 즉시 이동
    for _ in range(3):
        info = find_and_scroll_to_section()
        if info and info.get("found"):
            _v(f"[완료] Price & inventory 섹션 확인: x={int(info.get('x',0))}, y={int(info.get('y',0))}")
            page.wait_for_timeout(600)
            return
        page.wait_for_timeout(400)

 # 2) scrollable container 전체를 조금씩 내려가며 찾기
    for attempt in range(1, 18):
        try:
            page.evaluate(
                """(attempt) => {
 const amount = 650;
 try { window.scrollBy(0, amount); } catch(e) {}
 try { document.scrollingElement && (document.scrollingElement.scrollTop += amount); } catch(e) {}
 const els = Array.from(document.querySelectorAll('div,main,section,aside'));
 for (const el of els) {
 try {
 if (el.scrollHeight > el.clientHeight + 80) {
 el.scrollTop += amount;
 }
 } catch(e) {}
 }
 }""",
                attempt,
            )
        except Exception:
            pass

        try:
            page.mouse.wheel(0, 700)
        except Exception:
            pass
        page.wait_for_timeout(450)

        info = find_and_scroll_to_section()
        if info and info.get("found"):
            _v(f"[완료] Price & inventory 섹션 확인: x={int(info.get('x',0))}, y={int(info.get('y',0))}")
            page.wait_for_timeout(600)
            return

 # 일부 화면에서는 섹션명 없이 바로 날짜 카드만 보일 수 있음
        body = get_body_text(page, 1200)
        if "Departure confirmed" in body and ("Inventory" in body or "KRW" in body or "₩" in body):
            _v("[완료] Price & inventory 섹션명은 없지만 날짜 카드 영역 확인")
            return

    raise Exception("Price & inventory 섹션을 찾지 못했습니다.")


def ensure_package_calendar_target_month(page):
    """Package 캘린더가 목표 월(익일 기준)을 보고 있도록 다음 월 버튼 클릭.

    월말/월초 전환 시 (예: 5/31 -> 6/1, 또는 오늘 6/2 인데 캘린더는 5월 표시) 익일 카드가
    화면에 없어서 'Price & inventory 영역에서 익일 날짜 카드 X를 찾지 못했습니다' 가 떨어지는
    문제 해결.
    """
    target = tomorrow_date_obj()
    month_label = target.strftime('%B %Y')          # "June 2026"
    short_label = target.strftime('%b %Y')           # "Jun 2026"
    year_month_num = target.strftime('%Y-%m')        # "2026-06"
    _v(f"[진행] Package 캘린더 목표 월 확인: {month_label}")

    def has_target_month():
        try:
            return bool(page.evaluate(
                """([full, short, ym]) => {
 const t = (document.body ? document.body.innerText : '');
 return t.indexOf(full) !== -1 || t.indexOf(short) !== -1 || t.indexOf(ym) !== -1;
 }""",
                [month_label, short_label, year_month_num]
            ))
        except Exception:
            body = get_body_text(page, 1200)
            return month_label in body or short_label in body or year_month_num in body

    if has_target_month():
        _v("[완료] Package 캘린더 목표 월 표시 확인")
        return

    for attempt in range(1, 8):
        clicked = None
        try:
            clicked = page.evaluate(
                """() => {
 function norm(s){return (s||'').replace(/\\s+/g,' ').trim();}
 function visible(el){
 const r = el.getBoundingClientRect();
 const st = getComputedStyle(el);
 return st.display !== 'none' && st.visibility !== 'hidden' && Number(st.opacity) !== 0 &&
 r.width>0 && r.height>0 && r.bottom>=0 && r.top <= (innerHeight||900);
 }
 // 1순위: aria-label / title 에 next 명시
 const aria = Array.from(document.querySelectorAll('[aria-label],[title]'))
 .filter(el => visible(el) && /next/i.test((el.getAttribute('aria-label')||el.getAttribute('title')||'')));
 if (aria.length) {
 // 캘린더 헤더 근처(상단 절반) 우선
 aria.sort((a,b) => a.getBoundingClientRect().y - b.getBoundingClientRect().y);
 for (const el of aria) {
 const r = el.getBoundingClientRect();
 if (r.y < 350) { el.click(); return {text: norm(el.innerText||el.textContent||'').slice(0,20) || 'next-aria'}; }
 }
 aria[0].click();
 return {text: 'next-aria(fallback)'};
 }
 // 2순위: 텍스트 chevron (›, >, »)
 const nodes = Array.from(document.querySelectorAll('button,a,span,div,i,[role="button"]'))
 .map(el => { const r=el.getBoundingClientRect(); return {el, text:norm(el.innerText||el.textContent||''), r}; })
 .filter(o => visible(o.el) && (o.text === '›' || o.text === '>' || o.text === '»') && o.r.y >= 100 && o.r.y < 600)
 .sort((a,b) => a.r.y - b.r.y || b.r.x - a.r.x);  // 같은 y 면 오른쪽(next) 우선
 if (nodes.length) { nodes[0].el.click(); return {text:nodes[0].text}; }
 return null;
 }"""
            )
        except Exception:
            clicked = None
        if not clicked:
            break
        _v(f"[진행] Package 캘린더 다음 월 버튼 클릭 #{attempt}: {clicked.get('text','')}")
        page.wait_for_timeout(900)
        if has_target_month():
            _v("[완료] Package 캘린더 목표 월 이동 완료")
            return
    print(f"[주의] Package 캘린더 목표 월({month_label}) 표시 확인 실패. 현재 보이는 캘린더로 계속 진행합니다.")


def open_tomorrow_edit_schedule(page):
    """
 익일 날짜 카드 선택 후 Edit schedule 팝업 열기.
 - x좌표가 화면 밖으로 잡히는 가로 스크롤 문제를 강제 보정합니다.
 - 날짜 카드 하단 클릭 후 나타나는 Edit 버튼을 클릭합니다.
    """
    target_day = tomorrow_day_number()
    _v(f"[진행] 익일 날짜 선택: {tomorrow_iso()} / day={target_day}")
    ensure_price_inventory_section(page)
    # 월 경계 전환 (5/31 -> 6/1, 또는 오늘이 6/2 인데 캘린더는 5월) 자동 처리
    try:
        ensure_package_calendar_target_month(page)
    except Exception as e:
        print(f"[주의] Package 캘린더 월 이동 실패 (계속 진행): {e}")

    def popup_is_open() -> bool:
        try:
            return bool(page.evaluate(
                """() => {
 function norm(s) { return (s || '').replace(/\s+/g, ' ').trim(); }
 const text = norm(document.body ? (document.body.innerText || document.body.textContent || '') : '');
 if (text.includes('Edit schedule')) return true;
 return text.includes('Inventory') && text.includes('Accept bookings until') && text.includes('Activate') && text.includes('Confirm');
 }"""
            ))
        except Exception:
            return False

    def wait_popup(timeout_ms=5000):
        deadline = datetime.now().timestamp() + timeout_ms / 1000
        while datetime.now().timestamp() < deadline:
            if popup_is_open():
                _v("[완료] Edit schedule 팝업 열림")
                return True
            page.wait_for_timeout(220)
        return False

    if popup_is_open():
        _v("[안내] 이미 Edit schedule 팝업이 열려 있습니다.")
        return

    def center_target_day_card():
        """익일 날짜 카드/컬럼을 화면 중앙으로 가져오고 카드 좌표를 반환합니다."""
        try:
            return page.evaluate(
                """(day) => {
 function norm(s) { return (s || '').replace(/\s+/g, ' ').trim(); }
 function isVisibleY(el) {
 if (!el) return false;
 const r = el.getBoundingClientRect();
 const st = window.getComputedStyle(el);
 return st.display !== 'none' && st.visibility !== 'hidden' && Number(st.opacity) !== 0 &&
 r.width > 0 && r.height > 0 && r.bottom >= 0 && r.top <= (window.innerHeight || 900) + 900;
 }
 function rect(el) { const r = el.getBoundingClientRect(); return {x:r.x,y:r.y,width:r.width,height:r.height}; }
 function scoreCard(el, dayEl) {
 const t = norm(el.innerText || el.textContent || '');
 const r = rect(el);
 let s = 0;
 if (t.includes(String(day))) s += 10;
 if (new RegExp('(^|\\s)' + String(day) + '(\\s|$)').test(t)) s += 10;
 if (new RegExp('(^|\\s)' + String(day).padStart(2, '0') + '(\\s|$)').test(t)) s += 10;
 if (t.includes('Departure confirmed')) s += 35;
 if (t.includes('Edit')) s += 20;
 if (t.includes('₩') || t.includes('KRW')) s += 7;
 if (/\d+\s*\/\s*\d+/.test(t)) s += 7;
 if (r.width >= 100 && r.width <= 430) s += 10;
 if (r.height >= 55 && r.height <= 330) s += 10;
 if (r.y > 180) s += 5;
 if (r.width > 800 || r.height > 650) s -= 60;
 if (dayEl) {
 const dr = rect(dayEl);
 if (dr.x >= r.x - 5 && dr.x <= r.x + r.width + 5 && dr.y >= r.y - 5 && dr.y <= r.y + r.height + 5) s += 10;
 }
 return s;
 }

 const dayNodes = Array.from(document.querySelectorAll('div,span,td,th,button'))
 .filter(el => isVisibleY(el) && (() => { const _t = norm(el.innerText || el.textContent || ''); return _t === String(day) || _t === String(day).padStart(2, '0') || (!isNaN(parseInt(_t, 10)) && parseInt(_t, 10) === parseInt(day, 10) && _t.length <= 3); })() && el.getBoundingClientRect().y > 180)
 .sort((a,b) => a.getBoundingClientRect().y - b.getBoundingClientRect().y || a.getBoundingClientRect().x - b.getBoundingClientRect().x);

 if (!dayNodes.length) return null;

 const candidates = [];
 for (const dayEl of dayNodes) {
 let cur = dayEl;
 for (let depth = 0; depth < 12 && cur; depth++, cur = cur.parentElement) {
 if (!isVisibleY(cur)) continue;
 const r = rect(cur);
 if (r.width < 60 || r.height < 35) continue;
 const sc = scoreCard(cur, dayEl);
 if (sc >= 28) {
 candidates.push({el:cur, dayEl, score:sc, text:norm(cur.innerText || cur.textContent || '').slice(0,220)});
 }
 }
 }
 if (!candidates.length) return null;
 candidates.sort((a,b) => b.score - a.score || a.el.getBoundingClientRect().y - b.el.getBoundingClientRect().y);
 const best = candidates[0];
 const card = best.el;

 // 가로 스크롤 가능한 모든 조상/컨테이너를 대상으로 중앙 정렬
 try { card.scrollIntoView({block:'center', inline:'center'}); } catch(e) {}
 for (let pass = 0; pass < 4; pass++) {
 let r = card.getBoundingClientRect();
 const viewportCenter = (window.innerWidth || 1920) / 2;
 const delta = (r.left + r.width / 2) - viewportCenter;

 let p = card.parentElement;
 while (p) {
 try {
 if (p.scrollWidth > p.clientWidth + 20) {
 p.scrollLeft += delta;
 }
 } catch(e) {}
 p = p.parentElement;
 }

 // 페이지 내 전체 horizontal scroll container도 보정
 const all = Array.from(document.querySelectorAll('div,main,section,table'));
 for (const el of all) {
 try {
 if (el.scrollWidth > el.clientWidth + 20) {
 const er = el.getBoundingClientRect();
 if (er.bottom >= 150 && er.top <= (window.innerHeight || 900) + 200) {
 el.scrollLeft += delta;
 }
 }
 } catch(e) {}
 }
 }

 const r = card.getBoundingClientRect();
 const dr = best.dayEl.getBoundingClientRect();
 return {
 x: r.x + r.width/2,
 y: r.y + r.height/2,
 contentX: Math.max(20, Math.min((window.innerWidth || 1920) - 20, r.x + r.width/2)),
 contentY: Math.max(20, Math.min((window.innerHeight || 900) - 20, Math.min(r.y + r.height - 35, dr.y + 70))),
 bottomX: Math.max(20, Math.min((window.innerWidth || 1920) - 20, r.x + r.width/2)),
 bottomY: Math.max(20, Math.min((window.innerHeight || 900) - 20, r.y + r.height - 28)),
 dayX: Math.max(20, Math.min((window.innerWidth || 1920) - 20, dr.x + dr.width/2)),
 dayY: Math.max(20, Math.min((window.innerHeight || 900) - 20, dr.y + dr.height/2)),
 cardX: r.x, cardY: r.y, cardW: r.width, cardH: r.height,
 text: best.text,
 score: best.score
 };
 }""",
                str(target_day),
            )
        except Exception:
            return None

    def click_edit_if_visible(card=None) -> bool:
        """날짜 카드 안의 Edit 아이콘(.anticon-edit) 직접 클릭 ().

 Klook 캘린더 DOM 구조 (2026-05 확인):
 <div class="calendar-table-item">
 <div class="calendar-table-item-title">
 <div class="calendar-table-item-title-panel">21</div>
 </div>
 <div class="calendar-table-slot-panel is-can-edit">
 ...
 <div class="hover-box"><div class="hover-box__content">
 <i class="anticon anticon-edit">...</i> ← 클릭 타겟
 </div></div>
 </div>
 </div>

 - "Edit" 텍스트 leaf 요소는 존재하지 않음 (아이콘만 있음).
 - .hover-box 안의 아이콘은 CSS :hover 로만 보이지만 JS .click() 으로
 직접 호출하면 visible 여부와 무관하게 핸들러가 동작함.
        """
        try:
            target_day_str = str(target_day)
            target_iso_str = tomorrow_iso()
            clicked = bool(page.evaluate(
                """([day, iso, card]) => {
 function norm(s) { return (s || '').replace(/\\s+/g, ' ').trim(); }
 function isOutdate(item) {
 const sp = item.querySelector('.calendar-table-slot-panel');
 if (!sp) return false;
 const cls = (sp.className || '').toString();
 return /is-outdate/.test(cls) || /is-disabled/.test(cls);
 }
 function isCanEdit(item) {
 const sp = item.querySelector('.calendar-table-slot-panel');
 return !!(sp && /is-can-edit/.test((sp.className || '').toString()));
 }

 // 1) 우선순위 1: data-slot-starttime^="YYYY-MM-DD" 으로 정확한 날짜 매칭
 //    (캘린더가 2개월을 동시 표시할 때 04-26 vs 05-26 같은 day-only 중복 회피)
 let targetCard = null;
 if (iso) {
 const panel = document.querySelector(
 '.calendar-table-slot-panel[data-slot-starttime^="' + iso + '"], ' +
 '.calendar-table-item-slot[data-slot-starttime^="' + iso + '"], ' +
 '[data-slot-starttime^="' + iso + '"]'
 );
 if (panel) {
 targetCard = panel.closest('.calendar-table-item') || panel.parentElement;
 }
 }

 // 2) 폴백: day 텍스트 매칭. is-can-edit 우선, is-outdate/is-disabled 마지막
 if (!targetCard) {
 const items = Array.from(document.querySelectorAll('.calendar-table-item'));
 const matched = items.filter(item => {
 const title = item.querySelector('.calendar-table-item-title');
 if (!title) return false; const _t = norm(title.innerText || title.textContent || ''); return _t === String(day) || _t === String(day).padStart(2, '0') || (!isNaN(parseInt(_t, 10)) && parseInt(_t, 10) === parseInt(day, 10) && _t.length <= 3);
 });
 // 정렬: is-can-edit 먼저 → 일반 → is-outdate/is-disabled 마지막
 matched.sort((a, b) => {
 const aScore = (isCanEdit(a) ? 0 : (isOutdate(a) ? 2 : 1));
 const bScore = (isCanEdit(b) ? 0 : (isOutdate(b) ? 2 : 1));
 return aScore - bScore;
 });
 if (matched.length) targetCard = matched[0];
 }

 // 3) card 좌표 hint 폴백 (보강)
 const items = Array.from(document.querySelectorAll('.calendar-table-item'));
 if (!targetCard && card && card.cardX !== undefined) {
 let best = null, bestDist = Infinity;
 for (const item of items) {
 if (isOutdate(item)) continue; // 지난 날짜 제외
 const r = item.getBoundingClientRect();
 const d = Math.abs(r.x - card.cardX) + Math.abs(r.y - card.cardY);
 if (d < bestDist) { bestDist = d; best = item; }
 }
 if (best && bestDist < 50) targetCard = best;
 }

 if (!targetCard) return {ok:false, reason:'no .calendar-table-item for day ' + day + ' (iso=' + iso + ')'};

 // 4) .anticon-edit 찾기 (hover-box 안에 있음, 또는 카드 어디든)
 let editIcon = targetCard.querySelector('.anticon-edit, .anticon.anticon-edit, [class*="anticon-edit"]');

 // 5) 못 찾으면 text-based Edit 시도 (이전 버전 UI 호환)
 if (!editIcon) {
 editIcon = Array.from(targetCard.querySelectorAll('a,button,span,div,i,[role="button"]'))
 .find(el => norm(el.innerText || el.textContent || '') === 'Edit');
 }

 if (!editIcon) return {ok:false, reason:'no .anticon-edit in card ' + day};

 // 6) is-can-edit 클래스 확인 (편집 가능 여부 — Klook DOM 힌트)
 const slotPanel = targetCard.querySelector('.calendar-table-slot-panel');
 const canEdit = slotPanel && /is-can-edit/.test(slotPanel.className || '');
 const slotDs = slotPanel ? (slotPanel.getAttribute('data-slot-starttime') || '') : '';

 // 7) 카드 스크롤 + 클릭
 try { targetCard.scrollIntoView({block:'center', inline:'center'}); } catch(e) {}
 // hover-box 가 CSS :hover 로만 보이지만, JS click 은 visible 무관하게 동작
 editIcon.click();
 return {ok:true, canEdit, iconCls: (editIcon.className || '').toString(), ds: slotDs};
 }""",
                [target_day_str, target_iso_str, card],
            ))
            if not clicked:
                return False
            _v(f"[진행] 날짜 카드 안 .anticon-edit 클릭 ()")
        except Exception as e:
            print(f"[주의] .anticon-edit click 실패: {e}")
            return False
        return wait_popup(timeout_ms=4500)

    point = None
    for attempt in range(1, 10):
        point = center_target_day_card()
        if point:
 # 좌표가 여전히 화면 밖이면 한 번 더 시도
            try:
                viewport = page.evaluate("""() => ({w: window.innerWidth || 1920, h: window.innerHeight || 900})""")
                if 10 <= float(point.get('contentX', 0)) <= float(viewport.get('w', 1920)) - 10:
                    break
            except Exception:
                break
        page.mouse.wheel(0, 450)
        page.wait_for_timeout(500)

    if not point:
        raise Exception(f"Price & inventory 영역에서 익일 날짜 카드 {target_day}를 찾지 못했습니다.")

    _v(f"[진행] 날짜 카드 확인: {point.get('text')} / x={int(point['x'])}, y={int(point['y'])}, score={point.get('score')}")

    if click_edit_if_visible(point):
        return

 #: 좌표 클릭 제거. 카드 element 자체를 JS로 click() 호출.
 # 카드를 클릭하면 Edit 버튼이 나타나는 경우가 있으므로, 클릭 후 Edit 재탐색.
    click_strategies = [
 # 카드 자체를 클릭
        lambda: page.evaluate("""(day) => {
 function norm(s) { return (s || '').replace(/\\s+/g, ' ').trim(); }
 const dayNodes = Array.from(document.querySelectorAll('div,span,td,th,button'))
 .filter(el => (() => { const _t = norm(el.innerText || el.textContent || ''); return _t === String(day) || _t === String(day).padStart(2, '0') || (!isNaN(parseInt(_t, 10)) && parseInt(_t, 10) === parseInt(day, 10) && _t.length <= 3); })());
 for (const dn of dayNodes) {
 let cur = dn;
 for (let i = 0; i < 10 && cur; i++, cur = cur.parentElement) {
 const r = cur.getBoundingClientRect();
 if (r.width >= 100 && r.width <= 430 && r.height >= 55 && r.height <= 330) {
 try { cur.scrollIntoView({block:'center'}); } catch(e) {}
 cur.click();
 return true;
 }
 }
 }
 return false;
 }""", str(target_day)),
 # 날짜 숫자 직접 클릭
        lambda: page.evaluate("""(day) => {
 function norm(s) { return (s || '').replace(/\\s+/g, ' ').trim(); }
 const dn = Array.from(document.querySelectorAll('div,span,td,th,button'))
 .find(el => (() => { const _t = norm(el.innerText || el.textContent || ''); return _t === String(day) || _t === String(day).padStart(2, '0') || (!isNaN(parseInt(_t, 10)) && parseInt(_t, 10) === parseInt(day, 10) && _t.length <= 3); })() && el.getBoundingClientRect().y > 180);
 if (!dn) return false;
 try { dn.scrollIntoView({block:'center'}); } catch(e) {}
 dn.click();
 return true;
 }""", str(target_day)),
    ]

    for idx, strategy in enumerate(click_strategies, start=1):
        try:
            _v(f"[진행] 날짜 카드 영역 JS click {idx}")
            ok = bool(strategy())
            if not ok:
                continue
            page.wait_for_timeout(650)
            if popup_is_open():
                _v("[완료] Edit schedule 팝업 열림")
                return
            if click_edit_if_visible(point):
                return
        except Exception as e:
            print(f"[주의] 날짜 카드 JS click {idx} 실패: {e}")

 # 마지막 fallback: 현재 화면에 보이는 Edit를 그냥 클릭
    if click_edit_if_visible(None):
        return

    raise Exception("Edit schedule 팝업을 확인하지 못했습니다.")

def fill_inventory_in_popup(page, inventory):
    """
 Inventory 수량 입력.
 - selector 기반 재작성 (좌표 클릭 제거).
 - Edit schedule 팝업의 'Inventory' 라벨 다음 첫 번째 ant-input 사용.
 - 키보드로 값 입력 후 JS 이벤트 강제 발생으로 React state 반영.
 - 입력값 검증 후 불일치면 raise.
    """
    value = str(inventory)
    _v(f"[진행] Inventory 수량 입력: {value}")

 # 'Inventory' 라벨 다음의 첫 번째 ant-input 찾기
    inv_input = page.locator(
        "xpath=//*[normalize-space(text())='Inventory']"
        "/following::input[contains(@class,'ant-input')][1]"
    ).first
    try:
        inv_input.wait_for(state='visible', timeout=3000)
    except Exception:
 # fallback: Edit schedule 팝업 내 첫 번째 ant-input
        inv_input = page.locator("input.ant-input").first
        try:
            inv_input.wait_for(state='visible', timeout=2000)
        except Exception:
            raise Exception('Inventory 입력칸을 찾지 못했습니다.')

 # 현재값 확인 (디버그)
    try:
        current_val = (inv_input.input_value(timeout=800) or '').strip()
    except Exception:
        current_val = ''
    _v(f"[진행] Inventory 입력칸 발견: 현재값='{current_val}'")

 # 클릭 → 전체선택 → 새 값 입력 → 이벤트 발생
    _click_safe(inv_input, timeout=2500)
    page.wait_for_timeout(150)
    page.keyboard.press('Control+A')
    page.keyboard.press('Backspace')
    page.keyboard.type(value, delay=25)
    page.keyboard.press('Tab')
    page.wait_for_timeout(300)

 # JS 이벤트 강제 발생 (React state 확실히 반영)
    try:
        handle = inv_input.element_handle(timeout=1500)
        page.evaluate(
            """([el, v]) => {
 const proto = Object.getPrototypeOf(el);
 const desc = Object.getOwnPropertyDescriptor(proto, 'value');
 if (desc && desc.set) desc.set.call(el, v); else el.value = v;
 el.dispatchEvent(new Event('input', {bubbles:true}));
 el.dispatchEvent(new Event('change', {bubbles:true}));
 el.dispatchEvent(new Event('blur', {bubbles:true}));
 }""",
            [handle, value],
        )
        page.wait_for_timeout(250)
    except Exception:
        pass

 # 최종 검증
    try:
        final_val = (inv_input.input_value(timeout=1000) or '').strip()
    except Exception:
        final_val = ''
    _v(f"[진행] Inventory 최종값: '{final_val}'")
    if final_val != value:
        raise Exception(
            f'Inventory 입력 검증 실패: 기대={value}, 실제={final_val!r}'
        )

    _v('[완료] Inventory 입력 및 검증 완료')


def set_accept_booking_until(page, accept_until=None):
    """
 Accept bookings until 설정 +.

 변경사항:
 - accept_until 파라미터 추가. 상품별로 '당일 06:00' / '하루 전 22:00' 등 설정 가능.
 - 파라미터 형식: {'when': 'same_day'|'day_before', 'time': 'HH:MM'}
 - 명시 안 하면 기본값 = 당일 06:00 (기존 동작과 동일)

 동작:
 - Inventory 수정 후 항상 지정된 옵션/시간을 다시 적용.
 - 값이 이미 같아도 클릭/입력 이벤트를 재발생시켜 저장 버튼이 활성화되도록 함.
    """
 #: accept_until 정규화
    if not accept_until or not isinstance(accept_until, dict):
        accept_until = {'when': 'same_day', 'time': '06:00'}
    when_key = str(accept_until.get('when', 'same_day')).strip().lower()
    time_value = str(accept_until.get('time', '06:00')).strip()

 # Klook UI 에 보이는 옵션 텍스트
    if when_key == 'day_before':
        day_option_text = '1 day in advance'
    else:
        when_key = 'same_day'
        day_option_text = 'On the day of participation'

    _v(f"[진행] Accept bookings until 설정: {day_option_text} / {time_value}")

    def body_text(timeout_ms=1500):
        return get_body_text(page, timeout_ms)

    def get_accept_fields():
        try:
            return page.evaluate(
                """() => {
 function norm(s){return (s||'').replace(/\s+/g,' ').trim();}
 function visible(el){
 if(!el) return false;
 const r=el.getBoundingClientRect();
 const st=getComputedStyle(el);
 return st.display !== 'none' && st.visibility !== 'hidden' && Number(st.opacity) !== 0 &&
 r.width > 0 && r.height > 0 && r.bottom >= 0 && r.top <= (innerHeight || 900) + 250;
 }
 function point(o){
 if(!o) return null;
 return {x:o.x+o.w/2, y:o.y+o.h/2, arrowX:o.x+o.w-18, leftX:o.x+28, top:o.y, bottom:o.y+o.h, w:o.w, h:o.h, text:o.text, tag:o.tag, source:o.source};
 }
 const labels = Array.from(document.querySelectorAll('label,div,span,p,strong'))
 .map(el => { const r=el.getBoundingClientRect(); return {el, text:norm(el.innerText||el.textContent||''), x:r.x,y:r.y,w:r.width,h:r.height, area:r.width*r.height}; })
 .filter(o => visible(o.el) && o.text.includes('Accept bookings until'))
 .sort((a,b) => {
 const ae = (a.text === 'Accept bookings until' || a.text.length <= 45) ? 0 : 1;
 const be = (b.text === 'Accept bookings until' || b.text.length <= 45) ? 0 : 1;
 return ae-be || a.area-b.area || b.y-a.y;
 });
 const label = labels[0];
 if(!label) return null;
 const raw = Array.from(document.querySelectorAll('input,textarea,button,div,span,[role="button"],[role="combobox"],.ant-select,.ant-select-selector'))
 .map(el => { const r=el.getBoundingClientRect(); return {el, text:norm(el.innerText||el.textContent||el.value||el.getAttribute('value')||''), x:r.x,y:r.y,w:r.width,h:r.height, tag:(el.tagName||'').toLowerCase(), cls:String(el.className||''), role:el.getAttribute('role')||'', source:'raw'}; })
 .filter(o => visible(o.el) && o.y >= label.y + label.h - 8 && o.y <= label.y + label.h + 150 && o.x >= label.x - 80 && o.x <= label.x + 760 && o.w >= 40 && o.h >= 18 && o.w <= 900 && o.h <= 90)
 .sort((a,b) => a.y-b.y || a.x-b.x || b.w-a.w);
 const rows=[];
 for(const n of raw){
 const dup=rows.findIndex(r=>Math.abs(r.y-n.y)<10);
 if(dup === -1) rows.push(n);
 else {
 const old=rows[dup];
 const oldScore = old.w + (/participation|advance|day/i.test(old.text) ? 500 : 0) + (/input|textarea/.test(old.tag) ? 150 : 0) + (/select|combobox|ant-select/.test(old.cls+old.role) ? 220 : 0);
 const newScore = n.w + (/participation|advance|day/i.test(n.text) ? 500 : 0) + (/input|textarea/.test(n.tag) ? 150 : 0) + (/select|combobox|ant-select/.test(n.cls+n.role) ? 220 : 0);
 if(newScore > oldScore) rows[dup]=n;
 }
 }
 let day = rows.find(o => /participation|advance|day/i.test(o.text) && o.w >= 130) || rows.find(o => o.w >= 180 && !/^\d{1,2}:\d{2}$/.test(o.text)) || rows[0] || null;
 let time = rows.find(o => /^\d{1,2}:\d{2}$/.test(o.text)) || rows.find(o => /\d{1,2}:\d{2}/.test(o.text)) || rows.find(o => day && o !== day && o.y > day.y + 16 && (o.tag === 'input' || o.w >= 90)) || null;
 return {label:{x:label.x,y:label.y,w:label.w,h:label.h,bottom:label.y+label.h,text:label.text}, day:point(day), time:point(time), rows:rows.map(point).slice(0,10)};
 }"""
            )
        except Exception:
            return None

    def click_option_exact(option_text, field=None, timeout_ms=3500):
        """: 좌표 클릭 제거. JS 안에서 직접 el.click() 호출."""
        import time as _time
        end = _time.perf_counter() + timeout_ms / 1000
        while _time.perf_counter() < end:
            try:
                clicked = bool(page.evaluate(
                    """([optionText, field]) => {
 function norm(s){return (s||'').replace(/\\s+/g,' ').trim();}
 function visible(el){
 if(!el) return false;
 const r=el.getBoundingClientRect();
 const st=getComputedStyle(el);
 return st.display !== 'none' && st.visibility !== 'hidden' && Number(st.opacity) !== 0 &&
 r.width > 0 && r.height > 0 && r.bottom >= 0 && r.top <= (innerHeight || 900);
 }
 const nodes = Array.from(document.querySelectorAll('div,span,li,button,[role="option"],[role="menuitem"],.ant-select-item-option,.ant-select-item-option-content'))
 .filter(el => {
 if(!visible(el)) return false;
 if(norm(el.innerText||el.textContent||'') !== optionText) return false;
 const r=el.getBoundingClientRect();
 if(r.width < 15 || r.height < 12) return false;
 if(field && field.bottom !== undefined){
 const closeY = r.y >= field.top - 80 && r.y <= field.bottom + 500;
 const closeX = r.x >= field.leftX - 240 && r.x <= field.arrowX + 360;
 if(!closeY && !closeX) return false;
 }
 return true;
 })
 .sort((a,b) => {
 const ar = a.getBoundingClientRect();
 const br = b.getBoundingClientRect();
 if(field && field.bottom !== undefined){
 const da = Math.abs(ar.y-field.bottom) + Math.abs(ar.x-field.leftX);
 const db = Math.abs(br.y-field.bottom) + Math.abs(br.x-field.leftX);
 return da-db;
 }
 return ar.y-br.y || ar.x-br.x;
 });
 if(!nodes.length) return false;
 const target = nodes[0];
 try { target.scrollIntoView({block:'center'}); } catch(e) {}
 target.click();
 return true;
 }""",
                    [option_text, field],
                ))
                if clicked:
                    _v(f"[진행] 옵션 클릭: {option_text} (JS click)")
                    page.wait_for_timeout(500)
                    return True
            except Exception:
                pass
            page.wait_for_timeout(180)
        return False

    def set_day():
 #: Ant Design ant-select selector 기반 재작성 (좌표 클릭 제거).
 # - 드롭다운: div.cut-off-time-input.ant-select (Klook 고유 클래스)
 # - 현재값: div.ant-select-selection-selected-value (title 속성)
 # - 옵션: li.ant-select-dropdown-menu-item (보이는 것만)
 # - 작업 후 드롭다운 닫음 (Escape) → set_time 에 영향 없게.
        _v(f"[진행] 날짜 드롭다운 설정 시작: target='{day_option_text}'")

 # 1) 드롭다운 박스 찾기
        select_box = page.locator('div.cut-off-time-input.ant-select').first
        try:
            select_box.wait_for(state='visible', timeout=3000)
        except Exception:
            raise Exception('cut-off-time-input 드롭다운을 찾지 못했습니다.')

 # 2) 현재값 확인
        try:
            current_value_locator = select_box.locator('div.ant-select-selection-selected-value').first
            current_val = (current_value_locator.get_attribute('title') or '').strip()
            if not current_val:
                current_val = (current_value_locator.inner_text(timeout=800) or '').strip()
        except Exception:
            current_val = ''
        _v(f"[진행] 날짜 드롭다운 현재값='{current_val}'")

 # 3) 이미 target 이면 작업 생략
        if current_val == day_option_text:
            _v(f"[완료] 날짜 드롭다운이 이미 '{day_option_text}' 입니다. 작업 생략")
            return True

 # 4) 드롭다운 클릭으로 열기
        _click_safe(select_box, timeout=2500)
        page.wait_for_timeout(450)

 # 5) 펼쳐진 옵션 목록 (숨겨지지 않은 것) 의 target li 클릭
 # Ant Design 은 옵션이 펼쳐지면 body 끝에 ant-select-dropdown 컨테이너를 추가하고,
 # 닫히면 ant-select-dropdown-hidden 클래스가 붙음. visible 옵션만 골라서 클릭.
        option_li = page.locator(
            f"xpath=//li[contains(@class,'ant-select-dropdown-menu-item')]"
            f"[normalize-space(text())='{day_option_text}']"
        ).first
        try:
            option_li.wait_for(state='visible', timeout=2500)
        except Exception:
            raise Exception(f"옵션 '{day_option_text}' 를 옵션 목록에서 찾지 못했습니다.")

        # Ant Design 드롭다운이 열리는 중이면 'stable' 을 못 기다려 여기서 죽는다.
        # 클릭은 Playwright 가 알아서 스크롤하므로 이건 거들 뿐이다. 실패해도 넘어간다.
        # (2026-08-26 오픈: 이 한 줄 때문에 한국 3건이 통째로 실패했다)
        try:
            option_li.scroll_into_view_if_needed(timeout=1000)
        except Exception:
            pass
        _click_safe(option_li, timeout=2500)
        page.wait_for_timeout(400)
        _v(f"[진행] 옵션 '{day_option_text}' 클릭 완료")

 # 6) 클릭 후 현재값 검증
        try:
            current_value_locator = select_box.locator('div.ant-select-selection-selected-value').first
            new_val = (current_value_locator.get_attribute('title') or '').strip()
            if not new_val:
                new_val = (current_value_locator.inner_text(timeout=800) or '').strip()
        except Exception:
            new_val = ''
        _v(f"[진행] 날짜 드롭다운 선택 후 값='{new_val}'")

        if new_val != day_option_text:
            raise Exception(
                f"날짜 드롭다운 선택 검증 실패: 기대='{day_option_text}', 실제='{new_val}'"
            )

 # 7) 드롭다운 잔재 닫기 (다음 단계인 set_time 에 영향 없게)
        try:
            page.keyboard.press('Escape')
            page.wait_for_timeout(200)
        except Exception:
            pass

        _v(f"[완료] 날짜 드롭다운 '{day_option_text}' 선택 완료")
        return True

    def set_time():
 #: Ant Design TimePicker selector 기반 재작성.
 # 시간 입력칸은 readonly="true" 이라 키보드 타이핑 불가.
 # 클릭하면 ant-time-picker-panel 팝업이 열리고, 그 안의 시/분 li 항목을 클릭해야 함.
        target_hour, target_min = time_value.split(':')
        _v(f"[진행] 시간 {time_value} 설정 시작: 시={target_hour}, 분={target_min}")

 # 1: 이전 단계(set_day)에서 열린 ant-select 드롭다운이 잔존하면
 # 시간 입력칸 위를 가려서 클릭 가로채짐. Escape 로 한 번 닫고 시작.
        try:
            page.keyboard.press('Escape')
            page.wait_for_timeout(250)
        except Exception:
            pass

 # 1) 시간 입력칸 찾기 (라벨 기준 selector)
        time_input = page.locator(
            "xpath=//*[contains(normalize-space(.), 'Accept bookings until')]"
            "/following::input[contains(@class, 'ant-time-picker-input')][1]"
        ).first
        try:
            time_input.wait_for(state='visible', timeout=3000)
        except Exception:
            raise Exception('시간 입력칸을 찾지 못했습니다. (ant-time-picker-input)')

 # 현재값 출력 (디버그)
        try:
            current_val = (time_input.input_value(timeout=1000) or '').strip()
        except Exception:
            current_val = ''
        _v(f"[진행] 시간 입력칸 발견: 현재값='{current_val}'")

 # 2: 이미 target 값이면 작업 생략 (Klook 서버에 이전 값이 남아있는 경우)
        if current_val == time_value:
            _v(f"[완료] 시간 입력칸이 이미 {time_value} 입니다. 작업 생략")
            return True

 # 1 추가 안전망: 드롭다운 메뉴 항목이 떠있으면 입력칸 외 안전한 영역 클릭으로 닫기
        dropdown_remnant = page.locator('li.ant-select-dropdown-menu-item').first
        try:
            if dropdown_remnant.is_visible(timeout=300):
                _v('[진행] 이전 드롭다운 잔재 감지, 닫기 시도')
 # Edit schedule 모달 헤더 클릭으로 닫기
                page.locator("xpath=//*[contains(normalize-space(.), 'Edit schedule')]").first.click(timeout=1500)
                page.wait_for_timeout(350)
        except Exception:
            pass

 # 2) 팝업 열기 (입력칸 클릭)
        _click_safe(time_input, timeout=3000)
        page.wait_for_timeout(400)

 # 3) 팝업 열림 검증 — ant-time-picker-panel-input 가 보이는지 확인
        panel_input = page.locator('input.ant-time-picker-panel-input').first
        try:
            panel_input.wait_for(state='visible', timeout=2000)
        except Exception:
            raise Exception('시간 선택 팝업이 열리지 않았습니다. (ant-time-picker-panel)')
        _v('[진행] 시간 선택 팝업 열림 확인')

 # 4) 시 패널의 target_hour 항목 클릭
 # 두 개의 ant-time-picker-panel-select 가 있고, 첫 번째 = 시, 두 번째 = 분
        hour_li = page.locator(
            f"xpath=(//div[contains(@class,'ant-time-picker-panel-select')])[1]"
            f"//li[normalize-space(text())='{target_hour}']"
        ).first
        try:
            hour_li.wait_for(state='visible', timeout=2000)
        except Exception:
            raise Exception(f'시간 패널에서 {target_hour} 항목을 찾지 못했습니다.')
        try:
            hour_li.scroll_into_view_if_needed(timeout=1000)
        except Exception:
            pass
        _click_safe(hour_li, timeout=2000)
        page.wait_for_timeout(250)
        _v(f'[진행] 시={target_hour} 클릭 완료')

 # 5) 분 패널의 target_min 항목 클릭
        min_li = page.locator(
            f"xpath=(//div[contains(@class,'ant-time-picker-panel-select')])[2]"
            f"//li[normalize-space(text())='{target_min}']"
        ).first
        try:
            min_li.wait_for(state='visible', timeout=2000)
        except Exception:
            raise Exception(f'분 패널에서 {target_min} 항목을 찾지 못했습니다.')
        try:
            min_li.scroll_into_view_if_needed(timeout=1000)
        except Exception:
            pass
        _click_safe(min_li, timeout=2000)
        page.wait_for_timeout(250)
        _v(f'[진행] 분={target_min} 클릭 완료')

 # 6) 패널 안에서 선택된 항목 검증
        try:
            selected_hour = page.locator(
                "xpath=(//div[contains(@class,'ant-time-picker-panel-select')])[1]"
                "//li[contains(@class,'ant-time-picker-panel-select-option-selected')]"
            ).first.inner_text(timeout=1000).strip()
            selected_min = page.locator(
                "xpath=(//div[contains(@class,'ant-time-picker-panel-select')])[2]"
                "//li[contains(@class,'ant-time-picker-panel-select-option-selected')]"
            ).first.inner_text(timeout=1000).strip()
            _v(f'[진행] 팝업 내 선택값: 시={selected_hour}, 분={selected_min}')
            if selected_hour != target_hour or selected_min != target_min:
                raise Exception(
                    f'팝업 선택값 불일치: 시={selected_hour}/{target_hour}, '
                    f'분={selected_min}/{target_min}'
                )
        except Exception as e:
 # selected 클래스를 못 잡은 경우도 실패로 처리
            raise Exception(f'팝업 선택값 검증 실패: {e}')

 # 7) 팝업 닫기 (외부 클릭 — Edit schedule 팝업 헤더 영역 클릭이 안전)
        try:
            page.keyboard.press('Escape')
            page.wait_for_timeout(300)
        except Exception:
            pass
 # Escape 가 안 먹으면 panel 외부의 안전한 영역 클릭으로 닫기
        if page.locator('input.ant-time-picker-panel-input').first.is_visible(timeout=500):
            try:
 # Edit schedule 팝업 제목 영역 (모달 상단)
                page.locator("xpath=//*[contains(normalize-space(.), 'Edit schedule')]").first.click(timeout=1500)
                page.wait_for_timeout(300)
            except Exception:
                pass

 # 8) 최종 검증 — 시간 입력칸의 value 가 정확히 target 인지
        try:
            final_val = time_input.input_value(timeout=1500) or ''
        except Exception:
            final_val = ''
        _v(f"[진행] 시간 입력칸 최종값: '{final_val}'")
        if final_val.strip() != time_value:
            raise Exception(
                f'시간 입력칸 최종 검증 실패: 기대={time_value}, 실제={final_val!r}'
            )

        _v(f'[완료] Accept bookings until 시간 {time_value} 설정 완료')
        return True

    if not set_day():
        raise Exception(f'{day_option_text} 옵션을 선택하지 못했습니다.')
    if not set_time():
        raise Exception(f'{time_value} 시간 설정을 완료하지 못했습니다.')
    _v('[완료] Accept bookings until 설정 완료')


def set_activate(page, on=True):
    """Activate 토글을 원하는 상태(on=True ON, on=False OFF)로 설정."""
    target = 'ON' if on else 'OFF'
    _v(f"[진행] Activate {target} 설정 시작")

    switch = page.locator(
        "xpath=//*[contains(normalize-space(.), 'Activate')]"
        "/following::button[contains(@class, 'ant-switch')][1]"
    ).first
    try:
        switch.wait_for(state='visible', timeout=2500)
    except Exception:
        switch = page.locator('button.ant-switch').first
        try:
            switch.wait_for(state='visible', timeout=2000)
        except Exception:
            raise Exception('Activate 스위치 (button.ant-switch) 를 찾지 못했습니다.')

    cls = (switch.get_attribute('class') or '').lower()
    aria = (switch.get_attribute('aria-checked') or '').lower()
    is_on = ('ant-switch-checked' in cls) or (aria == 'true')

    if is_on == on:
        _v(f'[완료] Activate 이미 {target} 상태')
        return

    _v(f'[진행] Activate {("OFF" if is_on else "ON")} → {target} 전환 시도')
    _click_safe(switch, timeout=2500)
    page.wait_for_timeout(450)

    cls2 = (switch.get_attribute('class') or '').lower()
    aria2 = (switch.get_attribute('aria-checked') or '').lower()
    is_on2 = ('ant-switch-checked' in cls2) or (aria2 == 'true')
    if is_on2 != on:
        raise Exception(f'Activate {target} 전환 실패: 클릭 후에도 반대 상태 유지')

    _v(f'[완료] Activate {target} 전환 완료')


def ensure_activate_on(page):
    """하위 호환 wrapper: 기존 호출처가 그대로 동작하도록 ON 으로 설정."""
    return set_activate(page, on=True)


def confirm_popup(page):
    """
 Confirm 저장 처리.
 - selector 기반 재작성 (좌표 클릭 제거).
 - 두 Confirm 버튼을 클래스 차이로 명확히 구분:
 1차 Edit schedule Confirm: <button class="button ant-btn ant-btn-primary">
 2차 Note 팝업 Confirm : <button class="ant-btn ant-btn-primary"> (button 클래스 없음)
 - 거짓 성공 방지: 1차 Confirm 버튼이 안 보이면 raise (이미 닫혀있다는 가정 X)
    """
    _v("[진행] Confirm 저장 처리 시작")

 # 두 Confirm 버튼 정확히 구분하는 selector
 # CSS selector 로 더 직관적으로 표현
    first_confirm_selector = (
        'button.button.ant-btn-primary:has(span:text-is("Confirm"))'
    )
 # 'button' 클래스 없는 Confirm = Note 팝업 Confirm
    note_confirm_selector = (
        'button.ant-btn-primary:not(.button):has(span:text-is("Confirm"))'
    )

    def is_visible(selector) -> bool:
        try:
            return page.locator(selector).first.is_visible(timeout=300)
        except Exception:
            return False

    def click_button(selector, label, tries: int = 3) -> bool:
        """
        버튼을 누른다. 사라지면 다시 찾아서 누른다.

        ⚠️ Ant Design 모달은 뜬 뒤에도 다시 그려진다. 그때 버튼 요소가
           DOM 에서 떨어져 나가(detached) 클릭이 실패한다.
           _click_safe 의 세 단계는 모두 '찾아 두고 → 누른다' 라 그 사이에
           사라지면 전부 실패한다. 그래서 찾기부터 다시 한다.
           (2026-08-30: MBC·남이섬셔틀이 이것 때문에 저장이 안 됐다)
        """
        last = ""
        for attempt in range(1, tries + 1):
            btn = page.locator(selector).first        # 매번 새로 찾는다
            try:
                btn.wait_for(state='visible', timeout=2500)
            except Exception:
                last = "버튼이 보이지 않음"
                page.wait_for_timeout(400)
                continue
            # 모달이 뜨는 중에 바로 누르면 'not stable' 로 거부당한다. 잠깐 앉힌다.
            page.wait_for_timeout(350 + attempt * 150)
            try:
                _click_safe(btn, timeout=2500)
                if attempt > 1:
                    _v(f'[진행] {label} 클릭 완료 ({attempt}번째 시도)')
                else:
                    _v(f'[진행] {label} 클릭 완료')
                return True
            except Exception as e:
                last = str(e).split("Call log")[0].strip()[:90]
                _v(f'[안내] {label} 클릭 실패({attempt}/{tries}) — 다시 찾는다: {last}')
                page.wait_for_timeout(500)

        # 마지막 수단: '찾아서 누르기' 를 한 번에 한다. 사라질 틈이 없다.
        try:
            done = page.evaluate(
                """(sel) => {
 const el = document.querySelector(sel);
 if (!el) return false;
 el.click();
 return true;
 }""", selector.replace("xpath=", ""))
            if done:
                _v(f'[진행] {label} 클릭 완료 (한 번에 찾아 누름)')
                return True
        except Exception:
            pass

        print(f'[오류] {label} 클릭 실패: {last}')
        return False

    def wait_for_hidden(selector, label, timeout_ms=8000) -> bool:
        deadline = timeout_ms
        step = 250
        while deadline > 0:
            if not is_visible(selector):
                _v(f'[완료] {label} 사라짐 확인')
                return True
            page.wait_for_timeout(step)
            deadline -= step
        return False

 # 1) Edit schedule 의 1차 Confirm 클릭 (필수)
    if not is_visible(first_confirm_selector):
 # 거짓 성공 방지: 1차 Confirm 버튼이 안 보이면 비정상 상황
        raise Exception(
            'Edit schedule Confirm 버튼이 보이지 않습니다. '
            '(button.button.ant-btn-primary 가 없음 — Edit schedule 팝업이 안 떠있거나 셀렉터 변경)'
        )

    def vanished(selector, label) -> bool:
        """
        '눌렀는데 실패' 인지 '눌러져서 사라진' 것인지 가른다.

        ⚠️ Ant Design 모달은 클릭이 먹은 순간 닫힌다. 그러면 다음 재시도에서
           버튼을 못 찾아 click_button 이 False 를 돌려주는데, 실제로는 저장이
           된 것이다. 그걸 안 가르면 이미 열린 상품을 '실패' 로 보고한다.

           2026-08-31: MBC 스튜디오 5 / 에버 15 가 이렇게 실패로 남았다.
             [오류] 1차 Edit schedule Confirm 클릭 실패: 버튼이 보이지 않음
           나중에 직접 오픈으로 다시 돌려 보니 둘 다 이미 열려 있었다.
           사람은 안 열린 줄 알고 다시 돌리게 된다.

        버튼이 처음부터 없었던 경우와는 다르다. 위에서 is_visible 로 이미
        확인하고 들어왔으므로, 여기 오는 것은 '있는 걸 보고 눌렀는데 사라진'
        경우뿐이다. 그래서 '거짓 성공 방지' 는 그대로 유지된다.

        여기서 성공으로 단정하지도 않는다 — 뒤의 Note 팝업 확인과
        모달 재확인이 그대로 돌아간다.
        """
        if is_visible(selector):
            return False
        print(f'[안내] {label} 이 사라졌습니다 — 클릭이 먹은 것으로 보고 계속합니다')
        return True

    _v('[진행] 1차 Edit schedule Confirm 클릭 시도')
    if not click_button(first_confirm_selector, '1차 Edit schedule Confirm'):
        if not vanished(first_confirm_selector, '1차 Edit schedule Confirm'):
            raise Exception('1차 Edit schedule Confirm 버튼 클릭 실패')

 # 1차 Confirm 버튼이 사라질 때까지 대기 (= Edit schedule 모달 닫힘)
    if not wait_for_hidden(first_confirm_selector, '1차 Confirm 버튼', timeout_ms=5000):
        print('[주의] 1차 Confirm 버튼이 5초 후에도 보임 → Note 단계로 진행')

 # 2) Note 팝업 Confirm 등장 대기 (최대 3초)
    _v('[진행] Note 팝업 등장 대기 (최대 3초)')
    note_appeared = False
    for _ in range(12):
        if is_visible(note_confirm_selector):
            note_appeared = True
            break
        page.wait_for_timeout(250)

    if note_appeared:
        _v('[진행] Note 팝업 발견 → 2차 Confirm 클릭')
        if not click_button(note_confirm_selector, '2차 Note Confirm'):
            # 여기도 같다 — 눌러져서 사라진 것을 실패로 보면 안 된다
            if not vanished(note_confirm_selector, '2차 Note Confirm'):
                raise Exception('Note 팝업 2차 Confirm 클릭 실패')
        if not wait_for_hidden(note_confirm_selector, '2차 Confirm 버튼', timeout_ms=8000):
            raise Exception('Note 팝업 Confirm 클릭 후에도 버튼이 안 사라짐')
 #: 2차 Confirm 버튼이 사라졌으면 저장 완료. 1차 selector 재검사 안 함.
 # (1차 selector 'button.button.ant-btn-primary' 가 페이지 다른 곳의 무관한 버튼과
 # 매칭되어 false positive 가 발생하는 케이스 발견. note_appeared 경로는
 # 2차 사라짐만으로 충분히 저장 완료를 보장함.)
        _v('[완료] Confirm 저장 처리 완료 (note 팝업 경유)')
        return

 # note 팝업이 안 떴으면 변경 사항 없이 즉시 저장된 케이스. 1차 모달이 닫혔는지 확인.
    _v('[안내] Note 팝업이 뜨지 않음 (변경사항 없거나 즉시 저장된 경우)')

 #: 1차 selector 만으로 검증할 때는 ant-modal 컨텍스트 안에 있는 것만 확인.
 # (페이지 다른 곳의 무관한 'button.button.ant-btn-primary' 매칭 false positive 방지)
    modal_confirm_selector = (
        '.ant-modal:visible button.button.ant-btn-primary:has(span:text-is("Confirm"))'
    )
    if is_visible(modal_confirm_selector):
 # 정말 Edit schedule 모달이 아직 열려있음 → 저장 실패
        raise Exception('저장 처리 후에도 Edit schedule 모달의 Confirm 버튼이 남아있음')

    _v('[완료] Confirm 저장 처리 완료 (즉시 저장)')


def return_to_package_search_page(page):
    """
: 저장 후 다음 상품 처리를 위해 Package 검색 화면으로 복귀합니다.
 기존 뒤로가기 1회 방식은 여러 상품 연속 처리 시 Activity 탭/상세 페이지에 남는 경우가 있어,
 캐시된 Package 검색 URL 직접 이동을 우선 사용합니다.
    """
    _v("[진행] 저장 후 Activity management > Package 화면으로 복귀")
    if go_package_search_hard(page, reason="저장 완료 후 다음 상품 준비"):
        _v("[완료] Activity management > Package 화면 복귀")
        return
    raise Exception("저장 후 Package 검색 화면으로 복귀하지 못했습니다.")



def _is_numeric_package_key(value) -> bool:
    return bool(re.fullmatch(r"\d+", str(value or "").strip()))


def _safe_filename_text(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣_.-]+", "_", str(value or "unknown"))[:80]


def _search_result_has_any_package_link(page) -> bool:
    try:
        return bool(page.evaluate(
            """() => Array.from(document.querySelectorAll('a[href]')).some(a => {
 const href = a.getAttribute('href') || a.href || '';
 const text = (a.innerText || a.textContent || '').replace(/\s+/g, ' ').trim();
 return href.includes('/package/info') || /\b\d{4,}\s*-/.test(text);
 })"""
        ))
    except Exception:
        return False


def search_package_smart(page, search_key):
    """Package ID 또는 title 검색어를 입력해 검색합니다.
 숫자 Package ID는 기존 안정 로직을 그대로 사용하고, title 검색어는 검색 결과 링크 기준으로 확인합니다.
    """
    search_key = str(search_key).strip()
    if _is_numeric_package_key(search_key):
        return search_package(page, search_key)

    _v(f"[진행] Package title 검색: {search_key}")
    goto_activity_management_package(page)

    try:
        try:
            page.get_by_text("Reset", exact=False).click(timeout=1000)
        except Exception:
            page.get_by_text("Reset", exact=False).click(timeout=800, force=True)
        page.wait_for_timeout(400)
    except Exception:
        pass

    if not fill_package_id(page, search_key):
        raise Exception("Package ID & title 입력칸을 찾지 못했습니다.")

    if not click_search_button(page):
        raise Exception("Package 검색 Search 버튼을 찾지 못했습니다.")

    deadline = datetime.now().timestamp() + 9
    while datetime.now().timestamp() < deadline:
        body = get_body_text(page, 1200)
        if search_key.lower() in body.lower() or _search_result_has_any_package_link(page):
            remember_package_search_url(page)
            _v(f"[완료] Package title 검색 결과 확인: {search_key}")
            return
        page.wait_for_timeout(300)

    body = get_body_text(page, 2000)
    raise Exception(f"검색 결과에서 Package title '{search_key}'를 확인하지 못했습니다. 현재 화면 일부: {body[:160]}")


def open_package_detail_smart(page, search_key):
    """숫자 Package ID는 기존 직접 href 로직을 사용합니다.
 title 검색어는 검색 결과에 보이는 package/info 링크 중 가장 적합한 첫 링크로 직접 이동합니다.
    """
    search_key = str(search_key).strip()
    if _is_numeric_package_key(search_key):
        return open_package_detail(page, search_key)

    _v(f"[진행] Package title 결과 링크 클릭: {search_key}")
    old_url = page.url

    def is_detail_page():
        body = get_body_text(page, 2500)
        if "Package ID & title" in body and "Package status" in body and "Search" in body:
            return False
        detail_keywords = [
            "Package info", "Package details", "Basic Info", "Price & inventory",
            "Inventory", "Unpublish package", "Publish package", "Adult", "Child", "Edit schedule"
        ]
        return any(k in body for k in detail_keywords)

    info = page.evaluate(
        """(query) => {
 function norm(s){return (s||'').replace(/\s+/g,' ').trim();}
 function visible(el){
 const r = el.getBoundingClientRect();
 const st = getComputedStyle(el);
 return st.display !== 'none' && st.visibility !== 'hidden' && Number(st.opacity) !== 0 &&
 r.width > 0 && r.height > 0 && r.bottom >= 0 && r.top <= (innerHeight || 900) + 500;
 }
 function absHref(href){
 if(!href) return '';
 try { return new URL(href, location.href).href; } catch(e) { return href; }
 }
 const q = String(query || '').toLowerCase();
 const tokens = q.split(/\s+|&|\+|\||\//).map(t => t.trim()).filter(t => t.length >= 3);
 const anchors = Array.from(document.querySelectorAll('a[href]'))
 .map(a => {
 const r = a.getBoundingClientRect();
 const text = norm(a.innerText || a.textContent || a.getAttribute('title') || '');
 const href = absHref(a.getAttribute('href') || a.href || '');
 const lower = text.toLowerCase();
 let score = 0;
 if (href.includes('/package/info')) score += 80;
 if (/\b\d{4,}\s*-/.test(text)) score += 25;
 if (lower.includes(q)) score += 80;
 for (const t of tokens) if (lower.includes(t)) score += 18;
 if (visible(a)) score += 25;
 if (r.y >= 200) score += 10;
 return {text, href, x:r.x, y:r.y, w:r.width, h:r.height, visible:visible(a), score};
 })
 .filter(o => o.href && o.href.includes('/package/info'))
 .sort((a,b) => b.score - a.score || a.y - b.y || a.x - b.x);
 return anchors[0] || null;
 }""",
        search_key,
    )

    if not info or not info.get("href"):
        raise Exception(f"'{search_key}' 검색 결과에서 Package 상세 링크를 찾지 못했습니다.")

    _v(f"[진행] Package href 직접 이동: {info.get('text','')[:120]} / href={info.get('href','')[:100]}")
    page.goto(info["href"], wait_until="domcontentloaded", timeout=15000)
    page.wait_for_timeout(1200)
    if is_detail_page() or page.url != old_url:
        _v("[완료] Package 상세 화면 진입")
        return
    raise Exception(f"'{search_key}' Package 상세 화면 진입을 확인하지 못했습니다.")




# ============================================================
# Activity workflow support
# - Some new Klook products must be opened from Activity tab, not Package tab.
# - Flow: Activity management > Activity search > Activity detail > Inventory schedule
# > English · Adult > Price / Inventory Settings > date > Edit schedule popup.
# ============================================================

def is_activity_search_screen(page) -> bool:
    try:
        return bool(page.evaluate("""() => {
 const text = (document.body ? document.body.innerText : '').replace(/\s+/g, ' ').trim();
 return text.includes('Activity ID & title') && text.includes('Activity approval status') && text.includes('Activity status') && text.includes('Search');
 }"""))
    except Exception:
        text = get_body_text(page, 1500)
        return 'Activity ID & title' in text and 'Activity status' in text and 'Search' in text


def remember_activity_search_url(page):
    global ACTIVITY_SEARCH_URL_CACHE
    try:
        if is_activity_search_screen(page) and page.url.startswith('http'):
            ACTIVITY_SEARCH_URL_CACHE = page.url
    except Exception:
        pass


def goto_activity_management_activity(page):
    """Move to My Activities > Activity management > Activity tab.

: 먼저 act/management URL 로 직접 이동 시도. 실패 시 기존 메뉴 클릭으로 fallback.
    """
    _v("[진행] My Activities > Activity management > Activity 이동")

    if is_activity_search_screen(page):
        _v("[안내] 현재 Activity 검색 화면으로 확인되어 이동 생략")
        remember_activity_search_url(page)
        return

    close_open_drawers_or_popups(page)
    try:
        page.wait_for_load_state('domcontentloaded', timeout=4000)
    except Exception:
        pass

 # ──: URL 직접 이동 시도 ──────────────────────────────────────
 # /act/management 는 Activity 탭이 기본으로 열리므로 별도 탭 클릭 불필요.
    try:
        origin = re.match(r'^(https?://[^/]+)', page.url).group(1)
        direct_url = origin + "/mspa/experiencesadmincommon/act/management"
        _v(f"[진행] URL 직접 이동 시도: {direct_url}")
        page.goto(direct_url, wait_until='domcontentloaded', timeout=12000)
        page.wait_for_timeout(800)
        for _ in range(8):
            if is_activity_search_screen(page):
                remember_activity_search_url(page)
                _v("[완료] URL 직접 이동으로 Activity 검색 화면 진입")
                return
            page.wait_for_timeout(250)
        print("[주의] URL 직접 이동 후 Activity 화면 확인 실패. 기존 메뉴 클릭으로 fallback")
    except Exception as e:
        print(f"[주의] URL 직접 이동 실패: {e}. 기존 메뉴 클릭으로 fallback")
 # ── 끝. 실패 시 아래 기존 로직 그대로 진행 ────────────────────

 # If we know the search URL, direct navigation is fastest.
    global ACTIVITY_SEARCH_URL_CACHE
    if ACTIVITY_SEARCH_URL_CACHE:
        try:
            page.goto(ACTIVITY_SEARCH_URL_CACHE, wait_until='domcontentloaded', timeout=12000)
            page.wait_for_timeout(800)
            if is_activity_search_screen(page):
                _v("[완료] Activity 검색 URL 직접 복귀 완료")
                return
        except Exception as e:
            print(f"[주의] Activity 검색 URL 직접 이동 실패: {e}")

 #: 사이드바 탐색은 공통 헬퍼로 (aria-haspopup / role=menuitem 우선)
    try:
        _open_sidebar_section(page, "My Activities", "Activity management")
    except Exception as e:
        print(f"[주의] 사이드바 탐색 실패: {e}")

 # Activity 탭은 기본값이지만, Package 탭에 있다가 진입한 경우를 대비해 명시 클릭
    for _ in range(8):
        if is_activity_search_screen(page):
            remember_activity_search_url(page)
            _v("[완료] Activity 검색 화면 진입")
            return
        page.wait_for_timeout(250)

 #: Activity 탭으로 전환
    try:
        _click_tab(page, "Activity")
    except Exception as e:
        print(f"[주의] Activity 탭 클릭 실패: {e}")

    for _ in range(12):
        if is_activity_search_screen(page):
            remember_activity_search_url(page)
            _v("[완료] Activity 검색 화면 진입")
            return
        page.wait_for_timeout(300)

    body = get_body_text(page, 2000)
    raise Exception(f"Activity 검색 화면 진입을 확인하지 못했습니다. 현재 화면 일부: {body[:160]}")


def go_activity_search_hard(page, reason=''):
    if reason:
        _v(f"[진행] Activity 검색 화면 강제 복귀: {reason}")
    close_open_drawers_or_popups(page)
    if is_activity_search_screen(page):
        remember_activity_search_url(page)
        _v("[완료] 이미 Activity 검색 화면")
        return True
    try:
        goto_activity_management_activity(page)
        remember_activity_search_url(page)
        return True
    except Exception as e:
        print(f"[주의] Activity 메뉴 방식 복귀 실패: {e}")
    try:
        from urllib.parse import urlparse
        origin = f"{urlparse(page.url).scheme}://{urlparse(page.url).netloc}"
        candidates = [
            origin + "/mspa/experiencesadmincommon/act/activity/list?lang=en_US",
            origin + "/mspa/experiencesadmincommon/act/activity/list",
        ]
        for url in candidates:
            try:
                _v(f"[진행] Activity 검색 후보 URL 이동: {url}")
                page.goto(url, wait_until='domcontentloaded', timeout=12000)
                page.wait_for_timeout(1200)
                if is_activity_search_screen(page):
                    remember_activity_search_url(page)
                    _v("[완료] 후보 URL Activity 검색 화면 복귀 완료")
                    return True
            except Exception:
                continue
    except Exception:
        pass
    return False


def fill_activity_id(page, activity_id):
    _v(f"[진행] Activity ID & title 입력: {activity_id}")
    try:
        focused = bool(page.evaluate("""() => {
 function norm(s){ return (s || '').replace(/\\s+/g, ' ').trim(); }
 function visible(el){
 const r = el.getBoundingClientRect();
 const st = window.getComputedStyle(el);
 return st.display !== 'none' && st.visibility !== 'hidden' && Number(st.opacity) !== 0 && r.width > 0 && r.height > 0;
 }
 const labels = Array.from(document.querySelectorAll('label,div,span'))
 .map(el => ({el, text:norm(el.innerText || el.textContent || ''), r:el.getBoundingClientRect()}))
 .filter(o => o.text.includes('Activity ID & title') && o.r.y >= 50 && o.r.y <= 190)
 .sort((a,b) => a.r.y - b.r.y || a.r.x - b.r.x);
 const label = labels[0];
 if (!label) return false;
 const inputs = Array.from(document.querySelectorAll('input'))
 .map(el => ({el, r:el.getBoundingClientRect()}))
 .filter(o => visible(o.el) && o.r.y >= label.r.y - 25 && o.r.y <= label.r.y + 60 && o.r.x > label.r.x)
 .sort((a,b) => a.r.x - b.r.x);
 if (!inputs.length) return false;
 const target = inputs[0].el;
 target.focus();
 target.click();
 return true;
 }"""))
        if focused:
            page.wait_for_timeout(150)
            page.keyboard.press('Control+A')
            page.keyboard.press('Backspace')
            page.keyboard.type(str(activity_id), delay=30)
            page.wait_for_timeout(300)
            _v("[완료] Activity ID 입력 완료 (JS click)")
            return True
    except Exception as e:
        print(f"[주의] Activity ID JS 입력 실패: {e}")

    for selector in [
        "xpath=//*[contains(normalize-space(), 'Activity ID & title')]/following::input[1]",
        "input[placeholder='Please enter']",
        "input[placeholder*='Please enter']",
        "input[type='text']",
    ]:
        try:
            locs = page.locator(selector)
            for i in range(locs.count()):
                inp = locs.nth(i)
                try:
                    if not inp.is_visible():
                        continue
                    box = inp.bounding_box(timeout=800)
                    if box and not (50 <= box['y'] <= 200):
                        continue
                    _click_safe(inp, timeout=1500)
                    page.keyboard.press('Control+A')
                    page.keyboard.press('Backspace')
                    page.keyboard.type(str(activity_id), delay=30)
                    page.wait_for_timeout(300)
                    _v("[완료] Activity ID 입력 완료")
                    return True
                except Exception:
                    continue
        except Exception:
            continue
    return False


def search_activity(page, activity_id):
    _v(f"[진행] Activity 검색: {activity_id}")
    goto_activity_management_activity(page)

    try:
        try:
            page.get_by_text('Reset', exact=False).click(timeout=1000)
        except Exception:
            page.get_by_text('Reset', exact=False).click(timeout=800, force=True)
        page.wait_for_timeout(400)
    except Exception:
        pass

    if not fill_activity_id(page, activity_id):
        raise Exception('Activity ID & title 입력칸을 찾지 못했습니다.')
    if not click_search_button(page):
        raise Exception('Activity 검색 Search 버튼을 찾지 못했습니다.')

    try:
        page.wait_for_function(
            """(aid) => {
 const text = (document.body ? document.body.innerText : '').replace(/\s+/g, ' ').trim();
 if (text.includes(String(aid) + ' -') || text.includes(String(aid))) return true;
 return Array.from(document.querySelectorAll('a[href]')).some(a => {
 const href = a.getAttribute('href') || a.href || '';
 const t = (a.innerText || a.textContent || '').replace(/\s+/g, ' ').trim();
 return href.includes(String(aid)) || t.startsWith(String(aid) + ' -');
 });
 }""",
            arg=str(activity_id),
            timeout=9000,
        )
    except Exception:
        page.wait_for_timeout(800)

    body = get_body_text(page, 2500)
    if str(activity_id) not in body:
        raise Exception(f"검색 결과에서 Activity ID {activity_id}를 확인하지 못했습니다.")
    remember_activity_search_url(page)
    _v(f"[완료] Activity 검색 결과 확인: {activity_id}")


def open_activity_detail(page, activity_id):
    _v(f"[진행] Activity 결과 링크 클릭: {activity_id}")
    page.wait_for_timeout(600)
    old_url = page.url
    context = page.context
    old_pages = list(context.pages)

    def is_activity_detail_page():
        body = get_body_text(page, 2500)
        if is_activity_search_screen(page):
            return False
        detail_keywords = [
            'Product settings', 'Basic info', 'Start & end', 'Itinerary',
            'Participant info', 'Inventory schedule', 'Unit type', 'Rules setting',
            'Status: Published', 'Approval status', 'Unpublish', 'Preview'
        ]
        return any(k in body for k in detail_keywords)

    def wait_after_move(timeout_ms=9000):
        deadline = datetime.now().timestamp() + timeout_ms / 1000
        while datetime.now().timestamp() < deadline:
            new_pages = list(context.pages)
            if len(new_pages) > len(old_pages):
                new_page = new_pages[-1]
                try:
                    new_page.bring_to_front()
                except Exception:
                    pass
                try:
                    new_page.wait_for_load_state('domcontentloaded', timeout=6000)
                except Exception:
                    pass
                return new_page
            if page.url != old_url or is_activity_detail_page():
                return page
            page.wait_for_timeout(250)
        return page

    info = page.evaluate(
        """(aid) => {
 function norm(s){return (s||'').replace(/\s+/g,' ').trim();}
 function visible(el){
 const r = el.getBoundingClientRect();
 const st = getComputedStyle(el);
 return st.display !== 'none' && st.visibility !== 'hidden' && Number(st.opacity) !== 0 &&
 r.width > 0 && r.height > 0 && r.bottom >= 0 && r.top <= (innerHeight || 900) + 500;
 }
 function absHref(href){ if(!href) return ''; try {return new URL(href, location.href).href;} catch(e) {return href;} }
 const prefix = String(aid) + ' -';
 const anchors = Array.from(document.querySelectorAll('a[href]'))
 .map(a => {
 const r = a.getBoundingClientRect();
 const text = norm(a.innerText || a.textContent || a.getAttribute('title') || '');
 const href = absHref(a.getAttribute('href') || a.href || '');
 let score = 0;
 if (text.startsWith(prefix)) score += 100;
 if (href.includes(String(aid))) score += 70;
 if (href.includes('/activity') || href.includes('/act/')) score += 30;
 if (visible(a)) score += 20;
 if (r.y >= 200) score += 8;
 return {text, href, x:r.x, y:r.y, w:r.width, h:r.height, visible:visible(a), score};
 })
 .filter(o => o.href && (o.text.startsWith(prefix) || o.href.includes(String(aid))))
 .sort((a,b) => b.score - a.score || a.y - b.y || a.x - b.x);
 if (anchors.length) return anchors[0];

 const nodes = Array.from(document.querySelectorAll('td,span,div,button,[role="link"],[role="button"]'))
 .map(el => {
 const r = el.getBoundingClientRect();
 const text = norm(el.innerText || el.textContent || '');
 return {text, x:r.x, y:r.y, w:r.width, h:r.height, visible:visible(el)};
 })
 .filter(o => o.visible && o.text.startsWith(prefix) && o.y >= 200 && o.w >= 20 && o.h >= 12)
 .sort((a,b) => a.y - b.y || a.x - b.x);
 return nodes[0] || null;
 }""",
        str(activity_id),
    )

    if not info:
        raise Exception(f"검색 결과에서 '{activity_id} -' Activity 링크/href를 찾지 못했습니다.")

    _v(f"[진행] Activity 링크 후보: {info.get('text','')[:120]} / href={info.get('href','')[:100]}")
    href = info.get('href') or ''
    if href:
        try:
            page.goto(href, wait_until='domcontentloaded', timeout=15000)
            page.wait_for_timeout(1400)
            if is_activity_detail_page() or page.url != old_url:
                _v("[완료] Activity 상세 화면 진입")
                return
        except Exception as e:
            print(f"[주의] Activity href 직접 이동 실패 → 좌표 클릭: {e}")

 # JS로 직접 element 다시 찾아 click 호출 (좌표 미사용)
    try:
        _v("[진행] Activity 링크 JS 클릭 시도")
        clicked = bool(page.evaluate(
            """(aid) => {
 function norm(s){return (s||'').replace(/\\s+/g,' ').trim();}
 function visible(el){
 const r = el.getBoundingClientRect();
 const st = getComputedStyle(el);
 return st.display !== 'none' && st.visibility !== 'hidden' && Number(st.opacity) !== 0 &&
 r.width > 0 && r.height > 0 && r.bottom >= 0 && r.top <= (innerHeight || 900) + 500;
 }
 function absHref(href){ if(!href) return ''; try {return new URL(href, location.href).href;} catch(e) {return href;} }
 const prefix = String(aid) + ' -';
 const anchors = Array.from(document.querySelectorAll('a[href]'))
 .map(a => {
 const r = a.getBoundingClientRect();
 const text = norm(a.innerText || a.textContent || a.getAttribute('title') || '');
 const href = absHref(a.getAttribute('href') || a.href || '');
 let score = 0;
 if (text.startsWith(prefix)) score += 100;
 if (href.includes(String(aid))) score += 70;
 if (href.includes('/activity') || href.includes('/act/')) score += 30;
 if (visible(a)) score += 20;
 if (r.y >= 200) score += 8;
 return {el:a, score};
 })
 .filter(o => o.score >= 70)
 .sort((a,b) => b.score - a.score);
 if (anchors.length) { anchors[0].el.click(); return true; }

 const nodes = Array.from(document.querySelectorAll('td,span,div,button,[role="link"],[role="button"]'))
 .map(el => {
 const r = el.getBoundingClientRect();
 const text = norm(el.innerText || el.textContent || '');
 return {el, text, r};
 })
 .filter(o => visible(o.el) && o.text.startsWith(prefix) && o.r.y >= 200 && o.r.width >= 20 && o.r.height >= 12)
 .sort((a,b) => a.r.y - b.r.y || a.r.x - b.r.x);
 if (nodes.length) { nodes[0].el.click(); return true; }
 return false;
 }""",
            str(activity_id),
        ))
        if clicked:
            new_page = wait_after_move(timeout_ms=7000)
            if new_page is not page:
                page = new_page
            if is_activity_detail_page() or page.url != old_url:
                _v("[완료] Activity 상세 화면 진입 (JS click)")
                return
    except Exception as e:
        print(f"[주의] Activity 링크 JS 클릭 실패: {e}")
    raise Exception(f"'{activity_id} -' Activity 링크 클릭 후에도 상세 화면으로 이동하지 못했습니다.")


def goto_inventory_schedule_section(page):
    """
 Activity 신버전 상세 화면에서 왼쪽 Product settings > Inventory schedule로 이동.

 수정:
 - 왼쪽 메뉴 클릭이 먹지 않는 경우를 대비해 URL 직접 이동 후보를 시도합니다.
 - URL 이동도 실패하면 Save and continue 버튼으로 다음 섹션을 순차 이동합니다.
 - 확인 로직이 실패하더라도 바로 작업을 중단하지 않고 Adult 항목 탐색 단계로 넘길 수 있게 False를 반환합니다.
    """
    _v("[진행] Product settings > Inventory schedule 클릭")
    try:
        page.wait_for_load_state('domcontentloaded', timeout=4000)
    except Exception:
        pass

    def inventory_content_visible():
        try:
            return bool(page.evaluate(
                """() => {
 function norm(s){return (s||'').replace(/\s+/g,' ').trim();}
 function visible(el){
 if(!el) return false;
 const r = el.getBoundingClientRect();
 const st = getComputedStyle(el);
 return st.display !== 'none' && st.visibility !== 'hidden' && Number(st.opacity) !== 0 &&
 r.width > 0 && r.height > 0 && r.bottom >= 0 && r.top <= (innerHeight || 900);
 }
 const body = norm(document.body ? (document.body.innerText || document.body.textContent || '') : '');

 // 캘린더/스케줄 관리 화면까지 이미 들어온 경우
 if (body.includes('Bulk edit price / inventory') && body.includes('Items to filter')) return true;
 if (body.includes('Price / Inventory Settings') && body.includes('Manage schedule status')) return true;
 if (body.includes('Price / Inventory Settings') && body.includes('Schedule:')) return true;
 if (body.includes('Create item') && body.includes('Item list')) return true;
 if (body.includes('Item list') && body.includes('Price / Inventory Settings')) return true;

 const nodes = Array.from(document.querySelectorAll('h1,h2,h3,h4,div,span,section,main,button'))
 .map(el => { const r=el.getBoundingClientRect(); return {el, text:norm(el.innerText||el.textContent||''), x:r.x, y:r.y, w:r.width, h:r.height}; })
 .filter(o => visible(o.el) && o.x >= 150 && o.y >= 50);

 // Inventory schedule 섹션 고유 텍스트
 if (nodes.some(o => o.text === 'Create item' || /^Item list\s*\(/i.test(o.text) || o.text === 'Item list')) return true;
 if (nodes.some(o => o.text === 'Schedule' && o.w >= 50 && o.h >= 12)) return true;
 if (nodes.some(o => o.text === 'Price / Inventory Settings')) return true;
 if (nodes.some(o => o.text === 'Inventory schedule' && o.x >= 240 && o.w >= 80)) return true;
 return false;
 }"""
            ))
        except Exception:
            body = get_body_text(page, 2000)
            return ('Create item' in body and 'Item list' in body) or ('Bulk edit price / inventory' in body) or ('Price / Inventory Settings' in body)

    def find_left_inventory_menu_point():
        try:
            return page.evaluate(
                """() => {
 function norm(s){return (s||'').replace(/\s+/g,' ').trim();}
 function visible(el){
 if(!el) return false;
 const r = el.getBoundingClientRect();
 const st = getComputedStyle(el);
 return st.display !== 'none' && st.visibility !== 'hidden' && Number(st.opacity) !== 0 &&
 r.width > 0 && r.height > 0 && r.bottom >= 0 && r.top <= (innerHeight || 900);
 }
 const raw = Array.from(document.querySelectorAll('a,button,div,span,li,[role="button"],[role="menuitem"]'))
 .map(el => {
 const r = el.getBoundingClientRect();
 return {el, text:norm(el.innerText||el.textContent||''), x:r.x, y:r.y, w:r.width, h:r.height};
 })
 .filter(o => visible(o.el) && o.text === 'Inventory schedule' && o.x <= 300)
 .sort((a,b) => a.y - b.y || a.x - b.x);
 if (!raw.length) return null;
 const n = raw[0];
 try { n.el.scrollIntoView({block:'center', inline:'nearest'}); } catch(e) {}

 // 클릭 핸들러가 span이 아니라 상위 div에 걸려있는 경우가 있어 여러 ancestor를 후보로 둡니다.
 const ancestors = [];
 let cur = n.el;
 for (let depth=0; depth<8 && cur; depth++, cur=cur.parentElement) {
 try {
 const r = cur.getBoundingClientRect();
 const text = norm(cur.innerText || cur.textContent || '');
 if (r.width > 10 && r.height > 10 && r.x <= 320 && text.includes('Inventory schedule')) {
 const href = cur.getAttribute && (cur.getAttribute('href') || cur.getAttribute('data-href') || cur.getAttribute('data-url') || cur.getAttribute('to') || '');
 ancestors.push({x:r.x, y:r.y, w:r.width, h:r.height, text:text.slice(0,120), href:href || '', tag:cur.tagName});
 }
 } catch(e) {}
 }
 ancestors.sort((a,b) => {
 // 너무 큰 wrapper보다 메뉴 한 줄 높이에 가까운 요소 우선
 const as = Math.abs(a.h - 48) + (a.w > 260 ? 30 : 0);
 const bs = Math.abs(b.h - 48) + (b.w > 260 ? 30 : 0);
 return as - bs;
 });
 const c = ancestors[0] || {x:n.x, y:n.y, w:n.w, h:n.h, text:n.text, href:'', tag:'UNKNOWN'};
 return {
 x: Math.max(8, Math.min((innerWidth||1920)-8, c.x + Math.min(Math.max(c.w/2, 30), Math.max(30, c.w-12)))),
 y: Math.max(8, Math.min((innerHeight||900)-8, c.y + c.h/2)),
 text: c.text,
 href: c.href,
 tag: c.tag
 };
 }"""
            )
        except Exception:
            return None

    def js_click_left_inventory_menu():
        try:
            return bool(page.evaluate(
                """() => {
 function norm(s){return (s||'').replace(/\s+/g,' ').trim();}
 function visible(el){
 if(!el) return false;
 const r = el.getBoundingClientRect();
 const st = getComputedStyle(el);
 return st.display !== 'none' && st.visibility !== 'hidden' && Number(st.opacity) !== 0 &&
 r.width > 0 && r.height > 0 && r.bottom >= 0 && r.top <= (innerHeight || 900);
 }
 const nodes = Array.from(document.querySelectorAll('a,button,div,span,li,[role="button"],[role="menuitem"]'))
 .filter(el => {
 const r = el.getBoundingClientRect();
 return visible(el) && norm(el.innerText||el.textContent||'') === 'Inventory schedule' && r.x <= 300;
 });
 if (!nodes.length) return false;
 const el = nodes[0];
 try { el.scrollIntoView({block:'center', inline:'nearest'}); } catch(e) {}
 const targets = [];
 let cur = el;
 for (let depth=0; depth<8 && cur; depth++, cur=cur.parentElement) {
 try {
 const r = cur.getBoundingClientRect();
 const t = norm(cur.innerText || cur.textContent || '');
 if (r.width > 10 && r.height > 10 && r.x <= 320 && t.includes('Inventory schedule')) targets.push(cur);
 } catch(e) {}
 }
 for (const target of targets) {
 try { target.focus && target.focus(); } catch(e) {}
 try { target.click && target.click(); } catch(e) {}
 try {
 ['pointerdown','mousedown','pointerup','mouseup','click'].forEach(type => {
 target.dispatchEvent(new MouseEvent(type, {bubbles:true, cancelable:true, view:window}));
 });
 } catch(e) {}
 }
 return true;
 }"""
            ))
        except Exception:
            return False

    def scroll_main_inventory_section_by_dom():
        """메인 콘텐츠 영역의 Inventory schedule 제목/섹션을 찾아 직접 이동합니다."""
        try:
            return page.evaluate(
                """() => {
 function norm(s){return (s||'').replace(/\s+/g,' ').trim();}
 function usable(el){
 if(!el) return false;
 const r = el.getBoundingClientRect();
 const st = getComputedStyle(el);
 return st.display !== 'none' && st.visibility !== 'hidden' && Number(st.opacity) !== 0 && r.width > 0 && r.height > 0;
 }
 const cands = Array.from(document.querySelectorAll('h1,h2,h3,h4,div,span,section'))
 .map(el => { const r=el.getBoundingClientRect(); return {el, text:norm(el.innerText||el.textContent||''), x:r.x, y:r.y, w:r.width, h:r.height}; })
 // 왼쪽 메뉴(x<=230)는 제외, 메인 영역만 대상
 .filter(o => usable(o.el) && o.text === 'Inventory schedule' && o.x >= 240)
 .sort((a,b) => a.y - b.y || a.x - b.x);
 if (!cands.length) return null;
 const n = cands[0];
 try { n.el.scrollIntoView({block:'start', inline:'nearest'}); } catch(e) {}
 try { window.scrollBy(0, -80); } catch(e) {}
 const r = n.el.getBoundingClientRect();
 return {x:r.x+r.width/2, y:r.y+r.height/2, text:n.text};
 }"""
            )
        except Exception:
            return None

    def try_direct_inventory_urls():
        """현재 basic URL에서 Inventory schedule에 해당할 가능성이 높은 URL slug를 직접 시도합니다."""
        old_url = page.url
        m = re.search(r'(/aid/tours/)([^/?#]+)(/)(\d+)', old_url)
        if not m:
            return False
        slugs = [
            'inventory-schedule', 'inventory_schedule', 'inventoryschedule',
            'inventory', 'schedule', 'schedules',
            'price-inventory', 'price_inventory', 'priceinventory',
            'inventory-schedule-list', 'inventorySchedule'
        ]
        tried = set()
        for slug in slugs:
            cand = old_url[:m.start(2)] + slug + old_url[m.end(2):]
            if cand in tried or cand == old_url:
                continue
            tried.add(cand)
            try:
                _v(f"[진행] Inventory schedule URL 직접 이동 시도: {slug}")
                page.goto(cand, wait_until='domcontentloaded', timeout=12000)
                page.wait_for_timeout(1800)
                if inventory_content_visible():
                    _v(f"[완료] Inventory schedule URL 직접 이동 성공: {slug}")
                    return True
                body = get_body_text(page, 2500)
                if ('Create item' in body and 'Item list' in body) or 'Price / Inventory Settings' in body:
                    _v(f"[완료] Inventory schedule URL 직접 이동 성공: {slug}")
                    return True
            except Exception as e:
                print(f"[주의] URL 직접 이동 실패({slug}): {e}")
        try:
            page.goto(old_url, wait_until='domcontentloaded', timeout=12000)
            page.wait_for_timeout(1200)
        except Exception:
            pass
        return False

    def click_save_and_continue_until_inventory(max_steps=12):
        """왼쪽 메뉴 이동이 안 될 때, 하단 Save and continue 버튼으로 섹션을 순차 이동합니다."""
        def click_save_continue_button():
            try:
                return page.evaluate(
                    """() => {
 function norm(s){return (s||'').replace(/\\s+/g,' ').trim();}
 function visible(el){
 if(!el) return false;
 const r = el.getBoundingClientRect();
 const st = getComputedStyle(el);
 return st.display !== 'none' && st.visibility !== 'hidden' && Number(st.opacity) !== 0 &&
 r.width > 0 && r.height > 0 && r.bottom >= 0 && r.top <= (innerHeight || 900);
 }
 const nodes = Array.from(document.querySelectorAll('button,a,div[role="button"],span'))
 .map(el => { const r=el.getBoundingClientRect(); return {el, text:norm(el.innerText||el.textContent||''), x:r.x, y:r.y, w:r.width, h:r.height}; })
 .filter(o => visible(o.el) && /^(Save and continue|Continue|Next)$/i.test(o.text) && o.w >= 40 && o.h >= 18)
 .sort((a,b) => b.y - a.y || b.x - a.x);
 if (!nodes.length) return null;
 const n = nodes[0];
 const target = n.el.closest('button') || n.el.closest('a') || n.el.closest('[role="button"]') || n.el;
 target.click();
 return {text:n.text};
 }"""
                )
            except Exception:
                return None

        def scroll_to_bottom():
            try:
                page.evaluate("""() => {
 try { window.scrollTo(0, document.body.scrollHeight); } catch(e) {}
 try { document.scrollingElement && (document.scrollingElement.scrollTop = document.scrollingElement.scrollHeight); } catch(e) {}
 for (const el of Array.from(document.querySelectorAll('main,section,article,div'))) {
 try {
 const r = el.getBoundingClientRect();
 if (r.x >= 120 && el.scrollHeight > el.clientHeight + 80) el.scrollTop = el.scrollHeight;
 } catch(e) {}
 }
 }""")
                page.mouse.wheel(0, 900)
            except Exception:
                pass

        for step in range(1, max_steps + 1):
            if inventory_content_visible():
                _v("[완료] Inventory schedule 메인 콘텐츠 확인")
                return True
            btn = click_save_continue_button()
            if not btn:
                scroll_to_bottom()
                page.wait_for_timeout(400)
                btn = click_save_continue_button()
            if not btn:
                print("[주의] Save and continue 버튼을 찾지 못했습니다.")
                return False
            try:
                _v(f"[진행] Save and continue 순차 이동 {step}/{max_steps} (JS click): {btn.get('text','')}")
                before_url = page.url
                page.wait_for_timeout(1800)
                try:
                    page.wait_for_load_state('domcontentloaded', timeout=5000)
                except Exception:
                    pass
                if inventory_content_visible():
                    _v("[완료] Inventory schedule 메인 콘텐츠 확인")
                    return True
 # 클릭 후 같은 위치에 머무르면 한 번 더 아래쪽 버튼을 찾을 수 있도록 스크롤 초기화
                if page.url == before_url:
                    page.wait_for_timeout(500)
            except Exception as e:
                print(f"[주의] Save and continue 클릭 실패: {e}")
                return False
        return False

 # 이미 해당 섹션 또는 캘린더 화면이면 바로 통과
    if inventory_content_visible():
        _v("[완료] Inventory schedule 메인 콘텐츠 확인")
        return True

 # 1) 왼쪽 메뉴 항목을 찾고, JS click을 시도합니다 (좌표 미사용).
    clicked = False
    for attempt in range(1, 12):
        point = find_left_inventory_menu_point()
        if point:
            clicked = True
            _v(f"[진행] Inventory schedule 메뉴 클릭 (JS click): tag={point.get('tag','')}")
 # JS로 직접 element 클릭 (좌표 미사용)
            js_click_left_inventory_menu()
            page.wait_for_timeout(900)
            if inventory_content_visible():
                _v("[완료] Inventory schedule 메인 콘텐츠 확인")
                return True

 # 한 번 더 JS click 시도
            js_click_left_inventory_menu()
            page.wait_for_timeout(900)
            if inventory_content_visible():
                _v("[완료] Inventory schedule 메인 콘텐츠 확인")
                return True
            break

 # 왼쪽 사이드바가 자체 스크롤인 경우 아래로 내려서 메뉴 찾기
        try:
            page.evaluate("""() => {
 for (const el of Array.from(document.querySelectorAll('aside,nav,div'))) {
 try {
 const r = el.getBoundingClientRect();
 if (r.x <= 320 && el.scrollHeight > el.clientHeight + 30) el.scrollTop += 450;
 } catch(e) {}
 }
 }""")
        except Exception:
            pass
        page.wait_for_timeout(300)

 # 2) 메뉴 클릭으로 안 움직이면, 메인 DOM의 Inventory schedule 섹션으로 직접 스크롤합니다.
    for attempt in range(1, 8):
        moved = scroll_main_inventory_section_by_dom()
        if moved:
            _v(f"[진행] 메인 Inventory schedule 섹션 직접 이동: x={int(moved['x'])}, y={int(moved['y'])}")
            page.wait_for_timeout(700)
            if inventory_content_visible():
                _v("[완료] Inventory schedule 메인 콘텐츠 확인")
                return True
        try:
            amount = 750 if attempt >= 3 else 450
            page.evaluate("""(amount) => {
 try { window.scrollBy(0, amount); } catch(e) {}
 try { document.scrollingElement && (document.scrollingElement.scrollTop += amount); } catch(e) {}
 for (const el of Array.from(document.querySelectorAll('main,section,article,div'))) {
 try {
 const r = el.getBoundingClientRect();
 if (r.x >= 120 && el.scrollHeight > el.clientHeight + 80) el.scrollTop += amount;
 } catch(e) {}
 }
 }""", amount)
            page.mouse.wheel(0, amount)
        except Exception:
            pass
        page.wait_for_timeout(400)
        if inventory_content_visible():
            _v("[완료] Inventory schedule 메인 콘텐츠 확인")
            return True

 # 3) URL slug 직접 이동 후보 시도
    if try_direct_inventory_urls():
        return True

 # 4) 마지막 fallback: Save and continue를 눌러 섹션을 순차 이동
    if click_save_and_continue_until_inventory(max_steps=12):
        return True

    body = get_body_text(page, 1800)
    print(f"[주의] Inventory schedule 진입 확인 실패. Adult 항목 탐색을 계속 시도합니다. 현재 화면 일부: {body[:220]}")
    return False


def select_published_package(page):
    """
 Activity 상세 페이지 좌측 사이드바에서 Published 상태의 패키지를 선택.

 - HTML 마커: <i class="common-pkg-status-circle published"> = 초록, "unpublished" = 회색
 - 좌측 사이드바의 패키지 카드(.panel-menu-wrap) 중 published 동그라미가 있는 것만 후보
 - Published 가 정확히 1개 → 그 카드의 텍스트 영역 클릭 (+ 버튼 영역 피함)
 - Published 가 0개 → 에러
 - Published 가 2개 이상 → 에러 (수동 처리 필요)

 이 함수는 Activity 워크플로우에만 사용. Package 워크플로우는 영향 없음.
    """
    _v("[진행] 좌측 사이드바에서 Published 패키지 선택")

 # Klook UI 가 완전히 로드될 때까지 잠시 대기
    page.wait_for_timeout(800)

    def find_published_packages():
        """좌측 사이드바의 패키지 카드들을 조사. Published/Unpublished 메타데이터 반환."""
        try:
            return page.evaluate(
                """() => {
 function norm(s){return (s||'').replace(/\\s+/g,' ').trim();}
 function visible(el){
 if(!el) return false;
 const r = el.getBoundingClientRect();
 const st = getComputedStyle(el);
 return st.display !== 'none' && st.visibility !== 'hidden' && Number(st.opacity) !== 0 &&
 r.width > 0 && r.height > 0;
 }
 const cards = Array.from(document.querySelectorAll('.panel-menu-wrap'));
 const all = [];
 const published = [];
 const unpublished = [];
 for (const card of cards) {
 if (!visible(card)) continue;
 const r = card.getBoundingClientRect();
 // 좌측 사이드바만 (x 작은 영역)
 if (r.x > 350) continue;
 const publishedDot = card.querySelector('.common-pkg-status-circle.published');
 const unpublishedDot = card.querySelector('.common-pkg-status-circle.unpublished');
 const titleEl = card.querySelector('.pkg-text .common-tooltip-style');
 const title = titleEl ? norm(titleEl.innerText || titleEl.textContent || '') : '';
 const info = {title: title};
 all.push(info);
 if (publishedDot) published.push(info);
 else if (unpublishedDot) unpublished.push(info);
 }
 return {all: all, published: published, unpublished: unpublished};
 }"""
            )
        except Exception as e:
            print(f"[주의] 패키지 카드 조사 실패: {e}")
            return None

    result = find_published_packages()
    if not result:
        _v("[안내] 좌측 사이드바 패키지 카드를 찾지 못함. Activity 단일 패키지 상품으로 추정, 패키지 선택 단계 생략")
        return  # 사이드바가 아예 없는 단일 패키지 상품은 그냥 통과

    total = len(result.get('all', []))
    published = result.get('published', [])
    unpublished = result.get('unpublished', [])

    _v(f"[진행] 좌측 사이드바 패키지 카드: 총 {total}개 (Published {len(published)}, Unpublished {len(unpublished)})")

    if total == 0:
 # 사이드바가 없는 상품 = 단일 패키지 = 패키지 선택 단계 불필요
        _v("[안내] 패키지 카드 없음. 단일 패키지 상품으로 추정, 선택 단계 생략")
        return

    if len(published) == 0:
        raise Exception(f"Published 패키지가 없습니다 (총 {total}개 카드, Unpublished {len(unpublished)}). 수동 확인 필요.")

    if len(published) > 1:
        titles = ' / '.join(p.get('title', '')[:40] for p in published)
        raise Exception(f"Published 패키지가 {len(published)}개입니다 (수동 처리 필요): {titles}")

 # Published 정확히 1개 → JS click (좌표 미사용)
    target = published[0]
    _v(f"[진행] Published 패키지 클릭 (JS click): '{target.get('title')}'")
    try:
        clicked = bool(page.evaluate(
            """() => {
 function norm(s){return (s||'').replace(/\\s+/g,' ').trim();}
 function visible(el){
 if(!el) return false;
 const r = el.getBoundingClientRect();
 const st = getComputedStyle(el);
 return st.display !== 'none' && st.visibility !== 'hidden' && Number(st.opacity) !== 0 &&
 r.width > 0 && r.height > 0;
 }
 const cards = Array.from(document.querySelectorAll('.panel-menu-wrap'));
 for (const card of cards) {
 if (!visible(card)) continue;
 const r = card.getBoundingClientRect();
 if (r.x > 350) continue;
 if (!card.querySelector('.common-pkg-status-circle.published')) continue;
 const titleEl = card.querySelector('.pkg-text .common-tooltip-style');
 const clickTarget = titleEl || card.querySelector('.pkg-text') || card;
 clickTarget.click();
 return true;
 }
 return false;
 }"""
        ))
        if not clicked:
            raise Exception("Published 패키지 element를 JS로 찾지 못했습니다.")
        page.wait_for_timeout(1200)
    except Exception as e:
        raise Exception(f"Published 패키지 클릭 실패: {e}")

    _v("[완료] Published 패키지 선택")


def goto_inventory_schedule_section_v37(page):
    """
 새버전 Activity 전용 Inventory schedule 진입.
 - 잘못된 URL slug 후보 직접 이동을 사용하지 않습니다. Page 404 방지.
 - 현재 상세 화면 DOM에 있는 Inventory schedule 메뉴의 실제 href/data-url을 우선 사용합니다.
 - href가 없으면 왼쪽 메뉴 항목을 실제 마우스/JS 이벤트로 클릭합니다.
 - 마지막에는 Save and continue 순차 이동을 시도합니다.
    """
    print("[v49-canary] goto_inventory_schedule_section_v37 진입")
    _v("[진행] 새버전 전용 Inventory schedule 진입")
    try:
        page.wait_for_load_state('domcontentloaded', timeout=4000)
    except Exception:
        pass
    detail_url = page.url
    print(f"[v49-canary] 진입 시 URL: {detail_url}")

    # 1순위 우선 시도: URL path 직접 변경 (basic/itinerary/photos/policies/included/restrictions/participant-info/unit-type/rules → inventory-schedule)
    # 사이드바 메뉴가 회색/비활성이라 클릭 안 되는 케이스 (Basic info 미완성 활동) 회피
    try:
        import re as _re
        m = _re.search(r'(/aid/tours/)(basic|itinerary|photos|policies|included|restrictions|participant-info|unit-type|rules|start-end)(/[^?]+)', detail_url)
        if m:
            new_url = detail_url[:m.start(2)] + 'inventory-schedule' + m.group(3) + detail_url[m.end():]
            print(f"[v49-canary] URL path 직접 변경 시도: {new_url}")
            try:
                page.goto(new_url, wait_until='domcontentloaded', timeout=12000)
                # SPA 본문 hydration 대기 — 최대 10초 동안 polling
                import time as _time
                deadline = _time.perf_counter() + 10.0
                last_body_sample = ''
                while _time.perf_counter() < deadline:
                    page.wait_for_timeout(500)
                    body = get_body_text(page, 1200)
                    last_body_sample = body[:120] if body else ''
                    if 'Item list' in body or 'Create item' in body or 'Price / Inventory Settings' in body:
                        print(f"[v49-canary] URL 직접 변경 성공 — Inventory schedule 본문 확인 ({_time.perf_counter():.1f}s)")
                        return True
                print(f"[v49-canary] URL 직접 변경 후 10초 대기 했지만 본문 안 뜸: {last_body_sample}")
            except Exception as e:
                print(f"[v49-canary] URL 직접 변경 실패: {e}")
    except Exception as e:
        print(f"[v49-canary] URL path 변경 로직 에러: {e}")

    def body_text(timeout_ms=1500):
        return get_body_text(page, timeout_ms)

    def is_404_page():
        text = body_text(1200)
        return ('Page 404' in text) or ('OOPS!' in text and '404' in text) or ('Back to home' in text and '404' in text)

    def recover_from_404():
        if not is_404_page():
            return True
        print('[주의] 404 화면 감지 → Activity 상세 화면으로 복구 시도')
        for _ in range(2):
            try:
                page.go_back(wait_until='domcontentloaded', timeout=8000)
                page.wait_for_timeout(1200)
                if not is_404_page():
                    return True
            except Exception:
                pass
        try:
            if detail_url and detail_url.startswith('http'):
                page.goto(detail_url, wait_until='domcontentloaded', timeout=12000)
                page.wait_for_timeout(1200)
                return not is_404_page()
        except Exception:
            pass
        return False

    def inventory_visible():
        if is_404_page():
            return False
        try:
            return bool(page.evaluate(
                """() => {
 function norm(s){return (s||'').replace(/\s+/g,' ').trim();}
 function visible(el){
 if(!el) return false;
 const r = el.getBoundingClientRect();
 const st = getComputedStyle(el);
 return st.display !== 'none' && st.visibility !== 'hidden' && Number(st.opacity) !== 0 &&
 r.width > 0 && r.height > 0 && r.bottom >= -200 && r.top <= (innerHeight || 900) + 900;
 }
 const body = norm(document.body ? (document.body.innerText || document.body.textContent || '') : '');
 if (body.includes('Page 404') || (body.includes('OOPS!') && body.includes('404'))) return false;
 if (body.includes('Bulk edit price / inventory') && body.includes('Items to filter')) return true;
 if (body.includes('Price / Inventory Settings') && (body.includes('Schedule:') || body.includes('Manage schedule status'))) return true;
 if (body.includes('Create item') && body.includes('Item list')) return true;
 if (body.includes('Item list') && body.includes('Price / Inventory Settings')) return true;
 const nodes = Array.from(document.querySelectorAll('h1,h2,h3,h4,div,span,section,main,button'))
 .map(el => { const r=el.getBoundingClientRect(); return {el, text:norm(el.innerText||el.textContent||''), x:r.x, y:r.y, w:r.width, h:r.height}; })
 .filter(o => visible(o.el) && o.x >= 150);
 // 본문 키워드 — 실제 Inventory schedule 화면임을 보장 (사이드바 메뉴 visible 만으로는 부족)
 if (nodes.some(o => o.text === 'Create item' || /^Item list\s*\(/i.test(o.text) || o.text === 'Item list')) return true;
 if (nodes.some(o => o.text === 'Price / Inventory Settings')) return true;
 // Adult/Person 라벨이 본문 영역(x>=300)에 보이면 OK (Item list 가 이미 펼쳐진 상태)
 if (nodes.some(o => (o.text === 'Adult' || o.text === 'Person') && o.x >= 300)) return true;
 return false;
 }"""
            ))
        except Exception:
            text = body_text(1200)
            if 'Page 404' in text or 'OOPS!' in text:
                return False
            return ('Create item' in text and 'Item list' in text) or ('Price / Inventory Settings' in text) or ('Bulk edit price / inventory' in text)

    def wait_inventory_visible(timeout_ms=5000):
        import time as _time
        end = _time.perf_counter() + timeout_ms / 1000
        while _time.perf_counter() < end:
            recover_from_404()
            if inventory_visible():
                return True
            page.wait_for_timeout(300)
        return False

    def reset_sidebar_scroll():
        try:
            page.evaluate("""() => {
 try { window.scrollTo(0, 0); } catch(e) {}
 try { document.scrollingElement && (document.scrollingElement.scrollTop = 0); } catch(e) {}
 for (const el of Array.from(document.querySelectorAll('aside,nav,div'))) {
 try {
 const r = el.getBoundingClientRect();
 if (r.x <= 340 && el.scrollHeight > el.clientHeight + 30) el.scrollTop = 0;
 } catch(e) {}
 }
 }""")
        except Exception:
            pass

    def get_inventory_candidates():
        try:
            return page.evaluate(
                """() => {
 function norm(s){return (s||'').replace(/\s+/g,' ').trim();}
 function visible(el){
 if(!el) return false;
 const r = el.getBoundingClientRect();
 const st = getComputedStyle(el);
 return st.display !== 'none' && st.visibility !== 'hidden' && Number(st.opacity) !== 0 &&
 r.width > 0 && r.height > 0 && r.bottom >= -100 && r.top <= (innerHeight || 900) + 300;
 }
 function abs(h){ if(!h) return ''; try { return new URL(h, location.href).href; } catch(e) { return h; } }
 function hrefOf(el){
 let cur = el;
 for (let depth=0; depth<9 && cur; depth++, cur=cur.parentElement) {
 try {
 const href = cur.getAttribute && (
 cur.getAttribute('href') || cur.getAttribute('data-href') || cur.getAttribute('data-url') ||
 cur.getAttribute('data-route') || cur.getAttribute('to') || cur.getAttribute('data-to') || ''
 );
 if (href && !String(href).startsWith('javascript')) return abs(href);
 if (cur.tagName && cur.tagName.toLowerCase() === 'a' && cur.href) return abs(cur.href);
 } catch(e) {}
 }
 return '';
 }
 const raw = Array.from(document.querySelectorAll('a,button,div,span,li,[role="button"],[role="menuitem"]'))
 .map(el => {
 const r = el.getBoundingClientRect();
 const text = norm(el.innerText || el.textContent || el.getAttribute('aria-label') || el.getAttribute('title') || '');
 return {el, text, x:r.x, y:r.y, w:r.width, h:r.height, href:hrefOf(el)};
 })
 .filter(o => visible(o.el) && o.text === 'Inventory schedule' && o.x <= 360)
 .sort((a,b) => a.y - b.y || a.x - b.x);
 const rows = [];
 for (const n of raw) {
 let best = null;
 let cur = n.el;
 for (let depth=0; depth<9 && cur; depth++, cur=cur.parentElement) {
 try {
 const r = cur.getBoundingClientRect();
 const t = norm(cur.innerText || cur.textContent || '');
 if (r.width > 20 && r.height > 12 && r.x <= 360 && t.includes('Inventory schedule')) {
 const score = Math.abs(r.height - 44) + (r.width > 330 ? 40 : 0) + (r.x > 260 ? 20 : 0);
 const href = hrefOf(cur) || n.href || '';
 const cand = {x:r.x+r.width/2, y:r.y+r.height/2, w:r.width, h:r.height, text:t.slice(0,120), href, score, tag:cur.tagName || ''};
 if (!best || cand.score < best.score) best = cand;
 }
 } catch(e) {}
 }
 const r = n.el.getBoundingClientRect();
 rows.push(best || {x:r.x+r.width/2, y:r.y+r.height/2, w:r.width, h:r.height, text:n.text, href:n.href || '', score:999, tag:n.el.tagName || ''});
 }
 const seen = new Set();
 return rows.filter(o => {
 const key = Math.round(o.x) + ':' + Math.round(o.y) + ':' + (o.href || '');
 if (seen.has(key)) return false;
 seen.add(key);
 return true;
 }).sort((a,b) => a.score - b.score || a.y - b.y || a.x - b.x).slice(0,8);
 }"""
            ) or []
        except Exception:
            return []

    def js_click_inventory_menu():
        """: .menus-title 텍스트로 라벨 찾고 → .custom-header-box (실제 navigable 컨테이너) click.

 Klook 신버전 Activity 좌측 사이드바 DOM (2026-05 확인):
 <div class="custom-header-box finished-style"> ← 클릭 타겟
 <span class="menus-title">
 <span class="ant-form-item-required"></span>
 Inventory schedule
 </span>
 </div>

 - .menus-title 자체 click 은 navigate 안 됨
 - .custom-header-box click 시 SPA route 가 /aid/tours/inventory-schedule/{id} 로 이동
        """
        try:
            return bool(page.evaluate(
                """() => {
 function norm(s){return (s||'').replace(/\\s+/g,' ').trim();}
 function visible(el){
 if(!el) return false;
 const r = el.getBoundingClientRect();
 const st = getComputedStyle(el);
 return st.display !== 'none' && st.visibility !== 'hidden' && Number(st.opacity) !== 0
 && r.width > 0 && r.height > 0;
 }

 // 1차: .menus-title 안에서 "Inventory schedule" 라벨 찾기
 let labelEl = Array.from(document.querySelectorAll('.menus-title, span.menus-title'))
 .find(el => visible(el) && norm(el.innerText || el.textContent || '') === 'Inventory schedule');

 // 2차 (fallback): 일반 텍스트 매칭
 if (!labelEl) {
 labelEl = Array.from(document.querySelectorAll('a,button,div,span,li,[role="button"],[role="menuitem"]'))
 .find(el => visible(el) && norm(el.innerText || el.textContent || '') === 'Inventory schedule' && el.getBoundingClientRect().x <= 360);
 }
 if (!labelEl) return false;

 // 3차: 클릭 가능한 컨테이너 찾기 (.custom-header-box 우선)
 const clickable =
 labelEl.closest('.custom-header-box') ||
 labelEl.closest('.ant-collapse-header') ||
 labelEl.closest('[role="menuitem"]') ||
 labelEl.closest('a, button, li') ||
 labelEl.parentElement || labelEl;

 try { clickable.scrollIntoView({block:'center', inline:'nearest'}); } catch(e) {}
 try { clickable.focus && clickable.focus(); } catch(e) {}
 // SPA route navigation 발화를 위해 모든 마우스 이벤트 dispatch + click
 for (const type of ['pointerover','mouseover','pointerdown','mousedown','pointerup','mouseup','click']) {
 try { clickable.dispatchEvent(new MouseEvent(type, {bubbles:true, cancelable:true, view:window})); } catch(e) {}
 }
 try { clickable.click(); } catch(e) {}
 return true;
 }"""
            ))
        except Exception:
            return False

    def click_save_continue_button():
        try:
            return page.evaluate(
                """() => {
 function norm(s){return (s||'').replace(/\\s+/g,' ').trim();}
 function visible(el){
 if(!el) return false;
 const r = el.getBoundingClientRect();
 const st = getComputedStyle(el);
 return st.display !== 'none' && st.visibility !== 'hidden' && Number(st.opacity) !== 0 &&
 r.width > 0 && r.height > 0 && r.bottom >= 0 && r.top <= (innerHeight || 900);
 }
 const nodes = Array.from(document.querySelectorAll('button,a,div[role="button"],span'))
 .map(el => { const r=el.getBoundingClientRect(); return {el, text:norm(el.innerText||el.textContent||''), x:r.x,y:r.y,w:r.width,h:r.height}; })
 .filter(o => visible(o.el) && /^(Save and continue|Continue|Next)$/i.test(o.text) && o.w >= 40 && o.h >= 18)
 .sort((a,b) => b.y - a.y || b.x - a.x);
 if (!nodes.length) return null;
 const n = nodes[0];
 const target = n.el.closest('button') || n.el.closest('a') || n.el.closest('[role="button"]') || n.el;
 target.click();
 return {text:n.text};
 }"""
            )
        except Exception:
            return None

    def scroll_bottom():
        try:
            page.evaluate("""() => {
 try { window.scrollTo(0, document.body.scrollHeight); } catch(e) {}
 try { document.scrollingElement && (document.scrollingElement.scrollTop = document.scrollingElement.scrollHeight); } catch(e) {}
 for (const el of Array.from(document.querySelectorAll('main,section,article,div'))) {
 try {
 const r = el.getBoundingClientRect();
 if (r.x >= 120 && el.scrollHeight > el.clientHeight + 80) el.scrollTop = el.scrollHeight;
 } catch(e) {}
 }
 }""")
            page.mouse.wheel(0, 900)
        except Exception:
            pass

    if wait_inventory_visible(1200):
        _v('[완료] Inventory schedule 콘텐츠 확인')
        return True

 # 1) 실제 메뉴 href/data-url이 있으면 그 URL만 사용합니다. 임의 slug 생성은 하지 않습니다.
    reset_sidebar_scroll()
    for scroll_try in range(1, 8):
        candidates = get_inventory_candidates()
        hrefs = [c.get('href') for c in candidates if c.get('href')]
        if candidates:
            _v(f"[안내] Inventory schedule 메뉴 후보 {len(candidates)}개 확인")
        for href in hrefs:
            try:
                _v(f"[진행] Inventory schedule 실제 href 이동: {href[:120]}")
                page.goto(href, wait_until='domcontentloaded', timeout=12000)
                page.wait_for_timeout(1200)
                if wait_inventory_visible(4000):
                    _v('[완료] Inventory schedule href 진입 성공')
                    return True
                recover_from_404()
            except Exception as e:
                print(f"[주의] Inventory schedule href 이동 실패: {e}")
                recover_from_404()
 # 2) JS click 이벤트 (좌표 미사용)
        for idx, c in enumerate(candidates, start=1):
            try:
                _v(f"[진행] Inventory schedule 메뉴 JS 클릭 {idx}: tag={c.get('tag','')}")
                js_click_inventory_menu()
                page.wait_for_timeout(1000)
                if wait_inventory_visible(3500):
                    _v('[완료] Inventory schedule JS 클릭 진입 성공')
                    return True
                recover_from_404()
            except Exception as e:
                print(f"[주의] Inventory schedule 메뉴 클릭 실패: {e}")
                recover_from_404()
 # sidebar scroll down to find menu
        try:
            page.evaluate("""() => {
 for (const el of Array.from(document.querySelectorAll('aside,nav,div'))) {
 try {
 const r = el.getBoundingClientRect();
 if (r.x <= 340 && el.scrollHeight > el.clientHeight + 30) el.scrollTop += 420;
 } catch(e) {}
 }
 }""")
        except Exception:
            pass
        page.wait_for_timeout(300)

 # 3) Save and continue fallback. 404로 가는 임의 URL은 시도하지 않습니다.
    _v('[진행] 메뉴 진입 실패 → Save and continue 순차 이동 시도')
    for step in range(1, 14):
        if wait_inventory_visible(800):
            _v('[완료] Inventory schedule 콘텐츠 확인')
            return True
        btn = click_save_continue_button()
        if not btn:
            scroll_bottom()
            page.wait_for_timeout(500)
            btn = click_save_continue_button()
        if not btn:
            print('[주의] Save and continue 버튼을 찾지 못했습니다.')
            break
        try:
            _v(f"[진행] Save and continue 순차 이동 {step}/13 (JS click): {btn.get('text','')}")
            page.wait_for_timeout(1600)
            try:
                page.wait_for_load_state('domcontentloaded', timeout=5000)
            except Exception:
                pass
            recover_from_404()
            if wait_inventory_visible(3500):
                _v('[완료] Inventory schedule 콘텐츠 확인')
                return True
        except Exception as e:
            print(f"[주의] Save and continue 클릭 실패: {e}")
            recover_from_404()
            break

    body = body_text(1800)
    print(f"[주의] 새버전 Inventory schedule 진입 실패. 현재 화면 일부: {body[:220]}")
    return False

def _parse_language_target(name: str) -> dict:
    """패키지 이름의 접미사로 언어 섹션 파싱.

    예: 'Toyako Niseko(한)' → {'aliases': ['Korean','한국어','한국','KOR'], 'label': 'Korean'}
        'Toyako Niseko' → {'aliases': ['English','EN','영어'], 'label': 'English'} (기본)

    aliases 리스트로 Klook 가 어떻게 표기하든 매칭 가능.
    """
    name = (name or '').strip()
    if name.endswith('(한)') or name.endswith('(KR)') or name.endswith('(ko)'):
        return {'label': 'Korean', 'aliases': ['Korean', '한국어', '한국', 'KOR', '韩语']}
    if name.endswith('(중)') or name.endswith('(CN)') or name.endswith('(zh)'):
        return {'label': 'Chinese', 'aliases': ['Chinese', 'Chinese (Simplified)', 'Simplified Chinese', '中文', '简体中文', 'CN']}
    if name.endswith('(일)') or name.endswith('(JP)') or name.endswith('(ja)'):
        return {'label': 'Japanese', 'aliases': ['Japanese', '日本語', 'JP', '일본어']}
    return {'label': 'English', 'aliases': ['English', 'EN', '영어', '英语']}



# 한국어 ↔ 영어 토큰 별칭. Klook UI 의 변형 텍스트는 영어로 표시되므로 사용자가
# packages.py 에 한국어로 입력해도 영어 토큰으로 확장해서 매칭이 되도록 한다.
# 한 Activity 안에 여러 패키지가 있는 경우 (예: MBC 107366 — 스튜디오 vs VIP Access)
# 구분에 사용. 알리아스에 없는 한국어 토큰은 영어 텍스트와 매칭이 안 되지만,
# 영어 토큰(MBC 같은)은 그대로 통과되므로 부분 매칭이라도 작동.
TOKEN_ALIASES_KR_EN: dict = {
    # 시설/장소
    '스튜디오': ['studio'],
    '드라마': ['drama', 'k-drama'],
    '리허설': ['rehearsal'],
    '투어': ['tour'],
    '인사이더': ['insider'],
    '액세스': ['access'],
    '체험': ['experience'],
    '가이드': ['guided', 'guide'],
    '풀데이': ['full-day', 'full day'],
    '하프데이': ['half-day', 'half day'],
    # 변형 옵션 (공통)
    '프리미엄': ['premium'],
    '스탠다드': ['standard'],
    '스탠더드': ['standard'],
    '디럭스': ['deluxe'],
    '베이직': ['basic'],
    '왕복': ['round-trip', 'round trip'],
    '편도': ['one-way', 'one way'],
    'vip': ['vip'],
    'VIP': ['vip'],
    # 액티비티 종류 (Hunter Valley 같이 영어 그대로인 경우는 매핑 불필요)
    '와이너리': ['winery'],
    '세그웨이': ['segway'],
    '양궁': ['archery'],
    '서핑': ['surfing'],
    '스키': ['ski'],
    '카약': ['kayak'],
}


def _expand_token_aliases(token: str) -> list[str]:
    """주어진 토큰을 별칭 맵 + 자기 자신으로 확장."""
    t = (token or '').strip()
    if not t:
        return []
    out = [t.lower()]
    aliases = TOKEN_ALIASES_KR_EN.get(t) or TOKEN_ALIASES_KR_EN.get(t.lower()) or []
    for a in aliases:
        if a.lower() not in out:
            out.append(a.lower())
    return out


def _parse_section_target(name: str) -> dict:
    """패키지 이름을 분석해서 언어 + 옵션(variant) + 베이스명 토큰을 추출.

    예시:
      'Hunter Valley(WINERY + SEGWAY)(한)' → {
        'language': {label:'Korean', aliases:['Korean','한국어',...]},
        'variant_tokens': ['winery', 'segway'],         # 마지막 괄호 — 필수 매칭
        'base_tokens': ['hunter', 'valley']              # 본명 — 타이브레이커 보너스
      }
      'MBC 스튜디오(한)' → {
        'language': Korean...,
        'variant_tokens': [],
        'base_tokens': ['mbc', '스튜디오', 'studio']     # 별칭 확장
      }
      'MBC 스튜디오(드라마 리허설)(한)' → {
        'language': Korean...,
        'variant_tokens': ['드라마', '리허설', 'drama', 'k-drama', 'rehearsal'],
        'base_tokens': ['mbc', '스튜디오', 'studio']
      }
      '에버' → {'language': English (default), 'variant_tokens': [], 'base_tokens': ['에버']}
    """
    name = (name or '').strip()
    lang_info = _parse_language_target(name)

    # 언어 접미사 제거
    base = name
    for suffix in ['(한)', '(중)', '(일)', '(영)', '(KR)', '(CN)', '(JP)', '(ko)', '(zh)', '(ja)']:
        if base.endswith(suffix):
            base = base[:-len(suffix)].strip()
            break

    # 마지막 괄호 (옵션/variant) 추출 — 필수 매칭용
    variant_tokens: list[str] = []
    base_for_tokens = base
    m = re.search(r'\(([^()]*)\)\s*$', base)
    if m:
        content = m.group(1).strip()
        # +, , , &, and, /, 공백 으로 분리해서 토큰 리스트로
        parts = re.split(r'[\s+,&/]+|\band\b', content, flags=re.IGNORECASE)
        for tok in parts:
            for alias in _expand_token_aliases(tok):
                if alias not in variant_tokens:
                    variant_tokens.append(alias)
        # 베이스 추출 시 마지막 괄호 제거
        base_for_tokens = base[:m.start()].strip()

    # 베이스명 토큰 추출 — 타이브레이커 보너스용
    # 남은 괄호들도 모두 제거하고 단어 단위로 split
    base_clean = re.sub(r'\([^()]*\)', ' ', base_for_tokens)
    base_parts = re.split(r'[\s+,&/]+|\band\b', base_clean, flags=re.IGNORECASE)
    base_tokens: list[str] = []
    for tok in base_parts:
        for alias in _expand_token_aliases(tok):
            if alias not in base_tokens:
                base_tokens.append(alias)

    # variant_tokens 중 라틴(영어) 문자를 포함하는 토큰이 하나도 없으면
    # — 즉, 모두 한국어/한자이고 별칭 맵에 등록도 안 된 케이스 —
    # Klook UI 가 영어로 표시되는 환경에서 매칭이 0 이 되어 전체 행이 탈락하므로
    # variant 필수 매칭을 강제하지 않고 base 보너스로 통합.
    # 예: '특공대(부산)' — '부산' 별칭 없음 → '부산' 을 base 로 옮겨서 50 baseline 으로 진행.
    has_latin_variant = any(re.search(r'[a-z]', t) for t in variant_tokens)
    if variant_tokens and not has_latin_variant:
        for t in variant_tokens:
            if t not in base_tokens:
                base_tokens.append(t)
        variant_tokens = []

    return {
        'language': lang_info,
        'variant_tokens': variant_tokens,
        'base_tokens': base_tokens,
    }


def click_activity_adult_inventory_item(page, target_language=None):
    """
 Activity 신버전 Inventory schedule에서 Item list 의 첫 번째 Adult/Person 항목을 열고
 See schedule 캘린더를 표시.

 변경사항:
 - 'Price / Inventory Settings' 버튼이 아니라 'See schedule' 버튼을 클릭한다.
 ('Price / Inventory Settings' 는 단순 가격/재고 입력 사이드패널을 띄울 뿐
 날짜별 캘린더가 안 나옴. 'See schedule' 만 캘린더를 띄움.)
 - 클릭 대상은 위쪽 'Item list' 섹션 안의 Adult/Person 행이며, 아래 'Schedule' 섹션은 무시.
 - Item list 안에 Adult 가 여러 개일 경우(예: English Adult, Korean Adult) 화면 위에서 첫 번째 것.

 기존 보완점(~):
 - 'English · Adult'만 찾지 않고, 'Adult', 'Person', 'English · Adult', 'Korean · Person' 등 모두.
 - 왼쪽 메뉴/Basic info의 Adult 가 아니라 메인 영역(x > 150)의 Adult 만 대상.
    """
    _v("[진행] Inventory schedule Item list 의 첫 번째 Adult/Person 항목 선택")

    def content_visible():
        try:
            return bool(page.evaluate(
                """() => {
 const text = (document.body ? (document.body.innerText || document.body.textContent || '') : '').replace(/\s+/g,' ').trim();
 return text.includes('Create item') || text.includes('Item list') || text.includes('Bulk edit price / inventory') || text.includes('Price / Inventory Settings');
 }"""
            ))
        except Exception:
            return False

    if not content_visible():
        _v("[안내] Inventory schedule 콘텐츠가 즉시 확인되지 않았지만 Adult 항목 탐색을 계속합니다.")

    def find_first_adult_row():
        try:
            return page.evaluate(
                """() => {
 // marker — JS 진입 확인 (window.__scoringDebug 이전에 설정해서 JS 시작 자체는 보장)
 window.__adultFnEntered = (window.__adultFnEntered || 0) + 1;
 try {
 // 함수 본체 — 에러 catch 해서 stash
 function norm(s){return (s||'').replace(/\s+/g,' ').trim();}
 function visible(el){
 if(!el) return false;
 const r = el.getBoundingClientRect();
 const st = getComputedStyle(el);
 return st.display !== 'none' && st.visibility !== 'hidden' && Number(st.opacity) !== 0 &&
 r.width > 0 && r.height > 0 && r.bottom >= 0 && r.top <= (innerHeight || 900) + 900;
 }
 function isAdultText(t){
 if (!t) return false;
 // 단순 Adult/Person
 if (t === 'Adult' || t === 'Person') return true;
 // 언어 · Adult
 if (/^(English|Korean|Japanese|Chinese)\s*[·\-]\s*(Adult|Person)$/i.test(t)) return true;
 if (/^(English|Korean|Japanese|Chinese)\s+(Adult|Person)$/i.test(t)) return true;
 // 언어 · 옵션텍스트 · Adult (예: 'English · Winery Experience with Segway · Adult')
 if (/^(English|Korean|Japanese|Chinese)\s*[·\-].+[·\-]\s*(Adult|Person)$/i.test(t)) return true;
 return false;
 }
 function rowScore(row, label){
 const rt = norm(row.innerText || row.textContent || '');
 const r = row.getBoundingClientRect();
 let s = 0;
 //: See schedule 가 있는 행이 Item list 의 Adult 행임. (Schedule 섹션은 Price/Inventory Settings 만 있음)
 if (rt.includes('See schedule')) s += 60;
 //: Price / Inventory Settings 가 있고 See schedule 가 없으면 아래 Schedule 섹션의 행. 페널티.
 if (rt.includes('Price / Inventory Settings') && !rt.includes('See schedule')) s -= 80;
 //: Item list 섹션 마커
 if (rt.includes('Main unit') || rt.includes('Sub-unit')) s += 15;
 if (rt.includes('Status:') || rt.includes('Status')) s += 15;
 if (rt.includes('ID:')) s += 10;
 if (rt.includes('Required to book')) s += 10;
 if (r.x >= 140) s += 10;
 if (r.width >= 400) s += 10;
 if (r.height >= 45 && r.height <= 260) s += 10;
 if (r.width > 1500 || r.height > 420) s -= 60;
 const lr = label.getBoundingClientRect();
 if (lr.x >= r.x - 3 && lr.x <= r.x + r.width + 3 && lr.y >= r.y - 3 && lr.y <= r.y + r.height + 3) s += 20;
 return s;
 }
 const labels = Array.from(document.querySelectorAll('div,span,td,button,a,[role="button"]'))
 .map(el => {
 const r = el.getBoundingClientRect();
 return {el, text:norm(el.innerText||el.textContent||''), x:r.x, y:r.y, w:r.width, h:r.height};
 })
 .filter(o => visible(o.el) && o.x >= 150 && o.y >= 110 && isAdultText(o.text))
 .sort((a,b) => a.y - b.y || a.x - b.x);

 // 언어 + 옵션 필터: window.__targetLangAliases + window.__targetVariantTokens + window.__targetBaseTokens.
 const langAliases = (typeof window.__targetLangAliases !== 'undefined') ? window.__targetLangAliases : null;
 const variantTokens = (typeof window.__targetVariantTokens !== 'undefined') ? window.__targetVariantTokens : null;
 const baseTokens = (typeof window.__targetBaseTokens !== 'undefined') ? window.__targetBaseTokens : null;

 const allLangs = ['english','korean','chinese','japanese','한국어','일본어','한국','일본','中文','简体中文','영어','일어','韩语','중국어'];
 const skipWords = new Set([...allLangs, 'adult','person','child']);

 // Adult 라벨 element 앞에 (문서 순서로) 가장 가까운 section header line 찾기.
 // 신UI: 한 부모 컨테이너 안에 18개 Adult 가 다 들어있고 그 사이사이에 'English · Winery
 // Experience Only', 'Chinese · Winery Experience Only', ... 헤더가 따로 있는 구조.
 // 단순히 위로 올라가면 첫 헤더만 잡으니까 문서 순서로 PRECEDING 마지막 헤더를 잡아야 함.
 // 반환: {lang, sectionLine}
 function findSectionContextFromAncestors(adultEl) {
 if (!adultEl) return {lang: null, sectionLine: ''};
 // 언어 + · 패턴 헤더 후보들
 const all = Array.from(document.querySelectorAll('div,p,h1,h2,h3,h4,h5,h6,span,section,article'));
 const headers = [];
 for (const el of all) {
 const raw = (el.innerText || el.textContent || '');
 if (!raw) continue;
 const firstLine = raw.split('\n')[0].trim();
 if (!firstLine || firstLine.length > 120) continue;
 const lower = firstLine.toLowerCase();
 // row 라벨 패턴만 제외 (헤더에 'per person' 같은 단어가 본문으로 포함될 수 있음):
 //   - 짧은 텍스트가 adult/person/child 단독 또는 행 끝
 //   - · 분리자 뒤 adult/person/child 로 끝남 (예: 'English · Type · Adult')
 const isProbableRow = (
 lower.length < 25 && /(^|\s|^\d+\s*)(adult|person|child)\s*$/i.test(firstLine)
 ) || /[·]\s*(adult|person|child)\s*$/i.test(firstLine);
 if (isProbableRow) continue;
 // 언어 단어 확인 (헤더 시작이거나 단어 단위로 매칭)
 let foundLang = null;
 const words = lower.split(/\s|·|·/).filter(w => w);
 for (const l of allLangs) {
 if (words.includes(l) || lower.startsWith(l)) { foundLang = l; break; }
 }
 if (!foundLang) continue;
 headers.push({el, lang: foundLang, line: lower});
 }
 // adultEl 보다 문서 순서 앞에 있는 헤더만
 const before = headers.filter(h => {
 try {
 const cmp = h.el.compareDocumentPosition(adultEl);
 return (cmp & Node.DOCUMENT_POSITION_FOLLOWING) !== 0;
 } catch (e) { return false; }
 });
 if (!before.length) return {lang: null, sectionLine: ''};
 // 문서 순서로 정렬 (앞→뒤)
 const sortByDocOrder = (a, b) => {
 try {
 const cmp = a.el.compareDocumentPosition(b.el);
 if (cmp & Node.DOCUMENT_POSITION_FOLLOWING) return -1;
 if (cmp & Node.DOCUMENT_POSITION_PRECEDING) return 1;
 } catch (e) {}
 return 0;
 };
 // · 가 포함된 (= 언어+variant 정보가 다 들어있는) 헤더 우선
 const rich = before.filter(h => h.line.includes('·'));
 if (rich.length) {
 rich.sort(sortByDocOrder);
 const closest = rich[rich.length - 1];
 return {lang: closest.lang, sectionLine: closest.line};
 }
 // 없으면 단순 언어 헤더 (특공대 같은 케이스)
 before.sort(sortByDocOrder);
 const closest = before[before.length - 1];
 return {lang: closest.lang, sectionLine: closest.line};
 }

 // 하위 호환 — 언어만 필요한 경우
 function findLangFromAncestors(adultEl) {
 return findSectionContextFromAncestors(adultEl).lang;
 }

 // Adult 행 텍스트로부터 매칭 점수 계산.
 //   text 예: 'English · Winery Experience with Segway · Adult' 또는 'Adult'
 //   score:
 //     - 언어 불일치 → -1 (탈락)
 //     - variant_tokens 있고 매칭 0 → -1 (필수 옵션 불일치)
 //     - variant_tokens 매칭 점수 (가중치 2) + base_tokens 매칭 점수 (가중치 1) + 언어 보너스 50
 //       → 한 언어에 여러 행 있으면 베이스명/옵션 단어 더 많이 겹치는 행이 이김.
 function scoreRowForSection(rowText, rowEl) {
 const t = (rowText || '').toLowerCase();
 const parts = t.split(/[·\-]/).map(p => p.trim()).filter(p => p);

 // 언어 + variant 컨텍스트: row 텍스트에 · 분리자가 있으면 그게 헤더 포함, 없으면 조상 박스에서 찾기
 const hasLangInRow = parts.some(p => allLangs.some(l => p === l || p.startsWith(l)));
 const ancestorCtx = hasLangInRow ? null : findSectionContextFromAncestors(rowEl);
 const ancestorLang = ancestorCtx ? ancestorCtx.lang : null;
 const ancestorLine = ancestorCtx ? ancestorCtx.sectionLine : '';
 const effectiveLangSource = hasLangInRow ? 'row' : (ancestorLang ? 'ancestor' : 'none');

 let langOk;
 if (!langAliases || !langAliases.length) {
 langOk = true;
 } else if (effectiveLangSource === 'none') {
 // 어디서도 언어 헤더 못 찾음 → 단일 언어 활동 → 영어 타깃과 호환
 langOk = langAliases.some(a => a.toLowerCase() === 'english');
 } else if (effectiveLangSource === 'ancestor') {
 // 조상 박스에서 발견된 언어와 타깃 비교
 langOk = false;
 for (const a of langAliases) {
 const al = a.toLowerCase();
 if (ancestorLang === al || ancestorLang.startsWith(al)) { langOk = true; break; }
 }
 } else {
 // row 자체에 언어 헤더 있음
 langOk = false;
 for (const part of parts) {
 for (const a of langAliases) {
 const al = a.toLowerCase();
 if (part === al || part.startsWith(al)) { langOk = true; break; }
 }
 if (langOk) break;
 }
 }
 if (!langOk) return -1;

 // 옵션/베이스 토큰 둘 다 없으면 언어만 매칭 → 100
 const hasVariant = !!(variantTokens && variantTokens.length);
 const hasBase = !!(baseTokens && baseTokens.length);
 if (!hasVariant && !hasBase) return 100;

 // 매칭 대상 텍스트: row 자체 + (있으면) 조상 section header line
 // 조상 헤더에 'English · Winery Experience Only' 같은 variant 정보가 있어서 row 텍스트가
 // 'Adult\nStatus: ...' 만 있어도 옵션 매칭 가능.
 const matchSourceParts = ancestorLine
 ? ancestorLine.split(/[·\-]/).map(p => p.trim()).filter(p => p)
 : parts;

 // 옵션 부분 (언어/Adult/Person/Child 제외)
 const middleParts = matchSourceParts.filter(p => !skipWords.has(p) && !(langAliases || []).some(a => p === a.toLowerCase()));
 const middleText = middleParts.join(' ');
 const middleWords = middleText.split(/\s+/).filter(w => w);
 const denom = Math.max(middleWords.length, 1);

 // variant — 명시된 경우 필수 (하나도 매칭 0 → 탈락)
 let variantMatched = 0;
 if (hasVariant) {
 for (const tok of variantTokens) {
 if (middleText.includes(String(tok).toLowerCase())) variantMatched++;
 }
 if (variantMatched === 0) return -1;
 }

 // base — 타이브레이커 보너스 (탈락 안 시킴)
 let baseMatched = 0;
 if (hasBase) {
 for (const tok of baseTokens) {
 if (middleText.includes(String(tok).toLowerCase())) baseMatched++;
 }
 }

 // 최종: 언어 기본 50 + variant 2배 가중 + base 1배 가중 (모두 단어수 정규화)
 const variantScore = (variantMatched * 200) / denom;  // 가중치 2 × 100
 const baseScore = (baseMatched * 100) / denom;
 return 50 + variantScore + baseScore;
 }
 // Adult 라벨의 행 텍스트 + 조상 박스 언어 컨텍스트 기준 점수 부여 → 점수 양수만 남김
 // 진단용: window.__scoringDebug 에 모든 라벨/점수 저장
 window.__scoringDebug = {labelsInitial: labels.length, scored: [], usedFallback: false};
 if (langAliases || variantTokens || baseTokens) {
 const scored = labels.map(lab => ({lab, score: scoreRowForSection(norm(lab.el.innerText || lab.el.textContent || ''), lab.el)}));
 window.__scoringDebug.scored = scored.map(s => ({
 text: (s.lab.text || '').slice(0, 60),
 y: Math.round(s.lab.y),
 score: s.score
 }));
 const valid = scored.filter(s => s.score > 0);
 if (valid.length) {
 valid.sort((a, b) => b.score - a.score);
 labels.length = 0;
 for (const v of valid) labels.push(v.lab);
 } else {
 // FALLBACK: 점수 매칭 0개 — 원본 isAdultText 통과 라벨 중 첫 번째 사용.
 // 새 UI / 매칭 못 한 케이스에서도 봇이 진행하도록 보장 (점수 무시).
 // labels 는 이미 y 오름차순 정렬되어 있어 화면 상단의 영어 Adult 가 첫 번째.
 window.__scoringDebug.usedFallback = true;
 // labels 그대로 유지 (점수 무관)
 }
 }

 const cands = [];
 for (const lab of labels) {
 let cur = lab.el;
 for (let depth=0; depth<10 && cur; depth++, cur=cur.parentElement) {
 if (!visible(cur)) continue;
 const r = cur.getBoundingClientRect();
 if (r.width < 180 || r.height < 28) continue;
 const sc = rowScore(cur, lab.el);
 if (sc >= 35) {
 cands.push({el:cur, label:lab.el, score:sc, text:norm(cur.innerText||cur.textContent||'').slice(0,240)});
 }
 }
 }
 if (!cands.length) return null;
 cands.sort((a,b) => b.score - a.score || a.el.getBoundingClientRect().y - b.el.getBoundingClientRect().y);
 const best = cands[0];
 try { best.el.scrollIntoView({block:'center', inline:'nearest'}); } catch(e) {}
 const rr = best.el.getBoundingClientRect();
 // 라벨 element를 글로벌에 저장하여 click 시 사용
 window.__adultLabelEl = best.label;
 window.__adultRowEl = best.el;
 return {
 rowX: rr.x, rowY: rr.y, rowW: rr.width, rowH: rr.height,
 text: best.text,
 score: best.score
 };
 } catch (_err) {
 window.__adultFnError = (_err && _err.message ? _err.message : String(_err)) + ' | stack: ' + ((_err && _err.stack ? String(_err.stack).slice(0, 500) : ''));
 return null;
 }
 }"""
            )
        except Exception as _pyerr:
            try:
                page.evaluate("(msg) => { window.__adultFnPyError = msg; }", str(_pyerr))
            except Exception:
                pass
            return None

    # 언어 + 옵션(variant) + 베이스명(타이브레이커) 필터 글로벌 설정
    try:
        sec = _parse_section_target(target_language or '')
        page.evaluate(
            "(args) => { window.__targetLangAliases = args.aliases; window.__targetVariantTokens = args.tokens; window.__targetBaseTokens = args.baseTokens; }",
            {
                "aliases": sec['language']['aliases'],
                "tokens": sec['variant_tokens'],
                "baseTokens": sec.get('base_tokens', []),
            }
        )
        if sec['variant_tokens']:
            _v(f"[진행] 섹션 필터: lang='{sec['language']['label']}', 옵션={sec['variant_tokens']}, 베이스={sec.get('base_tokens', [])}")
        else:
            _v(f"[진행] 섹션 필터: lang='{sec['language']['label']}', 베이스={sec.get('base_tokens', [])}")
    except Exception as e:
        print(f"[주의] 섹션 필터 설정 실패 (영어 기본으로 진행): {e}")

    # FAST PATH (1순위): See schedule 버튼 직접 클릭
    # 사용자 지적: 봇이 Adult 라벨 찾기 등으로 너무 복잡하게 돌고 있었음. 사실 See schedule 만 누르면 됨.
    try:
        sec = _parse_section_target(target_language or '')
        lang_aliases = sec['language']['aliases']
        variant_tokens = sec['variant_tokens']
        base_tokens = sec.get('base_tokens', [])
        # 페이지 top 으로 이동 (See schedule 보이도록)
        try:
            page.evaluate("() => { try { window.scrollTo(0, 0); } catch(e) {} }")
            page.wait_for_timeout(300)
        except Exception:
            pass
        simple_result = page.evaluate(
            "([langAliases, variantTokens, baseTokens]) => {\n"
            " function norm(s){return (s||'').replace(/\\s+/g,' ').trim();}\n"
            " function visible(el){const r=el.getBoundingClientRect();const st=getComputedStyle(el);return st.display!=='none'&&st.visibility!=='hidden'&&Number(st.opacity)!==0&&r.width>0&&r.height>0;}\n"
            " const btns = Array.from(document.querySelectorAll('button,a,[role=button]')).filter(el=>visible(el)&&/See\\s*schedule/i.test(norm(el.innerText||el.textContent||'')));\n"
            " if(!btns.length) return {ok:false, reason:'no See schedule buttons'};\n"
            " const SEPARATOR = String.fromCharCode(0xB7);\n"
            " function findHeaderBefore(btn){\n"
            "  const all = Array.from(document.querySelectorAll('div,span,h1,h2,h3,h4,h5,h6,p'));\n"
            "  const candidates = [];\n"
            "  for (const el of all){\n"
            "   const t = (el.innerText||'').split('\\n')[0].trim();\n"
            "   if (!t || t.length>120) continue;\n"
            "   if (/\\b(adult|person|child)\\b/i.test(t) && t.length<25) continue;\n"
            "   if (!/(English|Chinese|Korean|Japanese)/i.test(t)) continue;\n"
            "   try{const cmp = el.compareDocumentPosition(btn); if((cmp & 4)!==0) candidates.push({el,line:t.toLowerCase()});}catch(e){}\n"
            "  }\n"
            "  if(!candidates.length) return '';\n"
            "  candidates.sort((a,b)=>{try{const c=a.el.compareDocumentPosition(b.el); if(c&4) return -1; if(c&2) return 1;}catch(e){} return 0;});\n"
            "  const rich = candidates.filter(c=>c.line.indexOf(SEPARATOR)>=0);\n"
            "  if (rich.length) return rich[rich.length-1].line;\n"
            "  return candidates[candidates.length-1].line;\n"
            " }\n"
            " const scored = btns.map(btn=>{\n"
            "  const header = findHeaderBefore(btn);\n"
            "  let langOk = false;\n"
            "  for (const a of langAliases){const al=a.toLowerCase(); if(header.startsWith(al)||header.indexOf(' '+al+' ')>=0||header.indexOf(al+' '+SEPARATOR)>=0){langOk=true;break;}}\n"
            "  if (!langOk) return {btn,header,score:-1};\n"
            "  const parts = header.split(SEPARATOR).map(p=>p.trim());\n"
            "  const middle = parts.slice(1).join(' ');\n"
            "  let score = 50;\n"
            "  if (variantTokens && variantTokens.length){\n"
            "   let vm=0; for(const tok of variantTokens){if(middle.indexOf(tok.toLowerCase())>=0)vm++;}\n"
            "   if (vm===0) return {btn,header,score:-1};\n"
            "   const words = middle.split(/\\s+/).filter(w=>w).length||1; score += (vm*200)/words;\n"
            "  }\n"
            "  if (baseTokens && baseTokens.length){\n"
            "   let bm=0; for(const tok of baseTokens){if(middle.indexOf(tok.toLowerCase())>=0)bm++;}\n"
            "   const words = middle.split(/\\s+/).filter(w=>w).length||1; score += (bm*100)/words;\n"
            "  }\n"
            "  return {btn,header,score:Math.round(score)};\n"
            " }).filter(s=>s.score>0);\n"
            " scored.sort((a,b)=>b.score-a.score);\n"
            " if (!scored.length){\n"
            "  btns[0].scrollIntoView({block:'center'}); btns[0].click(); return {ok:true, fallback:'first', header:'(no match)', clicked:'first See schedule'};\n"
            " }\n"
            " const best = scored[0]; best.btn.scrollIntoView({block:'center'}); best.btn.click();\n"
            " return {ok:true, header:best.header, score:best.score, candidates:scored.length};\n"
            "}",
            [lang_aliases, variant_tokens, base_tokens]
        )
        if simple_result and simple_result.get('ok'):
            _v(f"[진행] See schedule 직접 클릭: header='{simple_result.get('header')}', score={simple_result.get('score')}")
            page.wait_for_timeout(1500)
            # 캘린더 진입 확인
            body_check = get_body_text(page, 1500)
            if ('Schedule:' in body_check and ('Items to filter' in body_check or re.search(r'\b[A-Z][a-z]+\s+\d{4}\b', body_check))) or 'Bulk edit price / inventory' in body_check:
                _v("[완료] Activity 캘린더 화면 진입 (fast path)")
                return
            _v(f"[안내] See schedule 클릭됐지만 캘린더 진입 확인 안 됨 → 옛 로직으로 fallback")
    except Exception as _e:
        print(f"[안내] See schedule fast path 에러 → 옛 로직 시도: {_e}")

    adult_row = None
    # 첫 시도 전: 페이지 top 으로 scroll up (URL 변경 직후라 보통 top 이지만 안전망)
    try:
        page.evaluate("""() => {
 try { window.scrollTo(0, 0); } catch(e) {}
 try { document.scrollingElement && (document.scrollingElement.scrollTop = 0); } catch(e) {}
 for (const el of Array.from(document.querySelectorAll('main,section,article,div'))) {
 try { el.scrollTop = 0; } catch(e) {}
 }
 }""")
        page.wait_for_timeout(300)
    except Exception:
        pass

    for attempt in range(1, 24):
        adult_row = find_first_adult_row()
        if adult_row:
            break
        # retry 사이에 scroll. 절반은 down, 절반은 다시 top 으로 (Adult 가 위/아래 어디에 있어도 잡히도록)
        try:
            if attempt % 4 == 0:
                # 4번마다 top 으로 리셋
                page.evaluate("""() => {
 try { window.scrollTo(0, 0); } catch(e) {}
 try { document.scrollingElement && (document.scrollingElement.scrollTop = 0); } catch(e) {}
 for (const el of Array.from(document.querySelectorAll('main,section,article,div'))) { try { el.scrollTop = 0; } catch(e) {} }
 }""")
            else:
                page.evaluate("""() => {
 const amount = 600;
 try { window.scrollBy(0, amount); } catch(e) {}
 try { document.scrollingElement && (document.scrollingElement.scrollTop += amount); } catch(e) {}
 for (const el of Array.from(document.querySelectorAll('main,section,article,div'))) {
 try {
 const r = el.getBoundingClientRect();
 if (r.x >= 120 && el.scrollHeight > el.clientHeight + 80) el.scrollTop += amount;
 } catch(e) {}
 }
 }""")
                page.mouse.wheel(0, 600)
        except Exception:
            pass
        page.wait_for_timeout(400)

    # FALLBACK (단순화): Adult 라벨/점수 매칭 실패 시, 그냥 target language 의 첫 See schedule 버튼 직접 클릭
    # 사용자 지적: See schedule 만 누르면 되는데 봇이 너무 복잡하게 돌고 있었음.
    if not adult_row:
        print("[안내] 복잡한 Adult 매칭 실패 → See schedule 버튼 직접 클릭 fallback 시도")
        try:
            sec = _parse_section_target(target_language or '')
            lang_aliases = sec['language']['aliases']
            variant_tokens = sec['variant_tokens']
            base_tokens = sec.get('base_tokens', [])
            simple_result = page.evaluate(
                "([langAliases, variantTokens, baseTokens]) => {\n"
                " function norm(s){return (s||'').replace(/\\s+/g,' ').trim();}\n"
                " function visible(el){const r=el.getBoundingClientRect();const st=getComputedStyle(el);return st.display!=='none'&&st.visibility!=='hidden'&&Number(st.opacity)!==0&&r.width>0&&r.height>0;}\n"
                " const btns = Array.from(document.querySelectorAll('button,a,[role=button]')).filter(el=>visible(el)&&/See\\s*schedule/i.test(norm(el.innerText||el.textContent||'')));\n"
                " if(!btns.length) return {ok:false, reason:'no See schedule buttons'};\n"
                " const SEPARATOR = String.fromCharCode(0xB7);\n"
                " // 각 버튼에 대해 직전 section header (lang \\u00B7 variant) 찾고 점수 매김\n"
                " function findHeaderBefore(btn){\n"
                "  const all = Array.from(document.querySelectorAll('div,span,h1,h2,h3,h4,h5,h6,p'));\n"
                "  const candidates = [];\n"
                "  for (const el of all){\n"
                "   const t = (el.innerText||'').split('\\n')[0].trim();\n"
                "   if (!t || t.length>120) continue;\n"
                "   if (/\\b(adult|person|child)\\b/i.test(t) && t.length<25) continue;\n"
                "   if (!/(English|Chinese|Korean|Japanese)/i.test(t)) continue;\n"
                "   try{const cmp = el.compareDocumentPosition(btn); if((cmp & 4)!==0) candidates.push({el,line:t.toLowerCase()});}catch(e){}\n"
                "  }\n"
                "  if(!candidates.length) return '';\n"
                "  candidates.sort((a,b)=>{try{const c=a.el.compareDocumentPosition(b.el); if(c&4) return -1; if(c&2) return 1;}catch(e){} return 0;});\n"
                "  // \\u00B7 포함된 헤더 우선\n"
                "  const rich = candidates.filter(c=>c.line.indexOf(SEPARATOR)>=0);\n"
                "  if (rich.length) return rich[rich.length-1].line;\n"
                "  return candidates[candidates.length-1].line;\n"
                " }\n"
                " const scored = btns.map(btn=>{\n"
                "  const header = findHeaderBefore(btn);\n"
                "  // lang 체크\n"
                "  let langOk = false;\n"
                "  for (const a of langAliases){const al=a.toLowerCase(); if(header.startsWith(al)||header.indexOf(' '+al+' ')>=0||header.indexOf(al+' '+SEPARATOR)>=0){langOk=true;break;}}\n"
                "  if (!langOk) return {btn,header,score:-1};\n"
                "  // variant 체크\n"
                "  const parts = header.split(SEPARATOR).map(p=>p.trim());\n"
                "  const middle = parts.slice(1).join(' ');\n"
                "  let score = 50;\n"
                "  if (variantTokens && variantTokens.length){\n"
                "   let vm=0; for(const tok of variantTokens){if(middle.indexOf(tok.toLowerCase())>=0)vm++;}\n"
                "   if (vm===0) return {btn,header,score:-1};\n"
                "   const words = middle.split(/\\s+/).filter(w=>w).length||1; score += (vm*200)/words;\n"
                "  }\n"
                "  if (baseTokens && baseTokens.length){\n"
                "   let bm=0; for(const tok of baseTokens){if(middle.indexOf(tok.toLowerCase())>=0)bm++;}\n"
                "   const words = middle.split(/\\s+/).filter(w=>w).length||1; score += (bm*100)/words;\n"
                "  }\n"
                "  return {btn,header,score};\n"
                " }).filter(s=>s.score>0);\n"
                " scored.sort((a,b)=>b.score-a.score);\n"
                " if (!scored.length){\n"
                "  // 정말 매칭 안 됨 → 첫 번째 See schedule 무조건 클릭\n"
                "  btns[0].scrollIntoView({block:'center'}); btns[0].click(); return {ok:true, fallback:'first', clicked:'first See schedule'};\n"
                " }\n"
                " const best = scored[0]; best.btn.scrollIntoView({block:'center'}); best.btn.click();\n"
                " return {ok:true, header:best.header, score:Math.round(best.score)};\n"
                "}",
                [lang_aliases, variant_tokens, base_tokens]
            )
            print(f"[안내] See schedule fallback 결과: {simple_result}")
            if simple_result and simple_result.get('ok'):
                page.wait_for_timeout(1500)
                body2 = get_body_text(page, 1500)
                if ('Schedule:' in body2 and ('Items to filter' in body2 or re.search(r'\b[A-Z][a-z]+\s+\d{4}\b', body2))) or 'Bulk edit price / inventory' in body2:
                    _v("[완료] Activity 캘린더 화면 진입 (fallback 경로)")
                    return
        except Exception as _e:
            print(f"[안내] See schedule fallback 에러: {_e}")

    if not adult_row:
        body = get_body_text(page, 1500)
        # 항상 진단 정보 출력 (실패 원인 추적)
        try:
            print(f"[진단] URL: {page.url}")
        except Exception as _e:
            print(f"[진단] URL 가져오기 실패: {_e}")
        try:
            dbg = page.evaluate("() => window.__scoringDebug || null")
            print(f"[진단] window.__scoringDebug: {dbg}")
        except Exception as _e:
            print(f"[진단] scoringDebug 가져오기 실패: {_e}")
        # 추가 진단: JS 진입 / 에러 캡처
        try:
            entered = page.evaluate("() => window.__adultFnEntered || 0")
            print(f"[진단] JS 진입 횟수: {entered}")
        except Exception as _e:
            print(f"[진단] 진입 횟수 가져오기 실패: {_e}")
        try:
            js_err = page.evaluate("() => window.__adultFnError || null")
            print(f"[진단] JS 내부 에러: {js_err}")
        except Exception as _e:
            print(f"[진단] JS 에러 가져오기 실패: {_e}")
        try:
            py_err = page.evaluate("() => window.__adultFnPyError || null")
            print(f"[진단] Python evaluate 에러: {py_err}")
        except Exception as _e:
            print(f"[진단] Python 에러 가져오기 실패: {_e}")
        # 본문 키워드 체크
        try:
            keyword_check = {
                'has_Item_list': 'Item list' in body,
                'has_Create_item': 'Create item' in body,
                'has_Price_Inv_Settings': 'Price / Inventory Settings' in body,
                'has_Adult': 'Adult' in body,
                'has_Person': 'Person' in body,
                'body_length': len(body) if body else 0,
            }
            print(f"[진단] 본문 키워드 체크: {keyword_check}")
        except Exception as _e:
            print(f"[진단] 키워드 체크 실패: {_e}")
        # 본문 더 길게 (1000 chars)
        print(f"[진단] 본문 일부 (1000자): {body[:1000] if body else '(빈 body)'}")
        raise Exception(f"Inventory schedule에서 Adult/Person 항목을 찾지 못했습니다. 현재 화면 일부: {body[:180]}")

    # fallback 사용됐는지 확인하고 안내
    try:
        dbg = page.evaluate("() => window.__scoringDebug || null")
        if dbg and dbg.get('usedFallback'):
            print(f"[안내] 정확한 매칭 못 찾음 → 첫 번째 Adult 자동 선택: {adult_row.get('text')}")
            print(f"[안내] 점수 결과: {dbg.get('scored')}")
            print(f"[안내] 의도와 다르면 packages.py 입력 확인 필요. URL: {page.url}")
    except Exception:
        pass

    _v(f"[진행] 첫 번째 Adult/Person 항목 확인: {adult_row.get('text')} / score={adult_row.get('score')}")

 # 클릭 전 'unsaved changes' 다이얼로그가 남아있을 수 있으니 정리
    try:
        dismiss_unsaved_changes_dialog(page)
    except Exception:
        pass

 # Adult/Person 행/라벨 JS click. 이미 펼쳐져 있어도 문제 없음.
    try:
        page.evaluate("""() => {
 const el = window.__adultLabelEl;
 if (el) { el.click(); return true; }
 return false;
 }""")
        page.wait_for_timeout(700)
    except Exception:
        pass

 # 클릭 직후 다이얼로그 뜨면 자동 닫기
    try:
        dismiss_unsaved_changes_dialog(page)
    except Exception:
        pass

    def click_see_schedule_button(row_info=None):
        """
: 'See schedule' 버튼을 찾아 직접 click 호출.
 - Item list 섹션 안의 Adult/Person 행에 붙어 있는 캘린더 아이콘 버튼
 - 텍스트는 'See schedule' (span 안에 있음, 외부 button 텍스트와 동일)
 - 아래 Schedule 섹션의 'Price / Inventory Settings' 버튼은 명시적으로 무시
        """
        try:
            return page.evaluate(
                """(row) => {
 function norm(s){return (s||'').replace(/\\s+/g,' ').trim();}
 function visible(el){
 if(!el) return false;
 const r = el.getBoundingClientRect();
 const st = getComputedStyle(el);
 return st.display !== 'none' && st.visibility !== 'hidden' && Number(st.opacity) !== 0 &&
 r.width > 0 && r.height > 0 && r.bottom >= 0 && r.top <= (innerHeight || 900);
 }
 // 'See schedule' 텍스트가 있는 버튼 (button 또는 그 안의 span)
 const buttons = Array.from(document.querySelectorAll('button,[role="button"]'))
 .map(el => { const r=el.getBoundingClientRect(); return {el, text:norm(el.innerText||el.textContent||''), x:r.x, y:r.y, w:r.width, h:r.height}; })
 .filter(o => visible(o.el) && o.text === 'See schedule' && o.x >= 150 && o.w <= 250);
 if (!buttons.length) return null;
 buttons.sort((a,b) => {
 if (row) {
 // Adult 행과 y 좌표가 가까운 버튼 우선
 const ay = Math.abs((a.y + a.h/2) - (row.rowY + Math.min(row.rowH/2, 80)));
 const by = Math.abs((b.y + b.h/2) - (row.rowY + Math.min(row.rowH/2, 80)));
 if (ay !== by) return ay - by;
 }
 return a.y - b.y || a.x - b.x;
 });
 const b = buttons[0];
 try { b.el.scrollIntoView({block:'center', inline:'nearest'}); } catch(e) {}
 b.el.click();
 return {text:b.text};
 }""",
                adult_row,
            )
        except Exception:
            return None

 # 버튼이 안 보이면 row 우측의 펼침 화살표도 한번 눌러봅니다.
    for attempt in range(1, 8):
        btn = click_see_schedule_button(adult_row)
        if btn:
            _v(f"[진행] See schedule 클릭 (JS click): {btn.get('text','')}")
            page.wait_for_timeout(1200)
            # See schedule 클릭 직후 'unsaved changes' 다이얼로그 떠 있으면 닫기
            try:
                dismiss_unsaved_changes_dialog(page)
            except Exception:
                pass
            body = get_body_text(page, 1800)
            if ('Schedule:' in body and ('Items to filter' in body or 'May ' in body or 'June ' in body)) or 'Bulk edit price / inventory' in body or re.search(r'\b[A-Z][a-z]+\s+\d{4}\b', body):
                _v("[완료] Activity 캘린더 화면 확인")
                return
        try:
 # row 오른쪽 끝 화살표 fallback - JS로 row element의 가장 오른쪽 자식(보통 화살표)을 click
            _v(f"[진행] Adult/Person 행 펼침 재시도 {attempt} (JS click)")
            page.evaluate("""() => {
 const row = window.__adultRowEl;
 if (!row) return false;
 // 행의 오른쪽 끝 영역에 있는 클릭 가능한 element를 찾는다
 const rr = row.getBoundingClientRect();
 const candidates = Array.from(row.querySelectorAll('button,[role="button"],i,svg,span,div'))
 .map(el => ({el, r:el.getBoundingClientRect()}))
 .filter(o => o.r.width > 0 && o.r.height > 0 && o.r.x >= rr.x + rr.width - 80)
 .sort((a,b) => b.r.x - a.r.x);
 if (candidates.length) { candidates[0].el.click(); return true; }
 row.click();
 return true;
 }""")
            page.wait_for_timeout(600)
        except Exception:
            pass

    raise Exception('Item list 의 첫 번째 Adult/Person 항목의 See schedule 버튼을 클릭해 캘린더를 열지 못했습니다.')



def ensure_activity_calendar_target_month(page):
    """Move activity calendar to target month if current month is not visible."""
    target = tomorrow_date_obj()
    month_label = target.strftime('%B %Y')
    year_month_num = target.strftime('%Y-%m')
    _v(f"[진행] Activity 캘린더 목표 월 확인: {month_label}")

    def has_target_month():
        body = get_body_text(page, 1200)
        return month_label in body or year_month_num in body or target.strftime('%b %Y') in body

    if has_target_month():
        _v("[완료] 목표 월이 화면에 표시됨")
        return

 # Try next month button a few times.
    for attempt in range(1, 8):
        btn = None
        try:
            btn = page.evaluate(
                """() => {
 function norm(s){return (s||'').replace(/\\s+/g,' ').trim();}
 function visible(el){
 const r = el.getBoundingClientRect();
 const st = getComputedStyle(el);
 return st.display !== 'none' && st.visibility !== 'hidden' && Number(st.opacity) !== 0 && r.width>0 && r.height>0 && r.bottom>=0 && r.top <= (innerHeight||900);
 }
 const nodes = Array.from(document.querySelectorAll('button,a,span,div,[role="button"]'))
 .map(el => { const r=el.getBoundingClientRect(); return {el, text:norm(el.innerText||el.textContent||''), x:r.x,y:r.y,w:r.width,h:r.height}; })
 .filter(o => visible(o.el) && (o.text === '›' || o.text === '>' || o.text === '»' || o.text.includes('next')) && o.y >= 250)
 .sort((a,b) => a.y - b.y || b.x - a.x);
 if (!nodes.length) return null;
 const n = nodes[0];
 n.el.click();
 return {text:n.text};
 }"""
            )
        except Exception:
            btn = None
        if btn:
            _v(f"[진행] 다음 월 버튼 클릭 (JS click): {btn.get('text','')}")
            page.wait_for_timeout(900)
            if has_target_month():
                _v("[완료] 목표 월 이동 완료")
                return
        else:
            break
    print("[주의] 목표 월 표시 확인 실패. 현재 보이는 캘린더에서 날짜 탐색을 계속합니다.")


def open_tomorrow_edit_schedule_activity(page):
    target_day = tomorrow_day_number()
    _v(f"[진행] Activity 캘린더 익일 날짜 선택: {tomorrow_iso()} / day={target_day}")
    ensure_activity_calendar_target_month(page)

    def popup_is_open():
        try:
            return bool(page.evaluate(
                """() => {
 const text = (document.body ? document.body.innerText : '').replace(/\s+/g, ' ').trim();
 return text.includes('Edit schedule') && text.includes('Accept bookings until') && text.includes('Inventory') && text.includes('Confirm');
 }"""
            ))
        except Exception:
            body = get_body_text(page, 1000)
            return 'Edit schedule' in body and 'Accept bookings until' in body

    if popup_is_open():
        _v("[안내] 이미 Edit schedule 팝업이 열려 있습니다.")
        return

    def find_day_cell():
        try:
            return page.evaluate(
                """([day, isoDate]) => {
 function norm(s){return (s||'').replace(/\\s+/g,' ').trim();}
 function visible(el){
 const r = el.getBoundingClientRect();
 const st = getComputedStyle(el);
 return st.display !== 'none' && st.visibility !== 'hidden' && Number(st.opacity) !== 0 &&
 r.width > 0 && r.height > 0 && r.bottom >= 0 && r.top <= (innerHeight || 900) + 500;
 }
 function score(el, dayEl){
 const r = el.getBoundingClientRect();
 const t = norm(el.innerText || el.textContent || '');
 let s = 0;
 if (new RegExp('(^|\\\\s)' + String(day) + '(\\\\s|$)').test(t)) s += 20;
 if (new RegExp('(^|\\\\s)' + String(day).padStart(2, '0') + '(\\\\s|$)').test(t)) s += 20;
 //: 한국/일본 시간 패턴 + 원화/엔화/달러 모두 가점
 if (t.includes('09:30') || t.includes('07:20') || /\\d{1,2}:\\d{2}/.test(t)) s += 20;
 if (t.includes('/') || t.includes('¥') || t.includes('₩') || t.includes('JPY') || t.includes('KRW') || t.includes('USD')) s += 15;
 if (r.width >= 110 && r.width <= 360) s += 15;
 if (r.height >= 55 && r.height <= 240) s += 15;
 if (r.y >= 350) s += 10;
 if (r.width > 500 || r.height > 350) s -= 80;
 const dr = dayEl.getBoundingClientRect();
 if (dr.x >= r.x - 5 && dr.x <= r.x + r.width + 5 && dr.y >= r.y - 5 && dr.y <= r.y + r.height + 5) s += 10;
 return s;
 }

 //: data-slot-starttime / slot-id attribute 로 직접 찾기 (가장 안정적).
 // DOM: <div class="calendar-table-slot-panel" data-slot-starttime="2026-05-21 08:20:00" slot-id="2026-05-21-0">
 // 같은 날 여러 슬롯이 있을 수 있고, 일부는 placeholder("null 0 ¥ -")일 수 있어
 // 실제 데이터(KRW/Departure confirmed/숫자 가격) 있는 슬롯 우선 선택.
 if (isoDate) {
 const allMatched = Array.from(document.querySelectorAll(
 '[data-slot-starttime^="' + isoDate + '"], [slot-id^="' + isoDate + '"]'
 )).filter(visible);

 // 각 패널에 score 부여: 실제 데이터 있으면 가점
 // 모든 지역 통화 지원: KRW(₩) / JPY(¥) / AUD / USD / GBP(£) / EUR
 // 'Sold out' (재고 0) 도 실제 데이터로 취급 (호주 Wollongong 케이스)
 function panelScore(p) {
 const t = norm(p.innerText || p.textContent || '');
 let s = 0;
 // 음수 가점: placeholder
 if (/\\bnull\\b/i.test(t)) s -= 100;
 if (/[¥₩$£€]\\s*-(?!\\d)/.test(t)) s -= 50;  // '¥ -' 같은 빈 가격 (음수가격 아님)
 if (/^\\s*null/.test(t)) s -= 50;
 // 양수 가점: 실제 데이터 (통화 기호/코드 — 모든 지역)
 if (/₩|KRW/.test(t)) s += 50;
 if (/\\bAUD\\b|\\bUSD\\b|\\bGBP\\b|\\bEUR\\b|\\bJPY\\b|\\bSGD\\b|\\bNZD\\b/.test(t)) s += 50;
 if (/[¥$£€]\\s*\\d/.test(t)) s += 50;  // 기호 뒤 숫자 (¥1000, $125 등)
 if (/\\bDeparture\\s+confirmed\\b/i.test(t)) s += 40;
 if (/\\bSold\\s*out\\b/i.test(t)) s += 40;  // 매진 = 데이터 있음 (재고 0)
 if (/\\d{1,3}(,\\d{3})+/.test(t)) s += 30; // 콤마 가격 형식 (54,000)
 if (/\\d+\\.\\d{1,2}(?!\\d)/.test(t)) s += 20; // 소수점 가격 (166.67)
 if (/\\d{1,2}:\\d{2}/.test(t)) s += 20; // 시간 패턴
 if (/\\d+\\s*\\/\\s*\\d+/.test(t)) s += 10; // 인벤토리 ratio (1 / 98)
 // 같은 날 여러 슬롯이면 빠른 시간 우선
 const st = p.getAttribute('data-slot-starttime') || p.getAttribute('slot-id') || '';
 return {score: s, startTime: st};
 }

 const scored = allMatched.map(p => ({panel: p, ...panelScore(p)}));
 // score 높은 순, 동점이면 가장 이른 시간 순
 scored.sort((a,b) => b.score - a.score || a.startTime.localeCompare(b.startTime));

 if (scored.length) {
 const best = scored[0];
 //: 데이터 로드 race condition 방지.
 // data-slot-starttime 매칭은 있어도 panel 내용이 placeholder("null 0 ¥ -")면
 // Vue 가 아직 데이터 렌더링 안 끝난 상태 → click 핸들러 미바인딩 → click 무효.
 // real data (KRW + 시간 패턴) 가 있어야만 return success, 없으면 null → retry 루프가 대기 후 재시도.
 if (best.score >= 30) {
 const panel = best.panel;
 const card = panel.closest('.calendar-table-item') || panel;
 const titleEl = card.querySelector('.calendar-table-item-title') || card;
 try { card.scrollIntoView({block:'center', inline:'center'}); } catch(e) {}
 window.__dayCellEl = panel;
 window.__dayDayEl = titleEl;
 window.__targetIsoDate = isoDate;
 return {
 text: norm(card.innerText || '').slice(0, 180),
 score: 999 + best.score,
 source: 'data-slot-starttime[진행]',
 startTime: best.startTime,
 matchedTotal: allMatched.length,
 bestPanelScore: best.score,
 };
 } else {
 // 모든 매칭 panel 이 placeholder → null 반환해서 retry 대기 유도
 console.log('[진행] panels matched but all placeholder, retry awaiting data load:',
 scored.map(s => ({score: s.score, st: s.startTime})));
 return null;
 }
 }
 }

 //: .calendar-table-item 안의 day 정확 매칭 + placeholder/null 카드 제외.
 // - 'null', 빈 통화/시간 ('¥ -', '- -' 등) 패턴은 미설정 placeholder 카드라 점수 깎음
 // - 실제 inventory 가 있는 카드 (시간 + 가격 + Departure confirmed) 를 우선
 const candidates = [];
 const calendarItems = Array.from(document.querySelectorAll('.calendar-table-item'))
 .filter(item => visible(item));
 for (const item of calendarItems) {
 const titleEl = item.querySelector('.calendar-table-item-title');
 if (!titleEl) continue;
 { const _t = norm(titleEl.innerText || titleEl.textContent || ''); if (_t !== String(day) && _t !== String(day).padStart(2, '0') && !(!isNaN(parseInt(_t, 10)) && parseInt(_t, 10) === parseInt(day, 10) && _t.length <= 3)) continue; }
 const t = norm(item.innerText || item.textContent || '');
 // 카드 score 산정: 실제 데이터 있는 카드 우선
 let sc = 100;
 // 음수 가점: placeholder 표시
 if (/\bnull\b/i.test(t)) sc -= 80; // "null" 텍스트 = 미설정
 if (/^\d+\s*null/i.test(t)) sc -= 50; // "21 null" 같은 패턴
 if (/¥\s*-/.test(t) || /₩\s*-/.test(t)) sc -= 30; // "¥ -" = 가격 미설정
 if (/\b0\s*\/\s*0\b/.test(t)) sc -= 20; // "0 / 0" = 빈 인벤토리
 // 양수 가점: 실제 데이터
 if (/\d{1,2}:\d{2}/.test(t)) sc += 30; // 시간 패턴 (08:20 등)
 if (/₩|KRW/.test(t)) sc += 25; // 원화 = 실제 한국 상품 (Everland)
 if (/\bDeparture confirmed\b/i.test(t)) sc += 25;
 if (/\d{1,3}(,\d{3})+/.test(t)) sc += 15; // 가격 형식 (54,000 등)
 if (/\d+\s*\/\s*\d+/.test(t) && !/^\d+\s*null/i.test(t)) sc += 10;
 candidates.push({
 el: item, dayEl: titleEl,
 score: sc,
 text: t.slice(0, 180),
 });
 }

 // Fallback: 매칭 실패 시 기존 로직 (텍스트 '21' 직접 매칭)
 if (!candidates.length) {
 const dayNodes = Array.from(document.querySelectorAll('div,span,td,th,button'))
 .filter(el => visible(el) && (() => { const _t = norm(el.innerText || el.textContent || ''); return _t === String(day) || _t === String(day).padStart(2, '0') || (!isNaN(parseInt(_t, 10)) && parseInt(_t, 10) === parseInt(day, 10) && _t.length <= 3); })() && el.getBoundingClientRect().y >= 330)
 .sort((a,b) => a.getBoundingClientRect().y - b.getBoundingClientRect().y || a.getBoundingClientRect().x - b.getBoundingClientRect().x);
 for (const d of dayNodes) {
 let cur = d;
 for (let depth=0; depth<10 && cur; depth++, cur=cur.parentElement) {
 if (!visible(cur)) continue;
 const r = cur.getBoundingClientRect();
 if (r.width < 70 || r.height < 35) continue;
 const sc = score(cur, d);
 if (sc >= 25) candidates.push({el:cur, dayEl:d, score:sc, text:norm(cur.innerText||cur.textContent||'').slice(0,180)});
 }
 }
 }
 if (!candidates.length) return null;
 candidates.sort((a,b) => b.score - a.score || a.el.getBoundingClientRect().y - b.el.getBoundingClientRect().y);
 const best = candidates[0];
 const cell = best.el;
 try { cell.scrollIntoView({block:'center', inline:'center'}); } catch(e) {}
 // element 자체를 글로벌에 저장하여 click 시 사용
 window.__dayCellEl = cell;
 window.__dayDayEl = best.dayEl;
 return {
 text: best.text,
 score: best.score
 };
 }""",
                [str(target_day), tomorrow_iso()],
            )
        except Exception:
            return None

 #: 데이터 로드 race 방지 - 첫 진입 시 충분한 대기.
 # Klook Vue 캘린더는 See schedule 클릭 후 API 호출 → 데이터 렌더링까지 1~3초 걸림.
    _v("[진행]: 캘린더 데이터 로드 대기 (1.5초)")
    page.wait_for_timeout(1500)

    point = None
    for attempt in range(1, 16):   #: 12 → 16 회로 확장
        point = find_day_cell()
        if point:
            _v(f"[진행]: 시도 {attempt}/15 에서 real-data panel 확인")
            break
        try:
            page.evaluate("""() => {
 const amount = 500;
 try { window.scrollBy(0, amount); } catch(e) {}
 for (const el of Array.from(document.querySelectorAll('div,main,section'))) {
 try { if (el.scrollHeight > el.clientHeight + 80) el.scrollTop += amount; } catch(e) {}
 }
 }""")
            page.mouse.wheel(0, 500)
        except Exception:
            pass
 #: wait 시간 늘림 (450 → 700ms), 총 대기 시간 약 11초까지
        page.wait_for_timeout(700)

    if not point:
        raise Exception(f"Activity 캘린더에서 익일 날짜 {target_day} 의 real-data slot panel 을 찾지 못했습니다. (: 약 11초 대기 후에도 placeholder 만 보임 → Vue 렌더링 지연 또는 데이터 미설정)")

    _v(f"[진행] Activity 날짜 셀 확인: {point.get('text')} / score={point.get('score')}")
 #: Activity 캘린더는 .calendar-table-slot-panel 클릭이 정답.
 #
 # DOM 구조 (Klook 신버전 Activity inventory schedule, 2026-05 확인):
 # <div class="calendar-table-item"> ← 외부 카드 (클릭해도 안 됨)
 # <div class="calendar-table-item-title">21</div>
 # <div class="calendar-table-slot-panel ... is-unpublished"> ← 클릭 타겟
 # <div class="panel-title">...</div>
 # <div class="panel-content">...</div>
 # ...
 # </div>
 # </div>
 #
 # 클릭 시 .ant-popover.calendar-table-item-slot-wrap-merchant 가 열림.
 # (Package 워크플로우의 .anticon-edit 와 달리 hover-box / edit-icon 없음.)
    click_strategies = [
 #: ISO 날짜로 fresh query → slot-panel click (Vue 재렌더 후에도 안전)
        ('slot-panel-fresh', """() => {
 const iso = window.__targetIsoDate;
 if (!iso) return false;
 const panels = Array.from(document.querySelectorAll(
 '[data-slot-starttime^="' + iso + '"], [slot-id^="' + iso + '"]'
 )).filter(p => p.offsetParent !== null);
 if (!panels.length) return false;
 // 데이터 있는 패널 우선 (₩/시간/Departure)
 const real = panels.find(p => {
 const t = (p.innerText || '').replace(/\\s+/g,' ').trim();
 return /₩|KRW/.test(t) && /\\d{1,2}:\\d{2}/.test(t);
 });
 const target = real || panels[0];
 try { target.scrollIntoView({block:'center', inline:'center'}); } catch(e) {}
 target.click();
 return true;
 }"""),
 #: slot-panel 직접 click (stored reference, fresh query fallback)
        ('slot-panel-cached', """() => {
 const cell = window.__dayCellEl;
 if (!cell) return false;
 const card = (cell.closest && cell.closest('.calendar-table-item')) || cell;
 const slot = card.querySelector('.calendar-table-slot-panel');
 if (!slot) return false;
 try { card.scrollIntoView({block:'center', inline:'center'}); } catch(e) {}
 slot.click();
 return true;
 }"""),
 #: .anticon-edit (구버전 Package-like UI 호환)
        ('anticon-edit', """() => {
 const cell = window.__dayCellEl;
 if (!cell) return false;
 const card = (cell.closest && cell.closest('.calendar-table-item')) || cell;
 const editIcon = card.querySelector('.anticon-edit, .anticon.anticon-edit, [class*="anticon-edit"]');
 if (!editIcon) return false;
 try { card.scrollIntoView({block:'center', inline:'center'}); } catch(e) {}
 editIcon.click();
 return true;
 }"""),
 # 카드/셀 직접 click (이전 fallback)
        ('cell', "() => { const el = window.__dayCellEl; if (!el) return false; el.click(); return true; }"),
        ('cell-center-dispatch', """() => {
 const el = window.__dayCellEl;
 if (!el) return false;
 const r = el.getBoundingClientRect();
 const cx = r.x + r.width/2, cy = r.y + r.height/2;
 try {
 ['pointerdown','mousedown','pointerup','mouseup','click'].forEach(type => {
 el.dispatchEvent(new MouseEvent(type, {bubbles:true, cancelable:true, view:window, clientX:cx, clientY:cy}));
 });
 } catch(e) {}
 return true;
 }"""),
        ('day-element', "() => { const el = window.__dayDayEl; if (!el) return false; el.click(); return true; }"),
    ]
    for idx, (label, js) in enumerate(click_strategies, start=1):
        _v(f"[진행] Activity 날짜 클릭 {idx} (JS {label})")
        try:
            ok = page.evaluate(js)
        except Exception:
            ok = False
        page.wait_for_timeout(900)
        if popup_is_open():
            _v(f"[완료] Activity Edit schedule 팝업 열림 (전략: {label})")
            return

    raise Exception('Activity 날짜 클릭 후 Edit schedule 팝업을 확인하지 못했습니다.')


def return_to_activity_search_page(page):
    _v("[진행] 저장 후 Activity management > Activity 화면으로 복귀")
    if go_activity_search_hard(page, reason='저장 완료 후 다음 Activity 상품 준비'):
        _v("[완료] Activity 검색 화면 복귀")
        return
    raise Exception('저장 후 Activity 검색 화면으로 복귀하지 못했습니다.')

def classify_task_error(error_text: str) -> str:
    """검색 결과 없음/링크 없음은 '찾을 수 없음', 그 외 페이지 진입/작업 실패는 '실패'로 분류합니다."""
    text = str(error_text or '')
    not_found_patterns = [
        "검색 결과에서",
        "검색 결과를 확인하지",
        "Package 상세 링크를 찾지",
        "Package 링크/href를 찾지",
        "Package 결과 링크",
        "상세 링크를 찾지",
        "검색 결과 없음",
        "No result",
        "not found",
    ]
 # Price & inventory, 날짜 카드, 팝업 등은 검색 결과가 아니라 작업 실패로 둡니다.
    force_fail_patterns = [
        "Price & inventory",
        "Edit schedule",
        "Accept bookings",
        "Confirm",
        "날짜 카드",
        "Adult 옵션",
        "Person 옵션",
        "Adult 또는 Person 옵션",
        "Inventory 수량",
    ]
    if any(p in text for p in force_fail_patterns):
        return "실패"
    if any(p in text for p in not_found_patterns):
        return "찾을 수 없음"
    return "실패"



def _try_accept_until(page, accept_until):
    """
    Accept bookings until 설정. 실패해도 예외를 올리지 않는다.
    반환: 결과 메모에 덧붙일 문구 ("" 면 정상).
    """
    try:
        _step("Accept bookings until 설정", set_accept_booking_until, page, accept_until)
        return ""
    except Exception as e:
        msg = str(e).split("Call log")[0].strip()[:100]
        print(f"[주의] Accept bookings until 설정 실패 (재고 열기는 계속): {msg}")
        return " / 마감시간 미변경(수동 확인)"


def _step(label, func, *args, **kwargs):
    """ 단계 실행 래퍼. 단계 진입 로그는 _v() 로 (verbose 모드에서만 출력).
    실패 시 [단계 실패: X] 형식의 명확한 메시지로 raise.
    """
    _v(f"[단계] ▶ {label} 시작")
    try:
        result = func(*args, **kwargs)
        _v(f"[단계] ✓ {label} 완료")
        return result
    except Exception as e:
        raise Exception(f"[단계 실패: {label}] {e}")


# 화면 이동 단계 — 여기서 실패한 것은 재고를 아직 건드리지 않았으므로
# 다시 해도 안전하다. (Confirm 이후 단계는 재시도하지 않는다)
_RETRYABLE_STEPS = (
    "Package 검색", "Package 상세 진입",
    "Activity 검색", "Activity 상세 진입",
    "Published 패키지 선택", "Inventory schedule 진입",
)


def process_task(page, task, attempt: int = 1):
    """한 상품을 처리. 출력 정책:
    - 성공 + VERBOSE=False: 아무것도 출력 안 함 (모든 진행/단계/주의 로그 버려짐)
    - 실패: 캡처된 진행 로그 + [오류] 메시지를 출력 (디버깅 정보)
    - VERBOSE=True: 캡처 없이 모든 출력 그대로 표시
    """
    import io as _io
    import sys as _sys

    name = task["name"]
    package_id = str(task.get("package_id") or task.get("search_key") or name).strip()
    inventory = task["inventory"]
    input_text = str(task.get("input_text") or f"{name} {inventory}").strip()
    workflow = str(task.get("workflow") or "package").strip().lower()
    accept_until = task.get("accept_until") or {"when": "same_day", "time": "06:00"}

    # 특정 날짜 override 처리 — task['target_date'] 있으면 datetime 으로 파싱 후 설정
    # 형식: "YYYY-MM-DD" 또는 "M/D" / "MM/DD"
    target_date_str = task.get("target_date")
    is_specific_date = False  # 특정 날짜 모드 여부 (날짜만 override, 나머지 단계는 동일)
    if target_date_str:
        parsed_dt = _parse_target_date(str(target_date_str))
        if parsed_dt:
            set_target_date_override(parsed_dt)
            is_specific_date = True
            _acc_when = "당일" if str(accept_until.get("when")) == "same_day" else "전일"
            print(f"[안내] 특정 날짜 지정: {parsed_dt.strftime('%Y-%m-%d')} ({name}) "
                  f"— Accept bookings until {_acc_when} {accept_until.get('time')}")
        else:
            print(f"[주의] target_date 파싱 실패, 익일 기본값 사용: {target_date_str}")
            set_target_date_override(None)
    else:
        set_target_date_override(None)

    # 출력 캡처 (VERBOSE 면 캡처 안 함)
    _captured = None
    _orig_stdout = _sys.stdout
    if not VERBOSE:
        _captured = _io.StringIO()
        _sys.stdout = _captured

    _accept_note = ""
    try:
        try:
            if workflow == "activity":
                _v(f"[진행] Activity 방식 작업 시작: {name} / {package_id}")
                _step("Activity 검색", search_activity, page, package_id)
                _step("Activity 상세 진입", open_activity_detail, page, package_id)
                _step("Published 패키지 선택", select_published_package, page)
                if not _step("Inventory schedule 진입", goto_inventory_schedule_section_v37, page):
                    raise Exception('새버전 Inventory schedule 진입 실패')
                _step("Adult/Person 인벤토리 항목 클릭", click_activity_adult_inventory_item, page, name)
                _step("익일 날짜 Edit 팝업 열기", open_tomorrow_edit_schedule_activity, page)
                _step("Inventory 수량 입력", fill_inventory_in_popup, page, inventory)
                # Accept bookings until 은 특정 날짜 모드에서도 맞춘다.
                # 재고만 열고 마감시간을 안 건드리면, 이미 지난 마감시간 때문에
                # 자리는 열려 있는데 아무도 예약할 수 없다.
                #
                # 다만 이게 실패했다고 재고 열기까지 버리면 안 된다. 이 단계는
                # Inventory 입력 다음, Confirm 앞이라 여기서 죽으면 저장이 안 되고
                # 그 상품은 통째로 안 열린다. 실패는 기록만 하고 계속 간다.
                # (2026-08-26: 이것 때문에 한국 3건이 열리지 않았다)
                _accept_note = _try_accept_until(page, accept_until)
                _step(f"Activate {'OFF' if int(inventory) == 0 else 'ON'} 설정", set_activate, page, int(inventory) != 0)
                _step("Confirm 저장", confirm_popup, page)
                _step("Activity 검색 화면 복귀", return_to_activity_search_page, page)
            else:
                _v(f"[진행] Package 방식 작업 시작: {name} / {package_id}")
                _step("Package 검색", search_package_smart, page, package_id)
                _step("Package 상세 진입", open_package_detail_smart, page, package_id)
                _step("Adult 옵션 클릭", click_adult_option, page)
                _step("익일 날짜 Edit 팝업 열기", open_tomorrow_edit_schedule, page)
                _step("Inventory 수량 입력", fill_inventory_in_popup, page, inventory)
                # Accept bookings until 은 특정 날짜 모드에서도 맞춘다.
                # 재고만 열고 마감시간을 안 건드리면, 이미 지난 마감시간 때문에
                # 자리는 열려 있는데 아무도 예약할 수 없다.
                #
                # 다만 이게 실패했다고 재고 열기까지 버리면 안 된다. 이 단계는
                # Inventory 입력 다음, Confirm 앞이라 여기서 죽으면 저장이 안 되고
                # 그 상품은 통째로 안 열린다. 실패는 기록만 하고 계속 간다.
                # (2026-08-26: 이것 때문에 한국 3건이 열리지 않았다)
                _accept_note = _try_accept_until(page, accept_until)
                _step(f"Activate {'OFF' if int(inventory) == 0 else 'ON'} 설정", set_activate, page, int(inventory) != 0)
                _step("Confirm 저장", confirm_popup, page)
                _step("Package 검색 화면 복귀", return_to_package_search_page, page)
            # 성공: 캡처 버림. 아무것도 출력 안 함.
            return {
                "name": name,
                "input_text": input_text,
                "workflow": workflow,
                "package_id_or_search_key": package_id,
                "inventory": inventory,
                "date": tomorrow_iso(),
                "result": "성공",
                "memo": "Inventory open/update 완료" + (_accept_note or ""),
            }
        except Exception as e:
            # 실패: stdout 복원 후 캡처 + 에러 출력
            if _captured is not None:
                _sys.stdout = _orig_stdout
                _captured_text = _captured.getvalue()
                if _captured_text:
                    _sys.stdout.write(_captured_text)
                _captured = None  # 더 이상 캡처 안 함
            result_label = "새버전 실패" if workflow == "activity" else classify_task_error(str(e))
            print(f"[오류] {name} / {package_id}: {e}")
            try:
                if workflow == "activity":
                    go_activity_search_hard(page, reason="오류 발생 후 다음 Activity 상품 준비")
                else:
                    go_package_search_hard(page, reason="오류 발생 후 다음 상품 준비")
            except Exception as recover_error:
                print(f"[주의] 오류 후 검색 화면 복구 실패: {recover_error}")

            # ⚠️ 화면 이동 단계 실패는 그날그날 다르다. 한 번 더 해 본다.
            #
            #    같은 상품이 어제는 실패하고 오늘은 성공한다. 반대도 마찬가지다.
            #      09-03 실패: 486801 488101 486797 277737 284812
            #      09-04 성공: 위 전부 / 대신 299440 694032 이 실패
            #    상품 문제가 아니라 그때그때 화면이 안 뜬 것이다.
            #    VI·MRT 는 이미 재시도가 있는데 Klook 만 없어서, 한 번 미끄러지면
            #    그 상품은 그날 안 열렸다.
            #
            #    재고를 건드리기 전 단계만 다시 한다. Confirm 이후는 손대지 않는다.
            if attempt == 1 and any(f"[단계 실패: {st}]" in str(e) for st in _RETRYABLE_STEPS):
                print(f"[재시도] {name} / {package_id} — 화면 이동 단계라 한 번 더 합니다")
                page.wait_for_timeout(1500)
                try:
                    return process_task(page, task, attempt=2)
                except Exception as retry_error:
                    print(f"[오류] {name} / {package_id}: 재시도도 실패 — {retry_error}")
            return {
                "name": name,
                "input_text": input_text,
                "workflow": workflow,
                "package_id_or_search_key": package_id,
                "inventory": inventory,
                "date": tomorrow_iso(),
                "result": result_label,
                "memo": str(e),
            }
    finally:
        # stdout 항상 복원
        if _captured is not None:
            _sys.stdout = _orig_stdout


def print_result_summary(logs, site: str = "KLOOK"):
    """콘솔 결과를 요청 형식으로 간단히 출력합니다."""
    rows = list(logs or [])
    labels = ["성공", "실패", "찾을 수 없음", "새버전 실패"]
    groups = {label: [] for label in labels}
    for row in rows:
        result = str(row.get("result", "")).strip()
        workflow = str(row.get("workflow", "")).strip().lower()
        if workflow == "activity" and result == "실패":
            result = "새버전 실패"
        if result not in groups:
            result = "실패"
        groups[result].append(row)

    def item_text(row):
        raw = str(row.get("input_text", "")).strip()
        if raw:
            return raw
        name = str(row.get("name", "")).strip()
        inv = str(row.get("inventory", "")).strip()
        if name and inv:
            return f"{name} {inv}"
        return name or str(row.get("package_id_or_search_key", "")).strip() or "확인 필요"

    print(f"\n[{site.title()} 결과]")
    for label in labels:
        items = [item_text(row) for row in groups[label]]
        print(f"{label}({len(items)}): {', '.join(items) if items else '없음'}")


def _load_tasks_from_file(path: str):
    """
 main.py / bulk.py 가 만든 task JSON 을 로드.
 필드 우선순위:
 package_id: task JSON 의 명시값 > packages.py 조회값
 workflow: task JSON 의 명시값 > packages.py 조회값 > 'package'
 main.py / bulk.py 가 항상 명시값을 채워서 넘기므로, packages.py fallback 은
 예외 케이스(수동 JSON, 누락된 필드)에만 사용된다.
    """
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    tasks = []
    for item in data:
        name = str(item.get("name", "")).strip()
        if item.get("inventory") in (None, ""):
            raise ValueError(f"task JSON 항목에 inventory 가 없습니다: {item}")
        inventory = int(item.get("inventory"))

 # packages.py 조회 (fallback 용)
        info = get_package(name) or {}

        package_id = str(
            item.get("package_id")
            or item.get("search_key")
            or info.get("id")
            or name
        ).strip()
        workflow = str(
            item.get("workflow")
            or info.get("workflow")
            or "package"
        ).strip().lower()
        input_text = str(item.get("input_text") or f"{name} {inventory}").strip()

 # accept_until: task JSON 명시 > packages.py 조회 > worker 기본값
        accept_until = (
            item.get("accept_until")
            or info.get("accept_until")
            or {"when": "same_day", "time": "06:00"}
        )

        tasks.append({
            "name": name,
            "package_id": package_id,
            "inventory": inventory,
            "workflow": workflow,
            "input_text": input_text,
            "accept_until": accept_until,
            # target_date: main.py 가 특정 날짜 선택 시 넘겨줌 (없으면 None → 익일 기본)
            "target_date": item.get("target_date"),
        })
    return tasks


def _pick_klook_page(context):
    """
    이 Chrome 에서 Klook 탭을 고른다. 없으면 새로 열어 Klook 으로 보낸다.

    ⚠️ 예전에는 context.pages[0] 을 그냥 썼다. Klook 전용 Chrome 이던 시절엔
       맞았지만, 지금은 한 지역 Chrome 안에 Klook + KKday + GetYourGuide + Ctrip
       탭이 같이 있어서 첫 탭이 GetYourGuide 일 수 있다.
       실제로 2026-08-23 실행에서 Korea worker 가 GG 탭을 잡는 바람에
       'Page not found ... (c) 2008-2026 GetYourGuide' 를 보고 그 지역 7건이 전부 실패했다.
    """
    for pg in context.pages:
        try:
            if "klook.com" in (pg.url or ""):
                return pg
        except Exception:
            continue
    print("[안내] Klook 탭이 없어 새 탭을 엽니다.")
    pg = context.new_page()
    try:
        pg.goto("https://merchant.klook.com/", wait_until="domcontentloaded", timeout=30_000)
        pg.wait_for_timeout(1_500)
    except Exception as e:
        print(f"[주의] Klook 새 탭 초기 이동 실패 (계속 진행): {e}")
    return pg

def main():
    parser = argparse.ArgumentParser(description="Klook Merchant Center inventory open worker")
    parser.add_argument("--cdp-url", default=os.environ.get("KLOOK_CDP_URL", "http://localhost:9522"))
    parser.add_argument("--site", default=os.environ.get("KLOOK_SITE", "KLOOK"))
    parser.add_argument("--tasks-file", default="")
    parser.add_argument("--suppress-summary", action="store_true")
    args = parser.parse_args()

    if args.tasks_file:
        tasks = _load_tasks_from_file(args.tasks_file)
    else:
        tasks = ask_tasks_from_user()

    if not tasks:
        _v("[안내] 작업할 항목이 없습니다.")
        return

    print(f"\n[안내] 사이트: {args.site}")
    _v(f"[안내] CDP URL: {args.cdp_url}")
    _v("[안내] 작업 목록")
    for task in tasks:
        print(f"- {task['name']} / 방식 {task.get('workflow', 'package')} / 검색값 {task['package_id']} / Inventory {task['inventory']}")

    logs = []
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(args.cdp_url, timeout=CDP_CONNECT_TIMEOUT_MS)
        if not browser.contexts:
            raise Exception("열려 있는 Chrome context를 찾지 못했습니다. start_chrome.bat으로 크롬을 먼저 실행하세요.")
        context = browser.contexts[0]

        # ⚠️ 대화상자를 우리가 받는다. 안 받으면 Playwright 가 자동으로 닫는데,
        #    그 처리가 Node 쪽에서 돌다가 페이지가 사라지면 아무도 받지 않는
        #    오류가 되어 프로세스가 통째로 죽는다.
        #    (2026-08-26: Australia 워커가 이걸로 끝나 그 지역 2건이 날아갔다)
        def _swallow_dialog(dialog):
            try:
                kind = getattr(dialog, "type", "?")
                text = (getattr(dialog, "message", "") or "")[:120]
            except Exception:
                kind, text = "?", ""
            try:
                dialog.dismiss()
            except Exception:
                pass          # 페이지가 이미 사라졌으면 닫을 것도 없다
            print(f"[안내] 브라우저 알림 닫음 ({kind}): {text}")

        def _hook_dialog(pg):
            try:
                pg.on("dialog", _swallow_dialog)
            except Exception:
                pass

        for _pg in context.pages:
            _hook_dialog(_pg)
        try:
            context.on("page", _hook_dialog)      # 새로 열리는 탭에도
        except Exception as e:
            print(f"[주의] 알림 처리기 설정 실패 (계속 진행): {e}")

        page = _pick_klook_page(context)

 # ── viewport 강제 ( 2560x1440) ──────────────────────
 # 사용자 모니터 32인치 환경 기준으로 2560x1440 고정. 멀티 지역 운영 시
 # (Korea/Japan/Australia/London 등 여러 Chrome 동시 운영) 각 창을 모니터에
 # 작게 띄워두어도 selector / 좌표 계산이 일관되게 동작.
 # 컨텍스트의 모든 페이지에 동일하게 적용 (Klook 탭 외 다른 페이지 포함).
        VIEWPORT = {"width": 2560, "height": 1440}
        try:
            page.set_viewport_size(VIEWPORT)
            _v(f"[안내] viewport 강제 적용: {VIEWPORT['width']}x{VIEWPORT['height']} (사이트={args.site})")
        except Exception as e:
            print(f"[주의] viewport 강제 적용 실패 (계속 진행): {e}")
 # 컨텍스트의 다른 페이지에도 모두 적용 (탭 전환 시 일관성 유지)
        for _other in context.pages:
            if _other is page:
                continue
            try:
                _other.set_viewport_size(VIEWPORT)
            except Exception:
                pass
 # 새로 열리는 페이지(예: 검색 결과 새 탭)에도 자동 적용
        def _apply_viewport_on_new_page(new_page):
            try:
                new_page.set_viewport_size(VIEWPORT)
            except Exception:
                pass
        try:
            context.on("page", _apply_viewport_on_new_page)
        except Exception as e:
            print(f"[주의] new page viewport hook 설정 실패: {e}")
 # ─────────────────────────────────────────────────────────────────
        for task in tasks:
            try:
                if str(task.get('workflow') or 'package').lower() == 'activity':
                    go_activity_search_hard(page, reason="새 Activity 상품 시작 전 정리")
                else:
                    go_package_search_hard(page, reason="새 Package 상품 시작 전 정리")
            except Exception:
                pass
            result = process_task(page, task)
            logs.append(result)
            try:
                marker_line = "##RESULT## " + json.dumps(result, ensure_ascii=False, default=str)
                print(marker_line, flush=True)
            except Exception as e:
                print(f"[주의] 결과 마커 출력 실패: {e}")

    if not args.suppress_summary:
        print_result_summary(logs, args.site)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("[치명적 오류]", e)
        traceback.print_exc()
