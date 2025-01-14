from django.db import models
from django.contrib.auth.models import User

class Manager(models.Model):
    user = models.OneToOneField(
        User, on_delete=models.CASCADE, related_name='manager_profile')
    department = models.CharField(max_length=255)
    hire_date = models.DateField()

    def save(self, *args, **kwargs):
        """
        Override save to ensure the user is marked as staff.
        """
        self.user.is_staff = True
        self.user.save()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.user.username} - {self.department}"

