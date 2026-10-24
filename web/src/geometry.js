/** WGS84 ellipsoidal measurements and conservative local survey topology checks. */
import GeographicLib from 'geographiclib-geodesic';
import { featureCollection, intersect, union, booleanPointInPolygon } from '@turf/turf';

const geod = GeographicLib.Geodesic.WGS84;
const round = (value, places = 4) => Number(value.toFixed(places));
export function geometryError(message) { const error = new Error(message); error.status = 422; throw error; }
const object = value => value && typeof value === 'object' && !Array.isArray(value);
const polygonsOf = geometry => geometry.type === 'Polygon' ? [geometry.coordinates] : geometry.coordinates;
const cross = (a, b, c) => (b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0]);
const same = (a,b) => a[0] === b[0] && a[1] === b[1];
const planarRingArea = ring => ring.slice(1).reduce((sum,p,i) => sum + (ring[i][0]-ring[0][0])*(p[1]-ring[0][1])-(p[0]-ring[0][0])*(ring[i][1]-ring[0][1]),0)/2;

export function validateBounds(bounds, options = {}) {
  if (!Array.isArray(bounds) || bounds.length !== 4 || !bounds.every(v => typeof v === 'number' && Number.isFinite(v))) geometryError('Bounds must contain four finite numbers [west, south, east, north].');
  const [w,s,e,n] = bounds, limit = options.mercator ? 85.05112878 : 90;
  if (!(w >= -180 && w < e && e <= 180 && s >= -limit && s < n && n <= limit)) geometryError('Bounds must be ordered WGS84 coordinates and must not cross the antimeridian.');
  if (e-w >= 180) geometryError('Use a local survey extent smaller than 180 degrees of longitude.');
  return [...bounds];
}

function orientation(a,b,c) {
  const value = cross(a,b,c), tolerance = 1e-12 * (Math.abs((b[0]-a[0])*(c[1]-a[1])) + Math.abs((b[1]-a[1])*(c[0]-a[0]))) + 1e-25;
  return Math.abs(value) <= tolerance ? 0 : Math.sign(value);
}
const onSegment = (a,b,p) => !orientation(a,b,p) && p[0] >= Math.min(a[0],b[0]) && p[0] <= Math.max(a[0],b[0]) && p[1] >= Math.min(a[1],b[1]) && p[1] <= Math.max(a[1],b[1]);
function intersects(a,b,c,d) {
  const a1=orientation(a,b,c), a2=orientation(a,b,d), b1=orientation(c,d,a), b2=orientation(c,d,b);
  return (a1*a2 < 0 && b1*b2 < 0) || (!a1 && onSegment(a,b,c)) || (!a2 && onSegment(a,b,d)) || (!b1 && onSegment(c,d,a)) || (!b2 && onSegment(c,d,b));
}

/** Sweep bounding boxes; cap adversarial work rather than freezing the browser. */
function validateRings(polygons) {
  const segments=[];
  polygons.forEach((rings,pi) => rings.forEach((ring,ri) => ring.slice(1).forEach((b,i) => {
    const a=ring[i];
    if (same(a,b)) geometryError('A ring contains a zero-length edge. Remove repeated adjacent positions.');
    segments.push({a,b,pi,ri,i,length:ring.length-1,minX:Math.min(a[0],b[0]),maxX:Math.max(a[0],b[0]),minY:Math.min(a[1],b[1]),maxY:Math.max(a[1],b[1])});
  })));
  segments.sort((a,b) => a.minX-b.minX);
  let active=[],checks=0;
  for (const segment of segments) {
    active=active.filter(other => other.maxX >= segment.minX);
    for (const other of active) {
      if (other.maxY < segment.minY || other.minY > segment.maxY) continue;
      if (++checks > 1_000_000) geometryError('Geometry is too complex for a browser check. Simplify the boundary.');
      const sameRing=other.pi === segment.pi && other.ri === segment.ri;
      const adjacent=sameRing && (Math.abs(other.i-segment.i) === 1 || Math.abs(other.i-segment.i) === segment.length-1);
      if (adjacent) {
        // Adjacent edges may share one endpoint, but cannot double back over one another.
        const shared=[other.a,other.b].find(p => same(p,segment.a) || same(p,segment.b));
        const aa=same(other.a,shared) ? other.b : other.a, bb=same(segment.a,shared) ? segment.b : segment.a;
        if (!orientation(aa,shared,bb) && (onSegment(aa,shared,bb) || onSegment(shared,bb,aa))) geometryError('Polygon edges overlap or double back.');
        continue;
      }
      if (intersects(segment.a,segment.b,other.a,other.b)) {
        // Separate MultiPolygon components may touch at isolated points, but not along edges.
        if (segment.pi !== other.pi) {
          const o1=orientation(segment.a,segment.b,other.a),o2=orientation(segment.a,segment.b,other.b),o3=orientation(other.a,other.b,segment.a),o4=orientation(other.a,other.b,segment.b);
          const properCrossing=o1*o2<0&&o3*o4<0;
          const axis=Math.abs(segment.a[0]-segment.b[0])>=Math.abs(segment.a[1]-segment.b[1])?0:1;
          const positiveCollinearOverlap=!o1&&!o2&&Math.min(segment.a[axis],segment.b[axis])<Math.max(other.a[axis],other.b[axis])&&Math.min(other.a[axis],other.b[axis])<Math.max(segment.a[axis],segment.b[axis]);
          if (!properCrossing&&!positiveCollinearOverlap) continue;
        }
        geometryError('Invalid polygon: crossing, touching or overlapping rings.');
      }
    }
    active.push(segment);
  }
}

export function validateGeometry(geometry) {
  if (!object(geometry) || !['Polygon','MultiPolygon'].includes(geometry.type)) geometryError('Geometry must be a GeoJSON Polygon or MultiPolygon.');
  let count=0;
  const polygons=polygonsOf(geometry);
  if (!Array.isArray(polygons) || !polygons.length) geometryError('A polygon must contain an exterior ring.');
  const all=[];
  for (const rings of polygons) {
    if (!Array.isArray(rings) || !rings.length) geometryError('Each polygon requires an exterior ring.');
    for (const ring of rings) {
      if (!Array.isArray(ring) || ring.length < 4) geometryError('Each polygon ring needs at least four positions including closure.');
      for (const p of ring) {
        if (++count > 20_000) geometryError('Geometry exceeds the 20,000-vertex limit.');
        if (!Array.isArray(p) || p.length !== 2 || !p.every(v => typeof v === 'number' && Number.isFinite(v))) geometryError('Positions must be finite [longitude, latitude] pairs.');
        if (Math.abs(p[0]) > 180 || Math.abs(p[1]) > 90) geometryError('Coordinates are outside WGS84 longitude/latitude ranges.');
        all.push(p);
      }
      if (!same(ring[0],ring.at(-1))) geometryError('Polygon rings must be closed.');
      if (Math.abs(planarRingArea(ring)) < 1e-20) geometryError('Geometry is empty or degenerate.');
    }
  }
  const bbox=[Math.min(...all.map(p=>p[0])),Math.min(...all.map(p=>p[1])),Math.max(...all.map(p=>p[0])),Math.max(...all.map(p=>p[1]))];
  validateBounds(bbox);
  if (bbox[2]-bbox[0]>5 || bbox[3]-bbox[1]>5) geometryError('Use a local survey boundary within 5 degrees; split larger regions before browser measurement.');
  validateRings(polygons);
  for (const rings of polygons) {
    const exterior={type:'Polygon',coordinates:[rings[0]]};
    for (let i=1;i<rings.length;i++) {
      if (!booleanPointInPolygon(rings[i][0],exterior,{ignoreBoundary:true})) geometryError('A hole lies outside its exterior ring.');
      for (let j=1;j<i;j++) if (booleanPointInPolygon(rings[i][0],{type:'Polygon',coordinates:[rings[j]]}) || booleanPointInPolygon(rings[j][0],{type:'Polygon',coordinates:[rings[i]]})) geometryError('Polygon holes overlap or contain another hole.');
    }
  }
  const normalized=polygons.map(rings => rings.map((ring,index) => {
    const copy=ring.map(p=>[...p]);
    return (planarRingArea(copy)>0) === (index===0) ? copy : copy.reverse();
  }));
  const components=normalized.map(coordinates=>({type:'Feature',geometry:{type:'Polygon',coordinates},properties:{}}));
  for (let i=0;i<components.length;i++) for (let j=0;j<i;j++) {
    const overlap=intersect(featureCollection([components[i],components[j]]));
    if (overlap) geometryError('MultiPolygon components have overlapping interiors.');
  }
  let area=0,perimeter=0,cx=0,cy=0,weight=0;
  for (const rings of normalized) for (const [index,ring] of rings.entries()) {
    const polygon=geod.Polygon(false);
    for (const [lon,lat] of ring.slice(0,-1)) polygon.AddPoint(lat,lon);
    const result=polygon.Compute(false,true);
    area+=(index===0?1:-1)*Math.abs(result.area); perimeter+=result.perimeter;
    // Translation avoids cancellation for very small parcels far from the origin.
    const origin=ring[0]; let signed=0,x=0,y=0;
    for (let i=0;i<ring.length-1;i++) { const a=[ring[i][0]-origin[0],ring[i][1]-origin[1]],b=[ring[i+1][0]-origin[0],ring[i+1][1]-origin[1]],z=a[0]*b[1]-b[0]*a[1]; signed+=z; x+=(a[0]+b[0])*z; y+=(a[1]+b[1])*z; }
    cx+=(origin[0]+x/(3*signed))*signed; cy+=(origin[1]+y/(3*signed))*signed; weight+=signed;
  }
  if (!(area>0) || !Number.isFinite(area+perimeter)) geometryError('Geometry has no measurable positive geodesic area.');
  return {geometry:{type:geometry.type,coordinates:geometry.type==='Polygon'?normalized[0]:normalized},area_sqm:round(area),area_sqft:round(area*10.76391041671),perimeter_m:round(perimeter),centroid:[cx/weight,cy/weight],vertex_count:count,bbox,bounds:[...bbox]};
}
export const measureGeometry=validateGeometry;

/** Ellipsoidal azimuthal equidistant coordinates, in metres around a local origin. */
export function localProjector(center) {
  return {
    forward([lon,lat]) { const g=geod.Inverse(center[1],center[0],lat,lon),a=g.azi1*Math.PI/180; return [g.s12*Math.sin(a),g.s12*Math.cos(a)]; },
    inverse([x,y]) { const g=geod.Direct(center[1],center[0],Math.atan2(x,y)*180/Math.PI,Math.hypot(x,y)); return [g.lon2,g.lat2]; },
  };
}
function prepare(features) {
  if (!Array.isArray(features) || features.length>12000) geometryError('Check at most 12,000 boundaries at a time.');
  let vertices=0;
  const normalized=features.map((f,index) => {
    if (!object(f) || f.type!=='Feature' || (f.properties != null && !object(f.properties))) geometryError(`Boundary ${index+1} must be a GeoJSON Feature with object properties.`);
    const m=measureGeometry(f.geometry); vertices+=m.vertex_count;
    if (vertices>250000) geometryError('Too many boundary vertices for one check; split the survey.');
    return {...structuredClone(f),geometry:m.geometry,properties:{...(f.properties||{})}};
  });
  if (!normalized.length) return {features:[],polygons:[],areas:[]};
  const boxes=normalized.map(f=>measureGeometry(f.geometry).bbox);
  const bounds=[Math.min(...boxes.map(b=>b[0])),Math.min(...boxes.map(b=>b[1])),Math.max(...boxes.map(b=>b[2])),Math.max(...boxes.map(b=>b[3]))];
  if (bounds[2]-bounds[0]>5 || bounds[3]-bounds[1]>5 || Math.max(Math.abs(bounds[1]),Math.abs(bounds[3]))>85) geometryError('Spatial checks require a local survey within 5 degrees and 85 degrees latitude.');
  const projection=localProjector([(bounds[0]+bounds[2])/2,(bounds[1]+bounds[3])/2]);
  const polygons=normalized.map(f=>({type:'Feature',properties:{},geometry:{type:f.geometry.type,coordinates:f.geometry.type==='Polygon'?f.geometry.coordinates.map(r=>r.map(projection.forward)):f.geometry.coordinates.map(p=>p.map(r=>r.map(projection.forward)))}}));
  return {features:normalized,polygons,areas:polygons.map(f=>planarArea(f.geometry))};
}
function planarArea(geometry) { return polygonsOf(geometry).reduce((sum,rings)=>sum+rings.reduce((a,r,i)=>a+(i?-1:1)*Math.abs(planarRingArea(r)),0),0); }
const kind=f=>f.properties.feature_type||'building';
const identifier=(f,i)=>String(f.id||f.properties.id||`boundary-${i+1}`).slice(0,200);
function overlapArea(a,b) { const hit=intersect(featureCollection([a,b])); return hit ? planarArea(hit.geometry) : 0; }
const boxesOverlap=(a,b)=>!(a[2]<b[0]||a[0]>b[2]||a[3]<b[1]||a[1]>b[3]);
function box(f) { const p=polygonsOf(f.geometry).flat(2);return [Math.min(...p.map(v=>v[0])),Math.min(...p.map(v=>v[1])),Math.max(...p.map(v=>v[0])),Math.max(...p.map(v=>v[1]))]; }

export function reconcileProposals(proposals,existing=[]) {
  if (!Array.isArray(proposals)||!Array.isArray(existing)) geometryError('Proposals and existing boundaries must be arrays.');
  const p=prepare([...existing,...proposals]),offset=existing.length,accepted=new Set(existing.map((_,i)=>i)),features=[],duplicates=[],boxes=p.polygons.map(box);
  let checks=0;
  for (let i=offset;i<p.features.length;i++) {
    let best;
    if (kind(p.features[i])==='building') for (const j of accepted) {
      if (kind(p.features[j])!=='building'||!boxesOverlap(boxes[i],boxes[j])) continue;
      if (++checks>100000) geometryError('Too many intersecting boundaries; split the survey.');
      const intersection=overlapArea(p.polygons[i],p.polygons[j]); if (!intersection) continue;
      const iou=intersection/(p.areas[i]+p.areas[j]-intersection),coverage=intersection/Math.min(p.areas[i],p.areas[j]);
      if (iou>=.55 && coverage>=.85 && (!best||iou>best.iou)) best={iou,coverage,j};
    }
    if (best) duplicates.push({proposal_index:i-offset,existing_id:identifier(p.features[best.j],best.j),iou:round(best.iou,5),smaller_coverage:round(best.coverage,5)});
    else { accepted.add(i);features.push(p.features[i]); }
  }
  return {type:'FeatureCollection',features,duplicates,summary:{received:proposals.length,duplicates:duplicates.length,kept:features.length}};
}

export function projectQuality(features) {
  const p=prepare(features),issues=[],layers={},boxes=p.polygons.map(box);let pairs=0,truncated=false;
  for (let i=0;i<features.length;i++) for (let j=i+1;j<features.length;j++) {
    if (kind(p.features[i])!==kind(p.features[j]) || !boxesOverlap(boxes[i],boxes[j])) continue;
    if (++pairs>100000) geometryError('Too many intersecting boundaries; split the survey.');
    const area=overlapArea(p.polygons[i],p.polygons[j]);
    if (area<.1||area/Math.min(p.areas[i],p.areas[j])<.001) continue;
    const iou=area/(p.areas[i]+p.areas[j]-area);
    if (issues.length>=500) { truncated=true;continue; }
    issues.push({kind:iou>=.85?'duplicate':'overlap',feature_type:kind(p.features[i]),feature_ids:[identifier(p.features[i],i),identifier(p.features[j],j)],names:[String(p.features[i].properties.name||'').slice(0,200),String(p.features[j].properties.name||'').slice(0,200)],overlap_sqm:round(area),iou:round(iou,5)});
  }
  for (const layer of [...new Set(p.features.map(kind))].sort()) {
    const indexes=p.features.map((f,i)=>kind(f)===layer?i:-1).filter(i=>i>=0),selected=indexes.map(i=>p.polygons[i]),total=indexes.reduce((sum,i)=>sum+p.areas[i],0);
    const merged=selected.length===1?selected[0]:union(featureCollection(selected)),covered=merged?planarArea(merged.geometry):0;
    layers[layer]={features:selected.length,summed_area_sqm:round(total,3),covered_area_sqm:round(covered,3),overcount_sqm:round(Math.max(0,total-covered),3)};
  }
  return {issues,summary:{features:features.length,issues:issues.length,truncated,layers},limitations:['Overlaps are review warnings; intentional overlaps can be valid.','No gap/coverage claim is made without an authoritative survey coverage boundary.','Geometry checks do not establish land ownership or positional accuracy.']};
}

export function previewParcelImport(collection,featureType='parcel') {
  if (!['building','parcel'].includes(featureType)) geometryError('Import type must be building or parcel.');
  if (!object(collection)||collection.type!=='FeatureCollection'||!Array.isArray(collection.features)) geometryError('Import a WGS84 GeoJSON FeatureCollection.');
  if (collection.crs != null) geometryError('Reproject to WGS84 GeoJSON without a legacy CRS declaration before importing.');
  if (!collection.features.length || collection.features.length>1000 || new TextEncoder().encode(JSON.stringify(collection)).length>8*1024*1024) geometryError('Import 1–1,000 boundaries in a GeoJSON file smaller than 8 MB.');
  const features=collection.features.map((f,i)=>{
    if (!object(f)||f.type!=='Feature'||(f.properties!=null&&!object(f.properties))) geometryError(`Imported boundary ${i+1} must be a Feature with object properties.`);
    const p=f.properties||{},m=measureGeometry(f.geometry),reference=String(p.reference_id||p.parcel_id||p.plot_id||f.id||'').slice(0,200);
    return {type:'Feature',id:'import-'+crypto.randomUUID().replaceAll('-',''),geometry:m.geometry,properties:{name:String(p.name||reference||`Imported ${featureType} ${i+1}`).slice(0,200),owner:String(p.owner||'').slice(0,200),feature_type:featureType,status:'draft',reference_id:reference,notes:('Imported reference boundary; confirm source, date, position and any ownership claim before review.\n'+String(p.notes||'')).slice(0,10000),area_sqm:m.area_sqm,perimeter_m:m.perimeter_m}};
  });
  return {type:'FeatureCollection',features,quality:projectQuality(features),saved:false,notice:'Imported boundaries are unsaved drafts. Review them against source records before saving.'};
}
