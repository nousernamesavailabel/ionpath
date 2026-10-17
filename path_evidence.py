"""Keep actual radio roles while associating reports with geographic endpoints."""
import numpy as np
import pandas as pd

DIRECTIONS = ['Both directions', 'A → B', 'B → A']


def select_path(frame, a_center, a_radius, b_center, b_radius, distance, direction='Both directions', callsign=None):
    """Clip both endpoints; count overlapping-circle reports once, forward first.

    a_* / b_* describe geography. tx_* / rx_*, power and snr always describe
    the original reception report, including when B transmitted to A.
    """
    data = frame.copy()
    for role in ('tx', 'rx'):
        for coordinate in ('lat', 'lon'):
            name = f'{role}_{coordinate}'
            if name not in data:
                data[name] = np.nan
            data[name] = pd.to_numeric(data[name], errors='coerce')
    def inside(role, center, radius):
        lat, lon = data[f'{role}_lat'], data[f'{role}_lon']
        return (lat.between(-90, 90) & lon.between(-180, 180) &
                (distance(*center, lat, lon) <= radius))
    forward = inside('tx', a_center, a_radius) & inside('rx', b_center, b_radius)
    reverse = inside('rx', a_center, a_radius) & inside('tx', b_center, b_radius)
    if callsign:
        forward &= data.tx_sign.eq(callsign.strip().upper())
        reverse &= data.rx_sign.eq(callsign.strip().upper())
    if direction == 'A → B':
        reverse &= False
    elif direction == 'B → A':
        forward &= False
    elif direction != 'Both directions':
        raise ValueError('Unknown path direction')
    data['path_direction'] = np.where(forward, 'A → B', 'B → A')
    data = data[forward | reverse].copy()
    is_forward = data.path_direction.eq('A → B')
    for field in ('lat', 'lon', 'loc', 'sign'):
        tx = data.get(f'tx_{field}', pd.Series('', index=data.index))
        rx = data.get(f'rx_{field}', pd.Series('', index=data.index))
        data[f'a_{field}'] = tx.where(is_forward, rx)
        data[f'b_{field}'] = rx.where(is_forward, tx)
    return data
