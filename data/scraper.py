"""
MLRITM Public Document Scraper.
Crawls official mlritm.ac.in public resources (regulations, brochures, fee structures, calendars).
Adheres to robots.txt and logs source URLs with retrieval timestamps.
NOTE: Never contacts anvaya.mlritm.ac.in.
"""

import os
import time
import json
import logging
import urllib.robotparser
from urllib.parse import urljoin, urlparse
from datetime import datetime, timezone
import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

BASE_URL = "https://mlritm.ac.in"
ROBOTS_TXT_URL = "https://mlritm.ac.in/robots.txt"

# Official public documents and sections to ingest
TARGET_RESOURCES = [
    {
        "doc_id": "mlritm_academic_regulations_mlr20",
        "title": "MLRITM Autonomous Academic Regulations (MLR20/MLR22)",
        "url": "https://mlritm.ac.in/academics/academic-regulations",
        "category": "regulations",
        "type": "pdf"
    },
    {
        "doc_id": "mlritm_fee_structure_btech",
        "title": "MLRITM B.Tech Tuition & Miscellaneous Fee Structure",
        "url": "https://mlritm.ac.in/admissions/fee-structure",
        "category": "fees",
        "type": "html"
    },
    {
        "doc_id": "mlritm_scholarships_and_reimbursement",
        "title": "TS ePASS Fee Reimbursement and College Merit Scholarships",
        "url": "https://mlritm.ac.in/admissions/scholarships",
        "category": "scholarships",
        "type": "html"
    },
    {
        "doc_id": "mlritm_academic_calendar_current",
        "title": "MLRITM Academic Calendar (B.Tech All Years)",
        "url": "https://mlritm.ac.in/academics/academic-calendar",
        "category": "calendar",
        "type": "pdf"
    },
    {
        "doc_id": "mlritm_departments_and_courses",
        "title": "B.Tech Departments, Intake, and Course Catalog",
        "url": "https://mlritm.ac.in/departments",
        "category": "courses",
        "type": "html"
    }
]

DATA_DIR = os.path.join(os.path.dirname(__file__), "raw_documents")
METADATA_FILE = os.path.join(DATA_DIR, "crawl_manifest.json")


def check_robots_txt(target_url: str) -> bool:
    """Verifies that crawling the given URL complies with robots.txt."""
    try:
        rp = urllib.robotparser.RobotFileParser()
        rp.set_url(ROBOTS_TXT_URL)
        rp.read()
        can_fetch = rp.can_fetch("*", target_url)
        logger.info(f"robots.txt check for {target_url}: {'ALLOWED' if can_fetch else 'DISALLOWED'}")
        return can_fetch
    except Exception as e:
        logger.warning(f"Could not fetch robots.txt ({e}); proceeding conservatively with rate limiting.")
        return True


def run_scraper():
    """Fetches target documents with polite backoff and writes download manifest."""
    os.makedirs(DATA_DIR, exist_ok=True)
    manifest = []
    
    session = requests.Session()
    session.headers.update({
        "User-Agent": "MLRITM-Academic-Advising-Bot-Scraper/1.0 (+http://mlritm.ac.in/)"
    })

    for item in TARGET_RESOURCES:
        url = item["url"]
        doc_id = item["doc_id"]
        logger.info(f"Processing resource: {item['title']} ({url})")

        if not check_robots_txt(url):
            logger.warning(f"Skipping {url} due to robots.txt restrictions.")
            continue

        retrieval_time = datetime.now(timezone.utc).isoformat()
        file_ext = ".pdf" if item["type"] == "pdf" else ".html"
        output_path = os.path.join(DATA_DIR, f"{doc_id}{file_ext}")

        manifest_entry = {
            "doc_id": doc_id,
            "title": item["title"],
            "url": url,
            "category": item["category"],
            "file_path": output_path,
            "retrieval_date": retrieval_time,
            "status": "pending"
        }

        try:
            response = session.get(url, timeout=15)
            if response.status_code == 200:
                with open(output_path, "wb") as f:
                    f.write(response.content)
                manifest_entry["status"] = "success"
                manifest_entry["content_length"] = len(response.content)
                logger.info(f"Successfully saved: {output_path} ({len(response.content)} bytes)")
            else:
                manifest_entry["status"] = f"failed_http_{response.status_code}"
                logger.error(f"Failed to fetch {url}: HTTP {response.status_code}")
        except Exception as err:
            manifest_entry["status"] = f"failed_exception: {str(err)}"
            logger.error(f"Error fetching {url}: {err}")

        manifest.append(manifest_entry)
        time.sleep(2)  # Polite delay between requests

    with open(METADATA_FILE, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    logger.info(f"Crawl manifest written to: {METADATA_FILE}")


if __name__ == "__main__":
    run_scraper()
