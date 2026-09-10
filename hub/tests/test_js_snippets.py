# -*- coding: utf-8 -*-
"""
봇이 화면에 밀어넣는 JS 조각에 문법 오류가 없는지 검사.

2026-09-10 실제 마감에서 TPC 5개가 통째로 실패했다.

    [Mt. Fuji Highlight] Submit 누름 — 반영 기다리는 중
    [Mt. Fuji Highlight] [messages] Saved            <- 사이트는 저장했다고 답했다
    [Mt. Fuji Highlight] SUBMIT_FAILED — SyntaxError: Invalid or unexpected token

사이트 문제가 아니라 **우리 JS 의 문법 오류**였다. tpc_dom.py 에서
JS 문자열 안에 진짜 줄바꿈이 들어가 있었다.

    .map(w => w.innerText.split('
    ').join(' | '))                  <- 문자열이 줄바꿈으로 끊겼다

파이썬은 이걸 아무 불평 없이 통과시킨다. 브라우저에 가서야 터진다.
그리고 이 줄은 Submit 을 누른 **뒤** 도는 검사라서, 마감은 되고 보고만
실패로 남았다 — 가장 나쁜 종류의 오류다 (무슨 일이 일어났는지 알 수 없다).

여기서는 JS 를 실행하지 않고 따옴표만 센다. 한 줄 안에서 닫히지 않은
따옴표가 있으면 그 문자열은 줄바꿈으로 끊긴 것이다.

    python hub/tests/test_js_snippets.py
"""
import re
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[2]
FILES = sorted(list((ROOT / "OTA Close").rglob("*.py"))
               + list((ROOT / "Klook Open").glob("*.py"))
               + list((ROOT / "hub" / "core").rglob("*.py")))

bad = []
checked = 0

# 파이썬 안의 여러 줄 문자열 (JS 를 담는 그릇)
BLOCK = re.compile(r'(?P<pre>[rR]?)(?P<q>"""|\'\'\')(?P<body>.*?)(?P=q)', re.S)
# JS 인 것 같은가 (한글 설명용 여러 줄 문자열까지 보지 않도록)
JSISH = re.compile(r"=>|querySelector|document\.|function\s*\(|\breturn\b")


# JS 정규식 리터럴. 그 안의 따옴표는 문자열이 아니다 (/[&<>"]/g 처럼).
#
# ⚠️ 이 줄을 쉘 heredoc 으로 쓰다가 \n 이 진짜 줄바꿈으로 바뀌어, 이 검사가
#    잡으려는 바로 그 버그를 이 파일이 갖게 됐다. 백슬래시가 든 줄은
#    편집 도구로 직접 쓴다 ([[windows-bat-crlf]] 와 같은 함정).
RE_LIT = re.compile(
    r"(?<=[(,=:\s!&|])/(?:\\.|\[[^\]\n]*\]|[^/\n\\])+/[gimsuy]*")


def unclosed_quote(line: str) -> str:
    """그 줄에서 닫히지 않은 따옴표를 찾는다. 없으면 빈 문자열."""
    line = RE_LIT.sub("RE", line)          # 정규식은 통째로 치워 놓고 본다
    i = 0
    n = len(line)
    while i < n:
        c = line[i]
        if c == "\\":
            i += 2
            continue
        if c == "/" and i + 1 < n and line[i + 1] == "/":
            return ""                       # 줄 주석 — 나머지는 안 본다
        if c in "'\"`":
            if c == "`":
                return ""                   # 백틱은 여러 줄이 정상이다
            j = i + 1
            while j < n:
                if line[j] == "\\":
                    j += 2
                    continue
                if line[j] == c:
                    break
                j += 1
            else:
                return c
            if j >= n:
                return c
            i = j + 1
            continue
        i += 1
    return ""


for f in FILES:
    src = f.read_text(encoding="utf-8", errors="replace")
    for m in BLOCK.finditer(src):
        body = m.group("body")
        if not JSISH.search(body):
            continue
        checked += 1
        start_line = src[:m.start()].count("\n") + 1
        for k, line in enumerate(body.split("\n")):
            q = unclosed_quote(line)
            if q:
                where = f"{f.relative_to(ROOT)}:{start_line + k + 1}"
                print(f"  !! {where}")
                print(f"       {line.strip()[:90]}")
                print(f"       -> {q} 따옴표가 그 줄에서 안 닫혔다 "
                      f"(JS 문자열이 줄바꿈으로 끊김)")
                bad.append(where)

print()
print(f"  JS 조각 {checked}개 확인")
if bad:
    print()
    for b in bad:
        print("  !!", b)
    raise SystemExit(f"!! {len(bad)}곳에서 JS 문자열이 끊겼다 — 브라우저에서 SyntaxError 가 난다")
print("전부 통과 — 끊긴 JS 문자열이 없다")
