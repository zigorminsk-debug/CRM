#!/usr/bin/env python3
"""Рингтон новой заявки — «монеты сыплются в кассу» (v2, объёмный и многозвучный).

Слои:
  1) реальные записи монет Kenney RPG Audio (handleCoins, handleCoins2) — CC0;
  2) синтезированные «дзыни» с негармоничными обертонами (как у звонкой монеты)
     в пентатонике до-мажор (C6 D6 E6 G6 A6) — многоголосие;
  3) широкий стереообраз: каждая монета со своей панорамой (constant-power);
  4) стерео-эхо (118 мс, обратная связь 0.3) — объём;
  5) глухой «стук» кассового ящика в начале и мягкий в конце.

Результат: android/app/src/main/res/raw/new_request_coins.ogg
Запуск:  /путь/к/python tools/make_coin_sound.py   (нужны numpy, soundfile)
"""
import os
import numpy as np
import soundfile as sf

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "coin_sound")
OUT = os.path.join(HERE, "..", "android", "app", "src", "main", "res", "raw", "new_request_coins.ogg")
SR = 44100
TOTAL = 2.4


def load(name):
    data, sr = sf.read(os.path.join(SRC, name), always_2d=True)
    if sr != SR:
        n = int(len(data) * SR / sr)
        x = np.linspace(0, len(data) - 1, n)
        idx = np.floor(x).astype(int)
        frac = x - idx
        data = data[idx] * (1 - frac[:, None]) + data[np.minimum(idx + 1, len(data) - 1)] * frac[:, None]
    return data.astype(np.float64)


def pitch(data, k):
    n = max(8, int(len(data) / k))
    x = np.linspace(0, len(data) - 1, n)
    idx = np.floor(x).astype(int)
    frac = x - idx
    return data[idx] * (1 - frac[:, None]) + data[np.minimum(idx + 1, len(data) - 1)] * frac[:, None]


def stereo(sig, pan=0.0):
    """Pan -1..+1 (constant-power) -> пара каналов (стерео сначала даунмиксится)."""
    if sig.ndim == 2:
        sig = sig.mean(axis=1)
    th = (pan + 1) * np.pi / 4
    return np.stack([sig * np.cos(th), sig * np.sin(th)], axis=1)


def mix(base, layer, at, gain=1.0):
    i = int(at * SR)
    end = min(len(base), i + len(layer))
    if end > i:
        base[i:end] += layer[: end - i] * gain


def synth_clink(freq, dur=0.55):
    """Звонкая монета: сумма негармоничных партиалов + шумовой транзиент удара."""
    t = np.arange(int(dur * SR)) / SR
    env = np.exp(-t * 11) * (1 - np.exp(-t * 2200))
    partials = ((1.0, 1.0), (2.756, 0.50), (5.404, 0.26), (8.933, 0.12))
    x = sum(a * np.sin(2 * np.pi * freq * r * t + 0.7 * i) for i, (r, a) in enumerate(partials))
    noise = np.random.default_rng(int(freq)) .standard_normal(len(t)) * np.exp(-t * 95) * 0.22
    return (x + noise) * env


def thud(freq=140, dur=0.10, decay=38):
    t = np.arange(int(dur * SR)) / SR
    return np.sin(2 * np.pi * freq * t) * np.exp(-t * decay)


def echo(st, delay=0.118, fb=0.30, wet=0.24):
    """Простое стерео-эхо — «объём» помещения."""
    out = st.copy()
    d = int(delay * SR)
    g = wet
    i = d
    while i < len(st) and g > 0.02:
        out[i:] += st[: len(st) - i] * g
        i += d
        g *= fb
    return out


rng = np.random.default_rng(20260928)
out = np.zeros((int(TOTAL * SR), 2))

coins = load("rpg-audio-handleCoins.wav")     # перебор монет (0.85 c)
clink = load("rpg-audio-handleCoins2.wav")    # короткий дзынь (0.34 c)

# --- 1) вступление: горсть монет + стук ящика
mix(out, stereo(coins, -0.15), 0.08, 0.85)
mix(out, stereo(np.roll(coins, int(0.012 * SR)), 0.35), 0.09, 0.45)   # ширину
mix(out, stereo(thud(140), 0.0), 0.005, 0.85)

# --- 2) многоголосый дождь монет: пентатоника до-мажор
scale = [1046.5, 1174.7, 1318.5, 1568.0, 1760.0]     # C6 D6 E6 G6 A6
times = [0.03, 0.16, 0.28, 0.37, 0.46, 0.58, 0.66, 0.78, 0.87, 0.98,
         1.10, 1.21, 1.33, 1.46, 1.60, 1.76]
pans = [-0.7, 0.5, -0.2, 0.75, -0.55, 0.15, -0.8, 0.4, -0.35, 0.7,
        -0.6, 0.25, -0.45, 0.6, -0.15, 0.35]
for i, (at, pan) in enumerate(zip(times, pans)):
    f = scale[int(rng.integers(0, len(scale)))]
    g = 0.34 + 0.16 * float(rng.random())
    mix(out, stereo(synth_clink(f), pan), at, g)
    # дублирующий голос настоящей монеты, чуть иначе по тону и панораме
    k = (0.94, 1.0, 1.06, 1.12, 1.19)[i % 5]
    mix(out, stereo(pitch(clink, k), np.clip(-pan * 0.8, -1, 1)), at + 0.015, 0.42)

# --- 3) вторая горсть и финальный аккорд монет
mix(out, stereo(pitch(coins, 1.09), 0.2), 0.62, 0.5)
mix(out, stereo(pitch(coins, 1.16), -0.3), 1.15, 0.42)
mix(out, stereo(synth_clink(2093.0, 0.8), 0.0), 1.78, 0.42)           # «последняя монета»
mix(out, stereo(synth_clink(2637.0, 0.7), 0.1), 1.90, 0.30)
mix(out, stereo(thud(120, 0.12, 30), 0.0), 2.02, 0.55)                # ящик прикрылся

# --- 4) объём: стерео-эхо, нормализация, сглаживание краёв
out = echo(out)
out = out / np.max(np.abs(out)) * 0.88
fi = int(0.008 * SR)
out[:fi] *= np.linspace(0, 1, fi)[:, None]
fo = int(0.25 * SR)
out[-fo:] *= np.linspace(1, 0, fo)[:, None]

os.makedirs(os.path.dirname(OUT), exist_ok=True)
sf.write(OUT, out, SR, format="OGG", subtype="VORBIS")
print("OK:", os.path.abspath(OUT), "%.2f с, %d КБ" % (len(out) / SR, os.path.getsize(OUT) // 1024))
