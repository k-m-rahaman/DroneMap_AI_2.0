'use strict';

// The original page, classes, colours, video and Leaflet popup styling are retained.
// This controller uses real imagery and the authenticated survey API.
const state = {
    user: null, project: null, role: 'viewer', epoch: 0, loadSequence: 0,
    busy: false, records: [], entries: new Map(), source: null, selectedFile: null,
    sourceOverlay: null, target: null, scanSelection: null, counter: 0, publicReport: null,
    projects: [], sources: [], selectedKey: null, tab: 'survey', model: null, previewSource: null,
    sourceLoadSequence: 0, droneSequence: 0, drone: null, droneTimer: null, droneMarker: null,
    mission: null, missionLayer: null, projectDialogMode: 'create',
    recoveryTimer: null, recoveryPaused: false, recoveryWarning: false,
    publicDemo: false, deploymentChecked: false, demo: null,
};
let currentLang = 'en';
const $ = id => document.getElementById(id);
const escapeHTML = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const i18n = {
    en: {
        landing_head: 'A cadastral engine<br>makes it precise.', landing_sub: 'AI building mapping and secure survey records.',
        landing_btn: 'Access Portal', landing_scroll: 'Scroll to explore', feat1_t: 'AI Ortho-Slicing',
        feat1_d: 'Detect multiple building footprints from map imagery or georeferenced drone orthomosaics using overlapping image tiles.',
        feat2_t: 'Geodesic Map Math', feat2_d: 'Draw and edit boundaries on the map. Area and perimeter are calculated from the saved geographic coordinates.',
        feat3_t: 'Survey Record Registry', feat3_d: 'Save reviewed survey evidence and download PDF reports with version-specific QR verification links.',
        auth_title: 'Select Access', auth_viewer_t: 'Record Viewer', auth_viewer_d: 'Sign in to view your private survey records. Shared QR report links open directly.',
        auth_officer_t: 'Surveyor Workspace', auth_officer_d: 'Sign in with email and password. The first sign-in creates this local workspace.', auth_login: 'Sign in',
        tab_survey: 'Survey Tools', tab_reg: 'Survey Registry', sec1_t: '1. Target Area', sec2_t: '2. Vision Engine', calib: 'AI Confidence',
        upload: 'GeoTIFF / Image (Optional)', exec: 'Execute AI', inst: 'Execute scans the visible map. Optionally upload a GeoTIFF, or a north-up image with known bounds. Rectangle selects an area; Polygon draws a parcel. Each AI building needs review.',
        vis_out: 'Vision Output', total_p: 'Saved Survey Records', reg_desc: 'Survey evidence, not ownership titles. Search an ID/name for its PDF. Enter GEOJSON or CSV to export all saved boundaries.',
        none: 'No saved records yet.', dl: 'Download Survey PDF', search_title: 'Find / Export Records', search_place: 'ID / name / GEOJSON / CSV', search_btn: 'Find',
    },
    hi: {
        landing_head: 'कैडस्ट्रल इंजन<br>बेहतर सर्वेक्षण के लिए।', landing_sub: 'एआई भवन मानचित्रण और सुरक्षित सर्वेक्षण रिकॉर्ड।', landing_btn: 'पोर्टल खोलें', landing_scroll: 'नीचे स्क्रॉल करें',
        feat1_t: 'एआई ऑर्थो-स्लाइसिंग', feat1_d: 'मानचित्र या भू-संदर्भित ड्रोन ऑर्थोमोज़ेक से कई भवनों की सीमाएं पहचानें।',
        feat2_t: 'जियोडेसिक माप', feat2_d: 'सीमाएं बनाएं और संपादित करें। क्षेत्रफल और परिमाप सहेजे गए भौगोलिक निर्देशांकों से मापे जाते हैं।',
        feat3_t: 'सर्वेक्षण रिकॉर्ड', feat3_d: 'सर्वेक्षण सहेजें और संस्करण-विशिष्ट क्यूआर सत्यापन लिंक वाला पीडीएफ डाउनलोड करें।',
        auth_title: 'प्रवेश चुनें', auth_viewer_t: 'रिकॉर्ड दर्शक', auth_viewer_d: 'निजी रिकॉर्ड के लिए साइन इन करें। साझा क्यूआर रिपोर्ट लिंक सीधे खुलते हैं।',
        auth_officer_t: 'सर्वेक्षक कार्यक्षेत्र', auth_officer_d: 'ईमेल और पासवर्ड से साइन इन करें। पहला प्रवेश स्थानीय खाता बनाता है।', auth_login: 'साइन इन',
        tab_survey: 'सर्वेक्षण उपकरण', tab_reg: 'सर्वेक्षण रजिस्ट्री', sec1_t: '1. लक्षित क्षेत्र', sec2_t: '2. विजन इंजन', calib: 'एआई कॉन्फिडेंस',
        upload: 'GeoTIFF / चित्र (वैकल्पिक)', exec: 'एआई चलाएं', inst: 'एआई दृश्य मानचित्र को स्कैन करता है। GeoTIFF या ज्ञात सीमाओं वाला उत्तर-दिशा चित्र अपलोड कर सकते हैं। Rectangle से क्षेत्र, Polygon से भूखंड बनाएं। हर एआई भवन की समीक्षा करें।',
        vis_out: 'विजन आउटपुट', total_p: 'सहेजे गए रिकॉर्ड', reg_desc: 'ये सर्वेक्षण प्रमाण हैं, स्वामित्व पत्र नहीं। पीडीएफ के लिए आईडी/नाम खोजें। सभी सीमाएं निर्यात करने के लिए GEOJSON या CSV लिखें।',
        none: 'अभी कोई रिकॉर्ड नहीं।', dl: 'सर्वेक्षण पीडीएफ', search_title: 'खोज / निर्यात', search_place: 'आईडी / नाम / GEOJSON / CSV', search_btn: 'खोजें',
    },
    bn: {
        landing_head: 'ক্যাডাস্ট্রাল ইঞ্জিন<br>আরও ভালো জরিপের জন্য।', landing_sub: 'এআই ভবন ম্যাপিং এবং সুরক্ষিত জরিপের রেকর্ড।', landing_btn: 'পোর্টাল খুলুন', landing_scroll: 'নিচে স্ক্রল করুন',
        feat1_t: 'এআই অর্থো-স্লাইসিং', feat1_d: 'ম্যাপ বা ভূ-তথ্যসহ ড্রোন অর্থোমোজাইক থেকে একাধিক ভবনের সীমানা শনাক্ত করুন।',
        feat2_t: 'জিওডেসিক পরিমাপ', feat2_d: 'সীমানা আঁকুন এবং সম্পাদনা করুন। সংরক্ষিত ভৌগোলিক স্থানাঙ্ক থেকে ক্ষেত্রফল ও পরিসীমা মাপা হয়।',
        feat3_t: 'জরিপের রেকর্ড', feat3_d: 'জরিপ সংরক্ষণ করুন এবং সংস্করণভিত্তিক কিউআর যাচাই লিঙ্কসহ পিডিএফ ডাউনলোড করুন।',
        auth_title: 'অ্যাক্সেস বাছুন', auth_viewer_t: 'রেকর্ড ভিউয়ার', auth_viewer_d: 'ব্যক্তিগত রেকর্ড দেখতে সাইন ইন করুন। শেয়ার করা কিউআর রিপোর্ট লিঙ্ক সরাসরি খোলে।',
        auth_officer_t: 'সমীক্ষকের কর্মক্ষেত্র', auth_officer_d: 'ইমেল ও পাসওয়ার্ড দিয়ে প্রবেশ করুন। প্রথমবার স্থানীয় অ্যাকাউন্ট তৈরি হয়।', auth_login: 'সাইন ইন',
        tab_survey: 'জরিপ সরঞ্জাম', tab_reg: 'জরিপ রেজিস্ট্রি', sec1_t: '১. নির্বাচিত এলাকা', sec2_t: '২. ভিশন ইঞ্জিন', calib: 'এআই কনফিডেন্স',
        upload: 'GeoTIFF / ছবি (ঐচ্ছিক)', exec: 'এআই চালান', inst: 'এআই দৃশ্যমান ম্যাপ স্ক্যান করে। GeoTIFF বা জানা সীমানাসহ উত্তরমুখী ছবি আপলোড করা যায়। Rectangle দিয়ে এলাকা, Polygon দিয়ে জমি আঁকুন। প্রতিটি এআই ভবন পর্যালোচনা করুন।',
        vis_out: 'ভিশন আউটপুট', total_p: 'সংরক্ষিত রেকর্ড', reg_desc: 'এগুলি জরিপের প্রমাণ, মালিকানার দলিল নয়। পিডিএফের জন্য আইডি/নাম খুঁজুন। সমস্ত সীমানা নিতে GEOJSON বা CSV লিখুন।',
        none: 'কোনো রেকর্ড নেই।', dl: 'জরিপ পিডিএফ', search_title: 'খুঁজুন / এক্সপোর্ট', search_place: 'আইডি / নাম / GEOJSON / CSV', search_btn: 'খুঁজুন',
    },
};


function changeLanguage(lang) {
    currentLang=Object.hasOwn(i18n,lang)?lang:'en';const t=i18n[currentLang];
    for(const id of ['landingLangSelect','dashLangSelect'])$(id).value=currentLang;
    $('landing-headline').innerHTML=t.landing_head;
    const bindings={'landing-sub':'landing_sub','landing-btn':'landing_btn','landing-scroll-hint':'landing_scroll','feat1-t':'feat1_t','feat1-d':'feat1_d','feat2-t':'feat2_t','feat2-d':'feat2_d','feat3-t':'feat3_t','feat3-d':'feat3_d','auth-title':'auth_title','auth-viewer-t':'auth_viewer_t','auth-viewer-d':'auth_viewer_d','auth-officer-t':'auth_officer_t','auth-officer-d':'auth_officer_d','auth-login-btn':'auth_login'};
    for(const [id,key]of Object.entries(bindings))$(id).textContent=t[key];
    document.documentElement.lang=currentLang;
    renderDemoNotice();
    if(!state.busy)$('btnText').textContent='Detect buildings';
    renderRegistry();
}
function showToast(message,type='success') {
    const toast=document.createElement('div');toast.className='workspace-toast toast-enter '+(type==='success'?'toast-success':'toast-error');
    toast.setAttribute('role',type==='success'?'status':'alert');toast.textContent=String(message);$('toast-container').appendChild(toast);
    setTimeout(()=>{toast.style.opacity='0';setTimeout(()=>toast.remove(),300);},6500);
}
// Recovery contains survey geometry and field edits only. Authentication secrets
// never enter browser storage, and a snapshot is restored only after that user signs in.
const RECOVERY_PREFIX='dronemap.recovery.v1:',RECOVERY_MAX=3*1024*1024,RECOVERY_AGE=7*24*60*60*1000;
const volatileRecovery=new Map(),volatileProjects=new Map();
// One suspended live workspace avoids dropping edits when even a bounded JSON
// snapshot is too large. This reuses existing layer data, and is never persisted.
let suspendedRecovery=null;
function recoveryKey(userId=state.user?.id,projectId=state.project?.id){return userId&&projectId?RECOVERY_PREFIX+encodeURIComponent(userId)+':'+encodeURIComponent(projectId):null;}
function recoveryStorage(){return window.localStorage;}
function warnRecovery(){if(!state.recoveryWarning){state.recoveryWarning=true;showToast('Local draft recovery could not be saved. Keep this page open and save your boundaries to the workspace before closing.','error');}}
function validRecoveryGeometry(g){
    if(!g||!['Polygon','MultiPolygon'].includes(g.type)||!Array.isArray(g.coordinates))return false;
    const polygons=g.type==='Polygon'?[g.coordinates]:g.coordinates;
    return polygons.length>0&&polygons.every(p=>Array.isArray(p)&&p.length>0&&p.every(r=>Array.isArray(r)&&r.length>=4&&r.every(c=>Array.isArray(c)&&c.length>=2&&Number.isFinite(c[0])&&Number.isFinite(c[1])&&Math.abs(c[0])<=180&&Math.abs(c[1])<=90)));
}
function validRecoveryFields(fields){return Boolean(fields)&&typeof fields==='object'&&!Array.isArray(fields)&&['name','owner','notes','feature_type','status'].every(k=>fields[k]===undefined||typeof fields[k]==='string');}
function recoveryItems(){
    const items=[];
    for(const [key,e]of state.entries){
        if(e.saved&&!e.dirty&&!e.pendingCreateBody)continue;
        const geometry=geometryOf(e);if(!validRecoveryGeometry(geometry))throw Error('Invalid recovery geometry');
        items.push({key,feature:e.feature,geometry,saved:Boolean(e.saved),fields:e.fields,revision:e.revision,
            measurement:e.measurement&&Number.isFinite(e.measurement.area_sqm)?{area_sqm:e.measurement.area_sqm,perimeter_m:e.measurement.perimeter_m}:null,
            pendingCreateBody:e.pendingCreateBody||null,idempotencyKey:e.idempotencyKey,requestedStatus:e.requestedStatus||null});
    }
    if(items.length>1000)throw Error('Too many recovery entries');return items;
}
function discardRecovery(userId=state.user?.id,projectId=state.project?.id){
    const key=recoveryKey(userId,projectId);if(!key)return;
    volatileRecovery.delete(key);
    if(suspendedRecovery?.userId===userId&&suspendedRecovery.projectId===projectId)suspendedRecovery=null;
    try{recoveryStorage()?.removeItem(key);}catch{warnRecovery();}
}
function persistRecovery(){
    if(state.recoveryTimer)clearTimeout(state.recoveryTimer);state.recoveryTimer=null;
    if(state.recoveryPaused||state.publicReport)return false;const key=recoveryKey();if(!key)return false;
    try{
        const items=recoveryItems(),aoi=state.scanSelection?.toGeoJSON?.(false)?.geometry||null;
        const snapshot=JSON.stringify({schema:1,userId:state.user.id,projectId:state.project.id,updatedAt:Date.now(),items,
            sourceId:state.source?.id||null,aoi:validRecoveryGeometry(aoi)?aoi:null});
        if(snapshot.length*2>RECOVERY_MAX)throw Error('Recovery storage limit');
        if(items.length||aoi)volatileRecovery.set(key,snapshot);else volatileRecovery.delete(key);
        volatileProjects.set(state.user.id,state.project.id);
        const storage=recoveryStorage();if(!storage)throw Error('Storage unavailable');
        // Keep older projects until expiry; never silently evict another recoverable draft.
        let total=snapshot.length*2;
        for(let i=storage.length-1;i>=0;i--){const other=storage.key(i);if(!other?.startsWith(RECOVERY_PREFIX)||other===key)continue;
            const raw=storage.getItem(other);let parsed;try{parsed=JSON.parse(raw);}catch{}
            if(!parsed?.updatedAt||Date.now()-parsed.updatedAt>RECOVERY_AGE)storage.removeItem(other);else total+=(raw?.length||0)*2;
        }
        if(total>RECOVERY_MAX)throw Error('Recovery storage limit');
        if(items.length||aoi)storage.setItem(key,snapshot);else storage.removeItem(key);
        storage.setItem(RECOVERY_PREFIX+'last:'+encodeURIComponent(state.user.id),JSON.stringify({projectId:state.project.id,updatedAt:Date.now()}));
        state.recoveryWarning=false;return true;
    }catch{warnRecovery();return false;}
}
function scheduleRecovery(){if(state.recoveryPaused||!recoveryKey()||state.publicReport)return;if(state.recoveryTimer)clearTimeout(state.recoveryTimer);state.recoveryTimer=setTimeout(persistRecovery,350);}
function protectDraftsBeforeUnload(event){persistRecovery();if(suspendedRecovery?.entries.length||(!state.publicReport&&hasUnsaved())){event.preventDefault();event.returnValue='';}}
function suspendDrafts(){
    if(!recoveryKey())return;
    suspendedRecovery={userId:state.user.id,projectId:state.project.id,
        entries:[...state.entries].filter(([,e])=>!e.saved||e.dirty||e.pendingCreateBody),sourceId:state.source?.id,scanSelection:state.scanSelection};
}
function preferredRecoveryProject(){if(suspendedRecovery?.userId===state.user.id)return suspendedRecovery.projectId;if(volatileProjects.has(state.user.id))return volatileProjects.get(state.user.id);try{const raw=recoveryStorage()?.getItem(RECOVERY_PREFIX+'last:'+encodeURIComponent(state.user.id)),value=JSON.parse(raw||'null');return Date.now()-value?.updatedAt<=RECOVERY_AGE?value.projectId:null;}catch{return null;}}
function restoreSuspendedDrafts(){
    const recovered=suspendedRecovery;
    if(!recovered||recovered.userId!==state.user.id||recovered.projectId!==state.project.id)return null;
    let conflicts=0;
    for(const [key,e]of recovered.entries){
        for(const [currentKey,currentEntry]of state.entries)if(e.saved&&currentEntry.saved&&currentEntry.feature.id===e.feature.id){if(currentEntry.feature.properties.version!==e.feature.properties.version)conflicts++;window.map.removeLayer(currentEntry.layer);state.entries.delete(currentKey);}
        e.saving=false;e.layer.addTo?.(window.map);e.layer.pm?.disable();state.entries.set(key,e);styleEntry(e);e.layer.setPopupContent(popupContent(key));
    }
    const source=state.sources.find(s=>s.id===recovered.sourceId);if(source){state.source=source;$('scanSourceLabel').textContent=source.name;showSource(source);}
    if(recovered.scanSelection){if(state.scanSelection)window.map.removeLayer(state.scanSelection);state.scanSelection=recovered.scanSelection;state.scanSelection.addTo?.(window.map);updateScanArea();}
    suspendedRecovery=null;renderRegistry();renderDetectionList();renderSources();
    if(recovered.entries.length)showToast('Recovered '+recovered.entries.length+' unsaved boundaries on this page. Save them before closing; browser recovery is unavailable.'+(conflicts?' Newer saved versions need conflict review.':''),'error');
    return recovered.entries.length;
}
function restoreRecovery(){
    const key=recoveryKey();if(!key||state.publicReport)return 0;let snapshot;
    const suspended=restoreSuspendedDrafts();if(suspended!==null)return suspended;
    try{const raw=volatileRecovery.get(key)||recoveryStorage()?.getItem(key);if(!raw)return 0;if(raw.length*2>RECOVERY_MAX)throw Error('Oversize recovery');snapshot=JSON.parse(raw);
        if(snapshot?.schema!==1||snapshot.userId!==state.user.id||snapshot.projectId!==state.project.id||!Array.isArray(snapshot.items)||snapshot.items.length>1000)throw Error('Invalid recovery');
        if(!Number.isFinite(snapshot.updatedAt)||Date.now()-snapshot.updatedAt>RECOVERY_AGE){discardRecovery();return 0;}
        // Validate every item before modifying the visible map.
        for(const item of snapshot.items){if(!validRecoveryGeometry(item.geometry)||item.feature?.type!=='Feature'||!validRecoveryGeometry(item.feature.geometry)||!validRecoveryFields(item.feature.properties)||(item.fields!==null&&item.fields!==undefined&&!validRecoveryFields(item.fields))||typeof item.idempotencyKey!=='string'||item.idempotencyKey.length>200)throw Error('Invalid recovery item');
            if(item.pendingCreateBody){const body=JSON.parse(item.pendingCreateBody);if(typeof item.pendingCreateBody!=='string'||!validRecoveryGeometry(body.geometry)||!validRecoveryFields(body.properties))throw Error('Invalid pending save');}}
    }catch{showToast('A local recovery copy could not be read. It has been retained; save or export current work before clearing browser storage.','error');return 0;}
    let count=0,conflicts=0;state.recoveryPaused=true;
    try{
        for(const item of snapshot.items){
            const existing=[...state.entries].find(([,e])=>item.saved&&e.saved&&e.feature.id===item.feature.id);
            if(existing){if(existing[1].feature.properties.version!==item.feature.properties.version)conflicts++;window.map.removeLayer(existing[1].layer);state.entries.delete(existing[0]);}
            const display={...item.feature,geometry:item.geometry},entry=state.entries.get(addEntry(display,null,Boolean(item.saved)));
            // Retain the original server version for optimistic conflict detection.
            entry.feature=structuredClone(item.feature);entry.fields=item.fields;entry.revision=Number.isInteger(item.revision)?item.revision:0;
            entry.measurement=Number.isFinite(item.measurement?.area_sqm)?item.measurement:JSON.stringify(item.geometry)!==JSON.stringify(item.feature.geometry)?{area_sqm:NaN,perimeter_m:NaN}:null;
            entry.pendingCreateBody=item.pendingCreateBody||null;entry.idempotencyKey=item.idempotencyKey;entry.requestedStatus=['draft','reviewed'].includes(item.requestedStatus)?item.requestedStatus:null;entry.dirty=true;styleEntry(entry);count++;
        }
        const source=state.sources.find(s=>s.id===snapshot.sourceId);if(source){state.source=source;$('scanSourceLabel').textContent=source.name;showSource(source);}
        if(validRecoveryGeometry(snapshot.aoi)){if(state.scanSelection)window.map.removeLayer(state.scanSelection);state.scanSelection=L.geoJSON({type:'Feature',geometry:snapshot.aoi,properties:{}},{style:{color:'#e8d47a',weight:2,fillOpacity:.04,dashArray:'6 4'},pmIgnore:true}).getLayers()[0];state.scanSelection?.addTo(window.map);updateScanArea();}
        renderRegistry();renderDetectionList();renderSources();
    }finally{state.recoveryPaused=false;}
    if(count)showToast('Recovered '+count+' unsaved boundaries from this browser.'+(conflicts?' Some saved records have newer versions; review conflicts before saving.':''));
    return count;
}
async function api(path,options={}) {
    const requestEpoch=state.epoch,requestUserId=state.user?.id;
    const headers={...options.headers};
    if(options.body&&!(options.body instanceof FormData))headers['Content-Type']='application/json';
    const response=await fetch(path,{...options,headers,credentials:'same-origin'});
    const data=await response.json().catch(()=>({}));
    if(!response.ok){
        const detail=Array.isArray(data.detail)?data.detail.map(x=>x.msg).join('; '):data.detail;
        if(response.status===401&&state.user&&state.epoch===requestEpoch&&state.user.id===requestUserId){const recovered=persistRecovery();if(!recovered)suspendDrafts();clearWorkspace({preserveDrafts:false});navigateTo(state.publicDemo?'landing-view':'auth-view');showToast(state.publicDemo?'Your temporary demo session expired. Open a new demo workspace. Expired workspaces cannot be recovered; download sample exports before expiry.':recovered?'Your session expired. Sign in to the same account to recover unsaved boundaries.':'Your session expired. Sign in to the same account on this page. Keep it open because browser storage was unavailable.','error');}
        throw new Error(detail||'Request failed ('+response.status+').');
    }
    return data;
}
function navigateTo(id) {
    document.querySelectorAll('.view-section').forEach(el=>el.classList.remove('view-active'));$(id).classList.add('view-active');
    if(id==='dashboard-view'&&window.map)setTimeout(()=>window.map.invalidateSize(),100);
}
function renderDemoNotice(){
    const active=state.publicDemo;
    document.body?.classList.toggle('public-demo',active);
    $('demoNotice').classList.toggle('hidden',!active);
    $('uploadLimits').textContent=active?'TIFF, PNG, JPEG · Up to 8 MB / 16 million pixels · 4 sources per project':'Drop a file here · TIFF, PNG, JPEG · Up to 64 MB';
    $('scanLimits').textContent=active?'Demo: maximum 500 × 500 m map area, 16 native imagery windows, and 15 seconds between scans. Review every AI roof outline; it does not establish a land boundary.':'AI suggestions require boundary review. Roof outlines do not establish ownership boundaries.';
    $('feat3-d').textContent=active?'Review sample survey evidence and download PDF reports in your temporary workspace. Demo reports have no public verification link.':i18n[currentLang].feat3_d;
    $('inspectorReportBtn').textContent=active?'↓ Survey PDF':'↓ Survey PDF with QR';
    if(active){
        const hours=Math.max(1,Math.round((state.demo?.expires_in_seconds||7200)/3600));
        $('landing-btn').textContent='Open SIH demo';$('auth-login-btn').textContent='Open SIH demo';
        $('landing-sub').textContent='SIH26012 · GeoVision · Try building mapping in your own temporary workspace.';
        $('auth-officer-d').textContent='No account or password needed. Your temporary demo workspace is isolated from other visitors.';
        $('demoNoticeText').textContent='Temporary workspace · up to '+hours+' hours · Use sample data only. Real drone telemetry and capture require a local installation.';
    }else{const t=i18n[currentLang];$('landing-btn').textContent=t.landing_btn;$('auth-login-btn').textContent=t.auth_login;$('landing-sub').textContent=t.landing_sub;$('auth-officer-d').textContent=t.auth_officer_d;}
}
function applyDeploymentStatus(status){
    const wasDemo=state.publicDemo;
    state.deploymentChecked=true;state.publicDemo=Boolean(status.public_demo);state.demo=status.demo||null;renderDemoNotice();
    // Status arrives after the map is created. Start guests close enough to scan
    // the visible area within the hosted demo's 500 metre limit.
    if(state.publicDemo&&!wasDemo&&!state.user)window.map?.setZoom?.(defaultMapZoom());
}
function defaultMapZoom(){return state.publicDemo?19:17;}
async function openPortal(){
    if(state.busy)return;
    if(!state.deploymentChecked){try{applyDeploymentStatus(await api('/api/status'));}catch(error){showToast(error.message,'error');return;}}
    if(state.publicDemo)return login('official');navigateTo('auth-view');
}
function switchTab(name) {
    if(!['survey','drone','sources','registry'].includes(name))return;
    if(state.publicReport&&!['registry','survey'].includes(name))return showToast('Sign in to use private project tools.','error');
    state.tab=name;document.querySelectorAll('.workspace-tab,.rail-item').forEach(el=>el.classList.remove('active'));
    $('tab-'+name).classList.add('active');$(name+'-content').classList.add('active');
    $('panelTitle').textContent={survey:'Mapping & detection',drone:'Drone operations',sources:'Imagery & sources',registry:'Survey registry'}[name];
    if(window.matchMedia?.('(max-width:760px)').matches)$('dashboard-view').classList.remove('panel-collapsed');
    if(name==='sources'&&state.user&&!state.busy)loadSources();
    if(name==='drone'&&state.user)refreshDroneStatus();
    if(name==='registry')renderRegistry();
}
function togglePanel(){$('dashboard-view').classList.toggle('panel-collapsed');setTimeout(()=>window.map?.invalidateSize(),220);}
function collapseMobilePanel(){if(window.matchMedia?.('(max-width:760px)').matches)$('dashboard-view').classList.add('panel-collapsed');}
function canEdit(){return state.role==='official'&&Boolean(state.user)&&!state.publicReport;}
function current(epoch,projectId){return state.epoch===epoch&&state.project?.id===projectId;}
function hasUnsaved(){return [...state.entries.values()].some(e=>e.dirty||!e.saved);}
function setScanStatus(message,error=false){$('scanStatus').textContent=message;$('scanStatus').classList.toggle('danger-text',error);}
function reportScanError(message){setScanStatus(message,true);showToast(message,'error');}
function setBusy(value,label) {
    if(value)setScanStatus('');
    state.busy=value;const offline=!navigator.onLine;
    $('processBtn').disabled=value||(!state.source&&offline)||!canEdit();$('auth-login-btn').disabled=value;$('landing-btn').disabled=value;$('droneInput').disabled=value;
    $('projectSelect').disabled=value||Boolean(state.publicReport);$('btnText').textContent=value?(label||'Working…'):'Detect buildings';
    $('processingLabel').textContent=label||'Processing imagery';$('processingOverlay').classList.toggle('hidden',!value||!state.user);
    $('importSourceBtn').disabled=value||!state.selectedFile;
    $('droneConnectBtn').disabled=value||state.publicDemo||state.drone?.available===false||Boolean(state.drone?.connected)||state.drone?.state==='connecting';
    $('droneDisconnectBtn').disabled=value||!state.drone?.connection;$('missionPreviewBtn').disabled=value||!canEdit();
    for(const id of ['saveDraftBtn','saveReviewedBtn','deleteFeatureBtn'])$(id).disabled=value;
    document.querySelectorAll('#droneConnectForm input,#droneConnectForm select,#droneConnectForm button').forEach(el=>el.disabled=value||state.publicDemo||state.drone?.available===false);
    $('droneConnectBtn').disabled=value||state.publicDemo||state.drone?.available===false||Boolean(state.drone?.connected)||state.drone?.state==='connecting';
    $('droneDisconnectBtn').disabled=value||state.publicDemo||state.drone?.available===false||!state.drone?.connection;
    document.querySelectorAll('#missionAltitude,#missionSpeed,#missionFrontOverlap,#missionSideOverlap,#missionSensorWidth,#missionSensorHeight,#missionFocalLength,#missionAngle,#missionImageWidth,#missionImageHeight').forEach(el=>el.disabled=value);
    $('connectionDot').className=offline?'offline':'online';$('connectionLabel').textContent=offline?'Internet unavailable · local tools available':state.publicDemo?'Connected to temporary demo workspace':'Connected to local workspace';
    renderSources();
}
function clearMission(){if(state.missionLayer)window.map.removeLayer(state.missionLayer);state.missionLayer=null;state.mission=null;$('missionResult').classList.add('hidden');}
function clearUpload(event){event?.stopPropagation();state.selectedFile=null;$('droneInput').value='';$('uploadLabel').textContent='Choose GeoTIFF or image';$('importBounds').classList.add('hidden');$('importSourceBtn').disabled=true;}
function clearLayers() {
    setScanStatus('');
    $('qualityResults')?.replaceChildren();
    state.sourceLoadSequence++;
    for(const entry of state.entries.values())window.map?.removeLayer(entry.layer);
    state.entries.clear();state.records=[];state.sources=[];state.source=null;state.previewSource=null;state.selectedKey=null;
    for(const name of ['sourceOverlay','target','scanSelection','missionLayer']){if(state[name])window.map?.removeLayer(state[name]);state[name]=null;}
    state.mission=null;$('missionResult').classList.add('hidden');$('featureInspector').classList.add('hidden');$('previewBox').classList.add('hidden');
    $('resultImagePreview').removeAttribute('src');$('imageMetadata').classList.add('hidden');$('scanSourceLabel').textContent='Satellite map imagery';
    clearUpload();updateScanArea();renderSources();renderRegistry();renderDetectionList();
}
function clearWorkspace({preserveDrafts=true}={}) {
    if(preserveDrafts)persistRecovery();if(state.recoveryTimer)clearTimeout(state.recoveryTimer);state.recoveryTimer=null;
    state.epoch++;state.loadSequence++;state.sourceLoadSequence++;state.droneSequence++;
    if(state.droneTimer)clearTimeout(state.droneTimer);state.droneTimer=null;state.drone=null;
    if(state.droneMarker)window.map?.removeLayer(state.droneMarker);state.droneMarker=null;
    state.user=null;state.project=null;state.projects=[];state.publicReport=null;state.role='viewer';
    clearLayers();window.map?.pm.removeControls();setBusy(false);renderProjectOptions();
}
function configureAccess() {
    const official=canEdit();$('officialControls').classList.toggle('hidden',!official);$('publicControls').classList.toggle('hidden',official);
    $('roleIndicator').textContent=state.publicReport?'Report v'+state.publicReport.report.feature_version:(official?'Surveyor':'Viewer')+' · '+(state.user?.name||'');
    window.map.pm.removeControls();document.querySelectorAll('#tab-drone,#tab-sources,.project-switcher button').forEach(el=>el.disabled=!official);
    $('projectSelect').disabled=Boolean(state.publicReport);
}
function renderModel(model) {
    $('modelStatus').classList.toggle('ready',Boolean(model?.available));$('modelStatus').classList.toggle('error',!model?.available);
    $('modelStatus').lastElementChild.textContent=model?.available?'Building engine ready':'AI unavailable';
    $('modelStatus').title=model?.message||'Building segmentation. Review every boundary.';
}
async function login(role) {
    if(state.busy)return;setBusy(true,'Opening workspace…');const epoch=state.epoch;
    try{
        const status=await api('/api/status');applyDeploymentStatus(status);let user=status.user;
        if(state.publicDemo){role='official';if(!user)user=(await api('/api/demo/start',{method:'POST'})).user;}
        if(!user&&role==='viewer'){showToast('Private survey records require sign-in. Shared report QR links open directly.','error');navigateTo('auth-view');return;}
        if(!user){
            const email=$('officerNameInput').value.trim(),password=$('pinInput').value;
            if(!email||!password)throw new Error('Enter your email and password.');const body={email,password};
            if(status.setup_required){
                if(password.length<12)throw new Error('Choose a password with at least 12 characters.');
                const name=window.prompt('Set up your local survey workspace. Your name:',email.split('@')[0]);if(name===null)return;
                body.name=name.trim();if(!body.name)throw new Error('Enter a display name.');
            }
            user=(await api(status.setup_required?'/api/setup':'/api/login',{method:'POST',body:JSON.stringify(body)})).user;
        }
        if(state.epoch!==epoch)return;
        state.user=user;state.role=role;state.model=status.model;state.projects=(await api('/api/projects')).projects;
        if(state.epoch!==epoch)return;
        if(!state.projects.length){const p=await api('/api/projects',{method:'POST',body:JSON.stringify({name:'DroneMap Survey',description:'Building footprints and reviewed survey evidence.'})});if(state.epoch!==epoch)return;state.projects=[p];}
        state.project=state.projects.find(p=>p.id===preferredRecoveryProject())||state.projects[0];$('pinInput').value='';renderProjectOptions();configureAccess();renderModel(status.model);
        if(!(await loadVerifiedRecords()))return;
        if(state.epoch!==epoch)return;await loadSources();if(state.epoch!==epoch)return;
        navigateTo('dashboard-view');switchTab('survey');showToast('Workspace ready: '+user.name);restoreRecovery();startDronePolling();
        if(!status.model?.available)showToast(status.model?.message||'AI unavailable. Manual mapping remains available.','error');
    }catch(error){if(state.epoch===epoch)showToast(error.message,'error');}
    finally{if(state.epoch===epoch)setBusy(false);}
}
async function logout() {
    if(state.busy)return showToast('Wait for the current operation to finish before signing out.','error');
    if(hasUnsaved()&&!window.confirm('Discard unsaved map changes and sign out?'))return;
    const userId=state.user?.id,projectId=state.project?.id;
    try{await api('/api/logout',{method:'POST'});discardRecovery(userId,projectId);clearWorkspace({preserveDrafts:false});navigateTo('landing-view');}catch(error){showToast(error.message,'error');}
}
function renderProjectOptions() {
    $('projectSelect').replaceChildren();const projects=state.publicReport?[state.publicReport.project]:state.projects;
    for(const p of projects){const option=document.createElement('option');option.value=p.id;option.textContent=p.name;option.selected=p.id===state.project?.id;$('projectSelect').appendChild(option);}
    if(!projects.length){const option=document.createElement('option');option.textContent='No project';$('projectSelect').appendChild(option);}
    $('mapProjectName').textContent=state.project?.name||state.publicReport?.project?.name||'DroneMap Survey';
}
async function switchProject(id) {
    if(!id||id===state.project?.id)return;if(state.busy){$('projectSelect').value=state.project?.id||'';return;}
    const next=state.projects.find(p=>p.id===id);if(!next)return;
    if(hasUnsaved()&&!persistRecovery()){$('projectSelect').value=state.project.id;return showToast('Project switch paused because local recovery is unavailable. Save your boundaries or explicitly discard them first.','error');}
    const epoch=state.epoch,sequence=++state.loadSequence;setBusy(true,'Opening project…');
    try{
        const [records,sources]=await Promise.all([api('/api/projects/'+id+'/features'),api('/api/projects/'+id+'/sources')]);
        if(state.epoch!==epoch||sequence!==state.loadSequence)return;
        clearLayers();state.project=next;state.records=records.features;state.sources=sources.sources;
        for(const f of state.records)addEntry(f,null,true);
        renderProjectOptions();renderRegistry();renderDetectionList();renderSources();restoreRecovery();persistRecovery();
        if(state.records.length)fitAllBoundaries();showToast('Opened '+next.name);
    }catch(error){if(state.epoch===epoch&&sequence===state.loadSequence){$('projectSelect').value=state.project?.id||'';showToast(error.message,'error');}}
    finally{if(state.epoch===epoch&&sequence===state.loadSequence)setBusy(false);}
}
function openProjectDialog(edit=false) {
    if(state.busy||!canEdit())return;state.projectDialogMode=edit?'edit':'create';
    $('projectDialogTitle').textContent=edit?'Project settings':'Create survey project';
    $('newProjectName').value=edit?state.project.name:'';$('newProjectLocation').value=edit?state.project.location||'':'';$('newProjectDescription').value=edit?state.project.description||'':'';
    $('createProjectBtn').textContent=edit?'Save project settings':'Create project';$('projectDialog').classList.remove('hidden');$('newProjectName').focus();
}
function closeProjectDialog(){if(!state.busy)$('projectDialog').classList.add('hidden');}
async function createProject(event) {
    event.preventDefault();if(state.busy||!canEdit())return;
    const body={name:$('newProjectName').value.trim(),location:$('newProjectLocation').value.trim(),description:$('newProjectDescription').value.trim()};
    if(!body.name)return showToast('Enter a project name.','error');const edit=state.projectDialogMode==='edit';
    if(!edit&&hasUnsaved()&&!persistRecovery())return showToast('Save your boundaries before creating another project; browser recovery is unavailable.','error');
    const epoch=state.epoch,oldId=state.project?.id;setBusy(true,edit?'Saving project…':'Creating project…');$('createProjectBtn').disabled=true;
    try{
        const result=await api(edit?'/api/projects/'+oldId:'/api/projects',{method:edit?'PUT':'POST',body:JSON.stringify(body)});
        if(state.epoch!==epoch)return;
        if(edit){state.project=result;state.projects=state.projects.map(p=>p.id===result.id?result:p);}else{clearLayers();state.projects.unshift(result);state.project=result;}
        renderProjectOptions();$('projectDialog').classList.add('hidden');showToast(edit?'Project settings saved.':'Project created. Select an area or import imagery to begin.');
    }catch(error){if(state.epoch===epoch)showToast(error.message,'error');}
    finally{if(state.epoch===epoch){$('createProjectBtn').disabled=false;setBusy(false);}}
}
function updateTargetCoords(lat,lon) {
    $('inputLat').value=Number(lat).toFixed(6);$('inputLon').value=Number(lon).toFixed(6);
    if(state.target)window.map.removeLayer(state.target);
    state.target=L.circleMarker([lat,lon],{radius:5,color:'#37d6a1',fillColor:'#37d6a1',fillOpacity:.8,pmIgnore:true}).addTo(window.map);
}
function syncMapCenter(){const p=window.map.getCenter();updateTargetCoords(p.lat,p.lng);showToast('Coordinates synchronized with the map.');}
function flyToCoordinates() {
    const lat=Number($('inputLat').value),lon=Number($('inputLon').value);
    if(!$('inputLat').value||!$('inputLon').value||!Number.isFinite(lat)||!Number.isFinite(lon)||Math.abs(lat)>85||Math.abs(lon)>180)return showToast('Enter valid map coordinates.','error');
    window.map.flyTo([lat,lon],19,{animate:true,duration:1});updateTargetCoords(lat,lon);collapseMobilePanel();
}
function locateSurveyor(){window.map.once('locationfound',e=>updateTargetCoords(e.latlng.lat,e.latlng.lng));window.map.once('locationerror',()=>showToast('Location access is unavailable.','error'));window.map.locate({setView:true,maxZoom:19});collapseMobilePanel();}
function startManualDraw(){if(!canEdit()||state.busy)return showToast('Sign in as a surveyor and wait for processing to finish.','error');closeInspector();window.map.pm.disableDraw();window.map.pm.enableDraw('Polygon',{snappable:true,allowSelfIntersection:false});collapseMobilePanel();showToast('Click parcel corners; click the first point to finish.');}
function startScanArea(){if(!canEdit()||state.busy)return showToast('Sign in as a surveyor and wait for processing to finish.','error');closeInspector();window.map.pm.disableDraw();window.map.pm.enableDraw('Rectangle',{snappable:false});collapseMobilePanel();showToast('Click two opposite corners to select the survey area.');}
function clearScanArea(){if(state.busy)return;if(state.scanSelection)window.map.removeLayer(state.scanSelection);state.scanSelection=null;clearMission();updateScanArea();scheduleRecovery();}
function updateScanArea(){$('clearAreaBtn').classList.toggle('hidden',!state.scanSelection);if(!state.scanSelection){$('aoiSummary').textContent='Visible map is the scan area';return;}const b=state.scanSelection.getBounds();$('aoiSummary').textContent='Selected area · '+b.getWest().toFixed(5)+', '+b.getSouth().toFixed(5)+' → '+b.getEast().toFixed(5)+', '+b.getNorth().toFixed(5);}
function visibleBounds(){const b=window.map.getBounds();return[b.getWest(),b.getSouth(),b.getEast(),b.getNorth()];}
function parseBounds(value){let b;try{b=JSON.parse(value);}catch{b=String(value).split(',').map(x=>x.trim()===''?NaN:Number(x.trim()));}if(!Array.isArray(b)||b.length!==4||b.some(x=>typeof x!=='number'||!Number.isFinite(x))||b[0]>=b[2]||b[1]>=b[3]||Math.abs(b[0])>180||Math.abs(b[2])>180||Math.abs(b[1])>85.051128||Math.abs(b[3])>85.051128)throw new Error('Use valid WGS84 bounds: west,south,east,north.');return b;}
function confidence(){const v=Number($('inputCalibration').value);if(!$('inputCalibration').value||!Number.isFinite(v)||v<.05||v>.95)throw new Error('AI confidence must be from 0.05 to 0.95.');return v;}
function minimumArea(){const v=Number($('minAreaInput').value);if($('minAreaInput').value===''||!Number.isFinite(v)||v<0||v>10000)throw new Error('Minimum building area must be from 0 to 10,000 m².');return v;}

function entryKey(){return 'draft-'+Date.now().toString(36)+'-'+(++state.counter);}
function geometryOf(entry){return entry.layer.toGeoJSON(false).geometry;}
function dimensions(entry){const n=entry.measurement?.area_sqm??entry.feature.properties.area_sqm;return Number.isFinite(Number(n))?{sqm:Number(n).toFixed(2),sqft:(Number(n)*10.7639104167).toFixed(2),acres:(Number(n)/4046.8564224).toFixed(4)}:{sqm:'…',sqft:'…',acres:'…'};}
function entryFields(entry){return {...entry.feature.properties,...entry.fields};}
function styleEntry(entry){const reviewed=entry.saved&&entry.feature.properties.status==='reviewed'&&!entry.dirty;entry.layer.setStyle({color:reviewed?'#46dfab':entry.saved?'#e5ba77':'#76cce8',weight:state.entries.get(state.selectedKey)===entry?3:2,fillOpacity:.12,dashArray:reviewed?null:'5 3'});}
function popupContent(key){const e=state.entries.get(key);if(!e)return '';const p=entryFields(e),d=dimensions(e);return '<div class="map-popup"><span class="eyebrow">'+(p.feature_type==='building'?'BUILDING FOOTPRINT':'SURVEY BOUNDARY')+'</span><h3>'+escapeHTML(p.name)+'</h3><p>'+d.sqm+' m² · '+escapeHTML(e.saved?'Saved v'+e.feature.properties.version:'Unsaved suggestion')+'</p><button class="button primary" onclick="selectEntry(\''+key+'\',false)">Open boundary details →</button></div>';}
function updateEntryField(key,field,value){const entry=state.entries.get(key);if(!entry||!canEdit()||!['name','owner','notes','feature_type'].includes(field))return;const fields=entryFields(entry);if(fields[field]===value)return;entry.fields={...entry.fields,[field]:value};entry.dirty=true;entry.revision++;styleEntry(entry);renderDetectionList();renderRegistry();scheduleRecovery();if(state.selectedKey===key)$('inspectorStatus').textContent='Unsaved survey changes';}
function rememberFields(key){const e=state.entries.get(key);if(!e)return;if(state.selectedKey===key)for(const [id,field]of [['editorName','name'],['editorOwner','owner'],['editorNotes','notes'],['editorType','feature_type']])updateEntryField(key,field,$(id).value);}
function addEntry(feature,layer=null,saved=false){
    const key=entryKey();
    if(!layer){layer=L.geoJSON(feature).getLayers()[0];if(!layer)throw new Error('Boundary geometry could not be displayed.');layer.addTo(window.map);}
    const entry={feature:structuredClone(feature),layer,saved,dirty:!saved,revision:0,measureSequence:0,measurement:null,saving:false,fields:null,pendingCreateBody:null,idempotencyKey:globalThis.crypto?.randomUUID?.()||key};
    state.entries.set(key,entry);styleEntry(entry);layer.pm?.setOptions({limitMarkersToCount:10});if(!canEdit())layer.options.pmIgnore=true;
    layer.bindPopup(popupContent(key),{className:'custom-popup'});layer.on('popupopen',()=>layer.setPopupContent(popupContent(key)));
    layer.on('click',()=>selectEntry(key,false));layer.on('pm:edit',()=>geometryChanged(key));layer.on('pm:update',()=>geometryChanged(key));
    if(!saved)scheduleRecovery();return key;
}
async function geometryChanged(key){
    const entry=state.entries.get(key);if(!entry||!canEdit())return;
    entry.dirty=true;entry.revision++;const seq=++entry.measureSequence,epoch=state.epoch;styleEntry(entry);scheduleRecovery();
    if(state.selectedKey===key)$('inspectorStatus').textContent='Measuring edited boundary…';renderDetectionList();renderRegistry();
    try{
        const measured=await api('/api/geometry/measure',{method:'POST',body:JSON.stringify({geometry:geometryOf(entry)})});
        if(state.epoch!==epoch||state.entries.get(key)!==entry||seq!==entry.measureSequence)return;
        entry.measurement=measured;scheduleRecovery();if(state.selectedKey===key)renderInspector(false);renderDetectionList();
    }catch(error){if(state.epoch===epoch&&state.entries.get(key)===entry&&seq===entry.measureSequence){entry.measurement={area_sqm:NaN};if(state.selectedKey===key)renderInspector(false);showToast(error.message,'error');}}
}
function selectEntry(key,fit=true){
    const entry=state.entries.get(key);if(!entry)return;
    if(state.selectedKey&&state.selectedKey!==key)state.entries.get(state.selectedKey)?.layer.pm?.disable();
    state.selectedKey=key;for(const e of state.entries.values())styleEntry(e);
    window.map.closePopup();renderInspector();renderDetectionList();
    if(fit){const narrow=window.map.getSize().x<500;window.map.fitBounds(entry.layer.getBounds(),{paddingTopLeft:[30,150],paddingBottomRight:narrow?[30,200]:[310,60],maxZoom:20});}
    collapseMobilePanel();
}
function renderInspector(resetFields=true){
    const entry=state.entries.get(state.selectedKey);if(!entry)return closeInspector();
    const p=entryFields(entry),d=dimensions(entry);$('featureInspector').classList.remove('hidden');$('inspectorTitle').textContent=p.name||'Unnamed boundary';
    const perimeter=entry.measurement?.perimeter_m??entry.feature.properties.perimeter_m;
    $('inspectorMetrics').innerHTML='<div>'+d.sqm+'<small>AREA · m²</small></div><div>'+(Number.isFinite(Number(perimeter))?Number(perimeter).toFixed(2):'…')+'<small>PERIMETER · m</small></div>';
    const flags=entry.feature.properties.quality_flags||[];
    $('inspectorQuality').textContent=(p.feature_type==='building'?'Building footprint':'Survey feature')+(Number.isFinite(p.confidence)?' · '+(p.confidence*100).toFixed(0)+'% model confidence':'')+(flags.length?' · Edge clipping flagged; inspect this boundary carefully.':'');
    $('inspectorFields').classList.toggle('hidden',!canEdit());
    if(resetFields){$('editorName').value=p.name||'';$('editorOwner').value=p.owner||'';$('editorNotes').value=p.notes||'';$('editorType').value=p.feature_type||'building';$('featureHistory').open=false;$('historyList').replaceChildren();}
    $('inspectorReportBtn').classList.toggle('hidden',!entry.saved||Boolean(state.publicReport));$('featureHistory').classList.toggle('hidden',!entry.saved||Boolean(state.publicReport));
    $('inspectorStatus').textContent=state.publicReport?state.publicReport.report.notice:entry.dirty?'Unsaved changes'+(entry.requestedStatus?' · Pending '+entry.requestedStatus+' status':'')+' · Review the geometry and source before saving.':'Saved v'+entry.feature.properties.version+' · '+entry.feature.properties.status+' · '+(entry.feature.properties.updated_by||'');
    for(const id of ['saveDraftBtn','saveReviewedBtn','deleteFeatureBtn'])$(id).disabled=state.busy;
}
function closeInspector(){state.entries.get(state.selectedKey)?.layer.pm?.disable();state.selectedKey=null;$('featureInspector').classList.add('hidden');for(const e of state.entries.values())styleEntry(e);renderDetectionList();}
function editSelectedField(field,value){if(state.selectedKey)updateEntryField(state.selectedKey,field,value);}
function editSelectedBoundary(){const e=state.entries.get(state.selectedKey);if(!e||!canEdit()||state.busy)return;window.map.closePopup();e.layer.pm?.enable({allowSelfIntersection:false,limitMarkersToCount:10});showToast('Drag boundary vertices. Changes are measured automatically; save to record them.');}
async function saveSelected(status){if(state.selectedKey)return saveProperty(state.selectedKey,status);}
async function saveProperty(key,status){
    const entry=state.entries.get(key);if(!entry||!canEdit()||!state.project)return;
    if(state.busy||entry.saving)return showToast('Wait for the current operation to finish.','error');
    rememberFields(key);const fields=entryFields(entry),name=(fields.name||'').trim();if(!name)return showToast('Enter a survey name.','error');
    status=status??entry.requestedStatus??'reviewed';if(!['draft','reviewed'].includes(status))return;entry.requestedStatus=status;
    const geometry=geometryOf(entry),revision=entry.revision,props={...fields,name,owner:(fields.owner||'').trim(),notes:fields.notes||'',status};
    const latestBody=JSON.stringify({geometry,properties:props});if(!entry.saved&&!entry.pendingCreateBody)entry.pendingCreateBody=latestBody;
    const epoch=state.epoch,projectId=state.project.id;state.loadSequence++;entry.saving=true;persistRecovery();setBusy(true,'Saving survey…');
    try{
        const path=entry.saved?'/api/features/'+encodeURIComponent(entry.feature.id):'/api/projects/'+projectId+'/features';
        const feature=await api(path,{method:entry.saved?'PUT':'POST',headers:entry.saved?{}:{'Idempotency-Key':entry.idempotencyKey},body:entry.saved?latestBody:entry.pendingCreateBody});
        if(!current(epoch,projectId)||state.entries.get(key)!==entry)return;
        const newest=entryFields(entry),fieldChanges=['name','owner','notes','feature_type'].some(k=>String(newest[k]||'').trim()!==String(feature.properties[k]||'').trim());
        const retriedOld=entry.pendingCreateBody&&JSON.stringify(JSON.parse(entry.pendingCreateBody).geometry)!==JSON.stringify(geometry);
        const statusChanged=feature.properties.status!==status;
        entry.feature=feature;entry.saved=true;entry.pendingCreateBody=null;entry.dirty=revision!==entry.revision||fieldChanges||Boolean(retriedOld)||statusChanged;
        if(!entry.dirty){entry.fields=null;entry.requestedStatus=null;entry.measurement=feature.properties;entry.layer.pm?.disable();}
        const pos=state.records.findIndex(f=>f.id===feature.id);if(pos<0)state.records.push(feature);else state.records[pos]=feature;
        styleEntry(entry);entry.layer.setPopupContent(popupContent(key));renderRegistry();renderDetectionList();if(state.selectedKey===key)renderInspector();
        showToast(statusChanged?'Original save recovered; '+status+' status is still pending. Save '+status+' again to record it.':entry.dirty?'Survey saved; newer edits remain unsaved.':status==='reviewed'?'Reviewed survey saved with measured geometry.':'Draft saved. Review it before marking it reviewed.');
    }catch(error){if(current(epoch,projectId))showToast(error.message,'error');}
    finally{entry.saving=false;if(current(epoch,projectId)&&state.entries.get(key)===entry){persistRecovery();setBusy(false);}}
}
async function deleteSelected(){
    const key=state.selectedKey,e=state.entries.get(key);if(!e||!canEdit()||state.busy)return;
    if(!window.confirm(e.saved?'Delete this saved survey record? Existing shared PDF snapshots remain verifiable.':'Discard this unsaved boundary?'))return;
    if(!e.saved){window.map.removeLayer(e.layer);state.entries.delete(key);closeInspector();renderDetectionList();renderRegistry();persistRecovery();return;}
    const epoch=state.epoch,projectId=state.project.id;setBusy(true,'Deleting survey…');state.loadSequence++;
    try{await api('/api/features/'+encodeURIComponent(e.feature.id)+'?version='+e.feature.properties.version,{method:'DELETE'});if(!current(epoch,projectId))return;window.map.removeLayer(e.layer);state.entries.delete(key);state.records=state.records.filter(f=>f.id!==e.feature.id);closeInspector();renderRegistry();renderDetectionList();persistRecovery();showToast('Survey record deleted.');}
    catch(error){if(current(epoch,projectId))showToast(error.message,'error');}finally{if(current(epoch,projectId))setBusy(false);}
}
function clearDrafts(){if(!canEdit()||state.busy)return;const drafts=[...state.entries].filter(([,e])=>!e.saved);if(!drafts.length)return showToast('There are no unsaved boundaries to clear.');if(!window.confirm('Discard '+drafts.length+' unsaved boundaries? Saved records and their local edits are retained.'))return;for(const[key,e]of drafts){window.map.removeLayer(e.layer);state.entries.delete(key);}if(!state.entries.has(state.selectedKey))closeInspector();renderDetectionList();renderRegistry();persistRecovery();showToast('Unsaved boundaries cleared.');}
function fitAllBoundaries(){const entries=[...state.entries.values()];if(!entries.length)return showToast('Draw a boundary or run a scan first.','error');const bounds=L.latLngBounds(entries[0].layer.getBounds());for(const e of entries)bounds.extend(e.layer.getBounds());closeInspector();window.map.fitBounds(bounds,{padding:[35,70],maxZoom:20});collapseMobilePanel();}
async function loadVerifiedRecords(){
    if(!state.user||!state.project||state.publicReport)return false;
    const epoch=state.epoch,projectId=state.project.id,seq=++state.loadSequence;
    try{const data=await api('/api/projects/'+projectId+'/features');if(!current(epoch,projectId)||seq!==state.loadSequence)return false;state.records=data.features;
        for(const[key,e]of state.entries)if(e.saved&&!e.dirty&&!e.saving){window.map.removeLayer(e.layer);state.entries.delete(key);}
        for(const f of data.features)if(![...state.entries.values()].some(e=>e.saved&&e.feature.id===f.id))addEntry(f,null,true);
        renderRegistry();renderDetectionList();return true;
    }catch(error){if(current(epoch,projectId)&&seq===state.loadSequence)showToast(error.message,'error');return false;}
}
function emptyState(container,text,icon='◫'){const el=document.createElement('div');el.className='empty-state';const mark=document.createElement('span');mark.textContent=icon;el.appendChild(mark);el.appendChild(document.createTextNode(text));container.appendChild(el);}
function boundaryStatus(e){return !e.saved?'unsaved':e.dirty?'edited':e.feature.properties.status==='reviewed'?'reviewed':'draft';}
function renderDetectionList(){
    const entries=[...state.entries],query=($('detectionSearch').value||'').trim().toLocaleLowerCase(),filter=$('detectionFilter').value||'all';$('reviewCount').textContent=entries.length;
    const container=$('detectionList');container.replaceChildren();
    const filtered=entries.filter(([,e])=>{const p=entryFields(e),status=boundaryStatus(e);return(filter==='all'||filter==='unsaved'&&(status==='unsaved'||status==='edited')||filter===status)&&[p.name,p.owner,e.feature.id].some(v=>String(v||'').toLocaleLowerCase().includes(query));});
    if(!filtered.length)return emptyState(container,entries.length?'No boundaries match your filter.':'Your boundaries appear here. Select an area, detect buildings or draw a parcel.');
    for(const[key,e]of filtered.slice(0,250)){
        const p=entryFields(e),d=dimensions(e),status=boundaryStatus(e),item=document.createElement('button');item.type='button';item.className='boundary-item'+(key===state.selectedKey?' selected':'');
        item.innerHTML='<div class="item-heading"><strong>'+escapeHTML(p.name||'Unnamed boundary')+'</strong><span class="pill '+(status==='reviewed'?'emerald':status==='unsaved'||status==='edited'?'warning':'')+'">'+escapeHTML(status)+'</span></div><div class="item-meta"><span>'+(p.feature_type==='building'?'Building footprint':'Parcel / survey')+(Number.isFinite(p.confidence)?' · '+(p.confidence*100).toFixed(0)+'% AI':'')+'</span><span class="measured">'+d.sqm+' m²</span></div>';
        item.addEventListener('click',()=>selectEntry(key));container.appendChild(item);
    }
    if(filtered.length>250){const note=document.createElement('p');note.className='micro';note.textContent='Showing 250 of '+filtered.length+'. Use search or click any outline on the map.';container.appendChild(note);}
}
function renderRegistry(){
    const reviewed=state.records.filter(f=>f.properties.status==='reviewed').length,area=state.records.reduce((sum,f)=>sum+(Number(f.properties.area_sqm)||0),0);
    $('totalRecords').textContent=state.records.length;$('registryReviewed').textContent=reviewed;$('registryArea').textContent=area.toLocaleString(undefined,{maximumFractionDigits:1});
    $('statDrafts').textContent=[...state.entries.values()].filter(e=>boundaryStatus(e)!=='reviewed').length;$('statReviewed').textContent=reviewed;$('statArea').textContent=area.toLocaleString(undefined,{maximumFractionDigits:0});
    const query=($('searchInput').value||'').trim().toLocaleLowerCase(),filter=$('registryFilter').value||'all',container=$('registryList');container.replaceChildren();
    const records=state.records.filter(f=>(filter==='all'||f.properties.status===filter)&&[f.id,f.properties.id,f.properties.name,f.properties.owner].some(v=>String(v||'').toLocaleLowerCase().includes(query)));
    if(!records.length)return emptyState(container,state.records.length?'No saved surveys match this search.':'Save a draft or reviewed boundary to build your project registry.','▤');
    for(const f of records){const p=f.properties,item=document.createElement('div');item.className='registry-item';
        item.innerHTML='<div class="item-heading"><strong>'+escapeHTML(p.name)+'</strong><span class="pill '+(p.status==='reviewed'?'emerald':'')+'">'+escapeHTML(p.status)+'</span></div><div class="item-meta"><span>'+escapeHTML(p.owner||'Owner claim not recorded')+'</span><span class="measured">'+Number(p.area_sqm).toFixed(2)+' m²</span></div><div class="item-identity">'+escapeHTML(f.id)+' · Version '+escapeHTML(p.version||'')+'</div>';
        const actions=document.createElement('div');actions.className='button-pair';const view=document.createElement('button');view.className='button';view.textContent='View boundary';view.addEventListener('click',()=>{const pair=[...state.entries].find(([,e])=>e.feature.id===f.id);if(pair)selectEntry(pair[0]);});actions.appendChild(view);
        if(!state.publicReport){const pdf=document.createElement('button');pdf.className='button';pdf.textContent='↓ Survey PDF';pdf.addEventListener('click',()=>openReport(f.id));actions.appendChild(pdf);}
        else{const r=state.publicReport.report,note=document.createElement('p');note.className='micro';note.textContent='Report v'+r.feature_version+' · '+(r.deleted_since_report?'Original survey deleted':r.is_current?'Current saved version':'Survey changed after this report')+' · '+r.notice;item.appendChild(note);}
        item.appendChild(actions);container.appendChild(item);
    }
}
function openReport(id){window.open('/api/features/'+encodeURIComponent(id)+'/report.pdf','_blank','noopener');}
function downloadSelectedReport(){const e=state.entries.get(state.selectedKey);if(e?.saved&&!state.publicReport){if(e.dirty)showToast('The PDF contains the saved version. Save edits first to include them.','error');openReport(e.feature.id);}}
function exportProject(format){if(!state.user||!state.project)return showToast('Sign in to export private survey records.','error');if(['geojson','csv'].includes(format))window.open('/api/projects/'+state.project.id+'/export.'+format,'_blank','noopener');}
async function loadFeatureHistory(){
    const key=state.selectedKey,e=state.entries.get(key);if(!e?.saved||state.publicReport)return;const epoch=state.epoch;$('historyList').textContent='Loading versions…';
    try{const result=await api('/api/features/'+encodeURIComponent(e.feature.id)+'/history');if(state.epoch!==epoch||state.selectedKey!==key)return;$('historyList').replaceChildren();
        for(const h of result.history){const item=document.createElement('div');item.className='history-item';const when=new Date(h.timestamp);
            item.innerHTML='<strong>v'+escapeHTML(h.version)+' · '+escapeHTML(h.action)+'</strong><br>'+escapeHTML(h.user_name||'Surveyor')+' · '+escapeHTML(Number.isNaN(when.getTime())?h.timestamp:when.toLocaleString());$('historyList').appendChild(item);}
    }catch(error){if(state.epoch===epoch&&state.selectedKey===key)$('historyList').textContent=error.message;}
}
function updateFileInfo(file){if(state.busy||!file)return;const maxMB=state.publicDemo?8:64;if(!/\.(tiff?|png|jpe?g)$/i.test(file.name)||file.size===0||file.size>maxMB*1024*1024)return showToast('Choose a non-empty GeoTIFF, PNG or JPEG up to '+maxMB+' MB.','error');state.selectedFile=file;$('uploadLabel').textContent=file.name;$('importBounds').classList.toggle('hidden',/\.tiff?$/i.test(file.name));$('importSourceBtn').disabled=false;}
async function importSource(event){
    event.preventDefault();if(state.busy||!canEdit()||!state.selectedFile||!state.project)return;
    const epoch=state.epoch,projectId=state.project.id,file=state.selectedFile,form=new FormData();form.append('file',file);
    try{if(!/\.tiff?$/i.test(file.name)){const vals=['boundsWest','boundsSouth','boundsEast','boundsNorth'].map(id=>$(id).value.trim());if(vals.some(x=>!x))throw new Error('Enter all four geographic bounds for this north-up image.');form.append('bounds',JSON.stringify(parseBounds(vals.join(','))));}}catch(error){return showToast(error.message,'error');}
    setBusy(true,'Importing georeferenced imagery…');
    try{const source=await api('/api/projects/'+projectId+'/sources',{method:'POST',body:form});if(!current(epoch,projectId))return;state.sourceLoadSequence++;state.sources.unshift(source);selectSource(source.id);clearUpload();renderSources();showToast('Imagery imported. Review its position, then detect buildings.');}
    catch(error){if(current(epoch,projectId))showToast(error.message,'error');}finally{if(current(epoch,projectId))setBusy(false);}
}
async function loadSources(){if(!state.user||!state.project)return;const epoch=state.epoch,projectId=state.project.id,seq=++state.sourceLoadSequence;try{const data=await api('/api/projects/'+projectId+'/sources');if(!current(epoch,projectId)||seq!==state.sourceLoadSequence)return;state.sources=data.sources;renderSources();}catch(error){if(current(epoch,projectId)&&seq===state.sourceLoadSequence)showToast(error.message,'error');}}
function renderSources(){
    const container=$('sourceList');container.replaceChildren();$('sourceCount').textContent=state.sources.length;
    if(!state.sources.length)return emptyState(container,'No imagery imported yet. GeoTIFF reads its CRS automatically; other images need explicit bounds.','▧');
    for(const source of state.sources){
        const card=document.createElement('div');card.className='source-item'+(state.source?.id===source.id?' selected':'');
        card.innerHTML='<div class="item-heading"><strong>'+escapeHTML(source.name)+'</strong><span class="pill">'+(source.kind==='drone'?'Import':'Map')+'</span></div><div class="item-meta"><span>'+escapeHTML(source.crs||'Georeferenced')+'</span><span>'+escapeHTML(source.width)+' × '+escapeHTML(source.height)+' px</span></div>';
        const actions=document.createElement('div');actions.className='button-pair';
        const select=document.createElement('button');select.className='button';select.textContent=state.source?.id===source.id?'✓ Selected':'Select & preview';select.disabled=state.busy;select.addEventListener('click',()=>{if(!state.busy)selectSource(source.id);});
        const scan=document.createElement('button');scan.className='button primary';scan.textContent='Detect buildings';scan.disabled=state.busy||!canEdit();scan.addEventListener('click',()=>{if(state.busy)return;selectSource(source.id);switchTab('survey');executeAI();});
        actions.appendChild(select);actions.appendChild(scan);card.appendChild(actions);container.appendChild(card);
    }
}
function selectSource(id){const source=state.sources.find(s=>s.id===id);if(!source)return;state.source=source;$('scanSourceLabel').textContent=source.name;showSource(source);renderSources();setBusy(state.busy);scheduleRecovery();const b=source.preview_bounds||source.bounds;if(Array.isArray(b))window.map.fitBounds([[b[1],b[0]],[b[3],b[2]]],{padding:[35,75]});}
function useMapImagery(){if(state.busy)return;state.source=null;state.previewSource=null;$('scanSourceLabel').textContent='Satellite map imagery';if(state.sourceOverlay)window.map.removeLayer(state.sourceOverlay);state.sourceOverlay=null;renderSources();switchTab('survey');setBusy(state.busy);scheduleRecovery();showToast('Detection will use the selected scan area or visible satellite map.');}
function showSource(source){state.previewSource=source;if(state.sourceOverlay)window.map.removeLayer(state.sourceOverlay);state.sourceOverlay=null;const b=source.preview_bounds||source.bounds;if($('sourceOverlayToggle').checked&&Array.isArray(b)&&b.length===4)state.sourceOverlay=L.imageOverlay(source.preview_url,[[b[1],b[0]],[b[3],b[2]]],{opacity:.8,interactive:false,pmIgnore:true}).addTo(window.map);for(const e of state.entries.values())e.layer.bringToFront();}
function toggleSourceOverlay(show){$('sourceOverlayToggle').checked=show;if(state.previewSource)showSource(state.previewSource);}
function openSourcePreview(){if(state.previewSource)window.open(state.previewSource.preview_url,'_blank','noopener');}
async function executeAI(){
    if(state.busy||!canEdit()||!state.project)return;
    const epoch=state.epoch,projectId=state.project.id,source=state.source;let threshold,minArea;
    if(!source&&!navigator.onLine)return reportScanError('Internet is unavailable. Select imported imagery for local detection, or reconnect to scan map imagery.');
    try{threshold=confidence();minArea=minimumArea();}catch(error){return reportScanError(error.message);}
    setBusy(true,source?'Detecting source buildings…':'Fetching map imagery…');
    setScanStatus(source?'Detecting buildings in the selected source…':'Fetching map imagery and detecting buildings…');
    try{
        let result;
        if(source){const selected=state.scanSelection?.getBounds(),bounds=selected?[selected.getWest(),selected.getSouth(),selected.getEast(),selected.getNorth()]:null;result=await api('/api/sources/'+source.id+'/scan',{method:'POST',body:JSON.stringify({confidence:threshold,min_area_sqm:minArea,...(bounds?{bounds}:{})})});}
        else{const selected=state.scanSelection?.getBounds(),bounds=selected?[selected.getWest(),selected.getSouth(),selected.getEast(),selected.getNorth()]:visibleBounds();result=await api('/api/projects/'+projectId+'/scans/map',{method:'POST',body:JSON.stringify({bounds,confidence:threshold,min_area_sqm:minArea})});}
        if(!current(epoch,projectId))return;
        for(const feature of result.geojson.features)feature.properties={...feature.properties,source_id:result.source.id};
        const existing=[...state.entries.values()].filter(e=>e.saved?e.dirty:entryFields(e).feature_type==='building').map(e=>({type:'Feature',...(e.saved?{id:e.feature.id}:{}),geometry:geometryOf(e),properties:entryFields(e)}));
        const reconciled=await api('/api/projects/'+projectId+'/proposals/reconcile',{method:'POST',body:JSON.stringify({features:result.geojson.features,existing})});
        if(!current(epoch,projectId))return;
        const offset=[...state.entries.values()].filter(e=>e.feature.properties.feature_type==='building').length;
        const known=new Set([...state.entries.values()].map(e=>String(e.feature.properties.source_id)+'|'+JSON.stringify(geometryOf(e))));let added=0;
        for(const[i,feature]of reconciled.features.entries()){
            if(!feature.properties.name||feature.properties.name==='Building footprint')feature.properties.name='Building '+String(offset+i+1).padStart(3,'0');
            feature.properties.source_id=result.source.id;
            const signature=result.source.id+'|'+JSON.stringify(feature.geometry);
            if(!known.has(signature)){addEntry(feature);known.add(signature);added++;}
        }
        state.sourceLoadSequence++;if(!state.sources.some(s=>s.id===result.source.id))state.sources.unshift(result.source);
        showSource(result.source);$('previewBox').classList.remove('hidden');$('resultImagePreview').src=result.source.preview_url;$('detectionCount').textContent=result.geojson.features.length+' buildings';
        $('imageMetadata').classList.remove('hidden');$('imageMetadata').textContent=result.source.name+' · '+result.source.width+' × '+result.source.height+' px · '+(result.inference?.tiles_processed||0)+' tiles · '+result.geojson.features.length+' proposals. Review edge clipping, missed buildings and false detections.';
        renderDetectionList();renderRegistry();renderSources();collapseMobilePanel();persistRecovery();
        const duplicates=reconciled.summary?.duplicates??reconciled.duplicates?.length??0;
        const message=added?'Added '+added+' building proposals.'+(duplicates?' Skipped '+duplicates+' overlapping duplicates.':'')+' Select an outline, review its boundary and save.':result.geojson.features.length?'These boundaries are already on your map.':'No buildings passed the threshold. Check the imagery, lower the threshold or draw manually.';
        setScanStatus(message,!added);showToast(message,added?'success':'error');
    }catch(error){if(current(epoch,projectId))reportScanError(error.message);}finally{if(current(epoch,projectId)){setBusy(false);renderSources();}}
}
function updateDroneTransport(){const serial=$('droneTransport').value==='serial';$('droneNetworkFields').classList.toggle('hidden',serial);$('droneSerialFields').classList.toggle('hidden',!serial);$('droneHost').required=!serial;$('dronePort').required=!serial;$('droneDevice').required=serial;if(serial)refreshDronePorts();}
async function refreshDronePorts(){if(!state.user||state.publicDemo||state.drone?.available===false)return;const epoch=state.epoch;try{const result=await api('/api/drone/ports');if(state.epoch!==epoch)return;$('dronePortList').replaceChildren();for(const p of result.ports){const option=document.createElement('option');option.value=p.device;option.textContent=p.description||p.device;$('dronePortList').appendChild(option);}if(!result.ports.length)showToast('No serial telemetry devices found. Connect a radio and refresh.','error');}catch(error){if(state.epoch===epoch)showToast(error.message,'error');}}
function startDronePolling(){if(state.droneTimer)clearTimeout(state.droneTimer);const epoch=state.epoch;async function poll(){if(state.epoch!==epoch||!state.user)return;await refreshDroneStatus(true);if(state.epoch===epoch&&state.user)state.droneTimer=setTimeout(poll,state.drone?.connected?2500:6000);}poll();}
async function refreshDroneStatus(quiet=false){if(!state.user||state.publicReport)return;const epoch=state.epoch,seq=++state.droneSequence;try{const result=await api('/api/drone/status');if(state.epoch!==epoch||seq!==state.droneSequence)return;state.drone=result;renderDroneStatus();}catch(error){if(state.epoch===epoch&&seq===state.droneSequence){$('droneMessage').textContent=error.message;if(!quiet)showToast(error.message,'error');}}}
function validPosition(t){return Number.isFinite(t.latitude)&&Number.isFinite(t.longitude)&&Math.abs(t.latitude)<=90&&Math.abs(t.longitude)<=180;}
function renderDroneStatus(){
    const d=state.drone||{},t=d.telemetry||{},unavailable=state.publicDemo||d.available===false,connected=!unavailable&&Boolean(d.connected);
    $('droneStatusBadge').textContent=unavailable?'Local installation required':connected?'Connected':d.state==='waiting'?'Waiting for heartbeat':d.state==='connecting'?'Connecting':d.state==='stale'?'Link stale':'Disconnected';
    $('droneStatusBadge').className='pill '+(connected?'emerald':d.state==='waiting'||d.state==='stale'?'warning':'');
    $('droneMessage').textContent=d.message||'No vehicle connected. Telemetry appears only when received from a real vehicle.';
    const number=(v,suffix,decimals=1)=>connected&&Number.isFinite(v)?v.toFixed(decimals)+suffix:'—';
    $('telemetryGps').textContent=connected&&Number.isFinite(t.gps_fix)?(({0:'No GPS',1:'No fix',2:'2D',3:'3D',4:'DGPS',5:'RTK float',6:'RTK fixed'})[t.gps_fix]||'Fix '+t.gps_fix)+(Number.isFinite(t.satellites)?' · '+t.satellites+' sat':''):'—';
    $('telemetryBattery').textContent=number(t.battery_percent,'%',0);$('telemetryAltitude').textContent=number(t.relative_altitude_m,' m');$('telemetrySpeed').textContent=number(t.groundspeed_m_s,' m/s');
    $('telemetryPosition').textContent=connected&&validPosition(t)?t.latitude.toFixed(6)+', '+t.longitude.toFixed(6):'Waiting for a position fix';
    $('droneConnectBtn').disabled=state.busy||unavailable||connected||d.state==='connecting';$('droneDisconnectBtn').disabled=state.busy||unavailable||!d.connection;
    document.querySelectorAll('#droneConnectForm input,#droneConnectForm select,#droneConnectForm button').forEach(el=>el.disabled=state.busy||unavailable);
    $('droneConnectBtn').disabled=state.busy||unavailable||connected||d.state==='connecting';$('droneDisconnectBtn').disabled=state.busy||unavailable||!d.connection;
    if(connected&&validPosition(t)){
        if(!state.droneMarker)state.droneMarker=L.marker([t.latitude,t.longitude],{icon:L.divIcon({className:'',html:'<div class="drone-map-icon">⌘</div>',iconSize:[26,26],iconAnchor:[13,13]}),pmIgnore:true}).addTo(window.map);
        else state.droneMarker.setLatLng([t.latitude,t.longitude]);
        state.droneMarker.bindTooltip('Vehicle '+(t.system_id??'')+' · '+(t.armed?'Armed':'Disarmed'));
    }else if(state.droneMarker){window.map.removeLayer(state.droneMarker);state.droneMarker=null;}
}
async function connectDrone(event){
    event.preventDefault();if(state.busy||!canEdit())return;if(state.publicDemo||state.drone?.available===false)return showToast('Real drone connections require a local DroneMap AI installation. The coverage planner remains available.','error');const transport=$('droneTransport').value,body={transport};
    if(transport==='serial'){body.device=$('droneDevice').value.trim();body.baud=Number($('droneBaud').value);if(!body.device)return showToast('Choose a serial telemetry device.','error');}
    else{body.host=$('droneHost').value.trim();body.port=Number($('dronePort').value);if(!body.host||!Number.isInteger(body.port)||body.port<1024||body.port>65535)return showToast('Enter a host and port from 1024 to 65535.','error');}
    const epoch=state.epoch;state.droneSequence++;setBusy(true,'Opening telemetry link…');
    try{const result=await api('/api/drone/connect',{method:'POST',body:JSON.stringify(body)});if(state.epoch!==epoch)return;state.droneSequence++;state.drone=result;renderDroneStatus();showToast(result.message||'Telemetry link opened. Waiting for a real vehicle heartbeat.');}
    catch(error){if(state.epoch===epoch)showToast(error.message,'error');}finally{if(state.epoch===epoch)setBusy(false);}
}
async function disconnectDrone(){if(state.busy||!canEdit()||state.publicDemo||state.drone?.available===false)return;const epoch=state.epoch;state.droneSequence++;setBusy(true,'Closing telemetry link…');try{const result=await api('/api/drone/disconnect',{method:'POST'});if(state.epoch!==epoch)return;state.droneSequence++;state.drone=result;renderDroneStatus();showToast('Telemetry link disconnected.');}catch(error){if(state.epoch===epoch)showToast(error.message,'error');}finally{if(state.epoch===epoch)setBusy(false);}}
function focusDrone(){const t=state.drone?.telemetry;if(state.publicDemo||state.drone?.available===false||!state.drone?.connected||!t||!validPosition(t))return showToast('No live vehicle position has been received.','error');window.map.flyTo([t.latitude,t.longitude],18);collapseMobilePanel();}
function missionNumber(id,min,max){const raw=$(id).value,value=Number(raw);if(raw===''||!Number.isFinite(value)||value<min||value>max)throw new Error('Check '+($(id).parentElement?.firstChild?.textContent?.trim()||id)+': use '+min+'–'+max+'.');return value;}
async function previewMission(){
    if(state.busy||!canEdit()||!state.project)return;if(!state.scanSelection)return showToast('Draw a survey area before planning a flight.','error');
    let body;try{body={geometry:state.scanSelection.toGeoJSON(false).geometry,altitude_m:missionNumber('missionAltitude',10,120),speed_m_s:missionNumber('missionSpeed',.5,15),front_overlap:missionNumber('missionFrontOverlap',10,95)/100,side_overlap:missionNumber('missionSideOverlap',10,95)/100,sensor_width_mm:missionNumber('missionSensorWidth',1,100),sensor_height_mm:missionNumber('missionSensorHeight',1,100),focal_length_mm:missionNumber('missionFocalLength',1,200),image_width_px:missionNumber('missionImageWidth',100,20000),image_height_px:missionNumber('missionImageHeight',100,20000),angle_deg:missionNumber('missionAngle',0,180)};}catch(error){return showToast(error.message,'error');}
    const epoch=state.epoch,projectId=state.project.id;setBusy(true,'Planning coverage route…');
    try{
        const result=await api('/api/projects/'+projectId+'/missions/preview',{method:'POST',body:JSON.stringify(body)});if(!current(epoch,projectId))return;
        clearMission();state.mission=result;
        state.missionLayer=L.geoJSON(result.geojson,{style:f=>({color:f.geometry.type==='Polygon'?'#e8d47a':'#7ce3ec',weight:2,fillOpacity:.03,dashArray:f.geometry.type==='Polygon'?'4 4':null}),pointToLayer:(f,latlng)=>L.circleMarker(latlng,{radius:3,color:'#e7f8f8',fillColor:'#28b8b6',fillOpacity:1,weight:1,pmIgnore:true}),pmIgnore:true}).addTo(window.map);
        const s=result.summary;
        $('missionSummary').innerHTML=[['Survey area',(s.area_sqm/10000).toFixed(2)+' ha'],['Estimated time',(s.estimated_flight_seconds/60).toFixed(1)+' min'],['Flight route',s.route_distance_m.toFixed(0)+' m'],['Estimated photos',s.estimated_photos],['Ground sample',s.gsd_cm.toFixed(2)+' cm/px'],['Waypoints',s.waypoint_count]].map(([label,value])=>'<div>'+escapeHTML(value)+'<small>'+label+'</small></div>').join('');
        $('missionLimitations').textContent=(result.limitations||[]).join(' ');$('missionResult').classList.remove('hidden');window.map.fitBounds(state.missionLayer.getBounds(),{padding:[35,80]});collapseMobilePanel();showToast('Coverage route ready. Review terrain, camera settings and the route in your ground station.');
    }catch(error){if(current(epoch,projectId))showToast(error.message,'error');}finally{if(current(epoch,projectId))setBusy(false);}
}
function downloadMission(){if(!state.mission)return showToast('Preview a flight route first.','error');const blob=new Blob([JSON.stringify(state.mission.plan,null,2)],{type:'application/json'}),url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download='dronemap-'+(state.project?.id||'survey')+'.plan';document.body.appendChild(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);}
async function showPublicReport(token){try{const snapshot=await api('/api/public/reports/'+encodeURIComponent(token));state.publicReport=snapshot;state.project=snapshot.project;state.role='viewer';state.records=[snapshot.feature];const key=addEntry(snapshot.feature,null,true);state.entries.get(key).dirty=false;configureAccess();renderProjectOptions();renderRegistry();renderDetectionList();navigateTo('dashboard-view');switchTab('registry');setTimeout(()=>window.map.fitBounds(state.entries.get(key).layer.getBounds(),{padding:[40,80],maxZoom:20}),120);}catch(error){showToast(error.message,'error');}}
function initialize(){
    const satellite=L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',{maxZoom:22,maxNativeZoom:19,attribution:'Tiles © Esri and imagery providers'}),street=L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:22,maxNativeZoom:19,attribution:'© OpenStreetMap contributors'});
    window.map=L.map('map',{layers:[satellite],zoomControl:false}).setView([24.0885,87.7150],defaultMapZoom());
    L.control.zoom({position:'bottomright'}).addTo(window.map);L.control.layers({'Satellite imagery':satellite,'Street map':street},null,{position:'bottomright'}).addTo(window.map);
    window.map.pm.setGlobalOptions({snappable:true,snapDistance:15,snapMiddle:true,limitMarkersToCount:10});
    if(L.Control.geocoder)L.Control.geocoder({defaultMarkGeocode:false,position:'topleft',placeholder:'Search map…'}).on('markgeocode',e=>{window.map.fitBounds(e.geocode.bbox);updateTargetCoords(e.geocode.center.lat,e.geocode.center.lng);}).addTo(window.map);
    window.map.on('mousemove',e=>{$('mapCoordinates').textContent=Math.abs(e.latlng.lat).toFixed(6)+'° '+(e.latlng.lat<0?'S':'N')+' · '+Math.abs(e.latlng.lng).toFixed(6)+'° '+(e.latlng.lng<0?'W':'E');});
    window.map.on('pm:create',e=>{
        if(!canEdit()||state.busy){window.map.removeLayer(e.layer);return;}
        if(e.shape==='Rectangle'){
            if(state.scanSelection)window.map.removeLayer(state.scanSelection);clearMission();state.scanSelection=e.layer;
            e.layer.setStyle({color:'#e8d47a',weight:2,fillOpacity:.04,dashArray:'6 4'});e.layer.pm?.disable();e.layer.options.pmIgnore=true;updateScanArea();scheduleRecovery();showToast('Survey area selected. Detect buildings or preview a drone coverage route.');return;
        }
        const feature=e.layer.toGeoJSON(false);feature.properties={name:'Parcel '+(++state.counter),feature_type:'parcel',status:'draft',owner:'',notes:'Manually drawn survey boundary. Ownership and ground accuracy require independent verification.'};
        const key=addEntry(feature,e.layer);selectEntry(key,false);geometryChanged(key);renderRegistry();renderDetectionList();
    });
    $('processBtn').addEventListener('click',executeAI);$('droneInput').addEventListener('change',e=>updateFileInfo(e.target.files[0]));
    $('dropzone').addEventListener('dragover',e=>{e.preventDefault();$('dropzone').classList.add('dragging');});$('dropzone').addEventListener('dragleave',()=>$('dropzone').classList.remove('dragging'));$('dropzone').addEventListener('drop',e=>{e.preventDefault();$('dropzone').classList.remove('dragging');updateFileInfo(e.dataTransfer.files[0]);});
    $('pinInput').addEventListener('keydown',e=>{if(e.key==='Enter')login('official');});
    window.addEventListener('offline',()=>{persistRecovery();setBusy(state.busy);showToast('Internet unavailable. Imported imagery, local saves and editing can still work while the local server is running.','error');});window.addEventListener('online',()=>{setBusy(state.busy);showToast('Internet connection restored.');});
    window.addEventListener('beforeunload',protectDraftsBeforeUnload);
    window.addEventListener('pagehide',persistRecovery);
    document.addEventListener('visibilitychange',()=>{if(document.visibilityState==='hidden')persistRecovery();});
    window.addEventListener('keydown',event=>{if(event.key==='Escape'){if(!$('projectDialog').classList.contains('hidden'))closeProjectDialog();else{window.map.pm.disableDraw();closeInspector();}}});
    window.addEventListener('click',event=>{if(!event.target.closest('#menuDropdown')&&!event.target.closest('svg.w-8'))document.querySelectorAll('.dropdown-content').forEach(el=>el.classList.remove('show'));});
    document.querySelectorAll('#missionAltitude,#missionSpeed,#missionFrontOverlap,#missionSideOverlap,#missionSensorWidth,#missionSensorHeight,#missionFocalLength,#missionAngle,#missionImageWidth,#missionImageHeight').forEach(el=>el.addEventListener('input',()=>{if(state.mission)clearMission();}));
    changeLanguage('en');renderDetectionList();renderSources();updateScanArea();setBusy(false);
    api('/api/status').then(status=>applyDeploymentStatus(status)).catch(()=>{});
    const match=window.location.pathname.match(/^\/report\/([^/]+)$/);if(match)showPublicReport(decodeURIComponent(match[1]));
}
initialize();

