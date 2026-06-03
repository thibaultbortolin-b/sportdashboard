import httpx
import json
import os
from pathlib import Path
from datetime import datetime, timezone

TOKEN_FILE = Path(__file__).parent / ".strava_token.json"
STRAVA_BASE = "https://www.strava.com/api/v3"


def save_token(token_data: dict):
    TOKEN_FILE.write_text(json.dumps(token_data))


def load_token() -> dict | None:
    if TOKEN_FILE.exists():
        return json.loads(TOKEN_FILE.read_text())
    return None


def is_token_expired(token: dict) -> bool:
    return token.get("expires_at", 0) < datetime.now(timezone.utc).timestamp()


async def refresh_token(token: dict) -> dict:
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://www.strava.com/oauth/token",
            data={
                "client_id": os.environ["STRAVA_CLIENT_ID"],
                "client_secret": os.environ["STRAVA_CLIENT_SECRET"],
                "grant_type": "refresh_token",
                "refresh_token": token["refresh_token"],
            },
        )
        resp.raise_for_status()
        new_token = resp.json()
        save_token(new_token)
        return new_token


async def get_valid_token() -> dict | None:
    token = load_token()
    if not token:
        return None
    if is_token_expired(token):
        token = await refresh_token(token)
    return token


async def strava_get(path: str, params: dict = None) -> dict | list:
    token = await get_valid_token()
    if not token:
        raise RuntimeError("not_authenticated")
    headers = {"Authorization": f"Bearer {token['access_token']}"}
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{STRAVA_BASE}{path}", headers=headers, params=params or {})
        resp.raise_for_status()
        return resp.json()


async def get_athlete() -> dict:
    return await strava_get("/athlete")


async def get_activities(per_page: int = 100, page: int = 1) -> list:
    return await strava_get("/athlete/activities", {"per_page": per_page, "page": page})


async def get_activity_zones(activity_id: int) -> list:
    try:
        return await strava_get(f"/activities/{activity_id}/zones")
    except Exception:
        return []


async def get_activity_streams(activity_id: int) -> dict:
    try:
        data = await strava_get(
            f"/activities/{activity_id}/streams",
            {"keys": "heartrate,watts,velocity_smooth,altitude,cadence", "key_by_type": "true"},
        )
        return data
    except Exception:
        return {}


def seconds_to_hms(seconds: int) -> str:
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    if h:
        return f"{h}h{m:02d}m{s:02d}s"
    return f"{m}m{s:02d}s"


def meters_to_km(m: float) -> float:
    return round(m / 1000, 2)


def pace_per_km(distance_m: float, time_s: float) -> str:
    if distance_m <= 0 or time_s <= 0:
        return "-"
    pace_s = time_s / (distance_m / 1000)
    m = int(pace_s // 60)
    s = int(pace_s % 60)
    return f"{m}:{s:02d} /km"


def speed_to_pace(speed_mps: float) -> str:
    if speed_mps <= 0:
        return "-"
    return pace_per_km(1000, 1000 / speed_mps)


SPORT_ICONS = {
    "Run": "🏃",
    "Ride": "🚴",
    "Swim": "🏊",
    "Walk": "🚶",
    "Hike": "🥾",
    "VirtualRide": "🚴",
    "VirtualRun": "🏃",
    "WeightTraining": "🏋️",
    "Yoga": "🧘",
    "Workout": "💪",
}


def enrich_activity(a: dict) -> dict:
    sport = a.get("sport_type") or a.get("type", "")
    distance = a.get("distance", 0)
    moving_time = a.get("moving_time", 0)
    elapsed_time = a.get("elapsed_time", 0)
    elevation = a.get("total_elevation_gain", 0)
    avg_hr = a.get("average_heartrate")
    max_hr = a.get("max_heartrate")
    avg_speed = a.get("average_speed", 0)
    avg_watts = a.get("average_watts")
    suffer_score = a.get("suffer_score")
    kudos = a.get("kudos_count", 0)
    start_date = a.get("start_date_local", "")

    return {
        "id": a["id"],
        "name": a.get("name", "Activity"),
        "sport": sport,
        "icon": SPORT_ICONS.get(sport, "🏅"),
        "date": start_date[:10] if start_date else "",
        "datetime": start_date,
        "distance_km": meters_to_km(distance),
        "moving_time_fmt": seconds_to_hms(moving_time),
        "moving_time_s": moving_time,
        "elapsed_time_fmt": seconds_to_hms(elapsed_time),
        "elevation_m": round(elevation),
        "avg_hr": avg_hr,
        "max_hr": max_hr,
        "avg_pace": pace_per_km(distance, moving_time) if sport in ("Run", "VirtualRun", "Walk", "Hike") else None,
        "avg_speed_kmh": round(avg_speed * 3.6, 1),
        "avg_watts": avg_watts,
        "suffer_score": suffer_score,
        "kudos": kudos,
        "pr_count": a.get("pr_count", 0),
        "calories": a.get("calories") or a.get("kilojoules"),
    }
