"""Apply the reviewed patch only when all target files match the supplied snapshot."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
from datetime import datetime, timezone


def digest(path):
    return hashlib.sha256(path.read_text(encoding='utf-8-sig').strip().encode('utf-8')).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--project', required=True, type=Path)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    package = Path(__file__).resolve().parent
    project = args.project.resolve()
    if not (project / 'app').is_dir() or not (project / 'tests').is_dir():
        parser.error('Project must contain app/ and tests/.')
    manifest = json.loads((package / 'manifest.json').read_text(encoding='utf-8'))
    conflicts = []
    pending = []
    for item in manifest:
        relative = Path(item['path'])
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('Invalid manifest path')
        target = project / relative
        source = package / 'files' / relative
        if digest(source) != item['updated_sha256']:
            raise ValueError('Package content mismatch: ' + str(relative))
        if target.exists() and digest(target) == item['updated_sha256']:
            continue
        current = digest(target) if target.exists() else None
        if current != item['baseline_sha256']:
            conflicts.append(str(relative))
        else:
            pending.append((source, target, relative))
    if conflicts:
        raise SystemExit('No files changed. Snapshot conflicts:\n' + '\n'.join(conflicts))
    if args.check:
        print(f'Compatible: {len(pending)} files to apply. No files changed.')
        return
    if not pending:
        print('Patch already applied.')
        return
    backup = project / ('stage34_backup_' + datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f'))
    for source, target, relative in pending:
        if target.exists():
            destination = backup / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, destination)
    for source, target, relative in pending:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    print(f'Applied {len(pending)} files. Original files backed up to {backup}')


if __name__ == '__main__':
    main()
