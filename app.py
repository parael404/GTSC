from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import os
import secrets
import smtplib
import ssl
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from email.message import EmailMessage
from functools import wraps
from io import BytesIO
from xml.sax.saxutils import escape

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from flask import Flask, Response, abort, jsonify, redirect, render_template, request, session, url_for
from flask_socketio import SocketIO, join_room
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from werkzeug.security import check_password_hash, generate_password_hash

from connection import bootstrap_db, get_db


app = Flask(__name__)
IS_PRODUCTION = os.environ.get("FLASK_ENV") == "production" or os.environ.get("APP_ENV") == "production"
SECRET_KEY = os.environ.get("SECRET_KEY") or os.environ.get("FLASK_SECRET_KEY")
if IS_PRODUCTION and not SECRET_KEY:
    raise RuntimeError("Set SECRET_KEY before running in production.")
app.secret_key = SECRET_KEY or "codexmbs-local-development-secret"
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=IS_PRODUCTION,
)
socketio = SocketIO(app, async_mode="threading")

PROTECTED_PATH_PREFIXES = (
    "/admin",
    "/super-admin",
    "/dashboard",
    "/tracker-device",
    "/start_trip",
    "/end_trip",
    "/driver",
    "/conductor",
    "/api/admin",
    "/api/conductor",
)

CSRF_SESSION_KEY = "_csrf_token"
LIVE_TRACKING_ROOM = "live_tracking"
ADMIN_LIVE_ROOM = "admin_live"
PASSWORD_RESET_NOTIFICATION_TYPE = "password_reset"
PASSWORD_RESET_LOG_ACTION = "Password Reset Requested"
PASSWORD_RESET_TOKEN_MINUTES = 30
ADMIN_OPERATION_NOTIFICATION_TYPES = {"trip_started", "trip_ended", "high_crowd", "bus_full"}

DEFAULT_BUS_CAPACITY = 30
STOP_PASS_RADIUS_KM = 0.85
AUTO_OFFBOARD_BOARDING_GRACE_SECONDS = 90
CAMERA_STREAM_TYPES = {"hls", "mjpeg", "embed", "external", "webrtc", "rtsp_gateway"}
CAMERA_STATUSES = {"online", "offline", "maintenance", "unconfigured"}
DEFAULT_BUS_CAMERAS = [
    ("Front Road", "Forward-facing road and route visibility camera"),
    ("Passenger Cabin", "Interior passenger and occupancy monitoring camera"),
    ("Rear Door", "Rear doorway and boarding safety camera"),
]
MAP_FALLBACK_COORDS = [15.4865, 120.9667]
ROUTE_STOPS = {
    "Gajoda Terminal to Cabanatuan": [
        ("Gajoda Garage and Office", 15.284472, 120.938750),
        ("San Isidro Town Proper", 15.3295, 120.9392),
        ("Cabanatuan Terminal", 15.4865, 120.9667),
    ],
    "Cabanatuan to Gajoda Terminal": [
        ("Cabanatuan Terminal", 15.4865, 120.9667),
        ("San Isidro Town Proper", 15.3295, 120.9392),
        ("Gajoda Garage and Office", 15.284472, 120.938750),
    ],
}

DISCOUNTED_PASSENGER_TYPES = {"student", "pwd", "senior"}
ROUTE_STOP_DETAILS = {
    "Gajoda Terminal to Cabanatuan": [
        {"name": "Gajoda Garage and Office", "lat": 15.284472, "lng": 120.938750, "minutes_from_start": 0, "landmark": "Gajoda terminal, garage, and office"},
        {"name": "San Isidro Town Proper", "lat": 15.3295, "lng": 120.9392, "minutes_from_start": 10, "landmark": "Corridor stop"},
        {"name": "Cabanatuan Terminal", "lat": 15.4865, "lng": 120.9667, "minutes_from_start": 48, "landmark": "Cabanatuan terminal"},
    ],
    "Cabanatuan to Gajoda Terminal": [
        {"name": "Cabanatuan Terminal", "lat": 15.4865, "lng": 120.9667, "minutes_from_start": 0, "landmark": "Cabanatuan terminal"},
        {"name": "San Isidro Town Proper", "lat": 15.3295, "lng": 120.9392, "minutes_from_start": 38, "landmark": "Corridor stop"},
        {"name": "Gajoda Garage and Office", "lat": 15.284472, "lng": 120.938750, "minutes_from_start": 48, "landmark": "Gajoda terminal, garage, and office"},
    ],
}
ROUTE_STOPS = {
    route_name: [(stop["name"], stop["lat"], stop["lng"]) for stop in stops]
    for route_name, stops in ROUTE_STOP_DETAILS.items()
}


def get_csrf_token():
    """Return the per-session CSRF token used by forms and fetch requests."""
    token = session.get(CSRF_SESSION_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        session[CSRF_SESSION_KEY] = token
    return token


def validate_csrf_token():
    """Reject unsafe requests that do not include the current CSRF token."""
    expected = session.get(CSRF_SESSION_KEY)
    submitted = (
        request.form.get("csrf_token")
        or request.headers.get("X-CSRFToken")
        or request.headers.get("X-CSRF-Token")
    )
    if not expected or not submitted or not secrets.compare_digest(expected, submitted):
        if request.accept_mimetypes.accept_json and not request.accept_mimetypes.accept_html:
            return jsonify({"success": False, "error": "Invalid CSRF token."}), 400
        abort(400)
    return None


@app.context_processor
def inject_csrf_token():
    """Expose CSRF helpers to templates."""
    return {"csrf_token": get_csrf_token}


@app.before_request
def protect_unsafe_requests():
    """Require CSRF tokens for state-changing browser requests."""
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        return validate_csrf_token()
    return None

FORWARD_ROUTE_NAME = "Gajoda Terminal to Cabanatuan"
REVERSE_ROUTE_NAME = "Cabanatuan to Gajoda Terminal"
CORRIDOR_TRAVEL_MINUTES = 48
CORRIDOR_DISTANCE_KM = 25.0
CORRIDOR_START = (15.284472, 120.938750)
CORRIDOR_END = (15.4865, 120.9667)
CORRIDOR_STOP_DETAILS = [
    {"name": "Gajoda Garage and Office", "lat": 15.284472, "lng": 120.938750, "minutes_from_start": 0, "landmark": "Gajoda terminal, garage, and office"},
    {"name": "San Isidro Town Proper", "lat": 15.3295, "lng": 120.9392, "minutes_from_start": 10},
    {"name": "Malapit, San Isidro", "lat": 15.3335, "lng": 120.9426, "minutes_from_start": 12},
    {"name": "San Isidro Border", "lat": 15.3368, "lng": 120.9450, "minutes_from_start": 13},
    {"name": "San Nicolas, Brgy. Chalmers", "lat": 15.3408, "lng": 120.9472, "minutes_from_start": 14},
    {"name": "Sto. Nino, Gapan", "lat": 15.3458, "lng": 120.9488, "minutes_from_start": 16},
    {"name": "San Leonardo Welcome", "lat": 15.3498, "lng": 120.9498, "minutes_from_start": 18},
    {"name": "Castellano, DGDLH", "lat": 15.3588, "lng": 120.9538, "minutes_from_start": 20},
    {"name": "Northview Heights", "lat": 15.3648, "lng": 120.9562, "minutes_from_start": 22},
    {"name": "Jaen Diversion", "lat": 15.3685, "lng": 120.9560, "minutes_from_start": 24},
    {"name": "NEECO II - Area 2", "lat": 15.3755, "lng": 120.9575, "minutes_from_start": 26},
    {"name": "San Leonardo Rice Mill", "lat": 15.3830, "lng": 120.9592, "minutes_from_start": 28},
    {"name": "V. Del Rosario Rice Mill", "lat": 15.3905, "lng": 120.9607, "minutes_from_start": 30},
    {"name": "Eco Energy Fuel Stop", "lat": 15.3980, "lng": 120.9620, "minutes_from_start": 32},
    {"name": "Tabuating Magnolia", "lat": 15.4060, "lng": 120.9630, "minutes_from_start": 34},
    {"name": "Fuel Star Sta. Rosa", "lat": 15.4140, "lng": 120.9640, "minutes_from_start": 36},
    {"name": "San Mariano", "lat": 15.4230, "lng": 120.9648, "minutes_from_start": 38},
    {"name": "Sta. Rosa Newstar", "lat": 15.4320, "lng": 120.9654, "minutes_from_start": 40},
    {"name": "San Gregorio", "lat": 15.4410, "lng": 120.9659, "minutes_from_start": 42},
    {"name": "NEUST Sumacab", "lat": 15.4500, "lng": 120.9662, "minutes_from_start": 44},
    {"name": "NE Pacific", "lat": 15.4600, "lng": 120.9664, "minutes_from_start": 46},
    {"name": "Lamarang", "lat": 15.4730, "lng": 120.9665, "minutes_from_start": 47},
    {"name": "Cabanatuan Terminal", "lat": 15.4865, "lng": 120.9667, "minutes_from_start": 48, "landmark": "Cabanatuan terminal"},
]
CORRIDOR_STOP_NAMES = [stop["name"] for stop in CORRIDOR_STOP_DETAILS]


# Build the reverse route stop list by flipping stop order and travel minutes.
def build_reverse_stop_details(stop_details, total_minutes):
    """Build the reverse route stop list by flipping stop order and travel minutes."""
    reversed_details = []
    for index, stop in enumerate(reversed(stop_details), start=1):
        reversed_details.append(
            {
                "name": stop["name"],
                "lat": stop["lat"],
                "lng": stop["lng"],
                "minutes_from_start": total_minutes - int(stop["minutes_from_start"]),
                "landmark": stop.get("landmark") or "Gajoda-Cabanatuan corridor stop",
            }
        )
    reversed_details.sort(key=lambda item: item["minutes_from_start"])
    return reversed_details


FORWARD_ROUTE_STOPS = [
    {
        "name": stop["name"],
        "lat": stop["lat"],
        "lng": stop["lng"],
        "minutes_from_start": stop["minutes_from_start"],
        "landmark": stop.get("landmark") or "Gajoda-Cabanatuan corridor stop",
    }
    for stop in CORRIDOR_STOP_DETAILS
]
REVERSE_ROUTE_STOPS = build_reverse_stop_details(CORRIDOR_STOP_DETAILS, CORRIDOR_TRAVEL_MINUTES)
ROUTE_STOP_DETAILS = {
    FORWARD_ROUTE_NAME: FORWARD_ROUTE_STOPS,
    REVERSE_ROUTE_NAME: REVERSE_ROUTE_STOPS,
}
ROUTE_STOPS = {
    route_name: [(stop["name"], stop["lat"], stop["lng"]) for stop in stops]
    for route_name, stops in ROUTE_STOP_DETAILS.items()
}


# Return the current local datetime used for trip, GPS, and log timestamps.
def now():
    """Return the current local datetime used for trip, GPS, and log timestamps."""
    return datetime.now()


# Convert a datetime value into the database timestamp string format.
def to_db_time(value: datetime | None):
    """Convert a datetime value into the database timestamp string format."""
    return value.strftime("%Y-%m-%d %H:%M:%S") if value else None


# Convert a database datetime value back into a Python datetime object.
def from_db_time(value):
    """Convert a database datetime value back into a Python datetime object."""
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")


def parse_iso_date(value):
    """Parse a YYYY-MM-DD date string or return None."""
    try:
        return datetime.strptime(str(value or "").strip(), "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def resolve_report_date_range(start_value=None, end_value=None):
    """Normalize optional report date filters into ordered ISO date strings."""
    start_date = parse_iso_date(start_value)
    end_date = parse_iso_date(end_value)
    if start_date and end_date and start_date > end_date:
        start_date, end_date = end_date, start_date
    return {
        "start": start_date.isoformat() if start_date else "",
        "end": end_date.isoformat() if end_date else "",
        "is_filtered": bool(start_date or end_date),
    }


# Classify bus occupancy as Low, Medium, or High based on capacity ratio.
def classify_capacity(occupancy, capacity=DEFAULT_BUS_CAPACITY):
    """Classify bus occupancy as Low, Medium, or High based on capacity ratio."""
    safe_capacity = max(capacity or DEFAULT_BUS_CAPACITY, 1)
    ratio = occupancy / safe_capacity

    if ratio <= 0.4:
        return "Low"
    if ratio <= 0.75:
        return "Medium"
    return "High"


# Return occupancy count, capacity limit, percent full, and crowd label.
def capacity_details(occupancy, capacity=DEFAULT_BUS_CAPACITY):
    """Return occupancy count, capacity limit, percent full, and crowd label."""
    safe_capacity = max(capacity or DEFAULT_BUS_CAPACITY, 1)
    percent = round((occupancy / safe_capacity) * 100)
    return {
        "limit": safe_capacity,
        "count": occupancy,
        "percent": max(0, percent),
        "label": classify_capacity(occupancy, safe_capacity),
    }


# Safely parse form/API input into a non-negative integer.
def to_non_negative_int(value, default=0):
    """Safely parse form/API input into a non-negative integer."""
    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        return default


# Parse stored route coordinate JSON into Leaflet-ready latitude/longitude pairs.
def parse_route_coords(coords_json):
    """Parse stored route coordinate JSON into Leaflet-ready latitude/longitude pairs."""
    if isinstance(coords_json, bytes):
        coords_json = coords_json.decode("utf-8")
    if isinstance(coords_json, (list, tuple)):
        coords = coords_json
    else:
        try:
            coords = json.loads(coords_json or "[]")
        except (TypeError, json.JSONDecodeError):
            return []
    if not isinstance(coords, list):
        return []

    normalized = []
    for pair in coords:
        if isinstance(pair, (list, tuple)) and len(pair) >= 2:
            try:
                normalized.append([float(pair[0]), float(pair[1])])
            except (TypeError, ValueError):
                continue
    return normalized


# Convert database values such as Decimal and datetime into JSON-safe values.
def normalize_json_value(value):
    """Convert database values such as Decimal and datetime into JSON-safe values."""
    if isinstance(value, dict):
        return {key: normalize_json_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [normalize_json_value(item) for item in value]
    if isinstance(value, tuple):
        return [normalize_json_value(item) for item in value]
    if isinstance(value, bytes):
        return value.decode("utf-8")
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, Decimal):
        return float(value)
    return value


# Create a simple lowercase identifier from a display label.
def slugify_label(value):
    """Create a simple lowercase identifier from a display label."""
    return "".join(char.lower() if char.isalnum() else "-" for char in (value or "")).strip("-")


# Return cached stop details for a route name.
def get_route_stop_details(route_name):
    """Return cached stop details for a route name."""
    return [dict(stop, sequence=index + 1) for index, stop in enumerate(ROUTE_STOP_DETAILS.get(route_name, []))]


# Rebuild in-memory route stop lookup data from database rows.
def rebuild_route_stop_cache(stop_rows):
    """Rebuild in-memory route stop lookup data from database rows."""
    route_stop_details = {}
    for row in stop_rows:
        route_stop_details.setdefault(row["route_name"], []).append(
            {
                "name": row["stop_name"],
                "lat": float(row["latitude"]),
                "lng": float(row["longitude"]),
                "minutes_from_start": int(row["minutes_from_start"] or 0),
                "landmark": row.get("landmark") or "Corridor stop",
            }
        )

    for route_name, stops in route_stop_details.items():
        stops.sort(key=lambda stop: stop["minutes_from_start"])

    route_stops = {
        route_name: [(stop["name"], stop["lat"], stop["lng"]) for stop in stops]
        for route_name, stops in route_stop_details.items()
    }
    return route_stop_details, route_stops


# Load published route stops from the database into the route stop cache.
def refresh_route_stop_cache(conn):
    """Load published route stops from the database into the route stop cache."""
    global ROUTE_STOP_DETAILS, ROUTE_STOPS
    stop_rows = conn.execute(
        """
        SELECT r.route_name,
               s.stop_name,
               s.latitude,
               s.longitude,
               s.landmark,
               rs.stop_sequence,
               rs.minutes_from_start
        FROM route_stops rs
        JOIN routes r ON r.id = rs.route_id
        JOIN stops s ON s.id = rs.stop_id
        WHERE s.is_active = 1
        ORDER BY r.display_order, r.route_name, rs.stop_sequence
        """
    ).fetchall()
    if not stop_rows:
        return
    route_stop_details, route_stops = rebuild_route_stop_cache(stop_rows)
    if route_stop_details:
        ROUTE_STOP_DETAILS = route_stop_details
        ROUTE_STOPS = route_stops


# Normalize stop names for case-insensitive comparisons.
def stop_name_key(value):
    """Normalize stop names for case-insensitive comparisons."""
    return " ".join((value or "").lower().split())


# Safely parse a value into a float with a fallback default.
def to_float(value, default=0.0):
    """Safely parse a value into a float with a fallback default."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def parse_gps_coordinates(latitude, longitude):
    """Validate browser GPS coordinates before writing them to the trip log."""
    try:
        lat = float(latitude)
        lng = float(longitude)
    except (TypeError, ValueError):
        return None, None, "Latitude and longitude must be valid numbers."

    if not math.isfinite(lat) or not math.isfinite(lng):
        return None, None, "Latitude and longitude must be finite numbers."
    if lat < -90 or lat > 90 or lng < -180 or lng > 180:
        return None, None, "GPS coordinates are outside the valid latitude/longitude range."
    return lat, lng, None


# Safely parse fare values into Decimal for money calculations.
def to_decimal(value, default="0.00"):
    """Safely parse fare values into Decimal for money calculations."""
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal(default)


# Round Decimal money values to two decimal places.
def quantize_money(value):
    """Round Decimal money values to two decimal places."""
    return to_decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


# Calculate straight-line distance in kilometers between two GPS points.
def distance_between_points_km(lat_a, lng_a, lat_b, lng_b):
    """Calculate straight-line distance in kilometers between two GPS points."""
    latitude_a = math.radians(float(lat_a))
    longitude_a = math.radians(float(lng_a))
    latitude_b = math.radians(float(lat_b))
    longitude_b = math.radians(float(lng_b))
    delta_latitude = latitude_b - latitude_a
    delta_longitude = longitude_b - longitude_a
    haversine = (
        math.sin(delta_latitude / 2) ** 2
        + math.cos(latitude_a) * math.cos(latitude_b) * math.sin(delta_longitude / 2) ** 2
    )
    angular_distance = 2 * math.atan2(math.sqrt(haversine), math.sqrt(1 - haversine))
    return 6371 * angular_distance


# Round a fare value to the nearest whole peso.
def round_peso(value):
    """Round a fare value to the nearest whole peso."""
    return to_decimal(value).quantize(Decimal("1"), rounding=ROUND_HALF_UP)


# Estimate distance between two route stops using timing or stop sequence.
def estimate_segment_distance(route_distance_km, route_duration_minutes, origin_stop, destination_stop, stop_count):
    """Estimate distance between two route stops using timing or stop sequence."""
    numeric_distance_km = to_float(route_distance_km, 0.0)
    if route_duration_minutes and destination_stop["minutes_from_start"] >= origin_stop["minutes_from_start"]:
        covered_minutes = destination_stop["minutes_from_start"] - origin_stop["minutes_from_start"]
        return round(numeric_distance_km * (covered_minutes / route_duration_minutes), 1)

    index_gap = max(destination_stop["sequence"] - origin_stop["sequence"], 0)
    denominator = max(stop_count - 1, 1)
    return round(numeric_distance_km * (index_gap / denominator), 1)


# Build regular and discounted fare estimates for a route segment.
def estimate_fare_table(distance_km, minimum_fare=15, discounted_fare=None):
    """Build regular and discounted fare estimates for a route segment."""
    base_regular = max(round_peso(minimum_fare), Decimal("1"))
    numeric_distance_km = to_decimal(distance_km)
    extra_distance = max(numeric_distance_km - Decimal("4.00"), Decimal("0.00"))
    regular = max(round_peso(base_regular + (extra_distance * Decimal("1.35"))), base_regular)
    default_discounted = max(round_peso(base_regular * Decimal("0.8")), Decimal("1"))
    base_discounted = max(round_peso(discounted_fare if discounted_fare is not None else default_discounted), Decimal("1"))
    discounted_total = max(round_peso(base_discounted + (extra_distance * Decimal("1.10"))), base_discounted)
    fare_table = {"regular": float(regular)}
    for passenger_type in DISCOUNTED_PASSENGER_TYPES:
        fare_table[passenger_type] = float(discounted_total)
    return fare_table


# Calculate total fare for a passenger type and passenger quantity.
def calculate_passenger_fare_total(passenger_type, quantity, distance_km=0, minimum_fare=15, discounted_fare=None):
    """Calculate total fare for a passenger type and passenger quantity."""
    fare_table = estimate_fare_table(distance_km, minimum_fare, discounted_fare)
    unit_fare = round_peso(fare_table.get(passenger_type, fare_table["regular"]))
    return float(round_peso(unit_fare * max(int(quantity or 0), 0)))


# Find the sequence index of a stop inside a route stop list.
def find_stop_index(route_stops, stop_name):
    """Find the sequence index of a stop inside a route stop list."""
    keyed_name = stop_name_key(stop_name)
    for index, stop in enumerate(route_stops):
        if stop_name_key(stop["name"]) == keyed_name:
            return index
    return -1


# Estimate which stop a live bus is nearest to based on GPS and stop labels.
def infer_bus_stop_index(bus, route_stops):
    """Estimate which stop a live bus is nearest to based on GPS and stop labels."""
    next_stop_index = find_stop_index(route_stops, bus.get("nextStop"))
    if next_stop_index >= 0:
        return next_stop_index

    lat = bus.get("lat")
    lng = bus.get("lng")
    if lat is None or lng is None:
        return -1

    nearest_index = -1
    nearest_score = None
    for index, stop in enumerate(route_stops):
        score = distance_between_points_km(float(lat), float(lng), stop["lat"], stop["lng"])
        if nearest_score is None or score < nearest_score:
            nearest_score = score
            nearest_index = index
    return nearest_index


# Estimate minutes until a bus reaches a requested stop.
def estimate_bus_arrival_minutes(bus, route_stops, target_stop_name):
    """Estimate minutes until a bus reaches a requested stop."""
    target_index = find_stop_index(route_stops, target_stop_name)
    current_index = infer_bus_stop_index(bus, route_stops)
    if target_index < 0 or current_index < 0 or current_index > target_index:
        return None

    current_stop = route_stops[current_index]
    target_stop = route_stops[target_index]
    return max(target_stop["minutes_from_start"] - current_stop["minutes_from_start"], 0)


# Determine the current route stop for a trip from GPS or fallback record data.
def get_trip_current_stop_details(trip, latest_gps=None, fallback_stop_name=None):
    """Determine the current route stop for a trip from GPS or fallback record data."""
    route_stops = get_route_stop_details(trip.get("route_name"))
    if not route_stops:
        return None

    if latest_gps and latest_gps.get("latitude") is not None and latest_gps.get("longitude") is not None:
        latitude = float(latest_gps["latitude"])
        longitude = float(latest_gps["longitude"])
        nearest_stop = min(
            route_stops,
            key=lambda stop: distance_between_points_km(latitude, longitude, stop["lat"], stop["lng"]),
        )
        nearest_distance = distance_between_points_km(latitude, longitude, nearest_stop["lat"], nearest_stop["lng"])
        if nearest_distance <= STOP_PASS_RADIUS_KM:
            return nearest_stop
        if fallback_stop_name is None:
            return None

    fallback_index = find_stop_index(route_stops, fallback_stop_name)
    if fallback_index >= 0:
        return route_stops[fallback_index]
    return route_stops[0]


# Return valid downstream destination stops for ticketing from the current stop.
def get_trip_destination_options(trip, current_stop_name=None):
    """Return valid downstream destination stops for ticketing from the current stop."""
    route_stops = get_route_stop_details(trip.get("route_name"))
    current_index = find_stop_index(route_stops, current_stop_name)
    if current_index < 0:
        current_index = 0
    return route_stops[current_index + 1 :]


# Return true when the origin and destination are ordered stops for this trip.
def is_valid_trip_segment(trip, origin_stop_name, destination_stop_name):
    """Return true when the origin and destination are ordered stops for this trip."""
    route_stops = get_route_stop_details(trip.get("route_name"))
    origin_index = find_stop_index(route_stops, origin_stop_name)
    destination_index = find_stop_index(route_stops, destination_stop_name)
    return origin_index >= 0 and destination_index > origin_index


# Return true when an automatic offboard should ignore a fresh boarding record.
def is_recent_boarding(recorded_at, reference_time=None):
    """Return true when an automatic offboard should ignore a fresh boarding record."""
    boarded_at = from_db_time(recorded_at)
    if not boarded_at:
        return False
    current_time = reference_time or now()
    age_seconds = (current_time - boarded_at).total_seconds()
    return abs(age_seconds) < AUTO_OFFBOARD_BOARDING_GRACE_SECONDS


# Estimate trip segment distance between selected origin and destination stops.
def estimate_trip_segment_distance(trip, origin_stop_name, destination_stop_name):
    """Estimate trip segment distance between selected origin and destination stops."""
    route_stops = get_route_stop_details(trip.get("route_name"))
    origin_index = find_stop_index(route_stops, origin_stop_name)
    destination_index = find_stop_index(route_stops, destination_stop_name)
    if origin_index < 0 or destination_index < 0 or destination_index <= origin_index:
        return 0.0
    return estimate_segment_distance(
        trip.get("distance_km"),
        trip.get("expected_duration_minutes"),
        route_stops[origin_index],
        route_stops[destination_index],
        len(route_stops),
    )


# Calculate conductor ticket fare for a trip segment and passenger type.
def calculate_segment_fare_total(trip, passenger_type, quantity, origin_stop_name, destination_stop_name):
    """Calculate conductor ticket fare for a trip segment and passenger type."""
    segment_distance = estimate_trip_segment_distance(trip, origin_stop_name, destination_stop_name)
    return calculate_passenger_fare_total(
        passenger_type,
        quantity,
        segment_distance,
        trip.get("minimum_fare"),
        trip.get("discounted_fare"),
    )


# Return an admin-configured fare matrix row for an exact trip segment.
def get_fare_matrix_entry(conn, route_id, origin_stop_name, destination_stop_name):
    """Return an admin-configured fare matrix row for an exact trip segment."""
    if not route_id or not origin_stop_name or not destination_stop_name:
        return None
    return conn.execute(
        """
        SELECT id, route_id, origin_stop, destination_stop, regular_fare, discounted_fare
        FROM fare_matrix
        WHERE route_id = ?
          AND origin_stop = ?
          AND destination_stop = ?
        LIMIT 1
        """,
        (route_id, origin_stop_name, destination_stop_name),
    ).fetchone()


# Convert a fare matrix row into the passenger-type fare table used by the conductor UI.
def build_matrix_fare_table(matrix_row):
    """Convert a fare matrix row into a passenger-type fare table."""
    regular = float(round_peso(matrix_row["regular_fare"]))
    discounted = float(round_peso(matrix_row["discounted_fare"]))
    fare_table = {"regular": regular}
    for passenger_type in DISCOUNTED_PASSENGER_TYPES:
        fare_table[passenger_type] = discounted
    return fare_table


# Build the fare guide for a trip segment, preferring the admin fare matrix when present.
def build_segment_fare_table(conn, trip, origin_stop_name, destination_stop_name):
    """Build the fare guide for a trip segment, preferring the admin fare matrix when present."""
    matrix_row = get_fare_matrix_entry(
        conn,
        trip.get("route_id"),
        origin_stop_name,
        destination_stop_name,
    )
    if matrix_row:
        return build_matrix_fare_table(matrix_row)
    return estimate_fare_table(
        estimate_trip_segment_distance(trip, origin_stop_name, destination_stop_name),
        trip.get("minimum_fare"),
        trip.get("discounted_fare"),
    )


# Calculate conductor ticket fare while honoring admin fare matrix overrides.
def calculate_trip_fare_total(conn, trip, passenger_type, quantity, origin_stop_name, destination_stop_name):
    """Calculate conductor ticket fare while honoring admin fare matrix overrides."""
    fare_table = build_segment_fare_table(conn, trip, origin_stop_name, destination_stop_name)
    unit_fare = round_peso(fare_table.get(passenger_type, fare_table["regular"]))
    return float(round_peso(unit_fare * max(int(quantity or 0), 0)))


# Group currently onboard passengers by destination for conductor monitoring.
def build_trip_destination_manifest(conn, trip, current_stop_name=None):
    """Group currently onboard passengers by destination for conductor monitoring."""
    route_stops = get_route_stop_details(trip.get("route_name"))
    current_index = find_stop_index(route_stops, current_stop_name)
    manifest = {}
    for row in conn.execute(
        """
        SELECT event_type, destination_stop, quantity, recorded_at
        FROM trip_transactions
        WHERE trip_id = ?
          AND destination_stop IS NOT NULL
          AND destination_stop <> ''
        ORDER BY recorded_at ASC, id ASC
        """,
        (trip["id"],),
    ).fetchall():
        destination_stop = row.get("destination_stop")
        if not destination_stop:
            continue
        destination_index = find_stop_index(route_stops, destination_stop)
        if current_index >= 0 and destination_index >= 0 and destination_index <= current_index:
            continue
        quantity = int(row.get("quantity") or 0)
        if row.get("event_type") == "drop":
            manifest[destination_stop] = max(manifest.get(destination_stop, 0) - quantity, 0)
        else:
            manifest[destination_stop] = manifest.get(destination_stop, 0) + quantity
    ordered_manifest = []
    for stop in route_stops:
        count = manifest.get(stop["name"], 0)
        if count > 0:
            ordered_manifest.append({"name": stop["name"], "count": count})
    return ordered_manifest


# Create drop-off records for passengers whose saved destination matches the current stop.
def auto_offboard_due_passengers(
    conn,
    trip,
    current_stop_name=None,
    latitude=None,
    longitude=None,
    conductor_id=None,
    allow_next_destination_fallback=False,
):
    """Create drop-off records for passengers whose saved destination matches the current stop."""
    route_stops = get_route_stop_details(trip.get("route_name"))
    current_index = find_stop_index(route_stops, current_stop_name)

    destination_balances = {}
    for row in conn.execute(
        """
        SELECT event_type, destination_stop, quantity
        FROM trip_transactions
        WHERE trip_id = ?
          AND destination_stop IS NOT NULL
          AND destination_stop <> ''
        ORDER BY recorded_at ASC, id ASC
        """,
        (trip["id"],),
    ).fetchall():
        destination_stop = row.get("destination_stop")
        destination_index = find_stop_index(route_stops, destination_stop)
        if destination_index < 0:
            continue
        bucket = destination_balances.setdefault(
            destination_stop,
            {
                "destination_stop": destination_stop,
                "sequence": destination_index + 1,
                "boarded": 0,
                "dropped": 0,
                "latest_boarded_at": None,
            },
        )
        quantity = int(row.get("quantity") or 0)
        if row.get("event_type") == "drop":
            bucket["dropped"] += quantity
        else:
            bucket["boarded"] += quantity
            bucket["latest_boarded_at"] = row.get("recorded_at")

    current_outstanding = sum(max(item["boarded"] - item["dropped"], 0) for item in destination_balances.values())
    has_recent_boarding = any(
        item["boarded"] > item["dropped"] and is_recent_boarding(item.get("latest_boarded_at"))
        for item in destination_balances.values()
    )
    if has_recent_boarding and not allow_next_destination_fallback:
        return {"dropped": 0, "remaining": current_outstanding}

    auto_due_candidates = [
        item
        for item in destination_balances.values()
        if item["boarded"] > item["dropped"]
    ]
    if current_index >= 0:
        due_destinations = [
            item
            for item in auto_due_candidates
            if item["sequence"] - 1 <= current_index and item["boarded"] > item["dropped"]
        ]
        if not due_destinations and allow_next_destination_fallback:
            due_destinations = [
                item
                for item in auto_due_candidates
                if item["sequence"] - 1 > current_index and item["boarded"] > item["dropped"]
            ]
    elif allow_next_destination_fallback:
        due_destinations = auto_due_candidates
    else:
        return {"dropped": 0, "remaining": current_outstanding}
    due_destinations.sort(key=lambda item: item["sequence"])
    if allow_next_destination_fallback and due_destinations and (
        current_index < 0 or due_destinations[0]["sequence"] - 1 > current_index
    ):
        due_destinations = due_destinations[:1]
    if not due_destinations:
        return {"dropped": 0, "remaining": current_outstanding}

    recorded_at = to_db_time(now())
    total_dropped = 0
    for item in due_destinations:
        quantity = item["boarded"] - item["dropped"]
        current_outstanding = max(current_outstanding - quantity, 0)
        total_dropped += quantity
        offboard_stop = current_stop_name or item["destination_stop"]
        conn.execute(
            """
            INSERT INTO trip_transactions (
                trip_id, conductor_id, event_type, passenger_type, quantity, fare_amount,
                stop_name, origin_stop, destination_stop, latitude, longitude, occupancy_after, recorded_at
            )
            VALUES (?, ?, 'drop', 'mixed', ?, NULL, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                trip["id"],
                conductor_id,
                quantity,
                offboard_stop,
                offboard_stop,
                item["destination_stop"],
                float(latitude) if latitude is not None else None,
                float(longitude) if longitude is not None else None,
                current_outstanding,
                recorded_at,
            ),
        )

    sync_stop_name = current_stop_name or (due_destinations[-1]["destination_stop"] if due_destinations else None)
    manifest = sync_trip_occupancy_from_destinations(conn, trip, sync_stop_name)
    remaining = sum(item["count"] for item in manifest)
    crowd_level = classify_capacity(remaining, trip.get("capacity"))
    conn.execute(
        """
        INSERT INTO trip_records (
            trip_id, students, pwd, senior, regular, boarded, dropped, total,
            occupancy_after, crowd_level, stop_name, latitude, longitude, recorded_at
        )
        VALUES (?, 0, 0, 0, 0, 0, ?, 0, ?, ?, ?, ?, ?, ?)
        """,
        (
            trip["id"],
            total_dropped,
            remaining,
            crowd_level,
            current_stop_name or "Route stop",
            float(latitude) if latitude is not None else None,
            float(longitude) if longitude is not None else None,
            recorded_at,
        ),
    )
    if conductor_id:
        log_event(
            conn,
            conductor_id,
            "conductor",
            "Auto Offboard",
            f"Trip #{trip['id']} automatically offboarded {total_dropped} passenger(s) at {current_stop_name}.",
        )
    return {"dropped": total_dropped, "remaining": remaining}


# Recalculate active trip occupancy from the destination manifest.
def sync_trip_occupancy_from_destinations(conn, trip, current_stop_name=None):
    """Recalculate active trip occupancy from the destination manifest."""
    manifest = build_trip_destination_manifest(conn, trip, current_stop_name)
    occupancy = sum(item["count"] for item in manifest)
    capacity = max(int(trip.get("capacity") or DEFAULT_BUS_CAPACITY), 1)
    peak = max(int(trip.get("peak_occupancy") or 0), occupancy)
    conn.execute(
        """
        UPDATE trips
        SET occupancy = ?, peak_occupancy = ?, average_load = ?
        WHERE id = ?
        """,
        (occupancy, peak, round((occupancy / capacity) * 100, 1), trip["id"]),
    )
    trip["occupancy"] = occupancy
    trip["peak_occupancy"] = peak
    return manifest


# Return currently active public service alerts with route context.
def archive_expired_service_alerts(conn):
    """Move expired service alerts out of public visibility and into archive state."""
    current_time = now()
    conn.execute(
        """
        UPDATE service_alerts
        SET is_active = 0,
            archived_at = ?
        WHERE archived_at IS NULL
          AND (
              expires_at <= ?
              OR (expires_at IS NULL AND DATE(created_at) < ?)
          )
        """,
        (to_db_time(current_time), to_db_time(current_time), current_time.date().isoformat()),
    )
    conn.commit()


def resolve_alert_expiration(duration_key):
    """Convert an admin alert duration option into an expiration timestamp."""
    current_time = now()
    if duration_key == "1h":
        return current_time + timedelta(hours=1)
    if duration_key == "4h":
        return current_time + timedelta(hours=4)
    if duration_key == "8h":
        return current_time + timedelta(hours=8)
    if duration_key == "24h":
        return current_time + timedelta(hours=24)
    if duration_key == "3d":
        return current_time + timedelta(days=3)
    return current_time.replace(hour=23, minute=59, second=59, microsecond=0)


def get_active_service_alerts(conn):
    """Return currently active public service alerts with route context."""
    archive_expired_service_alerts(conn)
    rows = conn.execute(
        """
        SELECT sa.id,
               sa.trip_id,
               sa.route_id,
               sa.stop_name,
               sa.title,
               sa.message,
               sa.severity,
               sa.created_at,
               sa.expires_at,
               r.route_name
        FROM service_alerts sa
        LEFT JOIN routes r ON r.id = sa.route_id
        WHERE sa.is_active = 1
          AND sa.archived_at IS NULL
          AND (sa.expires_at IS NULL OR sa.expires_at > ?)
        ORDER BY
            CASE sa.severity
                WHEN 'critical' THEN 0
                WHEN 'warning' THEN 1
                ELSE 2
            END,
            sa.created_at DESC,
            sa.id DESC
        """,
        (to_db_time(now()),),
    ).fetchall()
    return [dict(row) for row in rows]


def get_archived_service_alerts(conn, limit=30):
    """Return archived service alerts for admin review."""
    archive_expired_service_alerts(conn)
    rows = conn.execute(
        """
        SELECT sa.id,
               sa.trip_id,
               sa.route_id,
               sa.stop_name,
               sa.title,
               sa.message,
               sa.severity,
               sa.created_at,
               sa.expires_at,
               sa.archived_at,
               r.route_name
        FROM service_alerts sa
        LEFT JOIN routes r ON r.id = sa.route_id
        WHERE sa.archived_at IS NOT NULL
        ORDER BY sa.archived_at DESC, sa.id DESC
        LIMIT %s
        """,
        (limit,),
    ).fetchall()
    return [dict(row) for row in rows]


# Publish or clear the automatic service alert connected to an active trip.
def sync_trip_service_alert(conn, trip_id, is_active):
    """Publish or clear the automatic service alert connected to an active trip."""
    trip = conn.execute(
        """
        SELECT t.id, t.started_at, b.plate_number, r.id AS route_id, r.route_name, r.start_point, r.end_point
        FROM trips t
        JOIN buses b ON b.id = t.bus_id
        JOIN routes r ON r.id = t.route_id
        WHERE t.id = ?
        """,
        (trip_id,),
    ).fetchone()
    if not trip:
        return

    existing_alert = conn.execute(
        "SELECT id FROM service_alerts WHERE trip_id = ?",
        (trip_id,),
    ).fetchone()

    title = f"New trip active: {trip['plate_number']}"
    message = f"{trip['plate_number']} is now active on {trip['route_name']} from {trip['start_point']} to {trip['end_point']}."
    expires_at = to_db_time(resolve_alert_expiration("end_of_day"))

    if is_active:
        if existing_alert:
            conn.execute(
                """
                UPDATE service_alerts
                SET route_id = ?, stop_name = ?, title = ?, message = ?, severity = 'info', is_active = 1, created_at = ?, expires_at = ?, archived_at = NULL
                WHERE id = ?
                """,
                (trip["route_id"], trip["start_point"], title, message, to_db_time(now()), expires_at, existing_alert["id"]),
            )
        else:
            conn.execute(
                """
                INSERT INTO service_alerts (trip_id, route_id, stop_name, title, message, severity, is_active, created_by, created_at, expires_at)
                VALUES (?, ?, ?, ?, ?, 'info', 1, NULL, ?, ?)
                """,
                (trip_id, trip["route_id"], trip["start_point"], title, message, to_db_time(now()), expires_at),
            )
    elif existing_alert:
        conn.execute(
            """
            UPDATE service_alerts
            SET is_active = 0,
                archived_at = ?
            WHERE id = ?
            """,
            (to_db_time(now()), existing_alert["id"]),
        )


# Build public route, stop, fare, and alert data for commuter pages and APIs.
def build_public_commuter_data(conn, live_data=None):
    """Build public route, stop, fare, and alert data for commuter pages and APIs."""
    live_data = live_data or build_live_bus_data(conn)
    active_alerts = get_active_service_alerts(conn)
    route_rows = [
        dict(row)
        for row in conn.execute(
            """
            SELECT id, route_name, start_point, end_point, distance_km, expected_duration_minutes, coords_json,
                   is_published, minimum_fare, discounted_fare, display_order
            FROM routes
            WHERE is_published = 1
            ORDER BY display_order, route_name
            """
        ).fetchall()
    ]

    live_buses = [bus for bus in live_data["buses"] if bus["tripStatus"] == "active" and bus["isLiveTracked"]]
    stops_by_name = {}
    routes_payload = []

    for route in route_rows:
        stops = get_route_stop_details(route["route_name"])
        fare_guide = estimate_fare_table(route["distance_km"], route["minimum_fare"], route["discounted_fare"])
        route_live_buses = [bus for bus in live_buses if bus["direction"] == route["route_name"]]

        next_bus = None
        next_bus_minutes = None
        if stops and route_live_buses:
            for bus in route_live_buses:
                arrival_minutes = estimate_bus_arrival_minutes(bus, stops, stops[0]["name"])
                if arrival_minutes is None:
                    continue
                if next_bus_minutes is None or arrival_minutes < next_bus_minutes:
                    next_bus_minutes = arrival_minutes
                    next_bus = bus

        routes_payload.append(
            {
                "id": route["id"],
                "slug": slugify_label(route["route_name"]),
                "routeName": route["route_name"],
                "startPoint": route["start_point"],
                "endPoint": route["end_point"],
                "distanceKm": float(route["distance_km"] or 0),
                "expectedDurationMinutes": int(route["expected_duration_minutes"] or 0),
                "coords": parse_route_coords(route["coords_json"]),
                "stops": stops,
                "fareGuide": fare_guide,
                "minimumFare": to_float(route["minimum_fare"], 15.0),
                "discountedFare": to_float(route["discounted_fare"], 12.0),
                "availableBusCount": len(route_live_buses),
                "nextBus": {
                    "plateNumber": next_bus["id"],
                    "nextStop": next_bus["nextStop"],
                    "minutesToStart": next_bus_minutes,
                    "crowdLevel": next_bus["crowdLevel"],
                } if next_bus else None,
            }
        )

        for stop in stops:
            key = stop_name_key(stop["name"])
            directory_entry = stops_by_name.setdefault(
                key,
                {
                    "name": stop["name"],
                    "lat": stop["lat"],
                    "lng": stop["lng"],
                    "landmark": stop["landmark"],
                    "routes": [],
                    "nextArrivals": [],
                },
            )
            directory_entry["routes"].append(route["route_name"])

            for bus in route_live_buses:
                arrival_minutes = estimate_bus_arrival_minutes(bus, stops, stop["name"])
                if arrival_minutes is None:
                    continue
                directory_entry["nextArrivals"].append(
                    {
                        "routeName": route["route_name"],
                        "plateNumber": bus["id"],
                        "crowdLevel": bus["crowdLevel"],
                        "minutes": arrival_minutes,
                    }
                )

    stop_directory = []
    for stop in stops_by_name.values():
        next_arrivals = sorted(stop["nextArrivals"], key=lambda item: item["minutes"])[:3]
        stop_directory.append(
            {
                "name": stop["name"],
                "lat": stop["lat"],
                "lng": stop["lng"],
                "landmark": stop["landmark"],
                "routes": sorted(stop["routes"]),
                "routeCount": len(stop["routes"]),
                "nextArrivals": next_arrivals,
            }
        )
    stop_directory.sort(key=lambda item: (-item["routeCount"], item["name"]))

    return {
        "routes": routes_payload,
        "stopDirectory": stop_directory,
        "stopNames": [stop["name"] for stop in stop_directory],
        "serviceAlerts": active_alerts,
    }


# Create a readable location label when GPS is not exactly on a known stop.
def derive_trip_location_label(trip, latitude, longitude):
    """Create a readable location label when GPS is not exactly on a known stop."""
    route_stops = ROUTE_STOPS.get(trip.get("route_name") or "", [])
    if route_stops:
        nearest_stop = min(
            route_stops,
            key=lambda stop: distance_between_points_km(latitude, longitude, stop[1], stop[2]),
        )
        if distance_between_points_km(latitude, longitude, nearest_stop[1], nearest_stop[2]) <= 3:
            return nearest_stop[0]

    coords = parse_route_coords(trip.get("coords_json"))
    if len(coords) >= 2:
        start_lat, start_lng = coords[0]
        end_lat, end_lng = coords[-1]
        if abs(latitude - start_lat) <= 0.003 and abs(longitude - start_lng) <= 0.003:
            return trip.get("start_point") or "Route start"
        if abs(latitude - end_lat) <= 0.003 and abs(longitude - end_lng) <= 0.003:
            return trip.get("end_point") or "Route end"
    return f"On route to {trip.get('end_point') or 'terminal'}"


# Render a chart as an in-memory PNG image for PDF reports.
def render_chart_image(title, labels, values, chart_type="bar", color="#D60000"):
    """Render a chart as an in-memory PNG image for PDF reports."""
    labels = [str(label or "") for label in labels]
    values = [float(value or 0) for value in values]
    x_positions = list(range(len(labels)))
    figure, axis = plt.subplots(figsize=(6.8, 2.8))
    axis.set_title(title, fontsize=12, fontweight="bold")

    if chart_type == "line":
        axis.plot(x_positions, values, color=color, linewidth=2.5, marker="o", markersize=4)
        axis.fill_between(x_positions, values, color=color, alpha=0.12)
    else:
        axis.bar(x_positions, values, color=color, alpha=0.9)

    axis.set_xticks(x_positions)
    axis.set_xticklabels(labels)
    axis.grid(axis="y", linestyle="--", linewidth=0.6, alpha=0.35)
    axis.spines["top"].set_visible(False)
    axis.spines["right"].set_visible(False)
    axis.tick_params(axis="x", labelrotation=30, labelsize=8)
    axis.tick_params(axis="y", labelsize=8)
    figure.tight_layout()

    chart_buffer = BytesIO()
    figure.savefig(chart_buffer, format="png", dpi=180, bbox_inches="tight")
    plt.close(figure)
    chart_buffer.seek(0)
    return chart_buffer


# Generate the downloadable admin PDF report from dashboard overview data.
def build_admin_pdf_report(overview):
    """Generate the downloadable admin PDF report from dashboard overview data."""
    pdf_buffer = BytesIO()
    document = SimpleDocTemplate(
        pdf_buffer,
        pagesize=landscape(A4),
        rightMargin=32,
        leftMargin=32,
        topMargin=32,
        bottomMargin=32,
    )
    styles = getSampleStyleSheet()
    story = []

    title_style = styles["Heading1"]
    title_style.textColor = colors.HexColor("#D60000")
    subtitle_style = styles["Normal"]
    subtitle_style.textColor = colors.HexColor("#475569")
    table_header_dark_style = styles["BodyText"].clone("table_header_dark_style")
    table_header_dark_style.fontName = "Helvetica-Bold"
    table_header_dark_style.fontSize = 7
    table_header_dark_style.leading = 8
    table_header_dark_style.textColor = colors.white
    table_header_light_style = styles["BodyText"].clone("table_header_light_style")
    table_header_light_style.fontName = "Helvetica-Bold"
    table_header_light_style.fontSize = 7
    table_header_light_style.leading = 8
    table_header_light_style.textColor = colors.HexColor("#7f1d1d")
    table_body_style = styles["BodyText"].clone("table_body_style")
    table_body_style.fontSize = 7
    table_body_style.leading = 8
    table_body_style.wordWrap = "CJK"

    def pdf_cell(value, style=table_body_style, max_length=120):
        text = str(value or "")
        if max_length and len(text) > max_length:
            text = f"{text[: max_length - 3]}..."
        return Paragraph(escape(text), style)

    def pdf_text(value):
        return escape(str(value or ""))

    story.append(Paragraph("Gajoda Transportation Services", title_style))
    story.append(Paragraph("Crowd Analytics Report", styles["Heading2"]))
    story.append(Paragraph(f"Generated: {now().strftime('%B %d, %Y %I:%M %p')}", subtitle_style))
    story.append(Spacer(1, 0.18 * inch))

    summary_rows = [
        ["Passengers Today", str(overview["today_total"]), "Trips Today", str(overview["trips_today"])],
        ["Active Live Buses", str(overview["active_bus_count"]), "Average Load", f'{overview["avg_crowd"]}%'],
        ["High Crowd Trips", str(overview["high_crowd_count"]), "Peak Hour", f'{overview["peak_hour_label"]} ({overview["peak_hour_value"]})'],
    ]
    summary_table = Table(summary_rows, colWidths=[1.55 * inch, 1.0 * inch, 1.55 * inch, 2.0 * inch])
    summary_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.whitesmoke),
                ("TEXTCOLOR", (0, 0), (-1, -1), colors.HexColor("#0f172a")),
                ("GRID", (0, 0), (-1, -1), 0.6, colors.HexColor("#dbe2ea")),
                ("FONTNAME", (0, 0), (-1, -1), "Helvetica-Bold"),
                ("PADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    story.append(summary_table)
    story.append(Spacer(1, 0.22 * inch))

    audit_summary = overview.get("audit_summary") or {}
    audit_rows = [
        ["Trips in Audit", str(audit_summary.get("trip_count", 0)), "Completed Trips", str(audit_summary.get("completed_trip_count", 0))],
        ["Total Boarded", str(audit_summary.get("total_boarded", 0)), "Total Revenue", f"PHP {audit_summary.get('total_revenue', 0):.2f}"],
        ["Avg Trip Boarded", str(audit_summary.get("average_trip_boarded", 0)), "Avg Trip Revenue", f"PHP {audit_summary.get('average_trip_revenue', 0):.2f}"],
    ]
    audit_table = Table(audit_rows, colWidths=[1.55 * inch, 1.0 * inch, 1.7 * inch, 1.85 * inch])
    audit_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#fff1f2")),
                ("TEXTCOLOR", (0, 0), (-1, -1), colors.HexColor("#0f172a")),
                ("GRID", (0, 0), (-1, -1), 0.6, colors.HexColor("#dbe2ea")),
                ("FONTNAME", (0, 0), (-1, -1), "Helvetica-Bold"),
                ("PADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    story.append(audit_table)
    story.append(Spacer(1, 0.22 * inch))

    charts = overview["charts"]
    chart_specs = [
        ("7-Day Passenger Trend", charts["daily_labels"], charts["daily_values"], "line"),
        ("Hourly Demand", charts["hourly_labels"], charts["hourly_values"], "bar"),
        ("Route Passenger Comparison", charts["route_labels"], charts["route_values"], "bar"),
    ]
    for title, labels, values, chart_type in chart_specs:
        if not labels:
            continue
        story.append(Paragraph(title, styles["Heading3"]))
        story.append(Image(render_chart_image(title, labels, values, chart_type), width=6.7 * inch, height=2.6 * inch))
        story.append(Spacer(1, 0.15 * inch))

    story.append(Paragraph("AI Insights", styles["Heading3"]))
    for insight in overview["insights"]:
        story.append(Paragraph(f"<b>{pdf_text(insight['title'])}</b>: {pdf_text(insight['body'])}", styles["BodyText"]))
        story.append(Spacer(1, 0.08 * inch))

    story.append(Spacer(1, 0.12 * inch))
    story.append(Paragraph("Route Summary", styles["Heading3"]))
    route_table_rows = [["Route", "Trips", "Passengers", "Avg Load %"]]
    for row in overview["route_rows"]:
        route_table_rows.append([
            row["route_name"],
            str(row["trip_count"]),
            str(row["passengers"]),
            f'{row["avg_load_percent"]}%',
        ])
    route_table_rows = [
        [pdf_cell("Route", table_header_dark_style), pdf_cell("Trips", table_header_dark_style), pdf_cell("Passengers", table_header_dark_style), pdf_cell("Avg Load %", table_header_dark_style)]
    ] + [
        [pdf_cell(row["route_name"]), pdf_cell(row["trip_count"]), pdf_cell(row["passengers"]), pdf_cell(f'{row["avg_load_percent"]}%')]
        for row in overview["route_rows"]
    ]
    route_table = Table(route_table_rows, colWidths=[2.8 * inch, 0.8 * inch, 1.0 * inch, 1.0 * inch], repeatRows=1)
    route_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#D60000")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.6, colors.HexColor("#dbe2ea")),
                ("PADDING", (0, 0), (-1, -1), 7),
                ("FONTSIZE", (0, 0), (-1, -1), 7),
                ("BACKGROUND", (0, 1), (-1, -1), colors.whitesmoke),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    story.append(route_table)

    if overview.get("bus_report_sections"):
        story.append(Spacer(1, 0.2 * inch))
        story.append(Paragraph("Bus-Specific Daily Tabulation", styles["Heading3"]))
        fleet_totals = (overview.get("report_bus_analytics") or {}).get("all") or {}
        fleet_total_rows = [[
            pdf_cell("Student", table_header_light_style),
            pdf_cell("PWD", table_header_light_style),
            pdf_cell("Senior", table_header_light_style),
            pdf_cell("Regular", table_header_light_style),
            pdf_cell("Total Pax", table_header_light_style),
            pdf_cell("Student Rev", table_header_light_style),
            pdf_cell("PWD Rev", table_header_light_style),
            pdf_cell("Senior Rev", table_header_light_style),
            pdf_cell("Regular Rev", table_header_light_style),
            pdf_cell("Total Revenue", table_header_light_style),
        ]]
        for bus in overview["bus_report_sections"]:
            fleet_total_rows.append([
                pdf_cell(bus["passengers_by_type"]["student"]),
                pdf_cell(bus["passengers_by_type"]["pwd"]),
                pdf_cell(bus["passengers_by_type"]["senior"]),
                pdf_cell(bus["passengers_by_type"]["regular"]),
                pdf_cell(bus["total_passengers"]),
                pdf_cell(f"PHP {bus['revenue_by_type']['student']:.2f}"),
                pdf_cell(f"PHP {bus['revenue_by_type']['pwd']:.2f}"),
                pdf_cell(f"PHP {bus['revenue_by_type']['senior']:.2f}"),
                pdf_cell(f"PHP {bus['revenue_by_type']['regular']:.2f}"),
                pdf_cell(f"PHP {bus['total_revenue']:.2f}"),
            ])
        fleet_total_rows.append([
            pdf_cell((fleet_totals.get("passenger_totals") or {}).get("student", 0)),
            pdf_cell((fleet_totals.get("passenger_totals") or {}).get("pwd", 0)),
            pdf_cell((fleet_totals.get("passenger_totals") or {}).get("senior", 0)),
            pdf_cell((fleet_totals.get("passenger_totals") or {}).get("regular", 0)),
            pdf_cell(fleet_totals.get("total_passengers", 0)),
            pdf_cell(f"PHP {(fleet_totals.get('revenue_totals') or {}).get('student', 0):.2f}"),
            pdf_cell(f"PHP {(fleet_totals.get('revenue_totals') or {}).get('pwd', 0):.2f}"),
            pdf_cell(f"PHP {(fleet_totals.get('revenue_totals') or {}).get('senior', 0):.2f}"),
            pdf_cell(f"PHP {(fleet_totals.get('revenue_totals') or {}).get('regular', 0):.2f}"),
            pdf_cell(f"PHP {fleet_totals.get('total_revenue', 0):.2f}"),
        ])
        fleet_total_table = Table(
            fleet_total_rows,
            colWidths=[0.5 * inch, 0.5 * inch, 0.5 * inch, 0.55 * inch, 0.6 * inch, 0.72 * inch, 0.72 * inch, 0.72 * inch, 0.76 * inch, 0.86 * inch],
            repeatRows=1,
        )
        fleet_total_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#fee2e2")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#7f1d1d")),
                    ("BACKGROUND", (0, 1), (-1, -2), colors.whitesmoke),
                    ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#fee2e2")),
                    ("TEXTCOLOR", (0, -1), (-1, -1), colors.HexColor("#7f1d1d")),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#dbe2ea")),
                    ("PADDING", (0, 0), (-1, -1), 6),
                    ("FONTSIZE", (0, 0), (-1, -1), 7),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ]
            )
        )
        story.append(fleet_total_table)
        story.append(Spacer(1, 0.08 * inch))
        for bus in overview["bus_report_sections"]:
            story.append(
                Paragraph(
                    f"{pdf_text(bus['plate_number'])} | Status: {pdf_text(bus['status'])} / {pdf_text(bus['trip_status'])} | Route: {pdf_text(bus['route_name'])}",
                    styles["Heading4"],
                )
            )
            if bus["rows"]:
                trip_rows = [[
                    pdf_cell("Date", table_header_dark_style),
                    pdf_cell("Trip ID", table_header_dark_style),
                    pdf_cell("Start", table_header_dark_style),
                    pdf_cell("Driver", table_header_dark_style),
                    pdf_cell("Route", table_header_dark_style),
                    pdf_cell("Student", table_header_dark_style),
                    pdf_cell("PWD", table_header_dark_style),
                    pdf_cell("Senior", table_header_dark_style),
                    pdf_cell("Regular", table_header_dark_style),
                    pdf_cell("Total Pax", table_header_dark_style),
                    pdf_cell("Student Rev", table_header_dark_style),
                    pdf_cell("PWD Rev", table_header_dark_style),
                    pdf_cell("Senior Rev", table_header_dark_style),
                    pdf_cell("Regular Rev", table_header_dark_style),
                    pdf_cell("Total Revenue", table_header_dark_style),
                ]]
                for row in bus["rows"]:
                    trip_rows.append(
                        [
                            pdf_cell(row["service_date"]),
                            pdf_cell(row["trip_id"]),
                            pdf_cell(row["started_at"] or "No start time"),
                            pdf_cell(row["driver_name"]),
                            pdf_cell(row["route_name"]),
                            pdf_cell(row["student_count"]),
                            pdf_cell(row["pwd_count"]),
                            pdf_cell(row["senior_count"]),
                            pdf_cell(row["regular_count"]),
                            pdf_cell(row["total_passengers"]),
                            pdf_cell(f"PHP {row['student_revenue']:.2f}"),
                            pdf_cell(f"PHP {row['pwd_revenue']:.2f}"),
                            pdf_cell(f"PHP {row['senior_revenue']:.2f}"),
                            pdf_cell(f"PHP {row['regular_revenue']:.2f}"),
                            pdf_cell(f"PHP {row['total_revenue']:.2f}"),
                        ]
                    )
                trip_rows.append(
                    [
                        pdf_cell("BUS TOTAL", table_header_light_style),
                        pdf_cell(""),
                        pdf_cell(""),
                        pdf_cell(""),
                        pdf_cell(""),
                        pdf_cell(bus["passengers_by_type"]["student"], table_header_light_style),
                        pdf_cell(bus["passengers_by_type"]["pwd"], table_header_light_style),
                        pdf_cell(bus["passengers_by_type"]["senior"], table_header_light_style),
                        pdf_cell(bus["passengers_by_type"]["regular"], table_header_light_style),
                        pdf_cell(bus["total_passengers"], table_header_light_style),
                        pdf_cell(f"PHP {bus['revenue_by_type']['student']:.2f}", table_header_light_style),
                        pdf_cell(f"PHP {bus['revenue_by_type']['pwd']:.2f}", table_header_light_style),
                        pdf_cell(f"PHP {bus['revenue_by_type']['senior']:.2f}", table_header_light_style),
                        pdf_cell(f"PHP {bus['revenue_by_type']['regular']:.2f}", table_header_light_style),
                        pdf_cell(f"PHP {bus['total_revenue']:.2f}", table_header_light_style),
                    ]
                )
                trip_table = Table(
                    trip_rows,
                    colWidths=[0.72 * inch, 0.52 * inch, 0.8 * inch, 0.95 * inch, 1.25 * inch, 0.46 * inch, 0.46 * inch, 0.46 * inch, 0.5 * inch, 0.56 * inch, 0.68 * inch, 0.68 * inch, 0.68 * inch, 0.72 * inch, 0.8 * inch],
                    repeatRows=1,
                )
                trip_table.setStyle(
                    TableStyle(
                        [
                            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#D60000")),
                            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#dbe2ea")),
                            ("PADDING", (0, 0), (-1, -1), 6),
                            ("FONTSIZE", (0, 0), (-1, -1), 7),
                            ("VALIGN", (0, 0), (-1, -1), "TOP"),
                            ("BACKGROUND", (0, 1), (-1, -1), colors.whitesmoke),
                            ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#fee2e2")),
                            ("TEXTCOLOR", (0, -1), (-1, -1), colors.HexColor("#7f1d1d")),
                        ]
                    )
                )
                story.append(trip_table)
            else:
                story.append(Paragraph("No trip data recorded for this bus yet.", styles["BodyText"]))
            story.append(Spacer(1, 0.12 * inch))

    if overview.get("attendance_rows"):
        story.append(Spacer(1, 0.2 * inch))
        story.append(Paragraph("Staff Attendance and Trip Assignment", styles["Heading3"]))
        attendance_rows = [[
            pdf_cell("Staff", table_header_light_style),
            pdf_cell("Login Time", table_header_light_style),
            pdf_cell("Trip", table_header_light_style),
            pdf_cell("Trip Window", table_header_light_style),
            pdf_cell("Status", table_header_light_style),
        ]]
        for row in overview["attendance_rows"][:12]:
            trip_window = "No trip assigned"
            if row.get("trip_id"):
                trip_window = f"{row['trip_started_at'] or 'No trip start'}"
                trip_window += f" to {row['trip_ended_at']}" if row.get("trip_ended_at") else " to active trip"
            attendance_rows.append(
                [
                    pdf_cell(f"{row['full_name']} ({row['role']})"),
                    pdf_cell(row["login_time"]),
                    pdf_cell(row["trip_summary"]),
                    pdf_cell(trip_window),
                    pdf_cell(row.get("trip_status") or "attendance only"),
                ]
            )
        attendance_table = Table(attendance_rows, colWidths=[1.55 * inch, 1.15 * inch, 1.55 * inch, 1.55 * inch, 0.8 * inch], repeatRows=1)
        attendance_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#fee2e2")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#7f1d1d")),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#dbe2ea")),
                    ("PADDING", (0, 0), (-1, -1), 6),
                    ("FONTSIZE", (0, 0), (-1, -1), 7),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ]
            )
        )
        story.append(attendance_table)

    if overview["recent_logs"]:
        story.append(Spacer(1, 0.2 * inch))
        story.append(Paragraph("Recent Logs", styles["Heading3"]))
        log_rows = [[
            pdf_cell("Time", table_header_light_style),
            pdf_cell("Role", table_header_light_style),
            pdf_cell("Action", table_header_light_style),
            pdf_cell("Description", table_header_light_style),
        ]]
        for log in overview["recent_logs"][:8]:
            log_rows.append([
                pdf_cell(log["created_at"]),
                pdf_cell(log["role"] or "system"),
                pdf_cell(log["action"]),
                pdf_cell(log["description"]),
            ])
        log_table = Table(log_rows, colWidths=[1.25 * inch, 0.7 * inch, 1.0 * inch, 3.35 * inch], repeatRows=1)
        log_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#fee2e2")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#7f1d1d")),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#dbe2ea")),
                    ("PADDING", (0, 0), (-1, -1), 6),
                    ("FONTSIZE", (0, 0), (-1, -1), 7),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ]
            )
        )
        story.append(log_table)

    document.build(story)
    pdf_buffer.seek(0)
    return pdf_buffer


# Generate a compact PDF if the full report renderer hits a layout or data issue.
def build_admin_pdf_report_fallback(overview, error_message=""):
    """Generate a minimal admin PDF report that avoids complex layout sections."""
    pdf_buffer = BytesIO()
    document = SimpleDocTemplate(
        pdf_buffer,
        pagesize=landscape(A4),
        rightMargin=32,
        leftMargin=32,
        topMargin=32,
        bottomMargin=32,
    )
    styles = getSampleStyleSheet()
    story = []

    def safe_text(value):
        return escape(str(value or ""))

    story.append(Paragraph("Gajoda Transportation Services", styles["Heading1"]))
    story.append(Paragraph("Crowd Analytics Report", styles["Heading2"]))
    story.append(Paragraph(f"Generated: {now().strftime('%B %d, %Y %I:%M %p')}", styles["Normal"]))
    story.append(Spacer(1, 0.2 * inch))

    if error_message:
        story.append(
            Paragraph(
                "The detailed PDF layout could not be rendered, so this compact export was generated instead.",
                styles["BodyText"],
            )
        )
        story.append(Spacer(1, 0.12 * inch))

    audit_summary = overview.get("audit_summary") or {}
    summary_rows = [
        ["Metric", "Value", "Metric", "Value"],
        ["Passengers Today", str(overview.get("today_total", 0)), "Trips Today", str(overview.get("trips_today", 0))],
        ["Active Live Buses", str(overview.get("active_bus_count", 0)), "Average Load", f"{overview.get('avg_crowd', 0)}%"],
        ["High Crowd Trips", str(overview.get("high_crowd_count", 0)), "Peak Hour", f"{overview.get('peak_hour_label', 'N/A')} ({overview.get('peak_hour_value', 0)})"],
        ["Trips in Audit", str(audit_summary.get("trip_count", 0)), "Completed Trips", str(audit_summary.get("completed_trip_count", 0))],
        ["Total Boarded", str(audit_summary.get("total_boarded", 0)), "Total Revenue", f"PHP {audit_summary.get('total_revenue', 0):.2f}"],
    ]
    summary_table = Table(summary_rows, colWidths=[1.7 * inch, 1.2 * inch, 1.7 * inch, 1.5 * inch], repeatRows=1)
    summary_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#D60000")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("BACKGROUND", (0, 1), (-1, -1), colors.whitesmoke),
                ("GRID", (0, 0), (-1, -1), 0.6, colors.HexColor("#dbe2ea")),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("PADDING", (0, 0), (-1, -1), 7),
            ]
        )
    )
    story.append(summary_table)
    story.append(Spacer(1, 0.22 * inch))

    story.append(Paragraph("Route Summary", styles["Heading3"]))
    route_rows = [["Route", "Trips", "Passengers", "Avg Load %"]]
    for row in overview.get("route_rows", [])[:20]:
        route_rows.append(
            [
                safe_text(row.get("route_name")),
                safe_text(row.get("trip_count")),
                safe_text(row.get("passengers")),
                safe_text(f"{row.get('avg_load_percent', 0)}%"),
            ]
        )
    if len(route_rows) == 1:
        route_rows.append(["No route records in this reporting window.", "", "", ""])
    route_table = Table(route_rows, colWidths=[3.2 * inch, 1.0 * inch, 1.1 * inch, 1.0 * inch], repeatRows=1)
    route_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#fee2e2")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#7f1d1d")),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#dbe2ea")),
                ("PADDING", (0, 0), (-1, -1), 6),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    story.append(route_table)

    document.build(story)
    pdf_buffer.seek(0)
    return pdf_buffer


# Fetch the newest GPS log for a trip.
def get_latest_trip_gps(conn, trip_id):
    """Fetch the newest GPS log for a trip."""
    row = conn.execute(
        """
        SELECT latitude, longitude, recorded_at
        FROM gps_logs
        WHERE trip_id = ?
        ORDER BY recorded_at DESC, id DESC
        LIMIT 1
        """,
        (trip_id,),
    ).fetchone()
    return dict(row) if row else None


# Store a GPS point for the active trip and offboard passengers whose destinations were passed.
def record_trip_gps_location(conn, trip, latitude, longitude, conductor_id=None):
    """Store a GPS point for the active trip and offboard passengers whose destinations were passed."""
    latitude = float(latitude)
    longitude = float(longitude)
    recorded_at = to_db_time(now())
    conn.execute(
        """
        INSERT INTO gps_logs (trip_id, latitude, longitude, recorded_at)
        VALUES (?, ?, ?, ?)
        """,
        (trip["id"], latitude, longitude, recorded_at),
    )
    current_stop_details = get_trip_current_stop_details(
        trip,
        {"latitude": latitude, "longitude": longitude},
        None,
    )
    if current_stop_details:
        auto_offboard_due_passengers(
            conn,
            trip,
            current_stop_details["name"],
            latitude,
            longitude,
            conductor_id,
        )
    return current_stop_details


# Fetch recent conductor ticketing transactions for one trip.
def get_recent_trip_transactions(conn, trip_id, limit=8):
    """Fetch recent conductor ticketing transactions for one trip."""
    rows = conn.execute(
        """
        SELECT recorded_at, event_type, passenger_type, quantity, stop_name, origin_stop, destination_stop, fare_amount, occupancy_after
        FROM trip_transactions
        WHERE trip_id = ?
          AND event_type = 'board'
        ORDER BY recorded_at DESC, id DESC
        LIMIT ?
        """,
        (trip_id, limit),
    ).fetchall()
    return [dict(row) for row in rows]


# Summarize today's conductor ticket activity.
def get_conductor_today_summary(conn, conductor_id):
    """Summarize today's conductor ticket activity."""
    empty_summary = {
        "students": 0,
        "pwd": 0,
        "senior": 0,
        "regular": 0,
        "boarded": 0,
        "dropped": 0,
        "transactions": 0,
    }
    summary_row = conn.execute(
        """
        SELECT
            COALESCE(SUM(CASE WHEN event_type = 'board' AND passenger_type = 'student' THEN quantity ELSE 0 END), 0) AS students,
            COALESCE(SUM(CASE WHEN event_type = 'board' AND passenger_type = 'pwd' THEN quantity ELSE 0 END), 0) AS pwd,
            COALESCE(SUM(CASE WHEN event_type = 'board' AND passenger_type = 'senior' THEN quantity ELSE 0 END), 0) AS senior,
            COALESCE(SUM(CASE WHEN event_type = 'board' AND passenger_type = 'regular' THEN quantity ELSE 0 END), 0) AS regular,
            COALESCE(SUM(CASE WHEN event_type = 'board' THEN quantity ELSE 0 END), 0) AS boarded,
            COALESCE(SUM(CASE WHEN event_type = 'drop' THEN quantity ELSE 0 END), 0) AS dropped,
            COUNT(*) AS transactions
        FROM trip_transactions tt
        JOIN trips t ON t.id = tt.trip_id
        WHERE t.conductor_id = ?
          AND DATE(tt.recorded_at) = DATE(?)
        """,
        (conductor_id, now().date().isoformat()),
    ).fetchone()

    if not summary_row:
        return empty_summary
    return {key: int(summary_row[key] or 0) for key in empty_summary}


# Return sidebar data used by the live conductor terminal panels.
def build_conductor_sidebar_payload(conn, conductor_id, trip, current_stop=None):
    """Return sidebar data used by the live conductor terminal panels."""
    if not trip:
        return {
            "destination_manifest": [],
            "recent_transactions": [],
            "today_summary": get_conductor_today_summary(conn, conductor_id),
        }
    return {
        "destination_manifest": build_trip_destination_manifest(conn, trip, current_stop),
        "recent_transactions": get_recent_trip_transactions(conn, trip["id"], 8),
        "today_summary": get_conductor_today_summary(conn, conductor_id),
    }


# Run a short database query and return a single row.
def fetch_one(query, params=()):
    """Run a short database query and return a single row."""
    conn = get_db()
    row = conn.execute(query, params).fetchone()
    conn.close()
    return dict(row) if row else None


# Write an auditable system activity entry.
def log_event(conn, user_id, role, action, description):
    """Write an auditable system activity entry."""
    cursor = conn.execute(
        """
        INSERT INTO system_logs (user_id, role, action, description, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (user_id, role, action, description, to_db_time(now())),
    )
    return cursor.lastrowid


def create_admin_notification(conn, notification_type, user_id, title, message):
    """Create an unread notification for the admin dashboard."""
    cursor = conn.execute(
        """
        INSERT INTO admin_notifications (notification_type, user_id, title, message, status, created_at)
        VALUES (?, ?, ?, ?, 'unread', ?)
        """,
        (notification_type, user_id, title, message, to_db_time(now())),
    )
    return cursor.lastrowid


def create_admin_notification_once(conn, notification_type, user_id, title, message, duplicate_key):
    """Create an admin notification unless a matching message already exists."""
    existing = conn.execute(
        """
        SELECT id
        FROM admin_notifications
        WHERE notification_type = ? AND message LIKE ?
        LIMIT 1
        """,
        (notification_type, f"%{duplicate_key}%"),
    ).fetchone()
    if existing:
        return existing["id"]
    return create_admin_notification(conn, notification_type, user_id, title, message)


def create_trip_capacity_notification(conn, trip, occupancy, actor_user_id=None):
    """Create one high-crowd or full-bus notification per trip threshold."""
    capacity = max(int(trip.get("capacity") or DEFAULT_BUS_CAPACITY), 1)
    occupancy = int(occupancy or 0)
    trip_key = f"trip #{trip['id']}"
    if occupancy >= capacity:
        return create_admin_notification_once(
            conn,
            "bus_full",
            actor_user_id,
            "Bus full",
            f"{trip['plate_number']} reached full capacity on {trip['route_name']} for {trip_key}: {occupancy}/{capacity} passengers.",
            trip_key,
        )
    if occupancy / capacity >= 0.9:
        return create_admin_notification_once(
            conn,
            "high_crowd",
            actor_user_id,
            "High crowd level",
            f"{trip['plate_number']} reached high crowd level on {trip['route_name']} for {trip_key}: {occupancy}/{capacity} passengers.",
            trip_key,
        )
    return None


def record_password_reset_request(conn, user, account_identifier):
    """Store the admin notification and audit log for a password reset request."""
    if user:
        reset_message = f"{user['full_name']} ({user['email']}) requested a password reset."
        notification_user_id = user["id"]
        log_user_id = user["id"]
        log_role = user["role"]
    else:
        reset_message = f"Password reset requested for unregistered account {account_identifier}."
        notification_user_id = None
        log_user_id = None
        log_role = "system"

    notification_id = create_admin_notification(
        conn,
        PASSWORD_RESET_NOTIFICATION_TYPE,
        notification_user_id,
        "Password reset request",
        reset_message,
    )
    log_id = log_event(
        conn,
        log_user_id,
        log_role,
        PASSWORD_RESET_LOG_ACTION,
        reset_message,
    )
    conn.commit()

    notification_row = conn.execute(
        "SELECT id FROM admin_notifications WHERE id = ?",
        (notification_id,),
    ).fetchone()
    log_row = conn.execute(
        "SELECT id FROM system_logs WHERE id = ?",
        (log_id,),
    ).fetchone()
    if not notification_row or not log_row:
        raise RuntimeError("Password reset request was not persisted.")

    return reset_message


def password_reset_token_hash(token):
    """Return the database-safe hash for a raw reset token."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def gmail_reset_configured():
    """Return whether Gmail SMTP credentials are available."""
    return bool(os.environ.get("GMAIL_USER") and os.environ.get("GMAIL_APP_PASSWORD"))


def create_password_reset_token(conn, user_id):
    """Create a one-time password reset token and store only its hash."""
    raw_token = secrets.token_urlsafe(48)
    token_hash = password_reset_token_hash(raw_token)
    created_at = now()
    expires_at = created_at + timedelta(minutes=PASSWORD_RESET_TOKEN_MINUTES)
    conn.execute(
        """
        UPDATE password_reset_tokens
        SET used_at = ?
        WHERE user_id = ? AND used_at IS NULL
        """,
        (to_db_time(created_at), user_id),
    )
    conn.execute(
        """
        INSERT INTO password_reset_tokens (user_id, token_hash, created_at, expires_at)
        VALUES (?, ?, ?, ?)
        """,
        (user_id, token_hash, to_db_time(created_at), to_db_time(expires_at)),
    )
    return raw_token, expires_at


def build_password_reset_url(token):
    """Build the absolute password reset URL sent by email."""
    base_url = (os.environ.get("APP_BASE_URL") or request.url_root or "").strip().rstrip("/")
    return f"{base_url}{url_for('reset_password', token=token)}"


def send_password_reset_email(user, reset_url, expires_at):
    """Send a password reset link through Gmail SMTP."""
    sender = os.environ.get("GMAIL_USER", "").strip()
    app_password = os.environ.get("GMAIL_APP_PASSWORD", "")
    if not sender or not app_password:
        raise RuntimeError("Gmail SMTP credentials are not configured.")

    subject = "Gajoda account password reset"
    body = (
        f"Hello {user['full_name']},\n\n"
        "We received a request to reset your Gajoda account password.\n\n"
        f"Reset your password here:\n{reset_url}\n\n"
        f"This link expires at {to_db_time(expires_at)} and can only be used once.\n\n"
        "If you did not request this, you can ignore this email."
    )

    message = EmailMessage()
    message["From"] = sender
    message["To"] = user["email"]
    message["Subject"] = subject
    message.set_content(body)

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context, timeout=20) as smtp:
        smtp.login(sender, app_password)
        smtp.send_message(message)


def get_valid_password_reset_token(conn, token):
    """Return the unused, unexpired reset token row for a raw token."""
    if not token:
        return None
    return conn.execute(
        """
        SELECT prt.id, prt.user_id, prt.expires_at, u.email, u.full_name, u.role
        FROM password_reset_tokens prt
        JOIN users u ON u.id = prt.user_id
        WHERE prt.token_hash = ?
          AND prt.used_at IS NULL
          AND prt.expires_at > ?
        LIMIT 1
        """,
        (password_reset_token_hash(token), to_db_time(now())),
    ).fetchone()


def is_password_hash(value):
    """Return whether a stored password value looks like a Werkzeug hash."""
    return isinstance(value, str) and value.startswith(("scrypt:", "pbkdf2:"))


def verify_password(stored_password, submitted_password):
    """Check submitted credentials against hashed or legacy plain-text passwords."""
    if is_password_hash(stored_password):
        return check_password_hash(stored_password, submitted_password)
    return secrets.compare_digest(str(stored_password or ""), str(submitted_password or ""))


def ensure_password_hashed(conn, user_id, stored_password):
    """Upgrade a legacy plain-text password row without changing the usable password."""
    if stored_password and not is_password_hash(stored_password):
        conn.execute(
            "UPDATE users SET password = ? WHERE id = ?",
            (generate_password_hash(stored_password), user_id),
        )


def role_home_endpoint(role_name):
    """Return the default dashboard endpoint for a user role."""
    if role_name == "super_admin":
        return "super_admin_dashboard"
    if role_name == "admin":
        return "admin_dashboard"
    if role_name == "driver":
        return "driver_dashboard"
    if role_name == "conductor":
        return "conductor"
    return "login"


# Protect Flask routes so only users with the required role can access them.
def require_role(role_name):
    """Protect Flask routes so only users with the required role can access them."""
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            user_id = session.get("user_id")
            if not user_id:
                return redirect(url_for("login"))

            conn = get_db()
            user = conn.execute(
                "SELECT id, role FROM users WHERE id = ?",
                (user_id,),
            ).fetchone()
            conn.close()

            if not user:
                session.clear()
                return redirect(url_for("login"))

            current_role = user["role"]
            session["role"] = current_role
            allowed_roles = {role_name} if isinstance(role_name, str) else set(role_name)
            if current_role not in allowed_roles:
                return redirect(url_for(role_home_endpoint(current_role)))

            return view(*args, **kwargs)

        return wrapped

    return decorator


# Return the active trip currently assigned to a driver.
def get_active_trip_for_driver(conn, driver_id):
    """Return the active trip currently assigned to a driver."""
    row = conn.execute(
        """
        SELECT t.*, b.plate_number, b.capacity, b.route_color,
               r.route_name, r.start_point, r.end_point, r.coords_json, r.expected_duration_minutes
        FROM trips t
        JOIN buses b ON b.id = t.bus_id
        JOIN routes r ON r.id = t.route_id
        WHERE t.driver_id = ? AND t.status = 'active' AND r.is_published = 1
        ORDER BY t.id DESC
        LIMIT 1
        """,
        (driver_id,),
    ).fetchone()
    return dict(row) if row else None


# Return the active trip currently monitored by a conductor.
def get_active_trip_for_conductor(conn, conductor_id):
    """Return the active trip currently monitored by a conductor."""
    row = conn.execute(
        """
        SELECT t.*, b.plate_number, b.capacity,
               r.route_name, r.start_point, r.end_point, r.coords_json,
               r.distance_km, r.minimum_fare, r.discounted_fare
        FROM trips t
        JOIN buses b ON b.id = t.bus_id
        JOIN routes r ON r.id = t.route_id
        WHERE t.conductor_id = ? AND t.status = 'active' AND r.is_published = 1
        ORDER BY t.id DESC
        LIMIT 1
        """,
        (conductor_id,),
    ).fetchone()
    return dict(row) if row else None


# Return the latest passenger/crowd record for a trip.
def get_latest_trip_record(conn, trip_id):
    """Return the latest passenger/crowd record for a trip."""
    row = conn.execute(
        """
        SELECT *
        FROM trip_records
        WHERE trip_id = ?
        ORDER BY recorded_at DESC, id DESC
        LIMIT 1
        """,
        (trip_id,),
    ).fetchone()
    return dict(row) if row else None


# Fill chart time series gaps so days without records still appear as zero.
def fill_missing_days(rows, key_name, days=7):
    """Fill chart time series gaps so days without records still appear as zero."""
    lookup = {}
    for row in rows:
        key = row[key_name]
        if hasattr(key, "isoformat"):
            key = key.isoformat()
        else:
            key = str(key)
        lookup[key] = row["total"]
    labels = []
    values = []
    for offset in range(days - 1, -1, -1):
        day = (now() - timedelta(days=offset)).date().isoformat()
        labels.append(day[5:])
        values.append(int(lookup.get(day, 0) or 0))
    return labels, values


# Build live bus map data from buses, active trips, GPS logs, and occupancy.
def build_live_bus_data(conn):
    """Build live bus map data from buses, active trips, GPS logs, and occupancy."""
    rows = conn.execute(
        """
        SELECT b.id AS bus_id,
               b.plate_number,
               b.capacity,
               b.status AS bus_status,
               b.route_color,
               COALESCE(active_trip.id, latest_trip.id) AS trip_id,
               COALESCE(active_trip.status, latest_trip.status, 'idle') AS trip_status,
               COALESCE(active_trip.occupancy, 0) AS occupancy,
               COALESCE(active_trip.peak_occupancy, 0) AS peak_occupancy,
               COALESCE(active_trip.started_at, latest_trip.started_at) AS started_at,
               r.route_name,
               r.start_point,
               r.end_point,
               r.distance_km,
               r.expected_duration_minutes,
               r.coords_json,
               u.full_name AS driver_name,
               tr.stop_name,
               gl.latitude AS latitude,
               gl.longitude AS longitude,
               gl.recorded_at AS recorded_at
        FROM buses b
        LEFT JOIN trips active_trip ON active_trip.id = (
            SELECT id
            FROM trips
            WHERE bus_id = b.id
              AND status = 'active'
              AND route_id IN (SELECT id FROM routes WHERE is_published = 1)
            ORDER BY started_at DESC, id DESC
            LIMIT 1
        )
        LEFT JOIN trips latest_trip ON latest_trip.id = (
            SELECT id
            FROM trips
            WHERE bus_id = b.id
              AND route_id IN (SELECT id FROM routes WHERE is_published = 1)
            ORDER BY started_at DESC, id DESC
            LIMIT 1
        )
        LEFT JOIN routes r ON r.id = COALESCE(active_trip.route_id, latest_trip.route_id)
        LEFT JOIN users u ON u.id = active_trip.driver_id
        LEFT JOIN trip_records tr ON tr.id = (
            SELECT id
            FROM trip_records
            WHERE trip_id = active_trip.id
            ORDER BY recorded_at DESC, id DESC
            LIMIT 1
        )
        LEFT JOIN gps_logs gl ON gl.id = (
            SELECT id
            FROM gps_logs
            WHERE trip_id = active_trip.id
            ORDER BY recorded_at DESC, id DESC
            LIMIT 1
        )
        ORDER BY b.plate_number
        """
    ).fetchall()

    buses = []
    active_buses = 0
    for raw_row in rows:
        row = dict(raw_row)
        coords = parse_route_coords(row["coords_json"])
        gps_history_rows = []
        if row["trip_status"] == "active" and row["trip_id"]:
            gps_history_rows = conn.execute(
                """
                SELECT latitude, longitude, recorded_at
                FROM gps_logs
                WHERE trip_id = ?
                ORDER BY recorded_at ASC, id ASC
                """,
                (row["trip_id"],),
            ).fetchall()
        gps_history = []
        for gps_row in gps_history_rows:
            if gps_row["latitude"] is None or gps_row["longitude"] is None:
                continue
            gps_history.append(
                [
                    float(gps_row["latitude"]),
                    float(gps_row["longitude"]),
                ]
            )
        lat = float(row["latitude"]) if row["latitude"] is not None else None
        lng = float(row["longitude"]) if row["longitude"] is not None else None
        occupancy = int(row["occupancy"] or 0)
        capacity = int(row["capacity"] or DEFAULT_BUS_CAPACITY)
        status = row["bus_status"] or "offline"
        is_live_tracked = row["trip_status"] == "active" and lat is not None and lng is not None
        if is_live_tracked:
            active_buses += 1
        buses.append(
            {
                "tripId": int(row["trip_id"]) if row["trip_id"] else None,
                "busId": int(row["bus_id"]),
                "id": row["plate_number"],
                "lat": lat,
                "lng": lng,
                "direction": row["route_name"] or FORWARD_ROUTE_NAME,
                "start": row["start_point"] or CORRIDOR_STOP_NAMES[0],
                "end": row["end_point"] or CORRIDOR_STOP_NAMES[-1],
                "distanceKm": float(row["distance_km"] or 0),
                "expectedDurationMinutes": int(row["expected_duration_minutes"] or 0),
                "driver": row["driver_name"] or "Driver pending",
                "crowdLevel": classify_capacity(occupancy, capacity),
                "status": status,
                "tripStatus": row["trip_status"],
                "isLiveTracked": is_live_tracked,
                "nextStop": row["stop_name"] or ("Live tracking active" if row["trip_status"] == "active" else "Awaiting dispatch"),
                "eta": "Live Trip" if row["trip_status"] == "active" else "Not in service",
                "passengers": occupancy,
                "capacity": capacity,
                "routeColor": row["route_color"] or "#1d4ed8",
                "coords": coords,
                "stops": get_route_stop_details(row["route_name"] or FORWARD_ROUTE_NAME),
                "history": gps_history,
            }
        )

    low_count = sum(1 for bus in buses if bus["crowdLevel"] == "Low")
    medium_count = sum(1 for bus in buses if bus["crowdLevel"] == "Medium")
    high_count = sum(1 for bus in buses if bus["crowdLevel"] == "High")

    average_ratio = 0
    active_bus_rows = [bus for bus in buses if bus["isLiveTracked"]]
    if active_bus_rows:
        average_ratio = sum(bus["passengers"] / max(bus["capacity"], 1) for bus in active_bus_rows) / len(active_bus_rows)

    return {
        "buses": buses,
        "active_bus_count": active_buses,
        "avg_crowd": int(round(average_ratio * 100)),
        "low_count": sum(1 for bus in active_bus_rows if bus["crowdLevel"] == "Low"),
        "medium_count": sum(1 for bus in active_bus_rows if bus["crowdLevel"] == "Medium"),
        "high_count": sum(1 for bus in active_bus_rows if bus["crowdLevel"] == "High"),
    }


# Generate simple decision-support insights from admin analytics values.
def generate_ai_insights(overview):
    """Generate simple decision-support insights from admin analytics values."""
    insights = []

    busiest_route = max(overview["route_rows"], key=lambda item: item["passengers"], default=None)
    if busiest_route:
        insights.append(
            {
                "category": "Route Insight",
                "title": f"Deploy more trips on {busiest_route['route_name']}",
                "body": f"{busiest_route['route_name']} is carrying {busiest_route['passengers']} passengers in the reporting window, which is the highest route demand in the system.",
                "tone": "hot",
            }
        )

    if overview["today_total"] > overview["yesterday_total"]:
        lift = overview["today_total"] - overview["yesterday_total"]
        insights.append(
            {
                "category": "Demand Insight",
                "title": "Demand is climbing today",
                "body": f"Passenger volume is up by {lift} versus yesterday. Prepare reserve buses during the afternoon peak if that trend continues.",
                "tone": "good",
            }
        )
    elif overview["today_total"] < overview["yesterday_total"]:
        drop = overview["yesterday_total"] - overview["today_total"]
        insights.append(
            {
                "category": "Schedule Insight",
                "title": "Use the lighter day to rebalance operations",
                "body": f"Volume is down by {drop} versus yesterday. This is a good window to shift buses into maintenance or refine scheduling without hurting availability.",
                "tone": "calm",
            }
        )
    else:
        insights.append(
            {
                "category": "Demand Insight",
                "title": "Demand is steady today",
                "body": "Passenger volume matches yesterday so far. Keep the current dispatch plan active and watch the next peak window before adding trips.",
                "tone": "calm",
            }
        )

    if overview["peak_hour_value"] > 0:
        peak_tone = "warn" if overview["peak_hour_value"] >= 60 else "good"
        insights.append(
            {
                "category": "Peak Insight",
                "title": f"Peak pressure is centered around {overview['peak_hour_label']}",
                "body": f"Peak hour records show {overview['peak_hour_value']} passengers at {overview['peak_hour_label']}. Staff dispatch and conductor readiness should be concentrated around that time block.",
                "tone": peak_tone,
            }
        )

    if overview["high_crowd_count"] > 0:
        insights.append(
            {
                "category": "Crowd Insight",
                "title": "High-crowd trips need intervention",
                "body": f"There are currently {overview['high_crowd_count']} active buses operating in high crowd mode. Consider route staggering or short-turning one reserve unit.",
                "tone": "warn",
            }
        )
    elif overview["active_bus_count"] > 0:
        insights.append(
            {
                "category": "Fleet Insight",
                "title": "Active buses are within manageable load",
                "body": f"{overview['active_bus_count']} live-tracked buses are reporting without any high-crowd trips. Keep monitoring medium-crowd units before the next dispatch cycle.",
                "tone": "good",
            }
        )

    top_stop = max(overview["stop_rows"], key=lambda item: item["boarded"], default=None)
    if top_stop and int(top_stop["boarded"] or 0) > 0:
        insights.append(
            {
                "category": "Stop Insight",
                "title": f"Watch boarding demand at {top_stop['stop_name']}",
                "body": f"{top_stop['stop_name']} has {top_stop['boarded']} recorded boardings, making it the busiest boarding point in the current stop analytics.",
                "tone": "warn" if int(top_stop["boarded"] or 0) >= 25 else "calm",
            }
        )

    return insights[:6]


# Summarize busiest stops by boarded and dropped passenger counts.
def build_stop_analytics(conn):
    """Summarize busiest stops by boarded and dropped passenger counts."""
    rows = [
        dict(row)
        for row in conn.execute(
            """
            SELECT stop_name,
                   COALESCE(SUM(CASE WHEN event_type = 'board' THEN quantity ELSE 0 END), 0) AS boarded,
                   COALESCE(SUM(CASE WHEN event_type = 'drop' THEN quantity ELSE 0 END), 0) AS dropped,
                   COUNT(*) AS transactions
            FROM trip_transactions
            WHERE stop_name IS NOT NULL AND stop_name <> ''
            GROUP BY stop_name
            ORDER BY boarded DESC, dropped DESC, stop_name
            LIMIT 8
            """
        ).fetchall()
    ]
    return rows


# Return active bus camera configuration rows for admin monitoring.
def build_bus_camera_rows(conn):
    """Return active bus camera configuration rows for admin monitoring."""
    rows = conn.execute(
        """
        SELECT c.id,
               c.bus_id,
               b.plate_number,
               c.camera_name,
               c.stream_type,
               c.stream_url,
               c.status,
               c.is_active,
               c.last_seen_at,
               c.notes
        FROM bus_cameras c
        JOIN buses b ON b.id = c.bus_id
        WHERE c.is_active = 1
        ORDER BY b.plate_number, c.camera_name
        """
    ).fetchall()
    return [
        {
            "id": int(row["id"]),
            "bus_id": int(row["bus_id"]),
            "plate_number": row["plate_number"],
            "camera_name": row["camera_name"],
            "stream_type": row["stream_type"],
            "stream_url": row["stream_url"] or "",
            "status": row["status"],
            "is_active": bool(row["is_active"]),
            "last_seen_at": normalize_json_value(row["last_seen_at"]),
            "notes": row["notes"] or "",
        }
        for row in rows
    ]


# Ensure a bus has the default three camera records used by admin monitoring.
def ensure_default_bus_cameras(conn, bus_id, plate_number):
    """Ensure a bus has the default three camera records used by admin monitoring."""
    for camera_name, camera_purpose in DEFAULT_BUS_CAMERAS:
        existing_camera = conn.execute(
            "SELECT id FROM bus_cameras WHERE bus_id = ? AND camera_name = ?",
            (bus_id, camera_name),
        ).fetchone()
        if existing_camera:
            continue
        conn.execute(
            """
            INSERT INTO bus_cameras (
                bus_id, camera_name, stream_type, stream_url,
                status, is_active, notes, created_at
            )
            VALUES (?, ?, 'external', NULL, 'unconfigured', 1, ?, ?)
            """,
            (
                bus_id,
                camera_name,
                f"{camera_purpose} for {plate_number}. Add the live stream URL after hardware selection.",
                to_db_time(now()),
            ),
        )


# Return recent ticketing transactions for the admin audit table.
def get_recent_transaction_audit(conn, limit=20):
    """Return recent ticketing transactions for the admin audit table."""
    rows = conn.execute(
        """
        SELECT tt.recorded_at,
               tt.event_type,
               tt.passenger_type,
               tt.quantity,
               tt.stop_name,
               tt.occupancy_after,
               COALESCE(tt.fare_amount, 0) AS fare_amount,
               t.id AS trip_id,
               b.plate_number,
               r.route_name,
               u.full_name AS conductor_name
        FROM trip_transactions tt
        JOIN trips t ON t.id = tt.trip_id
        JOIN buses b ON b.id = t.bus_id
        JOIN routes r ON r.id = t.route_id
        LEFT JOIN users u ON u.id = tt.conductor_id
        ORDER BY tt.recorded_at DESC, tt.id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [dict(row) for row in rows]


# Fill missing historical fare amounts on transaction records when possible.
def backfill_missing_transaction_fares(conn):
    """Fill missing historical fare amounts on transaction records when possible."""
    rows = conn.execute(
        """
        SELECT
            tt.id,
            tt.passenger_type,
            tt.quantity,
            r.distance_km,
            r.minimum_fare,
            r.discounted_fare
        FROM trip_transactions tt
        JOIN trips t ON t.id = tt.trip_id
        JOIN routes r ON r.id = t.route_id
        WHERE tt.event_type = 'board' AND (tt.fare_amount IS NULL OR tt.fare_amount = 0)
        """
    ).fetchall()

    updates = []
    for row in rows:
        updates.append(
            (
                calculate_passenger_fare_total(
                    row["passenger_type"],
                    row["quantity"],
                    row["distance_km"],
                    row["minimum_fare"],
                    row["discounted_fare"],
                ),
                row["id"],
            )
        )

    if updates:
        conn.executemany("UPDATE trip_transactions SET fare_amount = ? WHERE id = ?", updates)


# Build trip-level audit rows and summary totals for admin review.
def build_trip_audit_summary(conn, limit=50, start_date=None, end_date=None):
    """Build trip-level audit rows and summary totals for admin review."""
    where_clauses = []
    params = []
    if start_date:
        where_clauses.append("DATE(t.started_at) >= ?")
        params.append(start_date)
    if end_date:
        where_clauses.append("DATE(t.started_at) <= ?")
        params.append(end_date)
    where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
    params.append(limit)
    rows = conn.execute(
        f"""
        SELECT
            t.id AS trip_id,
            t.status,
            t.started_at,
            t.ended_at,
            t.duration_minutes,
            t.occupancy,
            t.peak_occupancy,
            b.plate_number,
            b.capacity,
            r.route_name,
            driver.full_name AS driver_name,
            conductor.full_name AS conductor_name,
            COALESCE(tx.passengers_boarded, 0) AS passengers_boarded,
            COALESCE(tx.passengers_dropped, 0) AS passengers_dropped,
            COALESCE(tx.student_count, 0) AS student_count,
            COALESCE(tx.pwd_count, 0) AS pwd_count,
            COALESCE(tx.senior_count, 0) AS senior_count,
            COALESCE(tx.regular_count, 0) AS regular_count,
            COALESCE(tx.revenue, 0) AS revenue,
            COALESCE(tx.stops_served, 0) AS stops_served,
            COALESCE(rec.crowd_updates, 0) AS crowd_updates,
            rec.latest_stop
        FROM trips t
        JOIN buses b ON b.id = t.bus_id
        JOIN routes r ON r.id = t.route_id
        LEFT JOIN users driver ON driver.id = t.driver_id
        LEFT JOIN users conductor ON conductor.id = t.conductor_id
        LEFT JOIN (
            SELECT
                trip_id,
                COALESCE(SUM(CASE WHEN event_type = 'board' THEN quantity ELSE 0 END), 0) AS passengers_boarded,
                COALESCE(SUM(CASE WHEN event_type = 'drop' THEN quantity ELSE 0 END), 0) AS passengers_dropped,
                COALESCE(SUM(CASE WHEN event_type = 'board' AND passenger_type = 'student' THEN quantity ELSE 0 END), 0) AS student_count,
                COALESCE(SUM(CASE WHEN event_type = 'board' AND passenger_type = 'pwd' THEN quantity ELSE 0 END), 0) AS pwd_count,
                COALESCE(SUM(CASE WHEN event_type = 'board' AND passenger_type = 'senior' THEN quantity ELSE 0 END), 0) AS senior_count,
                COALESCE(SUM(CASE WHEN event_type = 'board' AND passenger_type = 'regular' THEN quantity ELSE 0 END), 0) AS regular_count,
                COALESCE(SUM(CASE WHEN event_type = 'board' THEN fare_amount ELSE 0 END), 0) AS revenue,
                COUNT(DISTINCT NULLIF(stop_name, 'Unknown')) AS stops_served
            FROM trip_transactions
            GROUP BY trip_id
        ) tx ON tx.trip_id = t.id
        LEFT JOIN (
            SELECT
                tr.trip_id,
                COUNT(*) AS crowd_updates,
                SUBSTRING_INDEX(
                    GROUP_CONCAT(tr.stop_name ORDER BY tr.recorded_at DESC, tr.id DESC SEPARATOR '||'),
                    '||',
                    1
                ) AS latest_stop
            FROM trip_records tr
            GROUP BY tr.trip_id
        ) rec ON rec.trip_id = t.id
        {where_sql}
        ORDER BY t.started_at DESC, t.id DESC
        LIMIT %s
        """,
        tuple(params),
    ).fetchall()

    summary_rows = []
    total_revenue = Decimal("0.00")
    total_boarded = 0
    total_completed = 0
    peak_revenue_trip = None

    for row in rows:
        trip_row = dict(row)
        trip_row["passengers_boarded"] = int(trip_row["passengers_boarded"] or 0)
        trip_row["passengers_dropped"] = int(trip_row["passengers_dropped"] or 0)
        trip_row["student_count"] = int(trip_row["student_count"] or 0)
        trip_row["pwd_count"] = int(trip_row["pwd_count"] or 0)
        trip_row["senior_count"] = int(trip_row["senior_count"] or 0)
        trip_row["regular_count"] = int(trip_row["regular_count"] or 0)
        trip_row["stops_served"] = int(trip_row["stops_served"] or 0)
        trip_row["crowd_updates"] = int(trip_row["crowd_updates"] or 0)
        trip_row["occupancy"] = int(trip_row["occupancy"] or 0)
        trip_row["peak_occupancy"] = int(trip_row["peak_occupancy"] or 0)
        trip_row["capacity"] = int(trip_row["capacity"] or DEFAULT_BUS_CAPACITY)
        trip_row["net_passengers"] = max(trip_row["passengers_boarded"] - trip_row["passengers_dropped"], 0)
        trip_row["load_percent"] = round((trip_row["peak_occupancy"] / max(trip_row["capacity"], 1)) * 100, 1)
        trip_row["revenue"] = float(trip_row["revenue"] or 0)
        trip_row["average_fare"] = round(trip_row["revenue"] / trip_row["passengers_boarded"], 2) if trip_row["passengers_boarded"] else 0
        trip_row["latest_stop"] = trip_row["latest_stop"] or "No stop recorded"
        summary_rows.append(trip_row)

        total_revenue += Decimal(str(trip_row["revenue"]))
        total_boarded += trip_row["passengers_boarded"]
        if trip_row["status"] == "completed":
            total_completed += 1
        if peak_revenue_trip is None or trip_row["revenue"] > peak_revenue_trip["revenue"]:
            peak_revenue_trip = trip_row

    trip_count = len(summary_rows)
    audit_summary = {
        "trip_count": trip_count,
        "completed_trip_count": total_completed,
        "active_trip_count": sum(1 for row in summary_rows if row["status"] == "active"),
        "total_boarded": total_boarded,
        "total_revenue": float(total_revenue),
        "average_trip_revenue": round(float(total_revenue) / trip_count, 2) if trip_count else 0,
        "average_trip_boarded": round(total_boarded / trip_count, 1) if trip_count else 0,
        "top_revenue_trip": peak_revenue_trip,
    }
    return summary_rows, audit_summary




# Build daily bus passenger and revenue rows for reporting.
def build_daily_bus_tabulation(conn, limit=60, start_date=None, end_date=None):
    """Build daily bus passenger and revenue rows for reporting."""
    where_clauses = []
    params = []
    if start_date:
        where_clauses.append("DATE(t.started_at) >= ?")
        params.append(start_date)
    if end_date:
        where_clauses.append("DATE(t.started_at) <= ?")
        params.append(end_date)
    where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
    params.append(limit)
    rows = conn.execute(
        f"""
        SELECT
            t.id AS trip_id,
            DATE(t.started_at) AS service_date,
            t.started_at,
            t.ended_at,
            b.plate_number,
            COALESCE(driver.full_name, 'No driver assigned') AS driver_name,
            r.route_name,
            COALESCE(SUM(CASE WHEN tt.event_type = 'board' AND tt.passenger_type = 'student' THEN tt.quantity ELSE 0 END), 0) AS student_count,
            COALESCE(SUM(CASE WHEN tt.event_type = 'board' AND tt.passenger_type = 'pwd' THEN tt.quantity ELSE 0 END), 0) AS pwd_count,
            COALESCE(SUM(CASE WHEN tt.event_type = 'board' AND tt.passenger_type = 'senior' THEN tt.quantity ELSE 0 END), 0) AS senior_count,
            COALESCE(SUM(CASE WHEN tt.event_type = 'board' AND tt.passenger_type = 'regular' THEN tt.quantity ELSE 0 END), 0) AS regular_count,
            COALESCE(SUM(CASE WHEN tt.event_type = 'board' THEN tt.quantity ELSE 0 END), 0) AS total_passengers,
            COALESCE(SUM(CASE WHEN tt.event_type = 'board' AND tt.passenger_type = 'student' THEN tt.fare_amount ELSE 0 END), 0) AS student_revenue,
            COALESCE(SUM(CASE WHEN tt.event_type = 'board' AND tt.passenger_type = 'pwd' THEN tt.fare_amount ELSE 0 END), 0) AS pwd_revenue,
            COALESCE(SUM(CASE WHEN tt.event_type = 'board' AND tt.passenger_type = 'senior' THEN tt.fare_amount ELSE 0 END), 0) AS senior_revenue,
            COALESCE(SUM(CASE WHEN tt.event_type = 'board' AND tt.passenger_type = 'regular' THEN tt.fare_amount ELSE 0 END), 0) AS regular_revenue,
            COALESCE(SUM(CASE WHEN tt.event_type = 'board' THEN tt.fare_amount ELSE 0 END), 0) AS total_revenue
        FROM trips t
        JOIN buses b ON b.id = t.bus_id
        JOIN routes r ON r.id = t.route_id
        LEFT JOIN users driver ON driver.id = t.driver_id
        LEFT JOIN trip_transactions tt ON tt.trip_id = t.id
        {where_sql}
        GROUP BY t.id, DATE(t.started_at), t.started_at, t.ended_at, b.plate_number, driver.full_name, r.route_name
        ORDER BY DATE(t.started_at) DESC, b.plate_number, t.started_at DESC, t.id DESC
        LIMIT %s
        """,
        tuple(params),
    ).fetchall()

    tabulation_rows = []
    for row in rows:
        item = dict(row)
        for key in (
            "student_count",
            "pwd_count",
            "senior_count",
            "regular_count",
            "total_passengers",
        ):
            item[key] = int(item[key] or 0)
        for key in (
            "student_revenue",
            "pwd_revenue",
            "senior_revenue",
            "regular_revenue",
            "total_revenue",
        ):
            item[key] = float(item[key] or 0)
        item["trip_id"] = int(item["trip_id"])
        item["service_date"] = item["service_date"].isoformat() if hasattr(item["service_date"], "isoformat") else str(item["service_date"])
        item["started_at"] = normalize_json_value(item["started_at"])
        item["ended_at"] = normalize_json_value(item["ended_at"]) if item.get("ended_at") else None
        item["driver_name"] = item["driver_name"] or "No driver assigned"
        item["route_name"] = item["route_name"] or "No route assigned"
        tabulation_rows.append(item)
    return tabulation_rows


# Convert report rows into compact chart labels and values.
def compress_report_timeseries(rows, value_key):
    """Convert report rows into compact chart labels and values."""
    totals = {}
    for row in rows:
        day = str(row["service_date"])
        totals[day] = totals.get(day, 0) + row[value_key]
    ordered_days = sorted(totals.keys())
    values = [totals[day] for day in ordered_days]
    if value_key.endswith("revenue"):
        values = [round(float(value), 2) for value in values]
    else:
        values = [int(value) for value in values]
    return ordered_days, values


# Prepare per-bus passenger and revenue analytics for dashboard charts.
def build_report_bus_analytics(daily_bus_rows):
    """Prepare per-bus passenger and revenue analytics for dashboard charts."""
    bus_map = {}
    totals = {
        "student_count": 0,
        "pwd_count": 0,
        "senior_count": 0,
        "regular_count": 0,
        "total_passengers": 0,
        "student_revenue": 0.0,
        "pwd_revenue": 0.0,
        "senior_revenue": 0.0,
        "regular_revenue": 0.0,
        "total_revenue": 0.0,
    }

    for row in daily_bus_rows:
        plate_number = row["plate_number"]
        bus_entry = bus_map.setdefault(
            plate_number,
            {
                "plate_number": plate_number,
                "driver_name": row["driver_name"],
                "route_name": row["route_name"],
                "labels": [],
                "passenger_totals": {"student": 0, "pwd": 0, "senior": 0, "regular": 0},
                "revenue_totals": {"student": 0.0, "pwd": 0.0, "senior": 0.0, "regular": 0.0},
                "daily_passengers": [],
                "daily_revenue": [],
            },
        )
        bus_entry["driver_name"] = row["driver_name"]
        bus_entry["route_name"] = row["route_name"]

        for source_key, target_key in (
            ("student_count", "student"),
            ("pwd_count", "pwd"),
            ("senior_count", "senior"),
            ("regular_count", "regular"),
        ):
            value = int(row[source_key] or 0)
            bus_entry["passenger_totals"][target_key] += value
            totals[source_key] += value

        for source_key, target_key in (
            ("student_revenue", "student"),
            ("pwd_revenue", "pwd"),
            ("senior_revenue", "senior"),
            ("regular_revenue", "regular"),
        ):
            value = float(row[source_key] or 0)
            bus_entry["revenue_totals"][target_key] += value
            totals[source_key] += value

        totals["total_passengers"] += int(row["total_passengers"] or 0)
        totals["total_revenue"] += float(row["total_revenue"] or 0)

    for bus_entry in bus_map.values():
        bus_rows = [row for row in daily_bus_rows if row["plate_number"] == bus_entry["plate_number"]]
        labels, daily_passengers = compress_report_timeseries(bus_rows, "total_passengers")
        _, daily_revenue = compress_report_timeseries(bus_rows, "total_revenue")
        bus_entry["labels"] = labels
        bus_entry["daily_passengers"] = daily_passengers
        bus_entry["daily_revenue"] = daily_revenue

    all_labels, all_daily_passengers = compress_report_timeseries(daily_bus_rows, "total_passengers")
    _, all_daily_revenue = compress_report_timeseries(daily_bus_rows, "total_revenue")

    return {
        "bus_options": sorted(bus_map.keys()),
        "all": {
            "labels": all_labels,
            "daily_passengers": all_daily_passengers,
            "daily_revenue": all_daily_revenue,
            "passenger_totals": {
                "student": totals["student_count"],
                "pwd": totals["pwd_count"],
                "senior": totals["senior_count"],
                "regular": totals["regular_count"],
            },
            "revenue_totals": {
                "student": totals["student_revenue"],
                "pwd": totals["pwd_revenue"],
                "senior": totals["senior_revenue"],
                "regular": totals["regular_revenue"],
            },
            "total_passengers": totals["total_passengers"],
            "total_revenue": totals["total_revenue"],
        },
        "buses": bus_map,
    }


# Group report rows into per-bus sections for tables and PDF output.
def build_bus_report_sections(fleet_rows, daily_bus_rows):
    """Group report rows into per-bus sections for tables and PDF output."""
    rows_by_bus = {}
    for row in daily_bus_rows:
        rows_by_bus.setdefault(row["plate_number"], []).append(row)

    sections = []
    for fleet in fleet_rows:
        plate_number = fleet["plate_number"]
        bus_rows = rows_by_bus.get(plate_number, [])
        sections.append(
            {
                "plate_number": plate_number,
                "status": fleet["status"],
                "trip_status": fleet["trip_status"],
                "route_name": fleet.get("route_name") or "No assigned route",
                "rows": bus_rows,
                "total_passengers": sum(int(row["total_passengers"] or 0) for row in bus_rows),
                "total_revenue": round(sum(float(row["total_revenue"] or 0) for row in bus_rows), 2),
                "passengers_by_type": {
                    "student": sum(int(row["student_count"] or 0) for row in bus_rows),
                    "pwd": sum(int(row["pwd_count"] or 0) for row in bus_rows),
                    "senior": sum(int(row["senior_count"] or 0) for row in bus_rows),
                    "regular": sum(int(row["regular_count"] or 0) for row in bus_rows),
                },
                "revenue_by_type": {
                    "student": round(sum(float(row["student_revenue"] or 0) for row in bus_rows), 2),
                    "pwd": round(sum(float(row["pwd_revenue"] or 0) for row in bus_rows), 2),
                    "senior": round(sum(float(row["senior_revenue"] or 0) for row in bus_rows), 2),
                    "regular": round(sum(float(row["regular_revenue"] or 0) for row in bus_rows), 2),
                },
            }
        )
    return sections


# Return user account rows for the admin profile directory.
def build_user_directory(conn):
    """Return user account rows for the admin profile directory."""
    rows = conn.execute(
        """
        SELECT id, username, email, role, full_name, created_at
        FROM users
        ORDER BY created_at DESC, id DESC
        """
    ).fetchall()
    return [dict(row) for row in rows]


# Build staff login/activity attendance rows from session and user data.
def build_staff_attendance(conn, limit=25):
    """Build staff login/activity attendance rows from session and user data."""
    rows = conn.execute(
        """
        SELECT
            s.login_time,
            u.id AS user_id,
            u.full_name,
            u.role,
            u.username,
            (
                SELECT t.id
                FROM trips t
                WHERE
                    ((u.role = 'driver' AND t.driver_id = u.id) OR (u.role = 'conductor' AND t.conductor_id = u.id))
                    AND DATE(t.started_at) = DATE(s.login_time)
                ORDER BY t.started_at DESC, t.id DESC
                LIMIT 1
            ) AS trip_id,
            (
                SELECT b.plate_number
                FROM trips t
                JOIN buses b ON b.id = t.bus_id
                WHERE
                    ((u.role = 'driver' AND t.driver_id = u.id) OR (u.role = 'conductor' AND t.conductor_id = u.id))
                    AND DATE(t.started_at) = DATE(s.login_time)
                ORDER BY t.started_at DESC, t.id DESC
                LIMIT 1
            ) AS plate_number,
            (
                SELECT r.route_name
                FROM trips t
                JOIN routes r ON r.id = t.route_id
                WHERE
                    ((u.role = 'driver' AND t.driver_id = u.id) OR (u.role = 'conductor' AND t.conductor_id = u.id))
                    AND DATE(t.started_at) = DATE(s.login_time)
                ORDER BY t.started_at DESC, t.id DESC
                LIMIT 1
            ) AS route_name,
            (
                SELECT t.started_at
                FROM trips t
                WHERE
                    ((u.role = 'driver' AND t.driver_id = u.id) OR (u.role = 'conductor' AND t.conductor_id = u.id))
                    AND DATE(t.started_at) = DATE(s.login_time)
                ORDER BY t.started_at DESC, t.id DESC
                LIMIT 1
            ) AS trip_started_at,
            (
                SELECT t.ended_at
                FROM trips t
                WHERE
                    ((u.role = 'driver' AND t.driver_id = u.id) OR (u.role = 'conductor' AND t.conductor_id = u.id))
                    AND DATE(t.started_at) = DATE(s.login_time)
                ORDER BY t.started_at DESC, t.id DESC
                LIMIT 1
            ) AS trip_ended_at,
            (
                SELECT t.status
                FROM trips t
                WHERE
                    ((u.role = 'driver' AND t.driver_id = u.id) OR (u.role = 'conductor' AND t.conductor_id = u.id))
                    AND DATE(t.started_at) = DATE(s.login_time)
                ORDER BY t.started_at DESC, t.id DESC
                LIMIT 1
            ) AS trip_status
        FROM sessions s
        JOIN users u ON u.id = s.user_id
        ORDER BY s.login_time DESC
        LIMIT %s
        """,
        (limit,),
    ).fetchall()

    attendance_rows = []
    for row in rows:
        attendance_row = dict(row)
        attendance_row["trip_summary"] = (
            f"{attendance_row['plate_number']} / {attendance_row['route_name']}"
            if attendance_row.get("trip_id") and attendance_row.get("plate_number") and attendance_row.get("route_name")
            else "No trip linked on login date"
        )
        attendance_rows.append(attendance_row)
    return attendance_rows


def build_admin_password_reset_alerts(conn, limit=5):
    """Return unread password reset notifications for admin attention."""
    try:
        rows = conn.execute(
            """
            SELECT
                n.id,
                n.created_at,
                n.title,
                n.message AS description,
                n.status,
                u.full_name,
                u.email,
                u.role
            FROM admin_notifications n
            LEFT JOIN users u ON u.id = n.user_id
            WHERE n.notification_type = ? AND n.status = 'unread'
            ORDER BY n.created_at DESC, n.id DESC
            LIMIT ?
            """,
            (PASSWORD_RESET_NOTIFICATION_TYPE, limit),
        ).fetchall()
    except Exception as exc:
        if "admin_notifications" not in str(exc):
            raise
        conn.rollback()
        return []
    return [dict(row) for row in rows]


def build_admin_operation_notifications(conn, limit=8):
    """Return unread internal operations notifications for admin monitoring."""
    placeholders = ", ".join(["?"] * len(ADMIN_OPERATION_NOTIFICATION_TYPES))
    try:
        rows = conn.execute(
            f"""
            SELECT id, notification_type, title, message AS description, status, created_at
            FROM admin_notifications
            WHERE notification_type IN ({placeholders}) AND status = 'unread'
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (*sorted(ADMIN_OPERATION_NOTIFICATION_TYPES), limit),
        ).fetchall()
    except Exception as exc:
        if "admin_notifications" not in str(exc):
            raise
        conn.rollback()
        return []
    return [dict(row) for row in rows]


def build_super_admin_overview(conn):
    """Build account, security, and audit data for the super admin console."""
    user_rows = build_user_directory(conn)
    role_counts = {"super_admin": 0, "admin": 0, "driver": 0, "conductor": 0}
    for user in user_rows:
        role_counts[user["role"]] = role_counts.get(user["role"], 0) + 1

    recent_logs = [
        dict(row)
        for row in conn.execute(
            """
            SELECT sl.created_at, sl.role, sl.action, sl.description, u.full_name
            FROM system_logs sl
            LEFT JOIN users u ON u.id = sl.user_id
            ORDER BY sl.created_at DESC, sl.id DESC
            LIMIT 25
            """
        ).fetchall()
    ]
    return {
        "user_rows": user_rows,
        "role_counts": role_counts,
        "total_users": len(user_rows),
        "attendance_rows": build_staff_attendance(conn, 25),
        "recent_logs": recent_logs,
    }


# Return admin fare matrix rows with route labels for display and editing.
def build_fare_matrix_rows(conn):
    """Return admin fare matrix rows with route labels for display and editing."""
    return [
        dict(row)
        for row in conn.execute(
            """
            SELECT fm.id,
                   fm.route_id,
                   r.route_name,
                   fm.origin_stop,
                   fm.destination_stop,
                   fm.regular_fare,
                   fm.discounted_fare,
                   fm.updated_at,
                   fm.created_at
            FROM fare_matrix fm
            JOIN routes r ON r.id = fm.route_id
            ORDER BY r.display_order, r.route_name, fm.origin_stop, fm.destination_stop
            """
        ).fetchall()
    ]


# Return route and stop options used by the admin fare matrix form.
def build_fare_matrix_options(conn):
    """Return route and stop options used by the admin fare matrix form."""
    route_options = [
        dict(row)
        for row in conn.execute(
            """
            SELECT id, route_name
            FROM routes
            WHERE is_published = 1
            ORDER BY display_order, route_name
            """
        ).fetchall()
    ]
    stop_options = [
        dict(row)
        for row in conn.execute(
            """
            SELECT stop_name
            FROM stops
            WHERE is_active = 1
            ORDER BY stop_name
            """
        ).fetchall()
    ]
    return route_options, stop_options


# Build editable route fare matrix sections for the admin commuter tab.
def build_fare_matrix_editor(conn):
    """Build editable route fare matrix sections for the admin commuter tab."""
    route_rows = [
        dict(row)
        for row in conn.execute(
            """
            SELECT id,
                   route_name,
                   distance_km,
                   expected_duration_minutes,
                   minimum_fare,
                   discounted_fare
            FROM routes
            WHERE is_published = 1
            ORDER BY display_order, route_name
            """
        ).fetchall()
    ]
    matrix_lookup = {
        (row["route_id"], stop_name_key(row["origin_stop"]), stop_name_key(row["destination_stop"])): dict(row)
        for row in conn.execute(
            """
            SELECT id, route_id, origin_stop, destination_stop, regular_fare, discounted_fare
            FROM fare_matrix
            """
        ).fetchall()
    }

    editor_routes = []
    for route in route_rows:
        stops = get_route_stop_details(route["route_name"])
        segments = []
        trip_context = {
            "route_id": route["id"],
            "route_name": route["route_name"],
            "distance_km": route["distance_km"],
            "expected_duration_minutes": route["expected_duration_minutes"],
            "minimum_fare": route["minimum_fare"],
            "discounted_fare": route["discounted_fare"],
        }
        for origin_index, origin_stop in enumerate(stops):
            for destination_stop in stops[origin_index + 1 :]:
                matrix_row = matrix_lookup.get(
                    (
                        route["id"],
                        stop_name_key(origin_stop["name"]),
                        stop_name_key(destination_stop["name"]),
                    )
                )
                if matrix_row:
                    fare_table = build_matrix_fare_table(matrix_row)
                    is_saved = True
                else:
                    fare_table = estimate_fare_table(
                        estimate_trip_segment_distance(trip_context, origin_stop["name"], destination_stop["name"]),
                        route["minimum_fare"],
                        route["discounted_fare"],
                    )
                    is_saved = False
                segments.append(
                    {
                        "origin_stop": origin_stop["name"],
                        "destination_stop": destination_stop["name"],
                        "regular_fare": fare_table["regular"],
                        "discounted_fare": fare_table["student"],
                        "is_saved": is_saved,
                    }
                )
        editor_routes.append(
            {
                "id": route["id"],
                "route_name": route["route_name"],
                "stop_count": len(stops),
                "segments": segments,
            }
        )
    return editor_routes


# Build the full admin dashboard payload for analytics, reports, logs, and live fleet data.
def build_admin_overview(conn, report_start_date=None, report_end_date=None):
    """Build the full admin dashboard payload for analytics, reports, logs, and live fleet data."""
    live_data = build_live_bus_data(conn)
    today = now().date().isoformat()
    yesterday = (now().date() - timedelta(days=1)).isoformat()
    report_filters = resolve_report_date_range(report_start_date, report_end_date)

    passenger_totals_row = conn.execute(
        """
        SELECT
            COALESCE(SUM(CASE WHEN event_type = 'board' AND DATE(recorded_at) = ? THEN quantity ELSE 0 END), 0) AS today_total,
            COALESCE(SUM(CASE WHEN event_type = 'board' AND DATE(recorded_at) = ? THEN quantity ELSE 0 END), 0) AS yesterday_total,
            COUNT(DISTINCT CASE WHEN event_type = 'board' AND DATE(recorded_at) = ? THEN trip_id END) AS trips_today
        FROM trip_transactions
        """,
        (today, yesterday, today),
    ).fetchone()

    records_today_row = conn.execute(
        """
        SELECT COUNT(*) AS records_today
        FROM trip_records
        WHERE DATE(recorded_at) = ?
        """,
        (today,),
    ).fetchone()

    route_rows = [
        dict(row)
        for row in conn.execute(
            """
            SELECT r.route_name,
                   r.id,
                   r.is_published,
                   r.minimum_fare,
                   r.discounted_fare,
                   COUNT(DISTINCT t.id) AS trip_count,
                   COALESCE(SUM(tx.passengers), 0) AS passengers,
                   COALESCE(ROUND(AVG(t.peak_occupancy * 100.0 / b.capacity), 1), 0) AS avg_load_percent
            FROM routes r
            LEFT JOIN trips t ON t.route_id = r.id
            LEFT JOIN buses b ON b.id = t.bus_id
            LEFT JOIN (
                SELECT trip_id,
                       COALESCE(SUM(CASE WHEN event_type = 'board' THEN quantity ELSE 0 END), 0) AS passengers
                FROM trip_transactions
                GROUP BY trip_id
            ) tx ON tx.trip_id = t.id
            WHERE r.is_published = 1
            GROUP BY r.id
            ORDER BY passengers DESC, r.route_name
            """
        ).fetchall()
    ]
    report_trip_join_filters = []
    report_route_params = []
    if report_filters["start"]:
        report_trip_join_filters.append("DATE(t.started_at) >= ?")
        report_route_params.append(report_filters["start"])
    if report_filters["end"]:
        report_trip_join_filters.append("DATE(t.started_at) <= ?")
        report_route_params.append(report_filters["end"])
    report_trip_join_sql = ""
    if report_trip_join_filters:
        report_trip_join_sql = " AND " + " AND ".join(report_trip_join_filters)
    report_route_rows = [
        dict(row)
        for row in conn.execute(
            f"""
            SELECT r.route_name,
                   r.id,
                   r.is_published,
                   r.minimum_fare,
                   r.discounted_fare,
                   COUNT(DISTINCT t.id) AS trip_count,
                   COALESCE(SUM(tx.passengers), 0) AS passengers,
                   COALESCE(ROUND(AVG(t.peak_occupancy * 100.0 / b.capacity), 1), 0) AS avg_load_percent
            FROM routes r
            LEFT JOIN trips t ON t.route_id = r.id{report_trip_join_sql}
            LEFT JOIN buses b ON b.id = t.bus_id
            LEFT JOIN (
                SELECT trip_id,
                       COALESCE(SUM(CASE WHEN event_type = 'board' THEN quantity ELSE 0 END), 0) AS passengers
                FROM trip_transactions
                GROUP BY trip_id
            ) tx ON tx.trip_id = t.id
            WHERE r.is_published = 1
            GROUP BY r.id
            ORDER BY passengers DESC, r.route_name
            """,
            tuple(report_route_params),
        ).fetchall()
    ]

    live_bus_rows = []
    for bus in live_data["buses"]:
        live_bus_rows.append(
            {
                "plate_number": bus["id"],
                "route_name": bus["direction"],
                "driver": bus["driver"],
                "location": bus["nextStop"],
                "occupancy": bus["passengers"],
                "capacity": bus["capacity"],
                "crowd_level": bus["crowdLevel"],
                "lat": bus["lat"],
                "lng": bus["lng"],
            }
        )

    camera_rows = build_bus_camera_rows(conn)
    daily_rows = [
        dict(row)
        for row in conn.execute(
            """
            SELECT DATE(recorded_at) AS day,
                   COALESCE(SUM(CASE WHEN event_type = 'board' THEN quantity ELSE 0 END), 0) AS total
            FROM trip_transactions
            WHERE DATE(recorded_at) >= DATE_SUB(%s, INTERVAL 6 DAY)
            GROUP BY DATE(recorded_at)
            ORDER BY DATE(recorded_at)
            """,
            (today,),
        ).fetchall()
    ]
    daily_labels, daily_values = fill_missing_days(daily_rows, "day", 7)

    hourly_rows = [
        dict(row)
        for row in conn.execute(
            """
            SELECT CONCAT(LPAD(HOUR(recorded_at), 2, '0'), ':00') AS hour_label,
                   COALESCE(SUM(CASE WHEN event_type = 'board' THEN quantity ELSE 0 END), 0) AS total
            FROM trip_transactions
            WHERE DATE(recorded_at) = ?
            GROUP BY HOUR(recorded_at)
            ORDER BY HOUR(recorded_at)
            """,
            (today,),
        ).fetchall()
    ]
    hourly_lookup = {row["hour_label"]: row["total"] for row in hourly_rows}
    hourly_labels = [f"{str(hour).zfill(2)}:00" for hour in range(6, 22)]
    hourly_values = [int(hourly_lookup.get(label, 0) or 0) for label in hourly_labels]

    type_row = conn.execute(
        """
        SELECT
            COALESCE(SUM(CASE WHEN event_type = 'board' AND passenger_type = 'student' THEN quantity ELSE 0 END), 0) AS students,
            COALESCE(SUM(CASE WHEN event_type = 'board' AND passenger_type = 'pwd' THEN quantity ELSE 0 END), 0) AS pwd,
            COALESCE(SUM(CASE WHEN event_type = 'board' AND passenger_type = 'senior' THEN quantity ELSE 0 END), 0) AS senior,
            COALESCE(SUM(CASE WHEN event_type = 'board' AND passenger_type = 'regular' THEN quantity ELSE 0 END), 0) AS regular
        FROM trip_transactions
        WHERE DATE(recorded_at) >= DATE_SUB(%s, INTERVAL 6 DAY)
        """,
        (today,),
    ).fetchone()

    recent_logs = [
        dict(row)
        for row in conn.execute(
            """
            SELECT sl.created_at, sl.role, sl.action, sl.description, u.full_name
            FROM system_logs sl
            LEFT JOIN users u ON u.id = sl.user_id
            ORDER BY sl.created_at DESC, sl.id DESC
            LIMIT 10
            """
        ).fetchall()
    ]
    attendance_rows = build_staff_attendance(conn, 25)

    fleet_rows = [
        dict(row)
        for row in conn.execute(
            """
            SELECT b.id, b.plate_number, b.status, b.capacity, b.route_color, b.notes,
                   r.route_name,
                   COALESCE(t.status, 'idle') AS trip_status,
                   COALESCE(t.occupancy, 0) AS occupancy,
                   COALESCE(t.peak_occupancy, 0) AS peak_occupancy
            FROM buses b
            LEFT JOIN trips t ON t.id = (
                SELECT id
                FROM trips
                WHERE bus_id = b.id
                ORDER BY started_at DESC, id DESC
                LIMIT 1
            )
            LEFT JOIN routes r ON r.id = t.route_id
            ORDER BY b.plate_number
            """
        ).fetchall()
    ]
    stop_rows = build_stop_analytics(conn)
    recent_transaction_audit = get_recent_transaction_audit(conn, 20)
    trip_audit_rows, audit_summary = build_trip_audit_summary(
        conn,
        50,
        report_filters["start"] or None,
        report_filters["end"] or None,
    )
    daily_bus_rows = build_daily_bus_tabulation(
        conn,
        60,
        report_filters["start"] or None,
        report_filters["end"] or None,
    )
    report_bus_analytics = build_report_bus_analytics(daily_bus_rows)
    bus_report_sections = build_bus_report_sections(fleet_rows, daily_bus_rows)
    for fleet in fleet_rows:
        plate_number = fleet["plate_number"]
        if plate_number not in report_bus_analytics["buses"]:
            report_bus_analytics["buses"][plate_number] = {
                "plate_number": plate_number,
                "driver_name": "No driver assigned",
                "route_name": fleet.get("route_name") or "No assigned route",
                "labels": [],
                "passenger_totals": {"student": 0, "pwd": 0, "senior": 0, "regular": 0},
                "revenue_totals": {"student": 0.0, "pwd": 0.0, "senior": 0.0, "regular": 0.0},
                "daily_passengers": [],
                "daily_revenue": [],
            }
    report_bus_analytics["bus_options"] = sorted(report_bus_analytics["buses"].keys())
    service_alerts = get_active_service_alerts(conn)
    archived_service_alerts = get_archived_service_alerts(conn)
    password_reset_alerts = build_admin_password_reset_alerts(conn)
    operation_notifications = build_admin_operation_notifications(conn)
    user_rows = build_user_directory(conn)

    peak_hour_label = hourly_labels[0]
    peak_hour_value = 0
    if hourly_values:
        peak_hour_value = max(hourly_values)
        peak_hour_label = hourly_labels[hourly_values.index(peak_hour_value)]

    overview = {
        "today_total": int(passenger_totals_row["today_total"] or 0),
        "yesterday_total": int(passenger_totals_row["yesterday_total"] or 0),
        "trips_today": int(passenger_totals_row["trips_today"] or 0),
        "records_today": int(records_today_row["records_today"] or 0),
        "active_bus_count": live_data["active_bus_count"],
        "avg_crowd": live_data["avg_crowd"],
        "low_count": live_data["low_count"],
        "medium_count": live_data["medium_count"],
        "high_crowd_count": live_data["high_count"],
        "route_rows": route_rows,
        "report_route_rows": report_route_rows,
        "live_bus_rows": live_bus_rows,
        "camera_rows": camera_rows,
        "fleet_rows": fleet_rows,
        "recent_logs": recent_logs,
        "attendance_rows": attendance_rows,
        "service_alerts": service_alerts,
        "archived_service_alerts": archived_service_alerts,
        "password_reset_alerts": password_reset_alerts,
        "operation_notifications": operation_notifications,
        "fare_matrix_rows": [],
        "fare_route_options": [],
        "fare_stop_options": [],
        "fare_matrix_editor": [],
        "stop_rows": stop_rows,
        "recent_transaction_audit": recent_transaction_audit,
        "trip_audit_rows": trip_audit_rows,
        "daily_bus_rows": daily_bus_rows,
        "bus_report_sections": bus_report_sections,
        "report_bus_analytics": report_bus_analytics,
        "report_filters": report_filters,
        "audit_summary": audit_summary,
        "user_rows": user_rows,
        "peak_hour_label": peak_hour_label,
        "peak_hour_value": peak_hour_value,
        "charts": {
            "daily_labels": daily_labels,
            "daily_values": daily_values,
            "hourly_labels": hourly_labels,
            "hourly_values": hourly_values,
            "route_labels": [row["route_name"] for row in route_rows],
            "route_values": [row["passengers"] for row in route_rows],
            "stop_labels": [row["stop_name"] for row in stop_rows],
            "stop_values": [int(row["boarded"] or 0) for row in stop_rows],
            "mix_labels": ["Students", "PWD", "Senior", "Regular"],
            "mix_values": [int(type_row["students"]), int(type_row["pwd"]), int(type_row["senior"]), int(type_row["regular"])],
            "live_buses": live_data["buses"],
            "bus_cameras": camera_rows,
            "report_bus_analytics": report_bus_analytics,
        },
    }
    overview["insights"] = generate_ai_insights(overview)
    return overview


# Build the smaller live-refresh payload used by the admin dashboard API.
def build_admin_live_payload(conn):
    """Build the smaller live-refresh payload used by the admin dashboard API."""
    overview = build_admin_overview(conn)
    return {
        "active_bus_count": overview["active_bus_count"],
        "avg_crowd": overview["avg_crowd"],
        "live_bus_rows": overview["live_bus_rows"],
        "live_buses": overview["charts"]["live_buses"],
        "bus_cameras": overview["camera_rows"],
        "high_crowd_count": overview["high_crowd_count"],
        "password_reset_alerts": overview["password_reset_alerts"],
        "operation_notifications": overview["operation_notifications"],
    }


# Build driver dashboard data including active trip, available buses, and GPS metrics.
def build_driver_overview(conn, driver_id):
    """Build driver dashboard data including active trip, available buses, and GPS metrics."""
    driver = conn.execute(
        "SELECT id, username, full_name FROM users WHERE id = ?",
        (driver_id,),
    ).fetchone()
    active_trip = get_active_trip_for_driver(conn, driver_id)

    available_buses = [
        dict(row)
        for row in conn.execute(
            """
            SELECT *
            FROM buses
            WHERE status = 'online'
              AND id NOT IN (
                SELECT bus_id
                FROM trips
                WHERE status = 'active'
              )
            ORDER BY plate_number
            """
        ).fetchall()
    ]

    routes = [dict(row) for row in conn.execute("SELECT * FROM routes WHERE is_published = 1 ORDER BY display_order, route_name").fetchall()]

    trip_metrics = {
        "occupancy": 0,
        "capacity": DEFAULT_BUS_CAPACITY,
        "next_stop": "No active trip",
        "trip_duration": "00:00:00",
        "crowd_level": "Low",
        "updates_count": 0,
        "last_gps_at": None,
    }

    if active_trip:
        latest_record = get_latest_trip_record(conn, active_trip["id"])
        latest_gps = conn.execute(
            """
            SELECT latitude, longitude, recorded_at
            FROM gps_logs
            WHERE trip_id = ?
            ORDER BY recorded_at DESC, id DESC
            LIMIT 1
            """,
            (active_trip["id"],),
        ).fetchone()
        started_at = from_db_time(active_trip["started_at"])
        duration = now() - started_at if started_at else timedelta(0)
        total_seconds = max(int(duration.total_seconds()), 0)
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        seconds = total_seconds % 60
        current_stop_details = get_trip_current_stop_details(
            active_trip,
            dict(latest_gps) if latest_gps else None,
            latest_record["stop_name"] if latest_record else None,
        )

        if current_stop_details:
            next_stop = current_stop_details["name"]
        elif latest_gps and latest_gps["latitude"] is not None and latest_gps["longitude"] is not None:
            next_stop = derive_trip_location_label(active_trip, float(latest_gps["latitude"]), float(latest_gps["longitude"]))
        else:
            next_stop = "Waiting for GPS location"

        trip_metrics = {
            "occupancy": active_trip["occupancy"],
            "capacity": active_trip["capacity"],
            "next_stop": next_stop,
            "trip_duration": f"{hours:02d}:{minutes:02d}:{seconds:02d}",
            "crowd_level": classify_capacity(active_trip["occupancy"], active_trip["capacity"]),
            "updates_count": conn.execute("SELECT COUNT(*) AS total_count FROM trip_records WHERE trip_id = ?", (active_trip["id"],)).fetchone()["total_count"],
            "last_gps_at": normalize_json_value(latest_gps["recorded_at"]) if latest_gps else None,
        }

    return {
        "driver": dict(driver) if driver else None,
        "active_trip": normalize_json_value(active_trip) if active_trip else None,
        "available_buses": available_buses,
        "routes": routes,
        "trip_metrics": trip_metrics,
    }


# Build conductor terminal data for active trip monitoring and ticketing.
def build_conductor_overview(conn, conductor_id):
    """Build conductor terminal data for active trip monitoring and ticketing."""
    active_trip = get_active_trip_for_conductor(conn, conductor_id)
    available_trips = [
        dict(row)
        for row in conn.execute(
            """
            SELECT t.id, b.plate_number, r.route_name, u.full_name AS driver_name, t.occupancy
            FROM trips t
            JOIN buses b ON b.id = t.bus_id
            JOIN routes r ON r.id = t.route_id
            LEFT JOIN users u ON u.id = t.driver_id
            WHERE t.status = 'active'
              AND r.is_published = 1
              AND (t.conductor_id IS NULL OR t.conductor_id = ?)
            ORDER BY t.started_at DESC, t.id DESC
            """,
            (conductor_id,),
        ).fetchall()
    ]
    buses = [
        dict(row)
        for row in conn.execute(
            """
            SELECT *
            FROM buses
            WHERE status = 'online'
              AND id NOT IN (SELECT bus_id FROM trips WHERE status = 'active')
            ORDER BY plate_number
            """
        ).fetchall()
    ]
    routes = [dict(row) for row in conn.execute("SELECT * FROM routes WHERE is_published = 1 ORDER BY display_order, route_name").fetchall()]

    transaction_form = {
        "destination_stop": "",
        "passenger_type": "",
    }
    trip_summary = None
    latest_gps = None
    recent_transactions = []
    destination_manifest = []
    current_stop = "Waiting for location"
    destination_options = []
    fare_preview = 0.0
    today_summary = get_conductor_today_summary(conn, conductor_id)

    if active_trip:
        latest_record = get_latest_trip_record(conn, active_trip["id"])
        latest_gps = get_latest_trip_gps(conn, active_trip["id"])
        current_stop_details = get_trip_current_stop_details(
            active_trip,
            latest_gps,
            latest_record["stop_name"] if latest_record else None,
        )
        if current_stop_details:
            current_stop = current_stop_details["name"]
        destination_options = []
        for option in get_trip_destination_options(active_trip, current_stop):
            destination_options.append(
                {
                    **option,
                    "fare_guide": build_segment_fare_table(conn, active_trip, current_stop, option["name"]),
                }
            )
        destination_manifest = sync_trip_occupancy_from_destinations(conn, active_trip, current_stop)
        if latest_record:
            trip_summary = dict(latest_record)
            trip_summary["stop_name"] = current_stop
        else:
            trip_summary = {"stop_name": current_stop}

        recent_transactions = get_recent_trip_transactions(conn, active_trip["id"], 8)

    occupancy = capacity_details(
        active_trip["occupancy"] if active_trip else 0,
        active_trip["capacity"] if active_trip else DEFAULT_BUS_CAPACITY,
    )

    if active_trip and transaction_form["destination_stop"]:
        fare_preview = calculate_trip_fare_total(
            conn,
            active_trip,
            transaction_form["passenger_type"] or "regular",
            1,
            current_stop,
            transaction_form["destination_stop"],
        )

    return {
        "active_trip": active_trip,
        "available_trips": available_trips,
        "buses": buses,
        "routes": routes,
        "transaction_form": transaction_form,
        "trip_summary": trip_summary,
        "current_stop": current_stop,
        "destination_options": destination_options,
        "destination_manifest": destination_manifest,
        "fare_preview": fare_preview,
        "today_summary": today_summary,
        "recent_transactions": recent_transactions,
        "capacity": occupancy,
        "latest_position": normalize_json_value(latest_gps) if active_trip and latest_gps else None,
        "workflow_notes": [
            {
                "title": "Ticketing + crowd merge candidate",
                "body": "The current manual counter works, but it creates extra device switching for the conductor. The next redesign should combine passenger type entry and ticketing into one capture flow.",
            },
            {
                "title": "Low-friction counting direction",
                "body": "Keep GPS and stop detection automatic from the driver trip, then reduce manual conductor actions to the fewest taps possible per boarding event.",
            },
            {
                "title": "Camera counting is future scope",
                "body": "Camera-based passenger counting can later enrich validation and analytics, but it still needs planning, device integration, and data model changes before it should affect the workflow.",
            },
        ],
    }


# Return the default forward and reverse Gajoda-Cabanatuan route records.
def get_default_routes():
    """Return the default forward and reverse Gajoda-Cabanatuan route records."""
    forward_coords = [[stop["lat"], stop["lng"]] for stop in FORWARD_ROUTE_STOPS]
    reverse_coords = [[stop["lat"], stop["lng"]] for stop in REVERSE_ROUTE_STOPS]
    return [
        (
            FORWARD_ROUTE_NAME,
            FORWARD_ROUTE_STOPS[0]["name"],
            FORWARD_ROUTE_STOPS[-1]["name"],
            CORRIDOR_DISTANCE_KM,
            CORRIDOR_TRAVEL_MINUTES,
            15.0,
            12.0,
            1,
            json.dumps(forward_coords),
        ),
        (
            REVERSE_ROUTE_NAME,
            REVERSE_ROUTE_STOPS[0]["name"],
            REVERSE_ROUTE_STOPS[-1]["name"],
            CORRIDOR_DISTANCE_KM,
            CORRIDOR_TRAVEL_MINUTES,
            15.0,
            12.0,
            2,
            json.dumps(reverse_coords),
        ),
    ]


# Insert missing default routes and refresh route geometry without overwriting admin-edited fares.
def sync_default_routes(conn):
    """Insert missing default routes and refresh route geometry without overwriting admin-edited fares."""
    default_routes = get_default_routes()
    default_names = [route[0] for route in default_routes]
    for route_name, start_point, end_point, distance_km, expected_duration_minutes, minimum_fare, discounted_fare, display_order, coords_json in default_routes:
        existing = conn.execute(
            "SELECT id FROM routes WHERE route_name = ?",
            (route_name,),
        ).fetchone()
        if existing:
            conn.execute(
                """
                UPDATE routes
                SET start_point = ?, end_point = ?, distance_km = ?, expected_duration_minutes = ?, display_order = ?, coords_json = ?
                WHERE id = ?
                """,
                (start_point, end_point, distance_km, expected_duration_minutes, display_order, coords_json, existing["id"]),
            )
        else:
            conn.execute(
                """
                INSERT INTO routes (route_name, start_point, end_point, distance_km, expected_duration_minutes, minimum_fare, discounted_fare, display_order, coords_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (route_name, start_point, end_point, distance_km, expected_duration_minutes, minimum_fare, discounted_fare, display_order, coords_json),
            )
    placeholders = ",".join(["%s"] * len(default_names))
    conn.execute(
        f"UPDATE routes SET is_published = CASE WHEN route_name IN ({placeholders}) THEN 1 ELSE 0 END",
        tuple(default_names),
    )


# Insert or update default corridor stops and route-stop ordering.
def sync_default_stops(conn):
    """Insert or update default corridor stops and route-stop ordering."""
    for stop in CORRIDOR_STOP_DETAILS:
        existing_stop = conn.execute(
            "SELECT id FROM stops WHERE stop_name = ?",
            (stop["name"],),
        ).fetchone()
        if existing_stop:
            conn.execute(
                """
                UPDATE stops
                SET latitude = ?, longitude = ?, landmark = ?, is_active = 1
                WHERE id = ?
                """,
                (
                    stop["lat"],
                    stop["lng"],
                    stop.get("landmark") or "Gajoda-Cabanatuan corridor stop",
                    existing_stop["id"],
                ),
            )
        else:
            conn.execute(
                """
                INSERT INTO stops (stop_name, latitude, longitude, landmark, is_active)
                VALUES (?, ?, ?, ?, 1)
                """,
                (
                    stop["name"],
                    stop["lat"],
                    stop["lng"],
                    stop.get("landmark") or "Gajoda-Cabanatuan corridor stop",
                ),
            )

    route_stop_sets = {
        FORWARD_ROUTE_NAME: FORWARD_ROUTE_STOPS,
        REVERSE_ROUTE_NAME: REVERSE_ROUTE_STOPS,
    }
    for route_name, route_stops in route_stop_sets.items():
        route_row = conn.execute(
            "SELECT id FROM routes WHERE route_name = ?",
            (route_name,),
        ).fetchone()
        if not route_row:
            continue
        route_id = route_row["id"]
        conn.execute("DELETE FROM route_stops WHERE route_id = ?", (route_id,))
        for sequence, stop in enumerate(route_stops, start=1):
            stop_row = conn.execute(
                "SELECT id FROM stops WHERE stop_name = ?",
                (stop["name"],),
            ).fetchone()
            if not stop_row:
                continue
            conn.execute(
                """
                INSERT INTO route_stops (route_id, stop_id, stop_sequence, minutes_from_start)
                VALUES (?, ?, ?, ?)
                """,
                (route_id, stop_row["id"], sequence, int(stop["minutes_from_start"])),
            )


# Seed default users, buses, cameras, routes, stops, and service alerts.
def seed_demo_data():
    """Seed default users, buses, cameras, routes, stops, and service alerts."""
    conn = get_db()
    sync_default_routes(conn)
    sync_default_stops(conn)
    refresh_route_stop_cache(conn)
    conn.execute(
        """
        UPDATE users
        SET full_name = ?
        WHERE username = 'admin'
        """,
        ("Marites Mariano",),
    )
    users = [
        ("superadmin", "superadmin@example.com", "superadmin123", "super_admin", "System Super Admin"),
        ("admin", "admin@example.com", "admin123", "admin", "Marites Mariano"),
        ("driver1", "driver1@example.com", "driver123", "driver", "Juan Dela Cruz"),
        ("driver2", "driver2@example.com", "driver123", "driver", "Rico Mendoza"),
        ("conductor1", "conductor1@example.com", "conduct123", "conductor", "Ana Ramos"),
    ]
    for username, email, password, role, full_name in users:
        existing_user = conn.execute(
            "SELECT id, password FROM users WHERE username = ?",
            (username,),
        ).fetchone()
        if existing_user:
            ensure_password_hashed(conn, existing_user["id"], existing_user["password"])
        else:
            conn.execute(
                """
                INSERT INTO users (username, email, password, role, full_name, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (username, email, generate_password_hash(password), role, full_name, to_db_time(now())),
            )

    buses = [
        ("MB-01", DEFAULT_BUS_CAPACITY, "offline", "#0f766e", "Primary unit"),
        ("MB-02", DEFAULT_BUS_CAPACITY, "offline", "#1d4ed8", "Secondary unit"),
        ("MB-03", DEFAULT_BUS_CAPACITY, "offline", "#c2410c", "Reserve unit"),
        ("MB-04", DEFAULT_BUS_CAPACITY, "offline", "#7c3aed", "Standby unit"),
    ]
    for plate_number, capacity, status, route_color, notes in buses:
        existing_bus = conn.execute(
            "SELECT id FROM buses WHERE plate_number = ?",
            (plate_number,),
        ).fetchone()
        if existing_bus:
            conn.execute(
                """
                UPDATE buses
                SET route_color = ?, notes = ?
                WHERE id = ?
                """,
                (route_color, notes, existing_bus["id"]),
            )
        else:
            conn.execute(
                """
                INSERT INTO buses (plate_number, capacity, status, route_color, notes)
                VALUES (?, ?, ?, ?, ?)
                """,
                (plate_number, capacity, status, route_color, notes),
            )

    for bus in conn.execute("SELECT id, plate_number FROM buses ORDER BY plate_number").fetchall():
        ensure_default_bus_cameras(conn, bus["id"], bus["plate_number"])

    seeded_trip_ids = [
        row["id"]
        for row in conn.execute(
            """
            SELECT id
            FROM trips
            WHERE notes IN ('Completed seeded trip', 'Morning live route', 'Rush interval service')
            """
        ).fetchall()
    ]
    if seeded_trip_ids:
        placeholders = ",".join(["?"] * len(seeded_trip_ids))
        conn.execute(f"DELETE FROM gps_logs WHERE trip_id IN ({placeholders})", seeded_trip_ids)
        conn.execute(f"DELETE FROM trip_records WHERE trip_id IN ({placeholders})", seeded_trip_ids)
        conn.execute(f"DELETE FROM trips WHERE id IN ({placeholders})", seeded_trip_ids)

    conn.execute(
        """
        DELETE FROM system_logs
        WHERE action = 'Seed Complete'
           OR description IN (
               'Initial analytics dataset was generated for dashboard testing.',
               'Juan Dela Cruz started MB-01 on Gajoda Terminal to Cabanatuan.',
               'Ana Ramos attached crowd analytics to MB-01.',
               'Rico Mendoza started MB-02 on Cabanatuan to Gajoda Terminal.'
           )
        """
    )

    default_alerts = [
        ("Gajoda terminal boarding advisory", "Board early at the Gajoda garage and office terminal during peak periods because crowding can build before trips reach Cabanatuan.", "warning", FORWARD_ROUTE_NAME, "Gajoda Garage and Office"),
        ("Tracker ETA notice", "Next-bus estimates depend on recent GPS updates from active trips and may pause when a unit goes offline.", "info", None, None),
    ]
    for title, message, severity, route_name, stop_name in default_alerts:
        existing_alert = conn.execute(
            "SELECT id FROM service_alerts WHERE title = ?",
            (title,),
        ).fetchone()
        if not existing_alert:
            route_row = conn.execute("SELECT id FROM routes WHERE route_name = ?", (route_name,)).fetchone() if route_name else None
            expires_at = to_db_time(resolve_alert_expiration("end_of_day"))
            conn.execute(
                """
                INSERT INTO service_alerts (route_id, stop_name, title, message, severity, is_active, created_by, created_at, expires_at)
                VALUES (?, ?, ?, ?, ?, 1, NULL, ?, ?)
                """,
                (route_row["id"] if route_row else None, stop_name, title, message, severity, to_db_time(now()), expires_at),
            )

    conn.commit()
    backfill_missing_transaction_fares(conn)
    conn.commit()
    conn.close()


def initialize_database():
    """Create or update database tables and seed local starter data."""
    bootstrap_db()
    seed_demo_data()


if os.environ.get("CODEXMBS_BOOTSTRAP_ON_IMPORT", "").lower() in {"1", "true", "yes"}:
    initialize_database()


@app.after_request
def add_protected_cache_headers(response):
    """Prevent browsers from caching role-protected pages and API responses."""
    if request.path.startswith(PROTECTED_PATH_PREFIXES):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


@app.route("/")
# Render the public homepage with live service preview and commuter notices.
def landing():
    """Render the public homepage with live service preview and commuter notices."""
    conn = get_db()
    live_data = build_live_bus_data(conn)
    commuter_data = build_public_commuter_data(conn, live_data)
    conn.close()
    commuter_payload = normalize_json_value(commuter_data)
    preview_buses = [bus for bus in live_data["buses"] if bus["status"] == "online"][:3]
    primary_route = commuter_payload["routes"][0] if commuter_payload["routes"] else None
    return render_template(
        "landing/index.html",
        active_bus_count=live_data["active_bus_count"],
        avg_crowd=live_data["avg_crowd"],
        low_count=live_data["low_count"],
        medium_count=live_data["medium_count"],
        high_count=live_data["high_count"],
        preview_buses=preview_buses,
        buses_json=json.dumps(live_data["buses"]),
        primary_route=primary_route,
        commuter_data_json=json.dumps(commuter_payload),
        service_alerts=commuter_payload["serviceAlerts"],
        stop_directory_preview=commuter_payload["stopDirectory"][:6],
        route_cards=commuter_payload["routes"],
    )


@app.route("/track")
# Render the public commuter tracker with map, route planner, and bus list.
def tracker():
    """Render the public commuter tracker with map, route planner, and bus list."""
    conn = get_db()
    live_data = build_live_bus_data(conn)
    commuter_data = build_public_commuter_data(conn, live_data)
    conn.close()
    commuter_payload = normalize_json_value(commuter_data)
    return render_template(
        "landing/tracker.html",
        buses_json=json.dumps(live_data["buses"]),
        commuter_data_json=json.dumps(commuter_payload),
        service_alerts=commuter_payload["serviceAlerts"],
        active_bus_count=live_data["active_bus_count"],
        avg_crowd=live_data["avg_crowd"],
        low_count=live_data["low_count"],
        medium_count=live_data["medium_count"],
        high_count=live_data["high_count"],
    )


@app.route("/api/public-commuter")
# Return public route, stop, fare, and alert data as JSON.
def api_public_commuter():
    """Return public route, stop, fare, and alert data as JSON."""
    conn = get_db()
    live_data = build_live_bus_data(conn)
    payload = build_public_commuter_data(conn, live_data)
    conn.close()
    return jsonify(normalize_json_value(payload))


@app.route("/login", methods=["GET", "POST"])
# Authenticate users and redirect them to their role-specific dashboard.
def login():
    """Authenticate users and redirect them to their role-specific dashboard."""
    if request.method == "POST":
        submitted = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        conn = get_db()
        user = conn.execute(
            "SELECT * FROM users WHERE username = ? OR email = ?",
            (submitted, submitted),
        ).fetchone()

        if user and verify_password(user["password"], password):
            session["user_id"] = user["id"]
            session["role"] = user["role"]
            ensure_password_hashed(conn, user["id"], user["password"])
            conn.execute(
                "INSERT INTO sessions (user_id, login_time) VALUES (?, ?)",
                (user["id"], to_db_time(now())),
            )
            log_event(conn, user["id"], user["role"], "Login", f"{user['full_name']} signed in.")
            conn.commit()
            conn.close()

            return redirect(url_for(role_home_endpoint(user["role"])))

        conn.close()
        return render_template("login.html", error="Invalid login credentials.")

    return render_template("login.html")


@app.route("/forgot-password", methods=["GET", "POST"])
# Notify admins that a staff account needs a password reset.
def forgot_password():
    """Send a password reset link when Gmail is configured, otherwise notify admins."""
    if request.method == "POST":
        account_identifier = (
            request.form.get("account_identifier", "") or request.form.get("email", "")
        ).strip()

        if not account_identifier:
            return render_template("forgot_password.html", error="Enter your account username or email.")

        conn = None
        try:
            conn = get_db()
            user = conn.execute(
                "SELECT id, username, email, full_name, role FROM users WHERE username = ? OR email = ?",
                (account_identifier, account_identifier),
            ).fetchone()
            record_password_reset_request(conn, user, account_identifier)
            if user and gmail_reset_configured():
                token, expires_at = create_password_reset_token(conn, user["id"])
                reset_url = build_password_reset_url(token)
                send_password_reset_email(user, reset_url, expires_at)
                log_event(
                    conn,
                    user["id"],
                    user["role"],
                    "Password Reset Email Sent",
                    f"Password reset email was sent to {user['email']}.",
                )
                conn.commit()
            broadcast_live_tracking_update(conn)
        except Exception:
            app.logger.exception("Password reset request could not be recorded.")
            error_detail = "Email settings could not be verified. Check the Flask terminal for the Gmail SMTP error."
            if app.config.get("TESTING") or app.debug:
                import traceback
                error_detail = traceback.format_exc().splitlines()[-1]
            return render_template(
                "forgot_password.html",
                error=f"We could not send the reset request. {error_detail}",
            )
        finally:
            if conn:
                conn.close()

        if user and gmail_reset_configured():
            message = "If that account is registered, a password reset link has been sent to its email address."
        elif user:
            message = "Your request has been sent to the super administrator because Gmail email is not configured."
        else:
            message = "If that account is registered, reset instructions will be sent or reviewed by the super administrator."
        return render_template("forgot_password.html", message=message)
    return render_template("forgot_password.html")


@app.route("/reset-password/<token>", methods=["GET", "POST"])
# Let users set a new password from a valid one-time email token.
def reset_password(token):
    """Let users set a new password from a valid one-time email token."""
    conn = get_db()
    token_row = get_valid_password_reset_token(conn, token)

    if not token_row:
        conn.close()
        return render_template(
            "reset_password.html",
            error="This reset link is invalid or expired. Request a new password reset link.",
            token_valid=False,
        )

    if request.method == "POST":
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")
        if len(password) < 8:
            conn.close()
            return render_template(
                "reset_password.html",
                token=token,
                token_valid=True,
                error="Use at least 8 characters for the new password.",
            )
        if password != confirm_password:
            conn.close()
            return render_template(
                "reset_password.html",
                token=token,
                token_valid=True,
                error="The password confirmation does not match.",
            )

        conn.execute(
            "UPDATE users SET password = ? WHERE id = ?",
            (generate_password_hash(password), token_row["user_id"]),
        )
        conn.execute(
            "UPDATE password_reset_tokens SET used_at = ? WHERE id = ?",
            (to_db_time(now()), token_row["id"]),
        )
        log_event(
            conn,
            token_row["user_id"],
            token_row["role"],
            "Password Reset Completed",
            f"{token_row['full_name']} reset their account password from an email link.",
        )
        conn.commit()
        conn.close()
        return render_template(
            "reset_password.html",
            token_valid=False,
            message="Password updated. You can now sign in with your new password.",
            login_url=url_for("login"),
        )

    conn.close()
    return render_template("reset_password.html", token=token, token_valid=True)


@app.route("/super-admin", methods=["GET", "POST"])
@require_role("super_admin")
# Render and process the super admin console for accounts, reset requests, and audit review.
def super_admin_dashboard():
    """Render and process the super admin console for account and security actions."""
    conn = get_db()
    active_tab = request.args.get("tab", "profiles")
    if active_tab == "password-resets":
        active_tab = "profiles"

    if request.method == "POST":
        action = request.form.get("action")
        if action == "dismiss_password_reset_notification":
            abort(403)
        if action == "create_profile":
            redirect_tab = request.form.get("redirect_tab", "profiles").strip() or "profiles"
            username = request.form.get("username", "").strip()
            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")
            role = request.form.get("role", "").strip().lower()
            full_name = request.form.get("full_name", "").strip()

            if username and email and password and full_name and role in {"super_admin", "admin", "driver", "conductor"}:
                existing_user = conn.execute(
                    "SELECT id FROM users WHERE username = ? OR email = ?",
                    (username, email),
                ).fetchone()
                if not existing_user:
                    conn.execute(
                        """
                        INSERT INTO users (username, email, password, role, full_name, created_at)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (username, email, generate_password_hash(password), role, full_name, to_db_time(now())),
                    )
                    log_event(
                        conn,
                        session["user_id"],
                        "super_admin",
                        "Profile Created",
                        f"Created {role} profile for {full_name} ({username}).",
                    )
                    conn.commit()
                    conn.close()
                    return redirect(url_for("super_admin_dashboard", tab=redirect_tab))
        elif action == "update_profile":
            redirect_tab = request.form.get("redirect_tab", "profiles").strip() or "profiles"
            user_id_raw = request.form.get("user_id", "").strip()
            username = request.form.get("username", "").strip()
            email = request.form.get("email", "").strip().lower()
            role = request.form.get("role", "").strip().lower()
            full_name = request.form.get("full_name", "").strip()

            if user_id_raw.isdigit() and username and email and full_name and role in {"super_admin", "admin", "driver", "conductor"}:
                user_id = int(user_id_raw)
                user_row = conn.execute("SELECT id, full_name, role FROM users WHERE id = ?", (user_id,)).fetchone()
                duplicate_user = conn.execute(
                    "SELECT id FROM users WHERE (username = ? OR email = ?) AND id <> ?",
                    (username, email, user_id),
                ).fetchone()
                super_admin_count = conn.execute("SELECT COUNT(*) AS total FROM users WHERE role = 'super_admin'").fetchone()["total"]
                admin_count = conn.execute("SELECT COUNT(*) AS total FROM users WHERE role = 'admin'").fetchone()["total"]
                demotes_last_super_admin = user_row and user_row["role"] == "super_admin" and role != "super_admin" and super_admin_count <= 1
                demotes_last_admin = user_row and user_row["role"] == "admin" and role != "admin" and admin_count <= 1
                if user_row and not duplicate_user and not demotes_last_super_admin and not demotes_last_admin:
                    conn.execute(
                        """
                        UPDATE users
                        SET full_name = ?, username = ?, email = ?, role = ?
                        WHERE id = ?
                        """,
                        (full_name, username, email, role, user_id),
                    )
                    log_event(conn, session["user_id"], "super_admin", "Profile Updated", f"Updated profile for {full_name} ({username}).")
                    conn.commit()
                    conn.close()
                    return redirect(url_for("super_admin_dashboard", tab=redirect_tab))
        elif action == "delete_profile":
            redirect_tab = request.form.get("redirect_tab", "profiles").strip() or "profiles"
            user_id_raw = request.form.get("user_id", "").strip()
            if user_id_raw.isdigit():
                user_id = int(user_id_raw)
                user_row = conn.execute("SELECT id, username, full_name, role FROM users WHERE id = ?", (user_id,)).fetchone()
                super_admin_count = conn.execute("SELECT COUNT(*) AS total FROM users WHERE role = 'super_admin'").fetchone()["total"]
                admin_count = conn.execute("SELECT COUNT(*) AS total FROM users WHERE role = 'admin'").fetchone()["total"]
                deletes_last_super_admin = user_row and user_row["role"] == "super_admin" and super_admin_count <= 1
                deletes_last_admin = user_row and user_row["role"] == "admin" and admin_count <= 1
                if user_row and user_id != session["user_id"] and not deletes_last_super_admin and not deletes_last_admin:
                    conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
                    conn.execute("UPDATE trips SET driver_id = NULL WHERE driver_id = ?", (user_id,))
                    conn.execute("UPDATE trips SET conductor_id = NULL WHERE conductor_id = ?", (user_id,))
                    conn.execute("UPDATE trip_transactions SET conductor_id = NULL WHERE conductor_id = ?", (user_id,))
                    conn.execute("UPDATE service_alerts SET created_by = NULL WHERE created_by = ?", (user_id,))
                    conn.execute("UPDATE system_logs SET user_id = NULL WHERE user_id = ?", (user_id,))
                    conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
                    log_event(conn, session["user_id"], "super_admin", "Profile Deleted", f"Deleted profile for {user_row['full_name']} ({user_row['username']}).")
                    conn.commit()
                    conn.close()
                    return redirect(url_for("super_admin_dashboard", tab=redirect_tab))

    overview = build_super_admin_overview(conn)
    conn.close()
    return render_template("admin/super_admin_dashboard.html", overview=overview, active_tab=active_tab)


@app.route("/admin", methods=["GET", "POST"])
@require_role("admin")
# Render and process the admin dashboard for fleet, camera, route, alert, and user actions.
def admin_dashboard():
    """Render and process the admin dashboard for fleet, camera, route, alert, and user actions."""
    conn = get_db()
    active_tab = request.args.get("tab", "analytics")
    if active_tab in {"profiles", "password-resets"}:
        active_tab = "analytics"

    if request.method == "POST":
        action = request.form.get("action")
        if action in {"create_profile", "update_profile", "delete_profile", "dismiss_password_reset_notification"}:
            abort(403)
        if action == "dismiss_admin_notification":
            notification_id_raw = request.form.get("notification_id", "").strip()
            redirect_tab = request.form.get("redirect_tab", "operations").strip() or "operations"
            if notification_id_raw.isdigit():
                notification_id = int(notification_id_raw)
                placeholders = ", ".join(["?"] * len(ADMIN_OPERATION_NOTIFICATION_TYPES))
                notification_row = conn.execute(
                    f"""
                    SELECT id, title
                    FROM admin_notifications
                    WHERE id = ? AND notification_type IN ({placeholders}) AND status = 'unread'
                    """,
                    (notification_id, *sorted(ADMIN_OPERATION_NOTIFICATION_TYPES)),
                ).fetchone()
                if notification_row:
                    conn.execute(
                        """
                        UPDATE admin_notifications
                        SET status = 'read', read_at = ?
                        WHERE id = ?
                        """,
                        (to_db_time(now()), notification_id),
                    )
                    log_event(
                        conn,
                        session["user_id"],
                        "admin",
                        "Admin Notification Dismissed",
                        f"Dismissed operations notification #{notification_id}.",
                    )
                    conn.commit()
                    conn.close()
                    return redirect(url_for("admin_dashboard", tab=redirect_tab))
        if action == "update_bus_status":
            bus_id_raw = request.form.get("bus_id", "").strip()
            next_status = request.form.get("status", "").strip().lower()
            redirect_tab = request.form.get("redirect_tab", "analytics").strip() or "analytics"

            if bus_id_raw.isdigit() and next_status in {"online", "offline", "maintenance"}:
                bus_id = int(bus_id_raw)
                bus_row = conn.execute(
                    "SELECT plate_number FROM buses WHERE id = ?",
                    (bus_id,),
                ).fetchone()

                if bus_row:
                    conn.execute(
                        """
                        UPDATE buses
                        SET status = ?
                        WHERE id = ?
                        """,
                        (next_status, bus_id),
                    )
                    log_event(
                        conn,
                        session["user_id"],
                        "admin",
                        "Bus Status Updated",
                        f"Bus {bus_row['plate_number']} marked as {next_status}.",
                    )
                    conn.commit()
                    conn.close()
                    return redirect(url_for("admin_dashboard", tab=redirect_tab))
        elif action == "add_bus":
            plate_number = " ".join(request.form.get("plate_number", "").strip().upper().split())
            capacity = max(to_non_negative_int(request.form.get("capacity"), DEFAULT_BUS_CAPACITY), 1)
            status = request.form.get("status", "offline").strip().lower()
            notes = request.form.get("notes", "").strip() or None
            redirect_tab = request.form.get("redirect_tab", "operations").strip() or "operations"

            if plate_number and status in {"online", "offline", "maintenance"}:
                existing_bus = conn.execute(
                    "SELECT id FROM buses WHERE plate_number = ?",
                    (plate_number,),
                ).fetchone()
                if not existing_bus:
                    insert_result = conn.execute(
                        """
                        INSERT INTO buses (plate_number, capacity, status, route_color, notes)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (plate_number, capacity, status, "#1d4ed8", notes),
                    )
                    ensure_default_bus_cameras(conn, insert_result.lastrowid, plate_number)
                    log_event(
                        conn,
                        session["user_id"],
                        "admin",
                        "Bus Added",
                        f"Bus {plate_number} was added with {capacity}-passenger capacity.",
                    )
                    conn.commit()
                    conn.close()
                    return redirect(url_for("admin_dashboard", tab=redirect_tab))
        elif action == "update_bus_details":
            bus_id_raw = request.form.get("bus_id", "").strip()
            plate_number = " ".join(request.form.get("plate_number", "").strip().upper().split())
            capacity = max(to_non_negative_int(request.form.get("capacity"), DEFAULT_BUS_CAPACITY), 1)
            status = request.form.get("status", "offline").strip().lower()
            notes = request.form.get("notes", "").strip() or None
            redirect_tab = request.form.get("redirect_tab", "add-bus").strip() or "add-bus"

            if bus_id_raw.isdigit() and plate_number and status in {"online", "offline", "maintenance"}:
                bus_id = int(bus_id_raw)
                bus_row = conn.execute(
                    "SELECT plate_number FROM buses WHERE id = ?",
                    (bus_id,),
                ).fetchone()
                duplicate_row = conn.execute(
                    "SELECT id FROM buses WHERE plate_number = ? AND id <> ?",
                    (plate_number, bus_id),
                ).fetchone()
                if bus_row and not duplicate_row:
                    conn.execute(
                        """
                        UPDATE buses
                        SET plate_number = ?,
                            capacity = ?,
                            status = ?,
                            notes = ?
                        WHERE id = ?
                        """,
                        (plate_number, capacity, status, notes, bus_id),
                    )
                    ensure_default_bus_cameras(conn, bus_id, plate_number)
                    log_event(
                        conn,
                        session["user_id"],
                        "admin",
                        "Bus Details Updated",
                        f"Bus {bus_row['plate_number']} was updated to {plate_number} with {capacity} seats.",
                    )
                    conn.commit()
                    conn.close()
                    return redirect(url_for("admin_dashboard", tab=redirect_tab))
        elif action == "update_camera_settings":
            camera_id_raw = request.form.get("camera_id", "").strip()
            redirect_tab = request.form.get("redirect_tab", "operations").strip() or "operations"
            camera_name = request.form.get("camera_name", "").strip() or "Passenger Cabin"
            stream_type = request.form.get("stream_type", "external").strip().lower()
            stream_url = request.form.get("stream_url", "").strip() or None
            status = request.form.get("status", "unconfigured").strip().lower()
            notes = request.form.get("notes", "").strip() or None

            if camera_id_raw.isdigit() and stream_type in CAMERA_STREAM_TYPES and status in CAMERA_STATUSES:
                camera_id = int(camera_id_raw)
                camera_row = conn.execute(
                    """
                    SELECT c.id, c.camera_name, c.last_seen_at, b.plate_number
                    FROM bus_cameras c
                    JOIN buses b ON b.id = c.bus_id
                    WHERE c.id = ?
                    """,
                    (camera_id,),
                ).fetchone()
                if camera_row:
                    last_seen_at = to_db_time(now()) if status == "online" else camera_row["last_seen_at"]
                    conn.execute(
                        """
                        UPDATE bus_cameras
                        SET camera_name = ?,
                            stream_type = ?,
                            stream_url = ?,
                            status = ?,
                            last_seen_at = ?,
                            notes = ?
                        WHERE id = ?
                        """,
                        (camera_name, stream_type, stream_url, status, last_seen_at, notes, camera_id),
                    )
                    log_event(
                        conn,
                        session["user_id"],
                        "admin",
                        "Camera Settings Updated",
                        f"Camera {camera_row['camera_name']} for bus {camera_row['plate_number']} was set to {status}.",
                    )
                    conn.commit()
                    conn.close()
                    return redirect(url_for("admin_dashboard", tab=redirect_tab))
        elif action == "update_route_settings":
            route_id_raw = request.form.get("route_id", "").strip()
            redirect_tab = request.form.get("redirect_tab", "commuter").strip() or "commuter"
            if route_id_raw.isdigit():
                route_id = int(route_id_raw)
                is_published = 1 if request.form.get("is_published") == "1" else 0
                minimum_fare = max(to_float(request.form.get("minimum_fare"), 15.0), 1.0)
                discounted_fare = max(to_float(request.form.get("discounted_fare"), 12.0), 1.0)
                route_row = conn.execute("SELECT route_name FROM routes WHERE id = ?", (route_id,)).fetchone()
                if route_row:
                    conn.execute(
                        """
                        UPDATE routes
                        SET is_published = ?, minimum_fare = ?, discounted_fare = ?
                        WHERE id = ?
                        """,
                        (is_published, minimum_fare, discounted_fare, route_id),
                    )
                    log_event(
                        conn,
                        session["user_id"],
                        "admin",
                        "Route Settings Updated",
                        f"Route {route_row['route_name']} was {'published' if is_published else 'hidden'} with fares set to regular PHP {minimum_fare:.2f} and discounted PHP {discounted_fare:.2f}.",
                    )
                    conn.commit()
                    conn.close()
                    return redirect(url_for("admin_dashboard", tab=redirect_tab))
        elif action == "save_fare_matrix_table":
            redirect_tab = request.form.get("redirect_tab", "commuter").strip() or "commuter"
            route_id_raw = request.form.get("route_id", "").strip()
            if route_id_raw.isdigit():
                route_id = int(route_id_raw)
                route_row = conn.execute(
                    "SELECT id, route_name FROM routes WHERE id = ? AND is_published = 1",
                    (route_id,),
                ).fetchone()
                if route_row:
                    origin_stops = request.form.getlist("origin_stop")
                    destination_stops = request.form.getlist("destination_stop")
                    regular_fares = request.form.getlist("regular_fare")
                    discounted_fares = request.form.getlist("discounted_fare")
                    saved_count = 0
                    saved_at = to_db_time(now())
                    for origin_stop, destination_stop, regular_value, discounted_value in zip(
                        origin_stops,
                        destination_stops,
                        regular_fares,
                        discounted_fares,
                    ):
                        origin_stop = " ".join(origin_stop.strip().split())
                        destination_stop = " ".join(destination_stop.strip().split())
                        if not is_valid_trip_segment(route_row, origin_stop, destination_stop):
                            continue
                        regular_fare = max(to_float(regular_value, 0.0), 1.0)
                        discounted_fare = max(to_float(discounted_value, 0.0), 1.0)
                        conn.execute(
                            """
                            INSERT INTO fare_matrix (
                                route_id, origin_stop, destination_stop, regular_fare, discounted_fare, created_at, updated_at
                            )
                            VALUES (?, ?, ?, ?, ?, ?, ?)
                            ON DUPLICATE KEY UPDATE
                                regular_fare = VALUES(regular_fare),
                                discounted_fare = VALUES(discounted_fare),
                                updated_at = VALUES(updated_at)
                            """,
                            (
                                route_id,
                                origin_stop,
                                destination_stop,
                                regular_fare,
                                discounted_fare,
                                saved_at,
                                saved_at,
                            ),
                        )
                        saved_count += 1
                    if saved_count:
                        log_event(
                            conn,
                            session["user_id"],
                            "admin",
                            "Fare Matrix Table Saved",
                            f"Saved {saved_count} fare matrix rows for {route_row['route_name']}.",
                        )
                        conn.commit()
                        conn.close()
                        return redirect(url_for("admin_dashboard", tab=redirect_tab))
        elif action == "upsert_fare_matrix":
            redirect_tab = request.form.get("redirect_tab", "commuter").strip() or "commuter"
            route_id_raw = request.form.get("route_id", "").strip()
            origin_stop = " ".join(request.form.get("origin_stop", "").strip().split())
            destination_stop = " ".join(request.form.get("destination_stop", "").strip().split())
            regular_fare = max(to_float(request.form.get("regular_fare"), 0.0), 1.0)
            discounted_fare = max(to_float(request.form.get("discounted_fare"), 0.0), 1.0)
            if route_id_raw.isdigit() and origin_stop and destination_stop:
                route_id = int(route_id_raw)
                route_row = conn.execute(
                    "SELECT id, route_name FROM routes WHERE id = ? AND is_published = 1",
                    (route_id,),
                ).fetchone()
                if route_row and is_valid_trip_segment(route_row, origin_stop, destination_stop):
                    conn.execute(
                        """
                        INSERT INTO fare_matrix (
                            route_id, origin_stop, destination_stop, regular_fare, discounted_fare, created_at, updated_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        ON DUPLICATE KEY UPDATE
                            regular_fare = VALUES(regular_fare),
                            discounted_fare = VALUES(discounted_fare),
                            updated_at = VALUES(updated_at)
                        """,
                        (
                            route_id,
                            origin_stop,
                            destination_stop,
                            regular_fare,
                            discounted_fare,
                            to_db_time(now()),
                            to_db_time(now()),
                        ),
                    )
                    log_event(
                        conn,
                        session["user_id"],
                        "admin",
                        "Fare Matrix Updated",
                        f"Set {route_row['route_name']} fare from {origin_stop} to {destination_stop} to regular PHP {regular_fare:.2f} and discounted PHP {discounted_fare:.2f}.",
                    )
                    conn.commit()
                    conn.close()
                    return redirect(url_for("admin_dashboard", tab=redirect_tab))
        elif action == "delete_fare_matrix":
            redirect_tab = request.form.get("redirect_tab", "commuter").strip() or "commuter"
            fare_matrix_id_raw = request.form.get("fare_matrix_id", "").strip()
            if fare_matrix_id_raw.isdigit():
                fare_matrix_id = int(fare_matrix_id_raw)
                fare_row = conn.execute(
                    """
                    SELECT fm.id, fm.origin_stop, fm.destination_stop, r.route_name
                    FROM fare_matrix fm
                    JOIN routes r ON r.id = fm.route_id
                    WHERE fm.id = ?
                    """,
                    (fare_matrix_id,),
                ).fetchone()
                if fare_row:
                    conn.execute("DELETE FROM fare_matrix WHERE id = ?", (fare_matrix_id,))
                    log_event(
                        conn,
                        session["user_id"],
                        "admin",
                        "Fare Matrix Deleted",
                        f"Deleted {fare_row['route_name']} fare from {fare_row['origin_stop']} to {fare_row['destination_stop']}.",
                    )
                    conn.commit()
                    conn.close()
                    return redirect(url_for("admin_dashboard", tab=redirect_tab))
        elif action == "create_service_alert":
            redirect_tab = request.form.get("redirect_tab", "commuter").strip() or "commuter"
            title = request.form.get("title", "").strip()
            message = request.form.get("message", "").strip()
            severity = request.form.get("severity", "info").strip().lower()
            duration = request.form.get("duration", "end_of_day").strip()
            route_id_raw = request.form.get("route_id", "").strip()
            stop_name = request.form.get("stop_name", "").strip() or None
            if title and message and severity in {"info", "warning", "critical"}:
                route_id = int(route_id_raw) if route_id_raw.isdigit() else None
                expires_at = resolve_alert_expiration(duration)
                conn.execute(
                    """
                    INSERT INTO service_alerts (trip_id, route_id, stop_name, title, message, severity, is_active, created_by, created_at, expires_at)
                    VALUES (NULL, ?, ?, ?, ?, ?, 1, ?, ?, ?)
                    """,
                    (route_id, stop_name, title, message, severity, session["user_id"], to_db_time(now()), to_db_time(expires_at)),
                )
                log_event(conn, session["user_id"], "admin", "Service Alert Created", f"Service alert '{title}' was published until {to_db_time(expires_at)}.")
                conn.commit()
                conn.close()
                return redirect(url_for("admin_dashboard", tab=redirect_tab))
        elif action == "clear_service_alert":
            alert_id_raw = request.form.get("alert_id", "").strip()
            redirect_tab = request.form.get("redirect_tab", "commuter").strip() or "commuter"
            if alert_id_raw.isdigit():
                alert_id = int(alert_id_raw)
                alert_row = conn.execute("SELECT title FROM service_alerts WHERE id = ?", (alert_id,)).fetchone()
                if alert_row:
                    conn.execute("DELETE FROM service_alerts WHERE id = ?", (alert_id,))
                    log_event(conn, session["user_id"], "admin", "Service Alert Deleted", f"Service alert '{alert_row['title']}' was deleted.")
                    conn.commit()
                    conn.close()
                    return redirect(url_for("admin_dashboard", tab=redirect_tab))
        elif action == "dismiss_password_reset_notification":
            notification_id_raw = request.form.get("notification_id", "").strip()
            redirect_tab = request.form.get("redirect_tab", "password-resets").strip() or "password-resets"
            if notification_id_raw.isdigit():
                notification_id = int(notification_id_raw)
                notification_row = conn.execute(
                    """
                    SELECT id, title, message
                    FROM admin_notifications
                    WHERE id = ? AND notification_type = ? AND status = 'unread'
                    """,
                    (notification_id, PASSWORD_RESET_NOTIFICATION_TYPE),
                ).fetchone()
                if notification_row:
                    conn.execute(
                        """
                        UPDATE admin_notifications
                        SET status = 'read', read_at = ?
                        WHERE id = ?
                        """,
                        (to_db_time(now()), notification_id),
                    )
                    log_event(
                        conn,
                        session["user_id"],
                        "admin",
                        "Password Reset Notification Dismissed",
                        f"Dismissed password reset notification #{notification_id}.",
                    )
                    conn.commit()
                    conn.close()
                    return redirect(url_for("admin_dashboard", tab=redirect_tab))
        elif action == "create_profile":
            redirect_tab = request.form.get("redirect_tab", "profiles").strip() or "profiles"
            username = request.form.get("username", "").strip()
            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")
            role = request.form.get("role", "").strip().lower()
            full_name = request.form.get("full_name", "").strip()

            if username and email and password and full_name and role in {"admin", "driver", "conductor"}:
                existing_user = conn.execute(
                    "SELECT id FROM users WHERE username = ? OR email = ?",
                    (username, email),
                ).fetchone()
                if not existing_user:
                    conn.execute(
                        """
                        INSERT INTO users (username, email, password, role, full_name, created_at)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (username, email, generate_password_hash(password), role, full_name, to_db_time(now())),
                    )
                    log_event(
                        conn,
                        session["user_id"],
                        "admin",
                        "Profile Created",
                        f"Created {role} profile for {full_name} ({username}).",
                    )
                    conn.commit()
                    conn.close()
                    return redirect(url_for("admin_dashboard", tab=redirect_tab))
        elif action == "update_profile":
            redirect_tab = request.form.get("redirect_tab", "profiles").strip() or "profiles"
            user_id_raw = request.form.get("user_id", "").strip()
            username = request.form.get("username", "").strip()
            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")
            role = request.form.get("role", "").strip().lower()
            full_name = request.form.get("full_name", "").strip()

            if user_id_raw.isdigit() and username and email and full_name and role in {"admin", "driver", "conductor"}:
                user_id = int(user_id_raw)
                user_row = conn.execute("SELECT id, full_name, role FROM users WHERE id = ?", (user_id,)).fetchone()
                duplicate_user = conn.execute(
                    "SELECT id FROM users WHERE (username = ? OR email = ?) AND id <> ?",
                    (username, email, user_id),
                ).fetchone()
                admin_count = conn.execute("SELECT COUNT(*) AS total FROM users WHERE role = 'admin'").fetchone()["total"]
                demotes_last_admin = user_row and user_row["role"] == "admin" and role != "admin" and admin_count <= 1
                if user_row and not duplicate_user and not demotes_last_admin:
                    if password:
                        conn.execute(
                            """
                            UPDATE users
                            SET full_name = ?, username = ?, email = ?, password = ?, role = ?
                            WHERE id = ?
                            """,
                            (full_name, username, email, generate_password_hash(password), role, user_id),
                        )
                    else:
                        conn.execute(
                            """
                            UPDATE users
                            SET full_name = ?, username = ?, email = ?, role = ?
                            WHERE id = ?
                            """,
                            (full_name, username, email, role, user_id),
                        )
                    log_event(conn, session["user_id"], "admin", "Profile Updated", f"Updated profile for {full_name} ({username}).")
                    conn.commit()
                    conn.close()
                    return redirect(url_for("admin_dashboard", tab=redirect_tab))
        elif action == "delete_profile":
            redirect_tab = request.form.get("redirect_tab", "profiles").strip() or "profiles"
            user_id_raw = request.form.get("user_id", "").strip()
            if user_id_raw.isdigit():
                user_id = int(user_id_raw)
                user_row = conn.execute("SELECT id, username, full_name, role FROM users WHERE id = ?", (user_id,)).fetchone()
                admin_count = conn.execute("SELECT COUNT(*) AS total FROM users WHERE role = 'admin'").fetchone()["total"]
                if user_row and user_id != session["user_id"] and not (user_row["role"] == "admin" and admin_count <= 1):
                    conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
                    conn.execute("UPDATE trips SET driver_id = NULL WHERE driver_id = ?", (user_id,))
                    conn.execute("UPDATE trips SET conductor_id = NULL WHERE conductor_id = ?", (user_id,))
                    conn.execute("UPDATE trip_transactions SET conductor_id = NULL WHERE conductor_id = ?", (user_id,))
                    conn.execute("UPDATE service_alerts SET created_by = NULL WHERE created_by = ?", (user_id,))
                    conn.execute("UPDATE system_logs SET user_id = NULL WHERE user_id = ?", (user_id,))
                    conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
                    log_event(conn, session["user_id"], "admin", "Profile Deleted", f"Deleted profile for {user_row['full_name']} ({user_row['username']}).")
                    conn.commit()
                    conn.close()
                    return redirect(url_for("admin_dashboard", tab=redirect_tab))

    overview = build_admin_overview(
        conn,
        request.args.get("report_start"),
        request.args.get("report_end"),
    )
    conn.close()
    return render_template("admin/admin_dashboard.html", overview=overview, active_tab=active_tab)


@app.route("/api/live-buses")
# Return live bus data for public and dashboard map refreshes.
def api_live_buses():
    """Return live bus data for public and dashboard map refreshes."""
    conn = get_db()
    live_data = build_live_bus_data(conn)
    conn.close()
    return jsonify(normalize_json_value(live_data))


@app.route("/api/admin/live")
@require_role("admin")
# Return admin-only live fleet and camera monitoring data.
def api_admin_live():
    """Return admin-only live fleet and camera monitoring data."""
    conn = get_db()
    payload = build_admin_live_payload(conn)
    conn.close()
    return jsonify(normalize_json_value(payload))


@socketio.on("connect")
def handle_socket_connect():
    """Subscribe socket clients to live tracking update channels."""
    join_room(LIVE_TRACKING_ROOM)
    user_id = session.get("user_id")
    if not user_id:
        return

    conn = get_db()
    try:
        user = conn.execute("SELECT role FROM users WHERE id = ?", (user_id,)).fetchone()
        if user and user["role"] in {"admin", "super_admin"}:
            join_room(ADMIN_LIVE_ROOM)
    finally:
        conn.close()


def broadcast_live_tracking_update(conn):
    """Push live tracking payloads to connected map clients."""
    try:
        socketio.emit(
            "live_buses:update",
            normalize_json_value(build_live_bus_data(conn)),
            to=LIVE_TRACKING_ROOM,
        )
        socketio.emit(
            "admin_live:update",
            normalize_json_value(build_admin_live_payload(conn)),
            to=ADMIN_LIVE_ROOM,
        )
    except Exception as error:
        app.logger.warning("Live tracking socket broadcast failed: %s", error)


@app.route("/api/admin/cameras")
@require_role("admin")
# Return all active bus camera records for the admin camera view.
def api_admin_cameras():
    """Return all active bus camera records for the admin camera view."""
    conn = get_db()
    cameras = build_bus_camera_rows(conn)
    conn.close()
    return jsonify({"bus_cameras": normalize_json_value(cameras)})


@app.route("/api/admin/cameras/<int:bus_id>")
@require_role("admin")
# Return camera records for one selected bus.
def api_admin_bus_cameras(bus_id):
    """Return camera records for one selected bus."""
    conn = get_db()
    cameras = [camera for camera in build_bus_camera_rows(conn) if camera["bus_id"] == bus_id]
    conn.close()
    return jsonify({"bus_cameras": normalize_json_value(cameras)})


@app.route("/admin/report.pdf")
@require_role({"admin", "super_admin"})
# Download the current admin analytics report as a PDF file.
def admin_report():
    """Download the current admin analytics report as a PDF file."""
    conn = get_db()
    overview = build_admin_overview(
        conn,
        request.args.get("report_start"),
        request.args.get("report_end"),
    )
    conn.close()

    try:
        output = build_admin_pdf_report(overview)
    except Exception as exc:
        app.logger.exception("Admin PDF report export failed; serving compact fallback PDF.")
        output = build_admin_pdf_report_fallback(overview, str(exc))
    return Response(
        output.getvalue(),
        mimetype="application/pdf",
        headers={"Content-Disposition": "attachment; filename=gajoda-crowd-analytics-report.pdf"},
    )


@app.route("/dashboard")
@require_role("driver")
# Render the driver trip-control dashboard.
def driver_dashboard():
    """Render the driver trip-control dashboard."""
    conn = get_db()
    overview = build_driver_overview(conn, session["user_id"])
    conn.close()
    return render_template("driver/driver_dashboard.html", overview=overview)


@app.route("/tracker-device")
@require_role("driver")
# Render the driver GPS terminal page for live location transmission.
def tracker_device():
    """Render the driver GPS terminal page for live location transmission."""
    conn = get_db()
    overview = build_driver_overview(conn, session["user_id"])
    conn.close()
    return render_template("driver/tracker_device.html", overview=overview)


@app.route("/start_trip", methods=["POST"])
@require_role("driver")
# Start a new driver trip for an available online bus and selected route.
def start_trip():
    """Start a new driver trip for an available online bus and selected route."""
    conn = get_db()
    active_trip = get_active_trip_for_driver(conn, session["user_id"])
    if active_trip:
        conn.close()
        return jsonify({"error": "Driver already has an active trip."}), 400

    bus_id = to_non_negative_int(request.form.get("bus_id", 0), 0)
    route_id = to_non_negative_int(request.form.get("route_id", 0), 0)
    trip_source = request.form.get("source", "").strip().lower()
    source_label = "tracker device" if trip_source == "tracker-device" else "driver dashboard"

    bus_row = conn.execute("SELECT * FROM buses WHERE id = ? AND status = 'online'", (bus_id,)).fetchone()
    route_row = conn.execute("SELECT * FROM routes WHERE id = ? AND is_published = 1", (route_id,)).fetchone()
    active_bus = conn.execute("SELECT id FROM trips WHERE bus_id = ? AND status = 'active'", (bus_id,)).fetchone()

    if not bus_row or not route_row:
        conn.close()
        return jsonify({"error": "Select a valid bus and route."}), 400

    if active_bus:
        conn.close()
        return jsonify({"error": "This bus is already running an active trip."}), 400

    started_at = now()
    cursor = conn.execute(
        """
        INSERT INTO trips (
            driver_id, bus_id, route_id, status,
            started_at, scheduled_end, occupancy, peak_occupancy, notes
        )
        VALUES (?, ?, ?, 'active', ?, ?, 0, 0, ?)
        """,
        (
            session["user_id"],
            bus_id,
            route_id,
            to_db_time(started_at),
            to_db_time(started_at + timedelta(minutes=route_row["expected_duration_minutes"])),
            f"Started from {source_label}",
        ),
    )
    trip_id = cursor.lastrowid
    log_event(conn, session["user_id"], "driver", "Trip Started", f"Driver started {bus_row['plate_number']} on {route_row['route_name']} from the {source_label}.")
    create_admin_notification_once(
        conn,
        "trip_started",
        session["user_id"],
        "Trip started",
        f"{bus_row['plate_number']} started {route_row['route_name']} as trip #{trip_id} from the {source_label}.",
        f"trip #{trip_id}",
    )
    conn.commit()
    sync_trip_service_alert(conn, trip_id, True)
    conn.commit()
    conn.close()
    return jsonify({"success": True, "trip_id": trip_id})


@app.route("/end_trip", methods=["POST"])
@require_role("driver")
# Complete the driver's current active trip and save duration.
def end_trip():
    """Complete the driver's current active trip and save duration."""
    conn = get_db()
    trip = get_active_trip_for_driver(conn, session["user_id"])

    if not trip:
        conn.close()
        return jsonify({"error": "No active trip found."}), 400

    if trip.get("conductor_id"):
        conductor = conn.execute(
            "SELECT full_name FROM users WHERE id = ?",
            (trip["conductor_id"],),
        ).fetchone()
        conductor_name = conductor["full_name"] if conductor else "the assigned conductor"
        conn.close()
        return jsonify(
            {
                "error": (
                    f"Cannot end this trip yet because {conductor_name} still has the conductor terminal "
                    "attached to this bus. Ask the conductor to stop monitoring/end their terminal first, "
                    "then try End Trip again."
                )
            }
        ), 409

    started_at = from_db_time(trip["started_at"])
    duration_minutes = int(max((now() - started_at).total_seconds(), 0) // 60) if started_at else trip["duration_minutes"]
    conn.execute(
        """
        UPDATE trips
        SET status = 'completed',
            ended_at = ?,
            duration_minutes = ?
        WHERE id = ?
        """,
        (to_db_time(now()), duration_minutes, trip["id"]),
    )
    sync_trip_service_alert(conn, trip["id"], False)
    log_event(conn, session["user_id"], "driver", "Trip Ended", f"Driver completed trip #{trip['id']} on {trip['plate_number']}.")
    create_admin_notification_once(
        conn,
        "trip_ended",
        session["user_id"],
        "Trip ended",
        f"{trip['plate_number']} completed trip #{trip['id']} after {duration_minutes} minute(s). Peak load: {int(trip.get('peak_occupancy') or 0)}/{int(trip.get('capacity') or DEFAULT_BUS_CAPACITY)} passengers.",
        f"trip #{trip['id']}",
    )
    conn.commit()
    conn.close()
    return jsonify({"success": True})


@app.route("/driver/location", methods=["POST"])
@require_role("driver")
# Receive driver GPS coordinates and store them for live tracking.
def driver_location():
    """Receive driver GPS coordinates and store them for live tracking."""
    conn = get_db()
    trip = get_active_trip_for_driver(conn, session["user_id"])

    if not trip:
        conn.close()
        return jsonify({"error": "No active trip found."}), 400

    payload = request.get_json(silent=True) or {}
    latitude = payload.get("latitude")
    longitude = payload.get("longitude")

    if latitude is None or longitude is None:
        conn.close()
        return jsonify({"error": "Latitude and longitude are required."}), 400

    latitude, longitude, coordinate_error = parse_gps_coordinates(latitude, longitude)
    if coordinate_error:
        conn.close()
        return jsonify({"error": coordinate_error}), 400

    current_stop_details = record_trip_gps_location(conn, trip, latitude, longitude, trip.get("conductor_id"))
    current_stop = (
        current_stop_details["name"]
        if current_stop_details
        else derive_trip_location_label(trip, latitude, longitude)
    )
    conn.commit()
    broadcast_live_tracking_update(conn)
    conn.close()
    return jsonify(
        {
            "success": True,
            "current_stop": current_stop,
            "latitude": latitude,
            "longitude": longitude,
            "recorded_at": normalize_json_value(to_db_time(now())),
        }
    )


@app.route("/conductor/location", methods=["POST"])
@require_role("conductor")
# Receive conductor GPS coordinates as a fallback live tracking source.
def conductor_location():
    """Receive conductor GPS coordinates as a fallback live tracking source."""
    conn = get_db()
    conductor_id = session["user_id"]
    trip = get_active_trip_for_conductor(conn, conductor_id)

    if not trip:
        conn.close()
        return jsonify({"error": "No active trip found."}), 400

    payload = request.get_json(silent=True) or {}
    latitude = payload.get("latitude")
    longitude = payload.get("longitude")

    if latitude is None or longitude is None:
        conn.close()
        return jsonify({"error": "Latitude and longitude are required."}), 400

    latitude, longitude, coordinate_error = parse_gps_coordinates(latitude, longitude)
    if coordinate_error:
        conn.close()
        return jsonify({"error": coordinate_error}), 400

    record_trip_gps_location(conn, trip, latitude, longitude, conductor_id)
    conn.commit()
    broadcast_live_tracking_update(conn)
    conn.close()
    return jsonify({"success": True})


@app.route("/conductor", methods=["GET", "POST"])
@require_role("conductor")
# Render and process conductor trip attachment, ticketing, and monitoring actions.
def conductor():
    """Render and process conductor trip attachment, ticketing, and monitoring actions."""
    conn = get_db()
    conductor_id = session["user_id"]
    wants_json = (
        request.headers.get("X-Requested-With") == "XMLHttpRequest"
        or (request.accept_mimetypes.accept_json and not request.accept_mimetypes.accept_html)
    )

    if request.method == "POST":
        action = request.form.get("action")
        active_trip = get_active_trip_for_conductor(conn, conductor_id)
        ticket_payload = None
        ticket_error = None
        live_payload = None

        if action == "attach_trip":
            trip_id = int(request.form.get("trip_id", 0))
            trip = conn.execute(
                "SELECT t.id, t.notes, b.plate_number, r.route_name FROM trips t JOIN buses b ON b.id = t.bus_id JOIN routes r ON r.id = t.route_id WHERE t.id = ? AND t.status = 'active'",
                (trip_id,),
            ).fetchone()
            if trip:
                conn.execute(
                    "UPDATE trips SET conductor_id = ? WHERE id = ?",
                    (conductor_id, trip_id),
                )
                log_event(conn, conductor_id, "conductor", "Monitoring Attached", f"Conductor attached to trip #{trip_id} on {trip['plate_number']} ({trip['route_name']}).")

        elif action == "record_transaction" and active_trip:
            latest_gps = get_latest_trip_gps(conn, active_trip["id"])
            latitude = latest_gps["latitude"] if latest_gps and latest_gps["latitude"] is not None else None
            longitude = latest_gps["longitude"] if latest_gps and latest_gps["longitude"] is not None else None
            latest_record = get_latest_trip_record(conn, active_trip["id"])
            current_stop_details = get_trip_current_stop_details(
                active_trip,
                latest_gps,
                latest_record["stop_name"] if latest_record else None,
            )
            live_origin_stop = current_stop_details["name"] if current_stop_details else (active_trip.get("start_point") or "Waiting for GPS location")
            posted_origin_stop = request.form.get("origin_stop", "").strip()
            passenger_type = request.form.get("passenger_type", "").strip().lower()
            destination_stop = request.form.get("destination_stop", "").strip()
            origin_stop = live_origin_stop
            if not is_valid_trip_segment(active_trip, origin_stop, destination_stop) and is_valid_trip_segment(active_trip, posted_origin_stop, destination_stop):
                origin_stop = posted_origin_stop
            is_valid_destination = is_valid_trip_segment(active_trip, origin_stop, destination_stop)

            if passenger_type in {"student", "pwd", "senior", "regular"} and is_valid_destination:
                fare_amount = calculate_trip_fare_total(conn, active_trip, passenger_type, 1, origin_stop, destination_stop)
            else:
                fare_amount = 0
            recorded_at = to_db_time(now())
            if passenger_type in {"student", "pwd", "senior", "regular"} and is_valid_destination:
                ticket_cursor = conn.execute(
                    """
                    INSERT INTO trip_transactions (
                        trip_id, conductor_id, event_type, passenger_type, quantity, fare_amount,
                        stop_name, origin_stop, destination_stop, latitude, longitude, occupancy_after, recorded_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        active_trip["id"],
                        conductor_id,
                        "board",
                        passenger_type,
                        1,
                        fare_amount,
                        origin_stop,
                        origin_stop,
                        destination_stop,
                        float(latitude) if latitude is not None else None,
                        float(longitude) if longitude is not None else None,
                        0,
                        recorded_at,
                    ),
                )
                ticket_id = ticket_cursor.lastrowid

                manifest = sync_trip_occupancy_from_destinations(conn, active_trip, origin_stop)
                total = sum(item["count"] for item in manifest)
                crowd_level = classify_capacity(total, active_trip["capacity"])
                passenger_counts = {"student": 0, "pwd": 0, "senior": 0, "regular": 0}
                passenger_counts[passenger_type] = 1
                conn.execute(
                    """
                    INSERT INTO trip_records (
                        trip_id, students, pwd, senior, regular, boarded, dropped, total,
                        occupancy_after, crowd_level, stop_name, latitude, longitude, recorded_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        active_trip["id"],
                        passenger_counts["student"],
                        passenger_counts["pwd"],
                        passenger_counts["senior"],
                        passenger_counts["regular"],
                        1,
                        0,
                        1,
                        total,
                        crowd_level,
                        origin_stop,
                        float(latitude) if latitude is not None else None,
                        float(longitude) if longitude is not None else None,
                        recorded_at,
                    ),
                )
                conn.execute(
                    """
                    UPDATE trip_transactions
                    SET occupancy_after = ?
                    WHERE id = ?
                    """,
                    (total, ticket_id),
                )
                log_event(
                    conn,
                    conductor_id,
                    "conductor",
                    "Ticket Printed",
                    f"Trip #{active_trip['id']} boarded 1 {passenger_type} passenger from {origin_stop} to {destination_stop} for PHP {fare_amount:.0f}.",
                )
                create_trip_capacity_notification(conn, active_trip, total, conductor_id)
                ticket_payload = {
                    "id": ticket_id,
                    "recordedAt": recorded_at,
                    "passengerType": passenger_type,
                    "originStop": origin_stop,
                    "destinationStop": destination_stop,
                    "fareAmount": float(fare_amount or 0),
                    "occupancyAfter": int(total or 0),
                    "capacity": int(active_trip.get("capacity") or DEFAULT_BUS_CAPACITY),
                    "plateNumber": active_trip["plate_number"],
                    "busNumber": "".join(ch for ch in str(active_trip["plate_number"]) if ch.isdigit()) or active_trip["plate_number"],
                    "routeName": active_trip["route_name"],
                    "routeStart": active_trip["start_point"],
                    "routeEnd": active_trip["end_point"],
                    "sidebar": build_conductor_sidebar_payload(conn, conductor_id, active_trip, origin_stop),
                }
            else:
                ticket_error = "Select a valid downstream destination and passenger type before printing."

        elif action == "offboard_due" and active_trip:
            latest_gps = get_latest_trip_gps(conn, active_trip["id"])
            latest_record = get_latest_trip_record(conn, active_trip["id"])
            current_stop_details = get_trip_current_stop_details(
                active_trip,
                latest_gps,
                latest_record["stop_name"] if latest_record else None,
            )
            current_stop = current_stop_details["name"] if current_stop_details else None
            auto_offboard_due_passengers(
                conn,
                active_trip,
                current_stop,
                latest_gps["latitude"] if latest_gps and latest_gps["latitude"] is not None else None,
                latest_gps["longitude"] if latest_gps and latest_gps["longitude"] is not None else None,
                conductor_id,
                True,
            )
            active_trip = get_active_trip_for_conductor(conn, conductor_id) or active_trip
            live_payload = {
                "active": True,
                "stop_name": current_stop or "Route stop",
                "occupancy": int(active_trip.get("occupancy") or 0),
                "capacity": int(active_trip.get("capacity") or DEFAULT_BUS_CAPACITY),
                "sidebar": build_conductor_sidebar_payload(conn, conductor_id, active_trip, current_stop),
            }

        elif action == "stop_monitoring" and active_trip:
            if active_trip["driver_id"]:
                conn.execute("UPDATE trips SET conductor_id = NULL WHERE id = ?", (active_trip["id"],))
                log_event(conn, conductor_id, "conductor", "Monitoring Detached", f"Conductor stopped monitoring trip #{active_trip['id']}.")
            else:
                conn.execute(
                    "UPDATE trips SET status = 'completed', ended_at = ?, duration_minutes = ? WHERE id = ?",
                    (
                        to_db_time(now()),
                        int(max((now() - from_db_time(active_trip["started_at"])).total_seconds(), 0) // 60),
                        active_trip["id"],
                    ),
                )
                log_event(conn, conductor_id, "conductor", "Manual Trip Ended", f"Manual monitoring trip #{active_trip['id']} was closed.")

        conn.commit()
        if wants_json and action == "record_transaction":
            if ticket_payload:
                conn.close()
                return jsonify({"success": True, "ticket": normalize_json_value(ticket_payload)})
            conn.close()
            return jsonify({"success": False, "error": ticket_error or "Ticket was not saved."}), 400
        if wants_json and action == "offboard_due":
            conn.close()
            return jsonify({"success": True, "live": normalize_json_value(live_payload or {})})

    overview = build_conductor_overview(conn, conductor_id)
    conn.commit()
    conn.close()
    return render_template("conductor.html", overview=overview)


@app.route("/api/conductor/live")
@require_role("conductor")
# Return live conductor trip status, current stop, occupancy, and latest GPS.
def conductor_live():
    """Return live conductor trip status, current stop, occupancy, and latest GPS."""
    conn = get_db()
    conductor_id = session["user_id"]
    trip = get_active_trip_for_conductor(conn, conductor_id)

    if not trip:
        conn.close()
        return jsonify({"active": False})

    latest_gps = get_latest_trip_gps(conn, trip["id"])
    if not latest_gps:
        sidebar_payload = build_conductor_sidebar_payload(conn, conductor_id, trip, None)
        conn.close()
        return jsonify(
            {
                "active": True,
                "tracking": False,
                "stop_name": "Waiting for GPS location",
                "occupancy": int(trip.get("occupancy") or 0),
                "capacity": int(trip.get("capacity") or DEFAULT_BUS_CAPACITY),
                "sidebar": normalize_json_value(sidebar_payload),
            }
        )

    lat = float(latest_gps["latitude"])
    lng = float(latest_gps["longitude"])
    current_stop_details = get_trip_current_stop_details(trip, latest_gps, None)
    current_stop = current_stop_details["name"] if current_stop_details else derive_trip_location_label(trip, lat, lng)
    auto_offboard_due_passengers(conn, trip, current_stop, lat, lng, conductor_id)
    trip = get_active_trip_for_conductor(conn, conductor_id) or trip
    sidebar_payload = build_conductor_sidebar_payload(conn, conductor_id, trip, current_stop)
    conn.commit()
    conn.close()
    return jsonify(
        {
            "active": True,
            "tracking": True,
            "stop_name": current_stop,
            "occupancy": int(trip.get("occupancy") or 0),
            "capacity": int(trip.get("capacity") or DEFAULT_BUS_CAPACITY),
            "latitude": lat,
            "longitude": lng,
            "recorded_at": normalize_json_value(latest_gps["recorded_at"]),
            "sidebar": normalize_json_value(sidebar_payload),
        }
    )


@app.route("/api/conductor/panels")
@require_role("conductor")
# Return conductor sidebar counters, manifest, and recent tickets without requiring GPS work.
def conductor_panels():
    """Return conductor sidebar counters, manifest, and recent tickets without requiring GPS work."""
    conn = get_db()
    conductor_id = session["user_id"]
    trip = get_active_trip_for_conductor(conn, conductor_id)

    if not trip:
        conn.close()
        return jsonify({"active": False})

    latest_record = get_latest_trip_record(conn, trip["id"])
    latest_gps = get_latest_trip_gps(conn, trip["id"])
    current_stop_details = get_trip_current_stop_details(
        trip,
        latest_gps,
        latest_record["stop_name"] if latest_record else None,
    )
    current_stop = current_stop_details["name"] if current_stop_details else (
        latest_record["stop_name"] if latest_record else None
    )
    sidebar_payload = build_conductor_sidebar_payload(conn, conductor_id, trip, current_stop)
    conn.commit()
    conn.close()
    return jsonify(
        normalize_json_value(
            {
                "active": True,
                "occupancy": int(trip.get("occupancy") or 0),
                "capacity": int(trip.get("capacity") or DEFAULT_BUS_CAPACITY),
                "stop_name": current_stop or "Waiting for GPS location",
                "sidebar": sidebar_payload,
            }
        )
    )


@app.route("/logout")
# Clear the session and return the user to the public landing page.
def logout():
    """Clear the session and return the user to the public landing page."""
    session.clear()
    return redirect(url_for("landing"))


if __name__ == "__main__":
    initialize_database()
    socketio.run(
        app,
        host=os.environ.get("FLASK_RUN_HOST", "127.0.0.1"),
        port=int(os.environ.get("FLASK_RUN_PORT", "5000")),
        debug=os.environ.get("FLASK_DEBUG", "0").lower() in {"1", "true", "yes"},
        allow_unsafe_werkzeug=True,
    )
