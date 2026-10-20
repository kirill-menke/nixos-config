{
  inputs,
  config,
  pkgs,
  lib,
  ...
}:

{
  imports = [
    inputs.spicetify-nix.homeManagerModules.spicetify
    ../../modules/home/desktop.nix
  ];

  # The laptop's screens, lid and cursor; the rest of the Hyprland config is
  # dotfiles/hypr/hyprland.lua.
  desktop.hyprlandHostConfig = ./hyprland.lua;

  # Packages shared across all users
  home.packages = (
    with pkgs;
    [
      # Development tools
      python3
      nodejs_22
      uv
      jq

      # Infrastructure tools
      opentofu

      # GUI Applications
      google-chrome
      signal-desktop

      # Media applications
      celluloid

      # File management
      nautilus
      duf

      # Utilities
      wget
      pavucontrol
      nwg-look
      imagemagick
      pulsemixer
      file
      wl-clipboard
      wtype
      ydotool
      yt-dlp
      rclone

      # Themes/Appearance
      catppuccin-cursors
      rose-pine-hyprcursor
      noto-fonts-color-emoji

      # Auto-commit with Claude-generated message (aliased as cc)
      (pkgs.writeShellScriptBin "claude-commit" ''
        set -euo pipefail

        git add -A
        DIFF=$(git diff --cached)

        if [[ -z "$DIFF" ]]; then
          echo "Nothing to commit."
          exit 0
        fi

        echo "Generating commit message..."
        MSG=$(echo "$DIFF" | claude -p "Based on this git diff, generate a concise commit message (subject line only, under 70 chars, imperative mood, no quotes). Output ONLY the message with no other text.")

        if [[ -z "$MSG" ]]; then
          echo "Failed to generate commit message."
          exit 1
        fi

        echo "Commit message: $MSG"
        read -p "Commit? [Y/n] " CONFIRM
        [[ -z "$CONFIRM" || "$CONFIRM" =~ ^[Yy]$ ]] || { git reset HEAD -- . >/dev/null; exit 0; }

        git commit -m "$MSG"

        read -p "Push? [Y/n] " PUSH
        [[ -z "$PUSH" || "$PUSH" =~ ^[Yy]$ ]] && git push
      '')

      # CodeCommit PR creation (aliased as ccpr)
      (pkgs.writeShellScriptBin "create-pr" ''
        set -euo pipefail
        export AWS_PAGER=""

        PROFILE="research"
        REGION="eu-central-1"
        MERGE=false
        AWAIT_REVIEW=false
        KEEP=false
        NO_DEPLOY=false
        FORCE_SLOT=""

        args=()
        for arg in "$@"; do
          case "$arg" in
            --merge) MERGE=true ;;
            --merge-after-review) AWAIT_REVIEW=true ;;
            --keep) KEEP=true ;;
            --no-deploy) NO_DEPLOY=true ;;
            --force-blue) FORCE_SLOT="blue" ;;
            --force-green) FORCE_SLOT="green" ;;
            *) args+=("$arg") ;;
          esac
        done

        if [[ ''${#args[@]} -lt 2 ]]; then
          echo "Usage: create-pr <source-branch> <target-branch> [<target-branch>...] [--merge|--merge-after-review] [--keep] [--no-deploy] [--force-blue|--force-green]"
          exit 1
        fi

        SOURCE="''${args[0]}"
        TARGETS=("''${args[@]:1}")
        REPO=$(basename "$(git remote get-url origin)" .git | sed 's|.*/||')

        if [[ "$MERGE" == "true" && "$AWAIT_REVIEW" == "true" ]]; then
          echo "--merge merges at once, --merge-after-review after the review bot: pick one."
          exit 1
        fi
        if [[ "$AWAIT_REVIEW" == "true" ]]; then
          for TARGET in "''${TARGETS[@]}"; do
            if [[ "$TARGET" != "dev" ]]; then
              echo "--merge-after-review: the review bot only reviews PRs into dev, not $TARGET."
              exit 1
            fi
          done
        fi
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

        # Rebase $2 onto $3 if needed, override approvals, three-way-merge PR $1.
        # Always a merge commit, never a fast-forward: CodeCommit offers no merge
        # once the source is more than 100 parent steps from the destination, and
        # the release's dev -> main fast-forward needs that. A merge commit is one
        # step on dev however many commits the PR has, and unlike a squash it
        # keeps the source branch an ancestor of dev.
        merge_pr() {
          git fetch origin "$2" "$3" --quiet
          if ! git merge-base --is-ancestor "origin/$3" "origin/$2"; then
            echo "Rebasing $2 onto $3..."
            rebase_branch "$2" "origin/$3"
            git push origin "$2" --force-with-lease
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
            ${pkgs.jq}/bin/jq -r --arg author "$REVIEW_AUTHOR_SUFFIX" --arg commit "$2" '
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

        # Offers to push the commits on $1 that origin does not have yet.
        push_pending() {
          local wt ahead c
          wt=$(branch_worktree "$1")
          if [[ -n "$wt" && -n "$(git -C "$wt" status --porcelain)" ]]; then
            echo "Note: $wt has uncommitted changes; the PR only gets what is committed and pushed."
          fi
          git fetch origin "$1" --quiet || return 1
          ahead=$(git rev-list --count "origin/$1..refs/heads/$1" 2>/dev/null || echo 0)
          if (( ahead > 0 )); then
            read -r -p "Push $ahead new commit(s) on $1? [Y/n] " c
            if [[ -z "$c" || "$c" =~ ^[Yy]$ ]]; then
              git push origin "$1" || return 1
            fi
          else
            echo "Nothing new to push on $1."
          fi
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
          local pr=$1 src=$2 tgt=$3 choice
          wait_for_review "$pr" "$(git rev-parse "refs/remotes/origin/$src")" || return 0
          while :; do
            if [[ -n "$REVIEW" ]]; then
              printf '\n===== Review of %s =====\n%s\n=====\n' "$PR_COMMIT" "$REVIEW"
            else
              echo "PR #$pr: no review of its latest revision yet."
            fi
            read -r -p "PR #$pr: [m]erge, [v]erify findings with Claude (fixes the real ones), [r]e-review after pushing fixes, [q]uit (PR stays open): " choice
            case "$choice" in
              m|M)
                if merge_ready "$pr" "$src"; then
                  merge_pr "$pr" "$src" "$tgt"
                  return 0
                fi
                ;;
              v|V|r|R)
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
              q|Q)
                echo "PR #$pr stays open; run the same command again to resume it."
                return 0
                ;;
            esac
          done
        }

        TARGET_COUNT=''${#TARGETS[@]}
        TARGET_IDX=0
        for TARGET in "''${TARGETS[@]}"; do
        TARGET_IDX=$((TARGET_IDX + 1))
        if (( TARGET_COUNT > 1 )); then
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
        # truncate a multi-megabyte diff and hope the model guesses what got cut.
        LOG=$(git log --no-merges --pretty=format:'- %s' "$RANGE")
        STAT=$(git diff --stat "$RANGE")
        DIFF=$(git diff "$RANGE")

        if [[ -z "$LOG$DIFF" ]]; then
          echo "No diff between origin/$SOURCE and origin/$TARGET — skipping"
          continue
        fi

        SUMMARY="## Commits"$'\n'"$LOG"$'\n\n'"## Files"$'\n'"$STAT"
        DIFF_CHARS=''${#DIFF}
        SUMMARY_CHARS=''${#SUMMARY}
        EST_TOKENS=$(((DIFF_CHARS + SUMMARY_CHARS) / 3))
        MAX_TOKENS=150000

        if (( EST_TOKENS > MAX_TOKENS )); then
          echo "Branch is ~''${EST_TOKENS} tokens of diff — sending commit log + file stats only"
          INPUT="$SUMMARY"
        else
          INPUT="$SUMMARY"$'\n\n'"## Diff"$'\n'"$DIFF"
        fi

        echo "Generating PR title and description..."
        set +e
        PR_CONTENT=$(echo "$INPUT" | claude -p --bare "Based on this branch's commit log, file stats, and (when present) full diff, generate a PR title and description. Output EXACTLY in this format with no other text:
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

        if [[ "$KEEP" == "true" ]]; then
          TITLE="$TITLE [no-cleanup]"
        fi

        if [[ "$NO_DEPLOY" == "true" ]]; then
          TITLE="$TITLE [no-deploy]"
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
        read -p "Create PR? [Y/n] " CONFIRM
        [[ -z "$CONFIRM" || "$CONFIRM" =~ ^[Yy]$ ]] || continue

        PR_ID=$(aws codecommit create-pull-request \
          --title "$TITLE" \
          --description "$DESCRIPTION" \
          --targets "repositoryName=$REPO,sourceReference=$SOURCE,destinationReference=$TARGET" \
          --profile "$PROFILE" --region "$REGION" \
          --query 'pullRequest.pullRequestId' \
          --output text)

        echo "Created PR #$PR_ID"

        if [[ "$MERGE" == "true" ]]; then
          merge_pr "$PR_ID" "$SOURCE" "$TARGET"
        elif [[ "$AWAIT_REVIEW" == "true" ]]; then
          review_loop "$PR_ID" "$SOURCE" "$TARGET"
        fi
        done
      '')

      # Merge the current worktree's branch into a local branch checked out in
      # another worktree, e.g. the consolidation branch (aliased as ccm)
      (pkgs.writeShellScriptBin "merge-into" ''
        set -euo pipefail

        TARGET="''${1:-}"
        if [[ -z "$TARGET" ]]; then
          echo "Usage: merge-into <target-branch>  (run inside the worktree to merge)"
          exit 1
        fi

        SOURCE=$(git branch --show-current)
        if [[ -z "$SOURCE" ]]; then
          echo "Detached HEAD: check out the worktree's branch first."
          exit 1
        fi
        if [[ "$SOURCE" == "$TARGET" ]]; then
          echo "This checkout is $TARGET itself: run it from the worktree to merge."
          exit 1
        fi
        if [[ -n "$(git status --porcelain)" ]]; then
          echo "Uncommitted changes in $(git rev-parse --show-toplevel): commit them first."
          exit 1
        fi

        TARGET_WT=$(git worktree list --porcelain |
          awk -v ref="refs/heads/$TARGET" '/^worktree /{p=substr($0,10)} $0=="branch " ref{print p; exit}')
        if [[ -z "$TARGET_WT" ]]; then
          echo "$TARGET is not checked out in any worktree of this repo."
          exit 1
        fi
        if [[ -n "$(git -C "$TARGET_WT" status --porcelain --untracked-files=no)" ]]; then
          echo "$TARGET_WT has uncommitted changes: commit or stash them first."
          exit 1
        fi

        if git merge-base --is-ancestor "$SOURCE" "$TARGET"; then
          echo "$TARGET already contains $SOURCE."
          exit 0
        fi

        echo "Commits on $SOURCE not in $TARGET:"
        git log --oneline --no-merges "$TARGET..$SOURCE"
        read -p "Merge $SOURCE into $TARGET ($TARGET_WT)? [Y/n] " CONFIRM
        [[ -z "$CONFIRM" || "$CONFIRM" =~ ^[Yy]$ ]] || exit 0

        if ! git -C "$TARGET_WT" merge --no-edit "$SOURCE"; then
          if ! git -C "$TARGET_WT" rev-parse -q --verify MERGE_HEAD >/dev/null; then
            echo "Merging $SOURCE into $TARGET failed before any conflict (see above); $TARGET_WT is unchanged."
            exit 1
          fi
          CONFLICTS=$(git -C "$TARGET_WT" diff --name-only --diff-filter=U)
          printf 'Conflicts in %s:\n%s\nStarting Claude there to resolve them...\n' "$TARGET_WT" "$CONFLICTS"
          (cd "$TARGET_WT" && claude "A git merge of branch $SOURCE into $TARGET in this checkout stopped on conflicts in these files:"$'\n'"$CONFLICTS"$'\n\n'"Resolve every conflict so the intent of both sides survives: read what each side changed (git log --merge -p, git diff) instead of taking one side wholesale. Run the checks that cover the touched files if they are quick. Then git add the resolved files and conclude the merge with git commit --no-edit. Do not push and do not abort the merge; if a conflict needs a decision from me, ask.") || true
          if git -C "$TARGET_WT" rev-parse -q --verify MERGE_HEAD >/dev/null; then
            echo "The merge is still in progress in $TARGET_WT: finish it there (resolve, git add, git commit --no-edit), or: git -C $TARGET_WT merge --abort"
            exit 1
          fi
          if ! git merge-base --is-ancestor "$SOURCE" "$TARGET"; then
            echo "$TARGET does not contain $SOURCE: the merge was aborted."
            exit 1
          fi
          echo "Conflicts resolved: $TARGET now contains $SOURCE."
        fi

        read -p "Push $TARGET? [Y/n] " PUSH
        if [[ -z "$PUSH" || "$PUSH" =~ ^[Yy]$ ]]; then
          git -C "$TARGET_WT" push
        fi
      '')
    ]
  );

  # Shared programs
  programs.claude-code = {
    enable = true;
    # home-kirill.nix swaps in a Bedrock EU wrapper for Kirill.
    package = lib.mkDefault pkgs.claude-code;
  };
  programs.awscli.enable = true;
  programs.direnv = {
    enable = true;
    nix-direnv.enable = true;
  };
  programs.delta.enableGitIntegration = true;
  programs.discord.enable = true;
  programs.spicetify = {
    enable = true;
    theme = inputs.spicetify-nix.legacyPackages.${pkgs.stdenv.hostPlatform.system}.themes.catppuccin;
    colorScheme = "mocha";
  };
  programs.vscode = {
    enable = true;
    profiles.default.extensions = with pkgs.vscode-extensions; [
      catppuccin.catppuccin-vsc
    ];
  };
  programs.firefox = {
    enable = true;
    # Keep the existing profile location (newer home-manager defaults to XDG).
    configPath = ".mozilla/firefox";
    profiles.default = {
      isDefault = true;
      extensions.packages = with inputs.firefox-addons.packages.${pkgs.stdenv.hostPlatform.system}; [
        ublock-origin
        sponsorblock
        catppuccin-mocha-mauve
      ];
    };
  };

  programs.thunderbird = {
    enable = true;
    profiles.default = {
      isDefault = true;
    };
  };

  # Media Applications
  programs.zathura.enable = true;
  programs.imv.enable = true;
  home.file.".config/imv/config".source = ../../dotfiles/imv/config;
  programs.mpv.enable = true;
  # MPRIS for mpv, so it shows in waybar's now-playing module and the
  # swaync panel and answers playerctl
  programs.mpv.scripts = [ pkgs.mpvScripts.mpris ];

  # Utilities
  programs.fastfetch.enable = true;
  programs.htop.enable = true;
  programs.btop.enable = true;

  # Sound: a WirePlumber drop-in (a Bluetooth headset's mic never becomes
  # the default input by itself; short names for the laptop codec's
  # devices) and a pipewire-pulse one (a Bluetooth output that connects
  # takes over). Each file explains itself. They apply after a re-login or
  # a restart of wireplumber and pipewire-pulse.
  xdg.configFile."wireplumber/wireplumber.conf.d/51-sound.conf".source =
    ../../dotfiles/wireplumber/51-sound.conf;
  xdg.configFile."pipewire/pipewire-pulse.conf.d/51-switch-on-connect.conf".source =
    ../../dotfiles/pipewire/51-switch-on-connect.conf;
  # "Clean mic", a noise-suppressed microphone from PipeWire's built-in
  # WebRTC processing, offered as one more input (Sound card, apps' device
  # menus) but never the default by itself; it opens the real mic only
  # while an app records from it. No echo cancelling: see the file for why.
  # Applies after a re-login or a restart of pipewire.
  xdg.configFile."pipewire/pipewire.conf.d/60-clean-mic.conf".source =
    ../../dotfiles/pipewire/60-clean-mic.conf;

  programs.zsh = {
    enable = true;
    enableCompletion = true;
    autosuggestion.enable = true;
    syntaxHighlighting.enable = true;

    history = {
      path = "${config.home.homeDirectory}/.histfile";
      size = 1000;
      save = 1000;
    };

    initContent = ''
      # Completion styles
      zstyle ':completion:*' completer _complete _ignored

      # Vi mode
      bindkey -v

      # Disable beep
      unsetopt beep
    '';

    completionInit = ''
      autoload -Uz compinit
      compinit -d "$HOME/.cache/zsh/zcompdump-$ZSH_VERSION"
    '';

    shellAliases = {
      ll = "ls -lah";
      la = "ls -a";
      grep = "grep --color=auto";
      icat = "kitty +kitten icat";
      open = "xdg-open";
      # The checkout of github.com/kirill-menke/nixos-config, also for lena
      # and christoph (their sudo rebuilds Kirill's checkout).
      rebuild = "sudo nixos-rebuild switch --flake /home/kirill/.config/nixos#laptop";
      update = "nix flake update --flake /home/kirill/.config/nixos && sudo nixos-rebuild switch --flake /home/kirill/.config/nixos#laptop";
      update-claude = "nix flake update claude-code --flake /home/kirill/.config/nixos && sudo nixos-rebuild switch --flake /home/kirill/.config/nixos#laptop";
      cc = "claude-commit";
      ccpr = "create-pr";
      ccm = "merge-into";
    };

    oh-my-zsh = {
      enable = true;
      theme = "agnoster";
      plugins = [
        "sudo"
        "z"
      ];
    };
  };

  programs.kitty = {
    enable = true;
    font = {
      name = "CaskaydiaCove Nerd Font Mono";
      size = 14;
    };
    themeFile = "Catppuccin-Mocha";
    settings = {
      confirm_os_window_close = 0;
      bold_font = "auto";
      italic_font = "auto";
      bold_italic_font = "auto";
    };
  };

  programs.neovim = {
    enable = true;
    defaultEditor = true;
    withRuby = true;
    withPython3 = true;
    extraConfig = ''
      set tabstop=2
      set shiftwidth=2
      set expandtab
      set clipboard=unnamedplus
      inoremap jj <Esc>
      nnoremap dd "_dd
    '';
  };

  home.pointerCursor = {
    enable = true;
    name = "catppuccin-mocha-dark-cursors";
    package = pkgs.catppuccin-cursors.mochaDark;
    size = 24;
    gtk.enable = true;
  };

  # The icon theme gtk.iconTheme names: Papirus-Dark plus the tray icons of
  # Discord, Steam and qBittorrent redrawn in the bar's @text.
  xdg.dataFile."icons/Papirus-Dark-Tray".source = ../../dotfiles/icons/Papirus-Dark-Tray;

  gtk = {
    enable = true;
    theme = {
      name = "catppuccin-mocha-mauve-standard";
      package = pkgs.catppuccin-gtk.override {
        accents = [ "mauve" ];
        variant = "mocha";
      };
    };
    iconTheme = {
      # Papirus-Dark with Discord's, Steam's and qBittorrent's tray icons in
      # the bar's text colour (dotfiles/icons/Papirus-Dark-Tray). It inherits
      # Papirus-Dark, so every other icon, launchers' and menus' too, stays
      # as it was; waybar's tray looks the apps' icon names up in this theme.
      # The package is the Papirus-Dark it inherits.
      name = "Papirus-Dark-Tray";
      package = pkgs.catppuccin-papirus-folders.override {
        flavor = "mocha";
        accent = "mauve";
      };
    };
    gtk4.theme = config.gtk.theme;
  };

  dconf.settings = {
    "org/gnome/desktop/interface" = {
      color-scheme = "prefer-dark";
    };
  };

  home.stateVersion = "24.11";
  programs.home-manager.enable = true;
}
