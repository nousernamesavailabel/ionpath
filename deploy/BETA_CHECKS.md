# Beta 0.1.0-beta.1 verification

Verified October 18, 2026 (America/Anchorage), using the locked release container.

- Image built successfully: `localhost/ionpath:beta-0.1.0-beta.1`.
- `pip check`: no broken requirements.
- Complete unittest suite: 80 passed in 13.383 seconds, no skipped tests.
- Checks ran with a 3 GiB memory limit, 2 CPU cores and a 256-process limit.
- History tests cover query invalidation, local reuse after target-hour changes,
  the explicit refresh button, and retaining original cache metadata after a
  failed refresh. Diagnostic preview and identity omission are tested.
- Two overlapping public Streamlit AppTest sessions used different locations and
  physical dipoles with the real NEC and ITU model. Each produced 24 hourly rows,
  distinct model outputs, separate station/antenna files and its own validation
  database. Neither session observed the other's private data.
- For that one-frequency concurrency check, measured wall times were 0.85 and
  1.18 seconds. Peak RSS was about 199 MiB per session process and 129 MiB per
  solver child. These per-process peaks are not a measurement of total container
  peak memory or a production capacity estimate.

The concurrency test uses a small Streamlit integration script exercising the
actual storage/model modules, rather than two browsers over the complete app.
The full app has separate AppTest interface coverage. Remote feeds are mocked
in interface tests; these results do not establish provider availability, rate
limits or remote latency. The test container runs as root to install the test
sources into its disposable filesystem; the release image runs as user 10001.

Before public sharing, perform the deployed two-browser, HTTPS, WebSocket and
firewall checks in deploy/README.md. No public deployment was performed by these
checks. Monitor real container resource use during the initial beta and adjust
limits for representative paths, frequency counts and concurrent visitors.

## Path details and PDF export follow-up

- Rebuilt the beta image with ReportLab 4.4.9 and bundled offline Natural Earth
  land outlines.
- Updated complete suite: 85 tests passed in 15.124 seconds under the same limits;
  dependency consistency passed.
- New checks cover initial bearings and reverse bearings, spherical long/short
  distances, date-line/antipodal/coincident cases, immutable run snapshots,
  partial-data reports and preparing a PDF through the app without new network
  requests.
- Rendered and visually inspected all four pages of an illustrative saved-run
  PDF with map, station setup, directional model outlooks, reception summary and
  ionosonde/source context. Verified expected coordinates/bearings, report identity
  and local/UTC target times through PDF text extraction.
- No Streamlit server was running in this workspace during the change. The
  container image is built; no public service was started or deployed.

## Magnetic azimuth and explicit assumptions follow-up

- Pinned pygeomag 1.1.0 with bundled WMM coefficients; rebuilt the beta image.
- 89 tests passed in 15.189 seconds; dependency consistency passed.
- Checked the documented Seattle WMM reference, independent east/west departure
  declinations, historical coefficient selection, out-of-range dates and blackout
  handling. Magnetic = true minus east-positive declination, normalized to 0-360.
- Verified that the assumptions table prefers actual model provenance over UI
  input values, including power, bandwidth and receiver noise.
- Rendered and visually inspected the updated four-page illustrative PDF. Text
  extraction confirms magnetic azimuth/model labels and explicit numeric setup
  values. No public deployment was performed.

## Browser/PDF modeled table parity

- Pinned vl-convert-python 1.9.0.post1 and installed DejaVu fonts in the image.
- Full suite: 94 tests passed in 17.343 seconds; dependency consistency passed.
- After the final chart-spacing adjustment, all five chart-parity/export tests
  passed again in 1.797 seconds.
- PDF target-hour tables use the browser's model_overview and snr_table_styles:
  same columns, sorted rows, one-decimal values and cell colors. Missing values
  now have a consistent gray treatment in both renderers.
- PDF heatmaps render the browser's actual heatmap_spec with SNR labels enabled,
  preserving frequency/hour order, selected clock and direction, shared viridis
  scale, target column and saved-run D-RAP markers. No model rerun is required.
- Export UI tests verify that changing clock/direction invalidates prepared bytes
  and that regenerating the report makes no provider requests.
- Visually inspected the four-page example, including the landscape two-panel
  chart page and its SNR cell labels. Checked portrait/landscape page dimensions,
  Unicode directional headers and numeric assumptions by PDF inspection.
- Rebuilt the beta image. No public deployment was performed.

## Direct-runtime dependency repair

The live app was using the existing virtual environment in
`wspr_path_planner_mgrs_flexible/.venv`, rather than the tested container. Installed
its missing pinned magnetic/PDF dependencies: pygeomag 1.1.0, ReportLab 4.4.9 and
vl-convert-python 1.9.0.post1. Dependency consistency passed in that environment.
All four magnetic tests passed there, including recovery after a missing-package
error. A path-bar AppTest in the same runtime displayed numeric magnetic headings
for both directions. Model results remain cached, while dependency/file failures
are retried rather than cached indefinitely. No live server was restarted.

## Portrait layout refinements

- Prepare and Download share one horizontal action row in the app.
- Station identities, WGS84 centers/search radii, antenna setup, receiver settings
  and solar/model assumptions now share one table; duplicate station setup was
  removed from the map page. Partial model runs retain a configured-setup table.
- Both directional 24-hour heatmaps are stacked on a portrait page, with the
  existing SNR values, colors and full-column target-hour outline preserved.
- 95 tests passed in 17.002 seconds; dependency consistency passed. All five chart
  export tests passed again after the final outline adjustment (1.697 seconds).
- Rendered and visually reviewed all five illustrative report pages; verified
  portrait dimensions throughout and that station identities and assumptions
  occur together on one page. Rebuilt the beta image; no public deployment.
