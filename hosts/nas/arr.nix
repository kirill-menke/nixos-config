#
# Automated acquisition: Prowlarr feeds indexers to Sonarr (TV) and Radarr
# (film), which drive qBittorrent and then import into the Jellyfin library.
#
# Why this shape:
#
#   * Prowlarr is the ONLY place indexers get configured. Sonarr and Radarr
#     hold no indexer definitions of their own -- Prowlarr pushes them over
#     each app's API, so adding a tracker is one edit in one UI instead of
#     three. This is the entire reason it exists; without it you maintain the
#     same list twice.
#   * All four run here rather than on the PC because the import step is a
#     hardlink. Sonarr moving a finished download into the library costs zero
#     bytes and zero seconds only when the download directory and the library
#     are on the same filesystem -- which is what retires the
#     download-on-the-PC-then-rsync-100GB-over-the-LAN workflow.
#   * ZFS datasets are separate filesystems and hardlinks cannot cross them,
#     so `downloads` sits beside `media` INSIDE tank/data. A dedicated
#     tank/downloads dataset would look tidier and would silently turn every
#     import back into a full copy plus double disk usage.
#
# Deliberately NOT included: Bazarr. subtitles.nix already watches mediaRoot
# with inotify and fetches subtitles within seconds of a file landing, and
# that watcher fires on Sonarr's imports exactly as it does on manual copies.
# Two subtitle fetchers would fight over the same .srt paths.
#
{
  config,
  lib,
  pkgs,
  ...
}:
let
  mediaRoot = "/tank/data/media";
  downloadRoot = "/tank/data/downloads";

  ports = {
    prowlarr = 9696;
    sonarr = 8989;
    radarr = 7878;
    qbitWebUI = 8080;
    qbitTorrenting = 51413;
  };
in
{
  #############################################################################
  # Download layout
  #############################################################################

  # Same dataset as mediaRoot -- see the hardlink note in the header.
  #
  # Split incomplete/ from complete/ so Sonarr never sees a half-written file:
  # it watches complete/, and qBittorrent only moves a torrent there once the
  # last piece has been verified. Both are on the same filesystem, so that
  # move is a rename rather than a copy.
  #
  # Group-writable by `users` for the same reason mediaRoot is: so you can
  # still reach into these by hand without sudo.
  systemd.tmpfiles.rules = [
    "d ${downloadRoot}             0775 qbittorrent users -"
    "d ${downloadRoot}/incomplete  0775 qbittorrent users -"
    "d ${downloadRoot}/complete    0775 qbittorrent users -"
  ];

  #############################################################################
  # Library write access
  #############################################################################

  # Sonarr and Radarr must write into mediaRoot, which is 0775 jellyfin:users.
  # Supplementary membership rather than overriding each module's `group`,
  # because the modules declare their own primary group and fighting that
  # invites an assertion on rebuild.
  #
  # qBittorrent needs it too: not for the library, but so files it creates
  # under downloadRoot stay readable to the *arr users that hardlink them.
  users.users.sonarr.extraGroups = [ "users" ];
  users.users.radarr.extraGroups = [ "users" ];
  users.users.qbittorrent.extraGroups = [ "users" ];

  #############################################################################
  # Indexer manager
  #############################################################################

  services.prowlarr = {
    enable = true;

    # Tailnet-only, exactly as adguard.nix does it. These UIs reach out to
    # trackers and hold their credentials, so they are strictly worse things
    # to leave open on the LAN than Jellyfin is.
    openFirewall = false;
  };

  #############################################################################
  # TV and film
  #############################################################################

  services.sonarr = {
    enable = true;
    openFirewall = false;
  };

  services.radarr = {
    enable = true;
    openFirewall = false;
  };

  #############################################################################
  # Download client
  #############################################################################

  services.qbittorrent = {
    enable = true;
    openFirewall = false;
    webuiPort = ports.qbitWebUI;

    # Fixed rather than random so the router forward below stays valid across
    # restarts. Incoming peer connections still require a matching port
    # forward on the router itself -- without it you get outbound-only peers,
    # which works but connects slowly.
    torrentingPort = ports.qbitTorrenting;

    # NOTE: the module's ExecStartPre `install`s this file over
    # /var/lib/qBittorrent/qBittorrent/config/qBittorrent.conf on EVERY start,
    # unconditionally. Anything changed in the WebUI therefore survives only
    # until the next restart. Settings belong here, not in the UI.
    #
    # That is the same bargain as `forceEncodingConfig = true` in media.nix --
    # config is declarative or it is not real -- but it has a sharp edge that
    # setting does not: a WebUI password would also be wiped, qBittorrent
    # would mint a fresh random one into the journal on each boot, and the
    # Sonarr/Radarr download-client connections would break every reboot.
    # Hence the auth handling below.
    serverConfig = {
      # Without this qBittorrent blocks on an interactive "do you accept"
      # prompt at first start and the service just sits there, never binding
      # the WebUI port and giving no obvious reason why.
      LegalNotice.Accepted = true;

      Preferences = {
        Downloads = {
          SavePath = "${downloadRoot}/complete";
          TempPath = "${downloadRoot}/incomplete";
          TempPathEnabled = true;
        };

        # Auth by network position rather than by a password, because the
        # alternative is a PBKDF2 hash sitting in the world-readable Nix
        # store, and because no password can persist across a restart anyway
        # (see above).
        #
        # Sonarr and Radarr reach the API over loopback, so LocalHostAuth
        # covers them. The 100.64.0.0/10 whitelist is the tailnet, which is
        # the only other way in -- the firewall never opens 8080 to the LAN.
        #
        # If either key name is wrong for this qBittorrent version the failure
        # is safe: an unrecognised key is ignored, auth stays on, and you get
        # a login prompt rather than an open UI.
        WebUI = {
          LocalHostAuth = false;
          AuthSubnetWhitelistEnabled = true;
          AuthSubnetWhitelist = "100.64.0.0/10";
        };
      };
    };
  };

  #############################################################################
  # Firewall
  #############################################################################

  # Web UIs are absent here on purpose (tailnet-only, see above); tailscale0
  # is a trusted interface via tailscale.nix, so they are reachable over the
  # tailnet without any rule.
  #
  # The torrenting port is the one thing that genuinely must accept traffic
  # from outside, so it is opened on all interfaces.
  networking.firewall = {
    allowedTCPPorts = [ ports.qbitTorrenting ];
    allowedUDPPorts = [ ports.qbitTorrenting ]; # DHT / uTP
  };
}
