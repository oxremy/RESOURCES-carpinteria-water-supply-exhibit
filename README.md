# Carpinteria Water Supply — Blender models

Authoring kit for the two CVWD supply Sankey scenes. **Not District-official.** Numbers are transcribed from public UWMP / related docs.

**Public exhibit (HTML + stills):** use the separate [carpinteria-water-supply-exhibit](https://github.com/oxremy/carpinteria-water-supply-exhibit) repo. Keep AF JSON in sync with that repo’s `data/` when numbers change (update exhibit first, then copy here and re-apply in Blender).

## Contents

| Path | Role |
|---|---|
| `CarpSupplySankey.blend` | Historical Sankey (UWMP Fig 4-1, years 2011–2025) |
| `CAPP_CarpSupplySankey.blend` | Planning / CAPP Sankey (UWMP §5, year 2030) |
| `scripts/sankey_supply_layout.py` | Historical: year → AF → ribbon layout |
| `scripts/capp_sankey_supply_layout.py` | Planning: scenario → AF → layout (+ CAPP lane) |
| `data/supply_historical.json` | Historical AF |
| `data/supply_planning_scenarios.json` | Planning scenario AF |

Requires **Blender 5.2** and **Git LFS** to clone the `.blend` files.

## Historical

1. Open `CarpSupplySankey.blend`
2. Scripting → open `scripts/sankey_supply_layout.py` → Run Script
3. 3D View → **N** → **CarpWater** → Apply Lineup / Example / Year

## Planning / CAPP

1. Open `CAPP_CarpSupplySankey.blend`
2. Run `scripts/capp_sankey_supply_layout.py`
3. **N** → **CarpWater CAPP** → Apply scenario or lineup

## Notes

- Scripts load JSON from `data/` next to this folder.
- CAPP recycled water is basin injection, not a fourth trunk supply.
- Ocean outfall AF is CSD WWTP effluent — not district demand.
