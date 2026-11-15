"""Fetch original trajectories from their providers and export t,x,y,z CSVs."""
import argparse
import csv
import hashlib
import io
import json
from pathlib import Path
import tempfile
import zipfile

import requests

from split_http_zip import SplitRangeReader


HERE = Path(__file__).resolve().parent
CATALOG = json.loads((HERE/'assets/datasets.json').read_text(encoding='utf-8'))
RATM = 'https://api.github.com/repos/tii-racing/drone-racing-dataset/releases/tags/v3.0.0'
NEUROBEM = 'https://download.ifi.uzh.ch/rpg/NeuroBEM/raw_data.zip'
VIRAL = 'https://raw.githubusercontent.com/ntu-aris/ntuviral_gt/master/'
VIRAL_CONTENTS = 'https://api.github.com/repos/ntu-aris/ntuviral_gt/contents/'


def get(url, **kwargs):
    response = requests.get(url, timeout=(20,60), **kwargs)
    response.raise_for_status()
    return response


def fetch_archive_member(dataset, member):
    if dataset == 'ratm':
        group = member.split('/')[0]
        assets = sorted((a for a in get(RATM).json()['assets'] if a['name'].startswith(group+'_zipchunk')),
                        key=lambda a:a['name'])
    else:
        with get(NEUROBEM, headers={'Range':'bytes=0-0','Accept-Encoding':'identity'}, stream=True) as response:
            extent = response.headers.get('Content-Range','')
            if response.status_code!=206 or not extent.startswith('bytes 0-0/'):
                raise OSError('NeuroBEM server did not accept a bounded range request')
            assets=[{'size':int(extent.rsplit('/',1)[1]),'browser_download_url':NEUROBEM}]
    with SplitRangeReader(assets,maximum_transfer=128*1024**2) as stream, zipfile.ZipFile(stream) as archive:
        with archive.open(member) as original:
            chunks=[]
            while chunk:=original.read(1024*1024):
                chunks.append(chunk)
        return b''.join(chunks)


def rows(dataset, raw):
    if dataset == 'neurobem':
        from rosbag_vicon import load_rosbag
        with tempfile.TemporaryDirectory(prefix='rata-neurobem-') as directory:
            path=Path(directory)/'flight.bag'
            path.write_bytes(raw)
            time, position, _=load_rosbag(path)
        return zip(time,*position.T)
    reader = csv.DictReader(io.StringIO(raw.decode('utf-8-sig')))
    if dataset == 'ratm':
        clock, axes, scale = 'timestamp', ('drone_x','drone_y','drone_z'), 1e-6
    else:
        clock, axes, scale = '%time', tuple(f'field.pose.position.{axis}' for axis in 'xyz'), 1e-9
    samples=[]
    origin=None
    for row in reader:
        stamp=int(row[clock])
        if origin is None:
            origin=stamp
        samples.append(((stamp-origin)*scale,*(float(row[key]) for key in axes)))
    return samples


def download(dataset, trajectory, destination, original=None):
    entry=CATALOG[dataset][trajectory]
    if original:
        raw=original.read_bytes()
    elif dataset=='ntu-viral':
        member=entry['member']
        try:
            raw=get(VIRAL+member).content
        except requests.RequestException:
            raw=get(VIRAL_CONTENTS+member, params={'ref':'master'},
                    headers={'Accept':'application/vnd.github.raw+json'}).content
    else:
        raw=fetch_archive_member(dataset,entry['member'])
    digest=hashlib.sha256(raw).hexdigest()
    if digest!=entry['sha256']:
        raise ValueError('The supplied original file does not match the paper dataset SHA-256')
    samples=list(rows(dataset,raw))
    import math
    if len(samples)<4 or any(not all(math.isfinite(x) for x in row) for row in samples) or any(a[0]>=b[0] for a,b in zip(samples,samples[1:])):
        raise ValueError('Expected finite positions and strictly increasing source time')
    destination.parent.mkdir(parents=True,exist_ok=True)
    if destination.exists():
        raise FileExistsError(f'Output already exists: {destination}')
    with destination.open('x',encoding='utf-8',newline='') as stream:
        writer=csv.writer(stream)
        writer.writerow(('t','x','y','z'))
        writer.writerows(samples)
    return destination


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='action',required=True)
    sub.add_parser('list')
    fetch=sub.add_parser('download')
    fetch.add_argument('dataset',choices=CATALOG)
    fetch.add_argument('trajectory')
    fetch.add_argument('--output',type=Path)
    fetch.add_argument('--local-original',type=Path,help='Convert a previously downloaded original CSV or bag')
    args=parser.parse_args()
    if args.action=='list':
        for key, values in CATALOG.items():
            print(key,', '.join(values))
        return
    if args.trajectory not in CATALOG[args.dataset]:
        parser.error('Unknown trajectory ID. Run datasets.py list to see the available IDs.')
    out=args.output or Path('data')/args.dataset/(args.trajectory+'.csv')
    try:
        print(download(args.dataset,args.trajectory,out,args.local_original))
    except (OSError,ValueError,requests.RequestException) as exc:
        parser.exit(1,f'{exc}\n')


if __name__=='__main__':
    main()
