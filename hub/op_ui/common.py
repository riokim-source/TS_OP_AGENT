# -*- coding: utf-8 -*-
"""
common.py
Streamlit 페이지들이 같이 쓰는 것들.

⚠️ 업무 로직은 여기에 넣지 않는다. hub/core 를 그대로 가져다 쓴다.
   화면을 Streamlit 으로 바꾸든 무엇으로 바꾸든 업무 규칙은 한 벌만 존재해야 한다.
   (v1.1 개발원칙 1번 / 17번)
"""
from __future__ import annotations

import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import streamlit as st

HUB_DIR = Path(__file__).resolve().parent.parent
if str(HUB_DIR) not in sys.path:
    sys.path.insert(0, str(HUB_DIR))

# 이 프로세스가 만든 작업은 잠금 파일에 'streamlit' 으로 표시된다.
# 기존 콘솔(8600)과 어느 쪽이 돌고 있는지 화면에서 구분하기 위해서다.
os.environ.setdefault("LMHUB_UI", "streamlit")

from core import paths                      # noqa: E402
from core.jobs import MANAGER, read_lock     # noqa: E402
from core.routing import get_routing         # noqa: E402

CHANNEL_LABEL = {"KLOOK": "Klook", "KK": "KKday", "GG": "GetYourGuide",
                 "VI": "Viator", "CP": "Trip.com/Ctrip", "MRT": "MyRealTrip"}


def _token() -> str:
    """
    접속 암호를 정한다. 앞의 것이 있으면 그것을 쓴다.

      1) Streamlit Secrets 의 APP_PASSWORD   ← 중앙 화면(클라우드)
      2) 환경변수 LMHUB_TOKEN
      3) hub/logs/_token.txt                 ← 이 PC 에서만 열 때

    ⚠️ 클라우드에서는 3) 이 쓸모없다. 서버가 다시 뜰 때마다 파일이 사라져
       암호가 매번 바뀌고, 그 값을 아무도 볼 수 없다. 그래서 1) 이 필요하다.
    """
    import secrets
    try:
        v = str(st.secrets.get("APP_PASSWORD") or "").strip()   # type: ignore[attr-defined]
        if v:
            return v
    except Exception:
        pass
    v = str(os.environ.get("LMHUB_TOKEN") or "").strip()
    if v:
        return v

    f = paths.LOG_DIR / "_token.txt"
    try:
        t = f.read_text(encoding="utf-8").strip()
        if t:
            return t
    except Exception:
        pass
    t = secrets.token_urlsafe(12)
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(t, encoding="utf-8")
    except Exception:
        pass
    return t


OFF_WORDS = {"off", "none", "no", "끄기", "없음", "false", "0"}


def _password_off() -> bool:
    """
    암호를 껐는가.

    Secrets 에 APP_PASSWORD = "off" 라고 적으면 묻지 않는다.
    빈 값은 '아직 안 정함' 이지 '끔' 이 아니다 — 설정을 깜빡한 것과
    일부러 끈 것은 결과가 완전히 다르므로 구분한다.

    ⚠️ 끄면 주소를 아는 사람은 누구나 마감·오픈을 누를 수 있다.
       그래서 화면 위에 계속 표시한다 (unlocked_note).
    """
    try:
        v = str(st.secrets.get("APP_PASSWORD") or "").strip().lower()   # type: ignore[attr-defined]
        if v in OFF_WORDS:
            return True
    except Exception:
        pass
    return str(os.environ.get("LMHUB_TOKEN") or "").strip().lower() in OFF_WORDS


def unlocked_note() -> None:
    """암호가 꺼져 있으면 알려 준다. 모르고 열려 있는 상태가 제일 위험하다."""
    if os.environ.get("LMHUB_MODE") == "central" and _password_off():
        st.caption("🔓 접속 암호 꺼짐 — 주소를 아는 사람은 누구나 실행할 수 있습니다")


def _password_is_set() -> bool:
    """중앙 화면에서 암호가 실제로 '정해져' 있는가 (임시 생성값이 아닌가)."""
    try:
        if str(st.secrets.get("APP_PASSWORD") or "").strip():   # type: ignore[attr-defined]
            return True
    except Exception:
        pass
    return bool(str(os.environ.get("LMHUB_TOKEN") or "").strip())


def require_auth() -> None:
    """
    이 PC 에서만 열리는 경우(127.0.0.1 바인딩)에는 토큰을 묻지 않는다.

    각자 자기 PC 에서 자기 Chrome 으로 실행하는 구조라, 이 PC 밖에서
    접근할 수 없으면 토큰이 하는 일이 없다. 매번 링크에 ?k= 를 달고 다니는
    번거로움만 남는다.

    ⚠️ 대신 LAN(0.0.0.0)으로 열 때는 토큰을 다시 요구한다.
       Streamlit 에는 자체 인증이 없고 이 화면은 실제 재고를 여닫는다.
       주소만 아는 사람이 들어와 마감/오픈을 눌러 버리면 막을 방법이 없다.
    """
    if os.environ.get("LMHUB_LOCAL_ONLY") == "1":
        return
    if st.session_state.get("_authed"):
        return

    if _password_off():
        return

    central = os.environ.get("LMHUB_MODE") == "central"
    if central and not _password_is_set():
        # 임시로 만든 암호는 클라우드에서 아무도 알 수 없다. 그대로 두면
        # 아무나 들어오는 화면이 되므로, 열어 주는 대신 설정하라고 말한다.
        st.title("TOURSTORY OP SYSTEM")
        st.error("접속 암호가 설정돼 있지 않습니다.", icon="🔒")
        st.markdown(
            """
관리자가 **Streamlit Cloud → Settings → Secrets** 에 아래 한 줄을 넣어야 합니다.

```toml
APP_PASSWORD = "정할_암호"
```

묻지 않게 하려면 `"off"` 라고 적으세요. 대신 주소를 아는 사람은
누구나 마감·오픈을 실행할 수 있게 됩니다.

```toml
APP_PASSWORD = "off"
```
            """)
        st.stop()

    want = _token()
    if st.query_params.get("k") == want:
        st.session_state["_authed"] = True
        return

    st.title("TOURSTORY OP SYSTEM")
    st.caption("이 화면은 실제 OTA 재고를 여닫습니다.")
    typed = st.text_input("접속 암호", type="password",
                          help="팀에서 정한 공용 암호입니다. 관리자에게 물어보세요.")
    if typed:
        if typed.strip() == want:
            st.session_state["_authed"] = True
            # 주소에 남겨 둔다. 새로고침해도 다시 묻지 않고, 즐겨찾기로 쓸 수 있다.
            try:
                st.query_params["k"] = want
            except Exception:
                pass
            st.rerun()
        else:
            st.error("암호가 맞지 않습니다.")
    st.stop()


def page(title: str, icon: str = "") -> None:
    st.set_page_config(page_title=f"{title} · TOURSTORY OP", page_icon=icon or "🧭",
                       layout="wide")
    require_auth()
    st.title(title)
    unlocked_note()


# ⚠️ 중앙 화면은 Streamlit Cloud 에서 돌고 그 서버는 UTC 다.
#    한국 자정~오전 9시 사이에는 서버가 아직 '어제' 라, 그대로 두면
#    '내일' 이 한국의 '오늘' 로 잡힌다. 마감은 오전 10시 전에 하므로
#    그 구간에 정확히 걸려서 오늘 재고를 닫아 버린다.
#    (2026-08-26 08:5x KST 에 실제로 그렇게 됐다)
#    업무 기준 시각은 언제나 한국이다. 서버가 어디에 있든 상관없어야 한다.
KST = timezone(timedelta(hours=9))


def now_kst() -> datetime:
    """지금(한국 기준). 화면에 보이는 시각은 전부 이것을 쓴다."""
    return datetime.now(KST)


def today() -> date:
    return now_kst().date()


def tomorrow() -> date:
    return today() + timedelta(days=1)


def date_picker(label: str = "대상 날짜", key: str = "target_date") -> date:
    """
    마감/오픈 대상 날짜.

    기본값은 '내일' 이다. 매일 아침 10시 전에 내일 것을 닫는 흐름이라
    그날그날 고르게 하면 잘못 고를 여지만 생긴다.
    """
    d = st.date_input(label, value=st.session_state.get(key, tomorrow()), key=key)
    wd = "월화수목금토일"[d.weekday()]
    delta = (d - today()).days
    when = {0: "오늘", 1: "내일", 2: "모레"}.get(delta, f"{delta:+d}일")
    st.caption(f"{d.isoformat()} ({wd}) · {when}")
    return d


def running_banner() -> dict | None:
    """
    지금 도는 작업이 있으면 알려준다.

    central 에서는 중계 지점을 본다 (이 서버의 잠금은 의미가 없다).
    신호가 끊긴 지 오래인 작업은 '돌고 있다' 고 보지 않고, 대신 정리할
    수 있게 보여준다. 그대로 두면 모든 실행 버튼이 영영 잠긴다.
    (2026-08-26: 오픈이 running 인 채로 남아 41분간 아무것도 못 눌렀다)
    """
    import dispatch
    if dispatch.is_central():
        from core import queue as Q
        try:
            dead = Q.stale()
        except Exception:
            dead = []
        if dead:
            names = ", ".join(str(d.get("title") or d.get("id")) for d in dead[:3])
            st.warning(
                f"멈춘 작업이 있습니다: **{names}** — 실행 도중 그 PC 의 Agent 창이 "
                "닫힌 것으로 보입니다. 결과는 받지 못했습니다.", icon="⚠️")
            if st.button("멈춘 작업 정리", key="btn-drop-stale"):
                got = Q.drop_stale()
                st.success(f"{len(got)}건 정리했습니다.")
                st.rerun()

        for d in (Q.active() or []):
            # 몇 분 걸리는 작업에서는 '실행 중' 만으로는 멈춘 건지 알 수 없다.
            # 마지막으로 한 일과 경과 시간을 같이 보여준다.
            import time as _t
            beat = float(d.get("beat") or 0)
            ago = int(_t.time() - beat) if beat else -1
            head = (f"실행 중: **{d.get('title')}** ({d.get('agent')}, "
                    f"{d.get('created', '')} 시작)")
            if ago >= 0:
                head += f" · 마지막 소식 {ago}초 전"
            st.warning(head + " — 끝나야 다른 작업을 시작할 수 있습니다.", icon="⏳")

            try:
                full = Q.get(str(d.get("id") or ""))
                logs = (full or {}).get("logs") or []
                res = (full or {}).get("results") or []
            except Exception:
                logs, res = [], []
            if logs:
                last = logs[-1]
                st.caption(f"지금: {last.get('t','')} [{last.get('src','')}] "
                           f"{str(last.get('line',''))[:110]}")
            if res:
                bad = [r for r in res
                       if "성공" not in str(r.get("result", ""))
                       and "집계" not in str(r.get("result", ""))]
                st.caption(f"여기까지 {len(res)}건 처리"
                           + (f" · 실패 {len(bad)}건" if bad else " · 실패 없음"))
            with st.expander("진행 로그 보기"):
                if logs:
                    st.code("\n".join(
                        f"{l.get('t','')} [{l.get('src','')}] {l.get('line','')}"
                        for l in logs[-200:]), language=None, height=320)
                else:
                    st.caption("아직 로그가 없습니다.")
            return d
        return None

    lock = read_lock()
    if not lock:
        return None
    where = "이 화면" if lock.get("ui") == os.environ.get("LMHUB_UI") else lock.get("ui")
    st.warning(f"실행 중: **{lock.get('title')}** ({where}, {lock.get('started')} 시작) "
               f"— 끝나야 다른 작업을 시작할 수 있습니다.", icon="⏳")
    return lock


def job_snapshot() -> dict:
    return MANAGER.snapshot(0)


def render_logs(snap: dict, height: int = 380) -> None:
    logs = snap.get("logs") or []
    if not logs:
        st.caption("아직 로그가 없습니다.")
        return
    body = "\n".join(f"{l['t']} [{l['src']}] {l['line']}" for l in logs[-800:])
    st.code(body, language=None, height=height)


def render_results(snap: dict) -> None:
    rows = snap.get("results") or []
    if not rows:
        return
    st.subheader(f"결과 {len(rows)}건")
    fail = [r for r in rows if "성공" not in str(r.get("result", "")) and
            "집계" not in str(r.get("result", ""))]
    if fail:
        st.error(f"실패/스킵 {len(fail)}건 — 아래 표에서 확인하세요", icon="⚠️")
    st.dataframe(
        [{"OTA": r.get("channel", ""), "지역": r.get("region", ""),
          "항목": r.get("item", ""), "결과": r.get("result", ""),
          "메모": r.get("memo", "")} for r in rows],
        width="stretch", hide_index=True)


def chrome_rows(agent: str | None = None) -> list[dict]:
    """
    Chrome 상태를 읽는다.

    local  : 이 PC 를 직접 본다.
    central: 화면은 클라우드에서 돌기 때문에 자기 Chrome 을 봐도 의미가 없다.
             (그대로 두면 팀원 화면에는 늘 '0/5 꺼짐' 으로 보인다)
             그래서 해당 PC 의 Agent 가 심장박동에 실어 보낸 것을 쓴다.
    """
    import dispatch
    if not dispatch.is_central():
        return get_routing().status_all()

    agent = agent or dispatch.current_agent()
    for a in dispatch.agent_rows():
        if a.get("agent") == agent:
            return list(a.get("chrome_rows") or [])
    return []


def chrome_source_note(agent: str | None = None) -> str:
    """이 Chrome 상태가 '어느 PC 의 것' 인지 화면에 밝힌다."""
    import dispatch
    if not dispatch.is_central():
        return "이 PC"
    return (agent or dispatch.current_agent()) or "선택된 PC 없음"


def paths_line() -> str:
    import dispatch
    if dispatch.is_central():
        return "중앙 화면 · 실제 실행은 각자 PC 의 Agent"
    p = paths.describe()
    return f"Klook Open: {p.get('klook_open') or '못 찾음'} · OTA Close: {p.get('ota_close') or '못 찾음'}"
