/** Local metre survey previews. No aircraft connection, mission upload or flight commands. */
import { measureGeometry, localProjector, geometryError } from './geometry.js';

const rounded=(value,n=3)=>Number(value.toFixed(n));
function number(body,name,fallback,low,high,integer=false) {
  const value=body[name]??fallback;
  if (typeof value!=='number'||!Number.isFinite(value)||value<low||value>high||(integer&&!Number.isInteger(value))) geometryError(`${name} must be ${integer?'an integer':'a finite number'} between ${low} and ${high}.`);
  return value;
}
const cross=(a,b,c)=>(b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0]);
const distance=(a,b)=>Math.hypot(b[0]-a[0],b[1]-a[1]);
function rotate(point,angle,origin) { const x=point[0]-origin[0],y=point[1]-origin[1],c=Math.cos(angle),s=Math.sin(angle);return [origin[0]+x*c-y*s,origin[1]+x*s+y*c]; }
function centroid(ring) {
  let area=0,x=0,y=0;
  for (let i=0;i<ring.length-1;i++) { const a=ring[i],b=ring[i+1],z=a[0]*b[1]-b[0]*a[1];area+=z;x+=(a[0]+b[0])*z;y+=(a[1]+b[1])*z; }
  return [x/(3*area),y/(3*area)];
}
function convex(ring) {
  const points=ring.slice(0,-1);let direction=0;
  for (let i=0;i<points.length;i++) { const value=cross(points[i],points[(i+1)%points.length],points[(i+2)%points.length]); if (Math.abs(value)<.00001) continue; if (direction&&Math.sign(value)!==direction) return false; direction=Math.sign(value); }
  return !!direction;
}
function within(point,ring,tolerance=.001) {
  for (let i=0;i<ring.length-1;i++) if (cross(ring[i],ring[i+1],point)<-tolerance*distance(ring[i],ring[i+1])) return false;
  return true;
}

export function planSurvey(body) {
  if (!body||typeof body!=='object'||Array.isArray(body)) geometryError('Survey settings must be an object.');
  const measured=measureGeometry(body.geometry),g=measured.geometry;
  if (g.type!=='Polygon') geometryError('Plan one contiguous polygon at a time.');
  if (g.coordinates.length!==1) geometryError('Plan separate convex sections around holes and exclusions.');
  if (measured.area_sqm<25||measured.area_sqm>1_000_000) geometryError('Plan a survey area between 25 m² and 1 km².');
  if (Math.abs(measured.centroid[1])>80) geometryError('Survey planning supports latitudes between 80° south and 80° north.');
  const altitude=number(body,'altitude_m',60,10,120),speed=number(body,'speed_m_s',5,.5,15),front=number(body,'front_overlap',.8,.1,.95),side=number(body,'side_overlap',.7,.1,.95);
  const sensorWidth=number(body,'sensor_width_mm',13.2,1,100),sensorHeight=number(body,'sensor_height_mm',8.8,1,100),focal=number(body,'focal_length_mm',8.8,1,200),imageWidth=number(body,'image_width_px',5472,100,20000,true),imageHeight=number(body,'image_height_px',3648,100,20000,true),angle=number(body,'angle_deg',body.angle??0,0,180);
  const projection=localProjector(measured.centroid),local=g.coordinates[0].map(projection.forward),xs=local.map(p=>p[0]),ys=local.map(p=>p[1]);
  if (Math.hypot(Math.max(...xs)-Math.min(...xs),Math.max(...ys)-Math.min(...ys))>4000) geometryError('Survey extent must fit within 4 km. Split long corridors into smaller sections.');
  if (!convex(local)) geometryError('Use a convex survey area so connecting flight segments remain inside it. Split concave areas into convex sections.');
  const footprintSide=altitude*sensorWidth/focal,footprintFront=altitude*sensorHeight/focal,maxSpacing=footprintSide*(1-side),trigger=footprintFront*(1-front),gsd=Math.max(footprintSide/imageWidth,footprintFront/imageHeight)*100;
  const origin=centroid(local),radians=angle*Math.PI/180,grid=local.map(p=>rotate(p,radians,origin)),left=Math.min(...grid.map(p=>p[0])),right=Math.max(...grid.map(p=>p[0]));
  const count=Math.max(1,Math.ceil((right-left)/maxSpacing));
  if (count*2>1000) geometryError('These settings exceed 1,000 waypoints. Reduce area or overlap, or increase altitude.');
  const spacing=(right-left)/count,segments=[],route=[];let photos=0;
  for (let index=0;index<count;index++) {
    const x=left+(index+.5)*spacing,intersections=[];
    for (let i=0;i<grid.length-1;i++) {
      const a=grid[i],b=grid[i+1];
      if (a[0]===b[0]) { if (Math.abs(a[0]-x)<1e-9) intersections.push(a[1],b[1]); continue; }
      const t=(x-a[0])/(b[0]-a[0]);
      if (t>=-1e-12&&t<=1+1e-12) intersections.push(a[1]+t*(b[1]-a[1]));
    }
    if (intersections.length<2) continue;
    const bottom=Math.min(...intersections),top=Math.max(...intersections);
    if (top-bottom<.01) continue;
    const segment=[[x,bottom],[x,top]].map(p=>rotate(p,-radians,origin));
    if (index%2) segment.reverse();
    segments.push(segment);route.push(...segment);photos+=Math.ceil(distance(...segment)/trigger)+1;
  }
  if (!segments.length||route.length>1000) geometryError('Selected area does not produce a valid route within the waypoint limit.');
  if (!route.every(point=>within(point,local))) geometryError('A route segment falls outside the selected area. Use a smaller convex section.');
  const length=route.slice(1).reduce((sum,p,i)=>sum+distance(route[i],p),0),geographic=route.map(projection.inverse),transects=segments.map(s=>s.map(projection.inverse)),first=geographic[0];
  const items=geographic.map(([lon,lat],i)=>({AMSLAltAboveTerrain:null,Altitude:altitude,AltitudeMode:1,autoContinue:true,command:16,doJumpId:i+1,frame:3,params:[0,0,0,null,lat,lon,altitude],type:'SimpleItem'}));
  return {
    summary:{area_sqm:measured.area_sqm,estimated_flight_seconds:rounded(length/speed,1),route_distance_m:rounded(length,2),estimated_photos:photos,gsd_cm:rounded(gsd),line_spacing_m:rounded(spacing),trigger_spacing_m:rounded(trigger),waypoint_count:items.length,transect_count:transects.length,altitude_m:altitude,speed_m_s:speed,angle_deg:angle},
    geojson:{type:'FeatureCollection',features:[{type:'Feature',properties:{kind:'survey_route',altitude_m:altitude},geometry:{type:'LineString',coordinates:geographic}},...transects.map((coordinates,index)=>({type:'Feature',properties:{kind:'transect',index:index+1},geometry:{type:'LineString',coordinates}}))]},
    plan:{fileType:'Plan',groundStation:'QGroundControl',version:1,geoFence:{circles:[],polygons:[],version:2},rallyPoints:{points:[],version:2},mission:{version:2,firmwareType:12,vehicleType:2,globalPlanAltitudeMode:1,cruiseSpeed:speed,hoverSpeed:speed,plannedHomePosition:[first[1],first[0],0],items}},
    limitations:[
      'Planning preview only. A pilot must inspect aircraft, firmware, route, actual home elevation, altitude, return and landing procedures in a compatible ground station.',
      'Waypoints use metres above home (MAV_FRAME_GLOBAL_RELATIVE_ALT). Home AMSL elevation is an unknown zero placeholder; set the actual home in the ground station.',
      'Export uses a PX4 multirotor template. Select the actual aircraft and firmware in the ground station. DJI SDK control is not supported.',
      'Waypoints only: no arming, takeoff, landing, mission upload, camera triggering or flight execution is performed by this browser app.',
      'GSD, overlap and photo counts assume level terrain, a nadir camera and the supplied physical camera settings. Configure triggering separately.',
      'Time estimates exclude ascent, descent, turns, wind and battery reserve. Obstacles, terrain, airspace and legal permissions are not assessed.',
    ],
  };
}
