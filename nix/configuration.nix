# RhubarbTart NixOS guest. Installed by guest/nixos/install.sh with
#   nixos-install -I nixpkgs=<pinned nixpkgs> -I nixos-config=/mnt/etc/nixos/configuration.nix
#
# Everything the image contains is decided here and by profile.json (generated from
# profiles/<id>.json by `resolve.py verify`), so a profile is the whole spec of the guest.
# Files staged next to this one at install time (never committed):
#   profile.json      packages + options for this profile
#   ssh.json          { "from": "<authorized_keys from= pattern>" }
#   authorized_keys   public keys allowed to SSH in (empty => sshd disabled)
#   lock.json         the reviewed lock this image was built from (copied into /etc for audit)
{ lib, pkgs, ... }:
let
  profile = lib.importJSON ./profile.json;
in
{
  imports = [
    ./modules/tart-vm.nix
    ./modules/hardening.nix
    ./modules/packages.nix
    ./modules/desktop.nix
  ];

  _module.args.profile = profile;

  networking.hostName = "rhubarb";
  time.timeZone = "UTC";
  i18n.defaultLocale = "en_US.UTF-8";

  # The pinned nixpkgs used to build this system is also what `nixos-rebuild` and
  # `nix-shell -p` use inside the guest: no channel, no floating registry entries.
  nix.nixPath = [ "nixpkgs=/etc/rhubarbtart/nixpkgs" ];
  environment.etc."rhubarbtart/nixpkgs".source = pkgs.path;
  nix.channel.enable = false;
  # Flakes/nix-command stay off, so the classic pinned nixPath above is the only source of
  # nixpkgs and the flake registry is never consulted. Clear it anyway so a later operator who
  # turns flakes on doesn't inherit a floating github: registry. Do NOT set
  # nix.settings.flake-registry here: that writes a flake-registry line into nix.conf, which
  # nix rejects while the flakes feature is disabled (breaks the nix.conf build).
  nix.registry = lib.mkForce { };

  environment.etc."rhubarbtart/lock.json".source = ./lock.json;
  environment.etc."rhubarbtart/profile.json".source = ./profile.json;

  # GitHub archives lack .git-revision; stamp the locked commit so `nixos-version`
  # reports exactly which nixpkgs built this image.
  system.nixos.revision = profile.nixpkgs.rev;
  system.nixos.versionSuffix = ".${builtins.substring 0 12 profile.nixpkgs.rev}";

  # Every image is a fresh install of the pinned nixpkgs release, so the state version is
  # that release (it must NOT change inside an existing clone; rebuild images instead).
  system.stateVersion = lib.trivial.release;
}
