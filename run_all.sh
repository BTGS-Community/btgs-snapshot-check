#!/usr/bin/env bash
# BTGS snapshot check - runs every read-only script at ONE fixed height,
# so everyone gets byte-identical result files they can compare.
# Usage: ./run_all.sh [HEIGHT] [DATADIR] [RPCPORT]
set -euo pipefail
HEIGHT="${1:-17416}"
DATADIR="${2:-$HOME/.btgs}"
PORT="${3:-18881}"
HERE="$(cd "$(dirname "$0")" && pwd)"
OUT="$HERE/results-$HEIGHT"
mkdir -p "$OUT" && cd "$OUT"
export PYTHONHASHSEED=0 NO_PROXY=127.0.0.1,localhost no_proxy=127.0.0.1,localhost
COMMON=(--port "$PORT" --datadir "$DATADIR")
echo "Checking at block $HEIGHT (read-only). This takes a few minutes per script."
for s in snapshot_analysis peel_follow premine_tree clusters miners link_check multisig_check; do
  echo "== btgs_$s.py"
  python3 "$HERE/scripts/btgs_$s.py" "${COMMON[@]}" --height "$HEIGHT"
done
echo "== btgs_vesting_model.py"
python3 "$HERE/scripts/btgs_vesting_model.py" "${COMMON[@]}" --height "$HEIGHT" \
  --thresholds 10000,15000,20000,25000,50000 \
  --exclude "$HERE/inputs/vesting-exclude.txt" --exempt "$HERE/inputs/vesting-exempt.txt"
sha256sum btgs-* > SHA256SUMS.txt
echo
echo "Done. Your result files are in: $OUT"
REF="$HERE/reference/SHA256SUMS-$HEIGHT.txt"
if [ ! -f "$REF" ]; then
  echo "No reference results for block $HEIGHT yet, so there is nothing to compare with."
  echo "Your fingerprints are in SHA256SUMS.txt - share them so others can compare."
elif diff -u "$REF" SHA256SUMS.txt; then
  echo "MATCH: you got exactly the same results as the reference."
else
  echo "Some files differ (see the lines above). Compare the totals in btgs-snapshot-report.txt and tell us in the BTGS Discord."
fi
