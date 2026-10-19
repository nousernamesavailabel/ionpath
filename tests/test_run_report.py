from datetime import datetime, timedelta, timezone
import math
from pathlib import Path
import sys
import unittest

import pandas as pd
from antennas import defaults
from path_geometry import EARTH_RADIUS_KM, path_details, magnetic_declination, magnetic_path_details
from run_report import build_pdf, capture_run, model_assumption_rows


def example_snapshot():
    now=datetime(2026,10,18,18,tzinfo=timezone.utc)
    a,b=(61.2181,-149.9003),(32.8,-96.8)
    frequencies=(1.9,3.75,5.35,7.15,10.125,14.175,18.1,21.225,24.94,28.5)
    table=pd.DataFrame([{'frequency_mhz':freq,'utc_hour':hour,'direction':direction,
                         'median_snr_db':-32+3*i+12*math.sin(hour/24*math.pi*2)+(2 if direction=='A→B' else 0)}
                        for i,freq in enumerate(frequencies) for hour in range(24) for direction in ('A→B','B→A')])
    settings={'antenna_a':defaults('40 m dipole at 10 m'),'antenna_b':defaults('20 m end-fed wire'),
              'power':100,'bandwidth':2400,'noise':'RURAL','manual':True,'ssn':100}
    inputs={'a':{'center':a,'radius_km':100},'b':{'center':b,'radius_km':600},
            'callsign':'TEST123','station_a_callsign':'TEST123','selection_scope':'Selected callsign',
            'direction':'Both directions','source':'WSPR.live (online)','lookback_days':90,'row_limit':50000,
            'timezone':'America/Anchorage','target_utc':now.isoformat(),'hour_tolerance':2,'season':False,'season_days':183,
            'model_settings':settings}
    station={'name':'GAKONA','code':'GA762','error':None,'cache_warning':None,'url':'https://lgdc.uml.edu/fastchar/getbest',
             'distance_a_km':250,'distance_b_km':4500,'path_distance_km':50,
             'rows':[{'time':now-timedelta(minutes=15),'confidence':80,'fof2_mhz':7.45,'hmf2_km':290,
                      'fof2_qualifier':'','hmf2_qualifier':''}]}
    run={'a':a,'b':b,'generated_at':now,'target':now,'errors':{},'weather':{},
         'iono':{'retrieved_at':now,'stations':[station],'warnings':[]},
         'hf':{'table':table,'target':now,'provenance':{'model':'ITU-R P.533-14 / v14.3','ssn':100,'ssn_source':'Manual R12'}}}
    scores=pd.DataFrame([{'Band':'14 MHz (20m)','A → B median SNR (dB)':-12.3,'B → A median SNR (dB)':-16.2,'Spots':123},
                         {'Band':'7 MHz (40m)','A → B median SNR (dB)':-18.5,'B → A median SNR (dB)':float('nan'),'Spots':42}])
    state={'path_dashboard':run,'results_mgrs':(scores,pd.DataFrame([{}]*165)),
           'analysis_context':{'source':inputs['source'],'waveform':'WSPR','row_limited':True},
           'wspr_history':{'start':now-timedelta(days=90),'end':now,'fetched_at':now,'row_count':1400,'limited':False}}
    return capture_run(state,inputs)


class PathGeometry(unittest.TestCase):
    def test_quarter_equator_and_reverse_bearing(self):
        d=path_details((0,0),(0,90))
        self.assertAlmostEqual(d['short_km'],math.pi*EARTH_RADIUS_KM/2)
        self.assertAlmostEqual(d['long_km'],math.pi*EARTH_RADIUS_KM*1.5)
        self.assertAlmostEqual(d['ab_short_deg'],90)
        self.assertAlmostEqual(d['ba_short_deg'],270)
        self.assertAlmostEqual(d['ab_long_deg'],270)
    def test_reverse_bearing_is_calculated_at_other_endpoint(self):
        d=path_details((61,-149),(32,-97))
        self.assertGreater(abs((d['ab_short_deg']+180)%360-d['ba_short_deg']),10)
    def test_antimeridian_coincident_antipodal_and_poles(self):
        d=path_details((10,179),(10,-179))
        self.assertLess(d['short_km'],250)
        self.assertIsNone(path_details((0,0),(0,0))['long_km'])
        self.assertIsNone(path_details((0,0),(0,180))['ab_short_deg'])
        self.assertIsNone(path_details((90,0),(10,20))['ab_short_deg'])


class MagneticBearings(unittest.TestCase):
    def test_wmm_documented_seattle_reference_and_historical_model(self):
        from datetime import date
        result=magnetic_declination(47.6205,-122.3493,date(2025,4,2))
        self.assertAlmostEqual(result['declination_deg'],15.065629638512593,delta=.001)
        self.assertIn('2025',result['model'])
        self.assertIn('2020',magnetic_declination(47.6205,-122.3493,date(2024,5,12))['model'])
        self.assertIsNone(magnetic_declination(47.6205,-122.3493,date(2035,1,1))['declination_deg'])
    def test_each_departure_uses_its_own_east_or_west_declination(self):
        from datetime import date
        from unittest.mock import patch
        with patch('path_geometry.magnetic_declination',side_effect=[
                {'declination_deg':20,'model':'test','status':'Available'},
                {'declination_deg':-10,'model':'test','status':'Available'}]):
            details=magnetic_path_details((0,0),(0,90),date(2026,10,18))
        self.assertEqual(details['ab_short_magnetic_deg'],70)
        self.assertEqual(details['ba_short_magnetic_deg'],280)
        self.assertEqual(details['ab_long_magnetic_deg'],250)
        self.assertEqual(details['ba_long_magnetic_deg'],100)
    def test_dependency_error_is_retried_for_same_location_and_date(self):
        from datetime import date
        from unittest.mock import patch
        from types import SimpleNamespace
        magnetic_declination.cache_clear()
        with patch('pygeomag.GeoMag',side_effect=ImportError("No module named 'pygeomag'")):
            unavailable=magnetic_declination(61.2181,-149.9003,date(2026,10,19))
            self.assertIsNone(unavailable['declination_deg'])
        restored=magnetic_declination(61.2181,-149.9003,date(2026,10,19))
        self.assertIsNotNone(restored['declination_deg'])
        self.assertEqual(restored['status'],'Available')
        magnetic_declination.cache_clear()

    def test_blackout_does_not_produce_an_azimuth(self):
        from datetime import date
        from unittest.mock import patch
        from types import SimpleNamespace
        magnetic_declination.cache_clear()
        with patch('pygeomag.GeoMag') as model:
            model.return_value.model='WMM-2025'
            model.return_value.calculate.return_value=SimpleNamespace(in_blackout_zone=True)
            value=magnetic_declination(85,140,date(2026,10,18))
            self.assertIsNone(value['declination_deg'])
            self.assertIn('blackout',value['status'])
        magnetic_declination.cache_clear()


class Reports(unittest.TestCase):
    def test_complete_report_and_snapshot_does_not_follow_control_edits(self):
        snapshot=example_snapshot()
        original=snapshot['inputs']['a']['center']
        state={'path_dashboard':snapshot['dashboard']}
        copied=capture_run(state,snapshot['inputs'])
        state['path_dashboard']['a']=(0,0)
        snapshot['inputs']['model_settings']['power']=500
        self.assertEqual(copied['dashboard']['a'],original)
        self.assertEqual(copied['inputs']['model_settings']['power'],100)
        pdf=build_pdf(example_snapshot())
        self.assertTrue(pdf.startswith(b'%PDF-'))
        self.assertGreater(len(pdf),20000)
    def test_assumptions_show_values_from_model_provenance(self):
        snapshot=example_snapshot()
        snapshot['dashboard']['hf']['provenance'].update(power_w_each_direction=125,bandwidth_hz=600,noise_at_both_receivers='CITY')
        rows=dict(model_assumption_rows(snapshot))
        self.assertEqual(rows['Transmit power, A to B / B to A'],'125 W / 125 W')
        self.assertEqual(rows['Receiver bandwidth, A / B'],'600 Hz / 600 Hz')
        self.assertEqual(rows['Receiver noise, A / B'],'CITY / CITY')
        self.assertIn('20 m',rows['Antenna A'])
        self.assertIn('90 deg true',rows['Antenna A'])
        self.assertIn('14.175',rows['Frequencies (MHz)'])
        self.assertIn('TEST123',rows['Station A'])
        self.assertIn('61.21810',rows['Station A'])
        self.assertIn('600 km',rows['Station B'])
        self.assertIn('100',rows['Smoothed sunspot number (R12)'])

    def test_unused_saved_callsign_is_not_presented_as_a_reception_filter(self):
        snapshot=example_snapshot()
        snapshot['inputs'].update(callsign=None,station_a_callsign='N0JMP',selection_scope='Any station in A circle')
        rows=dict(model_assumption_rows(snapshot))
        self.assertNotIn('N0JMP',str(rows))
        self.assertIn('Reception callsign filter: None (any station in A circle)',rows['Station A'])
        snapshot['inputs']['callsign']='USED123'
        rows=dict(model_assumption_rows(snapshot))
        self.assertIn('Reception callsign filter: USED123',rows['Station A'])
        self.assertNotIn('N0JMP',str(rows))

    def test_partial_run_and_antimeridian_map_still_export(self):
        snapshot=example_snapshot()
        snapshot['inputs']['a']['center']=(10,179)
        snapshot['inputs']['b']['center']=(10,-179)
        snapshot['dashboard']['hf']=None
        snapshot['dashboard']['iono']=None
        snapshot['dashboard']['errors']={'hf':'Model unavailable','iono':'Provider failed'}
        snapshot['reception_error']='No matching reports'
        snapshot['reception_scores']=None
        self.assertTrue(build_pdf(snapshot).startswith(b'%PDF-'))


if __name__=='__main__':
    if len(sys.argv)>1:
        Path(sys.argv[1]).write_bytes(build_pdf(example_snapshot()))
    else:
        unittest.main()
