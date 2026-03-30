#!/usr/bin/env bash
#
# sign_and_notarize.sh — Code-sign and notarize ContextPulse.app
#
# Required env vars:
#   IDENTITY              — Developer ID Application certificate name or SHA-1
#   APPLE_ID              — Apple ID email for notarization
#   TEAM_ID               — Apple Developer Team ID
#   APP_SPECIFIC_PASSWORD — App-specific password for notarytool
#
# Usage:
#   export IDENTITY="Developer ID Application: Jerard Ventures LLC (XXXXXXXXXX)"
#   export APPLE_ID="david@jerardventures.com"
#   export TEAM_ID="XXXXXXXXXX"
#   export APP_SPECIFIC_PASSWORD="xxxx-xxxx-xxxx-xxxx"
#   ./packaging/macos/sign_and_notarize.sh dist/ContextPulse.app
#
set -euo pipefail

APP_PATH="${1:?Usage: $0 <path-to-ContextPulse.app>}"
ENTITLEMENTS="$(dirname "$0")/entitlements.plist"

if [[ -z "${IDENTITY:-}" ]]; then
    echo "ERROR: IDENTITY env var not set" >&2; exit 1
fi
if [[ -z "${APPLE_ID:-}" ]]; then
    echo "ERROR: APPLE_ID env var not set" >&2; exit 1
fi
if [[ -z "${TEAM_ID:-}" ]]; then
    echo "ERROR: TEAM_ID env var not set" >&2; exit 1
fi
if [[ -z "${APP_SPECIFIC_PASSWORD:-}" ]]; then
    echo "ERROR: APP_SPECIFIC_PASSWORD env var not set" >&2; exit 1
fi

echo "==> Signing individual binaries inside the bundle..."

# Sign all .so files
find "$APP_PATH" -name "*.so" -exec \
    codesign --force --options runtime \
        --entitlements "$ENTITLEMENTS" \
        --sign "$IDENTITY" \
        --timestamp {} \;

# Sign all .dylib files
find "$APP_PATH" -name "*.dylib" -exec \
    codesign --force --options runtime \
        --entitlements "$ENTITLEMENTS" \
        --sign "$IDENTITY" \
        --timestamp {} \;

# Sign any executable binaries in Frameworks/
find "$APP_PATH/Contents/Frameworks" -type f -perm +111 ! -name "*.dylib" ! -name "*.so" 2>/dev/null | while read -r bin; do
    codesign --force --options runtime \
        --entitlements "$ENTITLEMENTS" \
        --sign "$IDENTITY" \
        --timestamp "$bin"
done

echo "==> Signing main executable..."
codesign --force --options runtime \
    --entitlements "$ENTITLEMENTS" \
    --sign "$IDENTITY" \
    --timestamp "$APP_PATH/Contents/MacOS/ContextPulse"

echo "==> Signing the app bundle..."
codesign --force --options runtime \
    --entitlements "$ENTITLEMENTS" \
    --sign "$IDENTITY" \
    --timestamp "$APP_PATH"

echo "==> Verifying code signature..."
codesign --verify --deep --strict --verbose=2 "$APP_PATH"

echo "==> Checking Gatekeeper assessment..."
spctl --assess --type execute --verbose=2 "$APP_PATH"

echo "==> Creating zip for notarization (using ditto)..."
ZIP_PATH="${APP_PATH%.app}.zip"
ditto -c -k --keepParent "$APP_PATH" "$ZIP_PATH"

echo "==> Submitting for notarization..."
xcrun notarytool submit "$ZIP_PATH" \
    --apple-id "$APPLE_ID" \
    --team-id "$TEAM_ID" \
    --password "$APP_SPECIFIC_PASSWORD" \
    --wait

echo "==> Stapling notarization ticket..."
xcrun stapler staple "$APP_PATH"

echo "==> Verifying stapled ticket..."
xcrun stapler validate "$APP_PATH"

echo ""
echo "Done. Signed and notarized: $APP_PATH"
echo "Notarization zip: $ZIP_PATH"
