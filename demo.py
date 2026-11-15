"""Run one archive demo and render the decoded result in a standalone HTML file."""
import argparse
import csv
import hashlib
from itertools import zip_longest
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


HERE = Path(__file__).resolve().parent
ENGINE = HERE / 'runtime' / ('rata-engine.exe' if sys.platform == 'win32' else 'rata-engine')


def write_viewer(page, label, replay):
    content = json.dumps(dict(label=label, **replay), separators=(',', ':'), allow_nan=False)
    template = (HERE/'assets/replay.html').read_text(encoding='utf-8')
    page.write_text(template.replace('<!--REPLAY_DATA-->', content.replace('<', '\\u003c'), 1), encoding='utf-8')


def run_demo(args, engine):
    if args.dataset:
        from datasets import CATALOG, download
        if not args.trajectory or args.trajectory not in CATALOG.get(args.dataset, {}):
            raise ValueError('Choose a trajectory from `python datasets.py list`')
        input_file = Path('data') / args.dataset / (args.trajectory+'.csv')
        if not input_file.exists():
            download(args.dataset,args.trajectory,input_file)
        label = f'{args.dataset} / {args.trajectory}'
    else:
        if args.trajectory:
            raise ValueError('--trajectory requires --dataset')
        input_file = args.input
        label = input_file.stem
    cmd = [str(engine),'encode',str(input_file.resolve()),str(args.output.resolve()),'--mode',args.mode,
           '--max-bytes',str(args.max_bytes),'--position-rms-m',str(args.position_rms_mm/1000),
           '--speed-rms-mps',str(args.speed_rms_mps)]
    status = subprocess.run(cmd).returncode
    if status:
        if status==2 and (args.output/'result.json').is_file():
            print(f'No qualifying archive was found. Solver details: {args.output / "result.json"}')
        return status
    report = json.loads((args.output/'result.json').read_text(encoding='utf-8'))
    archive = args.output/'archive.rata'
    assert hashlib.sha256(archive.read_bytes()).hexdigest()==report['archive_sha256']
    print(f"{report['bytes']:,} bytes | {1000*report['position_rms_m']:.3f} mm | {report['speed_rms_mps']:.4f} m/s")
    if args.encode_only:
        print(archive.resolve())
        return 0
    with tempfile.TemporaryDirectory(prefix='rata-view-') as work:
        samples = Path(work)/'series.json'
        subprocess.run([str(engine),'replay',str(input_file.resolve()),str(archive.resolve()),str(samples)],check=True)
        replay = json.loads(samples.read_text(encoding='utf-8'))
    page=args.output/'replay.html'
    write_viewer(page, label, dict(report=report, **replay))
    print(page.resolve())
    return 0


def decode_archive(args, engine):
    if args.output.exists():
        raise FileExistsError(f'Choose a new output directory: {args.output}')
    with tempfile.TemporaryDirectory(prefix='rata-decode-') as work:
        samples = Path(work)/'series.json'
        grid = Path(work)/'decoded.csv'
        subprocess.run([str(engine), 'decode', str(args.archive.resolve()), str(samples),
                        '--samples-csv', str(grid)], check=True)
        replay = json.loads(samples.read_text(encoding='utf-8'))
        args.output.mkdir(parents=True)
        path = args.output/'decoded.csv'
        shutil.copyfile(grid, path)
    page = args.output/'replay.html'
    write_viewer(page, args.archive.name, replay)
    print(path.resolve())
    print(page.resolve())
    return 0


def trajectory_rows(stream):
    rows = csv.reader(stream)
    if [field.strip() for field in next(rows, [])] != ['t', 'x', 'y', 'z']:
        raise ValueError(f'{stream.name} must have a t,x,y,z header')
    previous = -math.inf
    for row in rows:
        if not row:
            continue
        values = tuple(map(float, row))
        if len(values) != 4 or not all(map(math.isfinite, values)) or values[0] <= previous:
            raise ValueError(f'Expected four finite values with increasing time in {stream.name}, line {rows.line_num}')
        previous = values[0]
        yield values


def compare_trajectories(args):
    count = 0
    squared_error = peak_error = peak_clock_error = 0.0
    clock_tolerance = 1e-8
    with args.input.open(encoding='utf-8-sig', newline='') as original, args.decoded.open(
        encoding='utf-8-sig', newline=''
    ) as decoded:
        for count, (reference, restored) in enumerate(
            zip_longest(trajectory_rows(original), trajectory_rows(decoded)), 1
        ):
            if reference is None or restored is None:
                raise ValueError('The original and decoded files contain different numbers of samples')
            clock_error = abs(reference[0] - restored[0])
            if clock_error > clock_tolerance:
                raise ValueError(f'Timestamps differ by {clock_error:g} s at sample {count}')
            peak_clock_error = max(peak_clock_error, clock_error)
            error = math.dist(reference[1:], restored[1:])
            squared_error += error*error
            peak_error = max(peak_error, error)
    if not count:
        raise ValueError('Both files must contain samples')
    if not math.isfinite(squared_error):
        raise ValueError('Position error exceeds the supported numeric range')
    report = dict(samples=count, position_rms_m=math.sqrt(squared_error/count),
                  maximum_position_error_m=peak_error, maximum_timestamp_difference_s=peak_clock_error,
                  timestamp_tolerance_s=clock_tolerance)
    args.output.mkdir(parents=True)
    path = args.output/'comparison.json'
    path.write_text(json.dumps(report, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    print(f"{count:,} samples | position RMS {report['position_rms_m']*1000:.3f} mm | "
          f"maximum {peak_error*1000:.3f} mm | max time difference {peak_clock_error:g} s")
    print(path.resolve())
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument('--output', type=Path, required=True, help='New output directory')
    runtime = argparse.ArgumentParser(add_help=False)
    runtime.add_argument('--engine', type=Path, default=ENGINE)
    encode = argparse.ArgumentParser(add_help=False)
    inputs=encode.add_mutually_exclusive_group(required=True)
    inputs.add_argument('--input',type=Path,help='CSV with t,x,y,z in seconds and meters')
    inputs.add_argument('--dataset',choices=('ratm','ntu-viral','neurobem'))
    encode.add_argument('--trajectory')
    encode.add_argument('--max-bytes',type=int,required=True)
    encode.add_argument('--position-rms-mm',type=float,required=True)
    encode.add_argument('--speed-rms-mps',type=float,required=True)
    encode.add_argument('--encode-only', action='store_true',
                        help='Write the archive and encoding report without a comparison page')
    modes = parser.add_subparsers(dest='mode', required=True)
    modes.add_parser('rata', parents=[common, runtime, encode], help='Encode with points and curves')
    modes.add_parser('point', parents=[common, runtime, encode], help='Encode with points')
    decode = modes.add_parser('decode', parents=[common, runtime], help='Reconstruct a CSV and replay page from an archive')
    decode.add_argument('--archive', type=Path, required=True)
    compare = modes.add_parser('compare', parents=[common], help='Compare positions and timestamps in two CSV files')
    compare.add_argument('--input', type=Path, required=True, help='Original t,x,y,z CSV')
    compare.add_argument('--decoded', type=Path, required=True, help='Reconstructed t,x,y,z CSV')
    args=parser.parse_args()
    try:
        if args.mode == 'compare':
            return compare_trajectories(args)
        engine = args.engine.resolve()
        if not engine.is_file():
            raise FileNotFoundError('Extract the engine for your platform next to demo.py, or set --engine')
        if args.mode == 'decode':
            return decode_archive(args, engine)
        if args.max_bytes<1 or not all(math.isfinite(x) and x>0 for x in (args.position_rms_mm,args.speed_rms_mps)):
            parser.error('Byte and RMS limits must be positive and finite')
        return run_demo(args, engine)
    except (OSError,ValueError,subprocess.CalledProcessError) as exc:
        parser.exit(1,f'{exc}\n')


if __name__=='__main__':
    raise SystemExit(main())
