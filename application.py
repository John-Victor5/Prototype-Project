import serial
import serial.tools.list_ports
import time
import json
import threading
import asyncio
import uvicorn
import os
import time
import re
import random
import requests
import soundfile as sf
from datetime import datetime
import sounddevice as sd
import numpy as np
from pydantic import BaseModel
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse

from core.AudioEngine_Orpheus_ipex_2v import OrpheusTTS
from core.MicEngine_whipserv2 import AudioTranscriber
from core.ManagementAudio_2v import Play_Audio_Simultaneous
from core.ManagementChatv2 import HybridMemory
from core.Ollama_ipex_2v import OllamaIPEX

# ---------- Load system prompt ----------
if os.path.exists("prompts/service.md"):
    with open("prompts/service.md", "r", encoding="utf-8") as f:
        system_prompts = f.read()

# ---------- Sound greetings ----------
sound = {
    "Morning": ["Morning_1.wav", "Morning_2.wav", "Morning_3.wav", "Morning_4.wav", "Morning_5.wav"],
    "Afternoon": ["Afternoon_1.wav", "Afternoon_2.wav", "Afternoon_3.wav", "Afternoon_4.wav", "Afternoon_5.wav"],
    "Evening":  ["Evening_1.wav", "Evening_2.wav", "Evening_3.wav", "Evening_4.wav", "Evening_5.wav"]
}

def play_random_greeting():
    hour = datetime.now().hour
    if 5 <= hour < 12:
        period = "Morning"
    elif 12 <= hour < 18:
        period = "Afternoon"
    else:
        period = "Evening"
    chosen_file = random.choice(sound[period])
    full_path = os.path.join("database/sound", chosen_file)
    data, fs = sf.read(full_path, dtype="float32")
    Play_Audio_Simultaneous(data, fs)

def beep(freq, duration, volume=0.3):
    fs = 44100
    t = np.arange(int(fs * duration)) / fs
    samples = (np.sin(2 * np.pi * freq * t)).astype(np.float32)
    sd.play(samples * volume, fs)
    sd.wait()

Warning_count = 0
BAD_WORDS = ["arse", "arsehead", "arsehole", "ass", "asshole", "bastard", "bitch", "bloody", "bollocks", "brotherfucker", "bugger", "bullshit", "chigga", "child-fucker", "cock", "cocksucker", "crap", "cunt", "dammit", "damn", "damned", "dick", "dick-head", "dickhead", "dumb-ass", "dumbass", "dyke", "fag", "faggot", "father-fucker", "fatherfucker", "fuck", "fucked", "fucker", "fucking", "goddammit", "goddamn", "Goddamn", "goddamned", "goddamnit", "godsdamn", "hell", "horseshit", "jack-ass", "jackass", "kike", "mother", "fucker", "mother-fucker", "motherfucker", "nigga", "pigfucker", "piss", "prick", "pussy", "shit", "shite", "sisterfuck", "sisterfucker", "slut", "spastic", "tranny", "twat", "wanker"]

FORBIDDEN_PATTERNS = BAD_WORDS
pattern_re = re.compile(r'\b(' + '|'.join(re.escape(word) for word in FORBIDDEN_PATTERNS) + r')\b', re.IGNORECASE)

def three_small_beeps():
    for _ in range(3):
        beep(1000, 0.2)
        time.sleep(0.1)

def continuous_high_beep():
    for _ in range(20):
        beep(3000, 0.3, 0.1)

def active_warning():
    global Warning_count
    push_warning(Warning_count)
    
    if Warning_count == 0:
        three_small_beeps()
        path = os.path.join("database/sound", "First warning.wav")
        data, fs = sf.read(path, dtype="float32")
        Play_Audio_Simultaneous(data, fs)
        
    elif Warning_count == 1:
        three_small_beeps()
        path = os.path.join("database/sound", "Second warning.wav")
        data, fs = sf.read(path, dtype="float32")
        Play_Audio_Simultaneous(data, fs)
        
    elif Warning_count == 2:
        three_small_beeps()
        path = os.path.join("database/sound", "Final warning.wav")
        data, fs = sf.read(path, dtype="float32")
        Play_Audio_Simultaneous(data, fs)
        
    elif Warning_count == 3:
        push_warning(99) 
        continuous_high_beep() 
        time.sleep(6)
        push_reset_screen()
        Warning_count = -1
        
    Warning_count += 1

# ---------- Arduino serial (shared with lock) ----------
ser = None
serial_lock = threading.Lock()

def write_arduino(data: bytes):
    # If Off_arduino is False, it means we WANT to use the Arduino.
    # So we should only return (skip) if Off_arduino is True.
    if Off_arduino: 
        return
        
    """Thread‑safe write to Arduino."""
    with serial_lock:
        if ser and ser.is_open:
            # IMPORTANT: Add the newline character \n so Arduino 
            # readStringUntil('\n') knows the command is finished.
            if not data.endswith(b'\n'):
                data += b'\n'
                
            ser.write(data)
            ser.flush()
            print(f"Sent to Arduino: {data}") # Debug line

class ArduinoCommand(BaseModel):
    cmd: str

ALLOWED_COMMANDS = {
    'O','C','B','I','OS','CS','OM','CM','CVD',
    'DS','ES','DM','EM','DU','EU','DRFID','ERFID'
}

# ---------- Global state & SSE event queue ----------
active = False
stop_ai_requested = threading.Event()
lock = threading.Lock()
event_queue = asyncio.Queue()
main_loop = None
maintenance = False

def set_active(value: bool):
    global active
    with lock:
        if active != value:
            active = value
            if not active:
                stop_ai_requested.set()
            if main_loop is not None:
                asyncio.run_coroutine_threadsafe(
                    event_queue.put(f"data: {{\"active\": {str(active).lower()}}}\n\n"),
                    main_loop
                )

def push_stt_text(text: str):
    """Send STT text to the web page."""
    if main_loop is not None:
        asyncio.run_coroutine_threadsafe(
            event_queue.put(f"data: {{\"type\": \"stt\", \"text\": {json.dumps(text)}}}\n\n"),
            main_loop
        )

def push_ai_text(text: str):
    """Send AI response text to the web page."""
    if main_loop is not None:
        asyncio.run_coroutine_threadsafe(
            event_queue.put(f"data: {{\"type\": \"ai\", \"text\": {json.dumps(text)}}}\n\n"),
            main_loop
        )

def push_wait_processing(types: str):
    if main_loop is not None:
        asyncio.run_coroutine_threadsafe(
            event_queue.put(f"data: {{\"type\": \"processing >> {types}\"}}\n\n"),
            main_loop
        )

def push_done_processing(types: str):
    if main_loop is not None:
        asyncio.run_coroutine_threadsafe(
            event_queue.put(f"data: {{\"type\": \"done_processing >>{types}\"}}\n\n"),
            main_loop
        )

def push_warning(level: int):
    if main_loop is not None:
        asyncio.run_coroutine_threadsafe(
            event_queue.put(f"data: {{\"type\": \"warning\", \"level\": {level}}}\n\n"),
            main_loop
        )

def push_reset_screen():
    if main_loop is not None:
        asyncio.run_coroutine_threadsafe(
            event_queue.put(f"data: {{\"type\": \"reset_screen\"}}\n\n"),
            main_loop
        )

def push_maintenance_off():
    if main_loop is not None:
        asyncio.run_coroutine_threadsafe(
            event_queue.put(f"data: {{\"type\": \"push_maintenance_off\"}}\n\n"),
            main_loop
        )

def push_maintenance_on():
    if main_loop is not None:
        asyncio.run_coroutine_threadsafe(
            event_queue.put(f"data: {{\"type\": \"push_maintenance_on\"}}\n\n"),
            main_loop
        )
        
# ---------- AI components (initialised later) ----------
stt = None
tts = None
client = None
memory = None
memory_file = "memory.json"

def clear_ai_memory():
    global memory
    if os.path.exists(f"cache/{memory_file}"):
        os.remove(f"cache/{memory_file}")
    memory = HybridMemory(file_path=memory_file, system_prompt=system_prompts)

End_sentence = [r'\n', r'?', r'!', r'.']

def prompt(text: str):
    """Ollama AI call."""
    conversation_history = client.chat(
        messages=text,
        tools=prompt_function,
        available_functions=Hand_command,
        raw_output_tools=True
    )
    msg = memory.add_messages(conversation_history)
    chunks = ""
    sentence = ""
    print("\n", end="", flush=True)
    for chunk in client.chat(messages=msg, stream=True):
        chunks += chunk
        sentence += chunk
        print(chunk, end="", flush=True)
        if any(mark in chunk for mark in End_sentence):
            if len(sentence.split()) >= 5:
                tts.feed_text(sentence)
                sentence = ""
    print("\n")
    memory.add_chat(chunks, role="assistant")
    return chunks

def split_word_by_word(text: str, duration: float):
    words = text.split()
    if not words:
        return
    delay = duration / len(words)
    
    for word in words:
        push_ai_text(word + " ")
        time.sleep(delay)

def audio(text: str):
    """TTS and playback."""
    push_wait_processing(types="TTS")
    filename = f"cache/{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.wav"
    _ = tts.export_generate(
        output_file=filename
    )
    push_done_processing(types="TTS")
    data, fs = sf.read(filename, dtype="float32")
    dtaudio = np.array(data, dtype=np.float32)
    duration = len(dtaudio) / fs
    text_thread = threading.Thread(target=split_word_by_word, args=(text, duration))
    text_thread.start()
    Play_Audio_Simultaneous(dtaudio, fs)

# ---------- AI interaction loop (runs in its own thread) ----------

def ai_interaction_loop():
    global stop_ai_requested
    while True:
        while not active:
            time.sleep(0.2)
            if stop_ai_requested.is_set():
                stop_ai_requested.clear()
        stop_ai_requested.clear()
        if not maintenance: play_random_greeting()

        if not Offline_Test: stt.calibrate_noise()
        while active:
            def stop_check():
                return stop_ai_requested.is_set()
            if Offline_Test:
                time.sleep(0.5)
                continue
            text = stt.listen(stop_check=stop_check, arduinooverride=ser)
            if pattern_re.search(text):
                active_warning()
                write_arduino(b'I')
                continue

            if text == "STOP_SIGNAL" or stop_ai_requested.is_set():
                clear_ai_memory()
                global Warning_count
                Warning_count = 0
                push_reset_screen()
                break
            
            if text:
                print(f"\nUSER: {text}")
                push_stt_text(text)
                msg = memory.add_chat(text, role="user")
                push_wait_processing(types="LLM")
                content = prompt(msg)
                client.terminate_all_models()
                for api_ in api_list:
                    tts.requests_helper(api_) #GPU Intel
                push_done_processing(types="LLM")
                audio(content)
                client.terminate_all_models()
                write_arduino(b'I')
                time.sleep(3)
        time.sleep(0.5)

# ---------- Serial reader thread (only reads, never writes) ----------
def serial_reader():
    global active, maintenance
    while True:
        if ser and ser.in_waiting > 0:
            with serial_lock:
                line = ser.readline().decode('utf-8', errors='ignore').strip()
            print(f"Arduino: {line}")
            if "<FUNCTION-001>" in line:
                set_active(True)
            elif "<FUNCTION-000>" in line:
                set_active(False)

            elif "<MAINTENANCE-ENTER>" in line:
                maintenance = True
                push_maintenance_on()
            elif "<MAINTENANCE-EXIT>" in line:
                maintenance = False
                push_maintenance_off()
        time.sleep(0.05)

# ---------- Arduino port detection ----------
def find_arduino_port():
    print("🔍 Scanning for Arduino...")
    ports = serial.tools.list_ports.comports()
    for port in ports:
        if "USB" in port.device or "ACM" in port.device:
            print(f"✅ Found Arduino on: {port.device} ({port.description})")
            return port.device
    return None

# ---------- FastAPI setup ----------
def read_html():
    try:
        with open("templates/index.html", "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return "<h1>Error: index.html missing</h1>"

HTML_CONTENT = read_html()

app = FastAPI()

@app.on_event("startup")
def startup():
    global main_loop
    main_loop = asyncio.get_running_loop()

@app.get("/")
async def root():
    return HTMLResponse(content=HTML_CONTENT)

@app.get("/events")
async def sse_events(request: Request):
    async def event_generator():
        with lock:
            current = active
        yield f"data: {{\"active\": {str(current).lower()}}}\n\n"
        while True:
            try:
                event = await event_queue.get()
                yield event
            except asyncio.CancelledError:
                break
    return StreamingResponse(event_generator(), media_type="text/event-stream")

@app.get("/Maintenance")
async def maintenance_page():
    with open("templates/maintenance.html", "r") as f:
        return HTMLResponse(content=f.read())
    
@app.post("/arduino/command")
async def arduino_command(payload: ArduinoCommand):
    cmd = payload.cmd.strip()
    if cmd not in ALLOWED_COMMANDS:
        raise HTTPException(status_code=400, detail=f"Unknown command: {cmd}")
    write_arduino(cmd.encode())
    return {"status": "ok", "cmd": cmd}

# ---------- Main entry point ----------

def wait_for_ollama(host="192.168.4.30", port=11434, timeout=60, interval=2):
    url = f"http://{host}:{port}"
    start = time.time()
    
    while True:
        try:
            response = requests.get(url, timeout=3)
            if response.status_code == 200:
                print(f"✓ Connected to Ollama at {url}")
                return True
        except requests.exceptions.ConnectionError:
            print(f"Waiting for Ollama at {url}...")
        except requests.exceptions.Timeout:
            print(f"Request timed out, retrying...")
        
        if time.time() - start > timeout:
            raise TimeoutError(f"Could not connect to Ollama at {url} within {timeout}s")
        
        time.sleep(interval)

if __name__ == "__main__":
    Off_arduino = False
    Offline_Test = True

    if not Offline_Test:
        wait_for_ollama("192.168.137.1", 11434)
        wait_for_ollama("192.168.137.1", 11435)
        print("✓ Connected to Ollama at http://192.168.1.3")
        api_list = [
            "http://192.168.137.1:11434/"
        ]

        from AddistionFunctionTools import Hand_command, prompt_function

        stt = AudioTranscriber(MODEL_SIZE="small")
        tts = OrpheusTTS(voice="Tara", url="http://192.168.137.1:11434/", model="legraphista/Orpheus:3b-ft-q2_k")
        client = OllamaIPEX(model_name="llama3.1:8b", ollama_url="http://192.168.137.1:11434/")
        clear_ai_memory()

    for _ in range(5):
        beep(1000, 0.2, 0.2)
        time.sleep(0.1)

    if not Off_arduino:
        arduino_port = find_arduino_port()
        if not arduino_port:
            print("❌ Arduino not found. Exiting.")
            exit()
        try:
            ser = serial.Serial(arduino_port, 9600, timeout=0.1)
            time.sleep(2)
        except Exception as e:
            print(f"❌ Serial error: {e}")
            for _ in range(10):
                beep(3000, 0.2)
                time.sleep(0.1)
            exit()

        threading.Thread(target=serial_reader, daemon=True).start() 
        write_arduino(b'CVD')
        if not Offline_Test:
            write_arduino(b'<START>')
    else:
        set_active(True)
    threading.Thread(target=ai_interaction_loop, daemon=True).start()

    uvicorn.run(app, host="0.0.0.0", port=8000)