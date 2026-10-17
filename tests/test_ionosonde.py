from datetime import datetime, timedelta, timezone
from pathlib import Path
import unittest
from unittest.mock import patch

import requests
from streamlit.testing.v1 import AppTest

from ionosonde import parse_measurements, parse_stations, select_stations
from ionosonde_panel import context_table

NOW = datetime(2026, 10, 17, 17, 30, tzinfo=timezone.utc)
A, B = (61.2181, -149.9003), (32.7767, -96.797)
DATA = '''# Global Ionospheric Radio Observatory
# Location: GEO (62.4 N 215.0 E), URSI-Code GA762, GAKONA
# Time                    CS   foF2 QD   hmF2 QD
2026-10-17T17:15:05.000Z 45 4.625 // 216.2 //
2026-10-17T17:22:35.000Z 0 9999.000 __ --- __
'''


def context():
    station = {'code': 'GA762', 'name': 'GAKONA', 'lat': 62.38, 'lon': -145.,
               'distance_a_km': 288, 'distance_b_km': 4705, 'path_distance_km': 203,
               'rows': parse_measurements(DATA, 'GA762'), 'url': 'https://lgdc.uml.edu/fastchar/getbest', 'error': None}
    return {'a': A, 'b': B, 'retrieved_at': NOW, 'start': NOW-timedelta(hours=6), 'stations': [station]}


class Ionosondes(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.settings_folder=tempfile.TemporaryDirectory()
        self.addCleanup(self.settings_folder.cleanup)
        settings=patch('antenna_defaults.SETTINGS',Path(self.settings_folder.name))
        settings.start()
        self.addCleanup(settings.stop)

    def test_catalogue_parses_unclosed_rows_and_converts_longitude(self):
        text = '<table><tr><td>1</td><td>GA762</td><td>GAKONA</td><td>62.38</td><td>215.00</td><tr><td>2</td><td>EI764</td><td>EIELSON</td><td>64.66</td><td>212.93</td></table>'
        stations = parse_stations(text)
        self.assertEqual(len(stations), 2)
        self.assertEqual(stations[0]['lon'], -145)
        with self.assertRaises(ValueError):
            parse_stations('<html>Service unavailable</html>')

    def test_measurements_keep_missing_values_and_actual_quality(self):
        rows = parse_measurements(DATA, 'GA762')
        self.assertEqual(rows[0]['confidence'], 45)
        self.assertEqual(rows[0]['fof2_mhz'], 4.625)
        self.assertIsNone(rows[-1]['fof2_mhz'])
        self.assertIsNone(rows[-1]['hmf2_km'])
        self.assertEqual(rows[-1]['confidence'], 0)
        with self.assertRaises(ValueError):
            parse_measurements(DATA, 'IF843')
        with self.assertRaises(ValueError):
            parse_measurements(DATA.replace('CS   foF2', 'CS   hmF2'), 'GA762')
        self.assertEqual(parse_measurements('# Location: URSI-Code GA762\n# STATUS: ERROR (No measurement data could be found for requested time)', 'GA762'), [])

    def test_geographic_selection_is_bounded_and_includes_site_aliases(self):
        stations = [{'code': f'AA{i:03}', 'name': str(i), 'lat': float(i), 'lon': -100.} for i in range(70)]
        stations += [{'code': 'ALIAS', 'name': 'alias', 'lat': 33., 'lon': -100.}]
        selected = select_stations(stations, (33., -100.), (40., -100.))
        self.assertLessEqual(len(selected), 8)
        # Aliases are included when the geographic site selections leave room.
        compact = [s for s in stations if s['lat'] in (33., 36., 38., 40.)]
        self.assertTrue(any(s['code'] == 'ALIAS' for s in select_stations(compact, (33., -100.), (40., -100.))))
        self.assertTrue(all(s['path_distance_km'] >= 0 for s in selected))

    def test_latest_missing_does_not_silently_use_older_valid_measurement(self):
        frame = context_table(context(), NOW)
        self.assertEqual(frame.iloc[0]['Status'], 'Latest foF2 unavailable')
        self.assertIsNone(frame.iloc[0]['foF2 (MHz)'])
        frame = context_table(context(), NOW+timedelta(hours=2))
        self.assertEqual(frame.iloc[0]['Status'], 'Stale (>60 min)')

    def test_shared_run_loads_context_and_does_not_mix_previous_data_on_failure(self):
        test = AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app.py'), default_timeout=30).run()
        with patch('dashboard.cached_context', return_value=context()), patch('dashboard.refresh_weather', return_value={}), patch('requests.get', side_effect=requests.ConnectionError('offline')):
            next(b for b in test.button if b.label == 'Analyze propagation').click().run()
        self.assertFalse(test.exception)
        self.assertEqual(test.session_state['path_dashboard']['iono']['retrieved_at'], NOW)
        self.assertTrue(any('GIRO measurements' in m.value for m in test.markdown))
        self.assertIsNone(test.session_state['path_dashboard']['hf'])
        with patch('dashboard.cached_context', side_effect=requests.ConnectionError('offline')), patch('dashboard.refresh_weather', return_value={}), patch('requests.get', side_effect=requests.ConnectionError('offline')):
            next(b for b in test.button if b.label == 'Analyze propagation').click().run()
        self.assertFalse(test.exception)
        self.assertTrue(any('offline' in w.value for w in test.warning))
        self.assertIsNone(test.session_state['path_dashboard']['iono'])
