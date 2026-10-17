"""Historical evidence summaries; labels describe observations, never link reliability."""
import math
from datetime import datetime, time, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd


def grid_uncertainty(grid, latitude):
    """Conservative half-diagonal in km of a validated Maidenhead cell."""
    length = len(str(grid).strip())
    width, height = 2.0, 1.0
    if length >= 6:
        width, height = width / 24, height / 24
    if length >= 8:
        width, height = width / 10, height / 10
    return math.hypot(width * 111.32, height * 111.32) / 2


def origin_history(df, latitude, longitude, radius, selected_uncertainty, grid_coords, haversine, prefix="tx"):
    """Classify origins by cell overlap. Reported grids take precedence over centers."""
    result = df.copy()
    origins = []
    @lru_cache(maxsize=None)
    def grid_origin(grid):
        lat, lon = grid_coords(grid)
        return lat, lon, grid_uncertainty(grid, lat)
    for record in result.to_dict('records'):
        try:
            grid = str(record.get(f'{prefix}_loc', '')).strip().upper()
            lat, lon, uncertainty = grid_origin(grid)
            source = f'Grid {grid}'
        except (ValueError, TypeError, IndexError):
            try:
                lat, lon = float(record.get(f'{prefix}_lat')), float(record.get(f'{prefix}_lon'))
                if not (math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180):
                    raise ValueError('Unknown origin')
                uncertainty = 0.0
                source = 'Reported coordinates (precision unknown)'
            except (ValueError, TypeError):
                lat, lon, uncertainty, source = np.nan, np.nan, np.nan, 'Unknown'
        origins.append((lat, lon, uncertainty, source))
    coords = pd.DataFrame(origins, columns=['origin_lat', 'origin_lon', 'origin_uncertainty_km', 'origin_source'], index=result.index)
    for column in ['origin_lat', 'origin_lon', 'origin_uncertainty_km']:
        coords[column] = coords[column].astype(float)
    result = pd.concat([result, coords], axis=1)
    result['origin_distance_km'] = haversine(latitude, longitude, result.origin_lat, result.origin_lon)
    allowance = result.origin_uncertainty_km + selected_uncertainty
    result['origin_status'] = np.select([
        result.origin_distance_km.isna(),
        result.origin_distance_km + allowance <= radius,
        result.origin_distance_km - allowance <= radius,
    ], ['Unknown', 'Within radius', 'Boundary uncertain'], default='Outside radius')
    return result


def filter_origins(history, enabled, include_unknown):
    if not enabled:
        return history.copy()
    allowed = ['Within radius', 'Boundary uncertain']
    if include_unknown:
        allowed.append('Unknown')
    return history[history.origin_status.isin(allowed)].copy()


def coverage_flags(group):
    flags = []
    receivers = group.rx_sign.nunique()
    days = group.time.dt.date.nunique()
    if receivers == 1:
        flags.append('Only one receiver identity/grid')
    if days <= 2:
        flags.append(f'Reports from only {days} UTC day(s)')
    if group.dest_km.min() > 200:
        flags.append(f'Nearest B endpoint {group.dest_km.min():.0f} km away')
    busiest = group.time.dt.date.value_counts().iloc[0]
    if days > 1 and busiest / len(group) > 0.5:
        flags.append(f'{busiest / len(group):.0%} of reports on one UTC day')
    if ('mode' not in group or not group['mode'].eq('FT8').all()) and (~np.isfinite(group.power)).any():
        flags.append(f'{(~np.isfinite(group.power)).sum()} reports lack TX power')
    if 'origin_status' in group:
        for status in ['Outside radius', 'Boundary uncertain', 'Unknown']:
            count = int(group.origin_status.eq(status).sum())
            if count:
                flags.append(f'{count} origins: {status.lower()}')
    return flags


def ranking_explanation(group,waveform='WSPR'):
    days = group.time.dt.date.nunique()
    receivers = group.rx_sign.nunique()
    total = float(group.weight.sum())
    normalized=group[group.wspr_snr_1w.notna()]
    factor=(0.4+0.6*float(np.average(np.clip((normalized.wspr_snr_1w+30)/30,0,1),weights=normalized.weight))) if not normalized.empty else 0.7
    if waveform=='FT8':
        return (f'{len(group):,} FT8 reports contribute {total:.1f} weighted reports across {receivers} receiver identities and {days} UTC day(s). '
                f'Coverage activity heuristic = {math.log1p(total):.2f} × {math.sqrt(receivers):.2f} × {math.sqrt(days):.2f}. No power normalization or SNR factor; not comparable to WSPR scores.')
    explanation = (f'{len(group):,} reports contribute {total:.1f} weighted reports; '
                   f'{receivers} receiver identities/grids across {days} UTC days. '
                   f'Nearest B endpoint {group.dest_km.min():.0f} km; median distance {group.dest_km.median():.0f} km. '
                   f'Volume term {math.log1p(total):.2f} × receiver factor {math.sqrt(receivers):.2f} '
                   f'× day factor {math.sqrt(days):.2f} × SNR factor {factor:.2f}.')
    explanation += f' {len(normalized)}/{len(group)} reports normalized to 1 W WSPR.'
    if normalized.empty:
        explanation += ' Cannot normalize: no reported finite TX power; SNR factor uses neutral midpoint 0.7.'
    explanation += (f' Busiest receiver identity/grid contributes {group.rx_sign.value_counts().iloc[0] / len(group):.0%} '
                    f'of raw reports; busiest UTC day contributes {group.time.dt.date.value_counts().iloc[0] / len(group):.0%}.')
    return explanation


def hourly_evidence(df, latitude, longitude, effective_radius, radius, haversine):
    full = df.copy()
    full['dest_km'] = haversine(latitude, longitude, full.get('b_lat',full.rx_lat), full.get('b_lon',full.rx_lon))
    full = full[full.dest_km <= effective_radius].copy()
    full['utc_hour'] = full.time.dt.hour
    full['utc_day'] = full.time.dt.date
    full['proximity_weight'] = np.exp(-0.5 * (full.dest_km / max(radius / 2, 50)) ** 2)
    details = full.groupby(['band', 'utc_hour']).agg(
        weighted_reports=('proximity_weight', 'sum'), reports=('time', 'size'),
        days=('utc_day', 'nunique'), receivers=('rx_sign', 'nunique'),
        nearest_km=('dest_km', 'min')).reset_index()
    if 'path_direction' in full:
        for label,column in [('A → B','a_to_b'),('B → A','b_to_a')]:
            counts=full.assign(count=full.path_direction.eq(label).astype(int)).groupby(['band','utc_hour'])['count'].sum()
            details[column]=[int(counts.get((row.band,row.utc_hour),0)) for row in details.itertuples()]
    return full, details


def hour_label(hour, reference_date, timezone_name):
    instant = datetime.combine(reference_date, time(hour=int(hour)), tzinfo=timezone.utc)
    local = instant.astimezone(ZoneInfo(timezone_name))
    return f'{int(hour):02d}:00–{int(hour):02d}:59 UTC | {local:%Y-%m-%d %H}:00 {local:%Z (%z)}'


def activity_heatmap_data(hourly, bands, reference_date, timezone_name):
    lookup={(int(row.band),int(row.utc_hour)):row for row in hourly.itertuples()}
    times=[]
    for hour in range(24):
        utc=datetime.combine(reference_date,time(hour=hour),tzinfo=timezone.utc)
        local=utc.astimezone(ZoneInfo(timezone_name))
        label=local.strftime('%H:%M' if local.minute else '%H')+f'|({hour:02d})'
        times.append((local.hour*60+local.minute,hour,label,local,utc))
    times.sort(key=lambda value:(value[0],value[1]))
    records=[]
    for band,label in bands.items():
        for _,hour,axis,local,utc in times:
            row=lookup.get((band,hour))
            records.append({'Band':label,'Hour':axis,'utc_hour':hour,
                            'Local time':local.strftime('%Y-%m-%d %H:%M %Z'),
                            'UTC time':utc.strftime('%Y-%m-%d %H:%M UTC'),
                            'Weighted reports':float(row.weighted_reports) if row is not None else 0.,
                            'Reports':int(row.reports) if row is not None else 0,
                            'UTC days':int(row.days) if row is not None else 0,
                            'Receivers':int(row.receivers) if row is not None else 0,
                            'A → B':int(getattr(row,'a_to_b',0)) if row is not None else 0,
                            'B → A':int(getattr(row,'b_to_a',0)) if row is not None else 0})
    return pd.DataFrame(records),[entry[1] for entry in times]
