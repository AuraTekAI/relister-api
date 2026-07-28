"""
Pure helper functions for the vehicle data export endpoint (see
vehicle_export.py): computing a quarter's date range under either the
Australian Financial Year or Calendar Year convention, and the time-to-sell
percentile stats (median / 25th / 75th) over a set of sold listings.

Kept dependency-free — uses only the stdlib `statistics` module (available
since Python 3.8) rather than adding numpy/pandas for three numbers.
"""
from datetime import date
from statistics import median, quantiles

# (start_month, start_year_offset, end_month, end_year_offset) — year_offset is
# added to the caller's `year` param, since a financial-year quarter can span
# into the next calendar year (e.g. FY Q3/Q4).
_AUSTRALIAN_FY_QUARTERS = {
    'Q1': (7, 0, 9, 0),
    'Q2': (10, 0, 12, 0),
    'Q3': (1, 1, 3, 1),
    'Q4': (4, 1, 6, 1),
}
_CALENDAR_YEAR_QUARTERS = {
    'Q1': (1, 0, 3, 0),
    'Q2': (4, 0, 6, 0),
    'Q3': (7, 0, 9, 0),
    'Q4': (10, 0, 12, 0),
}

# Last calendar day of each month (non-leap; Feb is corrected for leap years below).
_MONTH_LAST_DAY = {1: 31, 2: 28, 3: 31, 4: 30, 5: 31, 6: 30, 7: 31, 8: 31, 9: 30, 10: 31, 11: 30, 12: 31}


def _is_leap(year):
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def _last_day_of_month(year, month):
    if month == 2 and _is_leap(year):
        return 29
    return _MONTH_LAST_DAY[month]


def quarter_date_range(quarter, year, year_type='financial'):
    """
    Returns (start_date, end_date) as date objects (inclusive) for the given
    quarter.

    `year` means:
      - year_type='financial': the calendar year the Australian financial
        year STARTS in (e.g. year=2024 -> FY runs 1 Jul 2024 - 30 Jun 2025;
        Q1/Q2 fall in 2024, Q3/Q4 fall in 2025).
      - year_type='calendar': the plain calendar year (Q1-Q4 all fall in
        that same year).

    Raises ValueError for an unknown quarter/year_type.
    """
    quarter = (quarter or '').strip().upper()
    table = _AUSTRALIAN_FY_QUARTERS if year_type == 'financial' else _CALENDAR_YEAR_QUARTERS
    if year_type not in ('financial', 'calendar'):
        raise ValueError(f"Unknown year_type '{year_type}' — expected 'financial' or 'calendar'")
    if quarter not in table:
        raise ValueError(f"Unknown quarter '{quarter}' — expected one of Q1, Q2, Q3, Q4")

    start_month, start_offset, end_month, end_offset = table[quarter]
    start_year = year + start_offset
    end_year = year + end_offset
    start = date(start_year, start_month, 1)
    end = date(end_year, end_month, _last_day_of_month(end_year, end_month))
    return start, end


def median_p25_p75(values):
    """
    Generic median / 25th / 75th percentile over a list of numbers (ints or
    floats), used by both the days-to-sell export analytics and vehicle price
    estimation. Returns (median, p25, p75) — all None for an empty list (a
    median of nothing is undefined, not zero); all equal to the single value
    for a one-item list.
    """
    values = [v for v in values if v is not None]
    if not values:
        return None, None, None
    if len(values) == 1:
        return values[0], values[0], values[0]
    # statistics.quantiles(n=4) with the default 'exclusive' method needs at
    # least 2 data points; returns [Q1, Q2(median), Q3].
    q1, q2, q3 = quantiles(values, n=4)
    return q2, q1, q3


def days_to_sell_percentiles(days_to_sell_values):
    """
    Median / 25th / 75th percentile of a list of integer day counts.
    Returns a dict with None values (not zeros) when there's no data to
    compute from — a median of an empty set isn't "0 days", it's undefined.
    """
    median, p25, p75 = median_p25_p75(days_to_sell_values)
    if median is None:
        return {'median_days_to_sell': None, 'p25_days_to_sell': None, 'p75_days_to_sell': None}
    return {
        'median_days_to_sell': round(median, 1),
        'p25_days_to_sell': round(p25, 1),
        'p75_days_to_sell': round(p75, 1),
    }
