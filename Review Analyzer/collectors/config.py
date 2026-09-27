from __future__ import annotations

SUPPORTED_CHANNELS = ("L", "KK", "GG", "TPC", "MRT")

CHANNEL_META = {
    "L": {
        "name": "KLOOK",
        "page": "https://merchant.klook.com/reviews",
        "endpoint": "review_list",
    },
    "KK": {
        "name": "KKDAY",
        "page": "https://scm.kkday.com/v1/en/comment/index",
        "endpoint": "get_comment_list",
    },
    "GG": {
        "name": "GetYourGuide",
        "page": "https://supplier.getyourguide.com/performance/reviews",
        "endpoint": "/graphql",
        "body_contains": "bookingReference",
    },
    "TPC": {
        "name": "Trip.com / Ctrip",
        "page": "https://vbooking.ctrip.com/tour/comment_manage/comment/list?bizScene=ACTIVITY",
        "endpoint": "listOrderComments",
    },
    "MRT": {
        "name": "MyRealTrip",
        "page": "https://partner.myrealtrip.com/reviews/touractivity",
        "endpoint": "reviews/search",
    },
}

CHANNEL_DISPLAY = {
    "L": "Klook",
    "KK": "KKday",
    "GG": "GetYourGuide",
    "TPC": "Trip.com/Ctrip",
    "MRT": "MyRealTrip",
}
