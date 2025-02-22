#! /bin/bash

# Helper functions for pbench-server bash scripts.  All environment variables
# are defined in pbench-base.py, execv() our caller.

if [ -z "$PROG" ]; then
    echo "$(basename $0): ERROR: \$PROG environment variable does not exist." > /dev/stdout
    exit 2
fi
if [ -z "$dir" ]; then
    echo "$(basename $0): ERROR: \$dir environment variable does not exist." > /dev/stdout
    exit 3
else
    # Ensure the configuration file in play is self-consistent with the
    # location from which this script is being invoked.
    if [ "$BINDIR" != "$dir" ]; then
        echo "$PROG: ERROR: BINDIR (\"$BINDIR\") not defined as \"$dir\"" > /dev/stdout
        exit 4
    fi
    # Ensure the path where pbench-base.sh was found is in the PATH environment
    # variable.
    if [[ ! ":$PATH:" =~ ":${BINDIR}:" ]]; then
        echo "$PROG: ERROR: BINDIR (\"$BINDIR\") not in PATH=\"$PATH\"" > /dev/stdout
        exit 5
    fi
fi

function doexit {
    echo "$PROG: $1" >&2
    exit 1
}

if [[ -z "$_PBENCH_SERVER_TEST" ]]; then
    # the real thing

    function timestamp {
        echo "$(date +'%Y-%m-%dT%H:%M:%S-%Z')"
    }

    function timestamp-seconds-since-epoch {
        echo "$(date +'%s')"
    }

    function get-tempdir-name {
        # make the names reproducible for unit tests
        echo "$TMP/${1}.$$"
    }
else
    # unit test regime

    function timestamp {
        echo "1970-01-01T00:00:42-UTC"
    }

    function timestamp-seconds-since-epoch {
        # 1970-01-01T00:00:42-UTC
        echo "42"
    }

    function get-tempdir-name {
        # make the names reproducible for unit tests
        echo "$TMP/${1}.XXXXX"
    }
fi

function mk_dirs {
    hostname=$1

    for d in $LINKDIRS ;do
        thedir=$ARCHIVE/$hostname/$d
        mkdir -p $thedir
        if [[ $? -ne 0 || ! -d "$thedir" ]]; then
            return 1
        fi
    done
    return 0
}

function log_init {
    LOG_DIR=$LOGSDIR/${1}
    mkdir -p $LOG_DIR
    if [[ $? -ne 0 || ! -d "$LOG_DIR" ]]; then
        doexit "Unable to find/create logging directory, $LOG_DIR"
    fi

    log_file=$LOG_DIR/${1}.log
    error_file=$LOG_DIR/${1}.error

    exec 100>&1  # Save stdout on FD 100
    exec 200>&2  # Save stderr on FD 200

    exec 1>>"$log_file"
    exec 2>&1
    exec 4>>"$error_file"
}

function log_finish {
    exec 1>&100  # Restore stdout
    exec 2>&200  # Restore stderr
    exec 100>&-  # Close log file
    exec 4>&-    # Close error file
}

function log_exit {
    local _msg="${PROG}: ${1}"
    if [[ -z "${3}" ]]; then
        printf -- "%s\n" "${_msg}" >&4
    else
        printf -- "%s\n" "${_msg}" | tee -a "${3}" >&4
    fi
    log_finish
    if [[ -z "${2}" ]]; then
        exit 1
    else
        exit ${2}
    fi
}

function log_info {
    if [[ -z "${2}" ]]; then
        printf -- "%b\n" "${1}"
    else
        printf -- "%b\n" "${1}" | tee -a "${2}"
    fi
}

function log_error {
    if [[ -z "${2}" ]]; then
        printf -- "%b\n" "${1}" >&4
    else
        printf -- "%b\n" "${1}" | tee -a "${2}" >&4
    fi
}
