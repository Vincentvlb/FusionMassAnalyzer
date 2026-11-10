"""Minimal stand-ins mimicking the shape of the Fusion API objects."""

import itertools

_tokens = itertools.count()


class Collection:
    """Fusion-style collection: .count + .item(i) (no __iter__ on purpose)."""

    def __init__(self, items=None):
        self._items = list(items or [])

    @property
    def count(self):
        return len(self._items)

    def item(self, i):
        return self._items[i]


class Material:
    def __init__(self, name="Steel"):
        self.name = name


class PhysicalProperties:
    def __init__(self, mass):
        self._mass = mass

    @property
    def mass(self):
        if isinstance(self._mass, Exception):
            raise self._mass
        return self._mass


class Body:
    def __init__(self, name, mass, material="Steel", visible=True, solid=True):
        self.name = name
        self._mass = mass
        self.material = Material(material) if material else None
        self.isVisible = visible
        self.isSolid = solid
        self.nativeObject = None
        self.entityToken = "body-%d" % next(_tokens)
        self.reads = 0  # how many times physicalProperties was evaluated

    @property
    def physicalProperties(self):
        self.reads += 1
        return PhysicalProperties(self._mass)


class BodyProxy:
    """Body seen through an occurrence: shares the native body."""

    def __init__(self, native, visible=None):
        self.nativeObject = native
        self.name = native.name
        self.material = native.material
        self.isSolid = native.isSolid
        self.isVisible = native.isVisible if visible is None else visible
        self.entityToken = native.entityToken + "@proxy-%d" % next(_tokens)

    @property
    def physicalProperties(self):
        return self.nativeObject.physicalProperties


class Component:
    def __init__(self, name, bodies=(), occurrences=()):
        self.name = name
        self.id = "comp-" + name
        self.bodies = list(bodies)
        self.occurrences = Collection(occurrences)

    @property
    def bRepBodies(self):
        return Collection(self.bodies)


class Occurrence:
    def __init__(self, component, index=1, visible=True, children=()):
        self.component = component
        self.name = "%s:%d" % (component.name, index)
        self.isVisible = visible
        self._children = list(children)
        self.entityToken = "occ-%d" % next(_tokens)

    @property
    def bRepBodies(self):
        return Collection(BodyProxy(b) for b in self.component.bodies)

    @property
    def childOccurrences(self):
        return Collection(self._children)
