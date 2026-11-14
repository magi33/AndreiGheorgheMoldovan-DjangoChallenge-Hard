import json
import urllib.request
import urllib.error
from urllib.parse import urlsplit
from django.conf import settings


class RemoteError(Exception):
    pass


def fetch_json(path):
    parts = urlsplit(path)
    if parts.scheme or parts.netloc or '..' in parts.path.split('/'):
        raise ValueError('Only challenge-host paths are allowed.')
    url = settings.CHALLENGE_HOST.rstrip('/') + '/' + path.lstrip('/')
    request = urllib.request.Request(url, headers={'Accept': 'application/json', 'User-Agent': 'CodeRun-DjangoChallenge/1.0'})
    try:
        with urllib.request.urlopen(request, timeout=settings.CHALLENGE_TIMEOUT) as response:
            data = json.load(response)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        raise RemoteError(str(exc)) from exc
    if not isinstance(data, dict) or data.get('status', 200) not in (200, 'ok', 'OK'):
        raise RemoteError('Unexpected challenge response: ' + str(data)[:300])
    return data
