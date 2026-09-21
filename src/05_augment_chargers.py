# TODO (Task 3): fetch external attributes and match to chargers. Placeholder only.
import argparse
from datetime import datetime, timezone
from difflib import SequenceMatcher
import hashlib
from html import unescape
import json
import math
import os
from pathlib import Path
import re
import sys

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

PROJECT_ROOT = Path(__file__).resolve().parent.parent
API_URL = "https://api.openchargemap.io/v3/poi/"
QUERY = {
    "output": "json", "countrycode": "AU", "maxresults": 10000,
    "compact": "false", "verbose": "false", "opendata": "true",
}
# OCM reference data defines CurrentTypeID 30 as DC.
DC_CURRENT_TYPE_ID = 30
MAX_DISTANCE_M = 150.0
EXACT_ADDRESS_DISTANCE_M = 500.0
CLOSE_DISTANCE_M = 30.0
ADDRESS_THRESHOLD = 0.50
OPERATOR_ALIASES = {
    "evienetworks": "evie", "evie": "evie",
    "teslainc": "tesla", "teslamotors": "tesla", "teslasupercharger": "tesla", "tesla": "tesla",
    # Exact OCM operator labels observed in the cached Australian response.
    # These normalize operator identity, not vehicle eligibility or access rules.
    "teslaincludingnontesla": "tesla",
    "teslateslaonlycharging": "tesla",
    "bppulseau": "bp",
    "bppulse": "bp", "bp": "bp", "ampolampcharge": "ampol", "ampcharge": "ampol",
    "nrmaelectric": "nrma", "nrma": "nrma",
}
ADDED_ATTRIBUTES = ["external_dc_plug_types", "external_usage_cost_text", "external_access_type", "external_bay_count"]


def text(value):
    """Convert a scalar to text while keeping missing values empty."""
    return "" if value is None or pd.isna(value) else str(value).strip()


def useful(value):
    value = text(value)
    return "" if value.lower() in {"unknown", "not specified", "n/a", "none", "null"} else value


def operator_key(value):
    key = re.sub(r"[^a-z0-9]", "", text(value).lower())
    if key in {"unknown", "notknown", "other", "notapplicable"}:
        return ""
    return OPERATOR_ALIASES.get(key, key)


def address_tokens(value):
    """Normalize common Australian street abbreviations for comparison."""
    aliases = {"rd": "road", "st": "street", "ave": "avenue", "dr": "drive",
               "hwy": "highway", "pde": "parade", "blvd": "boulevard"}
    words = re.findall(r"[a-z0-9]+", text(value).lower())
    return {aliases.get(word, word) for word in words
            if word not in {"nsw", "australia", "new", "south", "wales"}}


def address_similarity(left, right):
    a, b = address_tokens(left), address_tokens(right)
    return len(a & b) / len(a | b) if a and b else 0.0


def postcode(value):
    match = re.fullmatch(r"(\d{4})(?:\.0)?", text(value))
    return match.group(1) if match else ""


def distance_m(lat1, lon1, lat2, lon2):
    """Haversine great-circle distance; enough precision for candidate screening."""
    lat1, lat2 = math.radians(lat1), math.radians(lat2)
    dlat, dlon = lat2 - lat1, math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 6371008.8 * 2 * math.asin(math.sqrt(min(1.0, max(0.0, a))))


def validate_payload(payload):
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("Unrecognized OCM cache format. Use --refresh to replace it.")
    if payload.get("params") != QUERY or payload.get("api_url") != API_URL:
        raise ValueError("Cache query differs from this script. Use --refresh.")
    pois = payload.get("pois")
    if not isinstance(pois, list) or not pois:
        raise ValueError("OCM response must be a non-empty list of locations.")
    if len(pois) >= QUERY["maxresults"]:
        raise ValueError("OCM response reached maxresults; it may be truncated. Add pagination before use.")
    ids = [p.get("ID") for p in pois if isinstance(p, dict)]
    if len(ids) != len(pois) or None in ids or len(ids) != len(set(ids)):
        raise ValueError("OCM response has missing or duplicate location IDs.")
    return payload


def load_ocm(cache_file, refresh=False):
    """Fetch once, retain the raw JSON with provenance, then work offline."""
    if cache_file.exists() and not refresh:
        print(f"Using cached OCM data: {cache_file}")
        return validate_payload(json.loads(cache_file.read_text(encoding="utf-8")))
    api_key = os.environ.get("OCM_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError(
            "OCM_API_KEY is not set and no usable cache was selected. "
            "Register an application at https://openchargemap.org/site/profile/applications "
            "and set $env:OCM_API_KEY in PowerShell. See docs/05_augmentation_guide.md."
        )
    retry = Retry(total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504],
                  allowed_methods=["GET"], respect_retry_after_header=True)
    with requests.Session() as session:
        session.mount("https://", HTTPAdapter(max_retries=retry))
        response = session.get(
            API_URL, params=QUERY,
            headers={"X-API-Key": api_key, "User-Agent": "COMP5339-Student-Project/1.0",
                     "Accept": "application/json"}, timeout=(15, 120),
        )
        response.raise_for_status()
        payload = validate_payload({
            "schema_version": 1, "api_url": API_URL, "params": QUERY,
            "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
            "pois": response.json(),
        })
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    temporary = cache_file.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(cache_file)
    print(f"Saved {len(payload['pois'])} external locations to {cache_file}")
    return payload


def flatten_ocm(pois):
    """Keep located DC sites; retain external wording rather than invent values."""
    stations = []
    for poi in pois:
        address = poi.get("AddressInfo") or {}
        country = address.get("Country") or {}
        if country.get("ISOCode") != "AU":
            continue
        connections = [c for c in (poi.get("Connections") or [])
                       if c.get("CurrentTypeID") == DC_CURRENT_TYPE_ID
                       or (c.get("CurrentType") or {}).get("ID") == DC_CURRENT_TYPE_ID]
        if not connections:
            continue
        try:
            lat, lon = float(address["Latitude"]), float(address["Longitude"])
        except (KeyError, TypeError, ValueError):
            continue
        if not (math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180):
            continue
        provider = poi.get("DataProvider") or {}
        plugs = {useful((c.get("ConnectionType") or {}).get("Title")) for c in connections}
        stations.append({
            "external_id": poi["ID"], "latitude": lat, "longitude": lon,
            "external_title": text(address.get("Title")),
            "external_address": ", ".join(text(address.get(k)) for k in
                                      ["AddressLine1", "AddressLine2", "Town", "Postcode"]
                                      if text(address.get(k))),
            "external_postcode": postcode(address.get("Postcode")),
            "external_operator": useful((poi.get("OperatorInfo") or {}).get("Title")),
            "external_dc_plug_types": "; ".join(sorted(plugs - {""})),
            "external_usage_cost_text": useful(poi.get("UsageCost")),
            "external_access_type": useful((poi.get("UsageType") or {}).get("Title")),
            "external_number_of_points": poi.get("NumberOfPoints"),
            "external_status": useful((poi.get("StatusType") or {}).get("Title")),
            "external_last_verified": text(poi.get("DateLastVerified")),
            "external_provider": text(provider.get("Title")),
            "external_provider_license": text(provider.get("License")),
            "external_source_url": f"https://openchargemap.org/site/poi/details/{poi['ID']}",
        })
    return stations


def exact_address_key(value):
    """Preserve token order and distinguish unit numbers from number ranges."""
    aliases = {"rd": "road", "st": "street", "ave": "avenue", "dr": "drive",
               "hwy": "highway", "pde": "parade", "blvd": "boulevard"}
    words = re.findall(r"[a-z]+|\d+(?:[-/]\d+)?[a-z]?", text(value).lower())
    return tuple(aliases.get(w, w) for w in words
                 if w not in {"nsw", "australia", "new", "south", "wales"})


def find_candidates(row, stations):
    """Distance narrows the search; identity evidence determines acceptance."""
    candidates = []
    for station in stations:
        distance = distance_m(float(row["Latitude"]), float(row["Longitude"]),
                              station["latitude"], station["longitude"])
        if distance > EXACT_ADDRESS_DISTANCE_M:
            continue
        source_operator = operator_key(row["Operator"])
        external_operator = operator_key(station["external_operator"])
        operator_match = bool(source_operator and source_operator == external_operator)
        address_score = address_similarity(row["Station_address"], station["external_address"])
        source_name, external_name = text(row.get("Station_name")), station["external_title"]
        name_score = (SequenceMatcher(None, source_name.lower(), external_name.lower()).ratio()
                      if source_name and external_name else 0.0)
        source_postcode = postcode(row.get("PCODE"))
        postcode_conflict = bool(source_postcode and station["external_postcode"]
                                 and source_postcode != station["external_postcode"])
        identity_evidence = distance <= CLOSE_DISTANCE_M or address_score >= ADDRESS_THRESHOLD or name_score >= 0.8
        source_address = exact_address_key(row["Station_address"])
        external_address = exact_address_key(station["external_address"])
        exact_address = bool(
            len(source_address) >= 4 and source_address == external_address
            and any(re.search(r"\d", word) for word in source_address[:-1])
            and source_postcode and source_postcode == station["external_postcode"]
            and source_address[-1] == source_postcode
        )
        nearby = distance <= MAX_DISTANCE_M and identity_evidence
        eligible = operator_match and (nearby or exact_address) and not postcode_conflict
        method = "nearby_identity" if nearby else "exact_address_within_500m" if exact_address else ""
        candidates.append({
            "external_id": station["external_id"], "distance_m": round(distance, 2),
            "operator_match": operator_match, "address_similarity": round(address_score, 3),
            "name_similarity": round(name_score, 3), "postcode_conflict": postcode_conflict,
            "eligible": eligible, "match_method": method, "external_title": station["external_title"],
            "external_address": station["external_address"], "external_operator": station["external_operator"],
            "external_source_url": station["external_source_url"],
        })
    return sorted(candidates, key=lambda c: c["distance_m"])


def augment(ev, stations):
    """Preserve every input row; keep ambiguous matches for human review."""
    required = {"Charger_Type", "Latitude", "Longitude", "Operator", "Station_address"}
    if required - set(ev.columns):
        raise ValueError(f"Missing input columns: {sorted(required - set(ev.columns))}")
    out = ev.reset_index(drop=True).copy()
    if "augmentation_status" in out or "source_record_id" in out:
        raise ValueError("Input is already augmented. Use the output of step 04.")
    out["source_record_id"] = [f"ev_{i:05d}" for i in range(1, len(out) + 1)]
    dc = out["Charger_Type"].astype("string").str.strip().str.upper().eq("DC").fillna(False)
    for column in ["Latitude", "Longitude"]:
        numeric = pd.to_numeric(out.loc[dc, column], errors="coerce")
        if not numeric.map(lambda v: pd.notna(v) and math.isfinite(v)).all():
            raise ValueError("DC input has unusable coordinates. Recheck step 03.")
    if not (out.loc[dc, "Latitude"].astype(float).between(-90, 90).all()
            and out.loc[dc, "Longitude"].astype(float).between(-180, 180).all()):
        raise ValueError("DC coordinates are out of range.")
    out["dc_location_key"] = pd.Series(pd.NA, index=out.index, dtype="string")
    out.loc[dc, "dc_location_key"] = out.loc[dc].apply(
        lambda r: f"{float(r['Latitude']):.6f},{float(r['Longitude']):.6f}", axis=1)
    out["augmentation_status"] = "not_target"
    out.loc[dc, "augmentation_status"] = "unmatched"
    out["match_distance_m"] = float("nan")
    out["match_method"] = ""
    external_columns = ["external_id", "external_title", "external_address", "external_operator", *ADDED_ATTRIBUTES,
                        "external_number_of_points", "external_status", "external_last_verified", "external_provider",
                        "external_provider_license", "external_source_url"]
    for column in external_columns:
        out[column] = pd.Series([None] * len(out), dtype="object")
    out["augmented"] = False
    audit = []
    proposals = {}
    for idx, row in out.loc[dc].iterrows():
        candidates = find_candidates(row, stations)
        for candidate in candidates:
            audit.append({"source_record_id": row["source_record_id"],
                          "source_address": text(row["Station_address"]), **candidate})
        eligible = [c for c in candidates if c["eligible"]]
        if len(eligible) == 1:
            proposals[idx] = eligible[0]
        elif candidates:
            out.at[idx, "augmentation_status"] = "review_ambiguous" if len(eligible) > 1 else "review_identity"
    # Exact-address fallback cannot claim a site already assigned by the primary
    # nearby rule. Multiple nearby proposals still all require review.
    primary_locations = {}
    for idx, candidate in proposals.items():
        if candidate["match_method"] == "nearby_identity":
            primary_locations.setdefault(candidate["external_id"], set()).add(out.at[idx, "dc_location_key"])
    for idx, candidate in list(proposals.items()):
        occupied = primary_locations.get(candidate["external_id"], set())
        if (candidate["match_method"] == "exact_address_within_500m"
                and occupied - {out.at[idx, "dc_location_key"]}):
            out.at[idx, "augmentation_status"] = "review_shared_external"
            del proposals[idx]
    # Do not attach one external location to multiple distinct source coordinates silently.
    assigned_locations = {}
    for idx, candidate in proposals.items():
        assigned_locations.setdefault(candidate["external_id"], set()).add(out.at[idx, "dc_location_key"])
    by_id = {s["external_id"]: s for s in stations}
    for idx, candidate in proposals.items():
        if len(assigned_locations[candidate["external_id"]]) > 1:
            out.at[idx, "augmentation_status"] = "review_shared_external"
            continue
        station = by_id[candidate["external_id"]]
        for column in external_columns:
            out.at[idx, column] = station.get(column)
        out.at[idx, "match_distance_m"] = candidate["distance_m"]
        out.at[idx, "match_method"] = candidate["match_method"]
        has_new_attribute = any(useful(station.get(column)) for column in ADDED_ATTRIBUTES)
        out.at[idx, "augmented"] = has_new_attribute
        out.at[idx, "augmentation_status"] = "matched" if has_new_attribute else "matched_no_new_attribute"
    audit_columns = ["source_record_id", "source_address", "external_id", "distance_m", "operator_match",
                     "address_similarity", "name_similarity", "postcode_conflict", "eligible", "match_method",
                     "external_title", "external_address", "external_operator", "external_source_url"]
    audit_df = pd.DataFrame(audit, columns=audit_columns)
    locations = out.loc[dc].groupby("dc_location_key")["augmented"].any()
    summary = {
        "input_records": len(out), "dc_records": int(dc.sum()),
        "augmented_dc_records": int(out.loc[dc, "augmented"].sum()),
        "dc_location_coordinate_groups": len(locations),
        "augmented_location_coordinate_groups": int(locations.sum()),
        "location_coverage_percent": round(float(locations.mean()) * 100, 2) if len(locations) else 0,
        "target_met": bool(len(locations) and locations.mean() >= 0.5),
        "status_counts": out.loc[dc, "augmentation_status"].value_counts().to_dict(),
        "location_definition": "Coordinates rounded to 6 decimal places; a location proxy, not verified unique physical sites.",
        "coverage_definition": "Unique coordinate groups with at least one accepted DC record receiving a non-empty new attribute.",
        "counted_new_attributes": ADDED_ATTRIBUTES,
        "matching_rules": {"max_distance_m": MAX_DISTANCE_M, "exact_address_max_distance_m": EXACT_ADDRESS_DISTANCE_M, "close_distance_m": CLOSE_DISTANCE_M,
                           "address_jaccard_min": ADDRESS_THRESHOLD, "name_similarity_min": 0.8,
                           "operator_must_match": True, "postcode_conflicts_block_match": True,
                           "exact_address_fallback": "Identical ordered complete address with street number and postcode; matching postcode fields; cannot displace a primary nearby match."},
        "limitations": ["Automatic matches need spot checks; thresholds are not identity guarantees.",
                        "No fuzzy merging of source locations; inspect nearby and shared-coordinate records.",
                        "External data is retrieved now, not a reconstruction of December 2025.",
                        "UsageCost is unparsed source text, not a comparable numeric tariff.",
                        "Unknown DC current types are excluded; true coverage may be underestimated."],
    }
    return out, audit_df, summary


# These are station datasets, not network-wide contact or tariff assumptions.
EVIE_PAGE = "https://evie.com.au/find-a-charger/"
EVIE_URL = "https://evie.com.au/wp-admin/admin-ajax.php"
EVIE_PARAMS = {"action": "store_search", "lat": -33.8688, "lng": 151.2093,
               "max_results": 1000, "search_radius": 2000, "autoload": 1}
OSM_URL = "https://overpass-api.de/api/interpreter"
OSM_QUERY = '[out:json][timeout:60];nwr["amenity"="charging_station"](-38,140,-28,154);out center tags;'
AMPOL_PAGE = "https://ampcharge.ampol.com.au/find-a-charging-station"
AMPOL_URL = "https://digital-api.ampol.com.au/siteservice/Service"
AMPOL_PARAMS = {
    "$filter": "EVFastCharging eq true",
    "$select": "LocationID,DisplayName,Brand,Latitude,Longitude,Address,Suburb,State,Postcode,EVFastCharging,EVChargingData",
    "$top": 1000,
}
DC_STANDARDS = {"IEC_62196_T2_COMBO": "CCS Type 2", "CHADEMO": "CHAdeMO",
                "IEC_62196_T1_COMBO": "CCS Type 1"}
DC_SOCKETS = {"socket:type2_combo": "CCS Type 2", "socket:chademo": "CHAdeMO",
              "socket:type1_combo": "CCS Type 1",
              "socket:tesla_supercharger": "Tesla Supercharger (OSM label)",
              "socket:tesla_supercharger_ccs": "Tesla Supercharger CCS (OSM label)"}


def validate_public(payload, source):
    """Reject empty, duplicate, mismatched or evidently incomplete snapshots."""
    if not isinstance(payload, dict) or not payload.get("retrieved_at_utc"):
        raise ValueError(f"Missing provenance in {source} cache.")
    if source == "evie":
        if payload.get("url") != EVIE_URL or payload.get("params") != EVIE_PARAMS:
            raise ValueError("Evie cache query mismatch.")
        sites = payload.get("sites")
        if not isinstance(sites, list) or not sites:
            raise ValueError("Empty Evie response.")
        totals = {int(s["evie_total_results"]) for s in sites if "evie_total_results" in s}
        if totals != {len(sites)} or len(sites) >= EVIE_PARAMS["max_results"]:
            raise ValueError("Evie result count is missing or incomplete.")
        ids = [s.get("id") for s in sites]
    elif source == "osm":
        if payload.get("url") != OSM_URL or payload.get("query") != OSM_QUERY:
            raise ValueError("OSM cache query mismatch.")
        response = payload.get("response") or {}
        if response.get("remark"):
            raise ValueError("Overpass returned a warning/error; refuse partial data.")
        sites = response.get("elements")
        if not isinstance(sites, list) or not sites:
            raise ValueError("Empty Overpass response.")
        ids = [f"{s.get('type')}/{s.get('id')}" if s.get("id") is not None else None for s in sites]
    elif source == "ampol":
        if payload.get("url") != AMPOL_URL or payload.get("params") != AMPOL_PARAMS:
            raise ValueError("Ampol cache query mismatch.")
        response = payload.get("response") or {}
        sites = response.get("value")
        if not isinstance(sites, list) or not sites:
            raise ValueError("Empty Ampol response.")
        if response.get("@odata.nextLink") or len(sites) >= AMPOL_PARAMS["$top"]:
            raise ValueError("Ampol response may be truncated; pagination is required.")
        ids = [s.get("LocationID") for s in sites]
    else:
        raise ValueError(f"Unknown source: {source}")
    if None in ids or "" in ids or len(set(ids)) != len(ids):
        raise ValueError(f"Missing or duplicate {source} identifiers.")
    return payload


def load_public(cache, source, refresh=False):
    """Fetch the same public data used by official maps, or an open Overpass API."""
    if cache.exists() and not refresh:
        print(f"Using cached {source} data: {cache}")
        return validate_public(json.loads(cache.read_text(encoding="utf-8")), source)
    headers = {"User-Agent": "COMP5339-Student-Project/1.0"}
    with requests.Session() as session:
        session.headers.update(headers)
        if source == "evie":
            response = session.get(EVIE_URL, params=EVIE_PARAMS, timeout=(15, 90))
            response.raise_for_status()
            payload = {"url": EVIE_URL, "params": EVIE_PARAMS, "sites": response.json()}
        elif source == "osm":
            response = session.post(OSM_URL, data={"data": OSM_QUERY}, timeout=(15, 90))
            response.raise_for_status()
            payload = {"url": OSM_URL, "query": OSM_QUERY, "response": response.json()}
        elif source == "ampol":
            # The public locator publishes a client subscription token in its JS.
            # Discover it each time, just as the map does; never log or cache it.
            page = session.get(AMPOL_PAGE, timeout=(15, 60))
            page.raise_for_status()
            scripts = re.findall(r'<script[^>]+src="([^"]+)"', page.text)
            route = next((s for s in scripts if s.startswith("/_next/static/") and "route" in s), None)
            if not route:
                raise ValueError("Ampol locator script changed; inspect the official page.")
            script = session.get("https://ampcharge.ampol.com.au" + route, timeout=(15, 60))
            script.raise_for_status()
            token = re.search(r'"Ocp-Apim-Subscription-Key":"([^"]+)"', script.text)
            if not token:
                raise ValueError("Ampol public locator configuration changed.")
            response = session.get(AMPOL_URL, params=AMPOL_PARAMS,
                                   headers={"Ocp-Apim-Subscription-Key": token.group(1)},
                                   timeout=(15, 90))
            response.raise_for_status()
            payload = {"url": AMPOL_URL, "params": AMPOL_PARAMS, "source_page": AMPOL_PAGE,
                       "response": response.json()}
        else:
            raise ValueError(f"Unknown source: {source}")
    payload["retrieved_at_utc"] = datetime.now(timezone.utc).isoformat()
    validate_public(payload, source)
    cache.parent.mkdir(parents=True, exist_ok=True)
    temporary = cache.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(cache)
    return payload


def site_record(identifier, lat, lon, title, address, code, operator, plugs, url,
                price="", access="", bays=None, provider="", license_text=""):
    try:
        lat, lon = float(lat), float(lon)
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(lat) and math.isfinite(lon) and -44 <= lat <= -10 and 112 <= lon <= 154):
        return None
    return {
        "external_id": str(identifier), "latitude": lat, "longitude": lon,
        "external_title": unescape(text(title)), "external_address": unescape(text(address)),
        "external_postcode": postcode(code), "external_operator": operator,
        "external_dc_plug_types": plugs, "external_usage_cost_text": useful(price),
        "external_access_type": useful(access), "external_bay_count": bays,
        "external_source_url": url, "external_provider": provider,
        "external_provider_license": license_text,
    }


def flatten_evie(sites):
    output = []
    for site in sites:
        if site.get("country") not in {"AUS", "AU"}:
            continue
        chargers = json.loads(site.get("evie_chargers") or "[]")
        connectors = [c for charger in chargers for c in charger.get("connectors", [])
                      if c.get("standard") in DC_STANDARDS]
        if not connectors:
            continue
        station = site_record(
            site["id"], site.get("lat"), site.get("lng"), site.get("store"),
            " ".join(text(site.get(k)) for k in ["address", "address2", "city", "zip"]),
            site.get("zip"), "Evie Networks",
            "; ".join(sorted({DC_STANDARDS[c["standard"]] for c in connectors})), EVIE_PAGE,
            price="; ".join(sorted({useful(c.get("price")) for c in connectors} - {""})),
            provider="Evie Networks official station map",
            license_text="Official website; no open-data licence asserted",
        )
        if station:
            output.append(station)
    return output


def socket_present(value):
    """OSM positive socket counts/yes indicate presence; unknown does not."""
    value = text(value).lower()
    if value == "yes":
        return True
    return bool(re.fullmatch(r"\d+", value) and int(value) > 0)


def flatten_osm(elements):
    output = []
    for element in elements:
        tags = element.get("tags") or {}
        if tags.get("amenity") != "charging_station":
            continue
        plugs = [label for key, label in DC_SOCKETS.items() if socket_present(tags.get(key))]
        if not plugs:
            continue
        coordinates = element.get("center") or element
        identifier = f"{element['type']}/{element['id']}"
        station = site_record(
            identifier, coordinates.get("lat"), coordinates.get("lon"), tags.get("name"),
            " ".join(text(tags.get("addr:" + k)) for k in ["housenumber", "street", "city", "postcode"]),
            tags.get("addr:postcode"), tags.get("operator") or tags.get("brand") or tags.get("network", ""),
            "; ".join(sorted(set(plugs))), f"https://www.openstreetmap.org/{identifier}",
            price=tags.get("charge"), access=tags.get("access"),
            provider="OpenStreetMap contributors", license_text="ODbL 1.0; https://www.openstreetmap.org/copyright",
        )
        if station:
            output.append(station)
    return output


def flatten_ampol(sites):
    output = []
    for site in sites:
        connectors = [c for c in (site.get("EVChargingData") or [])
                      if text(c.get("EVPlugType")).lower() in {"ccs 2", "ccs2", "chademo", "ccs 1"}]
        if not site.get("EVFastCharging") or not connectors:
            continue
        bays = {useful(c.get("ChargingBayNo")) for c in connectors} - {""}
        station = site_record(
            site["LocationID"], site.get("Latitude"), site.get("Longitude"), site.get("DisplayName"),
            " ".join(text(site.get(k)) for k in ["Address", "Suburb", "Postcode"]),
            site.get("Postcode"), "Ampol",
            "; ".join(sorted({text(c["EVPlugType"]) for c in connectors})), AMPOL_PAGE,
            bays=len(bays) if bays else None, provider="Ampol official station locator",
            license_text="Official website; no open-data licence asserted",
        )
        if station:
            output.append(station)
    return output


def augment_sources(ev, sources):
    """Match independently, prefer official sources, and count the row union once."""
    out = None
    audits, matches, summaries = [], [], {}
    # Preserve each accepted source in a long table. Wide attributes come from
    # one chosen site record, so conflicting tariffs are never silently merged.
    for name, stations in sources.items():
        result, audit, summary = augment(ev, stations)
        summaries[name] = summary
        audit.insert(0, "source", name)
        audits.append(audit)
        good = result.loc[result["augmented"]].copy()
        good.insert(0, "augmentation_source", name)
        matches.append(good)
        if out is None:
            out = result.copy()
            out["augmentation_source"] = ""
            out.loc[out["augmented"], "augmentation_source"] = name
            out["matched_sources"] = ""
        else:
            use = ~out["augmented"] & result["augmented"]
            for col in result.columns:
                if col not in ev.columns:
                    out.loc[use, col] = result.loc[use, col]
            out.loc[use, "augmentation_source"] = name
        out[f"{name}_match_status"] = result["augmentation_status"]
        out[f"{name}_augmented"] = result["augmented"]
        for idx in good.index:
            previous = out.at[idx, "matched_sources"]
            out.at[idx, "matched_sources"] = f"{previous}; {name}" if previous else name
    if out is None:
        raise ValueError("At least one external source is required.")
    dc = out["Charger_Type"].astype("string").str.strip().str.upper().eq("DC").fillna(False)
    out.loc[dc & ~out["augmented"], "augmentation_status"] = "review_unresolved"
    groups = out.loc[dc].groupby("dc_location_key")["augmented"].any()
    summary = {
        "input_records": len(out), "dc_records": int(dc.sum()),
        "augmented_dc_records": int(out.loc[dc, "augmented"].sum()),
        "record_coverage_percent": round(float(out.loc[dc, "augmented"].mean()) * 100, 2) if dc.any() else 0.0,
        "dc_location_coordinate_groups": len(groups),
        "augmented_location_coordinate_groups": int(groups.sum()),
        "location_coverage_percent": round(float(groups.mean()) * 100, 2) if len(groups) else 0.0,
        "target_met": bool(len(groups) and groups.mean() >= 0.5),
        "coverage_definition": "DC coordinate groups with at least one accepted site match and a non-empty new site attribute; union across sources.",
        "location_definition": "Coordinates rounded to 6 decimals; proxy for physical locations, with no denominator reduction.",
        "counted_new_attributes": ADDED_ATTRIBUTES,
        "source_priority": list(sources),
        "sources": summaries,
        "status_counts": out.loc[dc, "augmentation_status"].value_counts().to_dict(),
        "extended_address_match_records": int((out["match_method"] == "exact_address_within_500m").sum()),
        "limitations": [
            "Coordinates are a location proxy, not a verified unique physical-site inventory.",
            "Heuristic matches require spot checks; unresolved and ambiguous records remain unaugmented.",
            "External snapshots are current retrievals, not reconstructed December 2025 observations.",
            "Raw prices may include time windows and surcharges; missing price does not mean free.",
            "No operator-wide contact details, assumed connector types or inferred prices count.",
            "OSM way/relation centres can differ from actual charger coordinates.",
        ],
    }
    return out, pd.concat(audits, ignore_index=True), pd.concat(matches, ignore_index=True), summary


def main(argv=None):
    parser = argparse.ArgumentParser(description="Match DC chargers to site-level external attributes.")
    parser.add_argument("--input", type=Path, default=PROJECT_ROOT / "data/processed/ev_with_sa4.csv")
    parser.add_argument("--cache", type=Path, default=PROJECT_ROOT / "data/external/ocm_au.json")
    parser.add_argument("--external-dir", type=Path, default=PROJECT_ROOT / "data/external")
    parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "data/external")
    parser.add_argument("--refresh", action="store_true", help="Refresh all four external snapshots; needs OCM_API_KEY")
    parser.add_argument("--ocm-only", action="store_true", help="Run the OCM baseline only")
    args = parser.parse_args(argv)
    ev = pd.read_csv(args.input)
    print(f"Loaded {len(ev)} input records.")
    payloads = {"ocm": load_ocm(args.cache, args.refresh)}
    cache_paths = {"ocm": args.cache}
    sources = {}
    if not args.ocm_only:
        adapters = {"evie": flatten_evie, "ampol": flatten_ampol, "osm": flatten_osm}
        for name, filename in [("evie", "evie_sites.json"), ("ampol", "ampol_sites.json"), ("osm", "osm_chargers.json")]:
            path = args.external_dir / filename
            payloads[name] = load_public(path, name, args.refresh)
            cache_paths[name] = path
            payload = payloads[name]
            items = payload["sites"] if name == "evie" else payload["response"]["value" if name == "ampol" else "elements"]
            sources[name] = adapters[name](items)
    # Official operators first, OCM next, OSM last. All matches remain auditable.
    osm = sources.pop("osm", None)
    sources["ocm"] = flatten_ocm(payloads["ocm"]["pois"])
    if osm is not None:
        sources["osm"] = osm
    for name, stations in sources.items():
        if not stations:
            raise ValueError(f"No usable DC sites in {name}; inspect the source schema.")
        ids = [s["external_id"] for s in stations]
        if len(ids) != len(set(ids)):
            raise ValueError(f"Duplicate normalized site IDs in {name}.")
    out, audit, matches, summary = augment_sources(ev, sources)
    summary["run_at_utc"] = datetime.now(timezone.utc).isoformat()
    summary["input_sha256"] = hashlib.sha256(args.input.read_bytes()).hexdigest()
    for name, payload in payloads.items():
        info = summary["sources"][name]
        info["retrieved_at_utc"] = payload["retrieved_at_utc"]
        info["cache_file"] = str(cache_paths[name])
        info["cache_sha256"] = hashlib.sha256(cache_paths[name].read_bytes()).hexdigest()
        info["external_dc_sites"] = len(sources[name])
    args.output_dir.mkdir(parents=True, exist_ok=True)
    out.to_csv(args.output_dir / "ev_augmented.csv", index=False)
    audit.to_csv(args.output_dir / "augmentation_candidates.csv", index=False)
    matches.to_csv(args.output_dir / "augmentation_matches.csv", index=False)
    dc = out["Charger_Type"].astype("string").str.strip().str.upper().eq("DC").fillna(False)
    review = out.loc[dc & ~out["augmented"]]
    review.to_csv(args.output_dir / "augmentation_review.csv", index=False)
    (args.output_dir / "augmentation_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"DC records: {summary['dc_records']}; augmented: {summary['augmented_dc_records']}")
    print(f"Site attribute coverage: {summary['augmented_location_coordinate_groups']}/"
          f"{summary['dc_location_coordinate_groups']} = {summary['location_coverage_percent']:.2f}%")
    print(f"Unresolved DC records: {len(review)}. Outputs: {args.output_dir}")
    if not summary["target_met"]:
        print("WARNING: 50% target not met. Inspect sources and unresolved matches; do not fabricate attributes.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, requests.RequestException) as exc:
        print(f"Step 05 failed: {exc}", file=sys.stderr)
        raise SystemExit(1)

