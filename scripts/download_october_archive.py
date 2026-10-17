"""Download the user-requested October 2025 public Madrigal archives serially.

Identity is read from a private JSON file, never written into the manifest/log.
Run with the project Python: script IDENTITY_JSON [--output DIRECTORY].
"""
import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import time

import h5py
import requests

BASE = 'https://cedar.openmadrigal.org'
ROOT = Path(__file__).resolve().parents[1]


def save(path, data):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(data, indent=2) + '\n')
    temporary.replace(path)


def download(session, item, identity, output):
    filename = Path(item['file']).name
    if not re.fullmatch(r'rsd2025-10-\d{2}\.\d+\.hdf5', filename):
        raise ValueError('Unexpected archive filename')
    target = output / filename
    partial = output / (filename + '.part')
    params = dict(fileName=item['file'], fileType=-2, **identity)
    size = item.get('bytes')
    if not size:
        with session.head(BASE + '/getMadfile.cgi', params=params,
                          timeout=(20, 90), allow_redirects=False) as response:
            response.raise_for_status()
            size = int(response.headers.get('Content-Length', 0))
    if not size or size < 1024:
        raise ValueError('Missing or invalid archive size')
    item['bytes'] = size
    if not (target.exists() and target.stat().st_size == size):
        start = partial.stat().st_size if partial.exists() else 0
        if start > size:
            raise ValueError('Partial file exceeds expected size')
        if shutil.disk_usage(output).free < size - start + 10 * 1024**3:
            raise ValueError('Insufficient free disk space (10 GiB reserve)')
        if start < size:
            headers = {'Range': f'bytes={start}-'} if start else {}
            with session.get(BASE + '/getMadfile.cgi', params=params, headers=headers,
                             stream=True, timeout=(20, 90), allow_redirects=False) as response:
                response.raise_for_status()
                if response.status_code == 206:
                    expected_range = f'bytes {start}-'
                    if not response.headers.get('Content-Range', '').startswith(expected_range):
                        raise ValueError('Unexpected resume range')
                    mode = 'ab' if start else 'wb'
                elif response.status_code == 200:
                    start, mode = 0, 'wb'
                else:
                    raise ValueError('Unexpected download response')
                received, last = start, time.monotonic()
                with partial.open(mode) as stream:
                    for block in response.iter_content(1024 * 1024):
                        received += len(block)
                        if received > size:
                            raise ValueError('Download exceeds expected size')
                        stream.write(block)
                        if time.monotonic() - last >= 20:
                            print(f"{item['date']}: {received/size:.0%} ({received/1e9:.2f} GB)", flush=True)
                            last = time.monotonic()
        if partial.stat().st_size != size:
            raise ValueError('Incomplete download')
        # Validate the container before publishing a completed file.
        with h5py.File(partial, 'r') as archive:
            if 'Data/Table Layout' not in archive:
                raise ValueError('Missing reception table')
        os.replace(partial, target)
    with h5py.File(target, 'r') as archive:
        item['rows'] = len(archive['Data/Table Layout'])
    with target.open('rb') as stream:
        item['sha256'] = hashlib.file_digest(stream, 'sha256').hexdigest()
    item['status'] = 'complete'
    print(f"{item['date']}: complete, {size/1e9:.2f} GB", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('identity_json', type=Path)
    parser.add_argument('--output', type=Path, default=ROOT / '.cache/madrigal/2025-10')
    args = parser.parse_args()
    identity = json.loads(args.identity_json.read_text())
    if set(identity) != {'user_fullname', 'user_email', 'user_affiliation'}:
        raise ValueError('Identity requires name, email and affiliation')
    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    rows = list(csv.reader((output / 'experiments.csv').read_text().splitlines()))
    experiments = [r for r in rows if r[7:9] == ['2025', '10']]
    manifest_path = output / 'manifest.json'
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {
        'source': BASE, 'month': '2025-10', 'status': 'running',
        'missing_days': sorted(set(range(1, 32)) - {int(r[9]) for r in experiments}),
        'files': [],
    }
    manifest['status'] = 'running'
    save(manifest_path, manifest)
    with requests.Session() as session:
        for row in experiments:
            day = f'2025-10-{int(row[9]):02d}'
            item = next((x for x in manifest['files'] if x['date'] == day), None)
            if item is None:
                response = session.get(BASE + '/getExperimentFilesService.py',
                                       params={'id': row[0]}, timeout=(20, 90))
                response.raise_for_status()
                files = [f for f in csv.reader(response.text.splitlines())
                         if len(f) >= 6 and f[3] == '1' and f[5] == '0']
                if len(files) != 1:
                    raise ValueError('Expected one default public file')
                item = {'date': day, 'experiment': row[0], 'file': files[0][0], 'status': 'pending'}
                manifest['files'].append(item)
            for attempt in range(3):
                try:
                    download(session, item, identity, output)
                    save(manifest_path, manifest)
                    break
                except (requests.RequestException, OSError, ValueError) as error:
                    # Request error strings contain the logging identity in their URL.
                    item['status'] = 'failed'
                    item['error_type'] = type(error).__name__
                    save(manifest_path, manifest)
                    print(f'{day}: attempt {attempt + 1} failed ({type(error).__name__})', flush=True)
                    if attempt == 2:
                        manifest['status'] = 'failed'
                        save(manifest_path, manifest)
                        return 1
                    time.sleep(5)
        manifest['status'] = 'complete'
        save(manifest_path, manifest)
    print('All available October 2025 days downloaded.', flush=True)
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (requests.RequestException, OSError, ValueError) as error:
        print(f'Download stopped ({type(error).__name__}); partial files retained.', flush=True)
        raise SystemExit(1)
