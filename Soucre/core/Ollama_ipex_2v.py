import requests
import json
import os
import base64
import copy
from typing import List, Dict, Optional, Union, Generator, Any

"""
[GIN-debug] POST   /api/pull                 --> ollama/server.(*Server).PullHandler-fm (5 handlers)
[GIN-debug] POST   /api/generate             --> ollama/server.(*Server).GenerateHandler-fm (5 handlers)
[GIN-debug] POST   /api/chat                 --> ollama/server.(*Server).ChatHandler-fm (5 handlers)
[GIN-debug] POST   /api/embed                --> ollama/server.(*Server).EmbedHandler-fm (5 handlers)
[GIN-debug] POST   /api/embeddings           --> ollama/server.(*Server).EmbeddingsHandler-fm (5 handlers)
[GIN-debug] POST   /api/create               --> ollama/server.(*Server).CreateHandler-fm (5 handlers)
[GIN-debug] POST   /api/push                 --> ollama/server.(*Server).PushHandler-fm (5 handlers)
[GIN-debug] POST   /api/copy                 --> ollama/server.(*Server).CopyHandler-fm (5 handlers)
[GIN-debug] DELETE /api/delete               --> ollama/server.(*Server).DeleteHandler-fm (5 handlers)
[GIN-debug] POST   /api/show                 --> ollama/server.(*Server).ShowHandler-fm (5 handlers)
[GIN-debug] POST   /api/blobs/:digest        --> ollama/server.(*Server).CreateBlobHandler-fm (5 handlers)
[GIN-debug] HEAD   /api/blobs/:digest        --> ollama/server.(*Server).HeadBlobHandler-fm (5 handlers)
[GIN-debug] GET    /api/ps                   --> ollama/server.(*Server).PsHandler-fm (5 handlers)
[GIN-debug] POST   /v1/chat/completions      --> ollama/server.(*Server).ChatHandler-fm (6 handlers)
[GIN-debug] POST   /v1/completions           --> ollama/server.(*Server).GenerateHandler-fm (6 handlers)
[GIN-debug] POST   /v1/embeddings            --> ollama/server.(*Server).EmbedHandler-fm (6 handlers)
[GIN-debug] GET    /v1/models                --> ollama/server.(*Server).ListHandler-fm (6 handlers)
[GIN-debug] GET    /v1/models/:model         --> ollama/server.(*Server).ShowHandler-fm (6 handlers)
[GIN-debug] GET    /                         --> ollama/server.(*Server).GenerateRoutes.func1 (5 handlers)
[GIN-debug] GET    /api/tags                 --> ollama/server.(*Server).ListHandler-fm (5 handlers)
[GIN-debug] GET    /api/version              --> ollama/server.(*Server).GenerateRoutes.func2 (5 handlers)
[GIN-debug] HEAD   /                         --> ollama/server.(*Server).GenerateRoutes.func1 (5 handlers)
[GIN-debug] HEAD   /api/tags                 --> ollama/server.(*Server).ListHandler-fm (5 handlers)
[GIN-debug] HEAD   /api/version              --> ollama/server.(*Server).GenerateRoutes.func2 (5 handlers)
"""


"""
Error Beeping:

3 beeps Embed failed
4 beeps Tools failed
5 beeps Failed to request terminate
5 fast beeps Olama server unreachable
"""
try:
    import sounddevice as sd
    import numpy as np
    def beep(freq: float, duration: float, repeats: int = 1, volume: float =0.3):
        for _ in range(repeats):
            fs = 44100
            t = np.arange(int(fs * duration)) / fs
            samples = (np.sin(2 * np.pi * freq * t)).astype(np.float32)
            sd.play(samples * volume, fs)
            sd.wait()

    active_beeping = True
except ModuleNotFoundError:
    print("Sounddevice module not found. Skipping beeps.")
    active_beeping = False

#%%
class OllamaIPEX:
    def __init__(
        self, 
        ollama_url: str = "http://localhost:11434", 
        model_name: str = "gemma3:4b", 
        options: Optional[Dict[str, Any]] = None,
        check_connection: bool = True,
        autopull: bool = False
    ):
        self.base_url = ollama_url.rstrip('/')
        self.model_name = model_name
        self.session = requests.Session()
        self.options = options or {}
        self.last_response_raw = None 
        self.autopull = autopull

        if self.autopull:
            self.is_model_downloaded(self.model_name)

        if check_connection:
            if not self._check_model_availability():
                print(f"Model '{self.model_name}' not found or Ollama server unreachable at {self.base_url}")

    def _check_model_availability(self) -> bool:
        """Checks if the Ollama server is up and the model exists."""
        try:
            response = self.session.get(f"{self.base_url}/api/tags", timeout=5)
            response.raise_for_status()
            models = [m['name'] for m in response.json().get('models', [])]
            is_available = any(self.model_name in m for m in models)
            return is_available
        except requests.RequestException as e:
            beep(2000, 0.1, 5) if active_beeping else None
            print(f"Connection check failed: {e}")
            return False

    def _process_image_to_base64(self, image_input: str) -> Optional[str]:
        """Converts image path to base64. Returns None if failed, or the string if not a file."""
        if os.path.isfile(image_input):
            try:
                with open(image_input, "rb") as image_file:
                    return base64.b64encode(image_file.read()).decode('utf-8')
            except OSError as e:
                print(f"Failed to read image file {image_input}: {e}")
                return None
        return image_input
    
    def get_local_models(self) -> List[str]:
        """Returns a list of all model names currently downloaded."""
        try:
            response = self.session.get(f"{self.base_url}/api/tags", timeout=5)
            response.raise_for_status()
            return [m['name'] for m in response.json().get('models', [])]
        except Exception as e:
            beep(2000, 0.1, 5) if active_beeping else None
            print(f"Error fetching local models: {e}")
            return []

    def is_model_downloaded(self, model_name: str) -> bool:
        """Checks if a specific model exists in the local library."""
        models = self.get_local_models()
        # Checks for exact match or match without the :latest tag
        return model_name in models or f"{model_name}:latest" in models

    def chat(
        self, 
        messages: List[Dict[str, Any]], 
        stream: bool = False, 
        tools: Optional[List[Dict[str, Any]]] = None, 
        available_functions: Optional[Dict[str, callable]] = None,
        images: Optional[List[str]] = None,
        recursion_limit: int = 5,
        raw_output_tools: bool = False
    ) -> Union[str, Dict[str, Any], Generator[str, None, None]]:
        """
        Sends a chat request to Ollama.
        """
        current_messages = copy.deepcopy(messages)

        if images:
            encoded_images = []
            for img in images:
                processed = self._process_image_to_base64(img)
                if processed:
                    encoded_images.append(processed)
            
            if encoded_images and current_messages:
                if current_messages[-1].get("role") == "user":
                    current_messages[-1]["images"] = encoded_images
                else:
                    print("Images provided but last message is not from 'user'. Attaching to new message.")
                    current_messages.append({"role": "user", "content": "", "images": encoded_images})

        payload = {
            "model": self.model_name,
            "messages": current_messages,
            "stream": stream,
        }
        
        if self.options:
            payload["options"] = self.options

        if tools:
            payload["stream"] = False 
            payload["tools"] = tools

        try:
            response = self.session.post(
                f"{self.base_url}/api/chat", 
                json=payload, 
                stream=stream,
                timeout=None
            )
            response.raise_for_status()

            if stream and not tools:
                return self._handle_streaming(response)

            try:
                response_data = response.json()
            except json.JSONDecodeError:
                return "Error: Invalid JSON response from server."
            finally:
                response.close()

            self.last_response_raw = response_data
            message = response_data.get("message", {})
            
            if tools and message.get("tool_calls"):
                if available_functions:
                    if recursion_limit <= 0:
                        return "Error: Max tool recursion limit reached."
                    
                    return self._handle_tool_execution(
                        current_messages, 
                        message, 
                        tools, 
                        available_functions,
                        recursion_limit,
                        raw_output_tools
                    )
                else:
                    return message 

            return message.get("content", "")

        except requests.RequestException as e:
            print(f"Ollama API Error: {e}")
            return f"Error: Could not connect to Ollama. {str(e)}"

    def _handle_streaming(self, response: requests.Response) -> Generator[str, None, None]:
        """Yields content chunks from the streaming response."""
        try:
            for line in response.iter_lines():
                if not line: 
                    continue
                try:
                    data = json.loads(line.decode("utf-8"))
                    
                    if data.get("done"):
                        self.last_response_raw = data
                        break
                        
                    token = data.get("message", {}).get("content", "")
                    if token:
                        yield token
                except json.JSONDecodeError:
                    continue
        finally:
            response.close()

    def _handle_tool_execution(
        self, 
        messages: List[Dict], 
        initial_msg: Dict, 
        tools: List[Dict], 
        functions: Dict,
        recursion_limit: int,
        raw_output_tools: bool
    ):
        """Executes tools and calls chat again with the results."""
        print(f"Executing tools. Remaining recursion: {recursion_limit}")
        
        messages.append(initial_msg)
        raw = []
        
        for tool in initial_msg.get("tool_calls", []):
            fn_name = tool["function"]["name"]
            fn_args = tool["function"]["arguments"]
            
            print(f"Calling function: {fn_name} with args: {fn_args}")
            
            if functions and fn_name in functions:
                try:
                    result = functions[fn_name](**fn_args)
                    content_str = json.dumps(result) if isinstance(result, (dict, list)) else str(result)
                except Exception as e:
                    print(f"Tool execution failed: {e}")
                    if active_beeping: beep(2500, 0.2, 3)
                    content_str = f"Error executing tool {fn_name}: {str(e)}"
            else:
                content_str = f"Error: Tool {fn_name} not found."
            
            if raw_output_tools:
                raw.append({
                    "role": "tool", 
                    "content": content_str,
                    "tool_name": fn_name
                })
            else:
                messages.append({
                    "role": "tool", 
                    "content": content_str,
                    "tool_name": fn_name
                })

        if raw_output_tools:
            """
            > Tools output
            > Cant do repeating ability
            > Good for complex tasks
            > Manual code generation
            """
            return [initial_msg] + raw
        else:
            """
            > Straight to Generate
            > No tools output
            > Good repeating ability when ai cant do input string incorrectly
            """
            return self.chat(
                messages=messages,
                stream=False,
                tools=tools,
                available_functions=functions,
                recursion_limit=recursion_limit - 1
            )

    def embed(self, input_data: Union[str, List[str]]) -> List[List[float]]:
        """Generate embeddings for input text."""
        payload = {
            "model": self.model_name, 
            "input": input_data
        }
        if self.options: 
            payload["options"] = self.options
            
        try:
            response = self.session.post(f"{self.base_url}/api/embed", json=payload)
            response.raise_for_status()
            data = response.json()
            return data.get("embeddings", [])
        except requests.RequestException as e:
            if active_beeping: beep(2500, 0.2, 5)
            print(f"Embed Error: {e}")
            return []
        
    def terminate_model(self, specific_model: Optional[str] = None):
        """Unloads a specific model from memory by setting keep_alive to 0."""
        target = specific_model or self.model_name
        try:
            # The standard way to unload a model in Ollama is sending keep_alive: 0
            response = self.session.post(
                f"{self.base_url}/api/generate", 
                json={"model": target, "keep_alive": 0}
            )
            if response.status_code == 200:
                print(f"Model '{target}' terminated/unloaded.")
        except Exception as e:
            if active_beeping: beep(2500, 0.2, 5)
            print(f"Error terminating model {target}: {e}")
        
    def terminate_all_models(self):
        """Checks for all currently running models and unloads them."""
        try:
            # /api/ps shows currently loaded models
            response = self.session.get(f"{self.base_url}/api/ps")
            response.raise_for_status()
            running_models = response.json().get('models', [])
            
            if not running_models:
                print("No models currently loaded in memory.")
                return

            for model in running_models:
                name = model.get('name')
                self.terminate_model(name)
        except Exception as e:
            if active_beeping: beep(2500, 0.2, 5) 
            print(f"Error terminating all models: {e}")
        
    def pull_model(self, model_name: Union[str, List[str]]):
        """Pulls models only if they are not already present."""
        targets = [model_name] if isinstance(model_name, str) else model_name
        
        local_models = self.get_local_models()
        
        for model in targets:
            if model in local_models or f"{model}:latest" in local_models:
                print(f"Model '{model}' already exists. Skipping pull.")
                continue
            
            print(f"Pulling model '{model}'...")
            try:
                with self.session.post(
                    f"{self.base_url}/api/pull", 
                    json={"name": model}, 
                    stream=True
                ) as response:
                    response.raise_for_status()
                    print(f"Successfully started pulling {model}...")
            except Exception as e:
                if active_beeping: beep(2500, 0.2, 5) 
                print(f"Failed to pull {model}: {e}")
                
####################################################################
####################################################################
####################################################################

#%%

if __name__ == "__main__":
    client = OllamaIPEX(model_name="llama3")

    messages = [{"role": "user", "content": "Why is the sky blue?"}]
    response = client.chat(messages)
    print(response)

    print("Bot: ", end="", flush=True)
    for chunk in client.chat(messages, stream=True):
        print(chunk, end="", flush=True)

#%%

if __name__ == "__main__":
    vision_client = OllamaIPEX(model_name="gemini-3-flash-preview")
    response = vision_client.chat(
        messages=[{"role": "user", "content": "What is in this image?"}],
        images=["./cat_photo.jpg"]
    )
    print(response)

#%%

if __name__ == "__main__":
    def get_weather(city: str):
        return {"temp": 25, "desc": "Sunny", "city": city}

    tools_schema = [{
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get current weather",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {"type": "string"}
                },
                "required": ["city"]
            }
        }
    }]

    fn_map = {"get_weather": get_weather}
    tools_client = OllamaIPEX(model_name="llama3.1:8b")
    response = tools_client.chat(
        messages=[{"role": "user", "content": "What is the weather in Tokyo?"}],
        tools=tools_schema,
        available_functions=fn_map
    )
    print(response)

#%%

if __name__ == "__main__":
    # Initialize the client (make sure you use an embedding model like 'nomic-embed-text' or 'all-minilm')
    # Standard LLMs (like llama3) work too, but dedicated embed models are faster/better.
    client = OllamaIPEX(model_name="nomic-embed-text") 

    text = "Hello, world!"
    embeddings = client.embed(text)

    # The API always returns a list of lists [[float, float, ...]]
    if embeddings:
        vector = embeddings[0]
        print(f"Dimension size: {len(vector)}") # e.g., 768 or 1024
        print(f"First 5 values: {vector[:5]}")

#%%
if __name__ == "__main__":
    client = OllamaIPEX(model_name="nomic-embed-text")

    documents = [
        "The sky is blue.",
        "The grass is green.",
        "Artificial Intelligence is growing."
    ]

    vectors = client.embed(documents)

    print(f"Generated {len(vectors)} vectors.")

    for i, vec in enumerate(vectors):
        print(f"Document {i+1} vector length: {len(vec)}")

#%%
if __name__ == "__main__":
    print("\n--- Tool Use (Raw Output) ---")
    
    def get_stock_price(ticker: str):
        return {"ticker": ticker, "price": 150.00}

    tools_schema = [{
        "type": "function",
        "function": {
            "name": "get_stock_price",
            "description": "Get stock price",
            "parameters": {
                "type": "object",
                "properties": {"ticker": {"type": "string"}},
                "required": ["ticker"]
            }
        }
    }]
    
    fn_map = {"get_stock_price": get_stock_price}
    raw_client = OllamaIPEX(model_name="llama3.1:8b")
    conversation_history = raw_client.chat(
        messages=[{"role": "user", "content": "What is the price of AAPL?"}],
        tools=tools_schema,
        available_functions=fn_map,
        raw_output_tools=True
    )

    if isinstance(conversation_history, list):
        print("Received Conversation History (Raw):")
        for msg in conversation_history:
            role = msg.get('role').upper()
            content = msg.get('content')
            tool_calls = msg.get('tool_calls')
            if tool_calls:
                print(f"[{role}] Calling Tool: {tool_calls[0]['function']['name']}")
            else:
                print(f"[{role}] {content}")
        
        final_response = raw_client.chat(conversation_history)
        print(f"\nManual Follow-up: {final_response}")
# %%
