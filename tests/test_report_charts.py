from copy import deepcopy
import unittest

from model_heatmap import heatmap_data, heatmap_spec, snr_domain
from report_charts import report_heatmap_spec, target_snr_table
from test_run_report import example_snapshot


class BrowserReportParity(unittest.TestCase):
    def test_heatmap_reuses_browser_spec_numbers_order_and_domain(self):
        snapshot=example_snapshot();hf=snapshot['dashboard']['hf']
        frame,order=heatmap_data(hf['table'],hf['target'],snapshot['inputs']['timezone'])
        from realtime_context import absorption_cells, current_context
        frame=absorption_cells(frame,hf['target'],current_context(snapshot['dashboard'],now=snapshot['dashboard']['generated_at']),now=snapshot['dashboard']['generated_at'])
        spec=report_heatmap_spec(snapshot)
        self.assertEqual(len(spec['vconcat']),2)
        self.assertNotIn('hconcat',spec)
        for panel,direction in zip(spec['vconcat'],('A→B','B→A')):
            browser=heatmap_spec(frame,direction,order,'Local time',snapshot['inputs']['timezone'],snr_domain(hf['table']),numbers=True)
            self.assertEqual(panel['encoding'],browser['encoding'])
            browser['layer'][1]['encoding']['y2']={'value':panel['height']}
            # The saved-run report adds the same D-RAP marker layer as the app.
            self.assertEqual(panel['layer'][0:3],browser['layer'][0:3])
            self.assertEqual(panel['layer'][-1],browser['layer'][-1])
            self.assertEqual(panel['encoding']['y']['sort'][0],'28.5 MHz')
            self.assertEqual(panel['encoding']['x']['sort'],order)
            self.assertEqual(panel['layer'][-1]['encoding']['text']['format'],'.0f')
    def test_selected_direction_clock_and_dst_bins_are_preserved(self):
        snapshot=example_snapshot()
        spec=report_heatmap_spec(snapshot,{'clock':'UTC','view':'Weaker direction'})
        self.assertEqual(len(spec['vconcat']),1)
        self.assertEqual(spec['vconcat'][0]['title']['text'],'Weaker direction')
        self.assertEqual(spec['vconcat'][0]['encoding']['x']['sort'],list(range(24)))
        spec=report_heatmap_spec(snapshot,{'clock':'Local time','view':'B→A'})
        self.assertEqual(spec['vconcat'][0]['title']['text'],'B→A')
    def test_target_table_columns_values_order_and_colors_match_browser(self):
        from dashboard import model_overview
        from propagation_panel import snr_table_styles
        snapshot=example_snapshot();hf=snapshot['dashboard']['hf']
        browser=model_overview(hf)
        printed=target_snr_table(hf,500)
        self.assertEqual([p.getPlainText() for p in printed._cellvalues[0]],['MHz',*browser.columns])
        self.assertEqual(printed._cellvalues[1][0].getPlainText(),f'{browser.index[0]:g}')
        self.assertEqual(printed._cellvalues[1][1].getPlainText(),f'{browser.iloc[0,0]:.1f}')
        expected=snr_table_styles(browser).iloc[0,0].split('background-color: ')[1].split(';')[0]
        from reportlab.lib.colors import HexColor
        actual=next(c[-1] for c in printed._bkgrndcmds if c[1]==(1,1))
        self.assertEqual(actual,HexColor(expected))
    def test_export_follows_display_controls_without_new_analysis(self):
        from streamlit.testing.v1 import AppTest
        from unittest.mock import patch
        script="""import streamlit as st
from run_report import render_report_export
from model_heatmap import render_model_heatmap
render_report_export(st.session_state)
render_model_heatmap(st.session_state['run_report_snapshot']['dashboard']['hf'],'America/Anchorage')
"""
        test=AppTest.from_string(script,default_timeout=40)
        test.session_state['run_report_snapshot']=example_snapshot()
        with patch('requests.get') as network:
            test.run()
            next(b for b in test.button if b.label=='Prepare run PDF').click().run()
            self.assertFalse(test.exception,test.exception)
            self.assertFalse(test.error,test.error)
            self.assertTrue(test.session_state['run_report_pdf'].startswith(b'%PDF-'))
            next(r for r in test.radio if r.label=='Heatmap clock').set_value('UTC').run()
            self.assertNotIn('run_report_pdf',test.session_state)
            next(s for s in test.selectbox if s.label=='Heatmap direction').set_value('Weaker direction').run()
            next(b for b in test.button if b.label=='Prepare run PDF').click().run()
            self.assertFalse(test.exception,test.exception)
            self.assertFalse(test.error,test.error)
            self.assertEqual(test.session_state['run_report_heatmap_options'],{'clock':'UTC','view':'Weaker direction'})
            network.assert_not_called()

    def test_missing_snr_is_gray_in_both_browser_and_print(self):
        from propagation_panel import colored_snr_table_html
        from dashboard import model_overview
        snapshot=example_snapshot();hf=snapshot['dashboard']['hf']
        hf['table'].loc[hf['table'].direction.eq('B→A'),'median_snr_db']=float('nan')
        html=colored_snr_table_html(model_overview(hf))
        self.assertIn('#d1d5db',html)
        self.assertIn('—',html)
        self.assertIsNotNone(target_snr_table(hf,500))
