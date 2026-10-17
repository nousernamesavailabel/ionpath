"""Local ITU-R P.533 monthly-median baseline; never calibrated link success."""
import io
import math
import os
import re
from pathlib import Path
import subprocess
import tempfile

import pandas as pd

MODEL_HOME = Path(__file__).resolve().parent / '.cache/iturhf/v14.3'
HF_FREQUENCIES = (1.9, 3.75, 5.35, 7.15, 10.125, 14.175, 18.1, 21.225, 24.94, 28.5)
NOISE_CHOICES = ('QUIETRURAL', 'RURAL', 'RESIDENTIAL', 'CITY')


def frequencies_from_text(text):
    values = sorted(set(float(x.strip()) for x in text.split(',') if x.strip()))
    if not values or len(values) > 20 or any(not math.isfinite(x) or not 1.6 <= x <= 30 for x in values):
        raise ValueError('Enter 1–20 distinct frequencies between 1.6 and 30 MHz.')
    if any(b - a < .02 for a, b in zip(values, values[1:])):
        raise ValueError('Model frequencies must be at least 0.02 MHz apart (report precision).')
    return tuple(values)


def model_ready(home=MODEL_HOME):
    return all((home / name).is_file() for name in ('ITURHFProp', 'libp533.so', 'libp372.so')) and all(
        (home / 'data' / name).is_file() for name in
        [*(f'ionos{i:02}.bin' for i in range(1, 13)), *(f'COEFF{i:02}W.txt' for i in range(1, 13)), 'P1239-3 Decile Factors.txt'])


def input_text(tx, rx, year, month, frequencies, ssn, power_w, bandwidth_hz, tx_gain, rx_gain, noise, data):
    for lat, lon in (tx, rx):
        if not math.isfinite(lat) or not math.isfinite(lon) or not -90 <= lat <= 90 or not -180 <= lon <= 180:
            raise ValueError('Invalid model endpoint coordinates.')
    if tx == rx:
        raise ValueError('Model endpoints must be different.')
    frequencies_from_text(','.join(map(str, frequencies)))
    if not 1900 <= year <= 2100 or not 1 <= month <= 12 or not 1 <= ssn <= 311:
        raise ValueError('Model requires year 1900–2100, month 1–12 and smoothed SSN 1–311.')
    if not all(math.isfinite(v) for v in (ssn, power_w, bandwidth_hz, tx_gain, rx_gain)) or not 1 <= power_w <= 100000 or not 1 <= bandwidth_hz <= 100000:
        raise ValueError('Invalid power, bandwidth or antenna gain.')
    if noise not in NOISE_CHOICES or not -30 <= tx_gain <= 40 or not -30 <= rx_gain <= 40:
        raise ValueError('Invalid noise environment or antenna gain.')
    lines = [
        'PathName "A-B monthly baseline"', 'PathTXName "TX"', 'PathRXName "RX"',
        f'Path.L_tx.lat {tx[0]}', f'Path.L_tx.lng {tx[1]}',
        f'Path.L_rx.lat {rx[0]}', f'Path.L_rx.lng {rx[1]}',
        'TXAntFilePath "ISOTROPIC"', 'RXAntFilePath "ISOTROPIC"',
        f'TXGOS {tx_gain}', f'RXGOS {rx_gain}', 'AntennaOrientation "TX2RX"',
        f'Path.year {year}', f'Path.month {month}',
        # The wrapper subtracts one: input 1..24 represents internal UTC 0..23.
        'Path.hour ' + ','.join(str(i) for i in range(1, 25)),
        f'Path.SSN {int(round(ssn))}', 'Path.frequency ' + ','.join(map(str, frequencies)),
        f'Path.txpower {10 * math.log10(power_w / 1000)}', f'Path.BW {bandwidth_hz}',
        'Path.SNRr 0', 'Path.SNRXXp 50', f'Path.ManMadeNoise "{noise}"',
        'Path.Modulation "ANALOG"', 'Path.SorL "SHORTPATH"',
        'RptFileFormat "RPT_D | RPT_BMUF | RPT_OPMUF | RPT_PR | RPT_SNR"',
        f'DataFilePath "{data.resolve()}/"',
    ]
    # A one-point receive grid prevents accidental geographic sweeps.
    for corner in ('LL', 'LR', 'UL', 'UR'):
        lines += [f'{corner}.lat {rx[0]}', f'{corner}.lng {rx[1]}']
    return '\n'.join(lines) + '\n'


def parse_report(text, expected_frequencies):
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines) if line.startswith('month,hour,frequency,')), None)
    if start is None:
        raise ValueError('ITU model did not produce a recognized CSV report.')
    frame = pd.read_csv(io.StringIO('\n'.join(lines[start:])))
    required = ['month', 'hour', 'frequency', 'distance', 'BMUF', 'OPMUF', 'PR', 'SNR']
    if not set(required) <= set(frame.columns):
        raise ValueError('ITU report is missing required columns.')
    for column in required:
        frame[column] = pd.to_numeric(frame[column], errors='raise')
        if not frame[column].map(math.isfinite).all():
            raise ValueError('ITU report contains non-finite results.')
    if len(frame) != 24 * len(expected_frequencies) or frame.duplicated(['hour', 'frequency']).any() or set(frame.hour) != set(range(1, 25)):
        raise ValueError('ITU model returned incomplete hourly coverage.')
    if len(frame.frequency.unique()) != len(expected_frequencies) or any(
        min(abs(actual - expected) for actual in frame.frequency) > .011 for expected in expected_frequencies
    ):
        raise ValueError('ITU model returned unexpected frequencies.')
    frame['frequency'] = frame.frequency.map(lambda value: min(expected_frequencies, key=lambda f: abs(f - value)))
    frame['utc_hour'] = frame.hour.astype(int) - 1
    return frame.rename(columns={'frequency': 'frequency_mhz', 'distance': 'distance_km',
                                 'BMUF': 'basic_muf_mhz', 'OPMUF': 'operational_muf_mhz',
                                 'PR': 'received_power_dbw', 'SNR': 'median_snr_db'}).drop(columns=['hour'])


def run_model(tx, rx, year, month, frequencies=HF_FREQUENCIES, ssn=100, power_w=100,
              bandwidth_hz=2400, tx_gain=0, rx_gain=0, noise='RURAL', home=MODEL_HOME, antenna_tx=None, antenna_rx=None):
    if not model_ready(home):
        raise FileNotFoundError('ITU model unavailable. Run .venv/bin/python scripts/install_itu_model.py first.')
    config = input_text(tx, rx, year, month, frequencies, ssn, power_w, bandwidth_hz, tx_gain, rx_gain, noise, home / 'data')
    if antenna_tx is not None or antenna_rx is not None:
        from antennas import pattern_file, defaults
        tx_config = defaults() | (antenna_tx or {})
        rx_config = defaults() | (antenna_rx or {})
        if tx_config['kind'] == rx_config['kind'] == 'Isotropic reference':
            return run_model(tx, rx, year, month, frequencies, ssn, power_w, bandwidth_hz,
                             tx_gain-tx_config['loss_db'], rx_gain-rx_config['loss_db'], noise, home)
        frames = []
        for frequency in frequencies:
            single = input_text(tx, rx, year, month, (frequency,), ssn, power_w, bandwidth_hz, tx_gain, rx_gain, noise, home / 'data')
            antenna_details = {}
            for role, antenna, offset in [('TX', tx_config, tx_gain), ('RX', rx_config, rx_gain)]:
                if antenna['kind'] == 'Isotropic reference':
                    single = single.replace(f'{role}GOS {offset}', f'{role}GOS {offset-antenna["loss_db"]}')
                else:
                    # The ITU wrapper ignores GOS for imported patterns: apply offsets in the pattern.
                    antenna = dict(antenna)
                    if offset != 0:
                        raise ValueError('Use antenna loss inputs rather than a separate gain offset with NEC patterns.')
                    path, impedance = pattern_file(antenna, frequency)
                    antenna_details.update({f'{role.lower()}_antenna_{key}': value for key, value in impedance.items()})
                    single = single.replace(f'{role}AntFilePath "ISOTROPIC"', f'{role}AntFilePath "{path.resolve()}"')
            single = single.replace('AntennaOrientation "TX2RX"', 'AntennaOrientation "ARBITRARY"\nTXBearing 0\nRXBearing 0')
            frame = parse_report(_execute_model(single, home, csv=True), (frequency,))
            for name, value in antenna_details.items():
                frame[name] = value
            frames.append(frame)
        return pd.concat(frames, ignore_index=True)
    return parse_report(_execute_model(config, home, csv=True), frequencies)


def _execute_model(config, home, csv=False):
    env = dict(os.environ)
    env['LD_LIBRARY_PATH'] = str(home.resolve()) + (':' + env['LD_LIBRARY_PATH'] if env.get('LD_LIBRARY_PATH') else '')
    with tempfile.TemporaryDirectory(prefix='hf-model-') as directory:
        work = Path(directory)
        (work / 'input.in').write_text(config)
        result = subprocess.run([str((home / 'ITURHFProp').resolve()), '-s', *(['-c'] if csv else []), 'input.in', 'output.csv'],
                                cwd=work, env=env, capture_output=True, text=True, timeout=60)
        output = work / 'output.csv'
        if result.returncode or not output.exists():
            raise RuntimeError('ITU model failed: ' + (result.stdout + result.stderr)[-1500:])
        return output.read_text()



def parse_control_points(text):
    """Parse only the F2 diagnostic fields; never expose dump reliability output."""
    length = re.search(r'^\s*distance\s*=\s*([0-9.]+)', text, re.M)
    if not length or not 0 < float(length.group(1)) <= 7000:
        raise ValueError('F2 control-point comparison supports short paths up to 7000 km only.')
    markers = list(re.finditer(r'\*\s+Control Point\s+-\s+([^*\n]+)\*', text))
    points = []
    for index, marker in enumerate(markers):
        block = text[marker.end():markers[index+1].start() if index+1 < len(markers) else len(text)]
        def value(pattern):
            match = re.search(pattern, block, re.M)
            return float(match.group(1)) if match else None
        lat = value(r'^\s*Latitude\s*=\s*[-0-9.]+\s*\(\s*([-0-9.]+)\)')
        lon = value(r'^\s*Longitude\s*=\s*[-0-9.]+\s*\(\s*([-0-9.]+)\)')
        fof2 = value(r'^\s*foF2\s*=\s*([-0-9.]+)')
        if fof2 == 0:  # Inactive / uninitialized control point.
            continue
        if any(v is None or not math.isfinite(v) for v in (lat, lon, fof2)) or not -90 <= lat <= 90 or not -180 <= lon <= 180 or not 0 < fof2 <= 30:
            raise ValueError('Invalid F2 control-point diagnostic output.')
        points.append({'region': marker.group(1).strip(), 'lat': lat, 'lon': lon, 'fof2_mhz': fof2})
    if not points:
        raise ValueError('No valid F2 control-point baseline available.')
    return points


def run_control_points(tx, rx, target, frequency, ssn, power_w=100, bandwidth_hz=2400,
                       tx_gain=0, rx_gain=0, noise='RURAL', home=MODEL_HOME):
    if not model_ready(home):
        raise FileNotFoundError('ITU model unavailable.')
    config = input_text(tx, rx, target.year, target.month, (frequency,), ssn, power_w,
                        bandwidth_hz, tx_gain, rx_gain, noise, home/'data')
    config = config.replace('Path.hour '+','.join(str(i) for i in range(1,25)), f'Path.hour {target.hour+1}')
    config = config.replace('RPT_D | RPT_BMUF | RPT_OPMUF | RPT_PR | RPT_SNR', 'RPT_DUMPPATH')
    return parse_control_points(_execute_model(config, home))
