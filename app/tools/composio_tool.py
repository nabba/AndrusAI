"""
composio_tool.py — Composio MCP integration for 850+ SaaS connections.

Provides agents access to external services (Gmail, Calendar, Slack,
GitHub, Jira, Notion, Airtable, etc.) via Composio's MCP framework.

Setup:
    1. pip install composio-core composio-crewai
    2. composio login
    3. composio add github  (etc. per service)

If Composio is not installed, tools gracefully degrade to unavailable.
"""

import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)

_composio_available = None


def _check_composio() -> bool:
    """Check if Composio is installed and configured."""
    global _composio_available
    if _composio_available is not None:
        return _composio_available
    try:
        from composio_crewai import ComposioToolSet
        _composio_available = True
    except ImportError:
        logger.info("composio_tool: Composio not installed — SaaS integrations unavailable")
        _composio_available = False
    return _composio_available


def get_composio_tools(actions: list[str] | None = None) -> list:
    """Get Composio tools for CrewAI agent assignment.

    Args:
        actions: Specific Composio actions to include (e.g., ["GITHUB_LIST_REPOS"])
                 If None, returns commonly useful tools.

    Returns: List of CrewAI-compatible tool objects, or empty if unavailable.
    """
    if not _check_composio():
        return []

    try:
        from composio_crewai import ComposioToolSet

        toolset = ComposioToolSet()

        if actions:
            return toolset.get_tools(actions=actions)

        # Default: commonly useful actions across services
        default_actions = []

        # Check which integrations are connected
        try:
            from composio import ComposioToolSet as CoreToolSet
            core = CoreToolSet()
            connected = core.get_connected_accounts()
            if connected:
                logger.info(f"composio_tool: {len(connected)} connected accounts found")
        except Exception:
            pass

        if default_actions:
            return toolset.get_tools(actions=default_actions)

        # Return all available tools if no specific actions requested
        return toolset.get_tools()

    except Exception as e:
        logger.warning(f"composio_tool: Failed to initialize — {e}")
        return []


def list_available_integrations() -> dict:
    """List available and connected Composio integrations."""
    if not _check_composio():
        return {"available": False, "reason": "Composio not installed"}

    try:
        from composio import ComposioToolSet
        toolset = ComposioToolSet()

        # List connected accounts
        connected = []
        try:
            accounts = toolset.get_connected_accounts()
            connected = [{"app": a.appUniqueId, "status": a.status}
                         for a in accounts]
        except Exception:
            pass

        return {
            "available": True,
            "connected_accounts": connected,
            "count": len(connected),
        }
    except Exception as e:
        return {"available": False, "reason": str(e)[:200]}


def execute_composio_action(action: str, params: dict = None) -> dict:
    """Execute a specific Composio action.

    Args:
        action: Composio action name (e.g., "GITHUB_LIST_REPOS")
        params: Action parameters

    Returns: Action result dict
    """
    if not _check_composio():
        return {"success": False, "error": "Composio not installed"}

    try:
        from composio import ComposioToolSet
        toolset = ComposioToolSet()
        result = toolset.execute_action(action=action, params=params or {})
        return {"success": True, "result": result}
    except Exception as e:
        return {"success": False, "error": str(e)[:300]}
