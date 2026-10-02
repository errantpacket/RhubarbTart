# Stage 2 (macOS): clone the vanilla VM, install the profile's locked packages
# (e.g. Chrome, ZAP, WARP, Tailscale), then harden and seal the image.
#
# Inputs come only from the verified stage dir built by `tools/resolve.py verify`;
# the guest re-verifies hashes and signatures before installing anything.
#
# Packer reaches the guest with password SSH (inherited from stage 1) over Tart's
# host-only NAT. guest/macos/finalize.sh turns password auth off for *new* connections,
# and the guest then powers itself off, so no later step needs to log in again.

packer {
  required_plugins {
    tart = {
      version = "= 1.21.0"
      source  = "github.com/cirruslabs/tart"
    }
  }
}

variable "base_vm" {
  type = string
}

variable "vm_name" {
  type = string
}

variable "stage_dir" {
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

variable "authorized_keys_path" {
  type        = string
  description = "Public keys allowed to SSH into the final image; an empty file disables SSH."
}

variable "ssh_from" {
  type        = string
  description = "authorized_keys from= pattern (Tart's shared-NAT host address); empty for none."
}

# Same rules as stage 1: the password is embedded in single-quoted shell.
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
    condition     = can(regex("^[A-Za-z0-9]{20,64}$", var.password))
    error_message = "The password must be 20-64 letters or digits."
  }
}

source "tart-cli" "apps" {
  vm_base_name   = var.base_vm
  vm_name        = var.vm_name
  cpu_count      = var.cpu_count
  memory_gb      = var.memory_gb
  disk_size_gb   = var.disk_gb
  run_extra_args = ["--no-audio", "--no-clipboard"] # no host microphone or clipboard (#170)
  headless       = true
  ssh_username   = var.username
  ssh_password   = var.password
  ssh_timeout    = "300s"
}

locals {
  # Run provisioner scripts as root with the password on stdin (no NOPASSWD sudoers).
  as_root = "chmod +x {{ .Path }}; echo '${var.password}' | sudo -S -p '' /usr/bin/env {{ .Vars }} {{ .Path }}"
}

build {
  sources = ["source.tart-cli.apps"]

  provisioner "shell" {
    inline = ["rm -rf /tmp/rhubarb && mkdir -p /tmp/rhubarb/guest && chmod 700 /tmp/rhubarb"]
  }

  provisioner "file" {
    source      = "${var.stage_dir}/"
    destination = "/tmp/rhubarb"
  }

  provisioner "file" {
    source      = "${path.root}/../../guest/macos/"
    destination = "/tmp/rhubarb/guest"
  }

  provisioner "file" {
    source      = var.authorized_keys_path
    destination = "/tmp/rhubarb/authorized_keys"
  }

  provisioner "shell" {
    execute_command = local.as_root
    inline          = ["/bin/bash /tmp/rhubarb/guest/install.sh /tmp/rhubarb"]
  }

  provisioner "shell" {
    execute_command   = local.as_root
    environment_vars  = ["RB_USER=${var.username}", "RB_SSH_FROM=${var.ssh_from}"]
    inline            = ["/bin/bash /tmp/rhubarb/guest/finalize.sh /tmp/rhubarb"]
    expect_disconnect = true
  }

  # finalize.sh powers the guest off; wait for Tart to report it stopped so the
  # disk is quiescent before Packer's own cleanup runs.
  provisioner "shell-local" {
    inline = [
      "for i in $(seq 1 60); do tart get '${var.vm_name}' --format json | grep -q '\"Running\" *: *false' && exit 0; sleep 5; done",
      "echo 'guest did not power off within 300s' >&2; exit 1",
    ]
  }
}
