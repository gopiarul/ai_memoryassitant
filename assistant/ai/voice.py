import speech_recognition as sr
import pyttsx3

engine = pyttsx3.init()

def speak(text):
    if not text:
        return
    engine.say(text)
    engine.runAndWait()


def listen():
    r = sr.Recognizer()
    try:
        with sr.Microphone() as source:
            r.adjust_for_ambient_noise(source)
            audio = r.listen(source)
        return r.recognize_google(audio)
    except:
        return None
