#!/usr/bin/env python3
"""Generate a no-JavaScript, one-page sunset forecast from Open-Meteo."""

from __future__ import annotations

import json
import math
import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"
TEMPLATE_PATH = ROOT / "templates" / "index.template.html"
OUTPUT_PATH = ROOT / "index.html"

API_URL = "https://api.open-meteo.com/v1/forecast"
HOURLY_VARS = [
    "cloud_cover_low",
    "cloud_cover_mid",
    "cloud_cover_high",
    "precipitation",
    "visibility",
    "relative_humidity_2m",
    "wind_speed_10m",
]


@dataclass
class ModelResult:
    name: str
    score: float
    low: float
    mid: float
    high: float
    precipitation: float
    visibility: float
    humidity: float
    wind: float


def clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def avg(values: list[float | None]) -> float:
    usable = [float(v) for v in values if v is not None]
    return sum(usable) / len(usable) if usable else 0.0


def triangular_score(value: float, ideal: float, width: float) -> float:
    """100 at ideal, decreasing linearly to 0 over width."""
    return clamp(100.0 - abs(value - ideal) * 100.0 / width)


def sunset_score(low: float, mid: float, high: float, precipitation: float,
                 visibility: float, humidity: float, wind: float) -> float:
    """Heuristic score for potentially colourful sunset clouds.

    This is an explainable rule-based forecast, not a trained AI model.
    """
    high_score = triangular_score(high, ideal=65, width=65)
    mid_score = triangular_score(mid, ideal=45, width=60)
    low_clear_score = clamp(100 - low * 1.15)
    rain_score = clamp(100 - precipitation * 55)
    visibility_score = clamp((visibility / 30000) * 100)
    humidity_score = triangular_score(humidity, ideal=68, width=45)
    wind_score = triangular_score(wind, ideal=12, width=30)

    score = (
        high_score * 0.28
        + mid_score * 0.18
        + low_clear_score * 0.25
        + rain_score * 0.12
        + visibility_score * 0.07
        + humidity_score * 0.06
        + wind_score * 0.04
    )
    return clamp(score)


def extract_model_series(hourly: dict[str, Any], variable: str, model_id: str) -> list[Any]:
    """Open-Meteo returns suffixed variable names when multiple models are selected."""
    candidates = [
        f"{variable}_{model_id}",
        variable,
    ]
    # Be tolerant of model aliases or future naming changes.
    candidates.extend(
        key for key in hourly
        if key.startswith(variable + "_") and model_id.lower() in key.lower()
    )
    for key in candidates:
        if key in hourly:
            return hourly[key]
    raise KeyError(f"Missing {variable} for model {model_id}. Keys: {list(hourly)[:20]}")


def fetch(config: dict[str, Any]) -> dict[str, Any]:
    params = {
        "latitude": config["latitude"],
        "longitude": config["longitude"],
        "hourly": ",".join(HOURLY_VARS),
        "daily": "sunset",
        "timezone": config["timezone"],
        "forecast_days": config.get("forecast_days", 3),
        "models": ",".join(config["models"].values()),
    }
    response = requests.get(API_URL, params=params, timeout=45)
    response.raise_for_status()
    return response.json()


def choose_target_date(data: dict[str, Any], tz: ZoneInfo) -> tuple[str, datetime]:
    now = datetime.now(tz)
    sunset_strings = data["daily"]["sunset"]
    parsed = [datetime.fromisoformat(s).replace(tzinfo=tz) for s in sunset_strings]
    for sunset in parsed:
        if sunset >= now - timedelta(hours=1):
            return sunset.date().isoformat(), sunset
    return parsed[-1].date().isoformat(), parsed[-1]


def target_indices(times: list[str], sunset: datetime, offsets: list[int], tz: ZoneInfo) -> list[int]:
    parsed = [datetime.fromisoformat(t).replace(tzinfo=tz) for t in times]
    indices: list[int] = []
    for offset in offsets:
        target = sunset + timedelta(hours=offset)
        indices.append(min(range(len(parsed)), key=lambda i: abs(parsed[i] - target)))
    return sorted(set(indices))


def model_results(data: dict[str, Any], config: dict[str, Any], sunset: datetime) -> list[ModelResult]:
    hourly = data["hourly"]
    tz = ZoneInfo(config["timezone"])
    indices = target_indices(
        hourly["time"],
        sunset,
        config.get("sunset_window_hours", [-1, 0, 1]),
        tz,
    )

    results: list[ModelResult] = []
    for display_name, model_id in config["models"].items():
        values: dict[str, float] = {}
        for variable in HOURLY_VARS:
            series = extract_model_series(hourly, variable, model_id)
            values[variable] = avg([series[i] for i in indices])

        score = sunset_score(
            values["cloud_cover_low"],
            values["cloud_cover_mid"],
            values["cloud_cover_high"],
            values["precipitation"],
            values["visibility"],
            values["relative_humidity_2m"],
            values["wind_speed_10m"],
        )
        results.append(ModelResult(
            name=display_name,
            score=score,
            low=values["cloud_cover_low"],
            mid=values["cloud_cover_mid"],
            high=values["cloud_cover_high"],
            precipitation=values["precipitation"],
            visibility=values["visibility"],
            humidity=values["relative_humidity_2m"],
            wind=values["wind_speed_10m"],
        ))
    return results


def confidence(results: list[ModelResult]) -> tuple[float, str]:
    """Convert inter-model disagreement into a 0-100 confidence score."""
    if len(results) < 2:
        return 50.0, "資料不足"

    high_sd = statistics.pstdev(r.high for r in results)
    mid_sd = statistics.pstdev(r.mid for r in results)
    low_sd = statistics.pstdev(r.low for r in results)
    rain_sd = statistics.pstdev(r.precipitation for r in results)
    score_sd = statistics.pstdev(r.score for r in results)

    # Cloud cover spread is already on a 0-100 scale.
    # Rain spread is amplified because small mm differences matter near sunset.
    disagreement = (
        high_sd * 0.24
        + mid_sd * 0.20
        + low_sd * 0.24
        + min(rain_sd * 30, 30) * 0.12
        + score_sd * 0.20
    )
    value = clamp(100 - disagreement * 1.65)
    label = "高" if value >= 80 else "中" if value >= 60 else "低"
    return value, label


def rating(score: float) -> tuple[str, str]:
    if score >= 80:
        return "很值得期待", "excellent"
    if score >= 65:
        return "值得留意", "good"
    if score >= 45:
        return "仍有機會", "fair"
    return "機會偏低", "low"


def explanation(median_result: ModelResult, confidence_label: str) -> str:
    notes: list[str] = []
    if median_result.high >= 45:
        notes.append("中高層雲具備染色條件")
    else:
        notes.append("高層雲量偏少")
    if median_result.low <= 35:
        notes.append("低雲遮蔽風險較低")
    else:
        notes.append("低雲可能遮住西方地平線")
    if median_result.precipitation <= 0.2:
        notes.append("日落時段降水訊號不強")
    else:
        notes.append("日落時段有降水干擾")
    notes.append(f"三模式一致度為{confidence_label}")
    return "；".join(notes) + "。"


def render(config: dict[str, Any], data: dict[str, Any], results: list[ModelResult],
           target_date: str, sunset: datetime) -> str:
    scores = [r.score for r in results]
    main_score = statistics.median(scores)
    conf_value, conf_label = confidence(results)
    status_text, status_class = rating(main_score)
    median_result = min(results, key=lambda r: abs(r.score - main_score))
    tz = ZoneInfo(config["timezone"])
    updated = datetime.now(tz)

    model_rows = "\n".join(
        f"""<div class="model-row">
          <span class="model-name">{r.name}</span>
          <div class="mini-track"><span style="width:{round(r.score)}%"></span></div>
          <strong>{round(r.score)}</strong>
        </div>"""
        for r in results
    )

    replacements = {
        "{{SITE_TITLE}}": str(config["site_title"]),
        "{{LOCATION}}": str(config["location_name"]),
        "{{TARGET_DATE}}": target_date,
        "{{SUNSET_TIME}}": sunset.strftime("%H:%M"),
        "{{UPDATED_TIME}}": updated.strftime("%Y-%m-%d %H:%M"),
        "{{REFRESH_SECONDS}}": str(int(config.get("refresh_minutes", 60)) * 60),
        "{{MAIN_SCORE}}": str(round(main_score)),
        "{{STATUS_TEXT}}": status_text,
        "{{STATUS_CLASS}}": status_class,
        "{{CONFIDENCE}}": str(round(conf_value)),
        "{{CONFIDENCE_LABEL}}": conf_label,
        "{{EXPLANATION}}": explanation(median_result, conf_label),
        "{{HIGH_CLOUD}}": str(round(statistics.median(r.high for r in results))),
        "{{MID_CLOUD}}": str(round(statistics.median(r.mid for r in results))),
        "{{LOW_CLOUD}}": str(round(statistics.median(r.low for r in results))),
        "{{PRECIPITATION}}": f"{statistics.median(r.precipitation for r in results):.1f}",
        "{{VISIBILITY}}": f"{statistics.median(r.visibility for r in results) / 1000:.1f}",
        "{{HUMIDITY}}": str(round(statistics.median(r.humidity for r in results))),
        "{{WIND}}": f"{statistics.median(r.wind for r in results):.1f}",
        "{{MODEL_ROWS}}": model_rows,
    }

    html = TEMPLATE_PATH.read_text(encoding="utf-8")
    for key, value in replacements.items():
        html = html.replace(key, value)
    return html


def fallback_html(config: dict[str, Any], error: Exception) -> str:
    html = TEMPLATE_PATH.read_text(encoding="utf-8")
    replacements = {
        "{{SITE_TITLE}}": str(config["site_title"]),
        "{{LOCATION}}": str(config["location_name"]),
        "{{TARGET_DATE}}": "資料更新失敗",
        "{{SUNSET_TIME}}": "--:--",
        "{{UPDATED_TIME}}": datetime.now(ZoneInfo(config["timezone"])).strftime("%Y-%m-%d %H:%M"),
        "{{REFRESH_SECONDS}}": str(int(config.get("refresh_minutes", 60)) * 60),
        "{{MAIN_SCORE}}": "--",
        "{{STATUS_TEXT}}": "暫無最新預報",
        "{{STATUS_CLASS}}": "fair",
        "{{CONFIDENCE}}": "--",
        "{{CONFIDENCE_LABEL}}": "資料不足",
        "{{EXPLANATION}}": f"無法取得最新模式資料。系統將於下次排程重試。錯誤：{type(error).__name__}",
        "{{HIGH_CLOUD}}": "--",
        "{{MID_CLOUD}}": "--",
        "{{LOW_CLOUD}}": "--",
        "{{PRECIPITATION}}": "--",
        "{{VISIBILITY}}": "--",
        "{{HUMIDITY}}": "--",
        "{{WIND}}": "--",
        "{{MODEL_ROWS}}": "<p class='muted'>ECMWF／GFS／ICON 資料暫時無法取得。</p>",
    }
    for key, value in replacements.items():
        html = html.replace(key, value)
    return html


def main() -> None:
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    try:
        data = fetch(config)
        target_date, sunset = choose_target_date(data, ZoneInfo(config["timezone"]))
        results = model_results(data, config, sunset)
        html = render(config, data, results, target_date, sunset)
    except Exception as exc:
        # Keep the site deployable even during a temporary API issue.
        html = fallback_html(config, exc)
        print(f"WARNING: generated fallback page: {exc}")
    OUTPUT_PATH.write_text(html, encoding="utf-8")
    print(f"Generated {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
