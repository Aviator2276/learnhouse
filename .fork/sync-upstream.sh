#!/usr/bin/env bash
# Merge the newest stable LearnHouse release into the current branch.
#
#   .fork/sync-upstream.sh [TAG]
#
# Exit codes:   0 merged (or nothing to do)   2 conflicts need a human
# Writes key=value lines to $GITHUB_OUTPUT when set (status, tag, conflicts).
#
# Works the same locally: run it on a fresh branch off main, fix any reported
# conflicts, `git commit`, push and open a PR.
set -euo pipefail

UPSTREAM_URL="${UPSTREAM_URL:-https://github.com/learnhouse/learnhouse.git}"
UPSTREAM_REPO="${UPSTREAM_REPO:-learnhouse/learnhouse}"
SEMVER='^[0-9]+\.[0-9]+\.[0-9]+$'

out() {
  if [ -n "${GITHUB_OUTPUT:-}" ]; then echo "$1" >> "$GITHUB_OUTPUT"; fi
  echo "$1"
}

latest_release() {
  # Prefer GitHub's "latest release" (never a draft or pre-release), fall back
  # to the highest plain x.y.z tag.
  local tag=""
  if command -v gh >/dev/null 2>&1; then
    tag=$(gh api "repos/${UPSTREAM_REPO}/releases/latest" --jq .tag_name 2>/dev/null || true)
  fi
  if ! [[ "$tag" =~ $SEMVER ]]; then
    tag=$(git ls-remote --tags --refs "$UPSTREAM_URL" | awk -F/ '{print $3}' | grep -E "$SEMVER" | sort -V | tail -1)
  fi
  echo "$tag"
}

current=$(tr -d '[:space:]' < .fork/UPSTREAM_VERSION)
target="${1:-$(latest_release)}"

if ! [[ "$target" =~ $SEMVER ]]; then
  echo "::error::Could not determine a stable upstream release (got '${target}')" >&2
  exit 1
fi
out "tag=${target}"

if [ "$(printf '%s\n%s\n' "$current" "$target" | sort -V | tail -1)" = "$current" ]; then
  echo "Already on LearnHouse ${current}; newest stable release is ${target}."
  out "status=noop"
  exit 0
fi

echo "Merging LearnHouse ${target} (fork is on ${current})"
git fetch --no-tags --quiet "$UPSTREAM_URL" "refs/tags/${target}:refs/tags/${target}"

set +e
git merge --no-ff --no-commit "refs/tags/${target}"
merge_rc=$?
set -e

# Upstream's own workflows are replaced by the fork-*.yml ones. Drop every
# non-fork workflow, whether upstream changed it (a modify/delete conflict) or
# added a new one, so nothing upstream-specific ever runs in this repository.
git ls-files -- .github/workflows | sort -u | { grep -v '^\.github/workflows/fork-' || true; } \
  | xargs -r git rm -q -f --ignore-unmatch --

conflicts=$(git diff --name-only --diff-filter=U || true)
if [ -n "$conflicts" ]; then
  echo "::error::Merging ${target} left conflicts that need a human:"
  echo "$conflicts"
  out "status=conflict"
  {
    echo "conflicts<<__EOF__"
    echo "$conflicts"
    echo "__EOF__"
  } >> "${GITHUB_OUTPUT:-/dev/null}"
  git merge --abort || true
  exit 2
fi
if [ "$merge_rc" -ne 0 ] && ! git rev-parse -q --verify MERGE_HEAD >/dev/null; then
  # The merge stopped for a reason other than content conflicts (it never
  # started: e.g. local changes in the way).
  echo "::error::git merge failed (exit ${merge_rc}) without reporting conflicts"
  git merge --abort || true
  exit 1
fi

echo "$target" > .fork/UPSTREAM_VERSION
git add .fork/UPSTREAM_VERSION
git commit --quiet -m "chore(upstream): merge LearnHouse ${target}" \
  -m "Automated merge of the upstream ${target} release tag into the Authentik fork."
out "status=merged"
