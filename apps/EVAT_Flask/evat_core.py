# In[1]:

from __future__ import annotations
import os, json, time, random, re, math, urllib.request, urllib.parse, json as _j
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple

# ================== CONFIG / PATHS ==================
USER_ID   = "cli_user"
DATA_DIR  = Path("ev_data"); DATA_DIR.mkdir(parents=True, exist_ok=True)
BASIC_CSV = DATA_DIR / "ev_charging_stations.csv"
ENRICHED_CSV = DATA_DIR / "ev_charging_stations_enriched.csv"
REFRESH_DATA = os.getenv("REFRESH_DATA", "0") == "1"

def in_australia(lat: float, lon: float) -> bool:
    # rough national bounding box
    return (-44.5 < lat < -9.0) and (111.0 < lon < 154.5)

# ================== ENV & KEYS (loads from Key.env first) ==================
GOOGLE_API_KEY = ""
OCM_API_KEY    = ""
try:
    from dotenv import load_dotenv
    # Look for Key.env in CWD or script folder; else fall back to default .env in CWD
    candidates = [Path.cwd() / "Key.env"]
    try:
        script_dir = Path(__file__).resolve().parent
        candidates.append(script_dir / "Key.env")
    except Exception:
        pass
    loaded = False
    for c in candidates:
        if c.exists():
            load_dotenv(dotenv_path=c, override=False)
            loaded = True
            break
    if not loaded:
        load_dotenv()  # fall back to ".env" if present
except Exception:
    pass

# Read keys from env (set inside Key.env)
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "") or GOOGLE_API_KEY
OCM_API_KEY    = os.getenv("OCM_API_KEY", "") or OCM_API_KEY

# ================== NLP (Sprint 1) ==================
try:
    import spacy
    NLP = spacy.load("en_core_web_sm")
except Exception:
    NLP = None

LOCATION_LABELS = {"GPE", "LOC", "FAC"}
FILLER = {"i","nearest","charging","charger","chargers","station","stations",
          "ev","electric","vehicle","please","show","find","where","what","from",
          "to","near","at","in","around","me","the","a","an"}

def _clean(s: str) -> str:
    s = re.sub(r"[\.,?!]+$", "", s).strip()
    return " ".join(t for t in s.split() if t.lower() not in FILLER).strip()

def extract_poi(text: str) -> Optional[str]:
    best = None
    if NLP:
        doc = NLP(text)
        for ent in doc.ents:
            if ent.label_ in LOCATION_LABELS:
                cand = _clean(ent.text)
                if cand and (best is None or len(cand) > len(best)): best = cand
        if best: return best
    m = re.search(r"(?:\bto\b|\bnear\b|\bat\b|\bin\b|\bfrom\b|\baround\b)\s+(.+)$", text, re.I)
    if m:
        cand = _clean(re.split(r"[?.,;]", m.group(1))[0])
        if cand: return cand
    run = []
    for t in (t.strip(",.?!") for t in text.split()):
        if t and t[0].isupper() and t.lower() not in FILLER: run.append(t)
        elif run: break
    return " ".join(run) if run else None

# ================== Geocoding ==================
_GOOGLE = None
if GOOGLE_API_KEY:
    try:
        import googlemaps
        _GOOGLE = googlemaps.Client(key=GOOGLE_API_KEY)
    except Exception:
        _GOOGLE = None

def geocode_google(q: str) -> Optional[Tuple[float,float]]:
    if not _GOOGLE: return None
    try:
        res = _GOOGLE.geocode(q)
        if res:
            loc = res[0]["geometry"]["location"]
            return float(loc["lat"]), float(loc["lng"])
    except Exception:
        return None
    return None

def geocode_osm(q: str) -> Optional[Tuple[float,float]]:
    try:
        params = urllib.parse.urlencode({"q": q, "format": "json", "limit": 1})
        url = f"https://nominatim.openstreetmap.org/search?{params}"
        req = urllib.request.Request(url, headers={"User-Agent": "EVAT-Chatbot/1.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = _j.loads(r.read().decode("utf-8"))
        if data: return float(data[0]["lat"]), float(data[0]["lon"])
    except Exception:
        return None
    return None

def geocode_australia(poi: str) -> Optional[Tuple[float,float]]:
    tries = [poi, f"{poi}, Australia"]
    parts = poi.split()
    if len(parts) >= 2: tries.append(f"{' '.join(parts[-2:])}, Australia")
    for q in tries:
        c = geocode_google(q) or geocode_osm(q)
        if c and in_australia(*c): return c
    return None

# ================== Distance ==================
from geopy.distance import geodesic
def distance_km(a: Tuple[float,float], b: Tuple[float,float]) -> float:
    return float(geodesic(a, b).km)

# ================== Profile & History (Sprint 3) ==================
@dataclass
class UserPreferences:
    consent: bool = True
    plug_types: List[str] = field(default_factory=list)
    min_kw: Optional[float] = None
    preferred_networks: List[str] = field(default_factory=list)
    price_cap: Optional[float] = None
    max_distance_km: float = 25.0
    amenities: List[str] = field(default_factory=list)  # e.g., ["toilet","food","wifi"]
    sightseeing_prefs: List[str] = field(default_factory=lambda: ["museum","gallery","park","lookout"])
    weights: Dict[str,float] = field(default_factory=lambda: {"distance":0.45,"preference":0.45,"recency":0.10})
    # Trip planning
    range_km: float = 300.0
    reserve_km: float = 40.0
    corridor_km: float = 8.0
    max_detour_km: float = 25.0

@dataclass
class UserHistory:
    recent_pois: List[Tuple[str,float]] = field(default_factory=list)
    recent_stations: List[Tuple[str,float]] = field(default_factory=list)

@dataclass
class UserProfile:
    user_id: str
    prefs: UserPreferences = field(default_factory=UserPreferences)
    history: UserHistory = field(default_factory=UserHistory)

_STATE_DIR = Path("./user_state"); _STATE_DIR.mkdir(parents=True, exist_ok=True)
def load_profile(uid: str) -> UserProfile:
    p = _STATE_DIR / f"{uid}.json"
    if p.exists():
        d = json.loads(p.read_text(encoding="utf-8"))
        return UserProfile(uid, UserPreferences(**d.get("prefs", {})), UserHistory(**d.get("history", {})))
    return UserProfile(uid)
def save_profile(prof: UserProfile) -> None:
    p = _STATE_DIR / f"{prof.user_id}.json"
    p.write_text(json.dumps({"prefs":asdict(prof.prefs),"history":asdict(prof.history)}, ensure_ascii=False, indent=2),
                 encoding="utf-8")
def reset_profile(uid: str) -> None:
    p = _STATE_DIR / f"{uid}.json"
    if p.exists(): p.unlink()

# ================== Dataset (Open Charge Map AU) ==================
import pandas as pd, requests

def fetch_ocm_australia(basic_csv: Path=BASIC_CSV, enriched_csv: Path=ENRICHED_CSV) -> bool:
    """
    AU-wide pull from Open Charge Map using **cursor-by-ID** pagination
    (sortby=id_asc & greaterthanid=<last_id>) to avoid the infinite offset loop.
    You can control behavior with env vars:
      - OCM_ONE_SHOT=1  -> fetch all in one huge request (no pagination)
      - OCM_MAX_PAGES=N -> stop after N pages (test safety, default: unlimited)
      - OCM_BATCHSIZE=M -> page size (default: 1000)
    """
    try:
        print("[i] Fetching Australia chargers from Open Charge Map …")
        headers = {"X-API-Key": OCM_API_KEY} if OCM_API_KEY else {}
        url = "https://api.openchargemap.io/v3/poi/"

        # One-shot mode (simple)
        if os.getenv("OCM_ONE_SHOT", "0") == "1":
            params = {
                "countrycode": "AU",
                "maxresults": int(os.getenv("OCM_BATCHSIZE", "1000000")),  # big fetch
                "compact": True,
                "verbose": False,
            }
            r = requests.get(url, params=params, headers=headers, timeout=180)
            r.raise_for_status()
            all_items: List[Dict[str, Any]] = r.json() or []
            print(f"  · one-shot received {len(all_items)} items")
        else:
            # ID-cursor pagination
            batch = int(os.getenv("OCM_BATCHSIZE", "1000"))
            last_id = 0
            all_items = []
            pages = 0
            max_pages = int(os.getenv("OCM_MAX_PAGES", "0"))  # 0 = unlimited
            sleep_sec = 0.2

            while True:
                params = {
                    "countrycode": "AU",
                    "compact": True,
                    "verbose": False,
                    "maxresults": batch,
                    "sortby": "id_asc",
                    "greaterthanid": last_id
                }
                r = requests.get(url, params=params, headers=headers, timeout=90)
                r.raise_for_status()
                items = r.json() or []

                # strictly-new IDs beyond the last cursor
                new = [it for it in items if isinstance(it.get("ID"), int) and it["ID"] > last_id]
                if not items or not new:
                    print("  · no more new records; done")
                    break

                all_items.extend(new)
                last_id = max(it["ID"] for it in new if isinstance(it.get("ID"), int))
                pages += 1
                print(f"  · fetched {len(items)} (new {len(new)}) after ID {last_id}; total {len(all_items)}")

                if len(items) < batch:
                    print("  · short page received; done")
                    break
                if max_pages and pages >= max_pages:
                    print(f"[WARN] Hit OCM_MAX_PAGES={max_pages}; stopping early (test mode).")
                    break

                try:
                    time.sleep(sleep_sec)  # polite pause
                except Exception:
                    pass

    except Exception as e:
        print(f"[WARN] OCM fetch failed: {e}")
        return False

    # ---- Build CSVs ----
    rows_basic, rows_enriched = [], []
    for i, it in enumerate(all_items, 1):
        ai = it.get("AddressInfo") or {}
        lat, lon = ai.get("Latitude"), ai.get("Longitude")
        if lat is None or lon is None:
            continue
        if not in_australia(float(lat), float(lon)):
            continue

        name = ai.get("Title") or ai.get("AddressLine1") or f"EV Site {i}"

        plugs = set()
        max_kw = None
        for conn in (it.get("Connections") or []):
            ct = (conn.get("ConnectionType") or {}).get("Title")
            if ct:
                plugs.add(ct.strip())
            pkw = conn.get("PowerKW")
            try:
                if pkw is not None:
                    pkw = float(pkw)
                    if math.isfinite(pkw):
                        max_kw = max(max_kw or 0.0, pkw)
            except Exception:
                pass

        rows_basic.append({
            "name": name,
            "latitude": float(lat),
            "longitude": float(lon)
        })
        rows_enriched.append({
            "name": name,
            "latitude": float(lat),
            "longitude": float(lon),
            "kw": max_kw,
            "plug_types": "|".join(sorted(plugs)) if plugs else "",
            "owner": (it.get("OperatorInfo") or {}).get("Title")
        })

    if not rows_basic:
        print("[WARN] OCM returned no usable records.")
        return False

    pd.DataFrame(rows_basic).to_csv(basic_csv, index=False)
    pd.DataFrame(rows_enriched).to_csv(enriched_csv, index=False)
    print(f"[ok] Saved {len(rows_basic)} AU sites → {basic_csv}")
    print(f"[ok] Enriched copy → {enriched_csv}")
    return True

def _synthetic_au(n: int = 500) -> List[Dict[str,Any]]:
    random.seed(42)
    seeds = [(-33.87,151.21),(-37.81,144.96),(-27.47,153.03),(-31.95,115.86),
             (-34.93,138.60),(-35.28,149.13),(-42.88,147.33),(-12.46,130.84)]
    out=[]
    for i in range(1, n+1):
        lat0, lon0 = random.choice(seeds)
        out.append({"name": f"EV Station {i}",
                    "latitude": lat0 + random.uniform(-0.3, 0.3),
                    "longitude": lon0 + random.uniform(-0.3, 0.3)})
    return out

def ensure_dataset() -> None:
    if REFRESH_DATA or not BASIC_CSV.exists():
        ok = fetch_ocm_australia(BASIC_CSV, ENRICHED_CSV)
        if ok: return
        print("[WARN] Falling back to synthetic AU dataset.")
        pd.DataFrame(_synthetic_au()).to_csv(BASIC_CSV, index=False)

def load_stations(prefer_enriched: bool=True) -> List[Dict[str,Any]]:
    ensure_dataset()
    use = ENRICHED_CSV if (prefer_enriched and ENRICHED_CSV.exists()) else BASIC_CSV
    df = pd.read_csv(use)
    assert {"name","latitude","longitude"}.issubset(df.columns), f"{use} missing required columns"
    recs = df.to_dict(orient="records")
    for r in recs:
        if "plug_types" in r:
            v = r["plug_types"]
            if isinstance(v, str) and v.strip():
                for sep in ["|",";","/",","]:
                    if sep in v:
                        r["plug_types"] = [p.strip() for p in v.split(sep) if p.strip()]
                        break
                if isinstance(r["plug_types"], str): r["plug_types"] = [r["plug_types"]]
            else:
                r["plug_types"] = []
        if "kw" in r:
            try:
                val = float(r["kw"]) if str(r["kw"]).strip() else None
                r["kw"] = val if (val is not None and math.isfinite(val)) else None
            except Exception:
                r["kw"] = None
    return recs

# ================== Ranking (S2+S3) ==================
def nearest_candidates(origin: Tuple[float,float], stations: List[Dict[str,Any]], radius_km: Optional[float]=None) -> List[Dict[str,Any]]:
    out = []
    for s in stations:
        d = distance_km(origin, (s["latitude"], s["longitude"]))
        if radius_km is not None and d > radius_km: continue
        out.append({**s, "distance_km": round(d, 2)})
    out.sort(key=lambda x: (x["distance_km"], x["name"]))
    return out

def _distance_score(km_val: float, max_km: float) -> float:
    if max_km <= 0: return 0.0
    return max(0.0, min(1.0, 1.0 - min(km_val / max_km, 1.0)))

def _pref_fit(prefs: UserPreferences, s: Dict[str,Any]) -> float:
    if not prefs.consent: return 0.0
    total, pts = 1e-9, 0.0
    total += 1
    st_plugs = s.get("plug_types") or []
    pts += 1 if (not prefs.plug_types or any(p in st_plugs for p in prefs.plug_types)) else 0
    total += 1
    st_kw = s.get("kw")
    pts += 1 if (prefs.min_kw is None or (st_kw is not None and float(st_kw) >= prefs.min_kw)) else 0
    return max(0.0, min(1.0, pts/total))

def _recency_score(hist: UserHistory, name: str, half_life_h: float=72.0) -> float:
    now = time.time(); hl = half_life_h*3600.0; s = 0.0
    for n, ts in (hist.recent_stations + hist.recent_pois):
        if n != name: continue
        age = max(0.0, now - ts); s += 0.5 ** (age / hl)
    return max(0.0, min(1.0, s))

def personalize_rank(origin: Tuple[float,float], profile: UserProfile, stations: List[Dict[str,Any]], want_k: int=3) -> List[Dict[str,Any]]:
    expansions = [profile.prefs.max_distance_km, 50, 100, 200, 400]
    w = profile.prefs.weights
    for r in expansions:
        pool = nearest_candidates(origin, stations, radius_km=r)
        if not pool: continue
        ranked = []
        for s in pool:
            ds = _distance_score(s["distance_km"], max(r, 1e-6))
            ps = _pref_fit(profile.prefs, s)
            rs = _recency_score(profile.history, s["name"])
            score = w["distance"]*ds + w["preference"]*ps + w["recency"]*rs
            ranked.append({**s, "score": round(score, 4)})
        ranked.sort(key=lambda x: (-x["score"], x["distance_km"], x["name"]))
        return ranked[:want_k]
    pool = nearest_candidates(origin, stations, radius_km=None); pool.sort(key=lambda x: x["distance_km"])
    return pool[:want_k]

# ================== Routing & Trip Planning (Sprint 4) ==================
def route_osrm(origin: Tuple[float,float], dest: Tuple[float,float]) -> Optional[Dict[str,Any]]:
    try:
        o = f"{origin[1]},{origin[0]}"; d = f"{dest[1]},{dest[0]}"
        url = f"https://router.project-osrm.org/route/v1/driving/{o};{d}?overview=full&geometries=geojson"
        req = urllib.request.Request(url, headers={"User-Agent": "EVAT-Chatbot/1.0"})
        with urllib.request.urlopen(req, timeout=20) as r:
            data = _j.loads(r.read().decode("utf-8"))
        if data.get("code") == "Ok" and data.get("routes"):
            route = data["routes"][0]
            coords = route["geometry"]["coordinates"]
            path = [(float(lat), float(lon)) for lon, lat in coords]
            return {"path": path, "distance_km": float(route["distance"])/1000.0}
    except Exception:
        return None
    return None

def _cumdist(path: List[Tuple[float,float]]) -> List[float]:
    out = [0.0]
    for i in range(1, len(path)):
        out.append(out[-1] + distance_km(path[i-1], path[i]))
    return out

def _pick_by_km(path: List[Tuple[float,float]], cum: List[float], tgt: float) -> Tuple[int, Tuple[float,float]]:
    for i, c in enumerate(cum):
        if c >= tgt: return i, path[i]
    return len(path)-1, path[-1]

def _nearest_in_corridor(pt: Tuple[float,float], stations: List[Dict[str,Any]], corridor_km: float, profile: UserProfile) -> Optional[Dict[str,Any]]:
    pool = []
    for s in stations:
        d = distance_km(pt, (s["latitude"], s["longitude"]))
        if d <= corridor_km: pool.append({**s, "distance_km": round(d, 2)})
    if not pool: return None
    ranked = []
    for s in pool:
        ps = _pref_fit(profile.prefs, s)
        score = 0.6*ps + 0.4*max(0.0, 1.0 - s["distance_km"]/max(corridor_km, 1e-6))
        ranked.append({**s, "corridor_score": round(score, 4)})
    ranked.sort(key=lambda x: (-x["corridor_score"], x["distance_km"], x["name"]))
    return ranked[0]

def _detour_fallback(pt: Tuple[float,float], stations: List[Dict[str,Any]], max_detour_km: float, profile: UserProfile) -> Optional[Dict[str,Any]]:
    pool = []
    for s in stations:
        d = distance_km(pt, (s["latitude"], s["longitude"]))
        if d <= max_detour_km: pool.append({**s, "distance_km": round(d, 2)})
    if not pool: return None
    ranked = []
    for s in pool:
        ps = _pref_fit(profile.prefs, s)
        ranked.append({**s, "detour_score": round(0.7*ps + 0.3*(1.0 - s["distance_km"]/max(max_detour_km,1e-6)), 4)})
    ranked.sort(key=lambda x: (-x["detour_score"], x["distance_km"]))
    return ranked[0]

def plan_trip_with_chargers(origin: Tuple[float,float], dest: Tuple[float,float], profile: UserProfile, stations: List[Dict[str,Any]]) -> Optional[Dict[str,Any]]:
    route = route_osrm(origin, dest)
    if not route: return None
    path = route["path"]; cum = _cumdist(path); total = cum[-1] if cum else 0.0

    usable   = max(60.0, profile.prefs.range_km)
    reserve  = max(10.0, min(profile.prefs.reserve_km, usable/2))
    corridor = max(4.0, profile.prefs.corridor_km)
    detour   = max(10.0, profile.prefs.max_detour_km)

    stops = []; next_break = usable - reserve
    while next_break < total - 5:
        _, pt = _pick_by_km(path, cum, next_break)
        cand = _nearest_in_corridor(pt, stations, corridor, profile) or _detour_fallback(pt, stations, detour, profile)
        if not cand:
            next_break += 50.0
            if next_break > total: break
            continue
        cand["at_km"] = round(next_break, 1)
        stops.append(cand)
        next_break += usable

    return {"distance_km": round(total, 1), "origin": origin, "destination": dest, "stops": stops, "path": path}

# --------- Sightseeing via Overpass (no key needed) ----------
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
TOURISM_TAGS = ["attraction","museum","gallery","viewpoint","zoo","theme_park","artwork","monument","memorial","park","beach"]

def find_sightseeing_near(pt: Tuple[float,float], prefs: UserPreferences, radius_km: float=3.0, max_items: int=3) -> List[Dict[str,Any]]:
    lat, lon = pt
    radius_m = int(radius_km * 1000)
    parts = []
    for tag in TOURISM_TAGS:
        parts.append(f'node(around:{radius_m},{lat},{lon})["tourism"="{tag}"];')
        parts.append(f'way(around:{radius_m},{lat},{lon})["tourism"="{tag}"];')
        parts.append(f'relation(around:{radius_m},{lat},{lon})["tourism"="{tag}"];')
    q = f"[out:json][timeout:25];({''.join(parts)});out center 20;"

    try:
        r = requests.post(OVERPASS_URL, data={"data": q}, timeout=30)
        r.raise_for_status()
        data = r.json().get("elements", [])
    except Exception:
        return []

    out = []
    for el in data:
        tags = el.get("tags", {})
        name = tags.get("name") or tags.get("ref") or "Attraction"
        if "lat" in el and "lon" in el:
            alat, alon = el["lat"], el["lon"]
        else:
            c = el.get("center", {})
            alat, alon = c.get("lat"), c.get("lon")
        if alat is None or alon is None: continue

        dist = round(distance_km(pt, (alat, alon)), 2)
        label = tags.get("tourism") or tags.get("amenity") or "poi"
        out.append({"name": name, "type": label, "latitude": alat, "longitude": alon, "distance_km": dist})

    prefs_kw = [p.lower() for p in (prefs.sightseeing_prefs or [])]
    def score(p):
        txt = (p["name"] + " " + p["type"]).lower()
        pref_hit = any(k in txt for k in prefs_kw) if prefs_kw else False
        return (1 if pref_hit else 0, -1.0/p["distance_km"] if p["distance_km"]>0 else 0)

    out.sort(key=score, reverse=True)
    return out[:max_items]

# --------- Dual plan (shortest vs enhanced) ----------
def plan_dual_routes(origin: Tuple[float,float], dest: Tuple[float,float], profile: UserProfile, stations: List[Dict[str,Any]]) -> Optional[Dict[str,Any]]:
    shortest = route_osrm(origin, dest)
    if not shortest: return None
    enhanced = plan_trip_with_chargers(origin, dest, profile, stations)
    if not enhanced: enhanced = {"distance_km": shortest["distance_km"], "origin": origin, "destination": dest, "stops": [], "path": shortest["path"]}

    sightseeing: List[Dict[str,Any]] = []
    for stop in (enhanced.get("stops") or []):
        pt = (stop["latitude"], stop["longitude"])
        nearby = find_sightseeing_near(pt, profile.prefs, radius_km=3.0, max_items=2)
        for a in nearby: a["near_stop"] = stop["name"]
        sightseeing.extend(nearby)

    path = enhanced.get("path") or shortest.get("path") or []
    if path:
        cum = _cumdist(path); total = cum[-1] if cum else 0.0
        sample_km = 150.0
        s = sample_km
        while s < total - 20:
            _, pt = _pick_by_km(path, cum, s)
            sightseeing.extend(find_sightseeing_near(pt, profile.prefs, radius_km=5.0, max_items=1))
            s += sample_km

    enhanced["sightseeing"] = sightseeing[:10]
    return {"shortest": shortest, "enhanced": enhanced}

# ================== Printing ==================
def print_nearby(user_query: str, poi: str, coords: Optional[Tuple[float,float]], stations: List[Dict[str,Any]]) -> None:
    print(f"User Query: {user_query}")
    print(f"Identified Location: {poi if poi else 'N/A'}")
    print(f"Coordinates: ({coords[0]:.7f}, {coords[1]:.7f})\n" if coords else "Coordinates: (N/A)\n")
    if not stations:
        print(" Nearest Charging Stations:\n  - None found within your search area")
        return
    print(" Nearest Charging Stations:")
    for s in stations[:3]:
        print(f"  - {s['name']} ({s['distance_km']} km)")

def print_dual_trip(plans: Dict[str,Any]) -> None:
    shortest = plans["shortest"]; enhanced = plans["enhanced"]
    print("\n=== Route A: Shortest Distance ===")
    print(f"  Distance: {round(shortest['distance_km'],1)} km")
    print("  (No planned charging stops; fastest path)")

    print("\n=== Route B: Charging + Sightseeing ===")
    print(f"  Distance: {round(enhanced['distance_km'],1)} km (may be similar to A)")
    stops = enhanced.get("stops") or []
    if not stops:
        print("  Charging Stops: None required (within vehicle range).")
    else:
        print("  Charging Stops (order):")
        for i, s in enumerate(stops, 1):
            kw_val = s.get("kw")
            kw_txt = f", ~{int(kw_val)} kW" if isinstance(kw_val,(int,float)) and math.isfinite(kw_val) else ""
            print(f"   {i}. {s['name']} at ~{s.get('at_km','?')} km (detour {s['distance_km']} km{kw_txt})")

    sights = enhanced.get("sightseeing") or []
    if sights:
        print("  Sightseeing suggestions near route:")
        for a in sights:
            near = f" (near {a.get('near_stop')})" if a.get("near_stop") else ""
            print(f"   - {a['name']} [{a['type']}] ~{a['distance_km']} km off route{near}")
    print()

# ================== Multi-turn State ==================
@dataclass
class TripState:
    active: bool = False
    origin_text: Optional[str] = None
    dest_text: Optional[str] = None
    origin_coords: Optional[Tuple[float,float]] = None
    dest_coords: Optional[Tuple[float,float]] = None
    awaiting: Optional[str] = None  # "origin" | "dest"

def parse_from_to(utt: str) -> Tuple[Optional[str], Optional[str]]:
    m = re.search(r"\bfrom\s+(.+?)\s+(?:to|->)\s+(.+)$", utt, re.I)
    if m: return m.group(1).strip(), m.group(2).strip()
    m2 = re.search(r"\bto\s+(.+)$", utt, re.I)
    return (None, m2.group(1).strip()) if m2 else (None, None)

# ================== Main Loop ==================
def main():
    profile = load_profile(USER_ID); profile.prefs.consent = True; save_profile(profile)
    stations = load_stations(prefer_enriched=True)

    print("Ask: 'Where can I charge near Melbourne Airport?' or 'Plan a trip from Geelong to Sydney'.")
    print("Type 'prefs' to view prefs, 'reset' to clear history, 'exit' to quit.\n")

    trip = TripState()

    while True:
        try:
            user_msg = input("You: ").strip()
        except KeyboardInterrupt:
            print("\nBye!"); break
        if not user_msg: continue

        low = user_msg.lower()
        if low in {"exit","quit","q"}: print("Goodbye!"); break
        if low == "reset":
            reset_profile(USER_ID); profile = load_profile(USER_ID)
            print("[ok] Cleared history.\n"); continue
        if low == "prefs":
            print(json.dumps(asdict(profile.prefs), indent=2)); print(); continue

        # Trip planning
        start = any(k in low for k in ["plan a trip","trip plan","trip planning","route to","drive to"]) \
                or bool(re.search(r"\bfrom\b.+\bto\b", low)) or trip.active
        if start:
            trip.active = True
            o_txt, d_txt = parse_from_to(user_msg)
            if o_txt: trip.origin_text = o_txt
            if d_txt: trip.dest_text = d_txt

            if not trip.origin_text:
                print("Bot: Where are you starting from (Australia)?"); trip.awaiting = "origin"; print(); continue
            if trip.awaiting == "origin": trip.awaiting = None

            if not trip.dest_text:
                print("Bot: Great. Where do you want to go (Australia)?"); trip.awaiting = "dest"; print(); continue
            if trip.awaiting == "dest": trip.awaiting = None

            if trip.origin_text and not trip.origin_coords:
                oc = geocode_australia(trip.origin_text)
                if not oc:
                    print("Bot: I couldn't geocode your origin in Australia. Please rephrase.")
                    trip.origin_text = None; print(); continue
                trip.origin_coords = oc

            if trip.dest_text and not trip.dest_coords:
                dc = geocode_australia(trip.dest_text)
                if not dc:
                    print("Bot: I couldn't geocode your destination in Australia. Please rephrase.")
                    trip.dest_text = None; print(); continue
                trip.dest_coords = dc

            if trip.origin_coords and trip.dest_coords:
                plans = plan_dual_routes(trip.origin_coords, trip.dest_coords, profile, stations)
                if not plans:
                    print("Bot: Sorry, routing failed. Try again."); print(); continue
                print_dual_trip(plans)
                if profile.prefs.consent:
                    profile.history.recent_pois += [(trip.origin_text, time.time()), (trip.dest_text, time.time())]
                    profile.history.recent_pois = profile.history.recent_pois[-50:]; save_profile(profile)
                trip = TripState()
                continue

            print(); continue

        # Nearby single-turn (S1–S3)
        poi = extract_poi(user_msg) or user_msg
        coords = geocode_australia(poi)
        if not coords:
            print_nearby(user_msg, poi, None, []); print(); continue

        if profile.prefs.consent:
            profile.history.recent_pois.append((poi, time.time()))
            profile.history.recent_pois = profile.history.recent_pois[-50:]; save_profile(profile)

        top3 = personalize_rank(coords, profile, stations, want_k=3)
        if top3 and profile.prefs.consent:
            profile.history.recent_stations.append((top3[0]["name"], time.time()))
            profile.history.recent_stations = profile.history.recent_stations[-50:]; save_profile(profile)

        print_nearby(user_msg, poi, coords, top3); print()

if __name__ == "__main__":
    ensure_dataset()
    main()
