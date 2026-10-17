"""Install the official ITU v14.3 Linux x86-64 release into this project only."""
import hashlib
import json
from pathlib import Path
import platform
import requests

ROOT = Path(__file__).resolve().parents[1]
HOME = ROOT / '.cache/iturhf/v14.3'


def install():
    if platform.system() != 'Linux' or platform.machine() not in ('x86_64', 'AMD64'):
        raise RuntimeError('This installer supports Linux x86-64 only.')
    manifest = json.loads(Path(__file__).with_name('itu_v14_3_manifest.json').read_text())
    total = sum(item['size'] for item in manifest.values())
    print(f'Official ITU release v14.3: {total / 1e6:.1f} MB. Installing in {HOME}', flush=True)
    for name, item in manifest.items():
        binary = name in ('ITURHFProp', 'libp533.so', 'libp372.so')
        destination = HOME / name if binary else HOME / 'data' / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists() and hashlib.sha256(destination.read_bytes()).hexdigest() == item['sha256']:
            continue
        url = ('https://github.com/ITU-R-Study-Group-3/ITU-R-HF/releases/download/v14.3/' + name if binary else
               'https://raw.githubusercontent.com/ITU-R-Study-Group-3/ITU-R-HF/v14.3/P533/Data/' + name)
        partial = destination.with_suffix(destination.suffix + '.part')
        try:
            digest = hashlib.sha256()
            size = 0
            with requests.get(url, timeout=(15, 90), stream=True) as response:
                response.raise_for_status()
                with partial.open('wb') as output:
                    for chunk in response.iter_content(1024 * 1024):
                        size += len(chunk)
                        if size > item['size']:
                            raise ValueError(f'Unexpected size for {name}')
                        digest.update(chunk)
                        output.write(chunk)
            if size != item['size'] or digest.hexdigest() != item['sha256']:
                raise ValueError(f'Integrity verification failed: {name}')
            partial.replace(destination)
        finally:
            partial.unlink(missing_ok=True)
        print(f'Installed {name}', flush=True)
    (HOME / 'ITURHFProp').chmod(0o755)
    (HOME / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print('ITU model ready.', flush=True)


if __name__ == '__main__':
    install()
