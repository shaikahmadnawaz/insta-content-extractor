"""Extractor package facade."""

from . import constants, core, sources, text

for _module in (constants, core, sources, text):
    for _name in dir(_module):
        if _name.startswith("__"):
            continue
        globals()[_name] = getattr(_module, _name)

del _module
del _name
