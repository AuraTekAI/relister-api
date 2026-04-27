import logging

from django import forms
from django.contrib import admin, messages
from django.db import DatabaseError

from .models import ZipFile
from .s3 import s3_delete, s3_key, s3_upload

logger = logging.getLogger(__name__)


class ZipFileAdminForm(forms.ModelForm):
    file = forms.FileField(
        required=False,
        help_text=(
            "Upload a .zip file named &lt;name&gt;_v&lt;number&gt;.zip "
            "(e.g. chrome-extension_v2.zip). "
            "Required when adding. Leave blank on edit to keep the existing file."
        ),
    )

    class Meta:
        model = ZipFile
        fields = []

    def clean_file(self):
        uploaded = self.cleaned_data.get('file')
        if not uploaded:
            return uploaded
        if not uploaded.name.endswith('.zip'):
            raise forms.ValidationError("Invalid file type. Only .zip files are accepted.")
        try:
            ZipFile.parse_filename(uploaded.name)
        except ValueError as exc:
            raise forms.ValidationError(str(exc))
        return uploaded

    def clean(self):
        cleaned = super().clean()
        uploaded = cleaned.get('file')
        is_add = self.instance.pk is None

        if is_add and not uploaded:
            self.add_error('file', "A ZIP file is required when adding a new record.")
            return cleaned

        if not uploaded:
            return cleaned

        try:
            base_name, version = ZipFile.parse_filename(uploaded.name)
        except ValueError:
            return cleaned

        # On edit: block version downgrade against the current record
        if not is_add and version <= self.instance.version:
            raise forms.ValidationError(
                f"Update rejected. Current version is v{self.instance.version}. "
                f"Please upload a newer version (v{self.instance.version + 1} or above)."
            )

        # Block conflict with a different record that has the same base_name
        qs = ZipFile.objects.filter(base_name=base_name)
        if not is_add:
            qs = qs.exclude(pk=self.instance.pk)
        existing = qs.first()
        if existing and version <= existing.version:
            raise forms.ValidationError(
                f"Update rejected. Existing version is v{existing.version}. "
                f"Please upload a newer version (v{existing.version + 1} or above)."
            )

        return cleaned


@admin.register(ZipFile)
class ZipFileAdmin(admin.ModelAdmin):
    form = ZipFileAdminForm
    list_display = ['filename', 'base_name', 'version', 's3_key', 'uploaded_at', 'updated_at']
    list_filter = ['base_name']
    search_fields = ['filename', 'base_name']
    readonly_fields = ['filename', 'base_name', 'version', 's3_key', 'uploaded_at', 'updated_at']
    ordering = ['-uploaded_at']

    def get_fields(self, request, obj=None):
        return ['file', 'filename', 'base_name', 'version', 's3_key', 'uploaded_at', 'updated_at']

    def save_model(self, request, obj, form, change):
        uploaded = form.cleaned_data.get('file')

        if not uploaded:
            # Edit with no new file — nothing to update
            return

        filename = uploaded.name
        try:
            base_name, version = ZipFile.parse_filename(filename)
        except ValueError as exc:
            self.message_user(request, str(exc), level=messages.ERROR)
            return

        key = s3_key(filename)
        uploaded.seek(0)
        ok, err = s3_upload(uploaded, key)
        if not ok:
            self.message_user(request, err, level=messages.ERROR)
            return

        old_key = obj.s3_key if change else None

        obj.filename = filename
        obj.base_name = base_name
        obj.version = version
        obj.s3_key = key

        try:
            obj.save()
        except DatabaseError as exc:
            logger.error("Admin DB save failed after S3 upload, rolling back key=%s: %s", key, exc)
            s3_delete(key)
            self.message_user(
                request,
                "Database error while saving. The S3 upload has been rolled back.",
                level=messages.ERROR,
            )
            return

        # Delete old S3 object after successful DB save (non-fatal)
        if old_key and old_key != key:
            ok, err = s3_delete(old_key)
            if not ok:
                logger.warning(
                    "Old S3 object not deleted (key=%s). New file is live. Manual cleanup may be needed.",
                    old_key,
                )
                self.message_user(
                    request,
                    f"File updated successfully, but the old S3 object ({old_key}) could not be deleted. Manual cleanup may be needed.",
                    level=messages.WARNING,
                )

    def delete_model(self, request, obj):
        key = obj.s3_key
        try:
            obj.delete()
        except DatabaseError as exc:
            logger.error("Admin DB delete failed for ZipFile pk=%s: %s", obj.pk, exc)
            self.message_user(
                request,
                "Database error while deleting. Please try again.",
                level=messages.ERROR,
            )
            return

        ok, err = s3_delete(key)
        if not ok:
            logger.warning("S3 object not deleted (key=%s) after admin delete. Manual cleanup needed.", key)
            self.message_user(
                request,
                f"Record deleted but the S3 file ({key}) could not be removed. Please clean it up manually.",
                level=messages.WARNING,
            )

    def delete_queryset(self, request, queryset):
        """Handle bulk delete from the changelist."""
        for obj in queryset:
            self.delete_model(request, obj)
