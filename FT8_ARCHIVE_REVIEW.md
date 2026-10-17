# HamSCI FT8 archive evaluation — 2026-10-16

## Verified live

- Archive: https://cedar.openmadrigal.org/, instrument 8308 (Amateur Radio Signal Report).
- Discovery for 2026-09-01 through 2026-10-16 returned HTTP 200 with no experiments. This is archive coverage, not evidence of no radio reception.
- Discovery for September 2025 returned available daily experiments with gaps. The documented May 12, 2024 example also exists (experiment 100012089).
- Its default file is `experiments4/2024/rsd/12may24/rsd2024-05-12.01.hdf5`, kindat 17578.
- A one-day isprint request, filtered by transmitter and receiver bounding boxes, returned HTTP 200 but an error message instead of data: the source file exceeds 200 MB, so dynamic file creation is unavailable and download-as-is is required. The response was only 199 bytes.
- A headers-only request for the HDF5 file returned Content-Length **3,134,405,611 bytes** (3.13 GB / 2.92 GiB). No reception-data file was downloaded at this initial headers-only stage.

The provider documents this check before applying isprint filters:
https://cedar.openmadrigal.org/docs/name/rt_webServices.html

## Remaining uncertainty

No published numeric request-rate allowance was established. Do not describe this service as unlimited. File availability varies by day, and the mode and date coverage must be checked for each additional archive file; actual FT8 records were verified in the approved sample below. The mixed archive contains PSK Reporter, WSPRNet and RBN records; do not treat the complete archive as FT8.

## Approved download and prototype results

The user approved this single file, which was downloaded completely and cached locally. SHA-256: `488536cc12266181a6c6d6170e8a5a62cd4924dfd3d87ded7f9047c421ce0594`. The reader scans at most 100,000 rows per chunk, clips both map circles, selects FT8, and retains a bounded set of newest reports. Never silently fall back to a bulk download, expand the date range, or bypass the provider's server-side size restriction. No additional archive days or live PSK Reporter requests are enabled.

Any eventual UI should show archive dates, source, selected mode, sample/download size, missing coverage, and transmitter/receiver counts. Keep FT8 evidence separate from WSPR power-normalized scores: FT8 reports generally lack the transmitter power required for 1 W normalization.

Download logging requires an operator-supplied name, email, and affiliation. Values were supplied and authorized in chat for this evaluation; they are not embedded in source code or this document.

References:

- https://hamsci.org/data
- https://madrigalweb.readthedocs.io/en/latest/api/
- https://spase-metadata.org/HamSCI/NumericalData/HFRadars/AmateurRadioSignalReport/AmateurRadioSignalReport.html

The app now offers **FT8 archive (cached day)** alongside WSPR sources. It reads this local file only, with no download on Analyze. WSPR and FT8 are analyzed separately; FT8 has no power-normalized SNR or SNR score modifier.

The file contains **87,742,401 total rows**, including **82,668,465 FT8 rows**. A real scan with TX center 61.5, -149.0 / radius 50 km and destination center 32.5, -97.0 / radius 600 km found **8 FT8 reports**, one transmitter (KL5NE), two receivers, and only 14 MHz (20m). That is sparse evidence for one day, not current or general path availability. The initial scan took about 65 seconds; filtered results are cached in the app.

## Timestamp discrepancy

The archive metadata labels its year/month/day/hour/minute/second fields as universal time. In 20 sampled rows distributed across the file, `ut1_unix` is consistently **18,000 seconds (five hours) ahead** of those labeled UTC fields. The prototype uses the labeled UTC calendar fields and displays an explicit warning. Which source representation is correct has not been independently established; do not interpret hourly FT8 results as validated timing. The sampled offsets and chosen time basis are retained in provenance.

The download manifest and local scan provenance are under `.cache/madrigal/`, excluded from version control. No access-logging identity is embedded in application source.

## October 2025 request

The user subsequently authorized downloading all available October 2025 days if no practical alternate historical source was found. Live instrument discovery lists 25 days; October 2–5, 23, and 27 are absent. This is an incomplete month, not 31 days of coverage. The first two files have advertised sizes of 3,051,955,865 and 3,988,213,067 bytes; the full total remains unverified.

`scripts/download_october_archive.py` downloads those public default files serially into `.cache/madrigal/2025-10/`, retaining partial files for retries and recording sizes, hashes and completion status in `manifest.json`. `download.log` records progress. A private identity JSON file supplies the previously authorized access-logging values; they are not embedded in the script or manifest. The script validates each HDF5 reception table and leaves 10 GiB free on disk. HTTP range resume is used only when the server supports it; otherwise a partial download restarts.

PSK Reporter's MQTT distribution supports filtering live reports by mode, callsign and grids, making it a possible source for collecting future regional history. It does not establish an alternate October 2025 backfill. No alternate public October 2025 source with practical filtered historical downloads was verified. See https://www.mqtt.pskreporter.info/ and https://pskreporter.info/pskdev.html .

Starting the download does not add October files to the current single-day app reader. Multi-day indexing and independent verification of each file's FT8 coverage and timestamp fields are still required before using this month in analysis.

## A/B endpoint and SNR update

The planner now accepts both A→B and B→A reports by default for WSPR, CSV and the cached FT8 day. Actual transmitter/receiver roles, SNR and TX power are retained; geographic endpoint columns and report direction are added separately. A selected callsign applies to the station at A in either role. Both reported endpoint centers must fall within their circles. Unlocated records are excluded.

Band ordering now uses the stronger of the two direction-specific median SNR values: measured FT8 SNR, or WSPR SNR normalized with the actual reported TX power to 1 W WSPR. Report volume and the old activity heuristic no longer determine ordering. Directional medians, usable/total counts and coverage flags stay visible. Reverse SNR describes reception at A, not an inferred reception at B. The activity heatmap remains an explicitly labeled coverage view.

A real browser check using the original Alaska/Dallas circles retained 144 FT8 reports across the cached day. At UTC hour 01 ±2 hours, the overview showed 14 MHz (20m): A→B 8 reports, median −12 dB; B→A 39 reports, median −16 dB. For 21 MHz (15m), only B→A had matches: 3 reports, median −17 dB. These values use the unresolved calendar timestamp basis described above and do not establish present conditions or a voice link.
