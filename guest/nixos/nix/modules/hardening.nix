# The RhubarbTart security posture, declared (mirrors guest/macos/finalize.sh):
# no default/empty passwords, no auto-login, sudo needs a password, key-only SSH
# restricted to the Tart host (or no SSH at all), firewall on, root locked.
{ lib, pkgs, profile, ... }:
let
  user = profile.username;
  keys = lib.filter (k: k != "" && !(lib.hasPrefix "#" k))
    (lib.splitString "\n" (builtins.readFile ./../authorized_keys));
  sshFrom = (lib.importJSON ./../ssh.json).from or "";
  keyOpts = lib.optionalString (sshFrom != "") ''from="${sshFrom}",''
    + "no-agent-forwarding,no-X11-forwarding ";
  sshEnabled = keys != [ ];
in
{
  users.mutableUsers = false;
  users.users.root.hashedPassword = "!"; # locked
  users.users.${user} = {
    isNormalUser = true;
    extraGroups = [ "wheel" ];
    # Written 0600 by install.sh from the host-keychain password; never in the Nix store.
    hashedPasswordFile = "/var/lib/rhubarbtart/password.hash";
    openssh.authorizedKeys.keys = map (k: keyOpts + k) keys;
  };

  security.sudo.enable = true;
  security.sudo.wheelNeedsPassword = true;
  security.sudo.execWheelOnly = true;

  services.openssh = {
    enable = sshEnabled;
    openFirewall = sshEnabled;
    settings = {
      PasswordAuthentication = false;
      KbdInteractiveAuthentication = false;
      AuthenticationMethods = "publickey";
      PermitRootLogin = "no";
      AllowUsers = [ user ];
      AllowAgentForwarding = false;
      X11Forwarding = false;
      PermitTunnel = "no";
    };
  };

  # `rhubarbtart new` rotates each clone's password: it writes a fresh yescrypt hash to the
  # hashedPasswordFile above (see tools/rhubarb/hostops.py), which needs mkpasswd.
  environment.systemPackages = [ pkgs.mkpasswd ];

  networking.firewall.enable = true;
  networking.firewall.logRefusedConnections = false;

  services.getty.autologinUser = lib.mkForce null;

  # Only admins may talk to the Nix daemon; binaries only from signed caches.
  nix.settings.allowed-users = [ "@wheel" ];
  nix.settings.trusted-users = [ "root" ];
  nix.settings.require-sigs = true;

  boot.kernel.sysctl = {
    "kernel.kptr_restrict" = 2;
    "kernel.dmesg_restrict" = 1;
    "kernel.unprivileged_bpf_disabled" = 1;
    "net.core.bpf_jit_harden" = 2;
  };
}
