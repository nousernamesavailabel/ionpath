from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd
from antennas import defaults, geometry, solve, KINDS
from hf_model import model_ready, run_model
from realtime_context import parse_irtam, sample_irtam, absorption_cells, load_irtam
from validation_log import save_run, save_observation, observations, runs, summary


def fixture(valid):
    metadata={'Valid_Time':valid.strftime('%Y%m%dT%H%M'),'Model_Code':'IRTAM fixture',
              'Latitude':{'scalefactor':.01},'Longitude':{'scalefactor':.01}}
    data={'metadata':metadata,'Latitude':[5000,4000],'Longitude':[750,35250]}
    for name,scale,values in [('foF2',.01,[[500,600],[700,-9999]]),('hmF2',.1,[[30000,3500],[4000,4500]]),
                              ('MUF(3000)F2',.01,[[1500,1600],[1700,1800]]),('30-day_median',.01,[[2000,2000],[2000,2000]]),
                              ('Data_Confidence',.01,[[75,-99],[42,61]])]:
        metadata[name]={'scalefactor':scale,'missingValue':-99 if name=='Data_Confidence' else -9999}
        data[name]=values
    return data


class Antennas(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher=patch('antenna_defaults.SETTINGS',Path(self.tmp.name))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_all_geometry_types_are_above_ground_with_real_height(self):
        for kind in KINDS[1:]:
            c=defaults() | {'kind':kind,'height_m':20.,'length_m':20.}
            wires,feed=geometry(c,28.5)
            self.assertTrue(wires);self.assertTrue(feed[0]>=1)
            self.assertGreaterEqual(min(p[2] for w in wires for p in w[2:]),.5)
        with self.assertRaises(ValueError): geometry(defaults('40 m inverted V')|{'height_m':1.},7.15)

    def test_nec_height_orientation_and_loss_change_actual_patterns(self):
        low,_=solve(defaults('40 m dipole at 5 m'),7.15)
        high,impedance=solve(defaults('40 m dipole at 20 m'),7.15)
        rotated,_=solve(defaults('40 m dipole at 5 m')|{'bearing_deg':0.},7.15)
        loss,_=solve(defaults('40 m dipole at 5 m')|{'loss_db':3.},7.15)
        self.assertEqual(low.shape,(360,91))
        self.assertTrue(np.isfinite(high).all())
        self.assertGreater(impedance['resistance_ohm'],0)
        self.assertGreater(abs(low[0,20]-high[0,20]),2)
        self.assertGreater(low[0,30]-rotated[0,30],3)
        self.assertAlmostEqual(low[0,30]-loss[0,30],3,places=5)

    @unittest.skipUnless(model_ready(),'ITU model unavailable')
    def test_patterns_change_p533_snr_and_reverse_swaps_endpoints(self):
        from propagation_panel import modeled_path
        a,b=(61.,-149.),(32.,-97.)
        baseline=run_model(a,b,2026,10,(7.15,),88)
        antenna=defaults('40 m dipole at 10 m')
        custom=run_model(a,b,2026,10,(7.15,),88,antenna_tx=antenna)
        self.assertGreater((baseline.median_snr_db-custom.median_snr_db).abs().max(),1)
        with patch('propagation_panel.run_model',return_value=baseline) as model:
            modeled_path.__wrapped__(a,b,2026,10,(7.15,),88,100,2400,0,0,'RURAL',antenna,defaults())
        self.assertEqual(model.call_args_list[1].kwargs['antenna_rx'],antenna)
        self.assertEqual(model.call_args_list[1].args[:2],(b,a))


    def test_aim_rotates_dipole_broadside_and_handles_omnidirectional(self):
        from antennas import aim_at_endpoint
        aimed=aim_at_endpoint(defaults('40 m dipole at 10 m'),(0,0),(10,0),7.15)
        self.assertAlmostEqual(aimed['path_bearing_deg'],0)
        self.assertAlmostEqual(aimed['bearing_deg']%180,90,delta=1)
        reverse=aim_at_endpoint(defaults('40 m dipole at 10 m'),(10,0),(0,0),7.15)
        self.assertAlmostEqual(reverse['path_bearing_deg'],180)
        self.assertAlmostEqual(reverse['bearing_deg']%180,90,delta=1)
        self.assertTrue(aim_at_endpoint(defaults(),(0,0),(10,0),7.15)['omnidirectional'])
        with self.assertRaises(ValueError): aim_at_endpoint(defaults(),(0,0),(0,0),7.15)

    def test_aim_button_updates_bearing_without_running_propagation(self):
        from streamlit.testing.v1 import AppTest
        script="""from antenna_panel import antenna_builder
antenna_builder('A',(0,0),(10,0),7.15)
"""
        test=AppTest.from_string(script).run()
        next(s for s in test.selectbox if s.label=='A antenna preset').set_value('40 m dipole at 10 m').run()
        next(n for n in test.number_input if 'baseline bearing' in n.label).set_value(0.).run()
        with patch('requests.get') as network:
            next(b for b in test.button if b.label=='Aim A main lobe at the other station').click().run()
        self.assertFalse(test.exception)
        self.assertAlmostEqual(next(n for n in test.number_input if 'baseline bearing' in n.label).value % 180,90,delta=1)
        self.assertTrue(test.success)
        network.assert_not_called()


class CurrentConditions(unittest.TestCase):
    def test_scaled_grids_missing_cells_and_nonphysical_height(self):
        valid=datetime(2026,10,17,18,tzinfo=timezone.utc)
        grid=parse_irtam(fixture(valid))
        sampled=sample_irtam(grid,49,-8)
        self.assertEqual(sampled['foF2'],6)
        self.assertIsNone(sampled['Data_Confidence'])
        self.assertIsNone(sample_irtam(grid,50,8)['hmF2'])
        self.assertEqual(sample_irtam(grid,40,8)['hmF2'],400)
        self.assertIsNone(sample_irtam(grid,40,-8)['foF2'])
        bad=fixture(valid);bad['foF2'][0]=[]
        with self.assertRaises(ValueError):parse_irtam(bad)

    def test_absorption_only_marks_fresh_time_matched_bins_on_actual_date(self):
        valid=datetime(2026,10,17,18,tzinfo=timezone.utc)
        frame=pd.DataFrame([{'utc_hour':h,'frequency_mhz':f} for h in range(24) for f in [3.75,14.175]])
        context={'drap':{'time':valid.isoformat(),'fresh':True,'haf_mhz':7.}}
        marked=absorption_cells(frame,valid,context,valid+timedelta(minutes=5))
        self.assertEqual((marked.absorption_marker=='!').sum(),1)
        self.assertEqual(marked.loc[marked.absorption_marker=='!','utc_hour'].tolist(),[18])
        for target,now in [(valid-timedelta(days=1),valid),(valid,valid+timedelta(hours=2))]:
            self.assertFalse(absorption_cells(frame,target,context,now).absorption_marker.eq('!').any())
        self.assertNotIn('absorption_marker',frame.columns)

    def test_failed_refresh_keeps_valid_time_and_rate_limits_next_attempt(self):
        valid=datetime(2026,10,17,18,tzinfo=timezone.utc)
        saved={'payload':fixture(valid),'retrieved_at':(valid-timedelta(hours=1)).isoformat()}
        def read(name):return saved if name=='irtam' else None
        with patch('realtime_context.read_cache',side_effect=read),patch('realtime_context.write_cache') as write,patch('realtime_context.provider_get',side_effect=ValueError('unavailable')):
            result=load_irtam(valid)
        self.assertEqual(result['payload']['metadata']['Valid_Time'],'20261017T1800')
        self.assertIn('error',result)
        self.assertEqual(write.call_args.args[0],'irtam_failure')
        with patch('realtime_context.read_cache',side_effect=lambda n: saved if n=='irtam' else {'retrieved_at':valid.isoformat(),'error':'Unavailable'}),patch('realtime_context.provider_get') as get:
            load_irtam(valid)
            get.assert_not_called()


class Validation(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'history.sqlite3'
        self.actual=datetime.now(timezone.utc).replace(minute=0,second=0,microsecond=0)-timedelta(hours=2)
        self.result={'a':(61,-149),'b':(32,-97),'generated_at':self.actual-timedelta(hours=1),'target':self.actual,
                     'weather':{},'hf':{'table':pd.DataFrame([{'frequency_mhz':7.15,'utc_hour':self.actual.hour,'direction':'A→B','median_snr_db':-10.}]),
                                      'provenance':{'test':True}}}
        self.id=save_run(self.result,path=self.path)

    def test_snapshots_are_immutable_and_missing_reports_do_not_create_failures(self):
        self.result['hf']['table'].loc[0,'median_snr_db']=99
        self.assertEqual(runs(self.path)[0]['predictions'][0]['median_snr_db'],-10)
        self.assertEqual(observations(self.path),[])
        self.assertEqual(summary([])['matched_prospective_tests'],0)

    def test_only_matched_later_confirmed_snr_is_compared(self):
        data=save_observation(self.id,7.15,self.actual.hour,'A→B',self.actual,'Heard',-7.,True,path=self.path)
        self.assertTrue(data['snr_comparable']);self.assertEqual(data['error_db'],3)
        save_observation(self.id,7.15,self.actual.hour,'A→B',self.actual,'Attempted; no reception',setup_confirmed=True,path=self.path)
        stats=summary(observations(self.path))
        self.assertEqual(stats['matched_prospective_tests'],2)
        self.assertEqual(stats['heard'],1);self.assertEqual(stats['mean_absolute_error_db'],3)
        for time,confirmed in [(self.actual+timedelta(hours=1),True),(self.actual,False),(self.actual-timedelta(hours=2),True)]:
            data=save_observation(self.id,7.15,self.actual.hour,'A→B',time,'Heard',-7.,confirmed,path=self.path)
            self.assertFalse(data['snr_comparable'])

    def test_invalid_or_unattempted_records_are_rejected(self):
        for outcome,snr in [('Unknown',None),('Attempted; no reception',-10.),('Heard',float('nan'))]:
            with self.assertRaises(ValueError):save_observation(self.id,7.15,self.actual.hour,'A→B',self.actual,outcome,snr,path=self.path)
        with self.assertRaises(ValueError):save_observation(self.id,28.5,self.actual.hour,'A→B',self.actual,'Heard',path=self.path)
