"""
plugins/build_guide.py — a step-by-step guide for building or making something.

"Give me a build guide for a gaming PC under $900", "how do I build a raised
garden bed", "guide to assemble this 3D printer kit". It writes a complete guide
— parts and tools, ordered steps with what to check at each one, common mistakes,
and safety notes — shows it on the HUD and saves it as Markdown you can print.
"""
from __future__ import annotations

import re
import time
from pathlib import Path

PLUGIN = {
    "name": "build_guide",
    "tier": "extra",
    "description": (
        "Write a complete step-by-step BUILD / HOW-TO-MAKE guide (parts and tools list, numbered "
        "steps with checks, common mistakes, safety) and save it as a file. Use for 'build guide for "
        "X', 'how do I build/assemble/make X', 'walk me through building X'. For a quick factual "
        "question use web_search; for a long web investigation use background research."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "project": {"type": "STRING", "description": "What to build, with any constraints (budget, size, parts owned)"},
            "level": {"type": "STRING", "description": "beginner (default) | intermediate | expert"},
            "language": {"type": "STRING", "description": "Language to write in; default the user's"},
        },
        "required": ["project"],
    },
}

_PROMPT = """Write a practical, accurate build guide for: {project}

Reader level: {level}. Write in {language}.

Use exactly this Markdown structure:
# <Title>
**Time:** … · **Difficulty:** … · **Estimated cost:** …
## What you need
(parts/materials with quantities, then tools)
## Before you start
(prep, workspace, anything to buy or decide first)
## Steps
(numbered; each step: what to do, then a line starting "Check:" saying how to know it is right)
## Common mistakes
## Safety
## When something goes wrong
(3-5 likely problems and the fix)

Rules: be concrete (sizes, torque, temperatures, part numbers only when you are sure); say
"verify against the manufacturer's manual" instead of inventing a specification; no filler."""


def run(parameters: dict, player=None, session_memory=None) -> str:
    project = str(parameters.get("project") or "").strip()
    if not project:
        return "What should the guide be for?"
    try:
        from core import gemini
        text = gemini.text(
            _PROMPT.format(project=project, level=str(parameters.get("level") or "beginner"),
                           language=str(parameters.get("language") or "the same language as the request")),
            tier=gemini.SMART, timeout_ms=120_000)
        text = re.sub(r"^```(?:markdown|md)?\s*|\s*```$", "", str(text or "").strip())
        if len(text) < 200:
            return "The guide came back empty — try again in a moment."
        folder = Path.home() / "Documents" / "PAX Guides"
        folder.mkdir(parents=True, exist_ok=True)
        slug = re.sub(r"[^\w]+", "-", project.lower()).strip("-")[:50] or "guide"
        path = folder / f"{slug}-{time.strftime('%Y%m%d')}.md"
        path.write_text(text, encoding="utf-8")
        steps = len(re.findall(r"^\s*\d+\.", text, re.M))
        if player:
            try:
                player.show_content("BUILD GUIDE", text)
                player.write_log(f"FILE: Build guide — {path}")
            except Exception:
                pass
        title = (re.search(r"^#\s+(.+)$", text, re.M) or [None, project])[1]
        return (f"Done: '{title}' — about {steps} steps. It's on screen and saved to {path}. "
                f"Want me to read it step by step?")
    except Exception as e:
        return f"Build guide failed: {e}"
