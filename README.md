# HF Path Planner

Choose two geographic areas, **A (blue)** and **B (orange)**, and a local target hour. Both directions is the default: reports from transmitters near A heard near B, and transmitters near B heard near A. A selected callsign identifies the station at A in either role. Both endpoints must be located inside the respective search circles; unlocated reports are excluded. Regional history pools different station configurations.

Click **Analyze propagation** once to run reception history, the HF model, NOAA conditions and ionosondes together. Three summary cards show reception evidence, the colored HF outlook and current ionospheric context side by side on desktop screens. Each source can fail independently, with its status visible; a failed new run does not silently retain a previous run’s results. Changing settings marks the whole dashboard as outdated until the next analysis.

The target hour and history time window stay at the top. **Path and map** contains a 680-pixel map that keeps the same expanded height before and after a run, endpoint radius controls, Path options and Coordinates; it collapses after analysis to make space for the results. Drag pins to move circles and outlines to resize them, or enter Maidenhead, MGRS or decimal WGS84 coordinates. Saved station A settings and the last B coordinate format persist locally. Advanced history/source and HF-model settings are in the sidebar. Charts, source quality/provenance and exports are in collapsed detail sections below the dashboard. On narrow screens Streamlit stacks the cards vertically.

## HF modeled outlook and NOAA conditions

Click **Analyze propagation** to calculate the HF outlook alongside reception history and ionosondes. The HF card remains available even when no WSPR/FT8 reports match. The selected reference date determines the model month/year and the local target hour determines the UTC hour. The underlying model uses monthly climatology and mid-month solar geometry; changing the day within a month does not produce a daily propagation forecast.

The panel runs the official **ITU-R P.533-14** implementation locally for both directions and all 24 UTC hours. Ten representative frequencies cover 160m through 10m; enter custom frequencies anywhere from **1.6–30 MHz**, including non-amateur frequencies. These are model samples, not suggested operating channels. 6m, groundwave and sporadic-E openings are outside this baseline.

Set transmit power, receiver bandwidth and receiver noise category. Under **Antenna A** and **Antenna B**, choose a preset or build each endpoint independently; both directions share the selected power, bandwidth and noise category. Antenna geometry and height affect the actual propagation calculation, as described below. The target-hour table sorts by the weaker direction's modeled median SNR, and the **24-hour frequency outlook** heatmaps show every analyzed frequency across all 24 model hours. A shared red–amber–green table scale highlights lower through higher modeled SNR relative to the displayed values, without implying pass/fail thresholds. Model SNR is not numerically comparable with the WSPR/FT8 measurements. No calibrated voice threshold or link-success percentage is presented. Hourly CSV and JSON assumptions/provenance downloads are available.

By default, the model uses NOAA's observed smoothed SSN for the exact selected UTC month, or NOAA's predicted SSN when a supported observation is unavailable. It never substitutes daily sunspot counts or a neighboring month. A manual smoothed sunspot number (R12) is available under **HF model settings** in the sidebar when needed. The shared analysis button requests NOAA conditions and ionosondes on every run (subject to their caches). The local HF model can still run with manual SSN when these network sources fail.

NOAA context includes timestamped planetary Kp and D-RAP highest affected frequency sampled at 41 points along the current A–B short great circle. This geographic absorption context is not a path-integrated loss, lowest usable frequency or closed-band threshold. The panel warns about stale observations and failed refreshes; cached data retain their original retrieval times. Current NOAA conditions are shown separately from the chosen target date and do not modify model SNR. Feed URLs and retrieval times remain inspectable. Live ionosonde measurements and IRTAM nowcasts provide additional current F2 context. These do not assimilate an electron-density profile into P.533 or directly change its monthly SNR values.

### Antenna builder

**Antenna A** and **Antenna B** in the sidebar offer an isotropic reference, dipoles at several heights, inverted Vs, horizontal or vertical square/rectangle/triangle loops, end-fed wires with a collinear counterpoise, and verticals with four elevated horizontal radials. Presets are starting dimensions: wire length/perimeter, height and true bearing are editable for each run. Height refers to the horizontal wire/loop plane, V apex, vertical loop bottom or vertical feedpoint. A loop’s bearing is its baseline direction, not its broadside beam. A triangle is equilateral; rectangles have an adjustable aspect ratio. The end-fed wire’s bearing points from its feed toward its far end. Advanced inputs set wire radius, poor/average/good ground and additional feedline/tuner loss.

A and B antenna settings save automatically and independently to `.cache/settings/antenna_A.json` and `antenna_B.json`. Preset, antenna type, dimensions, height, orientation (including the Aim button result), ground and losses reload in new sessions and after restarts. They stay assigned to their A/B station slots until manually changed; changing a path does not silently resize or re-aim the antennas. Selecting another preset, including the isotropic reference, replaces that slot’s default. No Analyze or Save click is required. Files update atomically only when settings change; load/save failures are shown in the sidebar.

Each physical antenna has an **Aim main lobe at the other station** button. It calculates the short-path bearing from that endpoint, finds the strongest NEC azimuth lobe at the selected aiming frequency and 20° elevation, and sets the wire/baseline orientation accordingly. The two endpoints use their own bearings. Effectively omnidirectional patterns need no rotation. Aiming is a frequency/elevation-specific choice; click Analyze propagation afterward to recalculate the outlook.

The local `necpp` 2.3.4 solver computes copper-wire NEC-2 patterns over Sommerfeld/Norton flat lossy ground at every analyzed frequency. Full 360 × 91 azimuth/elevation samples are written in the official ITU wrapper’s Type 13 format; the global bearings are fixed and the A/B antenna roles swap on reversal. P.533 selects gains at its modeled propagation angles. Geometry/frequency patterns are cached locally, with a 60-second solver limit and a 1200-segment ceiling. Invalid geometry reports a model error rather than silently substituting an isotropic pattern.

Physical dimensions remain fixed across frequencies. Results assume ideal matching to the wire at each frequency; they do not model a particular transformer/tuner, feedline impedance transformation, terrain, structures or polarization coupling. Inspect the saved geometry in model provenance and enter additional matching/feedline loss explicitly. A long wire with no defined return path is not modeled as an independent one-terminal radiator. An elevated-radial vertical is not a ground-mounted vertical with buried radials. Antenna selection does not alter historical WSPR/FT8 measurements. Source: [NEC2++](https://github.com/tmolteno/necpp), [ITU pattern reader](https://github.com/ITU-R-Study-Group-3/ITU-R-HF/blob/master/P533/Src/P533/ReadType13.c).

### Current D-RAP and IRTAM context

The **Current path conditions** panel runs with the same Analyze button. D-RAP samples the short great circle; `!` on a heatmap means the frequency lies within the sampled highest-affected-frequency context. It is not a closed-band indication or an absorption loss in dB. Flags require a source timestamp no older than 30 minutes and an hourly bin within 30 minutes of that timestamp on its actual UTC date. Historical/future bins never inherit today’s conditions.

IRTAM is automatically loaded from the public latest JSON map link published on [GIRO’s service page](https://giro.uml.edu/rix/test.html). Maps cache for 15 minutes; failed refreshes back off for one minute and share the existing provider-wide GIRO rate limit/429 cooldown. Cached maps retain their source valid time. The grid is sampled at P.533 F2 control regions, with longitude wrapping and source scale factors/missing values honored. Fresh maps within 30 minutes of the selected target allow a regional foF2 comparison against the P.533 baseline. The details show grid coordinates, confidence metadata and available foF2/hmF2 values. Nonphysical provider height values are excluded rather than silently rescaled. Coarse grid coverage and absent confidence do not establish local observational support. The comparison currently uses the A→B representative-frequency control regions; it is not a separate ray trace or a validation of every propagation mode.

This adds a nowcast context layer, not a corrected 24-hour SNR forecast. Direct assimilation and calibrated absorption corrections remain future work, to be justified with validation data. [IRTAM/GAMBIT documentation](https://giro.uml.edu/GAMBIT/).

### Local prediction validation

Every successful Analyze run saves its hourly predictions, input/antenna assumptions, current-condition timestamps and reception-evidence summary in `.cache/validation/history.sqlite3`. It survives restarts and stays local. No automatic background polling is scheduled. The **Prediction validation log** expander lets you inspect/export snapshots and record an actual attempted test against a saved run, specifying frequency, UTC hour, direction, actual time, reception outcome, optional receiver SNR and setup confirmation.

Only observations taken after the run was generated, within ±30 minutes of the selected hourly bin and with matching station setup explicitly confirmed enter comparable test statistics. Numerical SNR errors additionally require a measured receiver SNR in the modeled bandwidth and a successful reception. A missing report remains unknown; only an explicitly attempted test can be recorded as no reception. WSPR/FT8 history is kept as evidence and is not numerically compared to the model or relabeled as future validation. The log reports counts, measured-minus-predicted SNR bias and mean absolute error, not a calibrated probability. Comparing individual receptions against a monthly median is exploratory; multiple tests across independent dates/conditions are needed before claiming an accuracy improvement.

### Live ionosonde context

The shared **Analyze propagation** button queries GIRO/DIDBase measurements from the latest six hours, independent of the model date. The ionosonde summary card prioritizes recent readings and labels stale, missing and failed results; full station names/codes, quality scores, distances and the time-series chart are under **Ionosonde and NOAA source details**. The panel locates stations using the public catalogue: two nearby sites for each endpoint and each path-third, with alternate instrument codes at selected sites included up to a maximum of eight station queries. GIRO requests are serialized, spaced at least two seconds apart, and never immediately retried. The station catalogue is cached to disk for a week, station readings for ten minutes across paths/restarts, and empty station windows for thirty minutes. HTTP 429 starts a provider-wide cooldown honoring Retry-After (or a fifteen-minute default). During failures, previously retrieved catalogue/readings can be used with explicit warnings and unchanged observation times. No bulk archive downloads or background polling occur.

The table retains each station's latest foF2, hmF2, autoscaling confidence score, source qualifiers, UTC observation time, age, endpoint distances and distance to the sampled surface path. Missing latest values are not silently replaced with older valid ones. Empty query windows, request failures and stale observations (>60 minutes) remain distinct. An overview reports how far the path midpoint is from the nearest selected station with recent usable foF2. The six-hour foF2 chart shows temporal context, including autoscaled measurements that may contain errors.

Station catalogue presence does not establish current operation. A live check on October 17, 2026 found recent measurements from Gakona (GA762) and Idaho National Lab (IF843); Eielson (EI764) was several hours behind. The historical Idaho code AC843, Austin AU930, Dyess DS932 and Boulder BC840 returned no measurements in the checked six-hour windows. These findings are time-specific, not permanent station classifications.

**foF2 is a vertical critical frequency, not the A–B path MUF.** No ionosonde value adjusts P.533 SNR, band ordering or success probability. Geographic proximity is context; it does not establish that a station samples the relevant reflection region.

Measurements are credited to Lowell GIRO Data Center/DIDBase and the named station operators, with links to [GIRO data-use terms](https://giro.uml.edu/didbase/RulesOfTheRoad.html) (CC BY-NC-SA 4.0). No raw measurement exports are added. The working endpoint is `https://lgdc.uml.edu/fastchar/getbest`, linked by the [official FastChar search form](https://giro.uml.edu/didbase/scaled.php); the older `DIDBGetValues` endpoint returned HTTP 404 during verification.

### Reading the 24-hour frequency outlook

**24-hour frequency outlook** appears directly below the three dashboard cards, with no expander to open. The default visualization is a frequency × hour heatmap: rows are analyzed frequencies (highest first), columns are hourly model bins, and color represents monthly median modeled SNR in dB. Both directions appear side by side by default. A shared, labeled color domain is calculated from the complete two-direction model output and remains unchanged when switching between A→B, B→A and **Weaker direction**. Brighter means stronger modeled SNR; it is not an open/closed threshold or a calibrated voice assessment.

Hover any cell for its exact frequency, both directional SNRs, their minimum, and full local/UTC timestamps. A pink outline marks the selected target UTC hour. Optional cell labels show rounded SNR values. Gray cells indicate missing model values; weaker-direction cells remain unknown if either direction is missing.

Use **Heatmap clock** to switch between UTC and the timezone captured when the analysis ran. Local-clock mode orders the 24 UTC model bins by local clock time. Hover reveals the actual dates and timezone abbreviations; the bins do not necessarily make up one local calendar day. Repeated local hours at daylight-saving transitions retain separate cells rather than being merged. These remain monthly hourly predictions, not a daily weather forecast. The original line chart is available in the collapsed **Hourly model direction chart** section, with an A→B / B→A selector. All display controls reuse the current results and make no network requests or new model runs.

### Experimental measurement-support check

The HF card now reports **Supports F2 baseline**, **Disagrees with F2 baseline**, or **Cannot assess**. This is a regional foF2 consistency check only: it does not change the model's R12, SNR, band order, or reliability, and is not a validation of a voice link.

For the strongest candidate by weaker-direction modeled SNR, the app runs a local P.533 diagnostic for A→B at the selected UTC hour and extracts the reported control-point coordinates and monthly foF2 values. Only paths up to 7000 km are supported by this diagnostic. An unavailable diagnostic cannot cancel a successful HF outlook. Measurements are compared with model foF2, never with path MUF or received SNR.

The initial, deliberately conservative screening rules require every reported control region to have a sounding within 300 km; the latest sounding must be within 30 minutes of both now and the rounded-down model UTC hour; CS must be at least 80, or 999 for manual scaling, with no supplied foF2 qualifier. The current target model hour must itself be within 30 minutes of now. A missing latest value, stale or future timestamp, low/unknown confidence, partial coverage or distant station produces **Cannot assess**. No older favorable reading is substituted.

With complete qualifying coverage, all observed foF2 values within ±20% of the corresponding regional monthly baseline produce **Supports F2 baseline**; an excursion outside that tolerance produces **Disagrees with F2 baseline**. These distances, time windows, quality cutoff and tolerance are application heuristics, not calibrated scientific accuracy or success thresholds. Comparisons can span up to 300 km and do not correct for horizontal gradients. The check covers the representative frequency's A→B F2 baseline, not all bands, E-layer propagation, absorption, noise or two-way SNR.

The HF details section shows the control regions, station distances, quality, observation times, measured/model foF2, differences and exclusions. The JSON provenance includes the rules and assessment. The check is reevaluated against the clock on each page rerun so that previously qualifying observations cannot remain labeled current indefinitely. No additional GIRO requests are made for this check.

### Install the local model

The official Linux x86-64 release and all twelve monthly coefficient sets are installed in `.cache/iturhf/v14.3/` (about 137 MB). This workspace is already installed. For a fresh checkout:

```bash
.venv/bin/python scripts/install_itu_model.py
```

The installer downloads only the pinned official v14.3 release/data, verifies sizes and SHA-256 hashes against `scripts/itu_v14_3_manifest.json`, and changes no system libraries. The upstream release binary identifies its P533 library as version 14.2; provenance records that distinction. Other operating systems need a separately supported build. The app does not automatically download model binaries or coefficient files.

Sources: [ITU implementation](https://github.com/ITU-R-Study-Group-3/ITU-R-HF), [P.533](https://www.itu.int/rec/R-REC-P.533/en), [NOAA feeds](https://services.swpc.noaa.gov/), [D-RAP](https://www.swpc.noaa.gov/products/d-region-absorption-predictions-d-rap).

## SNR and coverage

Bands rank by the **stronger of their two direction-specific median received SNR values**, within the selected time window. WSPR uses reported TX power to normalize each measured SNR to 1 W WSPR before computing the medians. Reports without finite TX power cannot contribute a normalized SNR; their coverage counts remain visible. Bands without a usable ranking SNR appear last and are labeled SNR unavailable. FT8 uses measured SNR because archive TX power is unknown; do not compare FT8 and WSPR SNR numerically.

The operator table shows a median SNR and total report count for each direction, UTC-day coverage, B proximity, busiest-day share, and A location flags. A median from a single report is still only one observation. Detailed results include measured SNR, WSPR-normalized SNR, original TX/RX identities, and directional statistics. The old weighted-activity heuristic is diagnostic only and does not determine band order.

**A reverse report is evidence of reception at A, not a predicted SNR at B.** Noise, power, antennas and ionospheric effects can differ by direction. Strong one-way SNR does not establish a two-way or voice link. Confirm channels with ALE/LQA and a voice check. These observations have no calibrated good/bad threshold or success probability.

The heatmap remains a view of **distance-weighted report activity**, not SNR or reliability. Local hours appear first with UTC in parentheses; hover includes separate direction counts. Zero means no reports, not a closed band. The band/hour inspector, hourly coverage export and matched-report export retain directional information. Hourly activity windows are the busiest recorded hours, not predicted openings.

UTC is used internally. The chosen timezone and reference date control local-to-UTC conversion and daylight saving; the reference date does not filter historical dates. Reported position grids may span beyond circle boundaries or be stale. Band labels show rounded MHz and wavelength; precise digital reference frequencies are not suggested voice channels.

## Sources

- **WSPR.live:** WSPR-2 reports only. Both circles are applied before the row limit, independently in each direction, so busy unrelated destinations do not consume the query limit. Two-direction requests can return up to twice the selected per-direction limit. Online queries may time out, be limited, or have incomplete history.
- **CSV upload:** WSPR reports with actual transmitter/receiver identities and located endpoints. Missing coordinates can be derived from valid Maidenhead grids.
- **FT8 archive (cached day):** the approved local HamSCI/Madrigal file for **May 12, 2024 UTC**. It scans in bounded chunks and caches results, with a total archive row limit. No download occurs on Analyze. Both directions are selected within a single scan. The file is 3.13 GB and contains 82.7 million FT8 rows among 87.7 million total records.

**FT8 timestamp limitation:** sampled Unix timestamps differ by five hours from the fields labeled UTC. The reader uses the labeled calendar fields and warns about this unresolved discrepancy. This is not a validated correction or current propagation data.

The separately requested October 2025 download is stored under `.cache/madrigal/2025-10/`; October 2–5, 23 and 27 are not listed by the provider. Downloading these files does not yet enable multi-day analysis. See [archive evaluation](FT8_ARCHIVE_REVIEW.md) and [future validation work](FUTURE_WORK.md). Archive files, local settings and access-logging identity are excluded from version control.

Data credits: [WSPR.live](https://wspr.live/), [HamSCI](https://hamsci.org/data), [CEDAR Madrigal](https://cedar.openmadrigal.org/), PSK Reporter and reporting volunteers. HamSCI archive support: University of Scranton / University of Alabama, NASA grant 80NSSC21K1772.

## Run

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.lock
.venv/bin/streamlit run app.py
```

Restart a running Streamlit process after upgrading helper modules. Verification:

```bash
.venv/bin/python -m unittest discover -s tests -v
```

## Self-hosting

For RHEL 9, Podman and a separate NGINX reverse proxy, see [deployment instructions](deploy/README.md).

## Beta 0.1.0-beta.1

WSPR downloads include all hours. Analyze reuses the current session's history when
the endpoint circles, callsign, direction, lookback and row limit match. The history
caption shows fetch time and query range. **Refresh WSPR history** requests newer
reports and runs the analysis with the current settings. Failed refreshes show an
error and retain the cache for a later retry; they do not display it as fresh results.

**Beta feedback and diagnostics** previews the exact JSON download. Coordinates
and callsigns are included by default and can be omitted. No feedback is sent
automatically. Raw reports, uploads and validation observations are excluded.
Review error messages and source metadata before sharing.

Public deployments keep settings and validation history only for the current
browser session. Export them before leaving or restarting. Desktop installations
retain their local files. Python dependencies are pinned in `requirements.lock`;
the container also pins the Python base image digest. Update the lock deliberately
and rerun the release checks before shipping dependency changes.

## Path details and saved-run PDFs

A path details bar between the map and result cards shows short- and long-path
lengths in kilometers, plus initial true bearings from A to B and B to A. The
long-path headings are also shown. Calculations use the endpoint centers and a
6,371 km mean Earth radius. Coincident or antipodal endpoints have no unique
bearing; a station at an exact pole also has no unique true-north reference.
The HF model continues to calculate the short path.

After analyzing, click **Prepare run PDF** below the path bar, then **Download run
PDF**. The report includes an offline geographic map of the saved endpoint circles
and short great-circle path, coordinates, callsign, antenna and receiver setup,
target-hour modeled SNR, directional 24-hour tables, reception evidence and source
timestamps/errors. It includes coordinates and station details. Generating it makes
no provider requests and does not run the propagation models again.

The PDF always uses the last analyzed snapshot, even after controls are edited.
A new analysis clears the prepared download and replaces that snapshot. In public
mode, download before leaving or restarting; no report is stored permanently on
the server. The PDF map uses bundled public-domain Natural Earth vector outlines,
so it does not require a browser renderer or online map tile service.

Magnetic azimuths are also shown for both directions and both routes. Each
heading uses its departure endpoint's WMM declination at sea level on the target
UTC date: magnetic = true minus east-positive declination, wrapped to 0-360°.
Declinations and coefficient model names appear in the bar and PDF. Bundled WMM
models support 2010-2029; dates outside that interval and magnetic blackout zones
show unavailable rather than an extrapolated azimuth. Weak-field caution zones
are labeled. Antenna orientation inputs remain true bearings.

The PDF's Model assumptions table lists the values actually used: transmit power
in both directions, receiver bandwidth and noise at both endpoints, model month,
frequencies, endpoint antenna dimensions/orientations/ground/losses, and the
smoothed sunspot value/source. Model provenance takes precedence over UI inputs.
References: [WMM](https://www.ncei.noaa.gov/products/world-magnetic-model),
[pyGeoMag](https://pygeomag.readthedocs.io/en/latest/api.html).

PDF modeled tables now reuse the browser presentation: the target-hour summary
has the same MHz / A→B / B→A / Weaker columns, ordering, one-decimal SNR values and
relative cell colors. The 24-hour heatmaps render directly from the browser's
Vega-Lite specification, with the same descending frequency order, selected
local/UTC clock, selected direction view, shared viridis scale and target-hour
outline. SNR labels are always printed in available cells. Heatmaps use a
portrait report page with the two direction panels stacked vertically. Changing
the browser heatmap direction or clock clears a prepared PDF; prepare it again
to export the new presentation without rerunning the analysis. Rendering is local
and requires no browser or online chart service.

The PDF Prepare and Download buttons sit side by side. The report keeps station
identities, endpoint coordinates/search radii, antennas, receiver setup and model
assumptions together in one table. Every report page is portrait.
