import datetime
import json

date_tool_schema = {
    "type": "function",
    "function": {
        "name": "get_current_datetime",
        "description": "Get the current date and day of the week. Use this whenever the user asks 'What day is it?', 'What is the date?', or asks about scheduling relative to today.",
        "parameters": {
            "type": "object",
            "properties": {

            },
            "required": []
        }
    }
}

def get_current_datetime():
    """
    Returns current date details as a JSON string.
    Example output: '{"date": "2024-05-21", "day": "Tuesday", "time": "14:30"}'
    """
    now = datetime.datetime.now()
    
    result = {
        "date": now.strftime("%Y-%m-%d"),
        "day_of_week": now.strftime("%A"),
        "time": now.strftime("%H:%M:%S")
    }
    
    return json.dumps(result)


if __name__ == "__main__":
    print("Tool Output:")
    print(get_current_datetime())