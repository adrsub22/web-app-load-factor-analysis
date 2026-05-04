"""
app.py
======
Flask backend for the Transit Load Viewer.

Run:    python app.py
Open:   http://localhost:5000
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
from flask import Flask, abort, jsonify, render_template, request


PROJECT_ROOT = Path(__file__).resolve().parent
DATA_DIR = PROJECT_ROOT / "data"

app = Flask(__name__)


# ============================================================================
# Lazy-loaded, cached data
# ============================================================================
# Loaded once on first request and held in memory. If you regenerate data
# (re-run the generator script), restart the Flask server to pick it up.

_meta_cache = None
_segments_cache = None
_stops_cache = None
_loads_cache = None


def _load_or_die(path: Path, label: str):
    if not path.exists():
        abort(500, description=(
            f"Missing {label} ({path.name}). Run "
            f"`python scripts/generate_dummy_data.py` first."
        ))
    with open(path) as f:
        return json.load(f)


def get_meta():
    global _meta_cache
    if _meta_cache is None:
        _meta_cache = _load_or_die(DATA_DIR / "route_meta.json", "route metadata")
    return _meta_cache


def get_segments():
    global _segments_cache
    if _segments_cache is None:
        _segments_cache = _load_or_die(DATA_DIR / "segments.geojson", "segments GeoJSON")
    return _segments_cache


def get_stops():
    global _stops_cache
    if _stops_cache is None:
        _stops_cache = _load_or_die(DATA_DIR / "stops.geojson", "stops GeoJSON")
    return _stops_cache


def get_loads() -> pd.DataFrame:
    global _loads_cache
    if _loads_cache is None:
        path = DATA_DIR / "load_data.csv"
        if not path.exists():
            abort(500, description=(
                "Missing load_data.csv. Run "
                "`python scripts/generate_dummy_data.py` first."
            ))
        _loads_cache = pd.read_csv(
            path,
            dtype={"Route": str, "Direction ID": str, "Stop ID": str, "Pattern": str},
        )
    return _loads_cache


# ============================================================================
# Routes
# ============================================================================

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/meta")
def api_meta():
    """Signups, routes, directions, patterns (per signup), color legend, map center."""
    return jsonify(get_meta())


@app.route("/api/segments")
def api_segments():
    """
    Filtered segments GeoJSON.
    Query params (all optional):
        signup     e.g. May26
        route      e.g. 2
        direction  direction_id, e.g. 0 or 1
        pattern    pattern letter, e.g. A
    """
    signup    = request.args.get("signup")
    route     = request.args.get("route")
    direction = request.args.get("direction")
    pattern   = request.args.get("pattern")

    feats = get_segments()["features"]
    if signup:
        feats = [f for f in feats if f["properties"]["signup"] == signup]
    if route:
        feats = [f for f in feats if str(f["properties"]["route"]) == str(route)]
    if direction not in (None, ""):
        feats = [f for f in feats if str(f["properties"]["direction_id"]) == str(direction)]
    if pattern:
        feats = [f for f in feats if f["properties"]["pattern"] == pattern]

    return jsonify({"type": "FeatureCollection", "features": feats})


@app.route("/api/stops")
def api_stops():
    """
    Filtered stops GeoJSON. Stop geometry can vary across signups+patterns
    (different patterns have different stop sets), so signup matters here too.
    """
    signup    = request.args.get("signup")
    route     = request.args.get("route")
    direction = request.args.get("direction")
    pattern   = request.args.get("pattern")

    feats = get_stops()["features"]
    if signup:
        feats = [f for f in feats if f["properties"].get("signup") == signup]
    if route:
        feats = [f for f in feats if str(f["properties"]["route"]) == str(route)]
    if direction not in (None, ""):
        feats = [f for f in feats if str(f["properties"]["direction_id"]) == str(direction)]
    if pattern:
        feats = [f for f in feats if f["properties"]["pattern"] == pattern]

    return jsonify({"type": "FeatureCollection", "features": feats})


@app.route("/api/trend")
def api_trend():
    """
    Average load per signup for the selected route+direction+pattern.

    Pattern letter is the cross-signup join key. If a pattern letter
    doesn't exist in some signup, that signup's value comes back as null
    (the chart will show a gap). When direction is omitted, averages all
    directions; when pattern is omitted, averages all patterns.
    """
    route     = request.args.get("route")
    direction = request.args.get("direction")
    pattern   = request.args.get("pattern")

    if not route:
        return jsonify({"error": "route is required"}), 400

    df = get_loads()
    df = df[df["Route"] == str(route)]
    if direction not in (None, ""):
        df = df[df["Direction ID"] == str(direction)]
    if pattern:
        df = df[df["Pattern"] == pattern]

    meta = get_meta()
    labels = meta["signups"]

    values = []
    for label in labels:
        col = f"{label}SignupLoad"
        if col in df.columns and len(df):
            v = pd.to_numeric(df[col], errors="coerce").mean()
            values.append(round(float(v), 2) if pd.notna(v) else None)
        else:
            values.append(None)

    return jsonify({
        "labels":    labels,
        "values":    values,
        "route":     route,
        "direction": direction or None,
        "pattern":   pattern or None,
    })


@app.errorhandler(500)
def handle_500(err):
    if request.path.startswith("/api/"):
        return jsonify({"error": str(err.description)}), 500
    return err


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
