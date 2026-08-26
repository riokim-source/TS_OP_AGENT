# -*- coding: utf-8 -*-
"""Region x OTA 라우팅 / Chrome 프로필. 계정이 바뀔 때만 여는 화면."""
from __future__ import annotations

import streamlit as st

from common import CHANNEL_LABEL, page, running_banner
from core.routing import get_routing

page("Settings", "⚙️")
running_banner()

r = get_routing()

st.subheader("Region × OTA 연결")
st.caption("각 지역의 OTA 를 어느 Chrome 계정으로 열지 지정합니다. "
           "**미설정**인 조합은 실행되지 않습니다 (엉뚱한 계정에 붙는 것보다 안전).")

profiles = list(r.config.get("profiles", {}).keys())
routes = r.config.get("routes", {})
options = ["(미설정)"] + profiles

changed = False
for region in routes:
    st.markdown(f"**{region}**")
    cols = st.columns(len(CHANNEL_LABEL))
    for col, ch in zip(cols, CHANNEL_LABEL):
        cur = (routes.get(region) or {}).get(ch)
        idx = options.index(cur) if cur in options else 0
        with col:
            new = st.selectbox(CHANNEL_LABEL[ch], options, index=idx,
                               key=f"rt-{region}-{ch}")
            if new != (cur or "(미설정)"):
                try:
                    r.set_route(region, ch, None if new == "(미설정)" else new)
                    changed = True
                except ValueError as e:
                    st.error(str(e))
if changed:
    st.success("저장했습니다.")
    st.rerun()

st.divider()
st.subheader("Chrome Profile")
st.caption("계정이 분리/통합되면 여기서 포트와 프로필 폴더를 바꿉니다. "
           "같은 포트를 두 Profile 이 쓰면 저장되지 않습니다.")

st.dataframe(
    [{"ID": k, "이름": v.get("name", ""), "Port": v.get("port"),
      "user-data-dir": str(r.profile_dir(k))}
     for k, v in r.config.get("profiles", {}).items()],
    width="stretch", hide_index=True)

st.caption("저장값에는 `%LOCALAPPDATA%` 토큰이 들어갑니다. "
           "폴더를 다른 PC 로 옮겨도 각자 자기 폴더로 풀립니다.")

with st.expander("프로필 추가 / 수정"):
    c1, c2, c3, c4 = st.columns([1, 2, 1, 4])
    key = c1.text_input("ID", placeholder="JP2")
    name = c2.text_input("이름", placeholder="Japan 2nd Account")
    port = c3.number_input("Port", min_value=1024, max_value=65535, value=9526)
    pdir = c4.text_input("user-data-dir", placeholder=r"%LOCALAPPDATA%\OTABot\chrome_jp2")
    if st.button("저장", disabled=not key):
        try:
            r.upsert_profile(key, name or key, int(port), pdir)
            st.success(f"{key.upper()} 저장됨")
            st.rerun()
        except ValueError as e:
            st.error(str(e))
