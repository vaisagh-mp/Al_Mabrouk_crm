from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from datetime import datetime
from django.contrib import messages
from Admin.models import Employee, Attendance, ProjectAssignment, Project

@login_required
def employee_dashboard(request):
    # Fetch the logged-in user's employee profile
    employee = get_object_or_404(Employee, user=request.user)
    
    # Fetch attendance records for the employee
    attendance_records = Attendance.objects.filter(employee=employee).order_by('-login_time')

    assigned_work = ProjectAssignment.objects.filter(employee=employee)

    context = {
        'employee': employee,
        'attendance_records': attendance_records,
        'assigned_work': assigned_work,
    }
    return render(request, 'employee/employee_dashboard.html', context)


@login_required
def submit_attendance_request(request):
    if not hasattr(request.user, 'employee_profile'):  # Ensure the user has an employee profile
        return redirect('dashboard')

    if request.method == 'POST':
        location = request.POST.get('location')
        attendance_status = request.POST.get('attendance_status')
        login_time = request.POST.get('login_time')
        log_out_time = request.POST.get('log_out_time')
        project_id = request.POST.get('project')

        employee = request.user.employee_profile

        # Calculate total hours if times are provided
        total_hours_of_work = 0
        if login_time and log_out_time:
            # Convert the string to datetime objects
            login_time_obj = datetime.strptime(login_time, "%Y-%m-%dT%H:%M")
            log_out_time_obj = datetime.strptime(log_out_time, "%Y-%m-%dT%H:%M")

            # Calculate the total hours of work
            delta = log_out_time_obj - login_time_obj
            total_hours_of_work = delta.total_seconds() / 3600  # Convert seconds to hours

        # Fetch the project instance if a project ID is provided
        project = None
        if project_id:
            try:
                project = Project.objects.get(id=project_id)
            except Project.DoesNotExist:
                messages.error(request, "Selected project does not exist.")
                return redirect('submit_attendance_request')

        # Create the attendance request
        Attendance.objects.create(
            employee=employee,
            login_time=login_time_obj,
            log_out_time=log_out_time_obj,
            total_hours_of_work=total_hours_of_work,
            location=location,
            attendance_status=attendance_status,
            project=project,  # Include the project field
            status='PENDING',
        )
        messages.success(request, "Attendance request submitted successfully!")
        return redirect('employee_dashboard')
        
        # Fetch projects assigned to the current employee
    assigned_projects = Project.objects.filter(employees=employee)  # Replace 'employees' with your actual field name

    # Add context for location, attendance status, and project choices
    context = {
        'location_choices': Attendance.LOCATION_CHOICES,
        'attendance_status_choices': Attendance.ATTENDANCE_STATUS,
        'projects': assigned_projects,  # Pass projects for the form dropdown
    }

    return render(request, 'employee/submit_attendance_request.html', context)


def update_project_status(request, project_id):
    if request.method == "POST":
        project = get_object_or_404(Project, id=project_id)
        new_status = request.POST.get("status")
        if new_status in dict(Project.STATUS_CHOICES):
            project.status = new_status
            project.save()
            messages.success(request, f"Status for project '{project.name}' updated to {project.get_status_display()}.")
        else:
            messages.error(request, "Invalid status selected.")
    return redirect("employee_dashboard")  # Adjust the redirect as needed