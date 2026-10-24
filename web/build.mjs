import {build} from 'esbuild';
import {mkdir,readFile,writeFile,cp,copyFile,stat,readdir} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createHash} from 'node:crypto';

const root=path.dirname(fileURLToPath(import.meta.url));
const repository=path.dirname(root);
const output=path.join(root,'dist');
const base=process.env.DRONEMAP_WEB_BASE||'/DroneMap_AI_2.0/';
if(!/^\/[A-Za-z0-9_./-]*\/$/.test(base)&&base!=='/')throw Error('Use an absolute base path ending in /.');
await mkdir(output,{recursive:true});
await mkdir(path.join(output,'runtime'),{recursive:true});
await mkdir(path.join(output,'models'),{recursive:true});
await cp(path.join(repository,'static'),path.join(output,'static'),{recursive:true});
await cp(path.join(repository,'fonts'),path.join(output,'fonts'),{recursive:true});
await cp(path.join(root,'vendor'),path.join(output,'static','vendor','browser'),{recursive:true});
await cp(path.join(root,'models'),path.join(output,'models'),{recursive:true});
for(const name of ['my_logo.png','drone_bg.mp4'])await copyFile(path.join(repository,name),path.join(output,name));
const ort=path.join(root,'node_modules','onnxruntime-web','dist');
for(const name of ['ort-wasm-simd-threaded.mjs','ort-wasm-simd-threaded.wasm'])await copyFile(path.join(ort,name),path.join(output,'runtime',name));
const model=path.resolve(process.env.DRONEMAP_ONNX_MODEL||path.join(root,'models','buildings.onnx'));
if(!(await stat(model)).isFile())throw Error('Export the actual Building model before building the browser app.');
const modelManifest=JSON.parse(await readFile(path.join(root,'models','manifest.json'),'utf8'));
if(createHash('sha256').update(await readFile(model)).digest('hex')!==modelManifest.sha256)throw Error('Building model does not match its recorded export hash.');
await copyFile(model,path.join(output,'models','buildings.onnx'));
const bundle=await build({entryPoints:[path.join(root,'src','runtime.js')],outfile:path.join(output,'browser-runtime.js'),bundle:true,format:'iife',target:'es2022',minify:true,metafile:true,legalComments:'external',define:{DRONEMAP_WEB_BASE:JSON.stringify(base),'import.meta.url':JSON.stringify(''),'import.meta.env.BASE_URL':JSON.stringify(base)},logLevel:'info'});
// Retain package license texts for bundled dependencies, alongside the runtime's
// own complete upstream notices in static/vendor/browser/.
const packageRoots=new Map();
for(const input of Object.keys(bundle.metafile.inputs)){
  if(!input.includes('node_modules'))continue;
  let dir=path.dirname(path.resolve(input));
  while(dir.startsWith(root)&&dir!==root){
    try{const pkg=JSON.parse(await readFile(path.join(dir,'package.json'),'utf8'));if(pkg.name&&pkg.version){packageRoots.set(dir,pkg);break;}}catch{}
    dir=path.dirname(dir);
  }
}
const licenseFolder=path.join(output,'licenses');await mkdir(licenseFolder,{recursive:true});
const packageNotices=[];
for(const [dir,pkg] of [...packageRoots].sort((a,b)=>a[1].name.localeCompare(b[1].name))){
  const texts=[];
  for(const entry of await readdir(dir,{withFileTypes:true}))if(entry.isFile()&&/^(license|licence|copying|notice)(\.|$)/i.test(entry.name))texts.push(entry.name);
  const folder=pkg.name.replaceAll('/','__')+'@'+pkg.version;
  if(texts.length){await mkdir(path.join(licenseFolder,folder),{recursive:true});for(const name of texts)await copyFile(path.join(dir,name),path.join(licenseFolder,folder,name));}
  packageNotices.push({name:pkg.name,version:pkg.version,license:pkg.license||'See upstream notices',files:texts.map(name=>'licenses/'+folder+'/'+name)});
}
await writeFile(path.join(output,'dependency-licenses.json'),JSON.stringify(packageNotices,null,2));
await copyFile(path.join(root,'THIRD_PARTY_NOTICES.md'),path.join(output,'THIRD_PARTY_NOTICES.md'));
await copyFile(path.join(root,'help.html'),path.join(output,'help.html'));
await copyFile(path.join(root,'src','ui-bridge.js'),path.join(output,'browser-ui.js'));
await copyFile(path.join(root,'src','browser.css'),path.join(output,'browser.css'));
const originalUI=path.join(output,'static','existing-app.js');
await writeFile(originalUI,(await readFile(originalUI,'utf8'))
  .replace('Internet unavailable. Imported imagery, local saves and editing can still work while the local server is running.','Internet unavailable. Saved imagery, local saves and editing can work while this page stays open. Online map acquisition needs internet.')
  .replace('The server did not return a survey PDF. Please try again.','A valid survey PDF could not be generated. Please try again.'));
let html=await readFile(path.join(repository,'index.html'),'utf8');
html=html
  .replace('DroneMap AI | SVAMITVA Precision','DroneMap AI | GeoVision Survey Workspace')
  .replace('The SVAMITVA Vision','GeoVision · SIH26012')
  .replace('Ministry of Panchayati Raj, Govt. of India','AI-assisted building mapping and parcel review')
  .replace('SVAMITVA (Survey of Villages Abadi and Mapping with Improvised Technology in Village Areas) is a Central Sector Scheme providing a clear record of ownership to rural estate owners.','DroneMap AI helps review individual building outlines and document survey geometry from map imagery and georeferenced source images.')
  .replace('DroneMap AI accelerates this mission.','Review each AI proposal before saving.')
  .replace('server-calculated geodesic measurements','on-device geodesic measurements')
  .replace('Generate survey evidence PDFs with version-specific QR verification links. These reports are not ownership titles.','Generate unsigned survey PDFs with source and version details, plus GeoJSON and CSV exports. These reports are not ownership titles.')
  .replace('About SVAMITVA Engine','About DroneMap AI')
  .replace("showToast('API documentation is available at /docs for this local application.', 'error')","window.open('"+base+"help.html','_blank','noopener')")
  .replace('API Documentation','Workspace guide')
  .replace("showToast('Ask your project administrator for support. Include the imagery source and error message.')","window.open('"+base+"help.html','_blank','noopener')")
  .replace('Support & Helpdesk','Help & workflow')
  .replace('Connect a MAVLink vehicle through your ground station or a USB telemetry radio.','Plan a survey and import its georeferenced imagery. Live telemetry requires the separate local Python installation and compatible hardware.')
  .replace('This workspace reads telemetry and prepares a route; it does not arm, launch or control the vehicle.','This browser workspace prepares a route; live telemetry is available only in the separate local installation. It does not arm, launch or control the vehicle.');
for(const [remote,local] of [
  ['https://cdn.tailwindcss.com','tailwind-3.4.17.js'],
  ['https://cdn.jsdelivr.net/npm/@turf/turf@6/turf.min.js','turf-6.5.0.min.js'],
  ['https://unpkg.com/leaflet-control-geocoder/dist/Control.Geocoder.js','Control.Geocoder-4.0.0.js'],
  ['https://unpkg.com/leaflet-control-geocoder/dist/Control.Geocoder.css','Control.Geocoder-4.0.0.css']
])html=html.replaceAll(remote,base+'static/vendor/browser/'+local);
html=html.replaceAll('href="/static/','href="'+base+'static/').replaceAll('src="/static/','src="'+base+'static/').replaceAll('src="/my_logo.png"','src="'+base+'my_logo.png"').replaceAll('src="/drone_bg.mp4"','src="'+base+'drone_bg.mp4"');
html=html.replace('<script src="'+base+'static/existing-app.js">','<script src="'+base+'browser-runtime.js"></script>\n    <script src="'+base+'static/existing-app.js">');
html=html.replace('</body>','<script src="'+base+'browser-ui.js"></script>\n</body>');
html=html.replace('</head>','<link rel="stylesheet" href="'+base+'browser.css">\n</head>');
if(!html.includes('src="'+base+'browser-runtime.js"')||!html.includes('src="'+base+'browser-ui.js"'))throw Error('The existing UI integration scripts were not inserted.');
await writeFile(path.join(output,'index.html'),html);
await writeFile(path.join(output,'.nojekyll'),'');
console.log('Built browser workspace at '+output+' for '+base);
