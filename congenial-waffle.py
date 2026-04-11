#!/usr/bin/env python3
"""
congenial-waffle

A Google Maps lead generation tool that searches for businesses, extracts
contact information, and scrapes emails and social media links from websites.
"""

import requests
import pandas as pd
import re
import time
import math
import os
import sys
import logging
import warnings
import argparse
import threading
import queue
import json
import glob

from datetime import datetime
from urllib.parse import urljoin, urlparse
from pathlib import Path
from bs4 import BeautifulSoup

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import (
    StaleElementReferenceException,
    NoSuchElementException,
    ElementClickInterceptedException,
)

warnings.filterwarnings("ignore")


CONFIG = {
    "max_leads_per_search": 1000,
    "save_every": 30,
    "scroll_pause": 2.5,
    "click_pause": 2.0,
    "email_scrape_pause": 0.5,
    "max_consecutive_failures": 10,
    "request_timeout": 10,
    "max_browsers": 5,
}

SOCIAL_MEDIA_PATTERNS = {
    "facebook": [
        r"(?:https?://)?(?:www\.)?facebook\.com/[\w\.\-]+/?",
        r"(?:https?://)?(?:www\.)?fb\.com/[\w\.\-]+/?"
    ],
    "instagram": [r"(?:https?://)?(?:www\.)?instagram\.com/[\w\.\-]+/?"],
    "twitter": [
        r"(?:https?://)?(?:www\.)?twitter\.com/[\w\.\-]+/?",
        r"(?:https?://)?(?:www\.)?x\.com/[\w\.\-]+/?"
    ],
    "linkedin": [r"(?:https?://)?(?:www\.)?linkedin\.com/(?:company|in)/[\w\.\-]+/?"],
    "youtube": [r"(?:https?://)?(?:www\.)?youtube\.com/(?:c/|channel/|user/|@)?[\w\.\-]+/?"],
    "tiktok": [r"(?:https?://)?(?:www\.)?tiktok\.com/@[\w\.\-]+/?"],
}

INVALID_NAMES = {
    "results", "map", "search", "filter", "sort", "menu", "directions",
    "share", "save", "nearby", "photos", "reviews", "overview",
    "website", "call", "images", "street view", "satellite",
    "résultats", "carte", "rechercher", "resultados", "mapa"
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.FileHandler("congenial-waffle.log")]
)
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# CONFIG FILE
# ─────────────────────────────────────────────

CONFIG_FILE_TEMPLATE = """\
# congenial-waffle Config File
# Lines starting with # are comments
# Remove the # to activate a setting

# ── SEARCH ──────────────────────────────────
categories = bar, cafe, restaurant
regions = Paris France, Lyon France
leads_per_search = 20
use_gps = true
num_browsers = 1

# ── WORKING DIRECTORY ────────────────────────
# All output files will be saved here
# The directory will be created if it does not exist
working_directory = /path/to/output/folder

# ── OUTPUT ──────────────────────────────────
# Just the filename without extension
output_name = leads

# ── OUTPUT FORMATS ───────────────────────────
save_csv  = true
save_xlsx = true
save_json = true
"""


def generate_config_template(path="config.txt"):
    """Write a template config file"""
    with open(path, "w") as f:
        f.write(CONFIG_FILE_TEMPLATE)
    print(f"  Config template written to: {path}")
    print(f"  Edit it then run: python congenial-waffle.py --config {path}")


def load_config_file(path):
    """Load settings from a plain text or JSON config file"""
    if not os.path.exists(path):
        print(f"  Config file not found: {path}")
        return None

    settings = {}

    if path.endswith(".json"):
        try:
            with open(path, "r", encoding="utf-8") as f:
                settings = json.load(f)
            print(f"  Loaded config from: {path}")
            return settings
        except Exception as e:
            print(f"  Error reading JSON config: {e}")
            return None

    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()

                if not line or line.startswith("#"):
                    continue

                if "=" not in line:
                    continue

                key, _, value = line.partition("=")
                key = key.strip().lower().replace("-", "_").replace(" ", "_")
                value = value.strip()

                if not key or not value:
                    continue

                settings[key] = value

        print(f"  Loaded config from: {path}")
        return settings

    except Exception as e:
        print(f"  Error reading config file: {e}")
        return None


def parse_bool(value, default=True):
    """Parse a boolean value from string"""
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("true", "1", "yes", "on")


def parse_config_settings(settings):
    """Convert raw config file values into typed Python values"""
    if not settings:
        return {}

    parsed = {}

    if "categories" in settings:
        val = settings["categories"]
        parsed["categories"] = val if isinstance(val, list) else [c.strip() for c in val.split(",") if c.strip()] or [""]

    if "regions" in settings:
        val = settings["regions"]
        parsed["regions"] = val if isinstance(val, list) else [r.strip() for r in val.split(",") if r.strip()]

    if "leads_per_search" in settings:
        try:
            parsed["leads_per_search"] = int(str(settings["leads_per_search"]))
        except:
            pass

    if "use_gps" in settings:
        parsed["use_gps"] = parse_bool(settings["use_gps"])

    if "num_browsers" in settings:
        try:
            parsed["num_browsers"] = max(1, min(int(str(settings["num_browsers"])), CONFIG["max_browsers"]))
        except:
            pass

    if "working_directory" in settings:
        parsed["working_directory"] = str(settings["working_directory"]).strip()

    if "output_name" in settings:
        parsed["output_name"] = str(settings["output_name"]).strip()

    if "save_csv" in settings:
        parsed["save_csv"] = parse_bool(settings["save_csv"])

    if "save_xlsx" in settings:
        parsed["save_xlsx"] = parse_bool(settings["save_xlsx"])

    if "save_json" in settings:
        parsed["save_json"] = parse_bool(settings["save_json"])

    return parsed


# ─────────────────────────────────────────────
# OUTPUT PATH RESOLVER
# ─────────────────────────────────────────────

def resolve_working_directory(working_dir):
    """Resolve and create the working directory"""
    if not working_dir:
        return os.getcwd()

    working_dir = working_dir.strip()

    if not os.path.exists(working_dir):
        try:
            os.makedirs(working_dir)
            print(f"  Created working directory: {working_dir}")
        except Exception as e:
            print(f"  Error creating working directory: {e}")
            print(f"  Falling back to current directory")
            return os.getcwd()

    return working_dir


def build_output_paths(working_dir, output_name, save_csv=True, save_xlsx=True, save_json=True):
    """Build output file paths for each enabled format"""
    if not output_name:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_name = f"leads_{timestamp}"

    output_name = re.sub(r'[<>:"/\\|?*]', "", output_name).strip()
    output_name = output_name.rstrip(".")
    for ext in [".csv", ".xlsx", ".json"]:
        output_name = output_name.replace(ext, "")

    base_path = os.path.join(working_dir, output_name)

    paths = {}
    if save_csv:
        paths["csv"] = base_path + ".csv"
    if save_xlsx:
        paths["xlsx"] = base_path + ".xlsx"
    if save_json:
        paths["json"] = base_path + ".json"

    return paths


def find_existing_csv(working_dir, output_name):
    """Find existing CSV file to load duplicates from"""
    if not output_name:
        return None

    for ext in [".csv", ".xlsx", ".json"]:
        output_name = output_name.replace(ext, "")

    csv_path = os.path.join(working_dir, output_name + ".csv")
    return csv_path if os.path.exists(csv_path) else None


# ─────────────────────────────────────────────
# UTILITIES
# ─────────────────────────────────────────────

def parse_input_list(input_text, input_type="items"):
    """Parse comma-separated input or load from file"""
    items = []
    if os.path.isfile(input_text):
        try:
            with open(input_text, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        items.append(line)
            print(f"  Loaded {len(items)} {input_type} from file")
        except Exception as e:
            print(f"  Error reading file: {e}")
            return []
    else:
        items = [item.strip() for item in input_text.split(",") if item.strip()]
    return items


def normalize_for_comparison(name, phone, address):
    """Create a normalized key for duplicate detection"""
    norm_name = ""
    if name and name != "N/A":
        norm_name = re.sub(r'[^\w\s]', '', name.lower().strip())
        norm_name = re.sub(r'\s+', ' ', norm_name).strip()

    norm_phone = ""
    if phone and phone != "N/A":
        norm_phone = re.sub(r'[^\d]', '', phone)
        if len(norm_phone) >= 8:
            norm_phone = norm_phone[-8:]

    norm_address = ""
    if address and address != "N/A":
        norm_address = address.lower().strip()[:50]

    return (norm_name, norm_phone, norm_address)


def load_existing_leads(csv_path):
    """Load existing leads from CSV for duplicate detection"""
    existing_keys = set()
    existing_df = None

    if not csv_path or not os.path.exists(csv_path):
        return existing_df, existing_keys

    try:
        existing_df = pd.read_csv(csv_path, encoding='utf-8-sig')

        if len(existing_df) > 0:
            print(f"  Found existing file with {len(existing_df)} leads")
            for _, row in existing_df.iterrows():
                key = normalize_for_comparison(
                    str(row.get('name', '')),
                    str(row.get('phone', '')),
                    str(row.get('address', ''))
                )
                existing_keys.add(key)
            print(f"  Duplicates from existing file will be skipped")
        else:
            existing_df = None

    except pd.errors.EmptyDataError:
        existing_df = None
    except Exception as e:
        print(f"  Error reading existing file: {e}")
        existing_df = None

    return existing_df, existing_keys


def save_leads(leads, output_paths, existing_df=None):
    """Save leads to all enabled output formats"""
    if not leads and existing_df is None:
        return

    new_df = pd.DataFrame(leads) if leads else pd.DataFrame()

    if existing_df is not None and not existing_df.empty:
        combined_df = pd.concat([existing_df, new_df], ignore_index=True) if not new_df.empty else existing_df
    else:
        combined_df = new_df

    if combined_df.empty:
        return

    columns = [
        "name", "phone", "website", "emails",
        "facebook", "instagram", "twitter", "linkedin", "youtube", "tiktok",
        "address", "rating", "reviews", "category",
        "search_region", "search_category", "scraped_at"
    ]
    combined_df = combined_df[[c for c in columns if c in combined_df.columns]]

    if "csv" in output_paths:
        try:
            csv_dir = os.path.dirname(output_paths["csv"])
            if csv_dir and not os.path.exists(csv_dir):
                os.makedirs(csv_dir)
            combined_df.to_csv(output_paths["csv"], index=False, encoding="utf-8-sig")
            print(f"  CSV:   {output_paths['csv']} ({len(combined_df)} leads)")
        except Exception as e:
            print(f"  CSV save failed: {e}")

    if "xlsx" in output_paths:
        try:
            xlsx_dir = os.path.dirname(output_paths["xlsx"])
            if xlsx_dir and not os.path.exists(xlsx_dir):
                os.makedirs(xlsx_dir)
            combined_df.to_excel(output_paths["xlsx"], index=False, engine="openpyxl")
            print(f"  Excel: {output_paths['xlsx']}")
        except Exception as e:
            print(f"  Excel save failed: {e}")

    if "json" in output_paths:
        try:
            json_dir = os.path.dirname(output_paths["json"])
            if json_dir and not os.path.exists(json_dir):
                os.makedirs(json_dir)
            combined_df.to_json(output_paths["json"], orient="records", indent=2, force_ascii=False)
            print(f"  JSON:  {output_paths['json']}")
        except Exception as e:
            print(f"  JSON save failed: {e}")


# ─────────────────────────────────────────────
# SHARED STATE
# ─────────────────────────────────────────────

class SharedState:
    """Thread-safe shared state for cross-browser duplicate detection"""

    def __init__(self, existing_keys=None):
        self.lock = threading.Lock()
        self.seen_names = set()
        self.seen_phones = set()
        self.seen_addresses = set()
        self.existing_keys = existing_keys or set()
        self.all_leads = []
        self.leads_since_save = 0
        self.total_collected = 0
        self.total_duplicates = 0
        self.total_existing_skipped = 0

    def is_duplicate(self, name, phone, address):
        key = normalize_for_comparison(name, phone, address)

        with self.lock:
            if key in self.existing_keys:
                self.total_existing_skipped += 1
                return True

            if key[0] and len(key[0]) > 3:
                if key[0] in self.seen_names:
                    self.total_duplicates += 1
                    return True
                self.seen_names.add(key[0])

            if key[1] and len(key[1]) >= 8:
                if key[1] in self.seen_phones:
                    self.total_duplicates += 1
                    return True
                self.seen_phones.add(key[1])

            if key[2]:
                if key[2] in self.seen_addresses:
                    self.total_duplicates += 1
                    return True
                self.seen_addresses.add(key[2])

            self.existing_keys.add(key)
            return False

    def add_lead(self, lead):
        with self.lock:
            self.all_leads.append(lead)
            self.total_collected += 1
            self.leads_since_save += 1

    def get_leads(self):
        with self.lock:
            return list(self.all_leads)

    def should_save(self, save_every):
        with self.lock:
            if self.leads_since_save >= save_every:
                self.leads_since_save = 0
                return True
            return False


# ─────────────────────────────────────────────
# GEOCODER
# ─────────────────────────────────────────────

class Geocoder:
    """Thread-safe geocoder using OpenStreetMap Nominatim"""

    def __init__(self):
        self.base_url = "https://nominatim.openstreetmap.org/search"
        self.session = requests.Session()
        self.session.headers.update({'User-Agent': 'congenial-waffle/1.0'})
        self.cache = {}
        self.lock = threading.Lock()

    def geocode(self, location):
        with self.lock:
            if location in self.cache:
                return self.cache[location]

        try:
            params = {'q': location, 'format': 'json', 'limit': 1}
            response = self.session.get(self.base_url, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()

            if data and len(data) > 0:
                result = (
                    float(data[0]['lat']),
                    float(data[0]['lon']),
                    data[0].get('display_name', location)
                )
                with self.lock:
                    self.cache[location] = result
                time.sleep(1)
                return result

            with self.lock:
                self.cache[location] = None
            return None

        except Exception as e:
            logger.error(f"Geocoding error for {location}: {e}")
            with self.lock:
                self.cache[location] = None
            return None


# ─────────────────────────────────────────────
# BROWSER WORKER
# ─────────────────────────────────────────────

class BrowserWorker:
    """A single browser instance that handles search tasks"""

    def __init__(self, worker_id, shared_state, geocoder, use_gps=True):
        self.worker_id = worker_id
        self.shared_state = shared_state
        self.geocoder = geocoder
        self.use_gps = use_gps
        self.driver = None
        self.current_region = ""
        self.current_category = ""

    def log(self, message):
        print(f"  [Browser {self.worker_id}] {message}")

    def setup_driver(self):
        options = Options()
        options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1920,1080")
        options.add_argument("--disable-extensions")

        # Set user agent based on OS
        if sys.platform == "win32":
            options.add_argument(
                "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
        else:
            options.add_argument(
                "--user-agent=Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )

        try:
            # Linux system chromedriver paths
            linux_paths = [
                "/usr/bin/chromedriver",
                "/usr/local/bin/chromedriver"
            ]

            # Windows system chromedriver paths
            windows_paths = [
                r"C:\Program Files\Google\Chrome\chromedriver.exe",
                r"C:\chromedriver\chromedriver.exe",
                os.path.join(os.environ.get("USERPROFILE", ""), "chromedriver.exe"),
            ]

            # Pick paths based on OS
            search_paths = windows_paths if sys.platform == "win32" else linux_paths

            system_driver = None
            for path in search_paths:
                if os.path.exists(path):
                    system_driver = path
                    break

            if system_driver:
                service = Service(system_driver)
            else:
                # Fallback: webdriver-manager downloads it automatically
                os.environ['WDM_LOG'] = '0'
                from webdriver_manager.chrome import ChromeDriverManager
                service = Service(ChromeDriverManager().install())

            self.driver = webdriver.Chrome(service=service, options=options)
            self.driver.set_page_load_timeout(60)
            self.log("Ready")
            return True

        except Exception as e:
            self.log(f"Setup failed: {e}")
            return False

    def build_search_url(self, category, region):
        self.current_category = category
        self.current_region = region
        search_term = category if category else "businesses"

        if self.use_gps and self.geocoder:
            coords = self.geocoder.geocode(region)
            if coords:
                lat, lon, display_name = coords
                short_name = display_name.split(',')[0]
                self.log(f"GPS: {short_name} ({lat:.4f}, {lon:.4f})")
                return f"https://www.google.com/maps/search/{search_term}/@{lat},{lon},13z"
            else:
                self.log(f"Geocoding failed for '{region}', using text search")

        query = f"{category} near {region}" if category else f"businesses near {region}"
        return f"https://www.google.com/maps/search/{query.replace(' ', '+')}"

    def is_valid_business_name(self, name):
        if not name or name == "N/A":
            return False
        name_lower = name.lower().strip()
        if name_lower in INVALID_NAMES:
            return False
        if len(name_lower) < 2:
            return False
        if not re.search(r'[a-zA-Z]{2,}', name):
            return False
        if re.match(r'^\d+\s*[-–—]', name):
            return False
        return True

    def extract_business_info(self):
        try:
            time.sleep(1.5)
            data = {}

            name = "N/A"
            for selector in ["h1.DUwDvf.lfPIob", "h1.DUwDvf", "h1"]:
                try:
                    name = self.driver.find_element(By.CSS_SELECTOR, selector).text.strip()
                    break
                except:
                    continue

            if not self.is_valid_business_name(name):
                return None
            data["name"] = name

            try:
                btns = self.driver.find_elements(By.CSS_SELECTOR, "button[data-item-id^='phone:tel:']")
                data["phone"] = btns[0].get_attribute("data-item-id").replace("phone:tel:", "").strip() if btns else "N/A"
            except:
                data["phone"] = "N/A"

            try:
                links = self.driver.find_elements(By.CSS_SELECTOR, "a[data-item-id='authority']")
                data["website"] = links[0].get_attribute("href").strip() if links else "N/A"
            except:
                data["website"] = "N/A"

            try:
                btn = self.driver.find_element(By.CSS_SELECTOR, "button[data-item-id^='address']")
                addr = btn.get_attribute("aria-label") or btn.text
                data["address"] = addr.replace("Address: ", "").strip() if addr else "N/A"
            except:
                data["address"] = "N/A"

            if self.shared_state.is_duplicate(data["name"], data["phone"], data["address"]):
                return None

            try:
                data["rating"] = self.driver.find_element(
                    By.CSS_SELECTOR, "div.F7nice span[aria-hidden='true']"
                ).text.strip()
            except:
                data["rating"] = "N/A"

            try:
                found_reviews = "N/A"
                for el in self.driver.find_elements(By.CSS_SELECTOR, "div.F7nice span"):
                    aria = el.get_attribute("aria-label")
                    if aria and "review" in aria.lower():
                        m = re.search(r"([\d,]+)", aria)
                        if m:
                            found_reviews = m.group(1)
                            break
                data["reviews"] = found_reviews
            except:
                data["reviews"] = "N/A"

            try:
                data["category"] = self.driver.find_element(
                    By.CSS_SELECTOR, "button[jsaction*='category']"
                ).text.strip()
            except:
                data["category"] = "N/A"

            for field in ["emails", "facebook", "instagram", "twitter", "linkedin", "youtube", "tiktok"]:
                data[field] = "N/A"

            data["search_region"] = self.current_region
            data["search_category"] = self.current_category if self.current_category else "all"
            data["scraped_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            return data

        except Exception as e:
            logger.error(f"Browser {self.worker_id} extraction error: {e}")
            return None

    def get_result_cards(self):
        return self.driver.find_elements(By.CSS_SELECTOR, "div.Nv2PK")

    def click_result_by_index(self, index, retries=3):
        for _ in range(retries):
            try:
                cards = self.get_result_cards()
                if index >= len(cards):
                    return False
                card = cards[index]
                self.driver.execute_script(
                    "arguments[0].scrollIntoView({block: 'center'});", card
                )
                time.sleep(0.5)
                try:
                    link = card.find_element(By.CSS_SELECTOR, "a.hfpxzc")
                    self.driver.execute_script("arguments[0].click();", link)
                except:
                    self.driver.execute_script("arguments[0].click();", card)
                time.sleep(CONFIG["click_pause"])
                return True
            except (StaleElementReferenceException, ElementClickInterceptedException):
                time.sleep(1)
            except:
                time.sleep(1)
        return False

    def scroll_results(self, target_count):
        try:
            feed = None
            for selector in ["div[role='feed']", "div.m6QErb"]:
                try:
                    feed = self.driver.find_element(By.CSS_SELECTOR, selector)
                    break
                except:
                    continue
            if not feed:
                return
        except:
            return

        last_count = 0
        no_change = 0

        for _ in range(max(12, math.ceil(target_count / 8) + 10)):
            try:
                self.driver.execute_script(
                    "arguments[0].scrollTop = arguments[0].scrollHeight;", feed
                )
                time.sleep(CONFIG["scroll_pause"])
                current = len(self.get_result_cards())
                if current >= target_count:
                    break
                if current == last_count:
                    no_change += 1
                    if no_change >= 4:
                        break
                else:
                    no_change = 0
                last_count = current
            except:
                break

    def run_search(self, category, region, max_results):
        self.log(f"Searching: {category if category else 'all'} in {region}")

        try:
            url = self.build_search_url(category, region)
            self.driver.get(url)
            time.sleep(8)
            self.scroll_results(max_results)

            total_found = len(self.get_result_cards())
            self.log(f"Found {total_found} results")

            idx = 0
            collected = 0
            failures = 0

            while collected < max_results and idx < total_found:
                if failures >= CONFIG["max_consecutive_failures"]:
                    break

                if not self.click_result_by_index(idx):
                    idx += 1
                    failures += 1
                    continue

                lead = self.extract_business_info()
                if lead:
                    self.shared_state.add_lead(lead)
                    collected += 1
                    failures = 0
                    name = lead["name"][:40] + "..." if len(lead["name"]) > 40 else lead["name"]
                    self.log(f"[{collected}/{max_results}] {name}")
                else:
                    failures += 1

                idx += 1

                if idx >= total_found - 5 and collected < max_results:
                    self.scroll_results(max_results + 20)
                    total_found = len(self.get_result_cards())

            self.log(f"Done: {collected} leads")
            return collected

        except Exception as e:
            self.log(f"Search error: {e}")
            return 0

    def close(self):
        if self.driver:
            try:
                self.driver.quit()
            except:
                pass


# ─────────────────────────────────────────────
# WEBSITE SCRAPER
# ─────────────────────────────────────────────

class WebsiteScraper:
    """Scrapes emails and social media links from websites"""

    def __init__(self, timeout=10):
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "congenial-waffle/1.0"
        })

    def extract_emails(self, text):
        found = re.findall(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b", text)
        excluded = [
            "example.com", "schema.org", "w3.org", "sentry.io",
            "wixpress.com", "wordpress.com", "googleapis.com"
        ]
        return list(set(
            e.lower() for e in found
            if not any(bad in e.lower() for bad in excluded)
            and not e.lower().endswith((".png", ".jpg", ".gif", ".svg"))
        ))

    def extract_mailto_links(self, soup):
        emails = set()
        for link in soup.find_all("a", href=True):
            href = link.get("href", "")
            if href.startswith("mailto:"):
                email = href.replace("mailto:", "").split("?")[0].strip()
                if "@" in email and "." in email:
                    emails.add(email.lower())
        return list(emails)

    def extract_social_media(self, html, soup):
        social = {
            "facebook": "N/A", "instagram": "N/A", "twitter": "N/A",
            "linkedin": "N/A", "youtube": "N/A", "tiktok": "N/A"
        }

        checks = [
            ("facebook", ["facebook.com", "fb.com"]),
            ("instagram", ["instagram.com"]),
            ("twitter", ["twitter.com", "x.com"]),
            ("linkedin", ["linkedin.com"]),
            ("youtube", ["youtube.com", "youtu.be"]),
            ("tiktok", ["tiktok.com"])
        ]

        for link in soup.find_all("a", href=True):
            href = link.get("href", "").lower()
            for platform, domains in checks:
                if any(d in href for d in domains) and social[platform] == "N/A":
                    social[platform] = link.get("href", "N/A")

        for platform, patterns in SOCIAL_MEDIA_PATTERNS.items():
            if social[platform] == "N/A":
                for pattern in patterns:
                    matches = re.findall(pattern, html, re.IGNORECASE)
                    if matches:
                        url = matches[0]
                        social[platform] = url if url.startswith("http") else "https://" + url
                        break

        return social

    def get_page(self, url):
        try:
            r = self.session.get(url, timeout=self.timeout, verify=False)
            r.raise_for_status()
            return r.text
        except:
            return None

    def find_contact_pages(self, base_url, soup):
        keywords = ["contact", "about", "contact-us", "about-us", "support", "info"]
        urls = set()
        for link in soup.find_all("a", href=True):
            href = (link.get("href") or "").lower()
            text = link.get_text(" ", strip=True).lower()
            if any(k in href or k in text for k in keywords):
                full = urljoin(base_url, link["href"])
                if urlparse(full).netloc == urlparse(base_url).netloc:
                    urls.add(full)
        return list(urls)[:5]

    def scrape_website(self, website_url):
        result = {
            "emails": [], "facebook": "N/A", "instagram": "N/A",
            "twitter": "N/A", "linkedin": "N/A", "youtube": "N/A", "tiktok": "N/A"
        }
        if not website_url or website_url == "N/A":
            return result
        try:
            if not website_url.startswith("http"):
                website_url = "https://" + website_url
            all_emails = set()
            html = self.get_page(website_url)
            if not html:
                return result
            soup = BeautifulSoup(html, "html.parser")
            all_emails.update(self.extract_emails(html))
            all_emails.update(self.extract_mailto_links(soup))
            all_social = self.extract_social_media(html, soup)
            for page in self.find_contact_pages(website_url, soup)[:3]:
                time.sleep(0.5)
                page_html = self.get_page(page)
                if page_html:
                    page_soup = BeautifulSoup(page_html, "html.parser")
                    all_emails.update(self.extract_emails(page_html))
                    all_emails.update(self.extract_mailto_links(page_soup))
                    for p, u in self.extract_social_media(page_html, page_soup).items():
                        if all_social.get(p) == "N/A" and u != "N/A":
                            all_social[p] = u
            result["emails"] = list(all_emails)
            result.update(all_social)
            return result
        except:
            return result


# ─────────────────────────────────────────────
# LEAD GENERATOR
# ─────────────────────────────────────────────

class LeadGenerator:
    """Main orchestrator that manages browsers and coordinates everything"""

    def __init__(
        self,
        use_gps=True,
        working_dir=None,
        output_name=None,
        save_csv=True,
        save_xlsx=True,
        save_json=True,
        num_browsers=1,
    ):
        self.website_scraper = WebsiteScraper()
        self.use_gps = use_gps
        self.num_browsers = min(num_browsers, CONFIG["max_browsers"])
        self.save_lock = threading.Lock()
        self.existing_df = None
        self.existing_keys = set()

        self.working_dir = resolve_working_directory(working_dir)

        self.output_paths = build_output_paths(
            self.working_dir,
            output_name,
            save_csv=save_csv,
            save_xlsx=save_xlsx,
            save_json=save_json
        )

        existing_csv = find_existing_csv(self.working_dir, output_name)
        if existing_csv:
            self.existing_df, self.existing_keys = load_existing_leads(existing_csv)

    def save_progress(self, leads):
        if not leads:
            return
        with self.save_lock:
            save_leads(leads, self.output_paths, self.existing_df)

    def build_task_queue(self, categories, regions, max_results):
        tasks = queue.Queue()
        for category in categories:
            for region in regions:
                tasks.put((category, region, max_results))
        return tasks

    def worker_thread(self, worker_id, task_queue, shared_state, geocoder, results_list):
        worker = BrowserWorker(worker_id, shared_state, geocoder, self.use_gps)

        if not worker.setup_driver():
            return

        try:
            while True:
                try:
                    category, region, max_results = task_queue.get(timeout=5)
                except queue.Empty:
                    break

                try:
                    collected = worker.run_search(category, region, max_results)
                    results_list.append((category, region, collected))
                    self.save_progress(shared_state.get_leads())
                except Exception as e:
                    logger.error(f"Worker {worker_id} task error: {e}")
                    results_list.append((category, region, 0))
                finally:
                    task_queue.task_done()
        finally:
            worker.close()

    def scrape_websites(self, leads):
        print("\n[Websites] Scraping for emails and social media...")
        leads_with_website = sum(1 for l in leads if l.get("website") != "N/A")
        print(f"  {leads_with_website}/{len(leads)} leads have websites")

        found_count = 0
        for i, lead in enumerate(leads, 1):
            if lead.get("website") and lead["website"] != "N/A":
                name = lead["name"][:40] + "..." if len(lead["name"]) > 40 else lead["name"]
                data = self.website_scraper.scrape_website(lead["website"])

                lead["emails"] = ", ".join(data["emails"]) if data["emails"] else "N/A"
                lead["facebook"] = data["facebook"]
                lead["instagram"] = data["instagram"]
                lead["twitter"] = data["twitter"]
                lead["linkedin"] = data["linkedin"]
                lead["youtube"] = data["youtube"]
                lead["tiktok"] = data["tiktok"]

                found = []
                if data["emails"]:
                    found.append(f"email:{len(data['emails'])}")
                for s in ["facebook", "instagram", "twitter", "linkedin"]:
                    if data[s] != "N/A":
                        found.append(s[:2])

                if found:
                    print(f"  [{i}/{len(leads)}] {name} -> {', '.join(found)}")
                    found_count += 1

                time.sleep(0.2)

                if i % CONFIG["save_every"] == 0:
                    self.save_progress(leads)

        print(f"  Found contact info for {found_count}/{leads_with_website} websites")
        return leads

    def run(self, categories, regions, max_results_per_search):
        total_searches = len(categories) * len(regions)
        total_target = total_searches * max_results_per_search

        print("\n" + "="*60)
        print("congenial-waffle")
        print("="*60)
        print(f"  Mode:           {'GPS' if self.use_gps else 'Text'}")
        print(f"  Browsers:       {self.num_browsers} (parallel)")
        print(f"  Categories:     {len(categories)}")
        print(f"  Regions:        {len(regions)}")
        print(f"  Total searches: {total_searches}")
        print(f"  Leads/search:   {max_results_per_search}")
        print(f"  Total target:   ~{total_target}")
        print(f"  Working dir:    {self.working_dir}")
        print(f"  Formats:        {', '.join(self.output_paths.keys())}")
        if self.existing_df is not None:
            print(f"  Existing leads: {len(self.existing_df)}")
        print("="*60)

        shared_state = SharedState(existing_keys=self.existing_keys)
        geocoder = Geocoder()
        task_queue = self.build_task_queue(categories, regions, max_results_per_search)
        results_list = []

        actual_browsers = min(self.num_browsers, total_searches)
        print(f"\n[Setup] Starting {actual_browsers} browser(s)...")

        threads = []
        for i in range(1, actual_browsers + 1):
            t = threading.Thread(
                target=self.worker_thread,
                args=(i, task_queue, shared_state, geocoder, results_list),
                daemon=True
            )
            threads.append(t)
            t.start()
            time.sleep(2)

        for t in threads:
            t.join()

        print(f"\n[Done] All browsers finished")

        all_leads = shared_state.get_leads()

        if not all_leads:
            print("\nNo new leads found")
            return []

        print(f"\n[Maps] Collected {len(all_leads)} new leads")
        print(f"  Cross-browser duplicates skipped: {shared_state.total_duplicates}")
        print(f"  Existing file duplicates skipped: {shared_state.total_existing_skipped}")

        all_leads = self.scrape_websites(all_leads)

        print("\n[Saving]")
        save_leads(all_leads, self.output_paths, self.existing_df)

        stats = [
            {"category": r[0] if r[0] else "all", "region": r[1], "count": r[2]}
            for r in results_list
        ]
        self.print_summary(all_leads, stats, shared_state)

        return all_leads

    def print_summary(self, leads, stats, shared_state=None):
        total = len(leads)
        if total == 0:
            return

        with_phone = sum(1 for l in leads if l.get("phone") != "N/A")
        with_website = sum(1 for l in leads if l.get("website") != "N/A")
        with_email = sum(1 for l in leads if l.get("emails") != "N/A")
        with_social = sum(1 for l in leads if any(
            l.get(s) != "N/A" for s in ["facebook", "instagram", "twitter", "linkedin"]
        ))

        print("\n" + "="*60)
        print("SUMMARY")
        print("="*60)

        print("\nSearches:")
        for s in stats:
            print(f"  {s['category'][:15]:<15} | {s['region'][:20]:<20} | {s['count']} leads")

        if shared_state:
            print(f"\nDuplicate detection:")
            print(f"  Cross-browser duplicates: {shared_state.total_duplicates}")
            print(f"  Skipped from file:        {shared_state.total_existing_skipped}")

        print(f"\nNew leads:    {total}")
        print(f"With phone:   {with_phone} ({with_phone*100//total if total else 0}%)")
        print(f"With website: {with_website} ({with_website*100//total if total else 0}%)")
        print(f"With email:   {with_email} ({with_email*100//total if total else 0}%)")
        print(f"With social:  {with_social} ({with_social*100//total if total else 0}%)")

        if self.existing_df is not None:
            print(f"\nTotal in file: {len(self.existing_df) + total}")

        print("\nOutput files:")
        for fmt, path in self.output_paths.items():
            print(f"  {fmt.upper()}: {path}")

        print("="*60)


# ─────────────────────────────────────────────
# CONFIG TO PARAMS HELPER
# ─────────────────────────────────────────────

def build_run_params_from_config(settings):
    """Convert parsed config settings into run parameters"""
    parsed = parse_config_settings(settings)

    categories = parsed.get("categories", [""])
    regions = parsed.get("regions", [])
    max_results = parsed.get("leads_per_search", 20)

    kwargs = {
        "use_gps": parsed.get("use_gps", True),
        "working_dir": parsed.get("working_directory", None),
        "output_name": parsed.get("output_name", None),
        "save_csv": parsed.get("save_csv", True),
        "save_xlsx": parsed.get("save_xlsx", True),
        "save_json": parsed.get("save_json", True),
        "num_browsers": parsed.get("num_browsers", 1),
    }

    return categories, regions, max_results, kwargs


# ─────────────────────────────────────────────
# INTERACTIVE MODE
# ─────────────────────────────────────────────

def interactive_mode():
    print("\n" + "="*60)
    print("congenial-waffle")
    print("="*60)

    print("\nLoad from config file?")
    print("  Enter path, press Enter to configure manually")
    print("  Type 'template' to generate a sample config file")

    config_input = input("\nConfig file [Enter to skip]: ").strip()

    if config_input.lower() == "template":
        path = input("Save template as [config.txt]: ").strip() or "config.txt"
        generate_config_template(path)
        return

    if config_input:
        settings = load_config_file(config_input)
        if settings:
            categories, regions, max_results, kwargs = build_run_params_from_config(settings)

            if not regions:
                print("  Config file has no regions defined")
                return

            print(f"\nLoaded from config:")
            print(f"  Categories:   {categories}")
            print(f"  Regions:      {regions}")
            print(f"  Leads:        {max_results}")
            print(f"  Working dir:  {kwargs.get('working_dir') or 'current folder'}")
            print(f"  Output name:  {kwargs.get('output_name') or 'auto'}")
            print(f"  Save CSV:     {kwargs.get('save_csv', True)}")
            print(f"  Save Excel:   {kwargs.get('save_xlsx', True)}")
            print(f"  Save JSON:    {kwargs.get('save_json', True)}")
            print(f"  Browsers:     {kwargs.get('num_browsers', 1)}")
            print(f"  GPS:          {kwargs.get('use_gps', True)}")

            confirm = input("\nStart? [Y/n]: ").strip().lower()
            if confirm in ["n", "no"]:
                print("Cancelled")
                return

            start = time.time()
            generator = LeadGenerator(**kwargs)
            leads = generator.run(categories, regions, max_results)
            duration = int(time.time() - start)
            if leads:
                print(f"\nDone in {duration//60}m {duration%60}s")
            else:
                print("\nNo new leads found")
            return
        else:
            print("  Could not load config, switching to manual setup")

    print("\nManual Setup")
    print("-"*40)

    print("\nSearch mode:")
    print("  1. GPS-based (accurate)")
    print("  2. Text-based (faster)")
    mode = input("[1]: ").strip()
    use_gps = mode != "2"

    print(f"\nParallel browsers (1-{CONFIG['max_browsers']}):")
    print("  1 = stable | 2-3 = faster | 4-5 = fastest (high RAM)")
    browser_input = input("[1]: ").strip()
    num_browsers = 1
    if browser_input:
        try:
            num_browsers = max(1, min(int(browser_input), CONFIG["max_browsers"]))
        except:
            pass

    print("\nCategories (comma-separated, file path, or empty for all):")
    cat_input = input("> ").strip()
    categories = parse_input_list(cat_input, "categories") if cat_input else [""]
    if not categories:
        categories = [""]

    print("\nRegions (comma-separated or file path):")
    reg_input = input("> ").strip()
    while not reg_input:
        print("  At least one region required")
        reg_input = input("> ").strip()
    regions = parse_input_list(reg_input, "regions")
    if not regions:
        return

    print(f"\nLeads per search (max {CONFIG['max_leads_per_search']}):")
    max_input = input("[20]: ").strip()
    max_results = 20
    if max_input:
        try:
            max_results = min(int(max_input), CONFIG['max_leads_per_search'])
        except:
            pass

    print("\nWorking directory (where all files will be saved):")
    print("  Press Enter for current folder")
    working_dir = input("> ").strip() or None

    print("\nOutput filename (without extension):")
    print("  Example: paris_leads -> paris_leads.csv, .xlsx, .json")
    print("  Press Enter for auto-generated name with timestamp")
    output_name = input("> ").strip() or None

    print("\nOutput formats:")
    save_csv = input("  Save as CSV?   [Y/n]: ").strip().lower() not in ["n", "no"]
    save_xlsx = input("  Save as Excel? [Y/n]: ").strip().lower() not in ["n", "no"]
    save_json = input("  Save as JSON?  [Y/n]: ").strip().lower() not in ["n", "no"]

    if not save_csv and not save_xlsx and not save_json:
        print("  At least one format required, enabling CSV")
        save_csv = True

    print("\nSave these settings to a config file? [y/N]:", end=" ")
    if input().strip().lower() in ["y", "yes"]:
        config_path = input("Config filename [config.txt]: ").strip() or "config.txt"

        lines = [
            f"# congenial-waffle config - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "",
            "# ── SEARCH ──────────────────────────────────",
            f"categories = {', '.join(c for c in categories if c)}",
            f"regions = {', '.join(regions)}",
            f"leads_per_search = {max_results}",
            f"use_gps = {str(use_gps).lower()}",
            f"num_browsers = {num_browsers}",
            "",
            "# ── WORKING DIRECTORY ────────────────────────",
            f"working_directory = {working_dir or os.getcwd()}",
            "",
            "# ── OUTPUT ──────────────────────────────────",
            f"output_name = {output_name or 'leads'}",
            f"save_csv  = {str(save_csv).lower()}",
            f"save_xlsx = {str(save_xlsx).lower()}",
            f"save_json = {str(save_json).lower()}",
        ]

        with open(config_path, "w") as f:
            f.write("\n".join(lines) + "\n")

        print(f"  Saved: {config_path}")
        print(f"  Next time: python congenial-waffle.py --config {config_path}")

    print("\n" + "-"*60)
    print("Configuration:")
    print(f"  Mode:         {'GPS' if use_gps else 'Text'}")
    print(f"  Browsers:     {num_browsers}")
    print(f"  Categories:   {len(categories)}")
    print(f"  Regions:      {len(regions)}")
    print(f"  Searches:     {len(categories) * len(regions)}")
    print(f"  Leads/search: {max_results}")
    print(f"  Working dir:  {working_dir or 'current folder'}")
    print(f"  Output name:  {output_name or 'auto'}")
    print(f"  Formats:      {', '.join(f for f, e in [('csv', save_csv), ('xlsx', save_xlsx), ('json', save_json)] if e)}")
    print("-"*60)

    confirm = input("\nStart? [Y/n]: ").strip().lower()
    if confirm in ["n", "no"]:
        print("Cancelled")
        return

    start = time.time()

    generator = LeadGenerator(
        use_gps=use_gps,
        working_dir=working_dir,
        output_name=output_name,
        save_csv=save_csv,
        save_xlsx=save_xlsx,
        save_json=save_json,
        num_browsers=num_browsers,
    )

    leads = generator.run(categories, regions, max_results)
    duration = int(time.time() - start)

    if leads:
        print(f"\nDone in {duration//60}m {duration%60}s")
    else:
        print("\nNo new leads found")


# ─────────────────────────────────────────────
# MAIN / CLI
# ─────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="congenial-waffle - Google Maps Lead Generator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python congenial-waffle.py
  python congenial-waffle.py --config config.txt
  python congenial-waffle.py --template
  python congenial-waffle.py -c "bar,cafe" -r "Paris France" -w ./output -n paris
  python congenial-waffle.py -r "Tokyo Japan" -b 2 -w /home/user/leads -n tokyo -l 50
        """
    )

    parser.add_argument("--config",   help="Path to config file (.txt or .json)")
    parser.add_argument("--template", action="store_true", help="Generate sample config file")
    parser.add_argument("-c", "--categories", help="Categories (comma-separated or file)")
    parser.add_argument("-r", "--regions",    help="Regions (comma-separated or file)")
    parser.add_argument("-w", "--workdir",    help="Working directory for output files")
    parser.add_argument("-n", "--name",       help="Output filename without extension")
    parser.add_argument("-l", "--leads",      type=int, default=20, help="Leads per search")
    parser.add_argument("-b", "--browsers",   type=int, default=1,  help="Parallel browsers")
    parser.add_argument("--no-gps",           action="store_true",  help="Use text search")
    parser.add_argument("--no-csv",           action="store_true",  help="Skip CSV output")
    parser.add_argument("--no-xlsx",          action="store_true",  help="Skip Excel output")
    parser.add_argument("--no-json",          action="store_true",  help="Skip JSON output")

    args = parser.parse_args()

    try:
        if args.template:
            generate_config_template("config.txt")
            return

        if args.config:
            print("\n" + "="*60)
            print("congenial-waffle")
            print("="*60)

            settings = load_config_file(args.config)
            if not settings:
                print("Could not load config file")
                return

            categories, regions, max_results, kwargs = build_run_params_from_config(settings)

            if not regions:
                print("Config file has no regions defined")
                return

            start = time.time()
            generator = LeadGenerator(**kwargs)
            leads = generator.run(categories, regions, max_results)
            duration = int(time.time() - start)
            print(f"\nCompleted in {duration//60}m {duration%60}s")
            return

        if args.regions:
            print("\n" + "="*60)
            print("congenial-waffle")
            print("="*60)

            categories = parse_input_list(args.categories, "categories") if args.categories else [""]
            regions = parse_input_list(args.regions, "regions")
            num_browsers = max(1, min(args.browsers, CONFIG["max_browsers"]))

            start = time.time()

            generator = LeadGenerator(
                use_gps=not args.no_gps,
                working_dir=args.workdir,
                output_name=args.name,
                save_csv=not args.no_csv,
                save_xlsx=not args.no_xlsx,
                save_json=not args.no_json,
                num_browsers=num_browsers,
            )

            leads = generator.run(categories, regions, args.leads)
            duration = int(time.time() - start)
            print(f"\nCompleted in {duration//60}m {duration%60}s")
            return

        interactive_mode()

    except KeyboardInterrupt:
        print("\n\nInterrupted. Check working directory for partial results.")
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        print(f"\nError: {e}")
        print("See congenial-waffle.log for details")


if __name__ == "__main__":
    main()
