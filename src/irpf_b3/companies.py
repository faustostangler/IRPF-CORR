import os
import json
import base64
import re
import time
import random
import concurrent.futures
from pathlib import Path
import httpx

from irpf_b3.config import settings
from irpf_b3.helpers import worker_id


def _fetch_companies_page(
    client: httpx.Client, page_num: int
) -> tuple[list[dict], dict]:
    """Internal helper to fetch a single page from B3 with resiliency."""
    payload = {
        "language": settings.b3_language,
        "pageNumber": page_num,
        "pageSize": settings.b3_default_page_size,
    }
    payload_b64 = base64.b64encode(json.dumps(payload).encode("utf-8")).decode("utf-8")
    url = settings.b3_initial_companies_url_template.format(payload_b64=payload_b64)

    for attempt in range(settings.b3_api_retries):
        try:
            resp = client.get(url, headers=settings.b3_http_headers)
            resp.raise_for_status()
            data = resp.json()
            return data.get("results", []), data.get("page", {})
        except (httpx.RequestError, httpx.HTTPStatusError, json.JSONDecodeError) as e:
            if attempt == settings.b3_api_retries - 1:
                return [], {}
            # Exponential backoff with jitter
            sleep_time = (settings.b3_retry_sleep_seconds**attempt) + random.uniform(0.1, 1.0)
            time.sleep(sleep_time)
    return [], {}


def get_all_companies(cache_dir: str = None) -> list[dict]:
    """
    Fetches all listed companies from B3 using parallel pagination (multithreading).
    Always attempts to download fresh data, saving it to a local JSON cache.
    Falls back to the cache if the network download fails completely.
    """
    if cache_dir is None:
        cache_dir = os.path.dirname(os.path.abspath(__file__))

    json_cache_path = os.path.join(cache_dir, settings.companies_cache_filename)
    companies = []

    limits = httpx.Limits(
        max_keepalive_connections=settings.b3_max_workers, max_connections=settings.b3_max_workers + 5
    )
    
    try:
        with httpx.Client(verify=False, timeout=settings.b3_api_timeout, limits=limits) as client:
            # First obtain page 1 to find out the total number of pages
            results_page1, page_info = _fetch_companies_page(client, 1)
            if results_page1:
                companies.extend(results_page1)
                total_pages = page_info.get("totalPages", 1)

                if total_pages > 1:
                    # Use ThreadPoolExecutor to download the rest in parallel
                    with concurrent.futures.ThreadPoolExecutor(
                        max_workers=settings.b3_max_workers
                    ) as executor:
                        # Dispatch tasks with a short delay (rate limiting between dispatches)
                        future_to_page = {}
                        for page in range(2, total_pages + 1):
                            future = executor.submit(_fetch_companies_page, client, page)
                            future_to_page[future] = page

                        # Collect results as they complete
                        for future in concurrent.futures.as_completed(future_to_page):
                            page = future_to_page[future]
                            try:
                                results, _ = future.result()
                                companies.extend(results)
                            except Exception as exc:
                                print(
                                    f"[{worker_id()} -] Page {page} raised an exception: {exc}"
                                )
    except Exception as e:
        print(f"Network error during B3 companies download: {e}")

    # Fallback to local cache if download failed completely (empty list)
    if not companies:
        if os.path.exists(json_cache_path):
            try:
                with open(json_cache_path, "r", encoding="utf-8") as f:
                    cached_data = json.load(f)
                    if cached_data:
                        print("Loading local cache companies")
                        return cached_data
            except Exception as e:
                print(f"Error reading local companies cache during fallback: {e}")
    else:
        # Save cache if download succeeded
        try:
            with open(json_cache_path, "w", encoding="utf-8") as f:
                json.dump(companies, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"Could not save companies cache: {e}")

    return companies


def get_user_tickers(filepath: str) -> list[str]:
    """
    Reads the user's tickers file.
    """
    path = Path(filepath)
    if not path.exists():
        return []

    with open(path, "r", encoding="utf-8") as f:
        return [line.strip().upper() for line in f if line.strip()]


def get_cvm_for_ticker(
    ticker: str, all_companies: list[dict]
) -> tuple[str | None, str | None]:
    """
    Simple filter to find codeCVM by ticker.
    Returns a tuple (codeCVM, tradingName).
    """
    base_ticker = re.sub(r"\d+$", "", ticker)

    # First pass: try exact match on issuingCompany
    for c in all_companies:
        issuing = c.get("issuingCompany", "").upper()
        if issuing == base_ticker:
            return str(c.get("codeCVM")), c.get("tradingName")

    # Second pass: fallback to exact match on tradingName
    for c in all_companies:
        trading = c.get("tradingName", "").upper()
        if trading == base_ticker or trading == ticker:
            return str(c.get("codeCVM")), c.get("tradingName")

    return None, None


def get_filtered_companies(tickers_filepath: str) -> list[dict]:
    """
    Obtains all B3 companies and filters according to the provided tickers list.
    """

    print("Getting b3 companies...")

    all_companies = get_all_companies()
    user_tickers = get_user_tickers(tickers_filepath)

    filtered_companies = []

    for ticker in user_tickers:
        cvm, trading_name = get_cvm_for_ticker(ticker, all_companies)
        if cvm:
            filtered_companies.append(
                {"ticker": ticker, "cvm": cvm, "trading_name": trading_name}
            )

    return filtered_companies
