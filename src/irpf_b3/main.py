import os
from irpf_b3.companies import get_filtered_companies

# --- GLOBAL CONSTANTS & CONFIGURATIONS ---
TICKERS_FILENAME = "tickers.txt"


def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    tickers_path = os.path.join(script_dir, TICKERS_FILENAME)
    
    print("Iniciando o processamento do irpf_b3...")
    
    companies = get_filtered_companies(tickers_path)
    
    print(f"\nEncontradas {len(companies)} companhias mapeadas a partir de {tickers_path}:")
    for comp in companies:
        print(f" - {comp['ticker']}: CVM {comp['cvm']} ({comp['trading_name']})")

if __name__ == "__main__":
    main()
