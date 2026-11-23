"""Desktop UI Automation action. Uses UIA before coordinate-based fallback."""
from __future__ import annotations

from core.desktop import DesktopController
import re

_RISKY_CONTROL = re.compile(r"\b(send|submit|delete|remove|purchase|buy|pay|publish|install|share|transfer|invite|confirm|place order|checkout)\b", re.I)


def _approval(title: str, detail: str, run):
    from core import confirm
    if confirm.pending_title():
        return "Another action is awaiting confirmation. Resolve it first."
    return confirm.request(key="desktop-important", title=title, detail=detail, run=run)

TOOL = {
    "name": "desktop_control",
    "description": ("Control the Windows desktop: inspect visible UI Automation controls, gracefully close applications and verify their windows disappear, focus a window, resolve and click a named control, hover over a named control, "
                    "type text into the focused application, press a keyboard shortcut, scroll, or capture a screenshot. "
                    "Prefer named controls over coordinates. Use this for direct computer interaction, not to send Gmail messages."),
    "tier": "core",
    "brief": "Operate Windows windows, controls, keyboard, mouse, and screenshots.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "operation": {"type": "STRING", "enum": ["list_windows", "focus_window", "minimize_window", "maximize_window", "close_window", "close_application", "click_control", "list_controls", "hover_control", "move_cursor", "click_coordinates", "drag", "type_text", "hotkey", "scroll", "screenshot"]},
            "window_title": {"type": "STRING"},
            "control_name": {"type": "STRING"},
            "control_type": {"type": "STRING"},
            "expected_state": {"type": "STRING", "description": "Optional visible label/state expected after the click."},
            "x": {"type": "INTEGER", "description": "Absolute screen X coordinate for move_cursor or click_coordinates."}, "y": {"type": "INTEGER", "description": "Absolute screen Y coordinate for move_cursor or click_coordinates."},
            "x1": {"type": "INTEGER"}, "y1": {"type": "INTEGER"},
            "x2": {"type": "INTEGER"}, "y2": {"type": "INTEGER"},
            "text": {"type": "STRING"},
            "keys": {"type": "ARRAY", "items": {"type": "STRING"}},
            "direction": {"type": "STRING"}, "amount": {"type": "INTEGER"},
        },
        "required": ["operation"],
    },
}


def desktop_control(parameters: dict, **_context) -> str:
    try:
        ctl = DesktopController()
        op = parameters.get("operation")
        if op == "list_windows":
            rows = ctl.list_windows()
            return "Visible windows: " + ("; ".join(f"{r['title']} [handle {r['handle']}]" for r in rows) if rows else "none found")
        if op == "focus_window":
            return ctl.focus_window(parameters.get("window_title", ""))
        if op == "minimize_window":
            return ctl.minimize_window(parameters.get("window_title", ""))
        if op == "maximize_window":
            return ctl.maximize_window(parameters.get("window_title", ""))
        if op == "close_window":
            return ctl.close_window(parameters.get("window_title", ""))
        if op == "close_application":
            return ctl.close_application(parameters.get("window_title", ""))
        if op == "list_controls":
            return ctl.list_controls(parameters.get("window_title", ""), parameters.get("amount", 100))
        if op == "hover_control":
            return ctl.hover_control(parameters.get("window_title", ""), parameters.get("control_name", ""),
                                     parameters.get("control_type", ""))
        if op == "click_control":
            window = parameters.get("window_title", "")
            control = parameters.get("control_name", "")
            control_type = parameters.get("control_type", "")
            if _RISKY_CONTROL.search(control):
                return _approval("Confirm important desktop action",
                                 f"Window: {window}\nControl: {control}\nThis control may send, submit, delete, purchase, publish, install, or share data.",
                                 lambda: ctl.click_control(window, control, control_type, parameters.get("expected_state", "")))
            return ctl.click_control(window, control, control_type, parameters.get('expected_state', ''))
        if op == "move_cursor":
            x, y = parameters.get("x", -1), parameters.get("y", -1)
            return ctl.move_cursor(x, y)
        if op == "click_coordinates":
            x, y = parameters.get("x", -1), parameters.get("y", -1)
            return ctl.click_coordinates(x, y)
        if op == "drag":
            return ctl.drag(parameters.get("x1", -1), parameters.get("y1", -1),
                            parameters.get("x2", -1), parameters.get("y2", -1))
        if op == "type_text":
            return ctl.type_text(parameters.get("text", ""), parameters.get("window_title", ""))
        if op == "hotkey":
            keys = [str(k).lower() for k in parameters.get("keys", [])]
            if ({"ctrl", "enter"} <= set(keys) or {"alt", "f4"} <= set(keys)
                    or {"ctrl", "w"} <= set(keys)):
                return _approval("Confirm consequential keyboard shortcut",
                                 "Press " + "+".join(keys) + ". This may submit a form, send a message, or close an application/tab.",
                                 lambda: ctl.hotkey(keys))
            return ctl.hotkey(keys)
        if op == "scroll":
            return ctl.scroll(parameters.get("direction", "down"), parameters.get("amount", 3))
        if op == "screenshot":
            return "Screenshot saved and verified at " + ctl.screenshot()
        return f"Unsupported desktop operation: {op}"
    except Exception as exc:
        return f"Desktop control failed: {exc}"


TOOL["handler"] = desktop_control
