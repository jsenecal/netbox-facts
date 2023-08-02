#!/usr/bin/env python

"""The setup script."""

from setuptools import setup, find_packages

with open('README.md') as readme_file:
    readme = readme_file.read()

requirements = []

setup(
    author="Jonathan Senecal",
    author_email='contact@jonathansenecal.com',
    python_requires='>=3.9',
    classifiers=[
        'Development Status :: 2 - Pre-Alpha',
        'Intended Audience :: Developers',
        'Natural Language :: English',
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.9',
        'Programming Language :: Python :: 3.10',
    ],
    description="Gather operational facts from supported NetBox Devices",
    install_requires=requirements,
    long_description=readme,
    include_package_data=True,
    keywords='netbox_facts_plugin',
    name='netbox_facts_plugin',
    packages=find_packages(include=['netbox_facts', 'netbox_facts.*']),
    test_suite='tests',
    url='https://github.com/jsenecal/netbox-facts',
    version='0.0.1',
    zip_safe=False,
)
