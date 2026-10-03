"""Optional explicit provider routes; ordinary clients retain their defaults."""
import json
import os


def provider_options(model):
    routes = json.loads(os.getenv("OPENROUTER_PROVIDER_ROUTES", "{}"))
    if not isinstance(routes, dict):
        raise ValueError("Provider routes must map model names to provider lists")
    only = routes.get(model)
    if only is None:
        return {}
    if not isinstance(only, list) or not only or any(not isinstance(v, str) or not v.strip() for v in only):
        raise ValueError("A pinned model needs a nonempty list of provider identifiers")
    return {"provider": {"only": only, "allow_fallbacks": False, "require_parameters": True}}
