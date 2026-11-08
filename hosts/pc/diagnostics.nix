#
# Freeze diagnostics: after a hard freeze, the last health samples, any
# hardware errors and a lockup's backtrace are on disk.
#
{ config, pkgs, ... }:

{
  # Log CPU/NVMe temps, RAM, load and GPU state to the journal every 10s so
  # the last samples before a hard freeze are on disk.
  systemd.services.sysmon = {
    description = "Log system health metrics for crash diagnostics";
    wantedBy = [ "multi-user.target" ];
    after = [ "nvidia-persistenced.service" ];
    path = [
      pkgs.coreutils
      pkgs.gawk
      config.hardware.nvidia.package.bin
    ];
    script = ''
      while true; do
        cpu=""; nvme=""
        for h in /sys/class/hwmon/hwmon*; do
          case "$(cat "$h/name")" in
            coretemp) cpu=$(( $(cat "$h/temp1_input") / 1000 )) ;;
            nvme)     nvme=$(( $(cat "$h/temp1_input") / 1000 )) ;;
          esac
        done
        mem=$(awk '/MemAvailable/ {print int($2/1024)}' /proc/meminfo)
        swap=$(awk '/SwapFree/ {print int($2/1024)}' /proc/meminfo)
        load=$(cut -d' ' -f1-3 /proc/loadavg)
        gpu=$(nvidia-smi --query-gpu=temperature.gpu,power.draw,utilization.gpu,memory.used,clocks.sm,pstate \
              --format=csv,noheader 2>&1 | tr -d '\n')
        echo "cpu=''${cpu}C nvme=''${nvme}C mem_avail=''${mem}MiB swap_free=''${swap}MiB load=''${load} gpu: ''${gpu}"
        sleep 10
      done
    '';
    serviceConfig = {
      Restart = "always";
      RestartSec = 5;
      SyslogIdentifier = "sysmon";
    };
  };

  # Flush journal to disk every 15s (default is 5min) — otherwise the last
  # minutes before a hard freeze never reach disk and the log looks like the
  # system died earlier than it did.
  services.journald.settings.Journal.SyncIntervalSec = "15s";

  # Log MCEs / hardware errors (CPU, memory controller, PCIe AER) persistently.
  hardware.rasdaemon.enable = true;

  # Core dumps: 1 GiB at most (systemd's default is 4 GiB). A crash loop
  # (nvidia-smi on every sysmon sample, Oct 20 - Nov 4 2026) filled all 4 GiB.
  systemd.coredump.settings.Coredump.MaxUse = "1G";

  # If the kernel detects a lockup, panic instead of hanging silently: the
  # panic (with backtrace) lands in EFI pstore and is readable after reboot
  # under /sys/fs/pstore. kernel.panic=30 then reboots automatically.
  boot.kernel.sysctl = {
    "kernel.softlockup_panic" = 1;
    "kernel.hardlockup_panic" = 1;
    "kernel.panic" = 30;
    "kernel.sysrq" = 1; # allow Alt+SysRq (REISUB) as a softer escape than the power button
  };
}
