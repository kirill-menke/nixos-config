#
# WhatsApp archive -- wacli (github.com/openclaw/wacli) linked to Kirill's
# WhatsApp as a WhatsApp Web device, so agents can read his chats.
#
# wacli speaks the WhatsApp Web protocol (whatsmeow) and mirrors chats,
# contacts and groups into SQLite under /var/lib/wacli: session.db holds the
# linked-device keys (whoever has them can read and send as Kirill), wacli.db
# the archive. It is unofficial: WhatsApp's terms forbid such clients, so the
# account carries a small ban risk, which Kirill accepted on 2026-11-03. Text
# only (no media downloads) and nothing that sends.
#
#   wacli.service    `wacli sync --follow`: stays connected and stores every new
#                    message. Quiet presence, so the phone keeps notifying.
#   whatsapp ...     the agents' door: an allowlist of read commands, always
#                    --read-only, run as the store's owner. From the PC:
#                      ssh nas whatsapp messages search Prag --json
#                    (`sync --follow` holds the store lock, which is no
#                    authorization boundary: a running sync carries out sends
#                    handed to it, hence the allowlist.)
#   whatsapp-pair    links the device, once.
#
# One-time, after the first deploy (the unit stays inactive until then):
#   sudo whatsapp-pair +49...   prints a code. On the phone: WhatsApp > Linked
#                               devices > Link a device > Link with phone number
#                               instead, and enter it within ~2 minutes.
# The pairing asks the phone for one year of history (Kirill's choice); without
# --full-history WhatsApp sends about three months. The phone decides what it
# sends and may take hours. To unlink, log the device out on the phone.
#
# The release binary (cgo + SQLite FTS5, links only glibc), so nothing compiles
# here. Update: bump `version`, then `nix store prefetch-file` the tarball.
#
{ lib, pkgs, ... }:
let
  version = "0.25.1";

  wacli = pkgs.stdenv.mkDerivation {
    pname = "wacli";
    inherit version;
    src = pkgs.fetchurl {
      url = "https://github.com/openclaw/wacli/releases/download/v${version}/wacli_${version}_linux_amd64.tar.gz";
      hash = "sha256-U7u0UPCkfjsIAXPWH9bOxyJB2AZb7hflkidZg7er0oE=";
    };
    sourceRoot = ".";
    nativeBuildInputs = [ pkgs.autoPatchelfHook ];
    dontConfigure = true;
    dontBuild = true;
    installPhase = ''
      runHook preInstall
      install -Dm755 wacli $out/bin/wacli
      runHook postInstall
    '';
    meta.mainProgram = "wacli";
  };

  store = "/var/lib/wacli";
  wacliExe = lib.getExe wacli;

  # Exit 0 only when the store holds a live link; unit condition and pair check.
  linked = pkgs.writeShellApplication {
    name = "wacli-linked";
    runtimeInputs = [ pkgs.gnugrep ];
    text = ''
      ${wacliExe} --store ${store} --read-only --json auth status | grep -q '"authenticated":true'
    '';
  };

  door = pkgs.writeShellApplication {
    name = "whatsapp";
    runtimeInputs = [ pkgs.coreutils ];
    text = ''
      usage() {
        cat <<'EOF'
      whatsapp: read-only access to Kirill's WhatsApp archive (wacli on the NAS)

        whatsapp chats list|show            whatsapp contacts search|show|resolve
        whatsapp messages list|search|starred|show|context|export|changes
        whatsapp groups list|info           whatsapp groups participants list
        whatsapp calls list                 whatsapp history coverage
        whatsapp store stats                whatsapp auth status
        whatsapp doctor                     whatsapp version

      Flags go after the command: --json for JSON, --full for untruncated
      tables, --help for its flags. Sending, deleting, history backfill, media
      downloads and --output are not available here.
      EOF
      }
      deny() {
        echo "whatsapp: $*; run 'whatsapp --help' for what is allowed" >&2
        exit 2
      }

      case "''${1:-}" in
        "" | -h | --help | help)
          usage
          exit 0
          ;;
        doctor | version) words=1 ;;
        chats)
          case "''${2:-}" in list | show) words=2 ;; *) deny "chats ''${2:-}: not allowed" ;; esac
          ;;
        messages)
          case "''${2:-}" in
            list | search | starred | show | context | export | changes) words=2 ;;
            *) deny "messages ''${2:-}: not allowed" ;;
          esac
          ;;
        contacts)
          case "''${2:-}" in search | show | resolve) words=2 ;; *) deny "contacts ''${2:-}: not allowed" ;; esac
          ;;
        groups)
          case "''${2:-} ''${3:-}" in
            "list "* | "info "*) words=2 ;;
            "participants list") words=3 ;;
            *) deny "groups ''${2:-} ''${3:-}: not allowed" ;;
          esac
          ;;
        calls)
          [ "''${2:-}" = list ] || deny "calls ''${2:-}: not allowed"
          words=2
          ;;
        history)
          [ "''${2:-}" = coverage ] || deny "history ''${2:-}: not allowed"
          words=2
          ;;
        store)
          [ "''${2:-}" = stats ] || deny "store ''${2:-}: not allowed"
          words=2
          ;;
        auth)
          [ "''${2:-}" = status ] || deny "auth ''${2:-}: not allowed"
          words=2
          ;;
        -*) deny "flags go after the command, e.g. whatsapp chats list --json" ;;
        *) deny "$1: not allowed" ;;
      esac

      # No flag may switch the store, leave read-only mode, write files, take the
      # store lock or connect to WhatsApp.
      i=0
      for arg in "$@"; do
        i=$((i + 1))
        [ "$i" -le "$words" ] && continue
        case "$arg" in
          --read-only* | --store* | --account* | --output* | --connect* | --lock-wait* | --events* | --webhook*)
            deny "$arg is not allowed here"
            ;;
        esac
      done

      exec /run/wrappers/bin/sudo -n -u wacli -- ${pkgs.coreutils}/bin/env WACLI_READONLY=1 \
        ${wacliExe} --store ${store} --read-only "$@"
    '';
  };

  pair = pkgs.writeShellApplication {
    name = "whatsapp-pair";
    runtimeInputs = [
      pkgs.coreutils
      pkgs.util-linux
      pkgs.systemd
    ];
    text = ''
      if [ "$(id -u)" -ne 0 ]; then
        echo "Run as root: sudo whatsapp-pair +<country code><number>" >&2
        exit 1
      fi
      if [ $# -ne 1 ]; then
        echo "usage: whatsapp-pair +<country code><number>" >&2
        exit 2
      fi
      cd ${store}
      if runuser -u wacli -- ${lib.getExe linked}; then
        echo "Already linked. To link again, log this device out on the phone first" >&2
        echo "(WhatsApp > Linked devices > Chrome (Linux) > Log out)." >&2
        exit 1
      fi

      systemctl stop wacli.service
      # The bootstrap sync can pull a year of history; cap it like the service.
      systemd-run --scope --quiet -p MemoryHigh=512M -p MemoryMax=1G -- \
        runuser -u wacli -- ${wacliExe} --store ${store} auth --phone "$1" \
          --full-history --history-days 365 --history-size-mb 2048
      systemctl start wacli.service
      echo "Linked. wacli.service keeps the archive current; the phone may send the year of history over hours."
    '';
  };
in
{
  users.users.wacli = {
    isSystemUser = true;
    group = "wacli";
    home = store;
    description = "WhatsApp archive (wacli)";
  };
  users.groups.wacli = { };

  systemd.tmpfiles.settings."10-wacli".${store}.d = {
    user = "wacli";
    group = "wacli";
    mode = "0700";
  };

  environment.systemPackages = [
    door
    pair
  ];

  systemd.services.wacli = {
    description = "WhatsApp archive: wacli sync as a linked device";
    wantedBy = [ "multi-user.target" ];
    wants = [ "network-online.target" ];
    after = [ "network-online.target" ];

    serviceConfig = {
      # Skipped (not failed) until `sudo whatsapp-pair` has linked the store,
      # and again after the phone logs the device out.
      ExecCondition = lib.getExe linked;
      # Quiet presence: an "available" linked device makes WhatsApp hold back
      # the phone's notifications. --max-reconnect 0: never give up on the
      # network. A logout on the phone ends sync with status 0, so
      # Restart=on-failure leaves it stopped.
      ExecStart = "${wacliExe} --store ${store} sync --follow --presence-mode quiet --max-reconnect 0";
      User = "wacli";
      Group = "wacli";
      WorkingDirectory = store;
      StateDirectory = "wacli";
      StateDirectoryMode = "0700";
      Restart = "on-failure";
      RestartSec = 30;

      # Steady state is small; the bootstrap of a year of history is not. Above
      # MemoryHigh it is squeezed into zram, at MemoryMax it is killed here
      # rather than starving AdGuard (see zramSwap in default.nix).
      MemoryHigh = "512M";
      MemoryMax = "1G";

      UMask = "0077";
      NoNewPrivileges = true;
      PrivateTmp = true;
      PrivateDevices = true;
      ProtectSystem = "strict";
      ProtectHome = true;
      ProtectKernelTunables = true;
      ProtectKernelModules = true;
      ProtectKernelLogs = true;
      ProtectControlGroups = true;
      ProtectClock = true;
      ProtectHostname = true;
      ProtectProc = "invisible";
      ProcSubset = "pid";
      RestrictAddressFamilies = [
        "AF_INET"
        "AF_INET6"
        "AF_UNIX"
        "AF_NETLINK"
      ];
      RestrictNamespaces = true;
      RestrictRealtime = true;
      RestrictSUIDSGID = true;
      LockPersonality = true;
      MemoryDenyWriteExecute = true;
      CapabilityBoundingSet = "";
      SystemCallArchitectures = "native";
      SystemCallFilter = [
        "@system-service"
        "~@privileged"
      ];
    };
  };
}
