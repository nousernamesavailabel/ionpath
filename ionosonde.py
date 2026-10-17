"""Bounded GIRO FastChar context; measurements never modify modeled SNR."""
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
import math
import re

import requests

from space_weather import path_points, utc_time
from giro_cache import fresh, provider_get, read_cache, write_cache

STATION_URL = 'https://lgdc.uml.edu/common/DIDBFastStationList'
DATA_URL = 'https://lgdc.uml.edu/fastchar/getbest'
RULES_URL = 'https://giro.uml.edu/didbase/RulesOfTheRoad.html'


class StationTable(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows, self.row, self.cell = [], [], None

    def handle_starttag(self, tag, attrs):
        if tag == 'tr':
            if self.row:
                self.rows.append(self.row)
            self.row = []
        elif tag == 'td':
            self.cell = []

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag):
        if tag == 'td' and self.cell is not None:
            self.row.append(''.join(self.cell).strip())
            self.cell = None
        elif tag == 'tr':
            if self.row:
                self.rows.append(self.row)
            self.row = []


def parse_stations(html):
    parser = StationTable()
    parser.feed(html)
    rows = parser.rows + ([parser.row] if parser.row else [])
    stations = {}
    for row in rows:
        if len(row) != 5 or not re.fullmatch(r'[A-Z0-9_]{5}', row[1]):
            continue
        try:
            lat, lon = float(row[3]), float(row[4])
            if not math.isfinite(lat) or not math.isfinite(lon) or not -90 <= lat <= 90 or not -180 <= lon <= 360:
                continue
            stations[row[1]] = {'code': row[1], 'name': row[2], 'lat': lat, 'lon': (lon+180)%360-180}
        except ValueError:
            continue
    if not stations:
        raise ValueError('GIRO returned no recognized station coordinates.')
    return list(stations.values())


def distance(a, b):
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = math.sin((lat2-lat1)/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin((lon2-lon1)/2)**2
    return 12742 * math.asin(math.sqrt(max(0, min(1, h))))


def select_stations(stations, a, b):
    """Nearest sites to endpoints and path-thirds, including aliases; at most eight."""
    points = path_points(a, b, count=61)
    selected = {}
    for anchor in (a, b, points[20], points[40]):
        sites = set()
        for station in sorted(stations, key=lambda station: distance(anchor, (station['lat'], station['lon']))):
            site = (station['lat'], station['lon'])
            if site in sites:
                continue
            sites.add(site)
            selected[station['code']] = station
            if len(sites) == 2:
                break
    # A site may have both retired and current instrument codes. Try its aliases
    # within the same request bound rather than assuming the first code is active.
    sites = {(station['lat'], station['lon']) for station in selected.values()}
    for station in stations:
        if len(selected) >= 8:
            break
        if (station['lat'], station['lon']) in sites:
            selected[station['code']] = station
    selected = dict(list(selected.items())[:8])
    result = []
    for station in selected.values():
        location = station['lat'], station['lon']
        result.append({**station, 'distance_a_km': distance(a, location), 'distance_b_km': distance(b, location),
                       'path_distance_km': min(distance(point, location) for point in points)})
    return sorted(result, key=lambda station: station['path_distance_km'])


def parse_measurements(text, expected_code):
    if not re.search(r'URSI-Code\s+' + re.escape(expected_code) + r'\b', text):
        raise ValueError('GIRO measurement response has no matching station identity.')
    if '# STATUS: ERROR (No measurement data could be found for requested time)' in text:
        return []
    header = next((line for line in text.splitlines() if re.match(r'#\s*Time\s+CS\s+foF2\s+QD\s+hmF2\s+QD\s*$', line)), None)
    if header is None:
        raise ValueError('Unrecognized GIRO foF2 / hmF2 column layout.')
    results = []
    for line in text.splitlines():
        if not line.strip() or line.startswith('#'):
            continue
        tokens = line.replace('|', ' ').split()
        if len(tokens) != 6:
            raise ValueError('Malformed GIRO measurement row.')
        instant = utc_time(tokens[0])
        confidence = int(tokens[1])
        if confidence not in (-1, 999) and not 0 <= confidence <= 100:
            raise ValueError('Invalid autoscaling confidence score.')
        def number(token, maximum):
            try:
                value = float(token)
            except ValueError:
                return None
            return value if math.isfinite(value) and 0 < value <= maximum else None
        results.append({'time': instant, 'confidence': confidence, 'fof2_mhz': number(tokens[2], 30),
                        'fof2_qualifier': tokens[3], 'hmf2_km': number(tokens[4], 1000), 'hmf2_qualifier': tokens[5]})
    return sorted(results, key=lambda row: row['time'])


def station_catalogue(now):
    saved = read_cache('catalogue')
    if fresh(saved, now, 7*86400):
        return saved['stations'], None, saved['retrieved_at']
    try:
        response = provider_get(STATION_URL, timeout=(5, 20))
        stations = parse_stations(response.text)
        stamp = datetime.now(timezone.utc).isoformat()
        write_cache('catalogue', {'stations': stations, 'retrieved_at': stamp})
        return stations, None, stamp
    except (requests.RequestException, ValueError) as exc:
        if saved and saved.get('stations'):
            return saved['stations'], f'{exc} Using the cached station catalogue from {saved["retrieved_at"]}.', saved['retrieved_at']
        raise


def fetch_station(station, start, end):
    key = 'station_' + station['code']
    saved = read_cache(key)
    def unpack(saved, warning=None):
        rows = [{**row, 'time': utc_time(row['time'])} for row in saved['rows']]
        return {**station, 'rows': rows, 'url': saved['url'], 'error': None,
                'retrieved_at': saved['retrieved_at'], 'cache_warning': warning}
    # Reuse station measurements across paths and restarts. Empty windows are
    # cached longer so retired/inactive instruments are not queried every run.
    if fresh(saved, end, 600 if saved and saved.get('rows') else 1800):
        return unpack(saved)
    params = {'ursiCode': station['code'], 'charName': 'foF2,hmF2',
              'fromDate': start.strftime('%Y/%m/%d %H:%M:%S'), 'toDate': end.strftime('%Y/%m/%d %H:%M:%S')}
    try:
        response = provider_get(DATA_URL, params=params, timeout=(5, 20))
        rows = parse_measurements(response.text, station['code'])
        rows = [row for row in rows if start <= row['time'] <= end]
        saved = {'rows': [{**row, 'time': row['time'].isoformat()} for row in rows],
                 'url': response.url, 'retrieved_at': datetime.now(timezone.utc).isoformat()}
        write_cache(key, saved)
        return unpack(saved)
    except (requests.RequestException, ValueError) as exc:
        if saved:
            return unpack(saved, f'{exc} Showing cached measurements retrieved {saved["retrieved_at"]}; observation times are unchanged.')
        return {**station, 'rows': [], 'url': DATA_URL, 'error': str(exc), 'retrieved_at': None}


def fetch_context(a, b, now=None):
    now = now or datetime.now(timezone.utc)
    catalogue, warning, catalogue_time = station_catalogue(now)
    stations = select_stations(catalogue, a, b)
    start = now-timedelta(hours=6)
    # Shared provider gate serializes and spaces requests, stopping on 429.
    records = [fetch_station(station, start, now) for station in stations]
    warnings = list(dict.fromkeys([message for message in [warning, *(s.get('cache_warning') or s.get('error') for s in records)] if message]))
    return {'a': a, 'b': b, 'retrieved_at': now, 'start': start, 'stations': records,
            'catalogue_retrieved_at': catalogue_time, 'warnings': warnings}
