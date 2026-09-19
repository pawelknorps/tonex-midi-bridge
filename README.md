# tonex-midi-bridge — Ableton → TONEX One (software MIDI bridge)

**Repo: <https://github.com/pawelknorps/tonex-midi-bridge>**

Pełna kontrola TONEX One z Abletona **bez kupowania ESP32**. Pedał zostaje podpięty USB-C do komputera, a ten skrypt tłumaczy MIDI na natywny protokół USB pedała.

```
Ableton ──MIDI──> "ToneX Bridge" (wirtualny port, zero konfiguracji!) ──> tonex_bridge.py ──USB-C──> TONEX One
```

## Funkcje

- **Wirtualny port MIDI „ToneX Bridge"** — tworzony na starcie, widoczny w Live jako zwykłe wyjście; **IAC nie jest już potrzebny** (fallback automatyczny)
- **Przełączanie presetów**: PC 0-19, CC 127 (0-19), CC 86/87 up/down, **noty MIDI** (pady: `--note-base`)
- **Nazwy presetów** — 20 nazw pobieranych z pedała na starcie, pokazywane w logach i `--list-presets`
- **MIDI clock → BPM pedała** — delaye i modyfikacje śledzą tempo Live'a (histereza anty-jitter; `--no-clock`)
- **Setlist** — mapa utwór→preset (`--setlist`, CC 84/85 song next/prev)
- **Global volume (CC 122), bypass (CC 123), BPM (CC 88), pełna mapa parametrów CC**
- **Interaktywny CLI** — `preset 5`, `param 20 5.5`, `names`, `status`, `song next`, `map 40 20`, ...
- **Auto-reconnect** — odepnij/zapnij pedał bez restartu mostka
- **Config JSON** (`--config`) + `--param-map`, `--channel`

## ✅ Zweryfikowano na żywo

Przetestowane na prawdziwym TONEX One (`/dev/cu.usbmodem211401`):

- sync stanu pedała (164 B), **nazwy presetów 20/20 poprawnie przypisane** (np. preset 12 = „Morning Glory")
- przełączanie: CC127 → 4, nota 48 → 12, PC → 16 — z potwierdzeniem stanu pedała
- **MIDI clock 120 BPM → pedał przełączył BPM 45 → 120** (potem przywrócone bit-w-bit)
- wirtualny port „ToneX Bridge" widoczny w CoreMIDI jako destination
- setlist CC84 → „Outro"; CLI `names`/`status`/`bpm` działają
- testy wektorowe: `test_proto.py` **10/10** + `test_features.py` **9/9**

## Dokumentacja

- `docs/jak-to-dziala.md` — protokół USB (HDLC, CRC, layout stanu), semantyka komend, nowe funkcje
- `docs/ableton.md` — integracja z Ableton krok po kroku (wirtualny port, clock, setlist, noty)

## Instalacja

```bash
cd ~/tonex-utilities/tonex-midi-bridge
python3 -m venv .venv
.venv/bin/pip install pyserial mido python-rtmidi
```

## Setup MIDI (wirtualny port — zero konfiguracji)

**Nie musisz nic konfigurować.** Mostek tworzy w starcie wirtualny port **„ToneX Bridge"** (wpisany do CoreMIDI). Wystarczy w **Ableton Live → Preferences → Link, Tempo & MIDI** znaleźć go w listach i zapalić **Output → Track** (żółte pole). Gotowe.

IAC jest opcjonalny (fallback, gdy wirtualny port się nie stworzy — wtedy: Audio MIDI Setup → IAC Driver → „Device is online" → zapal Track dla „IAC Driver Bus 1").

## Uruchomienie

```bash
.venv/bin/python tonex_bridge.py                     # auto: TONEX + wirtualny "ToneX Bridge"
.venv/bin/python tonex_bridge.py --scan              # lista portów serial/MIDI
.venv/bin/python tonex_bridge.py --list-presets      # nazwy 20 presetów z pedała i wyjście
.venv/bin/python tonex_bridge.py --note-base 36 --setlist setlist.json --channel 1
.venv/bin/python tonex_bridge.py --config bridge.json
.venv/bin/python tonex_bridge.py --no-clock          # wyłącz sync BPM z MIDI clock
```

## Mapa MIDI

| Zdarzenie | Wartość | Akcja |
|---|---|---|
| **Program Change** | 0–19 | wczytaj preset N |
| **CC 127** | 0–19 | wczytaj preset N *(ścieżka Ableton-native)* |
| CC 86 / 87 | ≥64 | preset w dół / w górę |
| CC 123 | ≥64 | bypass toggle |
| CC 122 | 0–127 | global volume (−40…+3 dB) |
| CC 88 | 0–127 | BPM (40–240) |
| **MIDI clock** | — | sync BPM pedała do tempa Live (histereza; `--no-clock`) |
| **Note on** | N..N+19 | preset 0..19 *(tylko z `--note-base N`, np. 36 dla APC)* |
| CC 84 / 85 | ≥64 | song next / prev *(tylko z `--setlist`)* |
| CC 2 | ≥64/off | Delay Power |
| CC 5, 6, 8 | 0–127 | Dig. Delay Time / Feedback / Mix |
| CC 18, 19 | ≥64 / 0–127 | Comp Power / Comp Threshold |
| CC 32 | ≥64/off | Modulation Power |
| CC 37 | 0–127 | Chorus Level |
| CC 75 | ≥64/off | Reverb Power |
| CC 102, 103 | 0–127 | Amp Gain / Amp Volume |
| CC 106, 107 | 0–127 | Presence / Depth |

Numery CC parametrów celowo zgodne z `MidiCommands.md` projektu Builty — jak kiedyś zbudujesz ESP32, mapy się nie zmienią. Własna mapa: `--param-map mape.json` `{"cc": index_parametru}` (indeksy 0–108 wg `tonex.js`, lista w `PARAM_DEFS` w `tonex_proto.py`).

## Ableton: automatyzacja presetów w czasie

Live **nie potrafi wysyłać Program Change z klipów** (brak lane'u PC), ale CC automatyzuje natywnie:

1. Utwórz track MIDI, **MIDI From: ToneX Bridge** (wirtualny port mostka).
2. W Automation Lane wybierz **CC127** → rysuj segmenty 0…19 w miejscach zmian sekcji utworu.
3. Gotowe — preset przełącza się w locie. Ponowne wysłanie tej samej wartości (pętla klipu) jest ignorowane (brak „audio gap"), chyba że użyjesz flagi `--toggle-on-repeat` (zachowanie edytora: drugi raz ten sam preset = bypass).

Alternatywnie dla PC: małe urządzenie Max for Live `[midiin] → [midiparse p] → [pgmout] → [midiout]` albo zewnętrzna klawiatura MIDI z PC.

## Weryfikacja (bez pedała)

```bash
.venv/bin/python -m pytest test_proto.py -q     # lub: .venv/bin/python test_proto.py
```

Testy porównują ramki **byte po bycie** z:
- wektorem z testów PyTonexControl (`send_param(1, 1.0)` = `7E B9 03 81 09 03 82 0A 00 80 0B 03 B9 04 02 00 01 88 00 00 80 3F B2 71 7E`),
- ground truth wygenerowanym z `tonex.js` (node, `scripts/gen_vectors.js`).

## Znane ograniczenia / uwagi

- **TONEX App nie może działać równolegle** — port serial ma jednego właściciela. Zamknij appkę, odpal mostek.
- Zmiana presetu na TONEX One ma fizyczną krótką przerwę sygnału (~100–300 ms) — to cecha pedała.
- Po aktualizacji firmware pedała: odłącz/podłącz USB.
- Wartości parametrów edytują aktualny preset (jak w TONEX App); zmiany zostają zapisane w pedale.
- `state_info` pokazuje też Slot A/B/C, BPM, tuner itd. (`--verbose`).
- Jeśli TONEX One nie został wykryty: `--scan`, sprawdź kabel (musi być data, nie charge-only), zasilanie pedała.

## Pliki

- `tonex_proto.py` — protokół: HDLC framing, CRC-16, budowa komunikatów, parse stanu, patch presetów/globalsów, tabela PARAM_DEFS
- `tonex_bridge.py` — mostek: serial + MIDI + mapowanie
- `test_proto.py` — testy wektorowe (byte-exact)
- `scripts/gen_vectors.js` — generator ground truth z tonex.js (node)

## Licencja

Apache 2.0 — wzorowane na Builty/TonexOneController (attribution), PyTonexControl i tonex-one-control. Nieafiliowane z IK Multimedia.