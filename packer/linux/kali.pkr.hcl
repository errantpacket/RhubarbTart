# Kali (arm64) from the official installer ISO, verified against Kali's GPG-signed
# SHA256SUMS by `resolve.py`. Unattended debian-installer via preseed, then the
# profile's pinned packages, hardening and sealing (guest/kali/*.sh).
#
# The preseed is served by tools/serve_preseed.py on the vmnet host address only
# (see there for why Packer's own HTTP server can't be used before the guest has an IP).

packer {
  required_plugins {
    tart = {
      version = "= 1.21.0"
      source  = "github.com/cirruslabs/tart"
    }
  }
}

variable "iso_path" {
  type = string
}

variable "vm_name" {
  type = string
}

variable "stage_dir" {
  type = string
}

variable "preseed_url" {
  type = string
}

variable "authorized_keys_path" {
  type = string
}

variable "password_file" {
  type = string
}

variable "ssh_from" {
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

source "tart-cli" "kali" {
  from_iso      = [var.iso_path]
  vm_name       = var.vm_name
  cpu_count     = var.cpu_count
  memory_gb     = var.memory_gb
  disk_size_gb  = var.disk_gb
  headless      = true
  ssh_username  = var.username
  ssh_password  = var.bootstrap_password
  ssh_timeout   = "120m" # the whole unattended install happens before SSH is up
  ip_extra_args = ["--resolver", "arp"]

  # GRUB command line: kernel + initrd from the ISO's install.a64/, preseed by URL.
  boot_command = [
    "<wait10s>c<wait3s>",
    "linux /install.a64/vmlinuz auto=true priority=critical url=${var.preseed_url} hostname=rhubarb domain=local --- quiet<enter><wait2s>",
    "initrd /install.a64/initrd.gz<enter><wait2s>",
    "boot<enter>",
  ]
}

locals {
  # Root with the bootstrap password on stdin; finalize.sh rotates it at the very end.
  as_root = "chmod +x {{ .Path }}; echo '${var.bootstrap_password}' | sudo -S -p '' /usr/bin/env {{ .Vars }} {{ .Path }}"
}

build {
  sources = ["source.tart-cli.kali"]

  provisioner "shell" {
    inline = ["rm -rf /tmp/rhubarb && mkdir -p /tmp/rhubarb/guest && chmod 700 /tmp/rhubarb"]
  }

  provisioner "file" {
    source      = "${var.stage_dir}/"
    destination = "/tmp/rhubarb"
  }

  provisioner "file" {
    source      = "${path.root}/../../guest/kali/"
    destination = "/tmp/rhubarb/guest"
  }

  provisioner "file" {
    source      = var.authorized_keys_path
    destination = "/tmp/rhubarb/authorized_keys"
  }

  provisioner "file" {
    source      = var.password_file
    destination = "/tmp/rhubarb/password"
  }

  provisioner "shell" {
    execute_command = local.as_root
    inline          = ["chmod 600 /tmp/rhubarb/password", "/bin/bash /tmp/rhubarb/guest/install.sh /tmp/rhubarb"]
  }

  provisioner "shell" {
    execute_command   = local.as_root
    environment_vars  = ["RB_USER=${var.username}", "RB_SSH_FROM=${var.ssh_from}"]
    inline            = ["/bin/bash /tmp/rhubarb/guest/finalize.sh /tmp/rhubarb"]
    expect_disconnect = true
  }

  provisioner "shell-local" {
    inline = [
      "for i in $(seq 1 60); do tart get '${var.vm_name}' --format json | grep -q '\"Running\" *: *false' && exit 0; sleep 5; done",
      "echo 'guest did not power off within 300s' >&2; exit 1",
    ]
  }
}
