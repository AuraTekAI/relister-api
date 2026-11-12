"""Stripe subscription helpers — keep Stripe API surface small and testable."""
import logging
from decimal import Decimal

import stripe

logger = logging.getLogger('relister_views')


def sync_overage_subscription_item(subscription, stripe_sub, plan):
    """
    Set subscription.stripe_overage_subscription_item_id from Stripe subscription items
    when the plan has stripe_overage_price_id.
    """
    if not plan or not getattr(plan, 'stripe_overage_price_id', None):
        return False

    target_price = plan.stripe_overage_price_id
    sid = None
    items_obj = stripe_sub['items'] if not isinstance(stripe_sub, dict) else stripe_sub.get('items', {})
    items_data = items_obj.data if hasattr(items_obj, 'data') else (items_obj.get('data', []) if isinstance(items_obj, dict) else [])
    for item in items_data or []:
        price_obj = item['price'] if not isinstance(item, dict) else item.get('price')
        if isinstance(price_obj, dict):
            price_id = price_obj.get('id')
        else:
            price_id = getattr(price_obj, 'id', price_obj)
        if price_id == target_price:
            sid = item['id'] if not isinstance(item, dict) else item.get('id')
            break

    sub_id = stripe_sub['id'] if not isinstance(stripe_sub, dict) else stripe_sub.get('id')
    if not sid:
        logger.warning(
            f"sync_overage_subscription_item: No subscription item for overage price "
            f"{target_price} on subscription {sub_id}."
        )
        return False

    if subscription.stripe_overage_subscription_item_id != sid:
        subscription.stripe_overage_subscription_item_id = sid
        subscription.save(update_fields=['stripe_overage_subscription_item_id', 'updated_at'])
    return True


def _get(obj, key, default=None):
    """Read `key` from a Stripe object or plain dict; None-safe."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _line_price_id(line):
    """
    The Price id behind an invoice line, on every Stripe API version:
      • pre-2025-03-31: `line.price` is the Price object (or id);
      • 2025-03-31.basil and later: `price` is gone — the id lives at
        `line.pricing.price_details.price`.
    """
    price = _get(line, 'price')
    if price is not None:
        if isinstance(price, str):
            return price
        pid = _get(price, 'id')
        if pid:
            return pid
    pricing = _get(line, 'pricing')
    details = _get(pricing, 'price_details')
    pid = _get(details, 'price')
    return pid if isinstance(pid, str) else _get(pid, 'id')


def _line_subscription_item_id(line):
    """
    The subscription item this line bills, on every API version:
    `line.subscription_item` (old) or
    `line.parent.subscription_item_details.subscription_item` (basil+).
    """
    sid = _get(line, 'subscription_item')
    if sid:
        return sid if isinstance(sid, str) else _get(sid, 'id')
    parent = _get(line, 'parent')
    details = _get(parent, 'subscription_item_details')
    sid = _get(details, 'subscription_item')
    return sid if isinstance(sid, str) else _get(sid, 'id')


def _line_amount(line):
    if isinstance(line, dict):
        return int(line.get('amount') or 0)
    return int(getattr(line, 'amount', 0) or 0)


def _line_quantity(line):
    if isinstance(line, dict):
        return line.get('quantity')
    return getattr(line, 'quantity', None)


def _invoice_lines(invoice):
    lines = getattr(invoice, 'lines', None) or (invoice.get('lines') if isinstance(invoice, dict) else None)
    if lines is None:
        return None
    return lines.data if hasattr(lines, 'data') else lines.get('data', []) if isinstance(lines, dict) else []


def _line_price(line, cache=None):
    """
    The full Price object behind an invoice line, on every API version.

    Old API versions embed it as `line.price`. From 2025-03-31.basil the line
    only carries the id (`pricing.price_details.price`), so we fetch the Price
    (memoised in `cache` per invoice — a plan has at most two prices). Returns
    None when the line has no price or the fetch fails; callers then fall back
    to the line-level `pricing` fields.
    """
    price = _get(line, 'price')
    if price is not None and not isinstance(price, str) and _get(price, 'id'):
        return price
    pid = _line_price_id(line)
    if not pid:
        return None
    if cache is not None and pid in cache:
        return cache[pid]
    fetched = None
    try:
        fetched = stripe.Price.retrieve(pid)
    except Exception as exc:  # stripe.error.StripeError, network, …
        logger.warning(f"_line_price: could not retrieve Stripe price '{pid}': {exc}")
    if cache is not None:
        cache[pid] = fetched
    return fetched


def _price_usage_type(price):
    """'metered' / 'licensed' for recurring prices, None for one-time lines."""
    recurring = _get(price, 'recurring')
    if not recurring:
        return None
    return _get(recurring, 'usage_type')


def _price_unit_amount(price, line=None):
    """
    Unit amount in cents: from the Price, else from the basil line-level
    `pricing.unit_amount_decimal` (so the rate is still read when the Price
    could not be fetched).
    """
    unit = _get(price, 'unit_amount')
    if unit is not None:
        return int(unit)
    pricing = _get(line, 'pricing')
    dec = _get(pricing, 'unit_amount_decimal')
    if dec is None:
        return None
    try:
        return int(Decimal(str(dec)).quantize(Decimal('1')))
    except (ValueError, ArithmeticError):
        return None


def _is_overage_line(line, price, plan, subscription=None):
    """
    Is this line the plan's extra-listings charge? True when it bills the
    plan's overage price, OR the subscription's recorded overage item (so an
    archived overage price, or a plan whose overage id is missing after a
    failed sync, is still recognised), OR — last resort — any metered price.
    """
    pid = _line_price_id(line)
    target = _get(plan, 'stripe_overage_price_id')
    if target and pid == target:
        return True
    sub_item = _get(subscription, 'stripe_overage_subscription_item_id')
    if sub_item and _line_subscription_item_id(line) == sub_item:
        return True
    return _price_usage_type(price) == 'metered'


def extract_base_charge_from_stripe_invoice(invoice, plan, subscription=None):
    """
    The base plan charge Stripe ACTUALLY billed: the sum of the recurring,
    non-metered subscription lines (the plan's monthly price — current or an
    archived one a subscriber is still on). Metered overage and one-time lines
    are excluded. Returns Decimal AUD or None when no such line exists.
    """
    data = _invoice_lines(invoice)
    if not data:
        return None
    cache = {}
    total_cents = 0
    found = False
    for line in data:
        price = _line_price(line, cache)
        if _is_overage_line(line, price, plan, subscription):
            continue
        if _price_usage_type(price) != 'licensed':
            continue
        total_cents += _line_amount(line)
        found = True
    if not found:
        return None
    return (Decimal(total_cents) / Decimal('100')).quantize(Decimal('0.01'))


def extract_overage_rate_from_stripe_invoice(invoice, plan, subscription=None):
    """
    The per-listing overage rate Stripe ACTUALLY used: unit_amount of the
    plan's overage line (see _is_overage_line). Returns Decimal AUD or None.
    """
    data = _invoice_lines(invoice)
    if not data:
        return None
    cache = {}
    for line in data:
        price = _line_price(line, cache)
        if not _is_overage_line(line, price, plan, subscription):
            continue
        unit = _price_unit_amount(price, line)
        if unit is None:
            continue
        return (Decimal(unit) / Decimal('100')).quantize(Decimal('0.01'))
    return None


def extract_metered_overage_from_stripe_invoice(invoice, plan, rate=None, subscription=None):
    """
    Sum the extra-listings line(s) of the invoice (see _is_overage_line — the
    plan's overage price, the subscription's overage item, or any metered line,
    so an archived price or a missing plan id never hides a real charge).
    `rate` (Decimal) overrides plan.overage_rate_aud when deriving the listing
    count — pass the rate Stripe actually charged.
    Returns (overage_listings_count, overage_charge_aud) or (None, None) if no matching lines.
    """
    if not plan:
        return None, None

    total_cents = 0
    line_qty = 0

    data = _invoice_lines(invoice)
    if data is None:
        return None, None

    cache = {}
    matched = [line for line in data if _is_overage_line(line, _line_price(line, cache), plan, subscription)]

    for line in matched:
        total_cents += _line_amount(line)
        q = _line_quantity(line)
        if q is not None:
            line_qty += int(q)

    if total_cents == 0:
        return None, None

    charge = (Decimal(total_cents) / Decimal('100')).quantize(Decimal('0.01'))
    rate = rate if rate is not None else (plan.overage_rate_aud or Decimal('0'))
    if rate and rate > 0:
        count = int((charge / rate).quantize(Decimal('1')))
    else:
        count = max(1, line_qty)
    return max(1, count), charge


def is_stripe_invoice_paid(stripe_invoice):
    """Return True if Stripe considers the invoice fully paid."""
    if isinstance(stripe_invoice, dict):
        if stripe_invoice.get('paid') is True:
            return True
        return stripe_invoice.get('status') == 'paid'
    if getattr(stripe_invoice, 'paid', None) is True:
        return True
    return getattr(stripe_invoice, 'status', None) == 'paid'
