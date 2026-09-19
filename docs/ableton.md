# Ableton Live — integracja krok po kroku

## 1. Wejście MIDI — wirtualny port (zero konfiguracji)

Mostek tworzy przy starcie wirtualny port **„ToneX Bridge"** (CoreMIDI). **Nie konfigurujesz niczego.** Jedyny krok to: w Live zaznaczyć wyjście (poniżej).

IAC zostaje jako fallback (gdy wirtualny port jest niedostępny): Audio MIDI Setup → IAC Driver → „Device is online"; po polsku port nazywa się „IAC Magistrala 1".

## 2. Ableton Live — Preferences

**Settings → Link, Tempo & MIDI**:
- W sekcji MIDI Ports znajdź **ToneX Bridge**,
- kolumna **Output: Track = On** (żółte).
- (Fallback IAC: znajdź „IAC Driver Bus 1" / „IAC Magistrala 1" i też zapal Track.)

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

## 13. Debug

- `--verbose` pokazuje każdą odpowiedź stanu od pedała (`[state] len=164 …`).
- Mostek loguje akcje: `[12:35:12] control_change -> preset -> 5`.
- Nie widzisz logów z Abletona? Sprawdź w Live że track ma **MIDI From: IAC** i **Monitor: In** (albo arm), i że mostek ma `midi in : Sterownik IAC Magistrala 1`.
- Pedał nie odpowiada: zamknij TONEX App, odepnij/zapnij USB, sprawdź `--scan`.

## 8. Sync BPM z tempa Live (MIDI clock)

1. W **Preferences → Link, Tempo & MIDI**: sekcja **MIDI Sync** → dla **ToneX Bridge** zapal **Sync** (Output) — Live zacznie wysyłać clock po starcie transportu.
2. Mostek (domyślnie włączone) wylicza BPM z odległości impulsów i zapisuje do pedała — **delaye/mody śledzą tempo Live'a**.
3. Histereza: ignoruje jitter (rozsiew >4 BPM) i nie spamuje zapisów (zmiana ≥1 BPM, cooldown 0,5 s). Zmiana tempa w Live = jedna aktualizacja pedała.
4. Wyłączenie: `--no-clock` albo w CLI `clock off`. Uwaga: zapis BPM jest globalny dla pedała (trwały) — po próbie wróci do poprzedniej wartości tylko ręcznie (`bpm 120` itd.).

## 9. Setlist (utwór → preset)

```json
[{"song": "Intro", "preset": 0}, {"song": "Verse", "preset": 5}, {"song": "Chorus", "preset": 9}]
```

```bash
.venv/bin/python tonex_bridge.py --setlist setlist.json
```
- **CC 84** (≥64) → następny utwór, **CC 85** → poprzedni (numery można zmienić: `--song-next-cc` / `--song-prev-cc`).
- W logu: `song -> [1] Verse (preset 5)`.
- W CLI: `song next | prev | goto <i>`, `setlist <plik>` (przeładowanie w locie).
- Automatyzacja w Live: rysuj CC84 w miejscach zmiany utworu; presety ładują się z setlisty (nie musisz trzymać mapy 0-19 w głowie).

## 10. Pady / noty → presety (APC mini, Launchpad, klawisze)

```bash
.venv/bin/python tonex_bridge.py --note-base 36          # noty 36..55 = presety 0..19
```
- Note-on w zakresie `base..base+19` → preset; note-off ignorowany. Izoluj od melodii: `--channel 1` i daj padom własny kanał.
- APC mini mk2: siatka padów to noty 36..43 (rząd 1) do 92..99 (rząd 8) — `--note-base 36` daje 2.5 oktawy przełączania palcem.

## 11. Interaktywny CLI (stdin)

Po starcie mostek przyjmuje komendy z terminala: `help`, `preset 5`, `p 12`, `up`/`down`, `bypass`, `vol 0.5`, `db -12`, `param 20 5.5`, `bpm 120`, `names`, `status`, `song next`, `setlist plik.json`, `map 40 20`, `clock on|off`, `quit`. Przydatne na próbach (zmiana brzmienia bez schylania się do pedała).

## 12. Config JSON (`--config bridge.json`)

```json
{
  "serial": "/dev/cu.usbmodem211401",
  "midi_in": "ToneX Bridge",
  "channel": 1,
  "note_base": 36,
  "clock_sync": true,
  "setlist": "setlist.json",
  "song_next_cc": 84,
  "song_prev_cc": 85,
  "param_map": {"40": 20}
}
```
Flagi CLI wygrywają z configiem.