# quesys

Quesys is a small Python package for managing SLURM jobs and collecting job output in a predictable way.

## Features

- validate the SLURM environment
- create and manage a per-job working directory
- copy input data into the job directory
- generate a cleanup script
- collect output files into an output directory
- print job metadata and summary statistics

## Installation

Install from the local source tree:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
```

Or install from PyPI once published:

```bash
pip install quesys
```

## Example usage

```python
from quesys import JobSettings, job_start, job_end

settings = JobSettings()
settings.input = ["./input_data"]
settings.output = ["results", "plots"]
settings.maxfilesize = "500M"

job_start(settings)
# run your computation here
job_end(settings)
```

## Configuration

`JobSettings` reads an optional INI file. An explicitly supplied file must exist:

```python
settings = JobSettings(config_file="quesys.conf")
```

Without an explicit path, the first existing file among `quesys.conf`,
`quesys.ini`, and `~/.config/quesys/config.ini` is used. Values in the
`[quesys]` section can override the generic package defaults:

```ini
[quesys]
TIMEZONE = UTC
LOGIN_NODE = login.example.org
NODECLEAN = /opt/slurm/nodeclean.sh
SACCT_CLUSTER = my-cluster
workdir = /scratch/{user}
jobdir = {workdir}/{jobid}
outdir = {submitdir}/{jobid}out
maxfilesize = 500M

[commands]
# Commands set to true are executed through LOGIN_NODE.
sacct = true
```

`sacct` is always called with `-X`, so only the job allocation is queried.
When `SACCT_CLUSTER` is set, its value is passed as `-M <cluster>`; if it is
empty or omitted, no cluster argument is added.

Cluster-specific configuration files such as `quesys.conf` should not be
committed to the repository. The package itself contains no cluster-specific
login node, node-cleanup path, or work directory.

Path values support `{user}`, `{jobid}`, `{jobname}`, `{node}`, `{submitdir}`,
and `{workdir}`. By default only `sacct` requires the login node; commands
listed as `false` run locally. The cleanup script's SSH connection to the
compute node is independent of this setting.

## Package layout

```text
quesys/
├── pyproject.toml
├── requirements.txt
├── setup.cfg
├── README.md
├── src/
│   └── quesys/
│       ├── __init__.py
│       ├── nodeclean.sh
│       └── quesys.py
└── .venv/   # local virtual environment for development
```

## Notes

- The package expects a SLURM environment with typical variables such as `SLURM_JOB_ID`, `SLURM_JOB_NAME`, and `SLURM_SUBMIT_DIR`.
- `jobdir` is used as the execution directory on the compute node and `outdir` is used for collected results.
- The library uses `humanfriendly.parse_size` for file-size validation and conversion.
