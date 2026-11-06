from django import template
from finance.currency import format_money

register = template.Library()


@register.filter(name='money')
def money(value, currency='AED'):
    """
    Format amount with currency: {{ value|money:currency }}
    e.g. {{ 1000|money:'USD' }} -> "USD 1,000.00"
    """
    return format_money(value, currency)

