from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from giro_cache import RateLimited, provider_get, read_cache, retry_deadline, write_cache
from ionosonde import fetch_station, station_catalogue

NOW = datetime(2026, 10, 17, 17, 30, tzinfo=timezone.utc)
STATION = {'code': 'GA762', 'name': 'GAKONA', 'lat': 62.38, 'lon': -145.}


class GiroCache(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.cache = patch('giro_cache.CACHE', Path(self.directory.name))
        self.cache.start()
        self.addCleanup(self.cache.stop)

    def test_retry_after_seconds_date_and_default(self):
        self.assertEqual(retry_deadline('600', NOW), NOW+timedelta(minutes=10))
        self.assertEqual(retry_deadline('Sat, 17 Oct 2026 18:30:00 GMT', NOW), NOW+timedelta(hours=1))
        self.assertEqual(retry_deadline(None, NOW), NOW+timedelta(minutes=15))
        self.assertGreater(retry_deadline('0', NOW), NOW)

    def test_429_is_not_retried_and_cooldown_survives_next_request(self):
        response = Mock(status_code=429, headers={'Retry-After': '600'})
        with patch('giro_cache.requests.get', return_value=response) as request:
            with self.assertRaises(RateLimited):
                provider_get('https://lgdc.uml.edu/common/DIDBFastStationList')
            # Includes another URL: the cooldown is provider-wide, not path-specific.
            with self.assertRaises(RateLimited):
                provider_get('https://lgdc.uml.edu/fastchar/getbest')
            self.assertEqual(request.call_count, 1)
        self.assertIn('blocked_until', read_cache('provider'))

    def test_catalogue_cache_is_shared_and_stale_fallback_survives_429(self):
        write_cache('catalogue', {'retrieved_at': NOW.isoformat(), 'stations': [STATION]})
        with patch('ionosonde.provider_get') as request:
            stations, warning, stamp = station_catalogue(NOW+timedelta(days=2))
            request.assert_not_called()
            self.assertEqual(stations, [STATION])
            self.assertIsNone(warning)
        with patch('ionosonde.provider_get', side_effect=RateLimited('GIRO paused')):
            stations, warning, stamp = station_catalogue(NOW+timedelta(days=8))
            self.assertEqual(stations, [STATION])
            self.assertIn('cached station catalogue', warning)
            self.assertEqual(stamp, NOW.isoformat())

    def test_cached_measurements_keep_time_and_indicate_failed_refresh(self):
        row = {'time': NOW.isoformat(), 'confidence': 45, 'fof2_mhz': 4.6, 'hmf2_km': 220,
               'fof2_qualifier': '//', 'hmf2_qualifier': '//'}
        write_cache('station_GA762', {'retrieved_at': NOW.isoformat(), 'rows': [row], 'url': 'https://example.test'})
        with patch('ionosonde.provider_get') as request:
            result = fetch_station(STATION, NOW-timedelta(hours=6), NOW+timedelta(minutes=5))
            request.assert_not_called()
            self.assertEqual(result['rows'][0]['time'], NOW)
        with patch('ionosonde.provider_get', side_effect=RateLimited('GIRO paused')):
            result = fetch_station(STATION, NOW-timedelta(hours=5), NOW+timedelta(hours=1))
            self.assertEqual(result['rows'][0]['time'], NOW)
            self.assertEqual(result['retrieved_at'], NOW.isoformat())
            self.assertIn('cached measurements', result['cache_warning'])
            self.assertIsNone(result['error'])

    def test_empty_windows_are_cached_for_thirty_minutes(self):
        write_cache('station_GA762', {'retrieved_at': NOW.isoformat(), 'rows': [], 'url': 'https://example.test'})
        with patch('ionosonde.provider_get') as request:
            result = fetch_station(STATION, NOW-timedelta(hours=6), NOW+timedelta(minutes=20))
            request.assert_not_called()
            self.assertEqual(result['rows'], [])
