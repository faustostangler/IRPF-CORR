import datetime
from decimal import Decimal
import pytest

from irpf_corr.models import BrokerageNote, Trade, Position
from irpf_corr.portfolio import process_brokerage_notes

def test_process_single_brokerage_note():
    note = BrokerageNote(
        date=datetime.date(2023, 1, 5),
        number="100",
        broker="Test",
        settlement_fee=Decimal("1.00"),
        exchange_fee=Decimal("0.50"),
        brokerage_fee=Decimal("0.00"),
        iss_tax=Decimal("0.00"),
        irrf_daytrade=Decimal("0.00"),
        trades=[
            Trade(ticker="TEST3", direction="BUY", quantity=100, unit_price=Decimal("10.00"))
        ]
    )
    
    portfolio = process_brokerage_notes([note])
    
    assert "TEST3" in portfolio
    pos = portfolio["TEST3"]
    assert pos.ticker == "TEST3"
    assert pos.quantity == 100
    # Total volume = 1000. Fees = 1.50. Total cost = 1001.50. Average = 10.0150
    assert pos.average_cost == Decimal("10.0150")

def test_process_multiple_notes_chronological_ordering():
    note1 = BrokerageNote(
        date=datetime.date(2023, 1, 10),
        number="102",
        broker="Test",
        settlement_fee=Decimal("1.00"),
        exchange_fee=Decimal("0.50"),
        brokerage_fee=Decimal("0.00"),
        iss_tax=Decimal("0.00"),
        irrf_daytrade=Decimal("0.00"),
        trades=[
            Trade(ticker="TEST3", direction="BUY", quantity=100, unit_price=Decimal("12.00")) # cost: 1201.50
        ]
    )
    
    note2 = BrokerageNote(
        date=datetime.date(2023, 1, 5), # Note the earlier date
        number="101",
        broker="Test",
        settlement_fee=Decimal("1.00"),
        exchange_fee=Decimal("0.50"),
        brokerage_fee=Decimal("0.00"),
        iss_tax=Decimal("0.00"),
        irrf_daytrade=Decimal("0.00"),
        trades=[
            Trade(ticker="TEST3", direction="BUY", quantity=100, unit_price=Decimal("10.00")) # cost: 1001.50
        ]
    )
    
    # Process out of order to ensure the function sorts them
    portfolio = process_brokerage_notes([note1, note2])
    
    assert "TEST3" in portfolio
    pos = portfolio["TEST3"]
    assert pos.quantity == 200
    # Expected cost: (1001.50 + 1201.50) / 200 = 2203.00 / 200 = 11.0150
    assert pos.average_cost == Decimal("11.0150")

def test_process_notes_with_sells():
    note_buy = BrokerageNote(
        date=datetime.date(2023, 1, 5),
        number="101",
        broker="Test",
        settlement_fee=Decimal("1.00"),
        exchange_fee=Decimal("0.50"),
        brokerage_fee=Decimal("0.00"),
        iss_tax=Decimal("0.00"),
        irrf_daytrade=Decimal("0.00"),
        trades=[
            Trade(ticker="TEST3", direction="BUY", quantity=200, unit_price=Decimal("10.00")) # cost: 2001.50 => avg: 10.0075
        ]
    )
    
    note_sell = BrokerageNote(
        date=datetime.date(2023, 1, 10),
        number="102",
        broker="Test",
        settlement_fee=Decimal("1.00"),
        exchange_fee=Decimal("0.50"),
        brokerage_fee=Decimal("0.00"),
        iss_tax=Decimal("0.00"),
        irrf_daytrade=Decimal("0.00"),
        trades=[
            Trade(ticker="TEST3", direction="SELL", quantity=100, unit_price=Decimal("15.00")) 
        ]
    )
    
    portfolio = process_brokerage_notes([note_buy, note_sell])
    
    pos = portfolio["TEST3"]
    assert pos.quantity == 100
    # Sell does not change average cost
    assert pos.average_cost == Decimal("10.0075")

def test_process_notes_with_ticker_map():
    note = BrokerageNote(
        date=datetime.date(2023, 1, 5),
        broker="Test",
        trades=[
            Trade(ticker="OLDTICKER", direction="BUY", quantity=100, unit_price=Decimal("10.00"))
        ]
    )
    
    ticker_map = {"OLDTICKER": "NEWTICKER"}
    portfolio = process_brokerage_notes([note], ticker_map=ticker_map)
    
    assert "NEWTICKER" in portfolio
    assert "OLDTICKER" not in portfolio
    pos = portfolio["NEWTICKER"]
    assert pos.ticker == "NEWTICKER"
    assert pos.quantity == 100

