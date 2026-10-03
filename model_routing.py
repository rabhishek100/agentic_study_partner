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


def structured_client(client, *, model, schema=None):
    """Supply the JSON-mode hint required by Qwen's upstream endpoint.

    The provider translates structured output into JSON mode and rejects a
    request unless its messages mention JSON. Keep the schema and validators;
    this adds no inference call and does not change the requested content.
    """
    if not model.startswith("qwen/"):
        return client
    schema_json = schema.model_json_schema() if hasattr(schema, "model_json_schema") else schema
    instruction = "Return JSON conforming to the supplied response schema."
    if schema_json is not None:
        instruction += " The exact JSON Schema is: " + json.dumps(schema_json, ensure_ascii=False, sort_keys=True)
    from langchain_core.messages import SystemMessage
    from langchain_core.runnables import RunnableLambda
    def hint(messages):
        values = messages.to_messages() if hasattr(messages, "to_messages") else list(messages)
        return [SystemMessage(content=instruction), *values]
    return RunnableLambda(hint, name="provider_json_format") | client
