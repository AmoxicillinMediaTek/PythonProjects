#!/usr/bin/env python3
"""
orca.py - a ChatGPT-style terminal AI for Termux that can run shell commands.
Works with OpenRouter (default) or any custom OpenAI-compatible endpoint.
Custom endpoints are saved to ~/.config/orca/endpoint and reused on the next run.

Run it with: python orca.py
At startup you pick a provider:
  1) OpenRouter (default) - asks for an OpenRouter API key.
  2) CustomEndpoints      - type "<endpointurl> <apikey> <modeloptional>".
On first run it installs what it needs automatically.
"""
import os
import sys
import json
import shutil
import subprocess


def _ensure_requests():
    """Install the one third-party dependency ('requests') automatically if it is missing."""
    try:
        import requests  # noqa: F401
        return
    except ImportError:
        pass
    print("First run: installing required package 'requests' (one time only)...")
    pip = [sys.executable, "-m", "pip"]
    if subprocess.run(pip + ["--version"], capture_output=True).returncode != 0:
        subprocess.run([sys.executable, "-m", "ensurepip", "--upgrade"], capture_output=True)
        if subprocess.run(pip + ["--version"], capture_output=True).returncode != 0:
            print("pip is missing. In Termux run: pkg install python-pip -y then start again.")
            sys.exit(1)
    cmd = pip + ["install", "-q", "--disable-pip-version-check", "requests"]
    if subprocess.run(cmd).returncode != 0:
        # some systems mark Python as "externally managed"
        if subprocess.run(cmd + ["--break-system-packages"]).returncode != 0:
            print("Could not install 'requests'. Check your internet, then run: pip install requests")
            sys.exit(1)
    print("Done.\n")


_ensure_requests()
import requests  # noqa: E402

BASE_URL = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
DEFAULT_KEY = ""  # optional: paste a key here for personal use. Do NOT ship your own key to other people.
KEY_FILE = os.path.expanduser("~/.config/orca/key")
ENDPOINT_FILE = os.path.expanduser("~/.config/orca/endpoint")
API_KEY = ""
MODEL = os.environ.get("OPENROUTER_MODEL", "openrouter/free")
PROVIDER = "openrouter"  # "openrouter" | "custom"
THINK = True          # ask the model to reason, and show its thoughts
EFFORT = "medium"     # reasoning effort: low | medium | high
STORAGE = "/storage/emulated/0"  # Android internal storage
CMD_TIMEOUT = 60      # seconds per command
MAX_OUTPUT = 4000     # chars of command output sent back to the model
MAX_FILE = 12000      # chars of a file sent back to the model

SYSTEM_PROMPT = (
    "You are a helpful AI assistant running inside Termux on an Android phone. "
    "You can run shell commands with the run_command tool. Use it when you need "
    "to inspect files, install packages (pkg/pip), run scripts, or check the system. "
    "Prefer short, safe commands. Explain results briefly. "
    "Termux is minimal: `which` is not installed, so use `command -v NAME` to check "
    "whether a program exists. There is no sudo or apt; use `pkg install` instead. "
    "If a command fails with exit code 127, it means the program is not installed. "
    "STORAGE: the phone's internal storage is at /storage/emulated/0 (also reachable as "
    "~/storage/shared). Common folders there: Download, Documents, DCIM, Pictures, Music, "
    "Movies, Android. Use `ls -la /storage/emulated/0` to explore it. If you get "
    "'Permission denied', tell the user to run /storage to grant access. "
    "FILES: to look inside a file use the read_file tool (or `cat FILE`, `head`, `tail`, `ls -la`). "
    "To create or overwrite a file use the write_file tool (set append=true to add to the end). "
    "You can also write files from the shell with echo (`echo 'text' > f.txt`, `>>` to append) "
    "or a heredoc: `cat > file.py << 'EOF'` ... `EOF`. Always read a file before editing it, "
    "and after writing, read it back or run it to verify. "
    "FORMATTING: your replies are shown in a plain-text terminal that does not render markdown. "
    "Never use markdown: no asterisks for bold or italics, no # headers, no backticks or code fences, "
    "no markdown tables, and no markdown links. Write plain sentences. For lists use simple lines "
    "starting with a hyphen or a number. Write commands and file paths as plain text."
)

TOOLS = [{
    "type": "function",
    "function": {
        "name": "run_command",
        "description": "Run a shell command in Termux and return stdout, stderr and exit code.",
        "parameters": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "The shell command to run."}
            },
            "required": ["command"],
        },
    },
}, {
    "type": "function",
    "function": {
        "name": "read_file",
        "description": "Read a text file and return its contents (like cat), with line numbers.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to the file (~ is allowed)."}
            },
            "required": ["path"],
        },
    },
}, {
    "type": "function",
    "function": {
        "name": "write_file",
        "description": "Create or overwrite a text file with the given content (like cat > file << EOF). "
                       "Set append=true to add to the end instead. Parent folders are created automatically.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to the file (~ is allowed)."},
                "content": {"type": "string", "description": "Full text to write."},
                "append": {"type": "boolean", "description": "Append instead of overwrite. Default false."},
            },
            "required": ["path", "content"],
        },
    },
}]

# ANSI colors
C = "\033[36m"; G = "\033[32m"; Y = "\033[33m"; R = "\033[31m"; D = "\033[2m"; M = "\033[35m"; X = "\033[0m"


class BadKey(Exception):
    """Raised when OpenRouter rejects the API key."""


def load_key() -> str:
    """Key order: saved key file, environment variable, DEFAULT_KEY."""
    try:
        with open(KEY_FILE) as f:
            key = f.read().strip()
        if key:
            return key
    except OSError:
        pass
    return os.environ.get("OPENROUTER_API_KEY", "").strip() or DEFAULT_KEY.strip()


def save_key(key: str):
    try:
        os.makedirs(os.path.dirname(KEY_FILE), exist_ok=True)
        with open(KEY_FILE, "w") as f:
            f.write(key)
        os.chmod(KEY_FILE, 0o600)
    except OSError as e:
        print(f"{Y}Could not save the key ({e}); you will be asked again next time.{X}")


def forget_key():
    try:
        os.remove(KEY_FILE)
    except OSError:
        pass


def validate_key(key: str):
    """Ask OpenRouter if the key works.
    Returns (True, '') if valid, (False, reason) if rejected, (None, reason) if it could not be checked."""
    try:
        r = requests.get(f"{BASE_URL}/key", headers={"Authorization": f"Bearer {key}"}, timeout=15)
    except requests.exceptions.RequestException:
        return None, "could not reach OpenRouter (check your internet connection)"
    if r.status_code == 200:
        return True, ""
    if r.status_code in (401, 403):
        return False, "OpenRouter says this key is invalid or was deleted"
    return None, f"unexpected reply from OpenRouter (HTTP {r.status_code})"


def ask_key(first_time=True):
    """Ask for an API key until a working one is entered, then save it.
    Returns the key, or '' if the user cancels (empty input)."""
    import getpass
    if first_time:
        print(f"{C}Welcome!{X} This tool needs an OpenRouter API key.")
        print(f"{D}Get a free one at https://openrouter.ai/keys (it starts with sk-or-).{X}")
    print(f"{D}Paste it and press Enter. Nothing shows while you type or paste - that is normal. "
          f"Press Enter on an empty line to cancel.{X}")
    while True:
        try:
            key = getpass.getpass("API key: ").strip()
        except EOFError:
            return ""
        if not key:
            return ""
        print(f"{D}checking key...{X}", end="\r", flush=True)
        ok, why = validate_key(key)
        print("\033[K", end="", flush=True)
        if ok:
            save_key(key)
            print(f"{G}API key is correct and saved.{X}\n")
            return key
        if ok is False:
            print(f"{R}Wrong API key: {why}. Try again.{X}")
        else:
            print(f"{R}Could not verify the key: {why}. Try again.{X}")


def get_valid_key():
    """Use the saved key if it works; otherwise ask for a new one (and save it automatically)."""
    key = load_key()
    if key:
        print(f"{D}checking API key...{X}", end="\r", flush=True)
        ok, why = validate_key(key)
        print("\033[K", end="", flush=True)
        if ok:
            return key
        if ok is None:
            print(f"{Y}Could not verify your key ({why}). Continuing anyway.{X}")
            return key
        print(f"{Y}Your saved API key no longer works ({why}).{X}")
        forget_key()
    return ask_key(first_time=not key)


def parse_custom_endpoint(line: str):
    """Parse '<endpointurl> <apikey> [model]'.
    Returns (url, key, model). Raises ValueError with a short reason on bad input."""
    parts = line.split()
    if len(parts) < 2:
        raise ValueError("need at least <endpointurl> and <apikey>")
    url = parts[0].strip().rstrip("/")
    if not (url.startswith("http://") or url.startswith("https://")):
        raise ValueError("endpointurl must start with http:// or https://")
    key = parts[1].strip()
    if not key:
        raise ValueError("apikey is empty")
    model = parts[2].strip() if len(parts) > 2 else ""
    return url, key, model


def save_endpoint(line: str):
    """Persist a custom endpoint line to ~/.config/orca/endpoint."""
    try:
        os.makedirs(os.path.dirname(ENDPOINT_FILE), exist_ok=True)
        with open(ENDPOINT_FILE, "w") as f:
            f.write(line.strip() + "\n")
        os.chmod(ENDPOINT_FILE, 0o600)
    except OSError as e:
        print(f"{Y}Could not save the endpoint ({e}).{X}")


def load_endpoint():
    """Read the saved endpoint file.
    Returns {'url','key','model','line'} or None if there is nothing usable."""
    try:
        with open(ENDPOINT_FILE) as f:
            text = f.read()
    except OSError:
        return None
    line = "\n".join(l for l in text.splitlines()
                     if l.strip() and not l.strip().startswith("#")).strip()
    if not line:
        return None
    try:
        url, key, model = parse_custom_endpoint(line)
    except ValueError:
        return None
    return {"url": url, "key": key, "model": model, "line": line}


def forget_endpoint():
    try:
        os.remove(ENDPOINT_FILE)
    except OSError:
        pass


def apply_endpoint_dict(info: dict):
    """Apply an already-parsed endpoint dict and switch to the custom provider."""
    globals()["BASE_URL"] = info["url"]
    globals()["API_KEY"] = info["key"]
    globals()["PROVIDER"] = "custom"
    if info.get("model"):
        globals()["MODEL"] = info["model"]


def apply_custom_endpoint(line: str) -> bool:
    """Parse and apply a custom endpoint line. Returns True on success."""
    try:
        url, key, model = parse_custom_endpoint(line)
    except ValueError as e:
        print(f"{R}Invalid input: {e}.{X}")
        return False
    apply_endpoint_dict({"url": url, "key": key, "model": model})
    save_endpoint(line)
    print(f"{G}Custom endpoint set and saved.{X}")
    print(f"{D}endpoint: {url}{X}")
    print(f"{D}model: {MODEL}{X}")
    return True


def setup_custom_endpoint(initial: str = ""):
    """Show a text box and let the user type '<endpointurl> <apikey> <modeloptional>'.
    Returns a dict on success, or None if the user cancels (empty line)."""
    if initial and apply_custom_endpoint(initial):
        return {"url": BASE_URL, "key": API_KEY, "model": MODEL}
    print(f"{C}Custom endpoint{X} {D}(OpenAI-compatible base URL, e.g. https://host/v1){X}")
    print(f"{D}Format: <endpointurl> <apikey> <modeloptional>{X}")
    print(f"{D}Press Enter on an empty line to cancel and use OpenRouter instead.{X}")
    while True:
        try:
            line = input(f"{C}endpoint>{X} ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return None
        if not line:
            return None
        if apply_custom_endpoint(line):
            return {"url": BASE_URL, "key": API_KEY, "model": MODEL}


def choose_provider():
    """Ask which provider to use at startup (or on /provider).
    Returns a dict for a custom endpoint, or None for the OpenRouter default."""
    print(f"{C}Choose provider{X}")
    print(f"  {G}1{X}) OpenRouter {D}(default) - requires an OpenRouter API key{X}")
    saved = load_endpoint()
    if saved:
        print(f"  {G}2{X}) CustomEndpoints {D}- saved: {saved['url']}{X}")
    else:
        print(f"  {G}2{X}) CustomEndpoints {D}- your own OpenAI-compatible endpoint{X}")
    try:
        choice = input(f"{D}select [1/2, Enter = 1]:{X} ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        choice = "1"
    if choice == "2":
        if saved:
            apply_endpoint_dict(saved)
            print(f"{G}Using your saved custom endpoint.{X}")
            print(f"{D}endpoint: {BASE_URL} | model: {MODEL}{X}")
            print(f"{D}type /endpoint to change it{X}")
            return saved
        result = setup_custom_endpoint()
        if result is not None:
            return result
        # user cancelled -> fall back to OpenRouter
        globals()["PROVIDER"] = "openrouter"
        print(f"{D}using OpenRouter instead{X}")
        return None
    globals()["PROVIDER"] = "openrouter"
    return None


def _read_first_line(path: str) -> str:
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return ""


def open_in_editor(path: str) -> bool:
    """Open `path` in nano (or $EDITOR / vi). Returns True if the editor ran."""
    editor = os.environ.get("EDITOR", "").strip() or "nano"
    if shutil.which(editor) is None:
        editor = ""
        for alt in ("nano", "vi", "vim"):
            if shutil.which(alt):
                editor = alt
                break
        if not editor:
            print(f"{Y}No text editor found (nano/vi). Edit it manually: {path}{X}")
            return False
    print(f"{D}opening {editor} {path} ... (save and quit to come back){X}")
    try:
        rc = subprocess.call([editor, path])
    except OSError as e:
        print(f"{R}Could not run {editor}: {e}{X}")
        return False
    print()
    return rc == 0


def edit_key_file():
    """Open the API-key file in an editor, then reload it."""
    os.makedirs(os.path.dirname(KEY_FILE), exist_ok=True)
    if not os.path.exists(KEY_FILE):
        seed = API_KEY or load_key()
        try:
            with open(KEY_FILE, "w") as f:
                f.write(seed)
            os.chmod(KEY_FILE, 0o600)
        except OSError as e:
            print(f"{R}Could not create {KEY_FILE}: {e}{X}")
            return
    if not open_in_editor(KEY_FILE):
        return
    new = _read_first_line(KEY_FILE)
    if not new:
        print(f"{Y}Key file is empty - keeping your current key.{X}")
        return
    globals()["API_KEY"] = new
    if PROVIDER == "openrouter":
        print(f"{D}checking key...{X}", end="\r", flush=True)
        ok, why = validate_key(new)
        print("\033[K", end="", flush=True)
        if ok:
            print(f"{G}API key updated and it works.{X}")
        elif ok is False:
            print(f"{R}Saved, but OpenRouter rejects it: {why}.{X}")
        else:
            print(f"{Y}Saved, but could not verify: {why}.{X}")
    else:
        print(f"{G}API key updated.{X}")


def edit_endpoint_file():
    """Open the endpoint file in an editor, then reload it."""
    os.makedirs(os.path.dirname(ENDPOINT_FILE), exist_ok=True)
    if not os.path.exists(ENDPOINT_FILE):
        if PROVIDER == "custom" and API_KEY:
            seed = f"{BASE_URL} {API_KEY}" + (f" {MODEL}" if MODEL else "")
        else:
            seed = ("# <endpointurl> <apikey> <modeloptional>\n"
                    "# e.g. https://host/v1 sk-abc gpt-4o\n")
        try:
            with open(ENDPOINT_FILE, "w") as f:
                f.write(seed if seed.endswith("\n") else seed + "\n")
            os.chmod(ENDPOINT_FILE, 0o600)
        except OSError as e:
            print(f"{R}Could not create {ENDPOINT_FILE}: {e}{X}")
            return
    if not open_in_editor(ENDPOINT_FILE):
        return
    info = load_endpoint()
    if not info:
        print(f"{Y}Nothing usable in {ENDPOINT_FILE} - keeping your current endpoint.{X}")
        print(f"{D}Format: <endpointurl> <apikey> <modeloptional>{X}")
        return
    apply_endpoint_dict(info)
    save_endpoint(info["line"])
    print(f"{G}Custom endpoint updated.{X}")
    print(f"{D}endpoint: {BASE_URL} | model: {MODEL}{X}")


def change_credentials():
    """Menu for /changeapikey: edit either the API key or the endpoint file in nano."""
    print(f"{C}Change what?{X}")
    print(f"  {G}1{X}) API key   {D}({KEY_FILE}){X}")
    print(f"  {G}2{X}) Endpoint  {D}({ENDPOINT_FILE}){X}")
    try:
        choice = input(f"{D}select [1/2, Enter = cancel]:{X} ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return
    if choice == "1":
        edit_key_file()
    elif choice == "2":
        edit_endpoint_file()
    else:
        print(f"{D}cancelled{X}")


def read_file(path: str) -> str:
    path = os.path.expanduser(path)
    try:
        if os.path.isdir(path):
            return f"{path} is a folder. Use run_command with `ls -la {path}` to list it."
        with open(path, "rb") as f:
            raw = f.read(MAX_FILE * 4 + 1)
        size = os.path.getsize(path)
    except PermissionError:
        return (f"Permission denied: {path}. If this is in internal storage, the user must "
                f"run /storage (termux-setup-storage) and allow access.")
    except Exception as e:
        return f"Error reading {path}: {e}"
    if b"\x00" in raw[:2048]:
        return (f"{path} is a binary file ({size} bytes), not readable as text. "
                f"Use `file`, `ls -lh`, or a suitable tool via run_command instead.")
    text = raw.decode("utf-8", errors="replace")
    lines = text.splitlines()
    out = "\n".join(f"{i + 1}\t{line}" for i, line in enumerate(lines))
    if len(out) > MAX_FILE:
        out = out[:MAX_FILE] + f"\n...[truncated, file has {len(lines)} lines]"
    return out if out else "(empty file)"


def write_file(path: str, content: str, append: bool = False) -> str:
    path = os.path.expanduser(path)
    try:
        folder = os.path.dirname(path)
        if folder:
            os.makedirs(folder, exist_ok=True)
        with open(path, "a" if append else "w") as f:
            f.write(content)
    except Exception as e:
        return f"Error writing {path}: {e}"
    return f"{'Appended' if append else 'Wrote'} {len(content)} characters to {path}"


def run_command(cmd: str) -> str:
    try:
        p = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=CMD_TIMEOUT)
        out = f"exit_code: {p.returncode}\nstdout:\n{p.stdout}\nstderr:\n{p.stderr}"
    except subprocess.TimeoutExpired:
        out = f"Command timed out after {CMD_TIMEOUT}s."
    except Exception as e:
        out = f"Error running command: {e}"
    if len(out) > MAX_OUTPUT:
        out = out[:MAX_OUTPUT] + "\n...[output truncated]"
    return out


COMMON_FOLDERS = {"Download", "Documents", "DCIM", "Pictures", "Music", "Movies"}


def find_storage():
    """Return a path that really shows the phone's internal storage (not just Termux's app folder)."""
    for path in (STORAGE, os.path.expanduser("~/storage/shared"), "/sdcard"):
        try:
            names = set(os.listdir(path))
        except Exception:
            continue
        if names & COMMON_FOLDERS:
            return path
    return None


def check_storage(request=False):
    """Check read access to internal storage; optionally trigger the Termux permission dialog."""
    if request:
        try:
            subprocess.run(["termux-setup-storage"], timeout=60)
        except FileNotFoundError:
            return "termux-setup-storage not found. Update Termux: pkg install termux-tools"
        except Exception as e:
            return f"Could not request storage permission: {e}"
    path = find_storage()
    return f"internal storage OK: {path}" if path else None


def storage_diagnosis():
    """Explain what Termux can currently see, to help debug permission problems."""
    lines = []
    for path in (STORAGE, os.path.expanduser("~/storage/shared")):
        try:
            names = sorted(os.listdir(path))
            lines.append(f"{path} -> {len(names)} items: {', '.join(names[:12]) or '(empty)'}")
        except Exception as e:
            lines.append(f"{path} -> cannot read ({type(e).__name__})")
    return "\n".join(lines)


def note_storage(messages, path):
    """Tell the AI which path really works for internal storage."""
    messages[0]["content"] += (f" The verified working path for internal storage on this device is {path} "
                               f"(use it for Download, DCIM, Documents, etc.).")


HELP = f"""{C}Commands{X}
/help            show this list
/clear           reset the conversation
/auto            toggle auto-run (ON = commands run without asking)
/think           turn reasoning on/off
/effort LEVEL    set reasoning effort: low | medium | high
/model NAME      switch model (e.g. /model openrouter/free)
/provider        choose provider: 1) OpenRouter  2) CustomEndpoints
/endpoint        set a custom endpoint (or type it on the same line)
                 format: <endpointurl> <apikey> <modeloptional>
                 saved to ~/.config/orca/endpoint and reused next time
/storage         grant/check access to internal storage (/storage/emulated/0)
/changeapikey    edit your key or endpoint file in nano (~/.config/orca/)
/status          show current provider, model and settings
/exit or /quit   leave

{C}Tools the AI can use{X}
run_command      any shell command (echo, cat, heredoc <<EOF, ls, python, pkg ...)
read_file        read a file's contents with line numbers
write_file       create / overwrite / append to a file

{D}Anything else you type is sent to the AI. It can run shell commands
on its own to get things done. Ctrl+C cancels a request.{X}"""


def chat(messages):
    """Stream a response from OpenRouter.

    Reasoning tokens are printed live as they arrive in dim purple.
    The final assembled message dict is returned (no 'reasoning' key — already printed).
    """
    payload = {
        "model": MODEL,
        "messages": messages,
        "tools": TOOLS,
        "stream": True,
    }
    if THINK:
        payload["reasoning"] = {"effort": EFFORT}

    r = requests.post(
        f"{BASE_URL}/chat/completions",
        headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
        json=payload,
        stream=True,
        timeout=(10, 90),
    )
    if r.status_code in (401, 403):
        raise BadKey()
    if r.status_code == 402:
        raise RuntimeError("OpenRouter says you are out of credits for this model. "
                           "Try a free model with /model openrouter/free or add credits.")
    if r.status_code == 429:
        raise RuntimeError("Rate limited (429). Free models are busy - wait a bit or try /model with another one.")
    if r.status_code != 200:
        raise RuntimeError(f"API error {r.status_code}: {r.text[:500]}")

    # ── accumulators ───────────────────────────────────────────────────────
    content_buf = []        # reply text
    tool_calls_raw = {}     # index -> {id, name, arguments}
    in_thinking = False     # are we currently printing reasoning tokens?

    for raw_line in r.iter_lines():
        if not raw_line:
            continue
        line = raw_line.decode("utf-8", errors="replace")
        if not line.startswith("data:"):
            continue
        data_str = line[5:].strip()
        if data_str == "[DONE]":
            break
        try:
            chunk = json.loads(data_str)
        except json.JSONDecodeError:
            continue

        choice = (chunk.get("choices") or [{}])[0]
        delta = choice.get("delta", {})

        # ── reasoning / thinking tokens ───────────────────────────────────
        reason_chunk = delta.get("reasoning") or ""

        # also handle 'reasoning_details' list style
        if not reason_chunk:
            for rd in delta.get("reasoning_details") or []:
                reason_chunk += rd.get("text") or rd.get("summary") or ""

        if THINK and reason_chunk:
            if not in_thinking:
                print(f"\n{M}thinking:{X} ", end="", flush=True)
                in_thinking = True
            print(f"{D}{reason_chunk}{X}", end="", flush=True)

        # ── reply text ────────────────────────────────────────────────────
        text_chunk = delta.get("content") or ""
        if text_chunk:
            if in_thinking:
                print()  # newline after reasoning block
                in_thinking = False
            content_buf.append(text_chunk)

        # ── tool call fragments ───────────────────────────────────────────
        for tc in delta.get("tool_calls") or []:
            idx = tc.get("index", 0)
            if idx not in tool_calls_raw:
                tool_calls_raw[idx] = {"id": "", "name": "", "arguments": ""}
            if tc.get("id"):
                tool_calls_raw[idx]["id"] += tc["id"]
            fn = tc.get("function") or {}
            if fn.get("name"):
                tool_calls_raw[idx]["name"] += fn["name"]
            if fn.get("arguments"):
                tool_calls_raw[idx]["arguments"] += fn["arguments"]

    if in_thinking:
        print()  # ensure newline after last reasoning token

    # ── assemble message dict ──────────────────────────────────────────────
    msg = {"role": "assistant", "content": "".join(content_buf) or None}
    if tool_calls_raw:
        msg["tool_calls"] = [
            {
                "id": v["id"],
                "type": "function",
                "function": {"name": v["name"], "arguments": v["arguments"]},
            }
            for v in sorted(tool_calls_raw.values(), key=lambda x: list(tool_calls_raw.values()).index(x))
        ]
    return msg


def main():
    globals()["PROVIDER"] = "openrouter"
    choose_provider()
    if PROVIDER == "custom":
        # custom endpoint already supplied its own key; no OpenRouter validation.
        if not API_KEY:
            print(f"{R}No API key entered. Run the program again when you have one.{X}")
            sys.exit(1)
    else:
        globals()["API_KEY"] = get_valid_key()
        if not API_KEY:
            print(f"{R}No API key entered. Run the program again when you have one.{X}")
            sys.exit(1)

    auto_run = True  # commands run automatically; use /auto to ask for confirmation instead
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    provider_label = f"custom ({BASE_URL})" if PROVIDER == "custom" else "OpenRouter"
    print(f"{C}Termux AI{X} provider: {provider_label} | model: {MODEL}")
    st = check_storage()
    if st:
        note_storage(messages, find_storage())
    print(f"{G}{st}{X}" if st else f"{Y}internal storage not accessible yet - type /storage to grant access{X}")
    print(f"{D}type /help for all commands{X}\n")

    while True:
        try:
            user = input(f"{G}you>{X} ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not user:
            continue
        if user in ("/exit", "/quit"):
            break
        if user in ("/help", "/?", "help"):
            print(HELP)
            continue
        if user == "/storage":
            res = check_storage(request=True)
            if res:
                print(f"{G}{res}{X}")
                note_storage(messages, find_storage())
            else:
                print(f"{Y}Termux can only see its own app folder, not your full internal storage.{X}")
                print(f"{D}{storage_diagnosis()}{X}")
                print(f"{Y}Fix: Android Settings > Apps > Termux > Permissions > Files and media "
                      f"(or Storage) > Allow. On Android 11+ also enable 'All files access' if offered. "
                      f"Then run /storage again. (Termux from the Play Store is broken - use F-Droid "
                      f"or GitHub.){X}")
            continue
        if user in ("/changeapikey", "/key"):
            change_credentials()
            continue
        if user == "/provider":
            choose_provider()
            if PROVIDER == "custom":
                print(f"{G}provider: custom ({BASE_URL}){X}")
            else:
                print(f"{G}provider: OpenRouter{X}")
            continue
        if user.startswith("/endpoint"):
            arg = user.split(" ", 1)[1].strip() if " " in user else ""
            res = setup_custom_endpoint(arg) if arg else setup_custom_endpoint()
            if res is None and not arg:
                print(f"{D}cancelled{X}")
            continue
        if user == "/status":
            provider_label = f"custom ({BASE_URL})" if PROVIDER == "custom" else "OpenRouter"
            print(f"{D}provider: {provider_label} | model: {MODEL} | "
                  f"reasoning: {'on (' + EFFORT + ')' if THINK else 'off'} | "
                  f"auto-run: {'on' if auto_run else 'off'}{X}")
            continue
        if user == "/clear":
            messages = messages[:1]
            print(f"{D}chat cleared{X}")
            continue
        if user == "/auto":
            auto_run = not auto_run
            print(f"{Y}auto-run commands: {'ON (no confirmation!)' if auto_run else 'OFF'}{X}")
            continue
        if user == "/think":
            globals()["THINK"] = not THINK
            print(f"{M}reasoning: {'ON (effort: ' + EFFORT + ')' if THINK else 'OFF'}{X}")
            continue
        if user.startswith("/effort"):
            lvl = user.split(" ", 1)[1].strip().lower() if " " in user else ""
            if lvl in ("low", "medium", "high"):
                globals()["EFFORT"] = lvl
                globals()["THINK"] = True
                print(f"{M}reasoning effort: {lvl}{X}")
            else:
                print(f"{D}usage: /effort low|medium|high{X}")
            continue
        if user.startswith("/model "):
            globals()["MODEL"] = user.split(" ", 1)[1].strip()
            print(f"{D}model set to {MODEL}{X}")
            continue

        turn_start = len(messages)
        messages.append({"role": "user", "content": user})

        try:
            while True:
                msg = chat(messages)
                messages.append(msg)  # reasoning already displayed live during streaming

                if msg.get("content"):
                    print(f"\n{C}ai>{X} {msg['content']}\n")

                calls = msg.get("tool_calls")
                if not calls:
                    break

                for call in calls:
                    try:
                        args = json.loads(call["function"]["arguments"] or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    name = call["function"]["name"]

                    if name == "read_file":
                        label = f"cat {args.get('path', '')}"
                    elif name == "write_file":
                        mode = ">>" if args.get("append") else ">"
                        label = f"write {mode} {args.get('path', '')} ({len(args.get('content', ''))} chars)"
                    else:
                        label = f"$ {args.get('command', '')}"
                    print(f"{Y}{label}{X}")

                    if auto_run:
                        ok = True
                    else:
                        ans = input(f"{Y}allow this? [y/N/a=always]:{X} ").strip().lower()
                        if ans == "a":
                            auto_run = True
                        ok = ans in ("y", "a")

                    if not ok:
                        result = "User declined this action."
                    elif name == "read_file":
                        result = read_file(args.get("path", ""))
                    elif name == "write_file":
                        result = write_file(args.get("path", ""), args.get("content", ""),
                                            bool(args.get("append", False)))
                    elif name == "run_command":
                        result = run_command(args.get("command", ""))
                    else:
                        result = f"Unknown tool: {name}"

                    # Show command output prominently
                    shown = result if len(result) < 2000 else result[:2000] + "\n...[more hidden]"
                    cmd = args.get("command", "")
                    if name == "run_command":
                        # run_command prepends 'exit_code: N' on the first line
                        exit_code = "?"
                        first = shown.split("\n", 1)[0] if shown else ""
                        if first.startswith("exit_code:"):
                            exit_code = first.split(":", 1)[1].strip()
                        print(f"{G}=== Command ==={X}")
                        print(f"{G}> {cmd}{X}")
                        print(f"{D}--- Output (exit code: {exit_code}) ---{X}")
                        print(f"{D}{shown}{X}")
                        print(f"{D}=== End command ==={X}\n")
                    else:
                        print(f"{D}{shown}{X}")
                    messages.append({
                        "role": "tool",
                        "tool_call_id": call["id"],
                        "content": result,
                    })
        except BadKey:
            del messages[turn_start:]
            print(f"{R}OpenRouter rejected your API key (it may have been deleted or revoked).{X}")
            new = ask_key(first_time=False)
            if new:
                globals()["API_KEY"] = new
                print(f"{G}Send your message again.{X}")
        except KeyboardInterrupt:
            del messages[turn_start:]
            print(f"\n{Y}cancelled{X}")
        except requests.exceptions.Timeout:
            del messages[turn_start:]
            print(f"{R}Request timed out. The free model is slow - try again or use /model.{X}")
        except Exception as e:
            del messages[turn_start:]
            print(f"{R}{e}{X}")


if __name__ == "__main__":
    main()
