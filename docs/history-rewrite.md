# History rewrite (2026-09-30)

Before the repository went public, its history was rewritten once with `git filter-repo` (#147):

- The first commit was authored under a different name and email. Every commit now uses the
  maintainer's GitHub noreply identity.
- Two screenshots that #144 committed by mistake were removed from every commit.

Nothing else changed: the final file tree, every commit message and every date are the same. But
every commit hash changed, because each hash covers its parent. Issue and PR numbers did not change.

Use this table to translate an old hash, for example one quoted in an issue comment or recorded in a
provenance file (`out/<image>.provenance.json`, `git_commit`), into the commit on `main` today:

```sh
grep <old-hash-prefix> docs/history-rewrite.md
```

A hash that isn't in the table was never on `main`. Images are usually built from a pull request's
branch before it is squash-merged, so their provenance often records a branch commit. GitHub keeps
every pull request's commits (`refs/pull/<n>/head`), and the rewrite didn't touch them, so those
hashes still resolve on GitHub as before.

| Date | Old commit | New commit | Subject |
|---|---|---|---|
| 2026-09-25 | `3f47e19e7beddcd93ede46b809b4a029b8ba5b49` | `231dc63d55a9` | Initial RhubarbTart: provenance-first, hardened Tart images for macOS 26 |
| 2026-09-25 | `6f2f1c8e5314262866cb9527524eb016600a668a` | `a7f9064566d4` | Multi-OS guest profiles: macOS 27, NixOS, Kali; WARP + Tailscale |
| 2026-09-25 | `4acb8eea8d2dc6e90d98083385c74832012e3488` | `0034da918328` | Add rhubarb clone-management CLI, project plan, README polish |
| 2026-09-25 | `bc25b29ffe50b21a2b714fcb48f979d2cbc0caf0` | `eba3beeb1ab8` | Add SECURITY.md and CONTRIBUTING.md |
| 2026-09-26 | `9807bc6c67ff164924fdb31bd9713a190de4ae26` | `cc91893a2104` | nixos: fixes from first end-to-end build on Apple silicon |
| 2026-09-26 | `48c7197081651edb6acb9aded7c539f35efd7a18` | `7c3ce42950f1` | macos: support unsigned (hash-pinned) tools; make custom/tenant installers first-class |
| 2026-09-26 | `9458d19d16024bc8f7fb043383e3bae37ae035d2` | `5bd9123e3580` | macos: tolerate a vendor postinstall's GUI-launch failure (Tailscale) |
| 2026-09-26 | `a870c3103b20d3b2c7e4e0c327899a855e70caeb` | `9221833bb664` | env.sh: resolve the repo root under zsh too |
| 2026-09-26 | `55354a79a5b563f52fde2f08b1ba2d172bbaedbe` | `ccd8fcd3f003` | docs: management-interface strategy in PLAN; refresh known-gaps |
| 2026-09-26 | `5caf8cf22e23250ae5e571d3fdaf2555152d8a1d` | `61e7f326a31d` | docs: implementation charter for the management interface (Phase 0.5) |
| 2026-09-26 | `dcde121cabfa7fb122b1f2640532773f9287a60f` | `7913cbc5c22f` | interface: Stage A — typed control-plane core API (tools/rhubarb/api.py) |
| 2026-09-26 | `642e66ab10f60df17e4ec7bf5dbc62bbe78934a1` | `4986bc82c599` | hostops: survive tart's transient disk-image error; recoverable rm (#16) |
| 2026-09-26 | `63100f465d5e09dc1511a5e2f40ad9a3ef28b874` | `e0469889d063` | hostops: never orphan the detached boot process on new failure (#17) |
| 2026-09-26 | `f9fca2e64467e5aa39117b02c8eb878e9112cf5e` | `7432f8135fca` | interface: Stage B — read-only Textual TUI over the core API (#6-#10) |
| 2026-09-26 | `24584d228234fed2fd2b2f7a3d656c060d8230e0` | `0ff27fe97347` | hostops: rm/teardown never orphans a detached tart run (#18) |
| 2026-09-26 | `70bbc73721f48e76af8965e96fc1af212753cfe9` | `094bb0bbc351` | interface: Stage C — TUI write actions over the core API + live progress (#19) |
| 2026-09-26 | `6d4a5b3777d56bed27a9f06bc4f3d0757c2f243d` | `d2b7bce1078b` | hostops: list VMs even while a clone is running (disk-free fallback) (#21) |
| 2026-09-26 | `46316f2a2db8f0d23e44eeb0a1095d99e2ef18a3` | `cdac80c23f90` | tui: wire row highlight to the provenance pane (was always blank) |
| 2026-09-26 | `f3cf1f7220b85a760ecff14c62b65d73a12f197e` | `4b41689b6d66` | api: retry the rotation boot on an unreachable clone (#23) |
| 2026-09-26 | `7d9251ae272b31b3ab1fb9e1440254923e9feca8` | `e4d42a2a90d9` | tui: input-driven actions new/enroll/build via modals (#20) |
| 2026-09-26 | `9a5313fce041a33be6ca67ccd07fc1f2450b332e` | `a459cb154fd7` | docs: reflect Phase 0.5 delivered; track remaining gaps as issues |
| 2026-09-26 | `b29bdc8760d38ac11a1b18320280d689cf07fc3e` | `da0036893637` | enroll: capture output + report result; require warp --org in the TUI (#35) |
| 2026-09-26 | `1ad902a1a30f9ecdad1d3f14b2100e567d4ef9b9` | `82336a600504` | tui: cut refresh latency — active-pane polling + lazy provenance (#36) |
| 2026-09-26 | `da0bb6dd15192348a89a47c92d36523bcede32d1` | `fa33668c3262` | docs: sharpen the README's "why" and add concrete use cases |
| 2026-09-26 | `86f7a9d4d490fa10b8413496e643e719d3fcc50e` | `3a782968c7c5` | docs: split the wiki-sized README into a lean landing page + docs/ pages |
| 2026-09-26 | `1e58e2e1101de965daeec119a8613b1d6a09a843` | `ee1d5448b08f` | docs: Phase 1 charter — the Engagement object (engagements/ + rhubarb engagement) |
| 2026-09-26 | `372d3298b2e01da12c3c9501ba610f0f43ae977d` | `6bf8606de4a4` | engagement: scope-manifest schema + strict validation (Phase 1 Stage 1A) (#38, #39) |
| 2026-09-26 | `58198df809c080bd824a25911c95b0f8698c8720` | `d96353da295c` | engagement: tagged clones + provision/teardown + rhubarb engagement CLI (Stage 1B) (#41,#42,#43) |
| 2026-09-26 | `adb4829e17ce67b80efb93c186abbe55be15bc60` | `388996002088` | rotation: stream live SSH-wait progress (extends #19) (#45) |
| 2026-09-29 | `79d155b87276123696c230e20cbced3beed3fd4e` | `43bb1ba8a8e3` | fix: detect SSH-disabled images + harden rotation ssh (#46) |
| 2026-09-29 | `f8eea50cff57d114e1320859c845a80d8acbe9f3` | `09edc84f7c66` | docs: document RHUBARB_SSH_IDENTITY + SSH-disabled rotation refusal (#46) |
| 2026-09-29 | `90d56a829c9ce98c7df361c88174403e7844bd2d` | `76cc738131ec` | docs: full accuracy sweep of README/docs/skills vs code |
| 2026-09-29 | `bf5bd742a7368154e48e9bff7016b58bc63e5cbd` | `0adf3cee8251` | fix: rm / engagement teardown refuse cleanly on closed stdin (#47) |
| 2026-09-29 | `573502a156c490f4ff7fbfdce31fa090906ecb57` | `b52f415a01b0` | docs: adopt branch-per-issue workflow on main (#49) |
| 2026-09-29 | `6ef940546c575d5f093dd528a3cdeb3b947c839e` | `9deca6a5ec3f` | fix: Kali build honors RHUBARB_HEADLESS; warn against attaching VNC (#51) |
| 2026-09-29 | `fb39634683885397369ee7ad611e44b52952fca6` | `db716e05440e` | feat: pinned GnuPG in the host toolchain, built from source (#54) |
| 2026-09-29 | `c7543899894870622ef3ea636ae8d5703561bb39` | `0ca63e1ae43d` | fix: Kali profile avoids installer-reserved username; fail fast on it (#25) |
| 2026-09-29 | `74781a7d0b3d4c60492ef3ef4cff3e65114c336c` | `03efb3eb87dc` | locks: re-resolve kali-research for the username change (#25) |
| 2026-09-29 | `fe0b6204ae51820644143e3efb8c0bbc789f26fc` | `ffbf8c6eeba2` | fix: Kali install reads all packages.tsv columns (zaproxy<TAB>1) (#26) |
| 2026-09-29 | `59fb33b5831498ef6340087be14404b84824d8de` | `a1bdc4865e89` | fix: Kali finalize reports sshd evidence when the key-only assertion fails (#57) |
| 2026-09-29 | `20212a8079a86e9e713dcbeb3b660b46cd7cf599` | `2eb032501c5d` | fix: seal compares sshd -T keywords case-insensitively (OpenSSH 10.5) (#57) |
| 2026-09-29 | `217651baaacb2d932259fe5bb410b510f0f86e6f` | `684eaddbe7c0` | fix: Kali purges kali-grant-root; seal allows one scoped OpenVAS rule (#59) |
| 2026-09-29 | `4b486fedac0f149980c181c288b2e046cf50a92b` | `7653962cbc21` | fix: purge kali-grant-root together with the desktop metapackages (apt 3) (#59) |
| 2026-09-29 | `347a6047c35a89a5636aaed4c469737da7d9ce91` | `dbe48f614f63` | fix: remove Kali's Protected desktop metapackages only via a verified plan (#59) |
| 2026-09-29 | `f550a53301b79192c87e79f83fa6781fcc186f0e` | `495a1bcd360a` | fix: build.sh cleanup can't abort before removing $WORK (#61) |
| 2026-09-29 | `d942c286302c837a8165d3433cbb96977c5e4ace` | `a647e08a8f6f` | locks: first tahoe-research lock, macOS 26.6.2 (25G83) (#24) |
| 2026-09-29 | `cf433fc59bb9d41e172fbea2a24517c2c3489374` | `8d7ef8c9fe7f` | test: no-lock check uses a throwaway profile, not tahoe-research (#65) |
| 2026-09-29 | `eecdb4a9a7287981f04409b294a0f250ceefd8ff` | `91de64f0cb23` | fix: content-addressed artifact cache (artifacts/<sha256>/<file>) (#67) |
| 2026-09-29 | `7a0af4889674339222a181851aa03ecc3eb866a3` | `4f2fbc161fcd` | locks: re-resolve goldengate-research: macOS 27.0.1, Chrome .93 (#67) |
| 2026-09-29 | `611f212adf1411f70755022e54a3dcceb4014dc6` | `69488d4db84c` | feat: Chrome auto-updates off (manual only) in macOS images (#29) |
| 2026-09-29 | `834ca7508d0d980e853f7994c6272c27559ba20e` | `21bba31c43ca` | docs: drop the dangling Perimeter 81 Linux variant (#27) reference (#70) |
| 2026-09-29 | `50702073bd38062f5623f36aff582e17dd8190cd` | `0adee03a0829` | feat: publish signed images to a localhost registry, offline cosign (#32) |
| 2026-09-29 | `782df2ed4f2e7f0b6e3719dff2820409a73bdb50` | `a6e552a7f51b` | feat: rhubarb new --from-registry: verified, stacked macOS clones (#31) |
| 2026-09-29 | `890529e78e21da11eb79ddc6cf1c10cc7a226266` | `1d8e39adab2f` | docs: README rewrite for reading flow; concepts + publishing pages (#75) |
| 2026-09-29 | `f815c6cfbc25297d070c6b63b4aeb5dcc3628bf6` | `02eee80d31e5` | chore: remove an unused VPN/ZTNA agent package and its enroll path |
| 2026-09-29 | `39ad5fbb125f83bb5ef244307d7c4cbd3fc53702` | `4630bb0d5dfc` | docs: README copy edit for plain, precise wording (#77) |
| 2026-09-29 | `ec2aeeffa946669e5312c43a9ab2cfab0acde73f` | `248f452a6eb2` | docs: more detail in the README introduction (#79) |
| 2026-09-29 | `1cf096aab85f756fbe05e657493cf03da315adad` | `a0fc193563ef` | docs: README introduction explains the use cases (#81) |
| 2026-09-29 | `12d930d163c969531c6640f6fcfb3c0748153860` | `7234cb0d2cae` | feat: juiceshop-target profile, OWASP Juice Shop as a NixOS lab target (#83) |
| 2026-09-29 | `7146f0d6078340f2c39474ce28deac1e455b231d` | `ff8cf36bb4f9` | fix(nixos): declare the Rosetta mount only when Rosetta is enabled (#83) |
| 2026-09-29 | `3abac87c0e579312157b71799a18c8224e5e2996` | `bb11f2cf00a5` | fix(nixos): make the whole Rosetta mount entry conditional, in place (#83) |
| 2026-09-29 | `cadfb7bf2715712dad12d753fa33ae121a52fe76` | `30d9ff1eca17` | fix(juice-shop): tolerate the not-yet-created working dir in ExecStartPre (#83) |
| 2026-09-29 | `ff67af03993d4135317e7cd158e643b77f1959ed` | `b8b67461568b` | fix: tolerate a transient early-boot key refusal; reset keeps engagement (#88, #89) |
| 2026-09-29 | `b6ba81b152ed572f945d02fa6b895407a85fdb38` | `5a1ca5bd9de5` | fix: flush Linux guests before stopping them (tart stop cuts shutdown short) (#91) |
| 2026-09-29 | `9822044fdb5632b95898e48e665d50674d3afd44` | `ea8d14cc1170` | feat: juiceshop-lab engagement, Kali attacker + Juice Shop target (#84) |
| 2026-09-29 | `f56383847615b975e31f9dbc0279409a219468c7` | `ea48deb7c916` | feat(engagements): explicit links between clones; lab target has no egress (#30) (#95) |
| 2026-09-29 | `bb5c7eef00f563740a5744d812c51459a9948b01` | `fb82322fc224` | feat(evidence): host-side evidence capture for engagements (#85) (#99) |
| 2026-09-29 | `d5a6a77c2a0dfb0b2cdb4f0cc152408311cb0b49` | `9b198caa7e02` | feat(vault): signed, sealed, portable evidence vaults (#86) (#101) |
| 2026-09-29 | `3351d66eacdcb96058a28659e060d20a67975542` | `f22643c80701` | docs: herdr charter — the boundary before the code (#33) (#102) |
| 2026-09-29 | `4136ed1b86394fa7646b889a7aa55062f944325a` | `2847c9657037` | feat(service): read-only control-plane service over a Unix socket (#104) (#105) |
| 2026-09-29 | `22ae7031b4377f6361f5289ebe7db812071bdb90` | `9fbad354da7e` | feat(service): guarded actions over the control-plane socket (#104) (#106) |
| 2026-09-29 | `47663818832e47cdbb7df79834ae9a97ac0e2842` | `cc8eb1fdb012` | feat(service): evidence event stream over the control-plane socket (#104) (#107) |
| 2026-09-29 | `6343937956f566eadf27d4943619a51782c9b9ac` | `9371e5a8aa76` | feat(herdr): scoped range client — the agent's only door to its range (#108) (#109) |
| 2026-09-29 | `0560ff5ab045a2a5a18f5b66ce0bf87c46fc6f59` | `3b6b887b9a06` | feat(herdr): arm — launch an engagement's agents under herdr (#108) (#110) |
| 2026-09-30 | `03d4c4e324490367d8210934d22c18e3b44d5196` | `d05c6f31683b` | feat(herdr): tiered-action approvals, ledgered in evidence (#108) (#111) |
| 2026-09-30 | `e67b45cf085cd5cdd6eb209bdc5a559140d59385` | `ccfddd79c7a6` | fix(engagements): don't treat <id>.herdr.json as an engagement (#108) (#113) |
| 2026-09-30 | `b005f35a1aec878aa8795c834337e2bc4552dfd7` | `e49dbe88e657` | docs: macOS guests and containers (VM vs container, OCI packaging, clones, nested virt) (#116) |
| 2026-09-30 | `84015d982e6a2a2b35240181d16f43143a8bea21` | `ec5cc7941c36` | docs: challenges of testing macOS/iOS apps and how the tool addresses them (#118) |
| 2026-09-30 | `6df4e71621a8a82a914ebd3b4fb28f1a89fc99e7` | `f4c78c6b9732` | chore(demo): local presenterm capability demo (gitignored) + per-agent model in herdr config (#114) |
| 2026-09-30 | `eec8c2f99030495541b3fa283bd0d9d636b6e287` | `6753fc59ae03` | Add LICENSE (FSL-1.1-ALv2) and third-party notices (#34) (#119) |
| 2026-09-30 | `3d96438f61d1dfe9d155950f379bf6e87d1511dc` | `888a8d0e9323` | feat(tui): Logs tab, off-thread polling, keep selection, herdr-friendly mouse (#120) (#121) |
| 2026-09-30 | `7cd1dfd642a438919474348a128e9ff3e589c000` | `18b8a363aa37` | feat(tui): no freezes, contextual keys, log follow, modular shell (#122) (#123) |
| 2026-09-30 | `fa0baca6ccdcb369b2848e4eba8017fc906c278a` | `a999aa4de3b3` | docs: accuracy and style pass over README, docs and skills (#124) (#125) |
| 2026-09-30 | `ce1baaa14e1bdda684fe96675e1eeb63423fc4a6` | `7bf97e779198` | chore: repo structure: guest-side files under guest/, docs index, check.sh hardening, enroll fix (#127) (#129) |
| 2026-09-30 | `7282dbf1a498a70584e24b9030e94e27236ec69c` | `034525064384` | refactor: split api.py and test_rhubarb.py by domain, api stays the one import surface (#128) (#130) |
| 2026-09-30 | `dd581893ba8489e0a8f364079b8cffcb63edfea9` | `e2b284b986a9` | feat(tui): follow herdr's colour theme when launched inside herdr (#131) (#132) |
| 2026-09-30 | `5fbcc1306bc17e453ace0462f560b6166f598f7b` | `a9638e8106e0` | fix: second-boot smoke checks for Linux images; Juice Shop survives an interrupted start (#98) (#133) |
| 2026-09-30 | `e56a18733b0175d0ff632634cc6422874cc4d4ec` | `7aa659564a6d` | docs(readme): new screenshot under the badges; link to the docs index and issue tracker (#134) (#135) |
| 2026-09-30 | `7d9ca0f977bc744b30a1c93673deb72de1a30b28` | `7c2e6c5dad4b` | ci: run check.sh in GitHub Actions on every PR and push to main (#137) (#141) |
| 2026-09-30 | `596e2957dc2173a0566e80b17a91e74f618bc2b3` | `7ff6013ff621` | docs: security contact; restore the README title emoji (#136) (#140) |
| 2026-09-30 | `3997ef29578ee80d63efbdda5bb39362405ee87c` | `67da3624dc67` | feat: version the repository (0.1.0): --version, /health, provenance, CHANGELOG (#138) (#142) |
| 2026-09-30 | `eb1e30775fd4b412962a2b66211fe5281a5333ff` | `0d97ea6adbbc` | feat!: rename the commands to rhubarbtart and rhubarbtart-tui; 🍎 TUI title (#143) (#144) |
| 2026-09-30 | `555fb1118a4350c6b27b077c7c537c504bd851c4` | `62aa5e78883e` | docs: README review, vault in Why, writing rules in the dev skill, new screenshots (#145) (#146) |
