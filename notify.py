# -*- coding: utf-8 -*-
"""抢到提示音：哔哔响 / 播放音频文件 / TTS 朗读 / 不播放。

在抢票窗口内循环播放，直到按下任意键停止。
"""
import sys
import threading
import time

# 提示模式
MODE_NONE = "none"
MODE_BEEP = "beep"
MODE_AUDIO = "audio"
MODE_TTS = "tts"

MODE_NAMES = {
    MODE_NONE: "不播放",
    MODE_BEEP: "哔哔响",
    MODE_AUDIO: "播放音频文件",
    MODE_TTS: "TTS 朗读",
}


def _beep():
    """哔哔响：winsound（Windows 内置）。"""
    try:
        import winsound
    except ImportError:
        return False
    for _ in range(3):
        winsound.Beep(880, 300)
        time.sleep(0.2)
    return True


def _play_audio(path: str) -> bool:
    """播放音频文件（playsound3）。"""
    try:
        from playsound import playsound
    except ImportError:
        try:
            from playsound3 import playsound
        except ImportError:
            return False
    playsound(path, block=True)
    return True


def _tts(text: str) -> bool:
    """TTS 朗读（Windows SAPI）。"""
    try:
        import win32com.client
    except ImportError:
        return False
    speaker = win32com.client.Dispatch("SAPI.SpVoice")
    speaker.Speak(text)
    return True


class Notifier:
    """循环播放提示音，直到 stop() 被调用。"""

    def __init__(self, mode: str = MODE_NONE, audio_file: str = "",
                 text: str = ""):
        self.mode = mode
        self.audio_file = audio_file
        self.text = text
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        if self.mode == MODE_NONE:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _loop(self):
        while not self._stop.is_set():
            try:
                if self.mode == MODE_BEEP:
                    _beep()
                elif self.mode == MODE_AUDIO and self.audio_file:
                    _play_audio(self.audio_file)
                elif self.mode == MODE_TTS and self.text:
                    _tts(self.text)
            except Exception:
                pass
            # 每次播完间隔，避免循环过快
            for _ in range(20):
                if self._stop.is_set():
                    return
                time.sleep(0.1)


def wait_any_key(prompt: str = "按任意键停止提示音..."):
    """等待任意键（Windows msvcrt 阻塞式）。"""
    try:
        import msvcrt
        print(prompt, flush=True)
        msvcrt.getch()
    except ImportError:
        input(prompt)
