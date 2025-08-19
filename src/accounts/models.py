from django.db import models
from decimal import Decimal
from django.contrib.auth.models import (
    BaseUserManager,
    AbstractBaseUser,
    PermissionsMixin,

)
from django.utils.translation import gettext_lazy as _
class UserManager(BaseUserManager):
    """
    Custom user model manager where email is the unique identifiers
    for authentication instead of usernames.
    """

    def create_user(self, email, password, **extra_fields):
        """
        Create and save a User with the given email, password and username.
        """
        if not email:
            raise ValueError("Users must have an email address!")

        email = self.normalize_email(email)

        user = self.model(
            email=email.strip().lower(),
            **extra_fields,
        )
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password, **extra_fields):
        """
        Create and save a SuperUser with the given email and password.
        """
        extra_fields.setdefault("is_superuser", True)

        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True.")

        user = self.create_user(
            email=self.normalize_email(email),
            password=password,
            **extra_fields,
        )
        user.is_active = True
        user.is_staff = True
        user.is_superuser = True
        user.save(using=self._db)
        return user
class User(AbstractBaseUser, PermissionsMixin):
    # Required fields
    email = models.EmailField(verbose_name="email", max_length=60, unique=True)
    dealership_name = models.CharField(max_length=255, null=True, blank=True)
    contact_person_name = models.CharField(max_length=255, null=True, blank=True)
    phone_number = models.CharField(max_length=15, null=True, blank=True)
    gumtree_dealarship_url = models.URLField(max_length=200, null=True, blank=True)
    facebook_dealership_url = models.URLField(max_length=200, null=True, blank=True)
    is_approved = models.BooleanField(default=False)
    last_images_check_status_time = models.DateTimeField(null=True, blank=True)
    daily_listing_count = models.IntegerField(default=0)
    
    # Extra information
    last_delete_listing_time = models.DateTimeField(null=True, blank=True)
    last_facebook_listing_time = models.DateTimeField(null=True, blank=True)
    last_login = models.DateTimeField(verbose_name="last login", auto_now=True)
    is_superuser = models.BooleanField(default=False)
    is_staff = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []  # Remove required fields since we'll handle validation in serializer
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    objects = UserManager()

    def __str__(self):
        return self.email
    
