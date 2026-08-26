"""
OTA Close Bot - 메인 오케스트레이터 (subprocess 병렬 실행).

GUI 가 즉시 실행 담당. main.py 는 데몬 모드 기본.

실행:
    python main.py                       # 데몬 (지역별 시간표: 아래 SCHEDULE 참고)
    python main.py --once                # 시간표 무시, 전 지역 즉시 1회
    python main.py --daemon
    python main.py --once --dry-run
    python main.py --once --agency kkday,vi
    python main.py --once --regions AUSTRALIA          # 호주 마켓만 1회 (테스트)
    python main.py --once --regions KOREA,JAPAN
    python main.py --once --date 2026-05-23

지역별 시간표는 아래 SCHEDULE 에서 수정. 지역 마켓(KOREA/JAPAN/AUSTRALIA/UK)은
KKDAY/KLOOK/GG 에만 적용되고, VI(Viator)/MRT(일본 전용)는 글로벌 봇이라
include_global=True 인 시간대에서만 함께 실행된다.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared.health import boot_all_missing, healthcheck_all
from shared.logger import get_logger
from shared.notify import notify
from shared.types import Result

LOG = get_logger("main")

IMMEDIATE_MODE = False

BOT_ORDER = ["kkday", "vi", "mrt", "klook", "gg"]

# 지역 마켓이 있는 봇 (KOREA/JAPAN/AUSTRALIA/UK 로 필터 가능)
REGION_BOTS = ["kkday", "klook", "gg"]
# 지역 구분이 없는 글로벌 봇 (include_global=True 인 시간대에만 실행)
GLOBAL_BOTS = ["vi", "mrt"]

# ── 지역별 마감 시간표 ─────────────────────────────────────────────
# 각 슬롯: time=(시,분), regions=이 시간에 마감할 지역 마켓,
#          include_global=True 면 VI/MRT(글로벌)도 이 시간에 함께 실행.
# UK 는 시간 정해지면 아래 주석 슬롯의 # 만 풀고 시간을 채우면 됨.
SCHEDULE = [
    {"time": (8, 50),  "regions": ["AUSTRALIA"],       "include_global": False},
    {"time": (9, 50),  "regions": ["KOREA", "JAPAN"],  "include_global": True},
    # {"time": (10, 50), "regions": ["UK"],             "include_global": False},  # UK: 시간 확정 후 활성화
]

BOT_FILES: Dict[str, Path] = {}
for key in BOT_ORDER:
    p = ROOT / f"{key}.py"
    if p.exists():
        BOT_FILES[key] = p
    else:
        LOG.warning("'%s.py' 모듈 파일이 없음 - 이 봇은 건너뜀", key)

# 라벨에 /KOREA/forward 등 slash + 소문자 가능 → [^\]]+ 로 어떤 라벨이든 받음
RESULT_RE = re.compile(
    r"\[([^\]]+)\]\s*success=(\d+)\s*failed=(\d+)\s*skipped=(\d+)"
)


def _spawn_bot(key: str, dry_run: bool, target_date: Optional[str] = None) -> "subprocess.Popen[bytes]":
    return _spawn_bot_with_args(key, dry_run, target_date, extra_args=None, env_extra=None)


def _spawn_bot_with_args(
    key: str,
    dry_run: bool,
    target_date: Optional[str] = None,
    extra_args: Optional[List[str]] = None,
    env_extra: Optional[Dict[str, str]] = None,
) -> "subprocess.Popen[bytes]":
    bot_path = BOT_FILES[key]
    cmd = [sys.executable, str(bot_path)]
    if dry_run:
        cmd.append("--dry-run")
    if target_date:
        cmd.extend(["--date", target_date])
    if extra_args:
        cmd.extend(extra_args)
    env = os.environ.copy()
    # 한국어 Windows 의 cp949 가 이모지/특수문자 인코딩 못 해서 print 시 죽는 문제 방지
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    if env_extra:
        env.update(env_extra)
    LOG.info("subprocess 시작: %s", " ".join(cmd))
    return subprocess.Popen(
        cmd, cwd=str(ROOT),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        env=env,
    )


def _stream_proc_output(key: str, proc, collected: List[str]) -> None:
    """subprocess stdout 을 한 줄씩 즉시 LOG 로 흘려보내고 동시에 collected 에 모은다."""
    try:
        if proc.stdout is None:
            return
        for raw in iter(proc.stdout.readline, b""):
            if not raw:
                break
            try:
                line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            except Exception:
                line = repr(raw)
            collected.append(line)
            LOG.info("[%s] %s", key.upper(), line)
    except Exception as e:
        LOG.warning("[%s] stdout 스트리밍 에러: %s", key.upper(), e)


def _parse_result(key: str, text: str, returncode: int) -> Result:
    success = failed = skipped = 0
    errors: List[str] = []
    matched = False
    for line in text.splitlines():
        m = RESULT_RE.search(line)
        if m:
            success = int(m.group(2))
            failed = int(m.group(3))
            skipped = int(m.group(4))
            matched = True
            break

    if not matched:
        # P0-4: 결과 라인이 없으면 그 worker 가 맡은 물량은 통계에서 사라진다.
        #   klook_worker 처럼 '치명 오류' 를 print 만 하고 exit 0 으로 끝나는 봇이 있어서,
        #   여태 이런 경우가 success=failed=skipped=0 -> 화면상 "실패 0건" 으로 보였다.
        #   실제로는 아무것도 안 닫힌 상태다. 실패로 명시해서 눈에 띄게 만든다.
        failed = max(failed, 1)
        if returncode != 0:
            errors.append(f"[결과없음] exit code {returncode} - 이 worker 의 작업이 통계에 안 잡힘. "
                          f"실제로 마감됐는지 수동 확인 필요")
        else:
            errors.append("[결과없음] exit 0 인데 결과 라인이 없음 - Chrome 미연결/로그인 만료 가능성. "
                          "실제로 마감됐는지 수동 확인 필요")

    if "Errors:" in text:
        try:
            after = text.split("Errors:", 1)[1]
            for raw in after.splitlines()[:20]:
                s = raw.strip()
                if s.startswith("-"):
                    errors.append(s.lstrip("-").strip())
        except Exception:
            pass

    return Result(agency=key.upper(), success=success, failed=failed,
                  skipped=skipped, errors=errors[:30])


def run_once(
    dry_run: bool = False,
    agencies: Optional[List[str]] = None,
    target_date: Optional[str] = None,
    boot: bool = True,
) -> List[Result]:
    """
    boot=True : 죽은 Chrome 자동 부팅 (데몬 모드 기본)
    boot=False: 호출자(GUI)가 필요한 Chrome 만 부팅했으므로 skip
    """
    LOG.info("=" * 60)
    LOG.info("병렬 실행 시작 (dry_run=%s, agencies=%s, target_date=%s, boot=%s)",
             dry_run, agencies or "all", target_date, boot)
    LOG.info("=" * 60)

    if boot:
        LOG.info("Chrome 자동 부팅 + 헬스체크 중...")
        boot_all_missing()
        time.sleep(3)
    else:
        LOG.info("Chrome 헬스체크 중 (자동 부팅 skip)...")

    try:
        from shared.health import REGION_CHROME_MAP, is_port_alive
        for region, (port, _bat) in REGION_CHROME_MAP.items():
            sign = "OK" if is_port_alive(port) else "DEAD"
            LOG.info("  Chrome[%s] port %d : %s", region, port, sign)
    except Exception:
        status = healthcheck_all()
        for bot, items in status.items():
            for port, alive in items:
                sign = "OK" if alive else "DEAD"
                LOG.info("  Chrome[%s] port %d : %s", bot, port, sign)

    if agencies:
        targets = [k for k in agencies if k in BOT_FILES]
        missing = [a for a in agencies if a not in BOT_FILES]
        if missing:
            LOG.warning("등록되지 않은 agency: %s", missing)
    else:
        targets = [k for k in BOT_ORDER if k in BOT_FILES]

    if not targets:
        LOG.warning("실행할 봇이 없음")
        return []

    # 지역별 병렬 + 봇별 분할 방식
    PARALLEL_BY_REGION = {"kkday", "klook", "gg"}
    # KKDAY/MRT 는 4분할 (quarter 1~4), KLOOK 는 2분할 (forward/backward)
    SPLIT_QUARTERS = {"kkday", "mrt", "vi"}
    SPLIT_FORWARD_BACKWARD = {"klook"}
    # discover-once-share 지원 봇 (worker 들이 product list 를 동시에 안 긁고 1번만 긁어서 공유)
    DISCOVER_ONCE_BOTS = {"kkday", "mrt", "vi"}
    DEFAULT_REGIONS = ["KOREA", "JAPAN", "AUSTRALIA", "UK"]

    # ============================================================
    # Phase 1: Discover-once-share + Work stealing (KKDAY + MRT)
    # 1. main 에서 region 별로 discover 먼저 → JSON 파일 저장 → worker 들에 전달
    # 2. main 에서 claim-dir 생성 (worker 들이 원자적 claim 으로 동적 부하 분산)
    # 효과: 시간 1/4~1/8 단축 (discover) + 30~40% 추가 단축 (idle worker 없음).
    # ============================================================
    discover_files: Dict[str, str] = {}   # key="kkday/KOREA" or "mrt", value=파일 경로
    claim_dirs: Dict[str, str] = {}       # key 동일, value=claim 디렉토리 경로
    discover_dir = ROOT / "logs" / "discover"
    queue_root = ROOT / "logs" / "queue"
    try:
        discover_dir.mkdir(parents=True, exist_ok=True)
        queue_root.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    _ts_suffix = (target_date or datetime.now().strftime("%Y-%m-%d"))

    def _setup_claim_dir(key_label: str) -> str:
        """worker 들이 공유할 claim 디렉토리 생성 (기존 마커 cleanup)."""
        import shutil
        safe_label = key_label.replace("/", "_")
        cdir = queue_root / f"{safe_label}_{_ts_suffix}"
        try:
            if cdir.exists():
                shutil.rmtree(cdir, ignore_errors=True)
            cdir.mkdir(parents=True, exist_ok=True)
        except Exception as _e:
            LOG.warning("[%s] claim_dir 생성 실패: %s", key_label, _e)
            return ""
        return str(cdir)

    for key in [k for k in targets if k in DISCOVER_ONCE_BOTS]:
        try:
            if key in PARALLEL_BY_REGION:
                regions_env = os.environ.get(f"{key.upper()}_REGIONS", "")
                regions = [r.strip().upper() for r in regions_env.split(",") if r.strip()]
                if not regions:
                    regions = list(DEFAULT_REGIONS)
                for region in regions:
                    out_file = str(discover_dir / f"{key}_{region}_{_ts_suffix}.json")
                    LOG.info("[Discover] %s/%s 시작 → %s", key.upper(), region, out_file)
                    d_proc = _spawn_bot_with_args(
                        key, dry_run, target_date,
                        extra_args=["--mode", "discover", "--region", region, "--output", out_file],
                        env_extra={f"{key.upper()}_REGIONS": region},
                    )
                    # stdout 흘려보내며 종료 대기 (병렬X — discover 는 직렬)
                    out_lines: List[str] = []
                    _stream_thread = None
                    try:
                        import threading as _th
                        _stream_thread = _th.Thread(
                            target=_stream_proc_output,
                            args=(f"DISCOVER/{key}/{region}", d_proc, out_lines),
                            daemon=True,
                        )
                        _stream_thread.start()
                        d_proc.wait(timeout=600)  # discover 는 10분 이내 끝나야 함
                    except subprocess.TimeoutExpired:
                        d_proc.kill()
                        LOG.error("[Discover] %s/%s 타임아웃 → 원래 방식 fallback", key.upper(), region)
                        continue
                    finally:
                        if _stream_thread:
                            _stream_thread.join(timeout=5)
                    if d_proc.returncode == 0 and os.path.exists(out_file):
                        discover_files[f"{key}/{region}"] = out_file
                        LOG.info("[Discover] %s/%s 완료: %s", key.upper(), region, out_file)
                    else:
                        LOG.warning("[Discover] %s/%s 실패 (rc=%s) → worker 가 직접 discover 함",
                                    key.upper(), region, d_proc.returncode)
            else:
                # MRT 같은 글로벌 봇 (region 분할 없음)
                out_file = str(discover_dir / f"{key}_{_ts_suffix}.json")
                LOG.info("[Discover] %s 시작 → %s", key.upper(), out_file)
                d_proc = _spawn_bot_with_args(
                    key, dry_run, target_date,
                    extra_args=["--mode", "discover", "--output", out_file],
                )
                out_lines = []
                _stream_thread = None
                try:
                    import threading as _th
                    _stream_thread = _th.Thread(
                        target=_stream_proc_output,
                        args=(f"DISCOVER/{key}", d_proc, out_lines),
                        daemon=True,
                    )
                    _stream_thread.start()
                    d_proc.wait(timeout=600)
                except subprocess.TimeoutExpired:
                    d_proc.kill()
                    LOG.error("[Discover] %s 타임아웃 → 원래 방식 fallback", key.upper())
                    continue
                finally:
                    if _stream_thread:
                        _stream_thread.join(timeout=5)
                if d_proc.returncode == 0 and os.path.exists(out_file):
                    discover_files[key] = out_file
                    LOG.info("[Discover] %s 완료: %s", key.upper(), out_file)
                else:
                    LOG.warning("[Discover] %s 실패 (rc=%s) → worker 가 직접 discover 함",
                                key.upper(), d_proc.returncode)
        except Exception as e:
            LOG.exception("[Discover] %s 단계 예외: %s", key.upper(), e)

    LOG.info("Discover 단계 완료. 수집 결과: %d 개", len(discover_files))

    procs: Dict[str, subprocess.Popen] = {}
    for key in targets:
        try:
            if key in PARALLEL_BY_REGION:
                # 환경변수에서 지역 리스트 가져오기
                regions_env = os.environ.get(f"{key.upper()}_REGIONS", "")
                regions = [r.strip().upper() for r in regions_env.split(",") if r.strip()]
                if not regions:
                    regions = list(DEFAULT_REGIONS)
                for region in regions:
                    # discover_file (region 별) 있으면 worker 에 전달
                    _disc_file = discover_files.get(f"{key}/{region}")
                    _disc_args = ["--discover-file", _disc_file] if _disc_file else []
                    # work-stealing: discover 성공한 봇만 claim_dir 적용
                    _claim_args: List[str] = []
                    if _disc_file and key in DISCOVER_ONCE_BOTS:
                        _cd = _setup_claim_dir(f"{key}/{region}")
                        if _cd:
                            claim_dirs[f"{key}/{region}"] = _cd
                            _claim_args = ["--claim-dir", _cd]
                    if key in SPLIT_QUARTERS:
                        for q in ("1", "2", "3", "4"):
                            label = f"{key}/{region}/q{q}"
                            procs[label] = _spawn_bot_with_args(
                                key, dry_run, target_date,
                                extra_args=["--region", region, "--quarter", q] + _disc_args + _claim_args,
                                env_extra={f"{key.upper()}_REGIONS": region,
                                           f"{key.upper()}_QUARTER": q},
                            )
                    elif key in SPLIT_FORWARD_BACKWARD:
                        for direction in ("forward", "backward"):
                            label = f"{key}/{region}/{direction}"
                            procs[label] = _spawn_bot_with_args(
                                key, dry_run, target_date,
                                extra_args=["--region", region, "--direction", direction] + _disc_args,
                                env_extra={f"{key.upper()}_REGIONS": region,
                                           f"{key.upper()}_DIRECTION": direction},
                            )
                    else:
                        label = f"{key}/{region}"
                        procs[label] = _spawn_bot_with_args(
                            key, dry_run, target_date,
                            extra_args=["--region", region] + _disc_args,
                            env_extra={f"{key.upper()}_REGIONS": region},
                        )
            elif key in SPLIT_QUARTERS:
                # 지역 분할 안 하고 quarter 만 (현재 MRT 가 해당, region 없는 경우)
                _disc_file = discover_files.get(key)
                _disc_args = ["--discover-file", _disc_file] if _disc_file else []
                _claim_args: List[str] = []
                if _disc_file and key in DISCOVER_ONCE_BOTS:
                    _cd = _setup_claim_dir(key)
                    if _cd:
                        claim_dirs[key] = _cd
                        _claim_args = ["--claim-dir", _cd]
                for q in ("1", "2", "3", "4"):
                    label = f"{key}/q{q}"
                    procs[label] = _spawn_bot_with_args(
                        key, dry_run, target_date,
                        extra_args=["--quarter", q] + _disc_args + _claim_args,
                        env_extra={f"{key.upper()}_QUARTER": q},
                    )
            elif key in SPLIT_FORWARD_BACKWARD:
                # MRT 같이 글로벌 1개지만 forward/backward 분할 하는 경우
                _disc_file = discover_files.get(key)
                _disc_args = ["--discover-file", _disc_file] if _disc_file else []
                for direction in ("forward", "backward"):
                    label = f"{key}/{direction}"
                    procs[label] = _spawn_bot_with_args(
                        key, dry_run, target_date,
                        extra_args=["--direction", direction] + _disc_args,
                        env_extra={f"{key.upper()}_DIRECTION": direction},
                    )
            else:
                # VI 같이 단일 worker
                procs[key] = _spawn_bot(key, dry_run, target_date)
        except Exception as e:
            LOG.exception("[%s] subprocess 띄우기 실패: %s", key, e)

    LOG.info("총 %d 개 subprocess 생성 -> 병렬 stdout 스트리밍 시작", len(procs))

    # 각 subprocess 의 stdout 을 별도 스레드로 실시간 스트리밍 (병렬 가시화)
    import threading
    collected: Dict[str, List[str]] = {label: [] for label in procs}
    threads: Dict[str, threading.Thread] = {}
    for label, proc in procs.items():
        t = threading.Thread(
            target=_stream_proc_output,
            args=(label, proc, collected[label]),
            daemon=True,
        )
        t.start()
        threads[label] = t

    # 모든 subprocess 종료 대기
    raw_results: List[Result] = []
    for label, proc in procs.items():
        try:
            proc.wait(timeout=3600)
        except subprocess.TimeoutExpired:
            proc.kill()
            LOG.error("[%s] 타임아웃 (3600s) -> kill", label.upper())
        # 스레드도 마무리
        threads[label].join(timeout=10)
        text = "\n".join(collected[label])
        r = _parse_result(label, text, proc.returncode or 0)
        r = dict(r)
        r["agency"] = label.upper()
        raw_results.append(r)
        LOG.info("[%s] 완료 (success=%d failed=%d skipped=%d)",
                 label.upper(), r["success"], r["failed"], r["skipped"])

    # 같은 봇의 여러 subprocess 결과를 봇 단위로 집계
    agg: Dict[str, Result] = {}
    for r in raw_results:
        base = r["agency"].split("/")[0].upper()
        if base not in agg:
            agg[base] = Result(agency=base, success=0, failed=0, skipped=0, errors=[])
        agg[base]["success"] += r["success"]
        agg[base]["failed"] += r["failed"]
        agg[base]["skipped"] += r["skipped"]
        agg[base]["errors"].extend(r.get("errors", []))
    results = list(agg.values())
    for r in results:
        r["errors"] = r["errors"][:30]
        LOG.info("[%s] 봇 집계: success=%d failed=%d skipped=%d",
                 r["agency"], r["success"], r["failed"], r["skipped"])

    today_str = datetime.now().strftime("%Y-%m-%d")
    try:
        notify(results, today_str)
    except Exception as e:
        LOG.exception("notify 실패: %s", e)

    LOG.info("=" * 60)
    LOG.info("병렬 실행 완료 (%d 봇)", len(results))
    LOG.info("=" * 60)
    return results


def run_slot(slot: Dict, dry_run: bool = False) -> List[Result]:
    """시간표 슬롯 1개 실행: 지정 지역(KKDAY/KLOOK/GG) + 선택적 글로벌 봇(VI/MRT)."""
    regions = slot["regions"]
    agencies = list(REGION_BOTS)
    if slot.get("include_global"):
        agencies += GLOBAL_BOTS

    # 지역 봇들에 이번 슬롯의 지역 마켓 주입 (run_once 가 {KEY}_REGIONS 를 읽음)
    for b in REGION_BOTS:
        os.environ[f"{b.upper()}_REGIONS"] = ",".join(regions)

    h, m = slot["time"]
    LOG.info(">>> 슬롯 실행 %02d:%02d  지역=%s  글로벌봇=%s  봇=%s",
             h, m, regions, slot.get("include_global", False), agencies)
    return run_once(dry_run=dry_run,
                    agencies=[a for a in agencies if a in BOT_FILES])


def _next_slot(now: datetime):
    """SCHEDULE 중 가장 가까운 다음 실행 슬롯과 그 시각을 반환."""
    best = None
    for slot in SCHEDULE:
        h, m = slot["time"]
        nxt = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if nxt <= now:
            nxt += timedelta(days=1)
        if best is None or nxt < best[1]:
            best = (slot, nxt)
    return best


def daemon_mode(dry_run: bool = False) -> None:
    LOG.info("24/7 데몬 모드 시작 - 지역별 시간표:")
    for slot in SCHEDULE:
        h, m = slot["time"]
        LOG.info("  %02d:%02d  지역=%s  글로벌봇(VI/MRT)=%s",
                 h, m, slot["regions"], slot.get("include_global", False))
    LOG.info("등록된 봇: %s", list(BOT_FILES.keys()))

    if not SCHEDULE:
        LOG.error("SCHEDULE 이 비어있음 - 데몬 종료")
        return

    last_fired = None  # (date, (시,분)) - 같은 슬롯 하루 중복실행 방지
    while True:
        slot, nxt = _next_slot(datetime.now())
        wait_s = max(0.0, (nxt - datetime.now()).total_seconds())
        LOG.info("다음 실행: %s  지역=%s  (%.0f초 대기)",
                 nxt.strftime("%Y-%m-%d %H:%M:%S"), slot["regions"], wait_s)
        end_at = time.time() + wait_s
        while time.time() < end_at:
            time.sleep(min(30, max(0, end_at - time.time())))

        # 중복 방지: 같은 날 같은 슬롯은 한 번만
        fire_key = (datetime.now().date(), tuple(slot["time"]))
        if last_fired == fire_key:
            time.sleep(60)
            continue
        last_fired = fire_key

        try:
            run_slot(slot, dry_run=dry_run)
        except Exception as e:
            LOG.exception("[run_slot] 슬롯 실행 실패: %s", e)


def main() -> None:
    ap = argparse.ArgumentParser(description="OTA Close Bot")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--daemon", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--agency", default=None)
    ap.add_argument("--date", default=None)
    ap.add_argument("--regions", default=None,
                    help="콤마 구분 (예: KOREA,JAPAN). 지정 안 하면 시간표 따름.")
    ap.add_argument("--no-boot", action="store_true",
                    help="Chrome 자동 부팅 skip (호출자가 이미 필요한 Chrome 만 띄운 경우)")
    args = ap.parse_args()

    agencies = None
    if args.agency:
        agencies = [a.strip().lower() for a in args.agency.split(",") if a.strip()]

    if args.regions:
        wanted_regions = [r.strip().upper() for r in args.regions.split(",") if r.strip()]
        for bot in REGION_BOTS:
            os.environ[f"{bot.upper()}_REGIONS"] = ",".join(wanted_regions)
        LOG.info("CLI --regions 적용: %s", wanted_regions)

    if args.once or args.agency or args.regions or args.date:
        run_once(dry_run=args.dry_run, agencies=agencies,
                 target_date=args.date, boot=not args.no_boot)
        return

    # 데몬 모드: 다음 슬롯 시각까지 정확히 대기 후 실행 (robust — 분 놓침 방지)
    daemon_mode(dry_run=args.dry_run)


if __name__ == "__main__":    main()

