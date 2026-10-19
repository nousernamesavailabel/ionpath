"""Reviewable diagnostic bundles; never export reports, uploads or local files."""
from datetime import datetime, timezone
from importlib.metadata import version, PackageNotFoundError
import json

APP_VERSION = '0.1.0-beta.1'


def build_diagnostics(state, current_inputs, include_identity=True):
    result = state.get('path_dashboard') or {}
    history = state.get('wspr_history') or {}
    context = state.get('analysis_context') or {}
    def metadata(value):
        if isinstance(value, dict):
            out = {k: metadata(v) for k, v in value.items() if k not in ('payload', 'raw', 'table', 'rows')}
            if value.get('rows') and isinstance(value['rows'][0], dict):
                out['latest_observation_time'] = max(str(row.get('time', '')) for row in value['rows'])
            return out
        if isinstance(value, (list, tuple)):
            return [metadata(v) for v in value]
        return value
    packages = {}
    for name in ('streamlit', 'numpy', 'pandas', 'necpp', 'requests'):
        try:
            packages[name] = version(name)
        except PackageNotFoundError:
            packages[name] = 'unavailable'
    bundle = {
        'schema_version': 1, 'app_version': APP_VERSION,
        'exported_at': datetime.now(timezone.utc), 'packages': packages,
        'includes_coordinates_and_callsign': include_identity,
        'current_inputs': current_inputs,
        'last_run_inputs': state.get('diagnostic_run_inputs'),
        'last_run': {k: result.get(k) for k in ('generated_at', 'target', 'errors')},
        'reception_error': state.get('reception_error'),
        'source_metadata': {
            'wspr': {k: history.get(k) for k in ('fetched_at', 'start', 'end', 'row_count', 'limited')},
            'weather': metadata(result.get('weather', {})),
            'ionosondes': metadata(result.get('iono')),
            'irtam': metadata(result.get('irtam')),
        },
        'reception_summary': {k: context.get(k) for k in ('source', 'waveform', 'row_limited', 'archive_stats')},
        'model_provenance': metadata((result.get('hf') or {}).get('provenance')),
    }
    if result.get('a') and result.get('b') and result.get('target'):
        from realtime_context import current_context
        bundle['current_conditions'] = current_context(result)
    if not include_identity:
        # Location information can also occur in provider errors, derived control
        # points and provenance. Omit those sections rather than redact strings.
        for key in ('current_inputs', 'last_run_inputs'):
            if bundle[key]:
                bundle[key] = {k: v for k, v in bundle[key].items() if k not in ('a', 'b', 'callsign')}
        bundle['last_run']['errors'] = {k: 'Error details omitted' for k in (result.get('errors') or {})}
        bundle['reception_error'] = 'Error details omitted' if state.get('reception_error') else None
        bundle['source_metadata'] = {'wspr': bundle['source_metadata']['wspr']}
        bundle['model_provenance'] = None
        bundle.pop('current_conditions', None)
    return json.loads(json.dumps(bundle, default=str))


def render_diagnostics(state, current_inputs):
    import streamlit as st
    with st.expander('Beta feedback and diagnostics'):
        st.caption(f'Ionpath {APP_VERSION}. Download this file to attach to your beta feedback. Nothing is sent automatically.')
        include = st.checkbox('Include endpoint coordinates and callsign', value=True, key='diagnostic_identity')
        st.caption('Review the exact download below. When enabled, it includes locations, callsign, antenna/model settings, source metadata and error messages. Raw reception reports, uploads and saved validation observations are excluded.')
        bundle = build_diagnostics(state, current_inputs, include)
        st.json(bundle, expanded=False)
        st.download_button('Download beta diagnostics', json.dumps(bundle, indent=2),
                           file_name=f'ionpath-{APP_VERSION}-diagnostics.json', mime='application/json')
