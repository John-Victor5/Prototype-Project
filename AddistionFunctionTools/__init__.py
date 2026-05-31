from .date import get_current_datetime, date_tool_schema
from .inquery import get_inquery_info, get_appointment_info, get_navigation_info, get_inquery_schema, get_navigation_schema, get_appointment_schema
prompt_function = [
    date_tool_schema,
    get_inquery_schema,
    get_navigation_schema,
    get_appointment_schema
]
Hand_command = {
    "get_current_datetime": get_current_datetime,
    "get_inquery_info": get_inquery_info,
    "get_navigation_info": get_navigation_info,
    "get_appointment_info": get_appointment_info
}

__all__ = ["Handle_command", "prompt_function"]