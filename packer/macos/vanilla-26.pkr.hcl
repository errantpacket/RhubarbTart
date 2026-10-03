# Stage 1: vanilla macOS 26 from a locally verified Apple IPSW.
#
# The IPSW path comes from `tools/resolve.py verify`, which has already checked
# its sha256 against Apple's CDN metadata. Packer/Tart never download the OS
# themselves, and we never start from a prebuilt third-party image.
#
# The Setup Assistant boot_command is adapted from cirruslabs/macos-image-templates
# templates/vanilla-tahoe.pkr.hcl @ 2ff087f (2026-09-21, tested there on 26.6.2).
# It is timing- and layout-sensitive: re-validate it whenever the pinned build changes.
# Unlike upstream (a CI image), this template leaves Gatekeeper and SIP ENABLED.

packer {
  required_plugins {
    tart = {
      version = "= 1.21.0"
      source  = "github.com/cirruslabs/tart"
    }
  }
}

variable "ipsw_path" {
  type = string
}

variable "vm_name" {
  type = string
}

variable "cpu_count" {
  type    = number
  default = 4
}

variable "memory_gb" {
  type    = number
  default = 8
}

variable "disk_gb" {
  type    = number
  default = 80
}

# No defaults on purpose: scripts/build.sh supplies both via PKR_VAR_* env vars.
# Letters and digits only: the password is typed over VNC and embedded in shell.
variable "username" {
  type = string
  validation {
    condition     = can(regex("^[a-z][a-z0-9]{2,15}$", var.username))
    error_message = "The username must be 3-16 lowercase letters/digits, starting with a letter."
  }
}

variable "password" {
  type      = string
  sensitive = true
  validation {
    condition     = can(regex("^[A-Za-z0-9]{20,64}$", var.password)) && var.password != "admin"
    error_message = "The password must be 20-64 letters or digits; scripts/build.sh generates one."
  }
}

source "tart-cli" "vanilla" {
  from_ipsw      = var.ipsw_path
  vm_name        = var.vm_name
  cpu_count      = var.cpu_count
  memory_gb      = var.memory_gb
  disk_size_gb   = var.disk_gb
  run_extra_args = ["--no-audio", "--no-clipboard"] # no host microphone or clipboard (#170)
  ssh_username   = var.username
  ssh_password   = var.password
  ssh_timeout    = "300s"

  boot_command = [
    # hello, hola, bonjour, etc.
    "<wait60s><spacebar>",
    # Language: bounce through Italiano so "english" lands on English (US), not English (UK)
    # Then click the exact "English" entry: typing into a searchable list can land on a
    # neighbour if a keystroke lags (#63), and the exact entry is only on screen when the search
    # worked, so a wrong landing waits here until build.sh's stage-1 deadline instead of
    # continuing with another language.
    "<wait '^English$'><wait3s>italiano<esc>english<wait3s><click '^English$'><wait2s><enter>",
    # Select Your Country or Region
    # Type a short prefix, then click the exact entry (#63): "united states" once landed on
    # Estonia when the search restarted mid-word ("es").
    "<wait60s><click 'Select Your Country or Region'><wait5s>united<wait3s><click '^United States$'><wait2s><leftShiftOn><tab><leftShiftOff><spacebar>",
    # From here to account creation each step first waits for text only its own screen shows,
    # then a short settle, instead of a fixed wait (#63). On a busy host a fixed <wait10s> sent
    # the Accessibility keys before that screen had set its focus: Shift-Tab landed on
    # "Cognitive", and every later step hit the wrong screen. <wait 'TEXT'> polls the screen with
    # Vision text recognition; build.sh puts a deadline on stage 1 in case a screen's text changes.
    # Transfer Your Data to This Mac -> Not now
    "<wait 'Transfer Your Data'><wait3s><tab><tab><tab><spacebar><tab><tab><spacebar>",
    # Written and Spoken Languages
    "<wait 'Written and Spoken Languages'><wait3s><leftShiftOn><tab><leftShiftOff><spacebar>",
    # Accessibility ("Cognitive" is one of its four categories, shown on no other screen)
    "<wait 'Cognitive'><wait3s><leftShiftOn><tab><leftShiftOff><spacebar>",
    # Data & Privacy
    "<wait 'Data & Privacy'><wait3s><leftShiftOn><tab><leftShiftOff><spacebar>",
    # Create a Mac Account: full name, account name, password, verify
    "<wait 'Create a Mac Account'><wait3s><tab><tab><tab><tab><tab><tab>RhubarbTart<tab>${var.username}<tab>${var.password}<tab>${var.password}<tab><tab><spacebar><tab><tab><spacebar>",
    # Enable VoiceOver (makes the remaining screens keyboard-navigable)
    "<wait120s><leftAltOn><f5><leftAltOff>",
    # Sign In with Your Apple ID -> Set up later
    "<wait10s><leftShiftOn><tab><leftShiftOff><spacebar><up><spacebar>",
    # Are you sure you want to skip signing in with an Apple ID?
    "<wait10s><tab><spacebar>",
    # Terms and Conditions
    "<wait10s><leftShiftOn><tab><leftShiftOff><spacebar>",
    # I have read and agree to the macOS Software License Agreement
    "<wait10s><tab><spacebar>",
    # Age Range -> Adult
    "<wait10s><tab><tab><tab><spacebar>",
    # Enable Location Services -> no
    "<wait10s><leftShiftOn><tab><leftShiftOff><spacebar>",
    # Are you sure you don't want to use Location Services?
    "<wait10s><tab><spacebar>",
    # Select Your Time Zone
    "<wait10s><tab><tab><tab>UTC<enter><leftShiftOn><tab><leftShiftOff><spacebar>",
    # Analytics -> don't share
    "<wait10s><leftShiftOn><tab><leftShiftOff><spacebar>",
    # Screen Time -> later
    "<wait10s><tab><tab><spacebar>",
    # Siri -> off
    "<wait10s><tab><spacebar><leftShiftOn><tab><leftShiftOff><spacebar>",
    # Your Mac is Ready for FileVault -> not now (host disk encryption covers the VM bundle)
    "<wait10s><leftShiftOn><tab><tab><leftShiftOff><spacebar>",
    # Mac Data Will Not Be Securely Encrypted
    "<wait10s><tab><spacebar>",
    # Choose Your Look
    "<wait10s><leftShiftOn><tab><leftShiftOff><spacebar>",
    # Update Mac Automatically -> off (updates are an explicit, pinned rebuild)
    "<wait10s><tab><tab><spacebar>",
    # Welcome to Mac
    "<wait30s><spacebar>",
    # Disable VoiceOver
    "<wait10s><leftAltOn><f5><leftAltOff>",
    # Enable full keyboard navigation so System Settings can be driven by keys
    "<wait10s><leftAltOn><spacebar><leftAltOff>Terminal<wait10s><enter>",
    "<wait20s>defaults write NSGlobalDomain AppleKeyboardUIMode -int 3<enter>",
    # Spotlight is unreliable for System Settings on Tahoe; open it directly
    "<wait10s>open '/System/Applications/System Settings.app'<enter>",
    "<wait120s>",
    # General > Sharing
    "<wait10s><leftCtrlOn><f2><leftCtrlOff><right><right><right><down>Sharing<enter>",
    # Screen Sharing on
    "<wait10s><tab><tab><tab><tab><tab><spacebar>",
    "<wait10s>${var.password}<enter>",
    # Remote Login (SSH) on — Packer's communicator needs this
    "<wait10s><tab><tab><tab><tab><tab><tab><tab><tab><tab><tab><tab><tab><spacebar>",
    # Quit System Settings
    "<wait10s><leftAltOn>q<leftAltOff>",
  ]

  # Virtualization.framework's install sometimes needs a moment to settle.
  create_grace_time = "30s"

  # Keep recoveryOS so `softwareupdate` and csrutil remain possible later.
  recovery_partition = "keep"
}

build {
  sources = ["source.tart-cli.vanilla"]

  # Stage 1 stays as close to Apple defaults as possible: no auto-login, no
  # passwordless sudo, screen lock untouched. It is a local build intermediate;
  # all hardening happens in stage 2 so the vanilla VM can be reused.
  provisioner "shell" {
    # Password on stdin for sudo; never written to sudoers.
    execute_command = "chmod +x {{ .Path }}; echo '${var.password}' | sudo -S -p '' /usr/bin/env {{ .Vars }} {{ .Path }}"
    inline = [
      "set -eu",
      "systemsetup -setsleep Off 2>/dev/null || true",
      "csrutil status | grep -q 'status: enabled\\.'",
      "spctl --status | grep -qx 'assessments enabled'",
      "test ! -e /etc/kcpassword",
      "test -z \"$(ls /etc/sudoers.d 2>/dev/null)\"",
      "sw_vers",
    ]
  }
}
