from __future__ import annotations

import time

from collectors.base import CollectorContext
from collectors.config import CHANNEL_META
from rcore.utils import format_rating, norm_code


def collect(ctx: CollectorContext, dfrom: str, dto: str) -> dict[str, dict]:
    """MyRealTrip: paginate all reviews; client filter by travelStartDate."""
    if ctx.browser.driver is None:
        raise RuntimeError("Chrome is not connected")

    ctx.browser.driver.get(CHANNEL_META["MRT"]["page"])
    time.sleep(3)
    js = """
    var page=P[0], size=P[1];
    var tok=localStorage.getItem('accessToken');
    var res=await fetch('https://api3-backoffice.myrealtrip.com/review/partner/reviews/search',
      {method:'POST', credentials:'include',
       headers:{'Content-Type':'application/json','partner-access-token':tok},
       body:JSON.stringify({page:page, pageSize:size})});
    var j=await res.json();
    var list=Array.isArray(j.data)?j.data:[];
    done({rows:list.map(function(x){return {code:x.reservationNo, rating:x.score,
      content:x.comment||'', rdate:x.createdAt, tdate:x.travelStartDate};})});
    """

    reviews: dict[str, dict] = {}
    page, size = 1, 50
    while page <= 3000:
        r = ctx.browser.fetch_page(js, page, size)
        if not r or r.get("error"):
            raise RuntimeError(f"MRT p{page}: {r.get('error') if r else 'no response'}")
        rows = r.get("rows") or []
        if not rows:
            break
        for x in rows:
            tdate = (x.get("tdate") or "")[:10]
            if not tdate or not (dfrom <= tdate <= dto):
                continue
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
        ctx.log(f"[MRT] p{page}: {len(rows)} rows · {len(reviews)} collected")
        if len(rows) < size:
            break
        page += 1
        time.sleep(0.15)
    return reviews
