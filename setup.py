from setuptools import setup, find_packages

setup(
    name="harness-cli",
    version="0.10.0",
    packages=find_packages(include=["harness*"]),
    entry_points={
        "console_scripts": [
            "harness=harness.cli:main",
        ],
    },
)
