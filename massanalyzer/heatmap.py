"""Fusion side of the mass heatmap: create MassHeat_ appearances, apply them,
save the original appearances and restore them exactly."""

import adsk.core
import adsk.fusion

from . import heat

# Id of the "Fusion Appearance Library" (stable, unlike its localized name)
FUSION_APPEARANCE_LIBRARY_ID = "BA5EE55E-9982-449B-9D66-9F036540E140"
CANDIDATE_BASE_NAMES = (
    "Plastic - Matte (White)",
    "Plastic - Glossy (White)",
    "Paint - Enamel Glossy (White)",
    "Plastic - Matte (Red)",
)
COLOR_PROPERTY_IDS = ("opaque_albedo", "generic_diffuse", "metal_f0",
                      "layered_diffuse", "wallpaint_color")
_SCAN_LIMIT = 400

_OVERRIDE = getattr(adsk.core.AppearanceSourceTypes, "OverrideAppearanceSource", None)


def _safe(fn, *args):
    """Some Fusion lookups (e.g. a library's appearances.itemByName) raise
    'invalid name' instead of returning None when nothing matches."""
    try:
        return fn(*args)
    except Exception:
        return None


def _color_property(appearance):
    props = _safe(lambda: appearance.appearanceProperties)
    if not props:
        return None
    for pid in COLOR_PROPERTY_IDS:
        prop = adsk.core.ColorProperty.cast(_safe(props.itemById, pid))
        if prop:
            return prop
    return None


def _token(entity):
    # Proxies of one native entity can share a single appearance; saving each
    # proxy is still safe because restore() replays the saves in reverse order.
    try:
        return entity.entityToken
    except Exception:
        return id(entity)


class Heatmap:
    def __init__(self):
        # token -> (entity, kind, had_override, original_appearance)
        self._saved = {}
        self._order = []
        self.design = None

    @property
    def active(self):
        return bool(self._saved)

    # ------------------------------------------------------------ appearances
    def _base_candidates(self, app, design):
        """Appearances that can be copied, best first: Fusion appearance
        library (known names, then a scan), then appearances already in the design."""
        libs = app.materialLibraries
        ordered = []
        lib = _safe(libs.itemById, FUSION_APPEARANCE_LIBRARY_ID)
        if lib:
            ordered.append(lib)
        for i in range(libs.count):
            other = libs.item(i)
            if not lib or other.id != lib.id:
                ordered.append(other)
        for lib in ordered:
            for name in CANDIDATE_BASE_NAMES:
                a = _safe(lib.appearances.itemByName, name)
                if a and _color_property(a):
                    yield a
        for lib in ordered:
            apps = _safe(lambda: lib.appearances)
            if not apps:
                continue
            for i in range(min(apps.count, _SCAN_LIMIT)):
                a = _safe(apps.item, i)
                if a and _color_property(a):
                    yield a
        apps = design.appearances
        for i in range(apps.count):
            a = apps.item(i)
            if not a.name.startswith(heat.APPEARANCE_PREFIX) and _color_property(a):
                yield a

    def _create(self, app, design, name):
        errors = []
        for tries, base in enumerate(self._base_candidates(app, design)):
            if tries >= 25:
                break
            try:
                created = design.appearances.addByCopy(base, name)
            except Exception as exc:  # e.g. library appearance not downloaded
                errors.append("%s: %s" % (_safe(lambda: base.name), exc))
                continue
            if created and _color_property(created):
                return created
        raise RuntimeError("Impossible de créer l'apparence %s (%s)" % (
            name, "; ".join(errors[-3:]) or "aucune apparence colorable trouvée"))

    def ensure_appearances(self, design, buckets=heat.BUCKETS):
        app = adsk.core.Application.get()
        out = []
        for i in range(buckets):
            name = heat.appearance_name(i)
            appearance = _safe(design.appearances.itemByName, name)
            if not appearance:
                appearance = self._create(app, design, name)
            prop = _color_property(appearance)
            if prop:
                r, g, b = heat.bucket_color(i, buckets)
                prop.value = adsk.core.Color.create(r, g, b, 255)
            out.append(appearance)
        return out

    # ------------------------------------------------------------ save/apply
    def _save(self, entity, kind):
        key = (kind, _token(entity))
        if key in self._saved:
            return
        if kind == "occurrence":
            # Occurrence.appearance is None when there is no override
            original = entity.appearance
            had_override = original is not None
        else:
            original = entity.appearance
            try:
                had_override = entity.appearanceSourceType == _OVERRIDE
            except Exception:
                had_override = True
        self._saved[key] = (entity, kind, had_override, original)
        self._order.append(key)

    def apply(self, design, result, log_scale=False):
        if self.active:
            self.restore()
        self.design = design
        appearances = self.ensure_appearances(design)
        assigned = heat.assign_buckets(heat.heat_targets(result), log_scale=log_scale)
        colored = 0
        for kind, node, bucket in assigned:
            entity = result.entities.get(node.id)
            if entity is None or not entity.isValid:
                continue
            target = appearances[bucket]
            if kind == heat.TARGET_OCCURRENCE:
                self._save(entity, "occurrence")
                entity.appearance = target
                # body-level overrides would hide the occurrence color
                for body in _iter(entity.bRepBodies):
                    if _has_override(body):
                        self._save(body, "body")
                        body.appearance = target
            else:
                self._save(entity, "body")
                entity.appearance = target
            colored += 1
        adsk.core.Application.get().activeViewport.refresh()
        return colored

    def restore(self):
        """Put back exactly the saved appearances (reverse order)."""
        restored = 0
        for key in reversed(self._order):
            entity, kind, had_override, original = self._saved[key]
            try:
                if not entity.isValid:
                    continue
                entity.appearance = original if had_override else None
                restored += 1
            except Exception:
                pass
        self._saved.clear()
        self._order.clear()
        if self.design and self.design.isValid:
            self.cleanup(self.design)
        adsk.core.Application.get().activeViewport.refresh()
        return restored

    def strip_leftovers(self, design):
        """Fallback when no saved state exists (e.g. after restarting Fusion):
        remove every override that still points to a MassHeat_ appearance."""
        removed = 0
        root = design.rootComponent
        entities = [root.allOccurrences.item(i) for i in range(root.allOccurrences.count)]
        for comp in [root] + [o.component for o in entities]:
            entities.extend(_iter(comp.bRepBodies))
        for e in entities:
            try:
                a = e.appearance
                if a and a.name.startswith(heat.APPEARANCE_PREFIX):
                    e.appearance = None
                    removed += 1
            except Exception:
                pass
        self.cleanup(design)
        adsk.core.Application.get().activeViewport.refresh()
        return removed

    @staticmethod
    def cleanup(design):
        """Delete unused MassHeat_ appearances from the design."""
        apps = design.appearances
        for i in range(apps.count - 1, -1, -1):
            a = apps.item(i)
            if a.name.startswith(heat.APPEARANCE_PREFIX) and not a.isUsed:
                try:
                    a.deleteMe()
                except Exception:
                    pass


def _iter(coll):
    return [coll.item(i) for i in range(coll.count)]


def _has_override(body):
    try:
        return body.appearanceSourceType == _OVERRIDE
    except Exception:
        return False
