import os
import json
from datetime import datetime, timedelta
from collections import defaultdict

from dotenv import load_dotenv
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

import strava as sv

load_dotenv()

app = FastAPI()
app.add_middleware(SessionMiddleware, secret_key=os.environ.get("SECRET_KEY", "dev-secret"))
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

STRAVA_AUTH_URL = "https://www.strava.com/oauth/authorize"
STRAVA_TOKEN_URL = "https://www.strava.com/oauth/token"
REDIRECT_URI = "http://localhost:8000/callback"
SCOPE = "read,activity:read_all"


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    token = sv.load_token()
    if not token:
        return templates.TemplateResponse("login.html", {"request": request})
    return RedirectResponse("/dashboard")


@app.get("/auth")
async def auth():
    client_id = os.environ["STRAVA_CLIENT_ID"]
    url = (
        f"{STRAVA_AUTH_URL}?client_id={client_id}"
        f"&redirect_uri={REDIRECT_URI}"
        f"&response_type=code&scope={SCOPE}"
    )
    return RedirectResponse(url)


@app.get("/callback")
async def callback(code: str = None, error: str = None):
    if error or not code:
        return RedirectResponse("/?error=denied")
    import httpx
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            STRAVA_TOKEN_URL,
            data={
                "client_id": os.environ["STRAVA_CLIENT_ID"],
                "client_secret": os.environ["STRAVA_CLIENT_SECRET"],
                "code": code,
                "grant_type": "authorization_code",
            },
        )
        resp.raise_for_status()
        sv.save_token(resp.json())
    return RedirectResponse("/dashboard")


@app.get("/logout")
async def logout():
    if sv.TOKEN_FILE.exists():
        sv.TOKEN_FILE.unlink()
    return RedirectResponse("/")


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    token = sv.load_token()
    if not token:
        return RedirectResponse("/")
    try:
        athlete = await sv.get_athlete()
    except Exception:
        return RedirectResponse("/logout")
    return templates.TemplateResponse("dashboard.html", {"request": request, "athlete": athlete})


# ── API endpoints ──────────────────────────────────────────────────────────────

@app.get("/api/activities")
async def api_activities(weeks: int = 12):
    after = int((datetime.utcnow() - timedelta(weeks=weeks)).timestamp())
    activities = await sv.get_activities(per_page=100)
    # filter by date
    activities = [a for a in activities if a.get("start_date", "") >=
                  (datetime.utcnow() - timedelta(weeks=weeks)).strftime("%Y-%m-%d")]
    enriched = [sv.enrich_activity(a) for a in activities]
    return JSONResponse(enriched)


@app.get("/api/stats")
async def api_stats():
    activities = await sv.get_activities(per_page=200)
    enriched = [sv.enrich_activity(a) for a in activities]

    now = datetime.utcnow()
    week_start = now - timedelta(days=now.weekday())
    month_start = now.replace(day=1)
    year_start = now.replace(month=1, day=1)
    prev_week_start = week_start - timedelta(weeks=1)
    prev_month_start = (month_start - timedelta(days=1)).replace(day=1)

    def period_stats(acts):
        runs = [a for a in acts if a["sport"] in ("Run", "VirtualRun")]
        return {
            "count": len(acts),
            "run_count": len(runs),
            "distance_km": round(sum(a["distance_km"] for a in acts), 1),
            "run_distance_km": round(sum(a["distance_km"] for a in runs), 1),
            "moving_time_s": sum(a["moving_time_s"] for a in acts),
            "elevation_m": sum(a["elevation_m"] for a in acts),
        }

    def filter_period(start, end=None):
        s = start.strftime("%Y-%m-%d")
        e = (end or now).strftime("%Y-%m-%d")
        return [a for a in enriched if s <= a["date"] <= e]

    # weekly distance for last 12 weeks
    weekly = []
    for i in range(11, -1, -1):
        ws = now - timedelta(weeks=i, days=now.weekday())
        we = ws + timedelta(days=6)
        acts = filter_period(ws, we)
        runs = [a for a in acts if a["sport"] in ("Run", "VirtualRun")]
        weekly.append({
            "week": ws.strftime("%b %d"),
            "distance_km": round(sum(a["distance_km"] for a in runs), 1),
            "all_distance_km": round(sum(a["distance_km"] for a in acts), 1),
            "count": len(acts),
        })

    # sport breakdown
    sport_counts = defaultdict(lambda: {"count": 0, "distance_km": 0})
    for a in enriched:
        sport_counts[a["sport"]]["count"] += 1
        sport_counts[a["sport"]]["distance_km"] += a["distance_km"]

    # HR zone aggregation (from recent runs with HR data)
    hr_runs = [a for a in enriched[:50] if a["avg_hr"] and a["sport"] in ("Run", "VirtualRun")]
    zone_seconds = [0, 0, 0, 0, 0]
    # approximate from avg HR: bucket by % of max HR (assume 190 max)
    max_hr_assumed = 190
    for a in hr_runs:
        hr = a["avg_hr"]
        pct = hr / max_hr_assumed
        t = a["moving_time_s"]
        if pct < 0.60:
            zone_seconds[0] += t
        elif pct < 0.70:
            zone_seconds[1] += t
        elif pct < 0.80:
            zone_seconds[2] += t
        elif pct < 0.90:
            zone_seconds[3] += t
        else:
            zone_seconds[4] += t

    # recent pace trend (last 20 runs)
    recent_runs = [a for a in enriched if a["sport"] in ("Run", "VirtualRun") and a["distance_km"] > 0][:20]
    pace_trend = []
    for a in reversed(recent_runs):
        if a["moving_time_s"] and a["distance_km"]:
            pace_s = a["moving_time_s"] / a["distance_km"]
            pace_trend.append({"date": a["date"], "pace_s": round(pace_s), "name": a["name"]})

    return JSONResponse({
        "this_week": period_stats(filter_period(week_start)),
        "last_week": period_stats(filter_period(prev_week_start, week_start - timedelta(days=1))),
        "this_month": period_stats(filter_period(month_start)),
        "last_month": period_stats(filter_period(prev_month_start, month_start - timedelta(days=1))),
        "this_year": period_stats(filter_period(year_start)),
        "weekly_chart": weekly,
        "sport_breakdown": dict(sport_counts),
        "hr_zones": zone_seconds,
        "pace_trend": pace_trend,
    })


@app.get("/api/activity/{activity_id}")
async def api_activity_detail(activity_id: int):
    zones = await sv.get_activity_zones(activity_id)
    return JSONResponse({"zones": zones})
