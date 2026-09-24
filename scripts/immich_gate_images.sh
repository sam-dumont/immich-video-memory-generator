#!/usr/bin/env bash
# Get the gate's pinned images onto this Docker host, or save them for the next run.
#
#   immich_gate_images.sh fetch TARBALL REF...   load TARBALL if it holds every REF, else pull
#   immich_gate_images.sh save  TARBALL REF...   docker save every REF into TARBALL
#
# A registry that stalls mid-pull used to hold the gate for its whole timeout
# (run 36002698337, #1267). CI keeps the tarball in actions/cache, so a warm
# run never talks to a registry. Each pull attempt has its own timeout, so a
# stall costs one attempt, not the job. A ref that is still missing after the
# last attempt exits non-zero: the gate fails, it never runs without Immich.
set -euo pipefail

PULL_ATTEMPTS=${IMMICH_GATE_PULL_ATTEMPTS:-3}
# A healthy pull of the biggest set (the v2 server) takes under a minute.
PULL_TIMEOUT=${IMMICH_GATE_PULL_TIMEOUT:-180}

missing() {
	local ref
	for ref in "$@"; do
		docker image inspect "$ref" >/dev/null 2>&1 || echo "$ref"
	done
}

# Refs never contain whitespace, so a space-separated list is safe (and keeps
# this runnable on macOS's bash 3.2, which has no mapfile).
pull_missing() {
	local attempt ref todo
	for attempt in $(seq 1 "$PULL_ATTEMPTS"); do
		todo=$(missing "$@")
		if [ -z "$todo" ]; then return 0; fi
		echo "pull attempt $attempt/$PULL_ATTEMPTS:" $todo
		for ref in $todo; do
			timeout "$PULL_TIMEOUT" docker pull --quiet "$ref" || echo "pull of $ref failed or timed out"
		done
		if [ "$attempt" -lt "$PULL_ATTEMPTS" ]; then sleep $((attempt * 10)); fi
	done
	todo=$(missing "$@")
	if [ -z "$todo" ]; then return 0; fi
	echo "still missing after $PULL_ATTEMPTS attempts:" $todo >&2
	return 1
}

cmd=${1:?usage: immich_gate_images.sh fetch|save TARBALL REF...}
tarball=${2:?tarball path required}
shift 2
[ "$#" -gt 0 ] || { echo "no image refs given" >&2; exit 2; }

case "$cmd" in
fetch)
	if [ -s "$tarball" ]; then
		docker load --quiet --input "$tarball" || echo "docker load failed"
		if [ -z "$(missing "$@")" ]; then
			echo "all pinned images loaded from $tarball"
			exit 0
		fi
		echo "the cached tarball did not provide:" $(missing "$@")
	fi
	pull_missing "$@"
	;;
save)
	mkdir -p "$(dirname "$tarball")"
	docker save --output "$tarball" "$@"
	echo "saved $(du -h "$tarball" | cut -f1) to $tarball"
	;;
*)
	echo "unknown command: $cmd" >&2
	exit 2
	;;
esac
