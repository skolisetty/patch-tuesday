#!/usr/bin/env bash
# Patch Tuesday: offline data pull (design.md section 3).
#
# Run in WSL bash from the repo root:
#   bash scripts/pull_data.sh            # download what is missing, then verify
#   bash scripts/pull_data.sh --verify   # verify only, no network
#
# Idempotent: a file that already exists with non-zero size is skipped.
# Downloads go to "<file>.part" first and are renamed only on success, so an
# interrupted run never leaves a truncated file that a later run would skip.
# Everything fetched here is public data; no secrets are read or sent.

set -u
set -o pipefail

cd "$(dirname "$0")/.." || exit 1

UA="patch-tuesday-data-pull/1.0 (curl)"
EPSS_FIRST="2025-03-23"      # first Sunday snapshot (EPSS v4, v2025.03.14)
EPSS_COUNT=65                # ... through 2026-06-14
EPSS_HOST="https://epss.empiricalsecurity.com"
EPSS_MIRROR="https://raw.githubusercontent.com/empiricalsec/epss_scores/main"
NVD_BASE="https://nvd.nist.gov/feeds/json/cve/2.0"
NVD_YEARS="2020 2021 2022 2023 2024 2025 2026"

FAILED=()
MIRRORED=()
ZIPPED=()
KEV_LICENSE_MIRROR=0
VERIFY_ONLY=0
[ "${1:-}" = "--verify" ] && VERIFY_ONLY=1

log() { printf '%s\n' "$*"; }

# fetch <url> <dest> [sleep_seconds]
# Returns 0 if the file is present afterwards, 1 otherwise.
# Sleeps only when a request was actually made.
fetch() {
  local url="$1" dest="$2" nap="${3:-0}"
  if [ -s "$dest" ]; then
    log "skip   $dest"
    return 0
  fi
  mkdir -p "$(dirname "$dest")"
  rm -f "$dest.part"
  if curl -fL --retry 3 --retry-delay 2 --connect-timeout 30 -sS -A "$UA" -o "$dest.part" "$url" \
     && [ -s "$dest.part" ]; then
    mv -f "$dest.part" "$dest"
    log "got    $dest ($(stat -c %s "$dest") bytes)"
    [ "$nap" != "0" ] && sleep "$nap"
    return 0
  fi
  rm -f "$dest.part"
  log "FAIL   $dest <- $url"
  [ "$nap" != "0" ] && sleep "$nap"
  return 1
}

meta_gzsize() { tr -d '\r' < "$1" | sed -n 's/^gzSize://p'; }
meta_field()  { tr -d '\r' < "$1" | sed -n "s/^$2://p"; }

# Uncompressed byte count and upper-case SHA-256 of a .gz, as "<bytes> <SHA>".
gz_size_sha() {
  local n s
  n="$(gzip -dc "$1" 2>/dev/null | wc -c)"
  s="$(gzip -dc "$1" 2>/dev/null | sha256sum | cut -d' ' -f1 | tr 'a-f' 'A-F')"
  printf '%s %s' "$n" "$s"
}

# nvd_zip_fallback <year>
# The NVD site sometimes serves the .json.zip of a year while the .json.gz
# returns 404. Both hold the same JSON, so fetch the .zip (kept next to the
# .gz as the original download) and rebuild the .json.gz from it. The .zip is
# checked against zipSize in the .meta; the rebuilt .gz will NOT match gzSize,
# so verify() checks its uncompressed size and SHA-256 against the .meta.
nvd_zip_fallback() {
  local y="$1"
  local zip="data/raw/nvd/nvdcve-2.0-$y.json.zip" gz="data/raw/nvd/nvdcve-2.0-$y.json.gz"
  local meta="data/raw/nvd/nvdcve-2.0-$y.meta"
  log "zip    NVD $y: .json.gz unavailable, using the .json.zip"
  fetch "$NVD_BASE/nvdcve-2.0-$y.json.zip" "$zip" 2 || return 1
  if [ "$(stat -c %s "$zip")" != "$(meta_field "$meta" zipSize)" ]; then
    log "retry  NVD $y: zip size differs from meta zipSize; fetching both again"
    rm -f "$zip" "$meta"
    fetch "$NVD_BASE/nvdcve-2.0-$y.meta" "$meta" 1 || return 1
    fetch "$NVD_BASE/nvdcve-2.0-$y.json.zip" "$zip" 2 || return 1
  fi
  rm -f "$gz.part"
  # python3 -I: isolated mode, standard library only (WSL has no unzip).
  if python3 -I -c 'import sys, zipfile, shutil
z = zipfile.ZipFile(sys.argv[1])
names = z.namelist()
assert len(names) == 1 and names[0].endswith(".json"), names
shutil.copyfileobj(z.open(names[0]), sys.stdout.buffer, 1 << 20)' "$zip" | gzip -6 > "$gz.part" \
     && [ -s "$gz.part" ]; then
    mv -f "$gz.part" "$gz"
    log "built  $gz from the zip ($(stat -c %s "$gz") bytes)"
    return 0
  fi
  rm -f "$gz.part"
  return 1
}

download() {
  mkdir -p data/raw/nvd data/raw/kev data/raw/epss data/raw/attack data/raw/mappings \
           LICENSES web/public/models pipelines/patch_tuesday/assets/landing

  # --- NVD 2.0 year feeds (organised by CVE-ID year) + .meta ---------------
  # The year feeds are regenerated nightly, so the .meta and the .gz can be
  # from different builds if the pull straddles an update. If the sizes do not
  # match, fetch both again once.
  local y gz meta want have attempt
  for y in $NVD_YEARS; do
    gz="data/raw/nvd/nvdcve-2.0-$y.json.gz"
    meta="data/raw/nvd/nvdcve-2.0-$y.meta"
    for attempt in 1 2; do
      fetch "$NVD_BASE/nvdcve-2.0-$y.meta" "$meta" 1 || true
      fetch "$NVD_BASE/nvdcve-2.0-$y.json.gz" "$gz" 2 || true
      if [ -s "$gz" ] && [ -s "$meta" ]; then
        want="$(meta_gzsize "$meta")"; have="$(stat -c %s "$gz")"
        [ "$want" = "$have" ] && break
        if [ "$attempt" = 1 ]; then
          log "retry  NVD $y: gz is $have bytes, meta says $want; fetching both again"
          rm -f "$gz" "$meta"
        fi
      fi
    done
    if [ ! -s "$gz" ] && [ -s "$meta" ]; then
      nvd_zip_fallback "$y" && ZIPPED+=("$y")
    fi
    { [ -s "$gz" ] && [ -s "$meta" ]; } || FAILED+=("nvd-$y")
  done

  # --- CISA KEV ------------------------------------------------------------
  fetch https://www.cisa.gov/sites/default/files/csv/known_exploited_vulnerabilities.csv \
        data/raw/kev/known_exploited_vulnerabilities.csv 1 || FAILED+=("kev-csv")
  fetch https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json \
        data/raw/kev/known_exploited_vulnerabilities.json 1 || FAILED+=("kev-json")
  # cisa.gov may answer 403 for the licence file; CISA's own GitHub mirror of
  # the catalogue (cisagov/kev-data) carries the same licence text.
  fetch https://www.cisa.gov/sites/default/files/licenses/kev/license.txt \
        LICENSES/KEV-license.txt 1 \
    || { log "mirror LICENSES/KEV-license.txt (cisa.gov failed)"; \
         fetch https://raw.githubusercontent.com/cisagov/kev-data/develop/LICENSE \
               LICENSES/KEV-license.txt 1 && KEV_LICENSE_MIRROR=1; } \
    || FAILED+=("kev-license")

  # --- EPSS: Sunday snapshots; GitHub mirror if the primary host fails ------
  local i d f
  for i in $(seq 0 $((EPSS_COUNT - 1))); do
    d="$(date -u -d "$EPSS_FIRST +$((i * 7)) days" +%F)"
    f="data/raw/epss/epss_scores-$d.csv.gz"
    [ -s "$f" ] && { log "skip   $f"; continue; }
    if fetch "$EPSS_HOST/epss_scores-$d.csv.gz" "$f" 1 && gzip -t "$f" 2>/dev/null; then
      continue
    fi
    rm -f "$f"
    log "mirror $f (primary host failed)"
    if fetch "$EPSS_MIRROR/${d%%-*}/epss_scores-$d.csv.gz" "$f" 1 && gzip -t "$f" 2>/dev/null; then
      MIRRORED+=("$d")
    else
      rm -f "$f"
      FAILED+=("epss-$d")
    fi
  done

  # --- MITRE ATT&CK Enterprise 20.1 + licence -------------------------------
  fetch https://raw.githubusercontent.com/mitre-attack/attack-stix-data/master/enterprise-attack/enterprise-attack-20.1.json \
        data/raw/attack/enterprise-attack-20.1.json 1 || FAILED+=("attack")
  fetch https://raw.githubusercontent.com/mitre-attack/attack-stix-data/master/LICENSE.txt \
        LICENSES/ATTACK-LICENSE.txt 1 || FAILED+=("attack-license")

  # --- Mappings --------------------------------------------------------------
  fetch https://raw.githubusercontent.com/center-for-threat-informed-defense/mappings-explorer/main/mappings/kev/attack-16.1/kev-07.28.2025/enterprise/kev-07.28.2025_attack-16.1-enterprise.json \
        data/raw/mappings/kev_attack-16.1-enterprise.json 1 || FAILED+=("ctid-kev-map")
  fetch https://raw.githubusercontent.com/mitre/cti/master/capec/2.1/stix-capec.json \
        data/raw/mappings/stix-capec.json 1 || FAILED+=("capec")
  # Licence texts of the two mapping sources (their technique ids and names reach the deck).
  # fetch skips a file that is already there (LICENSES/CTID-MAPPINGS-APACHE-2.0.txt ships in the repo).
  fetch https://raw.githubusercontent.com/center-for-threat-informed-defense/mappings-explorer/main/LICENSE \
        LICENSES/CTID-MAPPINGS-APACHE-2.0.txt 1 || FAILED+=("ctid-license")
  fetch https://raw.githubusercontent.com/mitre/cti/master/LICENSE.txt \
        LICENSES/MITRE-CTI-LICENSE.txt 1 || FAILED+=("mitre-cti-license")

  # --- Game character (CC0) --------------------------------------------------
  fetch https://raw.githubusercontent.com/mrdoob/three.js/r187/examples/models/gltf/RobotExpressive/RobotExpressive.glb \
        web/public/models/RobotExpressive.glb 1 || FAILED+=("robot-glb")

  # --- KEV seed for the Bruin landing asset ---------------------------------
  local src=data/raw/kev/known_exploited_vulnerabilities.csv
  local dst=pipelines/patch_tuesday/assets/landing/kev.csv
  if [ -s "$src" ]; then
    if [ -s "$dst" ] && cmp -s "$src" "$dst"; then
      log "skip   $dst (identical)"
    else
      cp -f "$src" "$dst" && log "copied $dst"
    fi
  fi
}

verify() {
  local bad=0 y gz meta want have f d i n total

  log ""
  log "== NVD: gz size against .meta gzSize, and gzip -t =="
  for y in $NVD_YEARS; do
    gz="data/raw/nvd/nvdcve-2.0-$y.json.gz"; meta="data/raw/nvd/nvdcve-2.0-$y.meta"
    if [ ! -s "$gz" ] || [ ! -s "$meta" ]; then log "MISSING  NVD $y"; bad=1; continue; fi
    want="$(meta_gzsize "$meta")"; have="$(stat -c %s "$gz")"
    if [ "$want" = "$have" ]; then
      if gzip -t "$gz" 2>/dev/null; then log "ok       $y  $have bytes = gzSize, gzip -t ok"
      else log "CORRUPT  $y  gzip -t failed"; bad=1; fi
    elif [ -s "data/raw/nvd/nvdcve-2.0-$y.json.zip" ]; then
      # Rebuilt from the .zip: gzSize cannot match, so check the content.
      local zs ss
      zs="$(stat -c %s "data/raw/nvd/nvdcve-2.0-$y.json.zip")"
      ss="$(gz_size_sha "$gz")"
      if gzip -t "$gz" 2>/dev/null \
         && [ "$zs" = "$(meta_field "$meta" zipSize)" ] \
         && [ "$ss" = "$(meta_field "$meta" size) $(meta_field "$meta" sha256)" ]; then
        log "ok*      $y  rebuilt from .zip ($zs bytes = zipSize); gz $have bytes, gzip -t ok; uncompressed size and sha256 = meta"
      else
        log "MISMATCH $y  rebuilt gz: zip $zs vs zipSize $(meta_field "$meta" zipSize); content '$ss' vs meta '$(meta_field "$meta" size) $(meta_field "$meta" sha256)'"; bad=1
      fi
    else
      log "MISMATCH $y  file $have, meta gzSize $want"; bad=1
    fi
  done

  log ""
  log "== EPSS: $EPSS_COUNT Sundays from $EPSS_FIRST, gzip -t =="
  n=0; local missing=() first="" last=""
  for i in $(seq 0 $((EPSS_COUNT - 1))); do
    d="$(date -u -d "$EPSS_FIRST +$((i * 7)) days" +%F)"
    f="data/raw/epss/epss_scores-$d.csv.gz"
    if [ -s "$f" ] && gzip -t "$f" 2>/dev/null; then
      n=$((n + 1)); [ -z "$first" ] && first="$f"; last="$f"
    else
      missing+=("$d"); bad=1
    fi
  done
  log "present and valid: $n of $EPSS_COUNT"
  if [ "${#missing[@]}" -gt 0 ]; then log "MISSING EPSS Sundays: ${missing[*]}"; else log "missing EPSS Sundays: none"; fi
  if [ -n "$first" ]; then
    log "first  $(basename "$first"): $(gzip -dc "$first" 2>/dev/null | head -n 1)"
    log "last   $(basename "$last"): $(gzip -dc "$last" 2>/dev/null | head -n 1)"
  fi

  log ""
  log "== KEV =="
  f=data/raw/kev/known_exploited_vulnerabilities.json
  if [ -s "$f" ]; then
    log "json: $(grep -o '"catalogVersion"[^,]*' "$f" | head -n 1)  $(grep -o '"count"[^,]*' "$f" | head -n 1)"
    log "json cveID entries: $(grep -o '"cveID"' "$f" | wc -l)"
  else log "MISSING  $f"; bad=1; fi
  f=data/raw/kev/known_exploited_vulnerabilities.csv
  if [ -s "$f" ]; then
    log "csv data rows (lines starting with CVE-): $(grep -c '^"\?CVE-' "$f")"
    log "csv header: $(head -n 1 "$f" | tr -d '\r')"
  else log "MISSING  $f"; bad=1; fi
  if cmp -s "$f" pipelines/patch_tuesday/assets/landing/kev.csv; then
    log "landing/kev.csv: identical to the raw csv"
  else log "landing/kev.csv: MISSING or different"; bad=1; fi

  log ""
  log "== Other files =="
  for f in data/raw/attack/enterprise-attack-20.1.json \
           data/raw/mappings/kev_attack-16.1-enterprise.json \
           data/raw/mappings/stix-capec.json \
           web/public/models/RobotExpressive.glb \
           LICENSES/KEV-license.txt LICENSES/ATTACK-LICENSE.txt \
           LICENSES/CTID-MAPPINGS-APACHE-2.0.txt LICENSES/MITRE-CTI-LICENSE.txt; do
    if [ -s "$f" ]; then log "ok       $(stat -c %s "$f")  $f"; else log "MISSING  $f"; bad=1; fi
  done
  if [ -s web/public/models/RobotExpressive.glb ]; then
    log "glb magic: $(head -c 4 web/public/models/RobotExpressive.glb)"
  fi

  log ""
  total="$(find data/raw LICENSES web/public/models/RobotExpressive.glb -type f ! -name '*.part' -printf '%s\n' 2>/dev/null | awk '{s+=$1} END {print s+0}')"
  log "total bytes (data/raw + LICENSES + RobotExpressive.glb): $total"
  return $bad
}

if [ "$VERIFY_ONLY" = 0 ]; then
  download
fi
verify
rc=$?

log ""
if [ "${#MIRRORED[@]}" -gt 0 ]; then log "EPSS files taken from the GitHub mirror: ${MIRRORED[*]}"; fi
if [ "${#ZIPPED[@]}" -gt 0 ]; then log "NVD years rebuilt from the .json.zip: ${ZIPPED[*]}"; fi
if [ "$KEV_LICENSE_MIRROR" = 1 ]; then log "KEV licence taken from github.com/cisagov/kev-data"; fi
if [ "${#FAILED[@]}" -gt 0 ]; then
  log "DOWNLOAD FAILURES: ${FAILED[*]}"
  exit 1
fi
if [ "$rc" != 0 ]; then log "VERIFY: problems found (see above)"; exit 2; fi
log "ALL OK"
