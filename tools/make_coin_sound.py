#!/usr/bin/env python3
"""Джингл «монеты сыплются в кассу» для уведомления о новой заявке.

Слои: краткие "дзынь" (handleCoins2, с питч-вариациями) поверх полного
перебора монет (handleCoins) + мягкий низкий "стук" кассового ящика.
Исходники: Kenney RPG Audio (creativecommons.org/publicdomain/zero/1.0).
Результат: android/app/src/main/res/raw/new_request_coins.ogg
Запуск:  /путь/к/python tools/make_coin_sound.py
"""
import os
import numpy as np
import soundfile as sf

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "android", "app", "src", "main", "res", "raw", "new_request_coins.ogg")
SR = 44100


def load(name):
    data, sr = sf.read(os.path.join(HERE, "coin_sound", name), always_2d=True)
    if sr != SR:  # приводим к 44.1 кГц
        n = int(len(data) * SR / sr)
        x = np.linspace(0, len(data) - 1, n)
        idx = np.floor(x).astype(int)
        frac = x - idx
        data = data[idx] * (1 - frac[:, None]) + data[np.minimum(idx + 1, len(data) - 1)] * frac[:, None]
    return data.astype(np.float64)


def pitch(data, k):
    """Изменение тона k>1 — выше (и короче), классический «монетный» приём."""
    n = int(len(data) / k)
    x = np.linspace(0, len(data) - 1, n)
    idx = np.floor(x).astype(int)
    frac = x - idx
    return data[idx] * (1 - frac[:, None]) + data[np.minimum(idx + 1, len(data) - 1)] * frac[:, None]


def mix(base, layer, at, gain=1.0):
    i = int(at * SR)
    end = min(len(base), i + len(layer))
    if end > i:
        base[i:end] += layer[: end - i] * gain


total = 1.9
out = np.zeros((int(total * SR), 2))
coins = load("rpg-audio-handleCoins.wav")     # перебор монет, 0.85 с
clink = load("rpg-audio-handleCoins2.wav")    # короткий дзынь, 0.34 с

mix(out, coins, 0.10, 0.95)                   # горсть монет
mix(out, pitch(clink, 1.16), 0.00, 0.70)      # первая монета падает
mix(out, pitch(clink, 0.92), 0.42, 0.65)
mix(out, pitch(coins, 1.09), 0.55, 0.55)      # ещё порция, чуть выше
mix(out, pitch(clink, 1.30), 0.95, 0.55)
mix(out, pitch(clink, 1.10), 1.22, 0.40)      # последние монетки
mix(out, pitch(clink, 1.45), 1.38, 0.30)

# мягкий «стук» кассового ящика: 140 Гц с быстрым затуханием
t = np.arange(int(0.09 * SR)) / SR
thud = np.sin(2 * np.pi * 140 * t) * np.exp(-t * 38) * 0.35
mix(out, np.stack([thud, thud], axis=1), 0.005, 0.8)

# нормализация и сглаживание краёв
out = out / np.max(np.abs(out)) * 0.88
fi = int(0.008 * SR)
out[:fi] *= np.linspace(0, 1, fi)[:, None]
fo = int(0.18 * SR)
out[-fo:] *= np.linspace(1, 0, fo)[:, None]

os.makedirs(os.path.dirname(OUT), exist_ok=True)
sf.write(OUT, out, SR, format="OGG", subtype="VORBIS")
print("OK:", os.path.abspath(OUT), "%.2f с, %d КБ" % (len(out) / SR, os.path.getsize(OUT) // 1024))
