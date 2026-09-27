from __future__ import annotations

import json
import time
from datetime import date, timedelta

from collectors.base import CollectorContext
from collectors.config import CHANNEL_META
from rcore.utils import format_rating, norm_code

# 실측(2026-09): pageSize 10/20/30/50 은 허용, 100 은 거부되며
# message='json.page.pageSize' 가 온다. 그래서 상한을 50 으로 둔다.
PAGE_SIZE = 50
MAX_PAGES = 400

# ★ KKday 는 투어 날짜(begGoDate~endGoDate) 범위가 길면 요청을 거부한다.
#   실측: 32일 OK · 33일부터 result=false, message='Input parameter error'.
#   분기(92일)로 수집하면 첫 페이지에서 바로 이 오류가 났다. 그래서 기간을
#   잘라서 여러 번 요청하고 합친다. 여유를 두어 31일로 잡는다.
MAX_SPAN_DAYS = 31

# 기간을 잘랐는데도 거부되면 (KKday 가 상한을 더 줄인 경우) 반으로 더 쪼갠다.
MIN_SPAN_DAYS = 1


def _fmt_body(body: dict) -> str:
    """오류에 붙일 요청 요약. 길고 불필요한 값은 빼고 날짜·페이지만 남긴다."""
    keys = ("begGoDate", "endGoDate", "begRecDate", "endRecDate",
            "currentPage", "pageSize", "recScores")
    return " ".join(f"{k}={body.get(k)!r}" for k in keys if k in body)


def collect(ctx: CollectorContext, dfrom: str, dto: str) -> dict[str, dict]:
    """KKday: 로그인된 페이지가 보낸 실제 요청을 그대로 재생하고 날짜·페이지만 바꾼다."""
    cap = ctx.browser.capture_request(CHANNEL_META["KK"]["page"], "get_comment_list", wait=10)
    if not cap or not cap.get("body"):
        raise RuntimeError(
            "KKday 요청을 잡지 못했습니다. Chrome 에서 KKday SCM 리뷰 페이지에 "
            "로그인되어 있는지 확인하세요."
        )

    # 캡처된 요청의 필터는 그대로 둔다. 특히 recScores 를 억지로 바꾸지 않는다.
    try:
        captured_body = json.loads(cap.get("body") or "{}")
        if not isinstance(captured_body, dict):
            captured_body = {}
    except Exception:
        captured_body = {}

    captured_size = 0
    try:
        captured_size = int(captured_body.get("pageSize") or 0)
    except (TypeError, ValueError):
        captured_size = 0

    # 무엇을 보고 시작하는지 남긴다. 실패했을 때 이 로그가 있어야 원인을 좁힐 수 있다.
    ctx.log(f"[KK] 캡처된 요청: {_fmt_body(captured_body)}")
    ctx.log(f"[KK] 요청 기간(투어 날짜): {dfrom} ~ {dto}")

    js = r"""
    var cap=P[0], page=P[1], size=P[2], sd=P[3], ed=P[4];
    var body={};
    try { body=JSON.parse(cap.body)||{}; } catch(e) { body={}; }

    // 투어 날짜 범위와 페이지만 바꾼다.
    // recScores 등 나머지 필터는 캡처된 값(=실제로 동작하는 값)을 그대로 쓴다.
    body.begGoDate=sd;
    body.endGoDate=ed;
    body.begRecDate='';
    body.endRecDate='';
    body.currentPage=page;
    body.pageSize=size;

    var status=0, j={};
    try {
      var res=await fetch(cap.url, {
        method:'POST',
        credentials:'include',
        headers:Object.assign({}, cap.headers||{}, {'Content-Type':'application/json'}),
        body:JSON.stringify(body)
      });
      status=res.status;
      try { j=await res.json(); }
      catch(e) { done({error:'응답을 JSON 으로 읽지 못했습니다: '+String(e), status:status, sent:body}); return; }
    } catch(e) {
      done({error:'fetch 실패: '+String(e), status:status, sent:body});
      return;
    }

    var message=(j&&(j.message||j.msg))||'';
    var code=(j&&(j.code||j.resultCode))||'';
    if (status < 200 || status >= 300) {
      done({error:'HTTP='+status+' | code='+code+' | message='+message,
            status:status, code:code, message:message, sent:body});
      return;
    }
    if (j && j.result === false) {
      done({error:'API result=false | code='+code+' | message='+message,
            status:status, code:code, message:message, sent:body});
      return;
    }

    var data=(j&&j.data)||{};
    var rl=data.recommandList||data.recommendList||[];
    var total=null;
    if (data.totalCount !== undefined && data.totalCount !== null) total=data.totalCount;
    else if (data.size !== undefined && data.size !== null) total=data.size;
    else if (data.total !== undefined && data.total !== null) total=data.total;
    else if (j && j.totalCount !== undefined && j.totalCount !== null) total=j.totalCount;

    done({
      rows:rl.map(function(x){
        return {
          code:x.orderMid,
          rating:x.recScore,
          title:x.recTitle||'',
          desc:x.recDesc||'',
          rdate:x.userRecDt||'',
          id:x.recOid
        };
      }),
      total:total,
      status:status,
      code:code,
      message:message,
      dataKeys:Object.keys(data).join(',')
    });
    """

    def windows(a: str, b: str, span: int) -> list[tuple[str, str]]:
        """[a, b] 를 span 일 이하 구간으로 자른다."""
        start, end = date.fromisoformat(a), date.fromisoformat(b)
        if end < start:
            start, end = end, start
        out: list[tuple[str, str]] = []
        cur = start
        while cur <= end:
            last = min(cur + timedelta(days=span - 1), end)
            out.append((cur.isoformat(), last.isoformat()))
            cur = last + timedelta(days=1)
        return out

    def fetch(page: int, size: int, w_from: str, w_to: str) -> dict:
        r = ctx.browser.fetch_page(js, cap, page, size, w_from, w_to)
        if not r:
            raise RuntimeError(
                f"KKday p{page}: 응답 없음 (pageSize={size} 기간 {w_from}~{w_to})"
            )
        return r

    reviews: dict[str, dict] = {}
    state = {"size": PAGE_SIZE}

    def one_window(w_from: str, w_to: str, span: int) -> int:
        """한 구간을 수집한다. 기간이 길어 거부되면 반으로 쪼개 다시 시도한다."""
        page, seen, total, rows_seen = 1, set(), None, 0
        added = 0

        while page <= MAX_PAGES:
            r = fetch(page, state["size"], w_from, w_to)

            # pageSize 때문에 거부된 것이면 페이지가 실제로 쓰던 크기로 한 번 더.
            if r.get("error") and captured_size and state["size"] != captured_size:
                msg = str(r.get("message") or r.get("error") or "")
                if "pageSize" in msg:
                    ctx.log(
                        f"[KK] pageSize={state['size']} 가 거부되어 캡처된 "
                        f"{captured_size} 로 바꿉니다 (message={msg})"
                    )
                    state["size"] = captured_size
                    r = fetch(page, state["size"], w_from, w_to)

            if r.get("error"):
                msg = str(r.get("message") or "")
                # 기간이 길어서 거부된 경우: 더 쪼개면 통한다.
                if "Input parameter" in msg and span > MIN_SPAN_DAYS:
                    half = max(MIN_SPAN_DAYS, span // 2)
                    ctx.log(
                        f"[KK] {w_from}~{w_to} ({span}일) 이 거부되어 {half}일 "
                        f"단위로 더 쪼갭니다 (message={msg})"
                    )
                    for a, b in windows(w_from, w_to, half):
                        added += one_window(a, b, half)
                    return added

                sent = r.get("sent") if isinstance(r.get("sent"), dict) else {}
                raise RuntimeError(
                    f"KKday p{page}: {r.get('error')}"
                    f" | 보낸 값: {_fmt_body(sent) or '(확인 불가)'}"
                    f" | 캡처된 값: {_fmt_body(captured_body)}"
                )

            if total is None:
                total = r.get("total")

            rows = r.get("rows") or []
            if not rows:
                break

            rows_seen += len(rows)
            before = len(reviews)
            for x in rows:
                key = x.get("id") or x.get("code")
                if key in seen:
                    continue
                seen.add(key)
                code = norm_code(x.get("code"))
                if not code:
                    continue
                content = (
                    (x.get("title") or "").strip() + "\n"
                    + (x.get("desc") or "").strip()
                ).strip()
                reviews[code] = {
                    "rating": format_rating(x.get("rating")),
                    "content": content,
                    "review_date": (x.get("rdate") or "")[:10],
                }
            added += len(reviews) - before

            try:
                if total is not None and len(seen) >= int(total):
                    break
            except (TypeError, ValueError):
                pass
            if len(rows) < state["size"]:
                break
            page += 1
            time.sleep(0.15)
        else:
            ctx.log(f"[KK] ⚠ {w_from}~{w_to}: 페이지 상한 {MAX_PAGES} 에서 중단")

        ctx.log(
            f"[KK] {w_from}~{w_to}: 리뷰 {rows_seen}건"
            + (f" / 서버 total {total}" if total is not None else "")
            + f" · 누적 예약코드 {len(reviews)}"
        )
        if total is not None:
            try:
                if rows_seen < int(total):
                    ctx.log(
                        f"[KK] ⚠ {w_from}~{w_to}: 서버 total {total} 보다 적게 "
                        f"가져왔습니다 ({rows_seen}건)."
                    )
            except (TypeError, ValueError):
                pass
        return added

    spans = windows(dfrom, dto, MAX_SPAN_DAYS)
    if len(spans) > 1:
        ctx.log(
            f"[KK] KKday 는 투어 날짜 범위를 {MAX_SPAN_DAYS + 1}일 이상 받지 않습니다. "
            f"{len(spans)}개 구간으로 나눠 수집합니다."
        )
    for a, b in spans:
        one_window(a, b, (date.fromisoformat(b) - date.fromisoformat(a)).days + 1)

    ctx.log(f"[KK] 합계: 예약코드 {len(reviews)}건 ({len(spans)}개 구간)")
    return reviews
