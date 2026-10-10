"""IBR-FaultScreen: fault-scenario screening for grids with inverter-based resources."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("ibr-faultscreen")
except PackageNotFoundError:  # running from source without installing
    __version__ = "0.0.0"
