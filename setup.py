"""Legacy setup shim — all metadata lives in pyproject.toml.

Keeping this file as a minimal stub (no duplicated version/deps) so
`pip install -e .` and old `python setup.py` flows keep working without
drifting out of sync. Do NOT re-add install_requires/version here.
"""
from setuptools import setup

setup()
