#!/usr/bin/env bash
#
# vendor-sync.sh — re-vendor the qpdf qtest tree at a specified upstream tag.
#
# Usage:
#   scripts/vendor-sync.sh <qpdf-tag>     # e.g. v11.9.0
#
# Effects (idempotent):
#   - Downloads qpdf source tarball for <qpdf-tag> from github.com/qpdf/qpdf
#   - Replaces vendor/qtest/      with the upstream qtest/ subtree
#   - Replaces vendor/qpdf-qtest/ with the upstream qpdf/qtest/ subtree
#   - Records the tag in vendor/UPSTREAM_TAG
#
# vendor/qtest/      contains the qtest framework (Artistic 2.0)
# vendor/qpdf-qtest/ contains qpdf-specific .test files and fixtures (Apache 2.0)
#
# Both trees are intended to be pristine copies of upstream. Do not patch them
# locally; absorb divergences via shim/, normalize/, or allowlist.txt instead.

set -euo pipefail

if [[ $# -ne 1 ]]; then
    echo "usage: $(basename "$0") <qpdf-tag>" >&2
    echo "example: $(basename "$0") v11.9.0" >&2
    exit 2
fi

tag="$1"
repo_root="$(cd "$(dirname "$0")/.." && pwd)"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

tarball_url="https://github.com/qpdf/qpdf/archive/refs/tags/${tag}.tar.gz"
echo "==> Fetching ${tarball_url}"
curl -fL --retry 3 -o "${work}/qpdf.tar.gz" "${tarball_url}"

echo "==> Extracting"
tar -xzf "${work}/qpdf.tar.gz" -C "${work}"

src_root="$(find "${work}" -maxdepth 1 -mindepth 1 -type d -name "qpdf-*" | head -n1)"
if [[ -z "${src_root}" || ! -d "${src_root}/qtest" || ! -d "${src_root}/qpdf/qtest" ]]; then
    echo "error: extracted tree does not contain qtest/ or qpdf/qtest/" >&2
    exit 1
fi

dst_qtest="${repo_root}/vendor/qtest"
dst_qpdf_qtest="${repo_root}/vendor/qpdf-qtest"

echo "==> Refreshing ${dst_qtest}"
rm -rf "${dst_qtest}"
cp -a "${src_root}/qtest" "${dst_qtest}"

echo "==> Refreshing ${dst_qpdf_qtest}"
rm -rf "${dst_qpdf_qtest}"
cp -a "${src_root}/qpdf/qtest" "${dst_qpdf_qtest}"

echo "==> Recording upstream tag"
printf '%s\n' "${tag}" > "${repo_root}/vendor/UPSTREAM_TAG"

echo "==> Done. Summary:"
{
    printf '  qtest:      %s files, %s\n' \
        "$(find "${dst_qtest}" -type f | wc -l | tr -d ' ')" \
        "$(du -sh "${dst_qtest}" | cut -f1)"
    printf '  qpdf-qtest: %s files, %s\n' \
        "$(find "${dst_qpdf_qtest}" -type f | wc -l | tr -d ' ')" \
        "$(du -sh "${dst_qpdf_qtest}" | cut -f1)"
    printf '  tag:        %s\n' "${tag}"
}
