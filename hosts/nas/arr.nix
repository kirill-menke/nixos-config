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
{ config, lib, pkgs, ... }:
let
  inherit (config.nas) downloadRoot secretsDir;

  ports = {
    qbitWebUI = 8080;
    qbitTorrenting = 51413;
  };

  # Splices the WebUI secrets into the qBittorrent.conf the module has just
  # installed from the store (see the auth note in serverConfig). Runs as
  # ExecStartPre, as the qbittorrent user, reading systemd credentials.
  qbitWebUIAuth = pkgs.writers.writePython3Bin "qbittorrent-webui-auth" { flakeIgnore = [ "E501" ]; } ''
    import base64
    import hashlib
    import os
    import re
    import secrets
    import sys

    conf = sys.argv[1]
    creds = os.environ["CREDENTIALS_DIRECTORY"]


    def credential(name):
        with open(os.path.join(creds, name)) as f:
            return f.read().rstrip("\n")


    password = credential("webui-password")
    api_key = credential("webui-api-key").strip()

    if not password:
        sys.exit("qbittorrent-webui-auth: the WebUI password credential is empty")
    # Utils::APIKey::isValid. qBittorrent silently drops a malformed key, which
    # would only show up later as Sonarr/Radarr getting 403s.
    if not re.fullmatch(r"qbt_[A-Za-z0-9]{28}", api_key):
        sys.exit("qbittorrent-webui-auth: the API key must be qbt_ + 28 letters/digits")

    # Utils::Password::PBKDF2::generate: PBKDF2-HMAC-SHA512, 100000 rounds,
    # 16-byte salt, 64-byte key, stored as base64(salt):base64(key). A fresh
    # salt per start is fine: sessions do not survive a restart anyway.
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha512", password.encode(), salt, 100000, 64)
    pbkdf2 = base64.b64encode(salt).decode() + ":" + base64.b64encode(digest).decode()

    with open(conf) as f:
        lines = f.read().splitlines()
    at = lines.index("[Preferences]") + 1
    lines[at:at] = [
        'WebUI\\Password_PBKDF2="@ByteArray(' + pbkdf2 + ')"',
        "WebUI\\APIKey=" + api_key,
    ]

    tmp = conf + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write("\n".join(lines) + "\n")
    os.replace(tmp, conf)
  '';
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
  #
  # setgid + qBittorrent's UMask 0002 (below) make every downloaded file
  # qbittorrent:users 0664. That is what lets the import be a hardlink at all:
  # with fs.protected_hardlinks=1 the kernel only lets Sonarr link a file it
  # owns or can read AND write, and the old 0644 qbittorrent:qbittorrent files
  # were neither -- measured 2026-09, every "hardlink" import silently fell
  # back to a full copy.
  systemd.tmpfiles.rules = [
    "d ${downloadRoot}             2775 qbittorrent users -"
    "d ${downloadRoot}/incomplete  2775 qbittorrent users -"
    "d ${downloadRoot}/complete    2775 qbittorrent users -"
  ];

  # qBittorrent.conf holds the WebUI API key in clear (and the password hash,
  # see the auth note below). The ExecStartPre writes it 0600, but qBittorrent
  # rewrites the file itself at runtime under the UMask 0002 set further down
  # -- measured with 5.2.3: 0664, i.e. world-readable, which would hand the
  # key to every local process. The module creates this directory 0755; a
  # private directory makes the file's own mode moot. tmpfiles also tightens
  # the existing directory on the next activation.
  systemd.tmpfiles.settings.qbittorrent."${config.services.qbittorrent.profileDir}/qBittorrent/config/".d.mode =
    lib.mkForce "0700";

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
  users.users = lib.genAttrs [ "sonarr" "radarr" "qbittorrent" ] (_: {
    extraGroups = [ "users" ];
  });

  # Group membership is not enough on its own: with the modules' default
  # UMask 0022 every folder the *arrs create is 2755, and the jellyfin-run
  # library jobs (subtitles.nix) cannot write their temp file beside the
  # media. Measured 2026-09: strip-subtitles failed on all 67 *arr-imported
  # files, every night, with "Permission denied". 0002 makes new folders 2775
  # and files 664, which is the library's rule anyway.
  systemd.services = lib.mkMerge [
    (lib.genAttrs [ "sonarr" "radarr" "qbittorrent" ] (_: {
      serviceConfig.UMask = lib.mkForce "0002";
    }))

    {
      qbittorrent = {
        # ReadWritePaths below needs the dataset mounted, or the unit fails
        # with 226/NAMESPACE instead of waiting for it.
        unitConfig.RequiresMountsFor = [ downloadRoot ];

        serviceConfig = {
          # WebUI credentials, off this public repo. Hand-provisioned, like
          # the other secrets under secretsDir (see the auth note in
          # serverConfig for what reads them). If either file is missing
          # the unit fails to start (243/CREDENTIALS) -- deliberate, the
          # same fail-closed rule as reel-api's environmentFile.
          LoadCredential = [
            "webui-password:${secretsDir}/qbittorrent-webui-password"
            "webui-api-key:${secretsDir}/qbittorrent-api-key"
          ];
          # After the module's own ExecStartPre has installed the store copy.
          ExecStartPre = lib.mkAfter [
            "${lib.getExe qbitWebUIAuth} ${config.services.qbittorrent.profileDir}/qBittorrent/config/qBittorrent.conf"
          ];

          # Least privilege on disk: anything that does get the WebUI (and
          # with it "Run external program on torrent completion") runs as
          # this user, which is in `users` and could otherwise rewrite the
          # library. qBittorrent itself only ever writes its profile and
          # downloadRoot: the import into mediaRoot is Sonarr/Radarr's own
          # hardlink, done by their processes, and "Remove Completed"
          # deletes within downloadRoot. /tmp stays writable as under the
          # module's ProtectSystem=full: magnet metadata fetches use
          # /tmp/.qBittorrent (sessionimpl.cpp, Utils::Fs::tempPath).
          ProtectSystem = lib.mkForce "strict";
          ReadWritePaths = [
            config.services.qbittorrent.profileDir
            downloadRoot
            "/tmp"
          ];
        };
      };
    }
  ];

  # Imported downloads are deleted, not kept. qBittorrent never seeds past
  # completion (GlobalMaxSeedingMinutes = 0 below), so a finished torrent's
  # files are dead weight once imported -- and strip-subtitles rewrites the
  # library copy to a new inode anyway, which ends any hardlink sharing.
  # Measured 2026-09: 943 GB sat in complete/, nearly all of it duplicates.
  # The switch that does it is runtime state in each *arr's database, not
  # here: Settings > Download Clients > qBittorrent > "Remove Completed" is ON
  # in both Sonarr and Radarr (removeCompletedDownloads in the API).

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

    # Boot-time default only: proton-natpmp (vpn.nix) rewrites the listen
    # port over the WebUI API to whatever public port Proton's NAT-PMP hands
    # out, within a minute of every qBittorrent start. Kept fixed here anyway
    # -- a random port buys nothing and a stable pre-NAT-PMP default keeps
    # logs comparable.
    torrentingPort = ports.qbitTorrenting;

    # NOTE: the module's ExecStartPre `install`s this file over
    # /var/lib/qBittorrent/qBittorrent/config/qBittorrent.conf on EVERY start,
    # unconditionally. Anything changed in the WebUI therefore survives only
    # until the next restart. Settings belong here, not in the UI.
    #
    # That is the same bargain as `forceEncodingConfig = true` in media.nix --
    # config is declarative or it is not real -- but it has a sharp edge that
    # setting does not: a password set in the WebUI would also be wiped, so
    # the WebUI secrets are spliced back in by an ExecStartPre on every start
    # (qbitWebUIAuth above). Hence the auth handling below.
    serverConfig = {
      # Without this qBittorrent blocks on an interactive "do you accept"
      # prompt at first start and the service just sits there, never binding
      # the WebUI port and giving no obvious reason why.
      LegalNotice.Accepted = true;

      # Never seed AFTER completion, but DON'T throttle upload DURING download.
      #
      # GlobalMaxSeedingMinutes = 0 -> a torrent is paused the instant its
      # download verifies, so nothing uploads *after* completion. Paused, not
      # removed (MaxRatioAction defaults to 0 = pause), so the file stays put
      # for Sonarr/Radarr to hardlink-import and for reel-api's manual grabs to keep.
      #
      # Upload is left UNLIMITED (no GlobalUPSpeedLimit). A 1 KiB/s cap here was
      # measured to throttle DOWNLOADS ~100x: BitTorrent's choking algorithm
      # reciprocates at your upload rate, so peers were unchoking us at ~1 KiB/s
      # (113 KiB/s aggregate). Uncapped, the same swarm gave 11+ MiB/s. Since
      # seeding still stops dead at completion, uncapping only means uploading
      # during the (now much shorter) download -- best of both.
      BitTorrent.Session.GlobalMaxSeedingMinutes = 0;

      # Queueing: adaptive rather than the hard default of 3 active downloads.
      # Measured 2026-08: three parallel grabs from ~6-seed swarms pulled
      # ~10 Mbit/s of a 96 Mbit/s line (per-torrent speed is bounded by the
      # seeds' upload, not by our pipe) while better-seeded torrents sat in
      # queuedDL. With IgnoreSlowTorrentsForQueueing, a torrent below
      # 200 KiB/s for 60 s stops counting toward the active limit, so
      # supply-starved swarms make room instead of parking the queue.
      # Force-started torrents (reel-api's stream-open priority signal)
      # bypass these limits entirely, so the episode being watched always
      # keeps its slot.
      BitTorrent.Session.MaxActiveDownloads = 5;
      BitTorrent.Session.MaxActiveTorrents = 8;
      BitTorrent.Session.MaxActiveUploads = 5;
      BitTorrent.Session.IgnoreSlowTorrentsForQueueing = true;
      BitTorrent.Session.SlowTorrentsDownloadRate = 200; # KiB/s
      BitTorrent.Session.SlowTorrentsUploadRate = 200; # KiB/s
      BitTorrent.Session.SlowTorrentsInactivityTimer = 60; # seconds

      Preferences = {
        # qBittorrent's own UPnP/NAT-PMP client would race the proton-natpmp
        # loop (vpn.nix) for the mapping on Proton's gateway. One
        # port-forward manager, and it is the loop.
        Connection.UPnP = false;

        Downloads = {
          SavePath = "${downloadRoot}/complete";
          TempPath = "${downloadRoot}/incomplete";
          TempPathEnabled = true;
        };

        # Every client authenticates; there is no subnet whitelist. An
        # authenticated WebUI client can set "Run external program on
        # torrent completion" (app/setPreferences, which 5.2 has no way to
        # lock), i.e. it gets code execution as qbittorrent. The old
        # whitelist granted that to 100.64.0.0/10 -- every tailnet node --
        # and to 192.168.15.0/24, which is every process on this host:
        # host-side traffic to 192.168.15.1 always enters the namespace
        # from the bridge address 192.168.15.5, whoever sent it.
        #
        # Who uses what (secrets in secretsDir, provisioned by hand):
        #   * Sonarr, Radarr (and Prowlarr, if it has qBittorrent as a
        #     download client) and reap-stalled: the API key
        #     (qbittorrent-api-key, "qbt_" + 28 letters/digits), sent as
        #     `Authorization: Bearer`. Stateless, never expires, and exempt
        #     from the failed-login IP ban -- which matters, since all host
        #     clients share the one source address 192.168.15.5. In the
        #     *arrs: Settings > Download Clients > qBittorrent > ApiKey,
        #     with Username/Password empty (they refuse both at once).
        #   * The browser over the tailnet: user `admin` + the password
        #     (qbittorrent-webui-password, one line).
        #   * reel-api: the password too -- it cannot send a Bearer header,
        #     but its httpx client takes Basic auth from the URL and keeps
        #     the session cookie. reel-api.env carries the override, which
        #     beats the module's Environment= QBIT_URL:
        #       QBIT_URL=http://admin:<password>@192.168.15.1:8080
        #     Do NOT use QBIT_USERNAME/QBIT_PASSWORD: that logs in once and
        #     never again, so it breaks at the first session expiry or
        #     qBittorrent restart.
        #   * proton-natpmp (vpn.nix) runs inside the namespace and talks to
        #     127.0.0.1, covered by LocalHostAuth = false. Only qBittorrent
        #     and that loop live in the namespace, so loopback there is not a
        #     way in for anything else.
        #
        # The hash and key are never in the store: qbitWebUIAuth writes
        # them into the runtime copy of this file (0600, qbittorrent).
        WebUI = {
          Username = "admin";
          LocalHostAuth = false;
          AuthSubnetWhitelistEnabled = false;
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
  # The torrenting port is no longer opened: qBittorrent lives inside the
  # ProtonVPN namespace (vpn.nix), so peers could only ever reach it through
  # the tunnel -- a hole in the host firewall would sit unused. The matching
  # 51413 forward on the router is equally dead and should be removed.
}
