from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth import authenticate, login, logout
from django.shortcuts import render, redirect
from django.http import HttpResponse

def custom_login(request):
    if request.user.is_authenticated:
        if request.user.is_superuser:
            return redirect('admin-dashboard')  # Admin redirection
        elif request.user.is_staff:  # For staff/manager users
            return redirect('manager-dashboard')
        else:
            return redirect('employee_dashboard')  # Ensure 'employee-dashboard' is correct

    if request.method == 'POST':
        form = AuthenticationForm(request, data=request.POST)
        if form.is_valid():
            username = form.cleaned_data['username']
            password = form.cleaned_data['password']
            user = authenticate(username=username, password=password)
            if user is not None:
                login(request, user)
                if user.is_superuser:
                    return redirect('admin-dashboard')  # Admin redirection
                elif user.is_staff:  # For staff/manager users
                    return redirect('manager-dashboard')
                else:
                    return redirect('employee_dashboard')  # Ensure 'employee-dashboard' is correct
            else:
                return HttpResponse('Invalid login credentials', status=401)
        else:
            return HttpResponse('Invalid login form', status=400)
    else:
        form = AuthenticationForm()

    return render(request, 'Auth/login.html', {'form': form})


def custom_logout(request):
    logout(request)
    return redirect('custom-login')  # Redirect to login page
