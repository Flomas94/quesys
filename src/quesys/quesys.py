import getpass
import os
import re
import shlex
import shutil
import subprocess
import sys
from collections.abc import Sequence
from configparser import ConfigParser
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from humanfriendly import format_size, parse_size

DEFAULT_CONFIG_PATHS = (
    "quesys.conf",
    "quesys.ini",
    os.path.expanduser("~/.config/quesys/config.ini"),
)
DEFAULT_TIMEZONE = "UTC"
DEFAULT_WORKDIR = "{submitdir}"
DEFAULT_JOBDIR = "{workdir}/{jobid}"
DEFAULT_OUTDIR = "{submitdir}/{jobid}out"
DEFAULT_MAXFILESIZE = "500M"
DEFAULT_NODECLEAN = str(Path(__file__).with_name("nodeclean.sh"))
DEFAULT_SACCT_CLUSTER = ""


class JobSettings:
    def __init__(self, config_file: str | os.PathLike[str] | None = None):
        self.user = getpass.getuser()
        self.jobid = os.getenv("SLURM_JOB_ID", "")
        self.jobname = os.getenv("SLURM_JOB_NAME", "")
        self.node = os.getenv("SLURMD_NODENAME", "")
        self.submitdir = os.getenv("SLURM_SUBMIT_DIR", "")
        self.timezone = DEFAULT_TIMEZONE
        self.login_node = ""
        self.nodeclean = DEFAULT_NODECLEAN
        self.sacct_cluster = DEFAULT_SACCT_CLUSTER
        self.workdir = ""
        self.jobdir = ""
        self.outdir = ""
        self.maxfilesize = DEFAULT_MAXFILESIZE
        self.ssh_commands: dict[str, bool] = {}

        self.input: list[str] = []
        self.output: list[str] = []

        self.starttime: datetime
        self.endtime: datetime
        self.submittime: datetime

        self.copy_on_cleanup = False
        self._load_config(config_file)

    def _load_config(self, config_file: str | os.PathLike[str] | None) -> None:
        parser = ConfigParser(interpolation=None)
        if config_file is not None:
            config_path = Path(config_file)
            if not config_path.is_file():
                raise FileNotFoundError(
                    f"Quesys configuration not found: {config_path}"
                )
            parser.read(config_path)
        else:
            for path in DEFAULT_CONFIG_PATHS:
                if os.path.isfile(path):
                    parser.read(path)
                    break

        settings = parser["quesys"] if parser.has_section("quesys") else {}
        self.timezone = settings.get("TIMEZONE", self.timezone)
        self.login_node = settings.get("LOGIN_NODE", self.login_node)
        self.nodeclean = settings.get("NODECLEAN", self.nodeclean)
        self.sacct_cluster = settings.get("SACCT_CLUSTER", self.sacct_cluster).strip()
        self.maxfilesize = settings.get("maxfilesize", self.maxfilesize)

        self._set_paths(
            settings.get("workdir", DEFAULT_WORKDIR),
            settings.get("jobdir", DEFAULT_JOBDIR),
            settings.get("outdir", DEFAULT_OUTDIR),
        )

        if parser.has_section("commands"):
            for command, value in parser.items("commands"):
                try:
                    self.ssh_commands[command] = parser.getboolean("commands", command)
                except ValueError as error:
                    raise ValueError(
                        f"Invalid SSH setting for command '{command}': {value!r}"
                    ) from error

    def _set_paths(
        self, workdir_template: str, jobdir_template: str, outdir_template: str
    ) -> None:
        self.workdir = self._format_path(workdir_template)
        self.jobdir = self._format_path(jobdir_template)
        self.outdir = self._format_path(outdir_template)
        if not os.path.isabs(self.outdir):
            self.outdir = os.path.join(self.submitdir, self.outdir)

    def _format_path(self, value: str) -> str:
        try:
            return value.format(
                user=self.user,
                jobid=self.jobid,
                jobname=self.jobname,
                node=self.node,
                submitdir=self.submitdir,
                workdir=self.workdir,
            )
        except KeyError as error:
            raise ValueError(f"Unknown path placeholder: {error.args[0]}") from error

    def requires_ssh(self, command: str) -> bool:
        return self.ssh_commands.get(command, False)


def check_slurm():

    if not os.getenv("SLURM_JOB_ID"):
        print("No SLURM job running! Exiting.")
        sys.exit(1)


def run_command(
    command: Sequence[str], settings: JobSettings, command_name: str
) -> str:
    if settings.requires_ssh(command_name):
        full_command = ["ssh", settings.login_node, "-T", shlex.join(command)]
    else:
        full_command = list(command)
    result = subprocess.run(full_command, capture_output=True, text=True, check=True)
    return result.stdout


def print_stat(description: str, value: object) -> None:
    print(f"{description:<30}{value!s:>50}")


def _sacct_command(settings: JobSettings) -> list[str]:
    command = ["sacct", "-X"]
    if settings.sacct_cluster:
        command.extend(["-M", settings.sacct_cluster])
    command.extend(["-j", settings.jobid, "-o", "submit", "-n"])
    return command


def _check_variables(settings: JobSettings) -> JobSettings:

    if not os.path.exists(settings.workdir):
        print(f"Invalid workdir! {settings.workdir} was not found.")
        sys.exit(1)

    jobdir_real = os.path.realpath(settings.jobdir)
    outdir_real = os.path.realpath(settings.outdir)

    if jobdir_real == outdir_real:
        print(
            f"Invalid output directory! jobdir and outdir are identical: {settings.jobdir}"
        )
        sys.exit(1)

    try:
        if os.path.commonpath([jobdir_real, outdir_real]) == jobdir_real:
            print(
                f"Invalid output directory! outdir overlaps with jobdir: {settings.outdir}"
            )
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
    settings.starttime = datetime.now(ZoneInfo(settings.timezone))
    settings.submittime = datetime.fromisoformat(
        run_command(
            _sacct_command(settings),
            settings,
            "sacct",
        ).strip()
    ).replace(tzinfo=ZoneInfo(settings.timezone))
    introduction(settings)
    os.makedirs(settings.jobdir, exist_ok=True)
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
    os.makedirs(settings.outdir, exist_ok=True)
    os.chdir(settings.jobdir)
    os.environ["PATH"] = ":".join([settings.jobdir, os.environ["PATH"]])


def job_end(settings: JobSettings):

    settings.endtime = datetime.now(ZoneInfo(settings.timezone))
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
                f"ssh -q {settings.user}@{settings.node}.local 'find {settings.jobdir}/. -type f -executable -exec rm -r {{}} \\;'",
                f"scp -r -q -o LogLevel=QUIET {settings.node}.local:{settings.jobdir}/. {settings.outdir}",
            ]
        )

    if os.path.isfile(settings.nodeclean) and os.access(settings.nodeclean, os.R_OK):
        lines.extend(
            [
                f"if ssh -q {settings.user}@{settings.node}.local exit; then",
                f"  ssh {settings.user}@{settings.node}.local 'bash {settings.nodeclean} {settings.jobid} {settings.jobdir}'",
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
                f"if ssh -q {settings.user}@{settings.node}.local exit; then",
                f"  ssh {settings.user}@{settings.node}.local 'rm -r -f {settings.jobdir}'",
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
