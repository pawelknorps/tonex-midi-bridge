# tonex-midi-bridge — Ableton → TONEX One (software MIDI bridge)

Dokładnie to o co pytałeś: **pełna kontrola TONEX One z Abletona bez kupowania ESP32**. Pedał zostaje podpięty USB-C do komputera, a ten skrypt tłumaczy MIDI (z IAC bus / dowolnego źródła) na natywny protokół USB pedała.

```
Ableton ──MIDI──> IAC Driver Bus ──> tonex_bridge.py ──USB-C (serial 115200)──> TONEX One
```

Protokół to byte-exact port trzech niezależnych implementacji: `tonex.js` (edytor WebSerial), `PyTonexControl` i firmware `Builty/TonexOneController` — zweryfikowany testami wektorowymi (patrz niżej).

## ✅ Zweryfikowano na żywo

Przetestowane na prawdziwym TONEX One (`/dev/cu.usbmodem211401`, VID 1963:00D1):

- sync stanu pedała: **164 B**, sloty A/B/C, active, bypass, BPM, tuner 440 Hz, trim 1.5
- przełączanie presetów z potwierdzeniem pedała (16→17→16), stan pedała odczytany w odpowiedzi
- pełny E2E przez prawdziwy IAC bus: **PC 3, CC127=8, PC 16** — wszystkie wykonane i zalogowane przez mostek
- testy wektorowe: `test_proto.py` **10/10**

## Dokumentacja

- `docs/jak-to-dziala.md` — szczegóły techniczne: protokół USB (HDLC, CRC, komunikaty, layout stanu 164 B), semantyka load_preset, parametry/globalsy, zagrożenia
- `docs/ableton.md` — integracja z Ableton krok po kroku (IAC, automatyzacja CC127, Max for Live PC, hardware MIDI, debug)

## Instalacja

```bash
cd ~/tonex-utilities/tonex-midi-bridge
python3 -m venv .venv
.venv/bin/pip install pyserial mido python-rtmidi
```

## Setup MIDI (IAC — raz na zawsze)

1. **Audio MIDI Setup** (aplikacja macOS) → **IAC Driver** → zaznacz **Device is online** → nazwa np. „IAC Driver Bus 1".
2. **Ableton Live** → Preferences → **Link, Tempo & MIDI**: w „Control Surface" zostaw puste; w listach portów znajdź **IAC Driver Bus 1** i zapal **Track** (Output). Zamykaj/zaznaczaj ostrożnie — IAC widoczny tylko po włączeniu „Device is online".

## Uruchomienie

```bash
.venv/bin/python tonex_bridge.py            # auto: znajdzie TONEX One + pierwszy port IAC
.venv/bin/python tonex_bridge.py --scan     # lista portów serial/MIDI
.venv/bin/python tonex_bridge.py --channel 1 --verbose
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

1. Utwórz track MIDI, **MIDI From: IAC Driver Bus 1** (albo wyjście przez External Instrument).
2. W Automation Lane wybierz **CC127** → rysuj segmenty 0…19 w miejscach zmian sekcji utworu.
3. Gotowe — preset przełącza się w locie. Ponowne wysłanie tej samej wartości (pętla klipu) jest ignorowane (brak „audio gap"), chyba że użyjesz `--toggle-on-repeat` (zachowanie edytora: drugi raz ten sam preset = bypass).

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