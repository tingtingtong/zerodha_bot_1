import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.charge_calculator import calculate_charges, Segment, estimate_round_trip_charges


def test_intraday_charges_positive():
    charges = calculate_charges(10000, 10200, Segment.EQUITY_INTRADAY)
    assert charges.total > 0
    assert charges.brokerage > 0
    assert charges.stt > 0


def test_delivery_zero_brokerage():
    charges = calculate_charges(10000, 10200, Segment.EQUITY_DELIVERY)
    assert charges.brokerage == 0
    assert charges.stt > 0


def test_charges_scale_with_size():
    small = calculate_charges(5000, 5100, Segment.EQUITY_INTRADAY)
    large = calculate_charges(50000, 51000, Segment.EQUITY_INTRADAY)
    assert large.total > small.total


def test_round_trip_estimate():
    charges = estimate_round_trip_charges(2800, 2850, qty=5)
    assert 5 < charges < 200  # ~₹15 for 5 shares @ 2800 intraday
