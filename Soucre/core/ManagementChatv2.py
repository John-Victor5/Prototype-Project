import json
import os
import sys
from typing import List, Dict, Literal, Union, Any
from datetime import datetime

# Setup paths
path_ = sys.prefix.replace(".venv", "")
os.makedirs(os.path.join(path_, 'cache'), exist_ok=True)
cache_dir = os.path.abspath('cache')

class HybridMemory:
    def __init__(self, 
                 file_path: str, 
                 system_prompt: str, 
                 max_context_window: int = 20, 
                 max_storage_limit: int = 1000,
                 NAME = "KIRAIMI",
                 default_model: str = "llama3.2"
                 ):
        
        # Default configuration structure
        self.default_config = {
            
            "OPTIMIZE_MODEL": {
                "SEED": None,
                "temperature": None,
                "top_p": None,
                "top_k": None,
                "min_p": None,
                "max_tokens": None,
                "repeat_last_n": None,
                "repeat_penalty": None,
                "num_predict": None,
                "num_ctx": None
            },
            "OPTIMIZE_TTS": {
                "temperature": None,
                "top_p": None,
                "top_k": None,
                "min_p": None
            },
            "NAME": NAME,
            "MODEL": default_model,
            "HISTORY": []
        }

        if not os.path.exists(os.path.join(cache_dir, file_path)):
            file_path = os.path.join(cache_dir, file_path)
        
        self.file_path = file_path
        self.max_storage_limit = max_storage_limit
        self.max_context_window = max_context_window
        self.system_prompt = system_prompt
        
        # Load data and separate config from history
        loaded_data = self._load_from_file()
        self.config_data = {k: v for k, v in loaded_data.items() if k != "HISTORY"}
        self.storage_memories: List[Dict[str, str]] = loaded_data.get("HISTORY", [])

        # Initialize RAM context (Stripped of timestamps)
        # Ensure system prompt is context[0]
        self.context_memories: List[Dict[str, str]] = [
            {"role": "system", "content": self.system_prompt}
        ]
        
        # Load recent history into context, stripping timestamps
        recent_history_full = self.storage_memories[-self.max_context_window:]
        recent_history_clean = [
            {"role": x["role"], "content": x["content"]} 
            for x in recent_history_full 
            if x["role"] != "system" # Avoid duplicating system prompt if it exists in storage
        ]
        self.context_memories.extend(recent_history_clean)

    def _load_from_file(self) -> Dict[str, Any]:
        if os.path.exists(self.file_path):
            try:
                with open(self.file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    
                    # Migration: If old file format (List) is found, convert to new format (Dict)
                    if isinstance(data, list):
                        new_structure = self.default_config.copy()
                        new_structure["HISTORY"] = data
                        return new_structure
                    
                    # Ensure all keys exist by merging with default
                    merged = self.default_config.copy()
                    merged.update(data)
                    return merged
            except Exception as e:
                print(f"Error loading: {e}")
        return self.default_config.copy()

    def _save_to_file(self):
        try:
            # Reconstruct the full object
            save_data = self.config_data.copy()
            save_data["HISTORY"] = self.storage_memories
            
            with open(self.file_path, 'w', encoding='utf-8') as f:
                json.dump(save_data, f, ensure_ascii=False, indent=4)
        except Exception as e:
            print(f"Error saving: {e}")

    def add_chat(self, message: str, role: Literal["user", "assistant", "tools"]) -> List[Dict[str, str]]:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # 1. Prepare entry with Time for Storage (File)
        file_entry = {"role": role, "content": message, "timestamp": timestamp}
        self.storage_memories.append(file_entry)
        
        if len(self.storage_memories) > self.max_storage_limit:
            self.storage_memories = self.storage_memories[-self.max_storage_limit:]
        
        self._save_to_file()

        # 2. Prepare entry without Time for Context (LLM API)
        api_entry = {"role": role, "content": message}
        self.context_memories.append(api_entry)
        
        # Manage Context Window (Keep System Prompt at index 0)
        # Slicing logic: Keep [0] + last N items
        if len(self.context_memories) > (self.max_context_window + 1):
            self.context_memories = [self.context_memories[0]] + self.context_memories[-self.max_context_window:]

        return self.context_memories

    def add_messages(self, messages: List[Dict[str, Any]]) -> List[Dict[str, str]]:
        """
        Save a full conversation history including tool calls.
        """
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # First, find and add the assistant's message that contains the tool_calls
        for msg in messages:
            if msg.get("role") == "assistant" and "tool_calls" in msg:
                assistant_message = {
                    "role": "assistant",
                    "content": msg.get("content", ""),
                    "tool_calls": msg["tool_calls"],
                    "timestamp": timestamp,
                }
                self.storage_memories.append(assistant_message)
                self.context_memories.append({
                    "role": "assistant",
                    "content": msg.get("content", ""),
                    "tool_calls": msg["tool_calls"],
                })
                break  # Assume only one assistant message with tool_calls per turn

        # Now, add the results from the tool calls
        for msg in messages:
            if msg.get("role") == "tool":
                file_entry = {
                    "role": "tool",
                    "content": msg.get("content", ""),
                    "tool_call_id": msg.get("tool_call_id"), # Or however you identify the corresponding call
                    "time": timestamp
                }
                self.storage_memories.append(file_entry)

                ctx_entry = {
                    "role": "tool",
                    "content": msg.get("content", ""),
                    "tool_call_id": msg.get("tool_call_id"),
                }
                self.context_memories.append(ctx_entry)


        # Enforce limits
        if len(self.storage_memories) > self.max_storage_limit:
            self.storage_memories = self.storage_memories[-self.max_storage_limit:]

        if len(self.context_memories) > (self.max_context_window + 1):
            self.context_memories = [self.context_memories[0]] + self.context_memories[-self.max_context_window:]

        self._save_to_file()
        return self.context_memories

    def get_rag_data(self) -> List[Dict[str, str]]:
        """
        Formats memory for RAG. 
        Merges: User -> (User...) -> AI.
        """
        rag_dataset = []
        current_block = ""
        has_ai_reply = False
        is_block_active = False

        for mem in self.storage_memories:
            role = mem['role']
            content = mem['content']
            # Note: We ignore 'time' here for RAG text generation
            
            if role == "user" and has_ai_reply:
                rag_dataset.append({"Context": current_block.strip()})
                current_block = ""
                has_ai_reply = False
                is_block_active = False
            if role == "user":
                is_block_active = True
            
            if not is_block_active:
                continue

            label = "User" if role == "user" else "AI"
            current_block += f"{label}:\n{content}\n"
            if role == "assistant":
                has_ai_reply = True

        if current_block and has_ai_reply:
            rag_dataset.append({"Context": current_block.strip()})

        return rag_dataset

    # Helper to update config parameters if needed
    def update_config(self, key: str, value: any):
        if key in self.config_data:
            self.config_data[key] = value
        elif key in self.config_data.get("OPTIMIZE_MODEL", {}):
            self.config_data["OPTIMIZE_MODEL"][key] = value
        self._save_to_file()

    def get_config(self, key: str = None, subkey: str = None) -> Any:
        """
        Retrieves config parameters.
        """
        if key is None:
            return self.config_data

        if subkey is None:
            return self.config_data.get(key)

        return self.config_data.get(key, {}).get(subkey)

if __name__ == "__main__":
    # Initialize
    mem = HybridMemory(
        file_path="chat_history.json",
        system_prompt="You are a helpful assistant."
    )

    print("--- 1. User sends message ---")
    context = mem.add_chat("Hello, who are you?", role="user")
    # Verify context output (No time)
    print(f"Context for LLM: {context}")

    print("\n--- 2. Assistant replies ---")
    assistant_response = "I am an AI assistant."
    context = mem.add_chat(assistant_response, role="assistant")
    print(f"Context for LLM: {context[-1]}")

    print("\n--- 3. Verify Saved File Content ---")
    # Verify file content (Has time)
    if os.path.exists("cache/chat_history.json"):
        with open("cache/chat_history.json", "r") as f:
            saved_data = json.load(f)
            last_entry = saved_data["HISTORY"][-1]
            print(f"Saved Metadata Model: {saved_data['MODEL']}")
            print(f"Saved Last Entry: {last_entry}")