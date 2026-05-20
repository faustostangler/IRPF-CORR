import os
import json
import time
from irpf_b3.companies import get_filtered_companies
from irpf_b3.documents import process_company_documents
from irpf_b3.helpers import progress
from irpf_b3.scanner import run_pipeline

# --- GLOBAL CONSTANTS & CONFIGURATIONS ---
TICKERS_FILENAME = "tickers.txt"
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DOCS_PDF_DIR = os.path.join(PROJECT_ROOT, "docs", "pdf")

def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    tickers_path = os.path.join(script_dir, TICKERS_FILENAME)

    # Use a mock for debugging Step 2 as requested.
    # To use the real list later, just uncomment the get_filtered_companies line.
    companies = get_filtered_companies(tickers_path)
    # companies = [{"ticker": "WEGE3", "cvm": "5410", "trading_name": "WEG"}]

    total = len(companies)
    print(f"\nProcessing documents for {total} companies...")
    
    all_processed_facts = []
    start_time = time.time()
    
    for idx, comp in enumerate(companies, 1):
        print(f"\n{progress(idx, total, start_time)} Processing {comp['ticker']}...")
        facts = process_company_documents(comp, base_output_dir=DOCS_PDF_DIR)
        all_processed_facts.extend(facts)
        
    print(f"\nFinished processing. Total documents extracted: {len(all_processed_facts)}")

    # Save consolidated results at the project root for downstream tasks (Step 3)
    json_path = os.path.join(PROJECT_ROOT, "filtered_material_facts.json")
    try:
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(all_processed_facts, f, ensure_ascii=False, indent=2)
        print(f"Consolidated document data saved to: {json_path}")
    except Exception as e:
        print(f"Failed to save consolidated document data: {e}")

    # --- STAGE 3: Corporate Event Pipeline ---
    tickers = [comp['ticker'] for comp in companies]
    run_pipeline(tickers, DOCS_PDF_DIR)

if __name__ == "__main__":
    main()
print("done!")  # keep fo me for my final brakpoint debug
