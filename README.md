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
│       └── quesys.py
└── .venv/   # local virtual environment for development
```

## Notes

- The package expects a SLURM environment with typical variables such as `SLURM_JOB_ID`, `SLURM_JOB_NAME`, and `SLURM_SUBMIT_DIR`.
- `jobdir` is used as the execution directory on the compute node and `outdir` is used for collected results.
- The library uses `humanfriendly.parse_size` for file-size validation and conversion.
