'use strict';

// Additional survey tools share the workspace's authenticated API and visual
// controls. Imported references stay as recoverable drafts until reviewed.
function chooseParcelImport() {
    if (!canEdit() || state.busy || !state.project) return;
    $('parcelImportFile').click();
}

async function previewParcelImport(input) {
    const file = input.files?.[0];
    input.value = '';
    if (!file || !canEdit() || state.busy || !state.project) return;
    if (file.size > 8 * 1024 * 1024) return showToast('Import a GeoJSON file smaller than 8 MB.', 'error');
    const epoch = state.epoch, projectId = state.project.id;
    setBusy(true, 'Checking parcel references…');
    try {
        let collection;
        try { collection = JSON.parse(await file.text()); }
        catch { throw new Error('The file is not valid GeoJSON JSON.'); }
        if (!current(epoch, projectId)) return;
        const result = await api('/api/projects/' + projectId + '/imports/preview', {
            method: 'POST', body: JSON.stringify({collection, feature_type: 'parcel'}),
        });
        if (!current(epoch, projectId)) return;
        for (const feature of result.features) addEntry(feature);
        persistRecovery();
        renderDetectionList(); renderRegistry();
        fitAllBoundaries(); switchTab('survey');
        showToast(result.features.length + ' parcel references imported as unsaved drafts. Review the source and boundaries before saving.');
        if (result.quality?.issues?.length) showToast(result.quality.issues.length + ' overlaps found in the import. Inspect boundaries before review.', 'error');
    } catch (error) {
        if (current(epoch, projectId)) showToast(error.message, 'error');
    } finally {
        if (current(epoch, projectId)) setBusy(false);
    }
}

async function checkProjectQuality() {
    if (!state.user || !state.project || state.publicReport || state.busy) return;
    const epoch = state.epoch, projectId = state.project.id;
    setBusy(true, 'Checking saved boundary overlaps…');
    try {
        const result = await api('/api/projects/' + projectId + '/quality');
        if (!current(epoch, projectId)) return;
        const container = $('qualityResults'); container.replaceChildren();
        const summary = document.createElement('p');
        summary.textContent = 'Saved records: ' + result.summary.features + ' · Overlap warnings: ' + result.summary.issues + (result.summary.truncated ? '+' : '') + '. Unsaved edits are not included. Adjacent parcel gaps need a known coverage boundary.';
        container.appendChild(summary);
        for (const issue of result.issues.slice(0, 30)) {
            const button = document.createElement('button'); button.className = 'button full';
            button.textContent = issue.kind + ': ' + issue.names.map((name, i) => name || issue.feature_ids[i]).join(' / ') + ' · ' + issue.overlap_sqm.toFixed(2) + ' m²';
            button.addEventListener('click', () => {
                if (!current(epoch, projectId)) return;
                const pair = [...state.entries].find(([, entry]) => issue.feature_ids.includes(entry.feature.id));
                if (pair) selectEntry(pair[0]);
            });
            container.appendChild(button);
        }
        if (result.issues.length > 30) {
            const note = document.createElement('p'); note.textContent = 'Showing the first 30 warnings. Resolve and rerun the check.'; container.appendChild(note);
        }
        showToast(result.summary.issues ? 'Overlap warnings are ready to inspect.' : 'No significant same-layer overlaps found in saved records.');
    } catch (error) {
        if (current(epoch, projectId)) showToast(error.message, 'error');
    } finally {
        if (current(epoch, projectId)) setBusy(false);
    }
}
