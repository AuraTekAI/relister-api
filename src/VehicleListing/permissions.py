from rest_framework.permissions import BasePermission

from .models import ExtensionSyncStatus

# The extension matches on this code to switch on its existing verification
# banner and halt bulk/auto runs, instead of treating the 403 as a generic error.
FB_VERIFICATION_REQUIRED_CODE = 'fb_verification_required'


class HasNoFacebookVerificationBlock(BasePermission):
    """
    Blocks the request while the dealer's Facebook account is flagged as needing
    verification — ExtensionSyncStatus.status == 'verification_required', the same
    real-time state facebook_verification_status sets/clears and the admin
    "verification blocked" list reads. No row, or any other status, is allowed.

    Applied only to the pre-publish images-status call, never to the post-publish
    bookkeeping endpoints: a successful publish is itself proof the account is
    verified (the extension reports 'cleared' asynchronously right after), so
    gating listed-on/facebook-id would reject the record of a real publish.
    """
    message = {
        'detail': (
            'Your Facebook account needs to be verified before publishing. Open Facebook, '
            'complete the verification (e.g. confirm your mobile number), then click '
            '"I\'ve verified — retry".'
        ),
        'code': FB_VERIFICATION_REQUIRED_CODE,
    }

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False
        if user.is_superuser:
            return True
        return not ExtensionSyncStatus.objects.filter(
            user=user, status='verification_required'
        ).exists()
