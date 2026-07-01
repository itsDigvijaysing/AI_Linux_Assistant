# Import individual tools
from .do_nothing import tool_definition as do_nothing_def, DoNothing
from .get_report import tool_definition as get_report_def, GetReport
from .go_to_sleep import tool_definition as go_to_sleep_def, GoToSleep
from .slow_clap import tool_definition as slow_clap_def, SlowClap
from .speak import tool_definition as speak_def, Speak
from .vision_look import tool_definition as vision_look_def, VisionLook
from .preferences import (
    get_preferences_definition,
    set_preference_definition,
    GetPreferences,
    SetPreference,
)

# Export all tool definitions
tool_definitions = [
    do_nothing_def,
    get_report_def,
    go_to_sleep_def,
    slow_clap_def,
    speak_def,
    vision_look_def,
    get_preferences_definition,
    set_preference_definition,
]

# Export all tool classes
tool_classes = {
    "do_nothing": DoNothing,
    "get_report": GetReport,
    "go_to_sleep": GoToSleep,
    "slow clap": SlowClap,
    "speak": Speak,
    "vision_look": VisionLook,
    "get_preferences": GetPreferences,
    "set_preference": SetPreference,
}

# Export all tool names
all_tools = list(tool_classes.keys())
