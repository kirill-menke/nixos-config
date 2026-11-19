#
# Vaultwarden on the PC: the Bitwarden desktop app for Kirill, and for agents
# rbw, the Bitwarden CLI the NAS has too (hosts/nas/vaultwarden.nix), the
# vault fill tool (./vault-fill/), with which omp agents log in to websites
# for Kirill without the password ever showing up in the chat, and the mail
# tool (./mail.py), with which they read his Outlook mail and its login codes.
#
# rbw-agent keeps the unlocked vault key in memory until logout (Kirill's
# choice). It runs as a service of the graphical session, so its password
# window (pinentry-qt, on PATH as `pinentry`) always reaches the screen: the
# agent rbw would start on demand inherits its caller's environment, and a
# T3 Code session has no WAYLAND_DISPLAY. Logging out stops it and so locks
# the vault.
#
# One-time setup, as kirill (the server's tailnet name stays out of this
# public repo):
#   rbw config set email <account email>
#   rbw config set base_url https://<NAS tailnet name>:6443
#   rbw config set lock_timeout 31536000   # a year: in effect, until logout
#   rbw login     # registers the PC; the master password goes into the window
#
{ pkgs, lib, ... }:

let
  # OCR for scanned PDFs (pdf_text), with only the languages Kirill's mail
  # comes in
  tesseract = pkgs.tesseract.override {
    enableLanguages = [
      "deu"
      "eng"
    ];
  };

  # vault-fill.py, kept as ordered parts so that several people can edit it at
  # once; concatenated in file-name order, they are the installed file byte
  # for byte: 00-cheatsheet (header and cheat-sheet), 10-core-cdp-frames,
  # 20-page-js, 30-vault-login, 40-shop-browser, 50-tab-base, 60-tab-read,
  # 70-tab-act, 80-tab-collect-captcha, 90-fetch-pdf-glue. A part is cut at a
  # boundary between top-level statements or ShopTab methods, so it needn't
  # parse alone; only the whole file must.
  vaultFillSource = pkgs.concatText "vault-fill.py" (
    map (part: ./vault-fill + "/${part}") (
      builtins.filter (lib.hasSuffix ".py") (builtins.attrNames (builtins.readDir ./vault-fill))
    )
  );
in
{
  home-manager.users.kirill =
    { config, ... }:
    {
      home.packages = [
        # Kirill's own client; its login screen's "self-hosted" server URL is
        # the same as rbw's base_url above
        pkgs.bitwarden-desktop
        pkgs.rbw
        pkgs.pinentry-qt
        # pdftotext and friends, for agents in a shell too
        pkgs.poppler-utils
      ];

      systemd.user.services.rbw-agent = {
        Unit = {
          Description = "rbw agent: the unlocked Vaultwarden key, until logout";
          After = [ config.wayland.systemd.target ];
          PartOf = [ config.wayland.systemd.target ];
        };
        Service = {
          ExecStart = "${pkgs.rbw}/bin/rbw-agent --no-daemonize";
          # Also after a clean exit: `rbw config set` and `rbw stop-agent` end
          # the agent, and the next rbw call would otherwise start one outside
          # this service, without the session's display.
          Restart = "always";
          RestartSec = 1;
        };
        Install.WantedBy = [ config.wayland.systemd.target ];
      };

      # %load-ed by agents into their Python eval kernel (PROFILE.md's rules);
      # its pdf_text reads PDFs with poppler's tools, scans with tesseract;
      # captcha() draws its montage with ImageMagick, labels in DejaVu Sans.
      xdg.dataFile."vault-fill/vault-fill.py".source = pkgs.replaceVarsWith {
        name = "vault-fill.py";
        src = vaultFillSource;
        replacements = {
          rbw = "${pkgs.rbw}/bin/rbw";
          chrome = "${config.home.profileDirectory}/bin/google-chrome-stable";
          xvfb = "${pkgs.xvfb}/bin/Xvfb";
          pdftotext = "${pkgs.poppler-utils}/bin/pdftotext";
          pdftoppm = "${pkgs.poppler-utils}/bin/pdftoppm";
          tesseract = "${tesseract}/bin/tesseract";
          magick = "${pkgs.imagemagick}/bin/magick";
          font = "${pkgs.dejavu_fonts}/share/fonts/truetype/DejaVuSans.ttf";
        };
      };
      xdg.dataFile."vault-fill/mail.py".source = ./mail.py;
    };
}
