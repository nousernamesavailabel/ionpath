from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
import unittest

import pandas as pd
from streamlit.testing.v1 import AppTest
from hf_model import model_ready

ROOT = Path(__file__).resolve().parents[1]


class UnifiedDashboard(unittest.TestCase):
    def setUp(self):
        import tempfile
        from contextlib import ExitStack
        self.context = ExitStack()
        self.addCleanup(self.context.close)
        temporary = self.context.enter_context(tempfile.TemporaryDirectory())
        self.context.enter_context(patch('validation_log.DB', Path(temporary) / 'history.sqlite3'))
        self.context.enter_context(patch('antenna_defaults.SETTINGS', Path(temporary) / 'antenna_settings'))
        self.context.enter_context(patch('realtime_context.load_irtam', return_value={'error': 'Offline test'}))

    @unittest.skipUnless(model_ready(), 'Local official ITU model not installed')
    def test_one_click_runs_all_sources_and_retains_independent_results(self):
        now = datetime.now(timezone.utc)
        feeds = {'solar_observed': {'payload': [{'time-tag': now.strftime('%Y-%m'), 'smoothed_ssn': 110}],
                                    'retrieved_at': now.isoformat(), 'url': 'https://services.swpc.noaa.gov/'}}
        iono = {'a': (61.5, -149.), 'b': (32.5, -97.), 'start': now-timedelta(hours=6),
                'retrieved_at': now, 'stations': []}
        rows = []
        for band in (7, 14):
            rows.append(dict(time=now.replace(hour=6, minute=0).isoformat(), band=band,
                             tx_sign='TESTA', tx_lat=61.5, tx_lon=-149., tx_loc='BP51',
                             rx_sign='TESTB', rx_lat=32.5, rx_lon=-97., rx_loc='EM12', power=30, snr=-10, code=1))
        class Response:
            text = pd.DataFrame(rows).to_csv(index=False)
            def raise_for_status(self):
                pass
        with patch('dashboard.refresh_weather', return_value=feeds) as weather, patch('dashboard.cached_context', return_value=iono) as soundings, patch('requests.get', return_value=Response()) as receptions:
            test = AppTest.from_file(str(ROOT/'app.py'), default_timeout=30).run()
            next(s for s in test.slider if s.label == 'Target hour (local time)').set_value(22)
            next(b for b in test.button if b.label == 'Analyze propagation').click().run()
        self.assertFalse(test.exception)
        self.assertEqual(weather.call_count, 1)
        self.assertEqual(soundings.call_count, 1)
        self.assertEqual(receptions.call_count, 2)
        self.assertIsNotNone(test.session_state['path_dashboard']['hf'])
        self.assertIsNotNone(test.session_state['path_dashboard']['iono'])
        self.assertIn('results_mgrs', test.session_state)
        self.assertTrue(test.session_state['dashboard_has_run'])
        self.assertFalse(any(b.label in ('Calculate HF outlook', 'Refresh NOAA conditions', 'Load / refresh ionosonde context') for b in test.button))
        headings = [element.value for element in test.markdown]
        for label in ('### WSPR evidence', '### HF outlook', '### Ionosonde context'):
            self.assertIn(label, headings)
        self.assertTrue(any('R12 used: 110' in value for value in headings))
        self.assertTrue(any('Measurement check:' in value for value in headings))
        self.assertIn('measurement_support', test.session_state['path_dashboard']['hf']['provenance'])

    def test_failed_control_diagnostic_does_not_change_successful_snr_outlook(self):
        from dashboard import run_context_layers
        target = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        table = pd.DataFrame([{'utc_hour': target.hour, 'frequency_mhz': 7.15, 'direction': direction, 'median_snr_db': snr}
                              for direction, snr in [('A→B', -5.), ('B→A', -8.)]])
        settings = {'manual': True, 'ssn': 88, 'frequency_text': '7.15', 'power': 100, 'bandwidth': 2400,
                    'gain_a': 0, 'gain_b': 0, 'noise': 'RURAL'}
        iono = {'stations': []}
        with patch('dashboard.refresh_weather', return_value={}), patch('dashboard.cached_context', return_value=iono), patch('dashboard.modeled_path', return_value=table), patch('dashboard.run_control_points', side_effect=ValueError('unsupported diagnostic')):
            result = run_context_layers((61., -149.), (32., -97.), target, settings)
        pd.testing.assert_frame_equal(result['hf']['table'], table)
        self.assertNotIn('hf', result['errors'])
        support = result['hf']['provenance']['measurement_support']
        self.assertEqual(support['status'], 'Cannot assess')
        self.assertIn('diagnostics unavailable', support['reason'])
