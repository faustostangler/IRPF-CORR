import httpx
import base64
import json
from datetime import datetime
import re
import time
import os
import warnings
from pypdf import PdfReader
import argparse

# Disable unverified SSL warnings to keep the execution log clean
warnings.filterwarnings("ignore")


def get_all_companies(script_dir: str = None) -> list:
    """Fetches all listed companies from B3 on the initial page using dynamic pagination and resiliency, with a local JSON cache."""
    if script_dir is None:
        script_dir = os.path.dirname(os.path.abspath(__file__))

    json_cache_path = os.path.join(script_dir, "all_companies.json")
    if os.path.exists(json_cache_path):
        try:
            with open(json_cache_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"Error reading local companies cache: {e}")

    print("Local mapping not found or invalid. Fetching B3 company list...")
    payload = {"language": "pt-br", "pageNumber": 1, "pageSize": 120}

    companies = []
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

    with httpx.Client(verify=False, timeout=15.0) as client:
        page_number = 1
        total_pages = 1  # Will be updated on the first request

        while page_number <= total_pages:
            payload["pageNumber"] = page_number
            payload_b64 = base64.b64encode(json.dumps(payload).encode("utf-8")).decode(
                "utf-8"
            )
            url = f"https://sistemaswebb3-listados.b3.com.br/listedCompaniesProxy/CompanyCall/GetInitialCompanies/{payload_b64}"

            # Resiliency: Retry up to 3 times per page
            for attempt in range(3):
                try:
                    resp = client.get(url, headers=headers)
                    resp.raise_for_status()
                    data = resp.json()

                    results = data.get("results", [])
                    page_info = data.get("page", {})

                    if not results:
                        total_pages = 0  # Force exit from the loop
                        break

                    companies.extend(results)
                    total_pages = page_info.get("totalPages", page_number)
                    break  # Success, exit the retry loop

                except (
                    httpx.RequestError,
                    httpx.HTTPStatusError,
                    json.JSONDecodeError,
                ) as e:
                    print(f"Error on page {page_number} (attempt {attempt + 1}/3): {e}")
                    if attempt == 2:
                        print(
                            "Persistent pagination failure. Returning already extracted data."
                        )
                        return companies
                    time.sleep(2)

            page_number += 1

    # Save cache
    try:
        with open(json_cache_path, "w", encoding="utf-8") as f:
            json.dump(companies, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Could not save companies cache: {e}")

    return companies


def get_cvm_for_ticker(ticker: str, all_companies: list) -> tuple:
    """Simple filter to find the codeCVM by ticker."""
    # Remove numeric suffix, e.g.: RADL3 -> RADL, TAEE11 -> TAEE
    base_ticker = re.sub(r"\d+$", "", ticker)

    for c in all_companies:
        issuing = c.get("issuingCompany", "").upper()
        trading = c.get("tradingName", "").upper()

        # Match ticker prefix
        if issuing == base_ticker or base_ticker in issuing:
            return c.get("codeCVM"), c.get("tradingName")

        # In case of BDR or similar name
        if ticker in trading or base_ticker in trading:
            return c.get("codeCVM"), c.get("tradingName")

    return None, None


def fetch_material_facts(code_cvm: str) -> list:
    """Fetches historically documents of all categories of a codeCVM from the B3 API since 2000 with dynamic pagination."""
    current_year = datetime.now().year
    all_facts = {}
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

    with httpx.Client(verify=False, timeout=30.0) as client:
        empty_years_count = 0
        for yr in range(current_year, 1999, -1):
            year_had_results = False

            payload_dict = {
                "language": "pt-br",
                "codeCVM": code_cvm,
                "year": yr,
                "dateInitial": f"{yr}-01-01",
                "dateFinal": f"{yr}-12-31",
                "pageNumber": 1,
                "pageSize": 120,
            }

            page_number = 1
            total_pages = 1

            while page_number <= total_pages:
                payload_dict["pageNumber"] = page_number
                payload_b64 = base64.b64encode(
                    json.dumps(payload_dict).encode("utf-8")
                ).decode("utf-8")
                url = f"https://sistemaswebb3-listados.b3.com.br/listedCompaniesProxy/CompanyCall/GetMaterialFacts/{payload_b64}"

                for attempt in range(3):
                    try:
                        resp = client.get(url, headers=headers)
                        resp.raise_for_status()
                        data = resp.json()

                        results = data.get("results", [])
                        page_info = data.get("page", {})

                        if not results:
                            break

                        year_had_results = True
                        for f in results:
                            link = f.get("urlSearch") or f.get("urlDocument")
                            if link:
                                all_facts[link] = f

                        total_pages = page_info.get("totalPages", page_number)
                        break
                    except Exception as e:
                        print(
                            f"Error fetching CVM {code_cvm} year {yr} page {page_number} (attempt {attempt + 1}/3): {e}"
                        )
                        if attempt == 2:
                            break
                        time.sleep(2)
                page_number += 1

            if year_had_results:
                empty_years_count = 0
            else:
                empty_years_count += 1

            if empty_years_count >= 4:
                print(
                    f"    -> Stopping retroactive search for CVM {code_cvm}: 4 consecutive years without records ({yr} to {yr + 3})."
                )
                break

    return list(all_facts.values())


def sanitize_filename(name: str) -> str:
    """Removes forbidden or unwanted characters from filenames."""
    s = name.strip().lower()
    replacements = {
        "á": "a",
        "à": "a",
        "â": "a",
        "ã": "a",
        "ä": "a",
        "é": "e",
        "è": "e",
        "ê": "e",
        "ë": "e",
        "í": "i",
        "ì": "i",
        "î": "i",
        "ï": "i",
        "ó": "o",
        "ò": "o",
        "ô": "o",
        "õ": "o",
        "ö": "o",
        "ú": "u",
        "ù": "u",
        "û": "u",
        "ü": "u",
        "ç": "c",
        "ñ": "n",
    }
    for char, replacement in replacements.items():
        s = s.replace(char, replacement)
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[-\s]+", "_", s)
    return s[:85]


def sanitize_foldername(name: str) -> str:
    """Sanitizes the category name to be used safely in the folder name."""
    if not name:
        return "fato_relevante"
    return sanitize_filename(name)


def parse_year_month(item_dict: dict) -> tuple:
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


def download_pdf(link: str, output_path: str) -> bool:
    """Downloads the PDF by making the POST request directly to the CVM endpoint."""
    match = re.search(r"ID=(\d+)", link)
    if not match:
        return False

    protocol = match.group(1)
    url = "https://www.rad.cvm.gov.br/ENET/frmExibirArquivoIPEExterno.aspx/ExibirPDF"
    payload = {
        "codigoInstituicao": "2",
        "numeroProtocolo": protocol,
        "token": "",
        "versaoCaptcha": "",
    }
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    }

    try:
        with httpx.Client(verify=False) as client:
            resp = client.post(url, json=payload, headers=headers, timeout=40.0)
            resp.raise_for_status()
            data = resp.json()

            pdf_b64 = data.get("d")
            if not pdf_b64:
                return False

            pdf_bytes = base64.b64decode(pdf_b64)
            with open(output_path, "wb") as f:
                f.write(pdf_bytes)
            return True
    except Exception as e:
        print(f"Error downloading PDF for protocol {protocol}: {e}")
        return False


def extract_pdf_text(pdf_path: str) -> str:
    """Extracts all text from a PDF file using the pypdf library."""
    try:
        reader = PdfReader(pdf_path)
        text_parts = []
        for page in reader.pages:
            text = page.extract_text()
            if text:
                text_parts.append(text)
        return "\n\n".join(text_parts)
    except Exception as e:
        print(f"Error extracting text from PDF {pdf_path}: {e}")
        return ""


def main():
    parser = argparse.ArgumentParser(
        description="Scraper for B3 material facts and market notices."
    )
    parser.add_argument(
        "--ticker", type=str, default=None, help="Run only for a specific ticker"
    )
    args = parser.parse_args()

    script_dir = os.path.dirname(os.path.abspath(__file__))

    if args.ticker:
        tickers = [args.ticker.upper()]
    else:
        tickers_path = os.path.join(script_dir, "tickers.txt")
        with open(tickers_path, "r") as f:
            tickers = [line.strip().upper() for line in f if line.strip()]

    print(f"Loaded {len(tickers)} tickers of interest.")

    project_root = os.path.abspath(os.path.join(script_dir, "..", ".."))
    docs_pdf_dir = os.path.join(project_root, "docs", "pdf")
    os.makedirs(docs_pdf_dir, exist_ok=True)
    print(f"Base document directory: {docs_pdf_dir}")

    print("Fetching B3 company list...")
    all_companies = get_all_companies(script_dir)

    facts_found = []

    for ticker in tickers:
        cvm, trading_name = get_cvm_for_ticker(ticker, all_companies)
        if not cvm:
            print(f"Ticker {ticker} not mapped in B3 database.")
            continue

        print(f"\n[+] Fetching historical facts for {ticker} (CVM: {cvm})...")
        facts = fetch_material_facts(cvm)
        print(f"    -> Found {len(facts)} historical facts.")

        for f in facts:
            link = f.get("urlSearch") or f.get("urlDocument")
            if not link:
                continue

            category = f.get("category") or "Fato Relevante"
            type_str = f.get("type") or ""
            year, month = parse_year_month(f)

            cat_clean = sanitize_foldername(category)

            # Define categories of interest
            ALLOWED_CATEGORIES = [
                "comunicado_ao_mercado",
                "fato_relevante",
                "aviso_aos_acionistas",
            ]

            if cat_clean not in ALLOWED_CATEGORIES:
                continue

            type_clean = sanitize_filename(type_str.strip()) if type_str else ""
            raw_subject = (f.get("subject") or f.get("kind") or "").strip()
            subj_slug = sanitize_filename(raw_subject) if raw_subject else ""

            match_id = re.search(r"ID=(\d+)", link)
            doc_id = match_id.group(1) if match_id else "doc"

            # Folder: docs/pdf/{company}/{category}/
            # File: {year}-{month}-{type_str}-{subj_slug}-{doc_id}
            parts = [year, month]
            if type_clean:
                parts.append(type_clean)
            if subj_slug:
                parts.append(subj_slug)
            parts.append(doc_id)
            filename_base = "-".join(parts)
            pdf_filename = f"{filename_base}.pdf"
            txt_filename = f"{filename_base}.txt"

            ticker_dir = os.path.join(docs_pdf_dir, ticker, cat_clean)
            os.makedirs(ticker_dir, exist_ok=True)
            pdf_path = os.path.join(ticker_dir, pdf_filename)
            txt_path = os.path.join(ticker_dir, txt_filename)

            print(f"    [!]: {f.get('subject')} [{year}/{month}]")

            # Local Cache/Idempotency control
            has_pdf = False
            if os.path.exists(pdf_path) and os.path.getsize(pdf_path) > 0:
                print("        -> PDF already exists locally. Skipping download.")
                has_pdf = True
            else:
                print("        -> Downloading PDF...")
                success = download_pdf(link, pdf_path)
                if success:
                    print("        -> PDF downloaded successfully!")
                    has_pdf = True
                else:
                    print("        -> Failed to download PDF.")

            if has_pdf:
                has_text = False
                if os.path.exists(txt_path) and os.path.getsize(txt_path) > 0:
                    has_text = True
                    # Clean up PDF if it still exists
                    if os.path.exists(pdf_path):
                        try:
                            os.remove(pdf_path)
                        except Exception as e:
                            print(f"        -> Error deleting PDF {pdf_path}: {e}")
                else:
                    print("        -> Extracting text from PDF...")
                    extracted_text = extract_pdf_text(pdf_path)

                    if extracted_text.strip():
                        txt_saved = False
                        try:
                            with open(txt_path, "w", encoding="utf-8") as txt_file:
                                txt_file.write(extracted_text)
                            txt_saved = True
                            print(
                                f"        -> Text extracted successfully! ({len(extracted_text)} characters)"
                            )
                            has_text = True
                        except Exception as e:
                            print(f"        -> Failed to save txt: {e}")

                        # Only delete PDF after txt is confirmed saved
                        if txt_saved:
                            try:
                                os.remove(pdf_path)
                            except Exception as e:
                                print(f"        -> Error deleting PDF {pdf_path}: {e}")
                    else:
                        print("        -> PDF has no extractable text.")

                # Get project relative paths for JSON storage
                rel_pdf_path = os.path.relpath(pdf_path, project_root)
                rel_txt_path = (
                    os.path.relpath(txt_path, project_root) if has_text else None
                )

                item = {
                    "ticker": ticker,
                    "trading_name": trading_name,
                    "date": f.get("dateReference") or f.get("deliveryDate"),
                    "subject": f.get("subject"),
                    "category": category,
                    "year": year,
                    "month": month,
                    "link": link,
                    "pdf_path": rel_pdf_path,
                    "txt_path": rel_txt_path,
                }
                facts_found.append(item)

    # Save consolidated results at the project root
    json_path = os.path.join(project_root, "filtered_material_facts.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(facts_found, f, ensure_ascii=False, indent=2)

    print(f"\nFinished! {len(facts_found)} facts cataloged and historically extracted.")
    print(f"Consolidated data saved to: {json_path}")


if __name__ == "__main__":
    main()
