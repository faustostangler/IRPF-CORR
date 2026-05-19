from datetime import date
from decimal import Decimal

from irpf_corr.models import Trade, BrokerageNote, Position
from irpf_corr.calculator import apportion_fees, calculate_average_cost

def test_apportion_fees_single_trade():
    """Test that a single trade absorbs 100% of the operational costs."""
    trade = Trade(ticker="PETR4", quantity=100, unit_price=Decimal("38.50"), direction="BUY")
    note = BrokerageNote(
        date=date(2025, 1, 15),
        broker="Clear",
        trades=[trade],
        brokerage_fee=Decimal("15.00"),
    )
    
    trades_with_fees = apportion_fees(note)
    
    assert len(trades_with_fees) == 1
    assert trades_with_fees[0].allocated_fees == Decimal("15.00")

def test_apportion_fees_multiple_trades():
    """Test proportional fee distribution by financial volume."""
    trade1 = Trade(ticker="PETR4", quantity=100, unit_price=Decimal("38.50"), direction="BUY")
    trade2 = Trade(ticker="VALE3", quantity=50, unit_price=Decimal("62.00"), direction="BUY")
    
    note = BrokerageNote(
        date=date(2025, 1, 15),
        broker="Clear",
        trades=[trade1, trade2],
        brokerage_fee=Decimal("15.00"),
        exchange_fee=Decimal("3.29"),
        settlement_fee=Decimal("2.50")
    )
    
    trades_with_fees = apportion_fees(note)
    
    assert trades_with_fees[0].ticker == "PETR4"
    assert trades_with_fees[0].allocated_fees == Decimal("11.52")
    
    assert trades_with_fees[1].ticker == "VALE3"
    assert trades_with_fees[1].allocated_fees == Decimal("9.27")

def test_apportion_fees_zero_fees():
    """Test when there are no fees."""
    trade1 = Trade(ticker="PETR4", quantity=100, unit_price=Decimal("38.50"), direction="BUY")
    note = BrokerageNote(date=date(2025, 1, 15), broker="Clear", trades=[trade1])
    trades_with_fees = apportion_fees(note)
    assert trades_with_fees[0].allocated_fees == Decimal("0")

def test_average_cost_first_buy():
    position = Position(ticker="PETR4")
    trade = Trade(
        ticker="PETR4", 
        quantity=100, 
        unit_price=Decimal("38.50"), 
        direction="BUY", 
        allocated_fees=Decimal("11.52")
    )
    
    new_position = calculate_average_cost(position, trade)
    
    assert new_position.quantity == 100
    assert new_position.average_cost == Decimal("38.6152")

def test_average_cost_second_buy_different_price():
    position = Position(ticker="PETR4", quantity=100, average_cost=Decimal("38.6152"))
    trade = Trade(
        ticker="PETR4", 
        quantity=200, 
        unit_price=Decimal("40.00"), 
        direction="BUY", 
        allocated_fees=Decimal("12.50")
    )
    
    new_position = calculate_average_cost(position, trade)
    
    assert new_position.quantity == 300
    assert new_position.average_cost == Decimal("39.5801")

def test_average_cost_sell_does_not_change_avg():
    position = Position(ticker="PETR4", quantity=300, average_cost=Decimal("39.5801"))
    trade = Trade(
        ticker="PETR4", 
        quantity=150, 
        unit_price=Decimal("45.00"), 
        direction="SELL", 
        allocated_fees=Decimal("10.00")
    )
    
    new_position = calculate_average_cost(position, trade)
    
    assert new_position.quantity == 150
    assert new_position.average_cost == Decimal("39.5801")
