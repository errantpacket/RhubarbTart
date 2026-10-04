# Stage 1 for macOS 27+: vanilla macOS from a locally verified Apple IPSW, with the user
# account created by Virtualization.framework's guest provisioning API (Tart 2.33+,
# macOS 27 host AND guest) instead of Setup Assistant keystrokes.
#
# Tart only accepts the provisioning password on its command line (host process list),
# so the account is provisioned with a random *bootstrap* password that is rotated to
# the real, keychain-held password over SSH and then proven dead. Only a dead password
# is ever exposed. SIP and Gatekeeper stay enabled; no Screen Sharing is turned on.

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

# No defaults: scripts/build.sh supplies these via PKR_VAR_* env vars.
variable "username" {
  type = string
  validation {
    condition     = can(regex("^[a-z][a-z0-9]{2,15}$", var.username))
    error_message = "The username must be 3-16 lowercase letters/digits, starting with a letter."
  }
}

variable "bootstrap_password" {
  type      = string
  sensitive = true
  validation {
    condition     = can(regex("^[A-Za-z0-9]{20,64}$", var.bootstrap_password))
    error_message = "The bootstrap password must be 20-64 letters or digits."
  }
}

variable "password_file" {
  type        = string
  description = "Host path (0600) of the final password; uploaded, applied, shredded."
}

source "tart-cli" "vanilla" {
  from_ipsw    = var.ipsw_path
  vm_name      = var.vm_name
  cpu_count    = var.cpu_count
  memory_gb    = var.memory_gb
  disk_size_gb = var.disk_gb
  disk_format  = "asif"
  ssh_username = var.username
  ssh_password = var.bootstrap_password
  ssh_timeout  = "900s"
  # No boot_command: the provisioning API performs Setup Assistant on first boot.
  run_extra_args = [
    "--no-audio", "--no-clipboard", # no host microphone or clipboard (#170)
    "--provisioning-opts=${join(",", [
      "fullName=RhubarbTart",
      "username=${var.username}",
      "password=${var.bootstrap_password}",
      "logsInAutomatically=false",
      "enablesRemoteLogin=true",
    ])}",
  ]

  # A fixed pause after `tart create` returns, before the first boot. The plugin documents it as
  # a workaround for Virtualization.framework's installation still running in the background for
  # a while after `tart create` finishes. There is no condition to poll for instead; upstream
  # (cirruslabs/macos-image-templates) uses the same 30s. The only fixed wait in this path.
  create_grace_time  = "30s"
  recovery_partition = "keep"
}

build {
  sources = ["source.tart-cli.vanilla"]

  provisioner "file" {
    source      = var.password_file
    destination = "/tmp/rhubarb-password"
  }

  # Rotate bootstrap -> final password. dscl -passwd with the old password keeps the
  # account's SecureToken in sync. Runs as root via sudo -S (no NOPASSWD, ever).
  provisioner "shell" {
    execute_command  = "chmod +x {{ .Path }}; echo '${var.bootstrap_password}' | sudo -S -p '' /usr/bin/env {{ .Vars }} {{ .Path }}"
    environment_vars = ["RB_USER=${var.username}", "RB_BOOT=${var.bootstrap_password}"]
    inline = [
      "set -eu",
      "chmod 600 /tmp/rhubarb-password",
      "dscl . -passwd \"/Users/$RB_USER\" \"$RB_BOOT\" \"$(cat /tmp/rhubarb-password)\"",
      "dscl . -authonly \"$RB_USER\" \"$(cat /tmp/rhubarb-password)\"",
      "if dscl . -authonly \"$RB_USER\" \"$RB_BOOT\" 2>/dev/null; then echo 'bootstrap password still valid' >&2; exit 1; fi",
      "echo 'password rotated; bootstrap password rejected'",
    ]
  }

  provisioner "shell" {
    inline = [
      "set -eu",
      "csrutil status | grep -q 'status: enabled\\.'",
      "spctl --status | grep -qx 'assessments enabled'",
      "test ! -e /etc/kcpassword",
      "if defaults read /Library/Preferences/com.apple.loginwindow autoLoginUser >/dev/null 2>&1; then echo 'auto-login set' >&2; exit 1; fi",
      "test -z \"$(ls -A /etc/sudoers.d 2>/dev/null)\"",
      "sw_vers",
    ]
  }

  # The plugin's own graceful shutdown would `sudo -S` with the (now dead) bootstrap
  # password, so power off here: sudo reads the final password from the staged file,
  # which is shredded as root before the delayed shutdown.
  provisioner "shell" {
    inline = [
      "sudo -S -p '' sh -c 'rm -P /tmp/rhubarb-password; (sleep 3; shutdown -h now) >/dev/null 2>&1 &' < /tmp/rhubarb-password",
    ]
    expect_disconnect = true
  }

  provisioner "shell-local" {
    inline = [
      "for i in $(seq 1 60); do tart get '${var.vm_name}' --format json | grep -q '\"Running\" *: *false' && exit 0; sleep 5; done",
      "echo 'guest did not power off within 300s' >&2; exit 1",
    ]
  }
}
