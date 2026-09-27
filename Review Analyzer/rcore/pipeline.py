from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import pandas as pd

from collectors import getyourguide, kkday, klook, myrealtrip, tripcom
from collectors.base import CollectorContext
from collectors.config import CHANNEL_DISPLAY, SUPPORTED_CHANNELS
from rcore.browser import BrowserSession
from rcore.browser_profiles import BrowserProfileManager
from rcore.excel_loader import LoadedBookings, load_bookings
from rcore.matcher import build_master_dataset
from rcore.metrics import (
    build_area_metrics,
    build_channel_metrics,
    build_overall_metrics,
    build_tour_metrics,
)
from rcore.scope import ScopeResult, build_scope, unsupported_counts
from export.excel import save_phase1_excel


COLLECTORS = {
    "L": klook.collect,
    "KK": kkday.collect,
    "GG": getyourguide.collect,
    "TPC": tripcom.collect,
    "MRT": myrealtrip.collect,
}


@dataclass
class PipelineResult:
    loaded: LoadedBookings
    scope: ScopeResult
    master: pd.DataFrame
    overall: pd.DataFrame
    tours: pd.DataFrame
    areas: pd.DataFrame
    channels: pd.DataFrame
    unsupported: pd.DataFrame
    collection_status: dict
    output_path: str | None


def run_phase1(
    excel_path: str,
    start_date=None,
    end_date=None,
    areas: Iterable[str] | None = None,
    channels: Iterable[str] | None = None,
    debug_address: str = "127.0.0.1:9222",
    output_path: str | None = None,
    log=print,
    browser_manager: BrowserProfileManager | None = None,
    auto_launch_browsers: bool = True,
) -> PipelineResult:
    """Collect reviews and build metrics.

    v1.2 multi-account routing behavior:
    - Chrome account is selected by (Area + OTA), with optional exact Area overrides.
    - Default profiles: KR(9222), JP(9223), AU(9224), UK(9225).
    - Japan MRT intentionally routes to KR because Korea/Japan share the MRT account.
    - Routes and future extra accounts are configurable from the GUI.
    - Unconfigured Area/OTA combinations never fall back to a random account; those
      bookings are isolated as collection errors until a profile is assigned.
    - If one profile fails, only bookings routed through that profile are excluded
      from Valid Review Scope; successful profiles remain valid.

    ``debug_address`` is retained for backward compatibility. When a
    BrowserProfileManager is available, its per-area routing takes precedence.
    """
    loaded = load_bookings(excel_path)
    start = start_date or loaded.min_date
    end = end_date or loaded.max_date
    selected_channels = list(channels) if channels else list(SUPPORTED_CHANNELS)

    scope = build_scope(
        loaded.df,
        start,
        end,
        areas=areas,
        channels=selected_channels,
    )

    if scope.scope_df.empty:
        raise ValueError("선택 조건에 해당하는 예약이 없습니다.")

    manager = browser_manager or BrowserProfileManager()
    plan = manager.build_collection_plan(scope.crawl_df, selected_channels)

    dfrom = scope.start_date.strftime("%Y-%m-%d")
    dto = scope.end_date.strftime("%Y-%m-%d")
    used_channels = [
        ch for ch in selected_channels
        if ch in SUPPORTED_CHANNELS and ch in set(scope.crawl_df["Agency"])
    ]

    collected: dict[str, dict[str, dict]] = {}
    collection_status: dict[str, dict] = {}
    row_collection_status: dict[object, str] = {}
    browsers: dict[str, BrowserSession] = {}

    # Keep a short, visible routing summary in the progress log.
    log("\n[Chrome 자동 연결]")
    required = manager.required_profiles(scope.crawl_df, selected_channels)
    for profile, info in required.items():
        areas_txt = ", ".join(sorted(info["areas"]))
        channels_txt = ", ".join(sorted(info["channels"]))
        log(
            f"  {profile} · {manager.profile_name(profile)} · port {manager.profile_port(profile)}"
            f" · Area: {areas_txt} · OTA: {channels_txt}"
        )
    unconfigured = manager.unconfigured_routes(scope.crawl_df, selected_channels)
    for item in unconfigured:
        log(
            f"  ⚠ 미설정: {item['area']} · {CHANNEL_DISPLAY.get(item['channel'], item['channel'])}"
            " → 수집 제외 (연결 설정에서 Chrome Profile 지정 필요)"
        )

    for ch in used_channels:
        log(f"\n[{ch}] {CHANNEL_DISPLAY[ch]} 수집 시작")
        ch_reviews: dict[str, dict] = {}
        profile_results: dict[str, dict] = {}
        entries = plan.get(ch, [])

        for entry in entries:
            profile = entry["profile"]
            # NaT 는 truthy 라서 `or` 로 걸러지지 않는다. 명시적으로 확인한다.
            def _day(value, fallback):
                ts = pd.Timestamp(value) if value is not None else pd.NaT
                if pd.isna(ts):
                    ts = pd.Timestamp(fallback)
                return ts.strftime("%Y-%m-%d")

            p_start = _day(entry.get("start_date"), scope.start_date)
            p_end = _day(entry.get("end_date"), scope.end_date)
            area_text = ", ".join(entry.get("areas", [])) or "-"

            if profile is None:
                key = "UNCONFIGURED"
                err = (
                    f"{area_text} / {CHANNEL_DISPLAY.get(ch, ch)} Chrome Profile 미설정. "
                    "GUI의 OTA 계정/Chrome 설정에서 연결을 지정하세요."
                )
                profile_results[key] = {
                    "status": "UNCONFIGURED",
                    "count": 0,
                    "error": err,
                    "areas": list(entry.get("areas", [])),
                }
                for idx in entry.get("row_indices", []):
                    row_collection_status[idx] = "UNCONFIGURED"
                log(f"[{ch}] ⚠ {err}")
                continue

            log(
                f"[{ch}] {profile} · {manager.profile_name(profile)} 연결"
                f" · {area_text} · {p_start} ~ {p_end}"
            )

            try:
                if auto_launch_browsers:
                    started, ready = manager.ensure_profile(profile)
                    if started:
                        log(
                            f"[{ch}] Chrome 자동 실행: {profile} · {manager.profile_name(profile)}"
                            f" (port {manager.profile_port(profile)})"
                        )
                    if not ready:
                        raise RuntimeError(
                            f"Chrome debug port {manager.profile_port(profile)} 연결 실패"
                        )

                if profile not in browsers:
                    browser = BrowserSession(debug_address=manager.debug_address(profile))
                    browser.connect()
                    browsers[profile] = browser
                ctx = CollectorContext(browser=browsers[profile], log=log)

                reviews = COLLECTORS[ch](ctx, p_start, p_end)
                ch_reviews.update(reviews)
                profile_results[profile] = {
                    "status": "SUCCESS",
                    "count": len(reviews),
                    "error": "",
                    "areas": list(entry.get("areas", [])),
                }
                for idx in entry.get("row_indices", []):
                    row_collection_status[idx] = "SUCCESS"
                log(
                    f"[{ch}] {profile} · {manager.profile_name(profile)} 완료: {len(reviews)} reviews"
                )
            except Exception as exc:
                profile_results[profile] = {
                    "status": "FAILED",
                    "count": 0,
                    "error": str(exc),
                    "areas": list(entry.get("areas", [])),
                }
                for idx in entry.get("row_indices", []):
                    row_collection_status[idx] = "FAILED"
                log(f"[{ch}] {profile} · {manager.profile_name(profile)} 실패: {exc}")

        collected[ch] = ch_reviews
        statuses = [x.get("status") for x in profile_results.values()]
        if statuses and all(s == "SUCCESS" for s in statuses):
            agg_status = "SUCCESS"
            agg_error = ""
        elif any(s == "SUCCESS" for s in statuses):
            agg_status = "PARTIAL"
            agg_error = " | ".join(
                f"{p}: {x.get('error', '')}"
                for p, x in profile_results.items()
                if x.get("status") != "SUCCESS"
            )
        else:
            agg_status = "FAILED"
            agg_error = " | ".join(
                f"{p}: {x.get('error', '')}" for p, x in profile_results.items()
            )
        collection_status[ch] = {
            "status": agg_status,
            "count": len(ch_reviews),
            "error": agg_error,
            "profiles": profile_results,
        }
        log(f"[{ch}] 전체 완료: {len(ch_reviews)} reviews · {agg_status}")

    # Supported channels present in scope but not selected are not collection errors;
    # they are intentionally excluded from this run.
    for ch in SUPPORTED_CHANNELS:
        if ch in set(scope.eligible_df["Agency"]) and ch not in collection_status:
            collection_status[ch] = {
                "status": "NOT_SELECTED" if ch not in selected_channels else "NOT_RUN",
                "count": 0,
                "error": "",
                "profiles": {},
            }

    master = build_master_dataset(
        scope.scope_df,
        collected,
        collection_status,
        row_collection_status=row_collection_status,
    )

    # If a supported channel was intentionally not selected, mark those rows as excluded
    # rather than as a collection error.
    not_selected = master[
        master["Agency"].isin(SUPPORTED_CHANNELS)
        & ~master["Agency"].isin(selected_channels)
    ].index
    if len(not_selected):
        master.loc[not_selected, "Collection Status"] = "NOT_SELECTED"
        master.loc[not_selected, "Collection Eligible"] = False

    # 수집한 리뷰 중 이번 범위의 예약과 실제로 붙은 것이 몇 건인지 센다.
    # 남는 것은 '범위 밖 예약의 리뷰' 다. 보고하지 않으면 개수가 왜 다른지
    # 아무도 설명할 수 없다.
    if "Review Found" in master.columns:
        for ch, info in collection_status.items():
            matched = int(
                master[master["Agency"].astype(str).str.upper().eq(ch)]["Review Found"]
                .fillna(False).astype(bool).sum()
            )
            info["matched"] = matched
            info["unmatched"] = max(0, int(info.get("count", 0)) - matched)
            if info.get("count") and info["unmatched"]:
                log(
                    f"[{ch}] 수집 {info['count']}건 중 {matched}건이 이번 범위 예약과"
                    f" 연결됨 · {info['unmatched']}건은 범위 밖 예약의 리뷰"
                )

    overall = build_overall_metrics(master)
    tours = build_tour_metrics(master)
    areas_df = build_area_metrics(master)
    channels_df = build_channel_metrics(master)
    unsupported = unsupported_counts(scope)

    if output_path is None:
        src = Path(excel_path)
        output_path = str(src.with_name(f"Review Analyzer {dfrom}_{dto}.xlsx"))

    saved = save_phase1_excel(
        output_path,
        master=master,
        overall=overall,
        tours=tours,
        areas=areas_df,
        channels=channels_df,
        unsupported=unsupported,
        collection_status=collection_status,
    )

    return PipelineResult(
        loaded=loaded,
        scope=scope,
        master=master,
        overall=overall,
        tours=tours,
        areas=areas_df,
        channels=channels_df,
        unsupported=unsupported,
        collection_status=collection_status,
        output_path=saved,
    )
