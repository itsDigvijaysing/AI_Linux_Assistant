import sys, numpy as np, soundfile as sf
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2] / "src"))
from ai_linux.engine.tts import SpeechSynthesizer
out = sys.argv[1]
reply = ["I can browse the web, read your files and open applications for you.",
         "Tell me what you would like to do next, and I will take care of it right away.",
         "If something goes wrong, you can always press stop, and I will end the task at once.",
         "The weather in your city looks clear today, with a light breeze in the evening."]
user = ["What time is my meeting with the design team tomorrow morning?",
        "Please remind me to call the bank before five in the afternoon."]
t = SpeechSynthesizer(voice="F1")
for i, s in enumerate(reply):
    sf.write(f"{out}/reply{i}.wav", t.generate_speech_audio(s, 1.05), t.sample_rate)
t.set_voice("M1")
for i, s in enumerate(user):
    sf.write(f"{out}/user{i}.wav", t.generate_speech_audio(s, 1.0), t.sample_rate)
print(t.sample_rate)
