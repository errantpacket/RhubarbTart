"""RhubarbTart's Python package: input resolution and verification, the typed core API
(``api``), the ``rhubarb`` CLI, the control-plane service, herdr integration and the TUI.
Stdlib only, except the TUI's pinned Textual. Entry points live in ``tools/`` (#138)."""

# The repository's version (SemVer 0.x). It covers the CLI, the profile, engagement and lock
# file formats, and provenance records; not the internals of api.py. Bump it here and add the
# matching section to CHANGELOG.md (check.sh checks both agree). See CONTRIBUTING.md, Releases.
__version__ = "0.1.0"
