from dataclasses import dataclass
from enum import Enum


class Segment(Enum):
    EQUITY_INTRADAY = "equity_intraday"
    EQUITY_DELIVERY = "equity_delivery"
    FO_FUTURES = "fo_futures"
    FO_OPTIONS = "fo_options"


@dataclass
class ChargeBreakdown:
    brokerage: float
    stt: float
    exchange_txn: float
    sebi_charge: float
    gst: float
    stamp_duty: float
    total: float

    def as_dict(self) -> dict:
        return {k: round(v, 4) for k, v in self.__dict__.items()}


def calculate_charges(
    buy_value: float,
    sell_value: float,
    segment: Segment = Segment.EQUITY_INTRADAY,
) -> ChargeBreakdown:
    turnover = buy_value + sell_value

    if segment == Segment.EQUITY_INTRADAY:
        brokerage = min(20.0, buy_value * 0.0003) + min(20.0, sell_value * 0.0003)
        stt = sell_value * 0.00025
        exchange_txn = turnover * 0.0000345
        stamp_duty = buy_value * 0.00003
    elif segment == Segment.EQUITY_DELIVERY:
        brokerage = 0.0
        stt = turnover * 0.001
        exchange_txn = turnover * 0.0000345
        stamp_duty = buy_value * 0.00015
    else:
        brokerage = min(20.0, turnover * 0.0003)
        stt = sell_value * 0.0001
        exchange_txn = turnover * 0.00002
        stamp_duty = buy_value * 0.00003

    sebi_charge = turnover * 0.000001
    gst = brokerage * 0.18
    total = brokerage + stt + exchange_txn + sebi_charge + gst + stamp_duty

    return ChargeBreakdown(
        brokerage=brokerage, stt=stt, exchange_txn=exchange_txn,
        sebi_charge=sebi_charge, gst=gst, stamp_duty=stamp_duty, total=total,
    )


def estimate_round_trip_charges(
    entry_price: float,
    exit_price: float,
    qty: int,
    segment: Segment = Segment.EQUITY_INTRADAY,
) -> float:
    return calculate_charges(entry_price * qty, exit_price * qty, segment).total


def charge_pct_of_trade(
    entry_price: float, qty: int, segment: Segment = Segment.EQUITY_INTRADAY
) -> float:
    tv = entry_price * qty
    c = estimate_round_trip_charges(entry_price, entry_price, qty, segment)
    return (c / tv) * 100 if tv > 0 else 0
