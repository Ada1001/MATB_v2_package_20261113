"""Create a portable source+weights deployment directory and ZIP."""
import argparse
from pathlib import Path
import shutil

try:
    from .common import HERE, PROJECT, load_artifact, sha, write_json
except ImportError:
    from common import HERE, PROJECT, load_artifact, sha, write_json


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--artifact', required=True)
    p.add_argument('--output-dir', required=True)
    args = p.parse_args()
    load_artifact(args.artifact)
    out = Path(args.output_dir).resolve()
    if out.exists() or out.with_suffix('.zip').exists():
        raise FileExistsError('Choose a new bundle output path')
    if HERE == out or HERE in out.parents:
        raise ValueError('Bundle must be outside online_agmnet source folder')
    out.mkdir(parents=True)
    for path in HERE.iterdir():
        if path.suffix in {'.py', '.md', '.txt'}:
            shutil.copy2(path, out/path.name)
    shutil.copytree(args.artifact, out/'artifact')
    ignore = shutil.ignore_patterns('__pycache__', '*.pyc', '.git')
    shutil.copytree(PROJECT/'src', out/'vendor/src', ignore=ignore)
    shutil.copytree(PROJECT/'TSMNet/spdnets', out/'vendor/TSMNet/spdnets', ignore=ignore)
    for name in ('LICENSE', 'README.md'):
        upstream = PROJECT/'TSMNet'/name
        if upstream.is_file():
            shutil.copy2(upstream, out/'vendor/TSMNet'/name)
    write_json(out/'bundle_manifest.json', {str(p.relative_to(out)).replace('\\', '/'): sha(p)
                                           for p in out.rglob('*') if p.is_file()})
    archive = shutil.make_archive(str(out), 'zip', out.parent, out.name)
    print('Deployment directory:', out)
    print('ZIP:', archive)
    print('From extracted directory: python replay.py --artifact artifact --data-dir /path/to/COG-BCI --output-dir replay_results')


if __name__ == '__main__':
    main()
