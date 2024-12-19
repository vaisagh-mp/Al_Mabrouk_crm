from django.shortcuts import redirect, render
from django.shortcuts import get_object_or_404
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.http import JsonResponse
from django.contrib import messages
from django.contrib.auth.forms import UserCreationForm
from .forms import EmployeeCreationForm, ProjectForm, ProjectAssignmentForm
from django.contrib.auth.views import LoginView
from django.core.paginator import Paginator
from .models import Attendance, Employee, ProjectAssignment, Project


#Home
@login_required
def dashboard(request):
    return render(request, 'Admin/dashboard.html')

#create employee
@login_required
def create_employee(request):
    if not request.user.is_staff:  # Restrict to admin or staff users
        return redirect('dashboard')  # Redirect to the admin homepage

    if request.method == 'POST':
        form = EmployeeCreationForm(request.POST)
        if form.is_valid():
            user = form.save(commit=False)
            user.set_password(form.cleaned_data['password'])
            user.save()
            messages.success(request, 'Employee created successfully!')
            return redirect('create-employee')
    else:
        form = EmployeeCreationForm()
    return render(request, 'Admin/create_employee.html', {'form': form})


#employee list
@login_required
def employee_list(request):
    if not request.user.is_staff:  # Restrict to admin or staff users
        return redirect('login')
    
    # Query employees with necessary fields (Name, Rank, Email)
    employees = Employee.objects.select_related('user')  # Fetch the related user model

    # Paginate the employees (10 per page)
    paginator = Paginator(employees, 10)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    return render(request, 'Admin/employee_list.html', {'page_obj': page_obj})


# edit employee
@login_required
def edit_employee(request, employee_id):
    if not request.user.is_staff:  # Restrict to admin or staff users
        return redirect('login')

    employee = get_object_or_404(Employee, id=employee_id)
    if request.method == 'POST':
        form = EmployeeCreationForm(request.POST, instance=employee)
        if form.is_valid():
            form.save()
            messages.success(request, 'Employee updated successfully!')
            return redirect('employee-list')  # Redirect to a list of employees or dashboard
    else:
        form = EmployeeCreationForm(instance=employee)

    return render(request, 'admin/edit_employee.html', {'form': form, 'employee': employee})

# delete employee
@login_required
def delete_employee(request, employee_id):
    if not request.user.is_staff:  # Restrict to admin or staff users
        return redirect('login')

    employee = get_object_or_404(Employee, id=employee_id)
    if request.method == 'POST':
        employee.delete()
        messages.success(request, 'Employee deleted successfully!')
        return redirect('employee-list')  # Redirect to a list of employees or dashboard

    return render(request, 'admin/confirm_delete_employee.html', {'employee': employee})


# attendance list
@login_required
def attendance_list_view(request):
    search_query = request.GET.get('search', '')
    attendance_records = Attendance.objects.select_related('employee')

    if search_query:
        attendance_records = attendance_records.filter(
            employee__user__username__icontains=search_query
        ).order_by('-login_time')

    paginator = Paginator(attendance_records, 10)  
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    context = {
        'attendance_records': page_obj,
        'search_query': search_query,
    }
    return render(request, 'Admin/attendance_list.html', context)

# project list view
@login_required
def project_list_view(request):
    # Query all projects
    projects = Project.objects.all()

    # Prepare project data
    project_data = [
        {
            "id": project.id,
            "name": project.name,
            "code": project.code,
            "status": project.status,
        }
        for project in projects
    ]

    # Pass the data to the template
    context = {
        "projects": project_data,
    }

    return render(request, 'Admin/project_list.html', context)


# project summary
@login_required
def project_summary_view(request, project_id):
    # Get the specific project by ID
    project = get_object_or_404(Project, id=project_id)

    # Engineers involved in the project
    assignments = ProjectAssignment.objects.filter(project=project)
    engineers = []
    engineer_salaries = {}
    total_engineer_salary = 0  # Initialize the total salary variable

    for assignment in assignments:
        engineer = assignment.employee
        engineers.append(engineer.user.username)
        engineer_salaries[engineer.user.username] = engineer.salary  # Assuming the salary is stored on the Employee model
        total_engineer_salary += engineer.salary  # Add salary to the total

    # Calculate total work hours from ProjectAssignment
    total_work_hours = sum(
        (assignment.time_stop - assignment.time_start).total_seconds() / 3600
        for assignment in assignments
    )

    # Calculate total project duration (Total Project Hours)
    if assignments.exists():
        project_start = min(assignment.time_start for assignment in assignments)
        project_end = max(assignment.time_stop for assignment in assignments)
        total_project_hours = (project_end - project_start).total_seconds() / 3600  # Total duration in hours
    else:
        total_project_hours = 0

    # Calculate total expenses (will call the method which includes hours worked)
    total_expenses = project.calculate_expenses()

    # Calculate profit
    profit = project.calculate_profit()

    # Prepare data for the project
    project_data = {
        "project_name": project.name,
        "code": project.code,
        "purchase_and_expenses": project.purchase_and_expenses,
        "invoice_amount": project.invoice_amount,
        "engineers": engineers,
        "engineer_salaries": engineer_salaries,  # Include engineer salaries
        "total_work_hours": round(total_work_hours, 2),  # Rounded to 2 decimal points
        "total_project_hours": round(total_project_hours, 2),  # Rounded to 2 decimal points
        "currency_code": project.currency_code,
        "total_expenses": round(total_expenses, 2),  # Rounded total expenses
        "profit": profit,
        "status": project.status,
        "total_engineer_salary": round(total_engineer_salary, 2),  # Total salary for all engineers
    }

    # Pass the data to the template
    context = {
        "project_data": project_data
    }

    return render(request, 'Admin/project.html', context)


# add project
@login_required
def add_project(request):
    if request.method == 'POST':
        form = ProjectForm(request.POST)
        if form.is_valid():
            form.save()
            return JsonResponse({'success': True})
        else:
            return JsonResponse({'success': False, 'errors': form.errors}, status=400)
    return render(request, 'Admin/project_list.html', {'form': form})

#project_assignment_list
@login_required
def project_assignment_list(request):
    assignments = ProjectAssignment.objects.all()

    # Calculate total time for each assignment
    for assignment in assignments:
        # Calculate time difference
        total_time = assignment.time_stop - assignment.time_start
        assignment.total_time = total_time

    return render(request, 'Admin/project_assignment_list.html', {'assignments': assignments})

#project_assignment_create
@login_required
def project_assignment_create(request):
    if request.method == 'POST':
        form = ProjectAssignmentForm(request.POST)
        if form.is_valid():
            form.save()
            return redirect('project-assignment-list')
    else:
        form = ProjectAssignmentForm()
    return render(request, 'project_assignment_form.html', {'form': form})

#project_assignment_update
@login_required
def project_assignment_update(request, pk):
    assignment = get_object_or_404(ProjectAssignment, pk=pk)
    if request.method == 'POST':
        form = ProjectAssignmentForm(request.POST, instance=assignment)
        if form.is_valid():
            form.save()
            return redirect('project-assignment-list')
    else:
        form = ProjectAssignmentForm(instance=assignment)
    return render(request, 'project_assignment_form.html', {'form': form})

#project_assignment_delete
@login_required
def project_assignment_delete(request, pk):
    assignment = get_object_or_404(ProjectAssignment, pk=pk)
    assignment.delete()
    return redirect('project-assignment-list')