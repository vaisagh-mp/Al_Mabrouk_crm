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
    
register = template.Library()

@register.filter
def format_work_duration(decimal_hours):
    """
    Converts a decimal hours value to a friendly time format.
    
    - If less than 1 hour: returns "MM:SS min"
    - If 1 hour or more: returns "HH:MM:SS hrs"
    """
    if decimal_hours is None:
        return "00:00"  # or "00:00 min" if you prefer

    try:
        # Convert decimal hours to total seconds
        total_seconds = int(float(decimal_hours) * 3600)
        
        if total_seconds < 3600:
            # Less than one hour: show minutes and seconds
            minutes = total_seconds // 60
            seconds = total_seconds % 60
            return f"{minutes:02d}:{seconds:02d} min"
        else:
            # One hour or more: show hours, minutes, and seconds
            hours = total_seconds // 3600
            minutes = (total_seconds % 3600) // 60
            seconds = total_seconds % 60
            return f"{hours:02d}:{minutes:02d}:{seconds:02d} hrs"
    except (ValueError, TypeError):
        return "00:00"
    

@register.filter
def absolute(value):
    try:
        return abs(float(value))
    except (ValueError, TypeError):
        return value
    
    