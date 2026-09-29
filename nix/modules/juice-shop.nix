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
      ExecStartPre = "${pkgs.bash}/bin/bash -c 'rm -rf ${appDir} && cp -r ${juiceShop}/share/juice-shop ${appDir} && chmod -R u+w ${appDir}'";
      WorkingDirectory = appDir;
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
    };
  };

  networking.firewall.allowedTCPPorts = variant.ports or [ ];
}
