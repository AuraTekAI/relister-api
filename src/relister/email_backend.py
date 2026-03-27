"""
Custom Django email backend that sends messages via the Flashpost HTTP API.
Set EMAIL_BACKEND = 'relister.email_backend.FlashpostEmailBackend' in settings.py.
"""
import re
import logging
import requests
from django.core.mail.backends.base import BaseEmailBackend
from django.conf import settings

logger = logging.getLogger('relister_views')


class FlashpostEmailBackend(BaseEmailBackend):
    ENDPOINT = '/v1/send'
    REQUEST_TIMEOUT = 15  # seconds

    def __init__(self, fail_silently=False, **kwargs):
        super().__init__(fail_silently=fail_silently, **kwargs)
        self.api_url = getattr(settings, 'FLASHPOST_API_URL', '').rstrip('/')
        self.api_key = getattr(settings, 'FLASHPOST_API_KEY', '')
        self._session = None

    def open(self):
        if self._session is None:
            self._session = requests.Session()
            self._session.headers.update({
                'Authorization': f'Bearer {self.api_key}',
                'Content-Type': 'application/json',
            })

    def close(self):
        if self._session is not None:
            try:
                self._session.close()
            finally:
                self._session = None

    def send_messages(self, email_messages):
        if not email_messages:
            return 0
        new_session = self._session is None
        if new_session:
            self.open()
        num_sent = 0
        try:
            for message in email_messages:
                if self._send_one(message):
                    num_sent += 1
        finally:
            if new_session:
                self.close()
        return num_sent

    def _send_one(self, email_message):
        try:
            payload = self._build_payload(email_message)
        except Exception as exc:
            self._handle_error(f'Failed to build payload for "{email_message.subject}": {exc}', exc)
            return False
        try:
            response = self._session.post(
                self.api_url + self.ENDPOINT,
                json=payload,
                timeout=self.REQUEST_TIMEOUT,
            )
            response.raise_for_status()
            logger.info(f'FlashpostEmailBackend: Sent "{email_message.subject}" to {email_message.to}')
            return True
        except requests.exceptions.HTTPError as exc:
            self._handle_error(
                f'HTTP {exc.response.status_code} from Flashpost for "{email_message.subject}": {exc.response.text[:500]}',
                exc,
            )
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as exc:
            self._handle_error(f'Network error sending "{email_message.subject}": {exc}', exc)
        except Exception as exc:
            self._handle_error(f'Unexpected error for "{email_message.subject}": {exc}', exc)
        return False

    def _handle_error(self, message, exc):
        logger.error(f'FlashpostEmailBackend: {message}', exc_info=True)
        if not self.fail_silently:
            raise exc

    def _build_payload(self, email_message):
        is_html = getattr(email_message, 'content_subtype', 'plain') == 'html'
        body = email_message.body or ''
        payload = {
            'from': email_message.from_email,
            'to': list(email_message.to),
            'subject': email_message.subject,
        }
        if is_html:
            payload['html_body'] = body
            payload['text_body'] = _strip_html(body)
        else:
            payload['text_body'] = body
        if email_message.cc:
            payload['cc'] = list(email_message.cc)
        if email_message.bcc:
            payload['bcc'] = list(email_message.bcc)
        if not payload['to']:
            raise ValueError('EmailMessage has no recipients.')
        return payload


def _strip_html(html: str) -> str:
    text = re.sub(r'<[^>]+>', ' ', html)
    text = re.sub(r'[ \t]+', ' ', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()
