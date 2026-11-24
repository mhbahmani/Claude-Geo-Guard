"""Non-destructive structural merging; ownership is exact content, not a fuzzy name."""

import copy
import json

MARKER = "Claude Geo Guard: verifying configured egress country"
EVENTS = ("UserPromptSubmit", "UserPromptExpansion", "PreToolUse")


def parse(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    def invalid_constant(value):
        raise ValueError(f"invalid JSON constant: {value}")

    data = (
        json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid_constant)
        if raw is not None
        else {}
    )
    if not isinstance(data, dict) or not isinstance(data.get("hooks", {}), dict):
        raise ValueError("settings and hooks must be objects")
    for groups in data.get("hooks", {}).values():
        if not isinstance(groups, list):
            raise ValueError("hook event must contain a list")
        for group in groups:
            if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
                raise ValueError("unsupported hook group")
            if not all(isinstance(h, dict) for h in group["hooks"]):
                raise ValueError("unsupported hook handler")
    return data


def owned_groups(command):
    handler = {
        "type": "command",
        "command": str(command),
        "args": ["hook"],
        "timeout": 30,
        "onFailure": "block",
        "statusMessage": MARKER,
    }
    return {
        event: {
            **({"matcher": "*"} if event == "PreToolUse" else {}),
            "hooks": [copy.deepcopy(handler)],
        }
        for event in EVENTS
    }


def mentions(group, command):
    return any(
        (h.get("command") == str(command) or h.get("statusMessage") == MARKER)
        for h in group.get("hooks", [])
    )


def merge(data, groups, command):
    result = copy.deepcopy(data)
    if (
        result.get("disableAllHooks")
        or result.get("allowManagedHooksOnly")
        or (
            result.get("strictPluginOnlyCustomization") is True
            or "hooks" in (result.get("strictPluginOnlyCustomization") or [])
        )
    ):
        raise ValueError("user settings disable user hooks")
    hooks = result.setdefault("hooks", {})
    for event, current in hooks.items():
        if event not in groups and any(mentions(g, command) for g in current):
            raise ValueError(f"project hook moved to {event}; reconcile first")
    for event, owned in groups.items():
        current = hooks.setdefault(event, [])
        matching = [g for g in current if mentions(g, command)]
        if matching and matching != [owned]:
            raise ValueError(f"project hook conflict in {event}; resolve before installing")
        if not matching:
            current.append(copy.deepcopy(owned))
    return result


def remove(data, groups, command, original):
    result = copy.deepcopy(data)
    hooks = result.get("hooks", {})
    for event, current in hooks.items():
        if event not in groups and any(mentions(g, command) for g in current):
            raise ValueError(f"project hook moved to {event}; reconcile first")
    for event, owned in groups.items():
        current = hooks.get(event, [])
        if any(mentions(g, command) and g != owned for g in current):
            raise ValueError(f"edited or combined project hook in {event}; preserved")
        hooks[event] = [g for g in current if g != owned]
        if not hooks[event] and event not in original.get("hooks", {}):
            hooks.pop(event)
    if not hooks and "hooks" not in original:
        result.pop("hooks", None)
    return result
