"""Offline PDF reports of immutable analyzed runs; no provider calls or browser required."""
from copy import deepcopy
from datetime import datetime, timezone
import io
import json
import math
from pathlib import Path
from zoneinfo import ZoneInfo
from xml.sax.saxutils import escape

from beta_diagnostics import APP_VERSION

REPORT_FORMAT_VERSION = 2
from path_geometry import path_details, destination, magnetic_path_details, magnetic_description


def capture_run(state, inputs):
    history = state.get('wspr_history') or {}
    stored = state.get('results_mgrs')
    return deepcopy({'inputs': inputs, 'dashboard': state['path_dashboard'],
        'reception_error': state.get('reception_error'),
        'reception_scores': None if stored is None else stored[0],
        'reception_count': 0 if stored is None else len(stored[1]),
        'reception_context': {key: value for key, value in (state.get('analysis_context') or {}).items()
                              if key not in ('history', 'input_signature')},
        'wspr_history': {key: history.get(key) for key in ('start','end','fetched_at','row_count','limited')}
                        if inputs['source'].startswith('WSPR.live') else {},
        'path_details': magnetic_path_details(tuple(inputs['a']['center']),tuple(inputs['b']['center']),state['path_dashboard']['target']),
        'app_version': APP_VERSION})


def model_assumption_rows(snapshot):
    run=snapshot['dashboard']; inputs=snapshot['inputs']; settings=inputs['model_settings']
    provenance=(run.get('hf') or {}).get('provenance') or {}
    antennas=provenance.get('antennas') or {key: settings.get(key) for key in ('antenna_a','antenna_b')}
    station_rows=[]
    for endpoint in ('a','b'):
        area=inputs[endpoint]; lat,lon=area['center']
        selected_call=inputs.get('callsign') if endpoint=='a' else None
        identity=selected_call or f'None (any station in {endpoint.upper()} circle)'
        details=f"Reception callsign filter: {identity}\nWGS84: {lat:.5f}, {lon:.5f}\nSearch radius: {area['radius_km']:g} km"
        if area.get('effective_radius_km',area['radius_km'])!=area['radius_km']:
            details+=f"; effective {area['effective_radius_km']:g} km including cell uncertainty"
        station_rows.append(['Station '+endpoint.upper(),details])
    hf=run.get('hf')
    frequencies=', '.join(f'{v:g}' for v in sorted(hf['table'].frequency_mhz.unique())) if hf else settings.get('frequency_text','Unavailable')
    solar=provenance.get('ssn_source','Unavailable')
    solar=solar.get('source','Unknown') if isinstance(solar,dict) else solar
    rows=station_rows+[['Station selection / directions',inputs['selection_scope']+' / '+inputs['direction']],
          ['Smoothed sunspot number (R12)',f"{provenance.get('ssn','Unavailable')} | {solar}"],
          ['Transmit power, A to B / B to A',f"{provenance.get('power_w_each_direction',settings['power']):g} W / {provenance.get('power_w_each_direction',settings['power']):g} W"],
          ['Receiver bandwidth, A / B',f"{provenance.get('bandwidth_hz',settings['bandwidth']):g} Hz / {provenance.get('bandwidth_hz',settings['bandwidth']):g} Hz"],
          ['Receiver noise, A / B',str(provenance.get('noise_at_both_receivers',settings['noise']))+' / '+str(provenance.get('noise_at_both_receivers',settings['noise']))],
          ['Model year / month',run['target'].strftime('%Y-%m')],
          ['Path','Short great circle, endpoint centers'],
          ['Frequencies (MHz)',frequencies]]
    for endpoint in ('a','b'):
        config=antennas.get('antenna_'+endpoint) or {}
        fields=[config.get('kind','Isotropic reference')]
        if config.get('kind','Isotropic reference')!='Isotropic reference':
            fields.extend(f'{label}: {config[key]:g} {unit}' for key,label,unit in [
                ('length_m','Wire/perimeter','m'),('height_m','Height','m'),('bearing_deg','Orientation','deg true'),('radius_mm','Wire radius','mm')])
            fields.append('Ground: '+config['ground'])
            if 'loop' in config['kind'].lower():
                fields.append('Shape: '+config['shape'])
                if config['shape']=='Rectangle': fields.append(f"Aspect: {config['aspect']:g}")
            if config['kind']=='Inverted V': fields.append(f"Apex: {config['apex_deg']:g} deg")
            if 'counterpoise' in config['kind'] or 'radials' in config['kind']:
                fields.append(f"Counterpoise/radials: {config['counterpoise_m']:g} m")
        fields.append(f"Additional loss: {config.get('loss_db',0):g} dB")
        if config.get('kind','Isotropic reference')=='Isotropic reference':
            fields.append(f"Gain offset: {provenance.get('antenna_gain_dbi_'+endpoint,settings.get('gain_'+endpoint,0)):g} dBi")
        rows.append(['Antenna '+endpoint.upper(),'; '.join(fields)])
    return rows


def build_pdf(snapshot):
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.platypus import BaseDocTemplate, PageTemplate, Frame, Paragraph, Spacer, Table, TableStyle, PageBreak, Flowable, KeepTogether
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name='SmallReport', fontName='Helvetica', fontSize=8, leading=11, spaceAfter=4))
    styles.add(ParagraphStyle(name='CellReport', fontName='Helvetica', fontSize=8, leading=10))
    styles.add(ParagraphStyle(name='HeaderReport', parent=styles['CellReport'], textColor=colors.white))
    styles.add(ParagraphStyle(name='TinyReport', fontName='Helvetica', fontSize=6.5, leading=8, alignment=TA_CENTER))
    blue, orange = colors.HexColor('#2563eb'), colors.HexColor('#d97706')
    ink, light = colors.HexColor('#172d46'), colors.HexColor('#edf3f8')
    width = A4[0]-84
    def clean(value):
        return str(value).replace('→',' to ').replace('—','-').replace('–','-').replace('±','+/-').replace('√','sqrt ')
    def p(text, style='SmallReport'):
        return Paragraph(escape(clean(text)).replace('\n','<br/>'), styles[style])
    def number(value, digits=1):
        try:
            numeric=float(value)
            if not math.isfinite(numeric): return '-'
            if round(numeric,digits)==0: numeric=0.
            return f'{numeric:.{digits}f}'
        except (ValueError, TypeError): return '-'
    def table(headers, rows, widths=None):
        data = [[p(x,'HeaderReport') for x in headers]] + [[p(x,'CellReport') for x in row] for row in rows]
        item = Table(data,colWidths=widths or [width/len(headers)]*len(headers),repeatRows=1,hAlign='LEFT')
        item.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),ink),('TEXTCOLOR',(0,0),(-1,0),colors.white),
            ('ROWBACKGROUNDS',(0,1),(-1,-1),[colors.white,light]),('VALIGN',(0,0),(-1,-1),'TOP'),
            ('LEFTPADDING',(0,0),(-1,-1),7),('RIGHTPADDING',(0,0),(-1,-1),7),
            ('TOPPADDING',(0,0),(-1,-1),6),('BOTTOMPADDING',(0,0),(-1,-1),6),
            ('LINEBELOW',(0,0),(-1,0),.7,ink)]))
        return item
    inputs, run = snapshot['inputs'], snapshot['dashboard']
    a, b = tuple(inputs['a']['center']), tuple(inputs['b']['center'])
    details = snapshot.get('path_details') or magnetic_path_details(a,b,run['target'])
    def heading(value,reference='true'): return 'Unavailable' if value is None else f'{value:.1f}° {reference}'

    class PathMap(Flowable):
        def __init__(self): Flowable.__init__(self); self.width=width; self.height=240
        def draw(self):
            c=self.canv; w,h=self.width,self.height
            lon0=a[1]+((b[1]-a[1]+180)%360-180)/2
            def shift(lon): return (lon-lon0+180)%360-180
            route = [destination(a,details['ab_short_deg'],details['short_km']*i/160) for i in range(161)] if details['ab_short_deg'] is not None else []
            circles=[[destination(center,angle,radius) for angle in range(0,361,3)]
                     for center,radius in ((a,inputs['a']['radius_km']),(b,inputs['b']['radius_km']))]
            points=[a,b]+route+[q for ring in circles for q in ring]
            xs=[shift(q[1]) for q in points]; ys=[q[0] for q in points]
            spanx=min(360,max(30,(max(xs)-min(xs))*1.25)); spany=min(180,max(20,(max(ys)-min(ys))*1.3))
            # Keep geographic shapes at a reasonable aspect ratio in this overview.
            spanx=min(360,max(spanx,spany*w/h)); spany=min(180,max(spany,spanx*h/w))
            cx=(max(xs)+min(xs))/2; cy=(max(ys)+min(ys))/2
            xmin=max(-180,min(cx-spanx/2,180-spanx)); ymin=max(-90,min(cy-spany/2,90-spany))
            def xy(lat,lon,offset=0): return ((lon-xmin+offset)/spanx*w,(lat-ymin)/spany*h)
            c.saveState(); clip=c.beginPath(); clip.rect(0,0,w,h); c.clipPath(clip,stroke=0)
            c.setFillColor(colors.HexColor('#e5f1f8'));c.rect(0,0,w,h,fill=1,stroke=0)
            c.setStrokeColor(colors.HexColor('#c5d9e5'));c.setLineWidth(.3)
            for lat in range(-90,91,15):
                x,y=xy(lat,xmin);c.line(0,y,w,y)
            for lon in range(-180,181,30):
                x,y=xy(ymin,lon);c.line(x,0,x,h)
            land=json.loads((Path(__file__).parent/'assets/ne_110m_land.geojson').read_text())
            def polygon(ring,fill,stroke,line=.4,alpha=1):
                coords=[]; previous=None
                for lon,lat in ring:
                    x=shift(lon)
                    if previous is not None:
                        while x-previous>180: x-=360
                        while x-previous< -180: x+=360
                    coords.append((lat,x));previous=x
                for offset in (-360,0,360):
                    path=c.beginPath()
                    for i,(lat,lon) in enumerate(coords):
                        x,y=xy(lat,lon,offset)
                        (path.moveTo if i==0 else path.lineTo)(x,y)
                    path.close();c.setFillColor(fill);c.setFillAlpha(alpha);c.setStrokeColor(stroke);c.setLineWidth(line);c.drawPath(path,fill=1,stroke=1);c.setFillAlpha(1)
            for feature in land['features']:
                geom=feature['geometry']; polys=geom['coordinates'] if geom['type']=='MultiPolygon' else [geom['coordinates']]
                for poly in polys: polygon(poly[0],colors.HexColor('#f5f3e9'),colors.HexColor('#a9b7aa'))
            for ring,color in zip(circles,(blue,orange)):
                polygon([(lon,lat) for lat,lon in ring],color,color,.8,.15)
            c.setStrokeColor(ink);c.setLineWidth(1.3)
            for q,r in zip(route,route[1:]):
                if abs(shift(q[1])-shift(r[1]))<180:
                    c.line(*xy(q[0],shift(q[1])),*xy(r[0],shift(r[1])))
            for label,point,color in [('A',a,blue),('B',b,orange)]:
                x,y=xy(point[0],shift(point[1]));c.setFillColor(color);c.circle(x,y,4,fill=1,stroke=0)
                tx=min(w-15,max(3,x+7));ty=min(h-13,max(3,y+7))
                c.setFillColor(colors.white);c.roundRect(tx-2,ty-2,14,15,2,fill=1,stroke=0)
                c.setFillColor(color);c.setFont('Helvetica-Bold',10);c.drawString(tx,ty,label)
            c.restoreState();c.setStrokeColor(colors.HexColor('#bdccd8'));c.rect(0,0,w,h)

    story=[p('IONPATH | SAVED PROPAGATION RUN','Title'),
           p(f"Version {snapshot['app_version']} | Run generated {run['generated_at']:%Y-%m-%d %H:%M UTC}"),
           p(f"Target {run['target']:%Y-%m-%d %H:%M UTC} | Local: {run['target'].astimezone(ZoneInfo(inputs['timezone'])):%Y-%m-%d %H:%M %Z}"),
           Spacer(1,8),PathMap(),Spacer(1,5),
           p('A: blue | B: orange | Line: short great-circle path | Shading: search circles. Geographic overview; scale varies with latitude. Land: Natural Earth, public domain.'),
           p('Path details','Heading2'),
           table(['Route','Distance','A to B initial bearing','B to A initial bearing'],[
               ['Short path',f"{details['short_km']:,.1f} km",heading(details['ab_short_deg'])+' / '+heading(details['ab_short_magnetic_deg'],'magnetic'),heading(details['ba_short_deg'])+' / '+heading(details['ba_short_magnetic_deg'],'magnetic')],
               ['Long path','Undefined' if details['long_km'] is None else f"{details['long_km']:,.1f} km",heading(details['ab_long_deg'])+' / '+heading(details['ab_long_magnetic_deg'],'magnetic'),heading(details['ba_long_deg'])+' / '+heading(details['ba_long_magnetic_deg'],'magnetic')]]),
           p('Distances use mean Earth radius 6,371 km and endpoint centers. Bearings show true / magnetic azimuth at the departure endpoint. The propagation model uses the short path.'),
           p(magnetic_description(details)),
           PageBreak(),p('Station setup and model assumptions','Title'),
           table(['Setting','Value used in this run' if run.get('hf') else 'Configured value'],model_assumption_rows(snapshot),[145,width-145]),
           p('Callsign filters apply only to reception history. The HF model uses endpoint locations and station/antenna setup.'),
           p('Antenna geometry is fixed across frequencies. Physical antennas use NEC-2 over flat lossy ground with ideal matching; terrain, structures, polarization coupling and a specific tuner are not modeled.' if run.get('hf') else 'The model was unavailable for this run. This table records the configured station setup; it does not indicate a successful propagation calculation.')]
    hf=run.get('hf')
    if hf:
        from report_charts import target_snr_table, report_heatmap_image
        frame=hf['table']
        provenance=hf['provenance']
        story += [PageBreak(),p('Modeled propagation | target hour','Title'),
            p(f"{run['target']:%Y-%m-%d %H:%M UTC} | {provenance.get('model','ITU-R P.533 monthly baseline')}"),
            target_snr_table(hf,width),
            p('Modeled median SNR (dB). Columns, ordering, one-decimal values and relative cell colors match the browser HF outlook.'),
            p('These are monthly median modeled SNR values, not measured reception or a calibrated probability of voice success. The model is not a daily forecast. Current NOAA and ionosonde conditions do not modify these SNR values. No pass/fail SNR threshold is implied.'),
            PageBreak(),p('Modeled propagation | 24-hour outlook','Title'),
            p(f"{snapshot.get('heatmap_options',{}).get('clock','Local time')} | {inputs['timezone']} | SNR values are printed in every available cell. Pink outline marks target UTC hour {hf['target'].hour:02}:00; gray indicates missing data."),
            report_heatmap_image(snapshot,width,A4[1]-190),
            p('Same frequency order, hour order, direction panels and shared SNR scale as the browser. Labels show rounded SNR (dB); the target-hour table gives one decimal. Brighter colors indicate higher modeled SNR, not a guaranteed opening. All 24 UTC bins are retained. ! marks time-matched D-RAP context from this run.')]

    else:
        story += [PageBreak(),p('Modeled propagation unavailable','Title'),p((run.get('errors') or {}).get('hf','No modeled results for this run.'))]
    story += [PageBreak(),p('Reception evidence and current conditions','Title'),
              p('Reception history','Heading2')]
    context=snapshot['reception_context'];history=snapshot['wspr_history']
    story += [p(f"Source: {inputs['source']} | Lookback: {inputs.get('lookback_days') or '-'} days | Row limit: {inputs.get('row_limit') or '-'} | Target window +/- {inputs['hour_tolerance']} hours | Matched reports: {snapshot['reception_count']:,}"),
              p(f"Season filter: {inputs.get('season',False)} | Season window +/- {inputs.get('season_days','-')} days")]
    if history.get('fetched_at'): story += [p(f"History fetched {history['fetched_at']} | Query range {history['start']} to {history['end']}")]
    if snapshot.get('reception_error'): story.append(p(snapshot['reception_error']))
    scores=snapshot.get('reception_scores')
    if scores is not None and not scores.empty:
        story.append(table(['Band','A to B SNR (dB)','B to A SNR (dB)','Reports'],[
            [row['Band'],number(row.get('A → B median SNR (dB)')),number(row.get('B → A median SNR (dB)')),str(row['Spots'])] for _,row in scores.iterrows()]))
    else: story.append(p('No ranked reception evidence at the selected target hour. Availability is unknown.'))
    if context.get('row_limited'): story.append(p('History reached the row limit; coverage is incomplete.'))
    if context.get('archive_stats'): story.append(p('Archive metadata: '+json.dumps(context['archive_stats'],default=str)))
    story.append(p('WSPR SNR is normalized to 1 W; FT8 SNR is measured with unknown power. Historical reports describe one-way observations, not return-link or voice reliability. Historical and modeled SNR values are not numerically comparable.'))
    story.append(p('Current ionosonde measurements','Heading2'))
    iono=run.get('iono')
    if iono:
        from ionosonde_panel import context_table
        stations=context_table(iono,run['generated_at'])
        story.append(p(f"Retrieved {iono.get('retrieved_at')} | Ages below are relative to run generation, not PDF download time."))
        story.append(table(['Station','Observation UTC','foF2 MHz','hmF2 km','Status'],[
            [row['Station'],str(row['Observation UTC']),number(row['foF2 (MHz)'],2),number(row['hmF2 (km)']),row['Status']] for _,row in stations.iterrows()])) if not stations.empty else story.append(p('No stations returned.'))
        for warning in iono.get('warnings',[]): story.append(p(warning))
    else: story.append(p((run.get('errors') or {}).get('iono','No ionosonde data for this run.')))
    story.append(p('NOAA and nowcast source provenance','Heading2'))
    from space_weather import latest_kp
    try:
        kp=latest_kp((run.get('weather',{}).get('kp') or {}).get('payload') or [])
        story.append(p(f"Planetary Kp {kp['value']:.2f} | Observed {kp['time']}"))
    except (ValueError,TypeError): story.append(p('Planetary Kp unavailable.'))
    from realtime_context import current_context
    conditions=current_context(run,now=run['generated_at'])
    for name,item in conditions.items():
        if item.get('haf_mhz') is not None:
            story.append(p(f"D-RAP path highest affected frequency: {number(item['haf_mhz'])} MHz. Geographic context, not a closed-band threshold or modeled loss."))
        story.append(p(f"{name.upper()}: valid {item.get('time','unavailable')} | Fresh at run: {item.get('fresh','unknown')} | Error: {item.get('error') or 'none'}"))
    for name,feed in run.get('weather',{}).items():
        story.append(p(f"{name}: retrieved {feed.get('retrieved_at') or 'unavailable'} | {feed.get('url','')} | {feed.get('error') or 'No retrieval error'}"))
    for name,error in (run.get('errors') or {}).items(): story.append(p(f'{name} error: {error}'))
    story.append(p('Current measurements are context, not an assimilated forecast. Missing reports are unknown availability. Validate candidate channels with an actual link test. Data credits: WSPR.live and reporting volunteers; NOAA SWPC; GIRO/DIDBase and IRTAM; HamSCI / CEDAR Madrigal for FT8 archives.'))
    output=io.BytesIO()
    def footer(canvas,doc):
        page_width=canvas._pagesize[0]
        canvas.setStrokeColor(light);canvas.line(42,36,page_width-42,36)
        canvas.setFillColor(ink);canvas.setFont('Helvetica',8)
        canvas.drawString(42,24,f"Ionpath {snapshot['app_version']} | Saved run {run['generated_at']:%Y-%m-%d %H:%M UTC}")
        canvas.drawRightString(page_width-42,24,f'Page {doc.page}')
    doc=BaseDocTemplate(output,pagesize=A4,rightMargin=42,leftMargin=42,topMargin=38,bottomMargin=48,title='Ionpath saved propagation run',author='Ionpath')
    doc.addPageTemplates(PageTemplate(id='Portrait',pagesize=A4,onPage=footer,
        frames=[Frame(42,48,A4[0]-84,A4[1]-86,leftPadding=0,rightPadding=0,topPadding=0,bottomPadding=0)]))
    doc.build(story)
    return output.getvalue()


def render_report_export(state, stale=False):
    import streamlit as st
    if not state.get('run_report_snapshot'):
        return
    options={'view':state.get('model_heatmap_view','Both directions'),'clock':state.get('model_heatmap_clock','Local time')}
    if state.get('run_report_heatmap_options') != options or state.get('run_report_format_version') != REPORT_FORMAT_VERSION:
        state.pop('run_report_pdf',None)
    with st.container():
        if stale: st.caption('PDF exports the last analyzed run, including its saved map and station setup. Analyze again to include edited settings.')
        with st.container(horizontal=True):
            prepare=st.button('Prepare run PDF',key='prepare_run_pdf',help='Create a PDF with the saved map, station setup, predictions, reception evidence and source timestamps.')
            download_slot=st.empty()
        if prepare:
            try:
                with st.spinner('Preparing saved run PDF…'):
                    snapshot=dict(state['run_report_snapshot'],heatmap_options=options)
                    state['run_report_pdf']=build_pdf(snapshot)
                    state['run_report_heatmap_options']=options
                    state['run_report_format_version']=REPORT_FORMAT_VERSION
            except Exception as exc:
                st.error(f'Could not prepare run PDF: {exc}')
        if state.get('run_report_pdf'):
            stamp=state['run_report_snapshot']['dashboard']['generated_at']
            with download_slot:
                st.download_button('Download run PDF',state['run_report_pdf'],file_name=f'ionpath-run-{stamp:%Y%m%d-%H%M%S}.pdf',mime='application/pdf',key='download_run_pdf')
        st.caption('The PDF contains saved endpoint coordinates, station callsign and antenna settings. Public sessions: download before leaving or a server restart.')
