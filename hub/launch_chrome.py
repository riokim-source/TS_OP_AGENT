# -*- coding: utf-8 -*-
"""
launch_chrome.py
Chrome 런처 CLI. 모든 .bat 이 이걸 거치게 해서 '프로필/포트 단일 기준' 을 강제한다.

여태 Klook Open 과 OTA Close 가 각자 .bat 을 들고 있다가 같은 포트에 다른 프로필을
물려서 서로를 죽였다. 이제 어느 .bat 을 실행하든 hub/data/routing.json 이 정한
프로필 하나로 수렴한다.

사용:
    python launch_chrome.py KOREA        # 지역 이름
    python launch_chrome.py KR           # Profile ID
    python launch_chrome.py all          # 라우팅된 프로필 전부
    python launch_chrome.py --status     # 상태만 보기
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from core.routing import CHANNEL_META, get_routing

# 지역 이름 -> Profile ID (해당 지역의 Klook 이 붙은 프로필로 해석)
REGION_ALIAS = {
    "KOREA": "KLOOK", "JAPAN": "KLOOK", "AUSTRALIA": "KLOOK", "UK": "KLOOK",
}


def resolve(arg: str) -> str | None:
    r = get_routing()
    key = str(arg).strip().upper()
    if key in r.profile_keys:
        return key
    if key in REGION_ALIAS:
        # 그 지역의 대표 채널(Klook)이 붙은 프로필
        return r.route(key, REGION_ALIAS[key])
    if key == "GLOBAL":
        return "GLOBAL" if "GLOBAL" in r.profile_keys else None
    return None


def show_status() -> None:
    r = get_routing()
    print(f"{'Profile':10} {'Port':6} {'상태':16} OTA")
    print("-" * 74)
    for s in r.status_all():
        if s["conflict"]:
            state = "포트 충돌"
        elif s["owned"]:
            state = "연결됨"
        else:
            state = "미실행"
        print(f"{s['key']:10} {s['port']:<6} {state:16} {', '.join(s['routed_channels']) or '-'}")
        if s["conflict"]:
            print(f"{'':10} └ 다른 프로필의 Chrome 이 이 포트를 쓰고 있습니다. 그 창을 닫으세요.")


def launch(key: str) -> int:
    r = get_routing()
    chans = r.channels_for_profile(key)
    res = r.ensure(key, wait_seconds=20, channels=chans)
    mark = "OK " if res.get("ok") else "실패"
    print(f"[{mark}] {key} (port {r.profile_port(key)}) — {res.get('message', '')}")
    if res.get("ok"):
        print(f"       열린 사이트: {', '.join(CHANNEL_META[c]['label'] for c in chans) or '없음'}")
        print(f"       프로필: {r.profile_dir(key)}")
        print(f"       ※ 처음이면 각 사이트에 한 번씩 로그인해두세요. 창은 닫지 마세요(최소화는 OK).")
    return 0 if res.get("ok") else 1


def main() -> int:
    args = [a for a in sys.argv[1:] if a]
    if not args or args[0] in ("--status", "-s", "status"):
        show_status()
        return 0

    r = get_routing()
    if args[0].lower() == "all":
        targets = [k for k in r.profile_keys if r.channels_for_profile(k)]
    else:
        targets = []
        for a in args:
            key = resolve(a)
            if key is None:
                print(f"[실패] '{a}' 를 Profile 로 해석하지 못했습니다. "
                      f"가능한 값: {', '.join(r.profile_keys)} 또는 KOREA/JAPAN/AUSTRALIA/UK/GLOBAL")
                return 2
            if key not in targets:
                targets.append(key)

    rc = 0
    for key in targets:
        rc |= launch(key)
    print()
    show_status()
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
