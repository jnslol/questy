#!/usr/bin/env bash
#
# Download (or update) the questy binary from the latest GitHub release.
#
#   ./install.sh [options]
#
# Options:
#   --to PATH     install location (default: ./questy, or $QUESTY_BIN)
#   --tag TAG     install a specific release tag instead of latest (e.g. v1.0.5)
#   --force       replace the binary even if it is already up to date
#   --check       only compare versions, do not download or install
#                 (exit 0 if up to date, 10 if an update is available)
#   --require-checksum
#                 fail if the release checksum file cannot be verified
#   -h, --help    show this help
#
# Shows the current version/hash, the new version/hash, and skips the
# install when both hashes match (unless --force is given).
# The install location defaults to ./questy and can be overridden with
# --to PATH or the QUESTY_BIN environment variable.

set -euo pipefail

REPO="jnslol/questy"
ASSET="questy-linux"
CHECKSUM_FILE="SHA256SUMS"
TARGET="${QUESTY_BIN:-./questy}"
TAG=""
FORCE=0
CHECK_ONLY=0
REQUIRE_CHECKSUM=0

usage() {
    sed -n '2,/^$/p' "$0" | sed 's/^# \?//'
}

while [ $# -gt 0 ]; do
    case "$1" in
        --to)
            TARGET="${2:?--to needs a path}"; shift 2 ;;
        --to=*)
            TARGET="${1#--to=}"; shift ;;
        --tag)
            TAG="${2:?--tag needs a tag}"; shift 2 ;;
        --tag=*)
            TAG="${1#--tag=}"; shift ;;
        --force)
            FORCE=1; shift ;;
        --check)
            CHECK_ONLY=1; shift ;;
        --require-checksum)
            REQUIRE_CHECKSUM=1; shift ;;
        -h|--help)
            usage; exit 0 ;;
        *)
            echo "error: unknown argument: $1" >&2
            usage >&2; exit 2 ;;
    esac
done

need() {
    command -v "$1" >/dev/null 2>&1 || {
        echo "error: required tool not found: $1" >&2
        exit 1
    }
}

need curl
need sha256sum

hash_of() {
    sha256sum "$1" | awk '{print $1}'
}

version_of() {
    # Never fail: a foreign-arch binary cannot execute here.
    ("$1" --version 2>/dev/null) || echo "unknown"
}

echo "== questy installer =="
echo "target:        $TARGET"

CURRENT_VERSION="none"
CURRENT_HASH="none"
if [ -x "$TARGET" ]; then
    CURRENT_VERSION="$(version_of "$TARGET")"
    CURRENT_HASH="$(hash_of "$TARGET")"
elif [ -e "$TARGET" ]; then
    echo "warning: $TARGET exists but is not executable" >&2
fi
echo "current:       $CURRENT_VERSION"
echo "current sha256: $CURRENT_HASH"

if [ -z "$TAG" ]; then
    echo "resolving latest release..."
    if ! API_JSON="$(curl -fsSL "https://api.github.com/repos/${REPO}/releases/latest")"; then
        echo "error: could not reach the GitHub API for ${REPO}" >&2
        exit 1
    fi
    TAG="$(printf '%s' "$API_JSON" | grep -o '"tag_name": *"[^"]*"' | head -n 1 | sed 's/.*": *"//; s/"//')"
    if [ -z "$TAG" ]; then
        echo "error: could not parse a release tag from the GitHub API response" >&2
        exit 1
    fi
fi
echo "release tag:   $TAG"

BASE_URL="https://github.com/${REPO}/releases/download/${TAG}"

if [ "$CHECK_ONLY" -eq 1 ]; then
    # Version-only check: no binary download needed.
    TAG_NUM="${TAG#v}"
    if [ "$CURRENT_VERSION" != "none" ] && [ "$CURRENT_VERSION" != "unknown" ] \
        && printf '%s' "$CURRENT_VERSION" | grep -qF "$TAG_NUM"; then
        echo "up to date: $CURRENT_VERSION matches $TAG"
        exit 0
    fi
    echo "update available: $CURRENT_VERSION -> $TAG"
    echo "run without --check to download and install."
    exit 10
fi

TMP="$(mktemp)"
trap 'rm -f "$TMP" "$TMP.sums"' EXIT

echo "downloading ${ASSET} (${TAG})..."
curl -fL "${BASE_URL}/${ASSET}" -o "$TMP"
chmod +x "$TMP"

if [ ! -s "$TMP" ]; then
    echo "error: downloaded file is empty, aborting" >&2
    exit 1
fi

NEW_VERSION="$(version_of "$TMP")"
NEW_HASH="$(hash_of "$TMP")"
echo "new:           $NEW_VERSION"
echo "new sha256:    $NEW_HASH"

# Verify against the published checksums when available.
if curl -fsSL "${BASE_URL}/${CHECKSUM_FILE}" -o "$TMP.sums" 2>/dev/null; then
    echo "verifying checksum against ${CHECKSUM_FILE}..."
    EXPECTED="$(grep -E "[[:space:]]${ASSET}$" "$TMP.sums" | awk '{print $1}' | head -n 1)"
    if [ -z "$EXPECTED" ]; then
        echo "warning: no ${ASSET} entry in ${CHECKSUM_FILE}, skipping verification" >&2
    elif [ "$EXPECTED" = "$NEW_HASH" ]; then
        echo "checksum:      OK"
    else
        echo "error: checksum mismatch for ${ASSET}, aborting" >&2
        echo "  expected: $EXPECTED" >&2
        echo "  got:      $NEW_HASH" >&2
        exit 1
    fi
else
    if [ "$REQUIRE_CHECKSUM" -eq 1 ]; then
        echo "error: checksum file ${CHECKSUM_FILE} not found for ${TAG}" >&2
        exit 1
    fi
    echo "checksum:      skipped (no ${CHECKSUM_FILE} published for ${TAG})"
fi

if [ "$CURRENT_HASH" = "$NEW_HASH" ] && [ "$FORCE" -eq 0 ]; then
    echo "already up to date: $CURRENT_VERSION ($CURRENT_HASH)"
    exit 0
fi

if [ -e "$TARGET" ]; then
    echo "backing up old binary to ${TARGET}.bak"
    mv -f "$TARGET" "${TARGET}.bak"
fi

mv -f "$TMP" "$TARGET"
chmod +x "$TARGET"

echo "------------------------------"
echo "installed:     $TARGET"
echo "version:       $(version_of "$TARGET")"
echo "sha256:        $(hash_of "$TARGET")"
echo "  (previous: $CURRENT_VERSION / $CURRENT_HASH)"
