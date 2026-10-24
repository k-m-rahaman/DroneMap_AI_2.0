# Locally served browser dependencies

These unmodified upstream distributions let the SIH browser workspace start without downloading JavaScript or CSS from third-party CDNs. Their license files and embedded notices are retained.

| Asset | Pinned version | Upstream |
| --- | --- | --- |
| Tailwind browser runtime | 3.4.17 | `https://cdn.tailwindcss.com/3.4.17` |
| Turf UMD bundle | 6.5.0 | Official `@turf/turf` npm tarball |
| Leaflet Control Geocoder UMD and CSS | 4.0.0 | Official `leaflet-control-geocoder` npm tarball |

`manifest.json` records the exact source URLs, byte counts and SHA-256 hashes. The maintenance script verifies npm archive SHA-512 integrity against the registry before reading only the explicitly selected archive members. Building or opening the app does not run that script.

Turf 6.5.0 is intentional: the original app uses its two-argument `intersect` API. The browser app's separate bundled geospatial module can use Turf 7 without replacing this original UI global.

The geocoder CSS embeds its icon and spinner as SVG data URLs. It has no external stylesheet or image dependencies. Its source map is included for developer tools. Search results, online map tiles and map-image acquisition still require their respective network services; vendoring does not make those services available offline. Optional Google Fonts can fall back to the existing system font stack.

The small Tailwind runtime is retained for compatibility with the existing HTML. This is suitable for the SIH browser build; a separate generated stylesheet can replace it later if desired.
