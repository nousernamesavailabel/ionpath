import io
import hashlib
import json
import math
import os
import re
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode
from zoneinfo import ZoneInfo, available_timezones
from runtime_paths import private_path, public_mode
from beta_diagnostics import APP_VERSION, render_diagnostics
from path_geometry import render_path_details
from run_report import capture_run, render_report_export
from dashboard import model_settings, run_context_layers, render_dashboard
from path_evidence import DIRECTIONS, select_path
from ft8_archive import ARCHIVE_DATE, ARCHIVE_PATH, FT8_FREQUENCIES, read_archive
from streamlit_folium import st_folium
from map_selection import MAP_HEIGHT, build_map, area_updates, parse_areas, spatial_predicate

from planning import (activity_heatmap_data, grid_uncertainty, origin_history, filter_origins, coverage_flags,
                      ranking_explanation, hourly_evidence, hour_label)

import numpy as np
import pandas as pd
import requests
import mgrs
import streamlit as st

st.set_page_config(page_title='WSPR Path Planner', page_icon='📡', layout='wide',initial_sidebar_state='collapsed')
BANDS = {1:'160m',3:'80m',5:'60m',7:'40m',10:'30m',14:'20m',18:'17m',21:'15m',24:'12m',28:'10m',50:'6m'}
FREQ = {1:1.8366,3:3.5686,5:5.2872,7:7.0386,10:10.1387,14:14.0956,18:18.1046,21:21.0946,24:24.9246,28:28.1246,50:50.293}
# Keep database band identifiers as keys; show rounded frequency and wavelength together.
BANDS = {band: f'{FREQ[band]:.0f} MHz ({wavelength})' for band, wavelength in BANDS.items()}
WSPR_REFERENCE_DBM = 30  # Fixed 1 W WSPR comparison reference.

MGRS_CONVERTER = mgrs.MGRS()

COLS=['time','band','rx_sign','rx_lat','rx_lon','rx_loc','tx_sign','tx_lat','tx_lon','tx_loc','frequency','power','snr','code']
TRANSMITTER_SETTINGS_PATH = private_path('transmitter_settings.json')
DESTINATION_SETTINGS_PATH = private_path('destination_settings.json')
LOCATION_FORMATS = ['Maidenhead', 'MGRS', 'Latitude / longitude']


def validate_transmitter(settings):
    if not isinstance(settings, dict):
        raise ValueError('Transmitter settings must be an object.')
    call = str(settings.get('callsign', '')).strip().upper()
    if not call or any(c not in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789/-' for c in call):
        raise ValueError('Enter a callsign containing only letters, digits, slash, and hyphen.')
    coordinate_format = settings.get('coordinate_format')
    if coordinate_format not in LOCATION_FORMATS:
        raise ValueError('Choose a supported transmitter coordinate format.')
    location = str(settings.get('location', '')).strip()
    parse_location_detail(coordinate_format, location)
    return {'callsign': call, 'coordinate_format': coordinate_format, 'location': location}


def save_transmitter(settings):
    settings = validate_transmitter(settings)
    save_settings(TRANSMITTER_SETTINGS_PATH, settings)


def save_settings(path, settings):
    # Replace atomically so an interrupted write cannot leave partial settings.
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8',
                                         dir=path.parent,
                                         prefix=f'.{path.stem}-', suffix='.tmp', delete=False) as output:
            temporary_path = Path(output.name)
            json.dump(settings, output, indent=2)
            output.write('\n')
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def save_destination_format():
    try:
        save_settings(DESTINATION_SETTINGS_PATH, {'coordinate_format': st.session_state['dest_format']})
        st.session_state.pop('destination_settings_warning', None)
    except OSError as exc:
        st.session_state['destination_settings_warning'] = f'Could not save destination format: {exc}'


def update_area_radius(role):
    st.session_state['map_areas'][role]['radius_km']=float(st.session_state[f'{role}_area_radius'])


def evidence_heatmap_styles(matrix):
    """Use one color scale for the whole matrix without optional plotting packages."""
    values = matrix.to_numpy(dtype=float)
    low, high = float(values.min()), float(values.max())
    fractions = (values - low) / (high - low) if high > low else np.zeros_like(values)
    pale, dark = np.array([247, 251, 255]), np.array([8, 48, 107])
    styles = []
    for row in fractions:
        styles.append([
            'background-color: #%02x%02x%02x; color: %s' % (
                *np.rint(pale + fraction * (dark - pale)).astype(int),
                'white' if fraction > 0.5 else 'black')
            for fraction in row
        ])
    return pd.DataFrame(styles, index=matrix.index, columns=matrix.columns)


def grid_coords(grid):
    s=str(grid).strip().upper()
    if len(s) not in (4,6,8) or not ('A'<=s[0]<='R' and 'A'<=s[1]<='R') or not s[2:4].isdigit():
        raise ValueError('Enter a valid 4-, 6-, or 8-character Maidenhead locator (e.g. BP51).')
    lon=(ord(s[0])-65)*20-180+(int(s[2])*2)
    lat=(ord(s[1])-65)*10-90+int(s[3])
    w,h=2.,1.
    if len(s)>=6:
        if not ('A'<=s[4]<='X' and 'A'<=s[5]<='X'): raise ValueError('Invalid six-character grid.')
        w,h=w/24,h/24
        lon+=(ord(s[4])-65)*w; lat+=(ord(s[5])-65)*h
    if len(s)>=8:
        if not s[6:8].isdigit(): raise ValueError('Invalid eight-character grid.')
        w,h=w/10,h/10
        lon+=int(s[6])*w; lat+=int(s[7])*h
    return lat+h/2,lon+w/2



MGRS_BANDS = 'CDEFGHJKLMNPQRSTUVWX'


def parse_mgrs(value):
    """Return (lat, lon, half-diagonal uncertainty km, cell description).

    A short MGRS reference defines an area. Use the center, not the
    southwest corner returned by most MGRS libraries.
    """
    compact = re.sub(r'\s+', '', str(value).upper())
    match = re.fullmatch(r'(\d{1,2})([C-HJ-NP-X])([A-HJ-NP-Z]{2})?(\d{0,10})', compact)
    if not match:
        raise ValueError('Enter MGRS like 17R, 17RLL, 17RLL1234, or 17RLL12345678; numerical digits must be paired.')
    zone = int(match.group(1))
    band = match.group(2)
    square = match.group(3)
    digits = match.group(4)
    if not 1 <= zone <= 60:
        raise ValueError('MGRS zone must be between 1 and 60.')
    if len(digits) % 2:
        raise ValueError('MGRS requires equal easting and northing precision: use 0, 2, 4, 6, 8, or 10 digits.')
    if square is None:
        if digits:
            raise ValueError('Include the two-letter 100 km square before numeric coordinates.')
        # Zone + latitude band only: a broad region, not a precise point.
        index = MGRS_BANDS.index(band)
        lower = -80 + index * 8
        upper = 84 if band == 'X' else lower + 8
        lat = (lower + upper) / 2
        lon = zone * 6 - 183
        # Conservative bound: half the zone-band diagonal (longitude km varies by latitude).
        north_south = (upper - lower) * 111.2 / 2
        east_west = 3 * 111.32 * math.cos(math.radians(lower if abs(lower)>abs(upper) else upper))
        uncertainty = math.hypot(north_south, abs(east_west))
        return float(lat), float(lon), float(uncertainty), 'zone/band (very broad)'
    precision = len(digits) // 2
    # Pad both easting and northing to the CENTER of the encoded MGRS cell.
    # e.g. 17RLL -> 17RLL5000050000, 17RLL1234 -> 17RLL1250034500.
    easting = digits[:precision]
    northing = digits[precision:]
    pad = 5 - precision
    easting = easting + ('5' + '0' * (pad - 1) if pad else '')
    northing = northing + ('5' + '0' * (pad - 1) if pad else '')
    normalized = f'{zone}{band}{square}{easting}{northing}'
    try:
        lat, lon = MGRS_CONVERTER.toLatLon(normalized)
    except Exception as exc:
        raise ValueError(f'Invalid MGRS square or coordinate: {value}. Check the grid zone, band, and 100 km letters.') from exc
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError('MGRS coordinate converted outside valid WGS84 latitude/longitude ranges.')
    cell_m = 10 ** (5 - precision)
    uncertainty = (cell_m * math.sqrt(2) / 2) / 1000
    description = f'{cell_m:,} m cell'
    return float(lat), float(lon), uncertainty, description


def parse_location_detail(location_type, value):
    value = str(value).strip()
    if location_type == 'Maidenhead':
        lat, lon = grid_coords(value)
        return lat, lon, grid_uncertainty(value, lat), 'Maidenhead cell center'
    if location_type == 'MGRS':
        return parse_mgrs(value)
    if location_type == 'Latitude / longitude':
        try:
            parts = [float(x.strip()) for x in value.split(',')]
        except ValueError as exc:
            raise ValueError('Use decimal latitude, longitude, e.g. 61.2181, -149.9003.') from exc
        if len(parts) != 2 or not (-90 <= parts[0] <= 90 and -180 <= parts[1] <= 180):
            raise ValueError('Enter latitude (-90 to 90), longitude (-180 to 180), separated by a comma.')
        return parts[0], parts[1], 0.0, 'decimal coordinates'
    raise ValueError(f'Unsupported location type: {location_type}')


def parse_location(location_type, value):
    return parse_location_detail(location_type, value)[:2]


def location_label(lat, lon):
    try:
        mgrs_value = MGRS_CONVERTER.toMGRS(float(lat), float(lon), MGRSPrecision=5)
        if isinstance(mgrs_value, bytes):
            mgrs_value = mgrs_value.decode('ascii')
    except Exception:
        mgrs_value = 'unavailable'
    return f'{lat:.5f}, {lon:.5f} (WGS84) | MGRS: {mgrs_value}'


def haversine(lat1,lon1,lat2,lon2):
    x=np.radians(np.asarray(lat2,dtype=float)-lat1)
    y=np.radians(np.asarray(lon2,dtype=float)-lon1)
    a=np.sin(x/2)**2+np.cos(np.radians(lat1))*np.cos(np.radians(lat2,dtype=float))*np.sin(y/2)**2
    return 6371*2*np.arcsin(np.sqrt(np.clip(a,0,1)))


def bearing(lat1, lon1, lat2, lon2):
    a,b,c,d=map(math.radians,[lat1,lon1,lat2,lon2]); dl=d-b
    return (math.degrees(math.atan2(math.sin(dl)*math.cos(c),math.cos(a)*math.sin(c)-math.sin(a)*math.cos(c)*math.cos(dl)))+360)%360

@st.cache_data(ttl=3600,show_spinner=False)
def fetch_live(call,start,end,limit):
    call=call.upper().strip()
    if not call or any(c not in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789/-' for c in call):
        raise ValueError('Callsign may only contain letters, digits, slash, and hyphen.')
    # Partition pruning via UTC time predicate; avoid SELECT * for network and DB efficiency.
    fields=','.join(COLS)
    sql=(f"SELECT {fields} FROM wspr.rx WHERE tx_sign = '{call}' "
         f"AND time >= '{start:%Y-%m-%d %H:%M:%S}' AND time < '{end:%Y-%m-%d %H:%M:%S}' "
         f"AND code = 1 ORDER BY time DESC LIMIT {int(limit)} FORMAT CSVWithNames")
    r=requests.get('https://db1.wspr.live/',params={'query':sql},timeout=75,
                   headers={'User-Agent':'WSPR-Path-Planner/1.0'})
    check_wspr_response(r)
    if not r.text.strip(): return pd.DataFrame(columns=COLS)
    return pd.read_csv(io.StringIO(r.text))


@st.cache_data(ttl=3600,show_spinner=False)
def fetch_region(latitude,longitude,radius,start,end,limit):
    predicate=spatial_predicate(latitude,longitude,radius)
    sql=(f"SELECT {','.join(COLS)} FROM wspr.rx WHERE "
         f"time >= '{start:%Y-%m-%d %H:%M:%S}' AND time < '{end:%Y-%m-%d %H:%M:%S}' "
         f"AND band IN ({','.join(map(str,BANDS))}) AND code = 1 AND {predicate} "
         f"ORDER BY time DESC LIMIT {int(limit)} FORMAT CSVWithNames")
    response=requests.get('https://db1.wspr.live/',params={'query':sql},timeout=75,
                          headers={'User-Agent':'WSPR-Path-Planner/1.0'})
    check_wspr_response(response)
    return pd.read_csv(io.StringIO(response.text)) if response.text.strip() else pd.DataFrame(columns=COLS)


@st.cache_data(ttl=3600,show_spinner=False)
def fetch_path(a_center,a_radius,b_center,b_radius,start,end,limit,direction,callsign=None):
    if callsign and any(c not in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789/-' for c in callsign):
        raise ValueError('Invalid callsign.')
    frames=[]
    for label,a_role,b_role in [('A → B','tx','rx'),('B → A','rx','tx')]:
        if direction!='Both directions' and direction!=label:
            continue
        a_predicate=spatial_predicate(*a_center,a_radius,a_role)
        b_predicate=spatial_predicate(*b_center,b_radius,b_role)
        selected=f" AND {a_role}_sign = '{callsign}'" if callsign else ''
        sql=(f"SELECT {','.join(COLS)} FROM wspr.rx WHERE "
             f"time >= '{start:%Y-%m-%d %H:%M:%S}' AND time < '{end:%Y-%m-%d %H:%M:%S}' "
             f"AND band IN ({','.join(map(str,BANDS))}) AND code = 1 "
             f"AND ({a_predicate}) AND ({b_predicate}){selected} "
             f"ORDER BY time DESC LIMIT {int(limit)} FORMAT CSVWithNames")
        response=requests.get('https://db1.wspr.live/',params={'query':sql},timeout=75,
                              headers={'User-Agent':'WSPR-Path-Planner/1.0'})
        check_wspr_response(response)
        frame=pd.read_csv(io.StringIO(response.text)) if response.text.strip() else pd.DataFrame(columns=COLS)
        frame.attrs['limited']=len(frame)>=limit
        frames.append(frame)
    limited=any(frame.attrs.get('limited',False) for frame in frames)
    result=pd.concat(frames,ignore_index=True).drop_duplicates()
    result.attrs['limited']=limited
    return result


def load_wspr_history(state,a_center,a_radius,b_center,b_radius,lookback,limit,direction,callsign=None,refresh=False):
    """Reuse this session's reports until the path or query settings change."""
    signature=(a_center,a_radius,b_center,b_radius,lookback,limit,direction,callsign)
    cached=state.get('wspr_history')
    if refresh or cached is None or cached['signature'] != signature:
        end=datetime.now(timezone.utc)
        raw=fetch_path(a_center,a_radius,b_center,b_radius,end-timedelta(days=lookback),end,limit,direction,callsign)
        state['wspr_history']={'signature':signature,'raw':raw.copy(deep=True),
                               'start':end-timedelta(days=lookback),'end':end,
                               'fetched_at':datetime.now(timezone.utc),'row_count':len(raw),
                               'limited':raw.attrs.get('limited',False)}
        return raw
    return cached['raw'].copy(deep=True)


def check_wspr_response(response):
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        detail=' '.join(response.text.split())[:800]
        raise ValueError(f'WSPR.live rejected the request (HTTP {response.status_code}): {detail or "No server explanation provided."}') from exc


def normalize(df,call=None):
    rename={c:c.strip().lower().replace(' ','_') for c in df.columns}
    df=df.rename(columns=rename).copy()
    aliases={'date':'time','timestamp':'time','date_&_time':'time','reporter':'rx_sign','reporter_grid':'rx_loc',
             'reported_by':'rx_sign','reported_by_loc':'rx_loc','grid':'tx_loc','callsign':'tx_sign',
             'call':'tx_sign','db':'snr','dbm':'power','freq':'frequency'}
    df=df.rename(columns={k:v for k,v in aliases.items() if k in df.columns and v not in df.columns})
    needed={'time','rx_loc','snr'}
    missing=needed-set(df.columns)
    if missing: raise ValueError('CSV missing required fields: '+', '.join(sorted(missing)))
    if call and 'tx_sign' in df: df=df[df.tx_sign.astype(str).str.upper().str.strip()==call.upper()]
    if call is None and 'tx_sign' not in df:
        raise ValueError('Regional history requires tx_sign (transmitter callsign) in the CSV.')
    if 'tx_sign' in df:
        df['tx_sign']=df.tx_sign.fillna('').astype(str).str.strip().str.upper()
    if 'code' in df: df=df[pd.to_numeric(df.code,errors='coerce').eq(1)]
    df['time']=pd.to_datetime(df['time'],utc=True,errors='coerce')
    df['snr']=pd.to_numeric(df.snr,errors='coerce')
    if 'band' not in df:
        if 'frequency' not in df: raise ValueError('CSV must contain band or frequency.')
        freq=pd.to_numeric(df.frequency,errors='coerce')
        # WSPR.live uses Hz, other exporters may use MHz.
        mhz=np.where(freq>1e5,freq/1e6,freq)
        vals=np.array(list(FREQ.keys()))
        targets=np.array(list(FREQ.values()))
        best=np.argmin(np.abs(mhz[:,None]-targets[None,:]),axis=1)
        df['band']=np.where(np.abs(mhz-targets[best])<0.5,vals[best],np.nan)
    df['band']=pd.to_numeric(df.band,errors='coerce')
    if 'rx_lat' not in df or 'rx_lon' not in df:
        coords=[]
        for g in df.rx_loc:
            try: coords.append(grid_coords(g))
            except Exception: coords.append((np.nan,np.nan))
        df['rx_lat']=[x[0] for x in coords]
        df['rx_lon']=[x[1] for x in coords]
    if 'tx_lat' not in df or 'tx_lon' not in df:
        coords=[]
        for grid in df.get('tx_loc',pd.Series('',index=df.index)):
            try: coords.append(grid_coords(grid))
            except (ValueError,TypeError,IndexError): coords.append((np.nan,np.nan))
        df['tx_lat']=[point[0] for point in coords]
        df['tx_lon']=[point[1] for point in coords]
    # Grid centers are the position basis used by the existing location checks.
    # FT8 has no grid fields and therefore retains its reported coordinates.
    for role in ('tx','rx'):
        for index,grid in df.get(f'{role}_loc',pd.Series('',index=df.index)).items():
            try:
                latitude,longitude=grid_coords(grid)
                df.at[index,f'{role}_lat']=latitude
                df.at[index,f'{role}_lon']=longitude
            except (ValueError,TypeError,IndexError):
                pass
    for c in ('tx_lat','tx_lon','rx_lat','rx_lon','power'):
        if c in df: df[c]=pd.to_numeric(df[c],errors='coerce')
    if 'power' not in df: df['power']=np.nan
    if 'rx_sign' not in df: df['rx_sign']=df.rx_loc.astype(str)
    df['rx_sign']=df.rx_sign.fillna(df.rx_loc).astype(str).str.strip().str.upper()
    df.loc[df.rx_sign.eq(''), 'rx_sign'] = df.loc[df.rx_sign.eq(''), 'rx_loc'].astype(str)
    return df.dropna(subset=['time','band','rx_lat','rx_lon','snr']).loc[lambda d:d.band.isin(BANDS)&np.isfinite(d.snr)].copy()


def analyze(df,target_lat,target_lon,selected_hour,radius,hour_tol,season_days,waveform='WSPR'):
    d=df.copy()
    d['dest_km']=haversine(target_lat,target_lon,d.get('b_lat',d.rx_lat),d.get('b_lon',d.rx_lon))
    d=d[d.dest_km<=radius].copy()
    if d.empty: return pd.DataFrame(),d
    # All times UTC: support user-supplied target hour and seasonal envelope.
    utc_hours=d.time.dt.hour+d.time.dt.minute/60
    hour_diff=np.abs((utc_hours-selected_hour+12)%24-12)
    d['hour_diff']=hour_diff
    d=d[hour_diff<=hour_tol].copy()
    if d.empty: return pd.DataFrame(),d
    # Distance kernel and timing kernel; broad season filter is separately applied upstream.
    d['weight']=np.exp(-0.5*(d.dest_km/(max(radius/2,50)))**2)*np.exp(-0.5*(d.hour_diff/max(hour_tol/1.8,.3))**2)
    # Compare WSPR reports at a fixed WSPR power, never a voice-power equivalent.
    valid_power=d.power.where(np.isfinite(d.power))
    d['wspr_snr_1w']=d.snr+WSPR_REFERENCE_DBM-valid_power
    if waveform=='FT8':
        d['wspr_snr_1w']=np.nan
    d['normalization_status']='Unavailable for FT8' if waveform=='FT8' else np.where(d.wspr_snr_1w.notna(),'Normalized to 1 W WSPR','Cannot normalize')
    d['day']=d.time.dt.date
    rows=[]
    for band, g in d.groupby('band'):
        n=len(g); receivers=g.rx_sign.nunique(); dates=g.day.nunique()
        near=g[g.dest_km<=min(radius,200)]
        # A heuristic evidence score, not a probability of successful contact.
        normalized=g[g.wspr_snr_1w.notna()]
        snr_factor=(.4+.6*np.average(np.clip((normalized.wspr_snr_1w+30)/30,0,1),weights=normalized.weight)) if not normalized.empty else .7
        evidence=math.log1p(g.weight.sum())*math.sqrt(max(1,receivers))*math.sqrt(max(1,dates))
        score=evidence if waveform=='FT8' else evidence*snr_factor
        basis='snr' if waveform=='FT8' else 'wspr_snr_1w'
        medians=g.groupby('path_direction')[basis].median() if 'path_direction' in g else pd.Series({'A → B':g[basis].median()})
        ranking_snr=medians.max()
        raw_medians=g.groupby('path_direction').snr.median() if 'path_direction' in g else pd.Series({'A → B':g.snr.median()})
        rows.append({'Band':BANDS[int(band)],'Frequency (MHz)':(FT8_FREQUENCIES if waveform=='FT8' else FREQ)[int(band)],'Evidence score':score,'Ranking SNR (dB)':ranking_snr,
                     'A → B median SNR (dB)':medians.get('A → B',np.nan),'B → A median SNR (dB)':medians.get('B → A',np.nan),
                     'A → B measured SNR (dB)':raw_medians.get('A → B',np.nan),'B → A measured SNR (dB)':raw_medians.get('B → A',np.nan),
                     'Spots':n,'Receiving stations':receivers,'Distinct UTC days':dates,
                     'Nearest B endpoint (km)':round(g.dest_km.min()),
                     'Median B endpoint distance (km)':round(g.dest_km.median()),
                     'Median SNR (dB)':round(g.snr.median(),1),
                     'Median WSPR SNR at 1 W (dB)':round(normalized.wspr_snr_1w.median(),1) if not normalized.empty else np.nan,
                     'Power-normalized reports':len(normalized),
                     'Cannot normalize':len(g)-len(normalized),
                     'Spots within 200 km':len(near)})
    result=pd.DataFrame(rows).sort_values('Ranking SNR (dB)',ascending=False,na_position='last',kind='stable').reset_index(drop=True)
    return result,d

def mark_dashboard_run():
    st.session_state['dashboard_has_run'] = True

st.title('HF Path Planner')
st.caption(f'Beta {APP_VERSION}')
if public_mode():
    st.caption('Settings and validation history last only for this browser session. Download anything you want to keep before leaving or a server restart.')
st.markdown('''<style>
[data-testid="stMainBlockContainer"] {padding-top: 3.6rem;}
[data-testid="stMain"] h1 {font-size: 1.7rem; padding-top: 0; padding-bottom: .2rem;}
[data-testid="stMain"] h3 {font-size: 1.15rem; padding: .1rem 0;}
[data-testid="stMain"] [data-testid="stVerticalBlock"] {gap: .6rem;}
</style>''',unsafe_allow_html=True)
with st.sidebar:
    mode=st.radio('Source',['WSPR.live (online)','CSV upload (offline)','FT8 archive (cached day)'],key='data_source')
is_ft8=mode.startswith('FT8')
waveform='FT8' if is_ft8 else 'WSPR'
with st.sidebar:
    st.header('Setup')
    with st.expander('Station'):
        if 'transmitter_defaults_loaded' not in st.session_state:
            try:
                saved = validate_transmitter(json.loads(TRANSMITTER_SETTINGS_PATH.read_text(encoding='utf-8')))
                st.session_state['tx_callsign'] = saved['callsign']
                st.session_state['tx_format'] = saved['coordinate_format']
                st.session_state[f"tx_location_{saved['coordinate_format']}"] = saved['location']
            except FileNotFoundError:
                pass
            except (OSError, ValueError, TypeError) as exc:
                st.session_state['transmitter_load_warning'] = f'Could not load saved transmitter defaults: {exc}. Enter and save them again.'
            st.session_state['transmitter_defaults_loaded'] = True
        if 'transmitter_load_warning' in st.session_state:
            st.warning(st.session_state['transmitter_load_warning'])
        call=st.text_input('Station A callsign',placeholder='KL7ABC',key='tx_callsign').strip().upper()
        tx_format=st.selectbox('A coordinate format',LOCATION_FORMATS,key='tx_format')
        tx_defaults={'Maidenhead':'BP51','MGRS':'','Latitude / longitude':'61.2181, -149.9003'}
        st.session_state.setdefault(f'tx_location_{tx_format}', tx_defaults[tx_format])
        home=st.text_input('A location',key=f'tx_location_{tx_format}',help='MGRS accepts 17R, 17R LL, 17R LL 1234 5678, or finer precision. Short references use the area center.')
        st.caption('MGRS accepts a zone/band, 100 km square, or any paired numeric precision (0–10 digits). Short references are interpreted as areas.')
        if st.button('Save A defaults'):
            try:
                save_transmitter({'callsign': call, 'coordinate_format': tx_format, 'location': home})
                st.session_state.pop('transmitter_load_warning', None)
                st.success('A defaults saved for future runs.')
            except (OSError, ValueError, TypeError) as exc:
                st.error(f'Could not save transmitter defaults: {exc}')
        st.caption('Saved for this browser session only.' if public_mode() else 'Saved locally for this app installation. Edit these fields and save again to update your defaults.')
    with st.expander('History'):
        lookback=st.slider('History to fetch (days)',7,365,90) if mode.startswith('WSPR.live') else None
        limit=st.select_slider('Maximum rows per online direction / cached archive',options=[10000,25000,50000,100000],value=50000) if not mode.startswith('CSV') else None
        uploaded=st.file_uploader('Upload WSPR CSV',type=['csv']) if mode.startswith('CSV') else None
        upload_signature=hashlib.sha256(uploaded.getvalue()).hexdigest() if uploaded is not None else None
        season=st.checkbox('Use similar time of year only',value=False) if not is_ft8 else False
        season_days=st.slider('Season window ± days',7,90,45) if season else 183
        refresh_history=st.button('Refresh WSPR history',disabled=not mode.startswith('WSPR.live'),help='Fetch newer reports and update the analysis using the current settings.')
        if mode.startswith('WSPR.live'):
            st.caption('History includes all hours. Analyze propagation reuses it until the path or history settings change; Refresh WSPR history requests newer reports.')
        if is_ft8:
            st.info(f'One cached archive day: {ARCHIVE_DATE} UTC. No live feed or automatic archive downloads.')
    origin_filter=True
    include_unknown=False
    st.caption('Both endpoint centers must lie inside their circles. Unlocated reports are excluded.')


with st.container(border=True):
    time_panel=st.container()
    map_panel=st.container()
    with map_panel:
        with st.expander('Path and map', expanded=not st.session_state.get('dashboard_has_run', False)):
            placement_column,tx_radius_column,destination_radius_column,scope_column,coordinates_column=st.columns([2,2,2,1.6,2],vertical_alignment='bottom')
            with scope_column:
                with st.popover('Path options',use_container_width=True):
                    path_direction=st.radio('Report direction',DIRECTIONS,horizontal=True,key='path_direction')
                    selection_scope=st.radio('Station A history',['Any station in A circle','Selected callsign'],key='selection_scope')
                    st.caption('Both directions includes stations heard at A. One-way evidence does not confirm a two-way link.')
                    timezone_options=sorted(available_timezones())
                    local_timezone=st.selectbox('Local timezone',timezone_options,index=timezone_options.index('America/Anchorage'))
                    reference_date=st.date_input('Local-time reference date',value=ARCHIVE_DATE if is_ft8 else datetime.now(timezone.utc).date(),help='Sets the HF model month/year and local-to-UTC conversion, including daylight saving. Does not filter reception-history dates.')
            with placement_column:
                active_area=st.radio('Place circle',['B','A'],horizontal=True,key='active_area')
            with coordinates_column:
                with st.popover('Coordinates',use_container_width=True):
                    if 'destination_defaults_loaded' not in st.session_state:
                        try:
                            saved_destination = json.loads(DESTINATION_SETTINGS_PATH.read_text(encoding='utf-8'))
                            if not isinstance(saved_destination, dict) or saved_destination.get('coordinate_format') not in LOCATION_FORMATS:
                                raise ValueError('Unsupported destination coordinate format.')
                            st.session_state['dest_format'] = saved_destination['coordinate_format']
                        except FileNotFoundError:
                            pass
                        except (OSError, ValueError, TypeError) as exc:
                            st.session_state['destination_settings_warning'] = f'Could not load destination format: {exc}. Select a format to save it again.'
                        st.session_state['destination_defaults_loaded'] = True
                    dest_format=st.selectbox('B coordinate format',LOCATION_FORMATS,key='dest_format',on_change=save_destination_format)
                    if 'destination_settings_warning' in st.session_state:
                        st.warning(st.session_state['destination_settings_warning'])
                    dest_defaults={'Maidenhead':'EM12','MGRS':'','Latitude / longitude':'32.7767, -96.7970'}
                    dest=st.text_input('B location',value=dest_defaults[dest_format],key=f'dest_location_{dest_format}',help='Maidenhead (e.g., EM12), MGRS, or WGS84 decimal latitude, longitude.')
                    if dest_format=='MGRS': st.caption('Examples: 17R, 17R LL (100 km square), 17R LL 1234 5678 (10 m square). Spaces optional; digits must be paired. Short references use the cell center.')
                    entered_radius=st.session_state.get('map_areas',{}).get('destination',{}).get('radius_km',600.0)
                    entered_origin_radius=st.session_state.get('map_areas',{}).get('tx',{}).get('radius_km',50.0)
    
                    apply_coordinates=st.button('Apply coordinates to map')
            if 'map_areas' not in st.session_state or apply_coordinates:
                try:
                    initial_tx=parse_location_detail(tx_format,home)
                    initial_dest=parse_location_detail(dest_format,dest)
                    st.session_state['map_areas']={
                        'tx':{'center':list(initial_tx[:2]),'radius_km':float(entered_origin_radius)},
                        'destination':{'center':list(initial_dest[:2]),'radius_km':float(entered_radius)},
                    }
                    st.session_state['map_uncertainties']=(initial_tx[2],initial_dest[2])
                except ValueError as exc:
                    st.warning(f'Could not apply coordinates: {exc}')
                    st.session_state.setdefault('map_areas',{'tx':{'center':[61.2181,-149.9003],'radius_km':50.0},'destination':{'center':[32.7767,-96.7970],'radius_km':600.0}})
                    st.session_state.setdefault('map_uncertainties',(0.,0.))
            areas=st.session_state['map_areas']
            with tx_radius_column:
                st.session_state['tx_area_radius']=float(areas['tx']['radius_km'])
                st.number_input('Blue A circle radius (km)',min_value=0.1,max_value=5000.0,step=10.0,
                                key='tx_area_radius',on_change=update_area_radius,args=('tx',),
                                help='Type a distance or use − / +. Updates the circle immediately without moving its center.')
            with destination_radius_column:
                st.session_state['destination_area_radius']=float(areas['destination']['radius_km'])
                st.number_input('Orange B circle radius (km)',min_value=0.1,max_value=5000.0,step=10.0,
                                key='destination_area_radius',on_change=update_area_radius,args=('destination',),
                                help='Type a distance or use − / +. Updates the circle immediately without moving its center.')
            # Keep the base script constant: streamlit-folium keys its iframe by
            # script contents. Update layers separately so edits never remount it.
            if 'map_initial_areas' not in st.session_state:
                import copy
                st.session_state['map_initial_areas']=copy.deepcopy(areas)
            map_result=st_folium(build_map(st.session_state['map_initial_areas']),
                                 key='path_selection_map',height=MAP_HEIGHT,use_container_width=True,
                                 feature_group_to_add=area_updates(areas,'tx' if active_area=='A' else 'destination'),
                                 returned_objects=['all_drawings'])
            try:
                drawings=map_result.get('all_drawings')
                fresh=drawings != st.session_state.get('map_last_drawings')
                st.session_state['map_last_drawings']=drawings
                changed=parse_areas(drawings) if fresh else None
                if changed and changed != areas:
                    st.session_state['map_areas']=changed
                    st.session_state['map_uncertainties']=(0.,0.)
                    st.rerun()
            except (ValueError,TypeError) as exc:
                st.warning(f'Map selection not applied: {exc}')
            st.caption('Drag pins to move · Drag outlines to resize · '+path_direction)
    with time_panel:
        hour_column,window_column,run_column=st.columns([4,3,2],vertical_alignment='bottom')
        with hour_column:
            local_hour=st.slider('Target hour (local time)',0,23,6,help='Uses the selected local timezone. The UTC equivalent below is used for analysis.')
        with window_column:
            hour_tol=st.slider('Time window ± hours',1,6,2)
        local_target=datetime(reference_date.year,reference_date.month,reference_date.day,local_hour,tzinfo=ZoneInfo(local_timezone))
        utc_target=local_target.astimezone(timezone.utc)
        hour=utc_target.hour+utc_target.minute/60
        st.caption(f'{local_target:%Y-%m-%d %H:%M %Z} = {utc_target:%Y-%m-%d %H:%M UTC} · '+(f'FT8 archive {ARCHIVE_DATE}' if is_ft8 else (f'WSPR · {lookback} days' if lookback else 'WSPR CSV')))

        with run_column:
            run=st.button('Analyze propagation',type='primary',width='stretch', on_click=mark_dashboard_run, help='Update HF outlook and ionosondes; reuse reception history when the path and history settings are unchanged.')

regional=selection_scope=='Any station in A circle'
map_tx=tuple(areas['tx']['center'])
map_dest=tuple(areas['destination']['center'])
model_options = model_settings(map_tx, map_dest)
origin_radius=areas['tx']['radius_km']
radius=areas['destination']['radius_km']
map_signature=(map_tx,map_dest,origin_radius,radius,selection_scope,path_direction,st.session_state['map_uncertainties'])

@st.cache_data(ttl=86400,max_entries=8,show_spinner=False)
def load_ft8_day(path,stamp,tx_center,tx_radius,rx_center,rx_radius,row_limit,selected_call,direction):
    return read_archive(path,tx_center,tx_radius,rx_center,rx_radius,row_limit,selected_call,direction=direction)

dashboard_signature=(call,map_signature,hour,hour_tol,season,season_days,origin_radius,origin_filter,include_unknown,mode,lookback,limit,upload_signature,local_timezone,reference_date,json.dumps(model_options,sort_keys=True))

diagnostic_inputs={'a': {'center':map_tx,'radius_km':origin_radius},
                   'b': {'center':map_dest,'radius_km':radius,'effective_radius_km':radius+st.session_state['map_uncertainties'][1]},
                   'callsign':None if regional else call,'selection_scope':selection_scope,
                   'direction':path_direction,'source':mode,'lookback_days':lookback,'row_limit':limit,
                   'target_utc':utc_target.isoformat(),'timezone':local_timezone,'hour_tolerance':hour_tol,
                   'season':season,'season_days':season_days,'model_settings':model_options}

if run or refresh_history:
    st.session_state.pop('run_report_pdf', None)
    st.session_state.pop('run_report_snapshot', None)
    st.session_state['diagnostic_run_inputs']=diagnostic_inputs
    st.session_state.pop('reception_error', None)
    with st.spinner('Analyzing HF outlook, NOAA conditions and ionosondes…'):
        st.session_state['path_dashboard'] = run_context_layers(map_tx, map_dest, utc_target, model_options)
        st.session_state['path_dashboard']['timezone'] = local_timezone
    st.session_state['dashboard_signature'] = dashboard_signature
    st.session_state.pop('results_mgrs', None)
    st.session_state.pop('analysis_context', None)
    try:
        if not regional and not call: raise ValueError('Enter the station A callsign under Station or choose Any station in A circle.')
        txlat,txlon=map_tx
        destlat,destlon=map_dest
        tx_uncert,dest_uncert=st.session_state['map_uncertainties']
        tx_precision=dest_precision='map point / entered cell center'
        dest=f'{destlat:.5f}, {destlon:.5f}'
        effective_radius=radius+dest_uncert
        archive_stats=None
        if is_ft8:
            with st.spinner('Reading cached FT8 archive in chunks… First analysis may take a minute.'):
                raw,archive_stats=load_ft8_day(str(ARCHIVE_PATH),ARCHIVE_PATH.stat().st_mtime_ns if ARCHIVE_PATH.exists() else 0,map_tx,origin_radius,map_dest,effective_radius,limit,None if regional else call,path_direction)
        elif mode.startswith('CSV'):
            if uploaded is None: raise ValueError('Upload a CSV first.')
            raw=pd.read_csv(uploaded)
        else:
            with st.spinner('Loading WSPR history…'):
                raw=load_wspr_history(st.session_state,map_tx,origin_radius,map_dest,effective_radius,lookback,limit,path_direction,None if regional else call,refresh=refresh_history)
        if raw.empty and is_ft8:
            raise ValueError(f'No FT8 reports matched these circles on {ARCHIVE_DATE} UTC. This single archive day cannot establish whether the path is open now.')
        if raw.empty:
            raise ValueError('WSPR.live returned zero WSPR-2 reception reports for the selected A–B areas/callsign and date range. Confirm your callsign and try a longer history.')
        d=normalize(raw)
        d=select_path(d,map_tx,origin_radius,map_dest,effective_radius,haversine,path_direction,None if regional else call)
        if d.empty: raise ValueError(f'No usable {waveform} reports connect the A–B circles in the selected direction. Review both circles, callsign and history window.')
        if season:
            now_day=datetime.now(timezone.utc).timetuple().tm_yday
            dd=d.time.dt.dayofyear
            delta=np.abs((dd-now_day+183)%366-183)
            d=d[delta<=season_days]
        history=origin_history(d,txlat,txlon,origin_radius,tx_uncert,grid_coords,haversine,prefix='a')
        d=history[history.origin_distance_km.le(origin_radius)].copy() if regional else filter_origins(history,origin_filter,include_unknown)
        if regional:
            d=d[d.tx_sign.ne('')].copy()
        scores,matched=analyze(d,destlat,destlon,hour,effective_radius,hour_tol,season_days,waveform)
        st.session_state['analysis_context']={
            'ranking_version': 6, 'path_direction':path_direction, 'waveform': waveform, 'archive_stats': archive_stats, 'regional': regional, 'map_signature': map_signature, 'history': history, 'origin_radius': origin_radius, 'origin_filter': origin_filter,
            'include_unknown': include_unknown, 'row_limited': archive_stats['limited'] if is_ft8 else raw.attrs.get('limited',False),
            'hour_tol': hour_tol, 'timezone': local_timezone, 'reference_date': reference_date, 'utc_target': utc_target, 'local_target': local_target,
            'season': season, 'source': mode, 'lookback': lookback,
            'input_signature': (call,map_signature,hour,hour_tol,season,season_days,
                                origin_radius,origin_filter,include_unknown,mode,lookback,limit,upload_signature,local_timezone,reference_date),
        }
        st.session_state['results_mgrs']=(scores,matched,d,txlat,txlon,destlat,destlon,call,dest,radius,hour,limit,tx_precision,dest_precision,dest_uncert,effective_radius)
    except Exception as exc:
        st.session_state['reception_error'] = f'Reception evidence unavailable: {exc}'
    try:
        from validation_log import save_run
        evidence = {'source': mode, 'waveform': waveform, 'error': st.session_state.get('reception_error')}
        stored = st.session_state.get('results_mgrs')
        if stored is not None:
            evidence['band_summary'] = json.loads(stored[0].to_json(orient='records'))
            evidence['report_count'] = len(stored[1])
        identity = save_run(st.session_state['path_dashboard'], evidence)
        st.session_state['path_dashboard']['validation_run_id'] = identity
    except Exception as exc:
        st.session_state['path_dashboard']['validation_error'] = str(exc)

    st.session_state['run_report_snapshot']=capture_run(st.session_state,diagnostic_inputs)

render_path_details(map_tx,map_dest,utc_target)
render_report_export(st.session_state,st.session_state.get('dashboard_signature') != dashboard_signature)

if st.session_state.get('path_dashboard'):
    if dashboard_signature != st.session_state.get('dashboard_signature'):
        st.warning('Settings changed. The dashboard shows the previous run. Click Analyze propagation to update all three sources.')
    render_dashboard(BANDS, st.session_state.get('analysis_context', {}).get('waveform', waveform))
else:
    st.info('Set A and B and a target hour, then click Analyze propagation once for all three sources. Advanced settings are in the sidebar.')

if mode.startswith('WSPR.live') and st.session_state.get('wspr_history'):
    cached_history=st.session_state['wspr_history']
    st.caption(f"WSPR history fetched {cached_history['fetched_at']:%Y-%m-%d %H:%M UTC} · query range {cached_history['start']:%Y-%m-%d %H:%M} to {cached_history['end']:%Y-%m-%d %H:%M UTC} · {cached_history['row_count']:,} reports. These timestamps describe the cached query; changed settings apply on the next analysis.")

render_diagnostics(st.session_state, diagnostic_inputs)

with st.expander('Reception evidence details and exports'):
    if 'results_mgrs' in st.session_state:
        current_signature=(call,map_signature,hour,hour_tol,season,season_days,
                           origin_radius,origin_filter,include_unknown,mode,lookback,limit,upload_signature,local_timezone,reference_date)
        context=st.session_state.get('analysis_context')
        if context is None or context.get('ranking_version') != 6:
            st.session_state.pop('results_mgrs', None)
            st.rerun()
        if current_signature != context['input_signature']:
            st.warning('Controls have changed. Results below use the previous analysis settings. Click Analyze propagation to update them.')
        scores,matched,d,txlat,txlon,destlat,destlon,call,dest,radius,hour,limit,tx_precision,dest_precision,dest_uncert,effective_radius=st.session_state.results_mgrs
        result_waveform=context['waveform']
        result_ft8=result_waveform=='FT8'
        st.subheader(f'Operator overview — {result_waveform}')
        if result_ft8:
            stats=context['archive_stats']
            st.info(f"FT8 sample: {stats['date']} UTC only · {stats['returned_rows']:,} reports retained. This is archived activity, not current conditions. No TX-power normalization or cross-mode score comparison.")
            if any(offset!=0 for offset in stats.get('timestamp_offsets_seconds',[])):
                offsets=', '.join(f'{offset/3600:g} h' for offset in stats['timestamp_offsets_seconds'])
                st.warning(f'Archive time discrepancy: sampled Unix timestamps differ from the fields labeled UTC by {offsets}. This prototype uses the labeled UTC calendar fields; the discrepancy has not been independently resolved.')
        st.write(f"Selected target: **{context['local_target']:%Y-%m-%d %H:%M %Z} / {context['utc_target']:%Y-%m-%d %H:%M UTC} ± {context['hour_tol']} hours**")
        st.caption(f"{context['path_direction']} · {'Any station in A circle' if context['regional'] else 'Selected station A callsign'} · Confirm with ALE/LQA and voice check")
        st.caption('Ranked by the strongest direction’s median '+('measured FT8 SNR' if result_ft8 else 'WSPR SNR normalized to 1 W')+'. Report counts describe coverage; one-way reception does not confirm the return link.')
        if context['row_limited']:
            st.warning('Row limit reached: the history is truncated to the newest retained reports. Rankings and operating windows may reflect incomplete data.')
        if not scores.empty:
            overview=[]
            for index,row in scores.head(3).iterrows():
                group=matched[matched.band.map(BANDS).eq(row['Band'])]
                origins=group.origin_status.value_counts()
                origin_notes=', '.join(f'{origins[status]} {label}' for status,label in [('Outside radius','outside'),('Boundary uncertain','boundary'),('Unknown','unknown')] if origins.get(status,0)) or 'Within radius'
                def direction_summary(label):
                    count=int(group.path_direction.eq(label).sum())
                    value=row[f'{label} median SNR (dB)']
                    usable=count if result_ft8 else int(group.loc[group.path_direction.eq(label),'wspr_snr_1w'].notna().sum())
                    return f'{value:.1f} dB · {usable}/{count}' if pd.notna(value) else f'— · 0/{count}'
                overview.append({'Priority':('Try first' if index==0 else f'Alternative {index}') if pd.notna(row['Ranking SNR (dB)']) else 'SNR unavailable',
                                 'Band':row['Band'],'A → B SNR / reports':direction_summary('A → B'),'B → A SNR / reports':direction_summary('B → A'),
                                 'UTC days':int(row['Distinct UTC days']),
                                 'Nearest B (km)':int(row['Nearest B endpoint (km)']),
                                 'Busiest day':f'{group.time.dt.date.value_counts().iloc[0]/len(group):.0%}',
                                 'A location flags':origin_notes})
            st.dataframe(pd.DataFrame(overview),hide_index=True,use_container_width=True)
            st.caption('Direction cells: median SNR · usable/total reports. '+('FT8 measured SNR; TX power unknown.' if result_ft8 else 'WSPR SNR normalized to 1 W; measured SNR is in detailed reports.'))
            snr_plot=scores.set_index('Band')[['A → B median SNR (dB)','B → A median SNR (dB)']].rename(columns={'A → B median SNR (dB)':'A → B','B → A median SNR (dB)':'B → A'})
            snr_points=snr_plot.reset_index().melt(id_vars='Band',var_name='Direction',value_name='Median SNR (dB)').dropna()
            if not snr_points.empty:
                st.vega_lite_chart(snr_points,spec={
                    'mark':{'type':'point','filled':True,'size':100},
                    'encoding':{
                        'x':{'field':'Median SNR (dB)','type':'quantitative','scale':{'zero':False},
                             'axis':{'title':'Median SNR (dB)'+('' if result_ft8 else ' at 1 W WSPR')}},
                        'y':{'field':'Band','type':'nominal','sort':scores.Band.tolist(),'axis':{'title':None,'labelLimit':160}},
                        'yOffset':{'field':'Direction','type':'nominal'},
                        'color':{'field':'Direction','type':'nominal','legend':{'orient':'top'}},
                        'tooltip':[{'field':'Band'},{'field':'Direction'},{'field':'Median SNR (dB)','format':'.1f'}],
                    },
                },height=max(240,min(600,len(scores)*45+100)),use_container_width=True)
            with st.expander('Overview column guide'):
                st.caption('Direction cells show median SNR and usable/total report counts. WSPR medians use only reports with finite TX power, normalized to 1 W; missing values show —. FT8 medians use measured SNR with unknown TX power. Rankings use the stronger direction median, not a combined SNR or two-way prediction. Single-report medians can be fragile. Busiest day is the share of reports on one UTC day. Boundary means the station A reported area overlaps its circle. Small receiver/day counts and distant receivers limit coverage; none of these columns measures link reliability. Detailed coverage flags and missing-power counts are under Why these bands rank this way.')
        else:
            st.info('No matching reports between A and B at the selected time. Path availability is unknown.')
        st.subheader(f'Historical reception activity — {result_waveform} · local time (UTC)')
        st.caption('Less recorded evidence → More recorded evidence (darker blue). Numbers are distance-weighted reports, summed across historical days. Zero means No reports, not Closed. SNR, TX power and receiver/day diversity do not affect the shading.')
        full, hourly=hourly_evidence(d,destlat,destlon,effective_radius,radius,haversine)
        if full.empty:
            st.info('No reports between A and B at any UTC hour under the current analysis filters.')
        else:
            activity,hour_order=activity_heatmap_data(hourly,BANDS,reference_date,local_timezone)
            hour_labels=activity.drop_duplicates('utc_hour').set_index('utc_hour')['Hour'].to_dict()
            heatmap_spec={
                'autosize':{'type':'fit','contains':'padding'},
                'encoding':{
                    'x':{'field':'Hour','type':'ordinal','sort':[hour_labels[h] for h in hour_order],
                         'axis':{'title':'Local hour (UTC)','labelExpr':"split(datum.label, '|')",'labelAngle':0,'labelFontSize':9,'labelOverlap':False,'labelPadding':6}},
                    'y':{'field':'Band','type':'ordinal','sort':list(BANDS.values()),'axis':{'title':None,'labelLimit':150}},
                    'tooltip':[{'field':'Band'},{'field':'Local time'},{'field':'UTC time'},
                               {'field':'Weighted reports','format':'.2f'},{'field':'Reports'},
                               {'field':'UTC days'},{'field':'Receivers'},{'field':'A → B'},{'field':'B → A'}],
                },
                'layer':[
                    {'mark':{'type':'rect','stroke':'white','strokeWidth':0.5},
                     'encoding':{'color':{'field':'Weighted reports','type':'quantitative','scale':{'scheme':'blues'},'legend':None}}},
                    {'mark':{'type':'text','fontSize':9},
                     'encoding':{'text':{'field':'Weighted reports','type':'quantitative','format':'.2~f'},
                                 'color':{'condition':{'test':f"datum['Weighted reports'] > {activity['Weighted reports'].max()/2}",'value':'white'},'value':'#111827'}}},
                ],
            }
            st.vega_lite_chart(activity,spec=heatmap_spec,height=390,use_container_width=True)
            st.caption(f'{local_timezone} · Local hours first; UTC hours in parentheses. Offsets use {reference_date:%Y-%m-%d}. Values show up to two decimal places. Hover for exact values, dates and report/day counts.')
            matrix=hourly.pivot(index='band',columns='utc_hour',values='weighted_reports').reindex(index=list(BANDS),columns=hour_order).fillna(0)
            matrix.index=[BANDS[int(b)] for b in matrix.index]
            matrix.columns=[hour_labels[h].replace('|',' ')+' UTC' for h in matrix.columns]
            st.download_button('Download 24-hour evidence matrix (CSV)',matrix.to_csv().encode(),file_name='ft8_24hour_activity.csv' if result_ft8 else 'wspr_24hour_evidence.csv',mime='text/csv')
            st.subheader('Inspect an hour')
            inspect_band=st.selectbox('Band to inspect',list(BANDS.values()),key='inspect_band')
            inspect_hour=st.selectbox('Hour to inspect — local (UTC)',hour_order,index=hour_order.index(int(hour)),format_func=lambda h: hour_labels[h].replace('|',' ')+' UTC',key='inspect_hour')
            band_id=next(b for b,label in BANDS.items() if label==inspect_band)
            cell=hourly[(hourly.band==band_id)&(hourly.utc_hour==inspect_hour)]
            inspected_time=activity[activity.utc_hour.eq(inspect_hour)].iloc[0]
            st.caption(f"{inspected_time['Local time']} ({inspected_time['UTC time']})")
            if cell.empty:
                st.info('No reports. Path availability is unknown.')
            else:
                cell=cell.iloc[0]
                st.write(f"{cell.weighted_reports:.2f} weighted reports · {int(cell.reports):,} actual reports · {int(cell.receivers)} receiver identities/grids · {int(cell.days)} UTC days · nearest B endpoint {cell.nearest_km:.0f} km")
                cell_group=full[(full.band==band_id)&(full.utc_hour==inspect_hour)]
                st.caption(f"A → B: {int(cell_group.path_direction.eq('A → B').sum()):,} · B → A: {int(cell_group.path_direction.eq('B → A').sum()):,}")
                flags=coverage_flags(cell_group)
                if flags:
                    st.caption('Coverage flags: '+'; '.join(flags))
            st.subheader('Hours with the most recorded evidence')
            st.caption('Up to three individual UTC hours per band, ranked by distance-weighted reports. These are historical activity windows, not predicted openings. Receiver schedules and report volume affect this ranking. Local times use the selected reference date and may shift with daylight saving.')
            windows=hourly.sort_values(['weighted_reports','utc_hour'],ascending=[False,True]).groupby('band',sort=False).head(3).copy()
            windows['Band']=windows.band.map(BANDS)
            windows['UTC hour / local time']=[hour_label(h,reference_date,local_timezone) for h in windows.utc_hour]
            windows=windows.rename(columns={'weighted_reports':'Weighted reports','reports':'Actual reports','days':'Distinct UTC days','receivers':'Receiver identities/grids','nearest_km':'Nearest B endpoint (km)'})
            st.dataframe(windows[['Band','UTC hour / local time','Weighted reports','Actual reports','Distinct UTC days','Receiver identities/grids','Nearest B endpoint (km)']].round(1),hide_index=True,use_container_width=True)
            hourly_export=hourly.assign(Band=hourly.band.map(BANDS))
            st.download_button('Download hourly report and coverage counts (CSV)',hourly_export.to_csv(index=False).encode(),file_name='ft8_hourly_coverage.csv' if result_ft8 else 'wspr_hourly_coverage.csv',mime='text/csv')
        with st.expander('Station A locations and coverage'):
            history=context['history']
            counts=history.origin_status.value_counts()
            st.write(f"Origin check radius: {context['origin_radius']} km. Before origin filter: {len(history):,} reports; retained: {len(d):,}; excluded: {len(history)-len(d):,}.")
            st.write(' · '.join(f'{status}: {int(counts.get(status,0)):,}' for status in ['Within radius','Boundary uncertain','Outside radius','Unknown']))
            st.caption('Counts cover the usable history after any seasonal filter, before target-time selection. Boundary uncertain means grid uncertainty overlaps the radius; it does not confirm a nearby station.')
            st.caption('A is the transmitter in A → B reports and receiver in B → A reports. Both endpoint centers must lie within their circles; grid uncertainty can extend beyond a circle. Reported positions may be stale or incorrect.')
            if counts.get('Outside radius',0)>0:
                st.warning('Some reports originate outside the selected origin area. This callsign may have operated from other locations or reported inconsistent positions.')
            if counts.get('Unknown',0)>0:
                st.caption('Unknown origins cannot be checked. Unlocated reports are excluded from path selection.')
            if not d.empty:
                st.caption(f"Retained history: {d.time.min():%Y-%m-%d} to {d.time.max():%Y-%m-%d} UTC, on {d.time.dt.date.nunique()} distinct UTC days.")
            else:
                st.warning('No history remains after filtering. Review the circles and seasonal settings; no radius was widened.')
            if not history.empty:
                origin_table=history.groupby(['origin_source','origin_lat','origin_lon','origin_status'],dropna=False).agg(Reports=('time','size'), **{'Distance to selected origin (km)':('origin_distance_km','min'),'Grid uncertainty (km)':('origin_uncertainty_km','max')}).reset_index()
                st.dataframe(origin_table.round(1),hide_index=True,use_container_width=True)
        with st.expander('Detailed analysis and reception reports'):
            st.caption('A: '+location_label(txlat,txlon))
            st.caption('B: '+location_label(destlat,destlon))
            st.caption(f'Coordinate interpretation: A {tx_precision}; B {dest_precision}. B search radius {radius:.0f} km + cell uncertainty {dest_uncert:.1f} km = {effective_radius:.1f} km.')
            if dest_uncert > 200:
                st.warning('B grid covers a broad region. Rankings and path geometry may not represent one particular city or station. Use a 100 km square or finer for better accuracy.')
            a,b,c,dcol=st.columns(4)
            a.metric('Total usable path reports',f'{len(d):,}')
            b.metric('Matching path/time',f'{len(matched):,}')
            c.metric('Path length',f'{haversine(txlat,txlon,np.array([destlat]),np.array([destlon]))[0]:,.0f} km')
            dcol.metric('Bearing A → B',f'{bearing(txlat,txlon,destlat,destlon):.0f}°')
            if scores.empty:
                st.warning(f'No positive {result_waveform} reception reports matched the A–B circles and UTC time window. This is **not evidence that the band is closed**. Try a larger radius, wider UTC window, or longer history.')
            else:
                best=scores.iloc[0]
                if pd.notna(best['Ranking SNR (dB)']):
                    st.subheader(f"Strongest median SNR: {best['Band']} ({best['Frequency (MHz)']:.4f} MHz {result_waveform} reference frequency)")
                else:
                    st.info('No power-normalized SNR is available to rank these WSPR bands. Report activity alone does not establish signal strength.')
                st.caption('Historical reception evidence only. Reference frequencies identify the digital mode and are not suggested voice operating frequencies.')
                display=scores.copy();display['Evidence score']=display['Evidence score'].round(2)
                if result_ft8:
                    display=display.drop(columns=['Median WSPR SNR at 1 W (dB)','Power-normalized reports','Cannot normalize']).rename(columns={'Evidence score':'FT8 activity score'})
                display=display.rename(columns={'Evidence score':'Activity / coverage heuristic','Ranking SNR (dB)':'Ranking median SNR (dB)'})
                st.dataframe(display,use_container_width=True,hide_index=True)
                with st.expander('Why these bands rank this way'):
                    st.caption('Ranking uses the stronger direction median SNR, descending. WSPR is normalized using actual TX power; FT8 uses measured SNR. Bands with no usable ranking SNR appear last. Activity scores below are coverage diagnostics and do not determine order. These are one-way observations, not predicted return-link SNR.')
                    if context['path_direction']=='Both directions':
                        st.caption('Actual TX/RX roles remain attached to every observation. Noise, antennas and power differ between stations; compare direction-specific SNR alongside sample counts.')
                    elif result_ft8:
                        st.caption('FT8 activity score = log(1 + weighted reports) × √receiver count × √UTC day count. No SNR or transmit-power factor. Scores are separate from WSPR and do not measure voice reliability.')
                    else:
                        st.caption('Score = log(1 + weighted reports) × √receiver count × √UTC day count × SNR factor. Distance and time proximity weight each report. SNR factor uses only reports normalized to 1 W WSPR and ranges from 0.4 to 1. Missing/nonfinite TX power cannot be normalized and is excluded from that factor. If none can be normalized, the factor is set to its neutral midpoint, 0.7. No good/bad score threshold is validated.')
                    for _, row in scores.iterrows():
                        group=matched[matched.band.map(BANDS).eq(row['Band'])]
                        st.markdown(f"**{row['Band']}**")
                        st.write(ranking_explanation(group,result_waveform))
                        flags=coverage_flags(group)
                        if flags:
                            st.caption('Coverage flags: '+'; '.join(flags))
                        if group.time.dt.date.nunique()==1 or group.rx_sign.nunique()==1:
                            st.caption('Report volume is concentrated: ranking is not backed by both multiple days and multiple receiver identities.')
                st.download_button('Download ranked bands (CSV)',display.to_csv(index=False).encode(),file_name='ft8_band_activity.csv' if result_ft8 else 'wspr_band_ranking.csv',mime='text/csv')
                st.subheader('Matched reception reports')
                shown=matched.sort_values(['weight','time'],ascending=False).head(500).copy()
                shown['time']=shown.time.dt.strftime('%Y-%m-%d %H:%M UTC')
                shown['Band']=shown.band.map(BANDS)
                if 'tx_sign' not in shown: shown['tx_sign']=call
                report_columns=['time','Band','path_direction','a_sign','b_sign','tx_sign','rx_sign','rx_loc','dest_km','hour_diff','snr','weight'] if result_ft8 else ['time','Band','path_direction','a_sign','b_sign','tx_sign','rx_sign','rx_loc','dest_km','hour_diff','snr','power','wspr_snr_1w','normalization_status','weight']
                st.dataframe(shown[report_columns].round(2),use_container_width=True,hide_index=True)
                matched_export=matched.assign(Band=matched.band.map(BANDS),Mode=result_waveform)
                if result_ft8:
                    matched_export=matched_export.drop(columns=['wspr_snr_1w'])
                st.download_button('Download all matched spots (CSV)',matched_export.to_csv(index=False).encode(),file_name='ft8_matched_spots.csv' if result_ft8 else 'wspr_matched_spots.csv',mime='text/csv')
        st.info('**Mode planning:** PRC-160 STANAG 4538 ALE performs channel selection/link setup; MELP 600 and SSB have different voice requirements. Neither voice reliability nor an absolute receiver SNR threshold can be calibrated from WSPR spots alone.')
        st.info('**Interpretation:** Only successful decodes appear in this dataset. The tool does not know how many transmissions were sent and missed, receiver uptime, noise, antennas, or their listening schedules. Scores are relative indicators, not success probabilities. A digital reception report does not establish a usable voice path. Seasonal planning requires evidence across multiple months/years.')
    else:
        st.info('Choose the blue A and orange B areas on the map, select a local hour, then click **Analyze propagation**.')

with st.expander('Method and limitations'):
    st.markdown('''- A (blue) and B (orange) are geographic endpoints. Include A → B, B → A, or both. Reports must connect both circles; unlocated endpoints are excluded. A selected callsign applies to the station at A, transmitting or receiving as appropriate.
- Both directions is the default. Reverse reports add historical evidence, not proof of a return link or simultaneous two-way contact. Power, antennas, local noise and ionospheric effects can differ by direction.
- Actual TX/RX identities, reported transmitter power and measured receiver SNR are never swapped. Exports retain them alongside A/B identities and report direction.
- Bands rank by the stronger of the two direction-specific median SNR values. WSPR uses SNR normalized to 1 W; FT8 uses measured SNR with unknown TX power. Activity scores remain coverage diagnostics only. WSPR SNR at 1 W = reported SNR + 30 − actual TX power in dBm. FT8 never uses a power/SNR factor. Scores are experimental relative support, not reliability or cross-mode comparisons.
- Distance from B and difference from the selected UTC hour determine weights. Heatmap shading uses distance-weighted report counts only. Inspect the separate direction counts: one direction can dominate activity.
- Enter Maidenhead, MGRS or WGS84 coordinates, or move the circles. Cell inputs use centers; B search radius includes half the entered cell diagonal. Reported locations do not establish precise station positions.
- WSPR.live requests WSPR-2 only (code = 1), clipping both circles before limiting rows independently for each direction. The archive reader has a total row limit. Row limits, receiver schedules and missing archive days can bias results.
- UTC is used for historical bins. The timezone and reference date control local display and target-hour conversion, including daylight saving.
- Digital reception does not establish voice capability. Missing reports mean unknown availability, not a closed band. Validate channels with ALE/LQA and a voice check.
- Frequency references identify digital bands; they are not suggested voice channels. Use frequencies authorized for your service and mode.''')
    st.markdown('[WSPR.live](https://wspr.live/) · [HamSCI data](https://hamsci.org/data) · [CEDAR Madrigal](https://cedar.openmadrigal.org/)')
    st.caption('FT8 uses only the approved cached May 12, 2024 archive. Data credit: HamSCI, University of Scranton / University of Alabama; NASA grant 80NSSC21K1772; PSK Reporter contributors. Archive coverage varies; no unlimited-access assumption or automatic bulk retrieval.')
