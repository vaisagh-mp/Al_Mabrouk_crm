from django.contrib.auth.forms import AuthenticationForm
from django.core.exceptions import ObjectDoesNotExist
from django.contrib.auth import authenticate, login, logout
from django.shortcuts import render, redirect
from django.contrib import messages
from django.views.decorators.cache import never_cache

@never_cache
def custom_login(request):
    if request.user.is_authenticated:
        if request.user.is_superuser:
            return redirect('admin-dashboard')  # Redirect superusers to admin panel
        try:
            employee = request.user.employee_profile  # Get Employee profile
            
            if employee.is_manager:
                return redirect('manager-dashboard')
            elif employee.is_administration:
                return redirect('admstrn-dashboard')
            elif employee.is_hr:
                return redirect('hr_create_employee')  # Adjust if HR has a separate dashboard
            else:
                return redirect('employee_dashboard')  
        except ObjectDoesNotExist:
            messages.error(request, "No employee profile found for this user.")
            return redirect('custom-logout')  # Handle missing profile case

    if request.method == 'POST':
        form = AuthenticationForm(request, data=request.POST)
        if form.is_valid():
            username = form.cleaned_data['username']
            password = form.cleaned_data['password']
            user = authenticate(username=username, password=password)

            if user is not None:
                login(request, user)

                # ✅ Handle superuser separately
                if user.is_superuser:
                    return redirect('admin-dashboard')

                try:
                    employee = user.employee_profile  # Get Employee profile
                    
                    if employee.is_manager:
                        return redirect('manager-dashboard')
                    elif employee.is_administration:
                        return redirect('admstrn-dashboard')
                    elif employee.is_hr:
                        return redirect('hr_create_employee')  # Adjust as needed
                    else:
                        return redirect('employee_dashboard')
                except ObjectDoesNotExist:
                    messages.error(request, "No employee profile found for this user.")
                    return redirect('custom-logout')  # Handle missing profile case
            else:
                messages.error(request, "Invalid username or password.")  
        else:
            messages.error(request, "Invalid username or password.")  

    else:
        form = AuthenticationForm()

    return render(request, 'Auth/login.html', {'form': form})


@never_cache 
def custom_logout(request):
    logout(request)
    return redirect('custom-login')  # Redirect to login page
