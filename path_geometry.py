"""Spherical great-circle distances and initial true bearings (mean Earth radius)."""
import math
from datetime import date, datetime, timezone
from functools import lru_cache

EARTH_RADIUS_KM = 6371.0


def path_details(a, b):
    lat1, lon1 = map(math.radians, a)
    lat2, lon2 = map(math.radians, b)
    delta = lon2 - lon1
    cross = math.hypot(math.cos(lat2)*math.sin(delta),
                       math.cos(lat1)*math.sin(lat2)-math.sin(lat1)*math.cos(lat2)*math.cos(delta))
    dot = math.sin(lat1)*math.sin(lat2)+math.cos(lat1)*math.cos(lat2)*math.cos(delta)
    angle = math.atan2(cross, dot)
    short = EARTH_RADIUS_KM * angle
    ambiguous = cross < 1e-12
    def bearing(start, end):
        p, x = map(math.radians, start)
        q, y = map(math.radians, end)
        return math.degrees(math.atan2(math.sin(y-x)*math.cos(q),
            math.cos(p)*math.sin(q)-math.sin(p)*math.cos(q)*math.cos(y-x))) % 360
    # Exact poles have no unique local north reference.
    ab = None if ambiguous or abs(a[0]) == 90 else bearing(a, b)
    ba = None if ambiguous or abs(b[0]) == 90 else bearing(b, a)
    return {'short_km': short, 'long_km': None if angle < 1e-12 else 2*math.pi*EARTH_RADIUS_KM-short,
            'ab_short_deg': ab, 'ba_short_deg': ba,
            'ab_long_deg': None if ab is None else (ab+180)%360,
            'ba_long_deg': None if ba is None else (ba+180)%360}


def destination(origin, bearing_deg, distance_km):
    lat, lon = map(math.radians, origin)
    bearing, arc = math.radians(bearing_deg), distance_km / EARTH_RADIUS_KM
    phi = math.asin(max(-1., min(1., math.sin(lat)*math.cos(arc)+math.cos(lat)*math.sin(arc)*math.cos(bearing))))
    lam = lon + math.atan2(math.sin(bearing)*math.sin(arc)*math.cos(lat), math.cos(arc)-math.sin(lat)*math.sin(phi))
    return math.degrees(phi), (math.degrees(lam)+180)%360-180


@lru_cache(maxsize=256)
def _magnetic_declination(lat, lon, day):
    """Cache model results, while allowing transient dependency errors to retry."""
    if not 2010 <= day.year < 2030:
        return {'declination_deg': None, 'model': None, 'status': 'Unavailable: supported WMM dates are 2010-2029'}
    from pygeomag import GeoMag
    year = day.year + (day-date(day.year,1,1)).days / (date(day.year+1,1,1)-date(day.year,1,1)).days
    model = GeoMag(base_year=day.year)
    result = model.calculate(glat=lat, glon=lon, alt=0, time=year)
    if result.in_blackout_zone:
        return {'declination_deg': None, 'model': model.model, 'status': 'Unavailable: magnetic blackout zone'}
    return {'declination_deg': result.d, 'model': model.model,
            'status': 'Caution: weak horizontal magnetic field' if result.in_caution_zone else 'Available'}


def magnetic_declination(lat, lon, day):
    """Use bundled WMM coefficients at sea level; never cache lookup failures."""
    try:
        return _magnetic_declination(lat, lon, day)
    except (ImportError, ValueError, FileNotFoundError) as exc:
        return {'declination_deg': None, 'model': None, 'status': f'Unavailable: {exc}'}


magnetic_declination.cache_clear = _magnetic_declination.cache_clear


def magnetic_path_details(a, b, when):
    day=when.astimezone(timezone.utc).date() if isinstance(when,datetime) and when.tzinfo else (when.date() if isinstance(when,datetime) else when)
    details=path_details(a,b)
    details['magnetic_date']=day.isoformat()
    details['magnetic_altitude_km']=0
    for endpoint, center, direction in [('a',a,'ab'),('b',b,'ba')]:
        field=dict(magnetic_declination(*center,day))
        details[endpoint+'_magnetic']=field
        for route in ('short','long'):
            true=details[direction+'_'+route+'_deg']
            declination=field['declination_deg']
            details[direction+'_'+route+'_magnetic_deg']=None if true is None or declination is None else (true-declination)%360
    return details


def magnetic_description(details):
    parts=[]
    for endpoint in ('a','b'):
        field=details[endpoint+'_magnetic']; value=field['declination_deg']
        label=field['status'] if value is None else f"{abs(value):.1f}° {'E' if value>=0 else 'W'} ({field['model']})"
        if value is not None and field['status']!='Available': label+='; '+field['status']
        parts.append(endpoint.upper()+': '+label)
    return f"Magnetic declination on {details['magnetic_date']} at sea level - "+' · '.join(parts)


def render_path_details(a, b, when=None):
    import streamlit as st
    details = magnetic_path_details(a, b, when or datetime.now(timezone.utc))
    def heading(value,reference='true'): return 'Unavailable' if value is None else f'{value:05.1f}° {reference}'
    with st.container(border=True):
        columns = st.columns(4)
        columns[0].metric('Short path', f"{details['short_km']:,.0f} km")
        columns[1].metric('Long path', 'Undefined' if details['long_km'] is None else f"{details['long_km']:,.0f} km")
        columns[2].metric('A → B bearing', heading(details['ab_short_deg']))
        columns[2].caption('Magnetic azimuth: '+heading(details['ab_short_magnetic_deg'],'magnetic'))
        columns[3].metric('B → A bearing', heading(details['ba_short_deg']))
        columns[3].caption('Magnetic azimuth: '+heading(details['ba_short_magnetic_deg'],'magnetic'))
        st.caption(f"Initial bearings for the short path · Long path A → B: {heading(details['ab_long_deg'])} / {heading(details['ab_long_magnetic_deg'],'magnetic')} · B → A: {heading(details['ba_long_deg'])} / {heading(details['ba_long_magnetic_deg'],'magnetic')}. Distances use endpoint centers and a mean-radius spherical Earth. The HF model uses the short path.")
        st.caption(magnetic_description(details))
