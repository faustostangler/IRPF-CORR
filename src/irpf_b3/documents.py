import base64
import html
import json
import logging
import os
import re
import subprocess
import tempfile
import time
import random
import concurrent.futures
from datetime import datetime
from urllib.parse import urljoin

import httpx
from pypdf import PdfReader


# Suppress noisy warnings from pypdf about corrupted PDFs
logging.getLogger("pypdf").setLevel(logging.ERROR)

from irpf_b3.config import settings
from irpf_b3.helpers import worker_id, sanitize_filename, sanitize_foldername, progress


# Magic byte signatures for file format detection (implementation detail of this module)
_PDF_MAGIC = b"%PDF"
_OLE2_MAGIC = b"\xd0\xcf\x11\xe0"  # Microsoft Compound Document (DOC/XLS/PPT)
_ZIP_MAGIC = b"PK\x03\x04"         # ZIP / DOCX


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


def _fetch_all_pages_for_year(client: httpx.Client, cvm_code: str, year: int, ticker: str = "") -> list[dict]:
    """Fetches all pages of documents for a single year, sequentially.

    Each year is expected to run inside its own thread. Pages within
    the same year are fetched sequentially to keep per-year logic
    simple while the outer loop parallelizes across years.
    """
    wid = worker_id()
    print(f"[{wid}] {ticker or cvm_code} {year}")
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


def fetch_company_documents(cvm_code: str, ticker: str = "") -> list[dict]:
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
                executor.submit(_fetch_all_pages_for_year, client, cvm_code, yr, ticker): yr
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



def _clean_document_bytes(file_bytes: bytes) -> bytes:
    """Scans for magic headers (PDF, ZIP, OLE2) within the prefix of the file.

    If found at a non-zero offset (e.g., due to portal output garbage),
    strips the leading garbage bytes to yield a valid document file.
    """
    if not file_bytes:
        return file_bytes

    # Look for magic bytes in the first 4096 bytes
    prefix = file_bytes[:4096]

    indices = []
    for magic in [_PDF_MAGIC, _ZIP_MAGIC, _OLE2_MAGIC]:
        idx = prefix.find(magic)
        if idx != -1:
            indices.append(idx)

    if indices:
        earliest_idx = min(indices)
        if earliest_idx > 0:
            return file_bytes[earliest_idx:]

    return file_bytes


def _detect_file_type(file_bytes: bytes) -> str:
    """Detects document format from magic bytes, after removing potential leading garbage.

    Returns:
        File extension string: 'pdf', 'docx', 'xlsx', 'pptx', 'doc', or 'bin' for unknown formats.
    """
    cleaned = _clean_document_bytes(file_bytes)
    if cleaned[:4] == _PDF_MAGIC:
        return settings.ext_pdf
    if cleaned[:4] == _ZIP_MAGIC:
        import io
        import zipfile
        try:
            with zipfile.ZipFile(io.BytesIO(cleaned)) as z:
                names = z.namelist()
                if "word/document.xml" in names:
                    return settings.ext_docx
                if "xl/workbook.xml" in names:
                    return settings.ext_xlsx
                if "ppt/presentation.xml" in names:
                    return settings.ext_pptx
        except Exception:
            pass
        return settings.ext_docx  # default ZIP fallback
    if cleaned[:4] == _OLE2_MAGIC:
        return settings.ext_doc
    return settings.ext_bin


def download_document(download_url: str, search_url: str, output_dir: str, filename_base: str) -> tuple[str | None, str | None]:
    """Downloads a document using urlDownload (GET) with fallback to urlSearch (POST).

    After download, detects the actual file format via magic bytes and
    saves the cleaned bytes with the correct extension (.pdf / .doc / .bin).

    Returns:
        Tuple of (saved_file_path, detected_extension) or (None, None) on failure.
    """
    # Check if a previously downloaded file already exists to save data/bandwidth
    for ext in settings.supported_extensions:
        check_path = os.path.join(output_dir, f"{filename_base}.{ext}")
        if os.path.exists(check_path) and os.path.getsize(check_path) > 0:
            return check_path, ext

    file_bytes = _download_via_get(download_url)

    if not file_bytes and search_url:
        file_bytes = _download_via_post(search_url)

    if not file_bytes:
        return None, None

    file_bytes = _clean_document_bytes(file_bytes)
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


def extract_text_from_file(file_path: str, ext: str, idx: int, total: int) -> str:
    """Extracts text from a downloaded document based on its detected format.

    Dispatches to pypdf for PDFs, native ZIP parsing for DOCX/XLSX/PPTX, and antiword for legacy DOC files.
    Short-circuits if a non-empty .txt artifact already exists alongside the source file.
    """
    # Idempotency guard: skip CPU-heavy extraction if .txt already exists
    txt_path = os.path.splitext(file_path)[0] + ".txt"
    if os.path.exists(txt_path) and os.path.getsize(txt_path) > 0:
        return ""

    if ext == settings.ext_pdf:
        return _extract_text_pdf(file_path, idx, total)
    if ext == settings.ext_docx:
        return _extract_text_docx(file_path)
    if ext == settings.ext_xlsx:
        return _extract_text_xlsx(file_path)
    if ext == settings.ext_pptx:
        return _extract_text_pptx(file_path)
    if ext == settings.ext_doc:
        return _extract_text_doc(file_path)
    if ext == settings.ext_bin:
        return _extract_text_bin(file_path, idx, total)
    return ""


def _extract_text_pdf(pdf_path: str, idx: int, total: int) -> str:
    """Extracts text from PDF using pypdf, with OCR fallback for scanned documents.

    Strategy:
        1. Fast path — pypdf text extraction (native text layer).
        2. Fallback — pdf2image + pytesseract OCR (image-based/scanned pages).
    """
    # Fast path: native text extraction via pypdf
    try:
        reader = PdfReader(pdf_path)
        text_parts = []
        for page in reader.pages:
            text = page.extract_text()
            if text:
                text_parts.append(text)
        if text_parts:
            return "\n\n".join(text_parts)
    except Exception:
        pass

    # Fallback: OCR for scanned/image-only PDFs
    return _extract_text_pdf_ocr(pdf_path, idx, total)


def _extract_text_pdf_ocr(pdf_path: str, idx: int, total: int) -> str:
    """Renders PDF pages as images and runs Tesseract OCR.

    Requires system packages: tesseract-ocr, tesseract-ocr-por, poppler-utils.
    """
    try:
        from pdf2image import convert_from_path
        import pytesseract
    except ImportError:
        print("[-] OCR deps missing. Run: uv add pdf2image pytesseract")
        return ""

    try:
        from pdf2image import convert_from_path, pdfinfo_from_path
        import tempfile
        import os
        
        with tempfile.TemporaryDirectory() as temp_dir:
            try:
                info = pdfinfo_from_path(pdf_path)
                total_pages = int(info.get("Pages", 1))
            except Exception:
                total_pages = 1
                
            text_parts = []
            
            for i in range(1, total_pages + 1):
                if total_pages > 1:
                    print(f"[{worker_id()} {progress(idx, total)} OCR {progress(i, total_pages)}] {os.path.basename(pdf_path)}")
                
                # Render exactly one page at a time
                image_paths = convert_from_path(
                    pdf_path,
                    dpi=150,
                    output_folder=temp_dir,
                    paths_only=True,
                    fmt="jpeg",
                    first_page=i,
                    last_page=i
                )
                
                if image_paths:
                    img_path = image_paths[0]
                    text = pytesseract.image_to_string(img_path, lang="por")
                    if text and text.strip():
                        text_parts.append(text.strip())
                    
                    # Clean up the single image file immediately to free disk space
                    try:
                        os.remove(img_path)
                    except Exception:
                        pass
                        
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
        print("[-] antiword not installed. Run: sudo apt-get install -y antiword")
    except Exception:
        pass
    return ""


def _extract_text_docx(docx_path: str) -> str:
    """Extracts text from a DOCX (ZIP) file using zipfile and xml parsing."""
    import zipfile
    import xml.etree.ElementTree as ET

    try:
        with zipfile.ZipFile(docx_path) as z:
            names = z.namelist()
            if "word/document.xml" not in names:
                if "xl/workbook.xml" in names:
                    return _extract_text_xlsx(docx_path)
                if "ppt/presentation.xml" in names:
                    return _extract_text_pptx(docx_path)
                return ""
            xml_content = z.read("word/document.xml")

        root = ET.fromstring(xml_content)
        ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}

        paragraphs = []
        for p in root.findall(".//w:p", ns):
            texts = [t.text for t in p.findall(".//w:t", ns) if t.text]
            if texts:
                paragraphs.append("".join(texts))
        return "\n".join(paragraphs)
    except Exception as e:
        print(f"[-] Error extracting text from DOCX {docx_path}: {e}")
        return ""


def _extract_text_xlsx(xlsx_path: str) -> str:
    """Extracts text from an XLSX (ZIP) file using zipfile and xml parsing."""
    import zipfile
    import xml.etree.ElementTree as ET

    try:
        texts = []
        with zipfile.ZipFile(xlsx_path) as z:
            names = z.namelist()
            if "xl/workbook.xml" not in names and "xl/sharedStrings.xml" not in names:
                if "word/document.xml" in names:
                    return _extract_text_docx(xlsx_path)
                if "ppt/presentation.xml" in names:
                    return _extract_text_pptx(xlsx_path)
                return ""

            # Shared strings contains almost all text elements
            if "xl/sharedStrings.xml" in names:
                xml_content = z.read("xl/sharedStrings.xml")
                root = ET.fromstring(xml_content)
                ns = {"ns": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
                for t in root.findall(".//ns:t", ns):
                    if t.text:
                        texts.append(t.text)
            
            # Also parse sheet files for any inline strings
            for name in names:
                if name.startswith("xl/worksheets/sheet") and name.endswith(".xml"):
                    xml_content = z.read(name)
                    root = ET.fromstring(xml_content)
                    ns = {"ns": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
                    for t in root.findall(".//ns:t", ns):
                        if t.text:
                            texts.append(t.text)
        return "\n".join(texts)
    except Exception as e:
        print(f"[-] Error extracting text from XLSX {xlsx_path}: {e}")
        return ""


def _extract_text_pptx(pptx_path: str) -> str:
    """Extracts text from a PPTX (ZIP) file using zipfile and xml parsing."""
    import zipfile
    import xml.etree.ElementTree as ET

    try:
        texts = []
        with zipfile.ZipFile(pptx_path) as z:
            names = z.namelist()
            if "ppt/presentation.xml" not in names:
                if "word/document.xml" in names:
                    return _extract_text_docx(pptx_path)
                if "xl/workbook.xml" in names:
                    return _extract_text_xlsx(pptx_path)
                return ""

            slide_names = sorted([name for name in names if name.startswith("ppt/slides/slide") and name.endswith(".xml")])
            ns = {
                "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
                "p": "http://schemas.openxmlformats.org/presentationml/2006/main"
            }
            
            for slide_name in slide_names:
                xml_content = z.read(slide_name)
                root = ET.fromstring(xml_content)
                slide_texts = []
                for t in root.findall(".//a:t", ns):
                    if t.text:
                        slide_texts.append(t.text)
                if slide_texts:
                    texts.append(" ".join(slide_texts))
        return "\n".join(texts)
    except Exception as e:
        print(f"[-] Error extracting text from PPTX {pptx_path}: {e}")
        return ""


def _extract_input_value(html_content: str, input_name: str) -> str:
    """Resiliently extracts the value of a named input from HTML content."""
    # Try pattern: name="input_name" ... value="value"
    pattern1 = rf'<input\s+[^>]*?name="{re.escape(input_name)}"[^>]*?value="([^"]*)"'
    match = re.search(pattern1, html_content, re.IGNORECASE)
    if match:
        return match.group(1)
        
    # Try pattern: value="value" ... name="input_name"
    pattern2 = rf'<input\s+[^>]*?value="([^"]*)"[^>]*?name="{re.escape(input_name)}"'
    match = re.search(pattern2, html_content, re.IGNORECASE)
    if match:
        return match.group(1)
        
    # Try pattern with single quotes
    pattern3 = rf"<input\s+[^>]*?name='{re.escape(input_name)}'[^>]*?value='([^']*)'"
    match = re.search(pattern3, html_content, re.IGNORECASE)
    if match:
        return match.group(1)
        
    pattern4 = rf"<input\s+[^>]*?value='([^']*)'[^>]*?name='{re.escape(input_name)}'"
    match = re.search(pattern4, html_content, re.IGNORECASE)
    if match:
        return match.group(1)

    return ""


def _extract_form_action(html_content: str) -> str:
    """Resiliently extracts the form action URL from HTML content."""
    match = re.search(r'<form\s+[^>]*?action="([^"]*)"', html_content, re.IGNORECASE)
    if match:
        return match.group(1)
    match = re.search(r"<form\s+[^>]*?action='([^']*)'", html_content, re.IGNORECASE)
    if match:
        return match.group(1)
    return ""


def _extract_text_bin(bin_path: str, idx: int, total: int) -> str:
    """Extracts text from a .bin file which is an ASP.NET WebForms download page.
    
    It parses the HTML, extracts __VIEWSTATE and __VIEWSTATEGENERATOR,
    makes a POST request to the download endpoint, detects the file type
    (PDF, DOCX, or DOC), and delegates to the appropriate extractor.
    """
    try:
        with open(bin_path, "r", encoding="utf-8", errors="ignore") as f:
            html_content = f.read()
    except Exception as e:
        print(f"[-] Failed to read bin file {bin_path}: {e}")
        return ""

    # Parse form action, __VIEWSTATE, and __VIEWSTATEGENERATOR
    action = _extract_form_action(html_content)
    if not action:
        # Check if the content is actually not HTML, maybe it's already a PDF/DOC/DOCX/XLSX/PPTX but named .bin?
        try:
            with open(bin_path, "rb") as f:
                file_bytes = f.read()
            file_bytes = _clean_document_bytes(file_bytes)
            detected_type = _detect_file_type(file_bytes)
            if detected_type != settings.ext_bin:
                new_path = os.path.splitext(bin_path)[0] + f".{detected_type}"
                with open(new_path, "wb") as f_out:
                    f_out.write(file_bytes)
                if os.path.exists(bin_path):
                    os.remove(bin_path)
                if detected_type == settings.ext_pdf:
                    return _extract_text_pdf(new_path, idx, total)
                elif detected_type == settings.ext_docx:
                    return _extract_text_docx(new_path)
                elif detected_type == settings.ext_xlsx:
                    return _extract_text_xlsx(new_path)
                elif detected_type == settings.ext_pptx:
                    return _extract_text_pptx(new_path)
                elif detected_type == settings.ext_doc:
                    return _extract_text_doc(new_path)
        except Exception as e:
            print(f"[-] Error processing already-resolved bin file {bin_path}: {e}")
        return ""

    action_url = html.unescape(action)
    base_url = "https://www.rad.cvm.gov.br/ENET/"
    absolute_url = urljoin(base_url, action_url.lstrip("./"))

    viewstate = _extract_input_value(html_content, "__VIEWSTATE")
    viewstategen = _extract_input_value(html_content, "__VIEWSTATEGENERATOR")

    payload = {
        "__VIEWSTATE": viewstate,
        "__VIEWSTATEGENERATOR": viewstategen,
    }

    headers = {
        "User-Agent": settings.b3_http_headers.get("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"),
    }

    file_bytes = None
    try:
        print(f"[{worker_id()}] Downloading real file from bin: {os.path.basename(bin_path)}")
        with httpx.Client(verify=False, timeout=settings.cvm_pdf_timeout) as client:
            resp = client.post(absolute_url, data=payload, headers=headers)
            resp.raise_for_status()
            if len(resp.content) > 0:
                file_bytes = resp.content
    except Exception as e:
        print(f"[-] Failed to download real file from WebForms: {e}")
        return ""

    if not file_bytes:
        return ""

    file_bytes = _clean_document_bytes(file_bytes)
    ext = _detect_file_type(file_bytes)
    if ext not in (settings.ext_pdf, settings.ext_docx, settings.ext_xlsx, settings.ext_pptx, settings.ext_doc):
        print(f"[-] Real file download from bin has unsupported format: {ext}")
        return ""

    text = ""
    output_dir = os.path.dirname(bin_path)
    filename_base = os.path.splitext(os.path.basename(bin_path))[0]
    real_file_path = os.path.join(output_dir, f"{filename_base}.{ext}")

    try:
        # Save the real resolved file to disk
        with open(real_file_path, "wb") as f:
            f.write(file_bytes)

        # Delete the original .bin file since we resolved it
        try:
            if os.path.exists(bin_path):
                os.remove(bin_path)
        except Exception:
            pass

        # Extract text from the resolved file
        if ext == settings.ext_pdf:
            text = _extract_text_pdf(real_file_path, idx, total)
        elif ext == settings.ext_docx:
            text = _extract_text_docx(real_file_path)
        elif ext == settings.ext_xlsx:
            text = _extract_text_xlsx(real_file_path)
        elif ext == settings.ext_pptx:
            text = _extract_text_pptx(real_file_path)
        elif ext == settings.ext_doc:
            text = _extract_text_doc(real_file_path)
    except Exception as e:
        print(f"[-] Error extracting text from resolved bin: {e}")

    return text


def _process_single_fact(args: tuple) -> dict | None:
    """Internal helper to process a single fact in parallel: download, extract text, and save."""
    f, base_ticker, cvm, tickers, trading_name, base_output_dir, idx, total, start_time = args

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

    ticker_dir = os.path.join(base_output_dir, base_ticker, cat_clean)
    os.makedirs(ticker_dir, exist_ok=True)
    txt_path = os.path.join(ticker_dir, txt_filename)

    has_text = False

    # Tier 1: .txt already exists — skip download and extraction entirely
    if os.path.exists(txt_path) and os.path.getsize(txt_path) > 0:
        has_text = True
    else:
        doc_path, ext = None, None

        # Tier 2: source file (.pdf/.docx/.doc/.bin) exists locally — extract only, skip download
        for candidate_ext in settings.supported_extensions:
            candidate_path = os.path.join(ticker_dir, f"{filename_base}.{candidate_ext}")
            if os.path.exists(candidate_path) and os.path.getsize(candidate_path) > 0:
                doc_path, ext = candidate_path, candidate_ext
                break

        # Check if local file is .bin but has another format content (e.g. PDF/DOCX/DOC)
        if doc_path and ext == settings.ext_bin:
            try:
                with open(doc_path, "rb") as f_bytes_in:
                    file_bytes = f_bytes_in.read()
                file_bytes = _clean_document_bytes(file_bytes)
                detected_ext = _detect_file_type(file_bytes)
                if detected_ext != settings.ext_bin:
                    # It's actually a PDF/DOCX/DOC/XLSX/PPTX! Let's save the cleaned bytes to new path and remove the old .bin file
                    new_path = os.path.join(ticker_dir, f"{filename_base}.{detected_ext}")
                    with open(new_path, "wb") as f_bytes_out:
                        f_bytes_out.write(file_bytes)
                    os.remove(doc_path)
                    doc_path = new_path
                    ext = detected_ext
            except Exception as e:
                print(f"[-] Error detecting/renaming existing .bin file {doc_path}: {e}")

        # Tier 3: nothing on disk — download the document
        if not doc_path:
            doc_path, ext = download_document(download_url, search_url, ticker_dir, filename_base)

        if doc_path and ext:
            extracted_text = extract_text_from_file(doc_path, ext, idx, total)

            if extracted_text.strip():
                try:
                    with open(txt_path, "w", encoding="utf-8") as txt_file:
                        txt_file.write(extracted_text)
                    has_text = True
                    # Delete source file after successful extraction
                    if os.path.exists(doc_path):
                        os.remove(doc_path)
                except Exception as e:
                    print(f"[{worker_id()} -] Failed to save txt for {txt_filename}: {e}")
            else:
                print(f"[{worker_id()} -] No extractable text (kept for inspection): {os.path.basename(doc_path)}")

    if has_text:
        for ext_name in settings.supported_extensions:
            rem_path = os.path.join(ticker_dir, f"{filename_base}.{ext_name}")
            if os.path.exists(rem_path):
                try:
                    os.remove(rem_path)
                except Exception:
                    pass

        print(f"[{worker_id()} {progress(idx, total, start_time)}] {base_ticker}/{cat_clean}/{txt_filename}")
        return {
            "base_ticker": base_ticker,
            "cvm": cvm,
            "tickers": tickers,
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
        company: Dict with keys 'base_ticker', 'cvm', 'trading_name', 'tickers'
        base_output_dir: Base directory for storing downloaded files
        
    Returns:
        List of processed document metadata dictionaries.
    """
    if base_output_dir is None:
        base_output_dir = settings.docs_output_dir

    base_ticker = company["base_ticker"]
    cvm = company["cvm"]
    trading_name = company["trading_name"]
    tickers = company["tickers"]
    
    facts = fetch_company_documents(cvm, ticker=base_ticker)
    print(f"\n{trading_name} (CVM {cvm}, Base Ticker {base_ticker}) has {len(facts)} documents")

    # Prepare arguments for parallel processing
    total = len(facts)
    start_time = time.time()
    tasks = [(f, base_ticker, cvm, tickers, trading_name, base_output_dir, idx + 1, total, start_time) for idx, f in enumerate(facts)]
    processed_facts = []

    if not tasks:
        return processed_facts

    with concurrent.futures.ThreadPoolExecutor(max_workers=settings.b3_max_workers) as executor:
        future_to_task = {executor.submit(_process_single_fact, task): task for task in tasks}
        
        for future in concurrent.futures.as_completed(future_to_task):
            try:
                result = future.result()
                if result:
                    processed_facts.append(result)
            except Exception as e:
                print(f"[-] Task raised an exception: {e}")

    return processed_facts
