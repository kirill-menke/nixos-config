#
# Media serving for the NAS: Jellyfin + an NFS export of the library.
#
# Design intent, and why it is split this way:
#
#   * Jellyfin serves the LG C4 and the iPhone over WiFi. For the TV this is a
#     Direct Play path -- the NAS reads bytes off disk and pushes them, nothing
#     is decoded or re-encoded server-side.
#   * The NFS export exists so the desktop can keep playing the same files
#     locally via `play <file>` over HDMI. That path is the ONLY one that gets
#     lossless Dolby TrueHD Atmos, because the Jellyfin webOS app cannot
#     bitstream TrueHD. Moving the library to the NAS must not break it.
#
# Audio reality check for the TV path: the 4K rips carry both a TrueHD Atmos
# track and an E-AC3 (DD+) Atmos 5.1.2 track. webOS will direct-play the E-AC3
# one and pass it untouched over eARC to the Q995GF, so you still get real
# object-based Atmos -- lossy rather than lossless. Nothing is transcoded.
#
{
  config,
  lib,
  pkgs,
  ...
}:
let
  mediaRoot = "/tank/data/media";
in
{
  #############################################################################
  # Library layout
  #############################################################################

  # Owned by the jellyfin user so it can index, but group-writable by `users`
  # so you can drop files in over NFS without sudo.
  systemd.tmpfiles.rules = [
    "d ${mediaRoot}         0775 jellyfin users -"
    "d ${mediaRoot}/movies  0775 jellyfin users -"
    "d ${mediaRoot}/shows   0775 jellyfin users -"

    # Parent for the tmpfs transcode mount defined below.
    "d /var/cache/jellyfin  0755 jellyfin jellyfin -"
  ];

  #############################################################################
  # GPU / QuickSync
  #############################################################################

  # Headless box, but the Alder Lake-N iGPU is still wanted for transcoding.
  # intel-media-driver provides the iHD VAAPI driver (Gen8+), which is what
  # Jellyfin's QSV path sits on top of.
  #
  # intel-compute-runtime is not optional despite nothing here using OpenCL
  # directly. `enableToneMapping = true` below makes Jellyfin build a filter
  # chain around tonemap_opencl, and without an OpenCL ICD registered ffmpeg
  # aborts before it decodes a single frame:
  #
  #     Failed to get number of OpenCL platforms: -1001   (CL_PLATFORM_NOT_FOUND_KHR)
  #     Failed to set value 'opencl=ocl@va' for option 'init_hw_device'
  #
  # That kills EVERY transcode of HDR/DV content, not just an occasional one.
  # It stayed hidden because the C4 direct-plays; it surfaces the moment any
  # client asks for a capped bitrate, or the iPhone hits a 4K HDR file.
  hardware.graphics = {
    enable = true;
    extraPackages = with pkgs; [
      intel-media-driver # iHD -- HEVC/AV1/VP9 decode, H.264/HEVC encode
      vpl-gpu-rt # oneVPL runtime, the modern QSV entry point
      intel-compute-runtime # OpenCL (NEO) -- required by tonemap_opencl
    ];
  };

  # `vainfo` for diagnosing the GPU. This has to be in systemPackages, not in
  # hardware.graphics.extraPackages -- the latter only populates the driver
  # search path, it does not put any binary on PATH.
  environment.systemPackages = [ pkgs.libva-utils ];

  # Jellyfin runs as its own user and needs the render node to do anything on
  # the GPU. Without this it silently falls back to software transcoding.
  users.users.jellyfin.extraGroups = [
    "video"
    "render"
  ];

  #############################################################################
  # Jellyfin
  #############################################################################

  services.jellyfin = {
    enable = true;
    openFirewall = true; # 8096/tcp (and 8920 https, 1900+7359 udp discovery)
    user = "jellyfin";
    group = "jellyfin";

    hardwareAcceleration = {
      enable = true;
      type = "qsv"; # Intel Quick Sync, via the iHD driver above
      device = "/dev/dri/renderD128";
    };

    # Write encoding.xml from this config on every start, so transcoding
    # behaviour is declarative rather than clicked into the web UI and then
    # forgotten. A timestamped backup of the previous file is kept.
    forceEncodingConfig = true;

    transcoding = {
      # Decode in hardware for everything the iGPU can handle. av1 matters for
      # the iPhone: the base iPhone 15 is an A16 with no AV1 decode, so AV1
      # files are the one case that genuinely must be transcoded.
      hardwareDecodingCodecs = {
        h264 = true;
        hevc = true;
        hevc10bit = true;
        vp9 = true;
        av1 = true;
      };

      enableHardwareEncoding = true;
      # h264 is always enabled. HEVC too, so the phone can get an efficient
      # stream when a transcode is unavoidable.
      hardwareEncodingCodecs.hevc = true;

      # HDR -> SDR tone mapping, needed whenever a 4K HDR/DV file has to be
      # transcoded for a client that can't do HDR. Without it those transcodes
      # come out washed out and grey.
      enableToneMapping = true;

      # Extract text subtitles rather than burning them in. This is the single
      # most important setting for keeping Direct Play: an image-based PGS
      # track that has to be burned in forces a FULL video transcode, which
      # would throw away the 4K DV picture entirely.
      enableSubtitleExtraction = true;

      # The N150 is a 4-core part with no SMT. Leave headroom for ZFS and the
      # NFS server rather than letting ffmpeg take the whole box.
      threadCount = 3;
      maxConcurrentStreams = 2;

      # Stop transcoding far ahead of the playhead; saves CPU and disk when
      # someone seeks or abandons a stream.
      throttleTranscoding = true;
      deleteSegments = true;
    };
  };

  # Transcode scratch goes to the default /var/cache/jellyfin on the NVMe.
  # A tmpfs was considered and deliberately rejected: it needs explicit mount
  # ordering against jellyfin.service and pins RAM, in exchange for avoiding
  # SSD wear that is negligible at this workload -- the TV direct-plays
  # everything, so in practice only the iPhone hitting an AV1 file transcodes
  # at all. `deleteSegments` below keeps the directory from growing.

  #############################################################################
  # NFS export -- so the desktop keeps its lossless TrueHD path
  #############################################################################

  services.nfs.server = {
    enable = true;
    # Fixed ports so the firewall rule below is small and predictable, rather
    # than relying on rpcbind handing out arbitrary ones.
    lockdPort = 4001;
    mountdPort = 4002;
    statdPort = 4000;

    # NFSv4 only (no `insecure`, no v3 cruft). Read-only: the desktop plays
    # from here, it does not manage the library over NFS. Change `ro` to `rw`
    # if you later want to write from the PC.
    exports = ''
      ${mediaRoot} 192.168.0.0/24(ro,sync,no_subtree_check,root_squash)
    '';
  };

  networking.firewall = {
    allowedTCPPorts = [
      2049 # nfs4
      4000
      4001
      4002
    ];
    allowedUDPPorts = [
      4000
      4001
      4002
    ];
  };
}
