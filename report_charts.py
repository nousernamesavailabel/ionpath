"""Print the same modeled outlook tables/specifications used by the browser."""
import io
import json
from pathlib import Path

from model_heatmap import DIRECTIONS, heatmap_data, heatmap_spec, snr_domain


def report_heatmap_spec(snapshot, options=None):
    options=options or snapshot.get('heatmap_options') or {}
    view=options.get('view','Both directions')
    clock=options.get('clock','Local time')
    if view not in ('Both directions','Weaker direction',*DIRECTIONS) or clock not in ('Local time','UTC'):
        raise ValueError('Unknown heatmap display settings.')
    run=snapshot['dashboard'];hf=run['hf'];zone=snapshot['inputs']['timezone']
    frame,order=heatmap_data(hf['table'],hf['target'],zone)
    from realtime_context import absorption_cells, current_context
    frame=absorption_cells(frame,hf['target'],current_context(run,now=run['generated_at']),now=run['generated_at'])
    domain=snr_domain(hf['table'])
    views=DIRECTIONS if view=='Both directions' else (view,)
    panels=[]
    config=None
    for direction in views:
        panel=heatmap_spec(frame,direction,order,clock,zone,domain,numbers=True)
        config=panel.pop('config')
        panel['width']=600
        panel['height']=max(220,min(620,frame.frequency_mhz.nunique()*25))
        # A static export needs the panel's own height rather than the composed
        # chart's height signal for the full-column target outline.
        panel['layer'][1]['encoding']['y2']={'value':panel['height']}
        panel['title']={'text':direction,'anchor':'start','fontSize':14}
        panels.append(panel)
    return {'$schema':'https://vega.github.io/schema/vega-lite/v6.json',
            'data':{'values':json.loads(frame.to_json(orient='records'))},
            'vconcat':panels,'spacing':18,'resolve':{'scale':{'color':'independent'}},
            'background':'white','config':config}


def report_heatmap_image(snapshot, max_width, max_height):
    import vl_convert as vlc
    from PIL import Image as PILImage
    from reportlab.platypus import Image
    data=vlc.vegalite_to_png(report_heatmap_spec(snapshot),scale=3)
    with PILImage.open(io.BytesIO(data)) as image:
        width,height=image.size
    ratio=min(max_width/width,max_height/height)
    return Image(io.BytesIO(data),width=width*ratio,height=height*ratio,hAlign='CENTER')


def target_snr_table(hf,width):
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import Paragraph, Table, TableStyle
    from dashboard import model_overview
    from propagation_panel import snr_table_styles
    from xml.sax.saxutils import escape
    import math
    font='Helvetica-Bold'
    font_path=Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')
    if font_path.exists():
        font='ReportUnicodeBold'
        if font not in pdfmetrics.getRegisteredFontNames(): pdfmetrics.registerFont(TTFont(font,str(font_path.with_name('DejaVuSans-Bold.ttf'))))
    frame=model_overview(hf)
    shades=snr_table_styles(frame)
    def cell(text,header=False,left=False):
        text=str(text) if font!='Helvetica-Bold' else str(text).replace('→',' to ')
        return Paragraph(escape(text),ParagraphStyle('ModelCell',fontName=font,fontSize=9,leading=12,
                         alignment=0 if left else 2,textColor=colors.white if header else colors.HexColor('#17212b')))
    data=[[cell('MHz',True,True)]+[cell(column,True) for column in frame.columns]]
    for frequency,row in frame.iterrows():
        data.append([cell(f'{frequency:g}',True,True)]+[cell(f'{v:.1f}' if math.isfinite(float(v)) else '—') for v in row])
    table=Table(data,colWidths=[width/4]*4,repeatRows=1,hAlign='LEFT')
    commands=[('BACKGROUND',(0,0),(-1,0),colors.HexColor('#243247')),
              ('BACKGROUND',(0,1),(0,-1),colors.HexColor('#243247')),
              ('GRID',(0,0),(-1,-1),.5,colors.HexColor('#64748b')),
              ('VALIGN',(0,0),(-1,-1),'MIDDLE'),('LEFTPADDING',(0,0),(-1,-1),8),
              ('RIGHTPADDING',(0,0),(-1,-1),8),('TOPPADDING',(0,0),(-1,-1),6),('BOTTOMPADDING',(0,0),(-1,-1),6)]
    for y,(_,row) in enumerate(shades.iterrows(),1):
        for x,style in enumerate(row,1):
            shade=style.split('background-color: ')[1].split(';')[0]
            commands.append(('BACKGROUND',(x,y),(x,y),colors.HexColor(shade)))
    table.setStyle(TableStyle(commands))
    return table
