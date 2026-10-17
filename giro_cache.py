"""Persistent GIRO cache and provider-wide rate limiting across app sessions."""
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import fcntl
import json
import os
from pathlib import Path
import tempfile
import time

import requests

from space_weather import utc_time

CACHE = Path(__file__).resolve().parent / '.cache/giro'


class RateLimited(requests.RequestException):
    pass


def read_cache(name):
    try:
        value = json.loads((CACHE / f'{name}.json').read_text())
        return value if isinstance(value, dict) else None
    except (OSError, ValueError):
        return None


def write_cache(name, value):
    CACHE.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w', dir=CACHE, delete=False) as output:
        temporary = Path(output.name)
        json.dump(value, output)
    try:
        os.replace(temporary, CACHE / f'{name}.json')
    finally:
        temporary.unlink(missing_ok=True)


def fresh(saved, now, seconds):
    try:
        age = (now-utc_time(saved['retrieved_at'])).total_seconds()
        return 0 <= age < seconds
    except (TypeError, KeyError, ValueError):
        return False


def retry_deadline(header, now):
    try:
        seconds = int(header)
        deadline = now + timedelta(seconds=max(0, seconds))
    except (TypeError, ValueError, OverflowError):
        try:
            deadline = parsedate_to_datetime(header).astimezone(timezone.utc)
        except (TypeError, ValueError, OverflowError, AttributeError):
            deadline = now + timedelta(minutes=15)
    # Do not immediately retry even if the provider sends an expired/zero value.
    return max(deadline, now+timedelta(minutes=1))


def limited_message(deadline):
    return f'GIRO temporarily limited requests. Next refresh allowed after {deadline:%Y-%m-%d %H:%M UTC}. WSPR and the HF model are unaffected.'


def provider_get(url, **kwargs):
    """One attempt only, serialized, spaced, and stopped globally after HTTP 429."""
    CACHE.mkdir(parents=True, exist_ok=True)
    with (CACHE / 'provider.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        now = datetime.now(timezone.utc)
        state = read_cache('provider') or {}
        try:
            deadline = utc_time(state['blocked_until'])
        except (KeyError, ValueError, TypeError):
            deadline = now
        if deadline > now:
            raise RateLimited(limited_message(deadline))
        try:
            elapsed = (now-utc_time(state['last_request'])).total_seconds()
            if elapsed < 2:
                time.sleep(min(2, max(0, 2-elapsed)))
        except (KeyError, ValueError, TypeError):
            pass
        state['last_request'] = datetime.now(timezone.utc).isoformat()
        write_cache('provider', state)
        response = requests.get(url, **kwargs)
        if response.status_code == 429:
            deadline = retry_deadline(response.headers.get('Retry-After'), datetime.now(timezone.utc))
            state['blocked_until'] = deadline.isoformat()
            write_cache('provider', state)
            raise RateLimited(limited_message(deadline))
        response.raise_for_status()
        return response
