"""Step 6 — Pull SEC filings via EDGAR.

Downloads recent 10-K and 10-Q filings for each company
in companies.yaml using the EDGAR submissions API.

How it works:
1. Reads company list from companies.yaml
2. For each company, asks EDGAR "what filings does this company have?"
3. Filters for just 10-K (annual) and 10-Q (quarterly) filings
4. Downloads the actual filing document (HTML format)
5. Saves each one to data/raw/filings/{ticker}/{accession}.html

SEC rules:
- Max 10 requests per second (we add a delay to stay safe)
- Must include a User-Agent header identifying who you are
"""

import os
import sys
import time
import yaml
import requests

# Add project root to path so we can import config
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import settings

# ── Constants ──────────────────────────────────────────────

SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
ARCHIVES_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{filename}"
FILING_TYPES = {"10-K", "10-Q"}
MAX_FILINGS = 8  # last 8 filings per company (roughly 2 years)
OUTPUT_DIR = os.path.join("data", "raw", "filings")
REQUEST_DELAY = 0.15  # seconds between requests (keeps us under 10/sec)


def get_headers():
    """SEC requires a User-Agent header with your name and email."""
    ua = settings.edgar_user_agent
    if not ua:
        print("ERROR: EDGAR_USER_AGENT not set in .env")
        print('Set it to something like: "Your Name your-email@example.com"')
        sys.exit(1)
    return {"User-Agent": ua}


def load_companies():
    """Read the company list from companies.yaml."""
    with open("companies.yaml", "r") as f:
        data = yaml.safe_load(f)
    return data["companies"]


def get_filing_list(cik, headers):
    """Ask EDGAR for all filings a company has made."""
    url = SUBMISSIONS_URL.format(cik=cik)
    response = requests.get(url, headers=headers)
    response.raise_for_status()
    time.sleep(REQUEST_DELAY)
    return response.json()


def extract_filings(submissions_data):
    """Pull out just the 10-K and 10-Q filings from the full list.

    Returns a list of dicts with accession number, filing date,
    form type, and primary document filename.
    """
    recent = submissions_data.get("filings", {}).get("recent", {})
    if not recent:
        return []

    forms = recent.get("form", [])
    accessions = recent.get("accessionNumber", [])
    dates = recent.get("filingDate", [])
    primary_docs = recent.get("primaryDocument", [])

    filings = []
    for i in range(len(forms)):
        if forms[i] in FILING_TYPES:
            filings.append({
                "form": forms[i],
                "accession": accessions[i],
                "date": dates[i],
                "primary_doc": primary_docs[i],
            })
            if len(filings) >= MAX_FILINGS:
                break

    return filings


def download_filing(cik, filing, ticker, headers):
    """Download one filing document and save it to disk.

    Returns the filepath if successful, None if skipped (already exists).
    """
    # Create folder for this company
    company_dir = os.path.join(OUTPUT_DIR, ticker)
    os.makedirs(company_dir, exist_ok=True)

    # Build filename from accession number and form type
    safe_accession = filing["accession"].replace("-", "")
    filename = f"{filing['form']}_{filing['date']}_{safe_accession}.html"
    filepath = os.path.join(company_dir, filename)

    # Skip if already downloaded
    if os.path.exists(filepath):
        return None

    # Build the URL and download
    accession_path = filing["accession"].replace("-", "")
    url = ARCHIVES_URL.format(
        cik=cik.lstrip("0"),
        accession=accession_path,
        filename=filing["primary_doc"],
    )

    response = requests.get(url, headers=headers)
    response.raise_for_status()
    time.sleep(REQUEST_DELAY)

    # Save to disk
    with open(filepath, "wb") as f:
        f.write(response.content)

    return filepath


def main():
    """Main entry point — pull filings for all tracked companies."""
    headers = get_headers()
    companies = load_companies()

    print(f"Pulling filings for {len(companies)} companies...")
    print(f"Looking for: {', '.join(FILING_TYPES)}")
    print(f"Max {MAX_FILINGS} filings per company")
    print()

    total_downloaded = 0
    total_skipped = 0

    for company in companies:
        ticker = company["ticker"]
        cik = company["cik"]
        name = company.get("name", ticker)

        print(f"── {name} ({ticker}) ──")

        try:
            # Get filing list from EDGAR
            data = get_filing_list(cik, headers)
            filings = extract_filings(data)

            if not filings:
                print(f"  No 10-K/10-Q filings found")
                continue

            print(f"  Found {len(filings)} filings")

            # Download each filing
            for filing in filings:
                result = download_filing(cik, filing, ticker, headers)
                if result:
                    total_downloaded += 1
                    print(f"  ✓ {filing['form']} {filing['date']}")
                else:
                    total_skipped += 1
                    print(f"  · {filing['form']} {filing['date']} (already have it)")

        except requests.exceptions.HTTPError as e:
            print(f"  ERROR: {e}")
        except Exception as e:
            print(f"  ERROR: {e}")

        print()

    print(f"Done! Downloaded: {total_downloaded}, Skipped: {total_skipped}")


if __name__ == "__main__":
    main()