/* Keep the existing interface; clearly identify the on-device SIH workspace. */
(() => {
  const originalNotice=window.renderDemoNotice;
  window.renderDemoNotice=function(){
    originalNotice();
    document.body.classList.add('browser-workspace');
    $('landing-btn').textContent='Open mapping workspace';
    $('auth-login-btn').textContent='Open this device workspace';
    $('landing-sub').textContent='SIH26012 · GeoVision · Real building detection in your browser.';
    $('auth-officer-d').textContent='No account is needed in this edition. Projects and imagery stay in this browser.';
    $('demoNotice').classList.remove('hidden');
    $('demoNotice').querySelector('strong').textContent='BROWSER WORKSPACE';
    $('demoNoticeText').textContent=window.DroneMapBrowser.notice;
    const help=$('demoNotice').querySelector('a');
    help.href=window.DroneMapBrowser.base+'help.html';help.textContent='Workspace guide ↗';
    document.querySelectorAll('a.rail-item[title="Workspace help"]').forEach(link=>link.href=window.DroneMapBrowser.base+'help.html');
    $('uploadLimits').textContent='TIFF, PNG, JPEG · Up to 32 MB / 16 million pixels · Saved on this device';
    $('scanLimits').textContent='Map scans: maximum 500 × 500 m. AI runs on this device, up to 16 native tiles per scan. Review every outline.';
    $('inspectorReportBtn').textContent='↓ Survey PDF';
    $('feat3-d').textContent='Export reviewed boundaries and measurements as a PDF survey record, GeoJSON or CSV. No legal ownership is inferred.';
  };
  const originalBusy=window.setBusy;
  window.setBusy=function(...args){originalBusy(...args);$('connectionLabel').textContent=navigator.onLine?'Saved on this browser · map acquisition online':'Offline · saved imagery and records available';};
  window.openPortal=async()=>{if(!state.busy)await login('official');};
  window.defaultMapCenter=()=>[22.577,88.416];
  window.defaultMapZoom=()=>19;
  const originalSources=window.renderSources;
  window.renderSources=function(...args){
    originalSources(...args);
    // Imported georeferencing does not prove that imagery came from a drone.
    $('sourceList').querySelectorAll('.source-item .pill').forEach((badge,index)=>{
      badge.textContent=state.sources[index]?.kind==='map'?'Map':'Import';
    });
  };
  const originalGeometryChanged=window.geometryChanged;
  const sameCoordinates=(left,right)=>Array.isArray(left)&&Array.isArray(right)
    ?left.length===right.length&&left.every((value,index)=>sameCoordinates(value,right[index]))
    :Number.isFinite(left)&&Number.isFinite(right)&&Math.abs(left-right)<=1e-10;
  window.geometryChanged=function(key){
    const entry=state.entries.get(key);
    // Geoman emits pm:update again when save disables editing. A saved,
    // unchanged outline must not become an unsaved draft from that event.
    if(entry?.saved&&!entry.dirty){
      const current=geometryOf(entry),saved=entry.feature.geometry;
      if(current.type===saved.type&&sameCoordinates(current.coordinates,saved.coordinates))return;
    }
    return originalGeometryChanged(key);
  };
  window.exportProject=async function(format){
    if(!state.user||!state.project||!['geojson','csv'].includes(format))return;
    try{
      const blob=await api('/api/projects/'+state.project.id+'/export.'+format,{responseType:'blob'});
      const url=URL.createObjectURL(blob),link=document.createElement('a');link.href=url;link.download='dronemap-survey.'+format;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
    }catch(error){showToast(error.message,'error');}
  };
  window.addEventListener('dronemap:progress',event=>{if(state.busy){$('processingLabel').textContent=event.detail;$('btnText').textContent='Detecting buildings…';}});
  document.addEventListener('DOMContentLoaded',()=>{
    renderDemoNotice();
    if(window.map&&!state.project)window.map.setView(defaultMapCenter(),defaultMapZoom());
    const center=window.map?.getCenter();
    if(center){
      $('inputLat').value=center.lat.toFixed(6);$('inputLon').value=center.lng.toFixed(6);
      const showCenter=()=>{
        const point=window.map.getCenter();
        $('mapCoordinates').textContent=Math.abs(point.lat).toFixed(6)+'° '+(point.lat<0?'S':'N')+' · '+Math.abs(point.lng).toFixed(6)+'° '+(point.lng<0?'W':'E');
      };
      showCenter();window.map.on('moveend',showCenter);
    }
    document.querySelectorAll('[onclick="logout()"] small').forEach(element=>element.textContent='Close');
    window.DroneMapBrowser.ready.catch(error=>showToast('Browser storage is unavailable: '+error.message,'error'));
    navigator.storage?.persist?.().catch(()=>{});
  });
})();
