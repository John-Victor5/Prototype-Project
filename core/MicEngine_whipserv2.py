import os
import time
import numpy as np
import sounddevice as sd

CACHE_DIR = os.path.join(os.path.abspath(''), 'cache')
os.makedirs(CACHE_DIR, exist_ok=True)
os.environ['HF_HOME'] = CACHE_DIR
os.environ['XDG_CACHE_HOME'] = CACHE_DIR

import whisper
import torch
from collections import deque

class AudioTranscriber:
    def __init__(
            self,
            SAMPLE_RATE=16000,
            BLOCK_SIZE=4096,
            CHANNELS=1,
            MODEL_SIZE="base",
            NOISE_CALIBRATION_TIME=3.0,
            TALKING_THRESHOLD_DB=20,
            SILENCE_TIMEOUT=2.0,
            PRE_RECORD_SECONDS=1.0
            ):
        print("⏳ Loading Whisper model...")
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.model = whisper.load_model(MODEL_SIZE, device=self.device)
        print(f"✅ Model loaded on {self.device.upper()}")

        self.noise_floor = -80.0
        
        # State variables
        self.is_recording = False
        self.capture_complete = False
        self.last_speech_time = 0
        self.current_recording = []
        
        # Pre-roll buffer (stores last 1 second of audio)
        self.pre_buffer = deque(maxlen=int((SAMPLE_RATE / BLOCK_SIZE) * PRE_RECORD_SECONDS))

        self.CHANNELS = CHANNELS
        self.SAMPLE_RATE = SAMPLE_RATE
        self.BLOCK_SIZE = BLOCK_SIZE
        self.NOISE_CALIBRATION_TIME = NOISE_CALIBRATION_TIME
        self.TALKING_THRESHOLD_DB = TALKING_THRESHOLD_DB
        self.SILENCE_TIMEOUT = SILENCE_TIMEOUT

    def calculate_rms_db(self, indata):
        rms = np.sqrt(np.mean(indata**2))
        return 20 * np.log10(rms) if rms > 0 else -80

    def calibrate_noise(self):
        print("\n🔇 Stay silent... calibrating background noise")
        noise_levels = []
        
        def calibration_callback(indata, frames, time, status):
            noise_levels.append(self.calculate_rms_db(indata))

        with sd.InputStream(callback=calibration_callback, channels=self.CHANNELS, 
                            samplerate=self.SAMPLE_RATE, blocksize=self.BLOCK_SIZE):
            time.sleep(self.NOISE_CALIBRATION_TIME)

        self.noise_floor = sum(noise_levels) / len(noise_levels)
        print(f"✅ Noise floor: {self.noise_floor:.1f} dB")

    def _audio_callback(self, indata, frames, time_info, status):
        """Called automatically by sounddevice when new audio arrives"""
        if self.capture_complete: return

        current_db = self.calculate_rms_db(indata)
        threshold = self.noise_floor + self.TALKING_THRESHOLD_DB
        now = time.time()
        audio_chunk = indata.copy().flatten()

        if current_db > threshold:
            # Talking detected
            self.last_speech_time = now
            if not self.is_recording:
                self.is_recording = True
                # Dump pre-buffer into recording
                self.current_recording.extend(list(self.pre_buffer))
                self.pre_buffer.clear()
            
            self.current_recording.append(audio_chunk)
        
        else:
            # Silence detected
            if self.is_recording:
                self.current_recording.append(audio_chunk)
                # Check for timeout
                if now - self.last_speech_time > self.SILENCE_TIMEOUT:
                    self.is_recording = False
                    self.capture_complete = True # Signal to stop listening
            else:
                self.pre_buffer.append(audio_chunk)

        # Visual feedback
        state = "🔴 REC" if self.is_recording else "👂 Listening"
        print(f"\r{state} | Level: {current_db:.1f} dB    ", end="", flush=True)

    def listen(self, stop_check=None, arduinooverride=None):
        """
        Modified listen: checks stop_check() every 100ms.
        If stop_check returns True, it stops immediately.
        """
        self.current_recording = []
        self.pre_buffer.clear()
        self.is_recording = False
        self.capture_complete = False
        
        print("\n") 

        with sd.InputStream(
            channels=self.CHANNELS,
            samplerate=self.SAMPLE_RATE,
            blocksize=self.BLOCK_SIZE,
            callback=self._audio_callback
        ):
            # The critical change: check for Arduino signal while waiting for speech
            while not self.capture_complete:
                if stop_check and stop_check():
                    print("\n🛑 STT Interrupted by Arduino signal.")
                    return "STOP_SIGNAL" 
                time.sleep(0.1)

        print("\n⏳ Processing...")
        if not self.current_recording:
            return ""
        if arduinooverride: arduinooverride.write(b'B')
        audio_data = np.concatenate(self.current_recording)
        result = self.model.transcribe(audio_data, fp16=(self.device == "cuda"), language="en")
        return result["text"].strip()

    def start(self):
        self.calibrate_noise()

# ------------------ Main Execution ------------------
if __name__ == "__main__":
    app = AudioTranscriber(MODEL_SIZE="small")
    app.start()

    while True:
        # 1. This function BLOCKS until you finish a sentence
        text = app.listen()
        
        if text:
            print(f"🗣️  OUTPUT: {text}")
        else:
            print("❌ No speech detected.")

        # 2. Cooldown
        print("💤 Cooldown 3 seconds...")
        time.sleep(3)