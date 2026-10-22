'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../static/existing-app.js'),'utf8');
function deferred(){let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};}
function storage(){const values=new Map();return {get length(){return values.size;},key:i=>[...values.keys()][i]??null,getItem:k=>values.get(k)??null,setItem:(k,v)=>values.set(k,String(v)),removeItem:k=>values.delete(k)};}
function harness(sharedStorage=storage()){
    const elements=new Map();
    function element(id){if(!elements.has(id))elements.set(id,{value:'',textContent:'',innerHTML:'',disabled:false,style:{},children:[],classList:{add(){},remove(){},toggle(){},contains(){return false;}},appendChild(child){this.children.push(child);},replaceChildren(){this.children=[];},removeAttribute(){},setAttribute(){},remove(){},focus(){},addEventListener(){}});return elements.get(id);}
    const context=vm.createContext({console,structuredClone,FormData,crypto:require('node:crypto').webcrypto,navigator:{onLine:true},setTimeout:()=>0,clearTimeout(){},
        document:{documentElement:{},getElementById:element,createElement:()=>element(Symbol()),createTextNode:t=>({textContent:t}),querySelectorAll:()=>[],querySelector:()=>null},
        window:{localStorage:sharedStorage,map:{removeLayer(){},closePopup(){},pm:{removeControls(){}},getSize:()=>({x:1000,y:700}),getBounds:()=>({getWest:()=>1,getSouth:()=>2,getEast:()=>3,getNorth:()=>4}),getContainer:()=>({getBoundingClientRect:()=>({left:0})}),containerPointToLatLng:p=>({lng:p[0]/1000,lat:1-p[1]/1000})},confirm:()=>true,prompt:()=>null},
        L:{geoJSON:f=>({getLayers:()=>[{options:{},toGeoJSON:()=>f,addTo(){return this;},setStyle(){},bindPopup(){},setPopupContent(){},on(){}}]})},
        fetch:async()=>{throw Error('Unexpected fetch');}});
    vm.runInContext(source.replace(/initialize\(\);\s*$/,''),context);
    vm.runInContext('renderRegistry=()=>{};renderDetectionList=()=>{};renderSources=()=>{};showToast=(message)=>{globalThis.lastToast=message;};globalThis.testState=state;',context);
    element('minAreaInput').value='4';
    return {context,state:context.testState,element,storage:sharedStorage,run:s=>vm.runInContext(s,context)};
}
function feature(name='House',id='f1'){return {type:'Feature',id,geometry:{type:'Polygon',coordinates:[[[1,1],[1.001,1],[1.001,1.001],[1,1.001],[1,1]]]},properties:{name,feature_type:'building',area_sqm:100,perimeter_m:40,version:1,status:'draft'}};}
function entry(f=feature()){return {feature:f,layer:{toGeoJSON:precision=>{assert.equal(precision,false);return {geometry:f.geometry};},setPopupContent(){},setStyle(){}},saved:false,dirty:true,revision:0,measureSequence:0,measurement:null,saving:false,fields:{name:f.properties.name,owner:''},idempotencyKey:'stable-request'};}
function reportHarness(){
    const h=harness(),objectURLs=[],downloads=[],revoked=[],timers=[],createElement=h.context.document.createElement;
    h.context.document.body=h.element('body');
    h.context.document.createElement=tag=>{const el=createElement();if(tag==='a')el.click=()=>downloads.push({href:el.href,name:el.download});return el;};
    h.context.URL={createObjectURL:blob=>{objectURLs.push(blob);return 'blob:test-'+objectURLs.length;},revokeObjectURL:url=>revoked.push(url)};
    h.context.setTimeout=(callback,delay)=>{timers.push({callback,delay});return timers.length;};
    return {...h,objectURLs,downloads,revoked,timers};
}

test('public demo opens an isolated survey workspace without requesting an email or password',async()=>{
    const h=harness(),requests=[],project={id:'demo-project',name:'SIH visitor survey'};
    h.context.mockApi=async(p,o)=>{requests.push({p,o});if(p==='/api/status')return {public_demo:true,demo:{expires_in_seconds:7200},model:{available:true}};if(p==='/api/demo/start')return {user:{id:'visitor-1',name:'Demo surveyor'},project};if(p==='/api/projects')return {projects:[project]};throw Error('Unexpected endpoint '+p);};
    h.run('api=mockApi;loadVerifiedRecords=async()=>true;loadSources=async()=>{};renderModel=()=>{};startDronePolling=()=>{};');
    await h.context.login('viewer');
    assert.deepEqual(requests.map(r=>r.p),['/api/status','/api/demo/start','/api/projects']);
    assert.equal(requests[1].o.method,'POST');assert.equal(requests[1].o.body,undefined);
    assert.equal(h.state.role,'official');assert.equal(h.state.user.id,'visitor-1');assert.equal(h.state.project.id,project.id);assert.equal(h.state.busy,false);
    assert.equal(h.element('landing-btn').textContent,'Open SIH demo');assert.match(h.element('demoNoticeText').textContent,/Temporary workspace/);assert.match(h.element('demoNoticeText').textContent,/sample data only/);
});

test('local deployment keeps password authentication and does not create a demo guest',async()=>{
    const h=harness(),requests=[];h.context.mockApi=async(p)=>{requests.push(p);return {public_demo:false,user:null,setup_required:false};};h.run('api=mockApi');
    await h.context.login('official');assert.deepEqual(requests,['/api/status']);assert.equal(h.state.user,null);assert.match(h.context.lastToast,/email and password/);assert.equal(h.element('landing-btn').textContent,'Access Portal');
});

test('demo starts at the Kolkata sample within the scan limit without changing the local or returning visitor map',()=>{
    const local=harness(),localViews=[];local.context.window.map.setView=(center,zoom)=>localViews.push({center:Array.from(center),zoom});
    local.context.applyDeploymentStatus({public_demo:false});assert.equal(local.context.defaultMapZoom(),17);assert.deepEqual(Array.from(local.context.defaultMapCenter()),[24.0885,87.715]);assert.deepEqual(localViews,[]);
    const demo=harness(),demoViews=[];demo.context.window.map.setView=(center,zoom)=>demoViews.push({center:Array.from(center),zoom});
    demo.context.applyDeploymentStatus({public_demo:true});assert.equal(demo.context.defaultMapZoom(),19);assert.deepEqual(demoViews,[{center:[22.577,88.416],zoom:19}]);
    assert.match(demo.element('mapImageryStatus').textContent,/Kolkata sample area/);assert.equal(demo.element('inputLat').value,22.577);assert.equal(demo.element('inputLon').value,88.416);
    demo.context.applyDeploymentStatus({public_demo:true});assert.equal(demoViews.length,1);
    demo.state.user={id:'returning-visitor'};demo.context.applyDeploymentStatus({public_demo:true});assert.equal(demoViews.length,1);
});

test('missing basemap tiles fall back one parent level at a time, ignore stale errors and stop at level 16',()=>{
    const h=harness();let tileError,redraws=0;const satellite={options:{maxNativeZoom:19},on(event,handler){assert.equal(event,'tileerror');tileError=handler;},redraw(){redraws++;}};
    assert.equal(h.context.configureSatelliteFallback(satellite),satellite);h.context.setScanStatus('Imagery scan failed',true);
    tileError({coords:{z:19}});assert.equal(satellite.options.maxNativeZoom,18);assert.equal(redraws,1);assert.match(h.element('mapImageryStatus').textContent,/lower-resolution/);
    tileError({coords:{z:19}});assert.equal(redraws,1);assert.equal(satellite.options.maxNativeZoom,18);
    tileError({coords:{z:18}});tileError({coords:{z:17}});assert.equal(satellite.options.maxNativeZoom,16);assert.equal(redraws,3);
    tileError({coords:{z:16}});tileError({coords:{z:16}});assert.equal(redraws,3);assert.match(h.element('mapImageryStatus').textContent,/unavailable here/);
    assert.equal(h.element('scanStatus').textContent,'Imagery scan failed');
});

test('offline basemap errors do not trigger fallback request batches',()=>{
    const h=harness();let tileError,redraws=0;const satellite={options:{maxNativeZoom:19},on(event,handler){tileError=handler;},redraw(){redraws++;}};
    h.context.configureSatelliteFallback(satellite);h.context.navigator.onLine=false;tileError({coords:{z:19}});assert.equal(redraws,0);assert.equal(satellite.options.maxNativeZoom,19);
});

test('demo report descriptions remain accurate after language changes and local QR descriptions are restored',()=>{
    const h=harness();h.context.applyDeploymentStatus({public_demo:true});h.context.changeLanguage('bn');
    assert.match(h.element('feat3-d').textContent,/Demo reports have no public verification link/);assert.equal(h.element('inspectorReportBtn').textContent,'↓ Survey PDF');
    h.context.applyDeploymentStatus({public_demo:false});assert.equal(h.element('feat3-d').textContent,h.run('i18n.bn.feat3_d'));assert.equal(h.element('inspectorReportBtn').textContent,'↓ Survey PDF with QR');
});

test('survey reports fetch the authenticated PDF bytes and trigger a download without opening a popup',async()=>{
    const h=reportHarness(),requests=[],pdf=new Blob(['%PDF-1.7\nreal report bytes'],{type:'application/pdf'});h.state.user={id:'surveyor'};
    h.context.window.open=()=>{throw Error('Reports must not depend on popups');};
    h.context.fetch=async(path,options)=>{requests.push({path,options});return {ok:true,blob:async()=>pdf};};
    await h.context.openReport('survey/id');
    assert.equal(requests.length,1);assert.equal(requests[0].path,'/api/features/survey%2Fid/report.pdf');assert.equal(requests[0].options.credentials,'same-origin');assert.equal('responseType' in requests[0].options,false);
    assert.equal(h.objectURLs[0],pdf);assert.deepEqual(h.downloads,[{href:'blob:test-1',name:'dronemap-survey-survey_id.pdf'}]);assert.match(h.context.lastToast,/download started/);
    assert.equal(h.timers[0].delay,1000);h.timers[0].callback();assert.deepEqual(h.revoked,['blob:test-1']);
});

test('failed report requests show the server error and never download an error payload',async()=>{
    const h=reportHarness();h.state.user={id:'surveyor'};h.context.fetch=async()=>({ok:false,status:404,json:async()=>({detail:'Survey record no longer exists'})});
    await h.context.openReport('missing');assert.equal(h.context.lastToast,'Survey record no longer exists');assert.deepEqual(h.downloads,[]);assert.deepEqual(h.objectURLs,[]);
});

test('an expired report session follows normal session recovery instead of downloading a login error',async()=>{
    const h=reportHarness();h.state.user={id:'surveyor'};h.state.project={id:'p1'};h.state.role='official';h.state.entries.set('draft',entry());
    h.context.fetch=async()=>({ok:false,status:401,json:async()=>({detail:'Session expired'})});
    await h.context.openReport('report');assert.equal(h.state.user,null);assert.match(h.context.lastToast,/session expired.*recover unsaved boundaries/i);assert.ok(h.storage.getItem('dronemap.recovery.v1:surveyor:p1'));
    assert.deepEqual(h.downloads,[]);assert.deepEqual(h.objectURLs,[]);
});

test('a report arriving after an account change is discarded without creating a download URL',async()=>{
    const h=reportHarness(),pending=deferred();h.state.user={id:'first-surveyor'};h.context.fetch=()=>pending.promise;
    const download=h.context.openReport('report');h.context.clearWorkspace();h.state.user={id:'other-surveyor'};
    pending.resolve({ok:true,blob:async()=>new Blob(['%PDF-1.7\nprivate report'],{type:'application/pdf'})});await download;
    assert.deepEqual(h.downloads,[]);assert.deepEqual(h.objectURLs,[]);assert.equal(h.context.lastToast,undefined);
});

test('a successful HTTP response containing invalid PDF content is not saved as a survey document',async()=>{
    const h=reportHarness();h.state.user={id:'surveyor'};h.context.fetch=async()=>({ok:true,blob:async()=>new Blob(['<html>Not a report</html>'],{type:'application/pdf'})});
    await h.context.openReport('report');assert.match(h.context.lastToast,/did not return a survey PDF/);assert.deepEqual(h.downloads,[]);assert.deepEqual(h.objectURLs,[]);
});

test('returning demo visitors reuse their active workspace rather than creating another visitor',async()=>{
    const h=harness(),requests=[],project={id:'existing-project',name:'My temporary survey'};
    h.context.mockApi=async(p)=>{requests.push(p);if(p==='/api/status')return {public_demo:true,user:{id:'existing-guest',name:'Demo surveyor'},model:{available:true}};if(p==='/api/projects')return {projects:[project]};throw Error('Unexpected endpoint '+p);};
    h.run('api=mockApi;loadVerifiedRecords=async()=>true;loadSources=async()=>{};renderModel=()=>{};startDronePolling=()=>{};');
    await h.context.login('official');assert.deepEqual(requests,['/api/status','/api/projects']);assert.equal(h.state.user.id,'existing-guest');assert.equal(h.state.project.id,project.id);
});

test('public upload size guidance and preflight match the hosted limit while local imports retain 64 MB',()=>{
    const h=harness(),file={name:'example.tif',size:9*1024*1024};h.context.applyDeploymentStatus({public_demo:true,demo:{expires_in_seconds:7200}});
    h.context.updateFileInfo(file);assert.equal(h.state.selectedFile,null);assert.match(h.context.lastToast,/up to 8 MB/);assert.match(h.element('uploadLimits').textContent,/8 MB/);assert.match(h.element('scanLimits').textContent,/500/);
    h.context.applyDeploymentStatus({public_demo:false});h.context.updateFileInfo(file);assert.equal(h.state.selectedFile,file);assert.match(h.element('uploadLimits').textContent,/64 MB/);
});

test('public demo and unavailable telemetry cannot be connected or fabricated while planner stays enabled',async()=>{
    const h=harness();h.state.user={id:'demo'};h.state.role='official';h.state.project={id:'p'};h.state.publicDemo=true;
    h.state.drone={available:false,state:'unavailable',connected:false,message:'Public demo disables hardware; use a local installation.',telemetry:{}};
    let requests=0;h.context.mockApi=async()=>{requests++;};h.run('api=mockApi');h.context.renderDroneStatus();h.context.setBusy(true);h.context.setBusy(false);
    assert.equal(h.element('droneConnectBtn').disabled,true);assert.equal(h.element('droneDisconnectBtn').disabled,true);assert.equal(h.element('missionPreviewBtn').disabled,false);
    assert.equal(h.element('droneStatusBadge').textContent,'Local installation required');assert.equal(h.element('telemetryBattery').textContent,'—');
    await h.context.connectDrone({preventDefault(){}});await h.context.disconnectDrone();await h.context.refreshDronePorts();assert.equal(requests,0);
    h.state.publicDemo=false;h.context.setBusy(false);assert.equal(h.element('droneConnectBtn').disabled,true);
});

test('an expired demo session explains temporary retention without promising account recovery',async()=>{
    const h=harness();h.state.publicDemo=true;h.state.user={id:'guest'};h.state.project={id:'p'};h.state.role='official';
    h.context.fetch=async()=>({ok:false,status:401,json:async()=>({detail:'Expired demo session'})});
    await assert.rejects(h.context.api('/api/projects'),/Expired demo session/);assert.equal(h.state.user,null);assert.match(h.context.lastToast,/temporary demo session expired/);assert.ok(!h.context.lastToast.includes('same account'));
});
test('the landing and access styling is retained while a dedicated post-login workspace is available',()=>{
    const original=JSON.parse(fs.readFileSync(path.join(__dirname,'fixtures/original_visuals.json'),'utf8'));
    const updated=fs.readFileSync(path.join(__dirname,'../index.html'),'utf8').split('    <script src="/static/existing-app.js">')[0];
    assert.equal(updated.match(/<style>[\s\S]*?<\/style>/)[0].replace(/\r\n/g,'\n'),original.style.replace(/\r\n/g,'\n'));
    const classes=[...updated.split('    <!-- DASHBOARD VIEW:')[0].matchAll(/class="([^"]*)"/g)].map(x=>x[1]);
    assert.deepEqual(classes,original.classes.slice(0,classes.length));
    assert.ok(updated.includes('src="/drone_bg.mp4"'));assert.ok(updated.includes('src="/my_logo.png"'));
    assert.ok(updated.includes('onclick="logout()"'));assert.ok(updated.includes('value="0.5"'));
    assert.ok(updated.includes('id="drone-content"'));assert.ok(updated.includes('id="projectSelect"'));assert.ok(updated.includes('/static/workspace.css'));
});
test('saves use the current edited geometry and retain one idempotency key across a failed retry',async()=>{
    const h=harness(),e=entry();h.state.user={name:'Surveyor'};h.state.role='official';h.state.project={id:'p1'};h.state.entries.set('k',e);h.element('propName-k').value='House';
    const edited=structuredClone(e.feature.geometry);edited.coordinates[0][1][0]=1.002;
    e.layer.toGeoJSON=precision=>{assert.equal(precision,false);return {geometry:edited};};
    const requests=[];let attempt=0;h.context.mockApi=async(p,o)=>{requests.push({p,o});if(++attempt===1)throw Error('Connection lost');return {...feature(),geometry:edited,properties:{...feature().properties,status:'reviewed'}};};h.run('api=mockApi');
    await h.context.saveProperty('k');assert.equal(e.saved,false);await h.context.saveProperty('k');assert.equal(e.saved,true);
    assert.equal(requests[0].o.headers['Idempotency-Key'],'stable-request');assert.equal(requests[1].o.headers['Idempotency-Key'],'stable-request');
    assert.deepEqual(JSON.parse(requests[1].o.body).geometry,edited);assert.equal(h.state.records.length,1);assert.equal(h.state.busy,false);
});
test('double save clicks issue a single request and later edits stay dirty',async()=>{
    const h=harness(),e=entry(),pending=deferred();h.state.user={};h.state.role='official';h.state.project={id:'p1'};h.state.entries.set('k',e);h.element('propName-k').value='House';
    let count=0;h.context.mockApi=()=>{count++;return pending.promise;};h.run('api=mockApi');
    const save=h.context.saveProperty('k');await h.context.saveProperty('k');assert.equal(count,1);e.revision++;
    pending.resolve(feature());await save;assert.equal(e.dirty,true);assert.equal(e.saving,false);
});
test('an ambiguous create retry resolves the original payload before updating newer edits',async()=>{
    const h=harness(),e=entry();h.state.user={};h.state.role='official';h.state.project={id:'p1'};h.state.entries.set('k',e);h.element('propName-k').value='House';
    const requests=[];h.context.mockApi=async(p,o)=>{requests.push({p,o});if(requests.length===1)throw Error('Response lost');return feature();};h.run('api=mockApi');
    await h.context.saveProperty('k');h.context.updateEntryField('k','name','Renamed house');e.feature.geometry.coordinates[0][1][0]=1.002;
    await h.context.saveProperty('k');assert.equal(requests[0].o.body,requests[1].o.body);assert.equal(e.saved,true);assert.equal(e.dirty,true);assert.equal(e.fields.name,'Renamed house');
    await h.context.saveProperty('k');assert.equal(requests[2].o.method,'PUT');assert.equal(JSON.parse(requests[2].o.body).properties.name,'Renamed house');
});
test('stale boundary measurement responses cannot overwrite newer edits',async()=>{
    const h=harness(),e=entry(),a=deferred(),b=deferred();h.state.user={};h.state.role='official';h.state.entries.set('k',e);let n=0;
    h.context.mockApi=()=>++n===1?a.promise:b.promise;h.run('api=mockApi');
    const first=h.context.geometryChanged('k'),second=h.context.geometryChanged('k');b.resolve({area_sqm:222});await second;a.resolve({area_sqm:111});await first;
    assert.equal(e.measurement.area_sqm,222);assert.equal(e.revision,2);
});
test('map scan uses the selected rectangle and adds every returned building',async()=>{
    const h=harness();h.state.user={};h.state.role='official';h.state.project={id:'p1'};h.element('inputCalibration').value='.5';
    h.state.scanSelection={getBounds:()=>({getWest:()=>88.1,getSouth:()=>22.1,getEast:()=>88.2,getNorth:()=>22.2})};
    const buildings=['a','b','c'].map((id,i)=>{const f=feature('Building footprint',id);f.geometry.coordinates[0]=f.geometry.coordinates[0].map(([x,y])=>[x+i*.01,y]);return f;});
    const added=[],requests=[];h.context.capture=f=>added.push(f);h.context.mockApi=async(p,o)=>{requests.push({p,body:JSON.parse(o.body)});if(p.endsWith('/proposals/reconcile'))return {features:buildings,summary:{duplicates:0}};return {source:{id:'s1',name:'Map',width:512,height:512,preview_url:'/api/sources/s1/preview'},geojson:{features:buildings},inference:{tiles_processed:2}};};
    h.run('api=mockApi;addEntry=capture;showSource=()=>{}');await h.context.executeAI();
    assert.deepEqual(requests[0].body.bounds,[88.1,22.1,88.2,22.2]);assert.equal(added.length,3);assert.equal(new Set(added.map(f=>f.properties.name)).size,3);assert.ok(added.every(f=>f.properties.source_id==='s1'));assert.equal(h.state.busy,false);assert.match(h.element('scanStatus').textContent,/Added 3 building proposals/);
});
test('failed AI scans do not invent boundaries or discard existing drafts',async()=>{
    const h=harness(),e=entry();h.state.user={};h.state.role='official';h.state.project={id:'p1'};h.state.entries.set('draft',e);h.element('inputCalibration').value='.5';
    h.context.mockApi=async()=>{throw Error('Imagery unavailable');};h.run('api=mockApi');await h.context.executeAI();
    assert.equal(h.state.entries.size,1);assert.equal(h.state.entries.get('draft'),e);assert.equal(h.state.busy,false);assert.equal(h.context.lastToast,'Imagery unavailable');
    assert.equal(h.element('scanStatus').textContent,'Imagery unavailable');h.context.setBusy(false);assert.equal(h.element('scanStatus').textContent,'Imagery unavailable');
    h.context.setBusy(true,'Importing imagery…');assert.equal(h.element('scanStatus').textContent,'');
});

test('invalid scan inputs remain visible without sending an inference request',async()=>{
    const h=harness();h.state.user={};h.state.role='official';h.state.project={id:'p1'};h.element('inputCalibration').value='invalid';
    let calls=0;h.context.mockApi=async()=>{calls++;};h.run('api=mockApi');await h.context.executeAI();
    assert.equal(calls,0);assert.match(h.element('scanStatus').textContent,/confidence/i);assert.equal(h.element('scanStatus').textContent,h.context.lastToast);assert.equal(h.state.busy,false);
});
test('a scan completing after sign-out cannot insert results into another workspace',async()=>{
    const h=harness(),pending=deferred();h.state.user={};h.state.role='official';h.state.project={id:'p1'};h.element('inputCalibration').value='.5';
    let added=0;h.context.capture=()=>added++;h.context.mockApi=()=>pending.promise;h.run('api=mockApi;addEntry=capture;showSource=()=>{}');const scan=h.context.executeAI();h.context.clearWorkspace();
    pending.resolve({source:{id:'s1'},geojson:{features:[feature()]}});await scan;assert.equal(added,0);assert.equal(h.state.user,null);
});
test('popup values are escaped and uploads require explicit finite geographic bounds',()=>{
    const h=harness(),e=entry(feature('<img src=x onerror=alert(1)>'));h.state.role='official';h.state.entries.set('k',e);
    const html=h.context.popupContent('k');assert.ok(!html.includes('<img'));assert.ok(html.includes('&lt;img'));
    assert.throws(()=>h.context.parseBounds('1,2,,4'));assert.throws(()=>h.context.parseBounds('[1,2,null,4]'));assert.throws(()=>h.context.parseBounds('200,2,201,4'));
    assert.deepEqual(Array.from(h.context.parseBounds('88.1,22.1,88.2,22.2')),[88.1,22.1,88.2,22.2]);
});
test('dense masks use at most ten rendered edit handles without dropping geometry vertices',()=>{
    const h=harness(),f=feature();h.state.role='official';
    const ring=Array.from({length:1500},(_,i)=>[88+Math.cos(i*Math.PI/750)*.001,22+Math.sin(i*Math.PI/750)*.001]);ring.push([...ring[0]]);f.geometry.coordinates=[ring];
    let options;const layer={options:{},pm:{setOptions:o=>{options=o;}},setStyle(){},bindPopup(){},on(){}};
    const key=h.context.addEntry(f,layer);assert.equal(options.limitMarkersToCount,10);
    assert.equal(h.state.entries.get(key).feature.geometry.coordinates[0].length,1501);
    assert.deepEqual(h.state.entries.get(key).feature.geometry,f.geometry);
});
test('inspector metadata edits survive selection changes and mark saved records dirty',()=>{
    const h=harness(),events={},f=feature();h.state.user={};h.state.role='official';let content='';
    const layer={options:{},setStyle(){},bindPopup:html=>{content=html;},setPopupContent:html=>{content=html;},on:(event,fn)=>{events[event]=fn;}};
    const key=h.context.addEntry(f,layer,true),e=h.state.entries.get(key);
    h.context.updateEntryField(key,'name','Renamed house');h.context.updateEntryField(key,'notes','Check north wall');
    events.popupopen();assert.equal(e.fields.name,'Renamed house');assert.equal(e.fields.notes,'Check north wall');assert.equal(e.dirty,true);assert.ok(content.includes('Renamed house'));
});

test('draft status intent survives an ambiguous reviewed create and uses the current saved version',async()=>{
    const h=harness(),e=entry();h.state.user={};h.state.role='official';h.state.project={id:'p1'};h.state.entries.set('k',e);
    const requests=[];h.context.mockApi=async(p,o)=>{requests.push({p,o});if(requests.length===1)throw Error('Response lost');const body=JSON.parse(o.body),f=feature();f.properties={...f.properties,...body.properties,version:requests.length===2?1:2};return f;};h.run('api=mockApi');
    await h.context.saveProperty('k','reviewed');await h.context.saveProperty('k','draft');
    assert.equal(e.saved,true);assert.equal(e.dirty,true);assert.equal(e.requestedStatus,'draft');
    h.context.updateEntryField('k','notes','Updated after recovering the response');await h.context.saveProperty('k');
    const last=JSON.parse(requests[2].o.body);assert.equal(requests[2].o.method,'PUT');assert.equal(last.properties.version,1);assert.equal(last.properties.status,'draft');assert.equal(e.dirty,false);
});

test('a stale source refresh cannot erase the source created by a completed scan',async()=>{
    const h=harness(),pending=deferred();h.state.user={};h.state.role='official';h.state.project={id:'p1'};h.element('inputCalibration').value='.5';
    const source={id:'new-source',name:'Real scan source',width:512,height:512,preview_url:'/api/sources/new-source/preview'};
    h.context.mockApi=(p)=>p.endsWith('/sources')?pending.promise:p.endsWith('/proposals/reconcile')?Promise.resolve({features:[]}):Promise.resolve({source,geojson:{features:[]},inference:{tiles_processed:1}});h.run('api=mockApi;showSource=()=>{}');
    const refresh=h.context.loadSources();await h.context.executeAI();pending.resolve({sources:[]});await refresh;
    assert.equal(h.state.sources.length,1);assert.equal(h.state.sources[0].id,'new-source');
});

test('switching to map imagery clears the imported overlay preview reference',()=>{
    const h=harness();h.state.source={id:'old'};h.state.previewSource={id:'old'};h.state.sourceOverlay={};h.run('switchTab=()=>{}');
    h.context.useMapImagery();assert.equal(h.state.source,null);assert.equal(h.state.previewSource,null);assert.equal(h.state.sourceOverlay,null);
});

test('browser recovery restores edited geometry and fields only for the authenticated user and project',()=>{
    const shared=storage(),h=harness(shared),e=entry();h.state.user={id:'user-a',password:'must-not-store'};h.state.project={id:'p1'};h.state.role='official';h.state.entries.set('draft',e);
    const edited=structuredClone(e.feature.geometry);edited.coordinates[0][1][0]=1.002;e.layer.toGeoJSON=()=>({geometry:edited});e.fields={name:'Current edited house',notes:'Check wall'};
    assert.equal(h.context.persistRecovery(),true);const raw=shared.getItem(h.context.recoveryKey());assert.ok(!raw.includes('must-not-store'));
    const other=harness(shared);other.state.user={id:'user-b'};other.state.project={id:'p1'};other.state.role='official';assert.equal(other.context.restoreRecovery(),0);assert.equal(other.state.entries.size,0);
    other.state.user={id:'user-a'};other.state.project={id:'p2'};assert.equal(other.context.restoreRecovery(),0);
    const reloaded=harness(shared);reloaded.state.user={id:'user-a'};reloaded.state.project={id:'p1'};reloaded.state.role='official';assert.equal(reloaded.context.restoreRecovery(),1);
    const restored=[...reloaded.state.entries.values()][0];assert.equal(restored.fields.name,'Current edited house');assert.equal(restored.layer.toGeoJSON().geometry.coordinates[0][1][0],1.002);assert.equal(reloaded.context.preferredRecoveryProject(),'p1');
});

test('a 401 stores drafts before clearing private UI and restores them after same-user authentication',async()=>{
    const h=harness(),e=entry();h.state.user={id:'user-a'};h.state.project={id:'p1'};h.state.role='official';h.state.entries.set('draft',e);e.fields={name:'Preserved after expiry'};
    h.context.fetch=async()=>({ok:false,status:401,json:async()=>({detail:'Session expired'})});await assert.rejects(h.context.api('/api/projects/p1/features'),/Session expired/);
    assert.equal(h.state.user,null);assert.equal(h.state.entries.size,0);assert.match(h.context.lastToast,/Sign in to the same account/);
    h.state.user={id:'user-a'};h.state.project={id:'p1'};h.state.role='official';assert.equal(h.context.restoreRecovery(),1);assert.equal([...h.state.entries.values()][0].fields.name,'Preserved after expiry');
});

test('a pending create retains its original payload and idempotency key across browser reload',async()=>{
    const shared=storage(),h=harness(shared),e=entry(),requests=[];h.state.user={id:'a'};h.state.project={id:'p1'};h.state.role='official';h.state.entries.set('draft',e);
    h.context.mockApi=async(p,o)=>{requests.push(o);throw Error('Response lost');};h.run('api=mockApi');await h.context.saveProperty('draft','draft');h.context.updateEntryField('draft','name','Newer unsaved name');h.context.persistRecovery();
    const next=harness(shared);next.state.user={id:'a'};next.state.project={id:'p1'};next.state.role='official';assert.equal(next.context.restoreRecovery(),1);
    const [key,restored]=[...next.state.entries][0];next.context.mockApi=async(p,o)=>{requests.push(o);return feature();};next.run('api=mockApi');await next.context.saveProperty(key);
    assert.equal(requests[1].body,requests[0].body);assert.equal(requests[1].headers['Idempotency-Key'],requests[0].headers['Idempotency-Key']);assert.equal(restored.fields.name,'Newer unsaved name');assert.equal(restored.dirty,true);assert.equal(restored.requestedStatus,'draft');
});

test('storage failures are reported and retain same-page recovery without allowing a risky project switch',async()=>{
    const broken={get length(){return 0;},getItem(){throw Error('Blocked');},setItem(){throw Error('Quota exceeded');},removeItem(){throw Error('Blocked');}},h=harness(broken);
    h.state.user={id:'a'};h.state.project={id:'p1'};h.state.projects=[{id:'p1'},{id:'p2'}];h.state.role='official';h.state.entries.set('draft',entry());assert.equal(h.context.persistRecovery(),false);assert.match(h.context.lastToast,/could not be saved/);
    await h.context.switchProject('p2');assert.equal(h.state.project.id,'p1');assert.equal(h.state.entries.size,1);assert.match(h.context.lastToast,/switch paused/);
    h.context.clearWorkspace();h.state.user={id:'a'};h.state.project={id:'p1'};h.state.role='official';assert.equal(h.context.restoreRecovery(),1);
});

test('oversize recovery never replaces a previously stored valid snapshot',()=>{
    const h=harness(),e=entry();h.state.user={id:'a'};h.state.project={id:'p1'};h.state.entries.set('draft',e);assert.equal(h.context.persistRecovery(),true);const key=h.context.recoveryKey(),before=h.storage.getItem(key);
    e.fields.notes='x'.repeat(2*1024*1024);assert.equal(h.context.persistRecovery(),false);assert.equal(h.storage.getItem(key),before);assert.match(h.context.lastToast,/could not be saved/);
});

test('a 401 preserves the latest live edits when they exceed the recovery byte limit',async()=>{
    const h=harness(),e=entry();h.state.user={id:'a'};h.state.project={id:'p1'};h.state.role='official';h.state.entries.set('draft',e);
    assert.equal(h.context.persistRecovery(),true);const key=h.context.recoveryKey(),previous=h.storage.getItem(key);
    e.fields.notes='x'.repeat(2*1024*1024);e.fields.name='Latest oversized work';
    h.context.fetch=async()=>({ok:false,status:401,json:async()=>({detail:'Session expired'})});await assert.rejects(h.context.api('/api/projects/p1/features'),/Session expired/);
    assert.equal(h.state.entries.size,0);assert.equal(h.state.user,null);assert.equal(h.storage.getItem(key),previous);
    h.state.user={id:'a'};h.state.project={id:'p1'};h.state.role='official';assert.equal(h.context.preferredRecoveryProject(),'p1');
    assert.equal(h.context.restoreRecovery(),1);assert.equal(h.state.entries.get('draft'),e);assert.equal(e.fields.name,'Latest oversized work');assert.equal(e.fields.notes.length,2*1024*1024);
    assert.match(h.context.lastToast,/browser recovery is unavailable/);
});

test('a 401 retains every live boundary when the recovery entry limit is exceeded',async()=>{
    const h=harness();h.state.user={id:'a'};h.state.project={id:'p1'};h.state.role='official';
    for(let i=0;i<1001;i++)h.state.entries.set('draft-'+i,entry(feature('Building '+i,'f'+i)));
    h.context.fetch=async()=>({ok:false,status:401,json:async()=>({detail:'Session expired'})});await assert.rejects(h.context.api('/api/projects/p1/features'),/Session expired/);
    h.state.user={id:'a'};h.state.project={id:'p1'};h.state.role='official';assert.equal(h.context.restoreRecovery(),1001);assert.equal(h.state.entries.size,1001);
    assert.equal(h.state.entries.get('draft-1000').feature.properties.name,'Building 1000');
});

test('closing the signed-out page still warns when oversized drafts exist only in memory',async()=>{
    const h=harness(),e=entry();h.state.user={id:'a'};h.state.project={id:'p1'};h.state.role='official';e.fields.notes='x'.repeat(2*1024*1024);h.state.entries.set('draft',e);
    h.context.fetch=async()=>({ok:false,status:401,json:async()=>({detail:'Session expired'})});await assert.rejects(h.context.api('/api/drone/status'),/Session expired/);
    let prevented=false;const event={preventDefault(){prevented=true;}};h.context.protectDraftsBeforeUnload(event);assert.equal(prevented,true);assert.equal(event.returnValue,'');assert.equal(h.state.user,null);
    h.context.discardRecovery('a','p1');prevented=false;h.context.protectDraftsBeforeUnload({preventDefault(){prevented=true;}});assert.equal(prevented,false);
});

test('a suspended live workspace is restored only to its original user and project',async()=>{
    const h=harness(),e=entry();h.state.user={id:'a'};h.state.project={id:'p1'};h.state.role='official';e.fields.notes='x'.repeat(2*1024*1024);h.state.entries.set('private-draft',e);
    h.context.fetch=async()=>({ok:false,status:401,json:async()=>({detail:'Session expired'})});await assert.rejects(h.context.api('/api/projects/p1/features'),/Session expired/);
    h.state.user={id:'b'};h.state.project={id:'p1'};assert.equal(h.context.restoreRecovery(),0);assert.equal(h.state.entries.size,0);assert.notEqual(h.context.preferredRecoveryProject(),'p1');
    h.state.user={id:'a'};h.state.project={id:'p2'};assert.equal(h.context.restoreRecovery(),0);assert.equal(h.state.entries.size,0);
    h.state.project={id:'p1'};assert.equal(h.context.restoreRecovery(),1);assert.equal(h.state.entries.get('private-draft'),e);
});

test('a delayed 401 from an old session cannot clear a newly authenticated workspace',async()=>{
    const h=harness(),pending=deferred();h.state.user={id:'a'};h.state.project={id:'p1'};h.state.role='official';h.context.fetch=()=>pending.promise;
    const request=h.context.api('/api/drone/status');h.context.clearWorkspace({preserveDrafts:false});h.state.user={id:'b'};h.state.project={id:'p2'};h.state.role='official';const newer=entry();h.state.entries.set('newer-draft',newer);
    pending.resolve({ok:false,status:401,json:async()=>({detail:'Old session expired'})});await assert.rejects(request,/Old session expired/);
    assert.equal(h.state.user.id,'b');assert.equal(h.state.project.id,'p2');assert.equal(h.state.entries.get('newer-draft'),newer);
});

test('project switching preserves each project draft and restores it when returning',async()=>{
    const h=harness();h.state.user={id:'a'};h.state.role='official';h.state.project={id:'p1'};h.state.projects=[{id:'p1',name:'First'},{id:'p2',name:'Second'}];h.state.entries.set('first-draft',entry());
    h.context.mockApi=async p=>p.endsWith('/sources')?{sources:[]}:{features:[]};h.run('api=mockApi');await h.context.switchProject('p2');assert.equal(h.state.project.id,'p2');assert.equal(h.state.entries.size,0);
    h.state.entries.set('second-draft',entry(feature('Second draft')));await h.context.switchProject('p1');assert.equal(h.state.project.id,'p1');assert.equal(h.state.entries.size,1);assert.equal([...h.state.entries.values()][0].feature.properties.name,'House');
});

test('explicit successful logout discards current recovery without erasing another account recovery',async()=>{
    const h=harness();h.state.user={id:'b'};h.state.project={id:'p1'};h.state.entries.set('other',entry());h.context.persistRecovery();const otherKey=h.context.recoveryKey();
    h.state.user={id:'a'};h.state.entries.clear();h.state.entries.set('draft',entry());h.context.persistRecovery();const key=h.context.recoveryKey();h.context.mockApi=async()=>({});h.run('api=mockApi');await h.context.logout();
    assert.equal(h.storage.getItem(key),null);assert.ok(h.storage.getItem(otherKey));assert.equal(h.state.entries.size,0);assert.equal(h.state.user,null);
});

test('offline detection permits imported imagery, forwards its selected AOI and blocks map fetches',async()=>{
    const h=harness();h.state.user={id:'a'};h.state.project={id:'p1'};h.state.role='official';h.context.navigator.onLine=false;h.element('inputCalibration').value='.5';h.context.setBusy(false);assert.equal(h.element('processBtn').disabled,true);
    let requests=[];h.context.mockApi=async(p,o)=>{requests.push({p,body:JSON.parse(o.body)});return p.endsWith('/proposals/reconcile')?{features:[]}:{source:{id:'s1'},geojson:{features:[]}};};h.run('api=mockApi;showSource=()=>{}');await h.context.executeAI();assert.equal(requests.length,0);
    h.state.source={id:'s1'};h.state.scanSelection={getBounds:()=>({getWest:()=>88.1,getSouth:()=>22.1,getEast:()=>88.2,getNorth:()=>22.2})};h.context.setBusy(false);assert.equal(h.element('processBtn').disabled,false);
    await h.context.executeAI();assert.equal(requests.length,2);assert.equal(requests[0].p,'/api/sources/s1/scan');assert.deepEqual(requests[0].body.bounds,[88.1,22.1,88.2,22.2]);
});

test('scan reconciliation receives current edited geometry and only inserts surviving proposals',async()=>{
    const h=harness(),e=entry(),first=feature('Duplicate'),second=feature('New building','new');h.state.user={id:'a'};h.state.project={id:'p1'};h.state.role='official';h.state.entries.set('draft',e);h.element('inputCalibration').value='.5';
    const edited=structuredClone(e.feature.geometry);edited.coordinates[0][1][0]=1.002;e.layer.toGeoJSON=()=>({geometry:edited});let reconcile,added=[];h.context.capture=f=>added.push(f);
    h.context.mockApi=async(p,o)=>{if(p.endsWith('/proposals/reconcile')){reconcile=JSON.parse(o.body);return {features:[second],summary:{duplicates:1}};}return {source:{id:'s1'},geojson:{features:[first,second]}};};h.run('api=mockApi;addEntry=capture;showSource=()=>{}');await h.context.executeAI();
    assert.deepEqual(reconcile.existing[0].geometry,edited);assert.equal(reconcile.features.length,2);assert.equal(added.length,1);assert.equal(added[0].id,'new');assert.match(h.context.lastToast,/Skipped 1 overlapping duplicates/);
});

test('scan reconciliation includes dirty saved building type changes as record overrides',async()=>{
    const h=harness(),e=entry();e.saved=true;e.dirty=false;h.state.user={id:'a'};h.state.project={id:'p1'};h.state.role='official';h.state.entries.set('saved-building',e);h.element('inputCalibration').value='.5';
    h.context.updateEntryField('saved-building','feature_type','parcel');let reconciliation;
    h.context.mockApi=async(p,o)=>{if(p.endsWith('/proposals/reconcile')){reconciliation=JSON.parse(o.body);return {features:[]};}return {source:{id:'s1'},geojson:{features:[]}};};h.run('api=mockApi;showSource=()=>{}');await h.context.executeAI();
    assert.equal(reconciliation.existing.length,1);assert.equal(reconciliation.existing[0].id,e.feature.id);assert.equal(reconciliation.existing[0].properties.feature_type,'parcel');
    assert.equal(e.feature.properties.feature_type,'building');
});

test('a reconciliation response after authentication changes cannot insert proposals',async()=>{
    const h=harness(),pending=deferred();h.state.user={id:'a'};h.state.project={id:'p1'};h.state.role='official';h.element('inputCalibration').value='.5';let added=0;
    h.context.capture=()=>added++;h.context.mockApi=async p=>p.endsWith('/proposals/reconcile')?pending.promise:{source:{id:'s1'},geojson:{features:[feature()]}};h.run('api=mockApi;addEntry=capture;showSource=()=>{}');const scan=h.context.executeAI();await new Promise(resolve=>setImmediate(resolve));h.context.clearWorkspace();h.state.user={id:'b'};h.state.project={id:'p1'};
    pending.resolve({features:[feature()]});await scan;assert.equal(added,0);assert.equal(h.state.entries.size,0);
});

test('recovered saved edits retain their base version to prevent overwriting a newer server record',()=>{
    const shared=storage(),h=harness(shared),e=entry();e.saved=true;e.dirty=true;h.state.user={id:'a'};h.state.project={id:'p1'};h.state.entries.set('old',e);h.context.persistRecovery();
    const next=harness(shared);next.state.user={id:'a'};next.state.project={id:'p1'};next.state.role='official';const latest=feature();latest.properties.version=2;next.context.addEntry(latest,null,true);
    assert.equal(next.context.restoreRecovery(),1);assert.equal(next.state.entries.size,1);assert.equal([...next.state.entries.values()][0].feature.properties.version,1);assert.match(next.context.lastToast,/newer versions/);
});
