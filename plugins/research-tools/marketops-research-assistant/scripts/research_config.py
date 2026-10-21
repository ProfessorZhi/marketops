#!/usr/bin/env python3
"""First-use runtime configuration: where research data lives, where the one
experience summary file lives.

Member-A answers two questions once; the answers persist in a versioned JSON under
the user's own config directory (or wherever ``MARKETOPS_RESEARCH_CONFIG``
points) and every later start reads them instead of asking again.

Resolution order, highest first — documented because tests and existing
deployments rely on it:

1. ``MARKETOPS_RESEARCH_DATA_ROOT`` env: an explicit per-process data root. Kept
   for existing test/deployment setups, and it wins over the stored config.
2. the stored config file (``data_root`` / ``experience_file``).
3. nothing: raise ``CONFIG_REQUIRED``. There is deliberately **no default** —
   silently writing a customer's research into the plugin package is the failure
   this module exists to prevent.

No network, no credentials, no provider calls.
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from pathlib import Path, PureWindowsPath

CONFIG_VERSION = 1
APP_DIR = "marketops-research"
CONFIG_FILE_NAME = "config.json"
CONFIG_ENV = "MARKETOPS_RESEARCH_CONFIG"
DATA_ROOT_ENV = "MARKETOPS_RESEARCH_DATA_ROOT"

#: Written as the first line of the summary file so a later run can tell our file
#: from a human's own Markdown before overwriting anything.
EXPERIENCE_MARKER = "<!-- marketops:research-experience"

#: The first-use interview. Exactly two questions, asked once.
QUESTIONS = (
    "研究数据要保存在哪个目录？（绝对路径）",
    "经验/卡点汇总文件要保存到哪个绝对路径，例如 Research_Experience.md？",
)

_WIN_DRIVE = re.compile(r"^[A-Za-z]:[\\/]")
_WIN_UNC = re.compile(r"^\\\\[^\\]+\\")
#: An unexpanded template a client substituted nowhere, e.g. "${MARKETOPS_...}".
_PLACEHOLDER = re.compile(r"^\$\{?[A-Za-z_][A-Za-z0-9_]*\}?$")


def is_unset_config_value(value: object) -> bool:
    """True for absent, blank, or an unreplaced ``${VAR}`` template.

    A connector form that leaves a field empty, or hands us the raw template
    because the variable was never substituted, must read as "not configured".
    Treating either as a path would create a literal ``${VAR}`` directory and
    silently put a customer's research somewhere nobody chose.

    An already-constructed ``Path`` is a caller-supplied value, never a template.
    """
    if value is None:
        return True
    if isinstance(value, Path):
        return not str(value).strip()
    if not isinstance(value, str):
        return True
    text = value.strip()
    return not text or bool(_PLACEHOLDER.match(text))


class ConfigError(ValueError):
    """A refusal with a stable reason code the caller can branch on."""

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def is_absolute_path(value: object, platform: str = sys.platform) -> bool:
    """Absolute on the *target* platform, not merely on the one running now.

    A Windows client's path must validate while the tests run on Linux, so the
    check is textual for win32 rather than delegated to ``Path``.
    """
    if not isinstance(value, str) or not value.strip():
        return False
    text = value.strip()
    if platform == "win32":
        return bool(_WIN_DRIVE.match(text) or _WIN_UNC.match(text))
    return text.startswith("/")


def config_path(env: dict | None = None, platform: str = sys.platform) -> Path:
    env = os.environ if env is None else env
    override = (env.get(CONFIG_ENV) or "").strip()
    if override:
        return Path(override)
    if platform == "win32":
        base = (env.get("APPDATA") or "").strip() or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / APP_DIR / CONFIG_FILE_NAME
    base = (env.get("XDG_CONFIG_HOME") or "").strip()
    if not base:
        base = str(Path((env.get("HOME") or "").strip() or Path.home()) / ".config")
    return Path(base) / APP_DIR / CONFIG_FILE_NAME


def load(env: dict | None = None, platform: str = sys.platform) -> dict | None:
    """The stored config, or None when nothing has been configured yet."""
    path = config_path(env, platform)
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ConfigError("CONFIG_FILE_UNREADABLE", f"{path}: {type(exc).__name__}") from None
    try:
        data = json.loads(raw)
    except ValueError:
        raise ConfigError("CONFIG_FILE_INVALID", f"{path} is not valid JSON") from None
    if not isinstance(data, dict) or data.get("version") != CONFIG_VERSION:
        raise ConfigError("CONFIG_VERSION_UNSUPPORTED",
                          f"{path} is not a version {CONFIG_VERSION} MarketOps config")
    for key in ("data_root", "experience_file"):
        if not isinstance(data.get(key), str) or not data[key].strip():
            raise ConfigError("CONFIG_FILE_INVALID", f"{path} is missing {key}")
    return data


def _sources(env: dict, platform: str) -> tuple[str | None, str | None]:
    env_root = env.get(DATA_ROOT_ENV)
    data_root_source = None if is_unset_config_value(env_root) else "env"
    experience_source = None
    try:
        stored = load(env, platform)
    except ConfigError:
        stored = None
    if stored:
        data_root_source = data_root_source or "config"
        experience_source = "config"
    return data_root_source, experience_source


def status(env: dict | None = None, platform: str = sys.platform,
           redact: bool = False) -> dict:
    """Read-only view for a first-run client. Never creates anything."""
    env = os.environ if env is None else env
    path = config_path(env, platform)
    try:
        stored = load(env, platform)
        invalid = None
    except ConfigError as exc:
        stored, invalid = None, exc.code
    env_root = env.get(DATA_ROOT_ENV)
    env_root_usable = not is_unset_config_value(env_root)
    data_root_source = "env" if env_root_usable else ("config" if stored else None)
    experience_source = "config" if stored else None
    data_root_value = (env_root.strip() if env_root_usable else None) or (stored or {}).get("data_root")
    experience_value = (stored or {}).get("experience_file")
    configured = bool(data_root_value and experience_value) and invalid is None
    result: dict = {
        "status": "CONFIGURED" if configured else "CONFIG_REQUIRED",
        "configured": configured,
        "config_file": _redact(str(path), env) if redact else str(path),
        "config_file_exists": path.is_file(),
        "data_root_source": data_root_source,
        "experience_file_source": experience_source,
        "questions": [] if configured else list(QUESTIONS),
        "mutates_nothing": True,
    }
    if invalid:
        result["invalid_reason"] = invalid
    if data_root_value:
        result["data_root"] = _redact(str(data_root_value), env) if redact else str(data_root_value)
    if experience_value:
        result["experience_file"] = (_redact(str(experience_value), env) if redact
                                     else str(experience_value))
    stored_root = (stored or {}).get("data_root")
    if env_root_usable and stored_root and not _same_location(env_root.strip(), stored_root, platform):
        # The env override moves new material but not the experience file, which
        # still comes from the stored config. A run then reads one root and
        # writes its summary under another installation's directory. Say so
        # instead of reporting a pair that looks consistent.
        result["data_root_mismatch"] = True
        result["stored_data_root"] = (_redact(str(stored_root), env) if redact
                                      else str(stored_root))
    return result


def _same_location(left: str, right: str, platform: str = sys.platform) -> bool:
    """Whether two configured paths name the same directory, without touching disk."""
    normalise = (lambda text: os.path.normpath(text).casefold()) if platform.startswith("win")         else (lambda text: os.path.normpath(text))
    return normalise(str(left)) == normalise(str(right))


def _redact(text: str, env: dict) -> str:
    # XDG_CONFIG_HOME is included so an agent-facing summary shows "~" rather than
    # whatever local layout this machine happens to use.
    for name in ("HOME", "USERPROFILE", "APPDATA", "XDG_CONFIG_HOME"):
        base = (env.get(name) or "").strip()
        if base and text.startswith(base):
            return "~" + text[len(base):]
    return text


def _inside_git_checkout(path: Path) -> bool:
    return any((part / ".git").exists() for part in (path, *path.parents))


def _inside_plugin_source(path: Path) -> bool:
    here = Path(__file__).resolve().parents[1]
    return path == here or here in path.parents or path in here.parents


def _validate_target(kind: str, value: str, platform: str):
    if not is_absolute_path(value, platform):
        raise ConfigError("PATH_NOT_ABSOLUTE",
                          f"{kind} must be an absolute path, got {value!r}")
    if platform == "win32" and sys.platform != "win32":
        # Do not resolve a Windows path with a POSIX Path: it cannot be checked
        # for existence on this host. Native Windows still runs all checks below.
        return Path(value.strip())
    resolved = Path(value.strip()).expanduser().resolve()
    if _inside_plugin_source(resolved):
        raise ConfigError("PATH_INSIDE_PLUGIN_SOURCE",
                          f"{kind} must not be inside the plugin package")
    if _inside_git_checkout(resolved):
        raise ConfigError("PATH_INSIDE_GIT_CHECKOUT",
                          f"{kind} must not be inside a Git checkout: {resolved}")
    return resolved


def _reject_foreign_existing_file(experience_file: Path) -> None:
    """Never overwrite a file that is not ours."""
    if not experience_file.is_file():
        return
    try:
        head = experience_file.read_text(encoding="utf-8", errors="replace")[:4_000]
    except OSError as exc:
        raise ConfigError("EXPERIENCE_FILE_UNREADABLE",
                          f"{experience_file}: {type(exc).__name__}") from None
    if not head.lstrip().startswith(EXPERIENCE_MARKER):
        raise ConfigError(
            "EXPERIENCE_FILE_NOT_MARKETOPS",
            f"{experience_file} exists and is not a MarketOps experience file; "
            "choose another path or move that file first")


def configure(data_root: str, experience_file: str, *, confirm: bool = False,
              env: dict | None = None, platform: str = sys.platform) -> dict:
    """Persist the two chosen paths. Atomic, and refuses to clobber."""
    env = os.environ if env is None else env
    data_path = _validate_target("data_root", data_root, platform)
    exp_path = _validate_target("experience_file", experience_file, platform)
    summary_inside = (PureWindowsPath(str(exp_path)).is_relative_to(PureWindowsPath(str(data_path)))
                      if platform == "win32" else exp_path.is_relative_to(data_path))
    if summary_inside:
        raise ConfigError("EXPERIENCE_FILE_INSIDE_DATA_ROOT",
                          "the experience file must be collectable on its own, "
                          "outside the research data directory")
    _reject_foreign_existing_file(exp_path)
    previous = load(env, platform)
    unchanged = bool(previous) and (previous["data_root"] == str(data_path)
                                    and previous["experience_file"] == str(exp_path))
    if previous and not unchanged and not confirm:
        raise ConfigError(
            "RECONFIGURE_REQUIRES_CONFIRMATION",
            "a configuration already exists; changing the paths needs explicit confirmation")
    path = config_path(env, platform)
    payload = {"version": CONFIG_VERSION, "data_root": str(data_path),
               "experience_file": str(exp_path), "app": APP_DIR}
    _atomic_write(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return {"status": "CONFIGURED", "unchanged": unchanged,
            "config_file": str(path), "data_root": str(data_path),
            "experience_file": str(exp_path),
            "env_data_root_override": bool((env.get(DATA_ROOT_ENV) or "").strip())}


UPSTREAM_PROXY_ENV = "MARKETOPS_EGRESS_UPSTREAM_PROXY"


def set_upstream_proxy(value: str, env: dict | None = None,
                       platform: str = sys.platform) -> dict:
    """Store (or clear, with "") the http://host:port proxy research traffic goes through."""
    env = os.environ if env is None else env
    data = load(env, platform)
    if data is None:
        raise ConfigError("CONFIG_REQUIRED", "configure the data paths first")
    value = str(value or "").strip()
    if value and not re.fullmatch(r"http://[A-Za-z0-9.\-]+:\d{1,5}/?", value):
        raise ConfigError("UPSTREAM_PROXY_INVALID", "use http://host:port without a password")
    if value:
        data["upstream_proxy"] = value.rstrip("/")
    else:
        data.pop("upstream_proxy", None)
    _atomic_write(config_path(env, platform), json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    return {"status": "SAVED", "upstream_proxy": data.get("upstream_proxy", "")}


def load_upstream_proxy_into_environment(env: dict | None = None,
                                         platform: str = sys.platform) -> str:
    """An explicit environment value wins; otherwise the stored one, if any."""
    env = os.environ if env is None else env
    if (env.get(UPSTREAM_PROXY_ENV) or "").strip():
        return "ENVIRONMENT"
    try:
        data = load(env, platform)
    except ConfigError:
        return "CONFIG_UNREADABLE"
    if data and data.get("upstream_proxy"):
        env[UPSTREAM_PROXY_ENV] = data["upstream_proxy"]
        return "CONFIG"
    return "DIRECT"


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    handle = tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                                         prefix=".config-", delete=False)
    try:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
        handle.close()
        os.chmod(handle.name, 0o600)
        os.replace(handle.name, path)
    except BaseException:
        handle.close()
        if os.path.exists(handle.name):
            os.unlink(handle.name)
        raise


def data_root(env: dict | None = None, platform: str = sys.platform) -> Path:
    """Resolve the research data root, or refuse with CONFIG_REQUIRED."""
    env = os.environ if env is None else env
    explicit = env.get(DATA_ROOT_ENV)
    if not is_unset_config_value(explicit):
        return Path(explicit.strip()).expanduser().resolve()
    stored = load(env, platform)
    if stored:
        return Path(stored["data_root"]).expanduser().resolve()
    raise ConfigError(
        "CONFIG_REQUIRED",
        "研究数据目录尚未配置；请先回答两个问题（" + " / ".join(QUESTIONS) + "）")


def experience_file(env: dict | None = None, platform: str = sys.platform) -> Path:
    """Resolve the one summary file the user collects, or refuse."""
    env = os.environ if env is None else env
    stored = load(env, platform)
    if stored:
        return Path(stored["experience_file"]).expanduser().resolve()
    raise ConfigError(
        "CONFIG_REQUIRED",
        "经验汇总文件位置尚未配置；请先回答两个问题（" + " / ".join(QUESTIONS) + "）")


def status_or_refusal(env: dict | None = None, platform: str = sys.platform) -> dict:
    """The read-only status without ever raising, for a first-run MCP client."""
    try:
        return status(env=env, platform=platform)
    except ConfigError as exc:  # pragma: no cover - status() already catches these
        return {"status": "CONFIG_REQUIRED", "configured": False,
                "invalid_reason": exc.code, "questions": list(QUESTIONS)}


if __name__ == "__main__":  # local convenience; the MCP/Skill path is primary
    print(json.dumps(status(), ensure_ascii=False, indent=2))
