from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from accounts.models import User
from VehicleListing.tasks import profile_listings_for_approved_users
from VehicleListing.utils import send_user_approval_email


class UserAdmin(UserAdmin):
    ordering = ("email",)
    list_display = (
        "email",
        "dealership_name",
        "contact_person_name",
        "last_login",
        "is_superuser",
        "is_approved",
        "is_active",
        "daily_listing_count",
        "last_facebook_listing_time",
        'last_delete_listing_time',
        "last_images_check_status_time",
    )
    search_fields = ("email", "dealership_name", "contact_person_name", "is_approved")
    # Define the fields that will be editable on the user change form in the admin
    fieldsets = (
        (None, {'fields': ('email', 'password', 'is_approved', 'daily_listing_count', 'last_facebook_listing_time', 'last_delete_listing_time','last_images_check_status_time')}),
        ('Personal Info', {'fields': ['dealership_name', 'contact_person_name', 'phone_number', 'gumtree_dealarship_url', 'facebook_dealership_url']}),
        ('Permissions', {'fields': ('is_superuser', 'is_staff', 'is_active', 'groups', 'user_permissions')}),
        ('Important Dates', {'fields': ('last_login',)}),
    )
    readonly_fields = ("last_login",)
    list_filter = ("is_superuser", "groups")

    add_fieldsets = (
        (None, {"classes": ("wide",), "fields": ("email", "dealership_name", "contact_person_name", "phone_number", "gumtree_dealarship_url", "facebook_dealership_url", "is_approved",)}),
        ("Security", {"fields": ("password1", "password2")}),
        (
            "Permissions",
            {
                "fields": (
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "groups",
                    "user_permissions",
                )
            },
        ),
    )
    exclude = ('username',)

    def save_model(self, request, obj, form, change):
        """
        Override save_model to trigger Celery task and send email when user is approved.
        """
        # Check if this is an update (not a new user)
        if change:
            # Get the original user from database
            original_user = User.objects.get(pk=obj.pk)
            was_approved = original_user.is_approved
            
            # Save the user first
            super().save_model(request, obj, form, change)
            
            # Check if is_approved changed from False to True
            if not was_approved and obj.is_approved:
                # Trigger the Celery task
                profile_listings_for_approved_users.delay(obj.id)
                
                # Send approval email
                email_sent = send_user_approval_email(obj)
                
                # Add success messages
                self.message_user(
                    request,
                    f"User '{obj.email}' has been approved. Profile listings task has been triggered.",
                    level='SUCCESS'
                )
                
                if email_sent:
                    self.message_user(
                        request,
                        f"Approval email sent to '{obj.email}'.",
                        level='SUCCESS'
                    )
                else:
                    self.message_user(
                        request,
                        f"Warning: Failed to send approval email to '{obj.email}'. Please check logs.",
                        level='WARNING'
                    )
        else:
            # For new users, just save normally
            super().save_model(request, obj, form, change)
            
            # If new user is created as approved, also trigger the task and send email
            if obj.is_approved:
                profile_listings_for_approved_users.delay(obj.id)
                email_sent = send_user_approval_email(obj)
                
                self.message_user(
                    request,
                    f"User '{obj.email}' created as approved. Profile listings task has been triggered.",
                    level='SUCCESS'
                )
                
                if email_sent:
                    self.message_user(
                        request,
                        f"Approval email sent to '{obj.email}'.",
                        level='SUCCESS'
                    )


admin.site.register(User, UserAdmin)
