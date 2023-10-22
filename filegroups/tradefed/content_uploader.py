#!/usr/bin/env python3

#  Copyright (C) 2023 The Android Open Source Project
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.

"""The script to upload generated artifacts from build server to CAS."""
import argparse
import dataclasses
import glob
import json
import logging
import os
import subprocess
import tempfile
import time


@dataclasses.dataclass
class ArtifactConfig:
    """Configuration of an artifact to be uploaded to CAS.

    Attributes:
        source_path: path to the artifact that relative to the root of source code.
        unzip: true if the artifact should be unzipped and uploaded as a directory.
        exclude_filters: a list of regular expressions for files that are excluded from uploading.
    """
    source_path: str
    unzip: bool
    exclude_filters: list[str] = dataclasses.field(default_factory=list)

CAS_UPLOADER_PREBUILT_PATH = 'tools/tradefederation/prebuilts/'
CAS_UPLOADER_PATH = 'tools/content_addressed_storage/prebuilts/'
CAS_UPLOADER_BIN = 'casuploader'

UPLOADER_TIMEOUT_SECS = 600 # 10 minutes
LOG_PATH = 'logs/cas_uploader.log'

# Configurations of artifacts will be uploaded to CAS.
# TODO(b/298890453) Add artifacts after this script is attached to build process.
ARTIFACTS = [
    # test_suite targets
    ArtifactConfig('android-cts.zip', True),
    ArtifactConfig('android-gts.zip', True),
    ArtifactConfig('android-mts.zip', True),
    ArtifactConfig('android-pts.zip', True),
    ArtifactConfig('android-vts.zip', True),
    ArtifactConfig('art-host-tests.zip', True),
    ArtifactConfig('bazel-test-suite.zip', True),
    ArtifactConfig('host-unit-tests.zip', True),
    ArtifactConfig('general-tests.zip', True),
    ArtifactConfig('general-tests_configs.zip', True),
    ArtifactConfig('general-tests_host-shared-libs.zip', True),
    ArtifactConfig('google-tradefed.zip', True),
    ArtifactConfig('robolectric-tests.zip', True),

    # Device target artifacts
    ArtifactConfig('androidTest.zip', True),
    ArtifactConfig('device-tests.zip', True),
    ArtifactConfig('*-img-*zip', False)
]

# Artifacts will be uploaded if the config name is set in arguments `--experiment_artifacts`.
# These configs are usually used to upload artifacts in partial branches/targets for experiment
# purpose.
EXPERIMENT_ARTIFACT_CONFIGS = []

def _get_client():
    bin_path = os.path.join(CAS_UPLOADER_PATH, CAS_UPLOADER_BIN)
    if os.path.isfile(bin_path):
        logging.info('Using client at %s', bin_path)
        return bin_path
    client = glob.glob(CAS_UPLOADER_PREBUILT_PATH + '**/' + CAS_UPLOADER_BIN, recursive=True)
    if not client:
        raise ValueError('Could not find casuploader binary')
    logging.info('Using client at %s', client[0])
    return client[0]

def _get_env_var(key: str, default=None, check=False):
    value = os.environ.get(key, default)
    if check and not value:
        raise ValueError(f'Error: the environment variable {key} is not set')
    return value


def _parse_additional_artifacts(args) -> list[ArtifactConfig]:
    additional_artifacts = []
    for config in args.experiment_artifacts:
        if config not in EXPERIMENT_ARTIFACT_CONFIGS:
            logging.warning('Ignore invalid experiment_artifacts: %s', config)
        else:
            additional_artifacts.append(EXPERIMENT_ARTIFACT_CONFIGS[config])
            logging.info(
                'Added experiment artifact from arguments %s',
                EXPERIMENT_ARTIFACT_CONFIGS[config].source_path,
            )
    return additional_artifacts


def _upload(
        cas_instance: str,
        cas_service: str,
        artifact: ArtifactConfig,
        working_dir: str,
        log_file: str,
) -> str:
    """Upload the artifact to CAS by casuploader binary.

    Args:
      cas_instance: the instance name of CAS service.
      cas_service: the address of CAS service.
      artifact: the artifact to be uploaded to CAS.
      working_dir: the directory for intermediate files.
      log_file: the file where to add the upload logs.

    Returns: the digest of the uploaded artifact, formatted as "<hash>/<size>".
      returns None if artifact upload fails.
    """
    with tempfile.NamedTemporaryFile(mode='w+') as digest_file:
        start = time.time()
        logging.info(
            'Uploading %s to CAS instance %s', artifact.source_path, cas_instance
        )

        cmd = [
            _get_client(),
            '-cas-instance',
            cas_instance,
            '-cas-addr',
            cas_service,
            '-dump-digest',
            digest_file.name,
            '-use-adc',
        ]

        if artifact.unzip:
            cmd = cmd + ['-zip-path', artifact.source_path]
        else:
            # TODO(b/250643926) This is a workaround to handle non-directory files.
            tmp_dir = tempfile.mkdtemp(dir=working_dir)
            target_path = os.path.join(tmp_dir, os.path.basename(artifact.source_path))
            os.link(artifact.source_path, target_path)
            cmd = cmd + ['-dir-path', tmp_dir]

        for exclude_filter in artifact.exclude_filters:
            cmd = cmd + ['-exclude-filters', exclude_filter]

        try:
            with open(log_file, "a") as outfile:
                subprocess.run(
                    cmd,
                    check=True,
                    text=True,
                    stdout=outfile,
                    stderr=subprocess.STDOUT,
                    encoding='utf-8',
                    timeout=UPLOADER_TIMEOUT_SECS
                )
            logging.info(
                'Elapsed time of uploading %s: %d seconds',
                artifact.source_path,
                time.time() - start,
            )
        except subprocess.CalledProcessError as e:
            logging.warning(
                'Failed to upload %s to CAS instance %s. Skip.\nError message: %s\nLog: %s',
                artifact.source_path, cas_instance, e, e.stdout,
            )
            return None

        # Read digest of the root directory or file from dumped digest file.
        digest = digest_file.read()
        if digest:
            logging.info('Uploaded %s to CAS. Digest: %s', artifact.source_path, digest)
            return digest
        logging.warning(
            'No digest is dumped for file %s, the uploading may fail.', artifact.source_path)
        return None


def _output_results(
        cas_instance: str,
        cas_service: str,
        digests: dict[str, str],
        output_file: str,
):
    output_data = {
        'cas_instance': cas_instance,
        'cas_service': cas_service,
        'files': digests,
    }
    with open(output_file, 'w', encoding="utf8") as writer:
        writer.write(json.dumps(output_data, sort_keys=True, indent=2))
    logging.info('Output digests to %s', output_file)


def main():
    """Uploads the specified artifacts to CAS."""
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--experiment_artifacts',
        required=False,
        action='append',
        default=[],
        help='Name of configuration which artifact to upload',
    )
    args = parser.parse_args()

    cas_instance = _get_env_var('RBE_instance', check=True)
    cas_service = _get_env_var('RBE_service', check=True)
    dist_dir = _get_env_var('DIST_DIR', check=True)

    log_file = f'{dist_dir}/{LOG_PATH}'
    print('content_uploader.py will export logs to:', log_file)
    logging.basicConfig(
        level=logging.DEBUG,
        format='%(asctime)s %(levelname)s %(message)s',
        filename=log_file,
    )

    logging.info('Environment variables of running server: %s', os.environ)

    additional_artifacts = _parse_additional_artifacts(args)

    with tempfile.TemporaryDirectory() as working_dir:
        logging.info('The working dir is %s', working_dir)

        start = time.time()
        file_digests = {}
        for artifact in ARTIFACTS + additional_artifacts:
            source_path = artifact.source_path
            for f in glob.glob(dist_dir + '/**/' + source_path, recursive=True):
                name = os.path.basename(f)
                artifact.source_path = f
                digest = _upload(cas_instance, cas_service, artifact, working_dir, log_file)
                if digest:
                    file_digests[name] = digest
                else:
                    logging.warning(
                        'Skip to save the digest of file %s, the uploading may fail', name
                    )

        _output_results(
            cas_instance,
            cas_service,
            file_digests,
            os.path.join(dist_dir, 'cas_digests.json'),
        )
        logging.info('Total time of uploading build artifacts to CAS: %d seconds',
                     time.time() - start)


if __name__ == '__main__':
    main()
