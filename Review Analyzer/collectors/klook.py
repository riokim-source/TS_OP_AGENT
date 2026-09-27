from __future__ import annotations

import time

from collectors.base import CollectorContext
from collectors.config import CHANNEL_META
from rcore.utils import format_rating, norm_code


def collect(ctx: CollectorContext, dfrom: str, dto: str) -> dict[str, dict]:
    """Klook: participant-date server filter; collect 1~5 stars + review text."""
    cap = ctx.browser.capture_request(CHANNEL_META["L"]["page"], "review_list", wait=10)
    if not cap:
        raise RuntimeError("Klook request capture failed")

    js = """
    var cap=P[0], page=P[1], limit=P[2], sd=P[3], ed=P[4];
    var u=new URL(cap.url, location.origin);
    u.searchParams.set('date_type', 'ParticipantTime');
    u.searchParams.set('start_date', sd);
    u.searchParams.set('end_date', ed);
    u.searchParams.set('stars','0');
    u.searchParams.set('page', String(page));
    u.searchParams.set('limit', String(limit));
    var res=await fetch(u.toString(), {credentials:'include', headers:(cap.headers||{})});
    var j=await res.json();
    var list=(j.result&&j.result.review_list)||j.review_list||[];
    var total=(j.result&&j.result.total)||null;
    done({rows:list.map(function(x){return {code:x.booking_no, rating:x.stars,
      content:x.review||'', rdate:x.review_time};}), total:total});
    """

    reviews: dict[str, dict] = {}
    page, limit, total, got = 1, 50, None, 0
    while page <= 4000:
        r = ctx.browser.fetch_page(js, cap, page, limit, dfrom, dto)
        if not r or r.get("error"):
            raise RuntimeError(f"Klook p{page}: {r.get('error') if r else 'no response'}")
        rows = r.get("rows") or []
        if total is None:
            total = r.get("total")
        if not rows:
            break
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
        got += len(rows)
        ctx.log(f"[L] p{page}: {len(rows)} rows · {len(reviews)} matched candidates")
        if total and got >= total:
            break
        page += 1
        time.sleep(0.10)
    return reviews
