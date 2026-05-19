from datetime import date
from decimal import Decimal

from irpf_corr.models import Trade, BrokerageNote

def test_brokerage_note_total_fees():
    """Test that total_fees computes the sum of all deductible operational costs."""
    trade = Trade(ticker="PETR4", quantity=100, unit_price=Decimal("38.50"), direction="BUY")
    
    note = BrokerageNote(
        date=date(2025, 1, 15),
        broker="Clear",
        trades=[trade],
        brokerage_fee=Decimal("15.00"),
        settlement_fee=Decimal("2.50"),
        exchange_fee=Decimal("3.29"),
        iss_tax=Decimal("0.00"),
        # IRRF is informational for Day Trade, it doesn't increase the cost basis
        irrf_daytrade=Decimal("1.50"), 
        other_fees=Decimal("0.00"),
    )
    
    assert note.total_fees == Decimal("20.79")
