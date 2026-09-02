"""
Chrome 연결 및 공통 설정.

- 각 OTA 봇은 동일한 connect_and_setup(port) 호출
- 뷰포트 2560x1440 강제 (기존 페이지 + 새 탭 모두 자동 적용)
- 창이 최소화/가려져도 동작하도록 throttle 차단 플래그는 .bat 파일에서 처리
"""

from __future__ import annotations

import logging
import os
from typing import Tuple

from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright

# Chrome 에 붙을 때 기다리는 시간 (ms).
# ⚠️ playwright 기본값은 180초다. Chrome 이 "반쯤 죽은" 상태 -- /json/version 은
#    200 을 주는데 CDP 핸드셰이크(<ws connected> 이후)가 안 끝나는 상태 -- 면
#    워커마다 3분씩 버리고 그제서야 실패한다.
#    2026-09-01 팀원 PC: 마감에서 25번, 2026-09-02: MRT 오픈 3건 전멸.
#    빨리 실패해야 사람이 그 Chrome 을 다시 켤 시간이 있다.
CDP_CONNECT_TIMEOUT_MS = int(os.environ.get("CDP_CONNECT_TIMEOUT_MS") or 30000)


LOG = logging.getLogger(__name__)

VIEWPORT = {"width": 2560, "height": 1440}


def connect_and_setup(port: int) -> Tuple[Browser, BrowserContext, Page]:
    """
    이미 띄워져 있는 Chrome(--remote-debugging-port=<port>)에 attach.

    반환: (browser, context, page)
      - page 는 첫 번째 페이지 (없으면 새로 생성)
      - 모든 기존 페이지에 viewport 적용
      - 새로 열리는 페이지에도 자동으로 viewport 적용

    호출 측은 끝나고 browser.close()는 부르지 않는다 (Chrome은 계속 살아있어야 함).
    필요시 context.close() 정도만.
    """
    p = sync_playwright().start()
    browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{port}",
                                          timeout=CDP_CONNECT_TIMEOUT_MS)

    if not browser.contexts:
        raise RuntimeError(
            f"Chrome on port {port} has no contexts. "
            f"Launch the corresponding .bat first."
        )
    context = browser.contexts[0]

    # 새 페이지에 viewport 자동 적용
    def _on_page(pg: Page) -> None:
        try:
            pg.set_viewport_size(VIEWPORT)
        except Exception as e:
            LOG.warning("set_viewport_size on new page failed: %s", e)

    context.on("page", _on_page)

    # 기존 페이지에도 적용
    for pg in context.pages:
        try:
            pg.set_viewport_size(VIEWPORT)
        except Exception as e:
            LOG.warning("set_viewport_size on existing page failed: %s", e)

    if context.pages:
        page = context.pages[0]
    else:
        page = context.new_page()
        page.set_viewport_size(VIEWPORT)

    return browser, context, page


def click_safe(page: Page, selector: str, timeout: int = 10_000) -> None:
    """창이 가려져도 동작하는 클릭. visible 체크 + force=True 폴백."""
    loc = page.locator(selector).first
    try:
        loc.wait_for(state="visible", timeout=timeout)
        loc.click(timeout=timeout)
    except Exception:
        # 가려진 경우 force click 시도
        loc.click(force=True, timeout=timeout)


def assert_visible(page: Page, selector: str, timeout: int = 10_000) -> None:
    """엘리먼트가 실제 가시인지 검증. 화면 밖에 있으면 스크롤."""
    loc = page.locator(selector).first
    loc.wait_for(state="visible", timeout=timeout)
    try:
        loc.scroll_into_view_if_needed(timeout=2_000)
    except Exception:
        pass


def close_worker_page(page, label: str = "") -> None:
    """
    병렬 worker 가 자기 전용으로 만든 탭을 반드시 닫는다.

    ⚠️ P0-2 (탭 누수) 수정.
       여태 각 worker 가 context.new_page() 로 전용 탭을 만들고 아무도 닫지 않았다.
       KKDAY 4분할 x 4지역, VI 4분할, MRT 4분할, KLOOK 2분할 x 4지역이 매일 실행되면
       지역 Chrome 마다 하루 6개, GLOBAL Chrome 에 하루 8개씩 탭이 영구 누적된다.
       한 달이면 수백 개가 되어 렌더러가 죽고, 그때 나오는 증상이
       net::ERR_ABORTED / TargetClosedError / Locator.click Timeout 이다.
       (2026-08-20 로그의 VI 성공 0건이 정확히 이 케이스)

       마지막 남은 탭까지 닫으면 Chrome 창 자체가 종료되므로 그건 남겨둔다.
    """
    if page is None:
        return
    try:
        ctx = page.context
        if ctx is not None and len(ctx.pages) <= 1:
            LOG.info("탭 정리 skip%s: 마지막 탭이라 닫으면 Chrome 이 종료됨", f" ({label})" if label else "")
            return
    except Exception:
        pass
    try:
        page.close()
        LOG.info("탭 정리 완료%s", f" ({label})" if label else "")
    except Exception as e:
        LOG.warning("탭 정리 실패%s: %s", f" ({label})" if label else "", e)
