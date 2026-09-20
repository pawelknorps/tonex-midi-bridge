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
.venv/bin/python tonex_bridge.py                # auto: TONEX + ToneX Bridge (wirtualny)
.venv/bin/python tonex_bridge.py --verbose      # podgląd stanu pedała na żywo
```

Oczekiwany start: `ready : active preset N (slot A) bpm … bypass 0`.

**Auto-start (opcjonalnie)**: `scripts/install-launchagent.sh` — mostek startuje przy logowaniu i nie umiera (KeepAlive); logi w `~/Library/Logs/tonex-bridge*.log`. Odinstaluj: `scripts/install-launchagent.sh uninstall`.

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

Po starcie mostek przyjmuje komendy z terminala: `help`, `preset 5`, `p 12`, `up`/`down`, `bypass`, `vol 0.5`, `db -12`, `param 20 5.5`, `bpm 120`, `slot a|b|c <n>`, `toggle`, `snapshot save|recall|swap|list`, `tap`, `state`, `names`, `status`, `song next`, `setlist plik.json`, `map 40 20`, `clock on|off`, `osc`, `quit`. Przydatne na próbach (zmiana brzmienia bez schylania się do pedała). `state` pokazuje aktualne sloty/active/bypass/BPM z pamięci mostka.

**Snapshot = cofnij wszystko**: grałeś parametry/gain i chcesz wrócić do dźwięku sprzed 5 minut? `snapshot save 1` przed eksperymentem, potem `snapshot recall 1` — przywraca cały stan pedała (sloty, bypass, globalsy, BPM) dokładnie. `snapshot swap` zamienia dwa zapisane (brzmienie A/B).

Tryb headless: przy braku terminala (LaunchAgent, `</dev/null`) mostek **nie kończy się na EOF** — komendy można przesyłać pipą: `echo "state" | .venv/bin/python tonex_bridge.py…`.

## 12. Config JSON (`--config bridge.json`)

```json
{
  "serial": "/dev/cu.usbmodem211401",
  "midi_in": "ToneX Bridge",
  "channel": 1,
  "note_base": 36,
  "clock_sync": true,
  "osc_port": 9000,
  "osc_host": "0.0.0.0",
  "tap_cc": 10,
  "setlist": "setlist.json",
  "song_next_cc": 84,
  "song_prev_cc": 85,
  "param_map": {"40": 20}
}
```
Flagi CLI wygrywają z configiem.

## OSC — sterowanie z Maxa / touchOSC (UDP 9000)

Dla Ciebie najważniejsze: **OSC z Maxa bez żadnych nowych paczek** (mostek ma własny, minimalny OSC 1.0 na stdlib).

```
[udpsend 127.0.0.1 9000]  ←  z patchera
   └─ [sprintf /preset %d]   z intem 0..19
```

Albo pełny router: `[udpreceive 9000] → [route /preset /toggle /vol /param /status] → [unpack 0 i]…`

- `/preset 12` (int) → wczytaj preset; `/toggle` → przełącz A/B; `/slot 1 9` → preset 9 do slotu B; `/param 20 5.5` → gain; `/vol 0.5`; `/bpm 120`; `/bypass`; `/tap`; `/clock 1`; `/snapshot save 1` / `/snapshot recall 1` / `/snapshot swap` / `/snapshot list`.
- `/names` i `/status` **odpowiadają** do nadawcy stringiem — np. `[udpreceive 9000]` w m4l pokaże aktualny preset na UI.
- Domyślnie host `0.0.0.0` → działa touchOSC/tablet w tej samej sieci (bez hasła — sieć domowa/studyjna; wyłącz przez `--no-osc`).

## Feedback — „ToneX Bridge Out" (Live widzi aktualny preset)

Mostek otwiera drugi wirtualny port **„ToneX Bridge Out"** i na każde realne przełączenie wysyła tam `CC 127` = numer presetu + `PC`; na zmianę songu setlisty `CC 84` = index. Zastosowania: mały m4l `[ctlin 127]` → wyświetlacz aktualnego brzmienia; lampka na scenie. Konfiguracja: ten port robisz **MIDI From** na tracku (jak ToneX Bridge, tylko odwrotnie — to wejście do Live).

## A/B slots + tap tempo (lustro footswitcha)

- **CC 124/125** (0..19) → wczytaj preset prosto do slotu A/B; **CC 126** (≥64) → przełącz A/B jak stopą na pedale (natychmiastowy skok na drugi gotowy dźwięk).
- CLI `slot b 5` / `toggle`; OSC `/slot 1 5` / `/toggle`.
- **CC 10** (dwie wartości ≥1 w rytmie) → tap tempo: mostek przelicza odstęp na BPM i zapisuje do pedała. Wyłącz: `--tap-cc 0`.