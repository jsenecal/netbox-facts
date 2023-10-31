#!/usr/bin/env python

"""The setup script."""

import codecs
import os.path

from setuptools import find_packages, setup

with open("README.md", encoding="UTF-8") as readme_file:
    readme = readme_file.read()


def read(rel_path):
    """Read the specified file."""
    path = os.path.abspath(os.path.dirname(__file__))
    with codecs.open(os.path.join(path, rel_path), "r") as fp:
        return fp.read()


def get_version(rel_path) -> str | None:
    """Get the version from the specified file."""
    for line in read(rel_path).splitlines():
        if line.startswith("__version__"):
            delim = '"' if '"' in line else "'"
            return line.split(delim)[1]

    raise RuntimeError("Unable to find version string.")


requirements = ["napalm~=4.1.0", "requests~=2.31.0"]

setup(
    author="Jonathan Senecal",
    author_email="contact@jonathansenecal.com",
    python_requires=">=3.9",
    classifiers=[
        "Development Status :: 2 - Pre-Alpha",
        "Intended Audience :: Developers",
        "Natural Language :: English",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
    ],
    description="Gather operational facts from supported NetBox Devices",
    install_requires=requirements,
    long_description=readme,
    include_package_data=True,
    keywords="netbox_facts",
    name="netbox_facts",
    packages=find_packages(include=["netbox_facts", "netbox_facts.*"]),
    test_suite="tests",
    url="https://github.com/jsenecal/netbox-facts",
    version=get_version("netbox_facts/__init__.py") or "unknown",
    zip_safe=False,
)
