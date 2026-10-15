#!/usr/bin/env bash
# Rebuild the synthetic demo end to end so it looks current: generate -> forecast -> (load) -> (dump).
#
#   utils/synth_demo_refresh.sh [--output DIR] [--end-date DATE] [--config-dir DIR] [--presets FILE]
#                               [--force] [--dsn DSN] [--dump] [--dry-run]
#
#   --output DIR      org-demo directory to create          (default econ-forecast/artifacts/org-demo)
#   --end-date DATE   YYYY-MM-DD (a real calendar date) or "today"; the series ends here
#                     (default today, resolved once)
#   --config-dir DIR  users/roster/series/org YAMLs         (default econ-forecast/config/synthetic/org)
#   --presets FILE    forecast presets                      (default: the forecast CLI's own)
#   --force           replace an earlier refresh in --output (see below)
#   --dsn DSN         also load into this Postgres; the database name must end in _synth, and the
#                     database must already exist (the loader does not create it; a missing one
#                     fails only after generate and forecast, about 6 minutes in)
#                     (no default: econ_synth is never assumed)
#   --dump            also build the AWS seed dump (utils/synth_dump.sh, throwaway container); it
#                     does its own load, so it needs no --dsn. Writes $OUT, default
#                     deploy/seed/econ_synth.sql.gz (a relative $OUT is relative to the repo root)
#   --dry-run         print the stages and their exact commands; change and run nothing
#
# Stages, announced on stderr as "[synth-refresh] stage N/M <name> ...":
#   1 generate  econ-forecast/utils/synth_org.sh      users -> roster -> series -> org-expand
#   2 forecast  econ-forecast/utils/dashboard_forecast.sh   writes <output>/forecast/forecast_export.json
#   3 load      utils/load_synthetic.sh --replace --forecast-export ...   (only with --dsn)
#   4 dump      utils/synth_dump.sh                   (only with --dump)
#
# The generator refuses an output path that exists. An existing --output is therefore moved aside
# to a sibling "<output>.prev-<pid>" (an empty one too), the generator runs, and only when it has
# succeeded is the old copy deleted -- exactly what a refresh creates: configs pricing org roster
# series users forecast, then the directory. If generate fails (or you interrupt it) the partial
# output is removed and the old copy is moved back, so a failed refresh never leaves you without
# your demo. A directory that holds anything else than those names is refused up front, and a
# non-empty one is refused without --force.
#
# There is no lock: do not run two refreshes on the same --output at once.
#
# Exit: 0 complete (or --dry-run), 1 a stage failed (completed outputs stay in place), 2 usage or
# validation error, 129/130/131/143 interrupted (HUP/INT/QUIT/TERM). Human text goes to stderr; the last line on stdout is one
# JSON object. This script never connects to a database; only the two wrappers it calls do.
set -euo pipefail

CALLER_PWD="$PWD"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# The loader wrapper runs a bare `python -m server...`; give it the repo's interpreter and
# import path (the forecast wrappers bootstrap their own).
add_venv_to_path() {
  if [[ -d "$ROOT/venv/bin" ]]; then
    case ":$PATH:" in
      *":$ROOT/venv/bin:"*) ;;
      *) export PATH="$ROOT/venv/bin:$PATH" ;;
    esac
  fi
}
add_venv_to_path
export PYTHONPATH="$ROOT/src:$ROOT/econ-forecast/src${PYTHONPATH:+:$PYTHONPATH}"

LOG_TAG="[synth-refresh]"
OWNED_ENTRIES="configs pricing org roster series users forecast"
CONFIG_FILES="users.yaml roster.yaml series.yaml org.yaml"

OUTPUT_ARG=""
END_DATE_ARG="today"
CONFIG_ARG=""
PRESETS_ARG=""
DSN=""
FORCE=0
DUMP=0
DRY=0

# --- JSON helpers (plain bash; strings are escaped for backslash, quote and control chars) ---------
jesc() {
  local s="$1"
  s="${s//\\/\\\\}"
  s="${s//\"/\\\"}"
  s="${s//$'\n'/\\n}"
  s="${s//$'\t'/\\t}"
  s="${s//$'\r'/\\r}"
  printf '%s' "$s"
}
jstr() { if [[ -z "$1" ]]; then printf 'null'; else printf '"%s"' "$(jesc "$1")"; fi; }
jnum() { if [[ "$1" =~ ^[0-9]+$ ]]; then printf '%s' "$1"; else printf 'null'; fi; }

# One argument for display: a DSN's password is masked (the userinfo after "://user:" up to the
# last "@", and password=/sslpassword= query values), before any quoting, so spaces and raw "@"
# in a password cannot defeat it.
mask_arg() {
  case "$1" in
    *://*)
      printf '%s' "$1" | sed -E \
        -e 's#^([^:/]*://[^:@/]*):.*@#\1:REDACTED@#' \
        -e 's#([?&](ssl)?password)=[^&]*#\1=REDACTED#g'
      ;;
    *) printf '%s' "$1" ;;
  esac
}

# Command line for display: every argument masked, then shell-quoted.
show_cmd() {
  local arg line=""
  for arg in "$@"; do
    line="$line$(printf '%q' "$(mask_arg "$arg")") "
  done
  printf '%s' "${line% }"
}

invalid() {
  echo "$LOG_TAG $1" >&2
  printf '{"status": "invalid", "exit_code": 2, "error": %s}\n' "$(jstr "$1")"
  exit 2
}

usage() {
  sed -n '2,/^set -euo/p' "${BASH_SOURCE[0]}" | sed -e '/^set -euo/d' -e 's/^# \{0,1\}//' >&2
}

# Absolute and lexically normalised: "." and empty components dropped, ".." pops one. (Symbolic
# links are not resolved, so "A/../B" means B beside A as written.)
abspath() {
  local p="$1" rest part acc=""
  [[ "$p" == /* ]] || p="$CALLER_PWD/$p"
  rest="${p#/}/"
  while [[ -n "$rest" ]]; do
    part="${rest%%/*}"
    rest="${rest#*/}"
    case "$part" in
      ""|".") ;;
      "..") acc="${acc%/*}" ;;
      *) acc="$acc/$part" ;;
    esac
  done
  printf '%s' "${acc:-/}"
}

# A real calendar date (2026-02-30 is not one), in plain bash.
valid_date() {
  local y m d max
  y=$((10#${1%%-*})); m=${1#*-}; d=$((10#${m#*-})); m=$((10#${m%%-*}))
  case "$m" in
    1|3|5|7|8|10|12) max=31 ;;
    4|6|9|11) max=30 ;;
    2) if (( (y % 4 == 0 && y % 100 != 0) || y % 400 == 0 )); then max=29; else max=28; fi ;;
    *) return 1 ;;
  esac
  (( d >= 1 && d <= max ))
}

# --- arguments ---------------------------------------------------------------------------------------
# A value is "given" only when it is non-empty: an empty one is an error, never "use the default".
need_value() {  # option what remaining-arg-count value
  [[ "$3" -ge 2 ]] || invalid "$1 needs $2"
  [[ -n "$4" ]] || invalid "$1 must not be empty"
}
while (( $# )); do
  case "$1" in
    --output)     need_value --output "a directory" "$#" "${2-}"; OUTPUT_ARG="$2"; shift ;;
    --end-date)   need_value --end-date "a date" "$#" "${2-}"; END_DATE_ARG="$2"; shift ;;
    --config-dir) need_value --config-dir "a directory" "$#" "${2-}"; CONFIG_ARG="$2"; shift ;;
    --presets)    need_value --presets "a file" "$#" "${2-}"; PRESETS_ARG="$2"; shift ;;
    --dsn)        need_value --dsn "a DSN" "$#" "${2-}"; DSN="$2"; shift ;;
    --force)      FORCE=1 ;;
    --dump)       DUMP=1 ;;
    --dry-run)    DRY=1 ;;
    -h|--help)    usage; exit 0 ;;
    *)            usage; invalid "unknown argument: $(mask_arg "$1")" ;;
  esac
  shift
done

if [[ -n "$OUTPUT_ARG" ]]; then OUTPUT="$(abspath "$OUTPUT_ARG")"; else OUTPUT="$ROOT/econ-forecast/artifacts/org-demo"; fi
if [[ -n "$CONFIG_ARG" ]]; then CONFIG_DIR="$(abspath "$CONFIG_ARG")"; else CONFIG_DIR="$ROOT/econ-forecast/config/synthetic/org"; fi
PRESETS=""
[[ -z "$PRESETS_ARG" ]] || PRESETS="$(abspath "$PRESETS_ARG")"

# --- validation: everything that can be checked cheaply, before any stage runs ---------------------
DATE_RE='^[0-9]{4}-(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])$'
if [[ "$END_DATE_ARG" == "today" ]]; then
  END_DATE="$(date +%F)"
elif [[ "$END_DATE_ARG" =~ $DATE_RE ]] && valid_date "$END_DATE_ARG"; then
  END_DATE="$END_DATE_ARG"
else
  invalid "--end-date must be 'today' or a real calendar date, YYYY-MM-DD: $END_DATE_ARG"
fi

if [[ -n "$DSN" ]]; then
  DB_NAME="${DSN%%\?*}"
  DB_NAME="${DB_NAME##*/}"
  case "$DB_NAME" in
    ?*_synth) ;;
    *)
      # The name is echoed only when the DSN is a URL and the name a plain identifier, so a
      # malformed DSN can never carry its password into a message.
      SHOWN_NAME="(not shown)"
      if [[ "$DSN" == *://* && "$DB_NAME" =~ ^[A-Za-z0-9_.-]+$ ]]; then SHOWN_NAME="'$DB_NAME'"; fi
      invalid "refusing --dsn: the database name $SHOWN_NAME must end with '_synth' (the loader's own guard); there is no default database" ;;
  esac
fi

[[ -d "$CONFIG_DIR" ]] || invalid "--config-dir is not a directory: $CONFIG_DIR"
for f in $CONFIG_FILES; do
  [[ -f "$CONFIG_DIR/$f" ]] || invalid "--config-dir $CONFIG_DIR has no $f (needs $CONFIG_FILES)"
done
[[ -z "$PRESETS" || -f "$PRESETS" ]] || invalid "--presets is not a file: $PRESETS"

# --output is replaced, so the inputs must not live inside it (compared as written and, where the
# directories exist, with symbolic links resolved).
physdir() { (cd "$1" 2>/dev/null && pwd -P) || printf '%s' "$1"; }
under() {  # path dir: path is dir or below it
  [[ "$1" == "$2" || "$1" == "$2"/* ]]
}
OUTPUT_REAL="$OUTPUT"
[[ ! -d "$OUTPUT" ]] || OUTPUT_REAL="$(physdir "$OUTPUT")"
CONFIG_REAL="$(physdir "$CONFIG_DIR")"
if under "$CONFIG_DIR" "$OUTPUT" || under "$CONFIG_REAL" "$OUTPUT_REAL"; then
  invalid "--config-dir $CONFIG_DIR is inside --output $OUTPUT, which this run replaces; move it out"
fi
if [[ -n "$PRESETS" ]]; then
  PRESETS_REAL="$(physdir "$(dirname "$PRESETS")")/$(basename "$PRESETS")"
  if under "$PRESETS" "$OUTPUT" || under "$PRESETS_REAL" "$OUTPUT_REAL"; then
    invalid "--presets $PRESETS is inside --output $OUTPUT, which this run replaces; move it out"
  fi
fi

# An existing --output (empty or not) is moved aside, because the generator refuses any existing path.
OUTPUT_EXISTS=0
ASIDE="${OUTPUT}.prev-$$"
if [[ -L "$OUTPUT" ]]; then
  invalid "--output is a symbolic link: $OUTPUT"
elif [[ -e "$OUTPUT" && ! -d "$OUTPUT" ]]; then
  invalid "--output exists and is not a directory: $OUTPUT"
elif [[ -d "$OUTPUT" ]]; then
  OUTPUT_EXISTS=1
  if [[ -n "$(ls -A "$OUTPUT")" ]]; then
    (( FORCE )) || invalid "--output is not empty: $OUTPUT (pass --force to replace an earlier refresh; only $OWNED_ENTRIES are replaced)"
    EXTRAS=""
    while IFS= read -r -d '' entry; do
      name="${entry##*/}"
      case " $OWNED_ENTRIES " in
        *" $name "*) ;;
        *) EXTRAS="$EXTRAS $name" ;;
      esac
    done < <(find "$OUTPUT" -mindepth 1 -maxdepth 1 -print0)
    [[ -z "$EXTRAS" ]] || invalid "--force refuses $OUTPUT: it holds entries a refresh does not create:${EXTRAS}; nothing was changed"
  fi
  if [[ -e "$ASIDE" || -L "$ASIDE" ]]; then
    invalid "cannot move $OUTPUT aside: $ASIDE already exists"
  fi
fi

if (( DUMP )); then
  DUMP_TARGET="${OUT:-$ROOT/deploy/seed/econ_synth.sql.gz}"
  [[ "$DUMP_TARGET" == /* ]] || DUMP_TARGET="$ROOT/$DUMP_TARGET"
  if ! command -v docker >/dev/null 2>&1; then
    if (( DRY )); then
      echo "$LOG_TAG note: docker is not installed; --dump would fail" >&2
    else
      invalid "--dump needs docker, which is not installed"
    fi
  fi
else
  DUMP_TARGET=""
fi

# --- the stage commands --------------------------------------------------------------------------------
EXPORT_PATH="$OUTPUT/forecast/forecast_export.json"
GEN_CMD=("$ROOT/econ-forecast/utils/synth_org.sh" --config-dir "$CONFIG_DIR" --output "$OUTPUT" --end-date "$END_DATE")
FC_CMD=("$ROOT/econ-forecast/utils/dashboard_forecast.sh" --org-demo "$OUTPUT")
[[ -z "$PRESETS" ]] || FC_CMD+=(--presets "$PRESETS")
LOAD_CMD=("$ROOT/utils/load_synthetic.sh" --dataset "$OUTPUT/org" --dsn "$DSN" --replace --forecast-export "$EXPORT_PATH")
DUMP_CMD=("$ROOT/utils/synth_dump.sh" --dataset "$OUTPUT/org")

TOTAL=2
[[ -z "$DSN" ]] || TOTAL=$((TOTAL + 1))
(( DUMP )) && TOTAL=$((TOTAL + 1))

# --- state for the closing JSON --------------------------------------------------------------------------
STATUS="complete"
STAGES_JSON=""
FAILED_STAGE=""
FAILED_RC=""
ERROR=""
ORIGIN=""; DATA_THROUGH=""; SCOPES=""; PRESET_COUNT=""; GENERATED_AT=""
EXPORT_REPORTED=""
DUMP_REPORTED=""

add_stage() {  # name status seconds [command]
  local entry
  entry="{\"name\": $(jstr "$1"), \"status\": $(jstr "$2")"
  [[ -z "$3" ]] || entry="$entry, \"seconds\": $3"
  [[ -z "${4:-}" ]] || entry="$entry, \"command\": $(jstr "$4")"
  entry="$entry}"
  if [[ -z "$STAGES_JSON" ]]; then STAGES_JSON="$entry"; else STAGES_JSON="$STAGES_JSON, $entry"; fi
}

final_json() {
  local json
  json="{\"status\": $(jstr "$STATUS"), \"output\": $(jstr "$OUTPUT"), \"end_date\": $(jstr "$END_DATE")"
  json="$json, \"origin\": $(jstr "$ORIGIN"), \"data_through\": $(jstr "$DATA_THROUGH")"
  json="$json, \"scope_count\": $(jnum "$SCOPES"), \"preset_count\": $(jnum "$PRESET_COUNT")"
  json="$json, \"generated_at\": $(jstr "$GENERATED_AT")"
  json="$json, \"stages\": [$STAGES_JSON]"
  json="$json, \"export_path\": $(jstr "$EXPORT_REPORTED"), \"dump_path\": $(jstr "$DUMP_REPORTED")"
  if [[ "$STATUS" == "failed" ]]; then
    json="$json, \"failed_stage\": $(jstr "$FAILED_STAGE"), \"exit_code\": $(jnum "$FAILED_RC")"
    [[ -z "$ERROR" ]] || json="$json, \"error\": $(jstr "$ERROR")"
  fi
  printf '%s}\n' "$json"
}

# --- dry run -----------------------------------------------------------------------------------------------
if (( DRY )); then
  n=0
  plan() {  # name command...
    local name="$1"; shift
    n=$((n + 1))
    echo "$LOG_TAG stage $n/$TOTAL $name (dry run)" >&2
    echo "  $(show_cmd "$@")" >&2
    add_stage "$name" planned "" "$(show_cmd "$@")"
  }
  if [[ "$OUTPUT_EXISTS" == 1 ]]; then
    echo "$LOG_TAG dry run: would move $OUTPUT aside to $ASIDE, and delete it once generate has succeeded" >&2
  fi
  plan generate "${GEN_CMD[@]}"
  plan forecast "${FC_CMD[@]}"
  [[ -z "$DSN" ]] || plan load "${LOAD_CMD[@]}"
  if (( DUMP )); then
    echo "$LOG_TAG dry run: the dump would be written to $DUMP_TARGET" >&2
    plan dump "${DUMP_CMD[@]}"
  fi
  echo "$LOG_TAG dry run: nothing was changed or run" >&2
  STATUS="dry_run"
  EXPORT_REPORTED="$EXPORT_PATH"
  DUMP_REPORTED="$DUMP_TARGET"
  final_json
  exit 0
fi

# --- the earlier refresh, aside --------------------------------------------------------------------------
ASIDE_ACTIVE=0   # 1 while ASIDE holds the previous demo and OUTPUT is the generator's
GENERATE_DONE=0

remove_owned() {  # dir: delete exactly the entries a refresh creates, then the directory if empty
  local d="$1" name
  for name in $OWNED_ENTRIES; do
    if [[ -e "$d/$name" || -L "$d/$name" ]]; then rm -rf -- "${d:?}/$name"; fi
  done
  rmdir "$d" 2>/dev/null
}

# Generate did not complete: remove what it left at OUTPUT (only what a refresh creates), then put
# the previous demo, if there was one, back. It acts only on what this run itself put in place:
#   - ASIDE_ACTIVE=1 and ASIDE exists: the previous demo was moved aside, so OUTPUT can only be
#     the generator's partial output;
#   - ASIDE_ACTIVE=1 and ASIDE missing: the move never happened, OUTPUT is still the user's demo
#     and is left alone;
#   - ASIDE_ACTIVE=0: only when OUTPUT did not exist before this run.
restore_aside() {
  if [[ "$ASIDE_ACTIVE" == 1 ]]; then
    if [[ ! -e "$ASIDE" && ! -L "$ASIDE" ]]; then
      ASIDE_ACTIVE=0
      return 0
    fi
  elif [[ "$OUTPUT_EXISTS" == 1 ]]; then
    return 0
  fi
  if [[ -e "$OUTPUT" || -L "$OUTPUT" ]]; then
    if ! remove_owned "$OUTPUT"; then
      echo "$LOG_TAG could not clear the partial output at $OUTPUT" >&2
      [[ "$ASIDE_ACTIVE" != 1 ]] || echo "$LOG_TAG the previous demo is kept at $ASIDE" >&2
      return 1
    fi
  fi
  [[ "$ASIDE_ACTIVE" == 1 ]] || return 0
  echo "$LOG_TAG restoring the previous demo from $ASIDE to $OUTPUT ..." >&2
  if mv "$ASIDE" "$OUTPUT"; then
    ASIDE_ACTIVE=0
    echo "$LOG_TAG the previous demo is back in $OUTPUT" >&2
  else
    echo "$LOG_TAG could not move it back; the previous demo is kept at $ASIDE" >&2
    return 1
  fi
}

STAGE_OUT=""
CHILD=""
CURRENT_STAGE=""
cleanup() { [[ -z "$STAGE_OUT" ]] || rm -f "$STAGE_OUT"; }
trap cleanup EXIT

on_signal() {  # exit-code
  local code="$1"
  trap '' HUP INT QUIT TERM
  echo "$LOG_TAG interrupted during ${CURRENT_STAGE:-startup}" >&2
  if [[ -n "$CHILD" ]]; then
    kill -TERM -- "-$CHILD" 2>/dev/null || kill -TERM "$CHILD" 2>/dev/null || true
    wait "$CHILD" 2>/dev/null || true
  fi
  [[ "$GENERATE_DONE" == 1 ]] || restore_aside || true
  STATUS="failed"; FAILED_STAGE="$CURRENT_STAGE"; FAILED_RC="$code"; ERROR="interrupted"
  [[ -z "$CURRENT_STAGE" ]] || add_stage "$CURRENT_STAGE" failed ""
  final_json
  exit "$code"
}
trap 'on_signal 129' HUP
trap 'on_signal 130' INT
trap 'on_signal 131' QUIT
trap 'on_signal 143' TERM

STAGE_OUT="$(mktemp "${TMPDIR:-/tmp}/synth-refresh.XXXXXX")"

if [[ "$OUTPUT_EXISTS" == 1 ]]; then
  echo "$LOG_TAG moving the earlier $OUTPUT aside to $ASIDE (deleted once generate has succeeded) ..." >&2
  ASIDE_ACTIVE=1   # set first: a signal between the flag and the move must still be handled
  if ! mv "$OUTPUT" "$ASIDE"; then
    ASIDE_ACTIVE=0
    invalid "could not move $OUTPUT aside to $ASIDE; nothing was changed"
  fi
fi

STAGE_NO=0
fail_stage() {  # name rc
  STATUS="failed"; FAILED_STAGE="$1"; FAILED_RC="$2"
  echo "$LOG_TAG stage $1 failed (exit $2); completed outputs stay in place" >&2
  [[ "$GENERATE_DONE" == 1 ]] || restore_aside || true
  final_json
  exit 1
}

# The stage runs in its own process group (job control) so an interrupt can stop all of it; its
# stdout is shown live on stderr and kept in $STAGE_OUT.
set -m
run_stage() {  # name command...
  local name="$1"; shift
  CURRENT_STAGE="$name"
  STAGE_NO=$((STAGE_NO + 1))
  echo "$LOG_TAG stage $STAGE_NO/$TOTAL $name ..." >&2
  local start=$SECONDS rc=0
  : >"$STAGE_OUT"
  ( set +e; "$@" </dev/null | tee "$STAGE_OUT" >&2; exit "${PIPESTATUS[0]}" ) &
  CHILD=$!
  wait "$CHILD" || rc=$?
  CHILD=""
  STAGE_SECONDS=$((SECONDS - start))
  if (( rc != 0 )); then
    add_stage "$name" failed "$STAGE_SECONDS"
    fail_stage "$name" "$rc"
  fi
}

PY=""

# 1 generate
run_stage generate "${GEN_CMD[@]}"
if [[ ! -f "$OUTPUT/org/manifest.json" ]]; then
  echo "$LOG_TAG the generator reported success but $OUTPUT/org/manifest.json is missing" >&2
  add_stage generate failed "$STAGE_SECONDS"
  fail_stage generate 1
fi
GENERATE_DONE=1
add_stage generate ok "$STAGE_SECONDS"
if [[ "$ASIDE_ACTIVE" == 1 ]]; then
  if remove_owned "$ASIDE"; then
    echo "$LOG_TAG the earlier demo ($ASIDE) is deleted" >&2
  else
    echo "$LOG_TAG warning: could not delete the earlier demo at $ASIDE; remove it by hand" >&2
  fi
  ASIDE_ACTIVE=0
fi
# A fresh checkout gets its venv from the generator wrapper.
add_venv_to_path
PY="$ROOT/venv/bin/python"
[[ -x "$PY" ]] || PY="python3"

# 2 forecast: its stdout is exactly one JSON line
run_stage forecast "${FC_CMD[@]}"
SUMMARY="$(tail -n 1 "$STAGE_OUT")"
FIELDS=""
if ! FIELDS="$(printf '%s' "$SUMMARY" | "$PY" -c '
import json, sys
d = json.loads(sys.stdin.read())
assert d.get("status") == "complete", d.get("status")
print("\t".join([str(d["origin"]), str(d["data_through"]), str(int(d["scope_count"])),
                 str(int(d["preset_count"])), str(d.get("generated_at", ""))]))
' 2>/dev/null)"; then
  echo "$LOG_TAG the forecast summary line is not the expected JSON: $SUMMARY" >&2
  add_stage forecast failed "$STAGE_SECONDS"
  fail_stage forecast 1
fi
IFS=$'\t' read -r ORIGIN DATA_THROUGH SCOPES PRESET_COUNT GENERATED_AT <<<"$FIELDS"
if [[ ! -f "$EXPORT_PATH" ]]; then
  echo "$LOG_TAG the forecast reported success but $EXPORT_PATH is missing" >&2
  add_stage forecast failed "$STAGE_SECONDS"
  fail_stage forecast 1
fi
EXPORT_REPORTED="$EXPORT_PATH"
add_stage forecast ok "$STAGE_SECONDS"

# 3 load (only with --dsn)
if [[ -n "$DSN" ]]; then
  run_stage load "${LOAD_CMD[@]}"
  add_stage load ok "$STAGE_SECONDS"
fi

# 4 dump (only with --dump)
if (( DUMP )); then
  echo "$LOG_TAG the dump will be written to $DUMP_TARGET" >&2
  run_stage dump "${DUMP_CMD[@]}"
  if [[ ! -f "$DUMP_TARGET" ]]; then
    echo "$LOG_TAG the dump stage reported success but $DUMP_TARGET is missing" >&2
    add_stage dump failed "$STAGE_SECONDS"
    fail_stage dump 1
  fi
  DUMP_REPORTED="$DUMP_TARGET"
  add_stage dump ok "$STAGE_SECONDS"
fi
CURRENT_STAGE=""

# --- what next ------------------------------------------------------------------------------------------------
{
  echo "$LOG_TAG done: forecast as of $DATA_THROUGH (origin $ORIGIN), $SCOPES scopes x $PRESET_COUNT presets"
  if (( DUMP )); then
    echo "$LOG_TAG next: copy ${DUMP_TARGET#"$ROOT/"} to the host's deploy/seed/ and run utils/aws_synth_load.sh --replace"
    echo "$LOG_TAG       --replace is required: this dump carries forecast_runs and forecast_scope_series, which an"
    echo "$LOG_TAG       econ_synth loaded from an earlier dump does not have"
  fi
  if [[ -z "$DSN" ]]; then
    echo "$LOG_TAG to load it into a database whose name ends in _synth (create it first):"
    echo "$LOG_TAG   utils/load_synthetic.sh --dataset $OUTPUT/org --dsn <DSN ending in _synth> --replace \\"
    echo "$LOG_TAG     --forecast-export $EXPORT_PATH"
  fi
  if (( ! DUMP )); then
    echo "$LOG_TAG to build the AWS seed dump: utils/synth_dump.sh --dataset $OUTPUT/org"
  fi
} >&2

final_json
