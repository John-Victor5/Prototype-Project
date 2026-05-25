import sounddevice as sd
import soundfile as sf
import threading

def get_audio_devices():
    devices = sd.query_devices()
    audio_config = {
        "default_index": sd.default.device[1],
        "supported_outputs": {},
        "recommended_index": None
    }
    for i, dev in enumerate(devices):
        if dev['max_output_channels'] > 0:
            audio_config["supported_outputs"][i] = dev['name']
            if "default" in dev['name'].lower():
                audio_config["recommended_index"] = i
            elif "pipewire" in dev['name'].lower() and audio_config["recommended_index"] is None:
                audio_config["recommended_index"] = i
    if audio_config["recommended_index"] is None:
        audio_config["recommended_index"] = audio_config["default_index"]
    return audio_config["recommended_index"]

def Play_Audio_Simultaneous(data, fs, device_indexes=[get_audio_devices()]):
    threads = []

    def play_worker(device_id):
        try:
            sd.play(data, fs, device=device_id, blocking=True)
        except Exception as e:
            print(f"Error on device {device_id}: {e}")

    print(f"Starting playback on devices: {device_indexes}...")
    for dev_id in device_indexes:
        t = threading.Thread(target=play_worker, args=(dev_id,))
        threads.append(t)
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    
    print("Playback finished.")

if __name__ == "__main__":
    filename = "audio.wav" 
    
    try:
        data, fs = sf.read(filename, dtype="float32")
        device_indexes = [5]  
        
        Play_Audio_Simultaneous(data, fs, device_indexes)
        
    except FileNotFoundError:
        print(f"Error: Could not find file '{filename}'")
    except Exception as e:
        print(f"An error occurred: {e}")