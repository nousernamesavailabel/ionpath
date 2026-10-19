"""Public visitors must not share private settings or validation files."""
import os
import sys
import types
import unittest
from unittest.mock import patch

from runtime_paths import ROOT, private_path


class RuntimePaths(unittest.TestCase):
    def test_desktop_keeps_existing_path(self):
        with patch.dict(os.environ, {'IONPATH_PUBLIC': '0'}):
            self.assertEqual(private_path('transmitter_settings.json'), ROOT / 'transmitter_settings.json')

    def test_public_sessions_are_stable_and_separate(self):
        visitor_one = types.SimpleNamespace(session_state={})
        visitor_two = types.SimpleNamespace(session_state={})
        with patch.dict(os.environ, {'IONPATH_PUBLIC': '1'}):
            try:
                with patch.dict(sys.modules, {'streamlit': visitor_one}):
                    first = private_path('.cache/settings/antenna_A.json')
                    first.write_text('private station')
                    self.assertEqual(private_path('.cache/settings/antenna_A.json'), first)
                with patch.dict(sys.modules, {'streamlit': visitor_two}):
                    second = private_path('.cache/settings/antenna_A.json')
                    self.assertNotEqual(first, second)
                    self.assertFalse(second.exists())
                    self.assertTrue(second.parent.is_dir())
            finally:
                for visitor in (visitor_one, visitor_two):
                    visitor.session_state['_ionpath_private_directory'].cleanup()
