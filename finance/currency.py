from decimal import Decimal, ROUND_HALF_UP
import datetime

BASE_CURRENCY = 'AED'

CURRENCY_CHOICES = [
    ('AED', 'AED – UAE Dirham'),
    ('USD', 'USD – US Dollar'),
    ('INR', 'INR – Indian Rupee'),
    ('EUR', 'EUR – Euro'),
]

NON_BASE_CURRENCY_CHOICES = [
    (code, label) for code, label in CURRENCY_CHOICES if code != BASE_CURRENCY
]


def get_rate(currency, on_date=None) -> Decimal | None:
    """
    Return the exchange rate to AED for the given currency on on_date.
    AED -> Decimal('1').
    For other currencies, returns rate_to_aed from the latest active ExchangeRate
    with effective_from <= on_date; None if no active rate exists.
    """
    if not currency:
        return None

    currency = str(currency).strip().upper()
    if currency == BASE_CURRENCY:
        return Decimal('1')

    if on_date is None:
        from django.utils import timezone
        on_date = timezone.localdate()
    elif isinstance(on_date, datetime.datetime):
        on_date = on_date.date()

    from .models import ExchangeRate
    rate_obj = (
        ExchangeRate.objects.filter(
            currency=currency,
            is_active=True,
            effective_from__lte=on_date,
        )
        .order_by('-effective_from', '-created_at')
        .first()
    )
    if rate_obj is not None:
        return Decimal(str(rate_obj.rate_to_aed))
    return None


def to_aed(amount, rate) -> Decimal:
    """
    Convert an amount in foreign currency to AED using the given rate:
    (amount * rate).quantize(0.01, ROUND_HALF_UP).
    """
    if amount is None:
        dec_amount = Decimal('0')
    else:
        dec_amount = Decimal(str(amount))

    if rate is None:
        dec_rate = Decimal('0')
    else:
        dec_rate = Decimal(str(rate))

    return (dec_amount * dec_rate).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def format_money(amount, currency) -> str:
    """
    Format money with currency prefix, comma thousand separators, and 2 decimals.
    e.g. "USD 1,000.00"
    """
    cur = str(currency or BASE_CURRENCY).strip().upper()
    if amount is None:
        val = Decimal('0.00')
    else:
        try:
            val = Decimal(str(amount))
        except Exception:
            val = Decimal('0.00')
    return f"{cur} {val:,.2f}"

