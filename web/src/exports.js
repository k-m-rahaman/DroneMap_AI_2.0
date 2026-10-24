/** On-device GIS and survey evidence exports. Documents make no ownership claim. */
import { jsPDF } from 'jspdf';
import { measureGeometry, localProjector, geometryError } from './geometry.js';

function normalized(features) {
  const list=Array.isArray(features)?features:features?.features;
  if (!Array.isArray(list)||list.length>12000) geometryError('Export at most 12,000 valid boundaries at a time.');
  return list.map((f,i)=>{
    if (!f||f.type!=='Feature'||(f.properties!=null&&(typeof f.properties!=='object'||Array.isArray(f.properties)))) geometryError(`Boundary ${i+1} must be a GeoJSON Feature.`);
    const m=measureGeometry(f.geometry);
    return {...structuredClone(f),geometry:m.geometry,properties:{...(f.properties||{}),area_sqm:m.area_sqm,perimeter_m:m.perimeter_m,bbox:m.bbox}};
  });
}
export function exportGeoJSON(features) { return {type:'FeatureCollection',features:normalized(features)}; }
export function exportCSV(features) {
  const fields=['id','name','owner','reference_id','feature_type','status','area_sqm','perimeter_m','source_id','confidence','version','updated_by','reviewed_by','reviewed_at','updated_at','notes','geometry'];
  const quote=value=>{
    let text=String(value??'');
    if (/^[\s\u0000-\u001f]*[=+\-@]/u.test(text)) text="'"+text;
    return '"'+text.replaceAll('"','""')+'"';
  };
  return '\ufeff'+[fields.map(quote).join(','),...normalized(features).map(f=>fields.map(key=>quote(key==='id'?f.id:key==='geometry'?JSON.stringify(f.geometry):f.properties[key])).join(','))].join('\r\n')+'\r\n';
}

const clean=value=>String(value??'').replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/gu,'');
const base=()=>globalThis.DroneMapBrowser?.base||import.meta.env?.BASE_URL||'/';
const fontCache=new Map();
async function fontBytes(filename) {
  if (!fontCache.has(filename)) fontCache.set(filename,(async()=>{
    const response=await fetch(base()+'fonts/'+filename);
    if (!response.ok) throw new Error('Report font unavailable. Reload the app while connected, then retry.');
    return new Uint8Array(await response.arrayBuffer());
  })().catch(error=>{fontCache.delete(filename);throw error;}));
  return fontCache.get(filename);
}
function base64(bytes) { let binary='';for(let i=0;i<bytes.length;i+=32768) binary+=String.fromCharCode(...bytes.subarray(i,i+32768));return btoa(binary); }

/** Browser canvas shapes Indic text instead of putting unshaped glyphs in a PDF. */
async function unicodeRenderer(text) {
  if (!/[^\u0000-\u00ff]/u.test(text)) return null;
  if (typeof document==='undefined'||typeof FontFace==='undefined') geometryError('Unicode PDF text requires the browser font renderer.');
  for (const [family,file,pattern] of [['SurveyLatin','NotoSans-Regular.ttf',/[^\u0000-\u00ff]/u],['SurveyBengali','NotoSansBengali-Regular.ttf',/[\u0980-\u09ff]/u],['SurveyDevanagari','NotoSansDevanagari-Regular.ttf',/[\u0900-\u097f]/u]]) {
    if (!pattern.test(text)) continue;
    const face=new FontFace(family,await fontBytes(file));await face.load();document.fonts.add(face);
  }
  // Other scripts are intentionally rejected rather than silently substituting boxes.
  if (/[^\u0000-\u024f\u0900-\u09ff\u1e00-\u1eff\u2000-\u206f\u20a0-\u20cf]/u.test(text)) geometryError('PDF text supports Latin, Bengali and Devanagari. Use GeoJSON/CSV for other scripts.');
  return (pdf,value,x,y,width,size,color)=>{
    const canvas=document.createElement('canvas'),scale=3.2,pixelsPerMm=96/25.4,context=canvas.getContext('2d');
    if (!context) geometryError('Browser text rendering is unavailable.');
    const font=`${size*96/72}px SurveyLatin, SurveyBengali, SurveyDevanagari`;
    context.font=font;
    const max=width*pixelsPerMm,lines=[];
    for(const paragraph of value.split('\n')) {
      let line='';
      for(const word of paragraph.split(/\s+/u)) {
        if(context.measureText((line?line+' ':'')+word).width<=max) {line+=(line?' ':'')+word;continue;}
        if(line)lines.push(line);
        line='';
        for(const glyph of word) {if(context.measureText(line+glyph).width>max&&line){lines.push(line);line='';}line+=glyph;}
      }
      lines.push(line||' ');
    }
    const heightPx=lines.length*size*96/72*1.4+5;
    canvas.width=Math.ceil(max*scale);canvas.height=Math.ceil(heightPx*scale);context.scale(scale,scale);context.font=font;context.textBaseline='top';context.fillStyle=`rgb(${color.join(',')})`;
    lines.forEach((line,i)=>context.fillText(line,0,i*size*96/72*1.4));
    const height=heightPx/pixelsPerMm;pdf.addImage(canvas.toDataURL('image/png'),'PNG',x,y,width,height,undefined,'FAST');return height;
  };
}

/** Real PDF bytes; source/version snapshot is local, not signed or publicly verified. */
export async function buildReportPDF(feature,project={},source=null) {
  const record=normalized([feature])[0],p=record.properties,measured=measureGeometry(record.geometry);
  const allText=clean(JSON.stringify([record,project,source])),renderUnicode=await unicodeRenderer(allText);
  const pdf=new jsPDF({unit:'mm',format:'a4',compress:true}),left=16,width=178;
  pdf.setProperties({title:'DroneMap AI - Survey Record',author:'GeoVision / DroneMap AI',subject:'Browser-local mapped geometry and human review evidence'});
  // Unicode Latin embedded text; ASCII exports remain usable without a font request.
  if (/[^\u0000-\u007f]/u.test(allText)&&!/[\u0900-\u09ff]/u.test(allText)) {
    const bytes=await fontBytes('NotoSans-Regular.ttf');pdf.addFileToVFS('NotoSans-Regular.ttf',base64(bytes));pdf.addFont('NotoSans-Regular.ttf','Survey','normal');pdf.setFont('Survey','normal');
  } else pdf.setFont('helvetica','normal');
  const font=pdf.getFont().fontName;let y=17;
  function page() { pdf.addPage();y=17; }
  function ensure(height) {if(y+height>272)page();}
  function text(value,size=9,color=[35,55,64],available=width,x=left) {
    value=clean(value)||'Not recorded';pdf.setFontSize(size);pdf.setTextColor(...color);
    if(renderUnicode&&/[^\u0000-\u00ff]/u.test(value)) {
      // Keep rasterized, browser-shaped runs small enough to paginate long notes.
      const chunks=value.match(/[\s\S]{1,240}/gu)||['Not recorded'];
      for(const chunk of chunks){ensure(36);const height=renderUnicode(pdf,chunk,x,y,available,size,color);y+=height+1;}return;
    }
    const lines=pdf.splitTextToSize(value,available),lineHeight=size*.352778*1.4;
    for(const line of lines){ensure(lineHeight+1);pdf.text(line,x,y+size*.352778);y+=lineHeight;}y+=1;
  }
  function detail(label,value) {ensure(15);text(label.toUpperCase(),7,[91,112,121]);text(value,9);y+=1;}
  pdf.setFillColor(8,84,97);pdf.rect(0,0,210,5,'F');text('BUILDING & LAND SURVEY RECORD',17,[18,45,61]);text('Mapped geometry and human review | Browser workspace',9,[83,103,115]);y+=4;
  detail('Feature',p.name);detail('Project / location',[project.name,project.location].filter(Boolean).join(' / '));
  detail('Record / version',`${record.id||'Unsaved boundary'} / ${p.version??'Unsaved draft'}`);
  detail('Reference / type / status',[p.reference_id,p.feature_type||'building',p.status||'draft'].filter(Boolean).join(' / '));
  detail('Claimant (entered)',p.owner||'No claimant recorded; ownership not verified');
  detail('WGS84 ellipsoidal area / perimeter',`${measured.area_sqm.toFixed(2)} m2 / ${measured.perimeter_m.toFixed(2)} m`);
  detail('Reviewer / date',[p.reviewed_by||'Pending human review',p.reviewed_at||'Not reviewed'].join(' / '));
  detail('Generated',new Date().toISOString());
  ensure(97);text('MAPPED BOUNDARY',9,[22,69,81]);
  const top=y,projection=localProjector(measured.centroid),components=record.geometry.type==='Polygon'?[record.geometry.coordinates]:record.geometry.coordinates,polygons=components.map(rings=>rings.map(r=>r.map(projection.forward))),points=polygons.flat(2),xs=points.map(pt=>pt[0]),ys=points.map(pt=>pt[1]),west=Math.min(...xs),east=Math.max(...xs),south=Math.min(...ys),north=Math.max(...ys),scale=Math.min(140/Math.max(east-west,.001),55/Math.max(north-south,.001)),mx=(west+east)/2,my=(south+north)/2;
  pdf.setFillColor(247,250,252);pdf.setDrawColor(218,226,232);pdf.rect(left,top,width,77,'FD');pdf.setLineWidth(.5);
  for(const rings of polygons) for(const [index,ring] of rings.entries()) {
    const pts=ring.map(([x,yy])=>[left+width/2+(x-mx)*scale,top+38-(yy-my)*scale]);
    pdf.setFillColor(...(index?[247,250,252]:[204,235,235]));pdf.setDrawColor(0,111,123);
    const relative=pts.slice(1).map((pt,i)=>[pt[0]-pts[i][0],pt[1]-pts[i][1]]);pdf.lines(relative,pts[0][0],pts[0][1],[1,1],'FD',true);
  }
  pdf.setFontSize(8);pdf.setTextColor(35,55,64);pdf.text('N',left+width-12,top+10);pdf.line(left+width-11,top+14,left+width-11,top+22);pdf.line(left+width-11,top+14,left+width-13,top+17);pdf.line(left+width-11,top+14,left+width-9,top+17);
  const bar=Math.max(.01,Math.min(east-west,north-south)*.2),magnitude=10**Math.floor(Math.log10(bar)),metres=Math.max(magnitude,Math.floor(bar/magnitude)*magnitude),barLength=metres*scale;
  pdf.line(left+6,top+68,left+6+barLength,top+68);pdf.text(`${metres.toPrecision(2)} m`,left+6,top+65);y=top+80;text('Local metre projection; north up. Geometry measurement is not positional survey accuracy.',7,[96,113,125]);
  page();text('PROVENANCE & REVIEW NOTES',15,[18,45,61]);y+=4;
  detail('Imagery source',source?.name||'Manual geometry; no imagery source linked');
  if(source) {
    detail('Source type / CRS',[source.kind,source.crs].filter(Boolean).join(' / '));detail('Attribution',source.attribution);detail('Acquisition date',source.acquisition_date||'Unknown; not inferred from upload date');detail('Source bounds',JSON.stringify(source.bounds||null));detail('Resolution',typeof source.resolution_m==='number'?`${source.resolution_m.toFixed(3)} m/pixel`:'Not recorded');
  }
  if(typeof p.confidence==='number')detail('Model confidence',`${(p.confidence*100).toFixed(1)}%. Confidence is not boundary accuracy.`);
  if(p.quality_flags?.length)detail('Review warnings',p.quality_flags.join(', '));
  detail('Notes',p.notes||'No notes recorded');
  text('BROWSER-LOCAL RECORD',10,[22,69,81]);text('Generated from the current browser workspace. This PDF has no server signature, public verification link or independent cadastral verification. Keep a workspace backup and source evidence.',9);
  text('SURVEY EVIDENCE ONLY. This is not a property title, ownership certificate, cadastral approval, government-issued document or digitally signed PDF.',9,[110,74,22]);
  for(let pageNumber=1;pageNumber<=pdf.getNumberOfPages();pageNumber++){pdf.setPage(pageNumber);pdf.setFont(font,'normal');pdf.setFontSize(7);pdf.setTextColor(110,120,130);pdf.text(`DroneMap AI | Browser-local survey evidence | Page ${pageNumber} of ${pdf.getNumberOfPages()}`,105,287,{align:'center'});}
  return new Uint8Array(pdf.output('arraybuffer'));
}
