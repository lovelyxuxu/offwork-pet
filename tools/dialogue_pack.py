"""Validate or build a data-only character dialogue file."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from offwork_pet.dialogue import atomic_json, read_pack, validate_pack


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('source', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--from-draft', action='store_true')
    args = parser.parse_args()
    if args.from_draft:
        data = json.loads(args.source.read_text(encoding='utf-8'))
        if data.get('format') != 'offwork-pet.dialogue-draft/v1':
            raise ValueError('Expected a dialogue draft')
        data['format'] = 'offwork-pet.dialogue/v1'
        data.pop('status', None)
        data['version'] = '1.0.0'
        validate_pack(data)
    else:
        data = read_pack(args.source)
    if args.output:
        atomic_json(args.output, data)
    print(f"VALID {data['id']} {data['version']}: {len(data['scenes'])} scenes, "
          f"{sum(len(scene['lines']) for scene in data['scenes'])} lines")


if __name__ == '__main__':
    main()
