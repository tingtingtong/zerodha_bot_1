"""
Tests for EventCalendar — ex-dates, earnings, macro events.
"""
import pytest
import sys
from pathlib import Path
from datetime import date, timedelta
from unittest.mock import patch
import pytz

sys.path.insert(0, str(Path(__file__).parent.parent))

from research.event_calendar import EventCalendar, RBI_POLICY_DATES, BUDGET_DATES


@pytest.fixture
def cal():
    return EventCalendar()


# ── No events by default ──────────────────────────────────────────────────

def test_no_event_for_unknown_symbol(cal):
    assert not cal.has_event_soon("RELIANCE")


def test_no_macro_event_on_random_day(cal):
    assert not cal.is_macro_event_day(date(2024, 7, 15))


# ── Ex-date detection ─────────────────────────────────────────────────────

def test_registered_ex_date_detected(cal):
    today = date.today()
    cal.register_ex_date("RELIANCE", today + timedelta(days=1))
    assert cal.has_event_soon("RELIANCE")


def test_ex_date_outside_window_not_detected(cal):
    today = date.today()
    cal.register_ex_date("RELIANCE", today + timedelta(days=10))
    assert not cal.has_event_soon("RELIANCE", within_days=3)


def test_ex_date_on_boundary_detected(cal):
    today = date.today()
    cal.register_ex_date("INFY", today + timedelta(days=3))
    assert cal.has_event_soon("INFY", within_days=3)


# ── Earnings detection ────────────────────────────────────────────────────

def test_registered_earnings_detected(cal):
    today = date.today()
    cal.register_earnings("TCS", today + timedelta(days=2))
    assert cal.has_event_soon("TCS")


def test_earnings_outside_window_not_detected(cal):
    today = date.today()
    cal.register_earnings("TCS", today + timedelta(days=10))
    assert not cal.has_event_soon("TCS")


# ── Macro events ──────────────────────────────────────────────────────────

def test_rbi_policy_dates_are_macro_events(cal):
    for d in list(RBI_POLICY_DATES)[:3]:
        assert cal.is_macro_event_day(d)


def test_budget_date_is_macro_event(cal):
    for d in BUDGET_DATES:
        assert cal.is_macro_event_day(d)


def test_regular_date_not_macro(cal):
    assert not cal.is_macro_event_day(date(2024, 5, 20))


# ── Event symbols ─────────────────────────────────────────────────────────

def test_get_event_symbols_today(cal):
    today = date.today()
    cal.register_ex_date("HDFCBANK", today)
    cal.register_earnings("WIPRO", today + timedelta(days=1))
    symbols = cal.get_event_symbols_today()
    assert "HDFCBANK" in symbols
    assert "WIPRO" in symbols


def test_get_event_symbols_empty_when_no_events(cal):
    symbols = cal.get_event_symbols_today()
    assert len(symbols) == 0


def test_multiple_events_same_symbol(cal):
    today = date.today()
    cal.register_ex_date("RELIANCE", today)
    cal.register_earnings("RELIANCE", today + timedelta(days=1))
    assert cal.has_event_soon("RELIANCE")
    symbols = cal.get_event_symbols_today()
    assert "RELIANCE" in symbols
