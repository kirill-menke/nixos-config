#!/usr/bin/env bash
# create-pr (aliased as ccpr): opens CodeCommit PRs, waits for the heidi review
# bot, merges. Kept byte-identical in two places, so edit both and `cmp` them:
#   - ~/.config/nixos/hosts/laptop/create-pr.sh (kirill-menke/nixos-config, the laptop)
#   - /etc/nixos/create-pr.sh on vibe-machine (every developer there)
# Each Nix config wraps it with jq on the PATH; git, aws and claude come from the
# user's environment.
#
#   create-pr <source-branch> <target-branch> [<target-branch>...]
#     [--merge|--merge-after-review] [--destroy] [--no-deploy] [--skip-review]
#     [--force-blue|--force-green]
#
# A PR into dev first gets its reviewer page: heidi's review-briefing skill,
# whose scripts/ensure-page.sh (run as it stands on origin/dev) has a missing or
# stale page written and pushed onto the branch, so every PR documents what it
# changes. The same check runs again before a merge. A repo without that script
# on origin/dev skips the step.
set -euo pipefail
export AWS_PAGER=""

PROFILE="research"
REGION="eu-central-1"
MERGE=false
AWAIT_REVIEW=false
# By default the merge leaves the source branch's per-developer AWS stack
# standing: the title gets [no-cleanup] (below). The branch cleanup is
# irreversible (it destroys the stack, and a redeploy costs a full apply), so
# it takes an explicit --destroy.
DESTROY=false
NO_DEPLOY=false
SKIP_REVIEW=false
FORCE_SLOT=""

args=()
for arg in "$@"; do
  case "$arg" in
    --merge) MERGE=true ;;
    --merge-after-review) AWAIT_REVIEW=true ;;
    --destroy) DESTROY=true ;;
    --no-deploy) NO_DEPLOY=true ;;
    --skip-review) SKIP_REVIEW=true ;;
    --force-blue) FORCE_SLOT="blue" ;;
    --force-green) FORCE_SLOT="green" ;;
    # An unknown flag (e.g. the removed --keep) must not become a target branch.
    --*)
      echo "create-pr: unknown flag $arg"
      exit 1
      ;;
    *) args+=("$arg") ;;
  esac
done

if [[ ${#args[@]} -lt 2 ]]; then
  echo "Usage: create-pr <source-branch> <target-branch> [<target-branch>...] [--merge|--merge-after-review] [--destroy] [--no-deploy] [--skip-review] [--force-blue|--force-green]"
  exit 1
fi

SOURCE="${args[0]}"
TARGETS=("${args[@]:1}")
REPO=$(basename "$(git remote get-url origin)" .git | sed 's|.*/||')

if [[ "$MERGE" == "true" && "$AWAIT_REVIEW" == "true" ]]; then
  echo "--merge merges at once, --merge-after-review after the review bot: pick one."
  exit 1
fi
for TARGET in "${TARGETS[@]}"; do
  # Only the production release pipeline writes main (heidi CLAUDE.md).
  if [[ "$TARGET" == "main" ]]; then
    echo "main is not a valid target: only the production release pipeline writes it."
    exit 1
  fi
  if [[ "$AWAIT_REVIEW" == "true" && "$TARGET" != "dev" ]]; then
    echo "--merge-after-review: the review bot only reviews PRs into dev, not $TARGET."
    exit 1
  fi
done
if ! git ls-remote --exit-code --heads origin "$SOURCE" >/dev/null; then
  echo "$SOURCE is not on origin yet: commit + push it first."
  exit 1
fi

# The heidi review bot (tofu/dev-ci, buildspecs/scripts/pr-review) keeps one
# summary comment per PR into dev, written by this role session and ending
# in a footer that names the source commit it covers. That footer for the
# PR's current source commit means the review of the latest push is done.
REVIEW_AUTHOR_SUFFIX=":assumed-role/role-heidi-pr-review-poster/heidi-pr-review"

awscc() { aws "$@" --profile "$PROFILE" --region "$REGION"; }

# Path of the worktree that has branch $1 checked out; empty if none.
branch_worktree() {
  git worktree list --porcelain |
    awk -v ref="refs/heads/$1" '/^worktree /{p=substr($0,10)} $0=="branch " ref{print p; exit}'
}

# Rebase branch $1 onto $2 without switching any checkout's branch: in the
# worktree that has it checked out, else in a throwaway one (hooks off).
rebase_branch() {
  local wt
  wt=$(branch_worktree "$1")
  if [[ -n "$wt" ]]; then
    git -C "$wt" rebase "$2"
    return
  fi
  wt=$(mktemp -d)
  git -c core.hooksPath=/dev/null worktree add --quiet "$wt" "$1"
  if ! git -C "$wt" -c core.hooksPath=/dev/null rebase "$2"; then
    echo "Rebase of $1 stopped in $wt: resolve it there, or: git -C $wt rebase --abort"
    exit 1
  fi
  git worktree remove "$wt"
}

# Offers to push the commits on $1 that origin does not have yet.
push_pending() {
  local wt ahead c
  wt=$(branch_worktree "$1")
  if [[ -n "$wt" && -n "$(git -C "$wt" status --porcelain)" ]]; then
    echo "Note: $wt has uncommitted changes; the PR only gets what is committed and pushed."
  fi
  git fetch origin "$1" --quiet || return 1
  ahead=$(git rev-list --count "origin/$1..refs/heads/$1" 2>/dev/null || echo 0)
  if ((ahead > 0)); then
    read -r -p "Push $ahead new commit(s) on $1? [Y/n] " c
    if [[ -z "$c" || "$c" =~ ^[Yy]$ ]]; then
      git push origin "$1" || return 1
    fi
  else
    echo "Nothing new to push on $1."
  fi
}

# The reviewer page of branch $1 (PRs into dev only): ensure-page.sh as it
# stands on origin/dev writes and pushes a missing or stale one, keeping a stale
# page's scope and asking for the assignment when there is none. Fails only when
# no current page came of it and the user won't go on without one.
ENSURE_PAGE=.claude/skills/review-briefing/scripts/ensure-page.sh
page_step() {
  local f rc=0 c
  git fetch origin dev --quiet
  git cat-file -e "origin/dev:$ENSURE_PAGE" 2>/dev/null || return 0
  f=$(mktemp)
  git show "origin/dev:$ENSURE_PAGE" >"$f"
  bash "$f" --branch "$1" || rc=$?
  rm -f "$f"
  ((rc == 0)) && return 0
  read -r -p "No current reviewer page for $1. Go on without one? [y/N] " c
  [[ "$c" =~ ^[Yy]$ ]]
}

# Rebase $2 onto $3 if needed, make sure a PR into dev carries a current
# reviewer page, override approvals, three-way-merge PR $1. Always a merge
# commit, never a fast-forward: CodeCommit offers no merge once the source is
# more than 100 parent steps from the destination, and the release's
# dev -> main fast-forward needs that. A merge commit is one step on dev
# however many commits the PR has, and unlike a squash it keeps the source
# branch an ancestor of dev.
merge_pr() {
  git fetch origin "$2" "$3" --quiet
  if ! git merge-base --is-ancestor "origin/$3" "origin/$2"; then
    echo "Rebasing $2 onto $3..."
    rebase_branch "$2" "origin/$3"
    git push origin "$2" --force-with-lease
  fi
  if [[ "$3" == "dev" ]] && ! page_step "$2"; then
    echo "PR #$1 stays open."
    return 1
  fi

  REVISION_ID=$(awscc codecommit get-pull-request \
    --pull-request-id "$1" \
    --query 'pullRequest.revisionId' \
    --output text)

  echo "Overriding approval rules..."
  awscc codecommit override-pull-request-approval-rules \
    --pull-request-id "$1" \
    --revision-id "$REVISION_ID" \
    --override-status OVERRIDE

  echo "Merging PR #$1 (three-way)..."
  awscc codecommit merge-pull-request-by-three-way \
    --pull-request-id "$1" \
    --repository-name "$REPO" \
    --commit-message "Merge PR #$1: $2 into $3" \
    --author-name "$(git config user.name)" \
    --email "$(git config user.email)"

  echo "PR #$1 merged successfully."
}

# My open PR from $1 into $2, if any: re-running the command resumes it.
find_open_pr() {
  local me id refs
  me=$(awscc sts get-caller-identity --query Arn --output text)
  for id in $(awscc codecommit list-pull-requests --repository-name "$REPO" \
    --author-arn "$me" --pull-request-status OPEN --query 'pullRequestIds[]' --output text); do
    refs=$(awscc codecommit get-pull-request --pull-request-id "$id" \
      --query 'pullRequest.pullRequestTargets[0].[sourceReference, destinationReference]' --output text)
    if [[ "$refs" == "refs/heads/$1"$'\t'"refs/heads/$2" ]]; then
      echo "$id"
      return
    fi
  done
}

# Sets PR_STATUS and PR_COMMIT (the PR's current source commit) of PR $1.
pr_state() {
  local info
  info=$(awscc codecommit get-pull-request --pull-request-id "$1" \
    --query 'pullRequest.[pullRequestStatus, pullRequestTargets[0].sourceCommit]' --output text) || return 1
  read -r PR_STATUS PR_COMMIT <<<"$info"
}

# The bot's summary comment for source commit $2 of PR $1; empty until it lands.
review_for() {
  awscc codecommit get-comments-for-pull-request --pull-request-id "$1" --output json |
    jq -r --arg author "$REVIEW_AUTHOR_SUFFIX" --arg commit "$2" '
      [ .commentsForPullRequestData[]? | select(.location == null) | .comments[]?
        | select((.deleted | not) and .inReplyTo == null
                 and ((.authorArn // "") | endswith($author)))
        | select([.content | scan("heidi-pr-review · revision `([0-9a-f]{40})` · (?:review|conflict|failed)")][-1] == [$commit])
      ] | first | .content // empty'
}

# Polls until the bot has reviewed PR $1 at commit $2 (sets REVIEW and
# PR_COMMIT) or Enter is pressed (REVIEW empty). Fails once the PR closed.
wait_for_review() {
  REVIEW=""
  echo "Waiting for the review bot on PR #$1 (~2 min per revision; Enter stops waiting)..."
  while :; do
    pr_state "$1" || return 1
    if [[ "$PR_STATUS" != "OPEN" ]]; then
      echo "PR #$1 is $PR_STATUS."
      return 1
    fi
    # Right after a push the PR may still point at the previous revision.
    if [[ "$PR_COMMIT" == "$2" ]]; then
      REVIEW=$(review_for "$1" "$PR_COMMIT") || return 1
      if [[ -n "$REVIEW" ]]; then
        return 0
      fi
    fi
    if read -r -t 15; then
      return 0
    fi
  done
}

# Hands the review to an interactive Claude session in the checkout of $2:
# it verifies each finding against the code and fixes only the real ones.
verify_with_claude() {
  local wt
  wt=$(branch_worktree "$2")
  if [[ -z "$wt" ]]; then
    echo "$2 is not checked out in any worktree: nowhere to fix it."
    return
  fi
  (cd "$wt" && claude "The automatic review bot posted the summary below on CodeCommit PR #$1 ($2 -> $3) for revision $PR_COMMIT. Its findings are claims, not facts: verify each one against the code in this checkout (read the code paths involved, and reproduce it with a test or a quick script where that is practical). Report every finding as REAL or FALSE POSITIVE with the evidence. Fix the real ones, with a regression test where practical. Commit the fixes on $2 but do not push: the PR script pushes them and waits for the re-review."$'\n\n'"$REVIEW") || true
}

# Whether PR $1 may be merged now: still open, its revision reviewed (or
# the user says go anyway), and local $2 identical to that revision.
merge_ready() {
  local c local_sha
  pr_state "$1" || return 1
  if [[ "$PR_STATUS" != "OPEN" ]]; then
    echo "PR #$1 is $PR_STATUS."
    return 1
  fi
  if [[ -z "$(review_for "$1" "$PR_COMMIT")" ]]; then
    read -r -p "Revision $PR_COMMIT has not been reviewed. Merge anyway? [y/N] " c
    [[ "$c" =~ ^[Yy]$ ]] || return 1
  fi
  local_sha=$(git rev-parse --verify --quiet "refs/heads/$2") || return 0
  if [[ "$local_sha" != "$PR_COMMIT" ]]; then
    echo "Local $2 ($local_sha) is not the PR's revision ($PR_COMMIT): push or pull it first."
    return 1
  fi
}

# Once PR $1 ($2 -> $3) exists: wait for the bot, show its summary, and
# merge only when told to. Fixes pushed meanwhile are re-reviewed first.
review_loop() {
  local pr=$1 src=$2 tgt=$3 choice before
  wait_for_review "$pr" "$(git rev-parse "refs/remotes/origin/$src")" || return 0
  while :; do
    if [[ -n "$REVIEW" ]]; then
      printf '\n===== Review of %s =====\n%s\n=====\n' "$PR_COMMIT" "$REVIEW"
    else
      echo "PR #$pr: no review of its latest revision yet."
    fi
    read -r -p "PR #$pr: [m]erge, [v]erify findings with Claude (fixes the real ones), [r]e-review after pushing fixes, [q]uit (PR stays open): " choice
    case "$choice" in
      m | M)
        # The page first: a page commit is a new revision, which the bot carries
        # the review over to (it ignores reviews/), so wait for that before the merge.
        before=$(git rev-parse "refs/remotes/origin/$src")
        page_step "$src" || continue
        if [[ "$(git rev-parse "refs/remotes/origin/$src")" != "$before" ]]; then
          wait_for_review "$pr" "$(git rev-parse "refs/remotes/origin/$src")" || return 0
        fi
        if merge_ready "$pr" "$src"; then
          merge_pr "$pr" "$src" "$tgt"
          return 0
        fi
        ;;
      v | V | r | R)
        if [[ "$choice" == [vV] ]]; then
          if [[ -z "$REVIEW" ]]; then
            echo "No review to verify yet."
            continue
          fi
          verify_with_claude "$pr" "$src" "$tgt"
        fi
        push_pending "$src" || continue
        wait_for_review "$pr" "$(git rev-parse "refs/remotes/origin/$src")" || return 0
        ;;
      q | Q)
        echo "PR #$pr stays open; run the same command again to resume it."
        return 0
        ;;
    esac
  done
}

# The PR gets what origin has: offer to push what is still local first.
push_pending "$SOURCE"

PAGE_DONE=false
TARGET_COUNT=${#TARGETS[@]}
TARGET_IDX=0
for TARGET in "${TARGETS[@]}"; do
  TARGET_IDX=$((TARGET_IDX + 1))
  if ((TARGET_COUNT > 1)); then
    echo ""
    echo "=== [$TARGET_IDX/$TARGET_COUNT] $SOURCE -> $TARGET ==="
  fi

  # Re-fetch each iteration: an earlier --merge may have force-pushed $SOURCE
  git fetch origin "$SOURCE" "$TARGET" --quiet

  if [[ "$AWAIT_REVIEW" == "true" ]]; then
    PR_ID=$(find_open_pr "$SOURCE" "$TARGET")
    if [[ -n "$PR_ID" ]]; then
      echo "Resuming open PR #$PR_ID ($SOURCE -> $TARGET)"
      review_loop "$PR_ID" "$SOURCE" "$TARGET"
      continue
    fi
  fi

  # Find commits not yet in target (by patch content, handles squash merges)
  NEW_COMMITS=$(git cherry "origin/$TARGET" "origin/$SOURCE" | grep '^+' | awk '{print $2}')
  if [[ -z "$NEW_COMMITS" ]]; then
    echo "No new commits on origin/$SOURCE relative to origin/$TARGET — skipping"
    continue
  fi
  OLDEST_NEW=$(echo "$NEW_COMMITS" | head -1)
  RANGE="$OLDEST_NEW~1..origin/$SOURCE"

  # Always include commit log + file stats. Append full diff only if everything fits
  # under the 150K-token soft ceiling (Bedrock default context). Above that, send just
  # the summary — better to write a clean PR description from commit messages than to
  # truncate a multi-megabyte diff and hope the model guesses what got cut. Reviewer
  # pages (reviews/) are generated HTML: they would only crowd the diff out.
  LOG=$(git log --no-merges --pretty=format:'- %s' "$RANGE")
  STAT=$(git diff --stat "$RANGE" -- . ':(exclude)reviews/')
  DIFF=$(git diff "$RANGE" -- . ':(exclude)reviews/')

  if [[ -z "$LOG$DIFF" ]]; then
    echo "No diff between origin/$SOURCE and origin/$TARGET — skipping"
    continue
  fi

  SUMMARY="## Commits"$'\n'"$LOG"$'\n\n'"## Files"$'\n'"$STAT"
  DIFF_CHARS=${#DIFF}
  SUMMARY_CHARS=${#SUMMARY}
  EST_TOKENS=$(((DIFF_CHARS + SUMMARY_CHARS) / 3))
  MAX_TOKENS=150000

  if ((EST_TOKENS > MAX_TOKENS)); then
    echo "Branch is ~${EST_TOKENS} tokens of diff — sending commit log + file stats only"
    INPUT="$SUMMARY"
  else
    INPUT="$SUMMARY"$'\n\n'"## Diff"$'\n'"$DIFF"
  fi

  echo "Generating PR title and description..."
  # --bare reads no settings file, and Claude Code 2.1.318 (pc) answers "Not
  # logged in" with CLAUDE_CODE_USE_BEDROCK set only in the environment: pass
  # the repo's Bedrock env from origin/dev via --settings, as ensure-page.sh does.
  BARE_SETTINGS=$({ git show "refs/remotes/origin/dev:.claude/settings.json" 2>/dev/null || echo '{}'; } |
    jq -c '{env: (.env // {})}' 2>/dev/null || echo '{"env":{}}')
  set +e
  PR_CONTENT=$(echo "$INPUT" | claude -p --bare --settings "$BARE_SETTINGS" "Based on this branch's commit log, file stats, and (when present) full diff, generate a PR title and description. Output EXACTLY in this format with no other text:
TITLE: <short PR title under 70 chars>
DESCRIPTION: <concise markdown description with a ## Summary section listing bullet points of changes>" 2>&1)
  CLAUDE_RC=$?
  set -e
  if [[ $CLAUDE_RC -ne 0 ]]; then
    echo "claude -p failed (exit $CLAUDE_RC):"
    echo "$PR_CONTENT"
    continue
  fi

  TITLE=$(echo "$PR_CONTENT" | sed -n 's/^TITLE: //p')
  DESCRIPTION=$(echo "$PR_CONTENT" | sed -n '/^DESCRIPTION:/,$p' | tail -n +2)

  if [[ -z "$TITLE" ]]; then
    echo "Failed to generate PR content"
    echo "$PR_CONTENT"
    continue
  fi

  # Unless --destroy: the marker tells the CI cleanup job (buildspecs/scripts/
  # post-build.sh) to leave the source branch's per-developer infra standing
  # after the merge. See the comment on the flags above.
  if [[ "$DESTROY" != "true" ]]; then
    TITLE="$TITLE [no-cleanup]"
  fi

  if [[ "$NO_DEPLOY" == "true" ]]; then
    TITLE="$TITLE [no-deploy]"
  fi

  # --skip-review appends the marker the PR review bot's first gate looks
  # for (NO_REVIEW_MARKER in buildspecs/scripts/pr-review/lib/gate.mjs,
  # matched case-insensitively): the build then exits before any Bedrock
  # call. A --merge PR needs no marker — it is closed before the bot runs.
  if [[ "$SKIP_REVIEW" == "true" ]]; then
    TITLE="$TITLE [no-review]"
  fi

  # --force-blue/--force-green appends a [force-<slot>] marker read by
  # buildspecs/scripts/check-force-deploy.sh, forcing the prod build onto
  # that slot instead of the auto-detected inactive one. Forcing onto the
  # slot a live floating domain currently points at swaps live traffic to
  # the new artifact on upload — only use for changes safe to land live.
  if [[ -n "$FORCE_SLOT" ]]; then
    TITLE="$TITLE [force-$FORCE_SLOT]"
  fi

  echo "Title: $TITLE"
  echo "---"
  echo "$DESCRIPTION"
  echo "---"
  read -r -p "Create PR? [Y/n] " CONFIRM
  [[ -z "$CONFIRM" || "$CONFIRM" =~ ^[Yy]$ ]] || continue

  # The page before the PR, so its first revision already carries it.
  if [[ "$TARGET" == "dev" && "$PAGE_DONE" != "true" ]]; then
    page_step "$SOURCE" || continue
    PAGE_DONE=true
  fi

  PR_ID=$(awscc codecommit create-pull-request \
    --title "$TITLE" \
    --description "$DESCRIPTION" \
    --targets "repositoryName=$REPO,sourceReference=$SOURCE,destinationReference=$TARGET" \
    --query 'pullRequest.pullRequestId' \
    --output text)

  echo "Created PR #$PR_ID"

  if [[ "$MERGE" == "true" ]]; then
    merge_pr "$PR_ID" "$SOURCE" "$TARGET"
  elif [[ "$AWAIT_REVIEW" == "true" ]]; then
    review_loop "$PR_ID" "$SOURCE" "$TARGET"
  fi
done
