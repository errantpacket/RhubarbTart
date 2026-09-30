# Optional desktop ("options": { "desktop": "xfce" } in the profile). Login is always
# manual: LightDM with no auto-login.
{ lib, pkgs, profile, ... }:
let
  desktop = profile.options.desktop or "none";
in
{
  config = lib.mkMerge [
    {
      assertions = [{
        assertion = builtins.elem desktop [ "none" "xfce" ];
        message = "profile option desktop must be \"none\" or \"xfce\" (got ${desktop})";
      }];
    }
    (lib.mkIf (desktop == "xfce") {
      services.xserver.enable = true;
      services.xserver.desktopManager.xfce.enable = true;
      services.xserver.displayManager.lightdm.enable = true;
      services.displayManager.autoLogin.enable = false;
      environment.systemPackages = [ pkgs.firefox pkgs.xfce4-terminal ];
      fonts.packages = [ pkgs.dejavu_fonts pkgs.noto-fonts ];
    })
  ];
}
