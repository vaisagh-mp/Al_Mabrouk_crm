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
                return redirect('hr-dashboard')  # Adjust if HR has a separate dashboard
            elif employee.is_accountant:
                return redirect('fin_dashboard')
            else:
                return redirect('employee_dashboard')  
        except ObjectDoesNotExist:
            messages.error(request, "No employee profile found for this user.")
            return redirect('custom_logout')  # Handle missing profile case

    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')
        if username and password:
            from django.contrib.auth.models import User
            inactive_user = User.objects.filter(username=username).first()
            if inactive_user and not inactive_user.is_active and inactive_user.check_password(password):
                messages.error(request, "Your account has been deactivated. Please contact HR.")
                return render(request, 'Auth/login.html', {'form': AuthenticationForm(request)})

        form = AuthenticationForm(request, data=request.POST)
        if form.is_valid():
            username = form.cleaned_data['username']
            password = form.cleaned_data['password']
            user = authenticate(username=username, password=password)

            if user is not None:
                login(request, user)

                #Handle superuser separately
                if user.is_superuser:
                    return redirect('admin-dashboard')

                try:
                    employee = user.employee_profile  # Get Employee profile
                    
                    if employee.is_manager:
                        return redirect('manager-dashboard')
                    elif employee.is_administration:
                        return redirect('admstrn-dashboard')
                    elif employee.is_hr:
                        return redirect('hr-dashboard')  # Adjust as needed
                    elif employee.is_accountant:
                        return redirect('fin_dashboard')
                    else:
                        return redirect('employee_dashboard')
                except ObjectDoesNotExist:
                    messages.error(request, "No employee profile found for this user.")
                    return redirect('custom_logout')  # Handle missing profile case
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
