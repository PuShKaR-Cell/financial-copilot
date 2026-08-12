"""Step 11 — Convert filing documents to page images.

EDGAR filings come as HTML. This script:
1. Renders each HTML filing in a headless browser (Playwright)
2. Prints it to PDF (which adds proper page breaks)
3. Converts each PDF page to a PNG image (via pdf2image/Poppler)

The resulting page images are what the visual document retrieval
model (ColPali/ColQwen in Step 12) will embed — it sees the page
as a human would, tables and charts included, not just raw text.

Output: data/processed/page_images/{ticker}/{filing_name}/page_001.png
"""

import os
import sys
import glob
from pdf2image import convert_from_path
from playwright.sync_api import sync_playwright

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── Constants ──────────────────────────────────────────────

FILINGS_DIR = os.path.join("data", "raw", "filings")
PDF_DIR = os.path.join("data", "processed", "filing_pdfs")
IMAGES_DIR = os.path.join("data", "processed", "page_images")
DPI = 150  # balance between quality and file size


def html_to_pdf(html_path, pdf_path):
    """Render an HTML filing to PDF using a headless browser.

    Playwright handles the complex CSS, tables, and embedded
    styles that SEC filings use — simple HTML-to-PDF tools
    often choke on these.
    """
    os.makedirs(os.path.dirname(pdf_path), exist_ok=True)

    # Convert local file path to a file:// URL
    abs_path = os.path.abspath(html_path)
    file_url = "file:///" + abs_path.replace("\\", "/")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        # Some filings are large — give them time to load
        page.goto(file_url, timeout=60000, wait_until="networkidle")

        # Print to PDF with standard US Letter page size
        page.pdf(
            path=pdf_path,
            format="Letter",
            print_background=True,
            margin={"top": "0.5in", "bottom": "0.5in", "left": "0.5in", "right": "0.5in"},
        )

        browser.close()


def pdf_to_images(pdf_path, output_dir):
    """Convert each page of a PDF to a PNG image.

    Returns the number of pages converted.
    """
    os.makedirs(output_dir, exist_ok=True)

    # Check if images already exist
    existing = glob.glob(os.path.join(output_dir, "page_*.png"))
    if existing:
        return 0  # already processed

    images = convert_from_path(pdf_path, dpi=DPI)

    for i, image in enumerate(images, 1):
        image_path = os.path.join(output_dir, f"page_{i:03d}.png")
        image.save(image_path, "PNG")

    return len(images)


def process_filing(html_path, ticker):
    """Full pipeline for one filing: HTML → PDF → page images."""

    # Derive names from the HTML filename
    filename = os.path.splitext(os.path.basename(html_path))[0]
    pdf_path = os.path.join(PDF_DIR, ticker, f"{filename}.pdf")
    images_dir = os.path.join(IMAGES_DIR, ticker, filename)

    # Check if already fully processed
    if os.path.exists(images_dir) and glob.glob(os.path.join(images_dir, "page_*.png")):
        return 0, "skip"

    # Step 1: HTML → PDF
    if not os.path.exists(pdf_path):
        try:
            html_to_pdf(html_path, pdf_path)
        except Exception as e:
            return 0, f"pdf_error: {e}"

    # Step 2: PDF → page images
    try:
        num_pages = pdf_to_images(pdf_path, images_dir)
        return num_pages, "ok"
    except Exception as e:
        return 0, f"image_error: {e}"


def main():
    if not os.path.exists(FILINGS_DIR):
        print(f"No filings directory found at {FILINGS_DIR}")
        print("Run ingestion/edgar.py first (Step 6)")
        sys.exit(1)

    # Gather all HTML filings
    all_filings = []
    for ticker in sorted(os.listdir(FILINGS_DIR)):
        ticker_dir = os.path.join(FILINGS_DIR, ticker)
        if not os.path.isdir(ticker_dir):
            continue
        for filename in sorted(os.listdir(ticker_dir)):
            if filename.endswith(".html"):
                all_filings.append((ticker, os.path.join(ticker_dir, filename)))

    print(f"Processing {len(all_filings)} filings → page images")
    print(f"Output: {IMAGES_DIR}/")
    print()

    total_pages = 0
    processed = 0
    skipped = 0
    errors = 0

    for ticker, html_path in all_filings:
        filename = os.path.basename(html_path)
        short_name = f"{ticker}/{filename}"

        num_pages, status = process_filing(html_path, ticker)

        if status == "skip":
            skipped += 1
            print(f"  · {short_name} (already done)")
        elif status == "ok":
            processed += 1
            total_pages += num_pages
            print(f"  ✓ {short_name} → {num_pages} pages")
        else:
            errors += 1
            print(f"  ✗ {short_name} — {status}")

    print()
    print(f"Done! Processed: {processed}, Skipped: {skipped}, Errors: {errors}")
    print(f"Total page images: {total_pages}")


if __name__ == "__main__":
    main()