#!/bin/bash

# Copyright (C) 2010 The Android Open Source Project
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


# A library script to help other scripts run within an Android build environment, or while deployed
# on a target host.  Intended to be used with the `source` bash built-in command.  Defines the
# following environment variables:
# JAVA_VERSION, RDBG_FLAG, TF_PATH, TRADEFED_OPTS
#
# It will react to the following environment variables, if they are set:
# TF_DEBUG, TRADEFED_OPTS_FILE


checkPath() {
    if ! type -P "$1" &> /dev/null; then
        >&2 echo "Unable to find $1."
        exit
    fi;
}

checkFile() {
    if [ ! -f "$1" ]; then
        >&2 echo "Unable to locate $1"
        exit
    fi;
}

# All to specify an alternative Java binary, other than the one on PATH
TF_JAVA="java"
if [ -n "${TF_JAVA_HOME}" ]; then
  # following similar convention as JAVA_HOME
  TF_JAVA=${TF_JAVA_HOME}/bin/java
fi

checkPath ${TF_JAVA}

# check java version
java_version_string=$(${TF_JAVA} -version 2>&1)
JAVA_VERSION=$(echo "$java_version_string" | grep 'version [ "]\(1\.8\|9\|11\|17\|21\).*[ "]')
if [ "${JAVA_VERSION}" == "" ]; then
    >&2 echo "Wrong java version. 1.8, 9, 11, 17 or 21 is required. Found $java_version_string"
    >&2 echo "PATH value:"
    >&2 echo "$PATH"
    if [ "${IGNORE_JAVA_VERSION_ERROR}" == "y" ]; then
        >&2 echo "Ignoring the wrong java version error as \$IGNORE_JAVA_VERSION_ERROR is set to 'y'"
    else
        >&2 echo "If you want to ignore this error, set \$IGNORE_JAVA_VERSION_ERROR to 'y'"
        exit 8
    fi
fi

# java versions below 1.8 are not supported, java versions above 1.8 need add-opens
JAVA_VERSION=$(echo "$java_version_string" | grep 'version [ "]1\.8.*[ "]')
if [ "${JAVA_VERSION}" == "" ]; then
    ADD_OPENS_FLAG="--add-opens=java.base/java.nio=ALL-UNNAMED --add-opens=java.base/sun.reflect.annotation=ALL-UNNAMED"
fi

# check debug flag and set up remote debugging
if [ -n "${TF_DEBUG}" ]; then
    if [ -z "${TF_DEBUG_PORT}" ]; then
        TF_DEBUG_PORT=10088
    fi
    RDBG_FLAG="-agentlib:jdwp=transport=dt_socket,server=y,suspend=y,address=${TF_DEBUG_PORT}"
fi

# first try to find TF jars in same dir as this script
CUR_DIR=$(dirname "$0")
TF_JAR_DIR=$(dirname "$0")
if [ -f "${CUR_DIR}/tradefed.jar" ]; then
    TF_PATH="${CUR_DIR}/*"
elif [ ! -z "${ANDROID_HOST_OUT}" ]; then
    # in an Android build env, tradefed.jar should be in
    # $ANDROID_HOST_OUT/tradefed/
    if [ -f "${ANDROID_HOST_OUT}/tradefed/tradefed.jar" ]; then
        # We intentionally pass the asterisk through without shell expansion
        TF_PATH="${ANDROID_HOST_OUT}/tradefed/*"
        TF_JAR_DIR="${ANDROID_HOST_OUT}/tradefed/"
    fi
fi

if [ -z "${TF_PATH}" ]; then
    >&2 echo "ERROR: Could not find tradefed jar files"
    exit
fi

# include any host-side test jars from suite
if [ -n "${ANDROID_HOST_OUT_TESTCASES}" ]; then
    for folder in ${ANDROID_HOST_OUT_TESTCASES}/*; do
        for entry in "$folder"/*; do
            if [[ "$entry" = *".jar"* ]]; then
                # Exclude any jar that looks like robolectric
                if ! grep -q android/app/Application.class "$entry"; then
                    TF_PATH=${TF_PATH}:$entry
                fi
            fi
        done
    done
fi

# set any host specific options
# file format for file at $TRADEFED_OPTS_FILE is one line per host with the following format:
# <hostname>=<options>
# for example:
# hostname.domain.com=-Djava.io.tmpdir=/location/on/disk -Danother=false ...
# hostname2.domain.com=-Djava.io.tmpdir=/different/location -Danother=true ...
if [ -e "${TRADEFED_OPTS_FILE}" ]; then
    # pull the line for this host and take everything after the first =
    export TRADEFED_OPTS=`grep "^$HOSTNAME=" "$TRADEFED_OPTS_FILE" | cut -d '=' -f 2-`
fi
