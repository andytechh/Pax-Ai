"""Windows UI Automation with explicit outcome checks and safe fallbacks."""
from __future__ import annotations

import os
import re
import time
from pathlib import Path


class DesktopController:
    """Small Windows-only controller. UIA is preferred; coordinates are fallback."""

    def __init__(self):
        if os.name != "nt":
            raise RuntimeError("Desktop control is available only on Windows 10/11.")

    @staticmethod
    def _desktop():
        try:
            from pywinauto import Desktop
        except ImportError as exc:
            raise RuntimeError("Windows UI Automation requires pywinauto. Run setup.py again.") from exc
        return Desktop(backend="uia")

    def list_windows(self) -> list[dict]:
        rows = []
        for win in self._desktop().windows(visible_only=True):
            try:
                title = (win.window_text() or "").strip()
                if title:
                    rows.append({"title": title[:240], "handle": int(win.handle),
                                 "class_name": str(win.class_name() or "")[:120]})
            except Exception:
                continue
        return rows[:80]

    def _find_window(self, title: str):
        wanted = (title or "").strip().casefold()
        if not wanted:
            raise ValueError("A window title is required.")
        windows = self._desktop().windows(visible_only=True)
        exact, partial = [], []
        for win in windows:
            try:
                current = (win.window_text() or "").strip()
                if current.casefold() == wanted:
                    exact.append(win)
                elif wanted in current.casefold():
                    partial.append(win)
            except Exception:
                pass
        matches = exact or partial
        if not matches:
            raise RuntimeError(f"No visible window matches '{title}'.")
        if len(matches) > 1 and not exact:
            names = [w.window_text() for w in matches[:5]]
            raise RuntimeError("Window title is ambiguous; specify more text: " + "; ".join(names))
        return matches[0]

    def focus_window(self, title: str) -> str:
        win = self._find_window(title)
        win.set_focus()
        time.sleep(0.15)
        if not win.is_active():
            raise RuntimeError(f"Windows did not activate '{win.window_text()}'.")
        return f"Focused {win.window_text()}."

    def minimize_window(self, title: str) -> str:
        win = self._find_window(title)
        win.minimize()
        time.sleep(0.1)
        if not win.is_minimized():
            raise RuntimeError(f"Could not verify that '{win.window_text()}' was minimized.")
        return f"Minimized {win.window_text()}."

    def maximize_window(self, title: str) -> str:
        win = self._find_window(title)
        win.maximize()
        time.sleep(0.1)
        if not win.is_maximized():
            raise RuntimeError(f"Could not verify that '{win.window_text()}' was maximized.")
        return f"Maximized {win.window_text()}."

    def close_window(self, title: str) -> str:
        win = self._find_window(title)
        name = win.window_text()
        win.close()
        time.sleep(0.2)
        try:
            if win.is_visible():
                raise RuntimeError(f"'{name}' is still visible; it may have an unsaved-changes prompt.")
        except Exception as exc:
            if "still visible" in str(exc):
                raise
        return f"Closed {name}."

    def drag(self, x1: int, y1: int, x2: int, y2: int) -> str:
        values = [int(x1), int(y1), int(x2), int(y2)]
        if any(v < 0 or v > 100000 for v in values):
            raise ValueError("Drag coordinates are outside the permitted range.")
        try:
            import pyautogui
            pyautogui.moveTo(values[0], values[1], duration=0.15)
            pyautogui.dragTo(values[2], values[3], duration=0.35, button="left")
        except Exception as exc:
            raise RuntimeError(f"Drag failed: {exc}") from exc
        return f"Dragged from ({values[0]}, {values[1]}) to ({values[2]}, {values[3]})."

    @staticmethod
    def _norm(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", " ", str(value or "").casefold()).strip()

    def _controls(self, win, depth: int = 8) -> list[dict]:
        rows = []
        try:
            descendants = win.descendants()
        except Exception:
            descendants = []
        for control in descendants[:1500]:
            try:
                info = control.element_info
                rect = control.rectangle()
                if not control.is_visible() or rect.width() <= 0 or rect.height() <= 0:
                    continue
                rows.append({
                    "control": control,
                    "name": str(info.name or control.window_text() or "").strip(),
                    "control_type": str(info.control_type or ""),
                    "automation_id": str(info.automation_id or ""),
                    "rect": rect,
                    "enabled": bool(control.is_enabled()),
                    "depth": int(getattr(info, "depth", 0) or 0),
                })
            except Exception:
                continue
        return rows

    def list_controls(self, window_title: str, limit: int = 100) -> str:
        win = self._find_window(window_title)
        rows = self._controls(win)
        if not rows:
            return f"No visible UI Automation controls were exposed by {win.window_text()}."
        lines = []
        for item in rows[:max(1, min(int(limit), 200))]:
            r = item["rect"]
            lines.append(
                f"{item['control_type']} name={item['name']!r} automation_id={item['automation_id']!r} "
                f"rect=({r.left},{r.top},{r.right},{r.bottom}) enabled={item['enabled']}"
            )
        return "\n".join(lines)

    def _resolve_control(self, win, control_name: str, control_type: str = "") -> dict:
        wanted = self._norm(control_name)
        if not wanted:
            raise ValueError("A control name or description is required.")
        rows = self._controls(win)
        candidates = []
        for item in rows:
            if control_type and self._norm(item["control_type"]) != self._norm(control_type):
                continue
            names = [self._norm(item["name"]), self._norm(item["automation_id"])]
            best = 0.0
            for name in names:
                if not name:
                    continue
                if name == wanted:
                    best = max(best, 1.0)
                elif wanted in name or name in wanted:
                    best = max(best, 0.88)
                else:
                    from difflib import SequenceMatcher
                    best = max(best, SequenceMatcher(None, wanted, name).ratio())
            # Prefer actionable controls when labels are shared with containers.
            if item["control_type"].casefold() in {"button", "hyperlink", "checkbox", "radio button", "menu item", "tab item"}:
                best += 0.04
            if best >= 0.70:
                candidates.append((best, item))
        candidates.sort(key=lambda pair: pair[0], reverse=True)
        if not candidates:
            raise RuntimeError(
                f"Could not match '{control_name}' to a visible UI Automation control in '{win.window_text()}'. "
                "Inspect the window controls or use visual targeting; do not guess a nearby coordinate."
            )
        if len(candidates) > 1 and candidates[0][0] - candidates[1][0] < 0.06:
            choices = [f"{x['control_type']} {x['name']!r}" for _, x in candidates[:5]]
            raise RuntimeError("Target is ambiguous. Matching controls: " + "; ".join(choices))
        return candidates[0][1]

    def hover_control(self, window_title: str, control_name: str, control_type: str = "") -> str:
        win = self._find_window(window_title)
        item = self._resolve_control(win, control_name, control_type)
        if not item["enabled"]:
            raise RuntimeError(f"Control '{item['name']}' is disabled; pointer was not moved.")
        rect = item["rect"]
        x, y = int((rect.left + rect.right) / 2), int((rect.top + rect.bottom) / 2)
        try:
            import pyautogui
            pyautogui.moveTo(x=x, y=y, duration=0.2)
            time.sleep(0.15)
            px, py = pyautogui.position()
            if abs(px - x) > 3 or abs(py - y) > 3:
                raise RuntimeError("Pointer did not reach the resolved control.")
        except Exception as exc:
            raise RuntimeError(f"Could not hover over '{item['name']}': {exc}") from exc
        return f"Hovered over {item['control_type']} '{item['name']}' in {win.window_text()} at ({x}, {y})."

    def click_control(self, window_title: str, control_name: str, control_type: str = "") -> str:
        win = self._find_window(window_title)
        item = self._resolve_control(win, control_name, control_type)
        control = item["control"]
        if not item["enabled"]:
            raise RuntimeError(f"Control '{item['name']}' is disabled; it was not clicked.")
        try:
            control.click_input()
            time.sleep(0.15)
        except Exception as exc:
            raise RuntimeError(f"Click on '{item['name']}' failed: {exc}") from exc
        return f"Clicked {item['control_type']} '{item['name']}' in {win.window_text()}. Re-inspect the window to verify the resulting state."

    def move_cursor(self, x: int, y: int) -> str:
        """Move the pointer to a screen position without clicking (supports hover)."""
        if not (0 <= x <= 100000 and 0 <= y <= 100000):
            raise ValueError("Coordinates are outside the permitted range.")
        try:
            import pyautogui
            pyautogui.moveTo(x=int(x), y=int(y), duration=0.2)
        except Exception as exc:
            raise RuntimeError(f"Pointer movement failed: {exc}") from exc
        return f"Moved pointer to screen position {int(x)}, {int(y)}."

    def click_coordinates(self, x: int, y: int) -> str:
        if not (0 <= x <= 100000 and 0 <= y <= 100000):
            raise ValueError("Coordinates are outside the permitted range.")
        try:
            import pyautogui
            pyautogui.click(x=int(x), y=int(y))
        except Exception as exc:
            raise RuntimeError(f"Coordinate click failed: {exc}") from exc
        return f"Clicked screen position {int(x)}, {int(y)}."

    def type_text(self, text: str, window_title: str = "") -> str:
        value = str(text or "")
        if not value:
            return "No text was supplied."
        if len(value) > 12000:
            raise ValueError("Text is limited to 12,000 characters per operation.")
        if window_title:
            self.focus_window(window_title)
        try:
            import pyautogui
            import pyperclip
            pyperclip.copy(value)
            pyautogui.hotkey("ctrl", "v")
        except Exception as exc:
            raise RuntimeError(f"Text entry failed: {exc}") from exc
        return f"Entered {len(value)} characters."

    def hotkey(self, keys: list[str]) -> str:
        clean = [str(k).strip().lower() for k in keys if str(k).strip()]
        if not 1 <= len(clean) <= 4:
            raise ValueError("Provide between one and four keys.")
        if any(not re.fullmatch(r"[a-z0-9_]+", k) for k in clean):
            raise ValueError("One or more key names are invalid.")
        if set(clean) >= {"ctrl", "alt", "delete"} or any(k in {"win", "winleft", "winright"} for k in clean):
            raise ValueError("That system-level shortcut is blocked by the desktop safety policy.")
        try:
            import pyautogui
            pyautogui.hotkey(*clean)
        except Exception as exc:
            raise RuntimeError(f"Keyboard shortcut failed: {exc}") from exc
        return "Pressed " + "+".join(clean) + "."

    def scroll(self, direction: str, amount: int = 3) -> str:
        direction = str(direction).lower()
        if direction not in {"up", "down", "left", "right"}:
            raise ValueError("Scroll direction must be up, down, left, or right.")
        amount = max(1, min(abs(int(amount)), 20))
        try:
            import pyautogui
            if direction in {"up", "down"}:
                pyautogui.scroll(amount if direction == "up" else -amount)
            else:
                pyautogui.hscroll(amount if direction == "right" else -amount)
        except Exception as exc:
            raise RuntimeError(f"Scroll failed: {exc}") from exc
        return f"Scrolled {direction} by {amount} steps."

    def screenshot(self) -> str:
        try:
            import pyautogui
            from memory.config_manager import get_base_dir
            folder = get_base_dir() / "config" / "desktop_captures"
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / f"screen-{int(time.time())}.png"
            pyautogui.screenshot().save(path)
            if not path.exists() or path.stat().st_size < 1000:
                raise RuntimeError("Screenshot file was not written correctly.")
            return str(path)
        except Exception as exc:
            raise RuntimeError(f"Screenshot failed: {exc}") from exc
