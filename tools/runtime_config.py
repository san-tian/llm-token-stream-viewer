"""Read and save explicit deployment snapshots; never inspect the client environment."""
import json
import os
import pathlib
import re
from urllib.parse import urlsplit, urlunsplit

_SECRET = re.compile(r"(?:^|[_\-\s])(api[_\-]?key|admin[_\-]?key|access[_\-]?key|private[_\-]?key|token|password|passwd|secret|credential(?:s)?|authorization)(?:$|[_\-\s])", re.I)


def _clean(value, key=""):
    if value is None:
        return None
    if _SECRET.search(key):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(k): _clean(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_clean(v) for v in value]
    if isinstance(value, str):
        if re.search(r"\bBearer\s+\S+", value, re.I):
            return "[REDACTED]"
        if value.lower().startswith(("http://", "https://")):
            try:
                url = urlsplit(value)
                host = url.hostname or ""
                host = "[" + host + "]" if ":" in host else host
                port = ":" + str(url.port) if url.port is not None else ""
                return urlunsplit((url.scheme, host + port, url.path, "", ""))
            except ValueError:
                return "[REDACTED]"
        if value.lstrip().startswith(("{", "[")):
            try:
                return json.dumps(_clean(json.loads(value)), ensure_ascii=False)
            except ValueError:
                pass
    return value


def _read_object(path):
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(str(path) + " must contain a JSON object")
    return _clean(data)


def read_runtime_config(directory, meta=None):
    directory = pathlib.Path(directory)
    meta = meta or {}
    result = {}
    for name in ("env", "flags"):
        path = directory / (name + ".json")
        fallback = meta.get("deployment_" + name) or {}
        if not isinstance(fallback, dict):
            raise ValueError("deployment_" + name + " must be a JSON object")
        result[name] = _read_object(path) if path.exists() else _clean(fallback)
    result["note"] = meta.get("runtime_config_note") or ""
    return result


def save_runtime_config(directory):
    """Copy only explicitly supplied server snapshots, with sensitive values removed."""
    directory = pathlib.Path(directory)
    for name, variable in (("env", "DEPLOYMENT_ENV_FILE"), ("flags", "DEPLOYMENT_FLAGS_FILE")):
        source = os.environ.get(variable)
        data = _read_object(pathlib.Path(source)) if source else {}
        (directory / (name + ".json")).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
