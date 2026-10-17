from datetime import datetime, timedelta, timezone
import unittest

from hf_model import model_ready, parse_control_points, run_control_points
from measurement_support import assess_support

NOW = datetime(2026, 10, 17, 17, 0, tzinfo=timezone.utc)
POINTS = [{'region': 'MidPath', 'lat': 61., 'lon': -149., 'fof2_mhz': 5.},
          {'region': 'R - d0/2', 'lat': 61.5, 'lon': -149., 'fof2_mhz': 5.}]


def station(value=5., cs=95, age=5, lat=61., qualifier='//'):
    return {'code': 'GA762', 'lat': lat, 'lon': -149., 'rows': [
        {'time': NOW-timedelta(minutes=age), 'fof2_mhz': value, 'hmf2_km': 250.,
         'confidence': cs, 'fof2_qualifier': qualifier, 'hmf2_qualifier': '//'}]}


class SupportAssessment(unittest.TestCase):
    def test_coverage_agreement_and_disagreement_are_distinct(self):
        result = assess_support(POINTS, [station()], NOW, NOW)
        self.assertEqual(result['status'], 'Supports F2 baseline')
        self.assertEqual(result['covered_regions'], 2)
        self.assertEqual(result['regions'][0]['Model foF2 (MHz)'], 5)
        self.assertEqual(assess_support(POINTS, [station(7)], NOW, NOW)['status'], 'Disagrees with F2 baseline')
        self.assertEqual(assess_support(POINTS, [station(6)], NOW, NOW)['status'], 'Supports F2 baseline')

    def test_partial_geographic_coverage_never_claims_path_agreement_or_disagreement(self):
        points = POINTS + [{'region': 'T + d0/2', 'lat': 40., 'lon': -100., 'fof2_mhz': 5.}]
        result = assess_support(points, [station(8)], NOW, NOW)
        self.assertEqual(result['status'], 'Cannot assess')
        self.assertEqual(result['covered_regions'], 2)
        self.assertEqual(result['regions'][-1]['Comparison'], 'Too far from control region')
        self.assertEqual(result['regions'][0]['Comparison'], 'Outside tolerance')

    def test_stale_missing_low_quality_and_flagged_readings_cannot_support(self):
        for reading in (station(cs=45), station(cs=-1), station(age=31), station(age=-1), station(value=None), station(qualifier='A')):
            with self.subTest(reading=reading):
                self.assertEqual(assess_support(POINTS, [reading], NOW, NOW)['status'], 'Cannot assess')
        self.assertEqual(assess_support(POINTS, [station(cs=999)], NOW, NOW)['status'], 'Supports F2 baseline')

    def test_temporal_comparison_uses_actual_model_hour_and_requires_current_readings(self):
        result = assess_support(POINTS, [station()], NOW-timedelta(days=1), NOW)
        self.assertEqual(result['status'], 'Cannot assess')
        self.assertIn('target time', result['reason'])
        # A half-hour timezone target still uses the model's rounded-down UTC hour.
        result = assess_support(POINTS, [station()], NOW+timedelta(minutes=45), NOW+timedelta(minutes=45))
        self.assertEqual(result['status'], 'Cannot assess')
        self.assertEqual(result['model_hour_utc'], NOW.isoformat())
        self.assertEqual(assess_support([], [station()], NOW, NOW)['status'], 'Cannot assess')

    def test_parser_uses_degrees_and_rejects_unsupported_or_incomplete_output(self):
        dump = '''distance = 4909.174
* Control Point - MidPath *
 Latitude = 0.871 (49.906) [49 54 21]
 Longitude = -2.018 (-115.616) [-115 36 56]
 foF2 = 7.964 (MHz)
'''
        point = parse_control_points(dump)[0]
        self.assertEqual(point['lat'], 49.906)
        self.assertEqual(point['lon'], -115.616)
        self.assertEqual(point['fof2_mhz'], 7.964)
        for bad in (dump.replace('4909.174', '9000'), dump.replace('foF2 = 7.964', 'foE = 7.964'), 'unrecognized output'):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                parse_control_points(bad)

    @unittest.skipUnless(model_ready(), 'Local official model not installed')
    def test_real_control_point_dump_provides_corresponding_baseline(self):
        points = run_control_points((61.2181,-149.9003), (32.7767,-96.797), NOW, 7.15, 88)
        self.assertEqual(len(points), 5)
        midpoint = next(p for p in points if p['region'] == 'MidPath')
        self.assertAlmostEqual(midpoint['fof2_mhz'], 7.964, places=3)
        self.assertAlmostEqual(midpoint['lat'], 49.906, places=3)
