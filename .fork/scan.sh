#!/usr/bin/env bash
# Vulnerability scan with Grype (checksum-verified binary).
#
#   .fork/scan.sh <target> <report.md>      target: an image ref, or dir:<path>
#
# Prints a table, writes a Markdown report of FIXABLE CRITICAL findings and
# sets `criticals=<n>` in $GITHUB_OUTPUT. Never fails on findings: most come
# from versions pinned upstream (the fork cannot bump them without breaking
# upstream's lockfiles), so CI reports them and the publish workflow tracks
# them in an issue instead of blocking every update. Fails only if the scan
# itself cannot run.
set -euo pipefail

TARGET="${1:?target}"
REPORT="${2:?report path}"
GRYPE_VERSION="0.121.3"
GRYPE_SHA256="e9da18cc1429e786722e32161288103600efffd8e483c90761f90485bd1bec85"

bin_dir="$(mktemp -d)"
curl -sSfL -o "$bin_dir/grype.tgz" \
  "https://github.com/anchore/grype/releases/download/v${GRYPE_VERSION}/grype_${GRYPE_VERSION}_linux_amd64.tar.gz"
echo "${GRYPE_SHA256}  $bin_dir/grype.tgz" | sha256sum -c - >/dev/null
tar -xzf "$bin_dir/grype.tgz" -C "$bin_dir" grype

json="$bin_dir/result.json"
"$bin_dir/grype" "$TARGET" --only-fixed -o table -o "json=$json"

criticals=$(jq '[.matches[] | select(.vulnerability.severity == "Critical")] | length' "$json")
{
  echo "### Fixable critical vulnerabilities: ${criticals}"
  echo
  echo "Target: \`${TARGET}\`"
  echo
  if [ "$criticals" -gt 0 ]; then
    echo "| Package | Installed | Vulnerability | Fixed in |"
    echo "| --- | --- | --- | --- |"
    jq -r '[.matches[] | select(.vulnerability.severity == "Critical")]
           | unique_by(.artifact.name + .vulnerability.id)[]
           | "| \(.artifact.name) | \(.artifact.version) | \(.vulnerability.id) | \(.vulnerability.fix.versions | join(", ")) |"' "$json"
  fi
} > "$REPORT"
cat "$REPORT"
if [ -n "${GITHUB_OUTPUT:-}" ]; then echo "criticals=${criticals}" >> "$GITHUB_OUTPUT"; fi
