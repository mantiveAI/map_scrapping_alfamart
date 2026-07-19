"""
PART B – Task 1: Competitor QRIS Merchant Location Scraper
===========================================================
Target  : Alfamart store locator (alfamart.co.id)
          Alfamart is one of Indonesia's largest minimarket chains
          and a confirmed QRIS merchant network.
Strategy: The Alfamart store locator API is called by their website
          via a JSON endpoint. We replicate those requests per
          province/city to systematically collect all store locations.

Fallback: If the live endpoint is blocked, the script falls back to
          scraping the HTML store-locator page using BeautifulSoup.

Output  : alfamart_locations.csv
          scraping_summary.log

Author  : Fellix
"""

import requests
import pandas as pd
import time
import logging
import json
from datetime import datetime
from bs4 import BeautifulSoup

# ──────────────────────────────────────────────────────────
# Setup logging
# ──────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("scraping_summary.log"),
    ],
)
logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────
BASE_URL      = "https://www.alfamart.co.id"
LOCATOR_URL   = f"{BASE_URL}/store-locator"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/html, */*",
    "Referer": BASE_URL,
}

# Indonesian provinces for systematic coverage
PROVINCES = [
    "DKI Jakarta", "Jawa Barat", "Jawa Tengah", "Jawa Timur",
    "Banten", "DI Yogyakarta", "Bali", "Sumatera Utara",
    "Sumatera Selatan", "Lampung", "Kalimantan Barat",
    "Kalimantan Selatan", "Sulawesi Selatan", "Sulawesi Utara",
    "Nusa Tenggara Barat",
]

DELAY_BETWEEN_REQUESTS = 1.5   # seconds — respectful crawling


# ──────────────────────────────────────────────────────────
# Geocoding helper (OpenStreetMap Nominatim — free, no key)
# Used when lat/long is not in the source data
# ──────────────────────────────────────────────────────────
def geocode_address(address: str, city: str, province: str) -> tuple[float | None, float | None]:
    """
    Geocode an address using Nominatim (OSM).
    Returns (latitude, longitude) or (None, None) on failure.
    Rate-limited to 1 req/sec per OSM policy.
    """
    query = f"{address}, {city}, {province}, Indonesia"
    try:
        resp = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": query, "format": "json", "limit": 1},
            headers={"User-Agent": "QRISPayCompetitorAnalysis/1.0 contact@qrispay.id"},
            timeout=10,
        )
        resp.raise_for_status()
        results = resp.json()
        if results:
            return float(results[0]["lat"]), float(results[0]["lon"])
    except Exception as exc:
        logger.warning(f"Geocoding failed for '{address}': {exc}")
    return None, None


# ──────────────────────────────────────────────────────────
# PRIMARY scraper: attempt JSON API endpoint
# Alfamart's site loads store data via an AJAX endpoint.
# We inspect the network call pattern and replicate it.
# ──────────────────────────────────────────────────────────
def scrape_via_api(province: str, session: requests.Session) -> list[dict]:
    """
    Attempt to fetch stores for a province via Alfamart's internal API.
    Returns list of store dicts.
    """
    records = []
    # Alfamart store locator API (observed via browser DevTools)
    api_url = f"{BASE_URL}/api/stores"
    params  = {
        "province": province,
        "limit":    500,
        "offset":   0,
    }
    try:
        resp = session.get(api_url, params=params, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        stores = data.get("stores") or data.get("data") or []
        for s in stores:
            records.append({
                "store_name":       s.get("name", "Alfamart"),
                "full_address":     s.get("address", ""),
                "city":             s.get("city", ""),
                "province":         province,
                "latitude":         s.get("latitude") or s.get("lat"),
                "longitude":        s.get("longitude") or s.get("lng"),
                "store_type":       s.get("type", "minimarket"),
                "operating_hours":  s.get("opening_hours", ""),
                "source":           "api",
            })
        logger.info(f"[API] {province}: {len(records)} stores fetched")
    except Exception as exc:
        logger.warning(f"[API] Failed for {province}: {exc} — will try HTML fallback")
    return records


# ──────────────────────────────────────────────────────────
# FALLBACK scraper: parse HTML store locator page
# ──────────────────────────────────────────────────────────
def scrape_via_html(province: str, session: requests.Session) -> list[dict]:
    """
    Fallback: scrape the HTML store locator page for a given province.
    Alfamart renders store cards in <div class='store-item'> elements.
    """
    records = []
    params  = {"province": province}
    try:
        resp = session.get(LOCATOR_URL, params=params, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        # Common patterns for Alfamart store locator HTML structure
        store_cards = (
            soup.find_all("div", class_="store-item") or
            soup.find_all("div", class_="store-card") or
            soup.find_all("li",  class_="store-result")
        )

        if not store_cards:
            # Try JSON-LD embedded in page
            scripts = soup.find_all("script", type="application/ld+json")
            for s in scripts:
                try:
                    ld = json.loads(s.string or "{}")
                    if isinstance(ld, list):
                        for item in ld:
                            if item.get("@type") == "LocalBusiness":
                                geo = item.get("geo", {})
                                addr = item.get("address", {})
                                records.append({
                                    "store_name":      item.get("name", "Alfamart"),
                                    "full_address":    addr.get("streetAddress", ""),
                                    "city":            addr.get("addressLocality", ""),
                                    "province":        province,
                                    "latitude":        geo.get("latitude"),
                                    "longitude":       geo.get("longitude"),
                                    "store_type":      "minimarket",
                                    "operating_hours": item.get("openingHours", ""),
                                    "source":          "html_jsonld",
                                })
                except json.JSONDecodeError:
                    continue
        else:
            for card in store_cards:
                name    = card.find(class_="store-name")
                address = card.find(class_="store-address") or card.find("address")
                lat_el  = card.get("data-lat") or card.find(attrs={"data-lat": True})
                lng_el  = card.get("data-lng") or card.find(attrs={"data-lng": True})
                hours   = card.find(class_="store-hours") or card.find(class_="opening-hours")

                records.append({
                    "store_name":      name.get_text(strip=True)    if name    else "Alfamart",
                    "full_address":    address.get_text(strip=True) if address else "",
                    "city":            "",   # parsed below if address contains city
                    "province":        province,
                    "latitude":        float(lat_el) if lat_el and str(lat_el).replace(".", "").replace("-","").isdigit() else None,
                    "longitude":       float(lng_el) if lng_el and str(lng_el).replace(".", "").replace("-","").isdigit() else None,
                    "store_type":      "minimarket",
                    "operating_hours": hours.get_text(strip=True) if hours else "",
                    "source":          "html_scrape",
                })

        logger.info(f"[HTML] {province}: {len(records)} stores found")
    except Exception as exc:
        logger.error(f"[HTML] Total failure for {province}: {exc}")
    return records


# ──────────────────────────────────────────────────────────
# GEOCODING PASS: fill missing coordinates
# ──────────────────────────────────────────────────────────
def fill_missing_coordinates(df: pd.DataFrame) -> pd.DataFrame:
    """Geocode rows where lat or lng is missing."""
    missing_mask = df["latitude"].isna() | df["longitude"].isna()
    missing_count = missing_mask.sum()
    logger.info(f"Geocoding {missing_count} locations with missing coordinates...")

    for idx in df[missing_mask].index:
        row = df.loc[idx]
        lat, lng = geocode_address(
            row["full_address"], row.get("city", ""), row["province"]
        )
        df.at[idx, "latitude"]  = lat
        df.at[idx, "longitude"] = lng
        time.sleep(1.1)   # Nominatim rate limit: 1 req/sec

    return df


# ──────────────────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────────────────
def main():
    start_time = datetime.now()
    logger.info("=" * 60)
    logger.info("QRISPay Competitor Scraper — Alfamart Store Locations")
    logger.info("=" * 60)

    session  = requests.Session()
    all_data = []

    for province in PROVINCES:
        logger.info(f"Processing: {province}")

        # Try API first, fall back to HTML
        records = scrape_via_api(province, session)
        if not records:
            records = scrape_via_html(province, session)

        all_data.extend(records)
        time.sleep(DELAY_BETWEEN_REQUESTS)

    # ── Build DataFrame ──────────────────────────────────
    df = pd.DataFrame(all_data)

    if df.empty:
        logger.error("No data collected. Check network access or site structure changes.")
        return

    # De-duplicate by address
    df.drop_duplicates(subset=["full_address"], keep="first", inplace=True)
    df.reset_index(drop=True, inplace=True)

    # ── Geocode missing coordinates ──────────────────────
    df = fill_missing_coordinates(df)

    # ── Ensure clean column types ────────────────────────
    df["latitude"]  = pd.to_numeric(df["latitude"],  errors="coerce")
    df["longitude"] = pd.to_numeric(df["longitude"], errors="coerce")

    # ── Save CSV ─────────────────────────────────────────
    output_path = "alfamart_locations.csv"
    df.to_csv(output_path, index=False, encoding="utf-8-sig")

    # ── Summary log ──────────────────────────────────────
    end_time         = datetime.now()
    duration         = (end_time - start_time).total_seconds()
    total_locations  = len(df)
    missing_coords   = df["latitude"].isna().sum() + df["longitude"].isna().sum()

    summary = f"""
╔══════════════════════════════════════════════════════════╗
  SCRAPING SUMMARY
  Completed  : {end_time.strftime('%Y-%m-%d %H:%M:%S')}
  Duration   : {duration:.1f} seconds
  Provinces  : {len(PROVINCES)}
  Total stores found  : {total_locations:,}
  With coordinates    : {total_locations - df['latitude'].isna().sum():,}
  Missing coordinates : {df['latitude'].isna().sum():,}
  Output file : {output_path}
╚══════════════════════════════════════════════════════════╝
"""
    logger.info(summary)
    print(summary)

    return df


if __name__ == "__main__":
    df = main()
