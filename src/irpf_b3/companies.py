import os
import json
import base64
import re
import time
import random
import concurrent.futures
import threading
from pathlib import Path
import httpx

# --- GLOBAL CONSTANTS & CONFIGURATIONS ---
B3_INITIAL_COMPANIES_URL_TEMPLATE = "https://sistemaswebb3-listados.b3.com.br/listedCompaniesProxy/CompanyCall/GetInitialCompanies/{payload_b64}"
B3_HTTP_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
B3_API_TIMEOUT = 15.0
B3_API_RETRIES = 3
B3_RETRY_SLEEP_SECONDS = 2
B3_DEFAULT_PAGE_SIZE = 120
B3_LANGUAGE = "pt-br"
B3_MAX_WORKERS = 10
CACHE_FILENAME = "all_companies.json"


def _fetch_companies_page(client: httpx.Client, page_num: int) -> tuple[list[dict], dict]:
    """Helper interno para buscar uma única página da B3 com resiliência."""
    payload = {
        "language": B3_LANGUAGE,
        "pageNumber": page_num,
        "pageSize": B3_DEFAULT_PAGE_SIZE
    }
    payload_b64 = base64.b64encode(json.dumps(payload).encode('utf-8')).decode('utf-8')
    url = B3_INITIAL_COMPANIES_URL_TEMPLATE.format(payload_b64=payload_b64)
    
    for attempt in range(B3_API_RETRIES):
        try:
            resp = client.get(url, headers=B3_HTTP_HEADERS)
            resp.raise_for_status()
            data = resp.json()
            worker_name = threading.current_thread().name
            worker_id = threading.get_ident()
            print(f"[{worker_name} | ID: {worker_id}] Página {page_num} obtida com sucesso.")
            return data.get("results", []), data.get("page", {})
        except (httpx.RequestError, httpx.HTTPStatusError, json.JSONDecodeError) as e:
            print(f"Erro na página {page_num} (tentativa {attempt+1}/{B3_API_RETRIES}): {e}")
            if attempt == B3_API_RETRIES - 1:
                print(f"Falha persistente na página {page_num}.")
                return [], {}
            # Backoff exponencial com jitter
            sleep_time = (B3_RETRY_SLEEP_SECONDS ** attempt) + random.uniform(0.1, 1.0)
            time.sleep(sleep_time)
    return [], {}


def get_all_companies(cache_dir: str = None) -> list[dict]:
    """
    Busca todas as companhias listadas na B3 com paginação paralela (multithreading).
    Usa cache local em JSON para evitar requisições desnecessárias.
    """
    if cache_dir is None:
        cache_dir = os.path.dirname(os.path.abspath(__file__))
        
    json_cache_path = os.path.join(cache_dir, CACHE_FILENAME)
    if os.path.exists(json_cache_path):
        try:
            with open(json_cache_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"Erro ao ler cache local de companhias: {e}")

    print("Buscando lista da B3...")
    companies = []
    
    limits = httpx.Limits(max_keepalive_connections=B3_MAX_WORKERS, max_connections=B3_MAX_WORKERS + 5)
    with httpx.Client(verify=False, timeout=B3_API_TIMEOUT, limits=limits) as client:
        # Primeiro obtemos a página 1 para descobrir o total de páginas
        results_page1, page_info = _fetch_companies_page(client, 1)
        companies.extend(results_page1)
        
        total_pages = page_info.get("totalPages", 1)
        
        if total_pages > 1:
            print(f"Buscando as {total_pages - 1} páginas restantes em paralelo com {B3_MAX_WORKERS} workers...")
            # Usa ThreadPoolExecutor para baixar o restante em paralelo
            with concurrent.futures.ThreadPoolExecutor(max_workers=B3_MAX_WORKERS) as executor:
                # Dispara as tarefas com pequeno delay (rate limiting entre dispatches)
                future_to_page = {}
                for page in range(2, total_pages + 1):
                    time.sleep(0.15)  # Pequeno delay para aliviar sobrecarga de DNS/TCP simultânea
                    future = executor.submit(_fetch_companies_page, client, page)
                    future_to_page[future] = page
                
                # Coleta os resultados à medida que completam
                for future in concurrent.futures.as_completed(future_to_page):
                    page = future_to_page[future]
                    try:
                        results, _ = future.result()
                        companies.extend(results)
                    except Exception as exc:
                        print(f"A página {page} gerou uma exceção durante a execução paralela: {exc}")
                        
    try:
        with open(json_cache_path, "w", encoding="utf-8") as f:
            json.dump(companies, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Não foi possível salvar o cache de companhias: {e}")
        
    return companies


def get_user_tickers(filepath: str) -> list[str]:
    """
    Lê o arquivo de tickers do usuário.
    """
    path = Path(filepath)
    if not path.exists():
        return []
    
    with open(path, "r", encoding="utf-8") as f:
        return [line.strip().upper() for line in f if line.strip()]


def get_cvm_for_ticker(ticker: str, all_companies: list[dict]) -> tuple[str | None, str | None]:
    """
    Filtro simples para encontrar o codeCVM pelo ticker.
    Retorna uma tupla (codeCVM, tradingName).
    """
    base_ticker = re.sub(r'\d+$', '', ticker)
    
    for c in all_companies:
        issuing = c.get('issuingCompany', '').upper()
        trading = c.get('tradingName', '').upper()
        
        if issuing == base_ticker or base_ticker in issuing:
            return str(c.get('codeCVM')), c.get('tradingName')
            
        if ticker in trading or base_ticker in trading:
            return str(c.get('codeCVM')), c.get('tradingName')
            
    return None, None


def get_filtered_companies(tickers_filepath: str) -> list[dict]:
    """
    Obtém todas as companhias da B3 e filtra de acordo com a lista de tickers fornecida.
    """
    all_companies = get_all_companies()
    user_tickers = get_user_tickers(tickers_filepath)
    
    filtered_companies = []
    
    for ticker in user_tickers:
        cvm, trading_name = get_cvm_for_ticker(ticker, all_companies)
        if cvm:
            filtered_companies.append({
                "ticker": ticker,
                "cvm": cvm,
                "trading_name": trading_name
            })
        else:
            print(f"Aviso: Ticker {ticker} não encontrado na base de empresas da B3.")
            
    return filtered_companies
