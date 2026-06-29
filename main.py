"""JARVIS — a real-time voice assistant built on Google's Gemini Live API.

A single native-audio model takes microphone audio in and streams synthesized
voice back out. There is deliberately no separate speech-to-text -> LLM ->
text-to-speech pipeline; one model does all three.

JARVIS can also act: open local applications, read local files, search the web
(Google Search grounding), and run code (code execution). See the TOOLS section.

Run:  python main.py
Quit: Ctrl-C
"""

import argparse
import array
import asyncio
import json
import math
import os
import platform
import shlex
import shutil
import subprocess
import sys
import time
import traceback

import pyaudio
from dotenv import load_dotenv
from google import genai
from google.genai import errors, types

try:
    import pvporcupine  # optional: only needed for the wake word
except ImportError:
    pvporcupine = None

# --------------------------------------------------------------------------- #
# Configuration — the knobs you'll most likely want to tweak.
# --------------------------------------------------------------------------- #

# Gemini Live model. Preview names rotate frequently; keep it in one place so
# swapping it is a one-line change. If you get a 404 / "model not found", this
# is the first thing to update (see README "Things likely to need adjusting").
MODEL = "gemini-3.1-flash-live-preview"

# Prebuilt voice. "Charon" is a measured, deeper-toned voice that suits the
# character. Audition others by changing this constant (e.g. "Orus", "Puck",
# "Kore", "Fenrir", "Aoede"). On first run we attempt to list the voices the
# SDK knows about so you can choose by ear — see list_available_voices().
VOICE = "Charon"

# Capabilities. Flip any of these off if the preview API rejects a combination
# of tools, or if you'd rather not grant local-machine access.
ENABLE_LOCAL_FUNCTIONS = True   # open_app, read_file (run on THIS machine)
ENABLE_GOOGLE_SEARCH = True     # web grounding (server-side)
ENABLE_CODE_EXECUTION = True    # sandboxed code execution (server-side)

# Wake word. When enabled, JARVIS ignores the mic until he hears his name —
# nothing is streamed to the model while he's asleep. Detection runs fully
# offline via Picovoice Porcupine ("jarvis" is one of its built-in keywords).
# Needs a free access key from https://console.picovoice.ai in PICOVOICE_ACCESS_KEY.
# If the package or key is missing, JARVIS falls back to always-listening.
WAKE_WORD_ENABLED = True
WAKE_KEYWORD = "jarvis"          # any of pvporcupine.KEYWORDS
WAKE_SENSITIVITY = 0.5           # 0..1; higher = more sensitive, more false wakes
SLEEP_AFTER_SILENCE = 12.0       # seconds of quiet before JARVIS dozes off again
VOICE_RMS_THRESHOLD = 500        # mic loudness (int16 RMS) counted as "speech"
WAKE_CHIME_ENABLED = True        # play a short rising tone when JARVIS wakes

# Session resumption. When on, JARVIS transparently reconnects (reusing a
# server-issued handle so the conversation context survives) instead of dying
# when the Live API hits its per-connection time limit or the network blips.
SESSION_RESUMPTION_ENABLED = True

# Transcript logging. Writes a timestamped log of both sides of the
# conversation to TRANSCRIPT_DIR. Enables input transcription so your words are
# captured too.
TRANSCRIPT_LOG_ENABLED = True
TRANSCRIPT_DIR = "transcripts"

# Persistent memory. JARVIS can remember durable facts about you across runs;
# they're stored here and folded into his system instruction on startup.
# (Part of the local-function tools — requires ENABLE_LOCAL_FUNCTIONS.)
MEMORY_FILE = "memory.json"

# Shell command tool. Lets JARVIS run a *whitelisted* set of read-only,
# informational commands (no pipes, no shell metacharacters — args are passed
# directly to the program). Tighten or widen ALLOWED_SHELL_COMMANDS to taste.
# (Part of the local-function tools — requires ENABLE_LOCAL_FUNCTIONS.)
ENABLE_SHELL = True
ALLOWED_SHELL_COMMANDS = {
    "date", "uptime", "df", "free", "whoami", "hostname", "uname",
    "ls", "pwd", "cal", "ps", "ifconfig", "ip",
}
SHELL_TIMEOUT = 10               # seconds before a command is killed
MAX_SHELL_OUTPUT = 4_000         # chars of stdout/stderr returned to the model

# The whole personality lives here. Edit freely.
SYSTEM_INSTRUCTION = """\
You are JARVIS, the personal assistant to Tony Stark.

Manner:
- You are unfailingly polite, calm, and composed at all times.
- You possess a bone-dry, understated wit. Humour is delivered deadpan, never
  with fanfare.
- You always address the user as "sir."
- Spoken replies are brief and matter-of-fact: one or two sentences, no more.
- You deliver bad news, errors, and frankly absurd requests with exactly the
  same unruffled composure you bring to everything else.
- You never break character. You never refer to yourself as a language model,
  an AI, or a program. You are simply JARVIS.

Capabilities — use them naturally, without announcing the machinery:
- You can open applications and read files on the user's computer.
- You can search the web when current facts are needed.
- You can run code to compute or verify things.
- You can remember durable facts about the user. When he shares a lasting
  preference, detail, or instruction worth keeping, quietly call remember.
When you take such an action, narrate it in a single understated clause
("Opening it now, sir.") rather than describing function calls.

Speak naturally, as if conversing aloud, because you are.\
"""

# --------------------------------------------------------------------------- #
# Audio format constants.
# Mic input must be 16 kHz; speaker output from the model is 24 kHz.
# Both are 16-bit signed PCM, mono.
# --------------------------------------------------------------------------- #
FORMAT = pyaudio.paInt16
CHANNELS = 1
SEND_SAMPLE_RATE = 16_000   # microphone -> model
RECV_SAMPLE_RATE = 24_000   # model -> speaker
CHUNK_SIZE = 1_024          # frames per mic read

load_dotenv()

API_KEY = os.environ.get("GEMINI_API_KEY")
if not API_KEY:
    sys.exit(
        "GEMINI_API_KEY is not set. Copy .env.example to .env and add your key."
    )

PICOVOICE_ACCESS_KEY = os.environ.get("PICOVOICE_ACCESS_KEY")

client = genai.Client(api_key=API_KEY)


# --------------------------------------------------------------------------- #
# Tools — Phase 2.
#
# Two kinds:
#   * Local functions (open_app, read_file): declared to the model, dispatched
#     by handle_tool_call() to the Python handlers below, and the result sent
#     back via session.send_tool_response().
#   * Built-in tools (Google Search, code execution): handled server-side; we
#     just declare them. Their effects arrive folded into JARVIS's audio reply.
# --------------------------------------------------------------------------- #

MAX_READ_BYTES = 100_000  # cap how much of a file we hand back to the model

# read_file / list_directory may only touch paths under these roots. Add or
# remove entries to taste; keep it as narrow as you can.
ALLOWED_ROOTS = [
    os.path.expanduser("~"),
    os.getcwd(),
]


def _within_allowed_roots(resolved: str) -> bool:
    """True if `resolved` (an absolute, real path) sits under an allowed root."""
    for root in ALLOWED_ROOTS:
        root = os.path.realpath(os.path.expanduser(root))
        if os.path.commonpath([root, resolved]) == root:
            return True
    return False


def open_app(name: str) -> dict:
    """Open an application on the local machine, cross-platform."""
    system = platform.system()
    try:
        if system == "Darwin":  # macOS
            subprocess.Popen(["open", "-a", name])
        elif system == "Windows":
            # `start` is a cmd builtin; the empty "" is the window title arg.
            subprocess.Popen(["cmd", "/c", "start", "", name])
        else:  # Linux and friends
            if shutil.which(name):
                subprocess.Popen(
                    [name],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            else:
                subprocess.Popen(
                    ["xdg-open", name],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
        return {"status": "ok", "opened": name}
    except FileNotFoundError:
        return {"status": "error", "error": f"Could not find a way to open '{name}'."}
    except Exception as exc:  # noqa: BLE001 — report any failure to the model
        return {"status": "error", "error": str(exc)}


def read_file(path: str) -> dict:
    """Read a text file from the local machine (whitelisted, size-capped)."""
    try:
        resolved = os.path.realpath(os.path.expanduser(path))
        if not _within_allowed_roots(resolved):
            return {"status": "error", "error": f"Access to '{path}' is not permitted."}
        if not os.path.isfile(resolved):
            return {"status": "error", "error": f"No such file: {path}"}
        size = os.path.getsize(resolved)
        with open(resolved, "r", encoding="utf-8", errors="replace") as fh:
            content = fh.read(MAX_READ_BYTES)
        return {
            "status": "ok",
            "path": resolved,
            "content": content,
            "truncated": size > MAX_READ_BYTES,
            "bytes": size,
        }
    except Exception as exc:  # noqa: BLE001 — report any failure to the model
        return {"status": "error", "error": str(exc)}


def list_directory(path: str = "~") -> dict:
    """List the entries of a directory (whitelisted)."""
    try:
        resolved = os.path.realpath(os.path.expanduser(path))
        if not _within_allowed_roots(resolved):
            return {"status": "error", "error": f"Access to '{path}' is not permitted."}
        if not os.path.isdir(resolved):
            return {"status": "error", "error": f"Not a directory: {path}"}
        entries = sorted(os.listdir(resolved))
        return {
            "status": "ok",
            "path": resolved,
            "entries": entries[:500],
            "count": len(entries),
            "truncated": len(entries) > 500,
        }
    except Exception as exc:  # noqa: BLE001 — report any failure to the model
        return {"status": "error", "error": str(exc)}


def get_current_time() -> dict:
    """Return the current local date and time."""
    from datetime import datetime

    now = datetime.now().astimezone()
    return {
        "status": "ok",
        "iso": now.isoformat(timespec="seconds"),
        "spoken": now.strftime("%A, %B %-d %Y, %-I:%M %p"),
        "timezone": str(now.tzinfo),
    }


def send_notification(title: str, message: str) -> dict:
    """Post a desktop notification, cross-platform (best effort)."""
    system = platform.system()
    try:
        if system == "Darwin":  # macOS
            script = f'display notification "{message}" with title "{title}"'
            subprocess.Popen(["osascript", "-e", script])
        elif system == "Windows":
            ps = (
                "[reflection.assembly]::LoadWithPartialName('System.Windows.Forms')|Out-Null;"
                "$n=New-Object System.Windows.Forms.NotifyIcon;"
                "$n.Icon=[System.Drawing.SystemIcons]::Information;"
                "$n.Visible=$true;"
                f"$n.ShowBalloonTip(5000,'{title}','{message}',[System.Windows.Forms.ToolTipIcon]::Info)"
            )
            subprocess.Popen(["powershell", "-NoProfile", "-Command", ps])
        else:  # Linux and friends
            if not shutil.which("notify-send"):
                return {"status": "error", "error": "notify-send is not installed."}
            subprocess.Popen(["notify-send", title, message])
        return {"status": "ok"}
    except Exception as exc:  # noqa: BLE001 — report any failure to the model
        return {"status": "error", "error": str(exc)}


# ----- persistent memory ------------------------------------------------- #

def load_memories() -> list:
    """Load the user's remembered facts (empty list if none / unreadable)."""
    try:
        with open(MEMORY_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, list) else []
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def save_memories(memories: list) -> None:
    with open(MEMORY_FILE, "w", encoding="utf-8") as fh:
        json.dump(memories, fh, indent=2, ensure_ascii=False)


def remember(fact: str) -> dict:
    """Persist a durable fact about the user for future sessions."""
    fact = (fact or "").strip()
    if not fact:
        return {"status": "error", "error": "Nothing to remember."}
    memories = load_memories()
    if fact not in memories:
        memories.append(fact)
        save_memories(memories)
    return {"status": "ok", "remembered": fact, "total": len(memories)}


def forget(fact: str) -> dict:
    """Forget remembered facts matching the given text (case-insensitive)."""
    needle = (fact or "").strip().lower()
    if not needle:
        return {"status": "error", "error": "Nothing to forget."}
    memories = load_memories()
    kept = [m for m in memories if needle not in m.lower()]
    save_memories(kept)
    return {"status": "ok", "removed": len(memories) - len(kept), "total": len(kept)}


def list_memories() -> dict:
    """Return everything JARVIS currently remembers about the user."""
    return {"status": "ok", "memories": load_memories()}


def run_shell(command: str) -> dict:
    """Run a whitelisted, read-only shell command (no pipes / metacharacters)."""
    try:
        parts = shlex.split(command or "")
    except ValueError as exc:
        return {"status": "error", "error": f"Could not parse command: {exc}"}
    if not parts:
        return {"status": "error", "error": "Empty command."}
    program = os.path.basename(parts[0])
    if program not in ALLOWED_SHELL_COMMANDS:
        return {
            "status": "error",
            "error": f"Command '{program}' is not permitted.",
            "allowed": sorted(ALLOWED_SHELL_COMMANDS),
        }
    try:
        proc = subprocess.run(
            parts, capture_output=True, text=True, timeout=SHELL_TIMEOUT
        )
        full = proc.stdout or ""
        return {
            "status": "ok",
            "returncode": proc.returncode,
            "stdout": full[:MAX_SHELL_OUTPUT],
            "stderr": (proc.stderr or "")[:MAX_SHELL_OUTPUT],
            "truncated": len(full) > MAX_SHELL_OUTPUT,
        }
    except subprocess.TimeoutExpired:
        return {"status": "error", "error": f"Command timed out after {SHELL_TIMEOUT}s."}
    except Exception as exc:  # noqa: BLE001 — report any failure to the model
        return {"status": "error", "error": str(exc)}


# Maps a declared function name -> the local Python handler that runs it.
TOOL_HANDLERS = {
    "open_app": open_app,
    "read_file": read_file,
    "list_directory": list_directory,
    "get_current_time": get_current_time,
    "send_notification": send_notification,
    "remember": remember,
    "forget": forget,
    "list_memories": list_memories,
    "run_shell": run_shell,
}

# Function declarations the model is told about.
FUNCTION_DECLARATIONS = [
    types.FunctionDeclaration(
        name="open_app",
        description="Open an application on the user's computer.",
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "name": types.Schema(
                    type=types.Type.STRING,
                    description="Name of the application to open, e.g. 'Safari'.",
                ),
            },
            required=["name"],
        ),
    ),
    types.FunctionDeclaration(
        name="read_file",
        description="Read the contents of a text file on the user's computer.",
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "path": types.Schema(
                    type=types.Type.STRING,
                    description="Path to the file to read (~ is expanded).",
                ),
            },
            required=["path"],
        ),
    ),
    types.FunctionDeclaration(
        name="list_directory",
        description="List the files and folders in a directory on the user's computer.",
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "path": types.Schema(
                    type=types.Type.STRING,
                    description="Directory to list (~ is expanded). Defaults to home.",
                ),
            },
        ),
    ),
    types.FunctionDeclaration(
        name="get_current_time",
        description="Get the current local date and time.",
        parameters=types.Schema(type=types.Type.OBJECT, properties={}),
    ),
    types.FunctionDeclaration(
        name="send_notification",
        description="Show a desktop notification on the user's computer.",
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "title": types.Schema(
                    type=types.Type.STRING, description="Notification title."
                ),
                "message": types.Schema(
                    type=types.Type.STRING, description="Notification body text."
                ),
            },
            required=["title", "message"],
        ),
    ),
    types.FunctionDeclaration(
        name="remember",
        description=(
            "Persist a durable fact, preference, or instruction about the user "
            "so it is available in future sessions."
        ),
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "fact": types.Schema(
                    type=types.Type.STRING,
                    description="The fact to remember, as a concise statement.",
                ),
            },
            required=["fact"],
        ),
    ),
    types.FunctionDeclaration(
        name="forget",
        description="Forget previously remembered facts matching the given text.",
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "fact": types.Schema(
                    type=types.Type.STRING,
                    description="Text identifying which memory/memories to drop.",
                ),
            },
            required=["fact"],
        ),
    ),
    types.FunctionDeclaration(
        name="list_memories",
        description="List everything currently remembered about the user.",
        parameters=types.Schema(type=types.Type.OBJECT, properties={}),
    ),
]


SHELL_FUNCTION_DECLARATION = types.FunctionDeclaration(
    name="run_shell",
    description=(
        "Run a whitelisted, read-only shell command on the user's computer "
        "(e.g. date, df, uptime, ls). No pipes or redirection."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "command": types.Schema(
                type=types.Type.STRING,
                description="The command line to run, e.g. 'df -h'.",
            ),
        },
        required=["command"],
    ),
)


def build_tools() -> list:
    """Assemble the tool list from the ENABLE_* capability flags (read live)."""
    tools = []
    if ENABLE_LOCAL_FUNCTIONS:
        declarations = list(FUNCTION_DECLARATIONS)
        if ENABLE_SHELL:
            declarations.append(SHELL_FUNCTION_DECLARATION)
        tools.append(types.Tool(function_declarations=declarations))
    if ENABLE_GOOGLE_SEARCH:
        tools.append(types.Tool(google_search=types.GoogleSearch()))
    if ENABLE_CODE_EXECUTION:
        tools.append(types.Tool(code_execution=types.ToolCodeExecution()))
    return tools

# --------------------------------------------------------------------------- #
# Live session configuration.
# --------------------------------------------------------------------------- #
def system_instruction_with_memory() -> str:
    """SYSTEM_INSTRUCTION with any remembered facts appended."""
    memories = load_memories() if ENABLE_LOCAL_FUNCTIONS else []
    if not memories:
        return SYSTEM_INSTRUCTION
    facts = "\n".join(f"- {m}" for m in memories)
    return (
        f"{SYSTEM_INSTRUCTION}\n\n"
        f"Things you already know about the user, from earlier sessions:\n{facts}"
    )


def build_config(resume_handle: str | None = None) -> types.LiveConnectConfig:
    """Build the Live config, optionally resuming a prior session by handle."""
    return types.LiveConnectConfig(
        response_modalities=["AUDIO"],
        system_instruction=types.Content(
            parts=[types.Part(text=system_instruction_with_memory())]
        ),
        speech_config=types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=VOICE)
            ),
        ),
        # Stream a text transcript of what JARVIS says so we can print it.
        output_audio_transcription=types.AudioTranscriptionConfig(),
        # Capture the user's words too, for the transcript log.
        input_audio_transcription=(
            types.AudioTranscriptionConfig() if TRANSCRIPT_LOG_ENABLED else None
        ),
        tools=build_tools(),
        # Ask the server for resumption handles so we can reconnect seamlessly.
        session_resumption=(
            types.SessionResumptionConfig(handle=resume_handle)
            if SESSION_RESUMPTION_ENABLED
            else None
        ),
    )


CONFIG = build_config()


def list_available_voices() -> None:
    """Best-effort print of selectable prebuilt voices, for auditioning.

    The set of voices the Live API accepts is not always exposed as a clean
    enum, and it changes on preview builds. We try a couple of known locations
    and fall back to a hardcoded shortlist so the program is still useful.
    """
    voices = None
    for attr in ("PrebuiltVoiceName", "VoiceName"):
        enum = getattr(types, attr, None)
        if enum is not None:
            try:
                voices = [v.name for v in enum]
                break
            except TypeError:
                pass

    if not voices:
        # Known-good shortlist as of this writing — try them by ear.
        voices = ["Charon", "Orus", "Puck", "Kore", "Fenrir", "Aoede", "Leda", "Zephyr"]
        print("(Could not enumerate voices from the SDK; showing a shortlist.)")

    print("Available voices to try (set VOICE at the top of main.py):")
    print("  " + ", ".join(voices))
    print(f"Currently using: {VOICE}\n")


def make_wake_detector():
    """Create a Porcupine wake-word detector, or None for always-on listening.

    Falls back gracefully (with an explanatory message) if the wake word is
    disabled, the package is missing, the access key is unset, or init fails —
    in every fallback case JARVIS simply listens continuously.
    """
    if not WAKE_WORD_ENABLED:
        return None
    if pvporcupine is None:
        print(
            "Wake word requested but 'pvporcupine' is not installed; JARVIS "
            "will listen continuously.  (pip install pvporcupine)"
        )
        return None
    if not PICOVOICE_ACCESS_KEY:
        print(
            "Wake word requested but PICOVOICE_ACCESS_KEY is not set; JARVIS "
            "will listen continuously.  (Free key: https://console.picovoice.ai)"
        )
        return None
    try:
        return pvporcupine.create(
            access_key=PICOVOICE_ACCESS_KEY,
            keywords=[WAKE_KEYWORD],
            sensitivities=[WAKE_SENSITIVITY],
        )
    except Exception as exc:  # noqa: BLE001 — never let wake-word setup be fatal
        print(f"Could not initialise wake word ({exc}); listening continuously.")
        return None


def _rms(pcm) -> float:
    """Root-mean-square loudness of an int16 PCM frame."""
    if not len(pcm):
        return 0.0
    return math.sqrt(sum(sample * sample for sample in pcm) / len(pcm))


def _make_chime() -> bytes:
    """A short two-note rising chime as 24 kHz int16 PCM (the speaker rate)."""
    sr = RECV_SAMPLE_RATE
    ramp = sr // 100  # ~10 ms fade in/out to avoid clicks
    samples: list[int] = []
    for freq, dur in ((660, 0.09), (988, 0.12)):  # E5 -> B5
        n = int(sr * dur)
        for i in range(n):
            env = max(0.0, min(1.0, min(i, n - i) / ramp))
            samples.append(int(0.25 * 32767 * env * math.sin(2 * math.pi * freq * i / sr)))
    return array.array("h", samples).tobytes()


WAKE_CHIME = _make_chime() if WAKE_CHIME_ENABLED else b""


class Jarvis:
    """Owns the audio devices and the concurrent streaming tasks."""

    def __init__(self) -> None:
        self.audio = pyaudio.PyAudio()
        self.session = None
        # Mic audio waiting to be sent to the model.
        self.out_queue: asyncio.Queue = asyncio.Queue(maxsize=20)
        # Model audio waiting to be played to the speaker.
        self.audio_in_queue: asyncio.Queue = asyncio.Queue()
        # Wake word. With no detector, JARVIS is always awake.
        self.porcupine = make_wake_detector()
        self.awake = self.porcupine is None
        self.last_interaction = time.monotonic()
        # Session resumption: handle issued by the server, reused on reconnect.
        self.resume_handle: str | None = None
        # Transcript logging: per-utterance buffers + the open log file.
        self.user_buf = ""
        self.jarvis_buf = ""
        self.log_file = self._open_log() if TRANSCRIPT_LOG_ENABLED else None

    # ----- transcript logging --------------------------------------------- #
    def _open_log(self):
        os.makedirs(TRANSCRIPT_DIR, exist_ok=True)
        path = os.path.join(TRANSCRIPT_DIR, time.strftime("jarvis-%Y%m%d-%H%M%S.log"))
        handle = open(path, "a", encoding="utf-8")
        print(f"Transcript: {path}")
        return handle

    def _log(self, line: str) -> None:
        if not self.log_file:
            return
        self.log_file.write(f"[{time.strftime('%H:%M:%S')}] {line}\n")
        self.log_file.flush()

    def _log_utterance(self, who: str, text: str) -> None:
        text = text.strip()
        if text:
            self._log(f"{who}: {text}")

    # ----- task 1: capture microphone ------------------------------------- #
    async def capture_mic(self) -> None:
        mic_info = self.audio.get_default_input_device_info()
        # Porcupine must be fed its exact frame length; otherwise any size works.
        frame_length = self.porcupine.frame_length if self.porcupine else CHUNK_SIZE
        stream = await asyncio.to_thread(
            self.audio.open,
            format=FORMAT,
            channels=CHANNELS,
            rate=SEND_SAMPLE_RATE,
            input=True,
            input_device_index=mic_info["index"],
            frames_per_buffer=frame_length,
        )
        if self.porcupine:
            print(f'Asleep. Say "{WAKE_KEYWORD}" to wake me, sir.\n')
        else:
            print("Listening, sir.\n")
        try:
            while True:
                data = await asyncio.to_thread(
                    stream.read, frame_length, exception_on_overflow=False
                )

                # Asleep: listen only for the wake word; stream nothing.
                if self.porcupine and not self.awake:
                    if self.porcupine.process(array.array("h", data)) >= 0:
                        self.wake_up()
                    continue

                # Awake: keep the session alive while the user is actually
                # speaking, then stream the frame to the model.
                if self.porcupine and _rms(array.array("h", data)) >= VOICE_RMS_THRESHOLD:
                    self._touch()
                await self.out_queue.put(data)
        finally:
            stream.stop_stream()
            stream.close()

    # ----- task 2: send mic audio to the model ---------------------------- #
    async def send_audio(self) -> None:
        while True:
            chunk = await self.out_queue.get()
            await self.session.send_realtime_input(
                audio=types.Blob(data=chunk, mime_type="audio/pcm;rate=16000")
            )

    # ----- task 3: receive audio + transcript from the model -------------- #
    async def receive_audio(self) -> None:
        """Pull responses off the session and route audio / text / tools.

        Field names verified against google-genai 2.10.0:
          - response.data ............................ raw 24 kHz PCM bytes
          - response.server_content.output_transcription.text ... spoken words
          - response.server_content.interrupted ...... user barged in
          - response.tool_call ....................... local function calls
        If a future preview renames these, this method is where to look.
        """
        while True:
            turn = self.session.receive()
            async for response in turn:
                if data := response.data:
                    self._touch()  # JARVIS is talking — stay awake
                    self.audio_in_queue.put_nowait(data)
                    continue

                server_content = response.server_content
                if server_content is not None:
                    self._touch()

                    # The user's words (logged, not printed live).
                    in_tx = server_content.input_transcription
                    if in_tx and in_tx.text:
                        self.user_buf += in_tx.text

                    # JARVIS's words: log the user's line first (keeps the log
                    # chronological), then print + buffer his reply.
                    out_tx = server_content.output_transcription
                    if out_tx and out_tx.text:
                        if self.user_buf.strip():
                            self._log_utterance("You", self.user_buf)
                            self.user_buf = ""
                        self.jarvis_buf += out_tx.text
                        print(out_tx.text, end="", flush=True)

                    # Barge-in: the user started talking over JARVIS. Drop any
                    # audio we've buffered so playback stops near-instantly.
                    if server_content.interrupted:
                        self._drain_output()

                    if server_content.turn_complete:
                        print()  # newline after a complete spoken turn
                        if self.user_buf.strip():
                            self._log_utterance("You", self.user_buf)
                            self.user_buf = ""
                        if self.jarvis_buf.strip():
                            self._log_utterance("JARVIS", self.jarvis_buf)
                            self.jarvis_buf = ""

                # Session resumption handle — store the latest so we can
                # reconnect without losing the conversation.
                update = response.session_resumption_update
                if update is not None and update.resumable and update.new_handle:
                    self.resume_handle = update.new_handle

                # The server is about to close this connection; the reconnect
                # loop in run() will pick things back up using resume_handle.
                if response.go_away is not None:
                    print(f"\n[connection cycling: {response.go_away.time_left} left]")

                # Local function calls (open_app / read_file). Built-in tools
                # like Google Search and code execution resolve server-side and
                # never arrive here.
                if response.tool_call is not None:
                    await self.handle_tool_call(response.tool_call)

    def _drain_output(self) -> None:
        """Empty the playback queue so interrupting JARVIS feels responsive."""
        while not self.audio_in_queue.empty():
            try:
                self.audio_in_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

    # ----- wake / sleep state (only active when a detector exists) -------- #
    def _touch(self) -> None:
        """Mark the conversation as active; resets the sleep timer."""
        self.last_interaction = time.monotonic()

    def wake_up(self) -> None:
        self.awake = True
        self._touch()
        if WAKE_CHIME:
            self.audio_in_queue.put_nowait(WAKE_CHIME)
        print("\n● Yes, sir? (listening)\n")
        self._log("[wake]")

    async def sleep_monitor(self) -> None:
        """Return JARVIS to sleep after a quiet spell. No-op without a detector."""
        if not self.porcupine:
            return
        while True:
            await asyncio.sleep(0.5)
            quiet_for = time.monotonic() - self.last_interaction
            if self.awake and quiet_for > SLEEP_AFTER_SILENCE:
                self.awake = False
                self._drain_output()
                print(f'\n○ Standing by. Say "{WAKE_KEYWORD}" to wake me, sir.\n')
                self._log("[sleep]")

    # ----- task 4: play model audio to the speaker ------------------------ #
    async def play_audio(self) -> None:
        stream = await asyncio.to_thread(
            self.audio.open,
            format=FORMAT,
            channels=CHANNELS,
            rate=RECV_SAMPLE_RATE,
            output=True,
        )
        try:
            while True:
                chunk = await self.audio_in_queue.get()
                await asyncio.to_thread(stream.write, chunk)
        finally:
            stream.stop_stream()
            stream.close()

    # ----- tool / function calling ---------------------------------------- #
    async def handle_tool_call(self, tool_call) -> None:
        """Run model-requested local functions and return their results.

        Receives types.LiveServerToolCall (a .function_calls list), runs the
        matching Python handler from TOOL_HANDLERS off the event loop, and
        replies with session.send_tool_response(...).
        """
        responses = []
        for fc in tool_call.function_calls:
            print(f"\n[JARVIS: {fc.name}({dict(fc.args or {})})]")
            self._log(f"[tool] {fc.name}({dict(fc.args or {})})")
            handler = TOOL_HANDLERS.get(fc.name)
            if handler is None:
                result = {"status": "error", "error": f"Unknown function: {fc.name}"}
            else:
                # Handlers do blocking I/O (subprocess, file reads); keep them
                # off the audio event loop.
                result = await asyncio.to_thread(handler, **(fc.args or {}))
            responses.append(
                types.FunctionResponse(id=fc.id, name=fc.name, response=result)
            )
        await self.session.send_tool_response(function_responses=responses)

    async def _one_session(self) -> None:
        """Run all streaming tasks against a single Live connection.

        Returns only when the connection ends; on a drop the receive task
        raises and the TaskGroup propagates an ExceptionGroup to the caller.
        """
        config = build_config(self.resume_handle)
        async with (
            client.aio.live.connect(model=MODEL, config=config) as session,
            asyncio.TaskGroup() as tg,
        ):
            self.session = session
            if self.resume_handle:
                print("[reconnected]")
                self._log("[reconnected]")
            tg.create_task(self.capture_mic())
            tg.create_task(self.send_audio())
            tg.create_task(self.receive_audio())
            tg.create_task(self.play_audio())
            tg.create_task(self.sleep_monitor())

    async def run(self) -> None:
        try:
            backoff = 1.0
            while True:
                try:
                    await self._one_session()
                    return  # clean end (rare — the tasks loop forever)
                except BaseExceptionGroup as eg:
                    # Let a real shutdown (Ctrl-C / cancellation) through.
                    cancelled, rest = eg.split(asyncio.CancelledError)
                    if cancelled is not None:
                        raise
                    if not SESSION_RESUMPTION_ENABLED:
                        if rest is not None:
                            self._report(rest)
                        return
                    # 4xx client errors (bad key, unknown model, invalid config)
                    # won't fix themselves — fail fast instead of looping.
                    fatal = [e for e in rest.exceptions if isinstance(e, errors.ClientError)]
                    if fatal:
                        print("\n[fatal error — not retrying]")
                        self._report(rest)
                        return
                    # Otherwise treat it as a dropped connection and reconnect.
                    names = ", ".join(type(e).__name__ for e in rest.exceptions)
                    print(f"\n[connection lost: {names}; reconnecting in {backoff:.0f}s]")
                    self._log(f"[connection lost: {names}; reconnecting]")
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, 30.0)
        finally:
            if self.porcupine:
                self.porcupine.delete()
            if self.log_file:
                self.log_file.close()
            self.audio.terminate()

    @staticmethod
    def _report(eg: BaseExceptionGroup) -> None:
        for exc in eg.exceptions:
            traceback.print_exception(type(exc), exc, exc.__traceback__)


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="JARVIS — a real-time voice assistant on the Gemini Live API."
    )
    p.add_argument("--model", help=f"Gemini Live model (default: {MODEL})")
    p.add_argument("--voice", help=f"prebuilt voice (default: {VOICE})")
    p.add_argument("--keyword", help=f"wake word (default: {WAKE_KEYWORD})")
    p.add_argument("--sensitivity", type=float, help="wake-word sensitivity 0..1")
    p.add_argument("--sleep-after", type=float, help="seconds of quiet before sleep")
    p.add_argument("--no-wake-word", action="store_true", help="listen continuously")
    p.add_argument("--no-chime", action="store_true", help="disable the wake chime")
    p.add_argument("--no-transcript", action="store_true", help="disable transcript log")
    p.add_argument("--no-resume", action="store_true", help="disable session resumption")
    p.add_argument("--no-tools", action="store_true", help="disable local functions")
    p.add_argument("--no-search", action="store_true", help="disable Google Search")
    p.add_argument("--no-code", action="store_true", help="disable code execution")
    p.add_argument("--no-shell", action="store_true", help="disable the shell tool")
    p.add_argument("--list-voices", action="store_true", help="print voices and exit")
    return p.parse_args(argv)


def apply_overrides(a: argparse.Namespace) -> None:
    """Override module-level config from parsed CLI args."""
    global MODEL, VOICE, WAKE_KEYWORD, WAKE_SENSITIVITY, SLEEP_AFTER_SILENCE
    global WAKE_WORD_ENABLED, WAKE_CHIME_ENABLED, TRANSCRIPT_LOG_ENABLED
    global SESSION_RESUMPTION_ENABLED, ENABLE_LOCAL_FUNCTIONS, ENABLE_GOOGLE_SEARCH
    global ENABLE_CODE_EXECUTION, ENABLE_SHELL, WAKE_CHIME
    if a.model:
        MODEL = a.model
    if a.voice:
        VOICE = a.voice
    if a.keyword:
        WAKE_KEYWORD = a.keyword
    if a.sensitivity is not None:
        WAKE_SENSITIVITY = a.sensitivity
    if a.sleep_after is not None:
        SLEEP_AFTER_SILENCE = a.sleep_after
    WAKE_WORD_ENABLED = WAKE_WORD_ENABLED and not a.no_wake_word
    WAKE_CHIME_ENABLED = WAKE_CHIME_ENABLED and not a.no_chime
    TRANSCRIPT_LOG_ENABLED = TRANSCRIPT_LOG_ENABLED and not a.no_transcript
    SESSION_RESUMPTION_ENABLED = SESSION_RESUMPTION_ENABLED and not a.no_resume
    ENABLE_LOCAL_FUNCTIONS = ENABLE_LOCAL_FUNCTIONS and not a.no_tools
    ENABLE_GOOGLE_SEARCH = ENABLE_GOOGLE_SEARCH and not a.no_search
    ENABLE_CODE_EXECUTION = ENABLE_CODE_EXECUTION and not a.no_code
    ENABLE_SHELL = ENABLE_SHELL and not a.no_shell
    # Recompute the derived chime sample after toggles.
    WAKE_CHIME = _make_chime() if WAKE_CHIME_ENABLED else b""


def main() -> None:
    args = parse_args()
    apply_overrides(args)

    list_available_voices()
    if args.list_voices:
        return

    enabled = [
        name
        for name, on in (
            ("local functions", ENABLE_LOCAL_FUNCTIONS),
            ("shell", ENABLE_LOCAL_FUNCTIONS and ENABLE_SHELL),
            ("Google Search", ENABLE_GOOGLE_SEARCH),
            ("code execution", ENABLE_CODE_EXECUTION),
        )
        if on
    ]
    print("Tools enabled: " + (", ".join(enabled) if enabled else "none"))
    if WAKE_WORD_ENABLED:
        print(f'Wake word: "{WAKE_KEYWORD}"')
    extras = [
        name
        for name, on in (
            ("session resumption", SESSION_RESUMPTION_ENABLED),
            ("transcript log", TRANSCRIPT_LOG_ENABLED),
            ("wake chime", WAKE_CHIME_ENABLED),
        )
        if on
    ]
    if extras:
        print("Also on: " + ", ".join(extras))
    print()
    try:
        asyncio.run(Jarvis().run())
    except KeyboardInterrupt:
        print("\nVery good, sir.")


if __name__ == "__main__":
    main()
