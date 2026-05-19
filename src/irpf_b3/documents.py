import base64
import json
import os
import re
import subprocess
import time
import random
import concurrent.futures
from datetime import datetime

import httpx
from pypdf import PdfReader

from irpf_b3.config import settings
from irpf_b3.helpers import worker_id, sanitize_filename, sanitize_foldername


def parse_year_month(item_dict: dict) -> tuple[str, str]:
    """Extracts year and month resiliently from the B3 item dates."""
    dt_ref = item_dict.get("dateTimeReference")
    if dt_ref and len(dt_ref) >= 7:
        try:
            parts = dt_ref.split("-")
            if len(parts) >= 2:
                return parts[0], parts[1]
        except Exception:
            pass

    d_ref = item_dict.get("dateReference")
    if d_ref:
        try:
            parts = d_ref.split("/")
            if len(parts) == 3:
                return parts[2], parts[1]
        except Exception:
            pass

    now = datetime.now()
    return str(now.year), f"{now.month:02d}"


def _fetch_documents_page(
    client: httpx.Client, cvm_code: str, year: int, page_num: int
) -> tuple[list[dict], dict]:
    """Internal helper to fetch a single document page from B3 with resiliency."""
    payload_dict = {
        "language": settings.b3_language,
        "codeCVM": cvm_code,
        "year": year,
        "dateInitial": f"{year}-01-01",
        "dateFinal": f"{year}-12-31",
        "pageNumber": page_num,
        "pageSize": settings.b3_default_page_size,
    }
    payload_b64 = base64.b64encode(json.dumps(payload_dict).encode("utf-8")).decode("utf-8")
    url = settings.b3_material_facts_url_template.format(payload_b64=payload_b64)

    for attempt in range(settings.b3_api_retries):
        try:
            resp = client.get(url, headers=settings.b3_http_headers)
            resp.raise_for_status()
            data = resp.json()
            return data.get("results", []), data.get("page", {})
        except (httpx.RequestError, httpx.HTTPStatusError, json.JSONDecodeError) as e:
            if attempt == settings.b3_api_retries - 1:
                print(f"[-] Failed to fetch CVM {cvm_code} year {year} page {page_num} after {settings.b3_api_retries} attempts: {e}")
                return [], {}
            # Exponential backoff with jitter
            sleep_time = (settings.b3_retry_sleep_seconds**attempt) + random.uniform(0.1, 1.0)
            time.sleep(sleep_time)

    return [], {}


def _fetch_all_pages_for_year(client: httpx.Client, cvm_code: str, year: int) -> list[dict]:
    """Fetches all pages of documents for a single year, sequentially.

    Each year is expected to run inside its own thread. Pages within
    the same year are fetched sequentially to keep per-year logic
    simple while the outer loop parallelizes across years.
    """
    wid = worker_id()
    print(f"    [{wid}] {cvm_code} {year} ")
    results_page1, page_info = _fetch_documents_page(client, cvm_code, year, 1)
    if not results_page1:
        return []

    year_facts = list(results_page1)

    total_pages = page_info.get("totalPages", 1)
    for page_num in range(2, total_pages + 1):
        results, _ = _fetch_documents_page(client, cvm_code, year, page_num)
        if results:
            year_facts.extend(results)

    return year_facts


def fetch_company_documents(cvm_code: str) -> list[dict]:
    """Fetches historical documents for a CVM code, parallelizing by year.

    All years from the current year down to ``docs_start_year`` are
    dispatched concurrently via ThreadPoolExecutor.  Each thread
    sweeps all pages for its assigned year sequentially.

    Deduplication is done via the document URL key.
    """
    current_year = datetime.now().year
    years = list(range(settings.docs_start_year, current_year + 1))
    all_facts: dict[str, dict] = {}

    limits = httpx.Limits(
        max_keepalive_connections=settings.b3_max_workers,
        max_connections=settings.b3_max_workers,
    )

    with httpx.Client(verify=False, timeout=settings.b3_docs_timeout, limits=limits) as client:
        with concurrent.futures.ThreadPoolExecutor(max_workers=settings.b3_max_workers) as executor:
            future_to_year = {
                executor.submit(_fetch_all_pages_for_year, client, cvm_code, yr): yr
                for yr in years
            }

            for future in concurrent.futures.as_completed(future_to_year):
                yr = future_to_year[future]
                try:
                    year_results = future.result()
                    for doc in year_results:
                        link = doc.get("urlSearch") or doc.get("urlDocument")
                        if link:
                            all_facts[link] = doc
                except Exception as e:
                    print(f"[-] Year {yr} raised exception: {e}")

    return list(all_facts.values())


# Magic byte signatures for file format detection
_PDF_MAGIC = b"%PDF"
_OLE2_MAGIC = b"\xd0\xcf\x11\xe0"  # Microsoft Compound Document (DOC/XLS/PPT)


def _detect_file_type(file_bytes: bytes) -> str:
    """Detects document format from magic bytes.

    Returns:
        File extension string: 'pdf', 'doc', or 'bin' for unknown formats.
    """
    if file_bytes[:4] == _PDF_MAGIC:
        return "pdf"
    if file_bytes[:4] == _OLE2_MAGIC:
        return "doc"
    return "bin"


def download_document(download_url: str, search_url: str, output_dir: str, filename_base: str) -> tuple[str | None, str | None]:
    """Downloads a document using urlDownload (GET) with fallback to urlSearch (POST).

    After download, detects the actual file format via magic bytes and
    saves with the correct extension (.pdf / .doc / .bin).

    Returns:
        Tuple of (saved_file_path, detected_extension) or (None, None) on failure.
    """
    file_bytes = _download_via_get(download_url)

    if not file_bytes and search_url:
        file_bytes = _download_via_post(search_url)

    if not file_bytes:
        return None, None

    ext = _detect_file_type(file_bytes)
    output_path = os.path.join(output_dir, f"{filename_base}.{ext}")
    with open(output_path, "wb") as f:
        f.write(file_bytes)
    return output_path, ext


def _download_via_get(url: str) -> bytes | None:
    """Direct GET download from urlDownload."""
    if not url:
        return None
    try:
        with httpx.Client(verify=False, timeout=settings.cvm_pdf_timeout, follow_redirects=True) as client:
            resp = client.get(url, headers=settings.b3_http_headers)
            resp.raise_for_status()
            if len(resp.content) > 0:
                return resp.content
    except Exception:
        pass
    return None


def _download_via_post(search_url: str) -> bytes | None:
    """Fallback POST download via CVM ExibirPDF endpoint (extracts ID from urlSearch)."""
    match = re.search(r"ID=(\d+)", search_url)
    if not match:
        return None

    protocol = match.group(1)
    payload = {
        "codigoInstituicao": settings.cvm_pdf_institution_code,
        "numeroProtocolo": protocol,
        "token": "",
        "versaoCaptcha": "",
    }
    try:
        with httpx.Client(verify=False) as client:
            resp = client.post(
                settings.cvm_pdf_url, json=payload,
                headers=settings.cvm_http_headers, timeout=settings.cvm_pdf_timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            b64_content = data.get("d")
            if b64_content:
                return base64.b64decode(b64_content)
    except Exception:
        pass
    return None


def extract_text_from_file(file_path: str, ext: str) -> str:
    """Extracts text from a downloaded document based on its detected format.

    Dispatches to pypdf for PDFs and antiword for legacy DOC files.
    """
    if ext == "pdf":
        return _extract_text_pdf(file_path)
    if ext == "doc":
        return _extract_text_doc(file_path)
    return ""


def _extract_text_pdf(pdf_path: str) -> str:
    """Extracts text from PDF using pypdf."""
    try:
        reader = PdfReader(pdf_path)
        text_parts = []
        for page in reader.pages:
            text = page.extract_text()
            if text:
                text_parts.append(text)
        return "\n\n".join(text_parts)
    except Exception:
        return ""


def _extract_text_doc(doc_path: str) -> str:
    """Extracts text from legacy .doc (OLE2) using antiword."""
    try:
        result = subprocess.run(
            ["antiword", doc_path],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            return result.stdout
    except FileNotFoundError:
        print("    [-] antiword not installed. Run: sudo apt-get install -y antiword")
    except Exception:
        pass
    return ""


def _process_single_fact(args: tuple) -> dict | None:
    """Internal helper to process a single fact in parallel: download, extract text, and save."""
    f, ticker, trading_name, base_output_dir = args

    download_url = f.get("urlDownload") or ""
    search_url = f.get("urlSearch") or ""
    if not download_url and not search_url:
        return None

    category = f.get("category") or "unknown"
    cat_clean = sanitize_foldername(category)

    if not settings.b3_allow_all_categories:
        if cat_clean not in settings.allowed_categories:
            return None

    type_str = f.get("type") or ""
    year, month = parse_year_month(f)

    type_clean = sanitize_filename(type_str.strip()) if type_str else ""
    raw_subject = (f.get("subject") or f.get("kind") or "").strip()
    subj_slug = sanitize_filename(raw_subject) if raw_subject else ""

    # Extract doc ID from whichever URL is available
    id_source = search_url or download_url
    match_id = re.search(r"(?:ID|numProtocolo)=(\d+)", id_source)
    doc_id = match_id.group(1) if match_id else "doc"

    # Construct filename base (extension added dynamically after download)
    parts = [year, month]
    if type_clean:
        parts.append(type_clean)
    if subj_slug:
        parts.append(subj_slug)
    parts.append(doc_id)
    filename_base = "-".join(parts)

    txt_filename = f"{filename_base}.txt"

    ticker_dir = os.path.join(base_output_dir, ticker, cat_clean)
    os.makedirs(ticker_dir, exist_ok=True)
    txt_path = os.path.join(ticker_dir, txt_filename)

    has_text = False

    # Check idempotency based on final .txt artifact
    if os.path.exists(txt_path) and os.path.getsize(txt_path) > 0:
        has_text = True
    else:
        # Download document (format detected by magic bytes)
        doc_path, ext = download_document(download_url, search_url, ticker_dir, filename_base)

        if doc_path and ext:
            extracted_text = extract_text_from_file(doc_path, ext)

            if extracted_text.strip():
                try:
                    with open(txt_path, "w", encoding="utf-8") as txt_file:
                        txt_file.write(extracted_text)
                    print(f"    [{worker_id()} +] Processed and saved: {txt_filename}")
                    has_text = True
                    # Delete source file after successful extraction
                    os.remove(doc_path)
                except Exception as e:
                    print(f"    [{worker_id()} -] Failed to save txt for {txt_filename}: {e}")
            else:
                print(f"    [{worker_id()} -] No extractable text (kept for inspection): {os.path.basename(doc_path)}")

    if has_text:
        return {
            "ticker": ticker,
            "trading_name": trading_name,
            "date": f.get("dateReference") or f.get("deliveryDate"),
            "subject": f.get("subject"),
            "category": category,
            "category_clean": cat_clean,
            "year": year,
            "month": month,
            "link": download_url or search_url,
            "txt_path": txt_path,
        }
    return None


def process_company_documents(company: dict, base_output_dir: str = None) -> list[dict]:
    """
    Orchestrates fetching metadata, downloading PDFs, extracting text, and saving to structured directories.
    Utilizes ThreadPoolExecutor to parallelize PDF downloads and text extraction.
    Handles idempotency (skips if .txt already exists).
    
    Args:
        company: Dict with keys 'ticker', 'cvm', 'trading_name'
        base_output_dir: Base directory for storing downloaded files
        
    Returns:
        List of processed document metadata dictionaries.
    """
    if base_output_dir is None:
        base_output_dir = settings.docs_output_dir

    ticker = company["ticker"]
    cvm = company["cvm"]
    trading_name = company["trading_name"]
    
    print(f"\n[+] Fetching historical facts for {ticker} (CVM: {cvm})...")
    facts = fetch_company_documents(cvm)
    print(f"    -> Found {len(facts)} historical facts.")

    # Prepare arguments for parallel processing
    tasks = [(f, ticker, trading_name, base_output_dir) for f in facts]
    processed_facts = []

    if not tasks:
        return processed_facts

    print(f"    -> Extracting text and saving artifacts in parallel...")
    with concurrent.futures.ThreadPoolExecutor(max_workers=settings.b3_max_workers) as executor:
        future_to_task = {executor.submit(_process_single_fact, task): task for task in tasks}
        
        for future in concurrent.futures.as_completed(future_to_task):
            try:
                result = future.result()
                if result:
                    processed_facts.append(result)
            except Exception as e:
                print(f"    [-] Task raised an exception: {e}")

    return processed_facts
