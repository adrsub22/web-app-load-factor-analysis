"""
generate_dummy_data.py
======================
Reads GTFS zips from data/gtfs/<signup>/ and produces:

  data/load_data.csv      — wide-format load by (stop, route, direction, pattern) x signup
  data/segments.geojson   — one feature per stop-to-stop segment, color baked in
  data/stops.geojson      — stop markers, one per (signup, route, direction, pattern, stop)
  data/route_meta.json    — signups / routes / directions / patterns + map center

PATTERNS
--------
A pattern is a unique shape_id within a (route, direction) — the standard
GTFS-aligned definition. Letters A/B/C... are assigned per signup by trip
count (A = most-used = dominant). The same letter across signups means
"the dominant/2nd-most-common/etc. pattern in that signup" — which may be
a different shape if service was restructured between signups.

Run from the project root:
    python scripts/generate_dummy_data.py
"""

from __future__ import annotations

import json
import random
import string
import sys
import zipfile
from collections import Counter
from io import TextIOWrapper
from pathlib import Path

import pandas as pd
from shapely.geometry import LineString, Point, mapping
from shapely.ops import substring


# ============================================================================
# CONFIGURATION
# ============================================================================

PROJECT_ROOT = Path(__file__).resolve().parent.parent
GTFS_ROOT = PROJECT_ROOT / "data" / "gtfs"
DATA_OUT = PROJECT_ROOT / "data"

SIGNUPS = ["may25", "aug25", "jan26", "may26"]
SIGNUP_LABELS = {"may25": "May25", "aug25": "Aug25", "jan26": "Jan26", "may26": "May26"}

# Routes to extract — matched against route_short_name in routes.txt.
# Defaults match the sample GTFS produced by scripts/make_sample_gtfs.py
# so the project runs out of the box. Change these to your real route
# numbers when you swap in actual GTFS feeds.
ROUTES_OF_INTEREST = ["10", "20"]

LOAD_COLORS = [
    (0,  10, "#2ca02c", "0–10"),       # green
    (10, 20, "#9467bd", ">10–20"),     # violet
    (20, 30, "#1f77b4", ">20–30"),     # blue
    (30, 40, "#daa520", ">30–40"),     # gold
    (40, 50, "#ff7f0e", ">40–50"),     # orange
    (50, float("inf"), "#d62728", ">50"),  # red
]

# Drop patterns accounting for less than this share of trips on a direction —
# filters out one-off detours and data-entry artifacts. Set to 0 to keep all.
MIN_PATTERN_TRIP_SHARE = 0.02

RANDOM_SEED = 42


# ============================================================================
# COLOR / LOAD HELPERS
# ============================================================================

def color_for_load(load: float) -> str:
    for lo, hi, color, _ in LOAD_COLORS:
        if lo == 0:
            if lo <= load <= hi:
                return color
        else:
            if lo < load <= hi:
                return color
    return LOAD_COLORS[-1][2]


def synthesize_load(stop_index: int, n_stops: int, signup: str,
                    pattern_letter: str) -> int:
    """
    Bell-curve along the pattern × per-signup multiplier × per-pattern
    multiplier × noise. Different patterns get visibly different loads
    even at the same stop, which is what real-world AVL data shows.
    """
    t = stop_index / max(n_stops - 1, 1)
    bell = 4 * t * (1 - t)
    signup_mult = {"may25": 0.85, "aug25": 1.05, "jan26": 0.95, "may26": 1.15}.get(signup, 1.0)
    pattern_mult = {"A": 1.00, "B": 0.70, "C": 0.55, "D": 0.45}.get(pattern_letter, 0.4)
    base = 38 * bell * signup_mult * pattern_mult
    noise = random.uniform(-4, 4)
    return max(0, int(round(base + noise)))


# ============================================================================
# GTFS LOADING
# ============================================================================

def find_gtfs_zip(signup_folder: Path) -> Path | None:
    if not signup_folder.is_dir():
        return None
    zips = sorted(signup_folder.glob("*.zip"))
    return zips[0] if zips else None


def read_gtfs_table(zf: zipfile.ZipFile, name: str, **kwargs) -> pd.DataFrame:
    with zf.open(name) as raw:
        text = TextIOWrapper(raw, encoding="utf-8-sig", newline="")
        return pd.read_csv(text, **kwargs)


def load_gtfs_tables(zip_path: Path) -> dict:
    print(f"  Reading GTFS: {zip_path.name}")
    with zipfile.ZipFile(zip_path) as zf:
        names = set(zf.namelist())
        tables = {
            "routes":     read_gtfs_table(zf, "routes.txt", dtype=str),
            "trips":      read_gtfs_table(zf, "trips.txt", dtype=str),
            "stops":      read_gtfs_table(zf, "stops.txt", dtype=str),
            "stop_times": read_gtfs_table(zf, "stop_times.txt", dtype=str),
        }
        if "shapes.txt" in names:
            tables["shapes"] = read_gtfs_table(zf, "shapes.txt", dtype=str)
        else:
            print("    (no shapes.txt — using straight-line segments)")
            tables["shapes"] = None

    tables["stops"]["stop_lat"] = tables["stops"]["stop_lat"].astype(float)
    tables["stops"]["stop_lon"] = tables["stops"]["stop_lon"].astype(float)
    tables["stop_times"]["stop_sequence"] = tables["stop_times"]["stop_sequence"].astype(int)
    if tables["shapes"] is not None:
        tables["shapes"]["shape_pt_lat"] = tables["shapes"]["shape_pt_lat"].astype(float)
        tables["shapes"]["shape_pt_lon"] = tables["shapes"]["shape_pt_lon"].astype(float)
        tables["shapes"]["shape_pt_sequence"] = tables["shapes"]["shape_pt_sequence"].astype(int)
    return tables


# ============================================================================
# GEOSPATIAL
# ============================================================================

def build_shape_linestring(shapes_df: pd.DataFrame, shape_id: str) -> LineString:
    pts = shapes_df[shapes_df["shape_id"] == shape_id].sort_values("shape_pt_sequence")
    return LineString(list(zip(pts["shape_pt_lon"], pts["shape_pt_lat"])))


def cut_segments(line: LineString | None,
                 stop_points: list[tuple[str, Point]]) -> list[dict]:
    segments = []
    if line is None:
        for i in range(len(stop_points) - 1):
            from_id, p_from = stop_points[i]
            to_id,   p_to   = stop_points[i + 1]
            seg = LineString([(p_from.x, p_from.y), (p_to.x, p_to.y)])
            segments.append({"from_stop": from_id, "to_stop": to_id, "geometry": seg})
        return segments

    projected = [(sid, line.project(pt)) for sid, pt in stop_points]
    for i in range(len(projected) - 1):
        from_id, d_from = projected[i]
        to_id,   d_to   = projected[i + 1]
        if d_to <= d_from:
            p_from = next(p for sid, p in stop_points if sid == from_id)
            p_to   = next(p for sid, p in stop_points if sid == to_id)
            seg = LineString([(p_from.x, p_from.y), (p_to.x, p_to.y)])
        else:
            seg = substring(line, d_from, d_to)
            if seg.geom_type != "LineString" or len(seg.coords) < 2:
                p_from = next(p for sid, p in stop_points if sid == from_id)
                p_to   = next(p for sid, p in stop_points if sid == to_id)
                seg = LineString([(p_from.x, p_from.y), (p_to.x, p_to.y)])
        segments.append({"from_stop": from_id, "to_stop": to_id, "geometry": seg})
    return segments


# ============================================================================
# PATTERN IDENTIFICATION
# ============================================================================

def identify_patterns(trips_dir: pd.DataFrame,
                      stop_times: pd.DataFrame,
                      stops_df: pd.DataFrame) -> list[dict]:
    """
    For trips on a single (route, direction), return patterns sorted by
    trip count desc, with letters A/B/C... assigned. Each dict has:
        shape_id, trip_count, trip_share, stop_ids (ordered),
        pattern_letter, first_terminal, last_terminal
    """
    if trips_dir.empty:
        return []

    trips_with_shape = trips_dir.copy()
    if "shape_id" not in trips_with_shape.columns:
        trips_with_shape["shape_id"] = ""
    trips_with_shape["shape_id"] = trips_with_shape["shape_id"].fillna("")

    # Trips with no shape: fall back to a stop-sequence hash so they don't
    # all collapse together
    no_shape = trips_with_shape["shape_id"] == ""
    if no_shape.any():
        st = stop_times[stop_times["trip_id"].isin(trips_with_shape["trip_id"])]
        sigs = (st.sort_values(["trip_id", "stop_sequence"])
                  .groupby("trip_id")["stop_id"]
                  .apply(lambda s: "→".join(s)))
        for tid, sig in sigs.items():
            mask = (trips_with_shape["trip_id"] == tid) & (trips_with_shape["shape_id"] == "")
            if mask.any():
                trips_with_shape.loc[mask, "shape_id"] = f"NOSHAPE_{abs(hash(sig)) % 10**8}"

    total_trips = len(trips_with_shape)
    by_shape = trips_with_shape.groupby("shape_id")

    patterns = []
    for shape_id, group in by_shape:
        trip_count = len(group)
        trip_share = trip_count / total_trips
        if trip_share < MIN_PATTERN_TRIP_SHARE:
            continue

        trip_ids = group["trip_id"].tolist()
        st_pat = stop_times[stop_times["trip_id"].isin(trip_ids)]
        if st_pat.empty:
            continue
        rep_trip = st_pat.groupby("trip_id")["stop_sequence"].count().idxmax()
        ordered_stop_ids = (st_pat[st_pat["trip_id"] == rep_trip]
                            .sort_values("stop_sequence")["stop_id"].tolist())
        ordered_stop_ids = [sid for sid in ordered_stop_ids if sid in stops_df.index]
        if len(ordered_stop_ids) < 2:
            continue

        first_name = stops_df.loc[ordered_stop_ids[0]].get("stop_name", ordered_stop_ids[0])
        last_name  = stops_df.loc[ordered_stop_ids[-1]].get("stop_name", ordered_stop_ids[-1])

        patterns.append({
            "shape_id":       shape_id,
            "trip_count":     trip_count,
            "trip_share":     trip_share,
            "stop_ids":       ordered_stop_ids,
            "first_terminal": first_name,
            "last_terminal":  last_name,
        })

    patterns.sort(key=lambda p: -p["trip_count"])
    for i, p in enumerate(patterns):
        if i < 26:
            p["pattern_letter"] = string.ascii_uppercase[i]
        else:
            p["pattern_letter"] = string.ascii_uppercase[(i // 26) - 1] + string.ascii_uppercase[i % 26]
    return patterns


def pattern_label(p: dict) -> str:
    pct = round(p["trip_share"] * 100)
    return (f"Pattern {p['pattern_letter']} · "
            f"{p['first_terminal']} → {p['last_terminal']} "
            f"({len(p['stop_ids'])} stops, {pct}%)")


# ============================================================================
# MAIN PIPELINE
# ============================================================================

def main():
    random.seed(RANDOM_SEED)

    available = []
    for signup in SIGNUPS:
        zp = find_gtfs_zip(GTFS_ROOT / signup)
        if zp is None:
            print(f"WARN: no GTFS zip in data/gtfs/{signup}/ — skipping")
        else:
            available.append((signup, zp))

    if not available:
        print("ERROR: no GTFS zips found. Drop them in data/gtfs/<signup>/ and re-run.")
        sys.exit(1)

    print(f"Found {len(available)} signup(s): {[s for s, _ in available]}\n")

    # Per-stop loads keyed by (stop_id, route, direction_id, pattern_letter) → {col: load}
    load_table: dict[tuple, dict] = {}
    stop_meta: dict[tuple, dict] = {}
    stop_sequence_lookup: dict[tuple, dict[str, int]] = {}

    all_segment_features: list = []
    all_stop_features: list = []

    # patterns_meta: signup → route → direction → [pattern dicts]
    patterns_meta: dict[str, dict[str, dict[str, list]]] = {}
    direction_labels: dict[str, dict[str, list[str]]] = {}

    all_lats, all_lons = [], []
    routes_seen: set[str] = set()

    for signup, zp in available:
        print(f"=== Signup: {signup} ===")
        gtfs = load_gtfs_tables(zp)

        wanted = gtfs["routes"][gtfs["routes"]["route_short_name"].isin(ROUTES_OF_INTEREST)]
        if wanted.empty:
            print(f"  WARN: none of {ROUTES_OF_INTEREST} found in this signup\n")
            continue

        stops_df = gtfs["stops"].set_index("stop_id")
        signup_label = SIGNUP_LABELS[signup]
        load_col = signup_label + "SignupLoad"
        patterns_meta.setdefault(signup_label, {})

        for _, route_row in wanted.iterrows():
            route_id = route_row["route_id"]
            route_short = route_row["route_short_name"]
            print(f"  Route {route_short} (route_id={route_id})")
            routes_seen.add(route_short)
            patterns_meta[signup_label].setdefault(route_short, {})

            trips_for_route = gtfs["trips"][gtfs["trips"]["route_id"] == route_id]
            if trips_for_route.empty:
                continue

            for direction_id in sorted(trips_for_route["direction_id"].dropna().unique()):
                trips_dir = trips_for_route[trips_for_route["direction_id"] == direction_id]

                if "trip_headsign" in trips_dir.columns:
                    headsigns = trips_dir["trip_headsign"].dropna()
                    direction_label = (Counter(headsigns).most_common(1)[0][0]
                                       if len(headsigns) else f"Dir {direction_id}")
                else:
                    direction_label = f"Dir {direction_id}"

                direction_labels.setdefault(route_short, {}).setdefault(direction_id, []).append(direction_label)

                patterns = identify_patterns(trips_dir, gtfs["stop_times"], stops_df)
                if not patterns:
                    print(f"    direction {direction_id}: no patterns, skipping")
                    continue

                print(f"    direction {direction_id} ({direction_label}): "
                      f"{len(patterns)} pattern(s)")

                pattern_dicts = []
                for p in patterns:
                    pattern_dicts.append({
                        "id":             p["pattern_letter"],
                        "label":          pattern_label(p),
                        "stop_count":     len(p["stop_ids"]),
                        "trip_count":     p["trip_count"],
                        "trip_share":     round(p["trip_share"], 4),
                        "shape_id":       p["shape_id"],
                        "first_terminal": p["first_terminal"],
                        "last_terminal":  p["last_terminal"],
                    })
                patterns_meta[signup_label][route_short][str(direction_id)] = pattern_dicts

                for p in patterns:
                    letter = p["pattern_letter"]
                    label = pattern_label(p)
                    print(f"      {label}")

                    if (p["shape_id"] and not p["shape_id"].startswith("NOSHAPE_")
                            and gtfs["shapes"] is not None):
                        line = build_shape_linestring(gtfs["shapes"], p["shape_id"])
                    else:
                        line = None

                    stop_pts = []
                    for sid in p["stop_ids"]:
                        s = stops_df.loc[sid]
                        pt = Point(float(s["stop_lon"]), float(s["stop_lat"]))
                        stop_pts.append((sid, pt))

                    for idx, (sid, pt) in enumerate(stop_pts):
                        load = synthesize_load(idx, len(stop_pts), signup, letter)
                        key = (sid, route_short, direction_id, letter)
                        load_table.setdefault(key, {})[load_col] = load
                        stop_meta.setdefault(key, {
                            "stop_id":   sid,
                            "stop_name": stops_df.loc[sid].get("stop_name", ""),
                            "stop_lat":  pt.y,
                            "stop_lon":  pt.x,
                        })
                        stop_sequence_lookup.setdefault(
                            (route_short, direction_id, letter), {}
                        )[sid] = idx + 1
                        all_lats.append(pt.y)
                        all_lons.append(pt.x)

                    segs = cut_segments(line, stop_pts)
                    for seg in segs:
                        from_id = seg["from_stop"]
                        from_load = load_table.get(
                            (from_id, route_short, direction_id, letter), {}
                        ).get(load_col, 0)
                        all_segment_features.append({
                            "type": "Feature",
                            "geometry": mapping(seg["geometry"]),
                            "properties": {
                                "route":           route_short,
                                "direction_id":    int(direction_id) if direction_id.isdigit() else direction_id,
                                "direction_label": direction_label,
                                "signup":          signup_label,
                                "pattern":         letter,
                                "pattern_label":   label,
                                "from_stop":       from_id,
                                "to_stop":         seg["to_stop"],
                                "avg_load":        from_load,
                                "color":           color_for_load(from_load),
                            },
                        })

                    for idx, (sid, pt) in enumerate(stop_pts):
                        all_stop_features.append({
                            "type": "Feature",
                            "geometry": {"type": "Point", "coordinates": [pt.x, pt.y]},
                            "properties": {
                                "stop_id":         sid,
                                "stop_name":       stops_df.loc[sid].get("stop_name", ""),
                                "stop_sequence":   idx + 1,
                                "signup":          signup_label,
                                "route":           route_short,
                                "direction_id":    int(direction_id) if direction_id.isdigit() else direction_id,
                                "direction_label": direction_label,
                                "pattern":         letter,
                                "pattern_label":   label,
                            },
                        })

        print()

    # --- Write load_data.csv -------------------------------------------------
    print("Writing data/load_data.csv...")
    if not load_table:
        print("ERROR: no load data produced. Check that ROUTES_OF_INTEREST "
              f"({ROUTES_OF_INTEREST}) matches route_short_name values in "
              "your GTFS routes.txt.")
        sys.exit(1)

    dir_label_resolved: dict[tuple[str, str], str] = {
        (r, did): Counter(labels).most_common(1)[0][0]
        for r, dirs in direction_labels.items()
        for did, labels in dirs.items()
    }

    rows = []
    for key, loads in load_table.items():
        sid, route, direction_id, pattern = key
        meta = stop_meta[key]
        seq = stop_sequence_lookup.get((route, direction_id, pattern), {}).get(sid, 0)
        row = {
            "Stop ID":       sid,
            "Stop Name":     meta["stop_name"],
            "Route":         route,
            "Direction ID":  direction_id,
            "Direction":     dir_label_resolved.get((route, direction_id), ""),
            "Pattern":       pattern,
            "Stop Sequence": seq,
        }
        for s in SIGNUPS:
            col = SIGNUP_LABELS[s] + "SignupLoad"
            row[col] = loads.get(col, "")
        rows.append(row)

    df = pd.DataFrame(rows).sort_values(["Route", "Direction ID", "Pattern", "Stop Sequence"])
    csv_path = DATA_OUT / "load_data.csv"
    df.to_csv(csv_path, index=False)
    print(f"  {len(df)} rows -> {csv_path}")

    # --- Write GeoJSON files -------------------------------------------------
    print("Writing data/segments.geojson...")
    seg_path = DATA_OUT / "segments.geojson"
    with open(seg_path, "w") as f:
        json.dump({"type": "FeatureCollection", "features": all_segment_features}, f)
    print(f"  {len(all_segment_features)} features -> {seg_path}")

    print("Writing data/stops.geojson...")
    stops_path = DATA_OUT / "stops.geojson"
    with open(stops_path, "w") as f:
        json.dump({"type": "FeatureCollection", "features": all_stop_features}, f)
    print(f"  {len(all_stop_features)} features -> {stops_path}")

    # --- Write route_meta.json -----------------------------------------------
    print("Writing data/route_meta.json...")
    meta_signups = [SIGNUP_LABELS[s] for s, _ in available]
    meta_routes = sorted(routes_seen, key=lambda r: int(r) if r.isdigit() else r)

    meta_directions = {}
    for (r, did), lbl in dir_label_resolved.items():
        meta_directions.setdefault(r, []).append({"direction_id": did, "label": lbl})
    for r in meta_directions:
        meta_directions[r].sort(key=lambda d: d["direction_id"])

    center = ([sum(all_lats) / len(all_lats), sum(all_lons) / len(all_lons)]
              if all_lats and all_lons else [29.4241, -98.4936])

    meta_obj = {
        "signups":      meta_signups,
        "routes":       meta_routes,
        "directions":   meta_directions,
        "patterns":     patterns_meta,
        "map_center":   center,
        "color_legend": [
            {"label": label, "color": color, "min": lo,
             "max": hi if hi != float("inf") else None}
            for lo, hi, color, label in LOAD_COLORS
        ],
    }
    meta_path = DATA_OUT / "route_meta.json"
    with open(meta_path, "w") as f:
        json.dump(meta_obj, f, indent=2)
    print(f"  -> {meta_path}")

    print("\nDone. Now run:  python app.py")


if __name__ == "__main__":
    main()
