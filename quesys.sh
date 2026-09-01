#!/bin/bash
####################################################################################################
# function collection provided for SLURM based cluster to simplify job submit scripts
####################################################################################################
# name: quesys
# author: Franziska Engel
# modified by: Florian Mast, Simon Blaschke
#
# written by Franziska Engel
# First Version: 2018-11-29
#
# IMPORTANT:
# - "job_start" at the beginning of every job
# - "job_end" at the very end of every job
#
####################################################################################################

# Nodeclean script kills jobs and cleans the corresponding directories directly on the computation node.
# If not set a fallback routine is used.

# location of the nodeclean script
nodeclean=/mnt/software/slurm/nodeclean.sh

####################################################################################################

# get submit dir variable from slurm
submit_dir=$SLURM_SUBMIT_DIR

# get job id from slurm
jobid=$SLURM_JOBID

# get job name from slurm
jobname=$SLURM_JOB_NAME

# get node name from slurm
nodename=$SLURMD_NODENAME

# default setting for working directory (where the folder for the job is created)
default_workdir=/scr/$USER

# default setting for the output directors (files in the job directory are copied to this place at the end of the job)
default_outdir=$submit_dir/${jobid}out

# default maximum filesize for files that are copied to the output directory
default_maxsize=500M

# run at the very beginning of job_start to test certain variables.
#  Some variables can be overwritten in the job submit script.
#  If no values were given there, they are set to the corresponding default values (e.g. global variable \fI$default_workdir\fR for example)
function check_variables(){
  if [ -z "$workdir" ]; then
    workdir=$default_workdir
  elif ! [ -a $workdir ]; then
    echo "invalid user input for variable workdir: $workdir not found!\n workdir set to default ($default_workdir)"
    workdir=$default_workdir
  fi
  default_jobdir=$workdir/$jobid
  export jobdir=$default_jobdir
  if [ -z "$outdir"  ]; then
    outdir=$default_outdir
  elif [[ ! $outdir =~ /.* ]]; then  # $outdir holds a relative path --> add submit directory as prefix
    outdir=$submit_dir/$outdir
  fi
  if [ -z "$maxsize" ]; then
    maxsize=$default_maxsize
  elif ! [[ "$maxsize" =~ ^[0-9]+[ckMGTP]?$ ]]; then
    echo "invalid user input for maxsize!\n set to default (1000k)!"
    maxsize=$default_maxsize
  fi
}

# used and necessary to setup the environment for a job, including:
#  - store the start time
#  - create cleanup.script
#  - create job directory (where the job is run)
#  - copy everything specified in \fI$input\fR to job directory
#  - go to jobdirectory for the actual calcualtion
#  - include path to jobdirectory in systems $PATH variable so local executables (given in \fI$input\fR) are accessible
function job_start(){
  # test certain variables
  check_variables
  # store timestamp invariable starttime
  start=$(echo $(date +%s))
  # write short information about the job
  introduction
  # create job directory ( example: $workdir/$SLURM_JOBID )
  mkdir -p $jobdir
  # copy everything here that is needed
  cp -ar $input $jobdir
  # create cleanup script
  setup_cleanup $submit_dir/cleanup.$jobid
  # create outdir
  mkdir -p $outdir
  # enter job directory
  cd $jobdir
  # remove crab
  # add path to jobdir to access binaries that where copied here
  PATH=$jobdir:$PATH
}

# called at the very end of the job it calls \fIgather\fR to copy the content of the $jobdir directory to the $outdir directory.
#  After that it removes \fI$jobdir\fR and the \fIcleanup script\fR.
function job_end(){
  # copy stuff back
  gather
  # return to working directory
  cd $workdir
  # remove the jobs directory
  rm -r $jobdir
  # remove cleanup script
  rm $submit_dir/cleanup.$jobid
  # print statistics
  statistics
}

# copies content of \fI$jobdir\fR to \fI$outdir\fR with some restrictions
#  If the variable \fI$ouput\fR is a string of zero length (or contains only whitespace)
#   - delete files that are binaries
#   - delete files that are larger than the limit given by variable $maxsize (can be specified in runjob script)
#  else (\fI$output\fR has a NON ZERO length)
#   - copy every file listed in \fI$output\fR to \fI$outdir\fR
function gather(){
  # cd to jobdir
  cd $jobdir
  # check variable $output
  if [ -z "$output" ] ; then # variable NOT SET
    # remove binaries
    find . -type f -executable -exec rm -r {} \;
    # search for files that are larger than $maxsize and remove those
    find . -size +${maxsize} -exec rm -r {} \;
    # copy remaining content of job directory to specified output directory
    cp -R $jobdir/. $outdir
  else # variable SET
    # copy files given in $output to $outdir
    cp -r $output $outdir
  fi
}

# creates the cleanup script for the job.
#  - while the job is running the script remains in the submit directory
#  - the script prompts if the content of \fI$jobdir\fR should be copied to \fI$outdir\fR (type Y or y to copy)
#  - binaries are removed before the copy process
#  - the job is canceled
#  - \fI$jobdir\fR is removed if possible otherwise an email is sent to root to request the cleanup
function setup_cleanup(){
  write_cleanup > $submit_dir/cleanup.$jobid
  chmod +x $submit_dir/cleanup.$jobid
}

# writes the content of the cleanup script
function write_cleanup(){
  echo "#!/bin/bash"
  if [[ $copy_on_cleanup =~ ^[Yy][(es)(ES)]?|[Tt][(RUE)(rue)]?$ ]] ; then
    echo "  ssh -q $(whoami)@$(hostname) 'find $jobdir/. -type f -executable -exec rm -r {} \;'"
    echo "  scp -r -q -o LogLevel=QUIET $nodename:$jobdir/. $outdir"
  fi
  if [ -n "$nodeclean" -a -r $nodeclean ] ; then
    echo "if ssh -q $(whoami)@$(hostname) exit ; then"
    echo "  ssh $(whoami)@$(hostname) 'bash $nodeclean $jobid $jobdir'"
    echo "  echo 'Cleaned up Job $jobid succesfully!'"
    echo "else"
    echo "  scancel $jobid"
    echo "  echo -e \"subject:cleanup request \nto:psigl@uni-mainz.de \n\n please remove directory:\n$jobdir\" | sendmail -F $(whoami)@$(hostname) psigl@uni-mainz.de"
    echo "  echo 'Cleanup failed! Cancelled Job $jobid and sent cleanup request to the administrator.'"
    echo "fi"
  else
    echo "echo 'Nodeclean script missing! Using old routine for cleanup ...'"
    echo "scancel $jobid"
    echo "if ssh -q $(whoami)@$(hostname) exit ; then"
    echo "  ssh $(whoami)@$(hostname) 'rm -r -f $jobdir'"
    echo "  echo 'Cleaned up Job $jobid succesfully!'"
    echo "else"
    echo "  echo -e \"subject:cleanup request \nto:psigl@uni-mainz.de \n\n please remove directory:\n$jobdir\" | sendmail -F $(whoami)@$(hostname) psigl@uni-mainz.de"
    echo "  echo 'Cleanup failed! Cancelled Job $jobid and sent cleanup request to the administrator.'"
    echo "fi"
  fi
  echo "rm $submit_dir/cleanup.$jobid"
}

# Prints all sorts of information about the job (usually called at the end of a job, and part of the \fIjob_end\fR function)
function statistics(){
  end=$(echo $(date +%s))
  elapsed=$((end - start))
  get_memory_stat
  printf "%s\n" "=================================================================================="
  printf "%s\n" " Statistics for job $jobname (ID: $jobid)"
  printf "%s\n" "----------------------------------------------------------------------------------"
  print_stat " Job ID:" $jobid
  print_stat " Job name:" $jobname
  print_stat " Node:" $nodename
  print_stat " Submitted:" "$(date -d "$(ssh 192.168.1.109 -T "sacct -j ${SLURM_JOB_ID} -o submit -n")" -R)"
  print_stat " Started:" "$(date --date=@$start -R)"
  print_stat " Finished:" "$(date --date=@$end -R)"
  print_stat " Duration:" "$(convertsecs $elapsed)"
  print_stat " Average CPU time:" "$(sstat -n -j ${SLURM_JOBID}.batch --fields=AveCPU)"
  print_stat " Maximum RSS:" "$(convert_kbyte $rss $rss_u)"
  print_stat " Maximum VM:" "$(convert_kbyte $vm $vm_u)"
  print_stat " Max. Disk Write:" "$(convert_kbyte $hd "B")"
  printf "%s\n" "=================================================================================="
}

# print short information about the job (usually more or less directly after the job started (called by \fIjob_start\fR))
function introduction(){
  printf "%s\n" "=================================================================================="
  printf "%s\n" " Information for job $jobname (JOBID: $jobid)"
  printf "%s\n" "----------------------------------------------------------------------------------"
  print_stat " Current location:" "$(whoami)@$(hostname):$(pwd)"
  print_stat " Work directory:" $workdir
  print_stat " Job directory:" $jobdir
  print_stat " Output directory:" $outdir
  print_stat " Job ID:" $jobid
  print_stat " Job name:" $jobname
  print_stat " Node:" $nodename
  print_stat " Submitted:" "$(date -d "$(ssh 192.168.1.109 -T "sacct -j ${SLURM_JOB_ID} -o submit -n")" -R)"
  print_stat " Started:" "$(date --date=@$start -R)"
  printf "%s\n" "=================================================================================="
}

# format and command to print lines in \fIstatistics\fR and \fIintroduction\fR
#  it takes two arguments:
#   - the fields name (e.g. "Job name" )
#   - the corresponding value (e.g. "my-job" ) 
#  the use of a print function like this avoids redundance of formatting information and simplifies the tabular arrangement
function print_stat() {
  field=$1
  value=$2
  printf "%-30s %50s\n" "$field" "$value"
}

# innquires the hard disk and virtual memory used by the job from SLURM and preprocesses the variable for conversion from kilo to mega, giga or terrabyte (see convert_kbyte function)
function get_memory_stat() {
  rss_o=$(sstat -n -j ${SLURM_JOBID}.batch --fields=MaxRSS%20)
  vm_o=$(sstat -n -j ${SLURM_JOBID}.batch --fields=MaxVMSize%20)
  hd_o=$(sstat -n -j ${SLURM_JOBID}.batch --fields=MaxDiskWrite%20)
  rss_k=${rss_o//[ ]/}
  vm_k=${vm_o//[ ]/}
  hd_k=${hd_o//[ ]/}
  rss=${rss_k//[!0-9]/}
  vm=${vm_k//[!0-9]/}
  hd=${hd_k//[!0-9]/}
  rss_u=${rss_k//[0-9.]/}
  vm_u=${vm_k//[0-9.]/}
  hd_u=${hd_k//[0-9.]/}
}

# extract the number of days hours minutes and seconds from a given number of seconds.
#  There are:
#   - 86400 s in a day
#   -  3600 s in an hour
#   -    60 s in a minute
#  stepvise division allows extraction of the desired information.
#  The function returns an expression with the format
#   - d day(s) HH:MM:SS
#  The alternative format
#   - d day(s) HH hour(s) MM minute(s) SS seconds(s)
#  is commented out.
function convertsecs() {
  ((d=${1}/86400))
  ((h=(${1}%86400)/3600))
  ((m=(${1}%3600)/60))
  ((s=${1}%60))
#  echo "$d day(s) $h hour(s) $m min(s) $s second(s)" # alternative format
  echo "$d day(s) $(printf "%02d:%02d:%02d" $h $m $s) (HH:MM:SS)"
}

# converts a value given in B, KB, MB, GB or TB to a float with the largest possible unit prefix where the value remainus larger than 1.
#  - the function takes two arguments: value ($1) and original unit ($2)
#  - the given value is divided by 1024 recursively until the result is less than 1024.
#  - the unit prefix is chosen according to the number stored in \fI$cntr\fR.
#  - the resulting floating point value followed by the unit is returned (formatted)
function convert_kbyte() {
  x=${1}
  case ${2} in
    "B"|"") cntr=0 ;;
    "K"|"KB") cntr=1 ;;
    "M"|"MB") cntr=2 ;;
    "G"|"GB") cntr=3 ;;
    "T"|"TB") cntr=4 ;;
  esac
  while [ $cntr -lt 4 ]
  do
    if (( $(echo "$x < 1024" | bc -l) )); then break ; fi
    cntr=$((cntr+1))
    x=$(awk -v x=$x 'BEGIN { print x / 1024 }')
    if [ $cntr -ge 4 ]; then break ; fi
  done
  case $cntr in
    0) unit="B" ;;
    1) unit="KB" ;;
    2) unit="MB" ;;
    3) unit="GB" ;;
    4) unit="TB" ;;
  esac
  echo "$(printf "%3.3f" ${x}) $unit"
}

####################################################################################################
# CFOUR specific procedures
####################################################################################################

# files that are usually no longer needed after the cfour calculation and do not need to be preserved. They are removed before everything is copied to \f$outdir\fR.
#  This list is probably not yet complete.
skip_cfour_files=" IIII IIJJ IJIJ IJKL GENBAS"

# stuff that is -- or should be -- done before 'xcfour' is executed. Currently it only calls \fIready_zmat\fR.
function setup_cfour(){
  ready_zmat
}

# copy file stored in variable $zmat to ZMAT so cfour is will find its input file
function ready_zmat(){
  cp $zmat ZMAT
}

# stuff that is done before gathering the files for the output folder
function finalize_cfour(){
  # remove integral files that are no longer needed
  rm -f $skip_cfour_files
}

