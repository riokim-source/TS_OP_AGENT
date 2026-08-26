# -*- coding: utf-8 -*-
"""
main.py
Klook Merchant Center 익일 Inventory Open 실행 진입점.

기존 open.py + open_new.py 통합. 매핑은 packages.py 단일 source.
1회 실행 후 종료. (운영 패턴: 매일 수동 트리거)

사용법:
  1) Korea Chrome:     remote-debugging-port=9522 로 Klook Merchant Center 로그인
  2) Japan Chrome:     remote-debugging-port=9523 로 Klook Merchant Center 로그인
  3) Australia Chrome: remote-debugging-port=9524 로 Klook Merchant Center 로그인 (지사 추가 시)
  4) UK Chrome:        remote-debugging-port=9525 로 Klook Merchant Center 로그인 (지사 추가 시)
  5) python main.py
  6) 상품명 수량 입력 후 Ctrl+Z → Enter
  - 실제로 작업이 있는 region 의 worker 만 실행되므로, 모든 Chrome 을 띄울 필요는 없음.

입력 형식 (한 줄 또는 여러 줄, 쉼표/줄바꿈 구분):
  남쁘 5, Osaka Kobe 1, TOYAKO NISEKO 2

로그:
  - logs/booking_log_KLOOK_korea.txt      (AutoBots 호환 텍스트 로그)
  - logs/booking_log_KLOOK_japan.txt
  - logs/booking_log_KLOOK_australia.txt  (지사 운영 시)
  - logs/booking_log_KLOOK_uk.txt         (지사 운영 시)
  (xlsx / json 결과 로그는 만들지 않음. worker 결과는 stdout '##RESULT##' 마커로 main 이 직접 수집.)
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from packages import get_package


BASE_DIR = Path(__file__).resolve().parent
WORKER = BASE_DIR / "klook_worker.py"
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

# ⚠️ Klook 은 '지역 Chrome'(9522~9525) 을 OTA Close 의 KKday/GG/Ctrip 과 함께 쓴다.
#   예전에는 Klook 만 %LOCALAPPDATA%\KlookBot\chrome_* 프로필을 같은 포트에 물려서,
#   먼저 뜬 쪽이 포트를 잡으면 나머지는 로그인 안 된 Chrome 에 붙어 전부 실패했다.
#   지역 Chrome 은 이미 지역별로 나뉘어 있고 Klook 계정도 지역별이라 그냥 합치면 된다.
#   실제 값은 hub/data/routing.json 이 단일 기준이며, klook_core 가 그 값으로 덮어쓴다.
KOREA_CDP_URL     = os.environ.get("KLOOK_KOREA_CDP_URL",     "http://localhost:9522")
JAPAN_CDP_URL     = os.environ.get("KLOOK_JAPAN_CDP_URL",     "http://localhost:9523")
AUSTRALIA_CDP_URL = os.environ.get("KLOOK_AUSTRALIA_CDP_URL", "http://localhost:9524")
UK_CDP_URL        = os.environ.get("KLOOK_UK_CDP_URL",        "http://localhost:9525")

# 4개 region 의 (region key, CDP URL) 매핑. 작업 분류 및 worker 실행 시 사용.
REGION_CDP_URLS: dict[str, str] = {
    'KOREA':     KOREA_CDP_URL,
    'JAPAN':     JAPAN_CDP_URL,
    'AUSTRALIA': AUSTRALIA_CDP_URL,
    'UK':        UK_CDP_URL,
}



# ──────────────────────────────────────────────────────────────────────────────
# 텍스트 로그 (AutoBots 호환 포맷)
# ──────────────────────────────────────────────────────────────────────────────
# 형식: 'YYYY-MM-DD HH:MM | 상품명 수량 | STATUS [| 메모]'
# 상태:
#   SUCCESS              - 정상 처리
#   FAILED               - Package 워크플로우 실패
#   NEW_VERSION_FAILED   - Activity(새버전) 워크플로우 실패
#   NOT_FOUND            - packages.py 에 없는 상품명 / 형식 오류

STATUS_MAP = {
    "성공":      "SUCCESS",
    "실패":      "FAILED",
    "새버전 실패": "NEW_VERSION_FAILED",
    "찾을 수 없음": "NOT_FOUND",
}


def write_log(filepath: Path, item_text: str, status: str, memo: str = ""):
    """AutoBots 형식 텍스트 로그 한 줄 기록."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    if memo:
        line = f"{now} | {item_text} | {status} | {memo}\n"
    else:
        line = f"{now} | {item_text} | {status}\n"
    with open(filepath, "a", encoding="utf-8") as f:
        f.write(line)


def status_to_en(result_kr: str, workflow: str) -> str:
    """worker 의 한글 result 를 영문 status 로 변환."""
    # Activity 워크플로우의 '실패'는 '새버전 실패'로 분류
    if workflow == "activity" and result_kr == "실패":
        return "NEW_VERSION_FAILED"
    return STATUS_MAP.get(result_kr, "FAILED")


# ──────────────────────────────────────────────────────────────────────────────
# 입력 파싱
# ──────────────────────────────────────────────────────────────────────────────

SUPPORTED_REGIONS = ('KOREA', 'JAPAN', 'AUSTRALIA', 'UK')


def _split_items_with_site(text: str):
    """
    쉼표/줄바꿈으로 분리. [korea]/[japan]/[australia]/[uk] 라벨이 있으면 그 라벨을 우선 적용.
    """
    current_site = None
    for part in re.split(r",|\n", text or ""):
        raw = part.strip()
        if not raw:
            continue
        m = re.match(r"^\[?\s*(korea|japan|australia|uk)\s*\]?\s*:?\s*(.*)$", raw, flags=re.I)
        if m:
            current_site = m.group(1).upper()
            raw = m.group(2).strip()
            if not raw:
                continue
        yield raw, current_site


def _infer_site_for_unknown(raw: str, explicit_site: str | None = None) -> str:
    """
    매핑 실패 항목의 사이트 추정 (요약 출력용).
    명시 라벨이 있으면 그것을 따르고, 아니면 한글 포함 → KOREA, 그 외 → JAPAN 으로 기본 추정.
    AUSTRALIA / UK 는 명시 라벨이 없으면 자동 추정할 단서가 없어서 JAPAN 으로 떨어질 수 있음.
    이는 '찾을 수 없음' 요약에만 영향을 주고 실제 worker 실행에는 영향 없음.
    """
    if explicit_site in SUPPORTED_REGIONS:
        return explicit_site
    if re.search(r"[가-힣]", raw):
        return "KOREA"
    return "JAPAN"


def parse_tasks(text: str):
    """
    입력 텍스트를 파싱해서 region 별 task dict 와 매핑 실패 리스트를 만든다.
    workflow 는 packages.py 가 자동 판정.

    리턴:
        region_tasks: {'KOREA': [...], 'JAPAN': [...], 'AUSTRALIA': [...], 'UK': [...]}
        unknown:     [{...매핑 실패 항목...}]
    """
    text = (text or "").strip()
    region_tasks: dict[str, list[dict]] = {r: [] for r in SUPPORTED_REGIONS}
    unknown: list[dict] = []

    for raw, explicit_site in _split_items_with_site(text):
        m = re.match(r"^(?P<name>.+?)\s+(?P<qty>\d+)\s*$", raw)
        if not m:
            site_guess = _infer_site_for_unknown(raw, explicit_site)
            unknown.append({
                "site": site_guess,
                "name": raw,
                "input_text": raw,
                "inventory": "",
                "result": "찾을 수 없음",
                "memo": "형식 인식 실패",
            })
            print(f"[주의] 형식 인식 실패: {raw}")
            continue

        input_name = m.group("name").strip()
        qty = int(m.group("qty"))
        input_text = f"{input_name} {qty}"

        info = get_package(input_name)
        if not info:
            # 명시 site 라벨이 있어도 packages.py 에 없으면 not found
            site_guess = _infer_site_for_unknown(input_text, explicit_site)
            unknown.append({
                "site": site_guess,
                "name": input_name,
                "input_text": input_text,
                "inventory": qty,
                "workflow": "package",
                "result": "찾을 수 없음",
                "memo": "packages.py 매핑 없음",
            })
            print(f"[주의] 매핑 없음, 실행 안 함: {input_name}")
            continue

        # explicit_site 가 있고 packages.py 의 region 과 다르면 경고만 출력하고 packages.py 따름
        if explicit_site in SUPPORTED_REGIONS and explicit_site != info["region"]:
            print(f"[주의] 라벨 [{explicit_site}] 과 매핑 region [{info['region']}] 불일치: {input_name} → packages.py 기준 사용")

        region = info["region"]
        if region not in region_tasks:
            # packages.py 에 정의됐지만 지원 region 에 없는 케이스 (안전망)
            print(f"[주의] 미지원 region: {region} ({input_name}). 작업 건너뜀.")
            continue

        task = {
            "name": info["canonical_name"],
            "package_id": info["id"],
            "inventory": qty,
            "workflow": info["workflow"],
            "input_text": input_text,
            "accept_until": info["accept_until"],  # packages.py 가 기본값 처리까지 해줌
        }
        region_tasks[region].append(task)

    return region_tasks, unknown


# ──────────────────────────────────────────────────────────────────────────────
# 입력 읽기
# ──────────────────────────────────────────────────────────────────────────────

def _multi_lang_variant_names():
    """packages.py 에서 (한)/(중)/(일) 접미사가 붙은 상품명 자동 추출.

    리턴: dict {기본명: [변형명1, 변형명2, ...]}
    예: {'Fuji Highlight': ['Fuji Highlight(중)']}
    """
    from packages import PACKAGES
    import re as _re
    groups: dict[str, list[str]] = {}
    suffix_re = _re.compile(r'^(.+?)\((한|중|일|영|KR|CN|JP|ko|zh|ja)\)\s*$')
    for name in PACKAGES.keys():
        m = suffix_re.match(name)
        if m:
            base = m.group(1).strip()
            groups.setdefault(base, []).append(name)
    return groups


def _prompt_target_date() -> str | None:
    """실행 시 날짜 선택 프롬프트.
    반환:
      - None : 기본(내일)
      - "YYYY-MM-DD" : 특정 날짜
    """
    from datetime import datetime as _dt, timedelta as _td
    print()
    print("[날짜 선택] 어느 날짜를 열까요?")
    print("  1) 내일 (기본)")
    print("  2) 특정 날짜")
    while True:
        choice = input("선택 (1/2, Enter 는 1): ").strip()
        if choice == '' or choice == '1':
            tomorrow = _dt.today() + _td(days=1)
            print(f"[안내] 익일 자동: {tomorrow.strftime('%Y-%m-%d')}")
            return None
        if choice == '2':
            break
        print("  · 1 또는 2 입력하세요.")

    today = _dt.today()
    while True:
        raw = input("날짜 입력 (MM/DD 또는 YYYY-MM-DD): ").strip()
        if not raw:
            print("  · 날짜 입력 필요.")
            continue
        # YYYY-MM-DD
        import re as _re
        m = _re.match(r'^(\d{4})[-/](\d{1,2})[-/](\d{1,2})$', raw)
        if m:
            try:
                dt = _dt(int(m.group(1)), int(m.group(2)), int(m.group(3)))
                print(f"[안내] 특정 날짜: {dt.strftime('%Y-%m-%d')} ({['월','화','수','목','금','토','일'][dt.weekday()]}요일)")
                return dt.strftime('%Y-%m-%d')
            except Exception as e:
                print(f"  · 잘못된 날짜: {e}")
                continue
        # MM/DD 또는 MM-DD
        m = _re.match(r'^(\d{1,2})[-/](\d{1,2})$', raw)
        if m:
            try:
                mm = int(m.group(1)); dd = int(m.group(2))
                dt = _dt(today.year, mm, dd)
                if dt.date() < today.date():
                    dt = _dt(today.year + 1, mm, dd)
                print(f"[안내] 특정 날짜: {dt.strftime('%Y-%m-%d')} ({['월','화','수','목','금','토','일'][dt.weekday()]}요일)")
                return dt.strftime('%Y-%m-%d')
            except Exception as e:
                print(f"  · 잘못된 날짜: {e}")
                continue
        print("  · 형식 오류. MM/DD (예: 6/15) 또는 YYYY-MM-DD (예: 2026-06-15)")


def read_input() -> tuple[str, str | None]:
    """
    반환: (tasks_text, target_date_str_or_None)
      - target_date_str_or_None: None 이면 익일 자동, 아니면 "YYYY-MM-DD"
    """
    # 1) 날짜 선택 (내일 vs 특정 날짜)
    target_date = _prompt_target_date()

    # 2) 투어 입력
    print()
    if target_date:
        print(f"[입력] Klook Inventory Open 작업 목록 (대상 날짜: {target_date})")
    else:
        print("[입력] Klook 익일 Inventory Open 작업 목록을 붙여넣으세요.")
    print("[형식] 상품명 수량, 상품명 수량")
    print("[안내] packages.py 매핑으로 region (Korea/Japan/Australia/UK) 자동 분류")
    print("[안내] 수량 0 입력 시 → Inventory 0 + Activate OFF (마감)")
    print()
    print("예시) 남쁘 5, Kumamoto Takachiho(한) 4, Toyako Niseko 2")
    print()

    # 다국어 변형 상품 자동 표시
    try:
        variants = _multi_lang_variant_names()
        if variants:
            print("[다국어 변형 상품] (괄호 안 언어 코드로 해당 언어 SKU 선택)")
            for base, vlist in sorted(variants.items()):
                tags = ' / '.join(sorted(vlist))
                print(f"  · {base:30s} → {tags}")
            print()
    except Exception as _e:
        # packages.py 로드 실패 시 안내만 생략하고 계속 진행
        pass

    print("[완료] Windows CMD: Ctrl+Z → Enter\n")
    data = sys.stdin.read().strip()
    return data, target_date


# ──────────────────────────────────────────────────────────────────────────────
# Worker 실행
# ──────────────────────────────────────────────────────────────────────────────

def write_tasks_file(site: str, tasks: list[dict]) -> Path:
    """
    worker 가 읽을 task JSON 을 임시 파일로 작성.
    실행 종료 후 정리는 호출자가 담당.
    """
    path = LOG_DIR / f"_tmp_klook_tasks_{site.lower()}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(tasks, f, ensure_ascii=False, indent=2)
    return path


def run_worker(site: str, cdp_url: str, tasks: list[dict]):
    if not tasks:
        print(f"[{site}] 작업 항목 없음")
        return None
    if not WORKER.exists():
        raise FileNotFoundError(f"worker 파일을 찾을 수 없음: {WORKER}")

    tasks_file = write_tasks_file(site, tasks)

    cmd = [
        sys.executable, str(WORKER),
        "--site", site,
        "--cdp-url", cdp_url,
        "--tasks-file", str(tasks_file),
        "--suppress-summary",
    ]
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONWARNINGS"] = "ignore::SyntaxWarning"

    proc = subprocess.Popen(
        cmd,
        cwd=str(BASE_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    return {
        "site": site,
        "proc": proc,
        "tasks_file": tasks_file,  # 종료 후 삭제용
        "tasks": tasks,
        "results": [],   # stream_output 이 ##RESULT## 마커를 발견할 때마다 누적
    }


# worker 가 각 task 완료 시 출력하는 한 줄짜리 결과 마커
RESULT_MARKER = "##RESULT##"


def stream_output(prefix: str, proc: subprocess.Popen, results_buf: list):
    """
    worker stdout 을 한 줄씩 읽어서:
      - ##RESULT## 마커가 있는 줄 → results_buf 에 파싱해서 누적 (콘솔 출력 생략)
      - 그 외 → 그냥 콘솔에 prefix 붙여 출력
    """
    assert proc.stdout is not None
    for line in proc.stdout:
        stripped = line.rstrip()
        if stripped.startswith(RESULT_MARKER):
            payload = stripped[len(RESULT_MARKER):].strip()
            try:
                result = json.loads(payload)
                results_buf.append(result)
            except Exception as e:
                # 파싱 실패해도 운영은 계속. 콘솔에 경고만 남김.
                print(f"[{prefix}] [주의] 결과 마커 파싱 실패: {e} / line={stripped[:120]}")
            continue
        print(f"[{prefix}] {stripped}")


# ──────────────────────────────────────────────────────────────────────────────
# 결과 처리 및 출력
# ──────────────────────────────────────────────────────────────────────────────

def item_text_of(row: dict) -> str:
    raw = str(row.get("input_text", "")).strip()
    if raw:
        return raw
    name = str(row.get("name", "")).strip()
    inv = str(row.get("inventory", "")).strip()
    if name and inv:
        return f"{name} {inv}"
    return name or str(row.get("package_id_or_search_key", "")).strip() or "확인 필요"


def print_site_summary(site: str, rows: list[dict]):
    """지사별 결과 요약. 작업이 하나도 없으면 출력 생략. 있는 카테고리만 표시."""
    if not rows:
        return  # 해당 지사에 작업 자체가 없으면 출력 안 함

    labels = ["성공", "실패", "찾을 수 없음", "새버전 실패"]
    groups = {label: [] for label in labels}
    for row in rows:
        result = str(row.get("result", "")).strip()
        workflow = str(row.get("workflow", "")).strip().lower()
        if workflow == "activity" and result == "실패":
            result = "새버전 실패"
        if result not in groups:
            result = "실패"
        groups[result].append(row)

    print(f"\n[{site.title()} 결과]")
    for label in labels:
        items = [item_text_of(row) for row in groups[label]]
        print(f"{label}({len(items)}): {', '.join(items) if items else '없음'}")


def write_text_logs(site: str, rows: list[dict]):
    """AutoBots 호환 텍스트 로그 기록 (재시도 대상 판별의 1차 source)."""
    log_path = LOG_DIR / f"booking_log_KLOOK_{site.lower()}.txt"
    for row in rows:
        item = item_text_of(row)
        result = str(row.get("result", "")).strip()
        workflow = str(row.get("workflow", "")).strip().lower()
        status = status_to_en(result, workflow)
        memo = str(row.get("memo", "")).strip()
        # 메모가 너무 긴 경우 잘라서 한 줄로 유지
        if len(memo) > 200:
            memo = memo[:200] + "..."
        write_log(log_path, item, status, memo)


def format_duration(seconds: float) -> str:
    s = int(round(seconds))
    return f"{s // 3600:02d}:{(s % 3600) // 60:02d}:{s % 60:02d}"


# ──────────────────────────────────────────────────────────────────────────────
# 메인
# ──────────────────────────────────────────────────────────────────────────────

def main():
    text, target_date = read_input()
    region_tasks, unknown_records = parse_tasks(text)

    # 특정 날짜 지정 시 모든 task 에 target_date 필드 추가
    if target_date:
        for region in SUPPORTED_REGIONS:
            for t in region_tasks.get(region, []):
                t['target_date'] = target_date

    # region 표기용 (콘솔 출력 시 사람이 읽기 좋게)
    region_display = {
        'KOREA':     'Korea',
        'JAPAN':     'Japan',
        'AUSTRALIA': 'Australia',
        'UK':        'UK',
    }

    print("\n[작업 목록 - packages.py 매핑 자동 분류]")
    for region in SUPPORTED_REGIONS:
        tasks = region_tasks.get(region, [])
        print(f"- {region_display[region]}: {len(tasks)}개")
        for t in tasks:
            print(f"  · {t['name']} / {t['workflow']} / ID {t['package_id']} / Inv {t['inventory']}")

    if unknown_records:
        print("\n[주의] 아래 항목은 실행하지 않고 '찾을 수 없음'에 반영")
        for item in unknown_records:
            print(f"  - {item.get('input_text')}: {item.get('memo')}")

    has_any_task = any(region_tasks.get(r) for r in SUPPORTED_REGIONS)
    if not has_any_task and not unknown_records:
        print("\n[완료] 실행할 작업이 없습니다.")
        return

    started = time.perf_counter()

    print(f"\n[실행 설정]")
    for region in SUPPORTED_REGIONS:
        print(f"- {region_display[region]} CDP: {REGION_CDP_URLS[region]}")
    print(f"- Log dir: {LOG_DIR}")

    # 병렬로 worker 실행 (작업이 있는 region 만)
    runners = []
    for region in SUPPORTED_REGIONS:
        tasks = region_tasks.get(region, [])
        if not tasks:
            continue
        r = run_worker(region, REGION_CDP_URLS[region], tasks)
        if r:
            runners.append(r)

    threads = []
    for r in runners:
        th = threading.Thread(
            target=stream_output,
            args=(r["site"], r["proc"], r["results"]),
            daemon=True,
        )
        th.start()
        threads.append((th, r))

    # 결과 수집
    failed = False
    site_rows: dict[str, list[dict]] = {r: [] for r in SUPPORTED_REGIONS}
    for record in unknown_records:
        site = record.get("site", "KOREA")
        if site not in site_rows:
            site = "KOREA"
        site_rows[site].append(record)

    for th, r in threads:
        code = r["proc"].wait()
        th.join(timeout=2)
        loaded = list(r["results"])  # stream_output 이 누적한 결과
        loaded_keys = {str(row.get("input_text", "")).strip() for row in loaded}

        # worker 비정상 종료 시 누락된 task 를 fallback 으로 채움
        if code != 0:
            for task in r.get("tasks", []):
                key = str(task.get("input_text", "")).strip()
                if key and key not in loaded_keys:
                    fb = dict(task)
                    fb["result"] = (
                        "새버전 실패"
                        if str(task.get("workflow", "")).lower() == "activity"
                        else "실패"
                    )
                    fb["memo"] = f"worker 프로세스 종료 코드 {code} / 결과 마커 누락"
                    loaded.append(fb)
            print(f"[{r['site']}] [오류] worker 종료 코드: {code}")
            failed = True

        site_rows.setdefault(r["site"], []).extend(loaded)

        # 임시 task 파일 정리
        try:
            tf = r.get("tasks_file")
            if tf and Path(tf).exists():
                Path(tf).unlink()
        except Exception as e:
            print(f"[{r['site']}] [주의] 임시 task 파일 정리 실패: {e}")

    # 텍스트 로그 기록 (AutoBots 호환) - region 별 파일
    for region in SUPPORTED_REGIONS:
        rows = site_rows.get(region, [])
        if rows:  # 작업이 있었던 region 만 로그 파일 갱신
            write_text_logs(region.lower(), rows)

    # 콘솔 요약
    for region in SUPPORTED_REGIONS:
        print_site_summary(region_display[region], site_rows.get(region, []))
    print(f"\n총 런닝타임: {format_duration(time.perf_counter() - started)}")
    print(f"[로그] {LOG_DIR}/booking_log_KLOOK_*.txt")

    if failed:
        sys.exit(1)
    print("\n[완료] Klook 작업 종료")


if __name__ == "__main__":
    main()
