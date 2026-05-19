import csv
import httpx
import base64
import json
import os
import re
import time
import warnings
from datetime import datetime
from pypdf import PdfReader

from irpf_b3.llm_client import classify_corporate_event, MODEL_NAME

# Disable insecure request warnings for clean logging
warnings.filterwarnings("ignore")

def get_all_companies(script_dir: str) -> list:
    """Load listed companies from local JSON cache if possible, otherwise fetch from B3."""
    json_cache_path = os.path.join(script_dir, "all_companies.json")
    if os.path.exists(json_cache_path):
        try:
            with open(json_cache_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"Erro ao ler cache local de companhias: {e}")

    # Fallback to B3 fetch if cache is missing or corrupt
    print("Mapeamento local não encontrado ou inválido. Buscando lista da B3...")
    payload = {"language": "pt-br", "pageNumber": 1, "pageSize": 120}
    companies = []
    headers = {"User-Agent": "Mozilla/5.0"}
    
    with httpx.Client(verify=False, timeout=15.0) as client:
        page_number = 1
        total_pages = 1
        while page_number <= total_pages:
            payload["pageNumber"] = page_number
            payload_b64 = base64.b64encode(json.dumps(payload).encode('utf-8')).decode('utf-8')
            url = f"https://sistemaswebb3-listados.b3.com.br/listedCompaniesProxy/CompanyCall/GetInitialCompanies/{payload_b64}"
            for attempt in range(3):
                try:
                    resp = client.get(url, headers=headers)
                    resp.raise_for_status()
                    data = resp.json()
                    results = data.get("results", [])
                    page_info = data.get("page", {})
                    if not results:
                        total_pages = 0
                        break
                    companies.extend(results)
                    total_pages = page_info.get("totalPages", page_number)
                    break
                except Exception as e:
                    print(f"Erro na página B3 {page_number} (tentativa {attempt+1}/3): {e}")
                    if attempt == 2:
                        return companies
                    time.sleep(2)
            page_number += 1
            
    # Save cache
    try:
        with open(json_cache_path, "w", encoding="utf-8") as f:
            json.dump(companies, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Não foi possível salvar o cache de companhias: {e}")
        
    return companies

def get_cvm_for_ticker(ticker: str, all_companies: list) -> tuple:
    """Find the CVM code and trading name for a given B3 ticker."""
    base_ticker = re.sub(r'\d+$', '', ticker)
    for c in all_companies:
        issuing = c.get('issuingCompany', '').upper()
        trading = c.get('tradingName', '').upper()
        if issuing == base_ticker or base_ticker in issuing:
            return c.get('codeCVM'), c.get('tradingName')
        if ticker in trading or base_ticker in trading:
            return c.get('codeCVM'), c.get('tradingName')
    return None, None

def fetch_fato_relevante(code_cvm: str) -> list:
    """Retrieve all historical filings of all categories and years for a CVM code since 2000."""
    current_year = datetime.now().year
    all_fatos = {}
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
                "pageSize": 120 
            }
            
            page_number = 1
            total_pages = 1
            
            while page_number <= total_pages:
                payload_dict["pageNumber"] = page_number
                payload_b64 = base64.b64encode(json.dumps(payload_dict).encode('utf-8')).decode('utf-8')
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
                                all_fatos[link] = f
                                
                        total_pages = page_info.get("totalPages", page_number)
                        break
                    except Exception as e:
                        print(f"Erro CVM {code_cvm} ano {yr} pág {page_number} (tentativa {attempt+1}/3): {e}")
                        if attempt == 2:
                            break
                        time.sleep(2)
                page_number += 1
            
            if year_had_results:
                empty_years_count = 0
            else:
                empty_years_count += 1
                
            if empty_years_count >= 4:
                print(f"    -> Parando busca retroativa para CVM {code_cvm}: 4 anos consecutivos sem registros ({yr} a {yr+3}).")
                break
                
    return list(all_fatos.values())


def sanitize_filename(name: str) -> str:
    """Sanitize string for clean filenames."""
    s = name.strip().lower()
    replacements = {
        'á': 'a', 'à': 'a', 'â': 'a', 'ã': 'a', 'ä': 'a',
        'é': 'e', 'è': 'e', 'ê': 'e', 'ë': 'e',
        'í': 'i', 'ì': 'i', 'î': 'i', 'ï': 'i',
        'ó': 'o', 'ò': 'o', 'ô': 'o', 'õ': 'o', 'ö': 'o',
        'ú': 'u', 'ù': 'u', 'û': 'u', 'ü': 'u',
        'ç': 'c', 'ñ': 'n'
    }
    for char, replacement in replacements.items():
        s = s.replace(char, replacement)
    s = re.sub(r'[^\w\s-]', '', s)
    s = re.sub(r'[-\s]+', '_', s)
    return s[:85]

def parse_year_month(item_dict: dict) -> tuple:
    """Parse year and month from B3 dates safely."""
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
    """Download filing PDF directly via CVM POST endpoint."""
    match = re.search(r'ID=(\d+)', link)
    if not match:
        return False
    protocol = match.group(1)
    url = "https://www.rad.cvm.gov.br/ENET/frmExibirArquivoIPEExterno.aspx/ExibirPDF"
    payload = {
        "codigoInstituicao": "2",
        "numeroProtocolo": protocol,
        "token": "",
        "versaoCaptcha": ""
    }
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "User-Agent": "Mozilla/5.0"
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
        print(f"Erro download PDF ID {protocol}: {e}")
        return False

def extract_pdf_text(pdf_path: str) -> str:
    """Extract full plain text from PDF."""
    try:
        reader = PdfReader(pdf_path)
        text_parts = []
        for page in reader.pages:
            text = page.extract_text()
            if text:
                text_parts.append(text)
        return "\n\n".join(text_parts)
    except Exception as e:
        print(f"Erro extração texto PDF {pdf_path}: {e}")
        return ""

# WHY: LLM classification logic is now in llm_client.py (single source of truth).
# This alias preserves backward compatibility for callers within this module.
evaluate_text_with_qwen = classify_corporate_event

def load_processed_results(results_path: str) -> dict:
    """Load already evaluated document keys from CSV to avoid redundant LLM invocations."""
    processed = {}
    if os.path.exists(results_path):
        try:
            with open(results_path, "r", encoding="utf-8", newline="") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    filename = row.get("filename", "").strip()
                    resultado = row.get("resultado", "").strip()
                    if filename:
                        processed[filename] = resultado
        except Exception as e:
            print(f"Erro ao carregar resultados anteriores: {e}")
    return processed

def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    tickers_path = os.path.join(script_dir, "tickers.txt")
    
    if not os.path.exists(tickers_path):
        print(f"Arquivo de tickers não encontrado em: {tickers_path}")
        return
        
    with open(tickers_path, "r") as f:
        tickers = [line.strip().upper() for line in f if line.strip()]
        
    print(f"Carregados {len(tickers)} tickers de interesse.")
    
    project_root = os.path.abspath(os.path.join(script_dir, "..", ".."))
    docs_pdf_dir = os.path.join(project_root, "docs", "pdf")
    os.makedirs(docs_pdf_dir, exist_ok=True)
    
    results_txt_path = os.path.join(docs_pdf_dir, "bonificacoes_resultados.csv")
    processed_cache = load_processed_results(results_txt_path)
    print(f"Carregados {len(processed_cache)} resultados salvos anteriormente.")
    
    print("Mapeando companhias B3...")
    all_companies = get_all_companies(script_dir)
    
    # Open CSV in append mode; write header only if file is new
    csv_is_new = not os.path.exists(results_txt_path) or os.path.getsize(results_txt_path) == 0
    with open(results_txt_path, "a", encoding="utf-8", newline="") as out_file:
        csv_writer = csv.DictWriter(out_file, fieldnames=["resultado", "ticker", "year", "month", "category", "filename", "link"])
        if csv_is_new:
            csv_writer.writeheader()
        for ticker in tickers:
            cvm, trading_name = get_cvm_for_ticker(ticker, all_companies)
            if not cvm:
                print(f"\n[-] Ticker {ticker} não mapeado no cadastro da B3.")
                continue
                
            print(f"\n[+] Buscando fatos históricos para {ticker} (CVM: {cvm})...")
            fatos = fetch_fato_relevante(cvm)
            print(f"    -> Encontrados {len(fatos)} fatos históricos.")
            
            for f in fatos:
                link = f.get("urlSearch") or f.get("urlDocument")
                if not link:
                    continue
                    
                category = f.get("category") or "Fato Relevante"
                type_str = f.get("type") or ""
                year, month = parse_year_month(f)
                
                cat_clean = sanitize_filename(category) if category else "fato_relevante"
                
                # Definir categorias de interesse (expansível futuramente)
                ALLOWED_CATEGORIES = [
                    'comunicado_ao_mercado',
                    'fato_relevante',
                    # 'aviso_aos_acionistas',
                ]
                sobre_acoes = [
                    'comunicado_ao_mercado/2024-04-outros_comunicados_nao_considerados_fatos_relevantes-data_base_para_o_desdobramento_das_acoes_do_bb-1218752.txt',
                    'fato_relevante/2024-02-split_de_acoes_decisao_age_e_aprovacao_do_estatuto_pelo_bacen-1189463.txt',
                    
                ]

                if cat_clean not in ALLOWED_CATEGORIES:
                    continue

                type_clean = sanitize_filename(type_str.strip()) if type_str else ""
                # Use subject; fallback to kind (B3 uses both interchangeably by category)
                raw_subject = (f.get("subject") or f.get("kind") or "").strip()
                subj_slug = sanitize_filename(raw_subject) if raw_subject else ""
                
                match_id = re.search(r'ID=(\d+)', link)
                doc_id = match_id.group(1) if match_id else "doc"
                
                # Pasta: docs/pdf/{company}/{category}/
                # Arquivo: {year}-{month}-{type_str}-{subj_slug}-{doc_id}.txt
                parts = [year, month]
                if type_clean:
                    parts.append(type_clean)
                if subj_slug:
                    parts.append(subj_slug)
                parts.append(doc_id)
                filename_base = "-".join(parts)
                
                # Check cache of processed tags
                if filename_base in processed_cache:
                    # Already analyzed, skip entirely
                    print(f"    [OK] Já avaliado ({processed_cache[filename_base]}): {f.get('subject')} [{year}/{month}]")
                    continue
                
                ticker_dir = os.path.join(docs_pdf_dir, ticker, cat_clean)
                os.makedirs(ticker_dir, exist_ok=True)
                pdf_path = os.path.join(ticker_dir, f"{filename_base}.pdf")
                txt_path = os.path.join(ticker_dir, f"{filename_base}.txt")
                
                extracted_text = ""
                
                # Check local flat txt file cache
                if os.path.exists(txt_path) and os.path.getsize(txt_path) > 0:
                    try:
                        with open(txt_path, "r", encoding="utf-8") as text_file:
                            extracted_text = text_file.read()
                    except Exception as e:
                        print(f"        -> Erro ao ler txt local: {e}")
                
                # If no text local, download pdf and extract
                if not extracted_text.strip():
                    pdf_downloaded = False
                    if os.path.exists(pdf_path) and os.path.getsize(pdf_path) > 0:
                        # PDF already on disk (e.g. from interrupted previous run)
                        pdf_downloaded = True
                    else:
                        print(f"    -> Baixando PDF para: {f.get('subject')} [{year}/{month}]")
                        pdf_downloaded = download_pdf(link, pdf_path)
                        
                    if pdf_downloaded:
                        extracted_text = extract_pdf_text(pdf_path)
                        if extracted_text.strip():
                            txt_saved = False
                            try:
                                with open(txt_path, "w", encoding="utf-8") as text_file:
                                    text_file.write(extracted_text)
                                txt_saved = True
                            except Exception as e:
                                print(f"        -> Falha ao salvar txt: {e}")
                            
                            # Only delete PDF after txt is confirmed saved
                            if txt_saved:
                                try:
                                    os.remove(pdf_path)
                                except Exception as e:
                                    print(f"        -> Erro ao deletar PDF {pdf_path}: {e}")
                        else:
                            # Extraction failed (scanned/encrypted PDF) — keep PDF, log warning
                            print(f"        -> [AVISO] Texto vazio extraído do PDF — mantendo PDF para inspeção: {pdf_path}")
                
                if extracted_text.strip():
                    print(f"    -> Avaliando com Ollama ({MODEL_NAME}): {f.get('subject')[:50]}...")
                    decision = evaluate_text_with_qwen(extracted_text)
                    print(f"        === RESULTADO: {decision} ===")
                    
                    # Write row incrementally to CSV
                    csv_writer.writerow({
                        "resultado": decision,
                        "ticker": ticker,
                        "year": year,
                        "month": month,
                        "category": category,
                        "filename": filename_base,
                        "link": link,
                    })
                    out_file.flush()  # Force write to disk
                else:
                    print(f"    [-] Não foi possível obter o texto do fato: {f.get('subject')}")

    print(f"\nConcluído! Todos os fatos novos foram analisados e gravados em: {results_txt_path}")

if __name__ == "__main__":
    main()
