from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth import authenticate, login, logout
from django.shortcuts import render, redirect
from django.contrib import messages
from django.views.decorators.cache import never_cache

@never_cache 
def custom_login(request):
    if request.user.is_authenticated:
        if request.user.is_superuser:
            return redirect('admin-dashboard')  # Admin redirection
        elif request.user.is_staff:
            return redirect('manager-dashboard')
        else:
            return redirect('employee_dashboard')  # Ensure correct redirection

    if request.method == 'POST':
        form = AuthenticationForm(request, data=request.POST)
        if form.is_valid():
            username = form.cleaned_data['username']
            password = form.cleaned_data['password']
            user = authenticate(username=username, password=password)

            if user is not None:
                login(request, user)
                if user.is_superuser:
                    return redirect('admin-dashboard')
                elif user.is_staff:
                    return redirect('manager-dashboard')
                else:
                    return redirect('employee_dashboard')
            else:
                messages.error(request, "Invalid username or password.")  # Error message
        else:
            messages.error(request, "Invalid username or password.")  # Error message

    else:
        form = AuthenticationForm()

    return render(request, 'Auth/login.html', {'form': form})

@never_cache 
def custom_logout(request):
    logout(request)
    return redirect('custom-login')  # Redirect to login page
