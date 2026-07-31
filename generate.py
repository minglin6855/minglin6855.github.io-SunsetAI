#!/usr/bin/env python3
from __future__ import annotations

import html
import json
import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"
TEMPLATE_PATH = ROOT / "templates/index.template.html"
OUTPUT_PATH = ROOT / "index.html"

API_URL = "https://api.open-meteo.com/v1/forecast"
HOURLY_VARS = [
    "cloud_cover_low",
    "cloud_cover_mid",
    "cloud_cover_high",
    "precipitation",
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
    humidity: float
    wind: float


def clamp(value: float, low: float = 0, high: float = 100) -> float:
    return max(low, min(high, value))


def avg(values: list[float | None]) -> float:
    usable = [float(v) for v in values if v is not None]
    return sum(usable) / len(usable) if usable else 0


def triangular(value: float, ideal: float, width: float) -> float:
    return clamp(100 - abs(value - ideal) * 100 / width)


def sunset_score(low: float, mid: float, high: float, precipitation: float,
                 humidity: float, wind: float) -> float:
    high_score = triangular(high, 65, 65)
    mid_score = triangular(mid, 45, 60)
    low_clear = clamp(100 - low * 1.15)
    rain = clamp(100 - precipitation * 55)
    humid = triangular(humidity, 68, 45)
    wind_score = triangular(wind, 12, 30)
    return clamp(
        high_score * .28 + mid_score * .18 + low_clear * .25 +
        rain * .16 + humid * .08 + wind_score * .05
    )


def fetch_spot(spot: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    params = {
        "latitude": spot["latitude"],
        "longitude": spot["longitude"],
        "hourly": ",".join(HOURLY_VARS),
        "daily": "sunset,sunrise",
        "timezone": config["timezone"],
        "forecast_days": config.get("forecast_days", 3),
        "models": ",".join(config["models"].values()),
    }
    response = requests.get(API_URL, params=params, timeout=45)
    if not response.ok:
        raise RuntimeError(f"Open-Meteo HTTP {response.status_code}: {response.text[:500]}")
    payload = response.json()
    if payload.get("error"):
        raise RuntimeError(f"Open-Meteo API error: {payload.get('reason', payload)}")
    return payload


def series(hourly: dict[str, Any], variable: str, model_id: str) -> list[Any]:
    aliases = {
        "ecmwf_ifs025": ("ecmwf", "ifs025", "ifs_025", "ifs"),
        "gfs_global": ("gfs_global", "gfs"),
        "icon_global": ("icon_global", "icon"),
    }
    candidates = [f"{variable}_{model_id}", variable]
    tokens = aliases.get(model_id, (model_id,))
    candidates += [
        key for key in hourly
        if key.startswith(variable + "_")
        and any(token.lower() in key.lower() for token in tokens)
    ]
    for key in dict.fromkeys(candidates):
        if key in hourly:
            return hourly[key]
    matching = [key for key in hourly if key.startswith(variable + "_")]
    raise KeyError(
        f"Missing {variable} for {model_id}; available model fields: {matching}"
    )


def daily_series(daily: dict[str, Any], variable: str) -> list[Any]:
    """Read a daily variable from single- or multi-model Open-Meteo responses."""
    if variable in daily:
        return daily[variable]

    # Multi-model responses may suffix daily fields with the model identifier.
    matching = [
        key for key in daily
        if key.startswith(variable + "_") and isinstance(daily[key], list)
    ]
    if matching:
        # Sunrise/sunset differences between global models are negligible here.
        # Prefer ECMWF when available, otherwise use the first returned model.
        preferred = next(
            (key for key in matching if "ecmwf" in key.lower()),
            matching[0],
        )
        return daily[preferred]

    raise KeyError(
        f"Missing daily {variable}; available daily fields: {list(daily.keys())}"
    )


def choose_sunset(data: dict[str, Any], tz: ZoneInfo) -> tuple[str, datetime]:
    now = datetime.now(tz)
    sunset_values = daily_series(data["daily"], "sunset")
    sunsets = [
        datetime.fromisoformat(x).replace(tzinfo=tz)
        for x in sunset_values
        if x
    ]
    if not sunsets:
        raise ValueError("Open-Meteo returned no usable sunset values")

    for sunset in sunsets:
        if sunset >= now - timedelta(hours=1):
            return sunset.date().isoformat(), sunset
    return sunsets[-1].date().isoformat(), sunsets[-1]


def indices(times: list[str], target: datetime, offsets: list[int], tz: ZoneInfo) -> list[int]:
    parsed = [datetime.fromisoformat(x).replace(tzinfo=tz) for x in times]
    return sorted(set(
        min(range(len(parsed)), key=lambda i: abs(parsed[i] - (target + timedelta(hours=o))))
        for o in offsets
    ))


def calculate_models(data: dict[str, Any], config: dict[str, Any], sunset: datetime) -> list[ModelResult]:
    hourly = data["hourly"]
    idx = indices(
        hourly["time"], sunset,
        config.get("sunset_window_hours", [-1, 0, 1]),
        ZoneInfo(config["timezone"])
    )
    results = []
    for display, model_id in config["models"].items():
        vals = {
            var: avg([series(hourly, var, model_id)[i] for i in idx])
            for var in HOURLY_VARS
        }
        results.append(ModelResult(
            display,
            sunset_score(
                vals["cloud_cover_low"], vals["cloud_cover_mid"], vals["cloud_cover_high"],
                vals["precipitation"],
                vals["relative_humidity_2m"], vals["wind_speed_10m"]
            ),
            vals["cloud_cover_low"], vals["cloud_cover_mid"], vals["cloud_cover_high"],
            vals["precipitation"],
            vals["relative_humidity_2m"], vals["wind_speed_10m"],
        ))
    return results


def confidence(results: list[ModelResult]) -> tuple[float, str]:
    high_sd = statistics.pstdev(r.high for r in results)
    mid_sd = statistics.pstdev(r.mid for r in results)
    low_sd = statistics.pstdev(r.low for r in results)
    rain_sd = statistics.pstdev(r.precipitation for r in results)
    score_sd = statistics.pstdev(r.score for r in results)
    disagreement = (
        high_sd * .24 + mid_sd * .20 + low_sd * .24 +
        min(rain_sd * 30, 30) * .12 + score_sd * .20
    )
    value = clamp(100 - disagreement * 1.65)
    return value, "高" if value >= 80 else "中" if value >= 60 else "低"


def status(score: float) -> tuple[str, str]:
    if score >= 80: return "很值得期待", "excellent"
    if score >= 65: return "值得留意", "good"
    if score >= 45: return "仍有機會", "fair"
    return "機會偏低", "low"


def spot_card(spot: dict[str, Any], config: dict[str, Any]) -> tuple[str, str]:
    data = fetch_spot(spot, config)
    date, sunset = choose_sunset(data, ZoneInfo(config["timezone"]))
    results = calculate_models(data, config, sunset)
    main = statistics.median(r.score for r in results)
    conf, conf_label = confidence(results)
    label, css = status(main)
    med = min(results, key=lambda r: abs(r.score - main))

    model_pills = "".join(
        f'<span class="model-pill"><b>{html.escape(r.name)}</b>{round(r.score)}</span>'
        for r in results
    )

    card = f"""
    <article class="spot-card">
      <div class="spot-head">
        <div>
          <h3>{html.escape(spot["name"])}</h3>
          <p>{html.escape(spot.get("detail", ""))}</p>
        </div>
        <time>{sunset.strftime("%H:%M")} 日落</time>
      </div>

      <div class="score-block">
        <div>
          <span class="score-label">火燒雲指數</span>
          <div class="score-value">{round(main)}<small>/100</small></div>
        </div>
        <div class="confidence-badge" style="--confidence:{round(conf)}">
          <strong>{round(conf)}</strong>
          <span>{conf_label}信心</span>
        </div>
      </div>

      <div class="score-track"><i class="{css}" style="width:{round(main)}%"></i></div>
      <p class="verdict {css}">{label}</p>

      <div class="cloud-grid">
        <div><span>高雲</span><strong>{round(med.high)}%</strong></div>
        <div><span>中雲</span><strong>{round(med.mid)}%</strong></div>
        <div><span>低雲</span><strong>{round(med.low)}%</strong></div>
      </div>

      <div class="model-pills">{model_pills}</div>

      <details>
        <summary>查看氣象細節</summary>
        <dl>
          <div><dt>降水</dt><dd>{med.precipitation:.1f} mm</dd></div>
          <div><dt>濕度</dt><dd>{round(med.humidity)}%</dd></div>
          <div><dt>風速</dt><dd>{med.wind:.1f} km/h</dd></div>
        </dl>
      </details>
    </article>
    """
    return card, date


def fallback_card(spot: dict[str, Any]) -> str:
    return f"""
    <article class="spot-card unavailable">
      <div class="spot-head">
        <div><h3>{html.escape(spot["name"])}</h3><p>{html.escape(spot.get("detail", ""))}</p></div>
        <time>--:-- 日落</time>
      </div>
      <div class="score-block">
        <div><span class="score-label">火燒雲指數</span><div class="score-value">--<small>/100</small></div></div>
        <div class="confidence-badge"><strong>--</strong><span>資料更新中</span></div>
      </div>
      <p class="muted">Open-Meteo 暫時無法取得，系統將於下次排程重試。</p>
    </article>
    """


def build_regions(config: dict[str, Any]) -> tuple[str, str, str]:
    controls = []
    panels = []
    target_date = ""
    default = config.get("default_region", "south")

    for key, region in config["regions"].items():
        checked = " checked" if key == default else ""
        controls.append(
            f'<input class="region-radio" type="radio" name="region" id="region-{key}"{checked}>'
        )

    nav = '<nav class="region-tabs" aria-label="選擇區域">'
    for key, region in config["regions"].items():
        nav += (
            f'<label for="region-{key}" class="region-tab region-tab-{key}">'
            f'<strong>{html.escape(region["label"])}</strong>'
            f'<span>{html.escape(region["description"])}</span></label>'
        )
    nav += "</nav>"

    for key, region in config["regions"].items():
        cards = []
        for spot in region["spots"]:
            try:
                card, date = spot_card(spot, config)
                target_date = target_date or date
                cards.append(card)
            except Exception as exc:
                print(f"WARNING {spot['name']}: {type(exc).__name__}: {exc}")
                cards.append(fallback_card(spot))
        panels.append(
            f'<section class="region-panel panel-{key}" aria-labelledby="region-{key}">'
            f'<div class="panel-heading"><span>{html.escape(region["label"])}</span>'
            f'<p>{len(region["spots"])} 個攝影點</p></div>'
            f'<div class="spot-list">{"".join(cards)}</div></section>'
        )
    return "".join(controls) + nav, "".join(panels), target_date or "資料更新中"


def main() -> None:
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    controls, panels, target_date = build_regions(config)
    now = datetime.now(ZoneInfo(config["timezone"]))
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    replacements = {
        "{{SITE_TITLE}}": html.escape(config["site_title"]),
        "{{SITE_SUBTITLE}}": html.escape(config["site_subtitle"]),
        "{{REGION_CONTROLS}}": controls,
        "{{REGION_PANELS}}": panels,
        "{{TARGET_DATE}}": target_date,
        "{{UPDATED_TIME}}": now.strftime("%Y-%m-%d %H:%M"),
        "{{REFRESH_SECONDS}}": str(config.get("refresh_minutes", 60) * 60),
    }
    for k, v in replacements.items():
        template = template.replace(k, v)
    OUTPUT_PATH.write_text(template, encoding="utf-8")
    print(f"Generated {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
