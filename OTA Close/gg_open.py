# -*- coding: utf-8 -*-
"""
gg_open.py
GetYourGuide 라스트미닛 오픈.

마감(gg.py)은 availability 페이지에서 '페이지 단위 벌크 Block' 을 건다.
오픈은 반대로 '옵션 하나하나' 를 골라서 열어야 한다. 옵션 제목에 투어 코드와
픽업지가 둘 다 들어 있어서 픽업지별 오픈이 가능하다.

    알남레 - Shared Tour with Rail Bike, Meet at Hongik Univ Station     0 / 500
    감천미포 - Iconic Sky Capsule Tour, Meet at Seomyeon Station          2 / 500

오픈 = 두 단계
    1) Block 토글 끄기      (마감이 걸어둔 Block. 안 풀면 Not Bookable 그대로)
    2) 정원 = 이미 들어온 예약 + 그 옵션 몫
       0 / 6  에 6명 더 열면 -> 0 / 6      (예약 0 + 6)
       2 / 6  에 6명 더 열면 -> 2 / 8      (예약 2 + 6)

수량은 픽업 옵션 수만큼 나눈다 (MRT 와 같은 규칙).
    설낙 11명, 픽업 3곳 -> 홍대 4 / 명동 4 / 동대문 3
    차량은 하나라서 픽업마다 11씩 넣으면 33석이 열려버린다.

화면 구조 (2026-08-23 실측)
    [data-testid="agenda-item-{옵션ID}-{일시}"]   카드
      [data-testid="agenda-item-option-title"]    제목
      [data-testid="bookable-person-count"]       "0 / 500 Participants update"
      [data-testid="edit-bookable-person"]        수량 편집 진입
      input.p-inputnumber-input                   인라인 입력 (현재 정원)
      [data-testid="bookable-person-save-btn"]    저장
      [data-testid="block-date-toggle"]           Block 토글 (checked=Block중)

사용:
    python gg_open.py --date 2026-08-24 --items-file items.json [--dry-run]

items.json:
    [{"tour": "경주", "qty": 25, "pickups": ["Busan Station", "Haeundae"]}, ...]
    pickups 가 비어 있으면 그 투어의 모든 픽업 옵션을 연다.
"""
from __future__ import annotations

import sys as _sys_init

try:
    _sys_init.stdout.reconfigure(encoding="utf-8", errors="replace")
    _sys_init.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import json
import os
import re
import sys
from pathlib import Path

# Chrome 에 붙을 때 기다리는 시간 (ms).
# ⚠️ playwright 기본값은 180초다. Chrome 이 '반쯤 죽은' 상태 -- /json/version 은
#    200 을 주는데 CDP 핸드셰이크(<ws connected> 이후)가 안 끝나는 상태 -- 면
#    워커마다 3분씩 버리고 그제서야 실패한다.
#    빨리 실패해야 사람이 그 Chrome 을 다시 켤 시간이 있다.
CDP_CONNECT_TIMEOUT_MS = int(os.environ.get("CDP_CONNECT_TIMEOUT_MS") or 30000)

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from playwright.sync_api import TimeoutError as PWTimeoutError  # noqa: E402

from shared.chrome_setup import close_worker_page  # noqa: E402
from shared.logger import get_agency_logger  # noqa: E402

LOG = get_agency_logger("GG-OPEN")
RESULT_MARKER = "##GG_RESULT##"
AVAIL_URL = "https://supplier.getyourguide.com/manage/availability"

# ──────────────────────────────────────────────────────────────────────────────
# 이름 맞추기
#   예약 파일 표기 != GG 옵션 제목 표기. 다른 것만 여기에 적는다.
#   나머지는 그대로 일치한다 (알남레 / 레남아 / 설낙 / 감천미포 / 경주 ...).
# ──────────────────────────────────────────────────────────────────────────────
PICKUP_ALIASES: dict[str, list[str]] = {
    "hongdae":        ["hongik univ station", "hongik", "hongdae"],
    "myungdong":      ["myeongdong", "myungdong"],
    "dongdaemoon":    ["dongdaemun", "dongdaemoon"],
    "busan station":  ["ktx busan station", "busan station", "busan subway station"],
    "seomyun":        ["seomyeon station", "seomyeon", "seomyun"],
    "haeundae":       ["haeundae station", "haeundae"],
    "gwanghwamun":    ["gwanghwamun"],
    "sangwangsimni":  ["sangwangsimni"],
}

TOUR_ALIASES: dict[str, list[str]] = {
    "에버":        ["에버셔틀", "에버"],
    "mbc 스튜디오": ["mbc"],
    "mbc 스튜디오(드라마 리허설)": ["mbc"],
}


# GG 표기 -> 예약 파일 표기 (드롭다운에 시트 용어로 보여주기 위함)
PICKUP_DISPLAY: dict[str, str] = {
    "hongik univ station": "Hongdae",
    "myeongdong": "Myungdong",
    "dongdaemun": "Dongdaemoon",
    "ktx busan station": "Busan Station",
    "busan subway station": "Busan Station",
    "seomyeon station": "Seomyun",
    "haeundae station": "Haeundae",
}


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", str(s or "").replace("\xa0", " ").strip()).casefold()


def _tour_candidates(tour: str) -> list[str]:
    key = _norm(tour)
    out = [key]
    for alias in TOUR_ALIASES.get(key, []):
        if _norm(alias) not in out:
            out.append(_norm(alias))
    return out


def _pickup_candidates(pickup: str) -> list[str]:
    key = _norm(pickup)
    return [_norm(x) for x in PICKUP_ALIASES.get(key, [key])]


def parse_title(title: str) -> tuple[str, str]:
    """'알남레 - Shared Tour ..., Meet at Hongik Univ Station' -> ('알남레', 'Hongik Univ Station')"""
    t = str(title or "").replace("\xa0", " ")
    head = t.split(" - ")[0].strip()
    m = re.search(r"Meet at (.+?)\s*$", t)
    pickup = m.group(1).strip() if m else ""
    return head, pickup


def parse_count(text: str) -> tuple[int, int] | None:
    """'2 / 120 Participants update' -> (2, 120)"""
    m = re.search(r"(\d+)\s*/\s*(\d+)", str(text or ""))
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


# ──────────────────────────────────────────────────────────────────────────────
def collect_cards(page) -> list[dict]:
    return page.evaluate(
        """() => Array.from(document.querySelectorAll('[data-testid^="agenda-item-"]'))
            .filter(el => /^agenda-item-\\d+-/.test(el.getAttribute('data-testid')))
            .map(el => {
                const t = el.querySelector('[data-testid="agenda-item-option-title"]');
                const c = el.querySelector('[data-testid="bookable-person-count"]');
                const st = el.querySelector('[data-testid="bookable-status"]');
                const tg = el.querySelector('[data-testid="block-date-toggle"] input[type=checkbox]');
                return {
                    testid: el.getAttribute('data-testid'),
                    title: t ? t.innerText.trim().replace(/\\s+/g,' ') : '',
                    count: c ? c.innerText.trim().replace(/\\s+/g,' ') : '',
                    status: st ? st.innerText.trim() : '',
                    blocked: tg ? !!tg.checked : null,
                    canEdit: !!el.querySelector('[data-testid="edit-bookable-person"]')
                };
            })"""
    )


def select_date(page, date_str: str) -> None:
    url = f"{AVAIL_URL}?filter_date_from={date_str}&filter_date_to={date_str}&page=1"
    LOG.info("날짜 이동: %s", url)
    page.goto(url, wait_until="domcontentloaded", timeout=60_000)
    try:
        page.wait_for_selector('input[placeholder="From - to"]', timeout=30_000)
    except PWTimeoutError:
        LOG.warning("date input 30초 대기 실패 - 새로고침 후 재시도")
        page.reload(wait_until="domcontentloaded", timeout=45_000)
        page.wait_for_selector('input[placeholder="From - to"]', timeout=30_000)
    page.wait_for_timeout(2_500)


# 페이지 크기 / 페이지 이동 컨트롤은 목록 맨 아래 footer.pagination-footer 안에 있다.
#   "Previous 1 2 Next 15"   <- 끝의 15 가 페이지 크기 선택
# ⚠️ 그냥 '.p-select-label' 을 first 로 잡으면 화면 위쪽 필터 입력칸이 걸린다.
#    실제로 그 때문에 페이지 크기가 안 바뀌어 15개만 읽고 상품이 누락됐다.
FOOTER = "footer.pagination-footer"


def card_count(page) -> int:
    try:
        return page.evaluate(
            """() => Array.from(document.querySelectorAll('[data-testid^="agenda-item-"]'))
                 .filter(el => /^agenda-item-\\d+-/.test(el.getAttribute('data-testid'))).length"""
        )
    except Exception:
        return -1


def set_page_size(page, size: int = 50) -> bool:
    try:
        label = page.locator(f"{FOOTER} .p-select-label").first
        label.scroll_into_view_if_needed(timeout=4_000)
        cur = label.inner_text(timeout=3_000).strip()
        if cur == str(size):
            return True
        before = card_count(page)
        label.click(timeout=5_000)
        page.wait_for_timeout(600)
        page.locator(f"li[role='option']:has-text('{size}')").first.click(timeout=5_000)
        page.wait_for_timeout(3_000)
        after = card_count(page)
        now = label.inner_text(timeout=3_000).strip()
        LOG.info("page size %s → %s (옵션 %d → %d)", cur, now, before, after)
        return now == str(size)
    except Exception as e:
        LOG.warning("page size %s 변경 실패: %s", size, str(e)[:100])
        return False


def next_page(page) -> bool:
    """footer 의 'Next' 클릭. 마지막 페이지면 False."""
    try:
        foot = page.locator(FOOTER).first
        if not foot.is_visible(timeout=2_000):
            return False
        nxt = foot.get_by_text("Next", exact=True).first
        if not nxt.is_visible(timeout=2_000):
            return False
        # 비활성 판정: 자기 자신 + 조상 2단계까지 disabled 흔적 확인
        state = nxt.evaluate(
            """el => {
                let e = el, s = '';
                for (let i = 0; i < 3 && e; i++) {
                    s += ' ' + (e.className || '');
                    s += ' ' + (e.getAttribute('aria-disabled') || '');
                    if (e.disabled) s += ' disabled';
                    e = e.parentElement;
                }
                return s.toLowerCase();
            }"""
        )
        if "disabled" in state or "true" in state.split("aria-disabled")[-1][:6]:
            return False
        before = card_count(page)
        first_id = page.evaluate(
            """() => {
                const e = Array.from(document.querySelectorAll('[data-testid^="agenda-item-"]'))
                    .find(x => /^agenda-item-\\d+-/.test(x.getAttribute('data-testid')));
                return e ? e.getAttribute('data-testid') : null;
            }"""
        )
        nxt.click(timeout=5_000)
        page.wait_for_timeout(3_000)
        after_id = page.evaluate(
            """() => {
                const e = Array.from(document.querySelectorAll('[data-testid^="agenda-item-"]'))
                    .find(x => /^agenda-item-\\d+-/.test(x.getAttribute('data-testid')));
                return e ? e.getAttribute('data-testid') : null;
            }"""
        )
        if after_id == first_id and card_count(page) == before:
            # 내용이 그대로면 마지막 페이지였던 것
            return False
        return True
    except Exception as e:
        LOG.info("다음 페이지 없음/이동 실패: %s", str(e)[:70])
        return False


def close_one(page, card: dict, dry_run: bool) -> dict:
    """
    카드 하나 마감: Block 켜기.

    ⚠️ 정원을 0 으로 만드는 것이 아니다. GG 오픈은 '정원 = 예약 + 수량' 이라
       0 을 넣으면 정원은 이미 찬 상태로 두고 Block 만 풀어 버린다.
       GG 에서 '닫는다' 는 Block 이다 (마감 봇 gg.py 도 같은 토글을 쓴다).
    """
    tid = card["testid"]
    if card.get("blocked"):
        return {"testid": tid, "title": card["title"], "result": "성공",
                "memo": "이미 Block 상태 (마감됨)"}
    if dry_run:
        return {"testid": tid, "title": card["title"], "result": "DRY_RUN",
                "memo": f"DRY: Block 켜기 (현재 {card.get('count')})"}

    root = page.locator(f'[data-testid="{tid}"]').first
    try:
        root.scroll_into_view_if_needed(timeout=4_000)
    except Exception:
        pass
    try:
        root.locator('[data-testid="block-date-toggle"]').first.click(timeout=5_000)
        page.wait_for_timeout(1_200)
        after = collect_one(page, tid)
        if after and not after.get("blocked"):
            return {"testid": tid, "title": card["title"], "result": "실패",
                    "memo": "Block 토글을 켰는데 아직 열린 상태"}
        return {"testid": tid, "title": card["title"], "result": "성공",
                "memo": "Block 켬 (마감)"}
    except Exception as e:
        return {"testid": tid, "title": card["title"], "result": "실패",
                "memo": f"Block 켜기 실패: {str(e)[:90]}"}


def open_one(page, card: dict, qty: int, dry_run: bool) -> dict:
    """카드 하나 오픈: Block 해제 + 정원 = 예약 + qty."""
    tid = card["testid"]
    cnt = parse_count(card["count"])
    if cnt is None:
        return {"testid": tid, "title": card["title"], "result": "실패",
                "memo": f"수량 표기를 읽지 못함: {card['count']!r}"}
    booked, capacity = cnt
    want = booked + int(qty)

    if dry_run:
        return {"testid": tid, "title": card["title"], "result": "DRY_RUN",
                "memo": f"DRY: {booked}/{capacity} → {booked}/{want} "
                        f"(Block 해제 {'필요' if card.get('blocked') else '불필요'})"}

    root = page.locator(f'[data-testid="{tid}"]').first
    try:
        root.scroll_into_view_if_needed(timeout=4_000)
    except Exception:
        pass

    # 1) Block 해제 — 안 풀면 정원을 넣어도 Not Bookable 이라 예약이 안 들어온다
    unblocked = "불필요"
    if card.get("blocked"):
        try:
            root.locator('[data-testid="block-date-toggle"]').first.click(timeout=5_000)
            page.wait_for_timeout(1_200)
            after = collect_one(page, tid)
            if after and after.get("blocked"):
                return {"testid": tid, "title": card["title"], "result": "실패",
                        "memo": "Block 토글을 껐는데 아직 Block 상태"}
            unblocked = "해제됨"
        except Exception as e:
            return {"testid": tid, "title": card["title"], "result": "실패",
                    "memo": f"Block 해제 실패: {str(e)[:90]}"}

    # 2) 정원 입력
    try:
        root.locator('[data-testid="edit-bookable-person"]').first.click(timeout=5_000)
        page.wait_for_timeout(900)
        box = root.locator("input.p-inputnumber-input").first
        box.wait_for(state="visible", timeout=5_000)
        box.fill(str(want), timeout=4_000)
        page.wait_for_timeout(300)
        root.locator('[data-testid="bookable-person-save-btn"]').first.click(timeout=5_000)
        page.wait_for_timeout(1_800)
    except Exception as e:
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass
        return {"testid": tid, "title": card["title"], "result": "실패",
                "memo": f"정원 입력 실패: {str(e)[:90]}"}

    # 3) 저장 확인 — 성공 위장 방지
    after = collect_one(page, tid)
    got = parse_count(after["count"]) if after else None
    if not got or got[1] != want:
        return {"testid": tid, "title": card["title"], "result": "실패",
                "memo": f"저장 미반영: 기대 {booked}/{want}, 실제 {after['count'] if after else '읽기실패'}"}
    return {"testid": tid, "title": card["title"], "result": "성공",
            "memo": f"{booked}/{capacity} → {got[0]}/{got[1]} (Block {unblocked})"}


def collect_one(page, testid: str) -> dict | None:
    try:
        return page.evaluate(
            """(tid) => {
                const el = document.querySelector(`[data-testid="${tid}"]`);
                if (!el) return null;
                const c = el.querySelector('[data-testid="bookable-person-count"]');
                const st = el.querySelector('[data-testid="bookable-status"]');
                const tg = el.querySelector('[data-testid="block-date-toggle"] input[type=checkbox]');
                return {count: c ? c.innerText.trim().replace(/\\s+/g,' ') : '',
                        status: st ? st.innerText.trim() : '',
                        blocked: tg ? !!tg.checked : null};
            }""", testid)
    except Exception:
        return None


def _closest(want: str, names: list, top: int = 5) -> list:
    """
    이름이 어긋났을 때 '이걸 말한 건가' 를 보여준다.

    글자 겹침으로 대충 고른다. 정확할 필요는 없다 — 사람이 보고 판단한다.
    """
    import difflib
    w = _norm(want)
    scored = []
    for n in names:
        r = difflib.SequenceMatcher(None, w, _norm(n)).ratio()
        if r >= 0.35:
            scored.append((r, n))
    scored.sort(reverse=True)
    return [n for _r, n in scored[:top]]


def match_targets(cards: list[dict], items: list[dict]) -> tuple[list[tuple], list[dict]]:
    """(카드, 수량, 요청) 매칭 목록과 못 찾은 요청을 돌려준다."""
    pairs, missed = [], []
    for it in items:
        tour = it["tour"]
        qty = int(it["qty"])
        want_pickups = [p for p in (it.get("pickups") or []) if p]
        tcands = _tour_candidates(tour)
        hits = []
        for c in cards:
            head, pickup = parse_title(c["title"])
            if _norm(head) not in tcands:
                continue
            if want_pickups:
                pc = _norm(pickup)
                ok = any(pc in _pickup_candidates(w) or pc == _norm(w) for w in want_pickups)
                if not ok:
                    continue
            hits.append(c)
        if hits:
            for c in hits:
                pairs.append((c, qty, it))
        else:
            # 왜 못 찾았는지 알 수 있게 화면에 있던 이름을 함께 남긴다.
            #
            # GG 는 맵핑표 없이 화면 이름으로 찾는다. GG 쪽 상품명이 우리
            # 투어명과 다르면 조용히 빠지는데, 예전에는 '못 찾음' 만 남아서
            # 원인을 알 수 없었다. 로그인이 필요한 화면이라 나중에 열어볼
            # 수도 없다. (2026-08-30: JAPAN Mt. Fuji Signature)
            heads = []
            for c in cards:
                h, _pk = parse_title(c["title"])
                if h and h not in heads:
                    heads.append(h)
            near = _closest(tour, heads, 5)
            reason = "해당 옵션을 화면에서 찾지 못함"
            if near:
                reason += " | 비슷한 이름: " + ", ".join(near)
            if heads:
                reason += f" | 화면 옵션 {len(heads)}종: " + ", ".join(heads[:12])
            LOG.warning("[%s] 못 찾음 — 화면에 있던 이름 %d종: %s",
                        tour, len(heads), ", ".join(heads[:15]))
            missed.append({**it, "reason": reason[:600]})
    return pairs, missed


def split_across(total: int, n: int) -> list:
    """
    수량을 옵션(=픽업지) 개수만큼 나눈다. 나머지는 앞쪽부터 1씩. MRT 와 같은 규칙.
        11, 3곳 -> [4, 4, 3]
        12, 2곳 -> [6, 6]
    """
    if n <= 0:
        return []
    base, rem = divmod(max(0, int(total)), n)
    return [base + (1 if i < rem else 0) for i in range(n)]


def collect_catalog(date_str: str, port: int) -> dict:
    """
    그 날짜에 GG 가 파는 (투어 -> 픽업지 목록) 을 수집한다.

    왜 필요한가: 라스트미닛 화면의 픽업 드롭다운을 예약 파일만 보고 만들면,
    그날 예약이 들어온 픽업지만 후보로 나온다. '홍대 제외' 를 지시하려면
    홍대가 후보에 있어야 하는데, 홍대 예약이 없으면 고를 수가 없다.
    GG 옵션 목록이 '실제로 파는 픽업지' 의 사실상 유일한 출처다.
    """
    from playwright.sync_api import sync_playwright

    out: dict[str, list[str]] = {}
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(
            f"http://127.0.0.1:{port}", timeout=CDP_CONNECT_TIMEOUT_MS)
        if not browser.contexts:
            return {"error": "Chrome context 없음", "catalog": {}}
        ctx = browser.contexts[0]
        page = ctx.new_page()
        try:
            page.set_viewport_size({"width": 1700, "height": 1300})
            select_date(page, date_str)
            set_page_size(page, 50)
            seen: set = set()
            page_no = 1
            while True:
                for c in collect_cards(page):
                    if c["testid"] in seen:
                        continue
                    seen.add(c["testid"])
                    head, pickup = parse_title(c["title"])
                    if not head:
                        continue
                    label = PICKUP_DISPLAY.get(_norm(pickup), pickup).strip()
                    if not label:
                        continue
                    lst = out.setdefault(head, [])
                    if label not in lst:
                        lst.append(label)
                LOG.info("[카탈로그 page %d] 누적 투어 %d개", page_no, len(out))
                if not next_page(page):
                    break
                page_no += 1
        finally:
            close_worker_page(page, "GG/catalog")
    return {"catalog": {k: sorted(v) for k, v in out.items()}, "date": date_str}


def run(date_str: str, items: list[dict], port: int, dry_run: bool = False) -> dict:
    """
    2패스로 돈다.
      1패스: 모든 페이지를 읽어 매칭되는 옵션을 전부 모은다 (읽기만)
      2패스: 투어별로 수량을 옵션 수만큼 나눈 뒤, 페이지를 다시 돌며 적용

    1패스가 필요한 이유: 한 투어의 픽업 옵션이 여러 페이지에 흩어져 있을 수 있어서,
    다 모으기 전에는 몇 개로 나눠야 하는지 알 수 없다.
    """
    from playwright.sync_api import sync_playwright

    LOG.info("GG 오픈 시작 | date=%s | %d건 | dry_run=%s | port=%d",
             date_str, len(items), dry_run, port)
    results: list[dict] = []

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(
            f"http://127.0.0.1:{port}", timeout=CDP_CONNECT_TIMEOUT_MS)
        if not browser.contexts:
            return {"error": "Chrome context 없음", "results": []}
        ctx = browser.contexts[0]
        page = ctx.new_page()
        try:
            page.set_viewport_size({"width": 1700, "height": 1300})

            # ── 1패스: 전체 옵션 수집 ──────────────────────────────────────
            select_date(page, date_str)
            set_page_size(page, 50)
            all_cards: list[dict] = []
            seen_ids: set = set()
            page_no = 1
            while True:
                cards = collect_cards(page)
                LOG.info("[수집 page %d] 옵션 %d개", page_no, len(cards))
                for c in cards:
                    if c["testid"] not in seen_ids:
                        seen_ids.add(c["testid"])
                        all_cards.append(c)
                if not next_page(page):
                    break
                page_no += 1
            LOG.info("총 옵션 %d개 수집", len(all_cards))

            # ── 매칭 + 분할 ────────────────────────────────────────────────
            plan: dict[str, tuple] = {}     # testid -> (item, share)
            missed: list[dict] = []
            for it in items:
                pairs, miss = match_targets(all_cards, [it])
                if miss:
                    missed.extend(miss)
                    continue
                cards_hit = [c for c, _q, _i in pairs]
                shares = split_across(int(it["qty"]), len(cards_hit))
                names = []
                for c, share in zip(cards_hit, shares):
                    plan[c["testid"]] = (it, share, c)
                    _h, pk = parse_title(c["title"])
                    names.append(f"{pk or '?'}={share}")
                LOG.info("[분할] %s %d명 → 픽업 %d곳: %s",
                         it["tour"], it["qty"], len(cards_hit), ", ".join(names))
                print(f"[분할] {it['tour']} {it['qty']}명 → 픽업 {len(cards_hit)}곳: "
                      f"{', '.join(names)}", flush=True)

            # ── 2패스: 적용 ────────────────────────────────────────────────
            if plan:
                select_date(page, date_str)
                set_page_size(page, 50)
                applied: set = set()
                page_no = 1
                while True:
                    cards = collect_cards(page)
                    for c in cards:
                        tid = c["testid"]
                        if tid not in plan or tid in applied:
                            continue
                        it, share, _old = plan[tid]
                        applied.add(tid)
                        if int(it.get("qty") or 0) == 0:
                            # 수량 0 = 마감. 아래 '분할 결과 0명' 과는 다른 경우다.
                            r = close_one(page, c, dry_run)
                        elif share <= 0:
                            r = {"testid": tid, "title": c["title"], "result": "스킵",
                                 "memo": "분할 결과 0명 (픽업지 수보다 수량이 적음)"}
                        else:
                            r = open_one(page, c, share, dry_run)
                        r["tour"] = it["tour"]
                        r["qty"] = share
                        results.append(r)
                        print(f"{RESULT_MARKER} {json.dumps(r, ensure_ascii=False)}", flush=True)
                        LOG.info("  %s | %s | %s", r["result"], r["title"][:56], r["memo"][:70])
                    if len(applied) >= len(plan) or not next_page(page):
                        break
                    page_no += 1

                for tid, (it, share, c) in plan.items():
                    if tid not in applied:
                        r = {"testid": tid, "title": c["title"], "tour": it["tour"],
                             "qty": share, "result": "실패",
                             "memo": "2패스에서 해당 옵션을 다시 찾지 못함"}
                        results.append(r)
                        print(f"{RESULT_MARKER} {json.dumps(r, ensure_ascii=False)}", flush=True)
        finally:
            close_worker_page(page, "GG/open")

    for m in missed:
        r = {"tour": m["tour"], "qty": m["qty"], "title": "", "result": "찾을 수 없음",
             "memo": m["reason"] + (f" (픽업 {', '.join(m.get('pickups') or [])})"
                                    if m.get("pickups") else "")}
        results.append(r)
        print(f"{RESULT_MARKER} {json.dumps(r, ensure_ascii=False)}", flush=True)

    ok = sum(1 for r in results if r["result"] in ("성공", "DRY_RUN"))
    fail = sum(1 for r in results if r["result"] == "실패")
    miss = sum(1 for r in results if r["result"] in ("찾을 수 없음", "스킵"))
    print(f"\n[GG/open] success={ok} failed={fail} skipped={miss}", flush=True)
    return {"results": results, "success": ok, "failed": fail, "skipped": miss}


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True, help="YYYY-MM-DD")
    ap.add_argument("--items-file", help='[{"tour":"경주","qty":25,"pickups":[...]}]')
    ap.add_argument("--port", type=int, default=9522)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--mode", default="open", choices=["open", "catalog"],
                    help="catalog = 투어별 픽업지 목록만 수집")
    ap.add_argument("--output", help="catalog 모드 결과 JSON 경로")
    args = ap.parse_args()

    if args.mode == "catalog":
        res = collect_catalog(args.date, args.port)
        if res.get("error"):
            print("[GG/catalog]", res["error"])
            sys.exit(3)
        text = json.dumps(res, ensure_ascii=False, indent=2)
        if args.output:
            Path(args.output).write_text(text, encoding="utf-8")
            print(f"[GG/catalog] 투어 {len(res['catalog'])}개 -> {args.output}")
        else:
            print(text)
        sys.exit(0)

    if not args.items_file:
        print("--items-file 이 필요합니다.")
        sys.exit(2)
    data = json.loads(Path(args.items_file).read_text(encoding="utf-8"))
    if not data:
        print("[GG/open] 처리할 항목이 없습니다.")
        sys.exit(2)
    res = run(args.date, data, args.port, args.dry_run)
    sys.exit(0 if res.get("failed", 0) == 0 else 1)
