"""Hand-written spoken-style requests (test only, never training). Lowercase, no punctuation, like ASR output."""

S = {
    "set_screen_brightness": [
        "make the screen brighter", "turn the brightness down a bit", "set brightness to forty percent",
        "i can barely see the screen its so dark", "dim the display please", "screen brightness max",
        "uh can you lower the screen brightness", "brightness to 20",
    ],
    "set_volume": [
        "turn the volume down", "its way too loud", "mute the sound", "volume up a little",
        "set the volume to thirty", "i cant hear anything turn it up", "unmute please", "make it quieter",
    ],
    "lock_screen": [
        "lock the screen", "lock my computer", "im stepping away lock it", "lock it up please",
        "can you lock the laptop", "secure the screen i am leaving",
    ],
    "take_screenshot": [
        "take a screenshot", "grab a screenshot of this window", "save a picture of my screen",
        "screenshot please", "capture the screen to a file", "snap the whole screen",
    ],
    "open_app_or_link": [
        "open firefox", "launch spotify", "open youtube dot com", "start vs code", "open the calculator app",
        "can you open gmail", "fire up steam", "open my resume pdf",
    ],
    "search_web": [
        "search the web for pizza near me", "look up the weather in tokyo on google", "search youtube for lofi music",
        "google how to center a div", "find python tutorials on the web", "search github for whisper cpp",
        "look up albert einstein on wikipedia", "search maps for the nearest petrol pump",
    ],
    "control_media": [
        "pause the music", "next song", "skip this track", "resume playback", "stop the video",
        "go back to the previous song", "play", "pause it",
    ],
    "toggle_night_light": [
        "turn on night light", "switch off the night light", "make the screen warmer for the night",
        "enable the blue light filter", "night mode on for my eyes", "disable night light",
    ],
    "set_do_not_disturb": [
        "turn on do not disturb", "silence notifications", "stop showing me notifications for now",
        "turn off do not disturb", "no more popups please im presenting", "mute all notifications",
    ],
    "open_settings": [
        "open wifi settings", "open the sound settings", "go to display settings", "open settings",
        "show me the bluetooth settings page", "open power settings",
    ],
    "open_terminal": [
        "open a terminal", "open the terminal", "i need a new terminal window", "launch a shell window",
        "bring up a command line",
    ],
    "open_file_manager": [
        "open my downloads folder", "show me my documents", "open the file manager", "open files at pictures",
        "show the home folder", "browse my desktop folder",
    ],
    "clipboard": [
        "copy that to the clipboard", "what is on my clipboard", "read the clipboard to me",
        "copy hello world to the clipboard", "put this address on the clipboard", "paste what i copied earlier back to me",
    ],
    "run_command": [
        "list files in my home directory", "update my system packages", "kill the process using port eight thousand",
        "check how big my downloads folder is with du", "restart the bluetooth service", "run git status in my project",
        "show running docker containers", "create a folder called test on the desktop",
    ],
    "set_voice": [
        "switch to a female voice", "change your voice", "use a deeper voice", "talk in a different voice",
        "can you sound like a woman", "go back to the male voice",
    ],
    "go_to_sleep": [
        "go to sleep", "stop listening", "thats all for now thanks", "you can rest now", "bye for now stop listening",
        "goodnight assistant",
    ],
    # candidate new tools
    "get_time": [
        "what time is it", "whats the date today", "what day is it", "tell me the time", "is it monday today",
        "what is todays date",
    ],
    "system_status": [
        "how much battery do i have", "am i connected to the internet", "how much disk space is left",
        "why is my laptop so slow", "whats using all my memory", "check my battery level", "is my cpu running hot",
    ],
    "set_timer": [
        "set a timer for ten minutes", "wake me up at seven am", "remind me to call mom in an hour",
        "set an alarm for six thirty", "timer five minutes for the pasta", "remind me about the meeting at three",
    ],
    "describe_screen": [
        "what is on my screen", "take a screenshot and tell me what it says", "read this error message for me",
        "explain what im looking at", "can you see this window whats wrong", "summarize the page on my screen",
        "look at my screen and describe it",
    ],
    "switch_window": [
        "switch to firefox", "close this window", "minimize everything", "maximize the terminal window",
        "go to the spotify window", "close chrome", "bring the code editor to the front",
    ],
    "type_text": [
        "type dear john thank you for the update", "write hello how are you in this box", "type my email address",
        "dictate this message see you at five", "type the word banana",
    ],
    "toggle_wireless": [
        "turn off wifi", "switch on bluetooth", "enable airplane mode", "disconnect bluetooth",
        "turn the wifi back on", "airplane mode off",
    ],
    "remember": [
        "remember that my locker code is four two one", "what did i tell you about my meeting",
        "make a note that the plumber comes tuesday", "remember i parked on level three",
        "do you remember my wifi password", "note that i owe sam twenty dollars",
    ],
    "get_weather": [
        "whats the weather like", "will it rain tomorrow", "how hot is it in pune today", "weather forecast for the weekend",
        "do i need an umbrella today", "is it cold outside",
    ],
    "find_file": [
        "find my resume", "where is the file called budget", "search my computer for pdf invoices",
        "find the photos from last week", "locate the project folder named robot", "where did i save the tax document",
    ],
    "none": [
        "tell me a joke", "whats the capital of france", "how are you today", "who won the world cup in 2022",
        "what is the volume of a sphere", "how bright is the sun", "i love this song", "screenshots are so annoying",
        "how do i open a stuck jar", "what is a terminal illness", "the music at the party was great",
        "explain quantum computing simply", "write me a poem about rain", "how do night owls sleep",
        "whats the best laptop for gaming", "translate good morning to spanish", "what is two plus two",
        "who invented the light bulb", "can you help me plan a trip to goa", "my eyes hurt",
        "i am so tired today", "what does lock stock and two smoking barrels mean", "thank you",
        "how many legs does a spider have", "recommend a good book", "what is the meaning of life",
        "my friend is very loud", "how do clipboards work", "what is a file system", "sing me a song",
        "do you like music", "tell me about the windows operating system", "what should i cook for dinner",
        "how far is the moon", "is coffee bad for you", "why is the sky blue", "can you keep a secret",
        "i had a weird dream last night", "how do i become a better programmer", "whats your name",
    ],
}
