# OWASP Juice Shop as a lab *target* (#83): only in profiles whose packages include a variant
# with "service": "juice-shop" (config/packages/juice-shop.json).
#
# The app is the official prebuilt release tarball, staged by resolve.py (pinned by GitHub's
# sha256 digest, re-checked in the guest against SHA256SUMS) and copied next to this config by
# guest/nixos/install.sh. Its two native Node add-ons (sqlite3, libxmljs2) are built for FHS
# Linux, so autoPatchelfHook points them at Nix's libraries. Node itself comes from the pinned
# nixpkgs (the release targets Node 22).
#
# Juice Shop writes into its own tree (SQLite DB, uploads, logs), and the Nix store is
# read-only, so the service runs from a fresh copy under /var/lib/juice-shop on every start:
# each boot (and each clone) starts from the same clean state.
{ lib, pkgs, profile, ... }:
let
  variant = lib.findFirst (v: (v.service or null) == "juice-shop") null (lib.attrValues profile.packages);

  juiceShop = pkgs.stdenv.mkDerivation {
    pname = "juice-shop";
    version = variant.version;
    src = ../artifacts + "/${variant.file}";
    nativeBuildInputs = [ pkgs.autoPatchelfHook ];
    buildInputs = [ pkgs.stdenv.cc.cc.lib ];   # libstdc++ for the native add-ons
    dontConfigure = true;
    dontBuild = true;
    installPhase = ''
      runHook preInstall
      mkdir -p $out/share
      cp -r . $out/share/juice-shop
      runHook postInstall
    '';
  };

  appDir = "/var/lib/juice-shop/app";

  # Fresh copy of the app on every start. cp -r of the store tree makes read-only directories,
  # and only the final chmod makes them writable, so a copy cut short (the clone stopped or
  # powered off mid-copy, which `rhubarbtart new`'s short password-rotation boot can do) used to
  # leave a tree that rm -rf could not delete: every later start failed with "Permission denied"
  # until systemd's start limit gave up (#98). Making any leftover writable first fixes that.
  prepareApp = pkgs.writeShellScript "juice-shop-prepare" ''
    set -eu
    if [ -e ${appDir} ]; then chmod -R u+w ${appDir}; fi
    rm -rf ${appDir}
    cp -r ${juiceShop}/share/juice-shop ${appDir}
    chmod -R u+w ${appDir}
  '';

  # The Tart host: the only peer the app may talk to (the smoke test and `rhubarbtart engagement
  # connect` reach port 3000 from there). It is the SSH from= pin when that's a single IPv4,
  # else Tart's default vmnet gateway.
  sshFrom = (lib.importJSON ./../ssh.json).from or "";
  hostAddr = if builtins.match "[0-9]+\\.[0-9]+\\.[0-9]+\\.[0-9]+" sshFrom != null
             then sshFrom else "192.168.64.1";
in
lib.mkIf (variant != null) {
  users.users.juiceshop = { isSystemUser = true; group = "juiceshop"; home = "/var/lib/juice-shop"; };
  users.groups.juiceshop = { };

  systemd.services.juice-shop = {
    description = "OWASP Juice Shop (lab target)";
    wantedBy = [ "multi-user.target" ];
    after = [ "network.target" ];
    environment = { NODE_ENV = "production"; PORT = "3000"; };
    serviceConfig = {
      User = "juiceshop";
      Group = "juiceshop";
      StateDirectory = "juice-shop";
      StateDirectoryMode = "0750";
      ExecStartPre = prepareApp;
      # "-": the app dir doesn't exist yet when ExecStartPre (which creates it) runs; systemd
      # would otherwise fail that step at CHDIR. ExecStart then runs inside the fresh copy.
      WorkingDirectory = "-${appDir}";
      ExecStart = "${pkgs.nodejs_22}/bin/node build/app";
      Restart = "on-failure";
      # Sandbox: it's an intentionally vulnerable app, so contain it tightly.
      NoNewPrivileges = true;
      ProtectSystem = "strict";
      ProtectHome = true;
      PrivateTmp = true;
      PrivateDevices = true;
      ProtectKernelTunables = true;
      ProtectKernelModules = true;
      ProtectControlGroups = true;
      RestrictSUIDSGID = true;
      RestrictNamespaces = true;
      LockPersonality = true;
      CapabilityBoundingSet = "";
      AmbientCapabilities = "";
      RestrictAddressFamilies = [ "AF_INET" "AF_INET6" "AF_UNIX" ];
      # No egress (#30): an exploited app (SSRF, RCE as juiceshop) must not reach the LAN or
      # the internet. Enforced by systemd's cgroup BPF filter, outside the service's control.
      IPAddressDeny = "any";
      IPAddressAllow = [ "localhost" hostAddr ];
    };
  };

  networking.firewall.allowedTCPPorts = variant.ports or [ ];
  # And it may not *initiate* anything off-box, not even to the host: replies to inbound
  # connections pass, new outbound connections by the juiceshop user are rejected. Loopback
  # stays open (Juice Shop's own SSRF challenge targets localhost).
  networking.firewall.extraCommands = ''
    ip46tables -N rbt-juiceshop-out 2>/dev/null || true
    ip46tables -F rbt-juiceshop-out
    ip46tables -A rbt-juiceshop-out -o lo -j RETURN
    ip46tables -A rbt-juiceshop-out -m conntrack --ctstate NEW -j REJECT
    ip46tables -D OUTPUT -m owner --uid-owner juiceshop -j rbt-juiceshop-out 2>/dev/null || true
    ip46tables -A OUTPUT -m owner --uid-owner juiceshop -j rbt-juiceshop-out
  '';
  networking.firewall.extraStopCommands = ''
    ip46tables -D OUTPUT -m owner --uid-owner juiceshop -j rbt-juiceshop-out 2>/dev/null || true
    ip46tables -F rbt-juiceshop-out 2>/dev/null || true
    ip46tables -X rbt-juiceshop-out 2>/dev/null || true
  '';
}
