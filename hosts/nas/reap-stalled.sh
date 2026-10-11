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
#
# It also makes one import Radarr refuses to make on its own. A finished
# download whose name does not parse to the film -- "The Hunt (Jagten)
# (2012) ...", the original title in brackets -- and that matched only by the
# IMDb id an indexer attached, on an automatic search, sits at importBlocked
# ("release was matched to movie by ID. Manual Import required.") forever:
# Radarr re-checks it every minute and reaches the same verdict. The id is
# all that ties such a download to the film, and an indexer can attach the
# wrong one, so it is imported here only when every title in its name is a
# known title of that film and the year agrees (the identity check), and
# Radarr's manual import offers exactly one file for it with no objection
# (that only says the file is importable). Anything less stays blocked for a
# human.

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

# qBittorrent's API key goes in a header file in the unit's PrivateTmp rather
# than on curl's command line: it is worth code execution as qbittorrent, and
# argv is readable by every local user through /proc.
qbit_auth=$(mktemp)
trap 'rm -f "$qbit_auth"' EXIT
printf 'Authorization: Bearer %s\n' "$(<"$CREDENTIALS_DIRECTORY/qbit-api-key")" >"$qbit_auth"

qbit() { # path [curl args...]
  local path=$1
  shift
  curl -fsS -H "@$qbit_auth" "$QBIT_URL/api/v2/$path" "$@"
}

queue_ids() { # app hash -> JSON array of that download's queue record ids
  arr "$1" GET 'queue?pageSize=1000' |
    jq -c --arg h "$2" '[.records[] | select((.downloadId // "" | ascii_downcase) == $h) | .id]'
}

# Wait for an import command. Copies of 4K files take minutes; allow an hour
# before giving up.
wait_command() { # app command-id
  local app=$1 cmd=$2 status i
  # Callers run with errexit off (inside if), so a failed POST arrives here
  # as an empty id -- and GET command/ lists every command, which would poll
  # for the full hour.
  if [[ ! $cmd =~ ^[0-9]+$ ]]; then
    log "  ! no import command was started"
    return 1
  fi
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

# Import the files of a dead multi-file torrent that did finish. Returns
# non-zero only if an import was attempted and did not complete, in which
# case the caller must not delete the data.
salvage() { # app hash torrent-json
  local app=$1 h=$2 t=$3 files dir done_paths items payload cmd
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
  wait_command "$app" "$cmd"
}

# One unreadable record (a film deleted mid-run, Radarr restarting) is
# skipped rather than ending the run for the records after it.
skip() { # name reason
  log "radarr: $1 -- $2, skipped"
}

# Import a finished Radarr download blocked as "matched to movie by ID" (see
# the top of this file) if every check agrees it is that film.
unblock() { # queue-record-json
  local r=$1 id h movie_id name movie ok items payload cmd
  id=$(jq -r .downloadId <<<"$r")
  h=${id,,}
  movie_id=$(jq -r .movieId <<<"$r")
  name=$(jq -r --arg h "$h" 'first(.[] | select(.hash == $h and .progress == 1) | .name) // empty' <<<"$torrents")
  [[ -n $name ]] || return 0

  # The title check Radarr skips for an id match. What its parser made of the
  # name ("The Hunt (Jagten)") is split at brackets, quotes, slashes, " - "
  # and " aka ", and every piece must be the film's title, original title or
  # an alternate title, compared as lowercase letters and digits. Titles in
  # other scripts are left out: stripped to ASCII, "Гладiатор" is "i".
  # The one guess left open: a Latin translated title of this film that is
  # also another film's title from the same year.
  movie=$(arr radarr GET "movie/$movie_id") || { skip "$name" "film $movie_id unreadable"; return 0; }
  ok=$(arr radarr GET parse -G --data-urlencode "title=$name" | jq --argjson m "$movie" '
    def clean: ascii_downcase | gsub("[^a-z0-9]"; "");
    def latin: test("^[\\p{Latin}\\p{N}\\p{P}\\p{S}\\p{Z}\\p{M}]*$");
    [$m.title, $m.originalTitle, ($m.alternateTitles // [])[].title
     | strings | select(latin) | clean | select(. != "")] as $known
    | .parsedMovieInfo as $p
    | [($p.movieTitles // [])[]
       | splits("\\s+a\\.?k\\.?a\\.?\\s+|\\s+-\\s+|[()\\[\\]/\"]"; "i")
       | clean | select(. != "")] as $pieces
    | ($p.year // 0) > 1800 and ($p.year | IN($m.year, $m.secondaryYear))
      and $pieces != [] and all($pieces[]; IN($known[]))') ||
    { skip "$name" "parse failed"; return 0; }
  if [[ $ok != true ]]; then
    log "radarr: $name -- import blocked, left alone: its titles or year are not all $(jq -r '"\(.title) (\(.year))"' <<<"$movie")'s"
    return 0
  fi

  # With the downloadId, Radarr records the import against the download, which
  # clears the queue row and lets Remove Completed take the torrent away;
  # without it the film would be imported and the row would stay blocked.
  # "copy" is a hardlink on this box (copyUsingHardlinks). Deliberately
  # strict: a sample outside a Sample/ folder makes two files and a human.
  items=$(arr radarr GET manualimport -G --data-urlencode "downloadId=$id" \
    --data-urlencode filterExistingFiles=true) || { skip "$name" "manual import preview failed"; return 0; }
  payload=$(jq -c --arg id "$id" --argjson mid "$movie_id" '
    if length == 1 and .[0].movie.id == $mid and ((.[0].rejections // []) | length == 0)
    then {name: "ManualImport", importMode: "copy",
          files: [.[0] | {path, folderName, movieId: .movie.id, quality, languages,
                          releaseGroup, indexerFlags: (.indexerFlags // 0), downloadId: $id}]}
    else empty end' <<<"$items")
  if [[ -z $payload ]]; then
    log "radarr: $name -- import blocked, left alone: manual import offers $(jq length <<<"$items") file(s), not one clean one$(
      jq -r '[.[].rejections[]?.reason] | unique | if length > 0 then " (" + join("; ") + ")" else "" end' <<<"$items")"
    return 0
  fi

  log "radarr: $name -- import blocked (matched by ID only), importing"
  if [[ -n $DRY_RUN ]]; then
    unblocked=$((unblocked + 1))
    return 0
  fi
  cmd=$(arr radarr POST command --data "$payload" | jq -r .id) || { skip "$name" "import command refused"; return 0; }
  wait_command radarr "$cmd" || return 0
  # A completed command only means Radarr tried: a failed copy is reported
  # inside it, not as a failed command. The import is recorded in the film's
  # history under the download's id.
  if arr radarr GET "history/movie?movieId=$movie_id" | jq -e --arg h "$h" '
       any(.[]; .eventType == "downloadFolderImported"
                and (.downloadId // "" | ascii_downcase) == $h)' >/dev/null; then
    unblocked=$((unblocked + 1))
    log "  imported"
  else
    log "  ! import command finished, but Radarr's history shows no import"
  fi
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

# Radarr only: Sonarr matches a release by its title first and the id only
# as a fallback, and none of its grabs here has ever matched by id.
unblocked=0
mapfile -t blocked < <(arr radarr GET 'queue?pageSize=1000' | jq -c '
  [.records[] | select(.trackedDownloadState == "importBlocked"
                       and .downloadId != null and .movieId != null
                       and any(.statusMessages[]?.messages[]?; test("matched to movie by ID")))
   | {downloadId, movieId}]
  | unique_by(.downloadId)[]')
for r in "${blocked[@]}"; do
  unblock "$r"
done

log "done: $reaped dead download(s), $unblocked blocked import(s) cleared${DRY_RUN:+ (dry run)}"
