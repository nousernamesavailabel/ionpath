from datetime import datetime, timezone
from pathlib import Path
import unittest
from unittest.mock import patch

import altair as alt
import pandas as pd
from streamlit.testing.v1 import AppTest

from model_heatmap import heatmap_data, heatmap_spec, snr_domain


def table():
    return pd.DataFrame([{'frequency_mhz': frequency, 'utc_hour': hour, 'direction': direction,
                          'median_snr_db': hour-30 if direction == 'A→B' else hour-40}
                         for frequency in (3.75, 7.15, 28.5) for hour in range(24) for direction in ('A→B', 'B→A')])


class ModelHeatmap(unittest.TestCase):
    def test_all_hours_frequencies_and_both_directions_are_preserved(self):
        raw = table()
        frame, order = heatmap_data(raw, datetime(2026,10,17,14,tzinfo=timezone.utc), 'America/Anchorage')
        self.assertEqual(len(frame), 72)
        self.assertEqual(len(order), 24)
        self.assertEqual(frame.frequency_mhz.nunique(), 3)
        self.assertTrue(frame['Weaker direction'].eq(frame['B→A']).all())
        self.assertEqual(frame.is_target_hour.sum(), 3)
        clock = frame.drop_duplicates('utc_hour').set_index('hour_key').loc[order]
        self.assertEqual(clock.utc_hour.tolist(), list(range(8,24))+list(range(8)))
        self.assertIn('2026-10-16', frame.loc[frame.utc_hour.eq(0), 'local_time'].iloc[0])
        pd.testing.assert_frame_equal(raw, table())

    def test_missing_direction_is_unknown_not_a_false_two_way_prediction(self):
        raw = table()
        raw = raw[~((raw.direction == 'B→A') & (raw.utc_hour == 0))]
        frame, _ = heatmap_data(raw, datetime(2026,10,17,tzinfo=timezone.utc))
        self.assertEqual(len(frame), 72)
        self.assertTrue(frame.loc[frame.utc_hour.eq(0), 'Weaker direction'].isna().all())

    def test_dst_and_fractional_timezones_keep_24_distinct_bins(self):
        frame, order = heatmap_data(table(), datetime(2026,11,1,tzinfo=timezone.utc), 'America/Anchorage')
        hours = frame.drop_duplicates('utc_hour')
        self.assertEqual(hours.hour_key.nunique(), 24)
        self.assertEqual(len(order), 24)
        self.assertEqual((hours.local_hour == '01').sum(), 2)
        frame, order = heatmap_data(table(), datetime(2026,10,17,tzinfo=timezone.utc), 'Asia/Kolkata')
        self.assertTrue(frame.local_hour.str.endswith(':30').all())
        spec = heatmap_spec(frame, 'A→B', order, 'Local time', 'Asia/Kolkata', [-40,0])
        self.assertEqual(spec['encoding']['x']['axis']['labelAngle'], -45)

    def test_schema_and_color_domain_are_shared_for_all_views(self):
        raw = table()
        frame, order = heatmap_data(raw, datetime(2026,10,17,tzinfo=timezone.utc))
        domain = snr_domain(raw)
        self.assertEqual(domain, [-40, 0])
        for direction in ('A→B', 'B→A', 'Weaker direction'):
            spec = heatmap_spec(frame, direction, order, 'UTC', 'UTC', domain, numbers=True)
            alt.Chart.from_dict({**spec, 'data': {'values': frame.to_dict('records')}}).to_dict(validate=True)
            self.assertEqual(spec['layer'][0]['encoding']['color']['condition']['scale']['domain'], domain)
            self.assertEqual(spec['encoding']['x']['sort'], list(range(24)))
            self.assertEqual(spec['encoding']['y']['sort'], ['28.5 MHz', '7.15 MHz', '3.75 MHz'])

    def test_view_switches_use_existing_results_without_network_or_new_analysis(self):
        target = datetime(2026,10,17,14,tzinfo=timezone.utc)
        script = """import streamlit as st
from model_heatmap import render_model_heatmap
render_model_heatmap(st.session_state['hf'], 'America/Anchorage')
"""
        test = AppTest.from_string(script)
        test.session_state['hf'] = {'table': table(), 'target': target}
        with patch('requests.get') as request:
            test.run()
            self.assertFalse(test.exception)
            self.assertEqual(len(test.get('vega_lite_chart')), 2)
            next(s for s in test.selectbox if s.label == 'Heatmap direction').set_value('Weaker direction').run()
            next(c for c in test.checkbox if c.label == 'Show SNR labels').check().run()
            next(r for r in test.radio if r.label == 'Heatmap clock').set_value('UTC').run()
            self.assertFalse(test.exception)
            self.assertEqual(len(test.get('vega_lite_chart')), 1)
            request.assert_not_called()
