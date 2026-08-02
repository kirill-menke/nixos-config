#
# Tailscale -- remote access to the NAS, primarily Jellyfin on the iPhone.
#
# Why Tailscale rather than plain WireGuard:
#
#   * Plain WireGuard needs an inbound UDP port forwarded on the router and a
#     stable name (DDNS) for the home connection -- and behind DS-Lite/CGNAT
#     there is no inbound IPv4 to forward at all. Tailscale NAT-traverses from
#     both ends (falling back to a DERP relay when it must), so none of that
#     infrastructure exists to maintain or break.
#   * Per-device identity: each phone/laptop is enrolled and revoked
#     individually from the admin console, instead of hand-rotated peer blocks
#     in a config file.
#   * It is still WireGuard underneath; media traffic stays end-to-end
#     encrypted between the devices.
#
# The trade is a third-party control plane: login and key distribution go
# through Tailscale's coordination server (free tier covers this). If that
# ever becomes unacceptable, Headscale is a self-hosted drop-in replacement
# for the control plane and this module stays the same.
#
# One-time enrolment after deploy (prints a login URL to open on any device):
#
#   ssh root@nas.local tailscale up
#
# Then, in the admin console (login.tailscale.com), disable key expiry for
# this node -- otherwise it silently falls off the tailnet after ~180 days,
# which on a headless box you would only notice when streaming breaks.
#
{ ... }:
{
  services.tailscale = {
    enable = true;

    # UDP 41641, so peers can establish a direct connection instead of pushing
    # every stream through a DERP relay.
    openFirewall = true;

    # This box must never have its own name resolution depend on tailscaled
    # being alive (same reasoning as wait-online being disabled: nothing on a
    # headless host may hinge on one network daemon). MagicDNS is a client
    # feature anyway -- the phone still resolves `nas` on its side.
    extraSetFlags = [ "--accept-dns=false" ];
  };

  # Trust the tailnet interface wholesale rather than enumerating ports.
  # Every packet arriving on tailscale0 is already WireGuard-authenticated as
  # a device logged into this tailnet -- a stronger check than anything on the
  # LAN gets. This makes Jellyfin (8096) and SSH reachable remotely; the NFS
  # export stays LAN-only regardless, because exports are restricted to
  # 192.168.0.0/24.
  networking.firewall.trustedInterfaces = [ "tailscale0" ];
}
