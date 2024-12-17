from django.shortcuts import redirect, render
from django.shortcuts import get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.contrib.auth.forms import UserCreationForm
from .forms import EmployeeCreationForm  # Your custom form
from django.contrib.auth.views import LoginView
from django.core.paginator import Paginator
from .models import Attendance, Employee



def dashboard(request):
    return render(request, 'Admin/dashboard.html')

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


def employee_list(request):
    if not request.user.is_staff:  # Restrict to admin or staff users
        return redirect('login')
    employees = Employee.objects.all()
    paginator = Paginator(employees, 10)  # Paginate employees (10 per page)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    return render(request, 'Admin/employee_list.html', {'page_obj': page_obj})


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
