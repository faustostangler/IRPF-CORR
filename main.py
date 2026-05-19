import yaml
from pathlib import Path
from pydantic import TypeAdapter

from decimal import Decimal

from irpf_corr.models import BrokerageNote
from irpf_corr.portfolio import process_brokerage_notes
from irpf_corr.ptax import get_ptax

def main():
    notes_path = Path("docs/notas/notas.yaml")
    if not notes_path.exists():
        print(f"File not found: {notes_path}")
        return

    with open(notes_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    # Validate and parse
    adapter = TypeAdapter(list[BrokerageNote])
    notes = adapter.validate_python(data)

    for note in notes:
        if note.currency == "USD" and note.exchange_rate == Decimal("1"):
            note.exchange_rate = get_ptax(note.date)

    # Process
    ticker_map = {"RUMO3": "RAIL3"}
    portfolio = process_brokerage_notes(notes, ticker_map=ticker_map)

    # Display results
    print(f"{'Ticker':<10} | {'Qty':<10} | {'Average Price':<15} | {'Total Invested'}")
    print("-" * 65)
    for ticker in sorted(portfolio.keys()):
        pos = portfolio[ticker]
        print(f"{pos.ticker:<10} | {pos.quantity:<10} | R$ {pos.average_cost:<12} | R$ {pos.total_invested:.2f}")

if __name__ == "__main__":
    main()
