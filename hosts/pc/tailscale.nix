#
# Tailscale -- the phone reaches this PC from anywhere (T3 Code, ./t3code.nix),
# and this PC reaches the NAS's tailnet-only services by name.
#
# Same tailnet and account as the NAS (hosts/nas/tailscale.nix), so the owner
# grant in hosts/nas/tailscale-policy.hujson covers it. Unlike on the NAS,
# tailscale0 is not a trusted interface: nothing here listens for the tailnet.
# What the phone uses goes through `tailscale serve`, where tailscaled itself
# proxies to loopback, so no port needs opening.
#
# MagicDNS stays on (the default --accept-dns): the certificates `tailscale
# serve` uses are for the *.ts.net names, so clients on this PC (the T3
# desktop app, a browser) must resolve them. tailscaled registers itself with
# openresolv as the only resolver (100.100.100.100 in /etc/resolv.conf) and
# forwards every non-tailnet name to the servers NetworkManager had (AdGuard
# on the NAS, plus the router's IPv6 ones), so ad blocking is unchanged;
# `tailscale dns status` shows both sides.
#
# One-time enrolment after the first deploy:
#
#   tailscale up    # no sudo needed (operator below); open the printed URL
#
# Then disable key expiry for "pc" in the admin console, or the phone loses
# this PC after ~180 days until someone logs in again.
#
{ ... }:
{
  services.tailscale = {
    enable = true;

    # UDP 41641, so the phone can reach the PC directly instead of through a
    # DERP relay.
    openFirewall = true;

    # Lets kirill run `tailscale up`/`status`/`serve` without sudo, which on
    # this PC covers only nixos-rebuild, systemctl and journalctl.
    extraSetFlags = [ "--operator=kirill" ];
  };
}
