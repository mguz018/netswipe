"""JARVIS — a real-time voice assistant built on Google's Gemini Live API.

A single native-audio model takes microphone audio in and streams synthesized
voice back out. There is deliberately no separate speech-to-text -> LLM ->
text-to-speech pipeline; one model does all three.

Run:  python main.py
Quit: Ctrl-C
"""

import asyncio
import os
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
    # Phase 2 — tools. Scaffolded below; left out of the live config for now.
    # tools=TOOLS,
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
          - response.tool_call ....................... Phase 2 function calls
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

                # Phase 2 hook — see handle_tool_call() scaffold below.
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

    # ----- Phase 2 scaffold: tool / function calling ---------------------- #
    async def handle_tool_call(self, tool_call) -> None:
        """Dispatch model-requested function calls back to local handlers.

        Not wired into CONFIG yet. When you enable `tools=TOOLS` above, this
        receives types.LiveServerToolCall with a .function_calls list, runs the
        matching Python handler from TOOL_HANDLERS, and returns the results via
        session.send_tool_response(...). Left as a stub for the next phase.
        """
        responses = []
        for fc in tool_call.function_calls:
            handler = TOOL_HANDLERS.get(fc.name)
            if handler is None:
                result = {"error": f"Unknown function: {fc.name}"}
            else:
                result = handler(**(fc.args or {}))
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


# --------------------------------------------------------------------------- #
# Phase 2 scaffold — tool / function calling. NOT yet enabled.
#
# Roadmap:
#   1. Local-machine functions (below): open_app, read_file.
#   2. Google Search grounding: add types.Tool(google_search=types.GoogleSearch())
#   3. Code execution:           add types.Tool(code_execution=types.ToolCodeExecution())
#
# To turn these on: uncomment `tools=TOOLS` in CONFIG above. Note that
# google_search / code_execution are managed server-side, while the function
# declarations below are dispatched locally through handle_tool_call().
# --------------------------------------------------------------------------- #

def open_app(name: str) -> dict:
    """Stub: open an application on the local machine. Not implemented yet."""
    # TODO: subprocess to `open -a` (mac) / `xdg-open` (linux) / `start` (win).
    return {"status": "not_implemented", "requested": name}


def read_file(path: str) -> dict:
    """Stub: read a text file from the local machine. Not implemented yet."""
    # TODO: validate/whitelist paths, then return file contents (size-capped).
    return {"status": "not_implemented", "requested": path}


# Maps a declared function name -> the local Python handler that runs it.
TOOL_HANDLERS = {
    "open_app": open_app,
    "read_file": read_file,
}

# Function declarations the model is told about. Filled in next phase.
FUNCTION_DECLARATIONS = [
    types.FunctionDeclaration(
        name="open_app",
        description="Open an application on the user's computer.",
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "name": types.Schema(
                    type=types.Type.STRING,
                    description="Name of the application to open.",
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
                    description="Absolute path to the file to read.",
                ),
            },
            required=["path"],
        ),
    ),
]

# The full tool list to drop into CONFIG when Phase 2 goes live.
TOOLS = [
    types.Tool(function_declarations=FUNCTION_DECLARATIONS),
    # types.Tool(google_search=types.GoogleSearch()),          # grounding
    # types.Tool(code_execution=types.ToolCodeExecution()),    # code execution
]


def main() -> None:
    list_available_voices()
    try:
        asyncio.run(Jarvis().run())
    except KeyboardInterrupt:
        print("\nVery good, sir.")


if __name__ == "__main__":
    main()
