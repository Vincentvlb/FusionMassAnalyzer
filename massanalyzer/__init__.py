"""FusionMassAnalyzer internals.

`mass_tree` and `heat` are pure Python (no `adsk` import) so they can be unit
tested outside Fusion. `command`, `heatmap` and `palette` depend on the Fusion API.
"""
