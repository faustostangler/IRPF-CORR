from datetime import date
from decimal import Decimal
from typing import Literal
from pydantic import BaseModel, computed_field

class Trade(BaseModel):
    """A single trade within a brokerage note."""
    ticker: str
    quantity: int
    unit_price: Decimal
    direction: Literal["BUY", "SELL"]
    allocated_fees: Decimal = Decimal("0")

class BrokerageNote(BaseModel):
    """A complete brokerage note from a single trading day."""
    date: date
    broker: str
    trades: list[Trade]
    brokerage_fee: Decimal = Decimal("0")
    settlement_fee: Decimal = Decimal("0")
    exchange_fee: Decimal = Decimal("0")
    iss_tax: Decimal = Decimal("0")
    irrf_daytrade: Decimal = Decimal("0")
    other_fees: Decimal = Decimal("0")

    @computed_field
    @property
    def total_fees(self) -> Decimal:
        """Sum of all deductible operational costs."""
        return (self.brokerage_fee + self.settlement_fee
                + self.exchange_fee + self.iss_tax + self.other_fees)

class Position(BaseModel):
    """Current holding position for a single ticker."""
    ticker: str
    quantity: int = 0
    average_cost: Decimal = Decimal("0")
    total_invested: Decimal = Decimal("0")
