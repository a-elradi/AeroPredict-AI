"""
Download airport maps from OpenStreetMap and save them to backend/data/osm/,
so the website can answer instantly without waiting on the public map servers.

    python scripts/build_osm_snapshots.py            # default airports
    python scripts/build_osm_snapshots.py IST LHR    # specific airports

Run monthly by .github/workflows/osm-snapshots.yml.
"""
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import airports, wayfinding  # noqa: E402

PAUSE_BETWEEN_AIRPORTS_S = 10  # be polite to the free Overpass servers


def build(iata: str) -> bool:
    started = time.time()
    elements = wayfinding._fetch(iata, timeout=300)
    if elements is None:
        print(f"{iata}: FAILED ({wayfinding.last_errors.get(iata, 'unknown error')[:300]})")
        return False
    features = wayfinding.parse_elements(elements)
    if not features["gates"] and not features["places"]:
        print(f"{iata}: no map data, keeping any existing snapshot")
        return False
    snapshot = {
        "iata": iata,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "attribution": wayfinding.ATTRIBUTION,
        "gates": features["gates"],
        "places": features["places"],
    }
    path = wayfinding.SNAPSHOT_DIR / f"{iata}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"{iata}: {len(features['gates'])} gates, {len(features['places'])} places "
          f"({time.time() - started:.0f}s)")
    return True


def main() -> int:
    codes = [c.upper() for c in sys.argv[1:]] or sorted(airports.BOUNDS)
    ok = 0
    for i, iata in enumerate(codes):
        if i:
            time.sleep(PAUSE_BETWEEN_AIRPORTS_S)
        ok += build(iata)
    print(f"Done: {ok}/{len(codes)} airports saved")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
