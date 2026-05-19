from copy import deepcopy
from decimal import Decimal, ROUND_HALF_UP
from typing import List

from irpf_corr.models import BrokerageNote, Trade, Position

def apportion_fees(note: BrokerageNote) -> List[Trade]:
    """
    Apportion the total deductible fees of a brokerage note
    across its trades, proportional to their financial volume.
    Returns a list of Trade objects with their 'allocated_fees' set.
    """
    total_fees = note.total_fees
    
    if total_fees == Decimal("0"):
        result_trades = []
        for trade in note.trades:
            new_trade = deepcopy(trade)
            new_trade.unit_price = new_trade.unit_price * note.exchange_rate
            result_trades.append(new_trade)
        return result_trades
        
    total_volume = sum(trade.quantity * trade.unit_price for trade in note.trades)
    
    result_trades = []
    for trade in note.trades:
        trade_volume = trade.quantity * trade.unit_price
        
        # Proportion = (trade volume / total volume) * total fees
        allocated_fee = (trade_volume / total_volume) * total_fees
        
        # Rounding to 2 decimal places using standard financial rounding
        allocated_fee = allocated_fee.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        
        new_trade = deepcopy(trade)
        # Convert to BRL using the exchange rate (defaults to 1 for BRL)
        new_trade.unit_price = new_trade.unit_price * note.exchange_rate
        new_trade.allocated_fees = (allocated_fee * note.exchange_rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        result_trades.append(new_trade)
        
    return result_trades

def calculate_average_cost(position: Position, trade: Trade) -> Position:
    """
    Calculates the new average cost and position quantity after a trade.
    """
    new_position = deepcopy(position)
    
    if trade.direction == "BUY":
        current_invested = Decimal(str(new_position.quantity)) * new_position.average_cost
        trade_cost = (Decimal(str(trade.quantity)) * trade.unit_price) + trade.allocated_fees
        
        new_position.quantity += trade.quantity
        new_position.total_invested = current_invested + trade_cost
        
        if new_position.quantity > 0:
            avg_cost = new_position.total_invested / Decimal(str(new_position.quantity))
            new_position.average_cost = avg_cost.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
            
    elif trade.direction == "SELL":
        new_position.quantity -= trade.quantity
        new_position.total_invested = Decimal(str(new_position.quantity)) * new_position.average_cost
        # Average cost does not change on SELL
        
    return new_position
