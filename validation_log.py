"""Local immutable run snapshots and explicitly attempted validation observations."""
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sqlite3
import uuid
from contextlib import contextmanager

DB = Path(__file__).resolve().parent / '.cache/validation/history.sqlite3'


@contextmanager
def connect(path=None):
    path = path or DB
    Path(path).parent.mkdir(parents=True,exist_ok=True)
    connection=sqlite3.connect(path,timeout=10)
    connection.execute('PRAGMA foreign_keys=ON')
    connection.executescript('''
      CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, generated TEXT NOT NULL, target TEXT NOT NULL, snapshot TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS observations (id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id),
       recorded TEXT NOT NULL, data TEXT NOT NULL);
    ''')
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def save_run(result,evidence=None,path=None):
    if not result.get('hf'): return None
    from realtime_context import current_context
    run_id=str(uuid.uuid4())
    hf=result['hf']
    snapshot={'a':result['a'],'b':result['b'],'generated_at':result['generated_at'].isoformat(),
              'target':result['target'].isoformat(),'provenance':hf['provenance'],
              'predictions':json.loads(hf['table'].to_json(orient='records')),
              'current_conditions':current_context(result),'reception_evidence':evidence or {}}
    with connect(path) as db:
        db.execute('INSERT INTO runs VALUES (?,?,?,?)',(run_id,snapshot['generated_at'],snapshot['target'],json.dumps(snapshot)))
    return run_id


def runs(path=None):
    with connect(path) as db:
        return [{'id':identity,**json.loads(snapshot)} for identity,snapshot in db.execute('SELECT id,snapshot FROM runs ORDER BY generated DESC')]


def save_observation(run_id,frequency,hour,direction,actual_time,outcome,snr=None,setup_confirmed=False,notes='',path=None):
    if outcome not in ('Heard','Attempted; no reception') or direction not in ('A→B','B→A'):
        raise ValueError('Record an explicitly attempted reception test.')
    if actual_time.tzinfo is None: raise ValueError('Observation time must include UTC offset.')
    if actual_time>datetime.now(timezone.utc): raise ValueError('Observation time cannot be in the future.')
    if snr is not None and (not math.isfinite(snr) or not -150<=snr<=150): raise ValueError('Invalid measured SNR.')
    if outcome!='Heard' and snr is not None: raise ValueError('A non-reception has no measured SNR.')
    with connect(path) as db:
        record=db.execute('SELECT generated,target,snapshot FROM runs WHERE id=?',(run_id,)).fetchone()
        if record is None: raise ValueError('Unknown prediction run.')
        generated,target,snapshot=record
        prediction=next((p for p in json.loads(snapshot)['predictions'] if p['frequency_mhz']==frequency and p['utc_hour']==hour and p['direction']==direction),None)
        if prediction is None: raise ValueError('That frequency/hour/direction was not predicted.')
        predicted_time=datetime.fromisoformat(target).replace(hour=hour,minute=0,second=0,microsecond=0)
        time_aligned=abs((actual_time-predicted_time).total_seconds())<=1800
        prospective=datetime.fromisoformat(generated)<=actual_time
        comparable=bool(setup_confirmed and time_aligned and prospective and outcome=='Heard' and snr is not None)
        data={'frequency_mhz':frequency,'utc_hour':hour,'direction':direction,'actual_time':actual_time.isoformat(),
              'outcome':outcome,'measured_snr_db':snr,'predicted_snr_db':prediction['median_snr_db'],
              'setup_confirmed':bool(setup_confirmed),'time_aligned':time_aligned,'prospective':prospective,
              'snr_comparable':comparable,'error_db':snr-prediction['median_snr_db'] if comparable else None,'notes':notes[:2000]}
        db.execute('INSERT INTO observations VALUES (?,?,?,?)',(str(uuid.uuid4()),run_id,datetime.now(timezone.utc).isoformat(),json.dumps(data)))
    return data


def observations(path=None):
    with connect(path) as db:
        return [{'id':i,'run_id':r,'recorded':t,**json.loads(data)} for i,r,t,data in db.execute('SELECT * FROM observations ORDER BY recorded DESC')]


def summary(records):
    attempted=[r for r in records if r['prospective'] and r['time_aligned'] and r['setup_confirmed']]
    errors=[r['error_db'] for r in records if r['snr_comparable']]
    return {'matched_prospective_tests':len(attempted),'heard':sum(r['outcome']=='Heard' for r in attempted),
            'comparable_snr_count':len(errors),'mean_error_db':sum(errors)/len(errors) if errors else None,
            'mean_absolute_error_db':sum(abs(e) for e in errors)/len(errors) if errors else None}


def render_validation():
    import pandas as pd
    import streamlit as st
    with st.expander('Prediction validation log'):
        st.caption('Runs are saved locally when Analyze propagation completes. Existing WSPR/FT8 history is saved as evidence, not treated as a subsequent test or as failed reception when absent.')
        try:
            history=runs();records=observations()
        except (OSError,sqlite3.Error) as exc:
            st.warning(f'Local validation log unavailable: {exc}');return
        if not history:
            st.info('Run an analysis to start logging predictions.');return
        stats=summary(records)
        st.write(f"**{len(history)} saved runs · {stats['matched_prospective_tests']} matched prospective tests · {stats['heard']} heard**")
        if stats['comparable_snr_count']:
            st.write(f"Comparable SNR measurements: {stats['comparable_snr_count']} · mean absolute error {stats['mean_absolute_error_db']:.1f} dB · measured-minus-predicted bias {stats['mean_error_db']:+.1f} dB")
        st.caption('SNR errors include only later observations within ±30 minutes of a predicted hourly bin, with matching setup confirmed. Heard counts describe recorded tests, not calibrated success probabilities. Monthly medians vary from individual receptions; these summaries are exploratory.')
        selected=st.selectbox('Saved prediction run',range(len(history)),format_func=lambda i:f"{history[i]['generated_at'][:19]} UTC · target {history[i]['target'][:16]}")
        run=history[selected]
        table=pd.DataFrame(run['predictions'])
        st.caption(f"A {run['a']} → B {run['b']} · {run['provenance']['power_w_each_direction']:g} W · {run['provenance']['bandwidth_hz']:g} Hz")
        with st.expander('Saved setup and conditions'):
            st.json({k:v for k,v in run.items() if k not in ('predictions','id')},expanded=False)
        st.markdown('**Record an actual reception test**')
        with st.form('validation_observation'):
            direction=st.selectbox('Test direction',['A→B','B→A'])
            frequency=st.selectbox('Test frequency (MHz)',sorted(table.frequency_mhz.unique()))
            hour=st.selectbox('Predicted UTC hour',list(range(24)),index=datetime.fromisoformat(run['target']).hour)
            now=datetime.now(timezone.utc)
            date=st.date_input('Actual observation date (UTC)',now.date())
            clock=st.time_input('Actual observation time (UTC)',now.time().replace(tzinfo=None,second=0,microsecond=0))
            outcome=st.selectbox('Test outcome',['Select an actual test outcome','Heard','Attempted; no reception'])
            have_snr=st.checkbox('I measured receiver SNR in the modeled bandwidth')
            snr=st.number_input('Measured receiver SNR (dB)',-150.,150.,0.)
            confirmed=st.checkbox('Test used the saved endpoints, antennas, power, bandwidth and noise assumptions')
            notes=st.text_input('Test mode and notes (optional)')
            submit=st.form_submit_button('Save reception test')
        if submit:
            try:
                data=save_observation(run['id'],float(frequency),hour,direction,datetime.combine(date,clock,tzinfo=timezone.utc),outcome,
                                      snr if have_snr else None,confirmed,notes)
                st.success('Reception test saved.'+(' Included in SNR comparison.' if data['snr_comparable'] else ' Retained as an observation; excluded from SNR error statistics.'))
                records=observations()
            except (ValueError,OSError,sqlite3.Error) as exc: st.warning(str(exc))
        if records: st.dataframe(pd.DataFrame(records),hide_index=True)
        st.download_button('Download validation observations CSV',pd.DataFrame(records).to_csv(index=False),'validation_observations.csv','text/csv')
        st.download_button('Download saved predictions and assumptions',json.dumps(history,indent=2),'prediction_history.json','application/json')
