# -*- coding: utf-8 -*-
"""
cdp_page.py
Chrome 의 **페이지 타깃에 직접** 붙는 최소 CDP 클라이언트.

왜 Playwright 를 안 쓰나
    connect_over_cdp 는 브라우저 전체에 attach 한다. 그래서 같은 Chrome 에
    응답 없는 탭이 하나라도 있으면 그 탭의 초기화를 기다리다 통째로 타임아웃난다.
    2026-09-09 이 PC 의 9522 가 정확히 그 상태였다 — vBooking 탭은 멀쩡한데
    Klook/GG/KKday 로그인 탭 3개가 CDP 에 응답하지 않아
    connect_over_cdp 가 30초를 버리고 실패했다.

        OK   https://vbooking.ctrip.com/...            <- 쓰려는 탭
        HANG https://merchant.klook.com/home
        HANG https://supplier.getyourguide.com/auth/login
        HANG https://scm.kkday.com/v2/en/auth/login

    페이지 타깃 하나에만 붙으면 옆 탭이 죽어 있든 말든 상관이 없다.
    남의 탭을 닫거나 Chrome 을 다시 켜지 않는다 — 사람이 로그인해 둔 것을
    봇이 마음대로 정리하면 안 된다.

쓰는 쪽은 sync 다 (다른 마감 봇과 같다).
"""
from __future__ import annotations

import json
import logging
import time
import urllib.parse
import urllib.request
from typing import Any, Optional

from websockets.sync.client import connect as _ws_connect

LOG = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 30.0


class CdpError(RuntimeError):
    pass


# ──────────────────────────────────────────────────────────────────────────────
# 브라우저 수준 조회 (HTTP /json — 여기는 죽은 탭이 있어도 응답한다)
# ──────────────────────────────────────────────────────────────────────────────
def _http(port: int, path: str, method: str = "GET", timeout: float = 8.0):
    url = f"http://127.0.0.1:{int(port)}{path}"
    req = urllib.request.Request(url, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read().decode("utf-8", "replace")
    return json.loads(body) if body.strip().startswith(("{", "[")) else body


def version(port: int) -> dict:
    return _http(port, "/json/version")


def targets(port: int) -> list[dict]:
    return _http(port, "/json/list")


def pages(port: int, host: str = "") -> list[dict]:
    """host 조각이 URL 에 든 page 타깃만."""
    out = []
    for t in targets(port):
        if t.get("type") != "page":
            continue
        if host and host not in (t.get("url") or ""):
            continue
        out.append(t)
    return out


def open_tab(port: int, url: str) -> dict:
    """새 탭을 연다. 사람이 열어 둔 탭은 건드리지 않는다."""
    q = urllib.parse.quote(url, safe=":/?=&%")
    return _http(port, f"/json/new?{q}", method="PUT")


def close_tab(port: int, target_id: str) -> None:
    try:
        _http(port, f"/json/close/{target_id}")
    except Exception as e:                      # 이미 닫혔으면 그만이다
        LOG.debug("close_tab(%s) 무시: %s", target_id, e)


def alive(port: int) -> bool:
    try:
        version(port)
        return True
    except Exception:
        return False


# ──────────────────────────────────────────────────────────────────────────────
# 페이지 하나에 붙는다
# ──────────────────────────────────────────────────────────────────────────────
class CdpPage:
    """
    페이지 타깃 하나에 대한 CDP 연결.

    ⚠️ js() 는 페이지 안에서 코드를 돌린다. 값을 읽는 데도, 클릭하는 데도 쓴다.
       '읽기' 와 '누르기' 는 부르는 쪽에서 구분해야 한다.
    """

    def __init__(self, ws_url: str, timeout: float = DEFAULT_TIMEOUT):
        self.ws_url = ws_url
        self.timeout = timeout
        self._id = 0
        self._ws = _ws_connect(ws_url, max_size=64 * 1024 * 1024,
                               open_timeout=timeout, close_timeout=5)

    # ── 저수준 ────────────────────────────────────────────────────────────
    def send(self, method: str, timeout: Optional[float] = None, **params) -> dict:
        self._id += 1
        mid = self._id
        self._ws.send(json.dumps({"id": mid, "method": method, "params": params}))
        deadline = time.monotonic() + (timeout or self.timeout)
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                raise CdpError(f"{method}: 응답이 없습니다")
            msg = json.loads(self._ws.recv(timeout=left))
            if msg.get("id") != mid:
                continue                       # 이벤트는 흘려 보낸다
            if "error" in msg:
                raise CdpError(f"{method}: {msg['error']}")
            return msg.get("result", {})

    def js(self, expression: str, timeout: Optional[float] = None) -> Any:
        """페이지 컨텍스트에서 식을 평가한다. 페이지 예외는 CdpError 로 올린다."""
        r = self.send("Runtime.evaluate", timeout=timeout,
                      expression=expression, returnByValue=True,
                      awaitPromise=True, userGesture=True)
        if "exceptionDetails" in r:
            detail = r["exceptionDetails"]
            desc = ((detail.get("exception") or {}).get("description")
                    or detail.get("text") or "JS 예외")
            raise CdpError(str(desc)[:300])
        return r.get("result", {}).get("value")

    def url(self) -> str:
        return self.js("location.href") or ""

    # ── 기다리기 ──────────────────────────────────────────────────────────
    def wait(self, expression: str, timeout: float = 30.0, poll: float = 0.4,
             what: str = "") -> Any:
        """
        식이 참 같은 값을 낼 때까지 기다리고 그 값을 준다.

        ⚠️ 시간이 다 되면 '없다' 가 아니라 예외다. 못 본 것을 없는 것으로
           보고하면 재고가 조용히 남는다.
        """
        deadline = time.monotonic() + timeout
        last: Any = None
        while time.monotonic() < deadline:
            try:
                last = self.js(expression)
                if last:
                    return last
            except CdpError as e:
                last = f"({e})"
            time.sleep(poll)
        raise CdpError(f"{what or expression[:60]}: "
                       f"{timeout:.0f}초 안에 나타나지 않았습니다 (마지막={last!r})")

    def settle(self, expression: str, times: int = 2, poll: float = 0.6,
               timeout: float = 20.0, what: str = "") -> Any:
        """
        같은 값이 연속으로 나올 때까지 기다린다.

        화면이 아직 그리는 중일 때 읽으면 '없다' 가 나온다. 그걸 근거로
        판정하면 안 된다.
        """
        deadline = time.monotonic() + timeout
        prev: Any = object()
        streak = 0
        while time.monotonic() < deadline:
            cur = self.js(expression)
            if cur is not None and cur == prev:
                streak += 1
                if streak >= times - 1:
                    return cur
            else:
                streak = 0
            prev = cur
            time.sleep(poll)
        raise CdpError(f"{what or '화면'}: 값이 안정되지 않았습니다")

    def close(self) -> None:
        try:
            self._ws.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def attach(target: dict, timeout: float = DEFAULT_TIMEOUT) -> CdpPage:
    ws = target.get("webSocketDebuggerUrl")
    if not ws:
        raise CdpError(f"이 타깃에는 붙을 수 없습니다: {(target.get('url') or '')[:80]}")
    return CdpPage(ws, timeout=timeout)


def _ws_url_of(port: int, target_id: str, timeout: float = 10.0) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for x in targets(port):
            if x.get("id") == target_id and x.get("webSocketDebuggerUrl"):
                return x["webSocketDebuggerUrl"]
        time.sleep(0.3)
    return ""


def open_page(port: int, url: str,
              timeout: float = DEFAULT_TIMEOUT) -> tuple[CdpPage, str]:
    """새 탭을 열고 거기에 붙는다. (page, target_id) — 끝나면 close_tab 해야 한다."""
    t = open_tab(port, url)
    tid = t.get("id") or ""
    ws = t.get("webSocketDebuggerUrl") or _ws_url_of(port, tid)
    if not ws:
        close_tab(port, tid)
        raise CdpError("새 탭에 붙지 못했습니다")
    return CdpPage(ws, timeout=timeout), tid


def wait_new_page(port: int, before: set[str], host: str = "",
                  timeout: float = 30.0) -> dict:
    """
    클릭 때문에 새로 열린 탭 하나를 기다린다.

    ⚠️ 둘 이상이면 예외다. 아무거나 고르면 엉뚱한 상품을 건드린다.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        fresh = [t for t in pages(port, host) if t.get("id") not in before]
        if len(fresh) > 1:
            raise CdpError(f"새 탭이 {len(fresh)}개 열렸습니다. "
                           f"어느 것인지 알 수 없어 중단합니다.")
        if len(fresh) == 1 and fresh[0].get("webSocketDebuggerUrl"):
            return fresh[0]
        time.sleep(0.4)
    raise CdpError("클릭 뒤에 새 탭이 열리지 않았습니다")
