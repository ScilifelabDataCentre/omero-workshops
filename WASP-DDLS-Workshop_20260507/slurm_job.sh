#!/bin/bash
#SBATCH --job-name=<job-name>
#SBATCH --account=<account>
#SBATCH --reservation=<reservation>
#SBATCH --gpus-per-node=<gpus-per-node>
#SBATCH --time=01:00:00
#SBATCH --output=<output>
#SBATCH --error=<error>
#SBATCH --export=ALL

module load GCC/12.3.0 CUDA/12.1.1 Python/3.11.3-GCCcore-12.3.0 Java/11.0.27

source <env-path>/bin/activate
cd <workspace>
export OMERODIR=$(pwd)/OMERO.server-5.6.17-ice36

python cellpose_omero_nfs.py --dataset-id "$1" --output-dir "$2"
