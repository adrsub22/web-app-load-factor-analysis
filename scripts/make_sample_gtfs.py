"""
make_sample_gtfs.py
===================
Generates a small synthetic GTFS feed for each of the four signup periods
so the demo runs without needing any real transit data.

The synthetic data has two routes with deliberately interesting structure:

  Route 10 — North/South corridor
    Direction 0 (Northbound):
      Pattern A (full):       8 stops, ~67% of trips
      Pattern B (short turn): 5 stops, ~33% of trips     ← short turn at midpoint
    Direction 1 (Southbound):
      Pattern A (full):       8 stops, 100% of trips

  Route 20 — East/West crosstown
    Direction 0 (Eastbound):
      Pattern A (local):      6 stops, ~75% of trips
      Pattern B (express):    4 stops, ~25% of trips     ← skips two stops
    Direction 1 (Westbound):
      Pattern A (local):      6 stops, 100% of trips

This exercises every interesting code path: multiple patterns per direction,
short turns, skip-stop variants, and asymmetric direction structures.

The same synthetic GTFS is used for all four signups — the load synthesizer
in generate_dummy_data.py varies the values per signup so the trend chart is
not flat.

Run from the project root:
    python scripts/make_sample_gtfs.py
"""

from __future__ import annotations

import zipfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
GTFS_ROOT = PROJECT_ROOT / "data" / "gtfs"
SIGNUPS = ["may25", "aug25", "jan26", "may26"]

# A made-up midwest grid city — keeps the demo regionally neutral.
CITY_CENTER_LAT = 39.78
CITY_CENTER_LON = -89.65


def build_files() -> dict[str, str]:
    """Return a dict of GTFS filename → contents."""
    files = {}

    # agency.txt
    files["agency.txt"] = (
        "agency_id,agency_name,agency_url,agency_timezone\n"
        "STA,Sample Transit Authority,http://example.org,America/Chicago\n"
    )

    # routes.txt
    files["routes.txt"] = (
        "route_id,agency_id,route_short_name,route_long_name,route_type\n"
        "R10,STA,10,North/South Main Line,3\n"
        "R20,STA,20,Crosstown Express,3\n"
    )

    # calendar.txt — one weekday service spanning the relevant period
    files["calendar.txt"] = (
        "service_id,monday,tuesday,wednesday,thursday,friday,"
        "saturday,sunday,start_date,end_date\n"
        "WK,1,1,1,1,1,0,0,20250101,20271231\n"
    )

    # ----- stops.txt --------------------------------------------------------
    # Route 10: 8 stops along a north-south line at lon = CITY_CENTER_LON
    r10_stops = [
        ("R10_S1", "Main St & Northgate Plaza", CITY_CENTER_LAT + 0.060),
        ("R10_S2", "Main St & Riverside Dr",   CITY_CENTER_LAT + 0.045),
        ("R10_S3", "Main St & Maple Ave",      CITY_CENTER_LAT + 0.030),
        ("R10_S4", "Main St & Oak Ave",        CITY_CENTER_LAT + 0.015),
        ("R10_S5", "Main St & Civic Center",   CITY_CENTER_LAT + 0.000),
        ("R10_S6", "Main St & Elm St",         CITY_CENTER_LAT - 0.015),
        ("R10_S7", "Main St & 5th St",         CITY_CENTER_LAT - 0.030),
        ("R10_S8", "Main St & Southgate",      CITY_CENTER_LAT - 0.045),
    ]
    # Route 20: 6 stops along an east-west line at lat = CITY_CENTER_LAT
    r20_stops = [
        ("R20_S1", "1st Ave & Westside Mall",  CITY_CENTER_LON - 0.045),
        ("R20_S2", "1st Ave & Park St",        CITY_CENTER_LON - 0.030),
        ("R20_S3", "1st Ave & Library",        CITY_CENTER_LON - 0.015),
        ("R20_S4", "1st Ave & City Hall",      CITY_CENTER_LON + 0.000),
        ("R20_S5", "1st Ave & Hospital",       CITY_CENTER_LON + 0.020),
        ("R20_S6", "1st Ave & East Plaza",     CITY_CENTER_LON + 0.040),
    ]

    stop_lines = ["stop_id,stop_name,stop_lat,stop_lon"]
    for sid, name, lat in r10_stops:
        stop_lines.append(f"{sid},{name},{lat:.6f},{CITY_CENTER_LON:.6f}")
    for sid, name, lon in r20_stops:
        stop_lines.append(f"{sid},{name},{CITY_CENTER_LAT:.6f},{lon:.6f}")
    files["stops.txt"] = "\n".join(stop_lines) + "\n"

    # ----- trips.txt --------------------------------------------------------
    # R10 dir 0: 8 trips Pattern A (full) + 4 trips Pattern B (short turn)  → 67/33
    # R10 dir 1: 6 trips Pattern A (full)
    # R20 dir 0: 6 trips Pattern A (local) + 2 trips Pattern B (express)    → 75/25
    # R20 dir 1: 5 trips Pattern A (local)
    trip_rows = ["route_id,service_id,trip_id,trip_headsign,direction_id,shape_id"]

    # R10 northbound full (8 trips)
    for i in range(8):
        trip_rows.append(f"R10,WK,T10_NA_{i},Northbound to Northgate,0,SH_R10_N_FULL")
    # R10 northbound short turn (4 trips)
    for i in range(4):
        trip_rows.append(f"R10,WK,T10_NB_{i},Northbound to Civic Center,0,SH_R10_N_SHORT")
    # R10 southbound full (6 trips)
    for i in range(6):
        trip_rows.append(f"R10,WK,T10_SA_{i},Southbound to Southgate,1,SH_R10_S_FULL")
    # R20 eastbound local (6 trips)
    for i in range(6):
        trip_rows.append(f"R20,WK,T20_EA_{i},Eastbound to East Plaza,0,SH_R20_E_LOCAL")
    # R20 eastbound express (2 trips)
    for i in range(2):
        trip_rows.append(f"R20,WK,T20_EB_{i},Eastbound Express,0,SH_R20_E_EXP")
    # R20 westbound local (5 trips)
    for i in range(5):
        trip_rows.append(f"R20,WK,T20_WA_{i},Westbound to Westside Mall,1,SH_R20_W_LOCAL")
    files["trips.txt"] = "\n".join(trip_rows) + "\n"

    # ----- stop_times.txt ---------------------------------------------------
    st_rows = ["trip_id,arrival_time,departure_time,stop_id,stop_sequence"]

    def add_stops(trip_id: str, stop_ids: list[str], start_hr: int):
        for i, sid in enumerate(stop_ids, start=1):
            t = f"{start_hr:02d}:{i*2:02d}:00"
            st_rows.append(f"{trip_id},{t},{t},{sid},{i}")

    # R10 NB full: S8 → S1 (south to north along Main St)
    full_north = ["R10_S8","R10_S7","R10_S6","R10_S5","R10_S4","R10_S3","R10_S2","R10_S1"]
    for i in range(8):
        add_stops(f"T10_NA_{i}", full_north, start_hr=6 + i//4)
    # R10 NB short turn: S8 → S4 (only halfway up)
    short_north = ["R10_S8","R10_S7","R10_S6","R10_S5","R10_S4"]
    for i in range(4):
        add_stops(f"T10_NB_{i}", short_north, start_hr=7)
    # R10 SB full: S1 → S8 (north to south)
    full_south = ["R10_S1","R10_S2","R10_S3","R10_S4","R10_S5","R10_S6","R10_S7","R10_S8"]
    for i in range(6):
        add_stops(f"T10_SA_{i}", full_south, start_hr=8 + i//3)
    # R20 EB local: S1 → S6 (all stops)
    east_local = ["R20_S1","R20_S2","R20_S3","R20_S4","R20_S5","R20_S6"]
    for i in range(6):
        add_stops(f"T20_EA_{i}", east_local, start_hr=9 + i//3)
    # R20 EB express: S1 → S6, skips S3 and S5
    east_express = ["R20_S1","R20_S2","R20_S4","R20_S6"]
    for i in range(2):
        add_stops(f"T20_EB_{i}", east_express, start_hr=10)
    # R20 WB local: S6 → S1
    west_local = ["R20_S6","R20_S5","R20_S4","R20_S3","R20_S2","R20_S1"]
    for i in range(5):
        add_stops(f"T20_WA_{i}", west_local, start_hr=11 + i//3)

    files["stop_times.txt"] = "\n".join(st_rows) + "\n"

    # ----- shapes.txt -------------------------------------------------------
    # Each shape is a polyline that traces the route's actual path.
    # We add a few intermediate points between stops so the segments after
    # cutting hug the line rather than being a single straight chord.
    shape_rows = ["shape_id,shape_pt_lat,shape_pt_lon,shape_pt_sequence"]

    def add_shape(shape_id: str, points: list[tuple[float, float]]):
        for seq, (lat, lon) in enumerate(points, start=1):
            shape_rows.append(f"{shape_id},{lat:.6f},{lon:.6f},{seq}")

    # Helper: interpolate N intermediate points between two lat/lon endpoints
    def interp(p1: tuple[float, float], p2: tuple[float, float], n: int = 3):
        out = [p1]
        for k in range(1, n + 1):
            t = k / (n + 1)
            out.append((p1[0] + (p2[0] - p1[0]) * t, p1[1] + (p2[1] - p1[1]) * t))
        out.append(p2)
        return out

    # R10 NB full shape: trace from S8 (south) up to S1 (north)
    r10_n_full_pts = []
    for sid, _, lat in r10_stops[::-1]:  # reversed so south-to-north
        r10_n_full_pts.append((lat, CITY_CENTER_LON))
    # Interpolate
    interpolated = []
    for i in range(len(r10_n_full_pts) - 1):
        seg = interp(r10_n_full_pts[i], r10_n_full_pts[i+1], n=2)
        interpolated.extend(seg if i == 0 else seg[1:])
    add_shape("SH_R10_N_FULL", interpolated)

    # R10 NB short turn: from S8 only up to S4
    short_pts = [(lat, CITY_CENTER_LON) for _, _, lat in r10_stops[::-1][:5]]  # S8..S4
    interpolated = []
    for i in range(len(short_pts) - 1):
        seg = interp(short_pts[i], short_pts[i+1], n=2)
        interpolated.extend(seg if i == 0 else seg[1:])
    add_shape("SH_R10_N_SHORT", interpolated)

    # R10 SB full shape: north to south (reverse of full)
    r10_s_full_pts = [(lat, CITY_CENTER_LON) for _, _, lat in r10_stops]  # S1..S8
    interpolated = []
    for i in range(len(r10_s_full_pts) - 1):
        seg = interp(r10_s_full_pts[i], r10_s_full_pts[i+1], n=2)
        interpolated.extend(seg if i == 0 else seg[1:])
    add_shape("SH_R10_S_FULL", interpolated)

    # R20 EB local shape: S1 west → S6 east
    r20_e_local_pts = [(CITY_CENTER_LAT, lon) for _, _, lon in r20_stops]
    interpolated = []
    for i in range(len(r20_e_local_pts) - 1):
        seg = interp(r20_e_local_pts[i], r20_e_local_pts[i+1], n=2)
        interpolated.extend(seg if i == 0 else seg[1:])
    add_shape("SH_R20_E_LOCAL", interpolated)

    # R20 EB express shape: same line but slightly offset north (a different
    # corridor/highway). Visually distinguishable from the local pattern.
    offset = 0.003
    r20_e_exp_pts = [(CITY_CENTER_LAT + offset, lon) for _, _, lon in r20_stops
                     if _ != "1st Ave & Library" and _ != "1st Ave & Hospital"]
    # Connect express-stop endpoints with the offset
    exp_stop_lons = [lon for sid, _, lon in r20_stops if sid in ("R20_S1","R20_S2","R20_S4","R20_S6")]
    r20_e_exp_pts = [(CITY_CENTER_LAT + offset, lon) for lon in exp_stop_lons]
    interpolated = []
    for i in range(len(r20_e_exp_pts) - 1):
        seg = interp(r20_e_exp_pts[i], r20_e_exp_pts[i+1], n=2)
        interpolated.extend(seg if i == 0 else seg[1:])
    add_shape("SH_R20_E_EXP", interpolated)

    # R20 WB local shape: reverse direction
    r20_w_local_pts = list(reversed(r20_e_local_pts))
    interpolated = []
    for i in range(len(r20_w_local_pts) - 1):
        seg = interp(r20_w_local_pts[i], r20_w_local_pts[i+1], n=2)
        interpolated.extend(seg if i == 0 else seg[1:])
    add_shape("SH_R20_W_LOCAL", interpolated)

    files["shapes.txt"] = "\n".join(shape_rows) + "\n"
    return files


def main():
    files = build_files()
    print("Generating synthetic GTFS for 4 signups…")

    for signup in SIGNUPS:
        out_dir = GTFS_ROOT / signup
        out_dir.mkdir(parents=True, exist_ok=True)
        out_zip = out_dir / "sample_gtfs.zip"
        with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as zf:
            for name, contents in files.items():
                zf.writestr(name, contents)
        print(f"  wrote {out_zip}")

    print("\nDone. Next:  python scripts/generate_dummy_data.py")


if __name__ == "__main__":
    main()
