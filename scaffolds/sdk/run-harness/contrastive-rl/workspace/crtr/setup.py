from pathlib import Path
from setuptools import setup, find_packages

setup(
    name="Contrastive Representations for Temporal Reasoning",
    version="0.1",
    packages=find_packages(),
    install_requires=[
        line.strip() for line in Path("requirements.txt").read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ],
)
