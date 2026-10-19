from datetime import datetime, timezone
import unittest
from beta_diagnostics import APP_VERSION, build_diagnostics


class Diagnostics(unittest.TestCase):
    def test_bundle_has_provenance_without_bulk_or_private_files(self):
        state={'path_dashboard':{'generated_at':datetime.now(timezone.utc),
                                'weather':{'feed':{'retrieved_at':'stamp','payload':['bulk']}},
                                'iono':{'stations':[{'rows':['bulk'],'retrieved_at':'stamp'}]},
                                'errors':{'hf':'error at 61,-149'}},
               'diagnostic_run_inputs':{'a':{'center':[61,-149]},'callsign':'TEST'},
               'wspr_history':{'raw':'bulk','fetched_at':'stamp'}}
        bundle=build_diagnostics(state,{'a':[61,-149],'b':[32,-97],'callsign':'TEST'})
        self.assertEqual(bundle['app_version'],APP_VERSION)
        self.assertEqual(bundle['current_inputs']['callsign'],'TEST')
        self.assertNotIn('bulk',str(bundle))
        self.assertEqual(bundle['source_metadata']['weather']['feed']['retrieved_at'],'stamp')
        private=build_diagnostics(state,{'a':[61,-149],'callsign':'TEST'},False)
        self.assertNotIn('TEST',str(private))
        self.assertEqual(private['last_run']['errors'], {'hf':'Error details omitted'})
        self.assertNotIn('61,-149',str(private))
        self.assertNotIn('a',private['current_inputs'])
