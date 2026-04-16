# Proposal #589: Missing function import/definition

**Type:** code
**Created:** 2026-04-15T06:48:50.219042+00:00

## Description

Diagnosis: The function '_load_episteme_context' is being called but was never imported or defined in the orchestrator.py file.

Fix: Import or define the '_load_episteme_context' function in orchestrator.py since it's being called in the _run_crew method but isn't available. This should be added to the imports from app.agents.commander.context or defined locally if it's a new function.

## Files

None
