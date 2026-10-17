"""GIRO measurements displayed as context, separately from the path model."""
from datetime import datetime, timezone

import pandas as pd
import requests
import streamlit as st

from ionosonde import DATA_URL, RULES_URL, STATION_URL, distance, fetch_context
from space_weather import path_points


@st.cache_data(ttl=600, max_entries=8, show_spinner=False)
def cached_context(a, b):
    return fetch_context(a, b)


def context_table(context, now):
    rows = []
    for station in context['stations']:
        latest = station['rows'][-1] if station['rows'] else None
        age = (now-latest['time']).total_seconds()/60 if latest else None
        if station['error']:
            status = 'Retrieval failed'
        elif latest is None:
            status = 'No measurements in six-hour query'
        elif age < -5:
            status = 'Future timestamp — check source'
        elif age > 60:
            status = 'Stale (>60 min)'
        elif latest['fof2_mhz'] is None:
            status = 'Latest foF2 unavailable'
        else:
            status = 'Recent measurement'
        confidence = latest['confidence'] if latest else None
        quality = ('Manual scaling' if confidence == 999 else 'Unknown' if confidence == -1 else f'Autoscaled CS {confidence}') if confidence is not None else 'Unavailable'
        rows.append({'Station': f"{station['name']} ({station['code']})", 'Status': status,
                     'foF2 (MHz)': latest['fof2_mhz'] if latest else None,
                     'hmF2 (km)': latest['hmf2_km'] if latest else None,
                     'Observation UTC': latest['time'].strftime('%Y-%m-%d %H:%M:%S') if latest else 'Unavailable',
                     'Age (min)': round(age, 1) if age is not None else None, 'Scaling': quality,
                     'foF2 qualifier': latest['fof2_qualifier'] if latest else '',
                     'hmF2 qualifier': latest['hmf2_qualifier'] if latest else '',
                     'From A (km)': round(station['distance_a_km']), 'From B (km)': round(station['distance_b_km']),
                     'From sampled path (km)': round(station['path_distance_km']), 'Source': station['url']})
    return pd.DataFrame(rows)


def render_ionosonde_details(context):
    now = datetime.now(timezone.utc)
    for warning in context.get('warnings', []):
        st.warning(warning)
    st.write(f"**GIRO measurements** · context window {context['start']:%Y-%m-%d %H:%M}–{context['retrieved_at']:%H:%M UTC}")
    st.caption('Latest available measurements, independent of the model date. Station readings are cached for ten minutes (empty windows for thirty minutes); the catalogue is cached for a week. Cached fallback readings retain their original observation times and may extend beyond the current six-hour query window.')
    frame = context_table(context, now)
    st.dataframe(frame, hide_index=True, use_container_width=True,
                 column_config={'Source': st.column_config.LinkColumn('Source', display_text='GIRO'),
                                'foF2 (MHz)': st.column_config.NumberColumn(format='%.3f'),
                                'hmF2 (km)': st.column_config.NumberColumn(format='%.1f')})
    recent = [s for s in context['stations'] if s['rows'] and s['rows'][-1]['fof2_mhz'] is not None and
              0 <= (now-s['rows'][-1]['time']).total_seconds() <= 3600]
    if recent:
        midpoint = path_points(context['a'], context['b'], 3)[1]
        nearest = min(recent, key=lambda s: distance(midpoint, (s['lat'], s['lon'])))
        gap = distance(midpoint, (nearest['lat'], nearest['lon']))
        st.write(f"Nearest station with recent foF2 to the path midpoint: **{nearest['name']} ({nearest['code']})**, **{gap:,.0f} km away**.")
    else:
        st.warning('No selected station has a usable foF2 measurement from the past hour. This does not establish that the path is closed.')
    st.caption('CS is the provider’s autoscaling confidence score (0–100), not a link-success probability. 999 means manual scaling; −1 means unknown. Autoscaled values and heights can be wrong. Qualifiers are retained verbatim from the source. Distances use station coordinates and surface great-circle samples, not modeled ionospheric reflection points.')
    history = [{'UTC': row['time'], 'Station': station['code'], 'foF2 (MHz)': row['fof2_mhz']}
               for station in context['stations'] for row in station['rows'] if row['fof2_mhz'] is not None]
    if history:
        st.line_chart(pd.DataFrame(history).pivot_table(index='UTC', columns='Station', values='foF2 (MHz)', aggfunc='last'),
                      x_label='Observation time (UTC)', y_label='Measured foF2 (MHz)')
    failures = [f"{s['code']}: {s['error']}" for s in context['stations'] if s['error']]
    if failures:
        with st.expander('Station retrieval details'):
            for failure in failures:
                st.text(failure)
    st.markdown(f'Data: [Lowell GIRO Data Center / DIDBase]({DATA_URL}) and the named station operators · [Station catalogue]({STATION_URL}) · [GIRO data-use terms, CC BY-NC-SA 4.0]({RULES_URL}).')
