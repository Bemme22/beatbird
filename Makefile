# BeatBird — Top-level orchestrator.
#
# All targets assume the repo is checked out on the Pi at REPO_DIR (auto-detected
# from the directory this Makefile sits in). The active profile is read from
# profiles/current.yml which is a symlink created by `make profile`.
#
# Targets:
#   profile PROFILE=<name>   activate a speaker profile
#   secrets                  create secrets/*.example templates to fill in
#   install                  full first-time install (calls install/*.sh in order)
#   install-role ROLE=<n>    run a single install role (debug)
#   update                   git pull + re-render configs + restart services
#   status                   systemd status for all beatbird services
#   logs                     follow bridge log
#   amixer-apply             re-apply amplifier levels (soundcard-specific)
#   dsp-reload               reload CamillaDSP config without restart
#   uninstall                stop & remove services (config files stay)
#
# Anything under install/ is idempotent — safe to re-run.

SHELL        := /bin/bash
REPO_DIR     := $(abspath $(dir $(firstword $(MAKEFILE_LIST))))
PROFILE_LINK := $(REPO_DIR)/profiles/current.yml
ETC_DIR      := /etc/beatbird

# Resolve profile name (for `make profile PROFILE=…`)
PROFILE ?=

.PHONY: help profile secrets install update status logs amixer-apply dsp-reload \
        uninstall check-profile check-root install-role _banner

help:
	@echo "BeatBird targets:"
	@awk 'BEGIN{FS=":.*##"} /^[a-zA-Z_-]+:.*##/{printf "  %-20s %s\n",$$1,$$2}' $(MAKEFILE_LIST)

# ─── Profile activation ──────────────────────────────────────────────────────

profile: ## Activate a speaker profile (PROFILE=beat-1 | beat-2 | zipp-mini-2 | zipp-2 | zipp | lounge)
	@if [ -z "$(PROFILE)" ]; then \
		echo "ERROR: PROFILE not set. Usage: make profile PROFILE=beat-1"; \
		echo "Available profiles:"; ls -1 profiles/ | grep -vE '^(_|current)' | sed 's/\.yml$$//; s/^/  /'; \
		exit 1; \
	fi
	@if [ ! -f profiles/$(PROFILE).yml ]; then \
		echo "ERROR: profiles/$(PROFILE).yml not found"; exit 1; \
	fi
	@ln -sfn $(PROFILE).yml $(PROFILE_LINK)
	@echo "==> Active profile: $(PROFILE)"
	@python3 -c "import yaml; d=yaml.safe_load(open('$(PROFILE_LINK)')); print(f\"    hostname:  {d['identity']['hostname']}\"); print(f\"    soundcard: {d['soundcard']['driver']}\"); print(f\"    display:   {d['display']['type']}\")"

# ─── Secrets templates ───────────────────────────────────────────────────────

secrets: ## Create secrets/*.example templates (then edit them locally; NOT committed)
	@mkdir -p secrets
	@[ -f secrets/wifi.pass ] || { echo "your-wifi-psk" > secrets/wifi.pass; chmod 600 secrets/wifi.pass; echo "  wrote secrets/wifi.pass"; }
	@[ -f secrets/mqtt.pass ] || { echo "your-mqtt-password" > secrets/mqtt.pass; chmod 600 secrets/mqtt.pass; echo "  wrote secrets/mqtt.pass"; }
	@[ -f secrets/location.coords ] || { echo "0.0,0.0" > secrets/location.coords; chmod 600 secrets/location.coords; echo "  wrote secrets/location.coords (replace with 'lat,lon' to enable weather)"; }
	@echo "Edit these files, then run 'make install'. They are in .gitignore."

# ─── Full install ────────────────────────────────────────────────────────────

install: check-profile _banner ## Full first-time install on a fresh Pi OS
	@for script in $$(find install -maxdepth 2 -name '[0-9]*.sh' | sort); do \
		role=$${script#install/}; \
		echo "─── $$role ─────────────────────────────────"; \
		REPO_DIR=$(REPO_DIR) PROFILE_YML=$(PROFILE_LINK) sudo -E bash $$script || { \
			echo "FAILED: $$role"; exit 1; }; \
	done
	@echo
	@echo "==> BeatBird install complete."
	@echo "    Run 'make status' to verify."

install-role: check-profile ## Run a single install role (e.g. make install-role ROLE=30-camilladsp.sh)
	@test -n "$(ROLE)" || { echo "Usage: make install-role ROLE=30-camilladsp.sh"; exit 1; }
	@test -f install/$(ROLE) || { echo "No such role: install/$(ROLE)"; exit 1; }
	REPO_DIR=$(REPO_DIR) PROFILE_YML=$(PROFILE_LINK) sudo -E bash install/$(ROLE)

# ─── Day-to-day ──────────────────────────────────────────────────────────────

update: check-profile ## git pull + re-render configs + restart services
	git pull --ff-only
	REPO_DIR=$(REPO_DIR) PROFILE_YML=$(PROFILE_LINK) sudo -E bash install/30-camilladsp.sh
	REPO_DIR=$(REPO_DIR) PROFILE_YML=$(PROFILE_LINK) sudo -E bash install/40-go-librespot.sh
	REPO_DIR=$(REPO_DIR) PROFILE_YML=$(PROFILE_LINK) sudo -E bash install/70-bridge.sh
	sudo systemctl daemon-reload
	sudo systemctl restart beatbird-bridge camilladsp go-librespot 2>/dev/null || true
	@echo "==> Updated."

status: ## systemd status for all beatbird services
	@systemctl --no-pager --lines=10 status \
		beatbird-bridge camilladsp go-librespot louder-hat-init snapclient 2>/dev/null || true

logs: ## follow bridge log
	@journalctl -u beatbird-bridge -f

amixer-apply: check-profile ## re-apply amplifier levels for the active soundcard
	REPO_DIR=$(REPO_DIR) PROFILE_YML=$(PROFILE_LINK) sudo -E bash install/10-soundcard/_apply-levels.sh

dsp-reload: ## reload CamillaDSP config without restart
	@curl -s -f http://localhost:5000/reload >/dev/null 2>&1 \
		|| sudo systemctl reload camilladsp 2>/dev/null \
		|| sudo systemctl restart camilladsp
	@echo "CamillaDSP reloaded."

uninstall: ## Stop & disable services (config files stay)
	-sudo systemctl disable --now beatbird-bridge beatbird-web louder-hat-init camilladsp go-librespot snapclient 2>/dev/null
	-sudo rm -f /etc/systemd/system/beatbird-*.service
	sudo systemctl daemon-reload
	@echo "Services removed. /etc/beatbird/ and /etc/camilladsp/ left intact."

# ─── Internal ────────────────────────────────────────────────────────────────

check-profile:
	@test -L $(PROFILE_LINK) -o -f $(PROFILE_LINK) \
		|| { echo "ERROR: no active profile. Run 'make profile PROFILE=<name>' first."; exit 1; }

_banner:
	@echo
	@echo "   ___            _   ___ _         _"
	@echo "  | _ ) ___ __ _ | |_| _ |_)_ _ __| |"
	@echo "  | _ \/ -_) _\` ||  _| _ \ | '_/ _\` |"
	@echo "  |___/\___\__,_| \__|___/_|_| \__,_|"
	@echo

# ─── Test/Prod-Mode (Drop-Ins für Restart=no) ────────────────────────────────

.PHONY: test-mode prod-mode

test-mode: ## Services mit Restart=no für Hardware-Diagnose (speaker-test etc.)
	@for svc in camilladsp beatbird-bridge go-librespot; do \
	  sudo mkdir -p /etc/systemd/system/$$svc.service.d; \
	  printf '[Service]\nRestart=no\n' | sudo tee /etc/systemd/system/$$svc.service.d/no-restart.conf >/dev/null; \
	done
	sudo systemctl daemon-reload
	sudo systemctl stop go-librespot beatbird-bridge camilladsp
	@echo "==> test-mode aktiv. Services bleiben gestoppt bis 'make prod-mode'."

prod-mode: ## Drop-ins entfernen und Services normal starten
	sudo rm -f /etc/systemd/system/camilladsp.service.d/no-restart.conf
	sudo rm -f /etc/systemd/system/beatbird-bridge.service.d/no-restart.conf
	sudo rm -f /etc/systemd/system/go-librespot.service.d/no-restart.conf
	-sudo rmdir /etc/systemd/system/camilladsp.service.d 2>/dev/null
	-sudo rmdir /etc/systemd/system/beatbird-bridge.service.d 2>/dev/null
	-sudo rmdir /etc/systemd/system/go-librespot.service.d 2>/dev/null
	sudo systemctl daemon-reload
	sudo systemctl start camilladsp beatbird-bridge
	@echo "==> prod-mode aktiv. Bridge zieht go-librespot mit."
