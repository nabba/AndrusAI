"""
mobile_tools.py — Mobile app development tools via Expo/React Native.

All operations execute on the host via bridge.
Requires Node.js and Expo CLI on the host (one-time setup).

Host prerequisites:
  npm install -g eas-cli

Usage:
    from app.tools.mobile_tools import create_mobile_tools
    tools = create_mobile_tools("devops")
"""

import logging

logger = logging.getLogger(__name__)


def create_mobile_tools(agent_id: str) -> list:
    """Create mobile app development tools via bridge.

    Returns empty list if bridge is unavailable.
    """
    try:
        from app.bridge_client import get_bridge
        bridge = get_bridge(agent_id)
        if not bridge:
            return []
        if not bridge.is_available():
            return []
    except Exception:
        return []

    try:
        from crewai.tools import BaseTool
        from pydantic import BaseModel, Field
        from typing import Type
    except ImportError:
        return []

    # ── Tool definitions ──────────────────────────────────────────

    class _CreateExpoInput(BaseModel):
        name: str = Field(description="App name (lowercase, no spaces)")
        template: str = Field(
            default="blank",
            description="Expo template: blank, tabs, blank-typescript",
        )

    class CreateExpoAppTool(BaseTool):
        name: str = "create_expo_app"
        description: str = (
            "Create a new React Native app using Expo. "
            "Requires Node.js and npx on the host."
        )
        args_schema: Type[BaseModel] = _CreateExpoInput

        def _run(self, name: str, template: str = "blank") -> str:
            name = name.lower().replace(" ", "-")
            base = "/tmp/crewai-projects"
            result = bridge.execute(
                ["sh", "-c", f"cd {base} && npx create-expo-app@latest {name} --template {template} 2>&1"]
            )
            if "error" in result:
                return f"Error: {result.get('detail', result['error'])}"
            output = result.get("stdout", "")
            if "error" in output.lower() and "npm" in output.lower():
                return f"Expo creation failed. Ensure Node.js is installed.\n{output[:500]}"
            return f"Expo app created at {base}/{name}\n\nTo preview: cd {base}/{name} && npx expo start"

    class _EASBuildInput(BaseModel):
        project_path: str = Field(description="Path to the Expo project")
        platform: str = Field(
            default="all",
            description="Build platform: ios, android, or all",
        )
        profile: str = Field(
            default="preview",
            description="Build profile: development, preview, production",
        )

    class EASBuildTool(BaseTool):
        name: str = "eas_build"
        description: str = (
            "Build a mobile app using Expo Application Services (EAS). "
            "Compiles in the cloud (free tier: 30 builds/month). "
            "Requires 'eas-cli' installed and authenticated."
        )
        args_schema: Type[BaseModel] = _EASBuildInput

        def _run(self, project_path: str, platform: str = "all", profile: str = "preview") -> str:
            result = bridge.execute(
                ["sh", "-c", f"cd {project_path} && eas build --platform {platform} --profile {profile} --non-interactive 2>&1"]
            )
            if "error" in result:
                return f"Error: {result.get('detail', result['error'])}"
            output = result.get("stdout", "")
            return output[:3000] if output else "Build submitted. Check EAS dashboard for status."

    class _EASSubmitInput(BaseModel):
        project_path: str = Field(description="Path to the Expo project")
        platform: str = Field(description="Platform: ios or android")

    class EASSubmitTool(BaseTool):
        name: str = "eas_submit"
        description: str = (
            "Submit a built app to the App Store or Google Play Store. "
            "Requires EAS build to complete first and store credentials configured."
        )
        args_schema: Type[BaseModel] = _EASSubmitInput

        def _run(self, project_path: str, platform: str) -> str:
            result = bridge.execute(
                ["sh", "-c", f"cd {project_path} && eas submit --platform {platform} --non-interactive 2>&1"]
            )
            if "error" in result:
                return f"Error: {result.get('detail', result['error'])}"
            output = result.get("stdout", "")
            return output[:3000] if output else "Submission initiated."

    class _PWAInput(BaseModel):
        name: str = Field(description="App name")
        project_path: str = Field(
            default="/tmp/crewai-projects",
            description="Base directory to create PWA in",
        )

    class CreatePWATool(BaseTool):
        name: str = "create_pwa"
        description: str = (
            "Create a Progressive Web App (PWA) — a web app that works offline "
            "and can be installed on any device. No app store needed."
        )
        args_schema: Type[BaseModel] = _PWAInput

        def _run(self, name: str, project_path: str = "/tmp/crewai-projects") -> str:
            name_clean = name.lower().replace(" ", "-")
            app_dir = f"{project_path}/{name_clean}"

            # Create PWA files
            manifest = f'''{{"name": "{name}", "short_name": "{name_clean}", "start_url": "/", "display": "standalone", "background_color": "#ffffff", "theme_color": "#3b82f6", "icons": [{{"src": "icon-192.png", "sizes": "192x192", "type": "image/png"}}]}}'''

            sw = '''\
const CACHE_NAME = 'v1';
const ASSETS = ['/', '/index.html', '/style.css', '/app.js'];
self.addEventListener('install', e => e.waitUntil(caches.open(CACHE_NAME).then(c => c.addAll(ASSETS))));
self.addEventListener('fetch', e => e.respondWith(caches.match(e.request).then(r => r || fetch(e.request))));
'''

            html = f'''\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta name="theme-color" content="#3b82f6">
  <link rel="manifest" href="manifest.json">
  <title>{name}</title>
  <link rel="stylesheet" href="style.css">
</head>
<body>
  <main><h1>{name}</h1><p>Your PWA is ready.</p></main>
  <script>if ('serviceWorker' in navigator) navigator.serviceWorker.register('sw.js');</script>
  <script src="app.js"></script>
</body>
</html>
'''
            css = "* { margin:0; padding:0; box-sizing:border-box; }\nbody { font-family:system-ui; max-width:800px; margin:0 auto; padding:2rem; }\nh1 { margin-bottom:1rem; }\n"
            js = f"console.log('{name_clean} PWA loaded');\n"

            bridge.execute(["mkdir", "-p", app_dir])
            bridge.write_file(f"{app_dir}/index.html", html, create_dirs=True)
            bridge.write_file(f"{app_dir}/manifest.json", manifest, create_dirs=True)
            bridge.write_file(f"{app_dir}/sw.js", sw, create_dirs=True)
            bridge.write_file(f"{app_dir}/style.css", css, create_dirs=True)
            bridge.write_file(f"{app_dir}/app.js", js, create_dirs=True)

            return (
                f"PWA created at {app_dir}\n"
                f"Files: index.html, manifest.json, sw.js, style.css, app.js\n"
                f"To serve: cd {app_dir} && python3 -m http.server 8080\n"
                f"Deploy to any static host (GitHub Pages, Cloudflare Pages, Netlify)."
            )

    return [
        CreateExpoAppTool(),
        EASBuildTool(),
        EASSubmitTool(),
        CreatePWATool(),
    ]
