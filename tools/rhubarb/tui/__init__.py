"""Textual pane widgets for the read-only RhubarbTart TUI (Phase 0.5, Stage B).

Each pane lives in its own module so pane agents can work without conflict:

    images_pane.ImagesPane          -> api.images()      (list[api.Image])
    clones_pane.ClonesPane          -> api.clones()      (api.CloneList)
    provenance_pane.ProvenancePane  -> api.provenance(v) (api.Provenance)

Pane contract (the shell, tools/rhubarb_tui.py, depends only on this):
  * Each module exposes exactly the widget class named above.
  * Each class subclasses a Textual container and implements:
      - ``compose(self) -> ComposeResult`` — build the pane's widgets.
      - ``refresh_data(self) -> None`` — (re)load from ``rhubarb.api`` and
        re-render. Called by the shell on mount, on the ``r`` binding, and on
        the auto-refresh interval. MUST be read-only: only ``api.images`` /
        ``api.clones`` / ``api.provenance`` — never ``new``/``run``/``enroll``/
        ``reset``/``rm``, and never ``tart`` or the keychain directly.
  * ``ProvenancePane`` additionally exposes ``show(self, vm: str) -> None`` so a
    selection in the clones pane can point it at a VM; ``refresh_data`` re-reads
    whatever VM is currently selected.

Panes import the core API lazily (inside their methods), so importing this
package never requires ``tart`` to be installed — the headless render test and
Linux dev both work with placeholder panes.
"""
