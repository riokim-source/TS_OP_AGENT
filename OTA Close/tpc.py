# -*- coding: utf-8 -*-
"""
tpc.py
TPC (Trip.com / Ctrip vBooking) 마감 · 수집 · 오픈 봇.

실행
    python tpc.py --mode close                     # 내일 날짜로 마감
    python tpc.py --mode close --date 2026-09-10
    python tpc.py --mode close --dry-run           # OK 를 누르지 않고 계획만
    python tpc.py --mode collect                   # 읽기 전용. 아무것도 바꾸지 않는다
    python tpc.py --mode open  --date 2026-09-10   # 판매 재개
    python tpc.py --mode close --regions KOREA,JAPAN
    python tpc.py --mode close --targets 경주,감천미포

마감 순서 (운영 지시 그대로)
    1. 상품 목록에서 **내부명칭으로 검색**한다
    2. 나온 목록에서 **지정된 상품번호 행**을 골라 들어간다  ← 이중검색
    3. Package and pricing
    4. On/off 창에서 **Select All** (그 상품의 패키지 전부)
    5. Close sales
    6. Set by Date 로 대상 날짜 하나
    7. OK
    8. **화면 아래 Submit** — 여기까지 해야 서버에 반영된다 (조금 걸린다)
    9. 새로고침하고 다시 읽어서 정말 닫혔는지 확인한다

⚠️ OK 는 반영이 아니다
    OK 만 누르면 화면 안에서만 바뀐다. Submit 을 안 하면 재고는 그대로 열려 있다.
    그래서 이 봇은 OK 뒤에 반드시 Submit 하고, **새로고침해서 서버가 준 값**으로
    검증한다. 화면에 남아 있던 값을 근거로 '마감했다' 고 보고하지 않는다.

⚠️ '못 봤다' 를 '없다' 로 세지 않는다
    칸에서 스위치를 못 찾으면 no_switch 다. 그건 스킵이 아니라 **실패**다.
    (2026-09-09 마감에서 VI·MRT 가 정확히 이걸로 3건을 조용히 놓쳤다)
    반대로 화면이 'Not set' 이라고 분명히 말하는 것은 그날 안 파는 패키지라서
    스킵이 맞다.

⚠️ 오픈은 '아침에 우리가 닫은 것' 만 되연다
    '지금 닫혀 있는 패키지' 를 다 열면 안 된다. 시즌이 지나 닫아 둔 것까지 열린다
    (경주의 A-中文导游 [十月 ~ 三月] 는 9월에도 가격이 남은 채 닫혀 있다).
    그날 마감 로그(logs/tpc_close_<날짜>_*.json)에 남은 패키지만 되열고,
    로그가 없으면 열지 않고 NO_CLOSE_LOG 로 멈춘다.

⚠️ Select All 이 '화면의 모든 패키지' 는 아니다
    이름에 Invalid 가 붙은 패키지는 On/off 창 목록에 아예 안 나온다.
    닫아야 할 패키지가 창에 없으면 OK 를 누르지 않고 PKG_UNSELECTABLE 로 멈춘다.
    (2026-09-09 감천미포: 화면 11개 / 창 10개, 빠진 하나가 그날 유일하게 열린 것이었다)

⚠️ 서버를 두드리지 않는다
    상품을 하나씩 순서대로 처리한다. 워커를 나눠 동시에 붙이지 않고, 사이에
    쉬는 시간을 둔다. Submit 은 한 번만 누르고 기다린다.

⚠️ 사람 Chrome 을 건드리지 않는다
    봇이 연 탭만 닫는다. 로그인 탭이나 다른 OTA 탭은 손대지 않는다.
    Playwright 대신 페이지 타깃에 직접 붙는 이유는 shared/cdp_page.py 참고.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tpc_dom as D                                   # noqa: E402
import tpc_targets as T                               # noqa: E402
from shared import cdp_page as C                      # noqa: E402
from shared.cdp_page import CdpError                  # noqa: E402
from shared.logger import get_logger                  # noqa: E402

LOG = get_logger("tpc")

RESULT_MARKER = "##TPC_RESULT##"

# 지역 -> Chrome 포트. hub/core/routing.py 의 프로필과 같은 값이어야 한다.
#   KOREA/JAPAN 은 한국 계정(KR) 한 곳에서 같이 판다. 호주만 계정이 다르다.
REGION_PORT = {"KOREA": 9522, "JAPAN": 9522, "AUSTRALIA": 9524}

# 상품 사이 쉬는 시간. 서버에 몰아치지 않으려고 둔다.
GAP_SEC = float(os.environ.get("TPC_GAP_SEC") or 3.0)


class Status(str, Enum):
    SUCCESS = "SUCCESS"                 # 바꾸고 새로고침해서 확인까지 됨
    DRY_RUN = "DRY_RUN"                 # 계획만 (OK 안 누름)
    ALREADY = "ALREADY"                 # 이미 그 상태 (닫혀 있음 / 열려 있음)
    NOT_ON_SALE = "NOT_ON_SALE"         # 그날 파는 패키지가 하나도 없음 (화면이 Not set)
    COLLECTED = "COLLECTED"             # 수집 모드
    CHROME_DOWN = "CHROME_DOWN"         # 그 지역 Chrome 이 안 떠 있음
    NOT_IN_SEARCH = "NOT_IN_SEARCH"     # 명칭으로 찾았는데 지정 번호 행이 없음
    PRODUCT_MISMATCH = "PRODUCT_MISMATCH"   # 들어갔더니 다른 상품
    NO_PACKAGES = "NO_PACKAGES"         # 패키지 목록이 안 뜸
    NO_SWITCH = "NO_SWITCH"             # 칸은 있는데 판매 스위치를 못 봄 (판단 불가)
    NO_CLOSE_LOG = "NO_CLOSE_LOG"       # 오픈: 그날 무엇을 닫았는지 기록이 없다
    DIALOG_FAILED = "DIALOG_FAILED"     # On/off 창을 못 다룸
    PKG_UNSELECTABLE = "PKG_UNSELECTABLE"   # 닫아야 할 패키지가 창 목록에 없음
    DATE_FAILED = "DATE_FAILED"         # 날짜를 정확히 하나로 못 고름
    SUBMIT_FAILED = "SUBMIT_FAILED"     # Submit 실패
    VERIFY_FAILED = "VERIFY_FAILED"     # Submit 했는데 상태가 안 바뀜
    EXCEPTION = "EXCEPTION"

    @property
    def is_success(self) -> bool:
        return self in (Status.SUCCESS, Status.DRY_RUN, Status.COLLECTED)

    @property
    def is_skip(self) -> bool:
        return self in (Status.ALREADY, Status.NOT_ON_SALE)

    @property
    def is_failure(self) -> bool:
        return not (self.is_success or self.is_skip)


@dataclass
class ProductResult:
    name: str
    product_id: str
    region: str
    status: Status = Status.EXCEPTION
    detail: str = ""
    before: dict = field(default_factory=dict)
    after: dict = field(default_factory=dict)
    packages: list = field(default_factory=list)
    changed: list = field(default_factory=list)
    elapsed: float = 0.0

    def memo(self) -> str:
        if self.detail:
            return self.detail
        if self.changed:
            return f"{len(self.changed)}개 패키지 마감/오픈 확인"
        return ""


# ──────────────────────────────────────────────────────────────────────────────
def tomorrow(now: Optional[datetime] = None) -> str:
    """
    실행 PC 현지 날짜 + 1일. 다른 마감 봇과 같은 기준이다.

    월말/연말/윤년은 date 가 알아서 넘긴다.
    """
    d = (now or datetime.now()).date() + timedelta(days=1)
    return d.isoformat()


def valid_date(s: str) -> str:
    try:
        return date.fromisoformat(s).isoformat()
    except Exception:
        raise argparse.ArgumentTypeError(f"날짜는 YYYY-MM-DD 여야 합니다: {s}")


def emit(payload: dict) -> None:
    print(RESULT_MARKER + json.dumps(payload, ensure_ascii=False), flush=True)


# ──────────────────────────────────────────────────────────────────────────────
# 상품 하나
# ──────────────────────────────────────────────────────────────────────────────
def _scan_packages(edit, target_date: str) -> tuple[dict, list]:
    """
    패키지마다 그 날짜 칸의 상태를 읽는다. (읽기 전용)

    돌려주는 것: {패키지이름: {...}}, 패키지 목록
    """
    pkgs = D.packages(edit)
    if not pkgs:
        raise CdpError("패키지 목록이 비어 있습니다")
    state: dict = {}
    for pk in pkgs:
        D.select_package(edit, pk["id"])
        D.goto_month(edit, target_date)
        cell = D.read_cell_settled(edit, target_date)
        verdict, on = D.cell_verdict(cell)
        state[pk["name"]] = {
            "package_id": pk["id"],
            "verdict": verdict,
            "on": on,
            "sold": cell.get("sold"),
            "unlimited": bool(cell.get("unlimited")),
            "text": cell.get("text", "")[:160],
        }
    return state, pkgs


def _summarize(state: dict) -> dict:
    out = {"on": [], "off": [], "not_set": [], "no_switch": []}
    for name, s in state.items():
        out.setdefault(s["verdict"], []).append(name)
    return out


def closed_by_us(target_date: str) -> dict[str, list[str]]:
    """
    그날 이 봇이 **실제로 닫은** 패키지 목록. logs/tpc_close_<날짜>_*.json 에서 읽는다.

    ⚠️ 오픈이 이걸 봐야 하는 이유
       그냥 '지금 닫혀 있는 패키지' 를 다 열면 시즌이 지나 닫아 둔 것까지 열린다.
       경주의 A-中文导游 [十月 ~ 三月] 는 9월에 가격이 남아 있는 채로 닫혀 있다.
       그걸 열면 그날 운영하지 않는 자리가 팔린다.
       우리가 아침에 닫은 것만 저녁에 되연다.

    같은 날 여러 번 돌았으면 최근 것부터 합친다 (먼저 본 것이 이긴다).
    """
    out: dict[str, list[str]] = {}
    d = ROOT / "logs"
    if not d.exists():
        return out
    files = sorted(d.glob(f"tpc_close_{target_date}_*.json"), reverse=True)
    for f in files:
        try:
            rows = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        for row in rows:
            pid = str(row.get("product_id") or "")
            if not pid or row.get("status") != Status.SUCCESS.value:
                continue
            out.setdefault(pid, [])
            for name in row.get("changed") or []:
                if name not in out[pid]:
                    out[pid].append(name)
    return out


def process_product(port: int, target: dict, target_date: str, mode: str,
                    dry_run: bool, reopen: Optional[dict] = None) -> ProductResult:
    """상품 하나를 처음부터 끝까지. 봇이 연 탭은 반드시 정리한다."""
    t0 = time.perf_counter()
    r = ProductResult(name=target["name"], product_id=target["product_id"],
                      region=target["region"])
    lst = edit = None
    lst_id = edit_id = ""
    seen: set[str] = set()          # 이 상품을 시작할 때 이미 있던 탭

    def log(*a):
        msg = " ".join(str(x) for x in a)
        LOG.info("[%s] %s", target["name"], msg)

    try:
        lst, lst_id = C.open_page(port, T.PRODUCT_LIST_URL)
        time.sleep(3.0)
        D.boot(lst)

        # ── 이중검색 ──────────────────────────────────────────────────────
        D.search_by_name(lst, target["name"], log)
        try:
            row = D.pick_row(lst, target["name"], target["product_id"])
        except CdpError as e:
            r.status = Status.NOT_IN_SEARCH
            r.detail = str(e)
            return r
        log(f"행 확인: {row['supplier'][:60]}")

        seen = {t["id"] for t in C.pages(port)}
        D.open_edit_from_row(lst, target["product_id"])
        tgt = C.wait_new_page(port, seen, host="product-edit", timeout=45)
        edit_id = tgt["id"]
        edit = C.attach(tgt)
        edit.timeout = 60.0
        time.sleep(2.5)
        D.boot(edit)
        try:
            D.assert_product(edit, target["product_id"])
        except CdpError as e:
            r.status = Status.PRODUCT_MISMATCH
            r.detail = str(e)
            return r

        try:
            D.goto_pricing(edit, log)
        except CdpError as e:
            r.status = Status.NO_PACKAGES
            r.detail = str(e)
            return r

        # ── 지금 상태 ─────────────────────────────────────────────────────
        state, pkgs = _scan_packages(edit, target_date)
        r.before = state
        r.packages = [p["name"] for p in pkgs]
        buckets = _summarize(state)

        if mode == "collect":
            r.status = Status.COLLECTED
            r.detail = (f"판매중 {len(buckets['on'])} / 마감 {len(buckets['off'])} / "
                        f"미판매 {len(buckets['not_set'])}")
            return r

        # 판단 불가는 스킵이 아니다
        if buckets["no_switch"]:
            r.status = Status.NO_SWITCH
            r.detail = ("판매 스위치를 못 본 패키지: "
                        + ", ".join(buckets["no_switch"][:5]))
            return r

        closing = mode == "close"
        need = buckets["on"] if closing else buckets["off"]
        if not closing:
            # 아침에 우리가 닫은 것만 되연다 (closed_by_us 설명 참고).
            mine = (reopen or {}).get(target["product_id"])
            if mine is None:
                r.status = Status.NO_CLOSE_LOG
                r.detail = (f"{target_date} 마감 기록이 없어 무엇을 되열지 알 수 없습니다. "
                            f"시즌이 지나 닫아 둔 패키지까지 열릴 수 있어 중단합니다 "
                            f"(logs/tpc_close_{target_date}_*.json)")
                return r
            skipped_not_mine = [n for n in need if n not in mine]
            need = [n for n in need if n in mine]
            if skipped_not_mine:
                log(f"우리가 닫지 않아서 건드리지 않는 패키지 {len(skipped_not_mine)}개: "
                    f"{', '.join(skipped_not_mine)[:100]}")
        if not need:
            if not buckets["on"] and not buckets["off"]:
                r.status = Status.NOT_ON_SALE
                r.detail = f"{target_date} 에 파는 패키지가 없습니다 (전부 Not set)"
            else:
                r.status = Status.ALREADY
                r.detail = ("이미 전부 마감" if closing
                            else "아침에 닫은 패키지가 이미 전부 열려 있습니다")
            return r

        log(f"{'마감' if closing else '오픈'} 대상 패키지 {len(need)}개: "
            f"{', '.join(need)[:100]}")

        # ── On/off ────────────────────────────────────────────────────────
        #
        # DRY-RUN 도 여기까지는 진짜로 한다. 창을 열고, 패키지를 고르고, 날짜까지
        # 찍어 본 뒤 OK 대신 Cancel 한다. 그래야 '창을 다룰 수 있는지' 를 실제로
        # 확인할 수 있다. 창을 열어 보지도 않고 '계획 OK' 라고 하면, 정작 아침에
        # 창이 안 열려서 전부 실패하는 것을 전날 밤에 알 수 없다.
        try:
            D.open_sale_dialog(edit, log)
            if closing:
                # 운영 지시: 그 상품의 패키지를 전부 고른다 (Select All).
                # 그날 안 파는 패키지는 화면이 알아서 두므로 손대지 않는다.
                picked_pkgs = D.dialog_select_all_packages(edit, log)
                # ⚠️ Select All 이 '화면의 모든 패키지' 는 아니다.
                #    2026-09-09 감천미포: 화면에는 패키지가 11개인데 창 목록에는
                #    10개뿐이었다. 빠진 H-日文导游(早班) 는 이름에 'Invalid' 가
                #    붙어 있었고, 하필 그게 그날 유일하게 열려 있던 패키지였다.
                #    그대로 OK/Submit 하면 이미 닫힌 10개를 다시 닫고
                #    '마감했다' 는 모양만 남는다. 눌러 보기 전에 멈춘다.
                cannot = [n for n in need if n not in picked_pkgs]
                if cannot:
                    D.cancel_dialogs(edit)
                    r.status = Status.PKG_UNSELECTABLE
                    r.detail = ("On/off 창 목록에 없어서 닫을 수 없는 패키지: "
                                + ", ".join(cannot)
                                + " — 상품 화면에서 그 패키지 상태를 확인하세요 "
                                  "(Invalid 표시가 붙어 있으면 창에 안 나옵니다)")
                    return r
            else:
                # 오픈은 전체 선택을 쓰지 않는다. 원래 팔던 것만 다시 연다 —
                # Not set 인 패키지까지 열면 그날 운영하지 않는 자리가 팔린다.
                D.dialog_select_packages(edit, need, log)
            D.dialog_check_all_crowds(edit, log)
            D.dialog_set_operation(edit, close=closing, log=log)
        except CdpError as e:
            r.status = Status.DIALOG_FAILED
            r.detail = str(e)
            return r

        try:
            D.dialog_set_by_date(edit, target_date, log)
            picked = (D.dialog_state(edit) or {}).get("dates")
            if picked != [target_date]:
                raise CdpError(f"고른 날짜가 {picked!r} 입니다")
        except CdpError as e:
            # ⚠️ 여기서 OK 를 누르면 'Set by Week / Everyday' 로 전 날짜가 닫힌다.
            r.status = Status.DATE_FAILED
            r.detail = f"{str(e)} — OK 를 누르지 않았습니다"
            return r

        if dry_run:
            D.cancel_dialogs(edit)
            r.status = Status.DRY_RUN
            r.changed = need
            r.detail = (f"대상 {len(need)}개 — 창까지 확인하고 Cancel 했습니다 "
                        f"(OK/Submit 안 함)")
            return r

        D.dialog_ok(edit, log)

        # ── Submit (여기까지 해야 반영된다) ───────────────────────────────
        try:
            seen_msgs = D.submit_page(edit, log)
            r.detail = " / ".join((seen_msgs.get("messages") or [])[:2])[:200]
        except CdpError as e:
            r.status = Status.SUBMIT_FAILED
            r.detail = str(e)
            return r

        # ── 새로고침하고 서버 값으로 검증 ─────────────────────────────────
        D.reload_and_wait(edit, target["product_id"], log)
        after, _ = _scan_packages(edit, target_date)
        r.after = after
        want = "off" if closing else "on"
        bad = [n for n in need
               if (after.get(n) or {}).get("verdict") != want]
        if bad:
            r.status = Status.VERIFY_FAILED
            r.detail = (f"Submit 뒤에도 안 바뀐 패키지 {len(bad)}개: "
                        + ", ".join(bad[:5]))
            return r
        r.changed = need
        r.status = Status.SUCCESS
        r.detail = f"{len(need)}개 패키지 {'마감' if closing else '오픈'} 확인"
        return r

    except CdpError as e:
        r.status = Status.EXCEPTION
        r.detail = str(e)[:300]
        return r
    except Exception as e:                                # noqa: BLE001
        r.status = Status.EXCEPTION
        r.detail = f"{type(e).__name__}: {e}"[:300]
        LOG.debug("%s", traceback.format_exc())
        return r
    finally:
        r.elapsed = time.perf_counter() - t0
        for p, tid in ((edit, edit_id), (lst, lst_id)):
            if p is not None:
                try:
                    p.close()
                except Exception:
                    pass
            if tid:
                C.close_tab(port, tid)
        # 상품명을 눌러서 열렸는데 우리가 붙지 못한 탭도 치운다.
        # (새 탭이 늦게 떠서 wait_new_page 가 먼저 포기한 경우) 안 그러면
        # 10개를 도는 동안 상품 수정 탭이 쌓이고, 다음 상품에서
        # '새 탭이 여러 개 열렸습니다' 로 죽는다.
        #
        # ⚠️ '새로 생긴 상품 수정 탭' 을 다 닫으면 안 된다. 사람이 그 사이에
        #    다른 상품을 열어 볼 수도 있고, 실수로 이 봇을 두 번 돌리면 서로의
        #    탭을 닫아 버린다 (실제로 그렇게 ConnectionClosedError 가 났다).
        #    **우리 상품 번호가 URL 에 든 탭만** 닫는다.
        if seen:
            try:
                mine = f"productId={target['product_id']}"
                for t in C.pages(port, host="product-edit"):
                    if (t["id"] not in seen and t["id"] != edit_id
                            and mine in (t.get("url") or "")):
                        C.close_tab(port, t["id"])
            except Exception:
                pass


# ──────────────────────────────────────────────────────────────────────────────
def run(mode: str, target_date: str, regions: Optional[list[str]] = None,
        names: Optional[list[str]] = None, dry_run: bool = False,
        port_override: Optional[int] = None) -> dict:
    unknown = T.unknown_names(names)
    picks = T.by_names(names) if names else T.TARGETS
    if regions:
        want = {r.strip().upper() for r in regions if r.strip()}
        picks = [t for t in picks if t["region"] in want]

    results: list[ProductResult] = []
    # 오픈은 '아침에 우리가 닫은 것' 만 되연다.
    reopen = closed_by_us(target_date) if mode == "open" else None

    for miss in unknown:
        r = ProductResult(name=miss, product_id="", region="",
                          status=Status.NOT_IN_SEARCH,
                          detail="tpc_targets.py 에 없는 이름입니다")
        results.append(r)
        emit({"name": miss, "product_id": "", "region": "",
              "result": r.status.value, "memo": r.detail})

    # 포트가 살아 있는지 먼저 본다. 안 떠 있는 지역을 '스킵' 으로 세면
    # 그 지역 재고가 통째로 열린 채 남는다 — 실패로 남긴다.
    ports: dict[str, int] = {}
    for t in picks:
        ports[t["region"]] = port_override or REGION_PORT.get(t["region"], 0)
    dead = {rg: p for rg, p in ports.items() if not p or not C.alive(p)}

    for t in picks:
        port = ports[t["region"]]
        if t["region"] in dead:
            r = ProductResult(name=t["name"], product_id=t["product_id"],
                              region=t["region"], status=Status.CHROME_DOWN,
                              detail=(f"{t['region']} Chrome (포트 {port or '미설정'}) "
                                      f"가 응답하지 않습니다. 로그인된 Chrome 을 먼저 켜세요."))
            results.append(r)
            LOG.error("[%s] %s", t["name"], r.detail)
            emit({"name": r.name, "product_id": r.product_id, "region": r.region,
                  "result": r.status.value, "memo": r.detail})
            continue

        LOG.info("──── %s (%s) %s / %s ────", t["name"], t["product_id"],
                 t["region"], target_date)
        r = process_product(port, t, target_date, mode, dry_run, reopen)
        results.append(r)
        LOG.info("[%s] %s — %s (%.1f초)", t["name"], r.status.value,
                 r.memo(), r.elapsed)
        emit({"name": r.name, "product_id": r.product_id, "region": r.region,
              "date": target_date, "mode": mode,
              "result": r.status.value, "memo": r.memo(),
              "packages": r.packages, "changed": r.changed,
              "before": r.before, "after": r.after})
        time.sleep(GAP_SEC)

    success = sum(1 for r in results if r.status.is_success)
    failed = sum(1 for r in results if r.status.is_failure)
    skipped = sum(1 for r in results if r.status.is_skip)
    errors = [f"{r.name}: {r.status.value} — {r.detail}"
              for r in results if r.status.is_failure]

    print(_table(results, mode, target_date), flush=True)
    if errors:
        print("\nErrors:", flush=True)
        for e in errors:
            print(f"  - {e}", flush=True)
    print(f"\n[TPC] success={success} failed={failed} skipped={skipped}", flush=True)
    print(f"[  TPC] 성공 {success:3d} / 실패 {failed:3d} / 스킵 {skipped:3d}", flush=True)
    for e in errors:
        print(f"        └─ ERROR: {e}", flush=True)

    _save_log(mode, target_date, results)
    return {"agency": "TPC", "success": success, "failed": failed,
            "skipped": skipped, "errors": errors}


def _table(results: list[ProductResult], mode: str, target_date: str) -> str:
    lines = ["", "=" * 84,
             f" TPC {mode} 결과 ({target_date})", "=" * 84,
             f"{'상품':<18} {'번호':<11} {'지역':<10} {'결과':<16} 내용", "-" * 84]
    for r in results:
        lines.append(f"{r.name[:17]:<18} {r.product_id:<11} {r.region[:9]:<10} "
                     f"{r.status.value:<16} {r.memo()[:34]}")
    lines.append("=" * 84)
    return "\n".join(lines)


def _save_log(mode: str, target_date: str, results: list[ProductResult]) -> None:
    try:
        d = ROOT / "logs"
        d.mkdir(exist_ok=True)
        path = d / f"tpc_{mode}_{target_date}_{datetime.now():%H%M%S}.json"
        path.write_text(json.dumps(
            [{"name": r.name, "product_id": r.product_id, "region": r.region,
              "status": r.status.value, "detail": r.detail,
              "packages": r.packages, "changed": r.changed,
              "before": r.before, "after": r.after,
              "elapsed": round(r.elapsed, 1)} for r in results],
            ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception as e:                                # noqa: BLE001
        LOG.warning("로그 저장 실패: %s", e)


# ──────────────────────────────────────────────────────────────────────────────
def main() -> None:
    ap = argparse.ArgumentParser(description="TPC (Trip.com/Ctrip) 마감·수집·오픈")
    ap.add_argument("--mode", choices=["close", "open", "collect"], default="close")
    ap.add_argument("--date", type=valid_date, default=None,
                    help="대상 날짜 YYYY-MM-DD (기본: 내일)")
    ap.add_argument("--regions", default="",
                    help="KOREA,JAPAN,AUSTRALIA 중 쉼표 구분 (기본: TPC_REGIONS 또는 전체)")
    ap.add_argument("--region", default="", dest="region_single",
                    help="main.py 호환용. --regions 와 같은 뜻이다.")
    ap.add_argument("--targets", default="",
                    help="내부명칭 쉼표 구분 (기본: tpc_targets.py 전체)")
    ap.add_argument("--dry-run", action="store_true",
                    help="OK 를 누르지 않고 무엇을 바꿀지만 본다")
    ap.add_argument("--port", type=int, default=None, help="포트 강제 지정 (시험용)")
    args = ap.parse_args()
    if args.region_single and not args.regions:
        args.regions = args.region_single

    target_date = args.date or tomorrow()
    # 허브/스케줄러는 지역을 TPC_REGIONS 환경변수로 준다 (다른 지역 봇과 같은 방식).
    # 명령줄로 준 값이 우선이고, 둘 다 없으면 tpc_targets 의 전 지역을 돈다.
    regions = [x for x in (args.regions or os.environ.get("TPC_REGIONS", "")).split(",")
               if x.strip()]
    names = [x.strip() for x in args.targets.split(",") if x.strip()]

    if args.mode == "collect":
        LOG.info("수집 모드 — 읽기 전용입니다. 아무것도 바꾸지 않습니다.")
    elif args.dry_run:
        LOG.info("DRY-RUN — OK 를 누르지 않습니다.")

    r = run(args.mode, target_date, regions or None, names or None,
            dry_run=args.dry_run, port_override=args.port)
    sys.exit(1 if r["failed"] else 0)


if __name__ == "__main__":
    main()
