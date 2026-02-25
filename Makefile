PLUGIN_NAME=netbox_facts
REPO_PATH=/opt/netbox-facts
VENV_PY_PATH=/opt/netbox/venv/bin/python3
NETBOX_MANAGE_PATH=/opt/netbox/netbox
NETBOX_INITIALIZER_PATH=${REPO_PATH}/.devcontainer/initializers
VERFILE=./version.py
PROJECT_PATH:=$(shell dirname $(realpath $(firstword $(MAKEFILE_LIST))))

.PHONY: help ## Display help message
help:
	@grep -E '^[0-9a-zA-Z_-]+\.*[0-9a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-30s\033[0m %s\n", $$1, $$2}'

##################
##     DOCKER    #
##################
#
## Outside of Devcontainer
#
.PHONY: compose_cleanup ## Clean associated docker resources.
compose_cleanup:
	docker-compose -p netbox-facts_devcontainer rm -fv

.PHONY: inotify_watch_worker ## Watch for changes and restart worker container
inotify_watch_worker:
	fish -c "while true; inotifywait -q -r -e modify $(PROJECT_PATH)/netbox_facts --exclude \".*\.pyc\" ; docker restart netbox_facts-worker; sleep 2; end"

.PHONY: watch_worker_logs
watch_worker_logs:
	fish -c "while true; docker logs -f netbox_facts-worker | tail -n10; sleep 2; end"

##################
#   PLUGIN DEV   #
##################

# in VS Code Devcontianer

.PHONY: nbshell ## Run nbshell
nbshell:
	${VENV_PY_PATH} ${NETBOX_MANAGE_PATH}/manage.py nbshell
	from netbox_facts.models import *

.PHONY: setup ## Setup NetBox plugin.
setup:
	uv pip install --python ${VENV_PY_PATH} -e ${REPO_PATH}
	git config core.hooksPath .githooks

.PHONY: example_initializers ## Run initializers
example_initializers:
	mkdir -p ${REPO_PATH}/.devcontainer/initializers
	-${VENV_PY_PATH} ${NETBOX_MANAGE_PATH}/manage.py copy_initializers_examples --path ${REPO_PATH}/.devcontainer/initializers

.PHONY: load_initializers ## Run initializers
load_initializers:
	-${VENV_PY_PATH} ${NETBOX_MANAGE_PATH}/manage.py load_initializer_data  --path ${REPO_PATH}/.devcontainer/initializers

.PHONY: migrations ## Run makemigrations
migrations:
	-${VENV_PY_PATH} ${NETBOX_MANAGE_PATH}/manage.py makemigrations --name ${PLUGIN_NAME}

.PHONY: migrate ## Run migrate
migrate:
	-${VENV_PY_PATH} ${NETBOX_MANAGE_PATH}/manage.py migrate

.PHONY: collectstatic
collectstatic:
	-${VENV_PY_PATH} ${NETBOX_MANAGE_PATH}/manage.py collectstatic --no-input

PHONY: runserver
runserver:
	-${VENV_PY_PATH} ${NETBOX_MANAGE_PATH}/manage.py runserver 0.0.0.0:8000

.PHONY: initializers
initializers:
	-rm -rf ${NETBOX_INITIALIZER_PATH}
	-mkdir ${NETBOX_INITIALIZER_PATH}
	-${VENV_PY_PATH} ${NETBOX_MANAGE_PATH}/manage.py copy_initializers_examples --path ${NETBOX_INITIALIZER_PATH}
	-for file in ${NETBOX_INITIALIZER_PATH}/*.yml; do sed -i "s/^# //g" "$$file"; done
	-${VENV_PY_PATH} ${NETBOX_MANAGE_PATH}/manage.py load_initializer_data --path ${NETBOX_INITIALIZER_PATH}

.PHONY: launch ## Start NetBox
launch:
	- cd /opt/netbox/netbox/ && /opt/netbox/docker-entrypoint.sh && /opt/netbox/launch-netbox.sh

.PHONY: all ## Run all PLUGIN DEV targets
all: setup makemigrations migrate collectstatic initializers launch

.PHONY: rebuild ## Run PLUGIN DEV targets to rebuild
rebuild: setup makemigrations migrate collectstatic launch

.PHONY: test
test: setup
	${VENV_PY_PATH} ${NETBOX_MANAGE_PATH}/manage.py makemigrations ${PLUGIN_NAME} --check
	${VENV_PY_PATH} ${NETBOX_MANAGE_PATH}/manage.py test ${PLUGIN_NAME}

#relpatch:
#	$(eval GSTATUS := $(shell git status --porcelain))
#ifneq ($(GSTATUS),)
#	$(error Git status is not clean. $(GSTATUS))
#endif
#	git checkout develop
#	git remote update
#	git pull origin develop
#	$(eval CURVER := $(shell cat $(VERFILE) | grep -oE '[0-9]+\.[0-9]+\.[0-9]+'))
#	$(eval NEWVER := $(shell pysemver bump patch $(CURVER)))
#	$(eval RDATE := $(shell date '+%Y-%m-%d'))
#	git checkout -b release-$(NEWVER) origin/develop
#	echo '__version__ = "$(NEWVER)"' > $(VERFILE)
#	git commit -am 'bump ver'
#	git push origin release-$(NEWVER)
#	git checkout develop

#pbuild:
#	${VENV_PY_PATH} -m pip install --upgrade build
#	${VENV_PY_PATH} -m build
#
#pypipub:
#	${VENV_PY_PATH} -m pip install --upgrade twine
#	${VENV_PY_PATH} -m twine upload dist/*

