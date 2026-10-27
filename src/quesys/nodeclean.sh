#!/bin/bash
####################################################################################################
# script for canceling slurm jobs and cleaning the corresponding job directories
####################################################################################################
#
# IMPORTANT:
#  - jobid and job directory must be given in first and second argument
#  - this script is meant to be called by the quesys.sh script on a computation node
#
####################################################################################################

# get jobid from first argument
jobid=$1

# get jobdir from second argument
jobdir=$2

# cancel slurm job
scancel $jobid

# remove job directory
rm -r -f $jobdir
