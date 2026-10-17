"""Editable TX/RX circles for the Streamlit planner."""
import math
import folium
from folium.plugins import Draw
from branca.element import MacroElement, Template

MAP_HEIGHT = 680


class AreaControls(MacroElement):
    def __init__(self, group, circles, markers, active):
        super().__init__()
        self._name='AreaControls'
        self.group=group.get_name()
        self.circles=[c.get_name() for c in circles]
        self.markers=[m.get_name() for m in markers]
        self.active=active
        self._template=Template('''{% macro script(this, kwargs) %}
        (function() {
            const map={{this._parent.get_name()}}, group={{this.group}};
            const circles=[{{this.circles|join(',')}}], markers=[{{this.markers|join(',')}}];
            const roles=['tx','destination'], active={{this.active}};
            let resizing=null, wasDragging=false;
            function nearOutline(circle,latlng) {
                const center=circle.getLatLng(), point=map.latLngToLayerPoint(center);
                const tolerance=map.distance(center,map.layerPointToLatLng(point.add([12,0])));
                return Math.abs(map.distance(center,latlng)-circle.getRadius())<=tolerance;
            }
            function resizeTo(event) {
                if (!resizing) return;
                const location=map.mouseEventToLatLng(event);
                resizing.setRadius(Math.max(100,Math.min(5000000,map.distance(resizing.getLatLng(),location))));
            }
            function finishResize() {
                if (!resizing) return;
                const circle=resizing; resizing=null;
                document.removeEventListener('mousemove',resizeTo);
                document.removeEventListener('mouseup',finishResize);
                if (wasDragging) map.dragging.enable();
                map.fire('draw:edited',{layers:group,layer:circle,layerType:'circle'});
                setTimeout(function(){map._drawInProgress=false;},100);
            }
            circles.forEach(function(circle,i) {
                circle.feature={type:'Feature',properties:{role:roles[i]}};
                const base=circle.toGeoJSON.bind(circle);
                circle.toGeoJSON=function() {
                    const f=base(); f.properties={role:roles[i],radius:circle.getRadius()}; return f;
                };
                circle.on('mousemove',function(e) {
                    if (!map._drawInProgress && circle.getElement())
                        circle.getElement().style.cursor=nearOutline(circle,e.latlng)?'ew-resize':'';
                });
                circle.on('mousedown',function(e) {
                    if (map._drawInProgress || e.originalEvent.button!==0 || !nearOutline(circle,e.latlng)) return;
                    L.DomEvent.stop(e.originalEvent);
                    resizing=circle; wasDragging=map.dragging.enabled();
                    map.dragging.disable(); map._drawInProgress=true;
                    document.addEventListener('mousemove',resizeTo);
                    document.addEventListener('mouseup',finishResize);
                });
                markers[i].on('drag',function(e) {circle.setLatLng(e.target.getLatLng());});
                markers[i].on('dragend',function(e) {
                    circle.setLatLng(e.target.getLatLng());
                    map.fire('draw:edited',{layers:group,layer:circle,layerType:'circle'});
                });
            });
            map.on('click',function(e) {
                if (map._drawInProgress) return;
                circles[active].setLatLng(e.latlng); markers[active].setLatLng(e.latlng);
                map.fire('draw:edited',{layers:group,layer:circles[active],layerType:'circle'});
            });
            map.on('draw:drawstart draw:editstart',function() {map._drawInProgress=true;});
            map.on('draw:drawstop draw:editstop',function() {setTimeout(function(){map._drawInProgress=false;},100);});
            map.on('draw:created',function(e) {
                if (e.layerType!=='circle') return;
                circles[active].setLatLng(e.layer.getLatLng()).setRadius(e.layer.getRadius());
                markers[active].setLatLng(e.layer.getLatLng());
                group.removeLayer(e.layer);
                map.fire('draw:edited',{layers:group,layer:circles[active],layerType:'circle'});
            });
            map.on('draw:edited',function() {circles.forEach(function(c,i){markers[i].setLatLng(c.getLatLng());});});
        })();
        {% endmacro %}''')


class BrowserViewport(MacroElement):
    """Keep viewport state in the browser, outside Streamlit's rerun loop."""
    def __init__(self, revision):
        super().__init__()
        self._name='BrowserViewport'
        self.revision=revision
        self.height=MAP_HEIGHT
        self._template=Template('''{% macro script(this, kwargs) %}
        (function() {
            const map={{this._parent.get_name()}}, revision={{this.revision}};
            const key='wspr-planner-viewport';
            const container=map.getContainer();
            container.style.height='{{this.height}}px';
            // Leaflet must recalculate its viewport after the expander becomes visible.
            const observer=new ResizeObserver(function() {
                if (container.clientWidth && container.clientHeight) map.invalidateSize({pan:false});
            });
            observer.observe(container);
            map.on('unload',function(){observer.disconnect();});
            try {
                const saved=JSON.parse(sessionStorage.getItem(key));
                if (saved && saved.revision===revision && Number.isFinite(saved.zoom) &&
                    Array.isArray(saved.center) && saved.center.length===2 && saved.center.every(Number.isFinite)) {
                    map.setView(saved.center,saved.zoom,{animate:false});
                }
            } catch (_) {}
            function remember() {
                const center=map.getCenter();
                try {sessionStorage.setItem(key,JSON.stringify({revision:revision,center:[center.lat,center.lng],zoom:map.getZoom()}));} catch (_) {}
            }
            map.on('moveend zoomend',remember);
            remember();
        })();
        {% endmacro %}''')


def build_map(areas, active='destination', view_revision=0):
    tx,destination=areas['tx']['center'],areas['destination']['center']
    midpoint=[(tx[0]+destination[0])/2,(tx[1]+((destination[1]-tx[1]+180)%360-180)/2+180)%360-180]
    map_=folium.Map(location=midpoint,zoom_start=3,tiles='OpenStreetMap',height=MAP_HEIGHT)
    group=folium.FeatureGroup(name='Selected areas').add_to(map_)
    circles=[]; markers=[]
    for role,color,label in [('tx','blue','A'),('destination','orange','B')]:
        area=areas[role]
        circles.append(folium.Circle(area['center'],radius=area['radius_km']*1000,color=color,fill=True,fill_opacity=0.12,tooltip=f'{label}: {area["radius_km"]:.1f} km').add_to(group))
        markers.append(folium.Marker(area['center'],draggable=True,icon=folium.Icon(color=color,icon='bullseye',prefix='fa'),tooltip=f'{label} — drag to move').add_to(map_))
    Draw(feature_group=group,show_geometry_on_click=False,
         draw_options={'circle':{'shapeOptions':{'color':'blue' if active=='tx' else 'orange'}},'polyline':False,'polygon':False,'rectangle':False,'marker':False,'circlemarker':False},
         edit_options={'remove':False}).add_to(map_)
    AreaControls(group,circles,markers,0 if active=='tx' else 1).add_to(map_)
    folium.PolyLine([tx,destination],color='#64748b',weight=2,dash_array='6 6',tooltip='Selected endpoints (schematic path)').add_to(map_)
    map_.fit_bounds([areas['tx']['center'],areas['destination']['center']],padding=(50,50),max_zoom=8)
    BrowserViewport(view_revision).add_to(map_)
    return map_


def parse_areas(features):
    """Require both identified circles, finite WGS84 centers and bounded radii."""
    if not features:
        return None
    result={}
    for feature in features:
        properties=feature.get('properties',{})
        role=properties.get('role')
        if role not in ('tx','destination'):
            continue
        coordinates=feature.get('geometry',{}).get('coordinates',[])
        if feature.get('geometry',{}).get('type')!='Point' or len(coordinates)!=2:
            raise ValueError('Map areas must be circles.')
        lon,lat=map(float,coordinates)
        radius=float(properties.get('radius',0))/1000
        if not all(math.isfinite(n) for n in [lat,lon,radius]) or not -90<=lat<=90 or not 0.1<=radius<=5000:
            raise ValueError('Use a radius from 0.1 to 5,000 km and valid map coordinates.')
        lon=(lon+180)%360-180
        if role in result:
            raise ValueError('Duplicate map area.')
        result[role]={'center':[round(lat,6),round(lon,6)],'radius_km':round(radius,3)}
    return result if len(result)==2 else None


def spatial_predicate(latitude,longitude,radius,prefix="tx"):
    if prefix not in ("tx", "rx"):
        raise ValueError("Invalid station role")
    """Bounded center-distance query; handles date-line crossings and polar areas."""
    latitude,longitude,radius=map(float,[latitude,longitude,radius])
    if not all(math.isfinite(n) for n in [latitude,longitude,radius]) or not -90<=latitude<=90 or not -180<=longitude<=180 or not 0.1<=radius<=5000:
        raise ValueError('Invalid transmitter search circle.')
    angle=radius/6371
    delta=math.degrees(angle)
    lower,upper=max(-90,latitude-delta),min(90,latitude+delta)
    predicates=[f'tx_lat BETWEEN {lower:.9f} AND {upper:.9f}']
    if lower>-90 and upper<90:
        span=math.degrees(math.asin(min(1,math.sin(angle)/math.cos(math.radians(latitude)))))
        lo,hi=longitude-span,longitude+span
        if lo < -180:
            predicates.append(f'(tx_lon >= {lo+360:.9f} OR tx_lon <= {hi:.9f})')
        elif hi > 180:
            predicates.append(f'(tx_lon >= {lo:.9f} OR tx_lon <= {hi-360:.9f})')
        else:
            predicates.append(f'tx_lon BETWEEN {lo:.9f} AND {hi:.9f}')
    latitude_term=f"pow(sin((tx_lat-({latitude:.9f}))*pi()/360),2)"
    longitude_term=f"cos(({latitude:.9f})*pi()/180)*cos(tx_lat*pi()/180)*pow(sin((tx_lon-({longitude:.9f}))*pi()/360),2)"
    haversine_term=f"least(1., greatest(0., {latitude_term} + {longitude_term}))"
    predicates.append(f"6371 * 2 * asin(sqrt({haversine_term})) <= {radius:.9f}")
    return ' AND '.join(predicates).replace('tx_', prefix+'_')
