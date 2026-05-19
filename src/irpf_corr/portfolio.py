from typing import Dict, List, Optional
from irpf_corr.models import BrokerageNote, Position
from irpf_corr.calculator import apportion_fees, calculate_average_cost


def process_brokerage_notes(
    notes: List[BrokerageNote], ticker_map: Optional[Dict[str, str]] = None
) -> Dict[str, Position]:
    """
    Processes a list of brokerage notes and returns the final portfolio positions.
    Notes are automatically sorted by date to ensure chronological correctness.
    """
    portfolio: Dict[str, Position] = {}
    ticker_map = ticker_map or {}

    sorted_notes = sorted(notes, key=lambda n: n.date)

    for note in sorted_notes:
        apportioned_trades = apportion_fees(note)

        for trade in apportioned_trades:
            mapped_ticker = ticker_map.get(trade.ticker, trade.ticker)
            if mapped_ticker != trade.ticker:
                trade = trade.model_copy(update={"ticker": mapped_ticker})

            current_position = portfolio.get(
                mapped_ticker,
                Position(ticker=mapped_ticker, quantity=0, average_cost="0.0000"),
            )

            new_position = calculate_average_cost(current_position, trade)

            portfolio[mapped_ticker] = new_position

    return portfolio
