import hashlib, json, time
from datetime import date
from pathlib import Path
from typing import List

import numpy as np
import pandas as pd
import requests
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, model_validator
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

app = FastAPI(title="ITD112 Soil × Weather API", version="2.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

SITES = [
    {"site": "Iligan City", "lat": 8.200, "lon": 124.300},
    {"site": "Malaybalay", "lat": 8.150, "lon": 125.130},
    {"site": "Valencia", "lat": 7.900, "lon": 125.090},
    {"site": "Davao (Calinan)", "lat": 7.190, "lon": 125.460},
    {"site": "General Santos", "lat": 6.150, "lon": 125.150},
]
SITE_MAP = {x["site"]: x for x in SITES}
SOIL_URL = "https://rest.isric.org/soilgrids/v2.0/properties/query"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
SOIL_PROPERTIES = ["clay", "sand", "silt", "phh2o", "soc"]
TEXTURE_PROPERTIES = ["clay", "sand", "silt"]
SOIL_DEPTHS = ["0-5cm", "5-15cm", "15-30cm"]
SOIL_VALUES = ["mean", "Q0.05", "Q0.95"]
DAILY_VARS = ["temperature_2m_mean", "precipitation_sum", "et0_fao_evapotranspiration"]
TIMEZONE = "Asia/Manila"
PAUSE = 12
CACHE_DIR = Path(__file__).resolve().parents[1] / "cache"
CACHE_DIR.mkdir(exist_ok=True)


class RunRequest(BaseModel):
    sites: List[str] = Field(min_length=1)
    start_date: date
    end_date: date

    @model_validator(mode="after")
    def valid(self):
        unknown = [s for s in self.sites if s not in SITE_MAP]
        if unknown:
            raise ValueError(f"Unknown site(s): {', '.join(unknown)}")
        if self.start_date > self.end_date:
            raise ValueError("start_date must be on or before end_date")
        return self


def session():
    retry = Retry(
        total=3,
        backoff_factor=2,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
    )
    s = requests.Session()
    s.mount("https://", HTTPAdapter(max_retries=retry))
    s.headers["User-Agent"] = "itd112-lab1-fastapi/2.0 educational-app"
    return s


HTTP = session()


def full_url(url, params):
    return requests.Request("GET", url, params=params).prepare().url


def archive_req(sites, start, end):
    p = {
        "latitude": ",".join(str(x["lat"]) for x in sites),
        "longitude": ",".join(str(x["lon"]) for x in sites),
        "start_date": str(start),
        "end_date": str(end),
        "daily": ",".join(DAILY_VARS),
        "timezone": TIMEZONE,
    }
    return {"api": "Open-Meteo Archive", "label": "All selected sites", "url": ARCHIVE_URL, "params": p, "full_url": full_url(ARCHIVE_URL, p)}


def forecast_req(sites):
    p = {
        "latitude": ",".join(str(x["lat"]) for x in sites),
        "longitude": ",".join(str(x["lon"]) for x in sites),
        "daily": ",".join(DAILY_VARS),
        "forecast_days": 16,
        "timezone": TIMEZONE,
    }
    return {"api": "Open-Meteo Forecast", "label": "16-day forecast", "url": FORECAST_URL, "params": p, "full_url": full_url(FORECAST_URL, p)}


def soil_req(x):
    # One request per site contains all three requested depths and all uncertainty values.
    p = [("lat", x["lat"]), ("lon", x["lon"])]
    p += [("depth", d) for d in SOIL_DEPTHS]
    p += [("value", v) for v in SOIL_VALUES]
    p += [("property", v) for v in SOIL_PROPERTIES]
    return {"api": "SoilGrids", "label": x["site"], "url": SOIL_URL, "params": p, "full_url": full_url(SOIL_URL, p)}


def send(req, ttl_seconds=None):
    key = hashlib.sha1((req["url"] + json.dumps(req["params"], sort_keys=True, default=str)).encode()).hexdigest()
    path = CACHE_DIR / f"{key}.json"
    cache_valid = path.exists() and (ttl_seconds is None or time.time() - path.stat().st_mtime < ttl_seconds)
    if cache_valid:
        text = path.read_text()
        return {"status": None, "from_cache": True, "data": json.loads(text), "elapsed_ms": None, "bytes": len(text.encode())}
    t = time.perf_counter()
    r = HTTP.get(req["url"], params=req["params"], timeout=60)
    elapsed = (time.perf_counter() - t) * 1000
    r.raise_for_status()
    data = r.json()
    text = json.dumps(data)
    path.write_text(text)
    return {"status": r.status_code, "from_cache": False, "data": data, "elapsed_ms": round(elapsed, 1), "bytes": len(r.content)}


def parse_weather(data, sites, kind):
    results = data if isinstance(data, list) else [data]
    frames = []
    for x, res in zip(sites, results):
        df = pd.DataFrame(res.get("daily", {}))
        if df.empty:
            continue
        df.insert(0, "site", x["site"])
        df["kind"] = kind
        frames.append(df)
    if not frames:
        return pd.DataFrame(columns=["site", "time", *DAILY_VARS, "kind"])
    return pd.concat(frames, ignore_index=True)


def parse_soil(data, site):
    rows = []
    for layer in data.get("properties", {}).get("layers", []):
        prop = layer.get("name")
        unit = layer.get("unit_measure", {})
        factor = unit.get("d_factor") or 1
        for depth in layer.get("depths", []):
            label = depth.get("label") or f'{depth.get("range", {}).get("top_depth")}-{depth.get("range", {}).get("bottom_depth")}cm'
            if label not in SOIL_DEPTHS:
                continue
            vals = depth.get("values", {})
            row = {
                "site": site,
                "property": prop,
                "depth": label,
                "mapped_units": unit.get("mapped_units"),
                "d_factor": factor,
                "target_units": unit.get("target_units"),
            }
            for key in SOIL_VALUES:
                raw = vals.get(key)
                row[key] = None if raw is None else raw / factor
            rows.append(row)
    return pd.DataFrame(rows)


def records(df):
    if df.empty:
        return []
    return json.loads(df.replace({np.nan: None}).to_json(orient="records", date_format="iso"))


def make_profile(soil_long):
    if soil_long.empty:
        return pd.DataFrame()
    tex = soil_long[soil_long["property"].isin(TEXTURE_PROPERTIES)].copy()
    parts = []
    for value_name in SOIL_VALUES:
        p = tex.pivot_table(index=["site", "depth"], columns="property", values=value_name, aggfunc="first").reset_index()
        p.columns.name = None
        p = p.rename(columns={prop: f"{prop}_{value_name}" for prop in TEXTURE_PROPERTIES})
        parts.append(p)
    profile = parts[0]
    for p in parts[1:]:
        profile = profile.merge(p, on=["site", "depth"], how="outer")
    order = {d: i for i, d in enumerate(SOIL_DEPTHS)}
    profile["depth_order"] = profile["depth"].map(order)
    return profile.sort_values(["site", "depth_order"]).drop(columns="depth_order")


@app.get("/api/health")
def health():
    return {"status": "ok", "version": "2.0.0", "enhancements": ["multi-depth soil", "Q0.05/Q0.95 uncertainty", "16-day forecast", "split frontend"]}


@app.get("/api/sites")
def sites():
    return SITES


@app.post("/api/integrate")
def integrate(body: RunRequest):
    selected = [SITE_MAP[s] for s in body.sites]
    ar = archive_req(selected, body.start_date, body.end_date)
    fr = forecast_req(selected)
    srs = [soil_req(x) for x in selected]
    logs = []

    try:
        archive = send(ar)
        logs.append({"api": ar["api"], "label": ar["label"], **{k: archive[k] for k in ["status", "from_cache", "elapsed_ms", "bytes"]}})

        # Forecast cache expires after one hour so future weather does not become stale.
        forecast = send(fr, ttl_seconds=3600)
        logs.append({"api": fr["api"], "label": fr["label"], **{k: forecast[k] for k in ["status", "from_cache", "elapsed_ms", "bytes"]}})

        soil_results = []
        for i, rq in enumerate(srs):
            sr = send(rq)
            soil_results.append(sr)
            logs.append({"api": "SoilGrids", "label": rq["label"], **{k: sr[k] for k in ["status", "from_cache", "elapsed_ms", "bytes"]}})
            if not sr["from_cache"] and i < len(srs) - 1:
                time.sleep(PAUSE)
    except requests.RequestException as e:
        raise HTTPException(502, f"Upstream API request failed: {e}")

    weather = parse_weather(archive["data"], selected, "historical")
    forecast_df = parse_weather(forecast["data"], selected, "forecast")
    longs = [parse_soil(sr["data"], x["site"]) for sr, x in zip(soil_results, selected)]
    soil_long = pd.concat(longs, ignore_index=True) if longs else pd.DataFrame()
    profile = make_profile(soil_long)

    # Preserve original Q1-Q6 behavior using the 0-5 cm mean as topsoil.
    top = soil_long[soil_long["depth"] == "0-5cm"] if not soil_long.empty else pd.DataFrame()
    if top.empty:
        wide = pd.DataFrame({"site": [x["site"] for x in selected]})
        for p in SOIL_PROPERTIES:
            wide[p] = np.nan
    else:
        wide = top.pivot(index="site", columns="property", values="mean").reindex(columns=SOIL_PROPERTIES).reset_index()
        wide.columns.name = None
    soil = pd.DataFrame(selected).merge(wide, on="site", how="left")

    daily = weather.merge(soil, on="site", how="left")
    if not daily.empty:
        daily["time"] = pd.to_datetime(daily["time"])
        daily["month"] = daily["time"].dt.month
        daily["water_balance_mm"] = daily["precipitation_sum"] - daily["et0_fao_evapotranspiration"]
        summary = (
            daily.groupby("site", sort=False)
            .agg(
                rain_mm=("precipitation_sum", "sum"),
                et0_mm=("et0_fao_evapotranspiration", "sum"),
                water_balance_mm=("water_balance_mm", "sum"),
                temp_mean_c=("temperature_2m_mean", "mean"),
            )
            .reset_index()
            .merge(soil, on="site")
        )
    else:
        summary = soil.copy()

    if not forecast_df.empty:
        forecast_df["time"] = pd.to_datetime(forecast_df["time"])
        forecast_df["water_balance_mm"] = forecast_df["precipitation_sum"] - forecast_df["et0_fao_evapotranspiration"]

    missing = soil.loc[soil.get("clay", pd.Series(dtype=float)).isna(), "site"].tolist() if "clay" in soil else [x["site"] for x in selected]
    request_list = [{"api": ar["api"], "label": ar["label"], "full_url": ar["full_url"]}, {"api": fr["api"], "label": fr["label"], "full_url": fr["full_url"]}]
    request_list += [{"api": x["api"], "label": x["label"], "full_url": x["full_url"]} for x in srs]

    return {
        "meta": {
            "start_date": str(body.start_date),
            "end_date": str(body.end_date),
            "daily_rows": len(daily),
            "summary_rows": len(summary),
            "forecast_rows": len(forecast_df),
            "profile_rows": len(profile),
            "missing_soil": missing,
            "soil_depths": SOIL_DEPTHS,
            "soil_values": SOIL_VALUES,
            "source_note": "Sources: SoilGrids 2.0 (ISRIC, CC BY 4.0), 0-5/5-15/15-30 cm with mean, Q0.05 and Q0.95; Open-Meteo Historical Weather API and 16-day Forecast API. SoilGrids values are modelled estimates and the Q0.05-Q0.95 interval represents model prediction uncertainty.",
        },
        "requests": request_list,
        "logs": logs,
        "soil_audit": records(soil_long),
        "soil_profile": records(profile),
        "daily": records(daily),
        "forecast": records(forecast_df),
        "summary": records(summary),
    }
