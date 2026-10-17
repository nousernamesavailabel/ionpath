"""Current absorption and IRTAM context, never an invented correction to monthly SNR."""
from datetime import datetime, timezone
import math
import re
from urllib.parse import urlparse

from giro_cache import provider_get, read_cache, write_cache, fresh
from space_weather import parse_drap, path_absorption_context, utc_time

IRTAM_PAGE = 'https://giro.uml.edu/rix/test.html'


def parse_irtam(payload):
    meta=payload['metadata']
    valid=datetime.strptime(meta['Valid_Time'],'%Y%m%dT%H%M').replace(tzinfo=timezone.utc)
    def axis(name,low,high):
        scale=float(meta[name]['scalefactor'])
        values=[float(v)*scale for v in payload[name]]
        if not values or any(not math.isfinite(v) or not low<=v<=high for v in values):
            raise ValueError('Invalid IRTAM coordinates.')
        return values
    latitudes=axis('Latitude',-90,90);longitudes=axis('Longitude',0,360)
    data={}
    for name,limits in [('foF2',(0,30)),('hmF2',(100,1000)),('MUF(3000)F2',(0,100)),('30-day_median',(0,100)),('Data_Confidence',(0,1))]:
        spec=meta[name];raw=payload[name]
        if len(raw)!=len(latitudes) or any(len(row)!=len(longitudes) for row in raw):
            raise ValueError('Invalid IRTAM grid dimensions.')
        data[name]=[]
        for row in raw:
            converted=[]
            for v in row:
                value=float(v)*float(spec['scalefactor'])
                converted.append(value if v!=spec.get('missingValue') and math.isfinite(value) and limits[0]<value<=limits[1] else None)
            data[name].append(converted)
    if not any(v is not None for row in data['foF2'] for v in row):
        raise ValueError('No usable IRTAM foF2 cells.')
    return {'time':valid,'latitudes':latitudes,'longitudes':longitudes,'data':data,'model':meta.get('Model_Code','IRTAM')}


def load_irtam(now=None):
    """Use only the public latest-map link published by GIRO; share GIRO cooldown."""
    now=now or datetime.now(timezone.utc)
    saved=read_cache('irtam')
    if fresh(saved,now,900): return saved
    # Avoid repeated page discovery on failed refreshes.
    failed=read_cache('irtam_failure')
    if fresh(failed,now,60):
        return dict(saved or {},error=failed['error'])
    try:
        service=read_cache('irtam_service')
        if not fresh(service,now,86400):
            html=provider_get(IRTAM_PAGE,timeout=(5,12)).text
            links=re.findall(r'<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>',html,re.S)
            url=next(u for u,label in links if 'Latest Map in JSON' in label)
            parsed=urlparse(url)
            if parsed.scheme!='https' or parsed.hostname!='lgdc.uml.edu' or parsed.path!='/rix/gambit-muf-message':
                raise ValueError('Unrecognized GIRO map service.')
            service={'retrieved_at':now.isoformat(),'url':url}
            write_cache('irtam_service',service)
        payload=provider_get(service['url'],timeout=(5,12)).json()
        parse_irtam(payload)
        # Drop requester metadata, including provider-added IP, from local snapshots.
        payload['metadata']={k:v for k,v in payload['metadata'].items() if not k.startswith('_')}
        result={'payload':payload,'retrieved_at':now.isoformat(),'source':IRTAM_PAGE}
        write_cache('irtam',result)
        return result
    except Exception as exc:
        # Never include provider query credentials in error or provenance text.
        message=f'IRTAM refresh unavailable ({type(exc).__name__}); cached valid time is retained.'
        write_cache('irtam_failure',{'retrieved_at':now.isoformat(),'error':message})
        return dict(saved or {},error=message)


def sample_irtam(grid,lat,lon):
    row=min(range(len(grid['latitudes'])),key=lambda i:abs(grid['latitudes'][i]-lat))
    col=min(range(len(grid['longitudes'])),key=lambda i:abs((grid['longitudes'][i]-lon+180)%360-180))
    return {'grid_lat':grid['latitudes'][row],'grid_lon':(grid['longitudes'][col]+180)%360-180,
            **{name:values[row][col] for name,values in grid['data'].items()}}


def current_context(result,now=None):
    now=now or datetime.now(timezone.utc)
    context={'drap':{},'irtam':{}}
    try:
        feed=result['weather'].get('drap',{})
        grid=parse_drap(feed['payload'])
        context['drap']={'time':grid['time'].isoformat(),'haf_mhz':path_absorption_context(grid,result['a'],result['b']),
                         'fresh':0<=(now-grid['time']).total_seconds()<=1800,'error':feed.get('error')}
    except (KeyError,ValueError,TypeError):
        context['drap']={'error':'No usable D-RAP grid.'}
    feed=result.get('irtam') or {}
    try:
        grid=parse_irtam(feed['payload'])
        near=abs((result['target']-grid['time']).total_seconds())<=1800
        points=(result.get('hf') or {}).get('support_control_points',[])
        samples=[]
        for point in points:
            cell=sample_irtam(grid,point['lat'],point['lon'])
            baseline=point['fof2_mhz']
            cell.update(region=point['region'],control_lat=point['lat'],control_lon=point['lon'],baseline_fof2_mhz=baseline,
                        fof2_difference_percent=100*(cell['foF2']/baseline-1) if cell['foF2'] is not None and baseline>0 else None)
            samples.append(cell)
        context['irtam']={'time':grid['time'].isoformat(),'fresh':0<=(now-grid['time']).total_seconds()<=1800,
                          'target_aligned':near,'samples':samples,'error':feed.get('error'),'model':grid['model']}
    except (KeyError,ValueError,TypeError):
        context['irtam']={'error':feed.get('error','No usable IRTAM map.')}
    return context


def absorption_cells(frame,target,context,now=None):
    """Annotate only bins close to a fresh source timestamp, including the UTC date."""
    now=now or datetime.now(timezone.utc)
    out=frame.copy()
    drap=context.get('drap',{})
    out['absorption_context']='No time-matched current D-RAP context'
    out['absorption_marker']=''
    if not drap.get('time') or not drap.get('fresh'): return out
    valid=utc_time(drap['time'])
    if not 0<=(now-valid).total_seconds()<=1800: return out
    for index,row in out.iterrows():
        hour=target.replace(hour=int(row.utc_hour),minute=0,second=0,microsecond=0)
        if abs((hour-valid).total_seconds())>1800: continue
        haf=drap['haf_mhz']
        affected=float(row.frequency_mhz)<=haf and haf>0
        out.at[index,'absorption_context']=f'D-RAP {valid:%H:%M UTC}: path maximum HAF {haf:.1f} MHz; '+('frequency within affected-frequency context' if affected else 'above sampled HAF; absorption not ruled out')
        if affected: out.at[index,'absorption_marker']='!'
    return out


def render_current_context(result):
    import pandas as pd
    import streamlit as st
    context=current_context(result)
    st.subheader('Current path conditions')
    a,b=st.columns(2)
    with a:
        drap=context['drap']
        if 'time' not in drap: st.caption(drap['error'])
        else:
            st.write(f"**D-RAP absorption:** sampled path HAF {drap['haf_mhz']:.1f} MHz · {utc_time(drap['time']):%H:%M UTC}")
            if not drap['fresh']: st.warning('D-RAP is stale; heatmap flags are disabled.')
            st.caption('! marks frequencies within the sampled affected-frequency context, only at time-matched hours. Surface great-circle sampling is not ray-integrated loss, a closed-band threshold or a forecast for other hours.')
    with b:
        irtam=context['irtam']
        if 'time' not in irtam: st.caption(irtam['error'])
        else:
            st.write(f"**IRTAM F2 nowcast** · {utc_time(irtam['time']):%H:%M UTC}")
            usable=irtam['fresh'] and irtam['target_aligned'] and bool(irtam['samples'])
            if usable:
                changes=[s['fof2_difference_percent'] for s in irtam['samples'] if s['fof2_difference_percent'] is not None]
                st.caption(f'Control-region foF2 differences from P.533: {min(changes):+.0f}% to {max(changes):+.0f}%.' if changes else 'No usable grid cells at model control regions.')
            elif not irtam['fresh']:
                st.caption('IRTAM map is stale; no current F2 comparison applied.')
            elif not irtam['target_aligned']:
                st.caption(f"Target {result['target']:%Y-%m-%d %H:%M UTC} differs from this map time. No current F2 comparison applied.")
            else:
                st.caption('Model F2 control regions are unavailable; no current F2 comparison applied.')
            st.caption('Coarse global model cells, not local soundings. This comparison does not validate SNR or establish this path’s MUF.')
            if irtam.get('error'): st.caption(irtam['error'])
    with st.expander('Current conditions details'):
        if context['irtam'].get('samples'):
            st.dataframe(pd.DataFrame(context['irtam']['samples']),hide_index=True)
            st.caption('Provider scales and missing-value markers are honored. Nonphysical hmF2 values are omitted. Data_Confidence is provider metadata, not a link-success probability; missing confidence means local observational support is unknown.')
        st.caption('Current conditions annotate the monthly baseline. They do not apply arbitrary dB corrections or extend current measurements across the whole day.')
    return context
