import yaml
from pathlib import Path
from pydantic import TypeAdapter

from irpf_corr.models import BrokerageNote
from irpf_corr.portfolio import process_brokerage_notes

def main():
    notas_path = Path("docs/notas/notas.yaml")
    if not notas_path.exists():
        print(f"File not found: {notas_path}")
        return

    with open(notas_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    # Validate and parse
    adapter = TypeAdapter(list[BrokerageNote])
    notes = adapter.validate_python(data)

    # Process
    ticker_map = {"RUMO3": "RAIL3"}
    portfolio = process_brokerage_notes(notes, ticker_map=ticker_map)

    # Display results
    print(f"{'Ticker':<10} | {'Qtde':<10} | {'Preço Médio':<15} | {'Total Investido'}")
    print("-" * 65)
    for ticker in sorted(portfolio.keys()):
        pos = portfolio[ticker]
        print(f"{pos.ticker:<10} | {pos.quantity:<10} | R$ {pos.average_cost:<12} | R$ {pos.total_invested}")

if __name__ == "__main__":
    main()
