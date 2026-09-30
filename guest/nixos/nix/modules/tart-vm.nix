# Hardware/boot for a Tart (Apple Virtualization.framework) arm64 Linux VM.
{ lib, profile, ... }:
let
  rosetta = profile.options.rosetta or false;
in
{
  nixpkgs.hostPlatform = "aarch64-linux";

  # Tart boots EFI from its own NVRAM; don't write EFI variables, rely on the
  # removable-media fallback path (EFI/BOOT/BOOTAA64.EFI) that systemd-boot installs.
  boot.loader.systemd-boot.enable = true;
  boot.loader.systemd-boot.editor = false; # no kernel-cmdline editing at the boot menu
  boot.loader.efi.canTouchEfiVariables = false;
  boot.initrd.availableKernelModules = [ "virtio_pci" "virtio_blk" "virtio_net" "xhci_pci" "usbhid" ];

  # Labels are created by guest/nixos/install.sh.
  fileSystems."/" = { device = "/dev/disk/by-label/nixos"; fsType = "ext4"; };
  fileSystems."/boot" = {
    device = "/dev/disk/by-label/BOOT";
    fsType = "vfat";
    options = [ "fmask=0077" "dmask=0077" ];
  };

  # `tart ip` matches DHCP leases by MAC; systemd-networkd sends a DUID by default.
  networking.useDHCP = false;
  networking.useNetworkd = true;
  systemd.network.networks."10-uplink" = {
    matchConfig.Name = "en* eth*";
    networkConfig.DHCP = "yes";
    dhcpV4Config.ClientIdentifier = "mac";
  };

  # Rosetta runs x86_64 Linux binaries; the share exists only when the VM is started
  # with `tart run --rosetta=rosetta`, so the mount must not block boot without it.
  virtualisation.rosetta.enable = rosetta;
  # The mkIf must wrap the whole "/run/rosetta" entry, not just its options: a conditional
  # options value still declares the entry (with no device or fsType) when Rosetta is off, which
  # fails evaluation (first seen with the Rosetta-less juiceshop-target profile, #83).
  fileSystems."/run/rosetta" = lib.mkIf rosetta { options = [ "nofail" ]; };
}
