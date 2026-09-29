# RhubarbTart — notes for agents

Provenance-first, hardened Tart images. Before changing code, load the `rhubarb-dev` skill. For
builds and clones, load `rhubarb-build`.

## Workflow (every change)

- `main` is the only long-lived branch. Never commit to it directly, and never recreate `dev-*` branches.
- Find or file the **issue** first, then branch off current `main` as `<type>/<issue>-<slug>`
  (`fix|feat|docs|chore|validate`).
- `./tools/check.sh` must pass before you commit. Commit as `errantpacket`.
- Open a PR into `main` with `Closes #N` and what you verified (and what only a Mac build can show).
  Keep history linear; the branch is deleted on merge.
- Mac validation evidence goes on the issue as a comment. Handoff/coordination files are temporary
  and never committed.

Details: [CONTRIBUTING.md](CONTRIBUTING.md) → "Workflow".
