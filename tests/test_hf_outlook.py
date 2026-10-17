import json
import math
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import requests
from streamlit.testing.v1 import AppTest

from hf_model import frequencies_from_text, input_text, model_ready, parse_report, run_model
from space_weather import latest_kp, load_feed, parse_drap, path_absorption_context, path_points, solar_for_month

ROOT = Path(__file__).resolve().parents[1]
A, B = (61.2181, -149.9003), (32.7767, -96.797)
DRAP = '''# Product Valid At : 2026-10-17 12:00 UTC
 -178 178
------------------
 60 | 3.0 4.0
 -60 | 0.0 1.0
'''


class Weather(unittest.TestCase):
    def test_kp_formats_dates_nulls_and_ranges(self):
        rows = [{'time_tag': '2026-10-17T00:00:00', 'Kp': 2.3},
                {'time_tag': '2026-10-17T03:00:00Z', 'Kp': 5},
                {'time_tag': 'bad', 'Kp': 3}, {'time_tag': '2026-10-17', 'Kp': None}]
        self.assertEqual(latest_kp(rows)['value'], 5)
        self.assertEqual(latest_kp([['time_tag', 'Kp'], ['2026-10-17T00:00:00', '3.33']])['value'], 3.33)
        with self.assertRaises(ValueError):
            latest_kp([{'time_tag': '2026-10-17', 'Kp': 20}])

    def test_ssn_exact_month_prefers_smoothed_never_daily(self):
        observed = [{'time-tag': '2026-05', 'ssn': 200, 'smoothed_ssn': 110.4},
                    {'time-tag': '2026-06', 'ssn': 200, 'smoothed_ssn': -1}]
        predicted = [{'time-tag': '2026-06', 'predicted_ssn': 95}]
        self.assertEqual(solar_for_month(observed, predicted, '2026-05')['value'], 110)
        self.assertIn('predicted', solar_for_month(observed, predicted, '2026-06')['source'])
        self.assertIsNone(solar_for_month(observed, predicted, '2026-07'))

    def test_drap_validation_and_date_line_sampling(self):
        grid = parse_drap(DRAP)
        self.assertEqual(grid['time'].hour, 12)
        points = path_points((60, 179), (60, -179))
        self.assertTrue(all(abs(lon) > 178 for _, lon in points))
        self.assertEqual(path_absorption_context(grid, (60, 179), (60, -179)), 4)
        with self.assertRaises(ValueError):
            parse_drap(DRAP.replace('3.0 4.0', '3.0'))
        with self.assertRaises(ValueError):
            parse_drap(DRAP.replace('3.0', 'nan'))
        with self.assertRaises(ValueError):
            path_points((0, 0), (0, 180))

    def test_failed_refresh_preserves_data_and_age(self):
        with tempfile.TemporaryDirectory() as folder, patch('space_weather.CACHE', Path(folder)):
            saved = {'payload': [{'time_tag': '2026-10-17T00:00:00', 'Kp': 3}],
                     'retrieved_at': '2026-10-17T01:00:00+00:00', 'url': 'https://example.test'}
            (Path(folder)/'kp.json').write_text(json.dumps(saved))
            with patch('space_weather.requests.get', side_effect=requests.ConnectionError('offline')):
                result = load_feed('kp', refresh=True)
                self.assertEqual(result['payload'], saved['payload'])
                self.assertEqual(result['retrieved_at'], saved['retrieved_at'])
                self.assertIn('offline', result['error'])
                (Path(folder)/'kp.json').unlink()
                self.assertIsNone(load_feed('kp', refresh=True)['payload'])


class Model(unittest.TestCase):
    def setUp(self):
        from contextlib import ExitStack
        self.context = ExitStack()
        self.addCleanup(self.context.close)
        temporary = self.context.enter_context(tempfile.TemporaryDirectory())
        self.context.enter_context(patch('validation_log.DB', Path(temporary) / 'history.sqlite3'))
        self.context.enter_context(patch('antenna_defaults.SETTINGS', Path(temporary) / 'antenna_settings'))
        self.context.enter_context(patch('realtime_context.load_irtam', return_value={'error': 'Offline test'}))
        # Network failures must not cancel the independent local model.
        self.network = patch('requests.get', side_effect=requests.ConnectionError('offline test'))
        self.iono = patch('dashboard.cached_context', side_effect=requests.ConnectionError('offline test'))
        self.network.start()
        self.iono.start()
        self.addCleanup(self.network.stop)
        self.addCleanup(self.iono.stop)
    def test_frequency_validation_and_input_units(self):
        for text in ('nan', 'inf', '50', '1', '', '7.001,7.002'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                frequencies_from_text(text)
        self.assertEqual(frequencies_from_text('14, 7, 14'), (7., 14.))
        config = input_text(A, B, 2026, 10, (7., 14.), 88, 100, 2400, 2, 3, 'RURAL', Path('/tmp'))
        self.assertIn('Path.txpower -10.0', config)
        self.assertIn('Path.hour 1,2,3', config)
        self.assertIn('TXGOS 2', config)
        with self.assertRaises(ValueError):
            input_text(A, A, 2026, 10, (7.,), 88, 100, 2400, 0, 0, 'RURAL', Path('/tmp'))

    def test_report_hour_mapping_and_incomplete_rejection(self):
        header = 'month,hour,frequency,distance,BMUF,OPMUF,PR,SNR\n'
        rows = [f'10,{hour},7.15,4900,20,25,-120,{hour}' for hour in range(1,25)]
        result = parse_report(header+'\n'.join(rows), (7.15,))
        self.assertEqual(result.utc_hour.tolist(), list(range(24)))
        self.assertEqual(result.iloc[0].median_snr_db, 1)
        with self.assertRaises(ValueError):
            parse_report(header+'\n'.join(rows[:-1]), (7.15,))
        with self.assertRaises(ValueError):
            parse_report(header+'\n'.join(rows).replace('-120', 'nan'), (7.15,))

    @unittest.skipUnless(model_ready(), 'Local official ITU model not installed')
    def test_real_model_power_bandwidth_and_direction(self):
        base = run_model(A, B, 2026, 10, (7.15, 18.1), ssn=88)
        higher_power = run_model(A, B, 2026, 10, (7.15, 18.1), ssn=88, power_w=1000)
        wider = run_model(A, B, 2026, 10, (7.15, 18.1), ssn=88, bandwidth_hz=24000)
        reverse = run_model(B, A, 2026, 10, (7.15, 18.1), ssn=88)
        self.assertEqual(len(base), 48)
        self.assertTrue(((higher_power.median_snr_db-base.median_snr_db-10).abs()<.02).all())
        self.assertTrue(((wider.median_snr_db-base.median_snr_db+10).abs()<.02).all())
        self.assertTrue((base.distance_km-reverse.distance_km).abs().max()<.1)

    def test_missing_noaa_ssn_does_not_silently_use_manual_default(self):
        test = AppTest.from_file(str(ROOT/'app.py'), default_timeout=30).run()
        with patch('dashboard.refresh_weather', return_value={}):
            next(b for b in test.button if b.label == 'Analyze propagation').click().run()
        self.assertFalse(test.exception)
        self.assertTrue(any('NOAA monthly SSN unavailable' in e.value for e in test.warning))
        self.assertIsNone(test.session_state['path_dashboard']['hf'])

    @unittest.skipUnless(model_ready(), 'Local official ITU model not installed')
    def test_noaa_workflow_and_provenance(self):
        test = AppTest.from_file(str(ROOT/'app.py'), default_timeout=30).run()
        self.assertFalse(any(n.label == 'Manual smoothed sunspot number (R12)' for n in test.number_input))
        # Pin a historical month independently of the machine's current date.
        from datetime import date
        test.date_input[0].set_value(date(2026, 5, 12))
        payloads = {'kp': [{'time_tag': '2026-10-17T00:00:00', 'Kp': 5}],
                    'drap': DRAP,
                    'solar_observed': [{'time-tag': '2026-05', 'smoothed_ssn': 110}],
                    'solar_predicted': []}
        feeds = {name: {'payload': payload, 'retrieved_at': '2026-10-17T01:00:00+00:00',
                        'url': 'https://services.swpc.noaa.gov/'} for name, payload in payloads.items()}
        with patch('dashboard.refresh_weather', return_value=feeds):
            next(b for b in test.button if b.label == 'Analyze propagation').click().run()
        self.assertFalse(test.exception)
        self.assertFalse(test.error)
        metadata = test.session_state['path_dashboard']['hf']['provenance']
        self.assertEqual(metadata['ssn'], 110)
        self.assertTrue(any('R12 used: 110' in m.value for m in test.markdown))
        self.assertIn('background-color:', test.get('html')[0].proto.body)
        self.assertIn('Modeled SNR outlook', test.get('html')[0].proto.body)
        self.assertEqual(metadata['ssn_source']['source'], 'NOAA observed smoothed SSN')
        self.assertTrue(any('Geomagnetic storm' in w.value for w in test.warning))
        self.assertTrue(any('D-RAP:' in m.value for m in test.markdown))

    @unittest.skipUnless(model_ready(), 'Local official ITU model not installed')
    def test_panel_works_without_reception_reports_and_marks_changes(self):
        test = AppTest.from_file(str(ROOT/'app.py'), default_timeout=30).run()
        next(c for c in test.checkbox if c.label == 'Use manual SSN (offline fallback)').check().run()
        next(b for b in test.button if b.label == 'Analyze propagation').click().run()
        self.assertFalse(test.exception)
        self.assertFalse(test.error)
        self.assertEqual(len(test.session_state['path_dashboard']['hf']['table']), 480)
        self.assertNotIn('results_mgrs', test.session_state)
        next(s for s in test.slider if s.label == 'Target hour (local time)').set_value(22).run()
        self.assertTrue(any('previous run' in w.value for w in test.warning))
