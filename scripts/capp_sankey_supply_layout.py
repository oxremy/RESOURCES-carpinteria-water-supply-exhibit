"""
CAPP CarpWater Sankey — UWMP §5 scenario → AF → width + mesh-elbow layout
(Blender 5.x). Planning / CAPP figure companion to sankey_supply_layout.py.

HOW TO RUN
----------
1. Open CAPP_CarpSupplySankey.blend
2. Scripting workspace → Open this file → Run Script (▶)  (registers UI only)
3. 3D View → N → CarpWater CAPP tab → Apply Lineup / Scenario

GEOMETRY MODEL (trunk sides — same as Historical)
-------------------------------------------------
Chain: Down → In → 90 (quarter annulus) → Connect → Trunk
★ BendCenter = inside corner of the L (Cachuma / GW move with W)

CAPP LANE (Planning-only)
-------------------------
CAPP90 is a GW-style quarter annulus (θ=0→+π/2, R_inner=109) about ★.
Flow (one continuous seal):

  CAPPIn (under trunk −Y edge, DownArrow→★) → CAPP90 → CAPPConnect →
  Facility → CAPPOut (−Y) → CAPPOutDown → CAPPOutDownArrow

CAPPIn XY: −Y edge on CarpSupplyIn.ymin, body extends +Y under the trunk;
X from CarpSupplyDownArrow into ★.

Z: each CAPP ribbon keeps its own Sankey Z across scenario/lineup rebuilds
(hand-raised deck, stepped CAPPOut, etc.). XY length/width still follow data.
Facility / OutDown / Arrow origins stay fixed (width-only on OutDown/Arrow).

★ placement: X stays (Connect column / Facility alignment); Y tracks the
trunk so CAPP90's +Y spoke seals to CAPPIn (★.y = trunk.ymin − R_inner).
CAPPConnect lengthens/shortens between that spoke and fixed Facility.

FIXED (never moved by this script):
  CAPPFacility, CAPPOutDown, CAPPOutDownArrow

ADJUST with data:
  CAPPBendCenter (Y only), CAPP90, CAPPIn, CAPPConnect, CAPPOut
  (OutDown / Arrow: width only, origin fixed)

OCEAN OUTFALL + REUSE
---------------------
UWMP §5 does not publish scenario ocean-outfall AF. GSP narrates effluent
formerly to outfall / CAPP redirects it — no quantified outfall series.
Exhibit rule:

  OceanOUT + OceanREUSE  =  historical mean outfall (~1300 AFY)
  OceanREUSE             =  min(CAPP recycled_af, total)
  OceanOUT               =  total − reuse

Pair stacks on −Y (reuse) / +Y (out); combined band centered on trunk ymid.
OceanREUSE = flat XY border slab (rim ~40, Z=1) — not Wireframe tubes.
OceanREUSEArrow slightly inset (width factor) inside OceanREUSE.
Ocean Outfall label AF = residual OceanOUT only (not OUT+REUSE total).
CAPP lane width still uses recycled_af (injection).

AF → WIDTH
---------
Trunk share = AF / (AF_c + AF_s + AF_g) — recycled is NOT in trunk W.
CAPP is basin injection (separate ribbon), not a fourth trunk supply.

UWMP Table 5 has two grammars:
  • Normal (5-1): AVAILABLE supplies with surplus. GW 1,200 = long-term
    yield planning number (not residual after Recycled). Trunk uses Table GW
    as-is → Cachuma+SWP+GW ≈ 4,065 (~demand).
  • Dry (5-2 / 5-4): USE to meet demand. GW residual already nets out
    Recycled → trunk GW_wells = Table_GW + recycled so stack ≈ demand.

CAPP ribbon width always uses recycled_af alone (basin loop).

  AF == 0  → width 0 (lane hidden)
  share < 1% and AF > 0 → width = 40
  else → width = max(40, k × AF)

Scenarios: UWMP Tables 5-1 / 5-2 / 5-4 (2030) via
data/supply_planning_scenarios.json.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import bmesh
import bpy
from bpy.props import IntProperty, FloatProperty
from bpy.types import Operator, Panel
from mathutils import Vector


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

def _resolve_data_path() -> Path:
    try:
        candidate = (
            Path(__file__).resolve().parent.parent
            / "data"
            / "supply_planning_scenarios.json"
        )
        if candidate.exists():
            return candidate
    except NameError:
        pass
    if bpy.data.filepath:
        candidate = (
            Path(bpy.data.filepath).resolve().parent
            / "data"
            / "supply_planning_scenarios.json"
        )
        if candidate.exists():
            return candidate
    return Path(
        "/Users/jeremyknox/CarpWater/CarpWater/data/supply_planning_scenarios.json"
    )


_DATA_PATH = None


def data_path() -> Path:
    global _DATA_PATH
    if _DATA_PATH is None:
        _DATA_PATH = _resolve_data_path()
    return _DATA_PATH


def _load_data() -> dict:
    path = data_path()
    if not path.exists():
        raise FileNotFoundError(f"Missing data file: {path}")
    with path.open() as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Layout constants
# ---------------------------------------------------------------------------

W_VIS_MIN = 40.0  # floor for any non-zero lane; also <1% collapse width
SHARE_COLLAPSE = 0.01  # share of total AF below this → W_VIS_MIN
K_AF_TO_WIDTH = 0.75  # 1 AF → 0.75 Blender units
LINEUP_WIDTH = 1000.0

# Rest-pose margins from sealed GroundWater elbow @ W=3000, w=1000.
BEND_MARGIN_X = 116.766
BEND_MARGIN_Y = 109.003
BEND_Z = 2200.0

# Inner radius of 90 annulus (= gap from ★ to ribbon body). Matches GW R_min.
R_INNER = BEND_MARGIN_Y

TIP_EPS = 10.0
NINETY_STEPS = 24  # angular subdivisions
SEAL_EPS = 15.0  # max |In tip − 90 meeting edge| for "sealed"

# Horizontal Connect runouts (90 → CarpSupplyIn along −X).
# Barely overlap the 90; OK to overlap the trunk.
CONNECT_OVERLAP_90 = 50.0
CONNECT_OVERLAP_TRUNK = 40.0
CONNECT_MIN_LEN = 80.0  # safety floor if tip math collapses

# Keep CarpSupplyIn inland tip west of every ★ so 90s don't overlay the trunk.
TRUNK_CLEAR_90 = 50.0
TRUNK_MIN_LEN = 200.0

# CAPP lane — θ matches Groundwater (0 → +π/2).
# ★.x stays put; ★.y tracks trunk so +Y spoke seals to CAPPIn.
CAPP_R_INNER = R_INNER
CAPP_THETA0 = 0.0
CAPP_THETA1 = 0.5 * math.pi
CAPP_BORDER_RIM_XY = 40.0  # OceanREUSE flat border width in Sankey XY
CAPP_BORDER_Z = 1.0  # flat border slab thickness (not Wireframe tubes)
# OceanREUSEArrow sits inside OceanREUSE — slightly narrower than the ribbon.
OCEAN_REUSE_ARROW_WIDTH_FACTOR = 0.92
CAPP_FIXED = (
    "CAPPFacility",
    "CAPPOutDown",
    "CAPPOutDownArrow",
)

WIDTH_AXIS = {
    "CarpSupplyIn": "y",
    "SWPIn": "y",
    "OceanOUT": "y",
    "OceanREUSE": "y",
    "OceanOutArrow": "y",
    "CarpSupplyDownArrow": "y",
    "CachumaIn": "x",
    "GroundWaterIn": "x",
    "GroundWaterDown": "x",
    "CAPPOut": "x",
    # CAPPOutDownArrow: width along local Y (→ Sankey X). Local X is tip length
    # and, with rest rotation, maps toward world Z — do not scale X for width.
    "CAPPOutDownArrow": "y",
}

# Rest-pose tip length scale on CAPPOutDownArrow (local X). Keep fixed so width
# changes do not stretch the arrow into the map / past OutDown.
CAPP_OUT_ARROW_TIP_SCALE_X = 46.0724

SIDE_CHAINS = (
    {
        "key": "c",
        "in_name": "CachumaIn",
        "bend_name": "CachumaBendCenter",
        "ninety_name": "Cachuma90",
        "connect_name": "CachumaConnect",
        "down_name": None,
        "tip_key": "ymin",
        "length_axis": "y",
        # Quarter annulus: +X (In tip) → −Y (into trunk from above)
        "theta0": 0.0,
        "theta1": -0.5 * math.pi,
    },
    {
        "key": "g",
        "in_name": "GroundWaterIn",
        "bend_name": "GroundWaterBendCenter",
        "ninety_name": "GroundWater90",
        "connect_name": "GroundWaterConnect",
        "down_name": "GroundWaterDown",
        "tip_key": "ymax",
        "length_axis": "y",
        # Quarter annulus: +X (In tip) → +Y (into trunk from below)
        "theta0": 0.0,
        "theta1": 0.5 * math.pi,
    },
)

# Objects shown/hidden with their lane (AF==0 → hidden).
LANE_OBJECTS = {
    "c": (
        "CachumaIn",
        "Cachuma90",
        "CachumaConnect",
        "CachumaBendCenter",
    ),
    "s": ("SWPIn",),
    "g": (
        "GroundWaterIn",
        "GroundWater90",
        "GroundWaterConnect",
        "GroundWaterBendCenter",
        "GroundWaterDown",
        "GroundWaterUp",
    ),
}

# Year / AF text labels: inner WHITE + outer BLACK pairs.
# Titles match UWMP Figure 4-1 / live Blender bodies (do not "fix" punctuation).
LABEL_PAIRS = (
    {
        "names": ("CachumaLabel", "CachumaLabel.001"),
        "kind": "lane",
        "title": "Cachuma Project",
        "af_key": "af_c",
        "pct_key": "pct_c",
        "lane_key": "c",
    },
    {
        "names": ("SWPLabel", "SWPLabel.001"),
        "kind": "lane",
        "title": "State Water Project",
        "af_key": "af_s",
        "pct_key": "pct_s",
        "lane_key": "s",
    },
    {
        "names": ("GroundWaterLabel", "GroundWaterLabel.001"),
        "kind": "lane",
        "title": "Groundwater",
        "af_key": "af_g",
        "pct_key": "pct_g",
        "lane_key": "g",
    },
    {
        "names": ("CarpSupplyLabel", "CarpSupplyLabel.001"),
        "kind": "total",
    },
    {
        # Observed pair: OceanOutput (white) + OceanOutput.002 (black outline).
        # OceanOutput.001 is an unrelated stray copy — leave alone.
        "names": ("OceanOutput", "OceanOutput.002"),
        "kind": "ocean",
    },
    {
        # CAPP injection → basin (af_r / recycled). Not GW pumping.
        "names": ("CAPPLabel", "CAPPLabel.001"),
        "kind": "capp",
    },
    {
        # OceanREUSE limb: effluent diverted from outfall to CAPP (af_ocean_reuse).
        "names": ("DivertedOutfall", "DivertedOutfall.001"),
        "kind": "diverted_outfall",
    },
)

OCEAN_OBJECTS = ("OceanOUT", "OceanOutArrow", "OceanREUSE", "OceanREUSEArrow")

# Objects shown/hidden with CAPP injection width.
CAPP_LANE_OBJECTS = (
    "CAPPIn",
    "CAPPConnect",
    "CAPP90",
    "CAPPBendCenter",
    "CAPPOut",
    "CAPPOutDown",
    "CAPPOutDownArrow",
    "CAPPFacility",
)

OCEAN_REUSE_ARROW_NAME = "OceanREUSEArrow"


# ---------------------------------------------------------------------------
# Data rows
# ---------------------------------------------------------------------------

def _optional_af(row: dict, key: str):
    """Return float AF or None when key missing / JSON null."""
    if key not in row or row[key] is None:
        return None
    return float(row[key])


def get_example_row() -> dict:
    ex = _load_data()["example"]
    return {
        "label": ex.get("label", "Example"),
        "scenario_id": ex.get("scenario_id"),
        "year": ex.get("planning_year", 2030),
        "groundwater_af": float(ex["groundwater_af"]),
        "cachuma_af": float(ex["cachuma_af"]),
        "swp_af": float(ex["swp_af"]),
        "recycled_af": float(ex.get("recycled_af", 1000.0)),
        "demand_af": ex.get("demand_af"),
        "ocean_outfall_af": _optional_af(ex, "ocean_outfall_af"),
    }


def get_scenario_row(scenario_id: str) -> dict:
    data = _load_data()
    for row in data.get("scenarios", []):
        if row["id"] == scenario_id:
            return {
                "label": row.get("label", scenario_id),
                "scenario_id": row["id"],
                "year": int(row.get("planning_year", 2030)),
                "table": row.get("table"),
                "groundwater_af": float(row["groundwater_af"]),
                "cachuma_af": float(row["cachuma_af"]),
                "swp_af": float(row["swp_af"]),
                "recycled_af": float(row.get("recycled_af", 1000.0)),
                "demand_af": row.get("demand_af"),
                "supply_af": row.get("supply_af"),
                "balance_af": row.get("balance_af"),
                "ocean_outfall_af": _optional_af(row, "ocean_outfall_af"),
            }
    raise KeyError(f"No planning scenario '{scenario_id}' in {data_path()}")


def get_year_row(year: int) -> dict:
    """Compatibility shim — planning file has no historical years."""
    raise KeyError(
        f"capp_sankey_supply_layout has no historical year {year}; "
        "use get_scenario_row('normal_wy'|'single_dry'|'five_year_avg')"
    )


def get_lineup_row() -> dict:
    return {
        "label": "Lineup1000",
        "scenario_id": None,
        "year": 2030,
        "groundwater_af": LINEUP_WIDTH / K_AF_TO_WIDTH,
        "cachuma_af": LINEUP_WIDTH / K_AF_TO_WIDTH,
        "swp_af": LINEUP_WIDTH / K_AF_TO_WIDTH,
        "recycled_af": 1000.0,
        "demand_af": 3.0 * LINEUP_WIDTH / K_AF_TO_WIDTH,
        # Total effluent limb = historical mean (split reuse/out in attach).
        "ocean_outfall_af": historical_ocean_outfall_mean_af(),
    }


# ---------------------------------------------------------------------------
# Math
# ---------------------------------------------------------------------------

def lane_width_from_af(af: float, total_af: float) -> tuple[float, float, str]:
    """
    Returns (width, share 0..1, mode).
      mode: 'zero' | 'collapse' | 'scaled'
    """
    af = float(af)
    if af <= 0.0:
        return 0.0, 0.0, "zero"
    share = (af / total_af) if total_af > 0.0 else 0.0
    if share < SHARE_COLLAPSE:
        return W_VIS_MIN, share, "collapse"
    return max(W_VIS_MIN, K_AF_TO_WIDTH * af), share, "scaled"


def floor_forced_width(w: float) -> float:
    """Apply Widths path: 0 stays 0; any positive width ≥ W_VIS_MIN."""
    w = float(w)
    if w <= 0.0:
        return 0.0
    return max(W_VIS_MIN, w)


def attach_af_share(
    layout: dict,
    cachuma_af: float,
    swp_af: float,
    gw_af: float,
    mode_c: str,
    mode_s: str,
    mode_g: str,
    pct_c: float,
    pct_s: float,
    pct_g: float,
) -> dict:
    """AF + share% (0–100) for hover labels; modes for debug."""
    total = float(cachuma_af) + float(swp_af) + float(gw_af)
    layout.update({
        "af_c": float(cachuma_af),
        "af_s": float(swp_af),
        "af_g": float(gw_af),
        "af_total": total,
        "pct_c": 100.0 * pct_c,
        "pct_s": 100.0 * pct_s,
        "pct_g": 100.0 * pct_g,
        "mode_c": mode_c,
        "mode_s": mode_s,
        "mode_g": mode_g,
    })
    return layout


def _historical_data_path() -> Path:
    try:
        candidate = (
            Path(__file__).resolve().parent.parent / "data" / "supply_historical.json"
        )
        if candidate.exists():
            return candidate
    except NameError:
        pass
    if bpy.data.filepath:
        candidate = (
            Path(bpy.data.filepath).resolve().parent
            / "data"
            / "supply_historical.json"
        )
        if candidate.exists():
            return candidate
    return Path("/Users/jeremyknox/CarpWater/CarpWater/data/supply_historical.json")


def historical_ocean_outfall_mean_af() -> float:
    """
    Mean of known historical ocean_outfall_af years, rounded to nearest 100 AF.
    Same basis as sankey_supply_layout.average_ocean_outfall_af (≈1300 AFY).
    """
    path = _historical_data_path()
    if not path.exists():
        return 1300.0
    with path.open() as f:
        data = json.load(f)
    vals = []
    for row in data.get("years", []):
        af = row.get("ocean_outfall_af")
        if af is not None and float(af) > 0.0:
            vals.append(float(af))
    if not vals:
        return 1300.0
    mean = sum(vals) / len(vals)
    return float(int(round(mean / 100.0) * 100))


def average_ocean_outfall_af() -> float:
    """Planning default = historical mean total outfall (not a residual guess)."""
    return historical_ocean_outfall_mean_af()


def attach_ocean_outfall(layout: dict, ocean_outfall_af) -> dict:
    """
    Total CSD effluent limb (OceanOUT + OceanREUSE).

    ocean_outfall_af in planning JSON = TOTAL (historical mean), not residual.
    Missing → historical_ocean_outfall_mean_af(). Split applied in
    attach_capp_injection / attach_ocean_reuse_split.
    """
    if ocean_outfall_af is None:
        af = historical_ocean_outfall_mean_af()
        layout["af_ocean_total"] = af
        layout["ocean_total_unknown"] = True
    else:
        af = float(ocean_outfall_af)
        layout["af_ocean_total"] = max(0.0, af)
        layout["ocean_total_unknown"] = False
    return layout


def attach_capp_injection(layout: dict, recycled_af) -> dict:
    """CAPP injection width (lane) + split of ocean total into reuse / out."""
    af = 1000.0 if recycled_af is None else float(recycled_af)
    if af <= 0.0:
        layout["af_r"] = 0.0
        layout["w_r"] = 0.0
        layout["capp_active"] = False
    else:
        layout["af_r"] = af
        layout["w_r"] = max(W_VIS_MIN, K_AF_TO_WIDTH * af)
        layout["capp_active"] = True
    return attach_ocean_reuse_split(layout)


def attach_ocean_reuse_split(layout: dict) -> dict:
    """
    OceanOUT + OceanREUSE = af_ocean_total (historical mean by default).
    Reuse slice = min(CAPP recycled, total); Out = remainder still to Pacific.
    """
    total = float(layout.get("af_ocean_total") or historical_ocean_outfall_mean_af())
    recycled = float(layout.get("af_r") or 0.0)
    reuse = min(max(0.0, recycled), total) if total > 0.0 else 0.0
    out = max(0.0, total - reuse)

    layout["af_ocean_total"] = total
    layout["af_ocean_reuse"] = reuse
    layout["af_ocean"] = out  # residual label / OceanOUT

    if reuse > 0.0:
        layout["w_ocean_reuse"] = max(W_VIS_MIN, K_AF_TO_WIDTH * reuse)
    else:
        layout["w_ocean_reuse"] = 0.0

    if out > 0.0:
        layout["w_ocean"] = max(W_VIS_MIN, K_AF_TO_WIDTH * out)
        layout["ocean_active"] = True
    else:
        layout["w_ocean"] = 0.0
        layout["ocean_active"] = False

    # "Unknown" only if total was imputed; residual itself is derived.
    layout["ocean_unknown"] = bool(layout.get("ocean_total_unknown"))
    return layout


def ocean_active(layout: dict) -> bool:
    """True when residual OceanOUT (still-to-Pacific) has width."""
    return bool(layout.get("ocean_active")) and float(layout.get("w_ocean", 0.0)) > 0.0


def ocean_unknown(layout: dict) -> bool:
    return bool(layout.get("ocean_unknown"))


def capp_active(layout: dict) -> bool:
    return bool(layout.get("capp_active"))


def widths_from_af(cachuma_af: float, swp_af: float, gw_af: float) -> dict:
    cachuma_af = float(cachuma_af)
    swp_af = float(swp_af)
    gw_af = float(gw_af)
    total = max(0.0, cachuma_af + swp_af + gw_af)

    w_c, pct_c, mode_c = lane_width_from_af(cachuma_af, total)
    w_s, pct_s, mode_s = lane_width_from_af(swp_af, total)
    w_g, pct_g, mode_g = lane_width_from_af(gw_af, total)

    layout = _lanes(w_c, w_s, w_g)
    return attach_af_share(
        layout,
        cachuma_af, swp_af, gw_af,
        mode_c, mode_s, mode_g,
        pct_c, pct_s, pct_g,
    )


# Meet-demand UWMP tables (5-2 / 5-4): Supply Total ≈ Demand Total.
# Normal (5-1) lists available supplies with a large surplus — different GW meaning.
_MEET_DEMAND_SUPPLY_EPS_AF = 50.0


def well_production_af(
    groundwater_table_af: float,
    recycled_af: float,
    *,
    demand_af: float | None = None,
    supply_af: float | None = None,
) -> float:
    """
    Trunk GW ribbon AF for the CAPP Sankey.

    Dry Tables 5-2 / 5-4: Supply≈Demand; GW is residual after Recycled is
    counted as a supply column. Restore well production:

      GW_wells = Table_GW + recycled_af

    Normal Table 5-1: Supply≫Demand; GW 1,200 is long-term average yield
    (availability), not a residual that nets out CAPP. Do NOT add recycled
    (that would rebuild the 5,065 availability total and inflate the trunk).

    CAPP ribbon width still uses recycled_af alone (basin injection loop).
    """
    gw = max(0.0, float(groundwater_table_af))
    r = max(0.0, float(recycled_af or 0.0))
    if (
        demand_af is not None
        and supply_af is not None
        and abs(float(supply_af) - float(demand_af)) <= _MEET_DEMAND_SUPPLY_EPS_AF
    ):
        return gw + r
    return gw


def widths_forced_equal(width: float) -> dict:
    w = float(width)
    layout = _lanes(w, w, w)
    # Synthetic equal shares for hover / props
    af = w / K_AF_TO_WIDTH
    return attach_af_share(
        layout, af, af, af,
        "scaled", "scaled", "scaled",
        1.0 / 3.0, 1.0 / 3.0, 1.0 / 3.0,
    )


def _lanes(w_c: float, w_s: float, w_g: float) -> dict:
    """
    Lane order +Y → −Y: [ Cachuma | SWP | Groundwater ]
    Trunk centerline Y = 0. Zero-width lanes consume no trunk band.
    """
    W = w_c + w_s + w_g
    if W <= 0.0:
        return {
            "w_c": 0.0, "w_s": 0.0, "w_g": 0.0,
            "W": 0.0, "H": 0.0,
            "y_c": 0.0, "y_s": 0.0, "y_g": 0.0,
        }
    return {
        "w_c": w_c,
        "w_s": w_s,
        "w_g": w_g,
        "W": W,
        "H": W / 2.0,
        "y_c": W / 2.0 - w_c / 2.0,
        "y_s": W / 2.0 - w_c - w_s / 2.0,
        "y_g": -W / 2.0 + w_g / 2.0,
    }


def lane_active(layout: dict, key: str) -> bool:
    return float(layout.get(f"w_{key}", 0.0)) > 0.0


def bend_center_sankey(side: str, layout: dict, in_xmid: float) -> Vector:
    H = layout["H"]
    w = layout["w_c"] if side == "c" else layout["w_g"]
    x_inner = in_xmid - w / 2.0
    x = x_inner - BEND_MARGIN_X
    y = (H + BEND_MARGIN_Y) if side == "c" else -(H + BEND_MARGIN_Y)
    return Vector((x, y, BEND_Z))


def side_width(chain: dict, layout: dict) -> float:
    return layout["w_c"] if chain["key"] == "c" else layout["w_g"]


# ---------------------------------------------------------------------------
# SankeyFrame space helpers
# ---------------------------------------------------------------------------

def sankey_frame():
    sf = bpy.data.objects.get("SankeyFrame")
    if not sf:
        raise RuntimeError("SankeyFrame empty not found")
    return sf


def world_to_sankey(world_co: Vector) -> Vector:
    return sankey_frame().matrix_world.inverted() @ Vector(world_co)


def sankey_to_world(sankey_co: Vector) -> Vector:
    return sankey_frame().matrix_world @ Vector(sankey_co)


def mesh_sankey_bbox(obj) -> dict:
    """Axis-aligned Sankey bbox. Meshes use verts; curves/others use bound_box."""
    if obj.type == "MESH" and obj.data and len(obj.data.vertices) > 0:
        ls = [world_to_sankey(obj.matrix_world @ v.co) for v in obj.data.vertices]
    else:
        ls = [world_to_sankey(obj.matrix_world @ Vector(c)) for c in obj.bound_box]
    xs = [p.x for p in ls]
    ys = [p.y for p in ls]
    zs = [p.z for p in ls]
    return {
        "xmin": min(xs), "xmax": max(xs),
        "ymin": min(ys), "ymax": max(ys),
        "zmin": min(zs), "zmax": max(zs),
        "xmid": 0.5 * (min(xs) + max(xs)),
        "ymid": 0.5 * (min(ys) + max(ys)),
        "zmid": 0.5 * (min(zs) + max(zs)),
    }


def set_object_sankey_origin(obj, sankey_co: Vector) -> None:
    """Move object origin to sankey_co (honors parent inverse)."""
    world = sankey_to_world(sankey_co)
    parent = obj.parent
    if parent is not None:
        M = parent.matrix_world @ obj.matrix_parent_inverse
        obj.location = M.inverted() @ world
    else:
        obj.location = world


def translate_object_sankey(obj, delta_sankey: Vector) -> None:
    origin_s = world_to_sankey(obj.matrix_world.translation)
    new_world = sankey_to_world(origin_s + delta_sankey)
    mw = obj.matrix_world.copy()
    mw.translation = new_world
    obj.matrix_world = mw


def _depsgraph_update() -> None:
    bpy.context.view_layer.update()


# ---------------------------------------------------------------------------
# Width application
# ---------------------------------------------------------------------------

def _local_span(obj, axis: str) -> float:
    coords = [getattr(v.co, axis) for v in obj.data.vertices]
    return max(coords) - min(coords) or 2.0


def _set_mesh_width_scale(obj, axis: str, target_width: float) -> None:
    span = _local_span(obj, axis)
    scale_val = target_width / span
    sx, sy, sz = obj.scale
    if axis == "x":
        obj.scale = (scale_val, sy, sz)
    elif axis == "y":
        obj.scale = (sx, scale_val, sz)
    else:
        obj.scale = (sx, sy, scale_val)


def _set_curve_extrude_width(obj, target_width: float) -> None:
    if obj.type == "CURVE":
        # Allow true zero when lane is off; tiny epsilon only for positive stubs.
        if target_width <= 0.0:
            obj.data.extrude = 0.0
        else:
            obj.data.extrude = max(0.01, target_width / 2.0)


def set_lane_visibility(layout: dict) -> list[str]:
    """Hide whole source chains when AF → width is zero; show otherwise."""
    notes = []
    for key, names in LANE_OBJECTS.items():
        visible = lane_active(layout, key)
        for name in names:
            obj = bpy.data.objects.get(name)
            if not obj:
                continue
            obj.hide_viewport = not visible
            obj.hide_render = not visible
        notes.append(f"{key}:{'on' if visible else 'off'}")
    return notes


def set_ocean_visibility(layout: dict) -> str:
    """Show/hide OceanOUT with residual; OceanREUSE + ReuseArrow with reuse width."""
    out_vis = ocean_active(layout)
    reuse_vis = float(layout.get("w_ocean_reuse", 0.0)) > 0.0
    arrow_out_vis = out_vis
    arrow_reuse_vis = reuse_vis

    ocean = bpy.data.objects.get("OceanOUT")
    if ocean:
        ocean.hide_viewport = not out_vis
        ocean.hide_render = not out_vis
    oarr = bpy.data.objects.get("OceanOutArrow")
    if oarr:
        oarr.hide_viewport = not arrow_out_vis
        oarr.hide_render = not arrow_out_vis

    reuse = bpy.data.objects.get("OceanREUSE")
    if reuse:
        reuse.hide_viewport = not reuse_vis
        reuse.hide_render = not reuse_vis
    rarr = bpy.data.objects.get(OCEAN_REUSE_ARROW_NAME)
    if rarr:
        rarr.hide_viewport = not arrow_reuse_vis
        rarr.hide_render = not arrow_reuse_vis

    if not out_vis and not reuse_vis:
        return "ocean:off"
    total = layout.get("af_ocean_total", 0)
    return (
        f"ocean:total={total:.0f} "
        f"out={'on' if out_vis else 'off'} w={layout.get('w_ocean', 0):.0f} "
        f"af_out={layout.get('af_ocean', 0):.0f} "
        f"reuse={'on' if reuse_vis else 'off'} w={layout.get('w_ocean_reuse', 0):.0f} "
        f"af_reuse={layout.get('af_ocean_reuse', 0):.0f}"
    )


def set_capp_visibility(layout: dict) -> str:
    visible = capp_active(layout)
    for name in CAPP_LANE_OBJECTS:
        obj = bpy.data.objects.get(name)
        if not obj:
            continue
        # Bend empty stays visible in viewport for editing even if off? hide with lane.
        obj.hide_viewport = not visible
        obj.hide_render = not visible
    return f"capp:{'on' if visible else 'off'} w={layout.get('w_r', 0):.0f}"


def apply_widths(layout: dict) -> list[str]:
    w_c, w_s, w_g, W = layout["w_c"], layout["w_s"], layout["w_g"], layout["W"]
    w_ocean = float(layout.get("w_ocean", 0.0))
    w_r = float(layout.get("w_r", 0.0))
    changed = []
    mesh_widths = {
        "CarpSupplyIn": W,
        "SWPIn": w_s,
        "CarpSupplyDownArrow": W,
        "CachumaIn": w_c,
        "GroundWaterIn": w_g,
        "GroundWaterDown": w_g,
    }
    # OceanOUT / OceanREUSE / ocean arrows placed by update_ocean_pair.
    for name, width in mesh_widths.items():
        obj = bpy.data.objects.get(name)
        axis = WIDTH_AXIS.get(name)
        if not obj or obj.type != "MESH" or not axis:
            continue
        if width <= 0.0:
            continue
        _set_mesh_width_scale(obj, axis, width)
        changed.append(name)

    down = bpy.data.objects.get("CarpSupplyDown")
    if down and W > 0.0:
        _set_curve_extrude_width(down, W)
        changed.append("CarpSupplyDown")
    up = bpy.data.objects.get("GroundWaterUp")
    if up:
        _set_curve_extrude_width(up, w_g)
        changed.append("GroundWaterUp")
    cdown = bpy.data.objects.get("CAPPOutDown")
    if cdown and w_r > 0.0:
        _set_curve_extrude_width(cdown, w_r)
        changed.append("CAPPOutDown")
    arrow = bpy.data.objects.get("CAPPOutDownArrow")
    if arrow and arrow.type == "MESH" and w_r > 0.0:
        _set_capp_out_arrow_width(arrow, w_r)
        changed.append("CAPPOutDownArrow")

    _depsgraph_update()
    return changed


def _set_capp_out_arrow_width(obj: bpy.types.Object, target_width: float) -> None:
    """
    Width = local Y scale (ribbon across Sankey X). Tip length = local X scale,
    locked to rest pose so the arrow stays at the OutDown tip (not into the map).
    Origin / rotation are left untouched.
    """
    span_y = _local_span(obj, "y")
    sy = target_width / span_y
    obj.scale = (CAPP_OUT_ARROW_TIP_SCALE_X, sy, obj.scale[2])


# ---------------------------------------------------------------------------
# SWP lane + Down alignment
# ---------------------------------------------------------------------------

def place_swp_on_lane(layout: dict) -> str:
    """Move SWPIn so its Sankey Y centerline is y_s; keep X (length handled separately)."""
    if not lane_active(layout, "s"):
        return "SWPIn: off (AF=0)"
    obj = bpy.data.objects.get("SWPIn")
    if not obj or obj.type != "MESH":
        return "SWPIn: missing"
    _depsgraph_update()
    bb = mesh_sankey_bbox(obj)
    dy = layout["y_s"] - bb["ymid"]
    if abs(dy) <= 1.0:
        return f"SWPIn: on y_s={layout['y_s']:.0f}"
    translate_object_sankey(obj, Vector((0.0, dy, 0.0)))
    _depsgraph_update()
    return f"SWPIn: ymid→{layout['y_s']:.0f}"


def stretch_mesh_tip_to_x(
    obj: bpy.types.Object,
    tip_key: str,
    target_x: float,
    length_axis: str = "x",
) -> str:
    """
    Stretch/shrink mesh along length so tip_key (xmin|xmax) hits target_x.
    Opposite X end stays fixed.
    """
    _depsgraph_update()
    bb0 = mesh_sankey_bbox(obj)
    source_key = "xmin" if tip_key == "xmax" else "xmax"
    source_x = bb0[source_key]
    tip_x = bb0[tip_key]
    old_len = abs(source_x - tip_x)
    new_len = abs(source_x - target_x)

    if old_len < 1e-3:
        return f"{obj.name}: skip (zero length)"
    if abs(tip_x - target_x) <= TIP_EPS:
        return f"{obj.name}: {tip_key} ok ({tip_x:.0f}≈{target_x:.0f})"
    if new_len < 1e-3:
        return f"{obj.name}: skip (target collapses length)"

    factor = new_len / old_len
    sx, sy, sz = obj.scale
    if length_axis == "x":
        obj.scale = (sx * factor, sy, sz)
    elif length_axis == "y":
        obj.scale = (sx, sy * factor, sz)
    else:
        obj.scale = (sx, sy, sz * factor)

    _depsgraph_update()
    bb1 = mesh_sankey_bbox(obj)
    dx = source_x - bb1[source_key]
    translate_object_sankey(obj, Vector((dx, 0.0, 0.0)))
    _depsgraph_update()
    bb2 = mesh_sankey_bbox(obj)
    return (
        f"{obj.name}: {tip_key} {tip_x:.0f}→{bb2[tip_key]:.0f} "
        f"(target {target_x:.0f})"
    )


def bend_center_xs(layout: dict | None = None) -> list[float]:
    """★ X for active side lanes only (skipped when width=0)."""
    xs = []
    for chain in SIDE_CHAINS:
        if layout is not None and not lane_active(layout, chain["key"]):
            continue
        bend = bpy.data.objects.get(chain["bend_name"])
        if not bend:
            continue
        p = world_to_sankey(bend.matrix_world.translation)
        xs.append(p.x)
    return xs


def target_trunk_inland_xmax(layout: dict | None = None) -> float | None:
    """
    Inland tip of CarpSupplyIn: west of every active ★ so 90s clear the trunk.
    Returns None if no active side bends.
    """
    xs = bend_center_xs(layout)
    if not xs:
        return None
    return min(xs) - TRUNK_CLEAR_90


def shrink_trunk_from_nineties(layout: dict | None = None) -> str:
    """
    Pull CarpSupplyIn inland tip (−← +X) so it stops west of all active BendCenters.
    Oceanward xmin stays fixed; width (Y) unchanged.
    """
    obj = bpy.data.objects.get("CarpSupplyIn")
    if not obj or obj.type != "MESH":
        return "CarpSupplyIn: missing"
    target = target_trunk_inland_xmax(layout)
    if target is None:
        return "CarpSupplyIn: no active ★ to clear"

    _depsgraph_update()
    bb = mesh_sankey_bbox(obj)
    # Don't shrink past a short stub off the Down/ocean end.
    min_xmax = bb["xmin"] + TRUNK_MIN_LEN
    target = max(target, min_xmax)
    return stretch_mesh_tip_to_x(obj, "xmax", target, length_axis="x")


def extend_swp_to_trunk(layout: dict | None = None) -> str:
    """SWP western tip slightly overlaps CarpSupplyIn inland tip."""
    if layout is not None and not lane_active(layout, "s"):
        return "SWPIn: off (AF=0)"
    swp = bpy.data.objects.get("SWPIn")
    trunk = bpy.data.objects.get("CarpSupplyIn")
    if not swp or swp.type != "MESH" or not trunk or trunk.type != "MESH":
        return "SWPIn: missing trunk/SWP"
    _depsgraph_update()
    tb = mesh_sankey_bbox(trunk)
    target_xmin = tb["xmax"] - CONNECT_OVERLAP_TRUNK
    return stretch_mesh_tip_to_x(swp, "xmin", target_xmin, length_axis="x")


def align_down_to_in(chain: dict, layout: dict | None = None) -> str | None:
    """Keep GroundWaterDown (etc.) X-aligned with its In centerline."""
    down_name = chain.get("down_name")
    if not down_name:
        return None
    if layout is not None and not lane_active(layout, chain["key"]):
        return f"{down_name}: off"
    down = bpy.data.objects.get(down_name)
    inn = bpy.data.objects.get(chain["in_name"])
    if not down or down.type != "MESH" or not inn or inn.type != "MESH":
        return f"{down_name}: skip"
    _depsgraph_update()
    bi = mesh_sankey_bbox(inn)
    bd = mesh_sankey_bbox(down)
    dx = bi["xmid"] - bd["xmid"]
    if abs(dx) <= 1.0:
        return f"{down_name}: xmid aligned"
    translate_object_sankey(down, Vector((dx, 0.0, 0.0)))
    _depsgraph_update()
    return f"{down_name}: xmid→{bi['xmid']:.0f}"


# ---------------------------------------------------------------------------
# Bend centers
# ---------------------------------------------------------------------------

def place_bend_centers(layout: dict) -> list[str]:
    notes = []
    for chain in SIDE_CHAINS:
        if not lane_active(layout, chain["key"]):
            notes.append(f"{chain['bend_name']}: off")
            continue
        inn = bpy.data.objects.get(chain["in_name"])
        bend = bpy.data.objects.get(chain["bend_name"])
        if not inn or inn.type != "MESH" or not bend:
            notes.append(f"{chain['bend_name']}: skipped")
            continue
        bb = mesh_sankey_bbox(inn)
        target = bend_center_sankey(chain["key"], layout, bb["xmid"])
        set_object_sankey_origin(bend, target)
        notes.append(f"{chain['bend_name']}→({target.x:.0f},{target.y:.0f})")
    _depsgraph_update()
    return notes


# ---------------------------------------------------------------------------
# 90° elbows — regenerate quarter annulus
# ---------------------------------------------------------------------------

def _ensure_mesh_object(name: str) -> bpy.types.Object:
    obj = bpy.data.objects.get(name)
    sf = sankey_frame()
    if obj is None:
        mesh = bpy.data.meshes.new(name)
        obj = bpy.data.objects.new(name, mesh)
        bpy.context.scene.collection.objects.link(obj)
        obj.parent = sf
    elif obj.type != "MESH":
        raise RuntimeError(f"{name} exists but is not a MESH")
    if obj.parent != sf:
        obj.parent = sf
    return obj


def rebuild_ninety_annulus(
    obj: bpy.types.Object,
    center_s: Vector,
    r_inner: float,
    r_outer: float,
    theta0: float,
    theta1: float,
    steps: int = NINETY_STEPS,
) -> None:
    """
    Replace obj mesh with a flat quarter-annulus in the Sankey plane.
    Object origin is placed at center_s; local axes follow SankeyFrame
    (rotation 0 in parent space). Verts are authored via world round-trip
    so parent inverse cannot warp them.
    """
    obj.scale = (1.0, 1.0, 1.0)
    obj.rotation_euler = (0.0, 0.0, 0.0)
    set_object_sankey_origin(obj, center_s)
    _depsgraph_update()

    mw_inv = obj.matrix_world.inverted()
    bm = bmesh.new()
    rows = []
    for i in range(steps + 1):
        t = i / steps
        theta = theta0 * (1.0 - t) + theta1 * t
        ct, st = math.cos(theta), math.sin(theta)
        row = []
        for r in (r_inner, r_outer):
            sankey_p = Vector((
                center_s.x + r * ct,
                center_s.y + r * st,
                center_s.z,
            ))
            local = mw_inv @ sankey_to_world(sankey_p)
            row.append(bm.verts.new(local))
        rows.append(row)
    bm.verts.ensure_lookup_table()

    for i in range(steps):
        a0, a1 = rows[i][0], rows[i][1]
        b0, b1 = rows[i + 1][0], rows[i + 1][1]
        # Winding so normals face +SankeyZ (up)
        if theta1 >= theta0:
            bm.faces.new((a0, b0, b1, a1))
        else:
            bm.faces.new((a0, a1, b1, b0))

    mesh = obj.data
    bm.to_mesh(mesh)
    bm.free()
    mesh.update()
    _depsgraph_update()


def update_nineties(layout: dict) -> list[str]:
    """Rebuild both side 90 elbows about their BendCenters."""
    notes = []
    for chain in SIDE_CHAINS:
        if not lane_active(layout, chain["key"]):
            notes.append(f"{chain['ninety_name']}: off")
            continue
        bend = bpy.data.objects.get(chain["bend_name"])
        if not bend:
            notes.append(f"{chain['ninety_name']}: no bend empty")
            continue
        w = side_width(chain, layout)
        r_inner = R_INNER
        r_outer = r_inner + w
        center = world_to_sankey(bend.matrix_world.translation)
        center = Vector((center.x, center.y, BEND_Z))

        obj = _ensure_mesh_object(chain["ninety_name"])
        rebuild_ninety_annulus(
            obj,
            center,
            r_inner,
            r_outer,
            chain["theta0"],
            chain["theta1"],
        )
        notes.append(
            f"{chain['ninety_name']}: R={r_inner:.0f}..{r_outer:.0f} "
            f"at ({center.x:.0f},{center.y:.0f})"
        )
    return notes


# ---------------------------------------------------------------------------
# Connect runouts (90 → CarpSupplyIn along −X)
# ---------------------------------------------------------------------------

def rebuild_sankey_rect(
    obj: bpy.types.Object,
    xmin: float,
    xmax: float,
    ymin: float,
    ymax: float,
    z: float = BEND_Z,
) -> None:
    """Replace obj with an axis-aligned rectangle in Sankey XY (flat at z)."""
    if xmax < xmin:
        xmin, xmax = xmax, xmin
    if ymax < ymin:
        ymin, ymax = ymax, ymin

    mid = Vector((0.5 * (xmin + xmax), 0.5 * (ymin + ymax), z))
    obj.scale = (1.0, 1.0, 1.0)
    obj.rotation_euler = (0.0, 0.0, 0.0)
    set_object_sankey_origin(obj, mid)
    _depsgraph_update()

    mw_inv = obj.matrix_world.inverted()
    corners_s = (
        Vector((xmin, ymin, z)),
        Vector((xmax, ymin, z)),
        Vector((xmax, ymax, z)),
        Vector((xmin, ymax, z)),
    )
    bm = bmesh.new()
    verts = [bm.verts.new(mw_inv @ sankey_to_world(p)) for p in corners_s]
    bm.verts.ensure_lookup_table()
    bm.faces.new((verts[0], verts[1], verts[2], verts[3]))
    mesh = obj.data
    bm.to_mesh(mesh)
    bm.free()
    mesh.update()
    _depsgraph_update()


def connect_band_y(chain: dict, center: Vector, w: float) -> tuple[float, float]:
    """
    Y span of the 90's trunk-facing radial edge (thickness = w).
    Cachuma: θ=-π/2 spoke → Y in [cy - R_outer, cy - R_inner]
    GW:      θ=+π/2 spoke → Y in [cy + R_inner, cy + R_outer]
    """
    r_inner = R_INNER
    r_outer = r_inner + w
    if chain["key"] == "c":
        return (center.y - r_outer, center.y - r_inner)
    return (center.y + r_inner, center.y + r_outer)


def update_connects(layout: dict) -> list[str]:
    """
    Rebuild CachumaConnect / GroundWaterConnect as horizontal runouts:
      +X end barely overlaps the 90's trunk-facing edge (X ≈ ★.x)
      −X end overlaps CarpSupplyIn
      Y span = side width w, aligned to that 90 edge
    SWP needs no Connect (it is on-axis into the trunk).
    """
    trunk_obj = bpy.data.objects.get("CarpSupplyIn")
    if not trunk_obj or trunk_obj.type != "MESH":
        return ["Connect: CarpSupplyIn missing"]

    trunk = mesh_sankey_bbox(trunk_obj)
    notes = []
    for chain in SIDE_CHAINS:
        cname = chain.get("connect_name")
        if not cname:
            continue
        if not lane_active(layout, chain["key"]):
            notes.append(f"{cname}: off")
            continue
        bend = bpy.data.objects.get(chain["bend_name"])
        if not bend:
            notes.append(f"{cname}: no bend empty")
            continue

        w = side_width(chain, layout)
        center = world_to_sankey(bend.matrix_world.translation)
        center = Vector((center.x, center.y, BEND_Z))
        y0, y1 = connect_band_y(chain, center, w)

        # 90 trunk outlet is the vertical spoke at X = ★.x (+X into annulus body)
        x_east = center.x + CONNECT_OVERLAP_90  # barely into the 90
        x_west = trunk["xmax"] - CONNECT_OVERLAP_TRUNK  # barely into the trunk
        # Safety: never invert / collapse if tip math is odd
        if x_west > x_east - CONNECT_MIN_LEN:
            x_west = x_east - CONNECT_MIN_LEN
        xmin, xmax = x_west, x_east

        obj = _ensure_mesh_object(cname)
        rebuild_sankey_rect(obj, xmin, xmax, y0, y1, BEND_Z)
        notes.append(
            f"{cname}: X[{xmin:.0f},{xmax:.0f}] Y[{y0:.0f},{y1:.0f}] w={w:.0f}"
        )
    return notes


# ---------------------------------------------------------------------------
# In tip stretch → meet 90
# ---------------------------------------------------------------------------

def stretch_in_tip_to_y(obj, tip_key: str, target_tip_y: float, length_axis: str = "y") -> str:
    _depsgraph_update()
    bb0 = mesh_sankey_bbox(obj)
    source_key = "ymax" if tip_key == "ymin" else "ymin"
    source_y = bb0[source_key]
    tip_y = bb0[tip_key]
    old_len = abs(source_y - tip_y)
    new_len = abs(source_y - target_tip_y)

    if old_len < 1e-3:
        return f"{obj.name}: skip (zero length)"
    if abs(tip_y - target_tip_y) <= TIP_EPS:
        return f"{obj.name}: tip ok ({tip_y:.0f}≈{target_tip_y:.0f})"

    factor = new_len / old_len
    sx, sy, sz = obj.scale
    if length_axis == "x":
        obj.scale = (sx * factor, sy, sz)
    elif length_axis == "y":
        obj.scale = (sx, sy * factor, sz)
    else:
        obj.scale = (sx, sy, sz * factor)

    _depsgraph_update()
    bb1 = mesh_sankey_bbox(obj)
    dy = source_y - bb1[source_key]
    translate_object_sankey(obj, Vector((0.0, dy, 0.0)))
    _depsgraph_update()
    bb2 = mesh_sankey_bbox(obj)
    return (
        f"{obj.name}: tip {tip_y:.0f}→{bb2[tip_key]:.0f} "
        f"(target {target_tip_y:.0f})"
    )


def ninety_meeting_y(chain: dict, ninety_obj) -> float:
    """
    Y of the In↔90 seal edge.
    Annulus θ=0 spoke lies on Y = ★.y; that is the meeting line.
    Use 90 bbox edge facing the In as the stretch target.
    """
    b9 = mesh_sankey_bbox(ninety_obj)
    # Cachuma In is above 90 → meet at 90.ymax; GW In below → meet at 90.ymin
    return b9["ymax"] if chain["tip_key"] == "ymin" else b9["ymin"]


def stretch_side_ins_to_nineties(layout: dict) -> list[str]:
    notes = []
    for chain in SIDE_CHAINS:
        if not lane_active(layout, chain["key"]):
            notes.append(f"{chain['in_name']}: off")
            continue
        inn = bpy.data.objects.get(chain["in_name"])
        ninety = bpy.data.objects.get(chain["ninety_name"])
        if not inn or inn.type != "MESH" or not ninety or ninety.type != "MESH":
            continue
        target_y = ninety_meeting_y(chain, ninety)
        notes.append(
            stretch_in_tip_to_y(
                inn,
                chain["tip_key"],
                target_y,
                chain["length_axis"],
            )
        )
    return notes


def report_seals(layout: dict) -> list[str]:
    """Assert-style notes: In↔90, Connect↔90, Connect↔trunk, 90 clears trunk X."""
    notes = []
    H = layout["H"]
    trunk_obj = bpy.data.objects.get("CarpSupplyIn")
    trunk = mesh_sankey_bbox(trunk_obj) if trunk_obj and trunk_obj.type == "MESH" else None

    notes.append(
        f"share C/S/G="
        f"{layout.get('pct_c', 0):.1f}/{layout.get('pct_s', 0):.1f}/"
        f"{layout.get('pct_g', 0):.1f}% "
        f"mode={layout.get('mode_c')}/{layout.get('mode_s')}/{layout.get('mode_g')}"
    )

    if trunk and lane_active(layout, "s"):
        swp = bpy.data.objects.get("SWPIn")
        if swp and swp.type == "MESH" and not swp.hide_viewport:
            sw = mesh_sankey_bbox(swp)
            tip_gap = trunk["xmax"] - sw["xmin"]  # expect ~OVERLAP_TRUNK > 0
            tip_ok = "OK" if 0.0 < tip_gap <= CONNECT_OVERLAP_TRUNK + SEAL_EPS else "GAP"
            notes.append(
                f"SWP↔trunk {tip_ok}: x_ov={tip_gap:.0f} "
                f"(trunk.xmax={trunk['xmax']:.0f})"
            )

    for chain in SIDE_CHAINS:
        if not lane_active(layout, chain["key"]):
            notes.append(f"{chain['ninety_name']}: off")
            continue
        inn = bpy.data.objects.get(chain["in_name"])
        ninety = bpy.data.objects.get(chain["ninety_name"])
        if not inn or not ninety or ninety.type != "MESH":
            notes.append(f"{chain['ninety_name']}: missing")
            continue
        bi = mesh_sankey_bbox(inn)
        b9 = mesh_sankey_bbox(ninety)
        if chain["tip_key"] == "ymin":
            gap = bi["ymin"] - b9["ymax"]
        else:
            gap = b9["ymin"] - bi["ymax"]
        ok = "OK" if abs(gap) <= SEAL_EPS else "GAP"
        notes.append(
            f"{chain['ninety_name']}↔In {ok}: gap={gap:.0f} "
            f"Zspan={b9['zmax']-b9['zmin']:.0f}"
        )
        if chain["key"] == "c":
            into = H - b9["ymin"]
            notes.append(
                f"{chain['ninety_name']} trunkY: ymin={b9['ymin']:.0f} "
                f"(outer=+{H:.0f}, into={into:.0f})"
            )
        else:
            into = b9["ymax"] - (-H)
            notes.append(
                f"{chain['ninety_name']} trunkY: ymax={b9['ymax']:.0f} "
                f"(outer=-{H:.0f}, into={into:.0f})"
            )

        if trunk:
            # 90 west edge should sit east of trunk tip (clearance)
            clear = b9["xmin"] - trunk["xmax"]
            clear_ok = "OK" if clear >= -5.0 else "OVERLAP"
            notes.append(
                f"{chain['ninety_name']}↔trunkX {clear_ok}: "
                f"clear={clear:.0f} (want ≥0)"
            )

        cname = chain.get("connect_name")
        conn = bpy.data.objects.get(cname) if cname else None
        if not conn or conn.type != "MESH":
            notes.append(f"{cname or 'Connect'}: missing")
            continue
        bc = mesh_sankey_bbox(conn)
        # 90 outlet at ★.x: Conn should overlap 90 near that X
        x_meet = b9["xmin"] - bc["xmax"]  # expect slightly negative (overlap)
        y_ov = max(0.0, min(b9["ymax"], bc["ymax"]) - max(b9["ymin"], bc["ymin"]))
        w = side_width(chain, layout)
        meet_ok = "OK" if x_meet <= 5.0 and y_ov >= 0.5 * w else "GAP"
        notes.append(
            f"{cname}↔90 {meet_ok}: x_meet={x_meet:.0f} y_ov={y_ov:.0f} "
            f"(w={w:.0f})"
        )
        if trunk:
            x_ov_t = max(
                0.0,
                min(bc["xmax"], trunk["xmax"]) - max(bc["xmin"], trunk["xmin"]),
            )
            y_ov_t = max(
                0.0,
                min(bc["ymax"], trunk["ymax"]) - max(bc["ymin"], trunk["ymin"]),
            )
            trunk_ok = "OK" if x_ov_t > 0.0 and y_ov_t >= 0.5 * w else "GAP"
            notes.append(
                f"{cname}↔trunk {trunk_ok}: x_ov={x_ov_t:.0f} y_ov={y_ov_t:.0f}"
            )
    return notes


# ---------------------------------------------------------------------------
# Year labels (FONT body text)
# ---------------------------------------------------------------------------

def format_label_af(af: float) -> str:
    """Integer AF for 3D text (no thousands separators — cleaner at GIS scale)."""
    return f"{int(round(float(af)))}"


def format_label_pct(pct: float) -> str:
    """Share percent 0–100 → compact X% string."""
    p = float(pct)
    if abs(p - round(p)) < 0.05:
        return f"{int(round(p))}%"
    return f"{p:.1f}%"


def lane_label_body(title: str, af: float, pct: float) -> str:
    """Single-line lane labels (Cachuma / SWP / GW) — matches hand-tuned CAPP figure."""
    return f"{title} {format_label_af(af)} AF, {format_label_pct(pct)}"


def total_label_body(af_total: float) -> str:
    # Live CarpSupplyLabel: leading space on " Total ", two-line title.
    return f" Total \nWater Supply \n{format_label_af(af_total)} AF"


def ocean_label_body(af_ocean: float, unknown: bool = False) -> str:
    # Live OceanOutput: "Ocean Outfall \n…" (trailing space after Outfall).
    # Currently unused by update_year_labels — residual outfall AF is uncertain,
    # so the scene keeps a hand-tuned range placeholder (e.g. "200-400 AF").
    # Re-enable when a scenario-specific residual AF is locked.
    if unknown:
        return "Ocean Outfall \nUNKNOWN AF"
    return f"Ocean Outfall \n{format_label_af(af_ocean)} AF"


def capp_label_body(af_r: float) -> str:
    """CAPPLabel pair — groundwater replenishment / CAPP injection AF."""
    return f"Groundwater Replenishment\n{format_label_af(af_r)} AF"


def diverted_outfall_label_body(af_reuse: float) -> str:
    """DivertedOutfall pair — effluent diverted from ocean outfall to CAPP."""
    return f"Diverted From\nOutfall: {format_label_af(af_reuse)} AF"


def update_year_labels(layout: dict) -> list[str]:
    """
    Push AF / share% into label pairs (inner + outer).
    Updates FONT body only — does not change size, extrude, or scale
    (hand-tuned label sizing is preserved across scenarios).
    Supply lane + total labels always stay visible (0 AF, 0% when empty).
    Ocean / CAPP / diverted-outfall labels follow their limb visibility.
    Ocean Outfall body text is left alone (range placeholder until AF known).
    """
    notes = []
    for spec in LABEL_PAIRS:
        show = True
        body: str | None = None
        preserve_body = False
        if spec["kind"] == "lane":
            af = float(layout.get(spec["af_key"], 0.0))
            pct = float(layout.get(spec["pct_key"], 0.0))
            body = lane_label_body(spec["title"], af, pct)
        elif spec["kind"] == "total":
            af = float(layout.get("af_total", 0.0))
            body = total_label_body(af)
        elif spec["kind"] == "ocean":
            # Visibility only — do not overwrite hand-tuned range (e.g. 200-400 AF).
            show = (
                ocean_active(layout)
                or float(layout.get("w_ocean_reuse", 0.0)) > 0.0
            )
            preserve_body = True
            # body = ocean_label_body(
            #     float(layout.get("af_ocean") or 0.0),
            #     unknown=ocean_unknown(layout),
            # )
        elif spec["kind"] == "capp":
            show = capp_active(layout)
            body = capp_label_body(float(layout.get("af_r") or 0.0))
        elif spec["kind"] == "diverted_outfall":
            show = float(layout.get("w_ocean_reuse", 0.0)) > 0.0
            body = diverted_outfall_label_body(
                float(layout.get("af_ocean_reuse") or 0.0)
            )
        else:
            notes.append(f"unknown label kind {spec.get('kind')}")
            continue

        hit = []
        for name in spec["names"]:
            obj = bpy.data.objects.get(name)
            if not obj or obj.type != "FONT":
                notes.append(f"{name}: missing")
                continue
            if not preserve_body and body is not None and obj.data.body != body:
                obj.data.body = body
            obj.hide_viewport = not show
            obj.hide_render = not show
            hit.append(name)
        if hit:
            if show:
                if preserve_body:
                    sample = bpy.data.objects.get(spec["names"][0])
                    preview = (
                        sample.data.body.replace("\n", " | ")
                        if sample and sample.type == "FONT"
                        else "(preserved)"
                    )
                    notes.append(f"{'+'.join(hit)} → {preview} [AF text preserved]")
                else:
                    preview = body.replace("\n", " | ")
                    notes.append(f"{'+'.join(hit)} → {preview}")
            else:
                notes.append(f"{'+'.join(hit)}: hidden")
    return notes


# ---------------------------------------------------------------------------
# Apply pipeline
# ---------------------------------------------------------------------------

def _write_scene_props(row: dict, layout: dict) -> None:
    sc = bpy.context.scene
    sc["sankey_label"] = row["label"]
    if row.get("year") is not None:
        sc["selected_year"] = int(row["year"])
    sc["sankey_cachuma_af"] = row["cachuma_af"]
    sc["sankey_swp_af"] = row["swp_af"]
    sc["sankey_gw_af"] = row["groundwater_af"]
    ocean = row.get("ocean_outfall_af")
    sc["sankey_ocean_outfall_af"] = float(ocean) if ocean is not None else -1.0
    sc["sankey_ocean_unknown"] = 1 if layout.get("ocean_unknown") else 0
    for k, v in layout.items():
        if v is None:
            continue
        sc[f"sankey_{k}"] = v
    sf = bpy.data.objects.get("SankeyFrame")
    if sf:
        for k, v in layout.items():
            if v is None:
                continue
            sf[k] = v
        sf["sankey_label"] = row["label"]
        sf["bend_margin_x"] = BEND_MARGIN_X
        sf["bend_margin_y"] = BEND_MARGIN_Y
        sf["r_inner"] = R_INNER
        sf["ocean_unknown"] = bool(layout.get("ocean_unknown"))
        if layout.get("af_ocean") is not None:
            sf["af_ocean"] = float(layout["af_ocean"])
        elif "af_ocean" in sf:
            del sf["af_ocean"]


# ---------------------------------------------------------------------------
# CAPP lane (★.y tracks trunk / Facility / OutDown / Arrow)
# ---------------------------------------------------------------------------

def capp_bend_xy() -> Vector:
    """CAPPBendCenter XY in Sankey space (Z taken from CAPP deck)."""
    bend = bpy.data.objects.get("CAPPBendCenter")
    if not bend:
        raise RuntimeError("CAPPBendCenter empty not found")
    p = world_to_sankey(bend.matrix_world.translation)
    return Vector((p.x, p.y, p.z))


def capp_ribbon_z(name: str, fallback: float = 380.0) -> float:
    """
    Sankey Z for a CAPP ribbon — preserve hand-tuned height across rebuilds.
    Falls back through other CAPP meshes, then fallback.
    """
    obj = bpy.data.objects.get(name)
    if obj and obj.type == "MESH" and obj.data.vertices:
        return mesh_sankey_bbox(obj)["zmid"]
    for alt in ("CAPPIn", "CAPP90", "CAPPConnect", "CAPPOut"):
        if alt == name:
            continue
        other = bpy.data.objects.get(alt)
        if other and other.type == "MESH" and other.data.vertices:
            return mesh_sankey_bbox(other)["zmid"]
    return fallback


def capp_deck_z() -> float:
    """In→90→Connect deck Z (from CAPPIn when present)."""
    return capp_ribbon_z("CAPPIn")


def capp_center_sankey() -> Vector:
    """★ XY + CAPP90's preserved Z (90 may match deck or be nudged)."""
    xy = capp_bend_xy()
    return Vector((xy.x, xy.y, capp_ribbon_z("CAPP90")))


def place_capp_bend_for_in(layout: dict) -> str:
    """
    Move CAPPBendCenter so CAPP90's +Y spoke seals to CAPPIn under the trunk.

      ★.x  kept (Connect / Facility column alignment)
      ★.y  = CarpSupplyIn.ymin − R_inner
           → spoke Y [★.y+R_inner, ★.y+R_outer] = [trunk.ymin, trunk.ymin+w]
      ★.z  kept (empty height; mesh Z comes from each ribbon)
    """
    if not capp_active(layout):
        return "CAPPBendCenter: off"
    bend = bpy.data.objects.get("CAPPBendCenter")
    trunk = bpy.data.objects.get("CarpSupplyIn")
    if not bend:
        return "CAPPBendCenter: missing"
    if not trunk or trunk.type != "MESH":
        return "CAPPBendCenter: CarpSupplyIn missing"
    _depsgraph_update()
    tb = mesh_sankey_bbox(trunk)
    cur = world_to_sankey(bend.matrix_world.translation)
    y_star = tb["ymin"] - CAPP_R_INNER
    target = Vector((cur.x, y_star, cur.z))
    set_object_sankey_origin(bend, target)
    _depsgraph_update()
    p = world_to_sankey(bend.matrix_world.translation)
    return (
        f"CAPPBendCenter: XY→({p.x:.0f},{p.y:.0f}) "
        f"(Y from trunk.ymin={tb['ymin']:.0f}−R)"
    )


def _clear_wireframe_mods(obj: bpy.types.Object) -> None:
    for m in list(obj.modifiers):
        if m.type == "WIREFRAME":
            obj.modifiers.remove(m)


def rebuild_sankey_border_rect(
    obj: bpy.types.Object,
    xmin: float,
    xmax: float,
    ymin: float,
    ymax: float,
    z: float,
    rim_xy: float = CAPP_BORDER_RIM_XY,
    z_thick: float = CAPP_BORDER_Z,
) -> None:
    """
    Flat rectangular border (outer frame, inner hole) in Sankey XY.
    Rim width is in XY; Z thickness is a thin slab (default 1) — not Wireframe.
    """
    if xmax < xmin:
        xmin, xmax = xmax, xmin
    if ymax < ymin:
        ymin, ymax = ymax, ymin
    rim = max(1.0, float(rim_xy))
    # Keep a usable hole
    if (xmax - xmin) <= 2.0 * rim + 1.0:
        rim = max(1.0, 0.25 * (xmax - xmin))
    if (ymax - ymin) <= 2.0 * rim + 1.0:
        rim = min(rim, max(1.0, 0.25 * (ymax - ymin)))

    ix0, ix1 = xmin + rim, xmax - rim
    iy0, iy1 = ymin + rim, ymax - rim
    z0, z1 = z - 0.5 * z_thick, z + 0.5 * z_thick

    mid = Vector((0.5 * (xmin + xmax), 0.5 * (ymin + ymax), z))
    obj.scale = (1.0, 1.0, 1.0)
    obj.rotation_euler = (0.0, 0.0, 0.0)
    _clear_wireframe_mods(obj)
    set_object_sankey_origin(obj, mid)
    _depsgraph_update()

    mw_inv = obj.matrix_world.inverted()

    def L(sx, sy, sz):
        return mw_inv @ sankey_to_world(Vector((sx, sy, sz)))

    # Outer ring corners + inner hole corners, bottom and top
    outer_b = [L(xmin, ymin, z0), L(xmax, ymin, z0), L(xmax, ymax, z0), L(xmin, ymax, z0)]
    inner_b = [L(ix0, iy0, z0), L(ix1, iy0, z0), L(ix1, iy1, z0), L(ix0, iy1, z0)]
    outer_t = [L(xmin, ymin, z1), L(xmax, ymin, z1), L(xmax, ymax, z1), L(xmin, ymax, z1)]
    inner_t = [L(ix0, iy0, z1), L(ix1, iy0, z1), L(ix1, iy1, z1), L(ix0, iy1, z1)]

    bm = bmesh.new()
    ob = [bm.verts.new(p) for p in outer_b]
    ib = [bm.verts.new(p) for p in inner_b]
    ot = [bm.verts.new(p) for p in outer_t]
    it = [bm.verts.new(p) for p in inner_t]
    bm.verts.ensure_lookup_table()

    # Bottom annulus (outer CCW, inner CW for hole)
    bm.faces.new((ob[0], ob[1], ib[1], ib[0]))
    bm.faces.new((ob[1], ob[2], ib[2], ib[1]))
    bm.faces.new((ob[2], ob[3], ib[3], ib[2]))
    bm.faces.new((ob[3], ob[0], ib[0], ib[3]))
    # Top annulus
    bm.faces.new((ot[0], it[0], it[1], ot[1]))
    bm.faces.new((ot[1], it[1], it[2], ot[2]))
    bm.faces.new((ot[2], it[2], it[3], ot[3]))
    bm.faces.new((ot[3], it[3], it[0], ot[0]))
    # Outer walls
    for i in range(4):
        j = (i + 1) % 4
        bm.faces.new((ob[i], ot[i], ot[j], ob[j]))
    # Inner walls
    for i in range(4):
        j = (i + 1) % 4
        bm.faces.new((ib[i], ib[j], it[j], it[i]))

    mesh = obj.data
    bm.to_mesh(mesh)
    bm.free()
    mesh.update()
    _depsgraph_update()


def update_capp_ninety(layout: dict) -> str:
    """Rebuild CAPP90 about ★ (★.y already placed to seal CAPPIn)."""
    if not capp_active(layout):
        return "CAPP90: off"
    w = float(layout["w_r"])
    center = capp_center_sankey()
    obj = _ensure_mesh_object("CAPP90")
    rebuild_ninety_annulus(
        obj,
        center,
        CAPP_R_INNER,
        CAPP_R_INNER + w,
        CAPP_THETA0,
        CAPP_THETA1,
    )
    return (
        f"CAPP90: R={CAPP_R_INNER:.0f}..{CAPP_R_INNER + w:.0f} "
        f"at ({center.x:.0f},{center.y:.0f},{center.z:.0f})"
    )


def capp_in_band_y(center: Vector, w: float) -> tuple[float, float]:
    """Y span of the +Y spoke (θ=π/2) — CAPPIn meets this vertical edge."""
    r_inner = CAPP_R_INNER
    r_outer = r_inner + w
    return (center.y + r_inner, center.y + r_outer)


def capp_out_band_x(center: Vector, w: float) -> tuple[float, float]:
    """X span of the +X spoke (θ=0) — Out column sits in this band."""
    r_inner = CAPP_R_INNER
    r_outer = r_inner + w
    return (center.x + r_inner, center.x + r_outer)


def update_capp_in(layout: dict) -> str:
    """
    Horizontal CAPPIn under the trunk's −Y outer edge in XY:

      Y = CAPP90 +Y spoke (seals to ★ after place_capp_bend_for_in)
      X: from CarpSupplyDownArrow → barely into CAPP90 at ★.x
      Z: preserved (hand-tuned deck height)
    """
    if not capp_active(layout):
        return "CAPPIn: off"
    obj = _ensure_mesh_object("CAPPIn")
    center = capp_center_sankey()
    w = float(layout["w_r"])
    y0, y1 = capp_in_band_y(center, w)
    z = capp_ribbon_z("CAPPIn")

    trunk = bpy.data.objects.get("CarpSupplyIn")
    darr = bpy.data.objects.get("CarpSupplyDownArrow")
    if darr and darr.type == "MESH":
        x_west = mesh_sankey_bbox(darr)["xmid"]
    elif trunk and trunk.type == "MESH":
        x_west = mesh_sankey_bbox(trunk)["xmin"]
    else:
        x_west = center.x - CONNECT_MIN_LEN
    x_east = center.x + CONNECT_OVERLAP_90
    if x_east - x_west < CONNECT_MIN_LEN:
        x_west = x_east - CONNECT_MIN_LEN

    rebuild_sankey_rect(obj, x_west, x_east, y0, y1, z)
    return (
        f"CAPPIn: X[{x_west:.0f},{x_east:.0f}] Y[{y0:.0f},{y1:.0f}] "
        f"w={w:.0f} z={z:.0f} seals +Y spoke @ ★.x={center.x:.0f}"
    )


def update_capp_connect(layout: dict) -> str:
    """
    Bridge 90's +X exit down toward fixed Facility (slight overlaps).
    X band = θ=0 radial span; Y from Facility top up into the +X spoke.
    Length grows when ★ moves north to meet CAPPIn under the trunk.
    Z preserved across scenarios.
    """
    if not capp_active(layout):
        return "CAPPConnect: off"
    fac = bpy.data.objects.get("CAPPFacility")
    if not fac:
        return "CAPPConnect: no Facility"
    obj = _ensure_mesh_object("CAPPConnect")
    center = capp_center_sankey()
    w = float(layout["w_r"])
    x0, x1 = capp_out_band_x(center, w)
    z = capp_ribbon_z("CAPPConnect")
    fb = mesh_sankey_bbox(fac)
    # θ=0 spoke sits at Y=★.y (south edge of the 90). Reach NORTH into the
    # annulus so Connect overlaps the 90; south end overlaps Facility top.
    y_north = center.y + CONNECT_OVERLAP_90
    y_south = fb["ymax"] - CONNECT_OVERLAP_90
    if y_north < y_south + CONNECT_MIN_LEN:
        y_north = y_south + CONNECT_MIN_LEN
    rebuild_sankey_rect(obj, x0, x1, y_south, y_north, z)
    return (
        f"CAPPConnect: X[{x0:.0f},{x1:.0f}] Y[{y_south:.0f},{y_north:.0f}] "
        f"w={w:.0f} z={z:.0f} len={y_north - y_south:.0f}"
    )


def update_capp_out(layout: dict) -> str:
    """
    CAPPOut between fixed Facility and fixed OutDown.
    X centered on OutDown; width = w_r; length seals with slight overlap.
    Z preserved (may sit above the In→Connect deck).
    Does not move OutDown / Arrow / Facility.
    """
    if not capp_active(layout):
        return "CAPPOut: off"
    out = _ensure_mesh_object("CAPPOut")
    fac = bpy.data.objects.get("CAPPFacility")
    down = bpy.data.objects.get("CAPPOutDown")
    if not fac or not down:
        return "CAPPOut: missing Facility/OutDown"
    w = float(layout["w_r"])
    z = capp_ribbon_z("CAPPOut")
    fb = mesh_sankey_bbox(fac)
    db = mesh_sankey_bbox(down)
    xmid = db["xmid"]
    x0, x1 = xmid - w / 2.0, xmid + w / 2.0
    y_north = fb["ymin"] + CONNECT_OVERLAP_90
    y_south = db["ymax"] - CONNECT_OVERLAP_90
    if y_north < y_south + CONNECT_MIN_LEN:
        y_north = y_south + CONNECT_MIN_LEN
    rebuild_sankey_rect(out, x0, x1, y_south, y_north, z)
    return (
        f"CAPPOut: X[{x0:.0f},{x1:.0f}] Y[{y_south:.0f},{y_north:.0f}] "
        f"xmid→{xmid:.0f} z={z:.0f} (OutDown fixed)"
    )


def update_capp_lane(layout: dict) -> list[str]:
    """★.y → CAPP90 → CAPPIn → Connect → Out (continuous seal)."""
    notes = [
        place_capp_bend_for_in(layout),
        update_capp_ninety(layout),
        update_capp_in(layout),
        update_capp_connect(layout),
        update_capp_out(layout),
    ]
    return notes


# ---------------------------------------------------------------------------
# OceanOUT + OceanREUSE pair (centered on trunk)
# ---------------------------------------------------------------------------

def _ocean_x_span() -> tuple[float, float]:
    """Keep ocean ribbons' existing X reach (oceanward limb)."""
    ref = bpy.data.objects.get("OceanOUT") or bpy.data.objects.get("OceanREUSE")
    if not ref or ref.type != "MESH":
        return (-4800.0, -1600.0)
    bb = mesh_sankey_bbox(ref)
    return bb["xmin"], bb["xmax"]


def _place_ocean_out_arrow(target_ymid: float, target_width: float) -> str:
    """OceanOutArrow: same width as OceanOUT, centered on its Y; tip X unchanged."""
    arrow = bpy.data.objects.get("OceanOutArrow")
    if not arrow or arrow.type != "MESH":
        return "OceanOutArrow: missing"
    if target_width <= 0.0:
        return "OceanOutArrow: off"
    _set_mesh_width_scale(arrow, "y", target_width)
    _depsgraph_update()
    ab = mesh_sankey_bbox(arrow)
    dy = target_ymid - ab["ymid"]
    if abs(dy) > 1.0:
        translate_object_sankey(arrow, Vector((0.0, dy, 0.0)))
        _depsgraph_update()
    ab2 = mesh_sankey_bbox(arrow)
    return f"OceanOutArrow: w={target_width:.0f} ymid→{ab2['ymid']:.0f}"


def _place_ocean_reuse_arrow(target_ymid: float, target_width: float) -> str:
    """
    OceanREUSEArrow (points inland): slightly inset inside OceanREUSE.
    Uniform XY scale hits Sankey dy (arrow local axes ≠ OceanOutArrow);
    restore X after scale; Y-center on reuse; tuck inside OceanREUSE if needed.
    Strip Wireframe if present.
    """
    arrow = bpy.data.objects.get(OCEAN_REUSE_ARROW_NAME)
    if not arrow or arrow.type != "MESH":
        return f"{OCEAN_REUSE_ARROW_NAME}: missing"
    if target_width <= 0.0:
        return f"{OCEAN_REUSE_ARROW_NAME}: off"

    _clear_wireframe_mods(arrow)
    inset_w = max(W_VIS_MIN, target_width * OCEAN_REUSE_ARROW_WIDTH_FACTOR)

    _depsgraph_update()
    bb0 = mesh_sankey_bbox(arrow)
    dy0 = bb0["ymax"] - bb0["ymin"]
    if dy0 < 1e-3:
        return f"{OCEAN_REUSE_ARROW_NAME}: skip (zero dy)"

    x_keep = bb0["xmin"]
    s = inset_w / dy0
    sx, sy, sz = arrow.scale
    arrow.scale = (sx * s, sy * s, sz)
    _depsgraph_update()
    bb1 = mesh_sankey_bbox(arrow)
    dx = x_keep - bb1["xmin"]
    dy = target_ymid - bb1["ymid"]
    if abs(dx) > 1.0 or abs(dy) > 1.0:
        translate_object_sankey(arrow, Vector((dx, dy, 0.0)))
        _depsgraph_update()

    # Keep arrow inside OceanREUSE XY (tips were sticking out oceanward).
    reuse = bpy.data.objects.get("OceanREUSE")
    if reuse and reuse.type == "MESH" and reuse.data.vertices:
        rb = mesh_sankey_bbox(reuse)
        ab = mesh_sankey_bbox(arrow)
        pad = max(5.0, 0.04 * (rb["ymax"] - rb["ymin"]))
        shift_x = 0.0
        if ab["xmin"] < rb["xmin"] + pad:
            shift_x = (rb["xmin"] + pad) - ab["xmin"]
        elif ab["xmax"] > rb["xmax"] - pad:
            shift_x = (rb["xmax"] - pad) - ab["xmax"]
        if abs(shift_x) > 1.0:
            translate_object_sankey(arrow, Vector((shift_x, 0.0, 0.0)))
            _depsgraph_update()

    bb2 = mesh_sankey_bbox(arrow)
    return (
        f"{OCEAN_REUSE_ARROW_NAME}: w={bb2['ymax']-bb2['ymin']:.0f} "
        f"(factor={OCEAN_REUSE_ARROW_WIDTH_FACTOR}) ymid→{bb2['ymid']:.0f}"
    )


def update_ocean_pair(layout: dict) -> list[str]:
    """
    Stack OceanREUSE (−Y) + OceanOUT (+Y); center combined band on trunk ymid.
    OceanREUSE = flat XY border slab (Z=1), not Wireframe tubes.
    OceanOutArrow matches OceanOUT; OceanREUSEArrow slightly inset in OceanREUSE.
    """
    notes = []
    w_r = float(layout.get("w_ocean_reuse", layout.get("w_r", 0.0)))
    w_out = float(layout.get("w_ocean", 0.0))
    xmin, xmax = _ocean_x_span()

    z_out = -655.6
    ocean = bpy.data.objects.get("OceanOUT")
    if ocean and ocean.type == "MESH" and ocean.data.vertices:
        z_out = mesh_sankey_bbox(ocean)["zmid"]
    z_reuse = z_out
    reuse = bpy.data.objects.get("OceanREUSE")
    if reuse and reuse.type == "MESH" and reuse.data.vertices:
        z_reuse = mesh_sankey_bbox(reuse)["zmid"]

    # Combined OUT+REUSE band follows the main trunk centerline (not hardcoded 0).
    y_center = 0.0
    trunk = bpy.data.objects.get("CarpSupplyIn")
    if trunk and trunk.type == "MESH" and trunk.data.vertices:
        y_center = mesh_sankey_bbox(trunk)["ymid"]

    total = max(0.0, w_r) + max(0.0, w_out)
    y_lo = y_center - 0.5 * total
    y_mid_split = y_lo + max(0.0, w_r)
    y_hi = y_center + 0.5 * total

    reuse_ymid = 0.5 * (y_lo + y_mid_split)
    out_ymid = 0.5 * (y_mid_split + y_hi)

    if w_r > 0.0:
        obj = _ensure_mesh_object("OceanREUSE")
        rebuild_sankey_border_rect(
            obj, xmin, xmax, y_lo, y_mid_split, z_reuse,
            rim_xy=CAPP_BORDER_RIM_XY,
            z_thick=CAPP_BORDER_Z,
        )
        notes.append(
            f"OceanREUSE: Y[{y_lo:.0f},{y_mid_split:.0f}] w={w_r:.0f} "
            f"border_xy={CAPP_BORDER_RIM_XY:.0f} z={CAPP_BORDER_Z:.0f}"
        )
        notes.append(_place_ocean_reuse_arrow(reuse_ymid, w_r))
    else:
        notes.append("OceanREUSE: off")
        notes.append(_place_ocean_reuse_arrow(0.0, 0.0))

    if ocean_active(layout) and w_out > 0.0:
        obj = _ensure_mesh_object("OceanOUT")
        rebuild_sankey_rect(obj, xmin, xmax, y_mid_split, y_hi, z_out)
        notes.append(f"OceanOUT: Y[{y_mid_split:.0f},{y_hi:.0f}] w={w_out:.0f}")
        notes.append(_place_ocean_out_arrow(out_ymid, w_out))
    else:
        notes.append("OceanOUT: off")
        notes.append(_place_ocean_out_arrow(0.0, 0.0))

    notes.append(f"oceanPair center→trunk.ymid={y_center:.0f}")
    return notes


def apply_layout(row: dict, layout: dict) -> dict:
    """
    Full chain update (Historical trunk + CAPP lane + ocean pair):
      attach ocean/CAPP AF → visibility → trunk widths/90s/connects →
      CAPP lane (★ seals to CAPPIn) → ocean OUT+REUSE pair → labels
    """
    attach_ocean_outfall(layout, row.get("ocean_outfall_af"))
    attach_capp_injection(layout, row.get("recycled_af"))
    _write_scene_props(row, layout)

    vis_notes = set_lane_visibility(layout)
    capp_vis = set_capp_visibility(layout)
    ocean_note = set_ocean_visibility(layout)
    width_objs = apply_widths(layout)
    swp_note = place_swp_on_lane(layout)
    place_bend_centers(layout)
    update_nineties(layout)
    stretch_side_ins_to_nineties(layout)

    bend_notes = place_bend_centers(layout)
    ninety_notes = update_nineties(layout)
    stretch_notes = stretch_side_ins_to_nineties(layout)

    trunk_note = shrink_trunk_from_nineties(layout)
    connect_notes = update_connects(layout)
    swp_tip_note = extend_swp_to_trunk(layout)
    swp_note_2 = place_swp_on_lane(layout)

    down_notes = []
    for chain in SIDE_CHAINS:
        n = align_down_to_in(chain, layout)
        if n:
            down_notes.append(n)

    capp_notes = update_capp_lane(layout)
    ocean_pair_notes = update_ocean_pair(layout)

    label_notes = update_year_labels(layout)
    seal_notes = report_seals(layout)

    ocean_af = layout.get("af_ocean")
    if ocean_unknown(layout):
        ocean_txt = f"UNKNOWN(~{ocean_af:.0f})"
    elif ocean_af is None:
        ocean_txt = "—"
    else:
        ocean_txt = f"{ocean_af:.0f}"
    msg = (
        f"{row['label']}: AF c/s/g/r="
        f"{layout.get('af_c', 0):.0f}/{layout.get('af_s', 0):.0f}/"
        f"{layout.get('af_g', 0):.0f}/{layout.get('af_r', 0):.0f} "
        f"ocean={ocean_txt} "
        f"pct={layout.get('pct_c', 0):.1f}/{layout.get('pct_s', 0):.1f}/"
        f"{layout.get('pct_g', 0):.1f}% "
        f"w_c/w_s/w_g/W/w_r="
        f"{layout['w_c']:.0f}/{layout['w_s']:.0f}/{layout['w_g']:.0f}/"
        f"{layout['W']:.0f}/{layout.get('w_r', 0):.0f} | "
        f"vis→{','.join(vis_notes)} | {capp_vis} | {ocean_note} | "
        f"widths→{', '.join(width_objs)} | {swp_note} | "
        f"bends→{', '.join(bend_notes)} | "
        f"90→{', '.join(ninety_notes)} | "
        f"ins→{', '.join(stretch_notes)} | "
        f"trunk→{trunk_note} | "
        f"conn→{', '.join(connect_notes)} | "
        f"swpTip→{swp_tip_note} / {swp_note_2} | "
        f"down→{', '.join(down_notes) or '—'} | "
        f"capp→{'; '.join(capp_notes)} | "
        f"oceanPair→{'; '.join(ocean_pair_notes)} | "
        f"labels→{'; '.join(label_notes) or '—'} | "
        f"seal→{'; '.join(seal_notes)}"
    )
    print("[capp_sankey_supply_layout]", msg)
    layout["report"] = msg
    return layout


def apply_row_from_af(row: dict) -> dict:
    """
    Build trunk widths from Cachuma / SWP / GW.
    Dry meet-demand rows: restore well production (Table GW + recycled).
    Normal availability row: keep Table GW as-is (~4,065 trunk, not 5,065).
    """
    af_r = float(row.get("recycled_af") or 0.0)
    af_g_table = float(row["groundwater_af"])
    af_g_wells = well_production_af(
        af_g_table,
        af_r,
        demand_af=row.get("demand_af"),
        supply_af=row.get("supply_af"),
    )
    layout = widths_from_af(row["cachuma_af"], row["swp_af"], af_g_wells)
    layout["af_g_table"] = af_g_table
    return apply_layout(row, layout)


def apply_lineup_1000() -> dict:
    row = get_lineup_row()
    layout = widths_forced_equal(LINEUP_WIDTH)
    return apply_layout(row, layout)


def apply_forced_widths(
    w_c: float,
    w_s: float,
    w_g: float,
    w_r: float = LINEUP_WIDTH,
    w_ocean: float | None = None,
    label: str = "Forced",
) -> dict:
    """Apply explicit ribbon widths (Blender units). 0 stays 0; else ≥ W_VIS_MIN."""
    w_c = floor_forced_width(w_c)
    w_s = floor_forced_width(w_s)
    w_g = floor_forced_width(w_g)
    w_r = floor_forced_width(w_r)
    if w_ocean is None:
        w_ocean = floor_forced_width(historical_ocean_outfall_mean_af() * K_AF_TO_WIDTH)
    else:
        w_ocean = floor_forced_width(w_ocean)
    af_c = w_c / K_AF_TO_WIDTH if w_c > 0.0 else 0.0
    af_s = w_s / K_AF_TO_WIDTH if w_s > 0.0 else 0.0
    af_g = w_g / K_AF_TO_WIDTH if w_g > 0.0 else 0.0
    af_r = w_r / K_AF_TO_WIDTH if w_r > 0.0 else 0.0
    # UI "Ocean outfall" width = TOTAL (OUT+REUSE), matching JSON ocean_outfall_af.
    af_ocean = w_ocean / K_AF_TO_WIDTH if w_ocean > 0.0 else 0.0
    total = af_c + af_s + af_g
    pct_c = (af_c / total) if total > 0 else 0.0
    pct_s = (af_s / total) if total > 0 else 0.0
    pct_g = (af_g / total) if total > 0 else 0.0
    mode_c = "zero" if w_c <= 0 else ("collapse" if w_c <= W_VIS_MIN + 1e-6 else "scaled")
    mode_s = "zero" if w_s <= 0 else ("collapse" if w_s <= W_VIS_MIN + 1e-6 else "scaled")
    mode_g = "zero" if w_g <= 0 else ("collapse" if w_g <= W_VIS_MIN + 1e-6 else "scaled")
    row = {
        "label": label,
        "year": 2030,
        "groundwater_af": af_g,
        "cachuma_af": af_c,
        "swp_af": af_s,
        "recycled_af": af_r,
        "demand_af": total,
        "ocean_outfall_af": af_ocean,
    }
    layout = attach_af_share(
        _lanes(w_c, w_s, w_g),
        af_c, af_s, af_g,
        mode_c, mode_s, mode_g,
        pct_c, pct_s, pct_g,
    )
    return apply_layout(row, layout)


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

class CAPP_OT_apply_lineup(Operator):
    bl_idname = "capp_carpwater.apply_lineup_1000"
    bl_label = "Apply Lineup Widths (1000)"
    bl_description = (
        "Equal trunk widths 1000 (W=3000) + CAPP/ocean reuse widths; "
        "rebuild side 90s; update pinned CAPP lane + ocean pair"
    )

    def execute(self, context):
        try:
            layout = apply_lineup_1000()
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self.report({"INFO"}, f"Lineup1000 W={layout['W']:.0f} w_r={layout.get('w_r', 0):.0f}")
        return {"FINISHED"}


class CAPP_OT_apply_widths(Operator):
    bl_idname = "capp_carpwater.apply_widths"
    bl_label = "Apply Widths"
    bl_description = (
        "Apply Cachuma / SWP / GW / CAPP / Ocean ribbon widths "
        "(Blender units). Rebuilds elbows and CAPP lane seals."
    )

    def execute(self, context):
        sc = context.scene
        try:
            layout = apply_forced_widths(
                sc.capp_width_cachuma,
                sc.capp_width_swp,
                sc.capp_width_groundwater,
                w_r=sc.capp_width_recycled,
                w_ocean=sc.capp_width_ocean,
                label=(
                    f"W{sc.capp_width_cachuma:.0f}_"
                    f"{sc.capp_width_swp:.0f}_"
                    f"{sc.capp_width_groundwater:.0f}_"
                    f"R{sc.capp_width_recycled:.0f}"
                ),
            )
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self.report(
            {"INFO"},
            f"Widths C/S/G/R "
            f"{sc.capp_width_cachuma:.0f}/"
            f"{sc.capp_width_swp:.0f}/"
            f"{sc.capp_width_groundwater:.0f}/"
            f"{sc.capp_width_recycled:.0f} "
            f"W={layout['W']:.0f}",
        )
        return {"FINISHED"}


class CAPP_OT_apply_scenario(Operator):
    bl_idname = "capp_carpwater.apply_scenario"
    bl_label = "Apply Scenario"
    bl_description = "Apply UWMP §5 planning scenario from JSON"

    def execute(self, context):
        sid = context.scene.capp_selected_scenario
        try:
            layout = apply_row_from_af(get_scenario_row(sid))
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self.report(
            {"INFO"},
            f"{sid} → W={layout['W']:.0f} w_r={layout.get('w_r', 0):.0f}",
        )
        return {"FINISHED"}


class CAPP_OT_apply_normal(Operator):
    bl_idname = "capp_carpwater.apply_normal"
    bl_label = "Normal (5-1)"
    bl_description = "UWMP Table 5-1 normal water year 2030"

    def execute(self, context):
        context.scene.capp_selected_scenario = "normal_wy"
        return bpy.ops.capp_carpwater.apply_scenario()


class CAPP_OT_apply_single_dry(Operator):
    bl_idname = "capp_carpwater.apply_single_dry"
    bl_label = "Single dry (5-2)"
    bl_description = "UWMP Table 5-2 single dry year 2030"

    def execute(self, context):
        context.scene.capp_selected_scenario = "single_dry"
        return bpy.ops.capp_carpwater.apply_scenario()


class CAPP_OT_apply_five_year(Operator):
    bl_idname = "capp_carpwater.apply_five_year"
    bl_label = "Five-year avg (5-4)"
    bl_description = "UWMP Table 5-4 five-year drought average 2030"

    def execute(self, context):
        context.scene.capp_selected_scenario = "five_year_avg"
        return bpy.ops.capp_carpwater.apply_scenario()


class CAPP_PT_sankey(Panel):
    bl_label = "CAPP Sankey Supply"
    bl_idname = "CAPP_PT_sankey"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "CarpWater CAPP"

    def draw(self, context):
        layout = self.layout
        sc = context.scene

        box = layout.box()
        box.label(text="1. Rest pose", icon="SNAP_GRID")
        box.operator("capp_carpwater.apply_lineup_1000", icon="ARROW_LEFTRIGHT")

        box = layout.box()
        box.label(text="2. UWMP §5 scenarios (2030)", icon="TIME")
        row = box.row(align=True)
        row.operator("capp_carpwater.apply_normal")
        row = box.row(align=True)
        row.operator("capp_carpwater.apply_single_dry")
        row = box.row(align=True)
        row.operator("capp_carpwater.apply_five_year")
        box.prop(sc, "capp_selected_scenario")
        box.operator("capp_carpwater.apply_scenario", icon="CHECKMARK")

        box = layout.box()
        box.label(text="3. Test widths (units)", icon="DRIVER_DISTANCE")
        col = box.column(align=True)
        col.prop(sc, "capp_width_cachuma")
        col.prop(sc, "capp_width_swp")
        col.prop(sc, "capp_width_groundwater")
        col.prop(sc, "capp_width_recycled")
        col.prop(sc, "capp_width_ocean")
        wsum = (
            sc.capp_width_cachuma
            + sc.capp_width_swp
            + sc.capp_width_groundwater
        )
        box.label(text=f"Trunk W = {wsum:.0f}")
        box.operator("capp_carpwater.apply_widths", icon="CHECKMARK")

        layout.separator()
        layout.label(text=f"Label: {sc.get('sankey_label', '—')}")
        layout.label(
            text=f"W={sc.get('sankey_W', '—')}  "
            f"w={sc.get('sankey_w_c', '—')}/"
            f"{sc.get('sankey_w_s', '—')}/{sc.get('sankey_w_g', '—')}  "
            f"r={sc.get('sankey_w_r', '—')}"
        )
        layout.label(
            text=(
                f"AF  C {sc.get('sankey_af_c', '—')}  "
                f"S {sc.get('sankey_af_s', '—')}  "
                f"G {sc.get('sankey_af_g', '—')}  "
                f"R {sc.get('sankey_af_r', '—')}"
            )
        )
        ocean = sc.get("sankey_af_ocean", None)
        if sc.get("sankey_ocean_unknown"):
            ocean_txt = "UNKNOWN"
        elif ocean is None or ocean == -1 or ocean == -1.0:
            ocean_txt = "—"
        else:
            ocean_txt = f"{ocean:.0f}" if isinstance(ocean, (int, float)) else str(ocean)
        layout.label(text=f"Ocean outfall AF  {ocean_txt}")
        layout.label(text="Fixed: Facility / OutDown / Arrow  |  CAPP Z preserved")


_CLASSES = (
    CAPP_OT_apply_lineup,
    CAPP_OT_apply_widths,
    CAPP_OT_apply_scenario,
    CAPP_OT_apply_normal,
    CAPP_OT_apply_single_dry,
    CAPP_OT_apply_five_year,
    CAPP_PT_sankey,
)


def register():
    from bpy.props import StringProperty

    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.capp_selected_scenario = StringProperty(
        name="Scenario",
        description="Planning scenario id in supply_planning_scenarios.json",
        default="normal_wy",
    )
    bpy.types.Scene.capp_width_cachuma = FloatProperty(
        name="Cachuma",
        description="Cachuma ribbon width (Blender units); 0 = off, else ≥40",
        default=2110 * K_AF_TO_WIDTH,
        min=0.0,
        soft_max=10000.0,
    )
    bpy.types.Scene.capp_width_swp = FloatProperty(
        name="SWP",
        description="SWP ribbon width (Blender units); 0 = off, else ≥40",
        default=755 * K_AF_TO_WIDTH,
        min=0.0,
        soft_max=10000.0,
    )
    bpy.types.Scene.capp_width_groundwater = FloatProperty(
        name="Groundwater",
        description="Groundwater ribbon width (Blender units); 0 = off, else ≥40",
        default=1200 * K_AF_TO_WIDTH,
        min=0.0,
        soft_max=10000.0,
    )
    bpy.types.Scene.capp_width_recycled = FloatProperty(
        name="CAPP (recycled)",
        description="CAPP injection / OceanREUSE width (Blender units)",
        default=1000 * K_AF_TO_WIDTH,
        min=0.0,
        soft_max=10000.0,
    )
    bpy.types.Scene.capp_width_ocean = FloatProperty(
        name="Ocean outfall (total)",
        description=(
            "TOTAL OceanOUT+OceanREUSE width (Blender units). "
            "Default = historical mean outfall (~1300 AF). "
            "Split: reuse=min(CAPP,total), out=remainder."
        ),
        default=1300 * K_AF_TO_WIDTH,
        min=0.0,
        soft_max=10000.0,
    )


def unregister():
    for attr in (
        "capp_selected_scenario",
        "capp_width_cachuma",
        "capp_width_swp",
        "capp_width_groundwater",
        "capp_width_recycled",
        "capp_width_ocean",
    ):
        if hasattr(bpy.types.Scene, attr):
            delattr(bpy.types.Scene, attr)
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass


def _bootstrap():
    try:
        unregister()
    except Exception:
        pass
    register()
    print(
        "[capp_sankey_supply_layout] Registered. "
        "N-panel → CarpWater CAPP → scenarios / lineup. "
        "Does not auto-apply on run."
    )


if __name__ == "__main__":
    try:
        unregister()
    except Exception:
        pass
    register()
    print(
        "[capp_sankey_supply_layout] Registered. "
        "N-panel → CarpWater CAPP → scenarios / lineup. "
        "Does not auto-apply on run."
    )
