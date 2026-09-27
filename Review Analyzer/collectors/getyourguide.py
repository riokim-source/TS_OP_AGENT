from __future__ import annotations

import json
import time
from datetime import timedelta

import pandas as pd

from collectors.base import CollectorContext
from collectors.config import CHANNEL_META
from rcore.utils import format_rating, norm_code

# 한 번에 요청할 리뷰 수. 캡처된 요청이 더 작은 값을 쓰면 그걸 따른다.
# 서버가 요청보다 적게 주는 경우가 있어서, '적게 왔다' 를 끝으로 보지 않는다.
PAGE_SIZE = 50
# 무한 루프 방지. 50건씩 400페이지 = 2만 건.
MAX_PAGES = 400


def collect(ctx: CollectorContext, dfrom: str, dto: str) -> dict[str, dict]:
    """GetYourGuide: 투어 날짜로 서버 필터 · 1~5점 + 코멘트 수집.

    ⚠️ 예전에는 HTTP 상태와 GraphQL errors 를 보지 않았다. 그래서 401(로그인
       만료)이나 GraphQL 오류가 나면 리뷰 목록이 빈 배열로 읽혀 '더 이상
       페이지가 없다' 로 처리됐다. 수집 0건인데 상태는 SUCCESS 로 남고,
       보고서 숫자만 조용히 줄어든다. 실제로 이 증상이 확인됐다.
       이제는 반드시 오류로 멈춘다.
    """
    cap = ctx.browser.capture_request(
        CHANNEL_META["GG"]["page"], "/graphql", body_contains="bookingReference", wait=12
    )
    if not cap or not cap.get("body"):
        raise RuntimeError(
            "GetYourGuide GraphQL 요청을 잡지 못했습니다. "
            "Chrome 에서 GetYourGuide 공급자 페이지에 로그인되어 있는지 확인하세요."
        )

    # 캡처된 요청이 쓰는 페이지 크기를 확인한다. 서버가 상한을 둘 수 있으므로
    # 우리가 임의로 키운 값이 통하는지 첫 페이지에서 확인한 뒤 계속한다.
    captured_limit = None
    try:
        payload = json.loads(cap.get("body") or "{}")
        inp = (payload.get("variables") or {}).get("input") or {}
        captured_limit = int(inp.get("limit")) if inp.get("limit") else None
        ctx.log(f"[GG] 캡처된 limit={captured_limit} · offset={inp.get('offset')}")
    except Exception:
        pass

    # 투어 날짜 경계는 하루 넉넉하게 본다. 수집 후 예약과 매칭하므로 범위 밖
    # 리뷰는 저절로 빠지고, 시간대 차이로 하루가 잘리는 것을 막는다.
    sd = (pd.Timestamp(dfrom) - timedelta(days=1)).strftime("%Y-%m-%d")
    ed = dto

    js = r"""
    var cap=P[0], off=P[1], size=P[2], sd=P[3], ed=P[4];
    var payload={}; try{ payload=JSON.parse(cap.body); }catch(e){}
    payload.variables = payload.variables || {};
    var inp = payload.variables.input || {};
    inp.travelDateFrom=sd; inp.travelDateTo=ed;
    delete inp.reviewDateFrom; delete inp.reviewDateTo;
    inp.ratings=[1,2,3,4,5]; inp.limit=size; inp.offset=off;
    payload.variables.input = inp;

    var status=0, txt='';
    try {
      var res=await fetch(cap.url,{method:'POST',credentials:'include',
        headers:Object.assign({}, cap.headers||{}, {'Content-Type':'application/json',
          'apollo-require-preflight':'true',
          'x-apollo-operation-name':(payload.operationName||'Reviews_ReviewSearch')}),
        body:JSON.stringify(payload)});
      status=res.status;
      txt=await res.text();
    } catch(e) {
      done({error:'GG fetch 실패: '+String(e), status:status});
      return;
    }

    var j=null; try{ j=JSON.parse(txt); }catch(e){}

    // ★ 여기서 멈추지 않으면 401·500·GraphQL 오류가 '데이터 끝' 으로 읽힌다.
    if (status < 200 || status >= 300) {
      done({error:'HTTP '+status+' · '+String(txt).slice(0,200), status:status});
      return;
    }
    if (j && j.errors && j.errors.length) {
      var m=''; try{ m=JSON.stringify(j.errors).slice(0,300); }catch(e){ m='(errors)'; }
      done({error:'GraphQL 오류 · '+m, status:status});
      return;
    }
    if (!j) {
      done({error:'응답을 JSON 으로 읽지 못했습니다 · '+String(txt).slice(0,200), status:status});
      return;
    }

    function findArr(o,d){ if(d>12||!o||typeof o!=='object')return null;
      if(Array.isArray(o)&&o.length&&o[0]&&(o[0].bookingReference!==undefined||o[0].reviewId!==undefined))return o;
      for(var k in o){var r=findArr(o[k],d+1); if(r)return r;} return null; }
    var list=findArr(j,0)||[];

    // 서버가 총 개수를 주면 받아 둔다. 페이지를 다 넘겼는지 확인하는 데 쓴다.
    var total=null;
    (function scan(o,d){ if(total!==null||d>10||!o||typeof o!=='object')return;
      for(var k in o){ var v=o[k];
        if(typeof v==='number' && /^(total|totalCount|totalResults|count)$/i.test(k)){ total=v; return; }
        scan(v,d+1); } })(j,0);

    done({rows:list.map(function(x){return {code:x.bookingReference, rating:x.rating,
      content:x.comment||'', rdate:(x.createdAt||'').slice(0,10)};}), total:total, status:status});
    """

    reviews: dict[str, dict] = {}
    size = PAGE_SIZE
    if captured_limit and captured_limit > PAGE_SIZE:
        size = captured_limit

    page, rows_seen, total, empty_streak = 1, 0, None, 0
    while page <= MAX_PAGES:
        off = (page - 1) * size
        r = ctx.browser.fetch_page(js, cap, off, size, sd, ed)
        if not r:
            raise RuntimeError(f"GYG p{page}: 응답 없음 (offset={off} limit={size})")
        if r.get("error"):
            raise RuntimeError(
                f"GYG p{page}: {r.get('error')} (offset={off} limit={size} "
                f"travelDate {sd}~{ed})"
            )

        rows = r.get("rows") or []
        if total is None and r.get("total") is not None:
            total = r.get("total")

        before = len(reviews)
        rows_seen += len(rows)
        for x in rows:
            try:
                rating = float(x.get("rating"))
                if rating < 1 or rating > 5:
                    continue
            except (ValueError, TypeError):
                continue
            code = norm_code(x.get("code"))
            if not code:
                continue
            reviews[code] = {
                "rating": format_rating(x.get("rating")),
                "content": (x.get("content") or "").strip(),
                "review_date": (x.get("rdate") or "")[:10],
            }
        new = len(reviews) - before

        ctx.log(
            f"[GG] p{page}: {len(rows)} rows (신규 {new}) · 누적 예약코드 {len(reviews)}"
            + (f" / 서버 total {total}" if total is not None else "")
        )

        if not rows:
            break
        # ⚠️ '요청보다 적게 왔다' 를 끝으로 보지 않는다. 서버가 limit 을 자체
        #    상한으로 깎아도 다음 페이지에 데이터가 남아 있을 수 있다.
        #    새 코드가 하나도 안 늘어난 페이지가 두 번 연속이면 끝으로 본다.
        empty_streak = empty_streak + 1 if new == 0 else 0
        if empty_streak >= 2:
            break
        try:
            if total is not None and rows_seen >= int(total):
                break
        except (TypeError, ValueError):
            pass

        page += 1
        time.sleep(0.20)
    else:
        ctx.log(f"[GG] ⚠ 페이지 상한 {MAX_PAGES} 에 걸려 중단했습니다. 기간을 나눠 수집하세요.")

    if total is None:
        ctx.log(
            f"[GG] 서버가 총 개수를 주지 않아 수집 완전성을 확인할 수 없습니다 "
            f"(가져온 행 {rows_seen} · 예약코드 {len(reviews)})."
        )
    elif rows_seen < int(total):
        ctx.log(
            f"[GG] ⚠ 서버 total {total} 보다 적게 가져왔습니다 (행 {rows_seen}). "
            "기간을 나눠 다시 수집해 확인하세요."
        )
    if rows_seen != len(reviews):
        ctx.log(
            f"[GG] 참고: 가져온 리뷰 행 {rows_seen}건 → 예약코드 {len(reviews)}건. "
            "한 예약에 리뷰가 여러 건이거나 별점 범위를 벗어난 행이 있으면 차이가 납니다."
        )
    return reviews
