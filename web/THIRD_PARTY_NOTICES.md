# Browser workspace notices

The browser workspace source and build instructions are available at
https://github.com/k-m-rahaman/DroneMap_AI_2.0/tree/main/web.

`models/buildings.onnx` exports the repository's existing `best.pt` checkpoint;
`models/manifest.json` records both hashes, model dimensions and conversion scope.
Its embedded Ultralytics metadata identifies AGPL-3.0. The full license is retained
in `static/vendor/browser/Ultralytics-AGPL-3.0.txt`. Conversion does not establish
new training-data provenance or independently measured geographic accuracy.

ONNX Runtime Web 1.30.0 is distributed under its upstream MIT license.
`static/vendor/browser/ONNXRuntime-LICENSE.txt` and
`ONNXRuntime-ThirdPartyNotices.txt` preserve Microsoft's full notices from the
official `microsoft/onnxruntime` v1.30.0 release.

`static/vendor/browser/manifest.json` identifies the pinned Tailwind, Turf 6 and
Leaflet Control Geocoder distributions and their original license texts.
`dependency-licenses.json`, `licenses/`, and `browser-runtime.js.LEGAL.txt`
retain package and embedded notices from the bundled browser modules, including
Turf 7, GeographicLib, GeoTIFF, idb, jsPDF and proj4. ONNX Runtime package notices
are supplied separately above because their npm packages omit those text files.
The Noto fonts retain their SIL Open Font License in `fonts/OFL.txt`.

The original Leaflet and Geoman distributions retain their existing notices in
`static/vendor/`. Online imagery and search remain subject to their providers'
terms and attribution; a browser download does not establish ownership of imagery.
