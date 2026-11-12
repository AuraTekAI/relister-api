"""Stripe subscription helpers — keep Stripe API surface small and testable."""
import logging
from decimal import Decimal

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


def _line_price_id(line):
    price = line.get('price') if isinstance(line, dict) else getattr(line, 'price', None)
    if price is None:
        return None
    if isinstance(price, dict):
        return price.get('id')
    return getattr(price, 'id', None)


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


def _line_price(line):
    return line.get('price') if isinstance(line, dict) else getattr(line, 'price', None)


def _price_usage_type(price):
    """'metered' / 'licensed' for recurring prices, None for one-time lines."""
    if price is None:
        return None
    recurring = price.get('recurring') if isinstance(price, dict) else getattr(price, 'recurring', None)
    if not recurring:
        return None
    return recurring.get('usage_type') if isinstance(recurring, dict) else getattr(recurring, 'usage_type', None)


def _price_unit_amount(price):
    if price is None:
        return None
    return price.get('unit_amount') if isinstance(price, dict) else getattr(price, 'unit_amount', None)


def extract_base_charge_from_stripe_invoice(invoice, plan):
    """
    The base plan charge Stripe ACTUALLY billed: the sum of the recurring,
    non-metered subscription lines (the plan's monthly price — current or an
    archived one a subscriber is still on). Metered overage and one-time lines
    are excluded. Returns Decimal AUD or None when no such line exists.
    """
    data = _invoice_lines(invoice)
    if not data:
        return None
    overage_id = getattr(plan, 'stripe_overage_price_id', None) if plan else None
    total_cents = 0
    found = False
    for line in data:
        price = _line_price(line)
        pid = _line_price_id(line)
        if pid and pid == overage_id:
            continue
        if _price_usage_type(price) != 'licensed':
            continue
        total_cents += _line_amount(line)
        found = True
    if not found:
        return None
    return (Decimal(total_cents) / Decimal('100')).quantize(Decimal('0.01'))


def extract_overage_rate_from_stripe_invoice(invoice, plan):
    """
    The per-listing overage rate Stripe ACTUALLY used: unit_amount of the
    plan's metered overage line (plan.stripe_overage_price_id), else of any
    metered line. Returns Decimal AUD or None.
    """
    data = _invoice_lines(invoice)
    if not data:
        return None
    target = getattr(plan, 'stripe_overage_price_id', None) if plan else None
    fallback = None
    for line in data:
        price = _line_price(line)
        unit = _price_unit_amount(price)
        if unit is None:
            continue
        if target and _line_price_id(line) == target:
            return (Decimal(int(unit)) / Decimal('100')).quantize(Decimal('0.01'))
        if fallback is None and _price_usage_type(price) == 'metered':
            fallback = (Decimal(int(unit)) / Decimal('100')).quantize(Decimal('0.01'))
    return fallback


def extract_metered_overage_from_stripe_invoice(invoice, plan, rate=None):
    """
    Sum metered line amounts for plan.stripe_overage_price_id (falling back to
    any metered line, so a subscriber still on an archived overage price is
    still read correctly). `rate` (Decimal) overrides plan.overage_rate_aud when
    deriving the listing count — pass the rate Stripe actually charged.
    Returns (overage_listings_count, overage_charge_aud) or (None, None) if no matching lines.
    """
    if not plan or not getattr(plan, 'stripe_overage_price_id', None):
        return None, None

    target = plan.stripe_overage_price_id
    total_cents = 0
    line_qty = 0

    data = _invoice_lines(invoice)
    if data is None:
        return None, None

    matched = [line for line in data if _line_price_id(line) == target]
    if not matched:
        matched = [line for line in data if _price_usage_type(_line_price(line)) == 'metered']

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
