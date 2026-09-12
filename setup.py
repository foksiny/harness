from setuptools import setup, find_packages

setup(
    name="harness-cli",
    version="1.0.0",
    packages=find_packages(include=["harness*"]),
    entry_points={
        "console_scripts": [
            "harness=harness.cli:main",
        ],
    },
)
