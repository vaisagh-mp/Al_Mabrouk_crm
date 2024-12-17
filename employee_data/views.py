from django.shortcuts import render, get_object_or_404
from django.contrib.auth.decorators import login_required
from Admin.models import Employee, Attendance

@login_required
def employee_dashboard(request):
    # Fetch the logged-in user's employee profile
    employee = get_object_or_404(Employee, user=request.user)
    
    # Fetch attendance records for the employee
    attendance_records = Attendance.objects.filter(employee=employee).order_by('-login_time')
    
    # Other related data can be added here (e.g., projects, salaries, etc.)
    context = {
        'employee': employee,
        'attendance_records': attendance_records,
    }
    return render(request, 'employee_dashboard.html', context)
