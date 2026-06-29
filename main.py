"""JARVIS — a real-time voice assistant built on Google's Gemini Live API.

A single native-audio model takes microphone audio in and streams synthesized
voice back out. There is deliberately no separate speech-to-text -> LLM ->
text-to-speech pipeline; one model does all three.

JARVIS can also act: open local applications, read local files, search the web
(Google Search grounding), and run code (code execution). See the TOOLS section.

Run:  python main.py
Quit: Ctrl-C
"""

import asyncio
import os
import platform
import shutil
import subprocess
import sys
import traceback

import pyaudio
from dotenv import load_dotenv
from google import genai
from google.genai import types

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


# Maps a declared function name -> the local Python handler that runs it.
TOOL_HANDLERS = {
    "open_app": open_app,
    "read_file": read_file,
    "list_directory": list_directory,
    "get_current_time": get_current_time,
    "send_notification": send_notification,
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
]


def build_tools() -> list:
    """Assemble the tool list from the ENABLE_* capability flags."""
    tools = []
    if ENABLE_LOCAL_FUNCTIONS:
        tools.append(types.Tool(function_declarations=FUNCTION_DECLARATIONS))
    if ENABLE_GOOGLE_SEARCH:
        tools.append(types.Tool(google_search=types.GoogleSearch()))
    if ENABLE_CODE_EXECUTION:
        tools.append(types.Tool(code_execution=types.ToolCodeExecution()))
    return tools


TOOLS = build_tools()

# --------------------------------------------------------------------------- #
# Live session configuration.
# --------------------------------------------------------------------------- #
CONFIG = types.LiveConnectConfig(
    response_modalities=["AUDIO"],
    system_instruction=types.Content(
        parts=[types.Part(text=SYSTEM_INSTRUCTION)]
    ),
    speech_config=types.SpeechConfig(
        voice_config=types.VoiceConfig(
            prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=VOICE)
        ),
    ),
    # Stream a text transcript of what JARVIS says so we can print it.
    output_audio_transcription=types.AudioTranscriptionConfig(),
    tools=TOOLS,
)


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


class Jarvis:
    """Owns the audio devices and the four concurrent streaming tasks."""

    def __init__(self) -> None:
        self.audio = pyaudio.PyAudio()
        self.session = None
        # Mic audio waiting to be sent to the model.
        self.out_queue: asyncio.Queue = asyncio.Queue(maxsize=20)
        # Model audio waiting to be played to the speaker.
        self.audio_in_queue: asyncio.Queue = asyncio.Queue()

    # ----- task 1: capture microphone ------------------------------------- #
    async def capture_mic(self) -> None:
        mic_info = self.audio.get_default_input_device_info()
        stream = await asyncio.to_thread(
            self.audio.open,
            format=FORMAT,
            channels=CHANNELS,
            rate=SEND_SAMPLE_RATE,
            input=True,
            input_device_index=mic_info["index"],
            frames_per_buffer=CHUNK_SIZE,
        )
        print("Listening, sir.\n")
        try:
            while True:
                data = await asyncio.to_thread(
                    stream.read, CHUNK_SIZE, exception_on_overflow=False
                )
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
                    self.audio_in_queue.put_nowait(data)
                    continue

                server_content = response.server_content
                if server_content is not None:
                    transcription = server_content.output_transcription
                    if transcription and transcription.text:
                        print(transcription.text, end="", flush=True)

                    # Barge-in: the user started talking over JARVIS. Drop any
                    # audio we've buffered so playback stops near-instantly.
                    if server_content.interrupted:
                        self._drain_output()

                    if server_content.turn_complete:
                        print()  # newline after a complete spoken turn

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

    async def run(self) -> None:
        try:
            async with (
                client.aio.live.connect(model=MODEL, config=CONFIG) as session,
                asyncio.TaskGroup() as tg,
            ):
                self.session = session
                tg.create_task(self.capture_mic())
                tg.create_task(self.send_audio())
                tg.create_task(self.receive_audio())
                tg.create_task(self.play_audio())
        except* asyncio.CancelledError:
            pass
        except* Exception as eg:  # TaskGroup wraps failures in an ExceptionGroup
            for exc in eg.exceptions:
                traceback.print_exception(type(exc), exc, exc.__traceback__)
        finally:
            self.audio.terminate()


def main() -> None:
    list_available_voices()
    enabled = [
        name
        for name, on in (
            ("local functions", ENABLE_LOCAL_FUNCTIONS),
            ("Google Search", ENABLE_GOOGLE_SEARCH),
            ("code execution", ENABLE_CODE_EXECUTION),
        )
        if on
    ]
    print("Tools enabled: " + (", ".join(enabled) if enabled else "none") + "\n")
    try:
        asyncio.run(Jarvis().run())
    except KeyboardInterrupt:
        print("\nVery good, sir.")


if __name__ == "__main__":
    main()
