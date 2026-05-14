import sys
import os


def get_config_dir():
    if getattr(sys, 'frozen', False):
        base_dir = os.path.dirname(sys.executable)
    else:
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    config_dir = os.path.join(base_dir, 'config')
    os.makedirs(config_dir, exist_ok=True)
    return config_dir


def get_ai_config_path():
    return os.path.join(get_config_dir(), 'ai_config.json')


def normalize_ai_configs(config):
    """Return AI configs in failover order while keeping legacy ai_config.json compatible."""
    if not config:
        return []

    def _map_api_type(value):
        if isinstance(value, int):
            return {
                0: "openai", 1: "claude", 2: "azure", 3: "ollama",
                5: "openai", 6: "openai", 7: "openai", 8: "openai",
                9: "openai", 10: "openai", 12: "openai", 13: "openai", 14: "openai",
            }.get(value, "openai")
        if value == "anthropic":
            return "claude"
        if value == "local":
            return "ollama"
        return value or "openai"

    raw_models = config.get("models")
    if isinstance(raw_models, list) and raw_models:
        models = raw_models
    else:
        models = [config]

    normalized = []
    for index, item in enumerate(models):
        if not isinstance(item, dict):
            continue
        cfg = dict(config)
        cfg.update(item)
        cfg.pop("models", None)
        cfg["api_type"] = _map_api_type(cfg.get("api_type", "openai"))
        for key in ("api_key", "base_url", "model"):
            if key in cfg and isinstance(cfg[key], str):
                cfg[key] = cfg[key].replace("`", "").strip()
        cfg.setdefault("name", f"大模型{index + 1}")
        if cfg.get("enabled", True):
            normalized.append(cfg)
    return normalized
