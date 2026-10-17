# PythonProjects

A collection of open-source Python projects.

Current project:

- **Orca** — a ChatGPT-style AI assistant that lives in your terminal and can run shell commands for you. Built for Termux on Android, but works anywhere Python runs.

---

## Orca (`orca.py`)

Orca is a single-file AI chat client for the terminal. You talk to it in plain English, and it can use real tools — running shell commands, reading files, and writing files — to actually get things done instead of just answering questions.

It was designed for **Termux on Android**, so it can inspect your phone, install packages, and manage files in internal storage. But it is just a Python script with one dependency (`requests`), so it runs on any Linux, macOS, or Windows terminal too.

### Features

- **Streaming replies** — text prints live as the model produces it.
- **Reasoning display** — when supported, the model's "thinking" tokens are streamed in dim text so you can watch it plan. Toggle with `/think`, tune with `/effort`.
- **Tool use** — the model can run shell commands, read files, and write files on its own to complete a task.
- **Two providers** — [OpenRouter](https://openrouter.ai) by default, or any OpenAI-compatible endpoint you supply.
- **Auto-run with a safety switch** — commands run without confirmation by default, or flip to a prompt that asks before every action (`/auto`).
- **Zero-setup dependency install** — on first run it installs `requests` for you if it is missing.
- **Saved credentials** — your API key and custom endpoint are stored under `~/.config/orca/` and reused next time.
- **Internal-storage helpers** — commands to check and grant Termux access to `/storage/emulated/0`.

### Requirements

- Python 3.8+
- Internet access
- Optional: [Termux](https://termux.dev) (install the **F-Droid** or GitHub build — the Play Store version is outdated and broken)

### Quick start

```bash
python orca.py
```

On the first run Orca will:

1. Install the `requests` package if it is not already present.
2. Ask you to choose a provider.
3. Ask for an API key (for OpenRouter) or a custom endpoint URL.

Get a free OpenRouter key at <https://openrouter.ai/keys>. It starts with `sk-or-`. The key is verified once and saved to `~/.config/orca/key` with `600` permissions, so you are not asked again.

### Providers

**1) OpenRouter (default)** — works out of the box with a free key and the `openrouter/free` model.

**2) Custom endpoint** — any OpenAI-compatible base URL. Type it in one of these forms:

```
<endpointurl> <apikey>
<endpointurl> <apikey> <model>
```

For example:

```
https://my-host/v1 sk-abc123 gpt-4o
```

The endpoint is saved to `~/.config/orca/endpoint` and reused automatically on the next launch.

### Commands

Everything that does not start with `/` is sent to the AI. These control the session:

| Command | What it does |
| --- | --- |
| `/help` | Show the command list |
| `/clear` | Reset the conversation |
| `/auto` | Toggle auto-run (ON = commands run without asking) |
| `/think` | Turn reasoning on or off |
| `/effort low\|medium\|high` | Set reasoning effort |
| `/model NAME` | Switch model (e.g. `/model openrouter/free`) |
| `/provider` | Choose between OpenRouter and a custom endpoint |
| `/endpoint` | Set a custom endpoint (or pass it on the same line) |
| `/storage` | Grant or check access to internal storage |
| `/changeapikey` | Edit your key or endpoint file in nano |
| `/status` | Show the current provider, model, and settings |
| `/exit`, `/quit` | Leave |

`Ctrl+C` cancels an in-progress request.

### Tools the AI can use

| Tool | Purpose |
| --- | --- |
| `run_command` | Run any shell command and return stdout, stderr, and the exit code |
| `read_file` | Read a text file and return its contents with line numbers |
| `write_file` | Create, overwrite, or append to a file (parent folders are auto-created) |

Output is truncated before being sent back to the model (`run_command` output at 4000 chars, file reads at 12000 chars) to keep requests small. Each command has a 60-second timeout.

### Configuration

A few constants at the top of `orca.py` can be adjusted:

| Constant | Default | Meaning |
| --- | --- | --- |
| `MODEL` | `openrouter/free` | Default model |
| `THINK` | `True` | Show the model's reasoning |
| `EFFORT` | `medium` | Reasoning effort: `low`, `medium`, `high` |
| `STORAGE` | `/storage/emulated/0` | Android internal storage path |
| `CMD_TIMEOUT` | `60` | Seconds allowed per command |
| `MAX_OUTPUT` | `4000` | Chars of command output returned to the model |
| `MAX_FILE` | `12000` | Chars of a file returned to the model |

A few environment variables are also respected:

- `OPENROUTER_API_KEY` — API key (used if no saved key exists)
- `OPENROUTER_BASE_URL` — override the API base URL (default `https://openrouter.ai/api/v1`)
- `OPENROUTER_MODEL` — override the default model
- `EDITOR` — editor used by `/changeapikey` (defaults to `nano`, then `vi`)

### Files and permissions

Orca stores its settings under `~/.config/orca/`:

- `key` — your OpenRouter API key
- `endpoint` — your saved custom endpoint line

Both are created with `600` permissions. Never commit or share these files, and never paste your own key into a copy of the script you distribute.

### Using Android internal storage

Termux cannot see your phone's shared storage until you grant permission. Run:

```bash
/storage
```

inside Orca, or `termux-setup-storage` from the Termux shell. If it still fails, enable file access for Termux under **Android Settings → Apps → Termux → Permissions → Files and media**, and on Android 11+ also allow **All files access** if offered.

### Security notes

The AI can run arbitrary shell commands on your machine. Auto-run is enabled by default, so commands execute without confirmation. Review what it is doing, and use `/auto` if you would rather approve each action manually.

The whole conversation, including tool output, is sent to whichever provider you chose. Do not point Orca at sensitive files unless you are comfortable sending their contents to that provider.

### License

Add your preferred license here (for example, MIT) before publishing.