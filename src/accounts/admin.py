from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from accounts.models import User


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
    )
    search_fields = ("email", "dealership_name", "contact_person_name", "is_approved")
    # Define the fields that will be editable on the user change form in the admin
    fieldsets = (
        (None, {'fields': ('email', 'password', 'is_approved')}),
        ('Personal Info', {'fields': ['dealership_name', 'contact_person_name', 'phone_number', 'gumtree_dealarship_url', 'facebook_dealership_url']}),
        ('Permissions', {'fields': ('is_superuser', 'is_staff', 'is_active', 'groups', 'user_permissions')}),
        ('Important Dates', {'fields': ('last_login',)}),
    )
    readonly_fields = ("last_login",)
    list_filter = ("is_superuser", "groups")

    add_fieldsets = (
        (None, {"classes": ("wide",), "fields": ("email", "dealership_name", "contact_person_name", "phone_number", "gumtree_dealarship_url", "facebook_dealership_url", "is_approved")}),
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


admin.site.register(User, UserAdmin)
