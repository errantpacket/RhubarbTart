# Map profile packages (config/packages/*.json "nixos" variants) onto NixOS:
#   { "attr": "zap" }                  -> environment.systemPackages = [ pkgs.zap ]
#   { "module": "tailscale" }          -> services.tailscale.enable = true
#   { ..., "unfree": true }            -> allowed by name (nothing else unfree is accepted)
# VPN services start unenrolled; enrollment happens per clone (scripts/enroll.sh).
{ lib, pkgs, profile, ... }:
let
  variants = lib.attrValues profile.packages;
  attrs = lib.concatMap (v: lib.optional (v ? attr) v.attr) variants;
  modules = lib.concatMap (v: lib.optional (v ? module) v.module) variants;
  unfree = lib.concatMap (v: lib.optional (v.unfree or false) (v.attr or v.module)) variants;
in
{
  nixpkgs.config.allowUnfreePredicate = p: builtins.elem (lib.getName p) unfree;

  environment.systemPackages = map (a: pkgs.${a}) attrs;

  services = lib.genAttrs modules (_: { enable = true; });
}
