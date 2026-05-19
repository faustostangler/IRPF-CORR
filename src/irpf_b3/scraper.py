import httpx
import base64
import json
from datetime import datetime
import re
import time
import os
import warnings
from pypdf import PdfReader

# Desativa avisos de SSL não verificado para manter o log de execução limpo
warnings.filterwarnings("ignore")

def get_all_companies(script_dir: str = None) -> list:
    """Busca todas as companhias listadas na B3 na página inicial com paginação dinâmica e resiliência, com cache local em JSON."""
    if script_dir is None:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        
    json_cache_path = os.path.join(script_dir, "all_companies.json")
    if os.path.exists(json_cache_path):
        try:
            with open(json_cache_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"Erro ao ler cache local de companhias: {e}")

    print("Mapeamento local não encontrado ou inválido. Buscando lista da B3...")
    payload = {
        "language": "pt-br",
        "pageNumber": 1,
        "pageSize": 120
    }
    
    companies = []
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    
    with httpx.Client(verify=False, timeout=15.0) as client:
        page_number = 1
        total_pages = 1  # Será atualizado no primeiro request
        
        while page_number <= total_pages:
            payload["pageNumber"] = page_number
            payload_b64 = base64.b64encode(json.dumps(payload).encode('utf-8')).decode('utf-8')
            url = f"https://sistemaswebb3-listados.b3.com.br/listedCompaniesProxy/CompanyCall/GetInitialCompanies/{payload_b64}"
            
            # Resiliência: Tentar até 3 vezes por página
            for attempt in range(3):
                try:
                    resp = client.get(url, headers=headers)
                    resp.raise_for_status()
                    data = resp.json()
                    
                    results = data.get("results", [])
                    page_info = data.get("page", {})
                    
                    if not results:
                        total_pages = 0  # Força a saída do loop
                        break
                        
                    companies.extend(results)
                    total_pages = page_info.get("totalPages", page_number)
                    break  # Sucesso, sai do loop de retries
                    
                except (httpx.RequestError, httpx.HTTPStatusError, json.JSONDecodeError) as e:
                    print(f"Erro na página {page_number} (tentativa {attempt+1}/3): {e}")
                    if attempt == 2:
                        print("Falha persistente na paginação. Retornando dados já extraídos.")
                        return companies
                    time.sleep(2)
            
            page_number += 1
            
    # Salvar cache
    try:
        with open(json_cache_path, "w", encoding="utf-8") as f:
            json.dump(companies, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Não foi possível salvar o cache de companhias: {e}")
        
    return companies


def get_cvm_for_ticker(ticker: str, all_companies: list) -> tuple:
    """Filtro simples para encontrar o codeCVM pelo ticker."""
    # Remove final numérico ex: RADL3 -> RADL, TAEE11 -> TAEE
    base_ticker = re.sub(r'\d+$', '', ticker)
    
    for c in all_companies:
        issuing = c.get('issuingCompany', '').upper()
        trading = c.get('tradingName', '').upper()
        
        # Bate prefixo do ticker
        if issuing == base_ticker or base_ticker in issuing:
            return c.get('codeCVM'), c.get('tradingName')
            
        # Caso seja BDR ou nome parecido
        if ticker in trading or base_ticker in trading:
            return c.get('codeCVM'), c.get('tradingName')
            
    return None, None

def fetch_fato_relevante(code_cvm: str) -> list:
    """Busca historicamente documentos de todas as categorias de um codeCVM na API da B3 desde 2000 com paginação dinâmica."""
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
                        print(f"Erro ao buscar CVM {code_cvm} ano {yr} pág {page_number} (tentativa {attempt+1}/3): {e}")
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
    """Remove caracteres proibidos ou indesejados para nomes de arquivos."""
    # Remove acentos
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
    # Remove caracteres estranhos
    s = re.sub(r'[^\w\s-]', '', s)
    # Substitui espaços por underline para diferenciar do separador principal (hífen)
    s = re.sub(r'[-\s]+', '_', s)
    return s[:85]

def sanitize_foldername(name: str) -> str:
    """Higieniza o nome da categoria para ser usado no nome do arquivo de forma segura."""
    if not name:
        return "fato_relevante"
    return sanitize_filename(name)

def parse_year_month(item_dict: dict) -> tuple:
    """Extrai ano e mês de forma resiliente a partir das datas do item do B3."""
    # Tenta obter de dateTimeReference (ex: "2026-03-03T00:00:00")
    dt_ref = item_dict.get("dateTimeReference")
    if dt_ref and len(dt_ref) >= 7:
        try:
            parts = dt_ref.split("-")
            if len(parts) >= 2:
                return parts[0], parts[1]
        except Exception:
            pass
            
    # Tenta obter de dateReference (ex: "03/03/2026")
    d_ref = item_dict.get("dateReference")
    if d_ref:
        try:
            parts = d_ref.split("/")
            if len(parts) == 3:
                return parts[2], parts[1]
        except Exception:
            pass
            
    # Fallback para data atual
    now = datetime.now()
    return str(now.year), f"{now.month:02d}"

def download_pdf(link: str, output_path: str) -> bool:
    """Faz o download do PDF fazendo a requisição POST diretamente para o endpoint da CVM."""
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
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
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
        print(f"Erro ao baixar PDF do protocolo {protocol}: {e}")
        return False

def extract_pdf_text(pdf_path: str) -> str:
    """Extrai todo o texto de um arquivo PDF usando a biblioteca pypdf."""
    try:
        reader = PdfReader(pdf_path)
        text_parts = []
        for page in reader.pages:
            text = page.extract_text()
            if text:
                text_parts.append(text)
        return "\n\n".join(text_parts)
    except Exception as e:
        print(f"Erro ao extrair texto do PDF {pdf_path}: {e}")
        return ""

def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    tickers_path = os.path.join(script_dir, "tickers.txt")
    
    with open(tickers_path, "r") as f:
        tickers = [line.strip().upper() for line in f if line.strip()]
        
    print(f"Carregados {len(tickers)} tickers de interesse.")
    
    # Resolver o root do projeto (irpf-corr)
    # src/irpf_b3/scraper.py -> src/irpf_b3 -> src -> irpf-corr
    project_root = os.path.abspath(os.path.join(script_dir, "..", ".."))
    docs_pdf_dir = os.path.join(project_root, "docs", "pdf")
    os.makedirs(docs_pdf_dir, exist_ok=True)
    print(f"Diretório base de documentos (flat): {docs_pdf_dir}")
    
    print("Buscando lista mestre de empresas da B3...")
    all_companies = get_all_companies(script_dir)
    
    fatos_encontrados = []
    
    for ticker in tickers:
        cvm, trading_name = get_cvm_for_ticker(ticker, all_companies)
        if not cvm:
            print(f"Ticker {ticker} não mapeado no GetInitialCompanies da B3.")
            continue
            
        print(f"\n[+] Buscando Fatos Relevantes históricos de {ticker} (CVM: {cvm})...")
        fatos = fetch_fato_relevante(cvm)
        print(f"    -> Encontrados {len(fatos)} fatos históricos.")
        
        for f in fatos:
            link = f.get("urlSearch") or f.get("urlDocument")
            if not link:
                continue
            
            # Resgate do select/categoria, ano e mês reais
            category = f.get("category") or "Fato Relevante"
            type_str = f.get("type") or ""
            year, month = parse_year_month(f)
            
            # Higienização dos campos
            cat_clean = sanitize_foldername(category)
            type_clean = sanitize_filename(type_str.strip()) if type_str else ""
            subj_slug = sanitize_filename((f.get("subject") or "fato_relevante").strip())
            
            # Extrair ID de protocolo do link do documento
            match_id = re.search(r'ID=(\d+)', link)
            doc_id = match_id.group(1) if match_id else "doc"
            
            cat_type = f"{cat_clean} {type_clean}" if type_clean else cat_clean
            
            # Nome do arquivo unificado no formato: year-month-category+type.strip()-subject.strip()_docID.ext
            filename_base = f"{year}-{month}-{cat_type}-{subj_slug}_{doc_id}"
            pdf_filename = f"{filename_base}.pdf"
            txt_filename = f"{filename_base}.txt"
            
            ticker_dir = os.path.join(docs_pdf_dir, ticker, cat_clean)
            os.makedirs(ticker_dir, exist_ok=True)
            pdf_path = os.path.join(ticker_dir, pdf_filename)
            txt_path = os.path.join(ticker_dir, txt_filename)
            
            print(f"    [!]: {f.get('subject')} [{year}/{month}]")
            
            # Controle de Cache/Idempotência local
            has_pdf = False
            if os.path.exists(pdf_path) and os.path.getsize(pdf_path) > 0:
                print("        -> PDF já existe localmente. Pulando download.")
                has_pdf = True
            else:
                print("        -> Baixando PDF...")
                success = download_pdf(link, pdf_path)
                if success:
                    print("        -> PDF baixado com sucesso!")
                    has_pdf = True
                else:
                    print("        -> Falha ao baixar PDF.")
                    
            if has_pdf:
                has_text = False
                if os.path.exists(txt_path) and os.path.getsize(txt_path) > 0:
                    # print("        -> Texto já existe localmente.")
                    has_text = True
                else:
                    print("        -> Extraindo texto do PDF...")
                    extracted_text = extract_pdf_text(pdf_path)
                    
                    if extracted_text.strip():
                        with open(txt_path, "w", encoding="utf-8") as txt_file:
                            txt_file.write(extracted_text)
                        print(f"        -> Texto extraído com sucesso! ({len(extracted_text)} caracteres)")
                        has_text = True
                    else:
                        print("        -> PDF sem texto legível extraível.")
                
                # Obter caminhos relativos ao projeto para salvar no JSON
                rel_pdf_path = os.path.relpath(pdf_path, project_root)
                rel_txt_path = os.path.relpath(txt_path, project_root) if has_text else None
                
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
                    "txt_path": rel_txt_path
                }
                fatos_encontrados.append(item)
            
    # Salvar resultados consolidados no root do projeto
    json_path = os.path.join(project_root, "fatos_relevantes_filtrados.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(fatos_encontrados, f, ensure_ascii=False, indent=2)
        
    print(f"\nFinalizado! {len(fatos_encontrados)} fatos catalogados e extraídos historicamente desde 2002.")
    print(f"Dados consolidados salvos em: {json_path}")

if __name__ == "__main__":
    main()
