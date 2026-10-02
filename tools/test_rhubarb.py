"""Offline self-tests for the security-relevant parts of tools/rhubarb (run by tools/check.sh).

Each expected value was produced by the reference implementation, not by this code:
  - Ed25519: RFC 8032 §7.1 test vectors 1 and 2
  - NAR: `nix hash path` (Nix 2.28.4) on the exact tree built by _nar_tree()
  - dpkg ordering: deb-version(7) semantics as implemented by dpkg --compare-versions

The tests live in tools/tests/, one module per area (#128); this runner keeps the one entry point
(`uv run tools/test_rhubarb.py`, run by check.sh) and runs them in the same order as before.
"""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from tests.support import FAILS  # noqa: E402
from tests.test_verification import test_content_addressed_cache, test_dpkg, test_ed25519, test_github_release_resolver, test_nar, test_pgp_ed25519, test_toolchain_gpg  # noqa: E402
from tests.test_build import test_build_cleanup_trap, test_chrome_update_policy, test_guest_sync, test_kali_nopasswd_allowlist, test_packages_tsv_readers, test_profile_usernames, test_publish_offline_signing, test_rotation_script, test_sshd_T_normalization  # noqa: E402
from tests.test_clones import test_api_pure, test_cli_lifecycle, test_cli_progress_stream, test_confirm_prompt, test_fs_vms, test_hostops_resilience, test_reap_run, test_records, test_reset_keeps_engagement, test_rosetta_check, test_shutdown_reaps_boot_process, test_ssh_client, test_ssh_provenance, test_stacked_clones, test_version, test_vm_start_failure  # noqa: E402
from tests.test_engagements import test_engagement_links, test_engagement_ops, test_engagements  # noqa: E402
from tests.test_evidence import test_evidence_exec_collect, test_evidence_store, test_vault_seal_verify  # noqa: E402
from tests.test_control_plane import test_control_plane_service, test_herdr_arm, test_range_client_waits_for_approval, test_scoped_range_client, test_tiered_approvals  # noqa: E402
from tests.test_logs import test_logs_api  # noqa: E402

TESTS = (
    test_ed25519,
    test_nar,
    test_dpkg,
    test_records,
    test_cli_lifecycle,
    test_rotation_script,
    test_api_pure,
    test_engagements,
    test_engagement_ops,
    test_engagement_links,
    test_cli_progress_stream,
    test_hostops_resilience,
    test_shutdown_reaps_boot_process,
    test_reap_run,
    test_fs_vms,
    test_ssh_client,
    test_ssh_provenance,
    test_confirm_prompt,
    test_pgp_ed25519,
    test_toolchain_gpg,
    test_profile_usernames,
    test_packages_tsv_readers,
    test_sshd_T_normalization,
    test_kali_nopasswd_allowlist,
    test_build_cleanup_trap,
    test_content_addressed_cache,
    test_chrome_update_policy,
    test_publish_offline_signing,
    test_stacked_clones,
    test_github_release_resolver,
    test_reset_keeps_engagement,
    test_guest_sync,
    test_evidence_store,
    test_evidence_exec_collect,
    test_vault_seal_verify,
    test_control_plane_service,
    test_scoped_range_client,
    test_herdr_arm,
    test_tiered_approvals,
    test_range_client_waits_for_approval,
    test_logs_api,
    test_version,
    test_rosetta_check,
    test_vm_start_failure,
)


if __name__ == "__main__":
    for t in TESTS:
        print(t.__name__)
        # Each test gets a throwaway state dir, so nothing (records, evidence) can reach the
        # operator's real one; tests that manage RHUBARB_STATE_DIR themselves still may.
        with tempfile.TemporaryDirectory() as state:
            os.environ["RHUBARB_STATE_DIR"] = state
            try:
                t()
            finally:
                os.environ.pop("RHUBARB_STATE_DIR", None)
    if FAILS:
        sys.exit(f"{len(FAILS)} test(s) failed")
    print("all rhubarb self-tests passed")
