from django import template

register = template.Library()

@register.filter
def format_hours(value):
    """Convert decimal hours to hours and minutes format."""
    try:
        value = float(value)
        hours = int(value)
        minutes = round((value - hours) * 60)
        return f"{hours}:{minutes} hrs"
    except (ValueError, TypeError):
        return value  # Return the original value if conversion fails
    

@register.filter
def subtract(value, arg):
    """Subtract arg from value."""
    try:
        return value - arg
    except (ValueError, TypeError):
        return 0