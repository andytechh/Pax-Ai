"""
plugins/shopping_list.py — a shopping list that lives on your PC.

"Add milk and eggs to my shopping list", "what's on my list", "I bought the eggs",
"clear the list". It is shown on the HUD panel and stored in memory/shopping_list.json.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

_lock = threading.Lock()
_PATH = Path(__file__).resolve().parent.parent / "memory" / "shopping_list.json"

PLUGIN = {
    "name": "shopping_list",
    "tier": "extra",
    "description": (
        "The user's shopping list. add / remove / bought / show / clear. Use for 'add X to my "
        "shopping list', 'what do I need to buy', 'I got the milk', 'clear my list'. A reminder "
        "to buy something at a time is the reminder tool, not this."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "description": "add | remove | bought | show | clear"},
            "items": {"type": "ARRAY", "items": {"type": "STRING"},
                      "description": "Item names, one per entry (split 'milk and eggs' into two)"},
        },
        "required": ["action"],
    },
}


def _load() -> list[dict]:
    try:
        data = json.loads(_PATH.read_text(encoding="utf-8"))
        return [d for d in data if isinstance(d, dict) and d.get("item")]
    except (OSError, ValueError):
        return []


def _save(items: list[dict]) -> None:
    _PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = _PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(_PATH)


def _render(items: list[dict]) -> str:
    return "\n".join(f"[{'x' if i.get('done') else ' '}] {i['item']}" for i in items) or "(empty)"


def run(parameters: dict, player=None, session_memory=None) -> str:
    action = str(parameters.get("action") or "show").strip().lower()
    names = [str(x).strip() for x in (parameters.get("items") or []) if str(x).strip()]
    with _lock:
        items = _load()
        if action == "add":
            if not names:
                return "What should I add?"
            have = {i["item"].lower() for i in items if not i.get("done")}
            added = []
            for n in names:
                if n.lower() not in have:
                    added.append(n)
                    have.add(n.lower())
            items += [{"item": n, "done": False} for n in added]
            _save(items)
            msg = (f"Added {', '.join(added)}." if added else "Those are already on the list.")
        elif action in ("remove", "bought"):
            if not names:
                return "Which item?"
            hit = []
            for n in names:
                for i in items:
                    if n.lower() in i["item"].lower() and i not in hit:
                        hit.append(i)
            if not hit:
                return f"I don't see {', '.join(names)} on the list."
            if action == "remove":
                items = [i for i in items if i not in hit]
            else:
                for i in hit:
                    i["done"] = True
            _save(items)
            msg = f"{'Removed' if action == 'remove' else 'Ticked off'} {', '.join(i['item'] for i in hit)}."
        elif action == "clear":
            _save([])
            items, msg = [], "The shopping list is empty."
        elif action == "show":
            todo = [i["item"] for i in items if not i.get("done")]
            msg = f"You need: {', '.join(todo)}." if todo else "Your shopping list is empty."
        else:
            return f"Unknown action '{action}'."
    if player:
        try:
            player.show_content("SHOPPING LIST", _render(items))
        except Exception:
            pass
    return msg
