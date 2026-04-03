"""
llm_suite — Unified access to the LLM management layer.

Provides a single import point for the 5 LLM-related modules.

Usage:
    from app.llm_suite import create_specialist_llm, create_commander_llm
    from app.llm_suite import select_model
    from app.llm_suite import set_mode, get_mode
    from app.llm_suite import CATALOG
"""

# LLM creation (lazy crewai loading)
from app.llm_factory import (
    create_specialist_llm, create_commander_llm,
    create_vetting_llm, create_cheap_vetting_llm,
    get_last_model, get_last_tier,
)

# Model selection logic
from app.llm_selector import select_model

# Mode switching
from app.llm_mode import set_mode, get_mode

# Model catalog
from app.llm_catalog import CATALOG, get_model, get_model_id, get_provider, get_tier
