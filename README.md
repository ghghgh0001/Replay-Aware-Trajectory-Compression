# Replay-Aware Trajectory Archiving

The demos encode one flight and generate an offline replay page. `rata` offers full samples, time-uniform subsets, and curves. `point` offers the two point representations. The executable uses the paper's archive format, replay reference, and local error checks. This entry searches one absolute byte cap and applies both requested RMS limits during the search and after decoding.

## Source code availability

The prepublication distribution provides executables for Windows x64 and Ubuntu 22.04 x86-64, dataset download scripts, and replay demos. The encoder and decoder source code will be made available in this repository upon formal publication of the article.

## Setup

Download the runtime for your platform from [Releases](../../releases) and extract it in the directory containing this README. Keep the entire `runtime` directory together.

| Platform | Release asset | Executable |
| --- | --- | --- |
| Windows x64 | `rata-engine-windows-x64.zip` | `runtime/rata-engine.exe` |
| Ubuntu 22.04 x86-64 | `rata-engine-ubuntu-22.04-x86_64.tar.gz` | `runtime/rata-engine` |

Use Python 3.11 or later for the download and demo scripts. On Ubuntu 22.04, Python 3.10 as installed by the distribution also supports these scripts. The runtime itself has its own Python dependencies.

```powershell
python -m pip install -r requirements.txt
python datasets.py list
python datasets.py download ntu-viral eee_03
```

On Ubuntu, extract the runtime with `tar -xzf rata-engine-ubuntu-22.04-x86_64.tar.gz`. Create and activate a Python environment with `python3 -m venv .venv && source .venv/bin/activate`, then use the same commands. Install the Ubuntu package `python3-venv` if the venv module is absent.

The download script retrieves the original trajectory from its provider and checks the SHA-256 against the paper manifest before writing a local `t,x,y,z` CSV. The catalog includes 30 RATM flights, two NeuroBEM Vicon records, and 16 NTU VIRAL Leica prism records. Timestamp differences are converted to seconds, and positions are in meters. If an upstream server or proxy does not support ZIP range requests, obtain the specified source member from the provider and pass it to `datasets.py download ... --local-original PATH`.

- [RATM v3.0.0](https://github.com/tii-racing/drone-racing-dataset/releases/tag/v3.0.0)
- [NeuroBEM Vicon source](https://download.ifi.uzh.ch/rpg/NeuroBEM/)
- [NTU VIRAL Leica prism CSVs](https://github.com/ntu-aris/ntuviral_gt)

## Run the two demos

```powershell
python demo.py rata --dataset ntu-viral --trajectory eee_03 --max-bytes 15324 --position-rms-mm 4 --speed-rms-mps 0.1 --output demo-rata
python demo.py point --dataset ntu-viral --trajectory eee_03 --max-bytes 15324 --position-rms-mm 4 --speed-rms-mps 0.1 --output demo-point
```

### Choose another flight from the paper

You can run any of the 48 listed flights by changing command-line arguments. Run `python datasets.py list` to see the exact IDs grouped by dataset. The dataset keys are `ratm` (30 flights), `neurobem` (two flights), and `ntu-viral` (16 flights).

For example, to switch from NTU VIRAL `eee_03` to RATM `12p`, change `--dataset` to `ratm`, `--trajectory` to `12p`, and pick a new output directory. These commands use 10,042 bytes, twice the paper's 5,021-byte reference cap for this flight.

```powershell
python datasets.py download ratm 12p
python demo.py rata --dataset ratm --trajectory 12p --max-bytes 10042 --position-rms-mm 4 --speed-rms-mps 0.1 --output ratm-12p-rata
python demo.py point --dataset ratm --trajectory 12p --max-bytes 10042 --position-rms-mm 4 --speed-rms-mps 0.1 --output ratm-12p-point
```

The demo downloads the flight automatically when the local input is absent. Set `--max-bytes`, `--position-rms-mm`, and `--speed-rms-mps` for the selected flight and use a fresh `--output` directory on each run. The cap is an absolute byte count. The paper's supplementary record table gives a reference cap for each flight. The demo applies the requested position and velocity limits to the finished file, so a tight cap or strict limits may yield no qualifying archive.

To try your own flight, replace `--dataset ... --trajectory ...` with `--input your-flight.csv`. The CSV must have the header `t,x,y,z`, strictly increasing timestamps in seconds, and finite Cartesian coordinates in meters.

### Read the results

By default each successful run writes `archive.rata`, `result.json`, and `replay.html`. The comparison page shows original sample markers from the input and a separate curve sampled from the decoder. Drag to rotate the 3D path, scroll or pinch to zoom, and shift-drag to pan. Use **Reset view** to return to the initial framing. The time slider and playback button update both plots. Samples ahead of the cursor appear as unconnected hollow markers. Played samples are filled, and the solid replay curve extends up to the cursor. Reported RMS values come from continuous scoring against the quintic reference built from the original samples. The option `--speed-rms-mps` bounds the RMS norm of the **velocity vector error**. Archive bytes include the full container and all decoding information.

If no file qualifies, the command exits with code `2` and records the solver outcome in `result.json`. Status `0` denotes completion of the arithmetic graph search, `1` a solver limit, and `2` proof of infeasibility within that arithmetic graph or an insufficient cap for its fixed overhead. The final archive also passes the decoded byte, RMS, and local checks. Encoding builds candidates from the input and can take several minutes on long or fragmented records.

## Verify encoding, decoding, and error independently

1. Run one of the encoding examples above. Open `demo-rata/replay.html` to see the overlay, and keep `demo-rata/archive.rata` for decoding. The optional `--encode-only` flag skips the overlay.
2. Copy **only** `archive.rata` to a new folder in File Explorer or to another machine. You can keep the original CSV and encoding report wherever you prefer. For the example below, put the copy at `demo-transfer/flight.rata`. The decoder reads only the archive for flight data.

```powershell
python demo.py decode --archive demo-transfer/flight.rata --output decoded-flight
```

The generated `decoded-flight/decoded.csv` has the original `t,x,y,z` format with one reconstructed position for each stored original sample time. You can also open `decoded-flight/replay.html` to view the reconstructed flight between samples. The same decode command handles archives from either encoding mode.

3. To measure the reconstruction error yourself, supply the actual path of the original CSV and the decoded CSV. This command uses only those two files and runs without the engine.

```powershell
python demo.py compare --input "D:/your-data/eee_03.csv" --decoded decoded-flight/decoded.csv --output compared-flight
```

The comparison requires matching sample counts and timestamps within 10 nanoseconds. It reports the position root-mean-square error and maximum three-dimensional position error over those samples, plus the maximum timestamp difference, in `compared-flight/comparison.json`. The encoding report separately measures replay between samples, using position and velocity against the paper's continuous reference.

## Query specific times

Create `times.csv` with header `t` and one query time in seconds per row, relative to the first input sample.

```powershell
runtime\rata-engine.exe query demo-rata\archive.rata times.csv decoded.csv
```

On Ubuntu, run `./runtime/rata-engine query demo-rata/archive.rata times.csv decoded.csv`.

This returns `t,x,y,z,vx,vy,vz`. If the requested batch includes a time outside continuous support, the command exits with an error before writing the output CSV.
