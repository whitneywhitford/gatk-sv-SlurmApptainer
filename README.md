# GATK-SV Slurm Apptainer

This is a modification of the Broad Institute's GATK-SV pipeline so that it can run on a slurm-based HPC running apptainer. No root access is necessary, but access to a computer running docker (such as via WSL) is required.

__As of 19/18/2025, the Single Sample workflow in current commit for this repo does not work. However, the instructions for building the docker images and imputs, and running the workflow will all work if you use the WDL files from the latest release of GATK-SV [v1.0.5](https://github.com/broadinstitute/gatk-sv/releases/tag/v1.0.5). Users will have to alter the files in wdl/filestoalter.txt to update issues and add clocktime runtime attributes.__


For technical documentation on GATK-SV, including how to run the pipeline, please refer to the original website [website](https://broadinstitute.github.io/gatk-sv/).

## Setup
### Build docker images
1. Clone the repository on computer running docker
```
git clone https://github.com/whitneywhitford/gatk-sv-SlurmApptainer && cd gatk-sv-SlurmApptainer
```

2. Build the docker images according to instructions at https://broadinstitute.github.io/gatk-sv/docs/advanced/docker/manual. The version of build_docker.py included in this repo creates a list of the docker image tags.
```
python3 scripts/docker/build_docker.py \
	--image-tag <[Date]-[Release Tag]-[Head SHA 8]> \
	--targets all
	--tags-out built_image_refs.txt
```

3. Export built docker images 
```
bash ./export_docker_images.sh built_image_refs.txt 
```

4. Clone the repository on HPC

5. Transfer docker_images directory and ./inputs/values/dockers.json from docker computer to HPC
```
git clone https://github.com/whitneywhitford/gatk-sv-SlurmApptainer && cd gatk-sv-SlurmApptainer
```

6. Build GATK-SV docker images and update dockers.json
```
python3 scripts/docker/apptainer_build.py
```

7. Build other docker images and update dockers.json
```
python3 scripts/docker/pull_docker.py
```

### Building inputs for Single Sample
1. Create test inputs
```
bash scripts/inputs/build_default_inputs.sh -d .
```

2. Download input reference and 1kg ref panel files
```
python3 scripts/inputs/download_singlesamplerefs.py
```

3. Build json for your sample of choice
```
python3 scripts/inputs/build_inputs.py \
  inputs/values \
  inputs/templates/test/GATKSVPipelineSingleSample \
  inputs/build/<OUT_DIR> \
  -a '{ "single_sample" : "<sample_name>", "ref_panel" : "ref_panel_1kg" }'
```

4. Compress dependencies
```
zip wdl/dep.zip wdl/*.wdl
```


## Running Workflow
1. Update inputs/values/slurmApptainer_cromwell.config with account name
```
String account = "<account>"
```

2. Start cromwell server. I actually recommend running this in a different directory and updating the config file path to the full file path
```
java -Xmx6G -Dconfig.file=inputs/values/slurmApptainer_cromwell.config -jar <pathto>/cromwell.jar server
```

3. Submit job via cromshell
In a separate session to the cromwell server. Update inputs/values/gatksv_singleSample_options.json specific to your sample and desired output directory
```
cromshell submit wdl/GATKSVPipelineSingleSample.wdl inputs/build/<OUT_DIR>/gatkSVSinglePipeline.json -op inputs/values/gatksv_singleSample_options.json -d /nesi/project/nesi00322/APPS/gatk-sv/wdl/dep.zip
```


Please report any issues or questions by email to whitney.whitford@auckland.ac.nz