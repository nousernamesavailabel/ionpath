"""Timestamped NOAA context. No empirical SNR correction or success probability."""
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re

import requests

FEEDS = {
    'kp': 'https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json',
    'solar_observed': 'https://services.swpc.noaa.gov/json/solar-cycle/observed-solar-cycle-indices.json',
    'solar_predicted': 'https://services.swpc.noaa.gov/json/solar-cycle/predicted-solar-cycle.json',
    'drap': 'https://services.swpc.noaa.gov/text/drap_global_frequencies.txt',
}
CACHE = Path(__file__).resolve().parent / '.cache/noaa'


def utc_time(value):
    date = datetime.fromisoformat(value.replace('Z', '+00:00'))
    return date.replace(tzinfo=timezone.utc) if date.tzinfo is None else date.astimezone(timezone.utc)


def latest_kp(records):
    # NOAA changed from a header/row array to objects in 2026; support both.
    if records and isinstance(records[0], list):
        records = [dict(zip(records[0], row)) for row in records[1:]]
    valid = []
    for row in records:
        if not isinstance(row, dict):
            continue
        try:
            value = float(row.get('Kp', row.get('kp')))
            if math.isfinite(value) and 0 <= value <= 9:
                valid.append({'time': utc_time(row['time_tag']), 'value': value})
        except (ValueError, TypeError, KeyError):
            continue
    if not valid:
        raise ValueError('No valid Kp observations.')
    return max(valid, key=lambda row: row['time'])


def solar_for_month(observed, predicted, month):
    for records, key, source in ((observed, 'smoothed_ssn', 'NOAA observed smoothed SSN'),
                                  (predicted, 'predicted_ssn', 'NOAA predicted SSN')):
        for row in records:
            if isinstance(row, dict) and row.get('time-tag') == month:
                try:
                    value = float(row.get(key, -1))
                except (ValueError, TypeError):
                    continue
                if math.isfinite(value) and 1 <= value <= 311:
                    return {'value': int(round(value)), 'month': month, 'source': source}
    return None  # Never substitute a different month or a daily sunspot count.


def parse_drap(text):
    timestamp = re.search(r'Product Valid At\s*:\s*(\d{4}-\d\d-\d\d \d\d:\d\d) UTC', text)
    if not timestamp:
        raise ValueError('D-RAP has no recognized valid time.')
    rows, longitudes = [], None
    for line in text.splitlines():
        if line.startswith('#') or not line.strip() or set(line.strip()) == {'-'}:
            continue
        if '|' not in line and longitudes is None:
            longitudes = [float(x) for x in line.split()]
        elif '|' in line:
            lat, values = line.split('|', 1)
            values = [float(x) for x in values.split()]
            if longitudes is None or len(values) != len(longitudes):
                raise ValueError('Malformed D-RAP grid.')
            if any(not math.isfinite(x) or x < 0 for x in values):
                raise ValueError('Invalid D-RAP frequency.')
            rows.append((float(lat), values))
    if not rows or not longitudes:
        raise ValueError('D-RAP grid is empty.')
    if any(not math.isfinite(lon) or not -180 <= lon <= 180 for lon in longitudes) or any(not math.isfinite(lat) or not -90 <= lat <= 90 for lat, _ in rows):
        raise ValueError('Invalid D-RAP coordinates.')
    return {'time': utc_time(timestamp.group(1)), 'longitudes': longitudes, 'rows': rows}


def path_points(a, b, count=41):
    def vector(point):
        lat, lon = map(math.radians, point)
        return (math.cos(lat)*math.cos(lon), math.cos(lat)*math.sin(lon), math.sin(lat))
    va, vb = vector(a), vector(b)
    angle = math.acos(max(-1, min(1, sum(x*y for x, y in zip(va, vb)))))
    if abs(math.pi - angle) < 1e-6:
        raise ValueError('Antipodal path has no unique short great circle.')
    points = []
    for i in range(count):
        fraction = i / (count - 1)
        if angle < 1e-8:
            points.append(a)
            continue
        v = [(math.sin((1-fraction)*angle)*x + math.sin(fraction*angle)*y) / math.sin(angle) for x, y in zip(va, vb)]
        points.append((math.degrees(math.atan2(v[2], math.hypot(v[0], v[1]))), math.degrees(math.atan2(v[1], v[0]))))
    return points


def path_absorption_context(grid, a, b):
    values = []
    for lat, lon in path_points(a, b):
        _, row = min(grid['rows'], key=lambda item: abs(item[0]-lat))
        column = min(range(len(grid['longitudes'])), key=lambda i: abs((grid['longitudes'][i]-lon+180) % 360-180))
        values.append(row[column])
    return max(values)


def load_feed(name, refresh=False, now=None):
    """A failed refresh returns last good data with an explicit error and original retrieval time."""
    now = now or datetime.now(timezone.utc)
    path = CACHE / (name + '.json')
    saved = None
    try:
        saved = json.loads(path.read_text())
        if not refresh and 0 <= (now - utc_time(saved['retrieved_at'])).total_seconds() < 600:
            return saved
    except (OSError, ValueError, KeyError, TypeError):
        saved = None
    try:
        response = requests.get(FEEDS[name], timeout=(5, 12))
        response.raise_for_status()
        payload = response.text if name == 'drap' else response.json()
        if name == 'kp':
            latest_kp(payload)
        elif name == 'drap':
            parse_drap(payload)
        elif not isinstance(payload, list) or not payload or not isinstance(payload[0], dict):
            raise ValueError('Invalid NOAA solar-cycle data.')
        result = {'payload': payload, 'retrieved_at': now.isoformat(), 'url': FEEDS[name]}
        CACHE.mkdir(parents=True, exist_ok=True)
        import tempfile, os
        with tempfile.NamedTemporaryFile(mode='w', dir=CACHE, delete=False) as output:
            temporary = Path(output.name)
            json.dump(result, output)
        try:
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
        return result
    except (requests.RequestException, OSError, ValueError, TypeError) as exc:
        result = dict(saved) if saved else {'payload': None, 'url': FEEDS[name], 'retrieved_at': None}
        result['error'] = f'{type(exc).__name__}: {exc}'
        return result
