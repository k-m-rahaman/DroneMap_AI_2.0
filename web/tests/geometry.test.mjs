import test from 'node:test';
import assert from 'node:assert/strict';
import { measureGeometry, validateBounds, reconcileProposals, projectQuality, previewParcelImport, localProjector } from '../src/geometry.js';
import { planSurvey } from '../src/planning.js';
import { exportGeoJSON, exportCSV, buildReportPDF } from '../src/exports.js';

const ring=[[88.4,22.5],[88.401,22.5],[88.401,22.501],[88.4,22.501],[88.4,22.5]];
const square={type:'Polygon',coordinates:[ring]};
const hole=[[88.4002,22.5002],[88.4002,22.5008],[88.4008,22.5008],[88.4008,22.5002],[88.4002,22.5002]];
const feature=(geometry=square,properties={},id='building-1')=>({type:'Feature',geometry,properties,id});
const shifted=(x=0,y=0)=>({type:'Polygon',coordinates:[ring.map(p=>[p[0]+x,p[1]+y])]});
const rejects422=fn=>assert.throws(fn,error=>error.status===422);
const close=(a,b,tolerance=.0002)=>assert.ok(Math.abs(a-b)<=tolerance,`${a} is not within ${tolerance} of ${b}`);

test('WGS84 ellipsoid measures match native pyproj.Geod baseline',()=>{
  // Baseline recorded by geospatial.validate_geometry, pyproj.Geod(ellps=WGS84).
  const result=measureGeometry(square);
  assert.equal(result.area_sqm,11394.3901);assert.equal(result.perimeter_m,427.2659);
  assert.deepEqual(result.bbox,[88.4,22.5,88.401,22.501]);assert.equal(result.vertex_count,5);
  close(result.centroid[0],88.4005,1e-10);close(result.centroid[1],22.5005,1e-10);
  close(result.area_sqft,result.area_sqm*10.76391041671,.001);
});
test('holes subtract area and add perimeter; winding normalizes',()=>{
  const m=measureGeometry({type:'Polygon',coordinates:[[...ring].reverse(),[...hole].reverse()]});
  assert.equal(m.area_sqm,7292.4096);assert.equal(m.perimeter_m,683.6255);
  assert.deepEqual(m.geometry.coordinates,[ring,hole]);
});
test('MultiPolygon areas and perimeters include all components',()=>{
  const g={type:'MultiPolygon',coordinates:[square.coordinates,shifted(.002).coordinates]},m=measureGeometry(g);
  close(m.area_sqm,2*11394.3901);close(m.perimeter_m,2*427.2659);
});
test('geodesic local metre projector round trips coordinates',()=>{
  const projection=localProjector([88.4005,22.5005]);
  for(const p of ring){const q=projection.inverse(projection.forward(p));close(q[0],p[0],1e-10);close(q[1],p[1],1e-10);}
});
test('invalid bounds and antimeridian requests fail clearly',()=>{
  for(const b of [[0,0,0,1],[0,1,1,0],[179,0,-179,1],[-180,0,0,1],[0,0,1,Infinity],[false,0,1,1]])rejects422(()=>validateBounds(b));
  rejects422(()=>validateBounds([0,85,1,86],{mercator:true}));
});
test('non-polygon, unclosed, non-finite and degenerate coordinates rejected',()=>{
  for(const g of [null,{type:'Point',coordinates:[0,0]},{type:'Polygon',coordinates:[]},{type:'Polygon',coordinates:[ring.slice(0,-1)]},{type:'Polygon',coordinates:[[[0,0],[1,NaN],[1,1],[0,0]]]},{type:'Polygon',coordinates:[[[0,0],[1,1],[2,2],[0,0]]]},{type:'Polygon',coordinates:[[[0,0],[1,0],[1,91],[0,0]]]}])rejects422(()=>measureGeometry(g));
});
test('self-crossing, nonadjacent touching, overlapping and doubled edges rejected',()=>{
  for(const r of [ [[0,0],[1,1],[0,1],[1,0],[0,0]], [[0,0],[2,0],[1,0],[1,1],[0,1],[0,0]], [[0,0],[1,0],[1,1],[0,0],[0,1],[0,0]] ])rejects422(()=>measureGeometry({type:'Polygon',coordinates:[r]}));
});
test('outside, crossing and nested holes rejected',()=>{
  const outside=hole.map(p=>[p[0]+.01,p[1]]),crossing=hole.map(p=>[p[0]-.0005,p[1]]);
  for(const coordinates of [[ring,outside],[ring,crossing],[ring,hole,hole.map(p=>[88.4005+(p[0]-88.4005)*.5,22.5005+(p[1]-22.5005)*.5])]])rejects422(()=>measureGeometry({type:'Polygon',coordinates}));
});
test('overlapping MultiPolygon components rejected',()=>rejects422(()=>measureGeometry({type:'MultiPolygon',coordinates:[square.coordinates,shifted(.0005).coordinates]})));
test('MultiPolygon may touch at an isolated point, but may not share an edge',()=>{
  const one={type:'Polygon',coordinates:[[[0,0],[.001,0],[.001,.001],[0,.001],[0,0]]]};
  const two={type:'Polygon',coordinates:[[[.001,.001],[.002,.001],[.002,.002],[.001,.002],[.001,.001]]]};
  assert.ok(measureGeometry({type:'MultiPolygon',coordinates:[one.coordinates,two.coordinates]}).area_sqm>0);
  const shared={type:'Polygon',coordinates:[[[.001,0],[.002,0],[.002,.001],[.001,.001],[.001,0]]]};
  rejects422(()=>measureGeometry({type:'MultiPolygon',coordinates:[one.coordinates,shared.coordinates]}));
});
test('oversized and high-complexity input fails before topology work',()=>{
  rejects422(()=>measureGeometry({type:'Polygon',coordinates:[[[0,0],[6,0],[6,1],[0,1],[0,0]]]}));
  const many=Array.from({length:20001},(_,i)=>[88.4+Math.cos(i/20000*Math.PI*2)*.001,22.5+Math.sin(i/20000*Math.PI*2)*.001]);many.push(many[0]);
  rejects422(()=>measureGeometry({type:'Polygon',coordinates:[many]}));
});
test('reconciliation suppresses strong duplicate only and keeps adjacent structures',()=>{
  const response=reconcileProposals([feature(square,{},'same'),feature(shifted(.001),{},'adjacent'),feature(shifted(.0007),{},'overlap')],[feature()]);
  assert.deepEqual(response.summary,{received:3,duplicates:1,kept:2});assert.equal(response.duplicates[0].existing_id,'building-1');assert.equal(response.duplicates[0].iou,1);
});
test('reconciliation also deduplicates proposals, without suppressing parcel layers',()=>{
  const response=reconcileProposals([feature(),feature(square,{},'copy'),feature(square,{feature_type:'parcel'},'parcel')]);
  assert.equal(response.features.length,2);assert.equal(response.duplicates.length,1);
});
test('quality reports duplicates and separates natural building/parcel containment',()=>{
  const q=projectQuality([feature(),feature(square,{},'duplicate'),feature(square,{feature_type:'parcel'},'parcel')]);
  assert.equal(q.issues.length,1);assert.equal(q.issues[0].kind,'duplicate');assert.equal(q.summary.layers.building.features,2);assert.equal(q.summary.layers.parcel.overcount_sqm,0);
  close(q.summary.layers.building.overcount_sqm,11394.3901,.005);
});
test('quality clean and empty collections work',()=>{assert.equal(projectQuality([]).summary.features,0);assert.equal(projectQuality([feature(),feature(shifted(.002))]).issues.length,0);});
test('parcel imports become distinct unsaved drafts and preserve reference provenance',()=>{
  const imported=previewParcelImport({type:'FeatureCollection',features:[feature(square,{parcel_id:'Plot 123',owner:'Claimant',name:'Plot A'})]});
  assert.equal(imported.saved,false);assert.equal(imported.features[0].properties.reference_id,'Plot 123');assert.equal(imported.features[0].properties.feature_type,'parcel');assert.equal(imported.features[0].properties.status,'draft');assert.match(imported.features[0].properties.notes,/confirm source/);
});
test('imports reject CRS declarations, empty files and unsupported geometries',()=>{
  for(const c of [{type:'FeatureCollection',features:[]},{type:'FeatureCollection',crs:{},features:[feature()]},{type:'FeatureCollection',features:[feature({type:'Point',coordinates:[0,0]})]}])rejects422(()=>previewParcelImport(c));
});
test('native parity for zero and 37 degree survey routes',()=>{
  // Recorded native missions.plan_survey results; same ellipsoidal local projection.
  const zero=planSurvey({geometry:square}),angled=planSurvey({geometry:square,angle_deg:37});
  assert.equal(zero.summary.route_distance_m,520.12);assert.equal(zero.summary.waypoint_count,8);assert.equal(zero.summary.estimated_photos,44);assert.equal(zero.summary.line_spacing_m,25.724);
  assert.equal(angled.summary.route_distance_m,635.39);assert.equal(angled.summary.waypoint_count,12);assert.equal(angled.summary.estimated_photos,48);assert.equal(angled.summary.line_spacing_m,24.803);
  assert.equal(angled.plan.fileType,'Plan');assert.equal(angled.plan.mission.plannedHomePosition[2],0);
  for(const item of angled.plan.mission.items){assert.equal(item.command,16);assert.equal(item.frame,3);assert.equal(item.params[6],60);assert.equal(item.AMSLAltAboveTerrain,null);}
  assert.ok(angled.limitations.some(s=>s.includes('no arming')));
});
test('planner rejects holes, concavity, tiny areas and invalid settings',()=>{
  const concave={type:'Polygon',coordinates:[[[88.4,22.5],[88.401,22.5],[88.4005,22.5005],[88.401,22.501],[88.4,22.501],[88.4,22.5]]]};
  for(const body of [{geometry:{type:'Polygon',coordinates:[ring,hole]}},{geometry:concave},{geometry:square,altitude_m:0},{geometry:square,front_overlap:1},{geometry:square,image_width_px:100.5},{geometry:square,altitude_m:true},{geometry:{type:'Polygon',coordinates:[ring.map(p=>[88.4+(p[0]-88.4)*.001,22.5+(p[1]-22.5)*.001])]}}])rejects422(()=>planSurvey(body));
});
test('rotated routes and connecting legs remain inside convex selected area',()=>{
  const triangle={type:'Polygon',coordinates:[[[88.4,22.5],[88.401,22.5],[88.4005,22.501],[88.4,22.5]]]};
  for(const angle_deg of [0,22,90,137,180]){
    const result=planSurvey({geometry:triangle,angle_deg}),p=localProjector(measureGeometry(triangle).centroid),polygon=triangle.coordinates[0].map(p.forward),route=result.geojson.features[0].geometry.coordinates.map(p.forward);
    for(let index=0;index<route.length-1;index++)for(const fraction of [0,.25,.5,.75,1]){
      const point=[route[index][0]+(route[index+1][0]-route[index][0])*fraction,route[index][1]+(route[index+1][1]-route[index][1])*fraction];
      for(let i=0;i<polygon.length-1;i++){
        const a=polygon[i],b=polygon[i+1],cross=(b[0]-a[0])*(point[1]-a[1])-(b[1]-a[1])*(point[0]-a[0]);
        assert.ok(cross>=-.002,`route outside polygon at ${angle_deg}`);
      }
    }
  }
});
test('GIS exports preserve IDs and measured geometry; CSV quoting neutralizes formulas',()=>{
  const f=feature(square,{name:'=SUM(A1)',owner:'A, "B"',notes:'First\nSecond',area_sqm:999},'id-123'),collection=exportGeoJSON([f]),csv=exportCSV([f]);
  assert.equal(collection.features[0].properties.area_sqm,11394.3901);assert.equal(f.properties.area_sqm,999);assert.match(csv,/id-123/);assert.match(csv,/'=SUM\(A1\)/);assert.match(csv,/A, ""B""/);assert.match(csv,/First\nSecond/);assert.ok(csv.startsWith('\ufeff'));assert.doesNotThrow(()=>JSON.parse(JSON.stringify(collection)));
});
test('PDF export returns actual, multipage PDF with local evidence scope',async()=>{
  const bytes=await buildReportPDF(feature(square,{name:'Building 1',version:3,notes:'Visually checked; position requires survey control.'}),{name:'SIH Project',location:'Kolkata'},{name:'Uploaded image',kind:'raster',crs:'EPSG:4326',bounds:[88.4,22.5,88.401,22.501]});
  assert.ok(bytes instanceof Uint8Array);assert.equal(new TextDecoder().decode(bytes.subarray(0,5)),'%PDF-');
  const decoded=new TextDecoder('latin1').decode(bytes);assert.match(decoded,/\/Type \/Pages/);assert.ok(bytes.length>3000);assert.match(decoded,/%%EOF/);
});
