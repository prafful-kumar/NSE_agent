"""Standalone provider bake-off harness for Phase 3 (company intelligence).

This package is deliberately decoupled from investing_agent.db.models and
investing_agent.schemas: it exists to score candidate data providers, not to
run production ingestion. Nothing here is imported by the main app.

See investing_agent/research/provider_evaluation/REPORT.md for the bake-off
findings and recommendation.
"""
