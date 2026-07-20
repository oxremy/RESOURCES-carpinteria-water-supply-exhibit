"""
CarpWater Sankey — year → AF → width + mesh-elbow layout (Blender 5.x)

HOW TO RUN
----------
1. Open CarpSupplySankey.blend
2. Scripting workspace → Open this file → Run Script (▶)  (registers UI only)
3. 3D View → N → CarpWater tab → Apply Lineup / Example / Year

GEOMETRY MODEL
--------------
Chain: Down → In → 90 (quarter annulus) → Connect → Trunk
★ BendCenter = inside corner of the L

  X★ = (In.xmid − w/2) − margin_x
  Y★ = ±(W/2 + margin_y)     # outside trunk outer edge

90 = quarter annulus about ★ in Sankey XY:
  R_inner = margin_y
  R_outer = R_inner + w
  Groundwater: θ = 0 → +π/2   (+X along In tip → +Y into trunk)
  Cachuma:     θ = 0 → −π/2   (+X along In tip → −Y into trunk)

Connect = horizontal runout along −X from the 90's trunk-facing edge
  into CarpSupplyIn (barely overlap 90; OK to overlap trunk).
  Y span = w, aligned to that 90 edge. SWP needs no Connect.

CarpSupplyIn LENGTH (Sankey X): inland tip stays west of both BendCenters
  so the 90 annuli never sit on top of the trunk. Connects + SWP tip
  extend west to meet that tip (slight overlap). Width (Y) is unchanged.

When W or w changes: widths update, ★ moves, 90+Connect rebuild,
trunk tip tracks min(★.x), SWP tip tracks trunk tip, In tip stretches
to meet the 90 (source end fixed).

AF → WIDTH
---------
Share = AF / (AF_c + AF_s + AF_g). Stored with AF for later hover labels.
  AF == 0  → width 0 (lane hidden; no 90/Connect)
  share < 1% and AF > 0 → width = 40 (visible stub)
  else → width = max(40, k × AF)

Year also updates FONT label pairs (inner + outer). Bodies match live scene /
UWMP Figure 4-1 wording:
  Cachuma Project / State Water Project / Groundwater → "{title}\\n{N} AF, {X}%"
  CarpSupply → " Total \\nWater Supply \\n{N} AF"
  OceanOutput (+ .002) → "Ocean Outfall \\n{N} AF" or "Ocean Outfall \\nUNKNOWN AF"

OceanOUT / OceanOutArrow width = k × ocean_outfall_af (same k as ribbons).
  Null outfall years (2011–2013): ribbon at mean of known years (nearest 100 AF);
  label UNKNOWN. 2014–2024 from CAPP Title 22; 2025 from UWMP Table 6-3.
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
        candidate = Path(__file__).resolve().parent.parent / "data" / "supply_historical.json"
        if candidate.exists():
            return candidate
    except NameError:
        pass
    if bpy.data.filepath:
        candidate = Path(bpy.data.filepath).resolve().parent / "data" / "supply_historical.json"
        if candidate.exists():
            return candidate
    return Path("/Users/jeremyknox/CarpWater/CarpWater/data/supply_historical.json")


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

WIDTH_AXIS = {
    "CarpSupplyIn": "y",
    "SWPIn": "y",
    "OceanOUT": "y",
    "OceanOutArrow": "y",
    "CarpSupplyDownArrow": "y",
    "CachumaIn": "x",
    "GroundWaterIn": "x",
    "GroundWaterDown": "x",
}

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
)

OCEAN_OBJECTS = ("OceanOUT", "OceanOutArrow")


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
        "label": ex.get("label", "ExampleYear"),
        "year": ex.get("year"),
        "groundwater_af": float(ex["groundwater_af"]),
        "cachuma_af": float(ex["cachuma_af"]),
        "swp_af": float(ex["swp_af"]),
        "demand_af": ex.get("demand_af"),
        "ocean_outfall_af": _optional_af(ex, "ocean_outfall_af"),
    }


def get_year_row(year: int) -> dict:
    for row in _load_data().get("years", []):
        if int(row["year"]) == int(year):
            return {
                "label": str(year),
                "year": int(year),
                "groundwater_af": float(row["groundwater_af"]),
                "cachuma_af": float(row["cachuma_af"]),
                "swp_af": float(row["swp_af"]),
                "demand_af": row.get("demand_af"),
                "ocean_outfall_af": _optional_af(row, "ocean_outfall_af"),
            }
    raise KeyError(f"No supply row for year {year} in {data_path()}")


def get_lineup_row() -> dict:
    return {
        "label": "Lineup1000",
        "year": None,
        "groundwater_af": LINEUP_WIDTH / K_AF_TO_WIDTH,
        "cachuma_af": LINEUP_WIDTH / K_AF_TO_WIDTH,
        "swp_af": LINEUP_WIDTH / K_AF_TO_WIDTH,
        "demand_af": 3.0 * LINEUP_WIDTH / K_AF_TO_WIDTH,
        "ocean_outfall_af": None,
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


def average_ocean_outfall_af() -> float:
    """
    Mean of known ocean_outfall_af years in JSON, rounded to nearest 100 AF.
    Used as a stand-in ribbon width when a year has no outfall series.
    """
    vals = []
    for row in _load_data().get("years", []):
        af = _optional_af(row, "ocean_outfall_af")
        if af is not None and af > 0.0:
            vals.append(af)
    if not vals:
        return 1300.0  # safety fallback ≈ documented 2014–2025 mean
    mean = sum(vals) / len(vals)
    return float(int(round(mean / 100.0) * 100))


def attach_ocean_outfall(layout: dict, ocean_outfall_af) -> dict:
    """
    CSD WWTP → Pacific limb (independent of supply shares).

    Known AF → ribbon + "OCEAN {N} AF".
    Missing series (null) → ribbon at average_ocean_outfall_af(); label UNKNOWN.
    """
    if ocean_outfall_af is None:
        af = average_ocean_outfall_af()
        layout["af_ocean"] = af
        layout["w_ocean"] = max(W_VIS_MIN, K_AF_TO_WIDTH * af)
        layout["ocean_active"] = True
        layout["ocean_unknown"] = True
        return layout
    af = float(ocean_outfall_af)
    if af <= 0.0:
        # Explicit zero in data (not missing): show thin? Prefer stub average width
        # only for null. True zero keeps ribbon off.
        layout["af_ocean"] = 0.0
        layout["w_ocean"] = 0.0
        layout["ocean_active"] = False
        layout["ocean_unknown"] = False
    else:
        layout["af_ocean"] = af
        layout["w_ocean"] = max(W_VIS_MIN, K_AF_TO_WIDTH * af)
        layout["ocean_active"] = True
        layout["ocean_unknown"] = False
    return layout


def ocean_active(layout: dict) -> bool:
    return bool(layout.get("ocean_active"))


def ocean_unknown(layout: dict) -> bool:
    return bool(layout.get("ocean_unknown"))


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
    ls = [world_to_sankey(obj.matrix_world @ v.co) for v in obj.data.vertices]
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
    """Show/hide OceanOUT / OceanOutArrow with ocean_active."""
    visible = ocean_active(layout)
    for name in OCEAN_OBJECTS:
        obj = bpy.data.objects.get(name)
        if not obj:
            continue
        obj.hide_viewport = not visible
        obj.hide_render = not visible
    if not visible:
        return "ocean:off"
    tag = "UNKNOWN" if ocean_unknown(layout) else f"{layout.get('af_ocean', 0):.0f}"
    return f"ocean:on w={layout.get('w_ocean', 0):.0f} af={tag}"


def apply_widths(layout: dict) -> list[str]:
    w_c, w_s, w_g, W = layout["w_c"], layout["w_s"], layout["w_g"], layout["W"]
    w_ocean = float(layout.get("w_ocean", 0.0))
    changed = []
    mesh_widths = {
        "CarpSupplyIn": W,
        "SWPIn": w_s,
        "OceanOUT": w_ocean,
        "OceanOutArrow": w_ocean,
        "CarpSupplyDownArrow": W,
        "CachumaIn": w_c,
        "GroundWaterIn": w_g,
        "GroundWaterDown": w_g,
    }
    for name, width in mesh_widths.items():
        obj = bpy.data.objects.get(name)
        axis = WIDTH_AXIS.get(name)
        if not obj or obj.type != "MESH" or not axis:
            continue
        # Skip zero-width lane / ocean meshes (hidden); avoid scale=0.
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

    _depsgraph_update()
    return changed


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
    return f"{title}\n{format_label_af(af)} AF, {format_label_pct(pct)}"


def total_label_body(af_total: float) -> str:
    # Live CarpSupplyLabel: leading space on " Total ", two-line title.
    return f" Total \nWater Supply \n{format_label_af(af_total)} AF"


def ocean_label_body(af_ocean: float, unknown: bool = False) -> str:
    # Live OceanOutput: "Ocean Outfall \n…" (trailing space after Outfall).
    if unknown:
        return "Ocean Outfall \nUNKNOWN AF"
    return f"Ocean Outfall \n{format_label_af(af_ocean)} AF"


def update_year_labels(layout: dict) -> list[str]:
    """
    Push AF / share% into label pairs (inner + outer).
    Supply lane + total labels always stay visible (0 AF, 0% when empty).
    Ocean labels always show when ocean limb is active; UNKNOWN when no series.
    """
    notes = []
    for spec in LABEL_PAIRS:
        show = True
        if spec["kind"] == "lane":
            af = float(layout.get(spec["af_key"], 0.0))
            pct = float(layout.get(spec["pct_key"], 0.0))
            body = lane_label_body(spec["title"], af, pct)
        elif spec["kind"] == "total":
            af = float(layout.get("af_total", 0.0))
            body = total_label_body(af)
        elif spec["kind"] == "ocean":
            show = ocean_active(layout)
            body = ocean_label_body(
                float(layout.get("af_ocean") or 0.0),
                unknown=ocean_unknown(layout),
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
            if obj.data.body != body:
                obj.data.body = body
            obj.hide_viewport = not show
            obj.hide_render = not show
            hit.append(name)
        if hit:
            if show:
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

def apply_layout(row: dict, layout: dict) -> dict:
    """
    Full chain update:
      widths → visibility → SWP lane Y → ★ → 90s → stretch Ins →
      shrink trunk tip west of ★s → Connects → SWP tip → Downs → labels
    """
    attach_ocean_outfall(layout, row.get("ocean_outfall_af"))
    _write_scene_props(row, layout)

    vis_notes = set_lane_visibility(layout)
    ocean_note = set_ocean_visibility(layout)
    width_objs = apply_widths(layout)
    swp_note = place_swp_on_lane(layout)
    place_bend_centers(layout)
    update_nineties(layout)
    stretch_side_ins_to_nineties(layout)

    # Reseat after In stretch (xmid may nudge)
    bend_notes = place_bend_centers(layout)
    ninety_notes = update_nineties(layout)
    stretch_notes = stretch_side_ins_to_nineties(layout)

    # Trunk length: clear 90s, then bridge with Connects + SWP tip
    trunk_note = shrink_trunk_from_nineties(layout)
    connect_notes = update_connects(layout)
    swp_tip_note = extend_swp_to_trunk(layout)
    swp_note_2 = place_swp_on_lane(layout)  # Y may need a nudge after X stretch

    down_notes = []
    for chain in SIDE_CHAINS:
        n = align_down_to_in(chain, layout)
        if n:
            down_notes.append(n)

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
        f"{row['label']}: AF c/s/g="
        f"{layout.get('af_c', 0):.0f}/{layout.get('af_s', 0):.0f}/"
        f"{layout.get('af_g', 0):.0f} "
        f"ocean={ocean_txt} "
        f"pct={layout.get('pct_c', 0):.1f}/{layout.get('pct_s', 0):.1f}/"
        f"{layout.get('pct_g', 0):.1f}% "
        f"mode={layout.get('mode_c')}/{layout.get('mode_s')}/{layout.get('mode_g')} | "
        f"w_c/w_s/w_g/W="
        f"{layout['w_c']:.0f}/{layout['w_s']:.0f}/{layout['w_g']:.0f}/{layout['W']:.0f} | "
        f"vis→{','.join(vis_notes)} | {ocean_note} | "
        f"widths→{', '.join(width_objs)} | {swp_note} | "
        f"bends→{', '.join(bend_notes)} | "
        f"90→{', '.join(ninety_notes)} | "
        f"ins→{', '.join(stretch_notes)} | "
        f"trunk→{trunk_note} | "
        f"conn→{', '.join(connect_notes)} | "
        f"swpTip→{swp_tip_note} / {swp_note_2} | "
        f"down→{', '.join(down_notes) or '—'} | "
        f"labels→{'; '.join(label_notes) or '—'} | "
        f"seal→{'; '.join(seal_notes)}"
    )
    print("[sankey_supply_layout]", msg)
    layout["report"] = msg
    return layout


def apply_row_from_af(row: dict) -> dict:
    return apply_layout(row, widths_from_af(
        row["cachuma_af"], row["swp_af"], row["groundwater_af"]
    ))


def apply_lineup_1000() -> dict:
    return apply_layout(get_lineup_row(), widths_forced_equal(LINEUP_WIDTH))


def apply_forced_widths(w_c: float, w_s: float, w_g: float, label: str = "Forced") -> dict:
    """Apply explicit ribbon widths (Blender units). 0 stays 0; else ≥ W_VIS_MIN."""
    w_c = floor_forced_width(w_c)
    w_s = floor_forced_width(w_s)
    w_g = floor_forced_width(w_g)
    af_c = w_c / K_AF_TO_WIDTH if w_c > 0.0 else 0.0
    af_s = w_s / K_AF_TO_WIDTH if w_s > 0.0 else 0.0
    af_g = w_g / K_AF_TO_WIDTH if w_g > 0.0 else 0.0
    total = af_c + af_s + af_g
    pct_c = (af_c / total) if total > 0 else 0.0
    pct_s = (af_s / total) if total > 0 else 0.0
    pct_g = (af_g / total) if total > 0 else 0.0
    mode_c = "zero" if w_c <= 0 else ("collapse" if w_c <= W_VIS_MIN + 1e-6 else "scaled")
    mode_s = "zero" if w_s <= 0 else ("collapse" if w_s <= W_VIS_MIN + 1e-6 else "scaled")
    mode_g = "zero" if w_g <= 0 else ("collapse" if w_g <= W_VIS_MIN + 1e-6 else "scaled")
    row = {
        "label": label,
        "year": None,
        "groundwater_af": af_g,
        "cachuma_af": af_c,
        "swp_af": af_s,
        "demand_af": total,
        "ocean_outfall_af": None,
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

class CARPWATER_OT_apply_lineup(Operator):
    bl_idname = "carpwater.apply_lineup_1000"
    bl_label = "Apply Lineup Widths (1000)"
    bl_description = (
        "Equal widths 1000 (W=3000): trunk, SWP lane, BendCenters, "
        "rebuild 90 elbows, stretch Ins to seal"
    )

    def execute(self, context):
        try:
            layout = apply_lineup_1000()
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self.report({"INFO"}, f"Lineup1000 W={layout['W']:.0f}")
        return {"FINISHED"}


class CARPWATER_OT_apply_widths(Operator):
    bl_idname = "carpwater.apply_widths"
    bl_label = "Apply Widths"
    bl_description = (
        "Apply Cachuma / SWP / GW ribbon widths from the fields below "
        "(Blender units). Rebuilds 90s and stretches Ins to seal."
    )

    def execute(self, context):
        sc = context.scene
        try:
            layout = apply_forced_widths(
                sc.carpwater_width_cachuma,
                sc.carpwater_width_swp,
                sc.carpwater_width_groundwater,
                label=(
                    f"W{sc.carpwater_width_cachuma:.0f}_"
                    f"{sc.carpwater_width_swp:.0f}_"
                    f"{sc.carpwater_width_groundwater:.0f}"
                ),
            )
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self.report(
            {"INFO"},
            f"Widths {sc.carpwater_width_cachuma:.0f}/"
            f"{sc.carpwater_width_swp:.0f}/"
            f"{sc.carpwater_width_groundwater:.0f} "
            f"W={layout['W']:.0f}",
        )
        return {"FINISHED"}


class CARPWATER_OT_apply_example(Operator):
    bl_idname = "carpwater.apply_example_year"
    bl_label = "Apply ExampleYear (AF from JSON)"
    bl_description = "AF→widths from ExampleYear + full chain layout"

    def execute(self, context):
        try:
            layout = apply_row_from_af(get_example_row())
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self.report({"INFO"}, f"ExampleYear W={layout['W']:.0f}")
        return {"FINISHED"}


class CARPWATER_OT_apply_year(Operator):
    bl_idname = "carpwater.apply_year"
    bl_label = "Apply Year"
    bl_description = "Apply historical year from JSON + full chain layout"

    def execute(self, context):
        year = int(context.scene.carpwater_selected_year)
        try:
            layout = apply_row_from_af(get_year_row(year))
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self.report({"INFO"}, f"{year} → W={layout['W']:.0f}")
        return {"FINISHED"}


class CARPWATER_PT_sankey(Panel):
    bl_label = "Sankey Supply"
    bl_idname = "CARPWATER_PT_sankey"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "CarpWater"

    def draw(self, context):
        layout = self.layout
        sc = context.scene

        box = layout.box()
        box.label(text="1. Rest pose", icon="SNAP_GRID")
        box.operator("carpwater.apply_lineup_1000", icon="ARROW_LEFTRIGHT")

        box = layout.box()
        box.label(text="2. Test widths (units)", icon="DRIVER_DISTANCE")
        col = box.column(align=True)
        col.prop(sc, "carpwater_width_cachuma")
        col.prop(sc, "carpwater_width_swp")
        col.prop(sc, "carpwater_width_groundwater")
        wsum = (
            sc.carpwater_width_cachuma
            + sc.carpwater_width_swp
            + sc.carpwater_width_groundwater
        )
        box.label(text=f"Trunk W = {wsum:.0f}")
        box.operator("carpwater.apply_widths", icon="CHECKMARK")

        box = layout.box()
        box.label(text="3. From AF data", icon="TIME")
        box.operator("carpwater.apply_example_year", icon="FILE_TICK")
        box.prop(sc, "carpwater_selected_year")
        box.operator("carpwater.apply_year", icon="TIME")

        layout.separator()
        layout.label(text=f"Label: {sc.get('sankey_label', '—')}")
        layout.label(
            text=f"W={sc.get('sankey_W', '—')}  "
            f"w={sc.get('sankey_w_c', '—')}/"
            f"{sc.get('sankey_w_s', '—')}/{sc.get('sankey_w_g', '—')}"
        )
        layout.label(
            text=(
                f"AF  C {sc.get('sankey_af_c', '—')}  "
                f"S {sc.get('sankey_af_s', '—')}  "
                f"G {sc.get('sankey_af_g', '—')}"
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
        layout.label(
            text=(
                f"%   C {sc.get('sankey_pct_c', '—')}  "
                f"S {sc.get('sankey_pct_s', '—')}  "
                f"G {sc.get('sankey_pct_g', '—')}"
            )
        )


_CLASSES = (
    CARPWATER_OT_apply_lineup,
    CARPWATER_OT_apply_widths,
    CARPWATER_OT_apply_example,
    CARPWATER_OT_apply_year,
    CARPWATER_PT_sankey,
)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    # Scene props (not PropertyGroup) — reliable when run from Text Editor
    bpy.types.Scene.carpwater_selected_year = IntProperty(
        name="Year", default=2025, min=2011, max=2025
    )
    bpy.types.Scene.carpwater_width_cachuma = FloatProperty(
        name="Cachuma",
        description="Cachuma ribbon width (Blender units); 0 = off, else ≥40",
        default=2772.75,
        min=0.0,
        soft_max=10000.0,
    )
    bpy.types.Scene.carpwater_width_swp = FloatProperty(
        name="SWP",
        description="SWP ribbon width (Blender units); 0 = off, else ≥40",
        default=646.5,
        min=0.0,
        soft_max=10000.0,
    )
    bpy.types.Scene.carpwater_width_groundwater = FloatProperty(
        name="Groundwater",
        description="Groundwater ribbon width (Blender units); 0 = off, else ≥40",
        default=234.0,
        min=0.0,
        soft_max=10000.0,
    )


def unregister():
    for attr in (
        "carpwater_selected_year",
        "carpwater_width_cachuma",
        "carpwater_width_swp",
        "carpwater_width_groundwater",
        "carpwater_sankey",  # legacy PropertyGroup pointer if present
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
        "[sankey_supply_layout] Registered. "
        "CarpWater → Apply ExampleYear (AF→width ×0.75) or Apply Widths. "
        "Does not auto-apply on run."
    )


if __name__ == "__main__":
    try:
        unregister()
    except Exception:
        pass
    register()
    print(
        "[sankey_supply_layout] Registered. "
        "CarpWater → Apply ExampleYear (AF→width ×0.75) or Apply Widths. "
        "Does not auto-apply on run."
    )
