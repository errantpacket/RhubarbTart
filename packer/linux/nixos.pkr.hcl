# NixOS (aarch64) from the official minimal installer ISO (sha256 from releases.nixos.org),
# installed from the profile's configuration against nixpkgs pinned by (rev, NAR hash).
#
# The live ISO auto-logs in as `nixos` (passwordless sudo, live environment only). The
# boot_command sets a random throwaway password for that live user and starts sshd so
# Packer can connect; nothing from the live session reaches the installed system.

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
  default = 60
}

variable "bootstrap_password" {
  type      = string
  sensitive = true
  validation {
    condition     = can(regex("^[A-Za-z0-9]{20,64}$", var.bootstrap_password))
    error_message = "The bootstrap password must be 20-64 letters or digits."
  }
}

source "tart-cli" "nixos" {
  from_iso      = [var.iso_path]
  vm_name       = var.vm_name
  cpu_count     = var.cpu_count
  memory_gb     = var.memory_gb
  disk_size_gb  = var.disk_gb
  headless      = true
  ssh_username  = "nixos"
  ssh_password  = var.bootstrap_password
  ssh_timeout   = "15m"
  ip_extra_args = ["--resolver", "arp"]

  # Default boot entry, then the auto-logged-in live console.
  boot_command = [
    "<wait60s>",
    "echo 'nixos:${var.bootstrap_password}' | sudo chpasswd && sudo systemctl start sshd<enter>",
  ]
}

build {
  sources = ["source.tart-cli.nixos"]

  provisioner "shell" {
    inline = ["rm -rf /tmp/rhubarb && mkdir -p /tmp/rhubarb/nix && chmod 700 /tmp/rhubarb"]
  }

  provisioner "file" {
    source      = "${var.stage_dir}/"
    destination = "/tmp/rhubarb"
  }

  provisioner "file" {
    source      = "${path.root}/../../nix/"
    destination = "/tmp/rhubarb/nix"
  }

  provisioner "file" {
    source      = "${path.root}/../../guest/nixos/"
    destination = "/tmp/rhubarb"
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
    environment_vars = ["RB_SSH_FROM=${var.ssh_from}"]
    inline           = ["chmod 600 /tmp/rhubarb/password", "sudo -E bash /tmp/rhubarb/install.sh /tmp/rhubarb"]
  }

  provisioner "shell" {
    inline            = ["sudo bash /tmp/rhubarb/finalize.sh"]
    expect_disconnect = true
  }

  provisioner "shell-local" {
    inline = [
      "for i in $(seq 1 60); do tart get '${var.vm_name}' --format json | grep -q '\"Running\" *: *false' && exit 0; sleep 5; done",
      "echo 'guest did not power off within 300s' >&2; exit 1",
    ]
  }
}
