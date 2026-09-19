# Ableton Live — integracja krok po kroku

## 1. IAC (wirtualny kabel MIDI w macOS)

1. Otwórz **Audio MIDI Setup** (⌘-spacja → "Audio MIDI Setup").
2. Menu **Window → Show MIDI Studio**.
3. Kliknij dwukrotnie **IAC Driver** → zaznacz **Device is online**. Zostaw domyślną nazwę portu (po polsku: „IAC Magistrala 1").

## 2. Ableton Live — Preferences

**Settings → Link, Tempo & MIDI**:
- W sekcji MIDI Ports znajdź **IAC Driver Bus 1** (polski: „IAC Magistrala 1"),
- kolumna **Output: Track = On** (żółte). Reszta opcjonalna.
- Jeśli portu nie ma na liście — IAC nie jest online (wróć do pkt 1).

## 3. Start mostka

```bash
cd ~/tonex-utilities/tonex-midi-bridge
.venv/bin/python tonex_bridge.py                # auto: TONEX + IAC
.venv/bin/python tonex_bridge.py --verbose      # podgląd stanu pedała na żywo
```

Oczekiwany start: `ready : active preset N (slot A) bpm … bypass 0`.

## 4. Automatyzacja presetów w czasie — CC 127 (ścieżka natywna)

Live **nie rysuje Program Change w klipach** (brak talerza PC), ale CC automatyzuje w pełni:

1. Utwórz **track MIDI**.
2. **MIDI From**: IAC Driver Bus 1 (albo zostaw puste i wyślij przez Device/External Instrument — cel musi być IAC).
3. W widoku Clip → **Automation Lane (Z)** → wybierz **CC127** (search: „127").
4. Rysuj segmenty: **wartość = numer presetu (0..19)**, początek segmentu = moment zmiany sekcji.
5. Odpal transport. Mostek przełącza preset w miejscu segmentu.

Wskazówki:
- Powtórna ta sama wartość w pętli klipu = **skip** (zero przerw w audio).
- Na końcu utworu wróć do presetu otwierającego (segment CC127=startowy) — następne wykonanie wystartuje z właściwym brzmieniem.
- Preset switch w 2 utworach: po prostu rysuj wartości 0..19 w obu — pedał trzyma całe 20.

## 5. Program Change z klipów — Max for Live (2 obiekty)

Jeśli wolisz PC (zgodne też z zewnętrznymi sekwencerami):

```
[midiin] → [midiparse p] → [pgmout] → [midiout]
```

1. W M4L: `[midiparse p]` wyciąga program number z midiin, `[pgmout]` emituje zdarzenie PC na `[midiout]`.
2. Wstaw device na track; MIDI From: IAC.
3. W klipie wysyłaj **notę 0 (C-2) z velocity = numer presetu** — klasyczny trick zamiany note→PC albo użyj `[pgmout]` z klipu PC („P.GM" w envelope).

## 6. Automatyzacja parametrów (np. gain, delay mix)

- Wybierz w Automation Lane CC **102** (MDL GAIN) i rysuj 0..127.
- Uwaga: **edytuje aktualny preset** i zapisuje zmiany w pedale (jak TONEX App). Zrób backup zanim zaczniesz bawić się parametrami:
  `~/tonex-utilities/bcho-tonex-loader` (Full Backup).
- Sensowne do automatyzacji: 102 gain, 103 amp volume, 106 presence, 8 delay mix, 5/6 delay time/feedback, 122 global volume (ostrożnie — trwałe).

## 7. Hardware MIDI bez Abletona (próby, scena)

Mostek słucha **dowolnego** wejścia MIDI:
```bash
.venv/bin/python tonex_bridge.py --midi-in "KL Essential"     # Twoja klawiatura
.venv/bin/python tonex_bridge.py --channel 1                  # tylko kanał 1
```
- **KL Essential 61 mk3**: przypisz knob/pad → CC127 (wartość 0..19) albo CC 86/87 (up/down) — przełączanie stopą/klawiaturą bez DAW.
- **APC mini mk2**: pady → CC127, fadery → CC102/103 (gain/volume na żywo).
- Konsystencja z Builty ESP32 jakbyś kiedyś przesiadł się na sprzęt: te same numery CC w MidiCommands.md.

## 8. Debug

- `--verbose` pokazuje każdą odpowiedź stanu od pedała (`[state] len=164 …`).
- Mostek loguje akcje: `[12:35:12] control_change -> preset -> 5`.
- Nie widzisz logów z Abletona? Sprawdź w Live że track ma **MIDI From: IAC** i **Monitor: In** (albo arm), i że mostek ma `midi in : Sterownik IAC Magistrala 1`.
- Pedał nie odpowiada: zamknij TONEX App, odepnij/zapnij USB, sprawdź `--scan`.