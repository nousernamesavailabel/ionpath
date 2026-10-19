"""Independent modeled outlook and timestamped NOAA context for the path planner."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from html import escape
import math

import pandas as pd
import streamlit as st

from hf_model import run_model
from space_weather import FEEDS, latest_kp, load_feed, parse_drap, path_absorption_context, solar_for_month


@st.cache_data(ttl=86400, max_entries=32, show_spinner=False)
def modeled_path(a, b, year, month, frequencies, ssn, power, bandwidth, gain_a, gain_b, noise, antenna_a=None, antenna_b=None):
    forward = run_model(a, b, year, month, frequencies, ssn, power, bandwidth, gain_a, gain_b, noise, antenna_tx=antenna_a, antenna_rx=antenna_b)
    reverse = run_model(b, a, year, month, frequencies, ssn, power, bandwidth, gain_b, gain_a, noise, antenna_tx=antenna_b, antenna_rx=antenna_a)
    return pd.concat([forward.assign(direction='A→B'), reverse.assign(direction='B→A')], ignore_index=True)


def refresh_weather():
    with ThreadPoolExecutor(max_workers=4) as executor:
        return dict(zip(FEEDS, executor.map(lambda name: load_feed(name, refresh=True), FEEDS)))


def show_weather(feeds, a, b, target):
    st.markdown('**NOAA conditions — latest available, separate from the model**')
    now = datetime.now(timezone.utc)
    st.caption('These observations are not a reconstruction or forecast for the selected target date. They do not change the P.533 SNR values.')
    for name, feed in feeds.items():
        if feed.get('error'):
            st.warning(f"{name}: refresh failed. {'Showing previously retrieved data.' if feed.get('payload') is not None else 'No data available.'}")
    kp = feeds.get('kp', {}).get('payload')
    if kp:
        try:
            observation = latest_kp(kp)
            age = (now-observation['time']).total_seconds()/3600
            st.write(f"Planetary Kp: **{observation['value']:.2f}** · observation {observation['time']:%Y-%m-%d %H:%M UTC}")
            if age > 6 or age < -1:
                st.warning('Kp observation is stale or its timestamp is in the future.')
            if observation['value'] >= 5:
                st.warning('Geomagnetic storm conditions: high-latitude paths may depart substantially from the monthly model.')
        except (ValueError, KeyError, TypeError) as exc:
            st.warning(f'Kp unavailable: {exc}')
    payload = feeds.get('drap', {}).get('payload')
    if payload:
        try:
            grid = parse_drap(payload)
            haf = path_absorption_context(grid, a, b)
            st.write(f"D-RAP: **{haf:.1f} MHz** maximum sampled highest affected frequency along the current A–B short great circle · valid {grid['time']:%Y-%m-%d %H:%M UTC}")
            st.caption('Nearest grid cells at 41 surface points; this is geographic context for absorption, not a path-integrated loss, a lowest usable frequency, or a closed-band threshold. Zero does not establish zero absorption.')
            if abs((now-grid['time']).total_seconds()) > 1800:
                st.warning('D-RAP is more than 30 minutes from the current time; treat it as stale context.')
        except (ValueError, KeyError, TypeError) as exc:
            st.warning(f'D-RAP unavailable: {exc}')
    month = target.strftime('%Y-%m')
    solar = solar_for_month(feeds.get('solar_observed', {}).get('payload') or [], feeds.get('solar_predicted', {}).get('payload') or [], month)
    if solar:
        st.write(f"Solar-cycle input for {month}: **SSN {solar['value']}** · {solar['source']}")
    else:
        st.caption(f'No supported NOAA smoothed/predicted SSN for {month}. A manual smoothed SSN is needed to model that month.')
    with st.expander('NOAA sources and retrieval times'):
        for name, feed in feeds.items():
            st.markdown(f"[{name}]({feed['url']}) · retrieved {feed.get('retrieved_at') or 'unavailable'}")


def snr_table_styles(frame):
    """One relative red–amber–green scale shared by both directions and minimum."""
    finite=[float(value) for value in frame.to_numpy().ravel() if pd.notna(value) and math.isfinite(float(value))]
    low, high = (min(finite),max(finite)) if finite else (-10.,10.)
    def style(value):
        if pd.isna(value) or not math.isfinite(float(value)):
            return 'background-color: #d1d5db; color: #17212b'
        fraction = (float(value) - low) / (high - low) if high > low else 0.5
        start, end, weight = ((248, 113, 113), (252, 211, 77), fraction * 2) if fraction <= .5 else ((252, 211, 77), (74, 222, 128), (fraction - .5) * 2)
        rgb = tuple(round(a + (b - a) * weight) for a, b in zip(start, end))
        return 'background-color: #%02x%02x%02x; color: #17212b' % rgb
    return frame.map(style)


def colored_snr_table_html(frame):
    """Render explicit inline cell colors instead of relying on grid Styler support."""
    styles = snr_table_styles(frame)
    header_style = 'padding:12px 16px;background:#243247;color:#ffffff;text-align:right;border:1px solid #64748b'
    parts = ['<div style="overflow-x:auto"><table aria-label="Modeled SNR outlook" style="width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums">',
             f'<thead><tr><th scope="col" style="{header_style};text-align:left">Frequency (MHz)</th>']
    parts.extend(f'<th scope="col" style="{header_style}">{escape(str(column))}</th>' for column in frame.columns)
    parts.append('</tr></thead><tbody>')
    for index, row in frame.iterrows():
        parts.append(f'<tr><th scope="row" style="{header_style};text-align:left">{float(index):g}</th>')
        for column, value in row.items():
            label=f'{float(value):.1f}' if pd.notna(value) and math.isfinite(float(value)) else '—'
            parts.append(f'<td style="{styles.loc[index, column]};padding:12px 16px;text-align:right;border:1px solid #64748b;font-weight:600">{label}</td>')
        parts.append('</tr>')
    parts.append('</tbody></table></div>')
    return ''.join(parts)


