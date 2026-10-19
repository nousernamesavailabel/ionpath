"""Independent local antenna defaults for station slots A and B."""
import json
import math
import os
from pathlib import Path
import tempfile

from runtime_paths import private_path, public_mode
from antennas import defaults, KINDS, PRESETS

SETTINGS = Path(__file__).resolve().parent / '.cache/settings'


def settings_path(endpoint):
    if endpoint not in ('A', 'B'):
        raise ValueError('Unknown station slot.')
    return private_path(f'.cache/settings/antenna_{endpoint}.json') if public_mode() else SETTINGS / f'antenna_{endpoint}.json'


def validate(record):
    if not isinstance(record, dict) or record.get('preset') not in PRESETS:
        raise ValueError('Invalid saved antenna preset.')
    config = record.get('config')
    if not isinstance(config, dict) or set(config) != set(defaults()):
        raise ValueError('Invalid saved antenna fields.')
    if config['kind'] not in KINDS or config['shape'] not in ('Square', 'Rectangle', 'Triangle') or config['ground'] not in ('Poor', 'Average', 'Good'):
        raise ValueError('Invalid saved antenna type, shape or ground.')
    for field, low, high in [('length_m', .5, 200), ('height_m', .5, 100), ('bearing_deg', 0, 360),
                             ('counterpoise_m', .5, 100), ('radius_mm', .1, 10), ('loss_db', 0, 30),
                             ('aspect', 1, 10), ('apex_deg', 60, 170)]:
        value = config[field]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
            raise ValueError(f'Invalid saved antenna {field}.')
    return {'preset': record['preset'], 'config': dict(config)}


def load(endpoint):
    path = settings_path(endpoint)
    try:
        return validate(json.loads(path.read_text()))
    except FileNotFoundError:
        return None


def save(endpoint, preset, config):
    record = validate({'preset': preset, 'config': config})
    path = settings_path(endpoint)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, prefix=f'.antenna_{endpoint}_', delete=False) as output:
            temporary = Path(output.name)
            json.dump(record, output, indent=2)
            output.write('\n')
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return record
