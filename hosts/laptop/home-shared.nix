# What every user of the laptop has: the workstation setup it shares with
# the PC (modules/home/workstation.nix), plus the laptop's screens, sound,
# direnv, the CodeCommit helpers and the rebuild aliases.
{ pkgs, ... }:

{
  imports = [ ../../modules/home/workstation.nix ];

  # The laptop's screens and lid; the rest of the Hyprland config is
  # dotfiles/hypr/hyprland.lua.
  desktop.hyprlandHostConfig = ./hyprland.lua;

  # The CodeCommit helpers (aliased below), for every user of the laptop
  home.packages = (
    with pkgs;
    [
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

      # CodeCommit PR creation (aliased as ccpr). The script itself is heidi's
      # buildspecs/scripts/create-pr.sh, run as it stands on origin/dev (see its
      # header), so a change merged there reaches every machine without a rebuild.
      (pkgs.writeShellScriptBin "create-pr" ''
        export PATH=${pkgs.jq}/bin:$PATH
        git fetch -q origin dev || exit 1
        f=$(mktemp) || exit 1
        trap 'rm -f "$f"' EXIT
        if ! git show origin/dev:buildspecs/scripts/create-pr.sh >"$f" 2>/dev/null; then
          echo "create-pr: origin/dev has no buildspecs/scripts/create-pr.sh (not a heidi checkout?)" >&2
          exit 1
        fi
        bash "$f" "$@"
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

  programs.direnv = {
    enable = true;
    nix-direnv.enable = true;
  };

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

  programs.zsh.shellAliases = {
    # The checkout of github.com/kirill-menke/nixos-config, also for lena
    # and christoph (their sudo rebuilds Kirill's checkout).
    rebuild = "sudo nixos-rebuild switch --flake /home/kirill/.config/nixos#laptop";
    update = "nix flake update --flake /home/kirill/.config/nixos && sudo nixos-rebuild switch --flake /home/kirill/.config/nixos#laptop";
    # Claude Code comes from llm-agents, so this also bumps omp, Orca and t3code.
    update-claude = "nix flake update llm-agents --flake /home/kirill/.config/nixos && sudo nixos-rebuild switch --flake /home/kirill/.config/nixos#laptop";
    cc = "claude-commit";
    ccpr = "create-pr";
    ccm = "merge-into";
  };

  home.stateVersion = "24.11";
}
