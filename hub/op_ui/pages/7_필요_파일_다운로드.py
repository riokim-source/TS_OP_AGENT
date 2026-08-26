# -*- coding: utf-8 -*-
"""
필요 파일 다운로드.

팀원이 자기 PC 에 Agent 를 설치할 때 필요한 것을 여기서 받는다.
USB 나 사내 공유 폴더를 거치지 않아도 되게 한다.

무엇을 담을지는 hub/make_share.py 가 정한다. 목록을 여기에도 적으면
두 곳이 어긋나므로, 그 파일의 build_manifest() 를 그대로 쓴다.
"""
from __future__ import annotations

import json

import streamlit as st

from common import page, running_banner          # noqa: F401  (경로 설정 포함)

# ⚠️ 여기는 '설치가 막혔을 때 보는 화면' 이다. 이 페이지가 통째로 죽으면
#    팀원은 어디서 파일을 받는지조차 알 수 없다. 그래서 make_share 를
#    이름으로 끌어오지 않고, 없으면 없는 대로 넘어간다.
#
#    (2026-08-25: 배포 직후 Streamlit 이 옛 make_share 를 붙잡고 있어
#     ImportError 로 페이지 전체가 뜨지 않았다. 재시작하면 풀리지만,
#     그때도 주소만은 보여야 한다.)
FALLBACK_REPO = "https://github.com/riokim-source/TS_OP_AGENT"
try:
    import make_share
except Exception:                                    # pragma: no cover
    make_share = None


def _repo() -> str:
    fn = getattr(make_share, "repo_url", None)
    try:
        return fn() if fn else FALLBACK_REPO
    except Exception:
        return FALLBACK_REPO


def _zip() -> str:
    fn = getattr(make_share, "zip_url", None)
    try:
        return fn() if fn else f"{_repo()}/archive/refs/heads/main.zip"
    except Exception:
        return f"{_repo()}/archive/refs/heads/main.zip"


KEY_REL = getattr(make_share, "KEY_REL",
                  "hub/data/firebase_service_account.json")

page("필요 파일 다운로드", "📥")

st.caption("자기 PC 에 Agent 를 설치할 때 필요한 것들입니다. "
           "화면은 설치할 게 없습니다 — 이 주소를 즐겨찾기에 넣으면 됩니다.")


# ── 1) 설치 묶음 ──────────────────────────────────────────────────────────
st.subheader("1. 설치 묶음")

REPO = _repo()
ZIP = _zip()

st.markdown(f"""
설치용 파일은 저장소에 있습니다. 아래 **파일 받기** 를 누르면 압축 파일이
바로 내려받아집니다. Git 을 몰라도 됩니다.

[⬇ **파일 받기 (ZIP)**]({ZIP}) &nbsp;&nbsp;·&nbsp;&nbsp; [저장소 열어보기]({REPO})
""")
st.caption("받은 압축을 풀면 폴더가 하나 나옵니다. 원하는 곳에 두세요 (바탕화면 추천).")

with st.expander("무엇이 들어 있나"):
    try:
        items = make_share.build_manifest(with_key=False) if make_share else []
    except Exception as e:
        st.error(f"목록을 읽지 못했습니다: {e}")
        items = []
    groups: dict[str, list[str]] = {}
    for rel, why in items:
        groups.setdefault(why, []).append(rel)
    st.caption(f"파일 {len(items)}개 — 봇 코드, 화면, 맵핑, 실행 파일. "
               "실행 로그와 예약 엑셀은 들어 있지 않습니다.")
    st.dataframe(
        [{"쓰임": why, "파일 수": len(v), "예": v[0] + ("" if len(v) == 1 else " 외")}
         for why, v in groups.items()],
        width="stretch", hide_index=True)

with st.expander("저장소가 안 열릴 때 (여기서 바로 받기)"):
    st.caption("사내망에서 GitHub 이 막혀 있으면 이쪽을 쓰세요. 내용은 같습니다.")

    @st.cache_data(ttl=600, show_spinner="묶음을 만드는 중...")
    def _bundle() -> bytes:
        return make_share.make_zip_bytes(with_key=False)

    try:
        if make_share is None:
            raise RuntimeError("설치 목록을 읽지 못했습니다. 위 링크로 받으세요.")
        blob = _bundle()
        st.download_button(f"⬇  여기서 받기 ({len(blob)/1024/1024:.1f} MB)",
                           data=blob, file_name="TOURSTORY_OP_설치용.zip",
                           mime="application/zip")
    except Exception as e:
        st.error(f"묶음을 만들지 못했습니다: {e}")

st.divider()

# ── 2) 중계 열쇠 ──────────────────────────────────────────────────────────
st.subheader("2. 중계 열쇠")
st.caption("이 파일이 있어야 내 PC 의 Agent 가 중앙 화면과 연결됩니다. "
           "묶음에는 일부러 넣지 않았습니다.")

st.warning(
    "이 파일 하나면 아무 PC 나 우리 시스템에 붙을 수 있습니다. "
    "**메일·메신저·카톡으로 보내지 마세요.** 받은 뒤에는 "
    "`hub/data/firebase_service_account.json` 에 두고 그대로 두세요.",
    icon="🔑")

try:
    blob_key = dict(st.secrets.get("firebase") or {})      # type: ignore[attr-defined]
except Exception:
    blob_key = {}
if not blob_key:
    from core.paths import DATA_DIR
    f = DATA_DIR / "firebase_service_account.json"
    if f.exists():
        try:
            blob_key = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            blob_key = {}

# 중계 주소가 없으면 열쇠가 있어도 붙지 못한다. 같은 파일에 넣어 준다.
# (주소는 비밀이 아니다. 접근하려면 어차피 열쇠가 있어야 한다)
if blob_key and not blob_key.get("database_url"):
    try:
        from core.queue_firebase import database_url
        u = database_url()
        if u:
            blob_key["database_url"] = u
    except Exception:
        pass

if not blob_key:
    st.info("열쇠를 찾지 못했습니다. 관리자에게 직접 요청하세요.", icon="ℹ️")
elif not blob_key.get("database_url"):
    st.error("중계 주소(database_url)를 찾지 못했습니다. 이대로 받으면 "
             "Agent 가 붙지 못합니다. 관리자에게 알려 주세요.", icon="⚠️")
else:
    key_txt = json.dumps(blob_key, ensure_ascii=False, indent=1)
    st.caption(f"프로젝트: `{blob_key.get('project_id', '?')}`")
    if st.checkbox("위 내용을 읽었고, 이 PC 에서 쓰려고 받습니다"):
        st.download_button(
            "⬇  열쇠 받기",
            data=key_txt.encode("utf-8"),
            file_name="firebase_service_account.json",
            mime="application/json",
        )
        st.caption(f"받은 파일을 `{KEY_REL}` 위치에 두세요.")

st.divider()

# ── 3) 설치 순서 ──────────────────────────────────────────────────────────
st.subheader("3. 설치 순서")
st.markdown("""
| | 할 일 |
|---|---|
| **1** | [Python](https://www.python.org/downloads/) 설치 — **"Add python.exe to PATH"** 반드시 체크 |
| **2** | 위 **파일 받기** 로 압축을 받아 풀고, 원하는 곳에 둡니다 (바탕화면 추천) |
| **3** | 열쇠를 받아 `hub/data/firebase_service_account.json` 에 둡니다 |
| **4** | **`설치.bat`** 더블클릭 — 부족한 것을 알아서 채웁니다 |
| **5** | **`Agent 켜기.bat`** 더블클릭 — 이 창은 업무 중에 닫지 마세요 |
| **6** | 이 화면으로 돌아와 **Chrome 로그인** 에서 프로필을 켜고 OTA 에 로그인 |

한 번 해 두면, 다음부터 아침에 할 일은 **`Agent 켜기.bat` 하나**입니다.
""")

with st.expander("안 될 때"):
    st.dataframe(
        [{"증상": "연결된 PC 가 없습니다", "해결": "Agent 켜기.bat 을 실행하세요"},
         {"증상": "Agent 창이 바로 닫힘", "해결": "Python 이 없거나 PATH 미체크 — 다시 설치"},
         {"증상": "Firebase 서비스 계정이 없습니다", "해결": "위 2번 열쇠를 받아 그 경로에 두세요"},
         {"증상": "이미 Agent 가 돌고 있습니다", "해결": "정상입니다. 새 창을 닫고 원래 창을 쓰세요"},
         {"증상": "실행 중 0/5", "해결": "Chrome 로그인 에서 프로필을 실행하세요"}],
        width="stretch", hide_index=True)
