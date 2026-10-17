"""Hourly frequency heatmaps with shared SNR scale and explicit time provenance."""
from datetime import datetime, timezone
import math
import json
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st


DIRECTIONS = ('A→B', 'B→A')


def heatmap_data(table, target, timezone_name='UTC'):
    """Retain all 24 UTC bins, including repeated local hours at DST changes."""
    frequencies = sorted(table.frequency_mhz.unique(), reverse=True)
    matrix = table.pivot(index=['frequency_mhz', 'utc_hour'], columns='direction', values='median_snr_db')
    matrix = matrix.reindex(pd.MultiIndex.from_product([frequencies, range(24)], names=['frequency_mhz', 'utc_hour']))
    matrix = matrix.reindex(columns=list(DIRECTIONS))
    matrix['Weaker direction'] = matrix.min(axis=1, skipna=False)
    clock = []
    zone = ZoneInfo(timezone_name)
    for hour in range(24):
        utc = datetime(target.year, target.month, target.day, hour, tzinfo=timezone.utc)
        local = utc.astimezone(zone)
        label = local.strftime('%H:%M') if local.minute else local.strftime('%H')
        clock.append({'utc_hour': hour, 'hour_key': f'{label}|{hour:02}', 'local_hour': label,
                      'local_time': local.strftime('%Y-%m-%d %H:%M %Z'), 'utc_time': utc.strftime('%Y-%m-%d %H:%M UTC'),
                      'local_sort': local.hour*60+local.minute})
    frame = matrix.reset_index().merge(pd.DataFrame(clock), on='utc_hour', validate='many_to_one')
    frame['frequency_label'] = frame.frequency_mhz.map(lambda value: f'{value:g} MHz')
    frame['is_target_hour'] = frame.utc_hour.eq(target.hour)
    return frame, [item['hour_key'] for item in sorted(clock, key=lambda item: (item['local_sort'], item['utc_hour']))]


def snr_domain(table):
    values = pd.to_numeric(table.median_snr_db, errors='coerce').dropna()
    values = values[values.map(math.isfinite)]
    if values.empty:
        return [-10, 10]
    low, high = math.floor(values.min()/10)*10, math.ceil(values.max()/10)*10
    return [low, high if high > low else low+10]


def heatmap_spec(frame, direction, hour_order, clock, timezone_name, domain, numbers=False, legend=True):
    labels = frame[['frequency_mhz', 'frequency_label']].drop_duplicates().sort_values('frequency_mhz', ascending=False).frequency_label.tolist()
    tooltip = [{'field': 'frequency_mhz', 'type': 'quantitative', 'title': 'Frequency (MHz)', 'format': '.5~f'},
               {'field': 'local_time', 'type': 'nominal', 'title': 'Local time'},
               {'field': 'utc_time', 'type': 'nominal', 'title': 'UTC'},
               {'field': 'A→B', 'type': 'quantitative', 'title': 'A→B model SNR (dB)', 'format': '.1f'},
               {'field': 'B→A', 'type': 'quantitative', 'title': 'B→A model SNR (dB)', 'format': '.1f'},
               {'field': 'Weaker direction', 'type': 'quantitative', 'title': 'Weaker direction (dB)', 'format': '.1f'}]
    if 'absorption_context' in frame.columns:
        tooltip.append({'field': 'absorption_context', 'type': 'nominal', 'title': 'Current absorption context'})
    x = {'field': 'hour_key' if clock == 'Local time' else 'utc_hour', 'type': 'ordinal',
         'sort': hour_order if clock == 'Local time' else list(range(24)),
         'axis': {'title': f'Local hour · {timezone_name}' if clock == 'Local time' else 'UTC hour',
                  'labelAngle': -45 if clock == 'Local time' and frame.local_hour.str.contains(':').any() else 0, 'labelOverlap': False, 'labelFontSize': 10,
                  'labelExpr': "split(datum.label, '|')[0]" if clock == 'Local time' else "format(toNumber(datum.label), '02d')"}}
    layers = [{'mark': {'type': 'rect', 'stroke': '#d1d5db', 'strokeWidth': .3},
               'encoding': {'color': {'condition': {'test': f'isValid(datum[{json.dumps(direction)}])',
                                                    'field': direction, 'type': 'quantitative',
                                                    'scale': {'domain': domain, 'scheme': 'viridis', 'clamp': True},
                                                    'legend': {'title': 'Modeled SNR (dB)', 'orient': 'bottom', 'gradientLength': 260} if legend else None},
                                      'value': '#d1d5db'}}},
              {'transform': [{'filter': 'datum.is_target_hour'},
                             {'aggregate': [], 'groupby': ['hour_key', 'utc_hour']}],
               'mark': {'type': 'rect', 'fillOpacity': 0, 'stroke': '#f43f5e', 'strokeWidth': 2.5},
               'encoding': {'y': {'value': 0}, 'y2': {'value': {'expr': 'height'}},
                            'tooltip': {'value': None}}}]
    if 'absorption_marker' in frame.columns:
        layers.append({'transform': [{'filter': "datum.absorption_marker === '!'"}],
                       'mark': {'type': 'text', 'fontSize': 9, 'fontWeight': 'bold', 'dy': -3, 'color': '#111827'},
                       'encoding': {'text': {'field': 'absorption_marker', 'type': 'nominal'}}})
    if numbers:
        layers.append({'mark': {'type': 'text', 'fontSize': 9}, 'encoding': {
            'text': {'field': direction, 'type': 'quantitative', 'format': '.0f'},
            'color': {'condition': {'test': f'datum[{json.dumps(direction)}] > {domain[0]+.65*(domain[1]-domain[0])}', 'value': '#111827'}, 'value': '#ffffff'}}})
    return {'height': max(220, min(620, len(labels)*25)),
            'encoding': {'x': x, 'y': {'field': 'frequency_label', 'type': 'ordinal', 'sort': labels,
                                       'axis': {'title': None, 'labelLimit': 130, 'labelOverlap': False}}, 'tooltip': tooltip},
            'layer': layers, 'config': {'view': {'stroke': None}}}


def render_model_heatmap(hf, timezone_name='UTC'):
    st.caption('Frequency × hour × modeled SNR, with direction as a separate panel. Brighter cells mean stronger modeled SNR—not a guaranteed opening. Both panels and all direction views use the same scale.')
    view_column, clock_column, number_column = st.columns([2, 2, 1])
    with view_column:
        view = st.selectbox('Heatmap direction', ['Both directions', 'Weaker direction', *DIRECTIONS])
    with clock_column:
        clock = st.radio('Heatmap clock', ['Local time', 'UTC'], horizontal=True)
    with number_column:
        numbers = st.checkbox('Show SNR labels', value=False)
    frame, order = heatmap_data(hf['table'], hf['target'], timezone_name)
    from realtime_context import absorption_cells
    frame = absorption_cells(frame, hf['target'], hf.get('current_conditions', {}))
    domain = snr_domain(hf['table'])
    views = DIRECTIONS if view == 'Both directions' else (view,)
    containers = st.columns(2) if len(views) == 2 else [st.container()]
    for container, direction in zip(containers, views):
        with container:
            st.markdown(f'**{direction}**')
            st.vega_lite_chart(frame, spec=heatmap_spec(frame, direction, order, clock, timezone_name, domain, numbers), height=max(320, min(720, frame.frequency_mhz.nunique()*30+80)), use_container_width=True)
    st.caption(f'Hover any cell for both directional SNRs and exact local/UTC times. Pink outline marks the target UTC hour {hf["target"].hour:02}:00. Gray means a missing model value. The weaker-direction view requires both directions; it is not a success probability.')
    st.caption('These are 24 monthly model bins on the target UTC date, arranged by the chosen clock—not a daily forecast. Local dates and UTC offsets are shown on hover; daylight-saving changes can repeat or skip local hour labels. All UTC bins remain distinct.')
