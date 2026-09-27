# -*- coding: utf-8 -*-
"""
라스트미닛 수량 수집 -> Office/OP 텍스트 -> 자동 오픈.

업무 로직은 hub/core/lastmin 을 그대로 쓴다. 여기는 화면만 담당한다.
"""
from __future__ import annotations

import json
import time
from datetime import date

import streamlit as st

import dispatch
import open_tab
from common import (CHANNEL_LABEL, date_picker, render_logs, render_results,
                    who, who_input)
from core import paths
from core import pickups as lmpickups
from core.lastmin import constants as C
from core.lastmin import entries as lmentries
from core.lastmin.memo import RowInput, build_open_plan, render_memo
from core.lastmin.panels import build_panels
from core.lastmin import quickfill
from core.opens import IMPLEMENTED, NOT_IMPLEMENTED_REASON, summarize_plan

LANG_LABEL = {"english": "영어", "korean": "한국어", "chinese": "중국어", "japanese": "일본어"}
CACHE = paths.DATA_DIR / "last_upload.bin"
CACHE_META = paths.DATA_DIR / "last_upload.json"


# ── 입력 상태 ────────────────────────────────────────────────────────────
def _key(pi: int, row: dict) -> str:
    return f"{pi}::{row['key']}"


def _entry(pi: int, row: dict) -> dict:
    """투어 한 줄의 입력값. 기본값은 '제한 없음'."""
    k = _key(pi, row)
    e = st.session_state.setdefault("lm_entries", {}).setdefault(k, {})
    e.setdefault("qty", 0)
    e.setdefault("lang", list(row.get("languages") or []))
    e.setdefault("pick", list(row.get("pickups") or []))
    e.setdefault("ch", dict(row.get("lastmin") or {}))
    e.setdefault("move", {})
    return e


def _load(raw: bytes, filename: str, pick_dates=None) -> bool:
    r = build_panels(raw, filename, pick_dates=pick_dates)
    if not r.get("ok"):
        st.error(r.get("error", "변환 실패"))
        return False
    st.session_state["lm_raw"] = raw          # 날짜를 바꿔 다시 읽을 때 쓴다
    st.session_state["lm_panels"] = r["panels"]
    st.session_state["lm_loaded"] = r["loaded"]
    st.session_state["lm_catalog"] = r["pickup_catalog"]

    # 새로고침/재접속에도 손으로 넣은 값이 살아있게 한다.
    # 지금 화면에 없는 투어의 값은 버린다 (예약 파일이 바뀌었을 수 있다).
    valid = {f"{pi}::{row['key']}"
             for pi, p in enumerate(r["panels"])
             for g in p["groups"] for a in g["areas"] for row in a["rows"]}
    saved = lmentries.prune(lmentries.load(r["loaded"], who()), valid)
    st.session_state["lm_entries"] = saved
    # ⚠️ 위젯 키가 세션에 남아 있으면 Streamlit 이 그 값을 우선한다.
    #    되살린 수량이 화면에 안 나오고 이전 값이 그대로 보인다.
    #    파일을 새로 읽을 때는 위젯 상태를 비워서 되살린 값으로 다시 그리게 한다.
    for wk in [w for w in list(st.session_state)
               if str(w).startswith(("q-", "l-", "p-", "c-", "mv-", "qf-"))]:
        del st.session_state[wk]
    # 빠른 입력 칸에 적어 둔 글도 파일과 함께 비운다 (다른 날 글이 남으면 헷갈린다)
    st.session_state["lm_qf"] = {}
    st.session_state["lm_qf_note"] = {}
    st.session_state["lm_restored"] = sum(
        1 for v in saved.values() if int((v or {}).get("qty") or 0) > 0)
    return True


def _rows_for(pi: int, with_moves: bool = True) -> list:
    p = (st.session_state.get("lm_panels") or [])[pi]
    out = []
    for g in p["groups"]:
        for a in g["areas"]:
            for row in a["rows"]:
                e = _entry(pi, row)
                out.append(RowInput(
                    area=a["area"], product=row["product"], option=row.get("option") or "",
                    option_split=bool(row.get("option_split")), qty=int(e["qty"] or 0),
                    languages_all=list(row.get("languages") or []),
                    languages_sel=list(e["lang"]),
                    pickups_all=list(row.get("pickups") or []),
                    pickups_sel=list(e["pick"]),
                    options_all=list(row.get("options") or []),
                    options_sel=list(row.get("options") or []),
                    channel_qty={k: int(v or 0) for k, v in (e["ch"] or {}).items()},
                    channel_flag={},
                    channel_moves=dict(e["move"] or {}) if with_moves else {},
                ))
    return out


def _panels_payload() -> list:
    panels = st.session_state.get("lm_panels") or []
    return [{"date_label": p["date_label"], "is_latest": p["is_latest"],
             "rows": _rows_for(pi)} for pi, p in enumerate(panels)]


def _others_note(L: dict) -> None:
    """
    같은 파일을 만지고 있는 다른 사람을 알린다.

    ⚠️ 두 사람이 각자 수량을 넣고 한쪽이 실행하면 상대 몫이 빠진 채로
       열린다. 막지는 않는다 — 누가 몇 건 넣었는지 보여주고 사람이
       이야기해서 정하게 한다.
    """
    try:
        rest = lmentries.others(L, who())
    except Exception:
        return
    if not rest:
        return
    st.warning("같은 파일을 **다른 사람도** 작업하고 있습니다. "
               "각자 넣은 수량은 따로 저장되므로, 실행 전에 누가 넣을지 정하세요.",
               icon="👥")
    st.dataframe([{"사람": r["who"], "넣은 투어": r["count"], "마지막 저장": r["at"]}
                  for r in rest], width="stretch", hide_index=True)


def _dropped_note(L: dict) -> None:
    """
    읽다가 버린 행을 이유와 함께 보여준다.

    ⚠️ 조용히 빠지는 것이 제일 위험하다. Area 나 Product 가 비어 있으면
       그 행은 집계에서 빠져 투어가 화면에 아예 안 나온다. 안 보이니
       수량도 못 넣고, 그날 그 투어는 안 열린다.
       (숫자가 맞는지보다 '빠진 게 있는지' 를 사람이 알아야 한다)
    """
    probs = L.get("problems") or []
    if not probs:
        return
    rows = sum(int(p.get("rows") or 0) for p in probs)
    pax = sum(int(p.get("people") or 0) for p in probs)
    st.warning(f"**{rows}행 (인원 {pax}명)** 이 집계에서 빠지거나 값이 이상합니다. "
               "빠진 투어는 화면에 안 나오고, 그대로 두면 오늘 열리지 않습니다.",
               icon="⚠️")
    st.dataframe(
        [{"이유": p["reason"], "행": p["rows"], "인원": p["people"],
          "어떻게 할까": p["fix"]} for p in probs],
        width="stretch", hide_index=True)


def _date_choice(L: dict) -> None:
    """
    파일에 투어일자가 3개 이상이면 사람이 고르게 한다.

    ⚠️ 기본은 '가장 나중 2개' 다. 추출 범위를 넓게 잡아 9/15 가 섞여
       들어오면 오픈 대상이 9/15 가 되고, 내일만 있는 투어는 화면에
       아예 안 나와 그날 안 열린다. 날짜가 늘어난 것을 사람이 모르면
       끝까지 모른다.
    """
    if not L.get("date_choice"):
        return
    every = list(L.get("all_dates") or [])
    now = list(L.get("dates") or [])
    st.warning(f"이 파일에 투어일자가 **{len(every)}개** 있습니다 "
               f"({', '.join(every)}). 지금은 가장 나중 2개(**{' / '.join(now)}**)로 "
               "보고 있습니다. 내일 것이 맞는지 확인하세요.", icon="📅")
    c1, c2, c3 = st.columns([2, 2, 1])
    with c1:
        a = st.selectbox("오픈 대상 (최신)", every,
                         index=every.index(now[0]) if now and now[0] in every else 0,
                         key="lm_pick_a")
    with c2:
        rest = [d for d in every if d != a]
        prev = now[1] if len(now) > 1 and now[1] in rest else (rest[0] if rest else a)
        b = st.selectbox("전일 (10시 후 예약)", rest,
                         index=rest.index(prev) if prev in rest else 0,
                         key="lm_pick_b")
    with c3:
        st.write("")
        if st.button("이 날짜로", width="stretch", key="lm_pick_go"):
            raw = st.session_state.get("lm_raw")
            if not raw and CACHE.exists():
                raw = CACHE.read_bytes()
            if raw and _load(raw, L.get("filename") or "last.xlsx",
                             pick_dates=[date.fromisoformat(a), date.fromisoformat(b)]):
                st.rerun()


# ── 화면 ─────────────────────────────────────────────────────────────────
def render(lock) -> None:
    st.caption("예약 파일을 올리면 Region > Area > Tour 로 나뉩니다. "
               "수량을 넣고, 필요하면 언어·픽업지를 좁히세요.")
    who_input("lm_who")

    c1, c2 = st.columns([3, 2])
    with c1:
        up = st.file_uploader("예약 파일 (오늘 + 내일)", type=["xlsx", "xls", "csv"],
                              key="lm_file")
    with c2:
        st.write("")
        if CACHE.exists() and st.button("마지막 파일 다시 불러오기", width="stretch"):
            meta = {}
            if CACHE_META.exists():
                try:
                    meta = json.loads(CACHE_META.read_text(encoding="utf-8"))
                except Exception:
                    meta = {}
            if _load(CACHE.read_bytes(), meta.get("filename", "last.xlsx")):
                st.rerun()

    if up is not None and st.session_state.get("lm_upname") != up.name + str(up.size):
        raw = up.getvalue()
        if _load(raw, up.name):
            st.session_state["lm_upname"] = up.name + str(up.size)
            try:
                CACHE.write_bytes(raw)
                CACHE_META.write_text(json.dumps({"filename": up.name}, ensure_ascii=False),
                                      encoding="utf-8")
            except Exception:
                pass
            st.rerun()

    panels = st.session_state.get("lm_panels")
    if not panels:
        st.info("예약 파일을 올려주세요.")
        return

    L = st.session_state.get("lm_loaded") or {}
    cat = st.session_state.get("lm_catalog") or {}
    st.success(f"{L.get('filename')} · {L.get('rows')}행 · "
               f"{' / '.join(L.get('dates') or [])}", icon="📄")
    _dropped_note(L)
    _date_choice(L)
    cc1, cc2 = st.columns([5, 1])
    with cc1:
        if cat.get("exists"):
            st.caption(
                f"픽업 후보 = 예약에 나온 것 + GG 가 파는 것 + 같은 지역에서 쓰는 것 "
                f"(GG 기준 {cat.get('tours')}개 투어 · {cat.get('date') or '-'} 수집). "
                "`?` 표시는 GG 에서 확인 안 된 것입니다. 목록에 없으면 칸에 직접 "
                "적으면 됩니다. 예약이 없는 픽업지도 골라야 '홍대 제외' 가 됩니다.")
        else:
            st.warning("GG 픽업 목록이 없습니다. 예약에 나온 것과 같은 지역에서 "
                       "쓰는 픽업지만 보입니다. 없으면 칸에 직접 적으세요.", icon="⚠️")
    with cc2:
        if st.button("픽업 새로고침", width="stretch",
                     help="GG 에서 투어별 픽업지를 다시 수집합니다. 1~2분 걸립니다."):
            d = (st.session_state.get("lm_panels") or [{}])[0].get("date")
            with st.spinner("GG 에서 픽업지 수집 중..."):
                res = lmpickups.refresh(d or "", "KOREA")
            if res.get("ok"):
                st.session_state["lm_catalog"] = lmpickups.info()
                st.success(f"갱신됨 ({res.get('tours')}개 투어). 파일을 다시 불러오면 반영됩니다.")
            else:
                st.error(res.get("error", "수집 실패"))

    # 조용히 되살리면 어제 수량으로 여는 사고가 난다. 반드시 눈에 보이게 알린다.
    _others_note(L)

    n = st.session_state.get("lm_restored") or 0
    if n:
        cA, cB = st.columns([5, 1])
        mine = who() or "이름 없이 작업한 사람"
        cA.info(f"**{mine}** 이(가) 직전에 입력한 수량 {n}건을 되살렸습니다. "
                "다르면 [입력값 지우기]", icon="↩️")
        if cB.button("입력값 지우기", width="stretch"):
            lmentries.clear(L, who())
            st.session_state["lm_entries"] = {}
            st.session_state["lm_restored"] = 0
            for k in [k for k in st.session_state
                      if k.startswith(("q-", "l-", "p-", "c-", "ms-", "md-"))]:
                del st.session_state[k]
            st.rerun()

    latest = next((i for i, p in enumerate(panels) if p["is_latest"]), 0)

    _outsourced_block(latest, panels[latest])
    _quick_fill(latest, panels[latest])
    st.divider()

    for pi, p in enumerate(panels):
        head = "Last Min 오픈" if p["is_latest"] else "Last Min 10시 후 예약"
        if p["is_latest"]:
            head += " — 상품별 수량·언어·픽업 (펼쳐서 확인/수정)"
        # ⚠️ 접어 둔다. 빠른 입력으로 채우고 필요할 때만 펼쳐 본다는 요청이다.
        #    (2026-09-27) 펼친 상태가 기본이면 상품이 많은 날 화면이 끝없이 길어진다.
        with st.expander(f"[투어일자 {p['date_label']}] {head}", expanded=False):
            if not p["is_latest"]:
                st.caption("전날 10시 이후 들어온 예약을 자동 집계한 값입니다. 그대로 두면 됩니다.")
            for g in p["groups"]:
                if p["is_latest"] and g.get("region") == OUTSOURCED_GROUP:
                    continue      # 맨 위에 이미 그렸다 (위젯 키가 겹친다)
                st.markdown(f"**{g['region']}**")
                for a in g["areas"]:
                    st.caption(a["area"])
                    for row in a["rows"]:
                        _tour_row(pi, row, p["is_latest"])

    lmentries.save(L, st.session_state.get("lm_entries") or {}, who())

    st.divider()
    _memo_and_open(latest, lock)


def _with_saved(cands: list, saved) -> list:
    """후보 + 예전에 골라 둔 값. 후보에 없다고 버리면 화면이 죽는다 (_tour_row 설명)."""
    out = list(cands)
    for x in list(saved or []):
        if x not in out:
            out.append(x)
    return out


def _apply_quick(pi: int, rows: list, text: str) -> tuple[int, list[str]]:
    """
    빠른 입력 칸의 글을 그 지역 줄에 넣는다.

    ⚠️ **그 칸이 그 지역의 전부다.** 적지 않은 상품은 0 으로 되돌린다.
       안 그러면 칸을 고쳐 적어도 지운 상품의 예전 수량이 남아서 그대로 열린다.

    ⚠️ 위젯 키(q-/l-/p-)까지 같이 바꿔야 화면에 보인다. 세션에 위젯 값이
       남아 있으면 Streamlit 이 그쪽을 우선하기 때문이다 (_load 의 설명과 같다).
    """
    assign, problems = quickfill.resolve(quickfill.parse(text), rows)
    ent = st.session_state.setdefault("lm_entries", {})
    for row in rows:
        k = _key(pi, row)
        e = ent.setdefault(k, {})
        got = assign.get(row["key"])
        langs_all = list(row.get("languages") or [])
        picks_all = list(row.get("pickups") or [])
        qty = int(got["qty"]) if got else 0
        lang = [x for x in (got["lang"] if got else langs_all) if x in langs_all] or langs_all
        pick = [x for x in (got["pick"] if got else picks_all) if x in picks_all] or picks_all
        if got and not got["pick"]:
            pick = []          # '전부 빼라' 는 지시는 그대로 둔다
        e["qty"], e["lang"], e["pick"] = qty, list(lang), list(pick)
        e.setdefault("ch", dict(row.get("lastmin") or {}))
        e.setdefault("move", {})
        st.session_state[f"q-{k}"] = qty
        if langs_all:
            st.session_state[f"l-{k}"] = list(lang)
        if picks_all:
            st.session_state[f"p-{k}"] = list(pick)
    return len(assign), problems


OUTSOURCED_GROUP = "아웃소싱"


def _outsourced_block(pi: int, panel: dict) -> None:
    """
    예약 파일에 안 나오는 상품 (아웃소싱). 화면 맨 위에 둔다.

    ⚠️ 여기는 **빠른 입력 대상이 아니다.** 예전처럼 수량·언어·픽업을 직접
       고른다 (2026-09-27 요청). 예약이 없어 줄이 안 생기던 상품이라
       빠른 입력 칸에 적으면 '이 지역 목록에 없는 이름' 으로만 나왔다.
    """
    rows = [(a["area"], r) for g in panel["groups"]
            if g.get("region") == OUTSOURCED_GROUP
            for a in g["areas"] for r in a["rows"]]
    if not rows:
        return
    st.markdown(f"**{OUTSOURCED_GROUP}** — 예약에 안 잡히는 상품입니다. "
                "여기는 직접 넣으세요.")
    for area, row in rows:
        st.caption(area)
        _tour_row(pi, row, True)
    st.divider()


def _quick_fill(pi: int, panel: dict) -> None:
    """지역마다 빈칸 하나. 적은 글을 그대로 읽어 아래 수량·옵션을 채운다."""
    st.markdown("**빠른 입력** — 지역 칸에 적으면 아래 수량·언어·픽업이 채워집니다.")
    st.caption("예) `감천미포 4, 경주 4, 교촌경주 10` · `Mt. Fuji Signature 9(중국어불가)` · "
               "`Amanohashidate 22 (한, 영)` · `Yufuin Brewery 10(영어한국어10 중국어0)` · "
               "`알남아 9(홍대제외)` — 쉼표나 줄바꿈으로 여러 개. "
               "**적지 않은 상품은 0 이 됩니다.**")
    applied = st.session_state.setdefault("lm_qf", {})
    notes = st.session_state.setdefault("lm_qf_note", {})

    for g in panel["groups"]:
        if g.get("region") == OUTSOURCED_GROUP:
            continue              # 아웃소싱은 위에서 예전 방식으로 받는다
        for a in g["areas"]:
            area = a["area"]
            c1, c2 = st.columns([1, 6])
            c1.write(f"**{area}**")
            c1.caption(f"{len(a['rows'])}개 상품")
            text = c2.text_area(
                area, key=f"qf-{pi}-{area}", height=68, label_visibility="collapsed",
                placeholder=f"{area} 상품 수량 (예: 경주 4, 교촌경주 10)")
            if area not in applied:
                # 첫 그림에서는 손대지 않는다. 되살린 수량을 지워 버리면 안 된다.
                applied[area] = text
            elif text != applied[area]:
                applied[area] = text
                n, probs = _apply_quick(pi, a["rows"], text)
                notes[area] = {"n": n, "probs": probs}
            note = notes.get(area) or {}
            if note.get("n"):
                c2.caption(f"{note['n']}개 상품에 넣었습니다")
            for msg in note.get("probs") or []:
                c2.warning(msg, icon="⚠️")


def _tour_row(pi: int, row: dict, is_latest: bool) -> None:
    e = _entry(pi, row)
    k = _key(pi, row)
    label = row.get("display") or row["product"]

    if is_latest:
        c = st.columns([4, 1.4, 2.2, 2.6])
        c[0].write(label)
        # ⚠️ 세션에 이미 값이 있으면 value= 를 주지 않는다. 둘 다 주면 Streamlit 이
        #    "default value 와 Session State 를 같이 썼다" 고 경고를 콘솔에 쏟아낸다
        #    (빠른 입력이 수량을 넣을 때마다 줄 수만큼 찍힌다).
        e["qty"] = c[1].number_input(
            "수량", min_value=0, max_value=999, step=1, key=f"q-{k}",
            label_visibility="collapsed",
            **({} if f"q-{k}" in st.session_state else {"value": int(e["qty"])}))
        # ⚠️ 예약에 없는 언어·픽업지도 반드시 고를 수 있어야 한다.
        #    '중국어 불가' 나 '홍대 제외' 는 그 언어·픽업지의 예약이 **없을 때**
        #    하는 지시다. 예약을 기준으로 후보를 만들면 정작 필요한 순간에
        #    목록에 없어서 뺄 수가 없고, 그대로 '전체' 로 나가 열려 버린다.
        #    (2026-08-31: Seasonal BTS 홍대가 목록에 없어 못 뺐고 2자리가 열렸다)
        #    그래도 없는 이름은 직접 적을 수 있게 열어 둔다.
        #    ⚠️ 되살린 값에 후보에 없는 것이 있으면 **화면 전체가 안 열린다.**
        #       직접 적어 넣은 값(accept_new_options)이 그대로 저장되기 때문이다.
        #       실제로 lm_entries 에 언어 'chinese,english' 가 남아 있었고,
        #       그 파일을 다시 불러오는 순간 Last Minute 탭이 통째로 죽었다
        #       (StreamlitAPIException: default value is not part of the options).
        #       지우지 않고 후보에 얹는다 — 사람이 보고 직접 빼면 된다.
        langs = _with_saved(row.get("languages") or [], e["lang"])
        if langs:
            e["lang"] = c[2].multiselect(
                "언어", langs, key=f"l-{k}",
                format_func=lambda x: LANG_LABEL.get(x, x),
                label_visibility="collapsed", placeholder="언어",
                accept_new_options=True,
                **({} if f"l-{k}" in st.session_state
                   else {"default": [x for x in e["lang"] if x in langs]}))
        picks = _with_saved(row.get("pickups") or [], e["pick"])
        known = set(row.get("pickups_known") or [])
        if picks:
            e["pick"] = c[3].multiselect(
                "픽업", picks, key=f"p-{k}",
                **({} if f"p-{k}" in st.session_state
                   else {"default": [x for x in e["pick"] if x in picks]}),
                # GG 에서 확인된 것과 '같은 지역이라 아마 있을 것' 을 구분한다.
                # 없는 픽업지를 골라도 수량이 사라지진 않는다 — gg_open 은
                # 화면에서 실제로 찾은 옵션 개수로 나눈다.
                format_func=lambda x: x if (not known or x in known) else f"{x} ?",
                label_visibility="collapsed", placeholder="픽업지",
                accept_new_options=True)
    else:
        cols = st.columns([4] + [1] * len(C.CHANNELS))
        cols[0].write(label)
        for col, ch in zip(cols[1:], C.CHANNELS):
            e["ch"][ch] = col.number_input(
                ch, min_value=0, max_value=999, step=1,
                value=int((e["ch"] or {}).get(ch) or 0), key=f"c-{k}-{ch}")


def _memo_and_open(latest: int, lock) -> None:
    try:
        payload = _panels_payload()
        office = render_memo(payload, is_op=False)
        op = render_memo(payload, is_op=True)
        plan = build_open_plan(payload[latest]["rows"], is_op=True) if payload else []
    except Exception as e:
        st.error(f"계산 실패: {e}")
        return

    st.subheader("Office / OP 텍스트")
    # ⚠️ st.text_area 에 key 를 주면 처음 값이 세션에 박혀서 수량을 바꿔도 갱신되지 않는다.
    #    st.code 는 항상 현재 값을 그리고, 오른쪽 위에 복사 버튼이 붙는다.
    m1, m2 = st.columns(2)
    with m1:
        st.markdown("**Office 전용**")
        st.code(office, language=None, height=280)
    with m2:
        st.markdown("**OP 전용**")
        st.code(op, language=None, height=280)
    st.caption("오른쪽 위 아이콘으로 복사합니다. "
               "Office 는 '상품명 수량' 만, OP 는 제한 표기와 Klook 언어별 상품명이 들어갑니다. "
               "실제 오픈은 OP 기준입니다.")

    if not plan:
        st.info("수량을 입력하면 오픈 대상이 만들어집니다.")
        return

    st.divider()
    st.subheader("오픈 대상")
    summary = summarize_plan(plan)
    if summary["skipped_count"]:
        st.warning(
            f"자동 오픈 {summary['runnable_count']}건 / 스킵 {summary['skipped_count']}건 — "
            + " · ".join(f"{ch} {len(v)}건" for ch, v in summary["skipped"].items())
            + ". 스킵된 건은 위 메모를 보고 수동으로 열어야 합니다.", icon="⚠️")

    st.dataframe(
        [{"OTA": CHANNEL_LABEL.get(p["channel"], p["channel"]), "지역": p.get("region", ""),
          "상품": p["product"], "수량": p["qty"] if p["mode"] == "qty" else "-",
          "동작": "수량 오픈" if p["mode"] == "qty" else "판매 재개",
          "비고": p.get("note", "")} for p in plan],
        width="stretch", hide_index=True)

    _mapping_check(plan)
    _ota_override(latest)

    st.divider()
    _open_controls(plan, lock)


def _mapping_check(plan: list) -> None:
    """
    실행 전에 '이 상품을 봇이 찾을 수 있는가' 를 본다.

    매핑이 없으면 봇이 그 건을 건너뛰고, 그 자리는 조용히 안 열린 채로 남는다.
    실행하고 나서 로그를 뒤지는 것보다 여기서 미리 보는 편이 낫다.
    """
    if not st.button("오픈 대상 확인 (매핑 점검)", width="stretch"):
        return
    problems = []
    try:
        kl = klook_open.preview(plan, None)
        for u in kl.get("unknown") or []:
            problems.append(("Klook", u.get("text", ""), u.get("memo", "매핑 없음")))
    except Exception as e:
        st.warning(f"Klook 점검 실패: {str(e)[:120]}")
    try:
        for u in (mrt_open.resolve(plan).get("unmapped") or []):
            problems.append(("MyRealTrip", f"{u.get('tour')} {u.get('qty')}", u.get("reason", "")))
    except Exception as e:
        st.warning(f"MRT 점검 실패: {str(e)[:120]}")

    if problems:
        st.error(f"자동 오픈이 안 되는 항목 {len(problems)}건 — 아래는 수동으로 열어야 합니다.",
                 icon="🚫")
        st.dataframe([{"OTA": a, "항목": b, "사유": c} for a, b, c in problems],
                     width="stretch", hide_index=True)
    else:
        st.success("모든 항목이 매핑돼 있습니다.", icon="✅")


def _ota_override(latest: int) -> None:
    """
    배분 규칙이 정한 OTA 를 손으로 바꾼다.

    ⚠️ '원래 OTA' 를 사람이 직접 고르게 하면 안 된다. 그 줄이 지금 어느 OTA 인지
       화면에 없으면 없는 채널을 골라도 아무 일이 안 일어나고, 고장난 것처럼 보인다.
       규칙이 정한 채널을 그대로 보여주고 그 자리에서 바꾸게 한다.
    """
    with st.expander("OTA 수동 변경"):
        # 이동을 뺀 상태로 한 번 계산해서 '규칙이 정한 원래 채널' 을 얻는다.
        base = build_open_plan(_rows_for(latest, with_moves=False), is_op=True)
        pairs = []          # (row_key, 원래채널, 상품명, 수량)
        for p in base:
            if p.get("mode") != "qty" or int(p.get("qty") or 0) <= 0:
                continue
            pairs.append((p.get("row_key") or "", p["channel"], p["product"], p["qty"]))
        if not pairs:
            st.caption("수량이 있는 투어가 없습니다.")
            return

        st.caption("바꾸면 Office/OP 메모와 실제 오픈이 같이 바뀝니다.")
        h = st.columns([3.4, 1.6, 0.8, 1.8, 1.4])
        for col, t in zip(h, ["상품", "규칙", "", "바꿀 OTA", ""]):
            col.caption(t)

        by_row = {}
        panels = st.session_state["lm_panels"]
        for g in panels[latest]["groups"]:
            for a in g["areas"]:
                for row in a["rows"]:
                    by_row[row["key"]] = row

        seen = set()
        for row_key, orig, product, qty in pairs:
            row = by_row.get(row_key)
            if row is None or (row_key, orig) in seen:
                continue
            seen.add((row_key, orig))
            e = _entry(latest, row)
            wkey = f"mv-{latest}-{row_key}-{orig}"
            cur = (e["move"] or {}).get(orig, orig)
            if cur not in C.CHANNELS:
                cur = orig

            c = st.columns([3.4, 1.6, 0.8, 1.8, 1.4])
            c[0].write(f"{product} {qty}")
            c[1].write(CHANNEL_LABEL.get(orig, orig))
            c[2].write("→")
            new = c[3].selectbox("바꿀 OTA", C.CHANNELS, index=C.CHANNELS.index(cur),
                                 key=wkey, label_visibility="collapsed",
                                 format_func=lambda x: CHANNEL_LABEL.get(x, x))
            if new != cur:
                if new == orig:
                    (e["move"] or {}).pop(orig, None)
                else:
                    e["move"][orig] = new
                st.rerun()
            if cur != orig:
                c[4].caption("바뀜")


def _open_controls(plan: list, lock) -> None:
    target = date_picker("오픈 대상 날짜", key="open_date")

    # 수집한 Klook 목록을 팀이 같이 보는 곳에 남긴다.
    # 한 사람이 수집·오픈한 뒤 다른 사람이 특정 상품만 더 열거나 수량을
    # 고쳐야 하는 일이 잦다. 각자 브라우저 안에만 두면 그게 안 보인다.
    # [직접 오픈] 탭의 '수집한 것 불러오기' 가 이걸 읽는다.
    try:
        open_tab.save_collected(target, plan, who=dispatch.current_agent() or "")
    except Exception:
        pass        # 남기기 실패로 오픈을 막지 않는다

    st.write("실행할 OTA")
    cols = st.columns(len(IMPLEMENTED))
    channels = []
    for col, ch in zip(cols, sorted(IMPLEMENTED)):
        n = sum(1 for p in plan if p["channel"] == ch)
        with col:
            if st.checkbox(f"{CHANNEL_LABEL.get(ch, ch)} ({n})", value=n > 0,
                           key=f"oc-{ch}", disabled=n == 0):
                channels.append(ch)

    # ── 지역 (비우면 전체) ────────────────────────────────────────────────
    # 한 지역만 실패했을 때 그 지역만 다시 열 수 있어야 한다.
    # 계획에 지역이 붙어 있지 않은 항목(지역 구분이 없는 OTA)은 늘 포함한다.
    plan_regions = sorted({str(p.get("region") or "") for p in plan
                           if p.get("channel") in channels and p.get("region")})
    regions: list[str] = []
    if plan_regions:
        with st.expander(f"지역 지정 (비우면 전체 {len(plan_regions)}개)"):
            rcols = st.columns(len(plan_regions))
            for col, rg in zip(rcols, plan_regions):
                n = sum(1 for p in plan
                        if p.get("channel") in channels and p.get("region") == rg)
                with col:
                    if st.checkbox(f"{rg} ({n})", value=False, key=f"org-{rg}"):
                        regions.append(rg)

    agent = dispatch.agent_picker("agent_open") if dispatch.is_central() else None
    busy = bool(lock) or dispatch.busy()
    mine = [p for p in plan if p["channel"] in channels
            and (not regions or not p.get("region") or p.get("region") in regions)]
    if regions:
        st.caption(f"선택한 지역: {', '.join(regions)} — {len(mine)}건")
    blocked = busy or not mine or (dispatch.is_central() and not agent)

    def start(dry: bool) -> None:
        title = ("오픈 DRY-RUN " if dry else "오픈 ") + f"{', '.join(channels)} {len(mine)}건"
        skipped = [{"channel": p["channel"],
                    "item": (str(p["product"]) + " " + str(p["qty"] or "")).strip(),
                    "result": "미구현 스킵",
                    "memo": NOT_IMPLEMENTED_REASON.get(p["channel"], "미구현")}
                   for p in plan if p["channel"] not in IMPLEMENTED]
        started, msg = dispatch.start(
            "open", title,
            {"plan": mine, "date": target.isoformat(),
             "channels": channels, "dry_run": dry},
            total=len(mine), agent=agent, pre_results=skipped)
        if started:
            st.rerun()
        else:
            st.error(msg)

    c1, c2 = st.columns(2)
    with c1:
        if st.button("DRY-RUN (입력 없이 대상만)", width="stretch",
                     disabled=blocked, key="btn-open-dry"):
            start(True)
    with c2:
        if st.button("자동 오픈 실행", type="primary", width="stretch",
                     disabled=blocked, key="btn-open-run"):
            st.session_state["confirm_open"] = True

    if st.session_state.get("confirm_open"):
        rows = []
        for p in mine[:14]:
            qty = p["qty"] if p["mode"] == "qty" else "(재개)"
            rows.append("- " + p["channel"] + " " + p["product"] + " " + str(qty))
        if len(mine) > 14:
            rows.append("- ... 외 " + str(len(mine) - 14) + "건")
        head = "**" + target.isoformat() + "** 재고를 실제로 엽니다."
        if agent:
            head += "  (실행 PC: " + agent + ")"
        st.warning(head + "\n\n" + "\n".join(rows), icon="🔓")
        cc1, cc2 = st.columns(2)
        if cc1.button("진행", type="primary", width="stretch", key="co-yes"):
            st.session_state["confirm_open"] = False
            start(False)
        if cc2.button("취소", width="stretch", key="co-no"):
            st.session_state["confirm_open"] = False
            st.rerun()

    snap = dispatch.snapshot("open")
    if snap and (snap.get("logs") or snap.get("results") or snap.get("running")):
        st.divider()
        st.subheader(snap.get("title") or "실행")
        if snap.get("pending"):
            st.info(str(snap.get("agent")) + " 의 Agent 가 가져가기를 기다리는 중...",
                    icon="📨")
        elif snap.get("running"):
            extra = (" · " + str(snap.get("agent"))) if snap.get("agent") \
                    else (" · " + str(snap.get("elapsed", 0)) + "초 경과")
            st.info(str(snap.get("started")) + " 시작" + extra, icon="⏳")
        elif snap.get("error"):
            st.error(snap["error"])
        elif snap.get("summary") is not None:
            st.success("완료")
        if snap.get("stale"):
            st.warning("Agent 에서 오래 소식이 없습니다. 그 PC 를 확인하세요.", icon="⚠️")
        if snap.get("running") and st.button("중단", key="btn-open-stop"):
            dispatch.stop(snap)
            st.rerun()
        render_results(snap)
        with st.expander("로그", expanded=bool(snap.get("running"))):
            render_logs(snap)
        if snap.get("running"):
            time.sleep(3)
            st.rerun()
