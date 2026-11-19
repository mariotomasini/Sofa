# mediabox — controlli di qualità.
#   make check   → sintassi degli script, lint e test unitari
#   make test    → solo i test unitari
# Requisiti: bash, python3 con pytest e ruff, shellcheck (pip install shellcheck-py).
# PowerShell (pwsh) con PSScriptAnalyzer è facoltativo: se manca, quel controllo viene saltato.

PYTHON ?= python3
SCRIPT_BASH := install.sh mediabox $(wildcard lib/*.sh)
SCRIPT_PS := install.ps1 mediabox.ps1 lib/comune.ps1

.PHONY: check sintassi lint lint-bash lint-python lint-powershell test test-cerca test-configuratore dipendenze

check: sintassi lint test
	@echo "✔ Tutti i controlli sono passati"

dipendenze:
	$(PYTHON) -m pip install -q pytest ruff shellcheck-py \
		-r cerca/requirements.txt -r configuratore/requirements.txt

sintassi:
	@for f in $(SCRIPT_BASH); do bash -n "$$f" || exit 1; done
	@echo "✔ Sintassi bash"
	@if command -v pwsh >/dev/null 2>&1; then \
		pwsh -NoProfile -Command '$$err = 0; foreach ($$f in "$(SCRIPT_PS)".Split(" ")) { $$e = $$null; [void][System.Management.Automation.Language.Parser]::ParseFile((Resolve-Path $$f), [ref]$$null, [ref]$$e); if ($$e) { Write-Host "$$f : $$e"; $$err = 1 } }; exit $$err' \
		&& echo "✔ Sintassi PowerShell"; \
	else echo "· pwsh non installato: salto la sintassi PowerShell"; fi

lint: lint-bash lint-python lint-powershell

lint-bash:
	shellcheck -x -P SCRIPTDIR $(SCRIPT_BASH)
	@echo "✔ shellcheck"

lint-python:
	ruff check cerca configuratore
	@echo "✔ ruff"

lint-powershell:
	@if command -v pwsh >/dev/null 2>&1 && pwsh -NoProfile -Command 'if (Get-Module -ListAvailable PSScriptAnalyzer) { exit 0 } else { exit 1 }'; then \
		pwsh -NoProfile -Command '$$r = Invoke-ScriptAnalyzer -Path . -Recurse -Settings ./PSScriptAnalyzerSettings.psd1; $$r | Format-Table -AutoSize; if ($$r) { exit 1 }' \
		&& echo "✔ PSScriptAnalyzer"; \
	else echo "· PSScriptAnalyzer non disponibile: salto il lint PowerShell"; fi

test: test-cerca test-configuratore

test-cerca:
	cd cerca && $(PYTHON) -m pytest -q

test-configuratore:
	cd configuratore && $(PYTHON) -m pytest -q
