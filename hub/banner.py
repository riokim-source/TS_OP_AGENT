# -*- coding: utf-8 -*-
"""
banner.py
실행 창에 뜨는 한글 안내를 여기서 찍는다.

왜 .bat 이 아니라 여기인가
    cmd.exe 는 .bat 파일을 '지금 코드페이지'(한국 Windows 는 cp949)로 읽는다.
    파일이 UTF-8 이면 한글이 깨질 뿐 아니라, 깨진 바이트가 줄 구분을 망가뜨려
    `echo` 가 사라지고 그 줄이 명령어로 실행된다.

        '吏'은(는) 내부 또는 외부 명령... 이 아닙니다

    파일을 cp949 로 저장하면 이 PC 에서는 되지만, 영문 Windows 를 쓰는
    사람에게는 또 깨진다. 그래서 .bat 에는 아예 한글을 두지 않고,
    한글은 UTF-8 을 제대로 다루는 Python 이 찍게 한다.

    (2026-08-25: 팀원 PC 에서 'Agent 켜기.bat' 이 이 문제로 실행되지 않았다)

    python hub/banner.py agent
"""
from __future__ import annotations

import sys

LINE = "=" * 62

BANNERS = {
    "agent": [
        "",
        LINE,
        " TOURSTORY OP - Agent",
        LINE,
        "  이 창을 닫으면 이 PC 로 오는 작업이 실행되지 않습니다.",
        "  아침 업무 중에는 열어 두세요.",
        LINE,
        "",
    ],
    "agent-end": [
        "",
        "  Agent 가 종료되었습니다. 창을 닫으셔도 됩니다.",
        "",
    ],
    "hub": [
        "",
        LINE,
        " TOURSTORY OP SYSTEM",
        LINE,
        "   http://localhost:8610",
        "",
        "   이 창을 닫으면 시스템이 꺼집니다. 작업 중에는 열어 두세요.",
        LINE,
        "",
    ],
    "hub-lan": [
        "",
        LINE,
        " TOURSTORY OP SYSTEM  (다른 기기에서도 접속 가능)",
        LINE,
        "   이 PC        http://localhost:8610",
        "   같은 Wi-Fi   http://<이 PC 주소>:8610",
        "",
        "   이 창을 닫으면 시스템이 꺼집니다. 작업 중에는 열어 두세요.",
        LINE,
        "",
    ],
    "hub-already": [
        "",
        "  이미 실행 중입니다. 브라우저를 엽니다.",
        "  http://localhost:8610",
        "",
    ],
    "hub-end": [
        "",
        "  시스템이 종료되었습니다.",
        "",
    ],
    "install-end": [
        "",
        "  설치 창을 닫으셔도 됩니다.",
        "",
    ],
}


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    key = sys.argv[1] if len(sys.argv) > 1 else ""
    for line in BANNERS.get(key, []):
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
