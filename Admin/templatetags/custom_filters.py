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
def format_hours_minutes(value):
    """
    Converts decimal hours to a human-readable format:
    e.g. 16.75 -> '16 hrs 45 mins'
         1.25 -> '1 hr 15 mins'
         1.0  -> '1 hr'
         0.5  -> '30 mins'
         0    -> '0 hrs'
    """
    if value is None:
        return "0 hrs"
    try:
        total_seconds = int(round(float(value) * 3600))
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60

        parts = []
        if hours > 0:
            parts.append(f"{hours} {'hr' if hours == 1 else 'hrs'}")
        if minutes > 0:
            parts.append(f"{minutes} {'min' if minutes == 1 else 'mins'}")

        return " ".join(parts) if parts else "0 hrs"
    except (ValueError, TypeError):
        return value
    

@register.filter
def subtract(value, arg):
    """Subtract arg from value."""
    try:
        return value - arg
    except (ValueError, TypeError):
        return 0


@register.filter
def format_work_duration(decimal_hours):
    """
    Converts a decimal hours value to a friendly format:
    - Less than 1 hour: "MM:SS min"
    - 1 hour or more: "HH:MM hrs"
    """
    if decimal_hours is None:
        return "00:00"

    try:
        # Convert decimal hours to total seconds
        total_seconds = int(float(decimal_hours) * 3600)

        if total_seconds < 3600:
            minutes = total_seconds // 60
            seconds = total_seconds % 60
            return f"{minutes:02d}:{seconds:02d} min"
        else:
            hours = total_seconds // 3600
            minutes = (total_seconds % 3600) // 60
            return f"{hours:02d}:{minutes:02d} hrs"
    except (ValueError, TypeError):
        return "00:00"
    

@register.filter
def absolute(value):
    try:
        return abs(float(value))
    except (ValueError, TypeError):
        return value
    
@register.filter
def abs_value(value):
    try:
        return abs(value)
    except:
        return value    