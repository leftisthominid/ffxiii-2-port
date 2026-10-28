"""Map XIII-2 Steam assets without consulting Xbox DLC manifests or archives."""
import argparse
import collections
import gzip
import json
import ntpath
import sys
from pathlib import Path

MOVIES = ('tm_vazb_038', 'tm_vazb_013', 'em_vazb_030', 'em_vazb_055', 'tm_tevb_050')
PREFIXES = ('mot/mon/sk_m974_sz/', 'scene/lay/scene00131/',
            'event/schedule/ev_cszb_', 'sys/dlc/')


def describe(path, entry):
    archive, sector, unpacked, packed, original, code, kind = entry
    return dict(path=path, original_path=original, archive=ntpath.basename(archive),
                byte_offset=sector * 2048, byte_offset_hex=hex(sector * 2048),
                unpacked_bytes=unpacked, stored_bytes=packed, code=code, file_type=kind)


def summarize(files, sources):
    return dict(
        indexed_paths=len(files), sources=sources,
        archive_counts=dict(sorted(collections.Counter(ntpath.basename(e[0]) for e in files.values()).items())),
        selected_assets=[describe(k, v) for k, v in sorted(files.items()) if k.startswith(PREFIXES)],
        dlc_movie_candidates={name: describe('movie_win/' + name + '_us.bik', files['movie_win/' + name + '_us.bik'])
                              for name in MOVIES if 'movie_win/' + name + '_us.bik' in files},
        limitations=['An archive index establishes asset locations, not original Xbox DLC package membership.',
                     'No Xbox DLC manifests or payloads were used to construct this map.',
                     'Indexed PC movies still require format-aware decoding and runtime overlay support.'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--steam', type=Path, help='Steam installation, opened read-only')
    source.add_argument('--index', type=Path, help='Collector steam-archive-index.json.gz')
    parser.add_argument('--out', type=Path, required=True, help='JSON report outside the Steam install')
    args = parser.parse_args()
    if args.steam:
        if args.out.resolve().is_relative_to(args.steam.resolve()):
            parser.error('--out must be outside the source Steam installation')
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'converters'))
        from x2dlc.gameindex import GameIndex
        index = GameIndex(str(args.steam), 'win32')
        try: report = summarize(index.files, index.sources)
        finally: index.close()
    else:
        with gzip.open(args.index, 'rt', encoding='utf-8') as stream:
            data = json.load(stream)
        if data.get('platform') != 'win32': parser.error('--index must describe the Steam version')
        report = summarize(data['files'], data['sources'])
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(f"Mapped {report['indexed_paths']} Steam paths; located {len(report['dlc_movie_candidates'])} DLC movie candidates.")
    print(args.out)


if __name__ == '__main__':
    main()
