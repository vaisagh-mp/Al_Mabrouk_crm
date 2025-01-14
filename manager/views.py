from django.shortcuts import redirect, render
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from Admin.forms import ProjectAssignmentForm
from django.contrib import messages
from Admin.models import Attendance

# Create your views here.


#Home
def dashboard(request):
    if request.user.is_superuser:  # For admin users
        return render(request, 'Admin/dashboard.html')
    elif request.user.is_staff:  # For staff/manager users
        return render(request, 'Manager/dashboard.html')
    else: 
        return render(request, 'employee/employee_dashboard.html')
    

@login_required
def manage_attendance_requests(request):
    if not request.user.is_staff:  # Ensure the user is a manager
        return redirect('dashboard')

    pending_requests = Attendance.objects.filter(status='PENDING')

    if request.method == 'POST':
        attendance_id = request.POST.get('attendance_id')
        action = request.POST.get('action')  # APPROVE or REJECT
        rejection_reason = request.POST.get('rejection_reason', '')  # Get the rejection reason if provided

        if not action:  # Handle case where 'action' is None or missing
            messages.error(request, "Action (Approve/Reject) must be selected!")
            return redirect('manage_attendance_requests')

        try:
            attendance = Attendance.objects.get(id=attendance_id)
            if action == 'APPROVE':
                attendance.status = 'APPROVED'
                attendance.attendance_status = attendance.attendance_status  # Mark as present by default
            elif action == 'REJECT':
                attendance.status = 'REJECTED'
                attendance.attendance_status = 'LEAVE'
                attendance.rejection_reason = rejection_reason  # Store the rejection reason
                attendance.rejected_by = request.user
            attendance.save()
            messages.success(request, f"Attendance {action.lower()}ed successfully!")
        except Attendance.DoesNotExist:
            messages.error(request, "Attendance record not found!")

    return render(request, 'Manager/manage_attendance_requests.html', {'requests': pending_requests})


@login_required
def project_assignment_create(request):
    if request.method == 'POST':
        form = ProjectAssignmentForm(request.POST)
        if form.is_valid():
            form.save()
            return redirect('manager-dashboard')
    else:
        form = ProjectAssignmentForm()
    return render(request, 'Manager/project_assignment_form.html', {'form': form})

