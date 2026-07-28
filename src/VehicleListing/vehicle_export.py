"""
Admin-only vehicle data export: xlsx/csv/json of every VehicleListing in
scope (filtered by a custom date range or a financial/calendar quarter
preset), joined with its Vehicle attributes and the owning dealer's details,
plus time-to-sell percentile stats computed across the SOLD listings in that
same scope (see export_utils.py for the quarter-math and stats functions).

Design note: date-range filtering is applied against VehicleListing.created_at
(when the row entered our system) — always populated, unlike first_listed_at
which is null until a listing is actually published. If "listed in this
quarter" was meant to key off first_listed_at instead, that's a one-line
change in _base_queryset below.
"""
import csv
import io
from datetime import datetime

from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAdminUser

from .export_utils import days_to_sell_percentiles, quarter_date_range
from .models import VehicleListing

# Column order + header labels shared by xlsx/csv/json so all three formats
# always agree on exactly which fields are exported.
COLUMNS = [
    ('listing_id', 'Listing ID'),
    ('vin', 'VIN'),
    ('make', 'Make'),
    ('model', 'Model'),
    ('variant', 'Variant'),
    ('year', 'Year'),
    ('mileage', 'Mileage'),
    ('transmission', 'Transmission'),
    ('fuel_type', 'Fuel Type'),
    ('body_type', 'Body Type'),
    ('color', 'Color'),
    ('price', 'Price'),
    ('description', 'Description'),
    ('status', 'Status'),
    ('lifecycle_status', 'Lifecycle Status'),
    ('listed_on', 'Listed On'),
    ('first_listed_at', 'First Listed At'),
    ('delisted_at', 'Delisted At'),
    ('days_to_sell', 'Days To Sell'),
    ('relist_count', 'Relist Count'),
    ('created_at', 'Created At'),
    # Dealer join — every row carries its own dealer's details.
    ('dealer_email', 'Dealer Email'),
    ('dealership_name', 'Dealership Name'),
    ('contact_person_name', 'Contact Person'),
    ('phone_number', 'Phone Number'),
    ('dealership_suburb', 'Dealership Suburb'),
    ('dealership_state', 'Dealership State'),
    # Time-to-sell analytics — the SAME 3 numbers repeated on every row: they
    # are aggregate stats over all SOLD listings in this export's scope, not
    # a per-row computation (a single listing doesn't have "a percentile").
    ('median_days_to_sell', 'Median Days To Sell (scope)'),
    ('p25_days_to_sell', '25th Percentile Days To Sell (scope)'),
    ('p75_days_to_sell', '75th Percentile Days To Sell (scope)'),
]


def _parse_date(value):
    if not value:
        return None
    try:
        return datetime.strptime(value, '%Y-%m-%d').date()
    except ValueError:
        return None


def _resolve_date_range(request):
    """
    Custom start_date/end_date wins if both are given and valid; otherwise a
    quarter+year preset; otherwise no date filter (whole history).
    Returns (start_date, end_date, error_message) — error_message is None on
    success, and start/end are both None when it's set.
    """
    start_raw = request.GET.get('start_date')
    end_raw = request.GET.get('end_date')
    if start_raw or end_raw:
        start = _parse_date(start_raw)
        end = _parse_date(end_raw)
        if not start or not end:
            return None, None, "start_date and end_date must both be valid YYYY-MM-DD dates"
        if start > end:
            return None, None, "start_date must be on or before end_date"
        return start, end, None

    quarter = request.GET.get('quarter')
    if quarter:
        year_raw = request.GET.get('year')
        if not year_raw:
            return None, None, "year is required when quarter is provided"
        try:
            year = int(year_raw)
        except ValueError:
            return None, None, "year must be an integer"
        year_type = (request.GET.get('year_type') or 'financial').strip().lower()
        try:
            start, end = quarter_date_range(quarter, year, year_type)
        except ValueError as exc:
            return None, None, str(exc)
        return start, end, None

    return None, None, None  # no filter — export everything


def _base_queryset(start, end):
    qs = VehicleListing.objects.select_related('vehicle', 'user').all()
    if start and end:
        # created_at is timezone-aware (USE_TZ=True); range is inclusive of
        # the whole end calendar day.
        start_dt = timezone.make_aware(datetime.combine(start, datetime.min.time()))
        end_dt = timezone.make_aware(datetime.combine(end, datetime.max.time()))
        qs = qs.filter(created_at__gte=start_dt, created_at__lte=end_dt)
    return qs.order_by('-created_at')


def _row_for_listing(listing, stats):
    vehicle = listing.vehicle
    user = listing.user
    return {
        'listing_id': listing.id,
        'vin': vehicle.vin,
        'make': vehicle.make,
        'model': vehicle.model,
        'variant': vehicle.variant,
        'year': vehicle.year,
        'mileage': vehicle.mileage,
        'transmission': vehicle.transmission,
        'fuel_type': vehicle.fuel_type,
        'body_type': vehicle.body_type,
        'color': vehicle.color,
        'price': listing.price,
        'description': listing.description,
        'status': listing.status,
        'lifecycle_status': listing.lifecycle_status,
        'listed_on': listing.listed_on.isoformat() if listing.listed_on else None,
        'first_listed_at': listing.first_listed_at.isoformat() if listing.first_listed_at else None,
        'delisted_at': listing.delisted_at.isoformat() if listing.delisted_at else None,
        'days_to_sell': listing.days_to_sell,
        'relist_count': listing.relist_count,
        'created_at': listing.created_at.isoformat() if listing.created_at else None,
        'dealer_email': user.email if user else None,
        'dealership_name': getattr(user, 'dealership_name', None),
        'contact_person_name': getattr(user, 'contact_person_name', None),
        'phone_number': getattr(user, 'phone_number', None),
        'dealership_suburb': getattr(user, 'dealership_suburb', None),
        'dealership_state': getattr(user, 'dealership_state', None),
        **stats,
    }


def _build_xlsx(rows):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = 'Vehicle Export'

    header_font = Font(bold=True)
    sheet.append([label for _, label in COLUMNS])
    for cell in sheet[1]:
        cell.font = header_font

    for row in rows:
        sheet.append([row.get(key) for key, _ in COLUMNS])

    for index, (key, label) in enumerate(COLUMNS, start=1):
        widest = max([len(label)] + [len(str(row.get(key) or '')) for row in rows]) if rows else len(label)
        sheet.column_dimensions[get_column_letter(index)].width = min(widest + 2, 50)

    buffer = io.BytesIO()
    workbook.save(buffer)
    buffer.seek(0)
    return buffer.getvalue()


def _build_csv(rows):
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([label for _, label in COLUMNS])
    for row in rows:
        writer.writerow([row.get(key) for key, _ in COLUMNS])
    return buffer.getvalue()


@api_view(['GET'])
@permission_classes([IsAdminUser])
def export_vehicle_data(request):
    """
    GET /api/vehicle-listing/export/

    Query params:
      export_format: 'xlsx' (default) | 'csv' | 'json' — json returns the row
                   data + stats as JSON instead of a file download; used by
                   the frontend to render a preview/charts before exporting.
                   (Not named `format` — DRF reserves that name; see below.)
      start_date, end_date: YYYY-MM-DD custom range (both required together;
                   takes priority over quarter if both are somehow sent).
      quarter:     'Q1'..'Q4' — used together with `year`.
      year:        integer, required when `quarter` is given. Means "the
                   year the financial year starts in" for year_type=financial,
                   or the plain calendar year for year_type=calendar.
      year_type:   'financial' (default, Australian FY: Q1=Jul-Sep) |
                   'calendar' (Q1=Jan-Mar).

    No date params at all -> exports the full history.
    """
    # NOTE: named `export_format`, not `format` — DRF reserves the `format`
    # query param for its own content-negotiation (?format=json/api), and
    # silently 404s any value it doesn't recognize as a configured renderer
    # (e.g. 'xlsx'/'csv') before the view body even runs. See DRF's
    # DefaultContentNegotiation.filter_renderers.
    export_format = (request.GET.get('export_format') or 'xlsx').strip().lower()
    if export_format not in ('xlsx', 'csv', 'json'):
        return JsonResponse({'error': "export_format must be 'xlsx', 'csv', or 'json'"}, status=400)

    start, end, error = _resolve_date_range(request)
    if error:
        return JsonResponse({'error': error}, status=400)

    listings = list(_base_queryset(start, end))

    sold_days = [
        listing.days_to_sell
        for listing in listings
        if listing.lifecycle_status == VehicleListing.LIFECYCLE_SOLD and listing.days_to_sell is not None
    ]
    stats = days_to_sell_percentiles(sold_days)

    rows = [_row_for_listing(listing, stats) for listing in listings]

    if export_format == 'json':
        return JsonResponse({
            'count': len(rows),
            'sold_count': len(sold_days),
            'start_date': start.isoformat() if start else None,
            'end_date': end.isoformat() if end else None,
            'stats': stats,
            'results': rows,
        }, status=200)

    filename_range = f"{start.isoformat()}_to_{end.isoformat()}" if start and end else "all"

    if export_format == 'csv':
        response = HttpResponse(_build_csv(rows), content_type='text/csv')
        response['Content-Disposition'] = f'attachment; filename="vehicle-export-{filename_range}.csv"'
        return response

    response = HttpResponse(
        _build_xlsx(rows),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )
    response['Content-Disposition'] = f'attachment; filename="vehicle-export-{filename_range}.xlsx"'
    return response
