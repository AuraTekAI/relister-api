from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from accounts.models import User



class UserAdmin(UserAdmin):
    ordering = ("email",)
    list_display = (
        "email",
        "last_login",
        "is_superuser",
    )
    search_fields = ("email",)
    # Define the fields that will be editable on the user change form in the admin
    fieldsets = (
        (None, {'fields': ('email', 'password')}),
        ('Permissions', {'fields': ('is_superuser','is_staff','is_active','groups','user_permissions')}),
        ('Important Dates', {'fields': ('last_login',)}),
    )
    readonly_fields = ("last_login",)
    list_filter = ( "is_superuser", "groups")

    add_fieldsets = (
        (None, {"classes": ("wide",), "fields": ("email",)}),
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
