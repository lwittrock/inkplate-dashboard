#!/bin/bash
# Pull-based deploy of the screen service in CT 106. Runs every 5 minutes as
# screen-deploy (inkplate-screen-deploy.timer). Design: docs/server-rendering-design.md,
# "Deploying". Installed by hand to /usr/local/lib/inkplate-screen/deploy.sh, so a
# push can change the service but not the thing that deploys it.
#
#   /opt/inkplate-screen/repo        clone of the public repo, master only
#   /opt/inkplate-screen/releases/   one folder per server/ tree, with its own venv
#   /opt/inkplate-screen/current     symlink to the live release
#   /opt/inkplate-screen/deployed    rewritten on each switch; a path unit restarts the service
#
# A release goes live only after its dependencies install from hashes and
# `python -m screen.selftest` passes. Otherwise the current one keeps running.
set -euo pipefail

ROOT=/opt/inkplate-screen
BRANCH=${DEPLOY_BRANCH:-master}

hc() {  # healthchecks.io ping; never let a ping failure fail the deploy
    [ -n "${HC_PING_DEPLOY:-}" ] || return 0
    curl -fsS -m 10 --retry 3 -o /dev/null "$HC_PING_DEPLOY${1:+/$1}" || true
}

cd "$ROOT/repo"
# A failed fetch (GitHub or DNS briefly away) exits without a ping: the check's
# grace time decides when that silence becomes worth an alert.
git fetch --quiet --depth 1 origin "$BRANCH"
commit=$(git rev-parse FETCH_HEAD)
tree=$(git rev-parse "FETCH_HEAD:server")

if [ "$tree" = "$(cat "$ROOT/current/.tree" 2>/dev/null || true)" ]; then
    hc
    exit 0      # nothing under server/ changed
fi

release="$ROOT/releases/$tree"
if [ ! -d "$release" ]; then
    build="$ROOT/releases/.build-$tree"
    rm -rf "$build"
    mkdir -p "$build"
    trap 'rm -rf "$build"; hc fail' ERR
    git archive "$commit" server | tar -x --strip-components=1 -C "$build"
    # The venv moves with the folder below. That is fine for `.venv/bin/python -m ...`,
    # which is all the service runs; .venv/bin/pip's own path goes stale, so use
    # `.venv/bin/python -m pip` in a release by hand.
    python3 -m venv "$build/.venv"
    "$build/.venv/bin/pip" install --quiet --no-cache-dir --disable-pip-version-check \
        --require-hashes -r "$build/requirements.txt"
    (cd "$build" && .venv/bin/python -m screen.selftest)
    echo "$tree" > "$build/.tree"
    echo "$commit" > "$build/.commit"
    mv "$build" "$release"
    trap - ERR
fi

# Newest by time means most recently live, so a revert to an older release
# is not pruned below while it runs.
touch "$release"
ln -sfn "releases/$tree" "$ROOT/current.new"
mv -T "$ROOT/current.new" "$ROOT/current"
echo "$commit $(date -Is)" > "$ROOT/deployed"
echo "deployed $commit (server/ tree $tree)"

# Keep the three newest releases; the live one is always among them.
ls -1dt "$ROOT"/releases/*/ | tail -n +4 | xargs -r rm -rf
hc
