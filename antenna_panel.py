"""Independent endpoint antenna builders; settings are captured per analysis."""
import streamlit as st
from antennas import PRESETS, KINDS, defaults
import antenna_defaults


def antenna_builder(endpoint, origin=None, destination=None, aim_frequency=7.15):
    key='antenna_'+endpoint
    saved_key = key+'_saved_default'
    if saved_key not in st.session_state:
        try:
            saved = antenna_defaults.load(endpoint)
        except (OSError, ValueError, TypeError) as exc:
            saved = None
            st.session_state[key+'_default_error'] = f'Could not load {endpoint} antenna default: {exc}'
        st.session_state[saved_key] = saved
        if saved and key+'_preset' not in st.session_state:
            st.session_state[key+'_preset'] = saved['preset']
            prefix = key+'_'+saved['preset']
            suffixes = {'kind':'kind','length_m':'length','height_m':'height','bearing_deg':'bearing',
                        'shape':'shape','aspect':'aspect','apex_deg':'angle','counterpoise_m':'return',
                        'radius_mm':'radius','ground':'ground','loss_db':'loss'}
            for field,suffix in suffixes.items():
                st.session_state[prefix+'_'+suffix] = saved['config'][field]
    saved = st.session_state[saved_key]
    preset=st.selectbox(f'{endpoint} antenna preset',list(PRESETS),key=key+'_preset')
    base=defaults(preset)
    if saved and saved['preset'] == preset:
        base.update(saved['config'])
    # Include preset in widget keys so changing presets applies their dimensions.
    prefix=key+'_'+preset
    if preset=='Isotropic reference':
        remember_antenna(endpoint, preset, base)
        return base
    kind=st.selectbox(f'{endpoint} antenna type',KINDS,index=KINDS.index(base['kind']),key=prefix+'_kind')
    c=base | {'kind':kind}
    c['length_m']=st.number_input(f'{endpoint} wire length / loop perimeter / vertical length (m)',.5,200.,base['length_m'],key=prefix+'_length')
    c['height_m']=st.number_input(f'{endpoint} height above ground (m)',.5,100.,base['height_m'],key=prefix+'_height')
    st.caption('Height = dipole/wire level, V apex, horizontal loop plane, vertical loop bottom, or vertical feedpoint. Dimensions stay fixed across frequencies; antennas are not automatically resized or retuned.')
    if 'loop' in kind:
        c['shape']=st.selectbox(f'{endpoint} loop shape',['Square','Rectangle','Triangle'],index=['Square','Rectangle','Triangle'].index(base['shape']),key=prefix+'_shape')
        if c['shape']=='Rectangle': c['aspect']=st.number_input(f'{endpoint} loop length-to-width ratio',1.,10.,base['aspect'],key=prefix+'_aspect')
    if kind=='Inverted V': c['apex_deg']=st.number_input(f'{endpoint} V included angle (°)',60.,170.,base['apex_deg'],key=prefix+'_angle')
    if kind in ('End-fed wire + counterpoise','Vertical + 4 elevated radials'):
        c['counterpoise_m']=st.number_input(f'{endpoint} counterpoise / each radial length (m)',.5,100.,base['counterpoise_m'],key=prefix+'_return')
    with st.expander(f'{endpoint} advanced antenna inputs'):
        c['radius_mm']=st.number_input(f'{endpoint} wire radius (mm)',.1,10.,base['radius_mm'],key=prefix+'_radius')
        c['ground']=st.selectbox(f'{endpoint} ground',['Poor','Average','Good'],index=['Poor','Average','Good'].index(base['ground']),key=prefix+'_ground')
        c['loss_db']=st.number_input(f'{endpoint} feedline, tuner and additional loss (dB)',0.,30.,base['loss_db'],key=prefix+'_loss')
        st.caption('NEC-2 copper wire over flat lossy ground; ideal matching at each frequency. Enter additional tuner/feedline losses above. Buildings, terrain, actual matching network and polarization coupling are not modeled. End-fed return wire is collinear at the same height; vertical radials are horizontal and elevated.')
    if st.button(f'Aim {endpoint} main lobe at the other station',key=prefix+'_aim',disabled=origin is None or destination is None):
        try:
            from antennas import aim_at_endpoint
            c['bearing_deg']=st.session_state.get(prefix+'_bearing',base['bearing_deg'])
            with st.spinner('Calculating antenna direction…'):
                aimed=aim_at_endpoint(c,origin,destination,aim_frequency)
            st.session_state[prefix+'_bearing']=float(round(aimed['bearing_deg'],2))
            if aimed['omnidirectional']:
                st.info('This pattern is effectively omnidirectional at the aiming frequency and elevation; no rotation is needed.')
            else:
                st.success(f"Aimed toward {aimed['path_bearing_deg']:.1f}° true. Wire/baseline bearing set to {aimed['bearing_deg']:.1f}°.")
        except Exception as exc:
            st.warning(f'Could not aim this antenna: {exc}')
    st.caption(f'Aiming uses the strongest modeled lobe at {aim_frequency:g} MHz and 20° elevation. Other frequencies and elevations can have different lobes. Click Analyze propagation to apply the orientation.')
    c['bearing_deg']=st.number_input(f'{endpoint} wire axis / loop baseline bearing (° true)',0.,360.,base['bearing_deg'],key=prefix+'_bearing')
    remember_antenna(endpoint, preset, c)
    return c


def remember_antenna(endpoint, preset, config):
    key='antenna_'+endpoint
    record={'preset':preset, 'config':config}
    error=st.session_state.get(key+'_default_error')
    if error:
        st.warning(error)
        initial=key+'_default_error_initial'
        if initial not in st.session_state:
            st.session_state[initial]=record
        if st.session_state[initial]==record:
            st.caption('Saved file retained. Change an antenna setting to replace this default.')
            return
    try:
        if st.session_state.get(key+'_saved_default') != record:
            saved=antenna_defaults.save(endpoint,preset,config)
            st.session_state[key+'_saved_default']=saved
            st.session_state.pop(key+'_default_error',None)
        st.caption(f'{endpoint} antenna saved automatically as this station’s default. Kept across runs and restarts until you change it.')
    except (OSError,ValueError,TypeError) as exc:
        st.warning(f'{endpoint} antenna is active for this session, but its default could not be saved: {exc}')
