# JARVIS — a real-time voice assistant

A voice assistant in the spirit of Tony Stark's JARVIS, built on Google's
**Gemini Live API** with **native audio**. A single model takes your microphone
audio in and streams synthesized speech back out — there is no separate
speech-to-text → LLM → text-to-speech pipeline. One model does all three.

Speak, and JARVIS answers aloud: unfailingly polite, calm, bone-dry, and brief.

## How it works

`main.py` runs four concurrent `asyncio` tasks inside a `TaskGroup`, passing
audio between them over queues:

1. **capture mic** — reads 16 kHz / 16-bit / mono PCM from your microphone
2. **send audio** — streams those chunks to the Live session
3. **receive audio** — pulls the model's 24 kHz audio + text transcript back
4. **play audio** — plays the model's speech to your speakers

When you talk over JARVIS (barge-in), the queued output audio is drained so he
stops near-instantly. His words are also printed to the console as he speaks
(via output transcription).

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

## Customizing

The three things you'll most likely want to change live as constants at the top
of `main.py`:

- `MODEL` — the Gemini Live model name
- `VOICE` — the prebuilt voice (defaults to `"Charon"`, a measured deeper tone)
- `SYSTEM_INSTRUCTION` — the personality

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

## Roadmap — Phase 2: tools / function calling

Scaffolded in `main.py` but not yet wired into the live config:

- **Local-machine functions** — `open_app`, `read_file` (declarations and a
  `handle_tool_call` dispatcher are stubbed in).
- **Google Search grounding** — `types.Tool(google_search=...)`.
- **Code execution** — `types.Tool(code_execution=...)`.

To enable, fill in the handler bodies and uncomment `tools=TOOLS` in `CONFIG`.
