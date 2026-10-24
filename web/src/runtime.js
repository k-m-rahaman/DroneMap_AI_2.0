/** Adapt the existing UI to real, local browser services; no pretend server/login. */
import * as store from './storage.js';
import {measureGeometry,projectQuality,reconcileProposals,previewParcelImport} from './geometry.js';
import {planSurvey} from './planning.js';
import {buildReportPDF,exportCSV,exportGeoJSON} from './exports.js';
import {importSource,fetchMapSource,getSourcePixels} from './sources.js';
import {scanBuildings} from './inference.js';

const BASE=DRONEMAP_WEB_BASE;
const originalFetch=window.fetch.bind(window);
const PROFILE={id:'this-browser-workspace',name:'SIH workspace',email:'Stored on this browser',role:'surveyor'};
const notice=store.WORKSPACE_NOTICE;
let engineError=null,scanning=false;
const hardware={available:false,dependency_available:false,connected:false,state:'unavailable',message:'Browser edition: import georeferenced drone imagery and preview a survey route. Live aircraft telemetry and capture require the local installation and hardware testing.',connection:null,telemetry:{},telemetry_freshness:{},supported_transports:[],controls:{can_arm:false,can_upload_mission:false,can_capture:false}};

function fail(message,status=422){const error=new Error(message);error.status=status;throw error;}
function modelStatus(){return {available:!engineError&&typeof WebAssembly==='object',state:engineError?'error':'browser_ready',message:engineError||'Actual Building segmentation model runs on this device. First scan downloads the model/runtime; review every outline.'};}
function progress(value){window.dispatchEvent(new CustomEvent('dronemap:progress',{detail:typeof value==='string'?value:value?.message||'Processing buildings on this device…'}));}
function json(value,status=200){return new Response(JSON.stringify(value),{status,headers:{'Content-Type':'application/json','Cache-Control':'no-store'}});}
async function scan(source,metadata,body,signal){
  if(scanning)fail('A scan is already running in this page.',429);
  scanning=true;
  try{
    const result=await scanBuildings(source,{confidence:body.confidence??0.5,minAreaSqm:body.min_area_sqm??4,bounds:body.bounds??null,signal,onProgress:progress,modelUrl:new URL(BASE+'models/buildings.onnx',location.origin).href,wasmBaseUrl:new URL(BASE+'runtime/',location.origin).href});
    for(const feature of result.geojson.features)feature.properties={...feature.properties,source_id:metadata.id};
    return {...result,source:metadata};
  }catch(error){if(/model|wasm|onnx|runtime/i.test(error.message))engineError=error.message;throw error;}
  finally{scanning=false;}
}

async function dispatch(path,method,body,headers,signal){
  await store.openStore();
  if(path==='/api/status')return {setup_required:false,authenticated:true,user:PROFILE,model:modelStatus(),version:'4.2.0-browser-sih',public_demo:false,demo:null,browser_workspace:true,notice};
  if(path==='/api/login'||path==='/api/setup')return {user:PROFILE,notice};
  if(path==='/api/logout')return {ok:true,notice:'Workspace closed; saved projects remain in this browser.'};
  if(path==='/api/demo/start')fail('This is the browser workspace. Open it without a guest session.',404);
  if(path==='/api/drone/status'||path==='/api/drone/disconnect')return hardware;
  if(path==='/api/drone/ports')return {ports:[]};
  if(path==='/api/drone/connect')fail(hardware.message,403);
  if(path==='/api/geometry/measure')return measureGeometry(body.geometry);
  if(path==='/api/projects')return method==='POST'?store.createProject(body):{projects:await store.listProjects()};
  let match=path.match(/^\/api\/projects\/([^/]+)(?:\/(.*))?$/);
  if(match){
    const [,id,action]=match;
    const project=await store.getProject(id);
    if(!action)return method==='PUT'?store.updateProject(id,body):project;
    if(action==='features')return method==='POST'?store.createFeature(id,body,headers.get('Idempotency-Key')):store.listFeatures(id);
    if(action==='sources'){
      if(method!=='POST')return {sources:await store.listSources(id)};
      if(!(body instanceof FormData))fail('Choose an imagery file to import.');
      let bounds=null;
      if(body.get('bounds')){try{bounds=JSON.parse(body.get('bounds'));}catch{fail('Image bounds must be valid longitude/latitude JSON.');}}
      return store.addSource(id,await importSource(body.get('file'),bounds));
    }
    const features=await store.listFeatures(id);
    if(action==='quality')return projectQuality(features.features);
    if(action==='proposals/reconcile'){
      const drafts=body.existing||[],overrides=new Set(drafts.filter(f=>f.id).map(f=>String(f.id)));
      return reconcileProposals(body.features,features.features.filter(f=>!overrides.has(String(f.id))).concat(drafts));
    }
    if(action==='imports/preview')return previewParcelImport(body.collection,body.feature_type||'parcel');
    if(action==='missions/preview')return planSurvey(body);
    if(action==='export.geojson'){
      const result=exportGeoJSON(features.features,project);
      return new Response(typeof result==='string'?result:JSON.stringify(result,null,2),{headers:{'Content-Type':'application/geo+json'}});
    }
    if(action==='export.csv')return new Response(exportCSV(features.features,project),{headers:{'Content-Type':'text/csv;charset=utf-8'}});
    if(action==='scans/map'){
      if(!navigator.onLine)fail('Map acquisition needs internet. Use an imported or saved source while offline.',503);
      progress('Fetching original map pixels…');
      const source=await fetchMapSource(body.bounds,{signal,onProgress:progress});
      const metadata=await store.addSource(id,source);
      return scan(await getSourcePixels(source),metadata,body,signal);
    }
  }
  match=path.match(/^\/api\/features\/([^/]+)(?:\/(.*))?$/);
  if(match){
    const [,id,action]=match;
    if(action==='history')return store.featureHistory(id);
    if(action==='report.pdf'){
      const feature=await store.getFeature(id),project=await store.getProject(feature.properties.project_id);
      const source=feature.properties.source_id?await store.getSource(feature.properties.source_id).catch(()=>null):null;
      const pdf=await buildReportPDF(feature,project,source);
      return new Response(pdf,{headers:{'Content-Type':'application/pdf'}});
    }
    if(method==='PUT')return store.updateFeature(id,body);
    if(method==='DELETE')return store.deleteFeature(id,body.version);
    return store.getFeature(id);
  }
  match=path.match(/^\/api\/sources\/([^/]+)(?:\/(.*))?$/);
  if(match){
    const [,id,action]=match;
    if(action==='scan'){
      const record=await store.getSource(id,{includeBlob:true}),source=await getSourcePixels(record);
      return scan(source,await store.getSource(id),body,signal);
    }
    if(action==='preview'){
      const record=await store.getSource(id,{includeBlob:true});
      return new Response(record.previewBlob||record.blob,{headers:{'Content-Type':'image/png'}});
    }
    return store.getSource(id);
  }
  fail('This endpoint is not available in the browser workspace.',404);
}

window.fetch=async function(input,init={}){
  const url=new URL(typeof input==='string'||input instanceof URL?input:input.url,location.href);
  if(url.origin!==location.origin||!url.pathname.startsWith('/api/'))return originalFetch(input,init);
  const method=(init.method||input?.method||'GET').toUpperCase();
  try{
    let body=init.body??null;
    const headers=new Headers(init.headers||input?.headers||{});
    if(typeof body==='string'){try{body=JSON.parse(body);}catch{fail('Invalid JSON request.');}}
    if(!body)body={};
    if(method==='DELETE')body={...body,version:Number(url.searchParams.get('version'))};
    const result=await dispatch(url.pathname,method,body,headers,init.signal);
    return result instanceof Response?result:json(result,method==='POST'&&(/\/features$|^\/api\/projects$|\/sources$/).test(url.pathname)?201:200);
  }catch(error){console.error('Browser workspace:',error);return json({detail:error.message||'Browser operation failed.'},error.status||503);}
};
window.DroneMapBrowser={base:BASE,notice,ready:store.openStore(),modelStatus};
