#!/bin/bash

set -e
set -x

cd "$(dirname "$0")/.."
pwd

# Create config dir if not present
if [[ ! -d "${PWD}/config" ]]; then
    mkdir -p "${PWD}/config"
    # Add defaults configuration
    hass --config "${PWD}/config" --script ensure_config
fi

# Overwrite configuration.yaml if provided
if [ -f ${PWD}/.devcontainer/configuration.yaml ]; then
    rm -f ${PWD}/config/configuration.yaml
    ln -s ${PWD}/.devcontainer/configuration.yaml ${PWD}/config/configuration.yaml
fi

# Dev-only custom_components (climate_template)
if [ ! -d ${PWD}/config/custom_components ]; then
    mkdir -p ${PWD}/config/custom_components
fi

if [ ! -e ${PWD}/config/custom_components/climate_template ]; then
	rm -f ${PWD}/config/custom_components/climate_template
    ln -s ${PWD}/.devcontainer/climate_template \
          ${PWD}/config/custom_components/climate_template
fi

# Dev-only python_scripts
if [ ! -d ${PWD}/config/python_scripts ]; then
    mkdir -p ${PWD}/config/python_scripts
fi

if [ ! -e ${PWD}/config/python_scripts/set_vtherm_attr.py ]; then
	rm -f ${PWD}/config/python_scripts/set_vtherm_attr.py
    ln -s ${PWD}/.devcontainer/python_scripts/set_vtherm_attr.py \
          ${PWD}/config/python_scripts/set_vtherm_attr.py
fi

# Set the path to custom_components
## This let's us have the structure we want <root>/custom_components/integration_blueprint
## while at the same time have Home Assistant configuration inside <root>/config
## without resulting to symlinks.
export PYTHONPATH="${PWD}:${PWD}/config:${PYTHONPATH}"

# Start Home Assistant
hass --config "${PWD}/config" --debug