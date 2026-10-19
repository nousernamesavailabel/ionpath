import ast
import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import shutil

import numpy as np
import pandas as pd
from streamlit.testing.v1 import AppTest

from planning import activity_heatmap_data, origin_history, filter_origins, coverage_flags, hourly_evidence, hour_label
from map_selection import parse_areas, build_map, spatial_predicate

ROOT = Path(__file__).resolve().parents[1]
# Load application calculations without executing the Streamlit interface.
namespace = {}
tree = ast.parse((ROOT / 'app.py').read_text())
nodes = []
for node in tree.body:
    if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Attribute) and node.value.func.attr == 'title':
        break
    if isinstance(node, (ast.Import, ast.ImportFrom, ast.Assign, ast.FunctionDef)):
        nodes.append(node)
namespace['__file__'] = str(ROOT / 'app.py')
exec(compile(ast.Module(body=nodes, type_ignores=[]), str(ROOT / 'app.py'), 'exec'), namespace)


def reports():
    rows = []
    for day in range(1, 5):
        for band in [7, 14, 28]:
            for hour in [6, 7, 18]:
                rows.append(dict(time=f'2026-10-{day:02d} {hour:02d}:00:00', band=band,
                                 rx_sign=f'RX{day}', rx_loc='EM12', rx_lat=32.5, rx_lon=-97,
                                 tx_sign='TEST123', tx_lat=61.2, tx_lon=-149.9, tx_loc='BP51',
                                 snr=-10, power=0, code=1))
    rows[0].update(tx_loc='EM12', tx_lat=32.5, tx_lon=-97)
    rows[1].update(tx_loc='', tx_lat=None, tx_lon=None)
    return pd.DataFrame(rows)


class Calculations(unittest.TestCase):
    def test_wspr_history_reuses_reports_and_invalidates_query_changes(self):
        state={}
        args=[(61.5,-149),50,(32.5,-97),600,90,10000,'Both directions','AK']
        from unittest.mock import Mock
        fetch=Mock(side_effect=lambda *args: reports())
        with patch.dict(namespace,fetch_path=fetch):
            first=namespace['load_wspr_history'](state,*args)
            first.iloc[0,first.columns.get_loc('snr')]=999
            reused=namespace['load_wspr_history'](state,*args)
            self.assertEqual(fetch.call_count,1)
            self.assertNotEqual(reused.iloc[0].snr,999)
            for index,value in enumerate([(62,-149),60,(33,-97),650,120,25000,'A → B','OTHER']):
                args[index]=value
                namespace['load_wspr_history'](state,*args)
                self.assertEqual(fetch.call_count,index+2)

    def test_wspr_explicit_refresh_and_failed_refresh_preserve_timestamp(self):
        from unittest.mock import Mock
        state={}
        args=[(61.5,-149),50,(32.5,-97),600,90,10000,'Both directions','AK']
        fetch=Mock(return_value=reports())
        with patch.dict(namespace,fetch_path=fetch):
            namespace['load_wspr_history'](state,*args)
            first=state['wspr_history']
            namespace['load_wspr_history'](state,*args,refresh=True)
            self.assertEqual(fetch.call_count,2)
            self.assertIsNot(state['wspr_history'],first)
            current=state['wspr_history']
            fetch.side_effect=RuntimeError('Provider unavailable')
            with self.assertRaises(RuntimeError):
                namespace['load_wspr_history'](state,*args,refresh=True)
            self.assertIs(state['wspr_history'],current)

    def test_heatmap_local_first_labels_order_and_utc_data_alignment(self):
        from datetime import date
        hourly=pd.DataFrame([{'band':14,'utc_hour':1,'weighted_reports':12.5,'reports':15,'days':2,'receivers':3}])
        activity,order=activity_heatmap_data(hourly,{14:'14 MHz (20m)'},date(2026,10,16),'America/Anchorage')
        self.assertEqual(order,list(range(8,24))+list(range(8)))
        row=activity[activity.utc_hour.eq(1)].iloc[0]
        self.assertEqual(row.Hour,'17|(01)')
        self.assertEqual(row['Weighted reports'],12.5)
        self.assertIn('2026-10-15 17:00',row['Local time'])
        self.assertIn('2026-10-16 01:00 UTC',row['UTC time'])
        self.assertEqual(len(activity),24)
        fractional,_=activity_heatmap_data(hourly,{14:'14 MHz (20m)'},date(2026,10,16),'Asia/Kolkata')
        self.assertEqual(fractional[fractional.utc_hour.eq(1)].iloc[0].Hour,'06:30|(01)')

    def test_generated_circle_sql_executes_and_filters_centers(self):
        import sqlite3
        with sqlite3.connect(':memory:') as database:
            for name,function,args in [('pi',lambda: math.pi,0),('sin',math.sin,1),('cos',math.cos,1),('asin',math.asin,1),('sqrt',math.sqrt,1),('pow',pow,2),('least',min,2),('greatest',max,2)]:
                database.create_function(name,args,function)
            database.execute('CREATE TABLE origins (name TEXT, tx_lat REAL, tx_lon REAL)')
            database.executemany('INSERT INTO origins VALUES (?,?,?)',[
                ('center',61.5,-149),('near',61.6,-149),('far',62,-149),
                ('date_line',0,-179.9),('pole',89.9,120)])
            def matches(lat,lon,radius):
                return {row[0] for row in database.execute('SELECT name FROM origins WHERE '+spatial_predicate(lat,lon,radius))}
            self.assertEqual(matches(61.5,-149,50),{'center','near'})
            self.assertEqual(matches(0,179.9,50),{'date_line'})
            self.assertEqual(matches(90,0,50),{'pole'})

    def test_server_error_exposes_explanation_without_request_url(self):
        import requests
        response=requests.Response()
        response.status_code=400
        response.url='https://db1.wspr.live/?query=LONG_SQL'
        response._content=b'Code: 62. Syntax error: Unmatched parentheses.'
        with self.assertRaises(ValueError) as caught:
            namespace['check_wspr_response'](response)
        self.assertIn('HTTP 400',str(caught.exception))
        self.assertIn('Unmatched parentheses',str(caught.exception))
        self.assertNotIn('LONG_SQL',str(caught.exception))

    def test_ft8_score_has_no_power_or_snr_factor(self):
        frame=namespace['normalize'](reports(),'TEST123')
        frame['mode']='FT8'
        first,matched=namespace['analyze'](frame,32.5,-97,6,600,2,183,'FT8')
        frame['power']=60
        frame['snr']=30
        second,_=namespace['analyze'](frame,32.5,-97,6,600,2,183,'FT8')
        np.testing.assert_allclose(first['Evidence score'],second['Evidence score'])
        self.assertTrue(matched.wspr_snr_1w.isna().all())
        self.assertEqual(first[first.Band.eq(namespace['BANDS'][14])].iloc[0]['Frequency (MHz)'],14.074)

    def test_map_payloads_and_spatial_queries(self):
        features=[{'type':'Feature','geometry':{'type':'Point','coordinates':[181,60]},'properties':{'role':'tx','radius':50000}},
                  {'type':'Feature','geometry':{'type':'Point','coordinates':[-97,32]},'properties':{'role':'destination','radius':600000}}]
        areas=parse_areas(features)
        self.assertEqual(areas['tx']['center'],[60,-179])
        self.assertEqual(areas['destination']['radius_km'],600)
        html=build_map(areas).get_root().render()
        self.assertIn("'dragend'",html)
        self.assertIn("circle.on('mousedown'",html)
        self.assertIn('nearOutline(circle,e.latlng)',html)
        self.assertIn("document.addEventListener('mouseup',finishResize)",html)
        self.assertIn('map.dragging.disable()',html)
        self.assertIn('getRadius()',html)
        self.assertNotIn('sessionStorage',html)
        self.assertIn('pan:false,animate:false',html)
        self.assertIn('featureGroup',html)
        self.assertNotIn('.fitBounds(',html)
        self.assertNotIn('.setView(',html)
        self.assertIn(' OR ',spatial_predicate(0,179.9,100))
        self.assertNotIn('tx_lon BETWEEN',spatial_predicate(89,0,500))
        with self.assertRaises(ValueError):
            spatial_predicate(0,0,float('inf'))
        features[0]['properties']['radius']=0
        with self.assertRaises(ValueError):
            parse_areas(features)

    def test_fixed_wspr_normalization_and_unknown_power(self):
        df=namespace['normalize'](reports().iloc[:5].copy(),'TEST123')
        df['power']=[23,30,37,np.nan,np.inf]
        df['snr']=-20
        scores,matched=namespace['analyze'](df,32.5,-97,6,600,12,183)
        np.testing.assert_allclose(matched.wspr_snr_1w.iloc[:3],[-13,-20,-27])
        self.assertTrue(matched.wspr_snr_1w.iloc[3:].isna().all())
        self.assertTrue(matched.normalization_status.iloc[3:].eq('Cannot normalize').all())
        self.assertEqual(scores['Cannot normalize'].sum(),2)
        unknown=df.copy()
        unknown['power']=np.nan
        unknown_scores,unknown_matched=namespace['analyze'](unknown,32.5,-97,6,600,12,183)
        self.assertTrue(unknown_scores['Median WSPR SNR at 1 W (dB)'].isna().all())
        self.assertTrue(np.isfinite(unknown_scores['Evidence score']).all())
        # Unknown-power raw SNR must not leak into the strength factor.
        unknown['snr']=20
        other,_=namespace['analyze'](unknown,32.5,-97,6,600,12,183)
        np.testing.assert_allclose(other['Evidence score'],unknown_scores['Evidence score'])

    def test_origins_unknown_grid_overlap_and_coordinate_fallback(self):
        df=pd.DataFrame([
            {'tx_loc':'', 'tx_lat':0., 'tx_lon':0.},
            {'tx_loc':'', 'tx_lat':0., 'tx_lon':10.},
            {'tx_loc':'', 'tx_lat':999., 'tx_lon':0.},
            {'tx_loc':'JJ00', 'tx_lat':0., 'tx_lon':0.},
        ])
        history=origin_history(df,0,0,50,0,namespace['grid_coords'],namespace['haversine'])
        self.assertEqual(history.origin_status.tolist(), ['Within radius','Outside radius','Unknown','Boundary uncertain'])
        self.assertEqual(len(filter_origins(history,False,False)),4)
        self.assertEqual(len(filter_origins(history,True,False)),2)
        self.assertEqual(len(filter_origins(history,True,True)),3)
        empty=origin_history(df.iloc[:0],0,0,50,0,namespace['grid_coords'],namespace['haversine'])
        self.assertTrue(empty.empty)

    def test_coverage_and_hourly_counts(self):
        df=namespace['normalize'](reports(),'TEST123')
        full,hourly=hourly_evidence(df,32.5,-97,600,600,namespace['haversine'])
        cell=hourly[(hourly.band==14)&(hourly.utc_hour==6)].iloc[0]
        self.assertEqual(cell.reports,4)
        self.assertEqual(cell.days,4)
        self.assertEqual(cell.receivers,4)
        self.assertAlmostEqual(cell.weighted_reports,4)
        flags=coverage_flags(full.iloc[:1])
        self.assertTrue(any('one receiver' in flag for flag in flags))
        self.assertTrue(any('1 UTC day' in flag for flag in flags))
        scores,matched=namespace['analyze'](df,32.5,-97,6,600,2,183)
        self.assertEqual(len(scores),3)
        self.assertTrue((matched.hour_diff<=2).all())

    def test_snr_ranking_beats_volume_and_preserves_direction(self):
        frame=namespace['normalize'](reports())
        frame['path_direction']='A → B'
        frame['b_lat']=frame.rx_lat
        frame['b_lon']=frame.rx_lon
        frame.loc[frame.band.eq(7),'snr']=-25
        frame.loc[frame.band.eq(14),'snr']=-15
        frame.loc[frame.band.eq(28),'snr']=-5
        # Many weak reports must not outrank fewer strong reports.
        frame=pd.concat([frame, pd.concat([frame[frame.band.eq(7)]]*20)],ignore_index=True)
        frame.loc[frame.band.eq(28),'path_direction']='B → A'
        scores,matched=namespace['analyze'](frame,32.5,-97,6,600,12,183)
        self.assertEqual(scores.iloc[0]['Band'],namespace['BANDS'][28])
        self.assertTrue(pd.isna(scores.iloc[0]['A → B median SNR (dB)']))
        self.assertEqual(scores.iloc[0]['B → A median SNR (dB)'],25)
        self.assertEqual(scores.iloc[0]['B → A measured SNR (dB)'],-5)
        no_power=frame.copy();no_power.loc[no_power.band.eq(28),'power']=np.nan
        unknown,_=namespace['analyze'](no_power,32.5,-97,6,600,12,183)
        self.assertEqual(unknown.iloc[-1]['Band'],namespace['BANDS'][28])
        self.assertTrue(pd.isna(unknown.iloc[-1]['Ranking SNR (dB)']))

    def test_timezone_date_rollover_and_dst(self):
        from datetime import date
        winter=hour_label(6,date(2026,1,15),'America/Anchorage')
        summer=hour_label(6,date(2026,7,15),'America/Anchorage')
        self.assertIn('06:00–06:59 UTC',winter)
        self.assertIn('2026-01-14 21:00',winter)
        self.assertIn('-0900',winter)
        self.assertIn('-0800',summer)


class Interface(unittest.TestCase):
    def setUp(self):
        import tempfile
        from contextlib import ExitStack
        self.context = ExitStack()
        self.addCleanup(self.context.close)
        temporary = self.context.enter_context(tempfile.TemporaryDirectory())
        self.context.enter_context(patch('validation_log.DB', Path(temporary) / 'history.sqlite3'))
        self.context.enter_context(patch('antenna_defaults.SETTINGS', Path(temporary) / 'antenna_settings'))
        self.context.enter_context(patch('realtime_context.load_irtam', return_value={'error': 'Offline test'}))

        from datetime import datetime, timezone
        def context(a, b, target, settings):
            return {'hf': None, 'iono': None, 'weather': {}, 'errors': {'hf': 'Not loaded in reception test', 'iono': 'Not loaded in reception test'},
                    'target': target, 'a': a, 'b': b, 'generated_at': datetime.now(timezone.utc)}
        self.layers = patch('dashboard.run_context_layers', side_effect=context)
        self.layers.start()
        self.addCleanup(self.layers.stop)
    def test_history_refresh_ui_and_diagnostic_preview(self):
        class Response:
            text=reports().to_csv(index=False)
            def raise_for_status(self): pass
        with tempfile.TemporaryDirectory() as folder:
            app=Path(folder)/'app.py'
            shutil.copyfile(ROOT/'app.py',app)
            with patch('requests.get',return_value=Response()) as network:
                test=AppTest.from_file(str(app),default_timeout=30).run()
                next(b for b in test.button if b.label=='Analyze propagation').click().run()
                self.assertFalse(test.exception,test.exception)
                initial=network.call_count
                stamp=test.session_state['wspr_history']['fetched_at']
                next(sl for sl in test.slider if sl.label=='Target hour (local time)').set_value(18).run()
                next(b for b in test.button if b.label=='Analyze propagation').click().run()
                self.assertEqual(network.call_count,initial)
                self.assertEqual(test.session_state['wspr_history']['fetched_at'],stamp)
                next(b for b in test.button if b.label=='Refresh WSPR history').click().run()
                self.assertFalse(test.exception,test.exception)
                self.assertEqual(network.call_count,initial+2)
                self.assertGreater(test.session_state['wspr_history']['fetched_at'],stamp)
                self.assertTrue(any('WSPR history fetched' in c.value for c in test.caption))
                self.assertTrue(test.checkbox(key='diagnostic_identity').value)
                self.assertTrue(test.json)
                before=network.call_count
                next(b for b in test.button if b.label=='Prepare run PDF').click().run()
                self.assertFalse(test.exception,test.exception)
                self.assertEqual(network.call_count,before)
                self.assertTrue(test.session_state['run_report_pdf'].startswith(b'%PDF-'))
                saved=test.session_state['run_report_snapshot']['inputs']['target_utc']
                next(sl for sl in test.slider if sl.label=='Target hour (local time)').set_value(10).run()
                self.assertEqual(test.session_state['run_report_snapshot']['inputs']['target_utc'],saved)

    def test_ft8_source_uses_local_cache_and_separate_activity_labels(self):
        frame=reports()
        frame['time']=pd.to_datetime(frame.time,utc=True).map(lambda t:t.replace(year=2024,month=5,day=12))
        frame['mode']='FT8'
        frame['power']=np.nan
        stats={'date':'2024-05-12','returned_rows':len(frame),'limited':False,'timestamp_offsets_seconds':[18000]}
        with tempfile.TemporaryDirectory() as folder:
            app=Path(folder)/'app.py'
            shutil.copyfile(ROOT/'app.py',app)
            with patch('ft8_archive.read_archive',return_value=(frame,stats)) as archive, patch('requests.get') as network:
                test=AppTest.from_file(str(app),default_timeout=20).run()
                test.radio(key='data_source').set_value('FT8 archive (cached day)').run()
                next(sl for sl in test.slider if sl.label=='Target hour (local time)').set_value(22)
                next(b for b in test.button if b.label=='Analyze propagation').click().run()
                self.assertFalse(test.exception,test.exception)
                self.assertFalse(test.error,test.error)
                self.assertEqual(archive.call_count,1)
                self.assertEqual(network.call_count,0)
                self.assertTrue(any('FT8' in h.value for h in test.subheader))
                self.assertTrue(any('FT8 activity score' in table.value.columns for table in test.dataframe))
                self.assertTrue(any('Archive time discrepancy' in w.value for w in test.warning))

    def test_visible_radius_controls_resize_without_moving_centers(self):
        with tempfile.TemporaryDirectory() as folder:
            app=Path(folder)/'app.py'
            shutil.copyfile(ROOT/'app.py',app)
            test=AppTest.from_file(str(app)).run()
            centers={role:area['center'][:] for role,area in test.session_state['map_areas'].items()}
            initial_areas=test.session_state['map_initial_areas']
            test.number_input(key='tx_area_radius').set_value(125.0).run()
            test.number_input(key='destination_area_radius').set_value(300.0).run()
            self.assertFalse(test.exception,test.exception)
            areas=test.session_state['map_areas']
            self.assertEqual(areas['tx']['radius_km'],125.0)
            self.assertEqual(areas['destination']['radius_km'],300.0)
            self.assertEqual({role:area['center'] for role,area in areas.items()},centers)
            self.assertEqual(test.session_state['map_initial_areas'],initial_areas)
            next(b for b in test.button if b.label=='Apply coordinates to map').click().run()
            self.assertEqual(test.session_state['map_areas']['tx']['radius_km'],125.0)
            self.assertEqual(test.session_state['map_areas']['destination']['radius_km'],300.0)

    def test_map_updates_do_not_recreate_or_move_the_map(self):
        from streamlit_folium import generate_js_hash, _get_map_string
        hashes=[]
        parameters=[]
        locations=[]
        zooms=[]
        views=[{'zoom':8,'center':{'lat':61.2,'lng':-149.9}},
               {'zoom':10,'center':{'lat':61.3,'lng':-150.0}},
               {'zoom':10,'center':{'lat':61.3,'lng':-150.0}}]
        def render_map(figure,**kwargs):
            locations.append(figure.location)
            zooms.append(figure.options['zoom'])
            figure.get_root().render()
            hashes.append(generate_js_hash(_get_map_string(figure),kwargs['key']))
            parameters.append(kwargs['returned_objects'])
            self.assertNotIn('zoom',kwargs)
            self.assertNotIn('center',kwargs)
            return views[len(hashes)-1]
        with tempfile.TemporaryDirectory() as folder:
            app=Path(folder)/'app.py'
            shutil.copyfile(ROOT/'app.py',app)
            with patch('streamlit_folium.st_folium',side_effect=render_map):
                test=AppTest.from_file(str(app)).run()
                test.run()
                test.run()
                self.assertFalse(test.exception,test.exception)
                self.assertEqual(len(set(hashes)),1)
                self.assertEqual(locations,[[45,-120]]*3)
                self.assertEqual(zooms,[3]*3)
                self.assertEqual(parameters,[['all_drawings']]*3)
                self.assertNotIn('map_view',test.session_state)

    def test_region_uses_multiple_transmitters_and_excludes_outside_unknown(self):
        frame=reports()
        frame.loc[2:15,'tx_sign']='OTHER123'
        class Response:
            text=frame.to_csv(index=False)
            def raise_for_status(self):
                pass
        with tempfile.TemporaryDirectory() as folder:
            app=Path(folder)/'app.py'
            shutil.copyfile(ROOT/'app.py',app)
            with patch('requests.get',return_value=Response()) as request:
                namespace['fetch_region'].clear()
                test=AppTest.from_file(str(app),default_timeout=20).run()
                next(sl for sl in test.slider if sl.label=='Target hour (local time)').set_value(22)
                next(b for b in test.button if b.label=='Analyze propagation').click().run()
                self.assertFalse(test.exception,test.exception)
                self.assertFalse(test.error,test.error)
                retained=test.session_state['results_mgrs'][2]
                self.assertEqual(retained.tx_sign.nunique(),2)
                self.assertEqual(len(retained),34)
                self.assertIn('A → B SNR / reports',next(t.value for t in test.dataframe if 'Priority' in t.value.columns).columns)
                sql=request.call_args.kwargs['params']['query']
                self.assertNotIn('tx_sign =',sql)
                self.assertIn('tx_lat BETWEEN',sql)
                self.assertIn('band IN',sql)
                self.assertIn('LIMIT 50000',sql)

    def test_operator_summary_filters_and_no_target_hour_matches(self):
        class Response:
            text=reports().to_csv(index=False)
            def raise_for_status(self):
                pass
        with tempfile.TemporaryDirectory() as folder:
            app=Path(folder)/'app.py'
            shutil.copyfile(ROOT/'app.py',app)
            with patch('requests.get',return_value=Response()):
                test=AppTest.from_file(str(app),default_timeout=20).run()
                test.radio(key='selection_scope').set_value('Selected callsign')
                test.text_input(key='tx_callsign').set_value('TEST123')
                next(sl for sl in test.slider if sl.label=='Target hour (local time)').set_value(22)
                next(b for b in test.button if b.label=='Analyze propagation').click().run()
                self.assertFalse(test.exception, test.exception)
                self.assertFalse(test.error, test.error)
                self.assertEqual(next(t.value for t in test.dataframe if 'Priority' in t.value.columns).iloc[0]['Priority'],'Try first')
                self.assertIn('Busiest day',next(t.value for t in test.dataframe if 'Priority' in t.value.columns).columns)
                self.assertTrue(any('Historical reception activity' in element.value for element in test.subheader))
                self.assertGreater(len(test.dataframe),3)
                test.selectbox(key='inspect_band').set_value(namespace['BANDS'][14]).run()
                test.selectbox(key='inspect_hour').set_value(6).run()
                self.assertFalse(test.exception, test.exception)
                self.assertTrue(any('4 actual reports' in m.value for m in test.markdown))
                test.radio(key='path_direction').set_value('A → B').run()
                self.assertTrue(any('Controls have changed' in w.value for w in test.warning))
                next(b for b in test.button if b.label=='Analyze propagation').click().run()
                self.assertFalse(test.exception, test.exception)
                context=test.session_state['analysis_context']
                self.assertEqual(context['utc_target'].hour,6)
                self.assertEqual(context['local_target'].hour,22)
                self.assertEqual(len(context['history']),34)
                self.assertEqual(len(test.session_state['results_mgrs'][2]),34)
                next(s for s in test.slider if s.label=='Target hour (local time)').set_value(4).run()
                next(b for b in test.button if b.label=='Analyze propagation').click().run()
                self.assertFalse(test.exception, test.exception)
                self.assertTrue(test.session_state['results_mgrs'][0].empty)
                self.assertTrue(any('Inspect an hour' in element.value for element in test.subheader))
                # Exclude every report by selecting a far-away origin.
                test.selectbox(key='tx_format').set_value('Latitude / longitude').run()
                test.text_input(key='tx_location_Latitude / longitude').set_value('0, 0').run()
                next(b for b in test.button if b.label=='Apply coordinates to map').click().run()
                next(b for b in test.button if b.label=='Analyze propagation').click().run()
                self.assertFalse(test.exception, test.exception)
                self.assertNotIn('results_mgrs',test.session_state)
                self.assertTrue(any('Reception evidence unavailable' in w.value for w in test.warning))
                test.text_input(key='tx_callsign').set_value('').run()
                next(b for b in test.button if b.label=='Analyze propagation').click().run()
                self.assertTrue(any('Reception evidence unavailable' in w.value for w in test.warning))
                self.assertNotIn('results_mgrs',test.session_state)

    def test_online_fetch_limits_each_direction_and_filters_A_callsign_in_correct_role(self):
        from datetime import datetime,timezone,timedelta
        class Response:
            text=reports().to_csv(index=False)
            def raise_for_status(self):pass
        namespace['fetch_path'].clear()
        with patch('requests.get',return_value=Response()) as request:
            end=datetime(2026,10,16,tzinfo=timezone.utc)
            namespace['fetch_path']((61.5,-149),50,(32.5,-97),600,end-timedelta(days=1),end,10000,'Both directions','AK')
            self.assertEqual(request.call_count,2)
            queries=[call.kwargs['params']['query'] for call in request.call_args_list]
            self.assertIn("tx_sign = 'AK'",queries[0])
            self.assertIn("rx_sign = 'AK'",queries[1])
            for query in queries:
                self.assertIn('rx_lat BETWEEN',query)
                self.assertIn('tx_lat BETWEEN',query)
                self.assertIn('LIMIT 10000',query)

    def test_observed_controls_have_no_voice_power_or_threshold(self):
        with tempfile.TemporaryDirectory() as folder:
            app=Path(folder)/'app.py'
            shutil.copyfile(ROOT/'app.py',app)
            # Obsolete settings must not influence the new interface or ranking.
            (app.parent/'power_settings.json').write_text('{"dbm": 56}')
            test=AppTest.from_file(str(app)).run()
            self.assertFalse(test.exception,test.exception)
            self.assertTrue(any(sl.label=='Target hour (local time)' for sl in test.main.slider))
            self.assertTrue(any(b.label=='Analyze propagation' for b in test.main.button))
            self.assertFalse(any('threshold' in n.label.lower() for n in test.number_input))
            # The independent P.533 form has a power assumption; observed ranking does not.
            power_inputs=[n for n in test.number_input if 'power' in n.label.lower()]
            self.assertEqual([n.label for n in power_inputs], ['Transmit power in each direction (W)'])
            self.assertEqual(power_inputs[0].value, 100.0)

    def test_download_limit_detected_before_origin_filtering(self):
        frame=pd.concat([reports()]*278,ignore_index=True).head(10000)
        frame['tx_sign']='TESTLIMIT'
        class Response:
            text=frame.to_csv(index=False)
            def raise_for_status(self):
                pass
        with tempfile.TemporaryDirectory() as folder:
            app=Path(folder)/'app.py'
            shutil.copyfile(ROOT/'app.py',app)
            with patch('requests.get',return_value=Response()):
                test=AppTest.from_file(str(app),default_timeout=30).run()
                test.radio(key='selection_scope').set_value('Selected callsign')
                test.text_input(key='tx_callsign').set_value('TESTLIMIT')
                next(sl for sl in test.slider if sl.label=='Target hour (local time)').set_value(22)
                next(s for s in test.select_slider if s.label=='Maximum rows per online direction / cached archive').set_value(10000)
                test.radio(key='path_direction').set_value('A → B').run()
                next(b for b in test.button if b.label=='Analyze propagation').click().run()
                self.assertFalse(test.exception, test.exception)
                self.assertTrue(test.session_state['analysis_context']['row_limited'])
                self.assertLess(len(test.session_state['results_mgrs'][2]),10000)
                self.assertTrue(any('Row limit reached' in w.value for w in test.warning))


if __name__=='__main__':
    unittest.main()
