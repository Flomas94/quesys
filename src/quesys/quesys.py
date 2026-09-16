import getpass
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from humanfriendly import format_size, parse_size

TIMEZONE = "Europe/Berlin"

LOGIN_NODE = "192.168.1.109"

NODECLEAN = "/mnt/software/slurm/nodeclean.sh"


class JobSettings:
    def __init__(self):
        self.user = getpass.getuser()
        self.jobid = os.getenv("SLURM_JOB_ID", "")
        self.jobname = os.getenv("SLURM_JOB_NAME", "")
        self.node = os.getenv("SLURMD_NODENAME", "")
        self.submitdir = os.getenv("SLURM_SUBMIT_DIR", "")
        self.workdir = f"/scr/{self.user}"
        self.jobdir = f"{self.workdir}/{self.jobid}"
        self.outdir = f"{self.submitdir}/{self.jobid}out"
        self.maxfilesize = "500M"

        self.input: list[str] = []
        self.output: list[str] = []

        self.starttime: datetime
        self.endtime: datetime
        self.submittime: datetime

        self.copy_on_cleanup = False


def check_slurm():

    if not os.getenv("SLURM_JOB_ID"):
        print("No SLURM job running! Exiting.")
        sys.exit(1)


def ssh_command(command: str) -> str:
    full_command = ["ssh", LOGIN_NODE, "-T", command]
    result = subprocess.run(full_command, capture_output=True, text=True, check=True)
    return result.stdout


def print_stat(description: str, value: object) -> None:
    print(f"{description:<30}{value!s:>50}")


def _check_variables(settings: JobSettings) -> JobSettings:

    if not os.path.exists(settings.workdir):
        print(f"Invalid workdir! {settings.workdir} was not found.")
        sys.exit(1)

    if not os.path.isabs(settings.outdir):
        settings.outdir = f"{settings.submitdir}/{settings.outdir}"

    jobdir_real = os.path.realpath(settings.jobdir)
    outdir_real = os.path.realpath(settings.outdir)

    if jobdir_real == outdir_real:
        print(f"Invalid output directory! jobdir and outdir are identical: {settings.jobdir}")
        sys.exit(1)

    try:
        if os.path.commonpath([jobdir_real, outdir_real]) == jobdir_real:
            print(f"Invalid output directory! outdir overlaps with jobdir: {settings.outdir}")
            sys.exit(1)
    except ValueError:
        pass

    if not re.match(r"^[0-9]+[ckMGTP]?$", settings.maxfilesize):
        print(f"Invalid maximum file size! ({settings.maxfilesize})")
        sys.exit(1)

    return settings


def job_start(settings: JobSettings):
    check_slurm()
    settings = _check_variables(settings)
    settings.starttime = datetime.now(ZoneInfo(TIMEZONE))
    settings.submittime = datetime.fromisoformat(
        ssh_command(f"sacct -j {settings.jobid} -o submit -n").strip()
    ).replace(tzinfo=ZoneInfo("Europe/Berlin"))
    introduction(settings)
    os.makedirs(settings.jobdir)
    for input_object in settings.input:
        input_object_name = os.path.basename(os.path.normpath(input_object))
        destination_path = os.path.join(settings.jobdir, input_object_name)
        if os.path.isdir(input_object):
            shutil.copytree(
                input_object, destination_path, symlinks=True, dirs_exist_ok=True
            )
        else:
            shutil.copy2(input_object, destination_path, follow_symlinks=False)
    write_cleanup(settings)
    os.makedirs(settings.outdir)
    os.chdir(settings.jobdir)
    os.environ["PATH"] = ":".join([settings.jobdir, os.environ["PATH"]])


def job_end(settings: JobSettings):

    settings.endtime = datetime.now(ZoneInfo(TIMEZONE))
    gather(settings)
    os.chdir(settings.workdir)
    shutil.rmtree(settings.jobdir)
    os.remove(os.path.join(settings.submitdir, f"cleanup.{settings.jobid}"))
    statistics(settings)


def introduction(settings: JobSettings):

    print(
        "=================================================================================="
    )
    print(f" Information for job {settings.jobname} (JOBID: {settings.jobid})")
    print(
        "----------------------------------------------------------------------------------"
    )
    print_stat(
        " Current location:",
        f"{settings.user}@{settings.node}:{os.path.abspath(os.getcwd())}",
    )
    print_stat(" Work directory:", settings.workdir)
    print_stat(" Job directory:", settings.jobdir)
    print_stat(" Output directory:", settings.outdir)
    print_stat(" Job ID:", settings.jobid)
    print_stat(" Job name:", settings.jobname)
    print_stat(" Node:", settings.node)
    print_stat(" Submitted:", settings.submittime.strftime("%a, %d %b %Y %H:%M:%S %z"))
    print_stat(" Started:", settings.starttime.strftime("%a, %d %b %Y %H:%M:%S %z"))
    print(
        "=================================================================================="
    )


def _parse_slurm_value(raw_value: str) -> int:
    value = raw_value.strip()
    if not value:
        return 0

    try:
        return int(parse_size(value, binary=True))
    except ValueError:
        return 0


def _format_duration(delta: timedelta) -> str:
    total_seconds = int(delta.total_seconds())
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    days, hours = divmod(hours, 24)
    return f"{days} day(s) {hours:02d}:{minutes:02d}:{seconds:02d} (HH:MM:SS)"


def statistics(settings: JobSettings) -> None:
    rss, vm, hd = get_memory_stat(settings)
    delta = settings.endtime - settings.starttime

    avg_cpu = subprocess.run(
        ["sstat", "-n", "-j", f"{settings.jobid}.batch", "--fields=AveCPU"],
        text=True,
        check=True,
        capture_output=True,
    ).stdout.strip()

    print(
        "=================================================================================="
    )
    print(f" Statistics for job {settings.jobname} (JOBID: {settings.jobid})")
    print(
        "----------------------------------------------------------------------------------"
    )
    print_stat(" Job ID:", settings.jobid)
    print_stat(" Job name:", settings.jobname)
    print_stat(" Node:", settings.node)
    print_stat(" Submitted:", settings.submittime.strftime("%a, %d %b %Y %H:%M:%S %z"))
    print_stat(" Started:", settings.starttime.strftime("%a, %d %b %Y %H:%M:%S %z"))
    print_stat(" Finished:", settings.endtime.strftime("%a, %d %b %Y %H:%M:%S %z"))
    print_stat(" Duration:", _format_duration(delta))
    print_stat(" Average CPU time:", avg_cpu)
    print_stat(" Maximum RSS:", format_size(rss, binary=True))
    print_stat(" Maximum VM:", format_size(vm, binary=True))
    print_stat(" Max. Disk Write:", format_size(hd, binary=True))
    print(
        "=================================================================================="
    )


def get_memory_stat(settings: JobSettings) -> tuple[int, int, int]:
    fields = ["MaxRSS", "MaxVMSize", "MaxDiskWrite"]
    values: list[int] = []

    for field in fields:
        result = subprocess.run(
            [
                "sstat",
                "-n",
                "-j",
                f"{settings.jobid}.batch",
                f"--fields={field}%20",
            ],
            text=True,
            capture_output=True,
            check=True,
        )
        values.append(_parse_slurm_value(result.stdout))

    return values[0], values[1], values[2]


def write_cleanup(settings: JobSettings):
    cleanup_path = os.path.join(settings.submitdir, f"cleanup.{settings.jobid}")
    lines = ["#!/bin/bash"]

    if settings.copy_on_cleanup:
        lines.extend(
            [
                f"ssh -q {settings.user}@{settings.node} 'find {settings.jobdir}/. -type f -executable -exec rm -r {{}} \\;'",
                f"scp -r -q -o LogLevel=QUIET {settings.node}:{settings.jobdir}/. {settings.outdir}",
            ]
        )

    if os.path.isfile(NODECLEAN) and os.access(NODECLEAN, os.R_OK):
        lines.extend(
            [
                f"if ssh -q {settings.user}@{settings.node} exit; then",
                f"  ssh {settings.user}@{settings.node} 'bash {NODECLEAN} {settings.jobid} {settings.jobdir}'",
                f"  echo 'Cleaned up Job {settings.jobid} succesfully!'",
                "else",
                f"  scancel {settings.jobid}",
                f"  echo 'Cleanup failed! Cancelled Job {settings.jobid}.'",
                f"  echo 'Please remove directory {settings.jobdir} on node {settings.node} manually!'",
                "fi",
            ]
        )
    else:
        lines.extend(
            [
                "echo 'Nodeclean script missing! Using old routine for cleanup ...'",
                f"scancel {settings.jobid}",
                f"if ssh -q {settings.user}@{settings.node} exit; then",
                f"  ssh {settings.user}@{settings.node} 'rm -r -f {settings.jobdir}'",
                f"  echo 'Cleaned up Job {settings.jobid} succesfully!'",
                "else",
                f"  echo 'Cleanup failed! Cancelled Job {settings.jobid}.'",
                f"  echo 'Please remove directory {settings.jobdir} on node {settings.node} manually!'",
                "fi",
            ]
        )

    lines.append(f"rm {cleanup_path}")

    with open(cleanup_path, "w", encoding="utf-8") as cleanup_file:
        cleanup_file.write("\n".join(lines) + "\n")

    os.chmod(cleanup_path, 0o744)


def gather(settings: JobSettings) -> None:
    os.chdir(settings.jobdir)

    if settings.output:
        output_objects = settings.output
    else:
        maxfilesize = parse_size(settings.maxfilesize, binary=True)

        for root, _, files in os.walk("."):
            for filename in files:
                file_path = os.path.join(root, filename)

                if (
                    os.access(file_path, os.X_OK)
                    or os.path.getsize(file_path) > maxfilesize
                ):
                    os.remove(file_path)

        output_objects = os.listdir(".")

    for output_object in output_objects:
        output_object_name = os.path.basename(os.path.normpath(output_object))
        destination_path = os.path.join(settings.outdir, output_object_name)

        if os.path.isdir(output_object):
            shutil.copytree(
                output_object,
                destination_path,
                symlinks=True,
                dirs_exist_ok=True,
            )
        else:
            shutil.copy2(
                output_object,
                destination_path,
                follow_symlinks=False,
            )
