import httpx
import base64
import json
import os
import re
import time
import warnings
from datetime import datetime
from pypdf import PdfReader

# Disable insecure request warnings for clean logging
warnings.filterwarnings("ignore")

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL_NAME = "qwen2.5:7b"

SYSTEM_PROMPT = """Você é um assistente especializado em análise de fatos relevantes corporativos de empresas listadas na B3.
Sua tarefa é analisar o texto do documento fornecido e classificar estritamente a ocorrência de eventos societários corporativos de capital social, especificamente bonificações, desdobramentos ou grupamentos.

Regras Estritas de Classificação:
1. Responda APENAS com uma das seguintes palavras: "BONIFICAÇÃO", "EVENTOS", "TALVEZ" ou "NÃO".
2. Não adicione nenhuma introdução, explicação, justificativa, pontuação ou texto extra. A resposta deve ter exatamente uma palavra.
3. Responda "BONIFICAÇÃO" apenas se o documento confirmar explicitamente uma bonificação de ações (distribuição gratuita de novas ações aos acionistas) aprovada ou proposta.
4. Responda "EVENTOS" se o documento tratar explicitamente de desdobramento (split), grupamento (reverse split) de ações, ou alterações semelhantes na quantidade/estrutura de ações sem bonificação de fato.
5. Responda "TALVEZ" se houver indícios, estudos em andamento, propostas preliminares ou discussões sobre uma bonificação de ações futura ou um evento societário futuro (desdobramento/grupamento).
6. Responda "NÃO" para qualquer outro assunto (como pagamento regular de dividendos, JCP - Juros sobre o Capital Próprio, aumento de capital por subscrição em dinheiro, eleição de diretores, guidance, etc.)."""

USER_PROMPT_TEMPLATE = """Texto do documento:
---
{extracted_text}
---
Decisão (BONIFICAÇÃO, EVENTOS, TALVEZ ou NÃO):"""

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

def evaluate_text_with_qwen(text: str) -> str:
    """Send text snippet to local qwen2.5:7b Ollama instance for classification."""
    # Snip text to keep request context balanced (max ~4000 characters)
    snipped_text = text[:4000]
    
    payload = {
        "model": MODEL_NAME,
        "prompt": USER_PROMPT_TEMPLATE.format(extracted_text=snipped_text),
        "system": SYSTEM_PROMPT,
        "stream": False,
        "options": {
            "temperature": 0.0
        }
    }
    
    try:
        with httpx.Client(timeout=45.0) as client:
            resp = client.post(OLLAMA_URL, json=payload)
            resp.raise_for_status()
            data = resp.json()
            result = data.get("response", "").strip().upper()
            
            # Sanitize response to isolate exactly one of the valid tags
            for valid_tag in ["BONIFICAÇÃO", "EVENTOS", "TALVEZ", "NÃO"]:
                if valid_tag in result:
                    return valid_tag
            return f"DESCONHECIDO ({result[:20]})"
    except Exception as e:
        print(f"Erro ao chamar Ollama ({MODEL_NAME}): {e}")
        return "ERRO"

def load_processed_results(results_path: str) -> dict:
    """Load already evaluated document keys to avoid redundant LLM invocations."""
    processed = {}
    if os.path.exists(results_path):
        try:
            with open(results_path, "r", encoding="utf-8") as f:
                for line in f:
                    # Parse lines in format: [RESULT] name | Link: link
                    match = re.match(r"^\[(.*?)\]\s+(.*?)\s*\|\s*Link:\s*(.*?)$", line.strip())
                    if match:
                        res, doc_name, link = match.groups()
                        processed[doc_name.strip()] = res.strip()
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
    
    results_txt_path = os.path.join(project_root, "bonificacoes_resultados.txt")
    processed_cache = load_processed_results(results_txt_path)
    print(f"Carregados {len(processed_cache)} resultados salvos anteriormente.")
    
    print("Mapeando companhias B3...")
    all_companies = get_all_companies(script_dir)
    
    # Open results file in append mode to save incrementally
    with open(results_txt_path, "a", encoding="utf-8") as out_file:
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
                year, month = parse_year_month(f)
                cat_clean = sanitize_filename(category)
                subj_slug = sanitize_filename(f.get("subject") or "fato_relevante")
                
                match_id = re.search(r'ID=(\d+)', link)
                doc_id = match_id.group(1) if match_id else "doc"
                
                # Consistent flat naming format
                filename_base = f"{ticker}-{year}-{month}-{cat_clean}-{subj_slug}_{doc_id}"
                
                # Check cache of processed tags
                if filename_base in processed_cache:
                    # Already analyzed, skip entirely
                    print(f"    [OK] Já avaliado ({processed_cache[filename_base]}): {f.get('subject')} [{year}/{month}]")
                    continue
                
                ticker_dir = os.path.join(docs_pdf_dir, ticker)
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
                    # Check if pdf already exists locally
                    pdf_downloaded = False
                    if os.path.exists(pdf_path) and os.path.getsize(pdf_path) > 0:
                        pdf_downloaded = True
                    else:
                        print(f"    -> Baixando PDF para: {f.get('subject')} [{year}/{month}]")
                        pdf_downloaded = download_pdf(link, pdf_path)
                        
                    if pdf_downloaded:
                        extracted_text = extract_pdf_text(pdf_path)
                        if extracted_text.strip():
                            try:
                                with open(txt_path, "w", encoding="utf-8") as text_file:
                                    text_file.write(extracted_text)
                            except Exception as e:
                                print(f"        -> Falha ao salvar txt: {e}")
                        
                        # Delete the downloaded/existing PDF file immediately as requested
                        if os.path.exists(pdf_path):
                            try:
                                os.remove(pdf_path)
                            except Exception as e:
                                print(f"        -> Erro ao deletar PDF {pdf_path}: {e}")
                
                if extracted_text.strip():
                    print(f"    -> Avaliando com Ollama ({MODEL_NAME}): {f.get('subject')[:50]}...")
                    decision = evaluate_text_with_qwen(extracted_text)
                    print(f"        === RESULTADO: {decision} ===")
                    
                    # Write immediately to output file
                    out_file.write(f"[{decision}] {filename_base} | Link: {link}\n")
                    out_file.flush() # Force write to disk
                else:
                    print(f"    [-] Não foi possível obter o texto do fato: {f.get('subject')}")

    print(f"\nConcluído! Todos os fatos novos foram analisados e gravados em: {results_txt_path}")

if __name__ == "__main__":
    main()
