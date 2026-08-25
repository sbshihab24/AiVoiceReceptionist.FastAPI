import os
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


DEFAULT_REALTIME_MODEL = "gpt-realtime-1.5"


def _is_deprecated_realtime_model(model: str) -> bool:
    return (
        model.startswith("gpt-4o-realtime-preview")
        or model.startswith("gpt-4o-mini-realtime-preview")
    )


def get_openai_realtime_model() -> str:
    model = os.getenv("OPENAI_REALTIME_MODEL", DEFAULT_REALTIME_MODEL).strip()
    if not model or _is_deprecated_realtime_model(model):
        return DEFAULT_REALTIME_MODEL
    return model


def get_openai_realtime_ws_url() -> str:
    model = get_openai_realtime_model()
    configured_url = os.getenv("OPENAI_WS_URL", "").strip()
    if not configured_url:
        return f"wss://api.openai.com/v1/realtime?model={model}"

    parts = urlsplit(configured_url)
    query_items = []
    found_model = False
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        if key == "model":
            found_model = True
            if _is_deprecated_realtime_model(value):
                value = model
        query_items.append((key, value))

    if not found_model:
        query_items.append(("model", model))

    return urlunsplit((
        parts.scheme,
        parts.netloc,
        parts.path,
        urlencode(query_items),
        parts.fragment,
    ))
