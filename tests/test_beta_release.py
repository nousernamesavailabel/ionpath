"""Concurrent Streamlit sessions using real NEC/P.533 and isolated public storage."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from hf_model import model_ready

ROOT = Path(__file__).resolve().parents[1]
WORKER = r'''
import json, os, resource, sys, time
from pathlib import Path
from streamlit.testing.v1 import AppTest
os.environ['IONPATH_PUBLIC']='1'
index=int(sys.argv[1]); barrier=Path(sys.argv[2])
script = """
import streamlit as st
from datetime import datetime, timezone
from pathlib import Path
from antenna_defaults import save, load, settings_path
from antennas import defaults
from hf_model import run_model
from validation_log import save_run, runs
from runtime_paths import private_path
index=INDEX
config=defaults() | {'kind':'Dipole','length_m':20.+index,'height_m':10.+index}
a=(61.+index,-149.)
b=(32.,-97.+index)
save('A','Custom',config)
private_path('transmitter_settings.json').write_text('VISITOR'+str(index))
Path(BARRIER+'/'+str(index)).touch()
import time
deadline=time.monotonic()+30
while not all(Path(BARRIER+'/'+str(i)).exists() for i in (0,1)):
    if time.monotonic()>deadline: raise RuntimeError('Concurrent session did not start')
    time.sleep(.05)
table=run_model(a,b,2026,10,(7.15,),100,antenna_tx=config,antenna_rx=config)
assert len(table)==24
assert load('A')['config']['length_m']==20.+index
assert private_path('transmitter_settings.json').read_text()=='VISITOR'+str(index)
now=datetime.now(timezone.utc)
identity=save_run({'hf':{'table':table,'provenance':{}},'a':a,'b':b,'target':now,'generated_at':now})
assert len(runs())==1 and runs()[0]['id']==identity
st.session_state['checked']={'private_path':str(settings_path('A')),'a':a,'length_m':config['length_m'],'rows':len(table),'snr':table.median_snr_db.tolist()}
""".replace('INDEX',str(index)).replace('BARRIER',repr(str(barrier)))
start=time.monotonic()
at=AppTest.from_string(script,default_timeout=180).run()
assert not at.exception, str(at.exception)
report=at.session_state['checked']
report.update(seconds=round(time.monotonic()-start,2),peak_process_rss_mib=round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024,1),peak_child_rss_mib=round(resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss/1024,1))
print(json.dumps(report))
'''


class ConcurrentBeta(unittest.TestCase):
    @unittest.skipUnless(model_ready(), 'Installed ITU model required')
    def test_two_public_sessions_compute_real_models_and_keep_private_files(self):
        with tempfile.TemporaryDirectory() as barrier:
            def run(index):
                result=subprocess.run([sys.executable,'-c',WORKER,str(index),barrier],cwd=ROOT,
                                      capture_output=True,text=True,timeout=210)
                self.assertEqual(result.returncode,0,result.stderr[-5000:])
                return json.loads(result.stdout.splitlines()[-1])
            with ThreadPoolExecutor(max_workers=2) as pool:
                reports=list(pool.map(run,(0,1)))
        self.assertNotEqual(reports[0]['private_path'],reports[1]['private_path'])
        self.assertNotEqual(reports[0]['snr'],reports[1]['snr'])
        print('BETA_CONCURRENCY '+json.dumps(reports))
