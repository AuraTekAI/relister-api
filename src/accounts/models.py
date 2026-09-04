import uuid
from datetime import timedelta
from django.db import models
from django.contrib.auth.models import (
    BaseUserManager,
    AbstractBaseUser,
    PermissionsMixin,
)
from django.utils import timezone
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

    ACCOUNT_STATUS_CHOICES = [
        ('trial', 'Trial'),
        ('active', 'Active'),
        ('past_due', 'Past Due'),
        ('trial_expired', 'Trial Expired'),
        ('suspended', 'Suspended'),
    ]

    # Required fields
    email = models.EmailField(verbose_name="email", max_length=60, unique=True)
    first_name = models.CharField(max_length=150, null=True, blank=True)
    last_name = models.CharField(max_length=150, null=True, blank=True)
    dealership_name = models.CharField(max_length=255, null=True, blank=True)
    contact_person_name = models.CharField(max_length=255, null=True, blank=True)
    phone_number = models.CharField(max_length=15, null=True, blank=True)
    gumtree_dealarship_url = models.URLField(max_length=200, null=True, blank=True)
    facebook_dealership_url = models.URLField(max_length=200, null=True, blank=True)
    # Facebook dealership account(s) this user has logged into the extension
    # with, e.g. ["facebook_account_2", "facebook_account_1"]. Recorded at
    # extension login (the extension reports which FB account the browser is
    # signed into); each account is stored exactly once, most-recently-seen
    # first, so the latest profile is always at the top of the list. A repeat
    # login with an already-stored account moves it back to the top instead of
    # adding a second copy.
    # Existing users simply start with [] — see add_dealer_facebook_profile().
    dealer_facebook_profile = models.JSONField(default=list, blank=True)
    custom_domain_url = models.URLField(max_length=200, null=True, blank=True)
    dealership_license_number = models.CharField(max_length=100, null=True, blank=True)
    dealership_license_phone = models.CharField(max_length=20, null=True, blank=True)
    # Auto-discovered from the dealer's custom_domain_url at signup / profile-edit
    # time (or refreshed by the periodic custom-domain scrape). Used to populate
    # `location` on custom-domain listing responses when the source page exposes
    # no per-listing location, removing the need for the extension to prompt
    # the dealer at publish time. Gumtree-sourced listings ignore these fields —
    # their location comes from `adLocationData` on each ad.
    dealership_suburb = models.CharField(max_length=100, null=True, blank=True)
    # Best-effort full formatted address (street + suburb + state + postcode)
    # from the same auto-discovery pass, kept as a single free-text line since
    # that's what a future Google Maps geocoding lookup would take as input.
    # Not all sources expose a street-level address (e.g. hardcoded adapter
    # defaults only know suburb/state), so this stays null more often than
    # dealership_suburb/dealership_state.
    dealership_address = models.CharField(max_length=255, null=True, blank=True)
    AU_STATE_CHOICES = [
        ('WA', 'Western Australia'),
        ('NSW', 'New South Wales'),
        ('VIC', 'Victoria'),
        ('QLD', 'Queensland'),
        ('SA', 'South Australia'),
        ('TAS', 'Tasmania'),
        ('ACT', 'Australian Capital Territory'),
        ('NT', 'Northern Territory'),
    ]
    dealership_state = models.CharField(
        max_length=3,
        choices=AU_STATE_CHOICES,
        null=True,
        blank=True,
    )
    is_approved = models.BooleanField(default=False)

    # Trial / subscription status
    account_status = models.CharField(
        max_length=20,
        choices=ACCOUNT_STATUS_CHOICES,
        default='trial',
    )
    trial_start_date = models.DateTimeField(null=True, blank=True)
    trial_end_date = models.DateTimeField(null=True, blank=True)
    # Permanently marks that this email address has consumed its free trial.
    # Once True it can never be reset — ensures one trial per email address.
    trial_used = models.BooleanField(default=False)

    last_images_check_status_time = models.DateTimeField(null=True, blank=True)
    daily_listing_count = models.IntegerField(default=0)
    listing_count = models.IntegerField(default=0)      # total listings posted in current subscription period
    relist_cycles = models.IntegerField(default=0)      # total relist cycles in current subscription period
    overage_count = models.IntegerField(default=0)      # listings beyond plan quota in current period

    # Extra information
    last_delete_listing_time = models.DateTimeField(null=True, blank=True)
    last_facebook_listing_time = models.DateTimeField(null=True, blank=True)
    last_login = models.DateTimeField(verbose_name="last login", auto_now=True)
    is_superuser = models.BooleanField(default=False)
    is_staff = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    objects = UserManager()

    def __str__(self):
        return self.email

    def add_dealer_facebook_profile(self, accounts):
        """Record Facebook dealership account(s) on dealer_facebook_profile.

        The list is kept most-recently-seen first: whatever comes in goes to
        the top. Accepts a single value or a list (a list is taken as already
        newest-first and keeps the order it was given). Blank values are
        ignored and an account already stored is never inserted twice — it is
        moved back to the top instead, keeping the other accounts in their
        existing relative order. Works for existing users whose field is still
        NULL/[] — the list is created on first use. Saves only when the stored
        list actually changes (a new account, or a reordering); returns True in
        that case, False otherwise.
        """
        if not accounts:
            return False
        if not isinstance(accounts, (list, tuple)):
            accounts = [accounts]
        incoming = []
        for account in accounts:
            account = str(account).strip()
            if account and account not in incoming:
                incoming.append(account)
        if not incoming:
            return False
        current = list(self.dealer_facebook_profile or [])
        # Newest first, then everything already stored that wasn't just seen.
        updated = incoming + [a for a in current if a not in incoming]
        if updated == current:
            return False
        self.dealer_facebook_profile = updated
        self.save(update_fields=['dealer_facebook_profile'])
        return True


class NotificationPreference(models.Model):
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name='notification_preferences',
    )
    email_approaching_limit = models.BooleanField(default=True)
    email_quota_exceeded = models.BooleanField(default=True)
    email_billing_reminder = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Preferences — {self.user.email}"


class EmailVerificationToken(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='email_verification_tokens')
    token = models.CharField(max_length=64, unique=True, default=uuid.uuid4)
    new_email = models.EmailField()
    is_used = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()

    def save(self, *args, **kwargs):
        if not self.pk:
            self.expires_at = timezone.now() + timedelta(hours=24)
        super().save(*args, **kwargs)

    def is_valid(self):
        return not self.is_used and timezone.now() < self.expires_at

    def __str__(self):
        return f"EmailVerification — {self.user.email} → {self.new_email}"
