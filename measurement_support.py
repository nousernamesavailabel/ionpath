"""Experimental foF2 baseline comparison; no modification of model SNR or R12."""
from datetime import datetime, timezone

from ionosonde import distance

RULES = {'max_distance_km': 300, 'max_age_minutes': 30, 'min_autoscaling_cs': 80,
         'relative_fof2_tolerance': 0.20, 'max_target_offset_minutes': 30}


def assess_support(points, stations, target, now=None):
    now = now or datetime.now(timezone.utc)
    result = {'status': 'Cannot assess', 'reason': '', 'regions': [], 'rules': dict(RULES),
              'evaluated_at': now.isoformat(), 'covered_regions': 0, 'total_regions': len(points),
              'scope': 'Experimental F2 control-point baseline check, not SNR or link-success validation.'}
    target = target.replace(minute=0, second=0, microsecond=0)
    result['model_hour_utc'] = target.isoformat()
    if abs((now-target).total_seconds()) > 60*RULES['max_target_offset_minutes']:
        result['reason'] = 'Current ionosondes cannot assess this target time. Choose a model UTC hour within 30 minutes of now.'
        return result
    if not points:
        result['reason'] = 'No corresponding model F2 control-point baseline is available.'
        return result
    eligible = []
    rejected = {'no_reading': 0, 'time': 0, 'quality': 0, 'missing_fof2': 0}
    for station in stations:
        if not station.get('rows'):
            rejected['no_reading'] += 1
            continue
        row = station['rows'][-1]
        if row.get('fof2_mhz') is None:
            rejected['missing_fof2'] += 1
            continue
        age = (now-row['time']).total_seconds()/60
        if not 0 <= age <= RULES['max_age_minutes'] or abs((target-row['time']).total_seconds()) > 60*RULES['max_target_offset_minutes']:
            rejected['time'] += 1
            continue
        cs = row.get('confidence', -1)
        if not (cs == 999 or RULES['min_autoscaling_cs'] <= cs <= 100) or row.get('fof2_qualifier', '') not in ('', '//'):
            rejected['quality'] += 1
            continue
        eligible.append((station, row))
    result['excluded_stations'] = rejected
    for point in points:
        detail = {'Region': point['region'], 'Latitude': point['lat'], 'Longitude': point['lon'],
                  'Model foF2 (MHz)': point['fof2_mhz'], 'Station': None, 'Distance (km)': None,
                  'Measured foF2 (MHz)': None, 'Difference (%)': None, 'Comparison': 'No eligible reading'}
        if eligible:
            station, row = min(eligible, key=lambda pair: distance((point['lat'], point['lon']), (pair[0]['lat'], pair[0]['lon'])))
            km = distance((point['lat'], point['lon']), (station['lat'], station['lon']))
            detail.update({'Station': station['code'], 'Distance (km)': round(km),
                           'Observation UTC': row['time'].isoformat(), 'Autoscaling CS': row['confidence']})
            if km > RULES['max_distance_km']:
                detail['Comparison'] = 'Too far from control region'
            else:
                change = row['fof2_mhz']/point['fof2_mhz']-1
                detail.update({'Measured foF2 (MHz)': row['fof2_mhz'], 'Difference (%)': round(100*change, 1),
                               'Comparison': 'Within tolerance' if abs(change) <= RULES['relative_fof2_tolerance']+1e-12 else 'Outside tolerance'})
                result['covered_regions'] += 1
        result['regions'].append(detail)
    covered, total = result['covered_regions'], result['total_regions']
    if covered < total:
        result['reason'] = f'{covered}/{total} model control regions have a nearby, recent, quality-qualified reading.'
        if not eligible:
            result['reason'] += ' Available readings are missing, too old, or below the quality requirement.'
    elif any(row['Comparison'] == 'Outside tolerance' for row in result['regions']):
        result['status'] = 'Disagrees with F2 baseline'
        result['reason'] = 'All control regions are sampled, but at least one foF2 reading differs by more than 20%.'
    else:
        result['status'] = 'Supports F2 baseline'
        result['reason'] = 'All control regions have nearby foF2 readings within 20% of their monthly baseline.'
    return result
