# JARVIS — a real-time voice assistant

A voice assistant in the spirit of Tony Stark's JARVIS, built on Google's
**Gemini Live API** with **native audio**. A single model takes your microphone
audio in and streams synthesized speech back out — there is no separate
speech-to-text → LLM → text-to-speech pipeline. One model does all three.

Speak, and JARVIS answers aloud: unfailingly polite, calm, bone-dry, and brief.

## How it works

`main.py` runs concurrent `asyncio` tasks inside a `TaskGroup`, passing audio
between them over queues:

1. **capture mic** — reads 16 kHz / 16-bit / mono PCM from your microphone
2. **send audio** — streams those chunks to the Live session
3. **receive audio** — pulls the model's 24 kHz audio + text transcript back
4. **play audio** — plays the model's speech to your speakers
5. **sleep monitor** — returns JARVIS to standby after a quiet spell

When you talk over JARVIS (barge-in), the queued output audio is drained so he
stops near-instantly. His words are also printed to the console as he speaks
(via output transcription).

## Highlights

- **Native-audio, single model** — no STT/LLM/TTS pipeline.
- **"JARVIS" wake word** — fully offline, with an audible wake chime.
- **Tools** — open apps, read files, list directories, time, notifications,
  plus Google Search grounding and code execution.
- **Persistent memory** — remembers durable facts about you across runs.
- **Transcript logging** — both sides of every conversation, timestamped.
- **Session resumption** — transparently reconnects (keeping context) when the
  Live API hits its connection limit or the network blips.

## Resilience — session resumption

The Gemini Live API caps how long a single connection can stay open, and
networks drop. With `SESSION_RESUMPTION_ENABLED` (default on), JARVIS asks the
server for a resumption handle, and when a connection ends he **reconnects
automatically using that handle** — so the conversation context carries over
rather than resetting. Reconnects use exponential backoff (1s → 30s); a real
Ctrl-C still exits cleanly. Unrecoverable **4xx errors** (bad API key, unknown
model, invalid config) are detected and **fail fast** instead of looping.

## Persistent memory

With `ENABLE_LOCAL_FUNCTIONS` on, JARVIS can remember durable facts about you
between sessions. He calls `remember(fact)` when you share a lasting preference
or detail (and `forget` / `list_memories` as needed); facts are stored in
`memory.json` and folded into his system instruction at the start of each
session, so the next run begins already knowing them.

```
You:    JARVIS, remember I take my coffee black.
JARVIS: Noted, sir.
        # ...next session...
You:    Make me a coffee.
JARVIS: Black, as always, sir.
```

`memory.json` is gitignored. Delete it to wipe his memory.

## Transcript logging

With `TRANSCRIPT_LOG_ENABLED` (default on), each run writes a timestamped log to
`transcripts/jarvis-YYYYMMDD-HHMMSS.log` capturing **both** sides of the
conversation (input transcription is enabled for your words), plus wake/sleep
and tool-call events:

```
[17:05:05] [wake]
[17:05:08] You: what's the weather in Malibu
[17:05:10] [tool] open_app({'name': 'Weather'})
[17:05:11] JARVIS: Seventy-two and clear, sir. Shocking, for Malibu.
```

The `transcripts/` directory is gitignored.

## Setup

### 1. System dependency: PortAudio

`pyaudio` needs PortAudio installed at the OS level:

- **macOS:** `brew install portaudio`
- **Ubuntu/Debian:** `sudo apt-get install portaudio19-dev`

### 2. Python dependencies

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### 3. API key

```bash
cp .env.example .env
# then edit .env and paste your key from https://aistudio.google.com/apikey
```

### 4. Run

```bash
python main.py
```

On startup it prints the prebuilt voices it can find so you can audition them,
then starts listening. Press **Ctrl-C** to quit.

### Command-line options

Everything configurable as a constant can also be set per-run, without editing
the file:

```bash
python main.py --voice Orus --keyword computer   # audition a voice / wake word
python main.py --model gemini-3.1-flash-live-preview
python main.py --no-wake-word                    # listen continuously
python main.py --no-search --no-code --no-shell  # trim capabilities
python main.py --list-voices                     # print voices and exit
```

Run `python main.py --help` for the full list. Flags only *disable* features
that are on by default; the constants at the top of `main.py` remain the source
of defaults.

## Wake word — "JARVIS"

By default JARVIS doesn't stream a thing until he hears his name. Wake-word
detection runs **fully offline** via [Picovoice Porcupine](https://picovoice.ai/),
which ships `"jarvis"` as one of its built-in keywords — no custom model needed.

How it behaves:

1. **Asleep** — the mic is read locally and checked for the wake word; nothing
   is sent to the model.
2. Say **"JARVIS"** → a short chime plays, he wakes (`● Yes, sir?`), and starts
   streaming to the model. (Chime toggled by `WAKE_CHIME_ENABLED`.)
3. Talk normally. He stays awake while you're speaking and while he's replying.
4. After `SLEEP_AFTER_SILENCE` seconds of quiet he dozes off again
   (`○ Standing by.`) until the next "JARVIS".

### Setup

1. Add `pvporcupine` (already in `requirements.txt`).
2. Get a **free** access key at https://console.picovoice.ai and put it in
   `.env` as `PICOVOICE_ACCESS_KEY`.

If the package or key is missing, JARVIS prints a notice and simply
**listens continuously** — the wake word is a convenience, not a hard
dependency.

### Knobs (top of `main.py`)

- `WAKE_WORD_ENABLED` — turn the wake word on/off
- `WAKE_KEYWORD` — any of `pvporcupine.KEYWORDS` (e.g. `"computer"`, `"jarvis"`)
- `WAKE_SENSITIVITY` — `0..1`; higher catches more, with more false wakes
- `SLEEP_AFTER_SILENCE` — seconds of quiet before he sleeps again
- `VOICE_RMS_THRESHOLD` — mic loudness counted as speech (keeps him awake)

## Customizing

The things you'll most likely want to change live as constants at the top
of `main.py`:

- `MODEL` — the Gemini Live model name
- `VOICE` — the prebuilt voice (defaults to `"Charon"`, a measured deeper tone)
- `SYSTEM_INSTRUCTION` — the personality
- `WAKE_*` — the wake word (see above)

## Things likely to need adjusting on a preview API

This targets a **preview** model, and preview surfaces drift. The code was
written and verified against `google-genai` 2.10.0, but if something breaks,
these are the usual suspects:

- **Model name** (`MODEL`). Preview names rotate. A 404 / "model not found"
  almost always means this constant is stale — update it to the current Live
  model name.
- **Voice names** (`VOICE`). The accepted set changes between builds. The app
  prints what it can find on startup; if your choice is rejected, try another
  from that list (`Orus`, `Puck`, `Kore`, `Fenrir`, `Aoede`, …).
- **Response field names.** The receive loop reads `response.data` for audio
  and `response.server_content.output_transcription.text` for the transcript,
  and watches `server_content.interrupted` / `turn_complete`. These were
  verified against the installed SDK; if a future build renames them, the
  `receive_audio()` method is where to adjust.

## Tools

JARVIS can act, not just talk. Tools are toggled by the `ENABLE_*` flags at the
top of `main.py`:

- **Local-machine functions** (`ENABLE_LOCAL_FUNCTIONS`)
  - `open_app(name)` — opens an application (cross-platform: `open -a` on macOS,
    `start` on Windows, the binary or `xdg-open` on Linux).
  - `read_file(path)` — reads a text file (`~` expanded, capped at 100 KB,
    restricted to allowed roots — see below).
  - `list_directory(path)` — lists a directory's entries (same restriction).
  - `get_current_time()` — the current local date and time.
  - `send_notification(title, message)` — a desktop notification (`osascript`
    on macOS, `notify-send` on Linux, PowerShell balloon on Windows).
  - `remember(fact)` / `forget(fact)` / `list_memories()` — persistent memory
    across runs (see [Persistent memory](#persistent-memory)).
  - `run_shell(command)` — runs a **whitelisted, read-only** command
    (`ENABLE_SHELL`; allowlist in `ALLOWED_SHELL_COMMANDS`). Args are passed
    directly to the program — no shell, no pipes, no redirection — and output
    is size- and time-capped.
  - These run on **your machine**, dispatched locally by `handle_tool_call()`,
    and the result is returned to the model. See the security note below.
- **Google Search grounding** (`ENABLE_GOOGLE_SEARCH`) — JARVIS can pull current
  facts from the web. Resolved server-side.
- **Code execution** (`ENABLE_CODE_EXECUTION`) — JARVIS can run code in a
  sandbox to compute or verify things. Resolved server-side.

Each tool call is logged to the console (e.g. `[JARVIS: open_app({'name': 'Safari'})]`).

> **Security note:** with local functions enabled, the model can open apps,
> read files, post notifications, and run whitelisted shell commands on your
> computer in response to what it hears on the mic. `run_shell` only permits
> the read-only programs in `ALLOWED_SHELL_COMMANDS` and never invokes a
> shell, but treat the allowlist as a trust boundary — keep it narrow. `read_file` / `list_directory` are confined to the roots in
> `ALLOWED_ROOTS` (home directory and the working directory by default) and
> reject path-traversal escapes via `realpath`. Narrow `ALLOWED_ROOTS` further
> for untrusted environments, or set `ENABLE_LOCAL_FUNCTIONS = False`. Note that
> `open_app` is **not** restricted — it can launch any installed application.

> **If the API rejects combining tools:** some preview models don't allow
> function declarations alongside built-in tools in one session. If you see an
> error to that effect on connect, turn off one of the `ENABLE_*` flags.

### Adding your own function

1. Write a handler returning a JSON-serializable `dict`.
2. Register it in `TOOL_HANDLERS`.
3. Add a `types.FunctionDeclaration` to `FUNCTION_DECLARATIONS`.
