import requests
import json
import torch
import numpy as np
from scipy.io import wavfile
import re
import sys
import threading
import queue
import time
import random

# Check for SNAC library
try:
    from snac import SNAC
except ImportError:
    print("Error: 'snac' library not found. Please run: pip install snac")
    sys.exit(1)

AVAILABLE_VOICES = ["Tara", "Leo", "Leah", "Jess", "Dan", "Zac", "Mia", "Zoe"]

try:
    import sounddevice as sd
    def beep(freq: float, duration: float, repeats: int = 1, volume: float =0.3):
        for _ in range(repeats):
            fs = 44100
            t = np.arange(int(fs * duration)) / fs
            samples = (np.sin(2 * np.pi * freq * t)).astype(np.float32)
            sd.play(samples * volume, fs)
            sd.wait()
    active_beeping = True
except ModuleNotFoundError:
    active_beeping = False

class OrpheusTTS:
    def __init__(self, voice="Tara", url="http://localhost:11434/", max_tokens=4096, 
                 temperature=0.6, top_p=0.9, model="legraphista/Orpheus:3b-ft-q4_k_m",
                 old_hardware=False):

        self.voice = voice if voice in AVAILABLE_VOICES else "Tara"
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.top_p = top_p
        self.sample_rate = 24000
        self.old_hardware = old_hardware
        
        self.results_map = {}
        self.tasks_in_progress = set() 
        self.result_lock = threading.Lock()
        self.chunk_counter = 0 
        
        self.text_queue = queue.Queue()       
        self.decoding_queue = queue.Queue()   
        
        self.worker_threads = []
        self._generate_seed()

        if torch.cuda.is_available():
            self.device = "cuda"
        elif torch.xpu.is_available():
            self.device = "xpu"
        else:
            self.device = "cpu"
            if self.old_hardware:
                torch.backends.nnpack.enabled = False
            
        print(f"Loading SNAC model on {self.device.upper()}...")
        self.snac_model = SNAC.from_pretrained("hubertsiuzdak/snac_24khz").eval().to(self.device)

        self.decoder_thread = threading.Thread(target=self._decoder_worker, daemon=True)
        self.decoder_thread.start()
        self.requests_helper(url)

    def _generate_seed(self):
        self.seed = random.randint(0, 1000000)

    def requests_helper(self, url):
        base_url = url.split("/v1")[0].rstrip("/")
        api_url = base_url + "/v1/completions"
        try:
            resp = requests.get(f"{base_url}/api/tags", timeout=5)
            if resp.status_code == 200:
                t = threading.Thread(target=self._network_worker, args=(api_url,), daemon=True)
                t.start()
                self.worker_threads.append(t)
            else:
                print(f"Server {base_url} returned status {resp.status_code}")
        except Exception as e:
            print(f"Failed to connect to {base_url}: {e}")

    def _network_worker(self, server_url):
        while True:
            try:
                index, text = self.text_queue.get(timeout=1)
            except queue.Empty:
                continue

            with self.result_lock:
                if index in self.results_map or index in self.tasks_in_progress:
                    self.text_queue.task_done()
                    continue
                self.tasks_in_progress.add(index)

            print(f"[Network] Generating Tokens for Chunk ID: {index}")
            prompt = f"<|audio|>{self.voice.lower()}: {text}<|eot_id|>"
            token_ids = self._get_tokens_from_llm(prompt, server_url)
            
            if token_ids and len(token_ids) > 14:
                self.decoding_queue.put((index, token_ids))
            else:
                print(f"[Error] Chunk ID: {index} returned invalid/empty tokens. Retrying...")
                with self.result_lock:
                    self.tasks_in_progress.discard(index)
                self.text_queue.put((index, text))
            
            self.text_queue.task_done()

    def _decoder_worker(self):
        while True:
            try:
                index, token_ids = self.decoding_queue.get(timeout=1)
            except queue.Empty:
                continue

            audio_data = self._decode_snac_tokens(token_ids)

            if audio_data is not None and len(audio_data) > 2400:
                with self.result_lock:
                    self.results_map[index] = audio_data
                    self.tasks_in_progress.discard(index)
                print(f"[Decoder] Audio Complete for Chunk ID: {index}")
            else:
                print(f"[Error] Decoder failed for Chunk ID: {index}. Re-queueing...")
                with self.result_lock:
                    self.tasks_in_progress.discard(index)
                # Logic to retry could be added here
            
            self.decoding_queue.task_done()

    def _get_tokens_from_llm(self, prompt, server_url):
        token_ids = []
        token_count = 0
        payload = {
            "model": self.model, "prompt": prompt, "max_tokens": self.max_tokens,
            "temperature": self.temperature, "seed": self.seed, "top_p": self.top_p, "stream": True 
        }
        try:
            with requests.post(server_url, json=payload, stream=True, timeout=(10, None)) as response:
                if response.status_code != 200: return None
                for line in response.iter_lines():
                    if not line: continue
                    decoded_line = line.decode('utf-8').replace('data: ', '').strip()
                    if decoded_line == "[DONE]": break
                    try:
                        json_obj = json.loads(decoded_line)
                        text_chunk = json_obj['choices'][0]['text']
                        matches = re.findall(r'<custom_token_(\d+)>', text_chunk)
                        for num_str in matches:
                            raw_id = int(num_str)
                            decoded_id = raw_id - 10 - ((token_count % 7) * 4096)
                            if decoded_id >= 0:
                                token_ids.append(decoded_id)
                                token_count += 1
                    except: pass
            return token_ids
        except Exception:
            return None

    def _decode_snac_tokens(self, token_ids):
        valid_length = (len(token_ids) // 7) * 7
        token_ids = token_ids[:valid_length]
        num_frames = valid_length // 7
        if num_frames == 0: return None
        
        codes_0, codes_1, codes_2 = [], [], []
        for i in range(num_frames):
            idx = i * 7
            codes_0.append(token_ids[idx])     
            codes_1.extend([token_ids[idx+1], token_ids[idx+4]])   
            codes_2.extend([token_ids[idx+2], token_ids[idx+3], token_ids[idx+5], token_ids[idx+6]])   

        with torch.no_grad():
            input_codes = [
                torch.tensor([codes_0], dtype=torch.long).to(self.device),
                torch.tensor([codes_1], dtype=torch.long).to(self.device),
                torch.tensor([codes_2], dtype=torch.long).to(self.device)
            ]
            audio_hat = self.snac_model.decode(input_codes)
        return audio_hat.squeeze().cpu().numpy()

    def feed_text(self, text):
        print(f"[Queue] Added Task ID: {self.chunk_counter}")
        self.text_queue.put((self.chunk_counter, text))
        self.chunk_counter += 1

    def export_generate(self, output_file=None, smooth_merge=0.5):
        print("\n[Export] Finalizing tasks...")
        self.text_queue.join()
        self.decoding_queue.join()
        
        if not self.results_map:
            print("Error: No audio chunks were successfully generated.")
            return None

        sorted_indices = sorted(self.results_map.keys())
        expected_range = list(range(0, self.chunk_counter))
        
        if sorted_indices != expected_range:
            missing = set(expected_range) - set(sorted_indices)
            print(f"CRITICAL WARNING: Missing IDs {missing}. Export may be out of sync!")

        print(f"[Export] Starting merge of {len(sorted_indices)} chunks...")

        combined_audio = None
        fade_samples = int(smooth_merge * self.sample_rate)

        for pos, idx in enumerate(sorted_indices):
            print(f" >> Processing ID: {idx} | Position in line: {pos+1}/{len(sorted_indices)}")
            next_chunk = self.results_map[idx]
            
            if combined_audio is None:
                combined_audio = next_chunk
                continue

            actual_fade = min(fade_samples, len(combined_audio), len(next_chunk))
            if actual_fade > 0:
                main_part = combined_audio[:-actual_fade]
                fade_out_part = combined_audio[-actual_fade:]
                fade_in_part = next_chunk[:actual_fade]
                remaining_part = next_chunk[actual_fade:]
                
                fade_out_curve = np.linspace(1.0, 0.0, actual_fade)
                fade_in_curve = np.linspace(0.0, 1.0, actual_fade)
                merged_section = (fade_out_part * fade_out_curve) + (fade_in_part * fade_in_curve)
                combined_audio = np.concatenate([main_part, merged_section, remaining_part])
            else:
                combined_audio = np.concatenate([combined_audio, next_chunk])
        
        max_val = np.max(np.abs(combined_audio))
        if max_val > 1.0: combined_audio = combined_audio / max_val
        
        if output_file:
            int_audio = (combined_audio * 32767).astype(np.int16)
            wavfile.write(output_file, self.sample_rate, int_audio)
            print(f"\n[Success] File saved as: {output_file}")

        self.results_map = {}
        self.chunk_counter = 0
        return combined_audio

if __name__ == "__main__":
    tts = OrpheusTTS(url="http://localhost:11434/", old_hardware=True)

    tts.feed_text("Wait, let me check the first part.")
    tts.feed_text("This is the second part of the merge.")
    tts.feed_text("And finally, the third part completes the sequence.")

    audio = tts.export_generate("final_output.wav")