import subprocess
import platform

def open_notepad():
    if platform.system() == "Windows":
        subprocess.Popen(["notepad.exe"])

def open_calculator():
    if platform.system() == "Windows":
        subprocess.Popen(["calc.exe"])

def open_cmd():
    if platform.system() == "Windows":
        subprocess.Popen(["cmd.exe"])
