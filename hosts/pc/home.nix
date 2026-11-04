# Kirill's Home Manager config on the PC, on top of what he has on both
# desktops (modules/home/workstation.nix, modules/home/kirill.nix). The PC's
# feature modules add their own parts: ./iphone.nix (uxplay), ./tv.nix (play,
# celluloid-auto, tv, mpv's atmos profile, the TV's audio) and ./syncthing.nix.
{
  inputs,
  config,
  pkgs,
  lib,
  ...
}:

let
  # `update [INPUT...]` -- bump the flake lock (all inputs, or just the named
  # ones, e.g. `update llm-agents`), then dry-run the PC closure and show what
  # the new lock would compile locally as opposed to fetch.  A nixpkgs bump
  # is normally all-cache; anything of substance in the "built" list means an
  # override or an unfree/uncached package has crept back in, and this is the
  # moment to say no rather than after an hour of gcc.  Declining restores
  # the previous flake.lock.  Glue derivations (etc, activate, units, the
  # home-manager generation, ...) always rebuild and are filtered out.
  nixos-update = pkgs.writeShellScriptBin "nixos-update" ''
    set -eu
    FLAKE=${config.home.homeDirectory}/.config/nixos
    LOCK=$FLAKE/flake.lock
    SAVED=$(mktemp)
    trap 'rm -f "$SAVED"' EXIT
    cp "$LOCK" "$SAVED"

    if [ $# -gt 0 ]; then
      nix flake update --flake "$FLAKE" "$@"
    else
      nix flake update --flake "$FLAKE"
    fi
    if cmp -s "$LOCK" "$SAVED"; then
      echo "flake.lock unchanged -- nothing to do."
      exit 0
    fi

    echo
    echo "Dry-running the PC closure against the new lock ..."
    plan=$(nix build --dry-run --no-link \
      "$FLAKE#nixosConfigurations.pc.config.system.build.toplevel" 2>&1 >/dev/null \
      | grep -vE '^(warning|evaluation warning)') || true
    built=$(printf '%s\n' "$plan" \
      | ${pkgs.gawk}/bin/awk '/will be built/{f=1;next} /will be fetched/{f=0} f' \
      | sed -E 's#^ */nix/store/[a-z0-9]{32}-##; s/\.drv$//' \
      | grep -vE '^(activate|etc|system-units|unit-.*|nixos-system-.*|home-manager-.*|hm[_-].*|user-environment)$' \
      || true)
    fetched=$(printf '%s\n' "$plan" | grep -oE 'will be fetched \([^)]*\)' || true)

    if [ -n "$built" ]; then
      echo
      echo "These derivations would be COMPILED LOCALLY:"
      printf '%s\n' "$built" | sed 's/^/    /'
    else
      echo "Nothing to compile locally; everything comes from a binary cache."
    fi
    [ -n "$fetched" ] && echo "$fetched"
    echo

    printf 'Switch to the new lock? [y/N] '
    read -r answer
    case "$answer" in
      y|Y|yes) ;;
      *)
        cp "$SAVED" "$LOCK"
        echo "Aborted; flake.lock restored."
        exit 1
        ;;
    esac
    sudo nixos-rebuild switch --flake "$FLAKE#pc"
  '';
in
{
  imports = [
    ../../modules/home/workstation.nix
    ../../modules/home/kirill.nix
  ];

  # The PC's screens, colour management and Super+I; the rest of the Hyprland
  # config is dotfiles/hypr/hyprland.lua.
  desktop.hyprlandHostConfig = ./hyprland.lua;

  # Plain `claude` and `omp` talk to the Anthropic API directly;
  # claude-bedrock and omp-bedrock run on Bedrock EU.
  kirill.agentBackend = "direct";

  # The PC's own packages, on top of modules/home/workstation.nix and
  # modules/home/kirill.nix (and the features': ./iphone.nix, ./tv.nix)
  home.packages = with pkgs; [
    # Development tools
    gh
    nixos-update
    # GUI Applications
    # Claude Desktop (community repackage of the Electron app; unofficial on
    # Linux).  Prebuilt on cache.numtide.com.
    inputs.llm-agents.packages.${pkgs.stdenv.hostPlatform.system}.claude-desktop
    # 3D modelling/animation.  Upstream binaries rather than nixpkgs' blender:
    # the CUDA/OptiX kernels the 1080 Ti needs are only obtainable from source
    # there (hours of compile on every nixpkgs bump, never cached).  See the
    # header of blender-bin.nix; bump the version there.
    (callPackage ./blender-bin.nix { })
    # Spaced repetition for the proficiency wiki; the deck TSV comes from
    # ~/Documents/wiki-build/export-anki.py.  The native package rather than
    # anki-bin: both are far past the 2.1.55 that the file's `#deck column:`
    # import directive needs, and this one is 101MB unpacked against 650MB for
    # anki-bin's FHS environment.
    anki

    # Media applications
    obs-studio # screen recording (Wayland via PipeWire portal)
    kdePackages.kdenlive # video editing: keyframed zoom/pan post-production

    # Utilities
    (lib.lowPrio sox) # audio recording for Claude Code voice mode; lowPrio: its bin/play must lose to our play launcher (./tv.nix)
    rtorrent
    # Markdown in the terminal, both driven by the `md` function in programs.zsh
    glow # styled + paged rendering, no image support
    mdcat # inline images via kitty's graphics protocol, but only unpaged
  ];

  # The profile Firefox created before Home Manager managed it, so the
  # add-ons (modules/home/workstation.nix) land in the existing one.
  programs.firefox.profiles.default.path = "567gwbch.default";

  # Spicetify extensions on top of the shared Catppuccin theme
  programs.spicetify.enabledExtensions =
    let
      spicePkgs = inputs.spicetify-nix.legacyPackages.${pkgs.stdenv.hostPlatform.system};
    in
    with spicePkgs.extensions;
    [
      adblockify
      hidePodcasts
      shuffle
    ];

  home.file.".rtorrent.rc".text = ''
    directory.default.set = ~/Downloads
    ratio.enable =
    ratio.min.set = 0
    ratio.max.set = 0
    ratio.upload.set = 0
  '';

  services.hypridle.enable = false;

  programs.zsh = {
    initContent = ''
      # md [file...] — render markdown in the terminal.
      #
      # oh-my-zsh's base lib (lib/directories.zsh) defines `alias md='mkdir -p'`,
      # and zsh refuses to parse `md() { ... }` while that alias is live: it dies
      # with "defining function based on alias" and leaves the alias in place, so
      # `md notes.md` would quietly mkdir instead of rendering. Drop the alias
      # first — this runs after oh-my-zsh is sourced, so it wins. `mkdir -p` is
      # still available as `mkd` (see shellAliases).
      unalias md 2>/dev/null || true

      # mdcat draws inline images with kitty's graphics protocol, but only when
      # it writes straight to the terminal: `mdcat -p` pipes through a pager and
      # silently drops every image. So documents that contain images go to mdcat
      # unpaged (scroll them with kitty's own scrollback), and everything else
      # goes to glow, which pages and styles more nicely. With no argument glow
      # opens its file browser over the markdown in the current directory.
      md() {
        emulate -L zsh
        (( $# )) || { glow; return }
        if grep -qE '!\[[^]]*\]\(' -- "$@" 2>/dev/null; then
          mdcat -- "$@"
        else
          glow -p -- "$@"
        fi
      }
    '';

    shellAliases = {
      # Both Claude Code commands, direct API and Bedrock EU, may switch into
      # bypass-permissions mode.
      claude = "claude --allow-dangerously-skip-permissions";
      claude-bedrock = "claude-bedrock --allow-dangerously-skip-permissions";
      # oh-my-zsh's `md` (mkdir -p) is taken over by the markdown renderer in
      # initContent, so keep the directory shortcut under a free name.
      mkd = "mkdir -p";
      # Download a torrent into ~/Videos/Downloads without seeding afterwards;
      # quote the magnet link (it contains &) e.g. magnet 'magnet:?xt=...'
      magnet = "aria2c --seed-time=0 --dir ~/Videos/Downloads";
      # Blank both monitors (DPMS off) until a key is pressed; mouse movement
      # deliberately does not wake them (misc.key_press_enables_dpms in
      # hyprland.lua). The sleep keeps the Enter release from waking them.
      screens-off = "sleep 1 && hyprctl dispatch 'hl.dsp.dpms({ action = \"off\" })'";
      # No alias for playback: `play` is a real binary on PATH, so it also works
      # from scripts, .desktop files and non-interactive shells.
      rebuild = "sudo nixos-rebuild switch --flake ~/.config/nixos#pc";
      # Builds the NAS closure locally on the PC, copies it over SSH, then
      # activates remotely. No sudo: building is unprivileged and activation
      # runs as root on the NAS side.
      # Root SSH is disabled on the NAS (hosts/nas/default.nix); deploys go
      # through kirill, whose passwordless sudo makes --use-remote-sudo
      # non-interactive.
      rebuild-nas = "nixos-rebuild switch --flake ~/.config/nixos#nas --target-host kirill@nas.local --use-remote-sudo";
      # Pre-flight in the let-block above: shows what a lock bump would compile
      # locally and asks before switching.  `update llm-agents` bumps one input.
      update = "nixos-update";
    };
  };

  home.stateVersion = "24.11";
}
