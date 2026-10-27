"""Decisions beyond tool routing that a voice assistant makes. Hand-written, test only, never used for training.
Each task: (instructions, options {name: description}, items [(state, gold)])."""

Q_DEL, Q_OFF, Q_CLOSE = ("Should I delete the folder old_photos?", "Shall I shut down the computer now?",
                         "Do you want me to close all Firefox windows?")
_confirm = {
    "yes": ["yeah go ahead", "yes please", "sure do it", "absolutely", "yep", "go for it", "okay fine do it",
            "that's right", "please proceed", "yes yes hurry up", "of course", "alright"],
    "no": ["no", "nope don't", "wait stop", "cancel that", "no leave it as it is", "don't do that", "hold on no",
           "never mind", "not now", "absolutely not", "no thanks", "leave it"],
    "unclear": ["which one do you mean", "is that safe", "um let me think", "do something else instead",
                "why do you want to do that", "what time is it", "tell me more first", "what does that do exactly",
                "hmm", "how long will it take", "can you explain", "tell me a joke"],
}
CONFIRM = ("The assistant asked the user to confirm an action. Did the user's spoken reply confirm it?",
           {"yes": "The user agrees: go ahead with the action.",
            "no": "The user refuses or cancels the action.",
            "unclear": "The reply neither agrees nor refuses (a question, a change of plan, or unrelated talk)."},
           [(f"Assistant: {(Q_DEL, Q_OFF, Q_CLOSE)[i % 3]}\nUser: {t}", g)
            for g, ts in _confirm.items() for i, t in enumerate(ts)])

APPS = ("Which installed application does the user want to open?",
        {"firefox": "Firefox web browser", "chrome": "Google Chrome web browser",
         "code": "Visual Studio Code, a code editor", "terminal": "GNOME Terminal, a command line",
         "files": "Files (Nautilus), the file manager", "spotify": "Spotify music streaming app",
         "vlc": "VLC media player for video files", "writer": "LibreOffice Writer, a word processor for documents",
         "calc": "LibreOffice Calc, a spreadsheet", "gimp": "GIMP image editor",
         "obs": "OBS Studio for screen recording and streaming", "steam": "Steam game store and launcher",
         "settings": "GNOME Settings, the system settings", "calculator": "GNOME Calculator",
         "text_editor": "GNOME Text Editor, a simple plain-text editor", "none": "None of these apps"},
        [("open firefox", "firefox"), ("launch chrome", "chrome"), ("open google chrome", "chrome"),
         ("start the code editor", "code"), ("open vs code", "code"), ("open visual studio code", "code"),
         ("i need a terminal", "terminal"), ("open the command line", "terminal"), ("open my files", "files"),
         ("show the file manager", "files"), ("play some music on spotify", "spotify"),
         ("open the music streaming app", "spotify"), ("i want to watch a video file", "vlc"), ("open vlc", "vlc"),
         ("open the media player", "vlc"), ("i need to write a letter", "writer"),
         ("open a word processor", "writer"), ("open the spreadsheet app", "calc"),
         ("i need to make a budget table", "calc"), ("i want to edit a photo", "gimp"),
         ("open the image editor", "gimp"), ("start screen recording", "obs"), ("open obs", "obs"),
         ("i want to play games", "steam"), ("open steam", "steam"), ("open system settings", "settings"),
         ("open the calculator", "calculator"), ("open a plain text editor", "text_editor"),
         ("open telegram", "none"), ("open discord", "none"), ("launch zoom", "none"), ("open blender", "none")])

SETTINGS = ("Which settings page should open for this request?",
            {"wifi": "Wi-Fi and wireless networks", "bluetooth": "Bluetooth devices",
             "display": "Displays: resolution, monitors, refresh rate", "sound": "Sound: speakers, microphone, volume",
             "power": "Power: battery, power saving, automatic suspend",
             "keyboard": "Keyboard: layouts, shortcuts, input sources", "mouse": "Mouse and touchpad",
             "notifications": "Notifications", "privacy": "Privacy and security: screen lock, location",
             "users": "Users and accounts, passwords", "about": "About this computer: system details, hardware",
             "apps": "Apps: default apps, permissions", "datetime": "Date and time, time zone",
             "accessibility": "Accessibility: large text, screen reader", "appearance": "Appearance: wallpaper, dark mode"},
            [("my wifi isn't connecting", "wifi"), ("connect to a new network", "wifi"),
             ("pair my headphones", "bluetooth"), ("turn on bluetooth", "bluetooth"),
             ("change screen resolution", "display"), ("set up my second monitor", "display"),
             ("my microphone isn't working", "sound"), ("switch the output to headphones", "sound"),
             ("battery saver settings", "power"), ("stop the laptop from sleeping so fast", "power"),
             ("add a hindi keyboard layout", "keyboard"), ("change keyboard shortcuts", "keyboard"),
             ("touchpad scrolling is reversed", "mouse"), ("make the mouse pointer faster", "mouse"),
             ("too many notifications popping up", "notifications"), ("turn off location access", "privacy"),
             ("change my password", "users"), ("how much ram does this computer have", "about"),
             ("what version of ubuntu is this", "about"), ("change the default browser", "apps"),
             ("wrong time zone", "datetime"), ("the clock is wrong", "datetime"),
             ("turn on the screen reader", "accessibility"), ("make text bigger for low vision", "accessibility"),
             ("change my wallpaper", "appearance"), ("enable dark mode", "appearance")])

ADDRESSED = ("During a voice session, is this utterance meant for the assistant?",
             {"assistant": "A request or question for the voice assistant",
              "other": "Talking to another person, to oneself, or background speech such as TV or a phone call"},
             [(t, "assistant") for t in [
                 "turn the volume up", "what's the weather tomorrow", "open youtube", "set a timer for five minutes",
                 "how do i check disk space on linux", "can you lower the brightness", "play the next song",
                 "what time is it", "take a screenshot", "search for pasta recipes", "lock the screen",
                 "remind me in ten minutes", "close firefox", "what's fifteen percent of two hundred", "mute"]] +
             [(t, "other") for t in [
                 "mom i'll be down in five minutes", "honey did you feed the cat", "hello? yes i can hear you now",
                 "okay so where was i", "rahul pass me the charger", "breaking news tonight the prime minister announced",
                 "ugh this code still doesn't compile", "yeah i'll call you back later",
                 "who left the lights on in the kitchen", "and that's why you always read the instructions folks",
                 "come in the door is open", "no beta not now i'm working", "thanks for watching don't forget to subscribe",
                 "hmm where did i put my keys", "did you see the match yesterday"]])

FEEDBACK = ("How does the user feel about the assistant's last answer?",
            {"positive": "Satisfied: thanks, praise, it worked",
             "negative": "Unhappy: wrong answer, it didn't work, a complaint",
             "neutral": "Neither: a plain follow-up or a new request"},
            [(t, "positive") for t in ["perfect thanks", "that worked great", "nice one", "awesome thank you",
                                       "exactly what i needed", "you're the best", "good job", "thanks that fixed it"]] +
            [(t, "negative") for t in ["that's wrong", "no that's not what i asked", "it didn't work",
                                       "you opened the wrong app", "ugh useless", "that's not right at all",
                                       "the volume is still too loud", "you misunderstood me"]] +
            [(t, "neutral") for t in ["okay now open spotify", "what about tomorrow", "and the day after",
                                      "now lower the brightness", "next", "what's the time in london",
                                      "tell me more", "okay"]])

SHELL_RISK = ("Could this shell command delete, overwrite or permanently change the user's files or data?",
              {"yes": "Yes: it can remove, overwrite or irreversibly modify files or data",
               "no": "No: it only reads, lists or shows information, or starts a program"},
              [(c, "yes") for c in ["rm -rf ~/Downloads/old", "rm notes.txt", "git reset --hard HEAD~3",
                                    "dd if=/dev/zero of=/dev/sda", "truncate -s 0 log.txt",
                                    "find . -name '*.tmp' -delete", "echo hello > important.txt", "git clean -fdx",
                                    "shred -u secret.pdf", "sed -i 's/a/b/g' config.yaml", "docker system prune -a",
                                    "chmod -R 000 ~/Documents", "rsync -a --delete src/ backup/",
                                    "mv -f draft.txt final.txt"]] +
              [(c, "no") for c in ["ls -la", "df -h", "cat /etc/os-release", "free -m", "ps aux", "git status",
                                   "git log --oneline", "uptime", "du -sh ~/Videos", "firefox &", "grep -r TODO src",
                                   "ping -c 3 example.com", "head -n 20 log.txt", "which python3", "date"]])

TASKS = {"confirm": CONFIRM, "pick_app": APPS, "settings_page": SETTINGS, "addressed": ADDRESSED,
         "feedback": FEEDBACK, "shell_risk": SHELL_RISK}
