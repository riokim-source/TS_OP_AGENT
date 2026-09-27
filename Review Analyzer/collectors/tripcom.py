from __future__ import annotations

import time
from datetime import timedelta

import pandas as pd

from collectors.base import CollectorContext
from collectors.config import CHANNEL_META
from rcore.utils import format_rating, norm_code, parse_any_ms, to_epoch_ms


def collect(ctx: CollectorContext, dfrom: str, dto: str) -> dict[str, dict]:
    """Trip.com/Ctrip: comment-time pagination, client filter by departureTime."""
    cap = ctx.browser.capture_request(CHANNEL_META["TPC"]["page"], "listOrderComments", wait=10)
    if not cap:
        raise RuntimeError("Trip.com/Ctrip request capture failed")

    js = """
    var cap=P[0], page=P[1], size=P[2];
    var body={sceneInfo:{bizScene:'ACTIVITY'}, paging:{pageNo:page, pageSize:size},
      sorting:{orderBy:'COMMENT_TIME', desc:true}};
    try{ var b=JSON.parse(cap.body); if(b&&b.sceneInfo){ body.sceneInfo=b.sceneInfo; } }catch(e){}
    var res=await fetch(cap.url,{method:'POST',credentials:'include',
      headers:Object.assign({}, cap.headers||{}, {'Content-Type':'application/json'}),
      body:JSON.stringify(body)});
    var j=await res.json();
    var list=j.comments||[];
    done({rows:list.map(function(x){return {code:String(x.orderId), rating:x.score,
      content:x.content||'', ctime:x.commentTime,
      dtime:(x.orderInfo&&x.orderInfo.departureTime)||null};})});
    """

    lo = to_epoch_ms(pd.Timestamp(dfrom))
    hi = to_epoch_ms(pd.Timestamp(dto) + timedelta(days=1)) - 1
    # Comments are sorted by comment time, not departure time. Keep a generous stop buffer.
    stop_ms = to_epoch_ms(pd.Timestamp(dfrom) - timedelta(days=60))

    reviews: dict[str, dict] = {}
    page, size = 1, 50
    while page <= 3000:
        r = ctx.browser.fetch_page(js, cap, page, size)
        if not r or r.get("error"):
            raise RuntimeError(f"TPC p{page}: {r.get('error') if r else 'no response'}")
        rows = r.get("rows") or []
        if not rows:
            break
        oldest_ok = True
        for x in rows:
            ct = parse_any_ms(x.get("ctime"))
            dt = parse_any_ms(x.get("dtime"))
            if dt is not None and lo <= dt <= hi:
                try:
                    rating = float(x.get("rating"))
                    if 1 <= rating <= 5:
                        code = norm_code(x.get("code"))
                        if code and code != "0":
                            rd = pd.Timestamp(ct, unit="ms").strftime("%Y-%m-%d") if ct else ""
                            reviews[code] = {
                                "rating": format_rating(x.get("rating")),
                                "content": (x.get("content") or "").strip(),
                                "review_date": rd,
                            }
                except (ValueError, TypeError):
                    pass
            if ct is not None and ct < stop_ms:
                oldest_ok = False
        ctx.log(f"[TPC] p{page}: {len(rows)} rows · {len(reviews)} collected")
        if not oldest_ok:
            break
        page += 1
        time.sleep(0.12)
    return reviews
