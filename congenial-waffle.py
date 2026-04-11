#!/usr/bin/env python3
"""
Google Maps Lead Generator

Extract business information from Google Maps and scrape contact details from websites.
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
}

SOCIAL_MEDIA_PATTERNS = {
    "facebook": [
        r"(?:https?://)?(?:www\.)?facebook\.com/[\w\.\-]+/?",
        r"(?:https?://)?(?:www\.)?fb\.com/[\w\.\-]+/?"
    ],
    "instagram": [
        r"(?:https?://)?(?:www\.)?instagram\.com/[\w\.\-]+/?",
    ],
    "twitter": [
        r"(?:https?://)?(?:www\.)?twitter\.com/[\w\.\-]+/?",
        r"(?:https?://)?(?:www\.)?x\.com/[\w\.\-]+/?"
    ],
    "linkedin": [
        r"(?:https?://)?(?:www\.)?linkedin\.com/(?:company|in)/[\w\.\-]+/?",
    ],
    "youtube": [
        r"(?:https?://)?(?:www\.)?youtube\.com/(?:c/|channel/|user/|@)?[\w\.\-]+/?",
    ],
    "tiktok": [
        r"(?:https?://)?(?:www\.)?tiktok\.com/@[\w\.\-]+/?",
    ],
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
    handlers=[logging.FileHandler("lead_generation.log")]
)
logger = logging.getLogger(__name__)


def parse_input_list(input_text, input_type="items"):
    """Parse user input as comma-separated values or file path"""
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
    """Create a normalized key for duplicate comparison"""
    norm_name = ""
    if name and name != "N/A":
        norm_name = name.lower().strip()
        norm_name = re.sub(r'[^\w\s]', '', norm_name)
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


def resolve_output_path(user_input):
    """
    Resolve output path from user input.
    
    Handles:
    - Full path to CSV: /home/user/leads.csv -> use as is
    - Directory path: /home/user/data/ -> create leads_TIMESTAMP.csv inside
    - Just filename: leads.csv -> use in current directory
    - Directory that doesn't exist: create it and add file
    """
    
    if not user_input:
        # Default filename in current directory
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return f"leads_{timestamp}.csv"
    
    user_input = user_input.strip()
    
    # Check if it ends with .csv (it's a file path)
    if user_input.lower().endswith('.csv'):
        # It's a file path
        directory = os.path.dirname(user_input)
        
        # If directory specified, ensure it exists
        if directory:
            if not os.path.exists(directory):
                try:
                    os.makedirs(directory)
                    print(f"  Created directory: {directory}")
                except Exception as e:
                    print(f"  Error creating directory: {e}")
                    return None
        
        return user_input
    
    # Check if it's a directory (ends with / or exists as directory)
    if user_input.endswith('/') or user_input.endswith('\\') or os.path.isdir(user_input):
        # It's a directory
        if not os.path.exists(user_input):
            try:
                os.makedirs(user_input)
                print(f"  Created directory: {user_input}")
            except Exception as e:
                print(f"  Error creating directory: {e}")
                return None
        
        # Generate filename with timestamp
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"leads_{timestamp}.csv"
        return os.path.join(user_input, filename)
    
    # Check if path without extension might be a directory
    if os.path.isdir(user_input):
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"leads_{timestamp}.csv"
        return os.path.join(user_input, filename)
    
    # Assume it's a filename without extension or with different extension
    # Add .csv if not present
    if not user_input.lower().endswith('.csv'):
        user_input += '.csv'
    
    # Check if directory part exists
    directory = os.path.dirname(user_input)
    if directory and not os.path.exists(directory):
        try:
            os.makedirs(directory)
            print(f"  Created directory: {directory}")
        except Exception as e:
            print(f"  Error creating directory: {e}")
            return None
    
    return user_input


def load_existing_leads(file_path):
    """Load existing leads from CSV and return set of normalized keys"""
    existing_keys = set()
    existing_df = None
    
    if file_path and os.path.exists(file_path) and file_path.endswith('.csv'):
        try:
            existing_df = pd.read_csv(file_path, encoding='utf-8-sig')
            
            if len(existing_df) > 0:
                print(f"  Found existing file with {len(existing_df)} leads")
                
                for _, row in existing_df.iterrows():
                    name = str(row.get('name', ''))
                    phone = str(row.get('phone', ''))
                    address = str(row.get('address', ''))
                    
                    key = normalize_for_comparison(name, phone, address)
                    existing_keys.add(key)
                
                print(f"  Will skip duplicates from existing data")
            else:
                print(f"  Existing file is empty, starting fresh")
                existing_df = None
                
        except pd.errors.EmptyDataError:
            print(f"  Existing file is empty, starting fresh")
            existing_df = None
        except Exception as e:
            print(f"  Error reading existing file: {e}")
            existing_df = None
    
    return existing_df, existing_keys


def save_leads(leads, file_path, existing_df=None):
    """Save leads to file, merging with existing if needed"""
    
    if not leads and existing_df is None:
        print("  No leads to save")
        return
    
    new_df = pd.DataFrame(leads) if leads else pd.DataFrame()
    
    if existing_df is not None and not existing_df.empty:
        if not new_df.empty:
            combined_df = pd.concat([existing_df, new_df], ignore_index=True)
        else:
            combined_df = existing_df
    else:
        combined_df = new_df
    
    if combined_df.empty:
        print("  No leads to save")
        return
    
    columns = [
        "name", "phone", "website", "emails",
        "facebook", "instagram", "twitter", "linkedin", "youtube", "tiktok",
        "address", "rating", "reviews", "category",
        "search_region", "search_category", "scraped_at"
    ]
    combined_df = combined_df[[c for c in columns if c in combined_df.columns]]
    
    output_dir = os.path.dirname(file_path)
    if output_dir and not os.path.exists(output_dir):
        os.makedirs(output_dir)
    
    combined_df.to_csv(file_path, index=False, encoding="utf-8-sig")
    print(f"  Saved: {file_path} ({len(combined_df)} total leads)")
    
    try:
        xlsx_path = file_path.rsplit('.', 1)[0] + '.xlsx'
        combined_df.to_excel(xlsx_path, index=False, engine="openpyxl")
        print(f"  Saved: {xlsx_path}")
    except:
        pass
    
    try:
        json_path = file_path.rsplit('.', 1)[0] + '.json'
        combined_df.to_json(json_path, orient="records", indent=2, force_ascii=False)
        print(f"  Saved: {json_path}")
    except:
        pass


class Geocoder:
    """Geocodes location names to GPS coordinates"""
    
    def __init__(self):
        self.base_url = "https://nominatim.openstreetmap.org/search"
        self.session = requests.Session()
        self.session.headers.update({'User-Agent': 'LeadGenerator/1.0'})
        self.cache = {}
    
    def geocode(self, location):
        """Convert a location name to GPS coordinates"""
        if location in self.cache:
            return self.cache[location]
        
        try:
            params = {'q': location, 'format': 'json', 'limit': 1}
            response = self.session.get(self.base_url, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            if data and len(data) > 0:
                lat = float(data[0]['lat'])
                lon = float(data[0]['lon'])
                display_name = data[0].get('display_name', location)
                result = (lat, lon, display_name)
                self.cache[location] = result
                time.sleep(1)
                return result
            else:
                self.cache[location] = None
                return None
                
        except Exception as e:
            logger.error(f"Geocoding error for {location}: {e}")
            self.cache[location] = None
            return None


class GoogleMapsLeadScraper:
    """Scrapes business information from Google Maps"""
    
    def __init__(self, on_leads_update=None, use_gps=True, existing_keys=None):
        self.driver = None
        self.leads = []
        self.seen_names = set()
        self.seen_phones = set()
        self.seen_addresses = set()
        self.existing_keys = existing_keys or set()
        self.current_region = ""
        self.current_category = ""
        self.on_leads_update = on_leads_update
        self.leads_since_last_save = 0
        self.use_gps = use_gps
        self.geocoder = Geocoder() if use_gps else None
        self.duplicate_count = 0
        self.existing_skip_count = 0

    def setup_driver(self):
        """Start the Chrome browser"""
        print("\n[Setup] Starting browser...")
        
        options = Options()
        options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1920,1080")
        options.add_argument("--disable-extensions")
        options.add_argument(
            "--user-agent=Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )

        try:
            chromedriver_paths = ["/usr/bin/chromedriver", "/usr/local/bin/chromedriver"]
            system_driver = None
            
            for path in chromedriver_paths:
                if os.path.exists(path):
                    system_driver = path
                    break

            if system_driver:
                service = Service(system_driver)
            else:
                os.environ['WDM_LOG'] = '0'
                from webdriver_manager.chrome import ChromeDriverManager
                service = Service(ChromeDriverManager().install())

            self.driver = webdriver.Chrome(service=service, options=options)
            self.driver.set_page_load_timeout(60)
            print("[Setup] Browser ready")
            
        except Exception as e:
            logger.error(f"Browser setup failed: {e}")
            print(f"[Error] Browser setup failed: {e}")
            sys.exit(1)

    def build_search_url(self, category, region):
        """Create the Google Maps search URL"""
        category = (category or "").strip()
        region = (region or "").strip()
        
        self.current_category = category
        self.current_region = region
        
        search_term = category if category else "businesses"
        
        if self.use_gps and self.geocoder:
            coords = self.geocoder.geocode(region)
            
            if coords:
                lat, lon, display_name = coords
                url = f"https://www.google.com/maps/search/{search_term}/@{lat},{lon},13z"
                short_name = display_name.split(',')[0]
                print(f"  Location: {short_name} ({lat:.4f}, {lon:.4f})")
                return url
            else:
                print(f"  Could not geocode '{region}', using text search")
        
        query = f"{category} near {region}" if category else f"businesses near {region}"
        return f"https://www.google.com/maps/search/{query.replace(' ', '+')}"

    def is_valid_business_name(self, name):
        """Check if this is a real business name"""
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

    def is_duplicate(self, name, phone, address):
        """Check if this business is a duplicate"""
        key = normalize_for_comparison(name, phone, address)
        
        if key in self.existing_keys:
            self.existing_skip_count += 1
            return True
        
        if key[0] and len(key[0]) > 3:
            if key[0] in self.seen_names:
                self.duplicate_count += 1
                return True
            self.seen_names.add(key[0])
        
        if key[1] and len(key[1]) >= 8:
            if key[1] in self.seen_phones:
                self.duplicate_count += 1
                return True
            self.seen_phones.add(key[1])
        
        if key[2]:
            if key[2] in self.seen_addresses:
                self.duplicate_count += 1
                return True
            self.seen_addresses.add(key[2])
        
        self.existing_keys.add(key)
        return False

    def extract_business_info(self):
        """Extract all info from the currently open business panel"""
        try:
            time.sleep(1.5)
            data = {}

            name = "N/A"
            try:
                name = self.driver.find_element(By.CSS_SELECTOR, "h1.DUwDvf.lfPIob").text.strip()
            except:
                try:
                    name = self.driver.find_element(By.CSS_SELECTOR, "h1.DUwDvf").text.strip()
                except:
                    try:
                        name = self.driver.find_element(By.CSS_SELECTOR, "h1").text.strip()
                    except:
                        pass
            
            if not self.is_valid_business_name(name):
                return None
                
            data["name"] = name

            try:
                phone_btns = self.driver.find_elements(By.CSS_SELECTOR, "button[data-item-id^='phone:tel:']")
                if phone_btns:
                    phone = phone_btns[0].get_attribute("data-item-id") or ""
                    phone = phone.replace("phone:tel:", "").strip()
                    data["phone"] = phone if phone else "N/A"
                else:
                    data["phone"] = "N/A"
            except:
                data["phone"] = "N/A"

            try:
                site_links = self.driver.find_elements(By.CSS_SELECTOR, "a[data-item-id='authority']")
                if site_links:
                    href = site_links[0].get_attribute("href")
                    data["website"] = href.strip() if href else "N/A"
                else:
                    data["website"] = "N/A"
            except:
                data["website"] = "N/A"

            try:
                addr_btn = self.driver.find_element(By.CSS_SELECTOR, "button[data-item-id^='address']")
                addr = addr_btn.get_attribute("aria-label") or addr_btn.text
                data["address"] = addr.replace("Address: ", "").strip() if addr else "N/A"
            except:
                data["address"] = "N/A"

            if self.is_duplicate(data["name"], data["phone"], data["address"]):
                return None

            try:
                rating_span = self.driver.find_element(By.CSS_SELECTOR, "div.F7nice span[aria-hidden='true']")
                data["rating"] = rating_span.text.strip()
            except:
                data["rating"] = "N/A"

            try:
                review_els = self.driver.find_elements(By.CSS_SELECTOR, "div.F7nice span")
                found_reviews = "N/A"
                for r in review_els:
                    aria = r.get_attribute("aria-label")
                    if aria and "review" in aria.lower():
                        m = re.search(r"([\d,]+)", aria)
                        if m:
                            found_reviews = m.group(1)
                            break
                data["reviews"] = found_reviews
            except:
                data["reviews"] = "N/A"

            try:
                cat_btn = self.driver.find_element(By.CSS_SELECTOR, "button[jsaction*='category']")
                data["category"] = cat_btn.text.strip()
            except:
                data["category"] = "N/A"

            data["emails"] = "N/A"
            data["facebook"] = "N/A"
            data["instagram"] = "N/A"
            data["twitter"] = "N/A"
            data["linkedin"] = "N/A"
            data["youtube"] = "N/A"
            data["tiktok"] = "N/A"
            data["search_region"] = self.current_region
            data["search_category"] = self.current_category if self.current_category else "all"
            data["scraped_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            return data

        except Exception as e:
            logger.error(f"Error extracting business: {e}")
            return None

    def get_result_cards(self):
        return self.driver.find_elements(By.CSS_SELECTOR, "div.Nv2PK")

    def click_result_by_index(self, index, retries=3):
        for attempt in range(retries):
            try:
                cards = self.get_result_cards()
                if index >= len(cards):
                    return False

                card = cards[index]
                self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", card)
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
        except NoSuchElementException:
            return

        last_count = 0
        no_change = 0
        max_scrolls = max(12, math.ceil(target_count / 8) + 10)

        for i in range(max_scrolls):
            try:
                self.driver.execute_script("arguments[0].scrollTop = arguments[0].scrollHeight;", feed)
                time.sleep(CONFIG["scroll_pause"])

                current_count = len(self.get_result_cards())

                if current_count >= target_count:
                    break

                if current_count == last_count:
                    no_change += 1
                    if no_change >= 4:
                        break
                else:
                    no_change = 0

                last_count = current_count
            except:
                break

    def search_google_maps(self, category, region, max_results=20):
        try:
            print(f"\n[Search] {category if category else 'all'} in {region}")
            
            url = self.build_search_url(category, region)
            self.driver.get(url)
            time.sleep(8)

            print("  Loading results...")
            self.scroll_results(max_results)
            
            businesses = self.get_result_cards()
            total_found = len(businesses)
            print(f"  Found {total_found} results")

            idx = 0
            consecutive_failures = 0
            search_leads = []

            while len(search_leads) < max_results and idx < total_found:
                if consecutive_failures >= CONFIG["max_consecutive_failures"]:
                    break

                clicked = self.click_result_by_index(idx)
                if not clicked:
                    idx += 1
                    consecutive_failures += 1
                    continue

                lead = self.extract_business_info()
                if lead:
                    search_leads.append(lead)
                    self.leads.append(lead)
                    
                    name = lead["name"][:50] + "..." if len(lead["name"]) > 50 else lead["name"]
                    print(f"  [{len(search_leads)}/{max_results}] {name}")
                    
                    consecutive_failures = 0
                    
                    self.leads_since_last_save += 1
                    if self.leads_since_last_save >= CONFIG["save_every"]:
                        if self.on_leads_update:
                            self.on_leads_update(self.leads)
                        self.leads_since_last_save = 0
                        print(f"  [Auto-saved]")
                else:
                    consecutive_failures += 1

                idx += 1

                if idx >= total_found - 5 and len(search_leads) < max_results:
                    self.scroll_results(max_results + 20)
                    businesses = self.get_result_cards()
                    total_found = len(businesses)

            print(f"  Collected {len(search_leads)} new leads")
            if self.duplicate_count > 0:
                print(f"  Skipped {self.duplicate_count} duplicates")
            if self.existing_skip_count > 0:
                print(f"  Skipped {self.existing_skip_count} already in file")
            
            return search_leads

        except Exception as e:
            logger.error(f"Search error: {e}")
            print(f"  Error: {e}")
            return []

    def close(self):
        if self.driver:
            self.driver.quit()


class WebsiteScraper:
    """Scrapes emails and social media links from websites"""
    
    def __init__(self, timeout=10):
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"
        })

    def extract_emails(self, text):
        pattern = r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"
        found = re.findall(pattern, text)

        excluded = ["example.com", "yourdomain.com", "schema.org", "w3.org", 
                   "sentry.io", "wixpress.com", "wordpress.com", "googleapis.com"]

        cleaned = []
        for email in found:
            e = email.lower()
            if any(bad in e for bad in excluded):
                continue
            if e.endswith((".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp")):
                continue
            cleaned.append(e)

        return list(set(cleaned))

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
        social = {"facebook": "N/A", "instagram": "N/A", "twitter": "N/A",
                 "linkedin": "N/A", "youtube": "N/A", "tiktok": "N/A"}

        for link in soup.find_all("a", href=True):
            href = link.get("href", "").lower()
            
            if "facebook.com" in href or "fb.com" in href:
                if social["facebook"] == "N/A":
                    social["facebook"] = link.get("href", "N/A")
            elif "instagram.com" in href:
                if social["instagram"] == "N/A":
                    social["instagram"] = link.get("href", "N/A")
            elif "twitter.com" in href or "x.com" in href:
                if social["twitter"] == "N/A":
                    social["twitter"] = link.get("href", "N/A")
            elif "linkedin.com" in href:
                if social["linkedin"] == "N/A":
                    social["linkedin"] = link.get("href", "N/A")
            elif "youtube.com" in href or "youtu.be" in href:
                if social["youtube"] == "N/A":
                    social["youtube"] = link.get("href", "N/A")
            elif "tiktok.com" in href:
                if social["tiktok"] == "N/A":
                    social["tiktok"] = link.get("href", "N/A")

        for platform, patterns in SOCIAL_MEDIA_PATTERNS.items():
            if social[platform] == "N/A":
                for pattern in patterns:
                    matches = re.findall(pattern, html, re.IGNORECASE)
                    if matches:
                        url = matches[0]
                        if not url.startswith("http"):
                            url = "https://" + url
                        social[platform] = url
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
        result = {"emails": [], "facebook": "N/A", "instagram": "N/A", "twitter": "N/A",
                 "linkedin": "N/A", "youtube": "N/A", "tiktok": "N/A"}

        if not website_url or website_url == "N/A":
            return result

        try:
            if not website_url.startswith("http"):
                website_url = "https://" + website_url

            all_emails = set()
            all_social = {}

            html = self.get_page(website_url)
            if not html:
                return result

            soup = BeautifulSoup(html, "html.parser")
            
            all_emails.update(self.extract_emails(html))
            all_emails.update(self.extract_mailto_links(soup))
            all_social = self.extract_social_media(html, soup)

            contact_pages = self.find_contact_pages(website_url, soup)
            for page in contact_pages[:3]:
                time.sleep(CONFIG["email_scrape_pause"])
                page_html = self.get_page(page)
                if page_html:
                    page_soup = BeautifulSoup(page_html, "html.parser")
                    all_emails.update(self.extract_emails(page_html))
                    all_emails.update(self.extract_mailto_links(page_soup))
                    
                    page_social = self.extract_social_media(page_html, page_soup)
                    for platform, url in page_social.items():
                        if all_social.get(platform) == "N/A" and url != "N/A":
                            all_social[platform] = url

            result["emails"] = list(all_emails)
            result.update(all_social)
            return result

        except Exception as e:
            logger.debug(f"Error scraping {website_url}: {e}")
            return result


class LeadGenerator:
    """Main class that runs the whole scraping process"""
    
    def __init__(self, use_gps=True, output_file=None):
        self.website_scraper = WebsiteScraper()
        self.output_file = output_file
        self.maps_scraper = None
        self.use_gps = use_gps
        self.existing_df = None
        self.existing_keys = set()
        
        if output_file:
            self.existing_df, self.existing_keys = load_existing_leads(output_file)

    def save_progress(self, leads):
        if not leads or not self.output_file:
            return
        save_leads(leads, self.output_file, self.existing_df)

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
        print("LEAD GENERATION")
        print("="*60)
        print(f"  Mode:          {'GPS' if self.use_gps else 'Text'}")
        print(f"  Categories:    {len(categories)}")
        print(f"  Regions:       {len(regions)}")
        print(f"  Leads/search:  {max_results_per_search}")
        print(f"  Total target:  ~{total_target}")
        print(f"  Output:        {self.output_file}")
        if self.existing_df is not None:
            print(f"  Existing:      {len(self.existing_df)} leads")
        print("="*60)

        self.maps_scraper = GoogleMapsLeadScraper(
            on_leads_update=self.save_progress,
            use_gps=self.use_gps,
            existing_keys=self.existing_keys
        )
        self.maps_scraper.setup_driver()

        all_leads = []
        stats = []
        search_num = 0

        for category in categories:
            for region in regions:
                search_num += 1
                print(f"\n[{search_num}/{total_searches}]", end="")
                
                leads = self.maps_scraper.search_google_maps(category, region, max_results_per_search)
                
                stats.append({
                    "category": category if category else "all",
                    "region": region,
                    "count": len(leads)
                })
                
                all_leads.extend(leads)
                self.save_progress(all_leads)
                
                if search_num < total_searches:
                    time.sleep(3)

        self.maps_scraper.close()

        if not all_leads:
            print("\nNo new leads found")
            return []

        print(f"\n[Maps] Collected {len(all_leads)} new leads")

        all_leads = self.scrape_websites(all_leads)

        print("\n[Saving]")
        save_leads(all_leads, self.output_file, self.existing_df)

        self.print_summary(all_leads, stats)
        return all_leads

    def print_summary(self, leads, stats):
        total = len(leads)
        if total == 0:
            return
            
        with_phone = sum(1 for l in leads if l.get("phone") != "N/A")
        with_website = sum(1 for l in leads if l.get("website") != "N/A")
        with_email = sum(1 for l in leads if l.get("emails") != "N/A")
        with_social = sum(1 for l in leads if any(l.get(s) != "N/A" for s in ["facebook", "instagram", "twitter", "linkedin"]))

        print("\n" + "="*60)
        print("SUMMARY")
        print("="*60)
        
        print("\nSearches:")
        for s in stats:
            print(f"  {s['category'][:15]:<15} | {s['region'][:20]:<20} | {s['count']} leads")
        
        print(f"\nNew leads:    {total}")
        print(f"With phone:   {with_phone} ({with_phone*100//total if total else 0}%)")
        print(f"With website: {with_website} ({with_website*100//total if total else 0}%)")
        print(f"With email:   {with_email} ({with_email*100//total if total else 0}%)")
        print(f"With social:  {with_social} ({with_social*100//total if total else 0}%)")
        
        if self.existing_df is not None:
            print(f"\nTotal in file: {len(self.existing_df) + total}")
        
        print("="*60)


def interactive_mode():
    """Run in interactive mode"""
    
    print("\n" + "="*60)
    print("GOOGLE MAPS LEAD GENERATOR")
    print("="*60)
    
    # Search mode
    print("\nSearch mode:")
    print("  1. GPS-based (accurate)")
    print("  2. Text-based (faster)")
    
    mode = input("\nChoice [1]: ").strip()
    use_gps = mode != "2"
    print(f"  Using {'GPS' if use_gps else 'text'} search")

    # Categories
    print("\nCategories (comma-separated or file path, empty for all):")
    cat_input = input("> ").strip()
    
    if cat_input:
        categories = parse_input_list(cat_input, "categories")
        if not categories:
            categories = [""]
    else:
        categories = [""]
        print("  Searching all categories")

    # Regions
    print("\nRegions (comma-separated or file path):")
    reg_input = input("> ").strip()

    while not reg_input:
        print("  At least one region required")
        reg_input = input("> ").strip()

    regions = parse_input_list(reg_input, "regions")
    
    if not regions:
        return

    # Leads per search
    print(f"\nLeads per search (max {CONFIG['max_leads_per_search']}):")
    max_input = input("[20]: ").strip()
    
    max_results = 20
    if max_input:
        try:
            max_results = min(int(max_input), CONFIG['max_leads_per_search'])
        except:
            pass

    # Output path
    print("\nOutput path:")
    print("  - File:      /path/to/leads.csv (will create/append)")
    print("  - Directory: /path/to/folder/ (will create leads_TIMESTAMP.csv)")
    print("  - Empty:     creates leads_TIMESTAMP.csv in current dir")
    
    output_input = input("> ").strip()
    output_file = resolve_output_path(output_input)
    
    if not output_file:
        print("  Invalid output path")
        return
    
    print(f"  Output file: {output_file}")

    # Confirm
    print("\n" + "-"*60)
    print("Configuration:")
    print(f"  Mode:        {'GPS' if use_gps else 'Text'}")
    print(f"  Categories:  {len(categories)}")
    print(f"  Regions:     {len(regions)}")
    print(f"  Leads/each:  {max_results}")
    print(f"  Output:      {output_file}")
    print("-"*60)
    
    confirm = input("\nStart? [Y/n]: ").strip().lower()
    if confirm in ["n", "no"]:
        print("Cancelled")
        return

    # Run
    start = time.time()
    
    generator = LeadGenerator(use_gps=use_gps, output_file=output_file)
    leads = generator.run(categories, regions, max_results)
    
    duration = int(time.time() - start)
    mins = duration // 60
    secs = duration % 60

    if leads:
        print(f"\nDone in {mins}m {secs}s")
        print(f"Output: {output_file}")
    else:
        print("\nNo new leads found")


def main():
    """Main entry point"""
    
    parser = argparse.ArgumentParser(
        description="Google Maps Lead Generator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s
  %(prog)s -c "bar,cafe" -r "Paris France" -o leads.csv
  %(prog)s -c categories.txt -r regions.txt -o /path/to/data/
  %(prog)s -r "NYC" -o nyc_leads.csv --no-gps -l 50
        """
    )
    
    parser.add_argument("-c", "--categories", help="Categories (comma-separated or file)")
    parser.add_argument("-r", "--regions", help="Regions (comma-separated or file)")
    parser.add_argument("-o", "--output", help="Output path (file or directory)")
    parser.add_argument("-l", "--leads", type=int, default=20, help="Leads per search")
    parser.add_argument("--no-gps", action="store_true", help="Use text search")
    
    args = parser.parse_args()
    
    try:
        if args.regions:
            print("\n" + "="*60)
            print("GOOGLE MAPS LEAD GENERATOR")
            print("="*60)
            
            categories = parse_input_list(args.categories, "categories") if args.categories else [""]
            regions = parse_input_list(args.regions, "regions")
            
            output_file = resolve_output_path(args.output)
            if not output_file:
                print("  Invalid output path")
                return
            
            print(f"  Output: {output_file}")
            
            start = time.time()
            
            generator = LeadGenerator(use_gps=not args.no_gps, output_file=output_file)
            leads = generator.run(categories, regions, args.leads)
            
            duration = int(time.time() - start)
            print(f"\nCompleted in {duration//60}m {duration%60}s")
            
        else:
            interactive_mode()
            
    except KeyboardInterrupt:
        print("\n\nInterrupted. Check output file for partial results.")
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        print(f"\nError: {e}")
        print("See lead_generation.log for details")


if __name__ == "__main__":
    main()
