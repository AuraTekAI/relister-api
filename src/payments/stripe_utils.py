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


def extract_metered_overage_from_stripe_invoice(invoice, plan):
    """
    Sum metered line amounts for plan.stripe_overage_price_id.
    Returns (overage_listings_count, overage_charge_aud) or (None, None) if no matching lines.
    """
    if not plan or not getattr(plan, 'stripe_overage_price_id', None):
        return None, None

    target = plan.stripe_overage_price_id
    total_cents = 0
    line_qty = 0

    lines = getattr(invoice, 'lines', None) or (invoice.get('lines') if isinstance(invoice, dict) else None)
    if lines is None:
        return None, None
    data = lines.data if hasattr(lines, 'data') else lines.get('data', []) if isinstance(lines, dict) else []

    for line in data:
        pid = _line_price_id(line)
        if pid != target:
            continue
        total_cents += _line_amount(line)
        q = _line_quantity(line)
        if q is not None:
            line_qty += int(q)

    if total_cents == 0:
        return None, None

    charge = (Decimal(total_cents) / Decimal('100')).quantize(Decimal('0.01'))
    rate = plan.overage_rate_aud or Decimal('0')
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
