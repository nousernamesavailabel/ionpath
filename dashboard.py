"""One-run orchestration and compact, independent propagation evidence cards."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json

import pandas as pd
import streamlit as st

from hf_model import HF_FREQUENCIES, NOISE_CHOICES, frequencies_from_text, run_control_points
from measurement_support import assess_support
from model_heatmap import render_model_heatmap
from propagation_panel import modeled_path, refresh_weather, colored_snr_table_html, show_weather
from ionosonde_panel import cached_context, context_table
from space_weather import latest_kp, solar_for_month


def model_settings(a=None, b=None):
    with st.sidebar.expander('HF model settings'):
        st.caption('Defaults: 100 W, 2400 Hz and rural noise. Set each antenna below; NOAA supplies R12 automatically.')
        frequencies = st.text_input('Frequencies to model (MHz, comma separated)', ', '.join(map(str, HF_FREQUENCIES)))
        power = st.number_input('Transmit power in each direction (W)', 1., 100000., 100.)
        bandwidth = st.number_input('Receiver bandwidth (Hz)', 1., 100000., 2400.)
        noise = st.selectbox('Noise environment at both receivers', NOISE_CHOICES, index=1)
        gain_a = gain_b = 0.
        manual = st.checkbox('Use manual SSN (offline fallback)', value=False)
        ssn = st.number_input('Manual smoothed sunspot number (R12)', 1, 311, 100) if manual else None
        try:
            aim_frequencies = frequencies_from_text(frequencies)
        except ValueError:
            aim_frequencies = HF_FREQUENCIES
        aim_frequency = st.selectbox('Aim antennas at frequency (MHz)', aim_frequencies, index=min(range(len(aim_frequencies)), key=lambda i: abs(aim_frequencies[i]-7.15)))
    from antenna_panel import antenna_builder
    with st.sidebar.expander('Antenna A'):
        antenna_a = antenna_builder('A', a, b, aim_frequency)
    with st.sidebar.expander('Antenna B'):
        antenna_b = antenna_builder('B', b, a, aim_frequency)
    return dict(antenna_a=antenna_a, antenna_b=antenna_b, frequency_text=frequencies, power=power, bandwidth=bandwidth, noise=noise,
                gain_a=gain_a, gain_b=gain_b, manual=manual, ssn=ssn)


def run_context_layers(a, b, target, settings):
    """Collect independent sources; one source failure never cancels another."""
    result = {'hf': None, 'iono': None, 'weather': {}, 'errors': {}, 'target': target,
              'a': a, 'b': b, 'generated_at': datetime.now(timezone.utc)}
    with ThreadPoolExecutor(max_workers=3) as pool:
        weather_future = pool.submit(refresh_weather)
        iono_future = pool.submit(cached_context, a, b)
        irtam_future = pool.submit(__import__('realtime_context').load_irtam)
        try:
            result['weather'] = weather_future.result()
        except Exception as exc:
            result['errors']['weather'] = str(exc)
        try:
            solar = None if settings['manual'] else solar_for_month(
                result['weather'].get('solar_observed', {}).get('payload') or [],
                result['weather'].get('solar_predicted', {}).get('payload') or [], target.strftime('%Y-%m'))
            if not settings['manual'] and solar is None:
                raise ValueError('NOAA monthly SSN unavailable. Use the manual fallback under HF model settings.')
            ssn = settings['ssn'] if settings['manual'] else solar['value']
            frequencies = frequencies_from_text(settings['frequency_text'])
            table = modeled_path(a, b, target.year, target.month, frequencies, ssn, settings['power'],
                                 settings['bandwidth'], settings['gain_a'], settings['gain_b'], settings['noise'],
                                 **({k: settings[k] for k in ('antenna_a','antenna_b') if k in settings}))
            provenance = {'model': 'ITU-R P.533-14 / official v14.3 release (reports P533 14.2)',
                          'generated_at': result['generated_at'].isoformat(), 'target_utc': target.isoformat(),
                          'a': a, 'b': b, 'frequencies_mhz': frequencies, 'ssn': ssn,
                          'ssn_source': solar or 'Manual R12', 'power_w_each_direction': settings['power'],
                          'bandwidth_hz': settings['bandwidth'], 'antenna_gain_dbi_a': settings['gain_a'],
                          'antenna_gain_dbi_b': settings['gain_b'], 'noise_at_both_receivers': settings['noise'],
                          'antennas': {k: settings.get(k) for k in ('antenna_a','antenna_b')},
                          'antenna_model': 'NEC-2 copper wire, flat lossy ground, ideal matching; 1 degree pattern samples',
                          'path': 'short great circle, endpoint centers',
                          'noaa_retrievals': {name: {key: value for key, value in feed.items() if key != 'payload'}
                                             for name, feed in result['weather'].items()}}
            result['hf'] = {'table': table, 'provenance': provenance, 'target': target}
        except Exception as exc:
            result['errors']['hf'] = str(exc)
        try:
            result['iono'] = iono_future.result()
        except Exception as exc:
            result['errors']['iono'] = str(exc)
        try:
            result['irtam'] = irtam_future.result()
        except Exception:
            result['irtam'] = {'error': 'IRTAM map unavailable.'}
    if result['hf']:
        hf = result['hf']
        reference = float(model_overview(hf).index[0])
        hf['support_reference_frequency_mhz'] = reference
        try:
            hf['support_control_points'] = run_control_points(a, b, target, reference, ssn,
                settings['power'], settings['bandwidth'], settings['gain_a'], settings['gain_b'], settings['noise'])
        except Exception as exc:
            # Diagnostics must never cancel or modify a successful outlook.
            hf['support_error'] = str(exc)
        hf['provenance']['measurement_support'] = measurement_check(result)
    return result


def measurement_check(result):
    hf = result.get('hf') or {}
    iono = result.get('iono') or {}
    assessment = assess_support(hf.get('support_control_points', []), iono.get('stations', []), result['target'])
    if hf.get('support_error'):
        assessment['status'] = 'Cannot assess'
        assessment['reason'] = 'Model control-region diagnostics unavailable: ' + hf['support_error']
    assessment['reference_frequency_mhz'] = hf.get('support_reference_frequency_mhz')
    assessment['direction'] = 'A→B F2 baseline; not a check of every band or propagation mode'
    return assessment


def model_overview(result):
    selected = result['table'][result['table'].utc_hour.eq(result['target'].hour)]
    frame = selected.pivot(index='frequency_mhz', columns='direction', values='median_snr_db')
    frame['Weaker'] = frame.min(axis=1)
    frame = frame.sort_values('Weaker', ascending=False)
    frame.index.name = 'MHz'
    return frame


def render_dashboard(bands, waveform):
    result = st.session_state.get('path_dashboard')
    if not result:
        return
    st.caption(f"Run {result['generated_at']:%H:%M UTC} · target {result['target']:%Y-%m-%d %H:%M UTC} · Each source answers a different question; their SNR values are not interchangeable.")
    observed, modeled, measured = st.columns([1, 1, 1], gap='small')
    with observed, st.container(border=True):
        st.markdown(f'### {waveform} evidence')
        st.caption('Measured SNR (dB); TX power unknown.' if waveform == 'FT8' else 'Observed SNR (dB) at 1 W · — = unknown.')
        if st.session_state.get('reception_error'):
            st.warning(st.session_state['reception_error'])
        stored = st.session_state.get('results_mgrs')
        if stored is None:
            st.info('No reception evidence available for this run. That does not mean the bands are closed.')
        else:
            scores, matched = stored[:2]
            if scores.empty:
                st.info('No reports at the selected hour. Path availability is unknown.')
            else:
                frame = scores[['Band', 'A → B median SNR (dB)', 'B → A median SNR (dB)', 'Spots']].rename(
                    columns={'A → B median SNR (dB)': 'A→B dB', 'B → A median SNR (dB)': 'B→A dB', 'Spots': 'Reports'})
                for column in ('A→B dB', 'B→A dB'):
                    frame[column] = frame[column].map(lambda value: '—' if pd.isna(value) else f'{value:.1f}')
                st.dataframe(frame, height=278, hide_index=True, use_container_width=True)
                st.write(f"**Strongest observed:** {scores.iloc[0]['Band']}")
                st.caption(f"{len(matched):,} reports in the target window · {int(scores['Distinct UTC days'].max())} UTC days at most per band.")
            context = st.session_state.get('analysis_context', {})
            if context.get('row_limited'):
                st.warning('History reached the row limit; coverage is incomplete.')
            if context.get('archive_stats'):
                stats = context['archive_stats']
                st.caption(f"Archive: {stats['date']} UTC only.")
                if any(stats.get('timestamp_offsets_seconds', [])):
                    st.warning('Archive time discrepancy; hourly evidence is not validated.')
        st.caption('Measured FT8 SNR, power unknown.' if waveform == 'FT8' else 'WSPR SNR normalized to 1 W. Ranked by stronger direction; this is not a two-way guarantee.')
    with modeled, st.container(border=True):
        st.markdown('### HF outlook')
        st.caption('Monthly SNR (dB), including bands with no reports.')
        hf = result['hf']
        if hf is None:
            st.warning(result['errors'].get('hf', 'Model unavailable.'))
        else:
            metadata = hf['provenance']
            source = metadata['ssn_source']
            source_label = source['source'] if isinstance(source, dict) else source
            st.write(f"**R12 used: {metadata['ssn']}** · {source_label}")
            frame = model_overview(hf)
            # Compact HTML cells retain visible colors in light and dark themes.
            html = colored_snr_table_html(frame).replace('padding:12px 16px', 'padding:3px 8px').replace('width:100%;border-collapse', 'font-size:12px;line-height:1.3;width:100%;border-collapse').replace('Frequency (MHz)', 'MHz')
            st.html(html)
            antennas = metadata.get('antennas') or {'antenna_a': None, 'antenna_b': None}
            st.caption('Antennas: ' + ' · '.join(f"{name[-1].upper()}: {config['kind']} at {config['height_m']:g} m" if config and config['kind'] != 'Isotropic reference' else f"{name[-1].upper()}: isotropic reference" for name, config in antennas.items()))
            st.caption(f"{metadata['power_w_each_direction']:g} W · {metadata['bandwidth_hz']:g} Hz · {metadata['noise_at_both_receivers'].lower()} noise · {hf['target']:%Y-%m}")
        if hf:
            assessment = measurement_check(result)
            st.write(f"**Measurement check: {assessment['status']}**")
            st.caption(assessment['reason'])
        st.caption('Red → green = lower → higher modeled SNR within this table. F2 checks are experimental; modeled SNR is unchanged.')
    with measured, st.container(border=True):
        st.markdown('### Ionosonde context')
        st.caption('What the ionosphere is doing now near this path.')
        iono = result['iono']
        if iono is None:
            st.warning(result['errors'].get('iono', 'Ionosonde data unavailable.'))
        else:
            if iono.get('warnings'):
                st.warning(iono['warnings'][0])
            if any(station.get('cache_warning') for station in iono['stations']):
                st.caption('Showing previously retrieved measurements where available; their observation times and ages are unchanged.')
            frame = context_table(iono, datetime.now(timezone.utc))
            if not frame.empty:
                frame['_order'] = frame.Status.map({'Recent measurement': 0, 'Latest foF2 unavailable': 1, 'Stale (>60 min)': 2}).fillna(3)
                frame = frame.sort_values('_order', kind='stable')
                small = frame[['Station', 'foF2 (MHz)', 'Age (min)', 'Status']].copy()
                small['Station'] = small.Station.str.replace('IDAHO NATIONAL LAB', 'Idaho', regex=False).str.replace(r' \([^)]+\)$', '', regex=True).str.title()
                small['Status'] = small.Status.replace({'Recent measurement': 'Recent', 'Latest foF2 unavailable': 'Missing foF2', 'Stale (>60 min)': 'Stale', 'No measurements in six-hour query': 'No data', 'Retrieval failed': 'Failed', 'Future timestamp — check source': 'Future'})
                st.dataframe(small.rename(columns={'foF2 (MHz)': 'foF2 MHz', 'Age (min)': 'Age min'}), height=245, hide_index=True, use_container_width=True,
                             column_config={'foF2 MHz': st.column_config.NumberColumn(format='%.2f')})
                recent = int(frame.Status.eq('Recent measurement').sum())
                st.write(f'**{recent} of {len(frame)} stations** have recent usable foF2.')
            st.caption(f"Checked {iono['retrieved_at']:%H:%M UTC} · per-station cache; observation ages shown above.")
        weather = result['weather']
        try:
            kp = latest_kp(weather.get('kp', {}).get('payload') or [])
            st.write(f"**Planetary Kp {kp['value']:.2f}** · {kp['time']:%H:%M UTC}")
            if kp['value'] >= 5:
                st.warning('Geomagnetic storm: monthly model may differ substantially.')
            if (datetime.now(timezone.utc)-kp['time']).total_seconds() > 21600:
                st.caption('Kp is stale (>6 hours).')
        except (ValueError, TypeError):
            st.caption('Planetary Kp unavailable.')
        if any(feed.get('error') for feed in weather.values()) or result['errors'].get('weather'):
            st.caption('Some NOAA refreshes failed; inspect source details below for cached data ages.')
        st.caption('foF2 is a vertical measurement, not this path’s MUF. Ages >60 minutes are flagged stale. Scaling quality is in details.')
    st.caption('Read together: use the model to identify candidates, reception history to find supporting evidence, and ionosondes/space weather to judge how current conditions differ. None alone establishes a usable voice link.')
    from realtime_context import render_current_context
    conditions = render_current_context(result)
    if result['hf']:
        result['hf']['current_conditions'] = conditions
    st.subheader('24-hour frequency outlook')
    if result['hf']:
        render_model_heatmap(result['hf'], result.get('timezone', st.session_state.get('analysis_context', {}).get('timezone', 'UTC')))
    else:
        st.info('Run an analysis with a successful HF model to explore all hours and frequencies.')
    with st.expander('Hourly model direction chart'):
        if result['hf']:
            table = result['hf']['table']
            direction = st.radio('Hourly model direction', ['A→B', 'B→A'], horizontal=True)
            hourly = table[table.direction.eq(direction)].pivot(index='utc_hour', columns='frequency_mhz', values='median_snr_db')
            hourly.columns = [f'{frequency:g} MHz' for frequency in hourly.columns]
            hourly.index.name = 'UTC hour'
            st.line_chart(hourly, x_label='UTC hour', y_label='Monthly median modeled SNR (dB)')
            st.caption('Monthly model values for the selected direction, using the same results as the heatmaps.')
        else:
            st.info('No model output for this run.')
    with st.expander('HF model assumptions and downloads'):
        hf = result['hf']
        if hf:
            table = hf['table']
            assessment = measurement_check(result)
            st.markdown('**Why the measurement check says this**')
            st.write(f"{assessment['status']}: {assessment['reason']}")
            if assessment.get('reference_frequency_mhz'):
                st.caption(f"Reference: {assessment['reference_frequency_mhz']:g} MHz, A→B model F2 control regions. The sounding and model foF2 values can be up to 300 km apart; this is an approximate regional comparison, not a station-exact baseline.")
            if assessment['regions']:
                st.dataframe(pd.DataFrame(assessment['regions']), hide_index=True, use_container_width=True)
            st.caption('Experimental screening rules: every reported F2 control region must have a sounding within 300 km, observed within 30 minutes of both now and the target time, with autoscaling CS ≥80 or manual scaling and no supplied foF2 qualifier. Agreement tolerance: ±20%. These are conservative application heuristics, not validated prediction or success thresholds. Partial coverage always gives Cannot assess, even if a sampled region differs. foF2 agreement does not validate absorption, noise, SNR, voice usability, E-layer modes or every band.')
            antenna_columns = [name for name in table.columns if '_antenna_' in name]
            if antenna_columns:
                st.markdown('**Antenna feedpoint impedance by frequency**')
                st.dataframe(table.loc[table.direction.eq('A→B'), ['frequency_mhz', *antenna_columns]].drop_duplicates('frequency_mhz'), hide_index=True)
                st.caption('TX = A, RX = B in this table. High resistance or reactance can require substantial matching losses; the pattern model assumes ideal matching and only the additional losses you entered. This is not an SWR prediction for the actual feedline/tuner.')
            export_provenance = {**hf['provenance'], 'measurement_support': assessment,
                                 'current_conditions': hf.get('current_conditions', {})}
            st.json(export_provenance, expanded=False)
            st.download_button('Download hourly model CSV', table.to_csv(index=False), 'hf_model.csv', 'text/csv')
            st.download_button('Download model assumptions and provenance', json.dumps(export_provenance, indent=2), 'hf_model_provenance.json', 'application/json')
        else:
            st.info('No model output for this run.')
    with st.expander('Ionosonde and NOAA source details'):
        from ionosonde_panel import render_ionosonde_details
        if result['iono']:
            render_ionosonde_details(result['iono'])
        if result['weather']:
            show_weather(result['weather'], result['a'], result['b'], result['target'])

    from validation_log import render_validation
    if result.get('validation_error'):
        st.warning('Prediction log could not save this run: ' + result['validation_error'])
    render_validation()
