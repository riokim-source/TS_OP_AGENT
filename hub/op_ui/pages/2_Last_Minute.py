# -*- coding: utf-8 -*-
"""
Last Minute — 아침 업무 한 페이지.

  [Inventory 마감]  내일 날짜 재고를 0 으로 (10시 전)
  [수량 수집 · 오픈]  예약 파일 -> 수량 -> Office/OP 텍스트 -> 자동 오픈
  [직접 오픈]        수집을 거치지 않고 상품만 골라 오픈 (뒤늦은 추가/수정)

마감과 오픈은 같은 Chrome 을 쓰므로 동시에 돌 수 없다. 한쪽이 실행 중이면
다른 쪽 버튼이 잠긴다.
"""
from __future__ import annotations

import streamlit as st

import close_tab
import lastmin_tab
import open_tab
from common import page, running_banner

page("Last Minute", "🕙")
lock = running_banner()

t1, t2, t3 = st.tabs(["Inventory 마감", "수량 수집 · 오픈", "직접 오픈"])
with t1:
    close_tab.render(lock)
with t2:
    lastmin_tab.render(lock)
with t3:
    open_tab.render(lock)
