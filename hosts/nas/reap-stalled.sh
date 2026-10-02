# Remove Sonarr/Radarr downloads that will never finish, blocklist the
# release, and let the *arr search for another one. See reap-stalled.nix.
#
# A torrent is dead when either
#   * it has sat in "downloading metadata" for META_GRACE_H hours of being
#     active -- nobody in the swarm even has the file list, or
#   * it is actively downloading, no seeder is connected, the connected peers
#     between them do not hold a full copy (availability < 1), and its
#     verified byte count has not moved for STALL_GRACE_H hours.
#
# The byte count is tracked here, run to run, because qBittorrent has no
# "last downloaded" timestamp: last_activity also moves on *upload*, and a
# dead swarm is full of stuck leechers trading the same pieces with each
# other, so a torrent that has not gained a byte in weeks still looks active.
#
# Before removing a multi-file torrent, every file in it that did finish is
# imported through the *arr's manual import, so a season pack missing one
# episode still delivers the other nine.
#
# Only torrents an *arr queue is waiting on are ever touched: reel-api's own
# grabs and anything added by hand are left alone.

set -euo pipefail

: "${QBIT_URL:?}" "${SONARR_URL:?}" "${RADARR_URL:?}"
: "${CREDENTIALS_DIRECTORY:?}" "${STATE_DIRECTORY:?}"
META_GRACE_H=${META_GRACE_H:-6}
STALL_GRACE_H=${STALL_GRACE_H:-24}
# Set to any non-empty value to report what would be removed and change nothing.
DRY_RUN=${DRY_RUN:-}

STATE=$STATE_DIRECTORY/progress.json
now=$(date +%s)

log() { printf '%s\n' "$*"; }

# The *arr API keys live in each app's config.xml, handed in by systemd as
# credentials, so there is no second copy of them to drift.
declare -A url key
url[sonarr]=$SONARR_URL
url[radarr]=$RADARR_URL
for app in sonarr radarr; do
  key[$app]=$(sed -n 's:.*<ApiKey>\(.*\)</ApiKey>.*:\1:p' "$CREDENTIALS_DIRECTORY/$app.xml")
done

arr() { # app method path [curl args...]
  local app=$1 method=$2 path=$3
  shift 3
  curl -fsS -X "$method" -H "X-Api-Key: ${key[$app]}" \
    -H 'Content-Type: application/json' "${url[$app]}/api/v3/$path" "$@"
}

qbit() { # path [curl args...]
  local path=$1
  shift
  curl -fsS "$QBIT_URL/api/v2/$path" "$@"
}

queue_ids() { # app hash -> JSON array of that download's queue record ids
  arr "$1" GET 'queue?pageSize=1000' |
    jq -c --arg h "$2" '[.records[] | select((.downloadId // "" | ascii_downcase) == $h) | .id]'
}

# Import the files of a dead multi-file torrent that did finish. Returns
# non-zero only if an import was attempted and did not complete, in which
# case the caller must not delete the data.
salvage() { # app hash torrent-json
  local app=$1 h=$2 t=$3 files dir done_paths items payload cmd status i
  files=$(qbit "torrents/files?hash=$h")
  [[ $(jq length <<<"$files") -gt 1 ]] || return 0

  # Unfinished torrents live under download_path (the incomplete/ tree);
  # file names from the API are relative to it.
  dir=$(jq -r 'if .download_path != "" then .download_path else .save_path end' <<<"$t")
  done_paths=$(jq -c --arg dir "$dir" '[.[] | select(.progress == 1) | "\($dir)/\(.name)"]' <<<"$files")
  [[ $done_paths != '[]' ]] || return 0

  items=$(arr "$app" GET manualimport -G \
    --data-urlencode "folder=$(jq -r .content_path <<<"$t")" \
    --data-urlencode filterExistingFiles=true)

  # Only files that are complete, that the *arr matched to something, and
  # that it has no objection to. "copy" leaves the torrent's own file in
  # place until the torrent is removed below.
  #
  # No downloadId, and none on the GET above: both make Sonarr (v4.0.18)
  # resolve the tracked download's output path, which an unfinished torrent
  # does not have -- a NullReferenceException, after the files were already
  # imported, which would read here as a failed import.
  payload=$(jq -c --arg app "$app" --argjson finished "$done_paths" '
    [.[] | select((.path | IN($finished[])) and ((.rejections // []) | length == 0))
         | if $app == "sonarr"
           then select((.episodes // []) | length > 0)
                | {path, folderName, seriesId: .series.id, episodeIds: [.episodes[].id]}
           else select(.movie != null)
                | {path, folderName, movieId: .movie.id}
           end
         + {quality: .quality, languages: .languages, releaseGroup: .releaseGroup}]
    | if length == 0 then empty
      else {name: "ManualImport", importMode: "copy", files: .} end' <<<"$items")
  [[ -n $payload ]] || return 0

  log "  importing $(jq '.files | length' <<<"$payload") finished file(s) first"
  cmd=$(arr "$app" POST command --data "$payload" | jq -r .id)
  # Copies of 4K files take minutes; allow an hour before giving up.
  for ((i = 0; i < 360; i++)); do
    status=$(arr "$app" GET "command/$cmd" | jq -r .status)
    case $status in
      completed) return 0 ;;
      failed | aborted | cancelled | orphaned) break ;;
    esac
    sleep 10
  done
  log "  ! import command $cmd ended as '$status'"
  return 1
}

remove() { # app hash
  local app=$1 h=$2 ids
  ids=$(queue_ids "$app" "$h")
  if [[ $ids == '[]' ]]; then
    # Everything in it was salvaged; nothing left to blocklist or re-search.
    qbit torrents/delete --data "hashes=$h&deleteFiles=true" >/dev/null
    log "  removed from qBittorrent (fully salvaged)"
    return
  fi
  # blocklist: this exact release is never grabbed again -- a swarm with no
  # seeders almost never comes back. skipRedownload=false: the *arr
  # immediately searches for a different release of the same episodes.
  arr "$app" DELETE 'queue/bulk?removeFromClient=true&blocklist=true&skipRedownload=false' \
    --data "{\"ids\":$ids}" >/dev/null
  log "  removed, blocklisted, re-search triggered for $(jq length <<<"$ids") queue item(s)"
}

torrents=$(qbit torrents/info)
[[ -f $STATE ]] || echo '{}' >"$STATE"

# Advance the per-torrent progress record. A torrent's clock restarts
# whenever its verified byte count changes or it is not actively downloading
# (queued, paused, checking): a torrent that was never given a slot has not
# had the chance to stall.
state=$(jq -c --argjson now "$now" --slurpfile old "$STATE" '
  ($old[0]) as $old
  | map(select(.state | IN("stalledDL", "forcedDL", "downloading", "metaDL", "forcedMetaDL")))
  | map(.hash as $h | .completed as $c
        | {key: $h, value: (if $old[$h].completed == $c then $old[$h]
                            else {completed: $c, since: $now} end)})
  | from_entries' <<<"$torrents")
[[ -n $DRY_RUN ]] || printf '%s\n' "$state" >"$STATE"

reaped=0
for app in sonarr radarr; do
  mapfile -t hashes < <(arr "$app" GET 'queue?pageSize=1000' |
    jq -r '[.records[].downloadId // empty | ascii_downcase] | unique[]')

  for h in "${hashes[@]}"; do
    t=$(jq -c --arg h "$h" 'first(.[] | select(.hash == $h)) // empty' <<<"$torrents")
    [[ -n $t ]] || continue

    reason=$(jq -r --argjson now "$now" --argjson st "$state" \
      --argjson mg "$((META_GRACE_H * 3600))" --argjson sg "$((STALL_GRACE_H * 3600))" '
      ($st[.hash].since // $now) as $since
      | if (.state | IN("metaDL", "forcedMetaDL")) and ($now - $since) > $mg then
          "no metadata after \(($now - $since) / 3600 | floor)h"
        elif (.state | IN("stalledDL", "forcedDL", "downloading"))
             and .num_seeds == 0 and .availability < 1 and ($now - $since) > $sg then
          "no seeders, no progress for \(($now - $since) / 3600 | floor)h at \(.progress * 1000 | floor / 10)%"
        else empty end' <<<"$t")
    [[ -n $reason ]] || continue

    log "$app: $(jq -r .name <<<"$t") -- $reason"
    reaped=$((reaped + 1))
    [[ -z $DRY_RUN ]] || continue
    if ! salvage "$app" "$h" "$t"; then
      log "  keeping the data; will retry next run"
      continue
    fi
    remove "$app" "$h"
  done
done

log "done: $reaped dead download(s)${DRY_RUN:+ (dry run)}"
