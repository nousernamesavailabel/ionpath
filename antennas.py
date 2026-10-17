"""NEC-2 copper-wire patterns over flat ground, exported to P.533 Type 13."""
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile

CACHE = Path(__file__).resolve().parent / '.cache/antennas'
VERSION = 'nec2-1'
KINDS = ('Isotropic reference', 'Dipole', 'Inverted V', 'Horizontal loop', 'Vertical loop', 'End-fed wire + counterpoise', 'Vertical + 4 elevated radials')
PRESETS = {'Isotropic reference': {}, '40 m dipole at 5 m': dict(kind='Dipole', length_m=20., height_m=5.),
           '40 m dipole at 10 m': dict(kind='Dipole', length_m=20., height_m=10.),
           '40 m dipole at 20 m': dict(kind='Dipole', length_m=20., height_m=20.),
           '20 m dipole at 10 m': dict(kind='Dipole', length_m=10., height_m=10.),
           '40 m inverted V': dict(kind='Inverted V', length_m=20., height_m=12.),
           '80 m horizontal square loop': dict(kind='Horizontal loop', length_m=84., height_m=12.),
           '40 m vertical delta loop': dict(kind='Vertical loop', length_m=42., height_m=2., shape='Triangle'),
           '20 m elevated vertical': dict(kind='Vertical + 4 elevated radials', length_m=5., height_m=2., counterpoise_m=5.),
           '20 m end-fed wire': dict(kind='End-fed wire + counterpoise', length_m=20., height_m=10., counterpoise_m=4.),
           'Custom': dict(kind='Dipole')}


def defaults(preset='Isotropic reference'):
    return dict(kind='Isotropic reference', length_m=20., height_m=10., bearing_deg=90., shape='Square',
                aspect=2., apex_deg=120., counterpoise_m=5., radius_mm=1., ground='Average',
                loss_db=0., **{}) | PRESETS[preset]


def geometry(config, frequency):
    """Coordinates use X=north, Y=east, Z=height. All dimensions are physical metres."""
    c = defaults() | config
    if c['kind'] not in KINDS:
        raise ValueError('Unknown antenna type.')
    for field, low, high in [('length_m', .5, 200), ('height_m', .5, 100), ('bearing_deg', 0, 360),
                             ('counterpoise_m', .5, 100), ('radius_mm', .1, 10), ('loss_db', 0, 30),
                             ('aspect', 1, 10), ('apex_deg', 60, 170)]:
        if not math.isfinite(c[field]) or not low <= c[field] <= high:
            raise ValueError(f'Invalid antenna {field}.')
    length, h = c['length_m'], c['height_m']
    if c['kind'] == 'Isotropic reference':
        return [], (0, 0)
    segments = []
    if c['kind'] == 'Dipole':
        segments = [((-length/2,0,h), (length/2,0,h))]
    elif c['kind'] == 'Inverted V':
        drop = length/2*math.cos(math.radians(c['apex_deg']/2))
        span = length/2*math.sin(math.radians(c['apex_deg']/2))
        segments = [((-span,0,h-drop),(0,0,h)), ((0,0,h),(span,0,h-drop))]
    elif 'loop' in c['kind']:
        shape = c['shape']
        if shape == 'Triangle':
            side = length/3
            vertices = [(-side/2,0), (side/2,0), (0,side*math.sqrt(3)/2)]
        elif shape in ('Square','Rectangle'):
            ratio = c['aspect'] if shape == 'Rectangle' else 1.
            width = length/(2*(1+ratio)); long = width*ratio
            vertices = [(-long/2,0), (long/2,0), (long/2,width), (-long/2,width)]
        else:
            raise ValueError('Choose Square, Rectangle or Triangle loop shape.')
        points = [(x,y,h) if c['kind']=='Horizontal loop' else (x,0,h+y) for x,y in vertices]
        segments = list(zip(points, points[1:]+points[:1]))
    elif c['kind'] == 'End-fed wire + counterpoise':
        segments = [((0,0,h),(length,0,h)), ((0,0,h),(-c['counterpoise_m'],0,h))]
    else:
        segments = [((0,0,h),(0,0,h+length))]
        for az in range(0,360,90):
            angle = math.radians(az)
            segments.append(((0,0,h),(c['counterpoise_m']*math.cos(angle),c['counterpoise_m']*math.sin(angle),h)))
    if min(p[2] for pair in segments for p in pair) < .5:
        raise ValueError('Antenna wire would be less than 0.5 m above ground. Increase height or change the V angle.')
    angle = math.radians(c['bearing_deg'])
    def rotate(p):
        x,y,z=p
        # NEC phi is counter-clockwise from X; X is geographic north here.
        return (x*math.cos(angle)-y*math.sin(angle), x*math.sin(angle)+y*math.cos(angle),z)
    wires=[]
    for index,(a,b) in enumerate(segments,1):
        distance=math.dist(a,b)
        count=max(5,int(math.ceil(distance/(299.792458/frequency*.035))))
        if count%2==0: count+=1
        wires.append((index,count,rotate(a),rotate(b)))
    if sum(w[1] for w in wires)>1200:
        raise ValueError('Antenna geometry exceeds the 1200-segment calculation limit.')
    feed = (1, wires[0][1]//2+1) if c['kind'] in ('Dipole','Horizontal loop','Vertical loop') else (1, 1 if c['kind']!='Inverted V' else wires[0][1])
    return wires,feed


def solve(config, frequency):
    import necpp as nec
    import numpy as np
    c=defaults() | config
    wires,feed=geometry(c,frequency)
    if not wires:
        return np.full((360,91), -c['loss_db']), {'resistance_ohm': None, 'reactance_ohm': None}
    context=nec.nec_create()
    def checked(code):
        if code:
            raise ValueError('NEC antenna calculation failed: '+nec.nec_error_message())
    try:
        for tag,count,a,b in wires:
            checked(nec.nec_wire(context,tag,count,*a,*b,c['radius_mm']/1000,1,1))
        checked(nec.nec_geometry_complete(context,0))
        soils={'Poor':(5.,.001),'Average':(13.,.005),'Good':(20.,.03)}
        if c['ground'] not in soils: raise ValueError('Unknown ground type.')
        eps,sigma=soils[c['ground']]
        checked(nec.nec_gn_card(context,2,0,eps,sigma,0,0,0,0))
        checked(nec.nec_ld_card(context,5,0,0,0,5.8e7,0,0))
        checked(nec.nec_fr_card(context,0,1,frequency,0))
        checked(nec.nec_ex_card(context,0,*feed,0,1.,0,0,0,0,0))
        checked(nec.nec_rp_card(context,0,91,360,0,0,0,0,0.,0.,1.,1.,0.,0.))
        # NEC theta=0 is zenith; Type 13 elevation=0 is horizon.
        gains=np.array([[nec.nec_gain(context,0,90-e,az) for e in range(91)] for az in range(360)])-c['loss_db']
        if not np.isfinite(gains).all(): raise ValueError('NEC returned non-finite antenna gain.')
        return np.maximum(-99.,gains), {'resistance_ohm':nec.nec_impedance_real(context,0),'reactance_ohm':nec.nec_impedance_imag(context,0)}
    finally:
        nec.nec_delete(context)


def write_pattern(path, gains, frequency):
    lines=['NEC2 copper wire flat ground', '4 parameters', '0.0 Max gain', '13 Antenna type', '0.0 Efficiency ignored', f'{frequency:.5f} Frequency MHz']
    for az,row in enumerate(gains):
        for start in range(0,91,10):
            lines.append((str(az)+' ' if start==0 else ' ')+' '.join(f'{float(v):.3f}' for v in row[start:start+10]))
    path.write_text('\n'.join(lines)+'\n')


def pattern_file(config,frequency):
    """Isolate native solver crashes/timeouts, cache exact geometry + frequency."""
    c=defaults() | config
    geometry(c,frequency)
    key=hashlib.sha256(json.dumps([VERSION,c,frequency],sort_keys=True).encode()).hexdigest()
    path=CACHE/(key+'.t13'); info=CACHE/(key+'.json')
    if not path.exists() or not info.exists():
        CACHE.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='antenna-',dir=CACHE) as directory:
            task=Path(directory)/'task.json'
            task.write_text(json.dumps({'config':c,'frequency':frequency,'output':str(Path(directory)/'pattern.t13')}))
            process=subprocess.run([sys.executable,str(Path(__file__).resolve()),str(task)],capture_output=True,text=True,timeout=60)
            if process.returncode:
                raise ValueError('Antenna solver failed: '+process.stderr[-700:])
            import os
            os.replace(Path(directory)/'pattern.t13',path)
            os.replace(Path(directory)/'pattern.json',info)
    return path,json.loads(info.read_text())


def aim_at_endpoint(config, origin, destination, frequency, elevation=20):
    """Rotate the strongest NEC azimuth lobe at a stated frequency/elevation."""
    import numpy as np
    lat1,lon1=map(math.radians,origin)
    lat2,lon2=map(math.radians,destination)
    delta=lon2-lon1
    east=math.sin(delta)*math.cos(lat2)
    north=math.cos(lat1)*math.sin(lat2)-math.sin(lat1)*math.cos(lat2)*math.cos(delta)
    if math.hypot(east,north)<1e-10:
        raise ValueError('A distinct, non-antipodal endpoint is needed to calculate a bearing.')
    path_bearing=math.degrees(math.atan2(east,north))%360
    unrotated=dict(config,bearing_deg=0.)
    path,_=pattern_file(unrotated,frequency)
    lines=path.read_text().splitlines()[6:]
    gains=[]
    for az in range(360):
        block=lines[az*10:(az+1)*10]
        values=block[0].split()[1:]+[v for line in block[1:] for v in line.split()]
        gains.append(float(values[elevation]))
    gains=np.asarray(gains)
    if gains.max()-gains.min()<.05:
        return {'bearing_deg':config['bearing_deg'],'path_bearing_deg':path_bearing,'omnidirectional':True}
    peaks=np.flatnonzero(gains>=gains.max()-.0001)
    candidates=[(path_bearing-float(peak))%360 for peak in peaks]
    bearing=min(candidates,key=lambda candidate:abs((candidate-config['bearing_deg']+180)%360-180))
    return {'bearing_deg':bearing,'path_bearing_deg':path_bearing,'omnidirectional':False}


if __name__=='__main__':
    task=json.loads(Path(sys.argv[1]).read_text())
    gains,info=solve(task['config'],task['frequency'])
    output=Path(task['output']);write_pattern(output,gains,task['frequency'])
    output.with_suffix('.json').write_text(json.dumps(info))
