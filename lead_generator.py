import os
import re
import ssl
import sys
import json
import csv
import html
import time
import shutil
import urllib.parse
import urllib.request
import urllib.error
from datetime import datetime

# Set console output encoding to UTF-8 for Windows compatibility
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')


def load_dotenv(path):
    """Minimal .env loader (no extra dependency). Existing environment variables win."""
    if not os.path.exists(path):
        return
    with open(path, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, value = line.split('=', 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env'))


def is_configured(value):
    """False for empty values and for untouched .env.example placeholders (they all contain YOUR_)."""
    return bool(value) and 'YOUR_' not in value


LEADS_PER_RUN = 50
MAX_QUERIES = 30
MAX_EXTRA_PAGES = 4                  # up to 3 executive / management pages + the contact page per company
SEARCH_DELAY_SECONDS = 1.0           # pause after each search-engine request so Bing / DuckDuckGo do not throttle us
ENGINE_COOLDOWN_SECONDS = 120        # rest an engine this long once it answers with a block / captcha page
MAX_SEARCH_PASSES = 2                # if the first pass ends short because engines were throttled, wait out the cooldown and try new queries once more
MAX_ONLINE_LOOKUPS = 20              # per run: LinkedIn / web searches for the decision maker when the company's own site names nobody
GOOGLE_CSE_KEY = os.getenv("GOOGLE_CSE_KEY", "")   # optional: Google Programmable Search JSON API (free, 100 queries/day) for reliable lookups
GOOGLE_CSE_CX = os.getenv("GOOGLE_CSE_CX", "")
USE_OPENSTREETMAP = True             # free, keyless source of Dhaka facilities that already have website + phone + address
DHAKA_BBOX = "23.68,90.30,23.92,90.50"   # south,west,north,east - Dhaka metropolitan area (Keraniganj to Uttara)
OUTPUT_FILENAME = "dhaka_healthcare_leads.csv"
OFFER_FILE = os.getenv("OFFER_FILE", "")   # your products / services brief; default: offer.md (or .md) next to this script
OFFER_MAX_CHARS = 6000                     # keeps every AI call affordable - shorten the brief rather than raise this
OFFER_PROFILE = None                       # filled by main() from load_offer_profile()
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
OPENAI_MAX_ATTEMPTS = 3                    # retries on rate limits / server errors before falling back to the rule-based parser
TELEGRAM_MIN_FIT = int(os.getenv("TELEGRAM_MIN_FIT", "0") or 0)   # per-lead alerts only for fit_score >= this (0 = every lead)
REPORT_FILENAME = "run_report.html"        # self-contained HTML report of the last run (KPIs, fit, top leads, emails)
MANUAL_MINUTES_PER_LEAD = 25               # typical manual effort per lead: find the company, its decision maker, contacts, write a tailored email
AI_COST_PER_LEAD_USD = 0.003               # gpt-4o-mini with the offer brief in the prompt (estimate, for the run summary)

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}
# Company sites in Dhaka often have expired/self-signed certificates; we only read public pages from them
INSECURE_SSL = ssl._create_unverified_context()

FIELDNAMES = [
    "company_name", "healthcare_type", "dhaka_location", "full_address", "website", "phone", "generic_email",
    "facebook_page", "key_services", "fit_score", "fit_reason", "website_signals",
    "decision_maker_name", "decision_maker_title", "decision_maker_email", "decision_maker_phone",
    "decision_maker_linkedin", "other_decision_makers",
    "email_subject", "personalized_hook", "personalized_email", "timestamp",
]

AREAS = [
    'Dhanmondi', 'Gulshan', 'Banani', 'Uttara', 'Mirpur', 'Mohakhali', 'Panthapath', 'Motijheel',
    'Bashundhara', 'Badda', 'Tejgaon', 'Malibagh', 'Shyamoli', 'Mohammadpur', 'Wari', 'Khilgaon',
]
KNOWN_AREAS = tuple(AREAS)   # full neighbourhood list for address parsing, even when offer.md narrows the search AREAS
CATEGORIES = [
    'hospital', 'diagnostic center', 'clinic', 'pharmaceutical company', 'medical equipment supplier',
    'dental clinic', 'eye hospital', 'physiotherapy center', 'maternity hospital', 'medical college hospital',
]
GENERAL_QUERIES = [
    'list of private hospitals in Dhaka Bangladesh contact',
    'top diagnostic centers in Dhaka contact email',
    'pharmaceutical companies head office Dhaka Bangladesh contact',
    'medical equipment importers Dhaka Bangladesh contact',
]

# Real Dhaka Healthcare Entities Data for Fallback/Demo Execution
DEMO_DHAKA_LEADS = [
    {
        "company_name": "Labaid Diagnostic Center",
        "dhaka_location": "House 1, Road 4, Dhanmondi, Dhaka 1205",
        "healthcare_type": "Diagnostic Center & Hospital",
        "contact_phone": "+88029676356 / 10606",
        "generic_email": "info@labaidgroup.com",
        "company_url": "https://labaid.com.bd",
        "scraped_text": "Labaid Diagnostic Center is Bangladesh's premiere diagnostic chain. Managing Director: Dr. A. M. Shamim. Offering cardiac care, pathology, radiology, MRI, CT scan in Dhanmondi, Gulshan, Uttara."
    },
    {
        "company_name": "Popular Diagnostic Centre Ltd",
        "dhaka_location": "House 16, Road 2, Dhanmondi, Dhaka 1205",
        "healthcare_type": "Diagnostic & Medical Center",
        "contact_phone": "+8809666787801 / 10636",
        "generic_email": "info@populardiagnostic.com",
        "company_url": "https://populardiagnostic.com",
        "scraped_text": "Popular Diagnostic Centre Ltd is a leading healthcare service provider in Dhaka. Managing Director: Dr. Mustafizur Rahman. Head of Operations: Dr. Sushanta Kumar. Branches in Dhanmondi, English Road, Gulshan, Uttara."
    },
    {
        "company_name": "Ibn Sina Diagnostic & Imaging Center",
        "dhaka_location": "House 48, Road 9/A, Dhanmondi, Dhaka 1209",
        "healthcare_type": "Diagnostic & Specialized Hospital",
        "contact_phone": "+88029126625 / 10615",
        "generic_email": "info@ibnsinatrust.com",
        "company_url": "https://ibnsinatrust.com",
        "scraped_text": "Ibn Sina Trust operating specialized diagnostic and medical consultation centers across Dhaka. Managing Director: Professor Dr. A. K. M. Sadrul Islam. Chief Medical Officer: Dr. Mohammad Ali."
    },
    {
        "company_name": "Prescription Point Diagnostic & Consultation",
        "dhaka_location": "House 108, Road 8, Block C, Banani, Dhaka 1213",
        "healthcare_type": "Diagnostic & Consultation Center",
        "contact_phone": "+8801713333254",
        "generic_email": "info@ppointbd.com",
        "company_url": "https://ppointbd.com",
        "scraped_text": "Prescription Point Banani branch providing advanced pathology, cardiology, and consultant services. Chairman & Managing Director: Dr. Md. Abdul Majid. Operations Manager: Engr. Tanvir Ahmed."
    },
    {
        "company_name": "Square Hospitals Ltd",
        "dhaka_location": "18/F, Bir Uttam Qazi Nuruzzaman Sarak, West Panthapath, Dhaka 1205",
        "healthcare_type": "Tertiary Care Hospital",
        "contact_phone": "+88028144400 / 10616",
        "generic_email": "info@squarehospital.com",
        "company_url": "https://squarehospital.com",
        "scraped_text": "Square Hospitals Ltd is a 400-bed tertiary care hospital in Panthapath, Dhaka. Chief Executive Officer: Mr. Md. Esam Ebne Yousuf Siddique. Chief Medical Officer: Dr. Faisal Zaman. Head of Supply Chain & Procurement: Mr. M. A. Rahim."
    }
]

# ---------------------------------------------------------------- lead sources (all free, no API keys)
# Two search engines are queried for every search: Bing surfaces the facility's own website far more often than
# DuckDuckGo (which leans towards directory sites), and if one engine throttles automated traffic the other keeps
# the run going. OpenStreetMap adds facilities that already carry website / phone / address as structured data.
#
# DuckDuckGo markup: <a class="result__a" href="...">Title</a> ... <a class="result__snippet" ...>Snippet</a>
DDG_RESULT_RE = re.compile(
    r'<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>.*?<a[^>]*class="result__snippet"[^>]*>(.*?)</a>',
    re.S,
)
# Bing is read through its RSS output (&format=rss): plain XML with direct target URLs, no redirect wrappers
BING_RSS_ITEM_RE = re.compile(r'<item>.*?<title>(.*?)</title>.*?<link>(.*?)</link>.*?<description>(.*?)</description>', re.S)
# A Dhaka healthcare lead mentions the city / country or lives on a .bd domain. Anything else is off-topic - or one
# of the unrelated cached pages Bing serves when it suspects automated traffic - and is dropped before scraping.
RELEVANCE_RE = re.compile(r'dhaka|bangladesh|\.bd\b|\bbd\b', re.I)
# Blog / guide articles ("Maternity Leave in Bangladesh: Rules, Benefits") and government portals that are not care providers
ARTICLE_TITLE_RE = re.compile(r':\s*(rules|guide|benefits|tips|how|what|why|everything)|^(how|what|why|when)\b|\b(guide|tips|explained)\b', re.I)
HEALTHCARE_HINT_RE = re.compile(r'hospital|medical|health|clinic|diagnos|pharma|dental|physio|eye|maternity|cardiac', re.I)
# "Top 10 ...", "... - Verified List", "Doctors List", "... Directory" are aggregator pages, not a facility
AGGREGATOR_TITLE_RE = re.compile(r'\b(top|best)\s+\d+\b|\blists?\b|\blistings?\b|directory|\bnear me\b'
                                 r'|^(top|best|leading)\b[^|]*?\b[a-z]+s\s+in\s+(dhaka|bangladesh)', re.I)   # "Best Hospitals in Dhaka" = a list

SKIP_DOMAINS = (
    # search engines / social networks / Q&A
    'duckduckgo.com', 'bing.com', 'microsoft.com', 'google.com', 'facebook.com', 'wikipedia.org', 'youtube.com',
    'linkedin.com', 'instagram.com', 'twitter.com', 'x.com', 'quora.com', 'reddit.com', 'pinterest.com',
    # directories / aggregators: they list companies but are not the company itself
    'yelp.com', 'tripadvisor.com', 'justdial.com', 'yellowpages.com', 'mawbiz.com.bd', 'dhanmondi.bd',
    'businesslist.com.bd', 'bdtradeinfo.com', 'cybo.com', 'yellowpagesbd.com', 'bangladeshyellowpages.com',
    'medex.com.bd', 'doctorola.com', 'sasthyaseba.com', 'bdhospitals.com', 'hospitalsbd.com', 'kagoz.com',
    'bestinmeds.com', 'infoisinfo.com.bd', 'deho.ai', 'khojkorun.com', 'doctorbd24.com', 'doctorbangladesh.com',
    'investbangladesh.gov.bd',
)
DIRECTORY_HINT_RE = re.compile(r'yellowpage|directory|listing|bizlist|businesslist|\.blogspot\.|wordpress\.com', re.I)

OVERPASS_ENDPOINTS = ("https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter")
OSM_USER_AGENT = "dhaka-healthcare-leadgen/1.0 (python-urllib)"   # Overpass answers HTTP 406 to browser-like clients
OSM_TYPE_LABELS = {
    'hospital': 'Hospital', 'clinic': 'Clinic', 'doctors': 'Doctors Chamber / Medical Practice',
    'dentist': 'Dental Clinic', 'pharmacy': 'Pharmacy', 'laboratory': 'Diagnostic Laboratory',
    'centre': 'Healthcare Centre', 'physiotherapist': 'Physiotherapy Center', 'optometrist': 'Eye Care / Optometry',
    'blood_donation': 'Blood Bank', 'rehabilitation': 'Rehabilitation Center', 'birthing_centre': 'Maternity Center',
}


def build_queries():
    """~30 area x category queries, shuffled so every run explores new combinations."""
    import random
    combos = [f"{category} in {area} Dhaka contact email phone" for area in AREAS for category in CATEGORIES]
    random.shuffle(combos)
    return GENERAL_QUERIES + combos[:MAX_QUERIES - len(GENERAL_QUERIES)]


def to_domain(value):
    """Normalise any URL / hostname spelling to a bare lower-case domain for de-duplication."""
    raw = (value or '').strip()
    if not raw:
        return ''
    if not re.match(r'^https?://', raw, re.I):
        raw = 'https://' + raw
    return urllib.parse.urlparse(raw).netloc.lower().removeprefix('www.')


def is_skipped_domain(domain):
    """Search engines, social networks and business directories are never leads themselves."""
    return (not domain
            or any(domain == d or domain.endswith('.' + d) for d in SKIP_DOMAINS)
            or bool(DIRECTORY_HINT_RE.search(domain)))


def clean_html_text(fragment):
    """Strip tags, decode entities and collapse whitespace."""
    text = re.sub(r'<[^>]+>', '', fragment or '')
    return re.sub(r'\s+', ' ', html.unescape(text)).strip()


def make_search_lead(title_html, url, snippet_html, source):
    """Lead candidate from one search hit, or None when it is off-topic or an aggregator page."""
    title, snippet = clean_html_text(title_html), clean_html_text(snippet_html)
    if not RELEVANCE_RE.search(f"{title} {snippet} {url}") or AGGREGATOR_TITLE_RE.search(title) or ARTICLE_TITLE_RE.search(title):
        return None
    if to_domain(url).endswith('.gov.bd') and not HEALTHCARE_HINT_RE.search(f"{title} {snippet} {url}"):
        return None                                    # a ministry / investment portal is not a care provider
    return {
        "company_name": title,
        "company_url": url,
        "scraped_text": snippet,
        "dhaka_location": "Dhaka, Bangladesh",
        "healthcare_type": "Healthcare Provider",
        "source": source,
    }


ENGINE_PAUSED_UNTIL = {}     # engine name -> time.time() before which we leave it alone


def engine_available(name):
    return time.time() >= ENGINE_PAUSED_UNTIL.get(name, 0)


def pause_engine(name, reason):
    ENGINE_PAUSED_UNTIL[name] = time.time() + ENGINE_COOLDOWN_SECONDS
    print(f"[!] {name} is throttling automated searches ({reason}) - resting it for {ENGINE_COOLDOWN_SECONDS}s")


def fetch_search_page(url, engine):
    """Fetch a results page; HTTP 403 / 429 means the engine is blocking us, so rest it for a while."""
    req = urllib.request.Request(url, headers=BROWSER_HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return response.read().decode('utf-8', errors='replace')
    except urllib.error.HTTPError as e:
        if e.code in (403, 429):
            pause_engine(engine, f"HTTP {e.code}")
            return ''
        raise


def bing_results(query):
    """Raw (title, url, snippet) triples from Bing's RSS feed, or [] when Bing is resting / answered with a decoy page."""
    if not engine_available('Bing'):
        return []
    url = f"https://www.bing.com/search?q={urllib.parse.quote(query)}&format=rss&cc=BD&setlang=en"
    try:
        page = fetch_search_page(url, 'Bing')
        return [(clean_html_text(t), html.unescape(l).strip(), clean_html_text(d)) for t, l, d in BING_RSS_ITEM_RE.findall(page)]
    except Exception as e:
        print(f"[!] Bing notice: {e}")
        return []


def ddg_results(query):
    """Raw (title, url, snippet) triples from DuckDuckGo's HTML endpoint, or [] when it is resting / showing its captcha."""
    if not engine_available('DuckDuckGo'):
        return []
    url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(query)}"
    try:
        page = fetch_search_page(url, 'DuckDuckGo')
        if 'anomaly' in page and 'result__a' not in page:          # DDG's "are you a bot" challenge page
            pause_engine('DuckDuckGo', 'captcha challenge')
            return []
        results = []
        for href, title_html, snippet_html in DDG_RESULT_RE.findall(page):
            actual_url = html.unescape(href).strip()
            if actual_url.startswith('//'):
                actual_url = 'https:' + actual_url
            if "uddg=" in actual_url:                           # DDG wraps outbound links as //duckduckgo.com/l/?uddg=<target>
                parsed = urllib.parse.parse_qs(urllib.parse.urlparse(actual_url).query)
                if parsed.get('uddg'):
                    actual_url = parsed['uddg'][0]
            results.append((clean_html_text(title_html), actual_url, clean_html_text(snippet_html)))
        return results
    except Exception as e:
        print(f"[!] DuckDuckGo notice: {e}")
        return []


def search_bing(query, max_results=10):
    """Free Bing search via its RSS feed (no API key). Best at returning the facility's own website."""
    leads, raw = [], bing_results(query)
    for title, actual_url, snippet in raw:
        if is_skipped_domain(to_domain(actual_url)):
            continue
        lead = make_search_lead(title, actual_url, snippet, 'bing')
        if lead:
            leads.append(lead)
        if len(leads) >= max_results:
            break
    if len(raw) >= 5 and not leads:
        # Bing answers suspected bots with a random cached results page; the relevance filter drops it whole
        print("    [!] Bing returned unrelated results for this query (bot check) - relying on DuckDuckGo")
    return leads


def search_duckduckgo(query, max_results=10):
    """Free scraper for DuckDuckGo's HTML endpoint (no API key). Second engine / fallback."""
    leads = []
    for title, actual_url, snippet in ddg_results(query):
        if is_skipped_domain(to_domain(actual_url)):
            continue
        lead = make_search_lead(title, actual_url, snippet, 'duckduckgo')
        if lead:
            leads.append(lead)
        if len(leads) >= max_results:
            break
    return leads


def google_results(query):
    """Google Programmable Search JSON API - honours site: / quotes and never serves decoy pages. Free tier: 100 queries/day.
    Used only when GOOGLE_CSE_KEY and GOOGLE_CSE_CX are set (see .env.example)."""
    if not (is_configured(GOOGLE_CSE_KEY) and is_configured(GOOGLE_CSE_CX)) or not engine_available('Google'):
        return []
    url = ("https://www.googleapis.com/customsearch/v1?" +
           urllib.parse.urlencode({"key": GOOGLE_CSE_KEY, "cx": GOOGLE_CSE_CX, "q": query, "num": 10, "gl": "bd"}))
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": OSM_USER_AGENT}), timeout=15) as resp:
            data = json.loads(resp.read().decode('utf-8'))
        return [(item.get('title', ''), item.get('link', ''), item.get('snippet', '')) for item in data.get('items', [])]
    except urllib.error.HTTPError as e:
        detail = e.read().decode('utf-8', errors='replace')[:200]
        if e.code in (403, 429):
            pause_engine('Google', f"HTTP {e.code} - daily quota used or key not authorised")
        else:
            print(f"[!] Google search notice: HTTP {e.code} {detail}")
        return []
    except Exception as e:
        print(f"[!] Google search notice: {e}")
        return []


def search_raw(query):
    """Raw results from whichever engine answers first - Google (if configured), Bing, then DuckDuckGo. For lookups only."""
    for fetch, name in ((google_results, 'Google'), (bing_results, 'Bing'), (ddg_results, 'DuckDuckGo')):
        if not engine_available(name):
            continue
        results = fetch(query)
        if name != 'Google':
            time.sleep(SEARCH_DELAY_SECONDS)
        if results:
            return results
    return []


def search_web(query, per_engine=10):
    """Run the query on Bing and DuckDuckGo and merge the results by domain."""
    merged, seen = [], set()
    for engine, name in ((search_bing, 'Bing'), (search_duckduckgo, 'DuckDuckGo')):
        if not engine_available(name):
            continue
        for lead in engine(query, per_engine):
            domain = to_domain(lead['company_url'])
            if domain not in seen:
                seen.add(domain)
                merged.append(lead)
        time.sleep(SEARCH_DELAY_SECONDS)
    return merged


def search_openstreetmap():
    """Dhaka healthcare facilities from OpenStreetMap (Overpass API) that have a website tagged.
    Free and keyless; phone, email and address come back as structured data, no scraping needed."""
    query = f"""[out:json][timeout:90];
(
  nwr["amenity"~"^(hospital|clinic|doctors|dentist|pharmacy)$"]["website"]({DHAKA_BBOX});
  nwr["healthcare"]["website"]({DHAKA_BBOX});
);
out tags;"""
    elements = []
    for endpoint in OVERPASS_ENDPOINTS:
        try:
            req = urllib.request.Request(endpoint, data=urllib.parse.urlencode({"data": query}).encode(),
                                         headers={"User-Agent": OSM_USER_AGENT})
            with urllib.request.urlopen(req, timeout=120) as resp:
                elements = json.loads(resp.read().decode('utf-8')).get('elements', [])
            break
        except Exception as e:
            print(f"[!] OpenStreetMap notice ({to_domain(endpoint)}): {e}")

    leads = []
    for element in elements:
        tags = element.get('tags', {})
        website = (tags.get('website') or tags.get('contact:website') or '').strip()
        name = (tags.get('name:en') or tags.get('name') or '').strip()
        if not website or not name:
            continue
        if not re.match(r'^https?://', website, re.I):
            website = 'https://' + website
        if is_skipped_domain(to_domain(website)):      # e.g. a Facebook page tagged as the website
            continue

        kind = tags.get('healthcare') or tags.get('amenity') or ''
        healthcare_type = OSM_TYPE_LABELS.get(kind, 'Healthcare Provider')
        tag = lambda key: '' if (tags.get(key) or '').strip().upper() in ('', 'N/A') else tags[key].strip()
        street = ' '.join(filter(None, [tag('addr:housenumber'), tag('addr:street')]))
        address = tag('addr:full') or ', '.join(filter(None, [
            street, tag('addr:suburb'), tag('addr:city') or 'Dhaka', tag('addr:postcode')]))
        # Area = the first known Dhaka neighbourhood named in the address (OSM rarely fills addr:suburb here)
        location = next((area for area in KNOWN_AREAS if area.lower() in address.lower()), tag('addr:suburb') or 'Dhaka')
        phone = tags.get('phone') or tags.get('contact:phone') or tags.get('phone:mobile') or ''
        email = tags.get('email') or tags.get('contact:email') or ''

        leads.append({
            "company_name": name,
            "company_url": website,
            "healthcare_type": healthcare_type,
            "dhaka_location": location,
            "full_address": address,
            "contact_phone": phone,
            "generic_email": email,
            "scraped_text": ' · '.join(filter(None, [
                'OpenStreetMap record', healthcare_type, address,
                f"Phone {phone}" if phone else '', f"Email {email}" if email else ''])),
            "source": "openstreetmap",
        })
    return leads


# ---------------------------------------------------------------- company page fetching
# Which same-site pages to read besides the homepage. In Bangladesh the executives' names almost always sit on
# "Message from Chairman / Managing Director / CEO", "Board of Directors" or "Management" pages, signed at the bottom.
LINK_SCORES = (
    (3, re.compile(r'message|speech|desk[-_ ]of|chairman|managing[-_ ]?director|\bmd\b|\bceo\b|founder', re.I)),
    (2, re.compile(r'board|management|leadership|our[-_ ]?team|directors|executives|administration|governing', re.I)),
    (1, re.compile(r'about|profile|who[-_ ]we[-_ ]are|overview', re.I)),
)
NEGATIVE_LINK_RE = re.compile(r'/(departments?|services?|treatments?|packages?|doctors?|consultants?|news|events?|gallery|blog|careers?|jobs?)/|/dr-|appointment|login|register|\.(xml|json)$', re.I)
CONTACT_LINK_RE = re.compile(r'contact', re.I)
MAX_PEOPLE_PAGES = 3


PARKED_PAGE_RE = re.compile(r'defaultwebpage\.cgi|domain is parked|coming soon|under construction|index of /|buy this domain|website is temporarily|account suspended', re.I)
META_REFRESH_RE = re.compile(r'<meta[^>]+http-equiv=["\']?refresh["\']?[^>]+content=["\']?\s*\d+\s*;\s*url=([^"\'>\s]+)', re.I)


def swap_www(url):
    parts = urllib.parse.urlsplit(url)
    host = parts.netloc[4:] if parts.netloc.lower().startswith('www.') else 'www.' + parts.netloc
    return urllib.parse.urlunsplit((parts.scheme, host, parts.path, parts.query, parts.fragment))


def fetch_page(url, timeout=8, _hops=0):
    """(html, status, final_url). Follows one meta-refresh redirect, retries www / non-www when DNS fails, and reports
    WHY a site gave nothing ('dns failure', 'http 403 (blocks automated visits)', 'parked / default page', 'timeout')."""
    try:
        req = urllib.request.Request(url, headers={**BROWSER_HEADERS, "Accept": "text/html,*/*"})
        with urllib.request.urlopen(req, timeout=timeout, context=INSECURE_SSL) as resp:
            body = resp.read(1_500_000).decode('utf-8', errors='replace')
            final_url = resp.geturl()
    except urllib.error.HTTPError as e:
        return '', f"http {e.code}" + (" (blocks automated visits)" if e.code in (401, 403, 429, 503) else " (server error)" if e.code >= 500 else ''), url
    except urllib.error.URLError as e:
        reason = str(e.reason)
        if ('getaddrinfo' in reason or 'Name or service' in reason or 'nodename' in reason) and _hops == 0:
            body, status, final_url = fetch_page(swap_www(url), timeout, _hops=1)
            return (body, status, final_url) if body else ('', 'dns failure (domain no longer exists)', url)
        return '', 'timeout' if 'timed out' in reason else f"connection error ({reason[:40]})", url
    except Exception as e:
        return '', 'timeout' if 'timed out' in str(e) else f"connection error ({str(e)[:40]})", url

    refresh = META_REFRESH_RE.search(body[:4000])
    if refresh and len(body) < 6000 and _hops < 2:               # tiny page whose only job is to send visitors elsewhere
        target = urllib.parse.urljoin(final_url, html.unescape(refresh.group(1)))
        if not PARKED_PAGE_RE.search(target):
            return fetch_page(target, timeout, _hops=_hops + 1)
    if PARKED_PAGE_RE.search(body[:6000]) and len(body) < 6000:
        return '', 'parked / default page (site not live)', final_url
    return body, 'ok', final_url


def fetch_url(url, timeout=8):
    return fetch_page(url, timeout)[0]


def find_extra_links(page_html, base_url):
    """Up to 3 'people' pages (executive messages > board / management > about) plus the contact page, same site only."""
    base_host = to_domain(base_url)
    people, contact = {}, []
    for href, label in re.findall(r'<a[^>]+href=["\']([^"\'#]+)["\'][^>]*>(.*?)</a>', page_html, re.S | re.I):
        full = urllib.parse.urljoin(base_url, html.unescape(href)).split('#')[0]
        if to_domain(full) != base_host or full.rstrip('/') == base_url.rstrip('/'):
            continue
        if re.search(r'\.(pdf|jpe?g|png|gif|zip|docx?)$', urllib.parse.urlparse(full).path, re.I):
            continue
        haystack = f"{urllib.parse.urlparse(full).path} {clean_html_text(label)}"
        if CONTACT_LINK_RE.search(haystack):
            if full not in contact:
                contact.append(full)
            continue
        if NEGATIVE_LINK_RE.search(haystack):           # "/department/pain-management" is not a management page
            continue
        score = max((s for s, pattern in LINK_SCORES if pattern.search(haystack)), default=0)
        if score > people.get(full, 0):
            people[full] = score
    ranked = sorted(people, key=lambda u: -people[u])
    return (ranked[:MAX_PEOPLE_PAGES] + contact[:1])[:MAX_EXTRA_PAGES]


def page_to_text(page_html):
    """Visible text of a page. Tags become spaces so 'Rahman</p><p>Chairman' stays two words."""
    text = re.sub(r'<(script|style|noscript)[\s\S]*?</\1>', ' ', page_html or '', flags=re.I)
    text = re.sub(r'<[^>]+>', ' ', text)
    return re.sub(r'\s+', ' ', html.unescape(text)).strip()


def extract_signals(raw_html):
    """Emails, phones (BD formats), LinkedIn and Facebook links found in the raw HTML."""
    text = html.unescape(raw_html or '')

    emails = [m.lower() for m in re.findall(r'href=["\']mailto:([^"\'?]+)', text, re.I)]
    emails += [m.lower() for m in re.findall(r'[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}', text, re.I)]
    emails = [e.strip() for e in emails
              if not re.search(r'\.(png|jpe?g|gif|svg|webp|css|js)$', e)
              and not re.search(r'example\.|sentry\.|wixpress|yourdomain|domain\.com|email\.com', e)]

    phones = re.findall(r'href=["\']tel:([^"\']+)', text, re.I)
    phones += re.findall(r'(?:\+?880|0)?[\s-]?1[3-9]\d{2}[\s-]?\d{3}[\s-]?\d{3}\b', text)      # BD mobile
    phones += re.findall(r'(?:\+?880|0)?[\s-]?2[\s-]?\d{4}[\s-]?\d{3,4}\b', text)              # Dhaka landline
    phones += re.findall(r'\b1[06]\d{3}\b', text)                                               # 5-digit hotlines

    linkedin = re.findall(r'https?://(?:[a-z]{2,3}\.)?linkedin\.com/(?:in|company)/[a-z0-9_%.-]+', text, re.I)
    facebook = [u for u in re.findall(r'https?://(?:www\.|m\.)?(?:facebook|fb)\.com/[a-z0-9_.-]+', text, re.I)
                if not re.search(r'/(sharer|share|plugins|dialog|login|tr)\b', u, re.I)]

    dedupe = lambda seq, n: list(dict.fromkeys(s.strip() for s in seq))[:n]
    return {
        "emails": dedupe(emails, 10), "phones": dedupe(phones, 10),
        "linkedin": dedupe(linkedin, 5), "facebook": dedupe(facebook, 3),
    }


# Digital-maturity checks on the prospect's own site. They give the AI hard evidence for the fit score and a specific
# opening line ("your site still takes appointments by phone only ..."), instead of guesses from the marketing copy.
FEATURE_CHECKS = (
    ("online appointment booking", r'href=["\'][^"\']*(?:appointment|booking|book-now)|online appointment|book (?:an )?appointment online|appointment form'),
    ("online reports / patient portal", r'patient portal|online report|report download|download (?:your |lab )?reports?|view (?:your )?reports? online|e-?reports?\b|report portal'),
    ("mobile app", r'play\.google\.com|apps\.apple\.com|app store|google play|download (?:our|the) app'),
    ("telemedicine / video consultation", r'telemedicine|tele-?health|video consult|online consult(?:ation)?|virtual consult'),
    ("online payment", r'bkash|nagad|sslcommerz|online payment|pay online|pay now'),
    ("live chat / WhatsApp", r'wa\.me/|whatsapp|tawk\.to|livechat|live chat|m\.me/'),
)
TECH_CHECKS = (
    ("WordPress", r'wp-content|wp-includes|content=["\']WordPress'), ("Wix", r'wixstatic|wix\.com|_wix'),
    ("Joomla", r'joomla'), ("Drupal", r'drupal'), ("Blogger", r'blogger\.com|blogspot'), ("Squarespace", r'squarespace'),
    ("React / Next.js", r'__NEXT_DATA__|/_next/|data-reactroot'), ("Angular", r'ng-version'),
    ("Laravel", r'laravel_session'), ("ASP.NET", r'__VIEWSTATE|\.aspx\b'), ("PHP", r'\.php\b'),
)
COUNT_CHECKS = (
    # (?<!\d) keeps the tail of a phone number ("...896433 DEPARTMENTS") from being read as a count
    ("beds", r'(?<!\d)(\d{2,4})\s*[-+]?\s*(?:bedded|beds?)\b'), ("branches", r'(?<!\d)(\d{1,3})\s*\+?\s*branches'),
    ("doctors", r'(?<!\d)(\d{2,4})\s*\+?\s*(?:doctors|consultants|physicians|specialists)'), ("departments", r'(?<!\d)(\d{1,3})\s*\+?\s*departments'),
)
COPYRIGHT_RE = re.compile(r'(?:©|&copy;|copyright)[^0-9<]{0,40}(?:(?:19|20)\d\d\s*[-–]\s*)?((?:19|20)\d\d)', re.I)


def extract_website_signals(raw_html, url):
    """Deterministic checks of the prospect's HTML: digital features, tech stack, size figures, site freshness."""
    page = html.unescape(raw_html or '')
    if not page:
        return {"summary": "", "features_present": [], "features_missing": []}
    text = page_to_text(page)
    present = [label for label, pattern in FEATURE_CHECKS if re.search(pattern, page, re.I)]
    missing = [label for label, _ in FEATURE_CHECKS if label not in present]
    tech = [label for label, pattern in TECH_CHECKS if re.search(pattern, page, re.I)][:3]

    parts = [f"{label}: {'yes' if label in present else 'no'}" for label, _ in FEATURE_CHECKS]
    parts.append(f"tech: {', '.join(tech) if tech else 'unknown'}")
    for label, pattern in COUNT_CHECKS:
        numbers = [int(n) for n in re.findall(pattern, text, re.I) if int(n) < 10000]
        if numbers:
            parts.append(f"{label}: {max(numbers)}")
    years = [int(y) for y in COPYRIGHT_RE.findall(page)]
    if years:
        latest = max(years)
        parts.append(f"copyright year: {latest}" + (" (site looks unmaintained)" if latest < datetime.now().year - 1 else ""))
    parts.append(f"https: {'yes' if (url or '').lower().startswith('https://') else 'no'}")
    return {"summary": '; '.join(parts), "features_present": present, "features_missing": missing}


def executive_mentions(text, window=120, limit=8):
    """Text around every executive title on the fetched pages - the exact evidence the AI needs, whatever got truncated."""
    snippets, last_end = [], -1
    for match in title_regex().finditer(text):
        if match.start() < last_end:
            continue                                   # overlapping window already captured
        start, end = max(0, match.start() - window), min(len(text), match.end() + window)
        snippet = text[start:end].strip()
        if not re.search(r'[A-Z][a-z]+\s+[A-Z]', snippet):
            continue                                   # no capitalised words nearby = a nav / heading mention
        snippets.append(('…' if start else '') + snippet + ('…' if end < len(text) else ''))
        last_end = end
        if len(snippets) >= limit:
            break
    return snippets


def fetch_company_pages(url):
    """Homepage + up to 3 executive / management pages + contact page. Returns an excerpt for the AI (head + tail of
    each page - executive messages are signed at the bottom), the full text for the rule-based parser, and the signals."""
    homepage, status, final_url = fetch_page(url, timeout=10) if url else ('', 'no website', url)
    if homepage and to_domain(final_url) != to_domain(url):
        status = f"ok (redirects to {to_domain(final_url)})"
        url = final_url                                           # links, https check and dedupe use the real site
    extra_urls = find_extra_links(homepage, url) if homepage else []
    extra_pages = [(u, fetch_url(u)) for u in extra_urls]

    def excerpt(text, head, tail):
        return text if len(text) <= head + tail else f"{text[:head]} … {text[-tail:]}"

    sections, full = [], []
    if homepage:
        home_text = page_to_text(homepage)
        sections.append('[Homepage] ' + excerpt(home_text, 2000, 400))
        full.append(home_text)
    for u, page in extra_pages:
        if page:
            text = page_to_text(page)
            sections.append(f'[{u}] ' + excerpt(text, 900, 600))
            full.append(text)

    all_html = '\n'.join([homepage] + [p for _, p in extra_pages])
    signals = extract_signals(all_html)
    website = extract_website_signals(all_html, url)
    fetched = ([url] if homepage else []) + [u for u, p in extra_pages if p]
    full_text = '\n'.join(full)[:80000]
    if not homepage:
        website["summary"] = f"website: {status}"                  # tells the reader WHY there is no page evidence
    return {
        "site_status": status,
        "final_url": url,
        "scraped_text": '\n\n'.join(sections),
        "full_text": full_text,
        "executive_mentions": ' | '.join(executive_mentions(full_text)),
        "pages_fetched": ', '.join(fetched),
        "found_emails": ', '.join(signals["emails"]),
        "found_phones": ', '.join(signals["phones"]),
        "found_linkedin": ', '.join(signals["linkedin"]),
        "found_facebook": ', '.join(signals["facebook"]),
        "website_signals": website["summary"],
        "features_present": website["features_present"],
        "features_missing": website["features_missing"],
    }


# ---------------------------------------------------------------- offer profile (who you are, what you sell)
# offer.md is free text about your company and products. It is handed to the AI as context for the fit score and the
# personalised email. Optional "## Target healthcare types" / "## Target areas" / "## Target decision makers" bullet
# sections additionally steer the search queries and the decision-maker choice (see offer.example.md).
OFFER_SECTION_KEYS = {
    'target_types': re.compile(r'target (healthcare )?(types|customers|segments)|ideal customer', re.I),
    'target_areas': re.compile(r'target (areas|locations)', re.I),
    'target_titles': re.compile(r'target (decision makers|titles|roles|buyers)|decision makers we want', re.I),
}
BULLET_RE = re.compile(r'^\s*(?:[-*+]|\d+[.)])\s+(.*\S)\s*$')
HEADING_RE = re.compile(r'^\s{0,3}#{1,6}\s+(.*?)\s*#*\s*$')
COMPANY_NAME_RE = re.compile(r'^\s*[-*]?\s*(?:company\s*)?name\s*:\s*(.+)$', re.I | re.M)
COMPANY_MENTION_RE = re.compile(r'(?:for|on behalf of|represent(?:ing)?)\s+((?:[A-Z][\w&.\-]*\s+){0,4}(?:Ltd|Limited|Inc|LLC|PLC|Co)\.?)')


def short_term(item):
    """'diagnostic center - pathology, imaging' / 'hospital (private)' -> the term itself, for search queries."""
    return re.split(r'\s+[-–—:]\s+|\s*\(', item)[0].strip()


def markdown_sections(text):
    """{heading: [lines]} for every '#..######' block; text before the first heading is under ''."""
    sections, current = {'': []}, ''
    for line in text.splitlines():
        match = HEADING_RE.match(line)
        if match:
            current = match.group(1).strip()
            sections.setdefault(current, [])
        else:
            sections[current].append(line)
    return sections


def load_offer_profile():
    """Read the offer brief. Returns None when there is none (emails stay generic)."""
    here = os.path.dirname(os.path.abspath(__file__))
    for name in ([OFFER_FILE] if OFFER_FILE else ['offer.md', '.md']):
        path = name if os.path.isabs(name) else os.path.join(here, name)
        if os.path.isfile(path):
            break
    else:
        return None

    with open(path, encoding='utf-8-sig') as f:
        raw = re.sub(r'<!--.*?-->', '', f.read(), flags=re.S)            # template instructions live in HTML comments
    lines = [l for l in raw.splitlines() if 'TODO' not in l]             # unfilled template lines are ignored
    text = '\n'.join(lines).strip()
    if not any(BULLET_RE.match(l) or (l.strip() and not HEADING_RE.match(l)) for l in lines):
        return None
    if len(text) > OFFER_MAX_CHARS:
        print(f"[!] {os.path.basename(path)} is {len(text)} characters - only the first {OFFER_MAX_CHARS} are sent to the AI.")
        text = text[:OFFER_MAX_CHARS]

    profile = {"path": path, "text": text, "company_name": "", "target_types": [], "target_areas": [], "target_titles": []}
    sections = markdown_sections(text)
    for heading, body in sections.items():
        for key, pattern in OFFER_SECTION_KEYS.items():
            if pattern.search(heading):
                profile[key] = [short_term(m.group(1)) for m in map(BULLET_RE.match, body) if m]
    named = COMPANY_NAME_RE.search(text) or COMPANY_MENTION_RE.search(text)
    profile["company_name"] = named.group(1).strip().rstrip('.') if named else ''
    profile.update(offer_extras(text, sections))
    return profile


def offer_extras(text, sections):
    """Products, call to action, signature and 'digital vendor?' flag - used by the offline email writer."""
    product_lines = []
    for heading, body in sections.items():
        if heading and re.search(r'product|service|solution|offer|capabilit', heading, re.I):
            product_lines += [m.group(1).strip() for m in map(BULLET_RE.match, body) if m]
    if not product_lines:                            # free-text brief: bullets shaped "Name — what it does"
        product_lines = [m.group(1).strip() for m in map(BULLET_RE.match, text.splitlines()) if m and re.search(r'\s[—–-]\s', m.group(1))]
    cta = ''
    match = (re.search(r'call to action\s*[:\-–]?\s*\[?([^\]\n]+)', text, re.I)
             or re.search(r'(\d+\s*[-–]?\s*\d*\s*minute[^\]\n.]*)', text, re.I))
    if match:
        cta = match.group(1).strip(' .[]')
    signature = []
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if re.search(r'signature', line, re.I):
            for following in lines[i + 1:i + 7]:
                following = re.sub(r'[<>]', '', following).strip()
                if not following:
                    break
                signature.append(following)
            break
    digital = bool(re.search(r'software|digital|\berp\b|\bapps?\b|system|cloud|\bai\b|automation|it services', text, re.I))
    return {"product_lines": product_lines[:12], "cta": cta, "signature": signature, "digital": digital}


def apply_offer_targeting(profile):
    """Target lists from the brief replace the default search lists and extend the offline title parser."""
    global AREAS, CATEGORIES, TITLE_LABELS
    if profile['target_types']:
        CATEGORIES = profile['target_types']
    if profile['target_areas']:
        AREAS = profile['target_areas']
    TITLE_LABELS = list(dict.fromkeys(profile['target_titles'] + TITLE_LABELS))   # the brief's titles rank first


def offer_prompt_context(profile):
    """Extra AI instructions when a brief is present. Keep the wording in sync with n8n node '0. Offer Profile'."""
    if not profile:
        return ""
    titles = ', '.join(profile['target_titles']) or \
        'the commercial decision maker (Managing Director, CEO, Chairman, Head of Procurement / IT / Operations or similar)'
    return (
        "\n\nOFFER PROFILE - you research and write on behalf of the company described below. "
        f"When the site names several people, prefer decision makers with these titles: {titles}. "
        "Add fit_score ('1'-'5': how well this organisation matches the profile's target customers and could use its products or services) "
        "and fit_reason (one sentence citing concrete evidence from their site - e.g. manual booking, no patient portal, many departments, multiple branches). "
        "For the email: open with ONE specific observation about this organisation (a named service, department, branch, accreditation, technology or scale from the scraped text), "
        "connect it to the ONE or TWO capabilities from the profile that fit best and state the likely operational outcome for them, "
        "close with the profile's call to action and use its signature. Follow the profile's own writing requirements (length, subject line, tone) "
        "wherever they differ from the defaults above; treat bracketed placeholders in the profile such as [Name] or [Detail] as slots to fill from the lead data, "
        "and leave sender placeholders like [Sender Name] exactly as written. Never open with 'I hope this message finds you well' or similar filler; no generic praise.\n"
        f"--- OFFER PROFILE ---\n{profile['text']}\n--- END OFFER PROFILE ---"
    )


# ---------------------------------------------------------------- AI / rule-based extraction
def format_lookup(lookup):
    return f"{lookup['name']} - {lookup['title']} ({lookup['source']}) {lookup.get('linkedin', '')}".strip() if lookup and lookup.get('name') else ''


def identify_decision_maker_with_openai(api_key, lead_info):
    """Uses OpenAI API (gpt-4o-mini) to identify decision makers and write custom cold email"""
    if not is_configured(api_key):
        # Fallback intelligent extraction for testing without active key
        return mock_openai_response(lead_info)

    url = "https://api.openai.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

    system_prompt = (
        "You are an expert B2B healthcare intelligence researcher covering Dhaka, Bangladesh "
        "(hospitals, diagnostic centers, clinics, pharmaceutical companies, medical equipment suppliers). "
        "You receive a search result, text scraped from the organisation's website (homepage plus about/management/contact pages) "
        "and lists of emails, phone numbers and social links found in the raw HTML. "
        "Extract every detail you can about the organisation and its key decision maker "
        "(Managing Director, CEO, Chairman, Medical Director, Head of Operations, General Manager, Procurement Manager or similar), "
        "then write a concise personalized B2B cold email (under 120 words) addressed to that person by name and title when known. "
        "Executive names usually appear at the END of 'Message from Chairman / Managing Director / CEO' pages as a signature, or on board / management pages; "
        "when a title lists several people over time (a history of directors), the current holder is the one signing the message or the last one listed. "
        "If a name appears only in Bengali script, transliterate it into English and add the Bengali original in brackets. "
        "An online lookup result (LinkedIn) may be used for the decision maker when the site names nobody - then set decision_maker_linkedin to that profile URL. "
        "Rules: use only information present in the input; return an empty string when something is not available; never invent names or phone numbers. "
        "decision_maker_email may be predicted from the company's email pattern (e.g. md@company-domain) only when no direct email is found - "
        "then append ' (predicted)' to it. other_decision_makers lists additional executives as 'Name - Title' separated by '; '. "
        "Respond strictly with a JSON object using exactly these keys: "
        '{"company_name": "", "healthcare_type": "", "dhaka_location": "", "full_address": "", "contact_phone": "", "generic_email": "", '
        '"facebook_page": "", "key_services": "", "decision_maker_name": "", "decision_maker_title": "", "decision_maker_email": "", '
        '"decision_maker_phone": "", "decision_maker_linkedin": "", "other_decision_makers": "", "fit_score": "", "fit_reason": "", '
        '"email_subject": "", "personalized_email": "", "personalized_hook": ""}'
        + offer_prompt_context(OFFER_PROFILE)
    )

    known_facts = '; '.join(f"{label} {lead_info[key]}" for key, label in
                            (('full_address', 'address:'), ('contact_phone', 'phone:'), ('generic_email', 'email:'))
                            if lead_info.get(key))
    user_content = (
        f"Company URL: {lead_info.get('company_url')}\n"
        f"Source: {lead_info.get('source', 'web search')}\n"
        + (f"Known structured facts (already verified, reuse them): {known_facts}\n" if known_facts else '')
        + f"Search title: {lead_info.get('company_name')}\n"
        f"Search snippet: {lead_info.get('search_snippet', '')}\n"
        f"Pages fetched: {lead_info.get('pages_fetched', '')}\n"
        f"Emails found in HTML: {lead_info.get('found_emails', '')}\n"
        f"Phones found in HTML: {lead_info.get('found_phones', '')}\n"
        f"LinkedIn links found: {lead_info.get('found_linkedin', '')}\n"
        f"Facebook links found: {lead_info.get('found_facebook', '')}\n"
        f"Website signals (deterministic checks of their HTML - cite these in fit_reason and the email opener): {lead_info.get('website_signals', '') or 'not available'}\n"
        f"Executive mentions (text around every executive title found on their pages - names are usually right before the title): {lead_info.get('executive_mentions', '') or 'none found'}\n"
        f"Online lookup (LinkedIn / web search for this company's executives): {format_lookup(lead_info.get('online_lookup')) or 'not performed or nothing found'}\n"
        f"Website status: {lead_info.get('site_status', 'unknown')}\n"
        f"Scraped page content: {lead_info.get('scraped_text', '')}"
    )

    payload = {
        "model": OPENAI_MODEL,
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content}
        ]
    }

    last_error = ''
    for attempt in range(1, OPENAI_MAX_ATTEMPTS + 1):
        try:
            req = urllib.request.Request(url, data=json.dumps(payload).encode('utf-8'), headers=headers)
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read().decode('utf-8'))
            return json.loads(data['choices'][0]['message']['content'])
        except urllib.error.HTTPError as e:
            # Surface the API's own reason (invalid key, quota, rate limit) instead of a bare status code
            detail = e.read().decode('utf-8', errors='replace')[:300]
            last_error = f"HTTP {e.code}: {detail}"
            if e.code not in (429, 500, 502, 503, 504) or 'insufficient_quota' in detail:
                break                                   # bad key / request / exhausted quota - retrying cannot help
        except Exception as e:
            last_error = str(e)                         # timeout, connection reset, malformed JSON
        if attempt < OPENAI_MAX_ATTEMPTS:
            wait = 3 * attempt
            print(f"    [!] OpenAI attempt {attempt} failed ({last_error[:90]}) - retrying in {wait}s")
            time.sleep(wait)
    print(f"[!] OpenAI {last_error[:200]}. Falling back to rule-based parser.")
    return mock_openai_response(lead_info)


# Executive titles in priority order (the offer brief's "Target decision makers" are put in front at runtime).
TITLE_LABELS = [
    "Founder & Managing Director", "Founder and Managing Director", "Chairman & Managing Director", "Chairman and Managing Director",
    "Deputy Managing Director", "Managing Director", "MD & CEO", "Chief Executive Officer", "CEO", "Founder & Chairman",
    "Founding Chairman", "Chairman", "Vice Chairman", "Chief Operating Officer", "Executive Director", "Director General",
    "Medical Director", "Chief Medical Officer", "Director of Operations", "Director Operations", "Director (Hospital)",
    "Head of Operations", "General Manager", "Chief Information Officer", "Head of IT", "IT Manager",
    "Head of Procurement", "Procurement Manager",
]
HONORIFICS = {"dr", "prof", "professor", "md", "mr", "mrs", "ms", "engr", "eng", "adv", "hon", "brig", "col", "capt",
              "major", "maj", "gen", "lt", "cdr", "cmdr", "justice", "barrister", "alhaj", "al-haj", "hajee", "haji", "engineer"}
NAME_PARTICLES = {"bin", "ibn", "binte", "al", "ul", "ur", "e", "van", "de"}   # lowercase words that can sit inside a name
DEGREE_TOKENS = {"mba", "mbbs", "fcps", "phd", "mph", "frcs", "mrcp", "ms", "ddv", "bds", "mcps", "dch", "dtcd", "facs", "mrcs",
                 "frcp", "llb", "bsc", "msc", "dlo", "mrcog", "frcog", "fics", "facc", "dmrd", "mmed", "mrcpch", "frcpch", "dip",
                 "diploma", "psc", "ndc", "afwc", "retd", "rtd", "fcma", "fca", "acca", "cpa"}
STOP_TOKENS = {"sincerely", "regards", "wishes", "best", "with", "thanks", "thank", "yours", "truly", "faithfully", "warm", "kind",
               "message", "messages", "from", "form", "of", "the", "our", "hospital", "hospitals", "ltd", "limited", "plc", "group",
               "centre", "center", "clinic", "diagnostic", "trust", "foundation", "welcome", "home", "about", "contact", "us", "profile",
               "speech", "desk", "by", "and", "at", "in", "for", "to", "is", "was", "office", "board", "team", "management",
               "meet", "know", "new", "senior", "junior", "former", "current", "honorable", "honourable", "note", "words"}
# Words that never belong in a person's name: UI text, department / doctor-directory vocabulary, org words
NOISE_WORDS = {"view", "details", "profile", "read", "more", "click", "here", "see", "all", "list", "ex", "former", "past",
               "specialist", "surgeon", "consultant", "cunsultant", "department", "dept", "unit", "ward", "clinic", "hospital",
               "medical", "medicine", "surgery", "assistant", "associate", "senior", "junior", "doctors", "doctor", "team", "staff",
               "nurse", "nursing", "registrar", "fellow", "emeritus", "honorary", "trustee", "trustees", "board", "member", "members",
               "committee", "directors", "management", "navy", "army", "bangladesh", "dhaka", "office", "message", "welcome",
               "career", "careers", "notice", "notices", "news", "update", "updates", "vacancy", "circular", "tender", "gallery",
               # capitalised sentence starters that follow a name ("... Hossain It’s a pleasure", "... Rahman We believe")
               "it", "its", "we", "he", "she", "they", "this", "that", "these", "those", "here", "there", "you", "your", "his",
               "her", "their", "since", "after", "before", "during", "as", "on", "under", "over", "within", "also", "today"}
NOISE_WORD_RE = re.compile(r'^\w+olog(y|ist)$|^\w+surg(ery|eon)$|^\w+iatric|^\w+ics$', re.I)
TITLE_WORDS = {"director", "chairman", "chairperson", "manager", "officer", "ceo", "coo", "cmo", "cio", "head", "president",
               "secretary", "coordinator", "executive", "managing", "chief", "founder", "proprietor", "owner"}
FORMER_RE = re.compile(r'\b(ex|former|past|retired|late|acting)\b\.?', re.I)
HOSTED_PLATFORM_RE = re.compile(r'\.(wix|wixsite|weebly|blogspot|wordpress|godaddysites|webnode|site123)\.com$', re.I)
_TITLE_RE_CACHE = {}


def title_regex():
    """One alternation of every title, longest first, so 'Founding Chairman' beats 'Chairman' at the same spot."""
    key = tuple(TITLE_LABELS)
    if key not in _TITLE_RE_CACHE:
        labels = sorted(key, key=len, reverse=True)
        _TITLE_RE_CACHE[key] = re.compile(r'(?<![A-Za-z])(' + '|'.join(re.escape(t) for t in labels) + r')(?![A-Za-z])', re.I)
    return _TITLE_RE_CACHE[key]


def _core(token):
    return token.strip('()[]{}"\'“”‘’,;:|-–—')


def _token_kind(token):
    """'name' for a plausible name word, 'skip' for a degree (MBA, FCPS), None where a name cannot continue."""
    core = _core(token)
    if not core or re.search(r'[\d@/]', core) or core == 'I':     # a bare "I" is the pronoun, never an initial
        return None
    if re.search(r"[’']s$", core, re.I):                            # "It’s" / "Chairman's" - possessive or contraction
        return None
    low = core.rstrip('.').lower()
    if low in STOP_TOKENS:
        return None
    if low in DEGREE_TOKENS:
        return 'skip'
    if low in HONORIFICS or low in NAME_PARTICLES:
        return 'name'
    return 'name' if core[0].isupper() else None


def _ends_sentence(core):
    """'health.' ends a sentence; 'Dr.', 'Md.' and initials like 'A.M.' do not."""
    return core.endswith('.') and re.fullmatch(r'[A-Za-z]{3,}\.', core) is not None and core.rstrip('.').lower() not in HONORIFICS


def name_before(tokens):
    """Walk backwards from a title over 'Sincerely, Dr. Ahmed Zahid Hossain' and return ['Dr.', 'Ahmed', 'Zahid', 'Hossain']."""
    collected = []
    for token in reversed(tokens[-12:]):
        kind = _token_kind(token)
        if kind == 'skip':
            continue
        if kind is None:
            break
        core = _core(token)
        if _ends_sentence(core):
            break
        collected.insert(0, core)
        if core.rstrip('.').lower() in HONORIFICS and core.rstrip('.').lower() != 'md':
            break                                   # the honorific is where the name starts ('Md.' is part of many names)
        if token[:1] in '("“' or len(collected) >= 6:
            break
    return collected


def name_after(tokens, had_colon):
    """'Managing Director: Dr. X Y' / 'Chairman Mr. X Y' -> the name; nothing for 'Managing Director coordinated ...'."""
    if not tokens:
        return []
    if not had_colon and _core(tokens[0]).rstrip('.').lower() not in HONORIFICS:
        return []
    collected = []
    for token in tokens[:8]:
        kind = _token_kind(token)
        if kind == 'skip':
            continue
        if kind is None:
            break
        core = _core(token)
        if _ends_sentence(core):
            collected.append(core.rstrip('.'))      # 'Prof. Dr. Sadrul Islam.' - the name's last word ends the sentence
            break
        collected.append(core)
        if token.endswith((',', ';', ')')) or len(collected) >= 6:
            break
    return collected


def plausible_name(tokens, company=''):
    """Two to five name words (one is enough after an honorific), no title / department / UI words, not the company name."""
    honorific = lambda t: t.rstrip('.').lower() in HONORIFICS and t.rstrip('.').lower() != 'md'
    words = [t for t in tokens if not honorific(t)]
    lowered = [re.sub(r'[^a-z]', '', w.lower()) for w in words]
    if not words or len(words) > 5:
        return False
    if len(words) == 1 and not (any(honorific(t) for t in tokens) and len(words[0]) >= 4):
        return False
    if any(w in NOISE_WORDS or w in TITLE_WORDS or NOISE_WORD_RE.match(w) for w in lowered):
        return False
    company_words = {re.sub(r'[^a-z]', '', w.lower()) for w in (company or '').split()}
    if company_words and set(lowered) <= company_words:
        return False
    letters = sum(len(re.sub(r'[^A-Za-z]', '', w)) for w in words)
    return letters >= 5 and any(len(w) >= 3 for w in words)


def extract_decision_makers(text, company=''):
    """[{name, title}] found as 'Name, Title' / 'Title: Name' in the page text, best title first, one entry per person."""
    tokens = text.split()
    # character offset of every token so a regex match can be mapped back to token positions
    offsets, pos = [], 0
    for token in tokens:
        pos = text.index(token, pos)
        offsets.append(pos)
        pos += len(token)
    priority = {label.lower(): i for i, label in enumerate(TITLE_LABELS)}
    found, order = {}, 0
    for match in title_regex().finditer(text):
        first = next((i for i, off in enumerate(offsets) if off + len(tokens[i]) > match.start()), None)
        if first is None:
            continue
        last = first
        while last + 1 < len(tokens) and offsets[last + 1] < match.end():
            last += 1
        title = ' '.join(w.capitalize() if (w.islower() or (w.isupper() and len(w) > 3)) else w for w in match.group(1).split())   # 'CHAIRMAN' -> 'Chairman', 'CEO' stays
        before = tokens[:first] + ([tokens[first][:match.start() - offsets[first]]] if match.start() > offsets[first] else [])
        trailing = tokens[last][match.end() - offsets[last]:]
        after = tokens[last + 1:]
        had_colon = trailing.startswith((':', '-', '–', '—')) or (after[:1] and after[0] in (':', '-', '–', '—'))
        if had_colon and after[:1] and after[0] in (':', '-', '–', '—'):
            after = after[1:]
        heading = bool(after[:1]) and re.fullmatch(r"(?:'s)?\s*(?:messages?|speech|desk)[:\-–]?", after[0].lower()) is not None
        if heading:
            after, had_colon, before = after[1:], True, []   # "Chairman Message Prof. X" - the name follows the heading word
        if FORMER_RE.search(' '.join(before[-3:])):
            continue                                  # 'Former Professor and Chairman' is not the current chairman
        if re.match(r'(of|,)?\s*(the\s+)?(department|dept|division|unit|committee)', ' '.join(after[:3]), re.I):
            continue                                  # 'Chairman, Department of Surgery' is a department post
        candidates = [name_before([t for t in before if t]), name_after(after, had_colon)]
        for words in candidates:
            words = polish_name(words, company)
            if plausible_name(words, company):
                name = ' '.join(words)
                key = re.sub(r'[^a-z]', '', name.lower().replace('md', ''))[:40]
                rank = priority.get(match.group(1).lower(), len(priority))
                # a name right after "Sincerely," / "With best wishes" is the person signing the page = the current holder
                lead_in = text[max(0, match.start() - len(' '.join(words)) - 60):match.start()]
                signing = bool(SIGNOFF_RE.search(lead_in.rsplit(name.split()[0], 1)[0])) if name.split()[0] in lead_in else False
                order += 1
                if key not in found or rank < found[key]['rank']:
                    found[key] = {"name": name, "title": title, "rank": rank, "order": order, "signing": signing}
                break
    people = sorted(found.values(), key=lambda d: d['rank']) or extract_bengali_decision_makers(text)
    if len(people) > 1 and people[0]['rank'] == people[1]['rank']:
        # several people carry the top title (a chronological list of past holders, or branch heads): the one signing a
        # message wins, then a name with an honorific (Dr. / Prof. / Mr.), then the last one listed (histories run oldest first)
        same = [p for p in people if p['rank'] == people[0]['rank']]
        has_honorific = lambda p: p['name'].split()[0].rstrip('.').lower() in HONORIFICS
        current = max(same, key=lambda p: (p['signing'], has_honorific(p), p['order']))
        people = [current] + [p for p in people if p is not current]
    return people


def polish_name(words, company=''):
    """Trim company words glued to a name ('Md. Rashidul Islam GUK-Eye' -> 'Md. Rashidul Islam') and fix ALL-CAPS names."""
    company_words = {w for w in (re.sub(r'[^a-z]', '', t.lower()) for t in (company or '').split()) if len(w) >= 3}
    has_company_word = lambda w: any(cw in re.sub(r'[^a-z]', '', w.lower()) for cw in company_words)
    while len(words) > 2 and has_company_word(words[-1]):
        words = words[:-1]
    while len(words) > 2 and has_company_word(words[0]) and words[0].rstrip('.').lower() not in HONORIFICS:
        words = words[1:]
    fixed = []
    for w in words:
        low = w.rstrip('.').lower()
        if low in HONORIFICS:
            fixed.append(low.capitalize() + ('.' if w.endswith('.') or low in ('dr', 'prof', 'mr', 'mrs', 'ms', 'md', 'engr') else ''))
        elif w.isupper() and len(w) > 2:
            fixed.append(w.capitalize())              # 'SAMAD' -> 'Samad'; initials like 'MA' / 'A.M.' are kept
        else:
            fixed.append(w)
    return fixed


SIGNOFF_RE = re.compile(r'(sincerely|regards|wishes|thanks|thank you|yours|faithfully|truly|gratefully)[\s,.!]*$', re.I)


# Bengali-only pages: the same idea with Bengali titles and honorifics (মো:/মোঃ is part of the name, like Md.)
BENGALI_TITLES = (("ব্যবস্থাপনা পরিচালক", "Managing Director"), ("প্রধান নির্বাহী কর্মকর্তা", "Chief Executive Officer"),
                  ("প্রধান নির্বাহী", "Chief Executive Officer"), ("চেয়ারম্যান", "Chairman"), ("নির্বাহী পরিচালক", "Executive Director"),
                  ("মহাপরিচালক", "Director General"), ("পরিচালক", "Director"))
BENGALI_HONORIFIC_RE = re.compile(r'^(ডা|ডাঃ|ডা:|ডা\.|ড\.|ডঃ|প্রফেসর|প্রফেঃ|অধ্যাপক|জনাব|মিসেস|মিস|ইঞ্জিনিয়ার|ব্রিগেডিয়ার|মেজর|কর্নেল|ব্যারিস্টার)')
BENGALI_STOP = {"হাসপাতাল", "লিমিটেড", "এর", "ও", "এবং", "বার্তা", "মেসেজ", "থেকে", "কর্তৃক", "সম্পর্কে", "আমাদের", "মহোদয়",
                "মহোদয়ের", "কেন্দ্র", "ক্লিনিক", "প্রতিষ্ঠান", "সাহেব", "বাণী", "কথা", "পরিচিতি", "নতুন", "সাবেক", "প্রাক্তন"}


def extract_bengali_decision_makers(text):
    """[{name (Bengali), title (English)}] for 'ডা: মো: মঈনুল আহসান ব্যবস্থাপনা পরিচালক' style mentions."""
    found, taken = [], []
    for rank, (bn_title, en_title) in enumerate(BENGALI_TITLES, start=50):   # most specific titles first
        for match in re.finditer(re.escape(bn_title), text):
            if any(a <= match.start() < b for a, b in taken):
                continue                                          # 'পরিচালক' inside 'ব্যবস্থাপনা পরিচালক'
            before = text[max(0, match.start() - 90):match.start()].split()
            words = []
            for token in reversed(before[-6:]):
                core = token.strip('(),;|-–—।')          # keep the ':' / '.' of honorifics like ডা: and মো:
                if not re.fullmatch(r'[\u0980-\u09FF.:]+', core) or core in BENGALI_STOP:
                    break
                words.insert(0, core)
                if BENGALI_HONORIFIC_RE.match(core) or len(words) >= 5:
                    break
            generic = en_title == 'Director'                     # plain 'Director' only counts with an honorific (ডা:, জনাব ...)
            if (len(words) >= 2 and not generic) or (words and BENGALI_HONORIFIC_RE.match(words[0])):
                taken.append((match.start(), match.end()))
                found.append({"name": ' '.join(words), "title": en_title, "rank": rank, "order": len(found), "signing": False})
                break
    return found


COMPANY_STOPWORDS = {'hospital', 'hospitals', 'clinic', 'limited', 'ltd', 'centre', 'center', 'medical', 'diagnostic', 'general',
                     'specialized', 'specialised', 'private', 'bangladesh', 'dhaka', 'college', 'plc', 'group', 'trust', 'foundation',
                     'health', 'care', 'healthcare', 'international', 'research', 'institute', 'services', 'pharma', 'pharmaceuticals'}
LINKEDIN_TITLE_RE = re.compile(r'^(.+?)\s+[-–|]\s+(.+?)\s+[-–|]\s+(.+?)\s*\|\s*LinkedIn', re.I)     # Name - Title - Company | LinkedIn
LINKEDIN_AT_RE = re.compile(r'^(.+?)\s+[-–]\s+(.+?)\s+(?:at|@)\s+(.+?)\s*(?:[-|]\s*LinkedIn)?$', re.I)   # Name - Title at Company


def lookup_decision_maker_online(company, domain=''):
    """When the company's own site names nobody: LinkedIn profiles first ('Md. Hasan - Managing Director - Asgar Ali
    Hospital | LinkedIn'), then ordinary search snippets ('X, Managing Director of Y'). One or two search requests."""
    tokens = [w for w in re.findall(r'[a-z]{4,}', company.lower()) if w not in COMPANY_STOPWORDS][:3]
    if not tokens:
        return {}
    mentions = lambda text: any(t in (text or '').lower() for t in tokens)
    precise = is_configured(GOOGLE_CSE_KEY) and is_configured(GOOGLE_CSE_CX) and engine_available('Google')
    queries = ((f'"{company}" (managing director OR chairman OR ceo) site:linkedin.com/in', f'"{company}" managing director OR chairman OR "chief executive"')
               if precise else                                   # Bing RSS / DuckDuckGo HTML ignore site: and OR - plain words work better
               (f'{company} Dhaka managing director linkedin', f'{company} Dhaka managing director chairman'))
    for step, query in enumerate(queries):
        for title, url, snippet in search_raw(query):
            if 'linkedin.com/in/' in url:
                match = LINKEDIN_TITLE_RE.match(title) or LINKEDIN_AT_RE.match(title)
                if match and mentions(match.group(3)) and title_regex().search(match.group(2)):
                    words = polish_name(match.group(1).split(), company)
                    if plausible_name(words, company):
                        return {"name": ' '.join(words), "title": match_title(match.group(2)), "linkedin": url.split('?')[0],
                                "source": "LinkedIn search result"}
            elif step == 1 and mentions(f"{title} {snippet}"):
                people = extract_decision_makers(f"{title}. {snippet}", company)
                if people:
                    return {"name": people[0]['name'], "title": people[0]['title'], "linkedin": '',
                            "source": f"search snippet from {to_domain(url)}"}
    return {}


def match_title(text):
    """The canonical executive title inside a free-text headline ('Managing Director & Founder' -> 'Managing Director')."""
    match = title_regex().search(text)
    return ' '.join(w.capitalize() if (w.islower() or (w.isupper() and len(w) > 3)) else w for w in match.group(1).split()) if match else text.strip()


def match_direct_email(name, emails, domain=''):
    """An email whose local part contains a name token, or a role address (md@, ceo@, chairman@)."""
    tokens = [t.lower().strip('.') for t in name.split()
              if len(t) > 3 and t.lower().strip('.') not in HONORIFICS and t.lower().strip('.') not in (domain or '')]
    for email in emails:
        local = email.split('@')[0].lower()
        if any(t in local for t in tokens) or re.fullmatch(r'(md|ceo|chairman|director|managingdirector|dmd|coo|cmo|gm)\d*', local):
            return email
    return ''


GENERIC_TITLE_SEGMENT_RE = re.compile(r'^(?:home(?:page)?|welcome(?: to)?|contact(?: us)?|about(?: us)?|services|login|official website|হোম|স্বাগতম)$', re.I)
SEO_SEGMENT_RE = re.compile(r'^(?:the\s+)?(?:best|top|leading|no\.?\s*1|number one|#1)\b', re.I)


def clean_company_name(title, url=''):
    """'Green Life Hospital Ltd | Best Hospital in Dhaka' -> 'Green Life Hospital Ltd'; 'Best Physio Center in Dhaka | Ahmed Physio'
    -> 'Ahmed Physio'. When every segment is an SEO tagline the domain name is used instead."""
    segments = [s.strip() for s in re.split(r'\s+[|\-–—:]\s+|\s*\|\s*', title or '') if s.strip()]
    for segment in segments:
        segment = re.sub(r'^(?:home|welcome to|official website of)\s*[-:|]?\s*', '', segment, flags=re.I).strip()
        if len(segment) >= 3 and not GENERIC_TITLE_SEGMENT_RE.match(segment) and not SEO_SEGMENT_RE.match(segment):
            return segment
    label = to_domain(url).split('.')[0] if url else ''
    if len(label) >= 4:
        return label.replace('-', ' ').replace('_', ' ').title()
    if len(label) == 3:
        return label.upper()                           # kgh.dhaka.gov.bd -> KGH
    return segments[0] if segments else (title or '').strip()


def normalize_bd_phone(raw):
    """'01712-345 678' -> '+8801712345678', '02-9612345' -> '+88029612345'; short hotlines (10606) are kept."""
    digits = re.sub(r'\D', '', raw or '')
    if not digits:
        return (raw or '').strip()
    if digits.startswith('00880'):
        digits = digits[2:]
    if digits.startswith('880') and len(digits) >= 11:                # 880 + 2XXXXXXX landline (11) / 1XXXXXXXXX mobile (13)
        return '+' + digits
    if digits.startswith('0') and len(digits) in (9, 10, 11):      # 01XXXXXXXXX mobile, 02XXXXXXX(X) Dhaka landline
        return '+880' + digits[1:]
    if len(digits) <= 6:
        return digits
    return (raw or '').strip()


def normalize_phones(value):
    """Normalise every number in a 'a, b / c' list and drop duplicates, keeping the order."""
    parts = [p for p in re.split(r'[,;/|]|\s{2,}|\band\b', value or '') if p.strip()]
    return ', '.join(dict.fromkeys(normalize_bd_phone(p) for p in parts))


FEATURE_PHRASES = {
    "online appointment booking": "book appointments online",
    "online reports / patient portal": "download their reports online",
    "mobile app": "use a mobile app",
    "telemedicine / video consultation": "consult a doctor by video",
    "online payment": "pay online",
    "live chat / WhatsApp": "reach you on WhatsApp or live chat",
}


def signal_counts(lead_info):
    return {k: int(v) for k, v in re.findall(r'(beds|branches|doctors|departments): (\d+)', lead_info.get('website_signals', ''))}


def offline_fit(offer, lead_info, matched_type):
    """Rule-based stand-in for the AI's fit judgement: target type, organisation size, digital gaps."""
    if not offer:
        return '', ''
    signals = lead_info.get('website_signals', '')
    missing, present = lead_info.get('features_missing', []) or [], lead_info.get('features_present', []) or []
    counts = signal_counts(lead_info)
    sizeable = counts.get('beds', 0) >= 100 or counts.get('departments', 0) >= 10 or counts.get('branches', 0) >= 3 or counts.get('doctors', 0) >= 50
    score, reasons = 3, []
    if matched_type:
        score += 1
        reasons.append(f"target type '{matched_type}'")
    if sizeable:
        score += 1
        reasons.append("sizeable organisation (" + ', '.join(f"{k} {v}" for k, v in counts.items()) + ")")
    if offer.get('digital') and signals:
        if len(missing) >= 3:
            score += 1
            reasons.append(f"{len(missing)}/{len(FEATURE_CHECKS)} digital features missing ({', '.join(missing[:3])})")
        elif len(missing) <= 1:
            score -= 1
            reasons.append("already digital (" + ', '.join(present) + ")")
    if re.search(r'pharmacy|blood bank', lead_info.get('healthcare_type', ''), re.I) and not sizeable:
        score -= 1
        reasons.append("small buyer type")
    if to_domain(lead_info.get('company_url', '')).endswith('.gov.bd'):
        score -= 1
        reasons.append("government facility - procurement runs through tenders")
    if not signals:
        reasons.append("website not readable - scored on type only")
    return str(max(1, min(5, score))), "Rule-based: " + ('; '.join(reasons) or "Dhaka healthcare provider, no distinguishing evidence")


def pick_product(offer, lead_info):
    """The product line from the brief that best matches a healthcare buyer, else the first one."""
    lines = offer.get('product_lines') or []
    for line in lines:
        if re.search(r'hospital|clinic|health|patient|medical|lab|diagnos|pharma', line, re.I):
            return line
    return lines[0] if lines else ''


def compose_offline_email(company, name, lead_info, offer):
    """Template email built from the brief + website signals. Specific opener, one product, outcome, the brief's CTA and signature."""
    sender = offer.get('company_name') or 'our team'
    short = ' '.join(company.split()[:4])
    counts = signal_counts(lead_info)
    facts = [f for f in (f"{counts['beds']} beds" if 'beds' in counts else '',
                         f"{counts['departments']} departments" if 'departments' in counts else '',
                         f"{counts['branches']} branches" if 'branches' in counts else '') if f]
    missing = lead_info.get('features_missing', []) or []
    product_line = pick_product(offer, lead_info)
    product_name = short_term(product_line) if product_line else sender
    product_desc = re.split(r'\s[—–-]\s', product_line, maxsplit=1)[1].strip().rstrip('.') if product_line and re.search(r'\s[—–-]\s', product_line) else ''
    location = lead_info.get('dhaka_location') or 'Dhaka'
    kind = (lead_info.get('healthcare_type') or 'healthcare provider').lower()
    cta = offer.get('cta') or 'a 15-minute call'
    if not re.match(r'(a|an|the)\s', cta, re.I):
        cta = 'a ' + cta
    greeting = f"Dear {name}," if name else "Dear Management Team,"

    if offer.get('digital') and missing and lead_info.get('website_signals'):
        gap = ' or '.join(FEATURE_PHRASES.get(m, m) for m in missing[:2])
        observation = (f"Looking at {company}'s website{' (' + ', '.join(facts) + ')' if facts else ''}, I noticed patients still cannot {gap}; "
                       f"those requests run through the front desk and phone lines.")
        subject = (f"Online appointment booking at {short}" if missing[0] == "online appointment booking"
                   else f"Same-day digital reports for {short}" if missing[0].startswith("online reports")
                   else f"One system for {short} operations")
    elif offer.get('digital'):
        observation = (f"{company} serves {location} as a {kind}. Most facilities of this kind in Dhaka still run appointments, "
                       f"billing and lab reporting on separate tools or paper.")
        subject = f"One system for {short} operations"
    else:
        observation = f"{company}{' (' + ', '.join(facts) + ')' if facts else ''} is exactly the kind of {kind} in {location} we work with."
        subject = f"Working with {short}"

    if product_desc:
        pitch = (f"{sender}'s {product_name} brings {product_desc} into one system and connects to the tools you already run, "
                 f"so bookings, billing and reports stop being manual steps.")
    elif offer.get('product_lines'):
        pitch = f"{sender} provides {', '.join(short_term(l) for l in offer['product_lines'][:3])} for organisations like yours."
    else:
        pitch = f"{sender} works with healthcare organisations across Dhaka on exactly these operational challenges."
    outcome = (f"For a facility with {facts[0]} that usually means shorter reception queues and reports reaching patients the same day."
               if facts and offer.get('digital') else
               "The usual result is less manual work for your staff and faster service for patients." if offer.get('digital') else '')
    closing = f"Would {cta} next week be useful, to see whether this fits your workflows?"
    signature = '\n'.join(offer.get('signature') or [sender])
    body = f"{greeting}\n\n{observation}\n\n{pitch}{(' ' + outcome) if outcome else ''}\n\n{closing}\n\n{signature}"
    hook = re.split(r'[;.]\s', observation, maxsplit=1)[0].strip()
    return subject, body, hook


def mock_openai_response(lead_info):
    """Rule-based extraction + templated email for runs without an OpenAI key (free demo mode)."""
    text = ' '.join(filter(None, [lead_info.get('full_text') or lead_info.get('scraped_text', ''), lead_info.get('search_snippet', '')]))
    company = clean_company_name(lead_info.get('company_name', ''), lead_info.get('company_url', ''))
    people = extract_decision_makers(text, company)
    name, title = (people[0]['name'], people[0]['title']) if people else ('', '')
    others = [f"{p['name']} - {p['title']}" for p in people[1:4]]
    lookup = lead_info.get('online_lookup') or {}
    if not name and lookup.get('name'):
        name, title = lookup['name'], lookup['title']

    first = lambda key: (lead_info.get(key) or '').split(',')[0].strip()
    domain = to_domain(lead_info.get('company_url', ''))
    emails = [e.strip() for e in (lead_info.get('found_emails') or '').split(',') if e.strip()]
    offer = OFFER_PROFILE or {}
    haystack = f"{lead_info.get('healthcare_type', '')} {text[:3000]}".lower()
    matched_type = next((t for t in offer.get('target_types', []) if t.lower() in haystack), '')
    fit_score, fit_reason = offline_fit(offer, lead_info, matched_type)

    # Only guess info@domain for a site on its own domain - a Wix / Blogspot subdomain cannot receive mail
    own_domain = domain and not HOSTED_PLATFORM_RE.search(domain)
    generic_email = lead_info.get('generic_email') or first('found_emails') or (f"info@{domain}" if own_domain else '')
    direct_email = match_direct_email(name, emails, domain) if name else ''
    if not direct_email and name and own_domain:
        direct_email = f"md@{domain} (predicted)"

    if offer:
        subject, body, hook = compose_offline_email(company, name, lead_info, offer)
    else:
        subject = f"Partnership Proposal for {company} (Dhaka Operations)"
        body = (f"Dear {name or 'Management Team'},\n\nWe admire {company}'s work in healthcare across Dhaka and would like to explore "
                f"how we could support your operations.\n\nWould you be open to a 10-minute briefing next week?\n\nWarm regards,\nB2B Healthcare Solutions")
        hook = f"Healthcare provider in {lead_info.get('dhaka_location', 'Dhaka')}"

    return {
        "company_name": company,
        "healthcare_type": lead_info.get('healthcare_type', ''),
        "dhaka_location": lead_info.get('dhaka_location', 'Dhaka, Bangladesh'),
        "full_address": lead_info.get('full_address', ''),
        "contact_phone": lead_info.get('contact_phone') or first('found_phones'),
        "generic_email": generic_email,
        "facebook_page": first('found_facebook'),
        "key_services": "",
        "decision_maker_name": name,
        "decision_maker_title": title,
        "decision_maker_email": direct_email,
        "decision_maker_phone": "",
        "decision_maker_linkedin": lookup.get('linkedin') or first('found_linkedin'),
        "other_decision_makers": '; '.join(others),
        "fit_score": fit_score,
        "fit_reason": fit_reason,
        "email_subject": subject,
        "personalized_email": body,
        "personalized_hook": hook[:160],
    }


# ---------------------------------------------------------------- output
def send_telegram_message(bot_token, chat_id, text, label="Telegram notification"):
    """Free Telegram Bot API call (HTML parse mode). Silently skipped when the bot is not configured."""
    if not is_configured(bot_token) or not is_configured(chat_id):
        return
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text[:4000], "parse_mode": "HTML", "disable_web_page_preview": True}
    try:
        req = urllib.request.Request(url, data=json.dumps(payload).encode('utf-8'), headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5):
            pass
        print(f"  [+] {label} delivered successfully!")
    except urllib.error.HTTPError as err:
        print(f"  [!] Telegram HTTP {err.code}: {err.read().decode('utf-8', errors='replace')[:200]}")
    except Exception as err:
        print(f"  [!] Telegram notice: {err}")


def fit_value(lead):
    return int(lead['fit_score']) if str(lead.get('fit_score', '')).isdigit() else 0


def send_telegram_alert(bot_token, chat_id, lead):
    """Sends immediate free notification via Telegram Bot API"""
    if not is_configured(bot_token) or not is_configured(chat_id):
        return
    if TELEGRAM_MIN_FIT and fit_value(lead) < TELEGRAM_MIN_FIT:
        return                                          # low-fit leads still go to the CSV, just no per-lead alert

    # HTML parse mode + escaping: underscores/asterisks in emails and URLs would break Markdown mode
    lead = {**lead, 'fit': f"{lead['fit_score']}/5 - {lead['fit_reason']}" if lead.get('fit_score') else ''}
    line = lambda label, key: f"{label} {html.escape(str(lead.get(key)))}\n" if lead.get(key) else ''
    msg = (
        "🏥 <b>NEW DHAKA HEALTHCARE LEAD</b>\n\n"
        + line("🏢 <b>Company:</b>", 'company_name')
        + line("🏷 <b>Type:</b>", 'healthcare_type')
        + line("📍 <b>Location:</b>", 'dhaka_location')
        + line("🏠 <b>Address:</b>", 'full_address')
        + line("🌐 <b>Website:</b>", 'website')
        + line("📞 <b>Phone:</b>", 'phone')
        + line("📧 <b>Email:</b>", 'generic_email')
        + line("📘 <b>Facebook:</b>", 'facebook_page')
        + line("🩺 <b>Services:</b>", 'key_services')
        + line("🎯 <b>Offer fit:</b>", 'fit')
        + "\n👑 <b>DECISION MAKER</b>\n"
        + (line("👤 <b>Name:</b>", 'decision_maker_name') or "👤 <b>Name:</b> Not found on site\n")
        + line("💼 <b>Title:</b>", 'decision_maker_title')
        + line("📧 <b>Direct Email:</b>", 'decision_maker_email')
        + line("📱 <b>Direct Phone:</b>", 'decision_maker_phone')
        + line("🔗 <b>LinkedIn:</b>", 'decision_maker_linkedin')
        + line("👥 <b>Others:</b>", 'other_decision_makers')
        + "\n"
        + line("✉️ <b>Draft Subject:</b>", 'email_subject')
        + line("💡 <b>Hook:</b>", 'personalized_hook')
    )
    send_telegram_message(bot_token, chat_id, msg)


def business_metrics(n, ai_used):
    """Hours of manual research replaced and estimated AI spend - the two numbers a manager asks for."""
    hours = n * MANUAL_MINUTES_PER_LEAD / 60
    cost = n * AI_COST_PER_LEAD_USD if ai_used else 0.0
    return hours, cost


def run_summary(leads, output_file, ai_used=False):
    """Plain-text and Telegram-HTML summary of one execution: counts, sources, fit distribution, best leads, business impact."""
    n = len(leads)
    hours, cost = business_metrics(n, ai_used)
    sources = {}
    for lead in leads:
        sources[lead.get('_source', 'web search')] = sources.get(lead.get('_source', 'web search'), 0) + 1
    fits = [fit_value(l) for l in leads]
    fit_line = ' · '.join(f"{score}★ {fits.count(score)}" for score in (5, 4, 3) if fits.count(score)) or ''
    low = sum(1 for f in fits if 0 < f <= 2)
    unrated = fits.count(0)
    fit_line = ' · '.join(filter(None, [fit_line, f"≤2★ {low}" if low else '', f"unrated {unrated}" if unrated else ''])) or 'no offer.md'
    dm = sum(1 for l in leads if l.get('decision_maker_name'))
    dm_email = sum(1 for l in leads if l.get('decision_maker_email'))
    phone = sum(1 for l in leads if l.get('phone'))
    email = sum(1 for l in leads if l.get('generic_email'))
    top = sorted((l for l in leads if fit_value(l) >= 4), key=fit_value, reverse=True)[:5]

    plain = [
        f"RUN SUMMARY {datetime.now():%Y-%m-%d %H:%M}",
        f"{n} new leads saved to {output_file}",
        "Sources: " + ' · '.join(f"{src} {cnt}" for src, cnt in sorted(sources.items(), key=lambda kv: -kv[1])),
        f"Offer fit: {fit_line}",
        f"Decision maker found: {dm}/{n} · direct email {dm_email} · company phone {phone} · company email {email}",
        f"Manual research replaced: ~{hours:.1f} h ({n} leads x {MANUAL_MINUTES_PER_LEAD} min) · AI cost: "
        + (f"~${cost:.2f} ({OPENAI_MODEL})" if ai_used else "$0.00 (offline rule-based mode)"),
    ]
    if top:
        plain.append("Top fits:")
        plain += [f"  {i}. {l['company_name']} - {l['fit_score']}/5 - "
                  f"{(l['decision_maker_name'] + ' (' + (l['decision_maker_title'] or '?') + ')') if l['decision_maker_name'] else 'no decision maker found'}"
                  f" - {l['fit_reason'][:90]}" for i, l in enumerate(top, 1)]

    esc = html.escape
    tg = [f"📊 <b>RUN SUMMARY</b> - {datetime.now():%Y-%m-%d %H:%M}",
          f"✅ <b>{n} new leads</b> saved to {esc(output_file)}",
          "📡 " + esc(plain[2]),
          "🎯 Offer fit: " + esc(fit_line),
          f"👑 Decision maker found: {dm}/{n} · direct email {dm_email} · phone {phone} · email {email}",
          f"⏱ Manual research replaced: ~{hours:.1f} h · 💰 AI cost: " + (f"~${cost:.2f}" if ai_used else "$0.00 (offline mode)")]
    if top:
        tg.append("\n🔥 <b>Top fits</b>")
        tg += [f"{i}. <b>{esc(l['company_name'])}</b> - {l['fit_score']}/5"
               + (f" - {esc(l['decision_maker_name'])} ({esc(l['decision_maker_title'] or '?')})" if l['decision_maker_name'] else '')
               + (f"\n   <i>{esc(l['fit_reason'][:120])}</i>" if l['fit_reason'] else '') for i, l in enumerate(top, 1)]
    return '\n'.join(plain), '\n'.join(tg)


def write_run_report(leads, path, ai_used=False):
    """Self-contained HTML page for sharing a run: KPIs, fit distribution, sources, every lead with its email draft."""
    n = len(leads)
    hours, cost = business_metrics(n, ai_used)
    esc = html.escape
    fits = [fit_value(l) for l in leads]
    dm = sum(1 for l in leads if l.get('decision_maker_name'))
    sources = {}
    for l in leads:
        sources[l.get('_source', 'web search')] = sources.get(l.get('_source', 'web search'), 0) + 1
    ordered = sorted(leads, key=lambda l: (-fit_value(l), not l.get('decision_maker_name'), l.get('company_name', '')))

    def bar(label, count):
        pct = round(100 * count / n) if n else 0
        return f'<div class="bar"><span class="lbl">{label}</span><span class="track"><span style="width:{pct}%"></span></span><span class="cnt">{count}</span></div>'

    kpis = [("New leads this run", str(n), "not previously in the CSV / sheet"),
            ("Decision makers identified", f"{dm}/{n}", "name + title from the company's own pages"),
            ("High-fit leads (4-5★)", str(sum(1 for f in fits if f >= 4)), "scored against offer.md"),
            ("Manual research replaced", f"~{hours:.0f} h", f"{MANUAL_MINUTES_PER_LEAD} min per lead by hand"),
            ("AI cost", f"${cost:.2f}" if ai_used else "$0", OPENAI_MODEL if ai_used else "offline rule-based mode")]
    kpi_html = ''.join(f'<div class="kpi"><div class="v">{esc(v)}</div><div class="k">{esc(k)}</div><div class="s">{esc(s)}</div></div>' for k, v, s in kpis)
    fit_html = ''.join(bar(f"{s}★", fits.count(s)) for s in (5, 4, 3, 2, 1)) + bar("unrated", fits.count(0))
    src_html = ''.join(bar(esc(s), c) for s, c in sorted(sources.items(), key=lambda kv: -kv[1]))
    rows = []
    for l in ordered:
        stars = ('★' * fit_value(l) + '☆' * (5 - fit_value(l))) if fit_value(l) else '–'
        contact = ' · '.join(filter(None, [l.get('phone'), l.get('generic_email')]))
        dm_line = (f"<b>{esc(l['decision_maker_name'])}</b><br><span class=\"muted\">{esc(l.get('decision_maker_title', ''))}"
                   + (f"<br>{esc(l['decision_maker_email'])}" if l.get('decision_maker_email') else '') + "</span>"
                   if l.get('decision_maker_name') else '<span class="muted">not found on site</span>')
        rows.append(
            f'<tr><td><b>{esc(l.get("company_name", ""))}</b><br><span class="muted">{esc(l.get("healthcare_type", ""))} · {esc(l.get("dhaka_location", ""))}</span>'
            f'<br><a href="{esc(l.get("website", ""))}" target="_blank" rel="noopener">{esc(to_domain(l.get("website", "")))}</a></td>'
            f'<td>{dm_line}</td><td>{esc(contact)}</td>'
            f'<td><span class="stars">{stars}</span><br><span class="muted">{esc(l.get("fit_reason", ""))}</span></td>'
            f'<td><details><summary>{esc(l.get("email_subject", "") or "email")}</summary><pre>{esc(l.get("personalized_email", ""))}</pre>'
            f'<div class="muted">Website signals: {esc(l.get("website_signals", "") or "n/a")}</div></details></td></tr>')
    offer_name = (OFFER_PROFILE or {}).get('company_name') or 'no offer.md'
    page = f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Lead Run Report</title>
<style>
:root{{--bg:#f6f7f9;--card:#fff;--ink:#1a1d23;--muted:#6b7280;--line:#e5e7eb;--accent:#0f766e;--star:#d97706}}
@media (prefers-color-scheme:dark){{:root:not([data-theme=light]){{--bg:#0f1115;--card:#171a21;--ink:#e6e8ee;--muted:#9aa1ad;--line:#2a2f3a;--accent:#2dd4bf;--star:#fbbf24}}}}
:root[data-theme=dark]{{--bg:#0f1115;--card:#171a21;--ink:#e6e8ee;--muted:#9aa1ad;--line:#2a2f3a;--accent:#2dd4bf;--star:#fbbf24}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,Segoe UI,Roboto,sans-serif}}
main{{max-width:1200px;margin:0 auto;padding:24px 16px}}h1{{font-size:22px;margin:0 0 4px}}.sub{{color:var(--muted);margin-bottom:20px}}
.kpis{{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;margin-bottom:20px}}
.kpi{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px}}.kpi .v{{font-size:26px;font-weight:700;color:var(--accent)}}.kpi .k{{font-weight:600}}.kpi .s{{color:var(--muted);font-size:13px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:12px;margin-bottom:20px}}.card{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px}}.card h2{{font-size:15px;margin:0 0 10px}}
.bar{{display:grid;grid-template-columns:80px 1fr 36px;align-items:center;gap:8px;margin:4px 0;font-size:13px}}.track{{background:var(--line);border-radius:4px;height:10px;overflow:hidden}}.track span{{display:block;height:100%;background:var(--accent)}}.cnt{{text-align:right}}
table{{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);border-radius:10px;overflow:hidden}}th,td{{text-align:left;vertical-align:top;padding:10px;border-bottom:1px solid var(--line);font-size:14px}}th{{background:var(--bg);font-weight:600}}
.muted{{color:var(--muted);font-size:13px}}.stars{{color:var(--star);letter-spacing:1px}}pre{{white-space:pre-wrap;font:13px/1.45 inherit;background:var(--bg);padding:10px;border-radius:8px;margin:8px 0}}summary{{cursor:pointer;color:var(--accent)}}a{{color:var(--accent)}}
@media (max-width:760px){{table,thead,tbody,tr,td,th{{display:block}}th{{display:none}}td{{border-bottom:none}}tr{{border-bottom:1px solid var(--line)}}}}
</style></head><body><main>
<h1>Dhaka Healthcare Lead Run</h1>
<div class="sub">{datetime.now():%d %b %Y, %H:%M} · offer brief: {esc(offer_name)} · output: {esc(output_name(path))}</div>
<div class="kpis">{kpi_html}</div>
<div class="grid"><div class="card"><h2>Offer fit distribution</h2>{fit_html}</div><div class="card"><h2>Where the leads came from</h2>{src_html}</div></div>
<table><thead><tr><th>Company</th><th>Decision maker</th><th>Company contact</th><th>Fit</th><th>Email draft (click to open)</th></tr></thead><tbody>{''.join(rows)}</tbody></table>
<p class="muted">Fit = how well the organisation matches offer.md (1-5). Emails are drafts: verify the decision maker and replace bracketed placeholders before sending. "(predicted)" emails follow the company's address pattern and were not verified.</p>
</main></body></html>"""
    with open(path, 'w', encoding='utf-8') as f:
        f.write(page)


def output_name(path):
    return os.path.basename(path).replace('run_report.html', OUTPUT_FILENAME)


def load_known_domains(path):
    """Websites already in the CSV, so every run only adds NEW leads. Backs up a CSV with an outdated layout."""
    if not os.path.exists(path):
        return set()
    with open(path, newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        header = reader.fieldnames or []
        known = {to_domain(row.get('website')) for row in reader}
    if header != FIELDNAMES:
        backup = path.replace('.csv', f".backup-{datetime.now():%Y%m%d-%H%M%S}.csv")
        shutil.move(path, backup)
        print(f"[!] {path} used an older column layout - moved it to {backup} and starting a fresh file.")
    return {d for d in known if d}


def main():
    print("=" * 65)
    print("🏥 DHAKA HEALTHCARE LEAD GENERATOR & DECISION-MAKER FINDER")
    print("=" * 65)

    openai_key = os.getenv("OPENAI_API_KEY", "")
    telegram_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    telegram_chat = os.getenv("TELEGRAM_CHAT_ID", "")

    if not is_configured(openai_key):
        print("[!] OPENAI_API_KEY not set in .env - running with the offline rule-based parser.\n")

    global OFFER_PROFILE
    OFFER_PROFILE = load_offer_profile()
    if OFFER_PROFILE:
        apply_offer_targeting(OFFER_PROFILE)
        p = OFFER_PROFILE
        print(f"[*] Offer brief: {os.path.basename(p['path'])} ({len(p['text'])} chars) for {p['company_name'] or 'your company'} - "
              f"{len(p['target_types']) or 'default'} target types, {len(p['target_areas']) or 'default'} target areas, "
              f"{len(p['target_titles']) or 'default'} decision-maker titles. Emails and fit scores are tailored to it.\n")
    else:
        print("[!] No offer.md next to the script - emails stay generic and fit_score is empty. Copy offer.example.md to offer.md.\n")

    known_domains = load_known_domains(OUTPUT_FILENAME)
    print(f"[*] {len(known_domains)} leads already in {OUTPUT_FILENAME} will be skipped.\n")

    raw_leads = []
    seen_domains = set(known_domains)

    def add_candidates(candidates):
        for item in candidates:
            domain = to_domain(item.get('company_url', ''))
            if domain and domain not in seen_domains and not is_skipped_domain(domain):
                seen_domains.add(domain)
                raw_leads.append(item)

    if USE_OPENSTREETMAP:
        print("[*] Source 1/2 - OpenStreetMap: Dhaka facilities with website + phone + address on record...")
        osm_leads = search_openstreetmap()
        add_candidates(osm_leads)
        print(f"    └─ {len(osm_leads)} facilities returned, {len(raw_leads)} are new\n")

    print("[*] Source 2/2 - Web search (Bing + DuckDuckGo)...")
    for search_pass in range(1, MAX_SEARCH_PASSES + 1):
        for q in build_queries():                       # reshuffled every pass, so a second pass explores new combinations
            if len(raw_leads) >= LEADS_PER_RUN:
                break
            print(f"[*] Querying: '{q}'...")
            add_candidates(search_web(q))
        paused_until = max(ENGINE_PAUSED_UNTIL.values(), default=0)
        if len(raw_leads) >= LEADS_PER_RUN or search_pass == MAX_SEARCH_PASSES or paused_until <= time.time():
            break
        wait = min(ENGINE_COOLDOWN_SECONDS, int(paused_until - time.time()) + 1)
        print(f"\n[*] {len(raw_leads)}/{LEADS_PER_RUN} after pass {search_pass} and an engine is resting - "
              f"waiting {wait}s, then a second pass with new queries...")
        time.sleep(wait)

    if not raw_leads:
        # Search blocked / offline: fall back to the curated dataset (minus anything already exported)
        print("[!] No search results - using curated Dhaka healthcare lead dataset")
        raw_leads = [l for l in DEMO_DHAKA_LEADS if to_domain(l['company_url']) not in known_domains]

    raw_leads = raw_leads[:LEADS_PER_RUN]
    print(f"[*] New unique Dhaka healthcare targets for this execution: {len(raw_leads)}\n")
    if len(raw_leads) < LEADS_PER_RUN:
        print(f"[!] Fewer than {LEADS_PER_RUN} new leads were available this run (sources exhausted or blocked) - "
              f"widen AREAS / CATEGORIES or raise MAX_QUERIES.\n")

    processed_leads = []
    online_lookups = 0
    print(f"[*] Processing {len(raw_leads)} targets (fetching websites + decision maker identification)...\n")

    for i, lead in enumerate(raw_leads, 1):
        print(f"[{i}/{len(raw_leads)}] Analyzing: {lead.get('company_name')}")

        lead['search_snippet'] = lead.get('scraped_text', '')
        pages = fetch_company_pages(lead.get('company_url', ''))
        final_domain = to_domain(pages['final_url'])
        if final_domain and final_domain != to_domain(lead.get('company_url', '')):
            if final_domain in seen_domains:
                print(f"    └─ redirects to {final_domain}, which is already a lead - skipped\n")
                continue
            seen_domains.add(final_domain)
            lead['company_url'] = pages['final_url']
        lead.update({k: v for k, v in pages.items() if k not in ('scraped_text',) or v})
        if not pages['scraped_text']:
            print(f"    ├─ Website: {pages['site_status']}")

        # Nobody named on their own pages? Ask the search engines (LinkedIn profiles first) - a few requests per run
        company_label = clean_company_name(lead.get('company_name', ''), lead.get('company_url', ''))
        if (not extract_decision_makers(lead.get('full_text', ''), company_label) and online_lookups < MAX_ONLINE_LOOKUPS
                and (engine_available('Google') or engine_available('Bing') or engine_available('DuckDuckGo'))):
            online_lookups += 1
            lead['online_lookup'] = lookup_decision_maker_online(company_label, to_domain(lead['company_url']))
            if lead['online_lookup']:
                print(f"    ├─ Online lookup: {lead['online_lookup']['name']} ({lead['online_lookup']['title']}) - {lead['online_lookup']['source']}")
        extracted = identify_decision_maker_with_openai(openai_key, lead)

        get = lambda key, default='': (extracted.get(key) or default)
        lead_record = {
            "company_name": get('company_name') or clean_company_name(lead.get('company_name', ''), lead.get('company_url', '')),
            "healthcare_type": get('healthcare_type') or lead.get('healthcare_type', ''),
            "dhaka_location": get('dhaka_location') or lead.get('dhaka_location', ''),
            "full_address": get('full_address'),
            "website": lead.get('company_url'),          # the final site after redirects
            "phone": normalize_phones(get('contact_phone')),
            "generic_email": get('generic_email'),
            "facebook_page": get('facebook_page'),
            "key_services": get('key_services'),
            "fit_score": get('fit_score'),
            "fit_reason": get('fit_reason'),
            "website_signals": lead.get('website_signals', ''),
            "decision_maker_name": get('decision_maker_name'),
            "decision_maker_title": get('decision_maker_title'),
            "decision_maker_email": get('decision_maker_email'),
            "decision_maker_phone": normalize_phones(get('decision_maker_phone')),
            "decision_maker_linkedin": get('decision_maker_linkedin'),
            "other_decision_makers": get('other_decision_makers'),
            "email_subject": get('email_subject'),
            "personalized_hook": get('personalized_hook'),
            "personalized_email": get('personalized_email'),
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "_source": lead.get('source', 'web search'),          # for the run summary only (not a CSV column)
        }

        processed_leads.append(lead_record)
        print(f"    ├─ Decision Maker: {lead_record['decision_maker_name'] or '-'} ({lead_record['decision_maker_title'] or '-'})")
        print(f"    ├─ Direct Contact: {lead_record['decision_maker_email'] or '-'} | {lead_record['decision_maker_phone'] or '-'}")
        print(f"    ├─ Location: {lead_record['dhaka_location'] or '-'}")
        print(f"    └─ Offer fit: {(lead_record['fit_score'] + '/5 - ' + lead_record['fit_reason']) if lead_record['fit_score'] else 'n/a (no offer.md)'}\n")

        # Telegram Alert if configured
        send_telegram_alert(telegram_token, telegram_chat, lead_record)

    # Append to CSV so the file becomes the cumulative CRM (header only for a new file)
    is_new_file = not os.path.exists(OUTPUT_FILENAME)
    with open(OUTPUT_FILENAME, 'a', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES, extrasaction='ignore')
        if is_new_file:
            writer.writeheader()
        writer.writerows(processed_leads)

    ai_used = is_configured(openai_key)
    plain, telegram_html = run_summary(processed_leads, OUTPUT_FILENAME, ai_used)
    write_run_report(processed_leads, REPORT_FILENAME, ai_used)
    print("=" * 65)
    print(f"SUCCESS: Added {len(processed_leads)} new Dhaka Healthcare Leads to '{OUTPUT_FILENAME}'")
    print(f"         Shareable report written to '{REPORT_FILENAME}' (open it in a browser)")
    print("=" * 65)
    print(plain)
    send_telegram_message(telegram_token, telegram_chat, telegram_html, label="Telegram run summary")


if __name__ == "__main__":
    main()
