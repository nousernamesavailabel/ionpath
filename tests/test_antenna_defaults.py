from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest
import antenna_defaults
from antennas import defaults

SCRIPT="""from antenna_panel import antenna_builder
import streamlit as st
st.session_state['antenna_config_a']=antenna_builder('A',(0,0),(10,0),7.15)
st.session_state['antenna_config_b']=antenna_builder('B',(10,0),(0,0),7.15)
"""


class PersistentAntennas(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory();self.addCleanup(self.folder.cleanup)
        self.patch=patch('antenna_defaults.SETTINGS',Path(self.folder.name))
        self.patch.start();self.addCleanup(self.patch.stop)

    def test_each_station_and_all_advanced_fields_survive_new_session(self):
        c=defaults('40 m vertical delta loop') | {'height_m':8.5,'length_m':55.,'bearing_deg':234.,'shape':'Rectangle',
                                                 'aspect':3.5,'radius_mm':2.2,'ground':'Poor','loss_db':4.5}
        antenna_defaults.save('A','Custom',c)
        antenna_defaults.save('B','20 m elevated vertical',defaults('20 m elevated vertical') | {'height_m':4.})
        test=AppTest.from_string(SCRIPT).run()
        self.assertFalse(test.exception)
        self.assertEqual(test.session_state['antenna_config_a'],c)
        self.assertEqual(test.session_state['antenna_config_b']['kind'],'Vertical + 4 elevated radials')
        self.assertEqual(test.session_state['antenna_config_b']['height_m'],4.)
        next(n for n in test.number_input if n.label=='A height above ground (m)').set_value(12.5).run()
        self.assertFalse(test.exception)
        fresh=AppTest.from_string(SCRIPT).run()
        self.assertEqual(fresh.session_state['antenna_config_a']['height_m'],12.5)
        self.assertEqual(fresh.session_state['antenna_config_a']['ground'],'Poor')
        self.assertEqual(fresh.session_state['antenna_config_b']['height_m'],4.)

    def test_aimed_bearing_is_saved_and_restored(self):
        antenna_defaults.save('A','40 m dipole at 10 m',defaults('40 m dipole at 10 m') | {'bearing_deg':0.})
        test=AppTest.from_string(SCRIPT).run()
        with patch('antennas.aim_at_endpoint',return_value={'bearing_deg':123.45,'path_bearing_deg':33.45,'omnidirectional':False}):
            next(b for b in test.button if b.label=='Aim A main lobe at the other station').click().run()
        self.assertFalse(test.exception)
        self.assertEqual(antenna_defaults.load('A')['config']['bearing_deg'],123.45)
        fresh=AppTest.from_string(SCRIPT).run()
        self.assertEqual(fresh.session_state['antenna_config_a']['bearing_deg'],123.45)

    def test_explicit_isotropic_change_replaces_previous_default(self):
        antenna_defaults.save('A','40 m dipole at 10 m',defaults('40 m dipole at 10 m'))
        test=AppTest.from_string(SCRIPT).run()
        next(s for s in test.selectbox if s.label=='A antenna preset').set_value('Isotropic reference').run()
        fresh=AppTest.from_string(SCRIPT).run()
        self.assertFalse(fresh.exception)
        self.assertEqual(fresh.session_state['antenna_config_a']['kind'],'Isotropic reference')
        with patch('antenna_defaults.save') as save:
            fresh.run()
            save.assert_not_called()

    def test_bad_saved_file_is_retained_until_manual_change(self):
        path=antenna_defaults.settings_path('A');path.write_text('broken JSON')
        test=AppTest.from_string(SCRIPT).run()
        self.assertFalse(test.exception)
        self.assertTrue(any('Could not load A antenna default' in w.value for w in test.warning))
        self.assertEqual(path.read_text(),'broken JSON')
        next(s for s in test.selectbox if s.label=='A antenna preset').set_value('40 m dipole at 10 m').run()
        self.assertEqual(antenna_defaults.load('A')['config']['kind'],'Dipole')

    def test_write_failure_is_visible_and_does_not_destroy_last_default(self):
        previous=antenna_defaults.save('A','40 m dipole at 10 m',defaults('40 m dipole at 10 m'))
        test=AppTest.from_string(SCRIPT).run()
        with patch('antenna_defaults.save',side_effect=OSError('read-only')):
            next(n for n in test.number_input if n.label=='A height above ground (m)').set_value(15.).run()
        self.assertFalse(test.exception)
        self.assertTrue(any('could not be saved' in w.value for w in test.warning))
        self.assertEqual(antenna_defaults.load('A'),previous)
        self.assertEqual(test.session_state['antenna_config_a']['height_m'],15.)
