# -*- coding: utf-8 -*-
"""
tpc_dom.py
TPC(Trip.com / vBooking) 화면 조작. **화면을 아는 코드는 여기 한 벌만 둔다.**

마감·오픈·수집이 같은 화면을 쓰는데 각자 찾는 방법을 들고 있으면 반드시 어긋난다
(2026-09-09 MRT 가 날짜 열을 두 벌로 찾다가 4건 전멸했다). 그래서 '찾기' 는 전부
여기 있고, tpc.py 는 무엇을 할지만 정한다.

2026-09-09 실제 로그인 화면에서 확인한 것들 (추측 아님)
    상품 목록   [data-testid="vendorProductName"] 내부명칭 검색
                결과 행 tr[data-row-key="<상품번호>"]
                행 안의 "Supplier product ID: ...-<내부명칭>"
                행 안의 .product-name-text.clickable  -> 새 탭으로 상품 수정
    상품 수정   [data-testid="step-tab-PriceAndStock"]  Package and pricing
                [data-testid="package-item-<패키지번호>-btn"]  왼쪽 패키지 목록
                [data-testid="date-cell-YYYYMMDD"]  달력 한 칸
                    칸 안 .sale-txt 마다 대상(Adult/Child/Infant)과
                    button[role=switch][aria-checked]  <- 판매 상태의 진짜 근거
                [data-testid="calendar-prev-month-btn"] / -next-
                [data-testid="set-sale-btn"]   On/off 창
                .save-btn (Save) / .submit-audit-btn (Submit)
    On/off 창   [data-testid="sale-modal-container"]
                [data-testid="pro-cascader"]  패키지 여러 개 선택
                    드롭다운 .ant-cascader-dropdown
                    항목 [role="menuitemcheckbox"][aria-checked]
                    .check-all-handle  Select All
                [data-testid="crowd-filter"] input[value="Adult"|"Child"|"Infant"]
                #sale input[value="0"] 마감 / input[value="1"] 오픈
                'Set by Week' / 'Set by Date' 탭, 날짜는 td[title="YYYY-MM-DD"]
                푸터 Cancel / OK

⚠️ antd 는 el.click() 으로 안 열리는 것이 있다 (캐스케이더 드롭다운, Select All).
   그래서 사람이 누르는 것과 같은 pointer/mouse 순서를 만들어 보낸다 — __mclick.

⚠️ 좌표를 쓰지 않는다. 화면이 조금만 바뀌어도 엉뚱한 곳을 누른다.

⚠️ **화면 글자에 기대지 않는다 — vBooking 은 UI 언어가 바뀐다**
   2026-09-09 오후, 같은 계정 같은 Chrome 인데 화면이 영어에서 중국어로 바뀌었다.
   그 순간 'Search' 로 버튼을 찾던 코드가 전부 죽었다 (查询).

       Search -> 查询      Cancel -> 取 消     OK -> 确 定
       Not now -> 稍后处理  Set by Date -> 按日期设置   Not set -> (다른 말)

   그래서 여기서는 글자 대신 구조로 찾는다.

       검색 버튼     .topSearchList button.ant-btn-primary   (그 안에 하나뿐)
       팝업 닫기     그 모달 푸터의 default(비-primary) 버튼  = Not now/稍后处理
                     (primary 는 '去维护/Manage' 라서 다른 화면으로 넘어간다)
       OK / Cancel   .ant-modal-footer 의 .ant-btn-primary / 나머지
       주/날짜 선택  라디오 value="1"(주) / value="2"(날짜)  — #sale 바깥
       마감/오픈     #sale 안의 라디오 value="0" / value="1"
       판매 상태     button[role=switch] 의 aria-checked
       안 파는 날    .date-detail.empty          (Not set 이라는 글자가 아니라)

   글자를 쓰는 곳이 남아 있다면 그건 버그다.
"""
from __future__ import annotations

import json
import re
import time
from typing import Optional

from shared.cdp_page import CdpError, CdpPage

# ──────────────────────────────────────────────────────────────────────────────
# 페이지에 심는 도우미
# ──────────────────────────────────────────────────────────────────────────────
JS_BOOT = r"""
(() => {
  const W = window;
  W.__tpc = W.__tpc || {};

  // 사람이 누르는 것과 같은 순서. antd 는 mousedown 을 봐야 열리는 것이 있다.
  W.__tpc.mclick = function (el) {
    if (!el) return false;
    const r = el.getBoundingClientRect();
    const o = {bubbles: true, cancelable: true, view: W, button: 0,
               clientX: r.left + Math.min(20, Math.max(1, r.width / 2)),
               clientY: r.top + Math.max(1, r.height / 2)};
    for (const ty of ['pointerover','pointermove','mouseover','mousemove',
                      'pointerdown','mousedown','focus','pointerup','mouseup','click']) {
      const Ctor = ty.startsWith('pointer') ? PointerEvent
                 : ty === 'focus' ? FocusEvent : MouseEvent;
      try { el.dispatchEvent(new Ctor(ty, o)); } catch (e) { /* focus 는 없어도 그만 */ }
    }
    return true;
  };

  W.__tpc.visibleModals = function () {
    return [...document.querySelectorAll('.ant-modal-wrap')]
      .filter(e => getComputedStyle(e).display !== 'none');
  };

  W.__tpc.saleModal = function () {
    return document.querySelector('[data-testid="sale-modal-container"]');
  };

  // 검색 버튼. 글자('Search'/'查询')가 아니라 검색칸을 담은 상자로 찾는다.
  W.__tpc.searchButton = function () {
    const box = document.querySelector('.topSearchList');
    if (box) {
      const b = box.querySelector('button.ant-btn-primary');
      if (b) return b;
    }
    // .topSearchList 가 없어진 화면 변형 대비 — 검색칸의 조상 중
    // primary 버튼을 딱 하나만 가진 가장 가까운 상자.
    let e = document.querySelector('[data-testid="vendorProductName"]');
    for (let i = 0; i < 8 && e; i++) {
      e = e.parentElement;
      if (!e) break;
      const found = e.querySelectorAll('button.ant-btn-primary');
      if (found.length === 1) return found[0];
      if (found.length > 1) break;
    }
    return null;
  };

  // 모달 푸터의 확인 / 취소. 글자가 아니라 antd 의 버튼 종류로 가른다.
  W.__tpc.footerButtons = function (wrap) {
    const box = wrap.querySelector('.ant-modal-footer') || wrap;
    const btns = [...box.querySelectorAll('button')];
    return {
      confirm: btns.find(b => b.classList.contains('ant-btn-primary')) || null,
      cancel: btns.find(b => !b.classList.contains('ant-btn-primary')) || null,
      all: btns,
    };
  };

  // 우리가 다루는 창인가 (판매 On/off, 가격·재고). 그 밖의 것은 안내 팝업이다.
  W.__tpc.isOurModal = function (wrap) {
    return !!(wrap.querySelector('[data-testid="sale-modal-container"]')
              || wrap.querySelector('[data-testid="batch-set-price-stock-container"]'));
  };

  // 열려 있는 선택 드롭다운에서 항목 하나를 고른다.
  //
  // ⚠️ 이 목록은 가상 스크롤이라 화면에 보이는 것만 DOM 에 있다.
  //    (월 목록이 12개가 아니라 11개로 보였던 이유가 이것이다)
  //    그래서 '몇 번째' 로 고르면 안 되고, 안 보이면 굴려서 찾아야 한다.
  //
  // want.digits : 항목 글자에서 숫자만 뽑아 비교 (연도, 그리고 '9 月' 형태의 월)
  // want.names  : 그 숫자가 없을 때 쓸 이름들 ('Sep', 'September')
  W.__tpc.optionStep = function (want) {
    const dd = [...document.querySelectorAll('.ant-select-dropdown')]
      .filter(e => !e.classList.contains('ant-select-dropdown-hidden')).pop();
    if (!dd) return 'no-dropdown';
    const opts = [...dd.querySelectorAll('.ant-select-item-option')];
    const hit = opts.find(o => {
      const t = (o.getAttribute('title') || o.textContent || '').trim();
      const digits = t.replace(/\D/g, '');
      if (digits) return digits === String(want.digits);
      return (want.names || []).some(
        n => t.toLowerCase().startsWith(String(n).toLowerCase()));
    });
    if (hit) {
      hit.scrollIntoView({block: 'center'});
      __tpc.mclick(hit);
      return true;
    }
    const holder = dd.querySelector('.rc-virtual-list-holder');
    if (!holder) return 'not-found';
    const before = holder.scrollTop;
    holder.scrollTop = before + Math.max(40, holder.clientHeight - 24);
    holder.dispatchEvent(new Event('scroll', {bubbles: true}));
    if (holder.scrollTop === before) {
      if (holder.scrollTop === 0) return 'not-found';
      holder.scrollTop = 0;                       // 끝까지 갔으면 처음부터 한 바퀴
      holder.dispatchEvent(new Event('scroll', {bubbles: true}));
    }
    return 'scrolled';
  };

  W.__tpc.dropdown = function () {
    return [...document.querySelectorAll('.ant-cascader-dropdown')]
      .filter(e => !e.classList.contains('ant-select-dropdown-hidden')).pop() || null;
  };

  // 달력 한 칸의 상태. 판매 여부의 근거는 오직 switch 의 aria-checked 다.
  W.__tpc.readCell = function (ymd) {
    const el = document.querySelector('[data-testid="date-cell-' + ymd + '"]');
    if (!el) return null;
    const text = el.innerText.split('\n').join(' | ');
    const rows = [...el.querySelectorAll('.sale-txt')].map(r => {
      const sw = r.querySelector('button[role=switch]');
      const label = ((r.querySelector('.res-txt') || {}).innerText || '').trim();
      return {cat: label, on: sw ? sw.getAttribute('aria-checked') === 'true' : null};
    });
    const detail = el.querySelector('.date-detail');
    // ⚠️ 'Not set' / 'Unlimited stock' 은 화면 언어를 타는 글자다. 판정에 쓰지 않는다.
    //    그날 팔 것이 있는지는 .date-detail 의 empty 클래스가 알려 준다.
    //    아래 두 값은 사람이 로그를 읽을 때 참고하라고 남기는 것뿐이다.
    const sold = (text.match(/(\d[\d,]*)\s*\/\s*(\d[\d,]*)/) || [])[0] || null;
    return {
      text: text,
      rows: rows,
      empty: !!(detail && detail.classList.contains('empty')),
      unlimited: /Unlimited|无限/.test(text),
      sold: sold,
    };
  };

  W.__tpc.monthsShown = function () {
    const ids = [...document.querySelectorAll('[data-testid^="date-cell-"]')]
      .map(e => e.getAttribute('data-testid').slice(-8))
      .filter(x => /^\d{8}$/.test(x));
    return ids.length ? {first: ids[0], last: ids[ids.length - 1], n: ids.length} : null;
  };

  // 달력 제목. ⚠️ 판정에 쓰지 않는다 — 화면 언어에 따라 'Sep 2026' 이기도
  // '2026年9月' 이기도 하다. 로그를 사람이 읽을 때만 참고한다.
  W.__tpc.calendarTitle = function () {
    const b = document.querySelector('[data-testid="calendar-prev-month-btn"]');
    if (!b) return null;
    let e = b;
    for (let i = 0; i < 5 && e; i++) {
      e = e.parentElement;
      if (!e) break;
      const m = (e.innerText || '').match(/([A-Z][a-z]{2})\s+(\d{4})/);
      if (m) return m[0];
    }
    return null;
  };

  return true;
})();
"""

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def boot(page: CdpPage) -> None:
    """
    도우미를 페이지에 심는다.

    ⚠️ 한 번만 넣으면 안 된다. 새 탭은 심은 직후에도 아직 이동 중일 수 있고,
       Submit 뒤 새로고침하면 window 가 통째로 새로 만들어져서 사라진다.
       그래서 '앞으로 열릴 문서마다 자동으로' 넣어 두고(addScriptToEvaluateOnNewDocument),
       지금 문서에도 한 번 넣는다.
       (처음엔 이걸 안 해서 collect 첫 실행이 '__tpc is not defined' 로 죽었다)
    """
    try:
        page.send("Page.enable")
        page.send("Page.addScriptToEvaluateOnNewDocument", source=JS_BOOT)
    except CdpError:
        pass                                   # 못 심어도 아래 즉시 주입으로 굴러간다
    ensure(page)


def ensure(page: CdpPage, timeout: float = 60.0) -> None:
    """문서가 준비되고 도우미가 살아 있을 때까지. 없으면 다시 심는다."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if page.js(r"""(typeof __tpc !== 'undefined' && !!__tpc.mclick)"""):
                return
            page.js(JS_BOOT)
            if page.js(r"""(typeof __tpc !== 'undefined' && !!__tpc.mclick)"""):
                return
        except CdpError:
            pass                               # 이동 중이면 컨텍스트가 잠깐 없다
        time.sleep(0.5)
    raise CdpError("페이지에 도우미를 심지 못했습니다")


def ymd_compact(date_str: str) -> str:
    return date_str.replace("-", "")


# ──────────────────────────────────────────────────────────────────────────────
# 1) 상품 목록 — 이중검색 (내부명칭으로 찾고, 상품번호로 확인한다)
# ──────────────────────────────────────────────────────────────────────────────
def search_by_name(page: CdpPage, name: str, log=lambda *_: None) -> None:
    """검색창에 내부명칭만 넣고 Search. 상품번호 칸은 반드시 비운다."""
    page.wait(r"""(() => {
        const e = document.querySelector('[data-testid="vendorProductName"]');
        return !!(e && e.offsetParent !== null);
      })()""", timeout=60, what="상품 검색 화면")
    ensure(page)
    esc = _js_str(name)
    ok = page.js(r"""(() => {
        const set = (el, v) => {
          const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement : HTMLInputElement;
          Object.getOwnPropertyDescriptor(proto.prototype, 'value').set.call(el, v);
          el.dispatchEvent(new Event('input', {bubbles: true}));
          el.dispatchEvent(new Event('change', {bubbles: true}));
        };
        const id = document.querySelector('[data-testid="mixProductId"]');
        const nm = document.querySelector('[data-testid="vendorProductName"]');
        if (!nm) return 'no-name-input';
        if (id) set(id, '');
        set(nm, %s);
        return 'filled';
      })()""" % esc)
    if ok != "filled":
        raise CdpError(f"검색창을 채우지 못했습니다 ({ok})")
    # 값이 실제로 들어갔는지 확인하고 나서 누른다
    page.wait(r"""document.querySelector('[data-testid="vendorProductName"]').value === %s""" % esc,
              timeout=10, what="검색어 입력 확인")
    # ⚠️ 입력칸이 떴다고 화면이 다 그려진 것은 아니다. 검색 버튼은 조금 늦게 붙는다.
    #    2026-09-09 전체 dry-run 에서 일본 상품 3건이 여기서 죽었다
    #    (같은 상품이 조금 전 수집에서는 멀쩡히 됐다).
    #    그리고 글자로 찾으면 안 된다 — 그날 오후 화면이 중국어로 바뀌면서
    #    'Search' 가 '查询' 이 됐다. 검색칸을 담은 .topSearchList 안의
    #    primary 버튼 하나가 검색 버튼이다.
    page.wait(r"""(() => {
        const b = __tpc.searchButton();
        return !!(b && !b.disabled);
      })()""", timeout=60, what="검색 버튼")
    clicked = page.js(r"""(() => {
        const b = __tpc.searchButton();
        if (!b) return 'no-button';
        __tpc.mclick(b);
        return 'ok';
      })()""")
    if clicked != "ok":
        raise CdpError("검색 버튼을 찾지 못했습니다")
    log(f"검색: 내부명칭 = {name}")
    time.sleep(2.0)


def rows_snapshot(page: CdpPage) -> list[dict]:
    return page.js(r"""(() => [...document.querySelectorAll('tr[data-row-key]')].map(tr => ({
        id: tr.getAttribute('data-row-key'),
        supplier: ((tr.querySelector('.product-status-vbk') || {}).innerText || '')
                    .split('\n').join(' ').trim(),
        title: ((tr.querySelector('.product-name-text') || {}).innerText || '').trim(),
      })))()""") or []


def pick_row(page: CdpPage, name: str, product_id: str,
             timeout: float = 40.0) -> dict:
    """
    검색 결과에서 지정 상품번호 행을 고른다. — 이중검색의 두 번째 단계.

    ⚠️ 못 찾으면 '없다' 가 아니라 예외다. 이름은 맞는데 번호가 없으면 상품이
       바뀌었거나 권한이 달라진 것이고, 그건 사람이 봐야 한다.
    """
    deadline = time.monotonic() + timeout
    rows: list[dict] = []
    while time.monotonic() < deadline:
        rows = rows_snapshot(page)
        if rows:
            break
        time.sleep(0.6)
    if not rows:
        raise CdpError(f"'{name}' 검색 결과가 한 건도 없습니다")

    hit = [r for r in rows if str(r.get("id")) == str(product_id)]
    if len(hit) > 1:
        raise CdpError(f"상품번호 {product_id} 행이 {len(hit)}개입니다")
    if not hit:
        got = ", ".join(str(r.get("id")) for r in rows[:8])
        raise CdpError(f"'{name}' 검색 결과 {len(rows)}건에 상품번호 {product_id} 가 "
                       f"없습니다 (나온 번호: {got})")
    row = hit[0]
    # 이름 쪽도 서로 확인한다. 화면 표기는 "Supplier product ID: <무엇>-<내부명칭>".
    supplier = str(row.get("supplier") or "")
    if name.strip() and name.strip() not in supplier:
        raise CdpError(f"상품번호 {product_id} 행의 내부명칭이 다릅니다 "
                       f"(기대 '{name}' / 화면 '{supplier[:80]}')")
    return row


def open_edit_from_row(page: CdpPage, product_id: str) -> None:
    """그 행의 상품명을 눌러 상품 수정 탭을 연다 (URL 직행 금지 — 이중검색의 뜻이 없어진다)."""
    r = page.js(r"""(() => {
        const tr = document.querySelector('tr[data-row-key="%s"]');
        if (!tr) return 'no-row';
        const a = tr.querySelector('.product-name-text.clickable');
        if (!a) return 'no-link';
        __tpc.mclick(a);
        return 'ok';
      })()""" % product_id)
    if r != "ok":
        raise CdpError(f"상품 수정 링크를 누르지 못했습니다 ({r})")


# ──────────────────────────────────────────────────────────────────────────────
# 2) 상품 수정 화면
# ──────────────────────────────────────────────────────────────────────────────
def dismiss_notices(page: CdpPage) -> int:
    """
    상품 수정 화면에 뜨는 안내 팝업을 닫는다 ('번역을 갱신하세요' 류).

    ⚠️ 우리가 여는 창(판매 On/off, 가격·재고)은 건드리지 않는다.
    ⚠️ 그 팝업의 **default(비-primary) 버튼**만 누른다. 그게 'Not now / 稍后처리'다.
       primary 는 'Manage / 去维护' 라서 누르면 다른 화면으로 끌려간다.
       글자로 고르면 안 된다 — 화면이 중국어로 바뀌면 못 찾는다.
    """
    return page.js(r"""(() => {
        let n = 0;
        for (const w of __tpc.visibleModals()) {
          if (__tpc.isOurModal(w)) continue;
          // 진짜 푸터가 있는 창만. 푸터가 없으면 어떤 버튼이 '나중에' 인지
          // 알 수 없으므로 손대지 않는다.
          if (!w.querySelector('.ant-modal-footer')) continue;
          const f = __tpc.footerButtons(w);
          if (f.cancel && f.all.length <= 3) { __tpc.mclick(f.cancel); n++; }
        }
        return n;
      })()""") or 0


def assert_product(page: CdpPage, product_id: str) -> None:
    """진입한 페이지가 정말 그 상품인지. (KKday 의 PRODUCT_MISMATCH 가드와 같은 것)"""
    url = page.url()
    if f"productId={product_id}" not in url:
        raise CdpError(f"상품 불일치: 기대 {product_id} / 실제 URL {url[:120]}")


def goto_pricing(page: CdpPage, log=lambda *_: None, timeout: float = 90.0) -> None:
    """Package and pricing 탭으로. 패키지 목록이 실제로 그려질 때까지 기다린다."""
    ensure(page)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        dismiss_notices(page)
        n = page.js(r"""document.querySelectorAll('[data-testid^="package-item-"]').length""") or 0
        if n:
            log(f"Package and pricing 진입 (패키지 {n}개)")
            return
        page.js(r"""(() => {
            const t = document.querySelector('[data-testid="step-tab-PriceAndStock"]');
            if (t) __tpc.mclick(t);
            return !!t;
          })()""")
        time.sleep(1.5)
    raise CdpError("Package and pricing 의 패키지 목록이 뜨지 않았습니다")


def packages(page: CdpPage) -> list[dict]:
    return page.js(r"""(() => [...document.querySelectorAll('[data-testid^="package-item-"]')].map(e => ({
        id: e.getAttribute('data-testid').replace(/^package-item-|-btn$/g, ''),
        name: e.innerText.trim().split('\n')[0].trim(),
        raw: e.innerText.trim().split('\n').join(' '),
        selected: e.classList.contains('selected'),
      })))()""") or []


def select_package(page: CdpPage, pkg_id: str, timeout: float = 30.0) -> None:
    r = page.js(r"""(() => {
        const e = document.querySelector('[data-testid="package-item-%s-btn"]');
        if (!e) return 'missing';
        __tpc.mclick(e);
        return 'ok';
      })()""" % pkg_id)
    if r != "ok":
        raise CdpError(f"패키지 {pkg_id} 를 고르지 못했습니다")
    page.wait(r"""(() => {
        const e = document.querySelector('[data-testid="package-item-%s-btn"]');
        return !!(e && e.classList.contains('selected')
                  && document.querySelector('[data-testid="package-card-%s"]'));
      })()""" % (pkg_id, pkg_id), timeout=timeout, what=f"패키지 {pkg_id} 선택")


# ── 달력 ─────────────────────────────────────────────────────────────────────
def month_of(date_str: str) -> str:
    y, m, _d = date_str.split("-")
    return f"{MONTHS[int(m) - 1]} {y}"


def goto_month(page: CdpPage, date_str: str, log=lambda *_: None,
               limit: int = 26) -> None:
    """
    대상 날짜 칸이 나올 때까지 달력을 옮긴다.

    ⚠️ 달력 제목('Sep 2026')을 읽어서 판단하면 안 된다. 화면 언어가 바뀌면
       '2026年9月' 이 되어 그 순간 못 읽는다 (2026-09-09 오후에 실제로 그랬다).
       대신 지금 그려진 칸의 **data-testid(date-cell-YYYYMMDD)** 를 본다.
       이건 언어와 무관하다.

    ⚠️ 몇 번 누를지를 미리 계산해서 그만큼 누르면 안 된다. 화면이 늦게 그려지면
       그대로 어긋난다. 매번 지금 무엇이 그려져 있는지 읽고 판단한다.
    """
    cell = ymd_compact(date_str)
    for _ in range(limit):
        shown = page.settle(r"""JSON.stringify(__tpc.monthsShown())""",
                            timeout=25, what="달력")
        try:
            span = json.loads(shown) if shown and shown != "null" else None
        except ValueError:
            span = None
        if not span:
            raise CdpError("달력에 날짜 칸이 하나도 없습니다")
        # 앞뒤 달 꼬리까지 포함해서, 대상 날짜가 이미 그려져 있으면 그대로 쓴다.
        if span["first"] <= cell <= span["last"]:
            page.wait(
                r"""!!document.querySelector('[data-testid="date-cell-%s"]')""" % cell,
                timeout=20, what=f"{date_str} 칸")
            return
        btn = ("calendar-next-month-btn" if cell > span["last"]
               else "calendar-prev-month-btn")
        moved = page.js(r"""(() => {
            const b = document.querySelector('[data-testid="%s"]');
            if (b) __tpc.mclick(b);
            return !!b;
          })()""" % btn)
        if not moved:
            raise CdpError("달력의 달 이동 버튼이 없습니다")
        time.sleep(1.0)
    raise CdpError(f"달력을 {date_str} 가 보이는 달로 옮기지 못했습니다")


def read_cell(page: CdpPage, date_str: str) -> Optional[dict]:
    return page.js(r"""__tpc.readCell('%s')""" % ymd_compact(date_str))


def read_cell_settled(page: CdpPage, date_str: str, timeout: float = 25.0) -> dict:
    """
    같은 값이 두 번 연속 나올 때까지 읽는다.

    ⚠️ 그리는 중에 읽으면 스위치가 없는 것처럼 보인다. 그걸 '판매 안 함' 으로
       단정하면 열린 재고가 그대로 남는다.
    """
    js = r"""JSON.stringify(__tpc.readCell('%s'))""" % ymd_compact(date_str)
    txt = page.settle(js, timeout=timeout, what=f"{date_str} 칸")
    import json as _json
    val = _json.loads(txt) if txt and txt != "null" else None
    if val is None:
        raise CdpError(f"{date_str} 칸이 화면에 없습니다")
    return val


def cell_verdict(cell: dict) -> tuple[str, list[str]]:
    """
    칸 하나를 (상태, 켜져 있는 대상들) 로 읽는다.

        on          하나라도 판매중         -> 마감 대상
        off         전부 꺼져 있음          -> 이미 마감
        not_set     가격/재고 자체가 없음   -> 그날 안 파는 패키지
        no_switch   스위치를 못 봄          -> 판단 불가 (스킵 아님, 실패다)
    """
    rows = cell.get("rows") or []
    switches = [r for r in rows if r.get("on") is not None]
    # ⚠️ 순서가 중요하다. '가격/재고가 아예 없는 날'(.date-detail.empty) 은
    #    스위치가 꺼진 채로 붙어 있기도 하다. 그걸 '마감됨' 으로 세면
    #    오픈이 그날 안 파는 패키지를 열려고 든다.
    if cell.get("empty"):
        return "not_set", []
    if not switches:
        return "no_switch", []
    on = [str(r.get("cat") or "") for r in switches if r.get("on")]
    return ("on" if on else "off"), on


# ──────────────────────────────────────────────────────────────────────────────
# 3) On/off 창
# ──────────────────────────────────────────────────────────────────────────────
def open_sale_dialog(page: CdpPage, log=lambda *_: None) -> None:
    r = page.js(r"""(() => {
        const b = document.querySelector('[data-testid="set-sale-btn"]');
        if (!b) return 'missing';
        __tpc.mclick(b);
        return 'ok';
      })()""")
    if r != "ok":
        raise CdpError("On/off 버튼이 없습니다")
    page.wait(r"""!!__tpc.saleModal()""", timeout=30, what="On/off 창")
    log("On/off 창 열림")


def cancel_dialogs(page: CdpPage, times: int = 4) -> None:
    for _ in range(times):
        n = page.js(r"""(() => {
            const ws = __tpc.visibleModals();
            if (!ws.length) return 0;
            const w = ws[ws.length - 1];
            const f = __tpc.footerButtons(w);
            if (f.cancel) __tpc.mclick(f.cancel);
            return ws.length;
          })()""") or 0
        if not n:
            return
        time.sleep(0.8)


def dialog_state(page: CdpPage) -> dict:
    import json as _json
    txt = page.js(r"""(() => {
        const m = __tpc.saleModal();
        if (!m) return 'null';
        const dd = __tpc.dropdown();
        return JSON.stringify({
          selected: [...m.querySelectorAll('.ant-select-selection-item')]
                      .map(e => (e.getAttribute('title') || e.innerText).trim()),
          crowd: [...m.querySelectorAll('[data-testid="crowd-filter"] input')]
                   .map(e => ({v: e.value, on: e.checked})),
          operation: [...m.querySelectorAll('#sale input')]
                       .map(e => ({v: e.value, on: e.checked})),
          menu: dd ? [...dd.querySelectorAll('[role="menuitemcheckbox"]')]
                       .map(e => ({name: e.innerText.trim(),
                                   on: e.getAttribute('aria-checked') === 'true'})) : null,
          dates: [...m.querySelectorAll('td.ant-picker-cell')]
                   .filter(e => e.className.includes('selected'))
                   .map(e => e.getAttribute('title')),
          has_date_panel: !!m.querySelector('td.ant-picker-cell'),
        });
      })()""")
    return _json.loads(txt) if txt and txt != "null" else {}


def dialog_select_all_packages(page: CdpPage, log=lambda *_: None,
                               timeout: float = 30.0) -> list[str]:
    """
    'Select setting range' 에서 그 상품의 패키지를 전부 고른다.

    Select All 을 먼저 누르고, 그래도 안 켜진 것이 있으면 항목을 하나씩 누른다.
    끝나고 aria-checked 로 전부 켜졌는지 확인한다 — 누른 것이 아니라 켜진 것이 근거다.
    """
    page.js(r"""(() => {
        const c = document.querySelector('[data-testid="pro-cascader"] .ant-select-selector');
        if (c) __tpc.mclick(c);
        return !!c;
      })()""")
    page.wait(r"""(() => { const d = __tpc.dropdown();
        return !!(d && d.querySelectorAll('[role="menuitemcheckbox"]').length); })()""",
              timeout=timeout, what="패키지 드롭다운")

    page.js(r"""(() => {
        const d = __tpc.dropdown();
        const h = d && (d.querySelector('.check-all-handle label')
                        || d.querySelector('.check-all-handle'));
        if (h) __tpc.mclick(h);
        return !!h;
      })()""")
    time.sleep(1.0)

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        left = page.js(r"""(() => {
            const d = __tpc.dropdown();
            if (!d) return -1;
            const items = [...d.querySelectorAll('[role="menuitemcheckbox"]')];
            const off = items.filter(e => e.getAttribute('aria-checked') !== 'true');
            if (off.length) __tpc.mclick(off[0]);
            return off.length;
          })()""")
        if left == 0:
            break
        if left == -1:
            raise CdpError("패키지 드롭다운이 닫혔습니다")
        time.sleep(0.7)
    else:
        raise CdpError("패키지를 전부 고르지 못했습니다")

    names = page.js(r"""(() => {
        const d = __tpc.dropdown();
        return [...d.querySelectorAll('[role="menuitemcheckbox"]')].map(e => e.innerText.trim());
      })()""") or []
    # 드롭다운을 닫는다 (열린 채로 두면 뒤의 날짜 칸을 가린다)
    page.js(r"""(() => {
        // 드롭다운을 닫는다. 글자로 라벨을 찾지 않고, 창 안에서 아무것도
        // 바꾸지 않는 자리(마감/오픈 라디오 묶음의 여백)를 누른다.
        const m = __tpc.saleModal();
        const anchor = m.querySelector('#sale') || m;
        __tpc.mclick(anchor);
        return true;
      })()""")
    time.sleep(0.8)
    log(f"패키지 {len(names)}개 전체 선택")
    return names


def dialog_select_packages(page: CdpPage, names: list[str],
                           log=lambda *_: None, timeout: float = 40.0) -> list[str]:
    """
    이름으로 고른 패키지만 켜고 나머지는 끈다.

    전체 마감에는 쓰지 않는다 (그건 Select All 이다). 되돌리기처럼 '아까 켜져
    있던 것만' 다시 손대야 할 때 쓴다. 이름이 하나라도 화면에 없으면 예외다 —
    비슷한 이름을 골라 주면 엉뚱한 패키지를 건드린다.
    """
    import json as _json
    page.js(r"""(() => {
        const c = document.querySelector('[data-testid="pro-cascader"] .ant-select-selector');
        if (c) __tpc.mclick(c);
        return !!c;
      })()""")
    page.wait(r"""(() => { const d = __tpc.dropdown();
        return !!(d && d.querySelectorAll('[role="menuitemcheckbox"]').length); })()""",
              timeout=timeout, what="패키지 드롭다운")

    have = page.js(r"""(() => { const d = __tpc.dropdown();
        return [...d.querySelectorAll('[role="menuitemcheckbox"]')].map(e => e.innerText.trim()); })()""") or []
    missing = [n for n in names if n not in have]
    if missing:
        raise CdpError(f"창에 없는 패키지: {missing}")

    want = _json.dumps(list(names), ensure_ascii=False)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        left = page.js(r"""(() => {
            const want = new Set(%s);
            const d = __tpc.dropdown();
            if (!d) return -1;
            const items = [...d.querySelectorAll('[role="menuitemcheckbox"]')];
            const wrong = items.filter(e =>
              (e.getAttribute('aria-checked') === 'true') !== want.has(e.innerText.trim()));
            if (wrong.length) __tpc.mclick(wrong[0]);
            return wrong.length;
          })()""" % want)
        if left == 0:
            break
        if left == -1:
            raise CdpError("패키지 드롭다운이 닫혔습니다")
        time.sleep(0.7)
    else:
        raise CdpError("지정한 패키지만 고르지 못했습니다")

    page.js(r"""(() => {
        // 드롭다운을 닫는다. 글자로 라벨을 찾지 않고, 창 안에서 아무것도
        // 바꾸지 않는 자리(마감/오픈 라디오 묶음의 여백)를 누른다.
        const m = __tpc.saleModal();
        const anchor = m.querySelector('#sale') || m;
        __tpc.mclick(anchor);
        return true;
      })()""")
    time.sleep(0.8)
    log(f"패키지 {len(names)}개 지정 선택: {', '.join(names)[:80]}")
    return list(names)


def dialog_check_all_crowds(page: CdpPage, log=lambda *_: None) -> list[str]:
    """
    'Apply to' 의 대상(Adult/Child/Infant)을 전부 켠다.

    패키지마다 대상 구성이 다르다 (Adult/Child, +Infant, 아예 없는 인원별 전세도 있다).
    화면에 있는 것만 켜고, 몇 개를 켰는지 돌려준다.
    """
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        left = page.js(r"""(() => {
            const m = __tpc.saleModal();
            const boxes = [...m.querySelectorAll('[data-testid="crowd-filter"] input')];
            const off = boxes.filter(e => !e.checked);
            if (off.length) __tpc.mclick(off[0].closest('label') || off[0]);
            return off.length;
          })()""")
        if left == 0:
            break
        time.sleep(0.5)
    got = page.js(r"""(() => {
        const m = __tpc.saleModal();
        return [...m.querySelectorAll('[data-testid="crowd-filter"] input')]
          .filter(e => e.checked).map(e => e.value);
      })()""") or []
    log(f"적용 대상: {', '.join(got) if got else '(대상 구분 없음)'}")
    return got


def dialog_set_operation(page: CdpPage, close: bool, log=lambda *_: None) -> None:
    """마감(0) / 오픈(1). 값으로 고르고 값으로 확인한다 — 글자가 아니라."""
    want = "0" if close else "1"
    page.js(r"""(() => {
        const m = __tpc.saleModal();
        const el = m.querySelector('#sale input[value="%s"]');
        if (el) __tpc.mclick(el.closest('label') || el);
        return !!el;
      })()""" % want)
    page.wait(r"""(() => {
        const m = __tpc.saleModal();
        const el = m.querySelector('#sale input[value="%s"]');
        return !!(el && el.checked);
      })()""" % want, timeout=15,
              what="Close sales 선택" if close else "Open sales 선택")
    log("마감(Close sales) 선택" if close else "오픈(Open sales) 선택")


def dialog_set_by_date(page: CdpPage, date_str: str, log=lambda *_: None) -> None:
    """
    'Set by Date' 로 바꾸고 그 날짜 하나만 고른다.

    ⚠️ 여기가 이 봇에서 가장 위험한 자리다. 창은 기본이 'Set by Week' 이고
       거기에는 'Everyday' 가 있다. 날짜 탭으로 못 넘어간 채 OK 를 누르면
       그 상품의 **모든 날짜**가 닫힌다. 그래서 누르기 전에
       (1) 날짜 패널이 떴는지 (2) 고른 날짜가 정확히 하나이고 그게 대상인지
       둘 다 확인하고, 아니면 OK 를 아예 누르지 않는다.
    """
    # 주/날짜 선택은 라디오다. value="1" 이 '주 단위', value="2" 가 '날짜 지정'.
    # #sale(마감/오픈) 라디오에도 value="1" 이 있으므로 그 밖의 것만 본다.
    page.js(r"""(() => {
        const m = __tpc.saleModal();
        const el = [...m.querySelectorAll('input[type=radio][value="2"]')]
          .find(e => !e.closest('#sale'));
        if (el) __tpc.mclick(el.closest('label') || el);
        return !!el;
      })()""")
    page.wait(r"""(() => {
        const m = __tpc.saleModal();
        if (!m) return false;
        const el = [...m.querySelectorAll('input[type=radio][value="2"]')]
          .find(e => !e.closest('#sale'));
        return !!(el && el.checked && m.querySelector('td.ant-picker-cell'));
      })()""", timeout=20, what="날짜 지정(Set by Date) 전환")

    _dialog_goto_month(page, date_str)

    r = page.js(r"""(() => {
        const m = __tpc.saleModal();
        const td = m.querySelector('td[title="%s"]');
        if (!td) return 'missing';
        if (td.className.includes('disabled')) return 'disabled';
        __tpc.mclick(td.querySelector('.ant-picker-cell-inner') || td);
        return 'ok';
      })()""" % date_str)
    if r == "disabled":
        raise CdpError(f"{date_str} 는 창에서 고를 수 없는 날짜입니다 (지난 날짜)")
    if r != "ok":
        raise CdpError(f"창의 달력에 {date_str} 칸이 없습니다")

    page.wait(r"""(() => {
        const m = __tpc.saleModal();
        const sel = [...m.querySelectorAll('td.ant-picker-cell')]
          .filter(e => e.className.includes('selected')).map(e => e.getAttribute('title'));
        return sel.length === 1 && sel[0] === '%s';
      })()""" % date_str, timeout=15, what=f"{date_str} 한 날짜만 선택")
    log(f"Set by Date: {date_str}")


def _dialog_goto_month(page: CdpPage, date_str: str) -> None:
    """
    창 안 달력을 대상 날짜가 보이는 달로 옮긴다.

    ⚠️ 이 달력에는 앞/뒤 화살표가 없다. 위에 연·월 선택기 두 개뿐이다
       (2026-09-09 실제 화면 확인: .ant-picker-calendar-mini, 버튼 0개).
       그래서 화살표를 찾다가 실패하면 안 되고, 처음부터 선택기로 간다.

    대부분은 여기까지 올 일이 없다. 대상이 내일이면 기본으로 열린 달에 이미
    그 칸이 있고(앞뒤 달 꼬리까지 그려진다), 그때는 바로 돌아간다.
    """
    if _dialog_has_date(page, date_str):
        return
    _dialog_pick_year_month(page, date_str)
    if not _dialog_has_date(page, date_str):
        raise CdpError(f"창의 달력을 {date_str} 가 보이는 달로 옮기지 못했습니다")


def _dialog_has_date(page: CdpPage, date_str: str) -> bool:
    return bool(page.js(r"""(() => {
        const m = __tpc.saleModal();
        if (!m) return false;
        const td = m.querySelector('td[title="%s"]');
        return !!(td && !td.className.includes('disabled'));
      })()""" % date_str))


def _dialog_pick_year_month(page: CdpPage, date_str: str) -> None:
    """
    연 · 월 선택기로 달을 옮긴다.

    ⚠️ 월을 **글자로도 자리로도** 고르지 않는다.
       글자: 화면 언어에 따라 'Mar' 이기도 '3 月'(사이 공백!) 이기도 하다.
       자리: 목록이 가상 스크롤이라 12개 중 11개만 DOM 에 있다.
       그래서 숫자(있으면)로, 없으면 영어 이름으로 찾고, 안 보이면 굴려서 찾는다.
       (2027-03 으로 못 가서 왕복 시험이 여기서 멈췄던 자리다)
    """
    year, month, _day = date_str.split("-")

    have = page.js(r"""(() => {
        const m = __tpc.saleModal();
        const cal = m && m.querySelector('.ant-picker-calendar');
        return cal ? cal.querySelectorAll('.ant-select').length : 0;
      })()""") or 0
    if have < 2:
        raise CdpError("창의 달력에 연/월 선택기가 없습니다")

    for index, want in ((0, {"digits": int(year), "names": []}),
                        (1, {"digits": int(month), "names": [MONTHS[int(month) - 1]]})):
        opened = page.js(r"""(() => {
            const m = __tpc.saleModal();
            const cal = m.querySelector('.ant-picker-calendar');
            const sel = cal.querySelectorAll('.ant-select')[%d];
            if (!sel) return 'no-select';
            const cur = sel.querySelector('.ant-select-selection-item');
            const t = cur ? (cur.getAttribute('title') || cur.textContent).trim() : '';
            const looksYear = /^(19|20)\d{2}$/.test(t);
            if (%s !== looksYear) return 'wrong-select:' + t;
            __tpc.mclick(sel.querySelector('.ant-select-selector') || sel);
            return true;
          })()""" % (index, "true" if index == 0 else "false"))
        if opened is not True:
            raise CdpError(f"연/월 선택기를 열지 못했습니다 ({opened})")
        time.sleep(0.8)

        want_js = json.dumps(want, ensure_ascii=False)
        for _ in range(30):
            r = page.js(r"""__tpc.optionStep(%s)""" % want_js)
            if r is True:
                break
            if r == "no-dropdown":
                raise CdpError("연/월 목록이 열리지 않았습니다")
            if r == "not-found":
                raise CdpError(f"연/월 목록에서 {want['digits']} 를 찾지 못했습니다")
            time.sleep(0.35)
        else:
            raise CdpError(f"연/월 목록에서 {want['digits']} 를 고르지 못했습니다")
        time.sleep(1.0)


def dialog_ok(page: CdpPage, log=lambda *_: None, timeout: float = 60.0) -> None:
    """OK. 누른 뒤 창이 실제로 닫히는 것까지 확인한다."""
    r = page.js(r"""(() => {
        const m = __tpc.saleModal();
        if (!m) return 'no-modal';
        const wrap = m.closest('.ant-modal-wrap') || m.closest('.ant-modal-content');
        const b = __tpc.footerButtons(wrap).confirm;   // 글자가 아니라 primary 버튼
        if (!b) return 'missing';
        if (b.disabled) return 'disabled';
        __tpc.mclick(b);
        return 'ok';
      })()""")
    if r != "ok":
        raise CdpError(f"OK 를 누르지 못했습니다 ({r})")
    page.wait(r"""!__tpc.saleModal()""", timeout=timeout, what="On/off 창 닫힘")
    log("OK — 창 닫힘 확인")


# ──────────────────────────────────────────────────────────────────────────────
# 4) 페이지 저장 (Submit)
# ──────────────────────────────────────────────────────────────────────────────
def submit_page(page: CdpPage, log=lambda *_: None, timeout: float = 180.0) -> dict:
    """
    화면 아래 Submit. **여기까지 해야 서버에 반영된다.**

    OK 는 초안(draft)으로만 들어간다. 실제로 확인한 것:
        OK -> submitProductDraft {draftTypes:["PriceAndStock"], type:2}
              -> Ack "Success", draftVersion 이 하나 오른다
    화면의 달력은 판매중인 값을 보여주므로 OK 만으로는 아무것도 안 바뀐 것처럼 보인다.

    반영이 느릴 수 있어서 넉넉히 기다린다. 다시 누르지 않는다 — 두 번 제출하면
    서버에 그만큼 부담이 간다.

    ⚠️ OK 가 띄운 'Saved' 토스트가 아직 화면에 남아 있는 채로 Submit 을 누른다.
       예전에는 그 토스트를 Submit 의 결과로 착각하고 곧바로 빠져나와서,
       Submit 이 처리될 시간도 주지 않고 새로고침해 버렸다.
       (2026-09-10: OK/Submit/Saved 가 전부 같은 초에 찍혔다. 6초 뒤 새로고침.
        그날 TPC 6건이 '안 바뀜' 으로 끝났다)
       그래서 누르기 전에 지금 떠 있는 안내를 적어 두고, **그 뒤에 새로 뜬 것만**
       Submit 의 결과로 센다.
    """
    before_msgs = set(page.js(r"""(() => [...document.querySelectorAll(
        '.ant-message-notice, .ant-notification-notice')]
        .map(e => e.innerText.split('\n').join(' | ').slice(0, 200)))()""") or [])
    if before_msgs:
        log(f"(누르기 전 화면에 남아 있던 안내 {len(before_msgs)}개는 세지 않습니다)")

    r = page.js(r"""(() => {
        const b = document.querySelector('.submit-audit-btn');
        if (!b) return 'missing';
        if (b.disabled) return 'disabled';
        b.scrollIntoView({block: 'center'});
        __tpc.mclick(b);
        return 'ok';
      })()""")
    if r != "ok":
        raise CdpError(f"Submit 을 누르지 못했습니다 ({r})")
    log("Submit 누름 — 반영 기다리는 중")

    deadline = time.monotonic() + timeout
    seen: dict = {"modals": [], "messages": []}
    while time.monotonic() < deadline:
        state = page.js(r"""(() => ({
            modals: __tpc.visibleModals().map(w => w.innerText.split('\n').join(' | ').slice(0, 300)),
            messages: [...document.querySelectorAll('.ant-message-notice, .ant-notification-notice')]
                        .map(e => e.innerText.split('\n').join(' | ').slice(0, 200)),
            spinning: document.querySelectorAll('.ant-spin-spinning').length,
          }))()""") or {}
        for key in ("modals", "messages"):
            for t in state.get(key) or []:
                if not t or t in seen[key]:
                    continue
                if key == "messages" and t in before_msgs:
                    continue          # 누르기 전부터 있던 것 — Submit 의 결과가 아니다
                seen[key].append(t)
                log(f"[{key}] {t}")
        if (seen["messages"] or seen["modals"]) and not state.get("spinning"):
            break
        time.sleep(1.5)
    else:
        # 새 안내도 창도 안 뜬 채로 시간이 다 갔다. 됐는지 안 됐는지 모른다.
        # 모르는 것을 '했다' 고 보고하지 않는다.
        raise CdpError(f"Submit 을 눌렀는데 {timeout:.0f}초 동안 아무 응답이 "
                       f"없었습니다 (안내도 창도 안 떴습니다)")

    # Submit 뒤에 안내 팝업이 뜬다. 2026-09-10 에 사람이 직접 눌러 확인한 것:
    #
    #     "The product info has been updated. Please update your human
    #      translations promptly to avoid potential impact on sales."
    #                                          [Not now]  [Manage]
    #
    #   이 창이 뜬다는 것 자체가 **Submit 이 먹혔다는 뜻**이다.
    #   Not now 로 닫으면 그대로 마감 상태가 된다 (직접 눌러 확인함).
    #
    # ⚠️ 반드시 default(비-primary) 를 누른다. primary 는 'Manage / 去维护' 라서
    #    누르면 번역 관리 화면으로 끌려간다. dismiss_notices() 가 그렇게 한다.
    #    글자로 고르지 않는다 — 화면이 중국어로 바뀌면 못 찾는다.
    #
    # ⚠️ 예전에는 창이 남아 있으면 무조건 실패로 올렸다. 무엇이 뜨는지 몰랐기
    #    때문인데, 그 바람에 Submit 이 된 것을 실패로 보고했다.
    #    이제는 닫아 보고, **닫은 뒤 새로고침해서 서버 값으로 검증**한다.
    #    잘못 눌렀다면 그 검증이 잡는다 (거짓 성공이 되지 않는다).
    def _left() -> list:
        return page.js(r"""(() => __tpc.visibleModals()
            .filter(w => !__tpc.isOurModal(w))
            .map(w => w.innerText.split('\n').join(' | ').slice(0, 300)))()""") or []

    for _ in range(4):
        left = _left()
        if not left:
            break
        for t in left:
            if t not in seen["modals"]:
                seen["modals"].append(t)
            log(f"[안내창] {t[:160]}")
        if not dismiss_notices(page):
            break              # 푸터가 없어 어느 버튼이 '나중에' 인지 알 수 없다
        time.sleep(1.2)

    left = _left()
    if left:
        raise CdpError("Submit 뒤에 닫지 못한 창이 남아 있습니다: "
                       + " // ".join(left)[:400])
    return seen


def reload_and_wait(page: CdpPage, product_id: str, log=lambda *_: None) -> None:
    """
    저장 뒤 새로고침하고 다시 읽는다.

    화면 안의 값이 아니라 **서버에서 다시 받은 값**으로 확인해야 진짜 검증이다.
    """
    page.js("location.reload()")
    time.sleep(3.0)
    page.wait(r"""document.readyState === 'complete'""", timeout=90, what="새로고침")
    boot(page)
    assert_product(page, product_id)
    goto_pricing(page, log)


def _js_str(s: str) -> str:
    import json as _json
    return _json.dumps(str(s), ensure_ascii=False)
