# tonex-midi-bridge — Ableton → TONEX One (software MIDI bridge)

**Repo: <https://github.com/pawelknorps/tonex-midi-bridge>**

Pełna kontrola TONEX One z Abletona **bez kupowania ESP32**. Pedał zostaje podpięty USB-C do komputera, a ten skrypt tłumaczy MIDI na natywny protokół USB pedała.

```
Ableton ──MIDI──> "ToneX Bridge" (wirtualny port, zero konfiguracji!) ──> tonex_bridge.py ──USB-C──> TONEX One
```

## Funkcje

- **Wirtualny port MIDI „ToneX Bridge"** — tworzony na starcie, widoczny w Live jako zwykłe wyjście; **IAC nie jest już potrzebny** (fallback automatyczny)
- **Przełączanie presetów**: PC 0-19, CC 127 (0-19), CC 86/87 up/down, **noty MIDI** (pady: `--note-base`)
- **Wirtualne wyjście feedback „ToneX Bridge Out"** — na każde realne przełączenie wysyła `CC 127` (preset) + `PC`, a na song `CC 84` → Live/Max pokazuje aktualny dźwięk
- **A/B slots (lustro footswitcha)** — `slot A/B/C`, `toggle` (CC 124/125/126): dwa gotowe brzmienia z przełączaniem jednym komunikatem
- **Snapshot A/B stanu** — `snapshot save/recall/swap` (CLI + OSC): zapisz cały stan pedała (sloty, bypass, globalsy, BPM) i cofnij eksperymenty jednym poleceniem
- **Nazwy presetów w logach** — `preset -> 5 [My Preset 222]`, `A/B toggle -> preset 8 (slot B) [RawMod '64  Custom Deluxe TOP1]`
- **Tap tempo (CC 10)** — nastukaj tempo na padzie, BPM pedała ustawia się sam
- **OSC (UDP, czysty stdlib, port 9000)** — `/preset /param /slot /toggle /tap /names /status` z Maxa, touchOSC, telefonu; host 0.0.0.0 (LAN)
- **Nazwy presetów** — 20 nazw pobieranych z pedała na starcie, pokazywane w logach i `--list-presets`
- **MIDI clock → BPM pedała** — delaye i modyfikacje śledzą tempo Live'a (histereza anty-jitter; `--no-clock`)
- **Setlist** — mapa utwór→preset (`--setlist`, CC 84/85 song next/prev)
- **Global volume (CC 122), bypass (CC 123), BPM (CC 88), pełna mapa parametrów CC**
- **Interaktywny CLI** — `preset 5`, `param 20 5.5`, `slot b 3`, `toggle`, `tap`, `state`, `names`, ...
- **LaunchAgent (macOS)** — `scripts/install-launchagent.sh` uruchamia mostek przy logowaniu i trzyma go żywym
- **Auto-reconnect** — odepnij/zapnij pedał bez restartu mostka
- **Config JSON** (`--config`) + `--param-map`, `--channel`

## ✅ Zweryfikowano na żywo

Przetestowane na prawdziwym TONEX One (`/dev/cu.usbmodem211401`):

- sync stanu pedała (164 B), **nazwy presetów 20/20 poprawnie przypisane** (np. preset 12 = „Morning Glory")
- przełączanie: CC127 → 4, nota 48 → 12, PC → 16 — z potwierdzeniem stanu pedała
- **MIDI clock 120 BPM → pedał przełączył BPM 45 → 120** (potem przywrócone bit-w-bit)
- wirtualny port „ToneX Bridge" widoczny w CoreMIDI jako destination
- setlist CC84 → „Outro"; CLI `names`/`status`/`bpm` działają
- **OSC `/preset 5` → pedał na „My Preset 222"** + **feedback `CC 127=5` odebrany na „ToneX Bridge Out"** w innym procesie
- **A/B toggle: slot A→B→A z powrotem ma dokładny preset** (lustro footswitcha); **CC 124 slot A:=7** działa
- **snapshot save/recall przez OSC: 100% undo całego stanu** (sloty+cur+BPM) — przywrócone dokładnie
- **nazwy presetów w logach toggle/slot** (`[Dream 65 - JJ´s Clean BEST1]`, `[RawMod '64 Custom Deluxe TOP1]`)
- **tap tempo CC 10 → BPM pedała 119** (potem 45.42… przywrócone bit-w-bit)
- po testach pedał **przywrócony w 100%** (sloty A/B/C, active slot, bypass, BPM) — weryfikowane stanem z pedała
- testy: `test_proto.py` **10/10** + `test_features.py` **18/18** + `test_osc.py` **9/9** + `test_device.py` **6/6** = **43/43**

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
.venv/bin/python tonex_bridge.py --osc-port 9001 --no-osc   # zmień/wyłącz OSC
.venv/bin/python tonex_bridge.py --tap-cc 0          # wyłącz tap tempo
scripts/install-launchagent.sh                       # auto-start przy logowaniu
```

**Tryb headless**: gdy stdin nie jest terminalem (np. `</dev/null`, LaunchAgent), mostek nie kończy się przy EOF — sterujesz go OSC/MIDI. Komendy CLI (np. `state`, `preset 5`) działają też przez pipę: `echo "state" | .venv/bin/python tonex_bridge.py…`.

## Mapa MIDI

| Zdarzenie | Wartość | Akcja |
|---|---|---|
| **Program Change** | 0–19 | wczytaj preset N |
| **CC 127** | 0–19 | wczytaj preset N *(ścieżka Ableton-native)* |
| CC 86 / 87 | ≥64 | preset w dół / w górę |
| **CC 124 / 125** | 0–19 | wczytaj do slotu A / B |
| **CC 126** | ≥64 | przełącz A/B (jak footswitch) |
| **CC 10** | dowolna ≥1 | tap tempo (2 tapsy = BPM) |
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

## OSC — sterowanie z Maxa / telefonu (port 9000)

Minimalny serwer OSC (UDP, czysty stdlib — zero nowych zależności). Domyślnie `--osc-host 0.0.0.0` (widoczny w LAN — touchOSC/max z telefonu). Typy argumentów: `i` (int), `f` (float), `s` (string).

| Ścieżka | Argumenty | Akcja |
|---|---|---|
| `/preset` | `i` 0–19 | wczytaj preset |
| `/param` | `i i` f | parametr → wartość (np. `/param 20 5.5` = gain) |
| `/vol` | `f` 0–1 | global volume (0–100%) |
| `/db` | `f` | global volume w dB (−40…+3) |
| `/bpm` | `f` | BPM pedała |
| `/slot` | `i i` | slot (0=A,1=B,2=C) → preset |
| `/toggle` | — | przełącz A/B (footswitch) |
| `/bypass` | — | bypass toggle |
| `/up` `/down` | — | preset w górę/dół |
| `/tap` | — | tap tempo |
| `/song next` `/song prev` | — | setlist (jeśli załadowany) |
| `/clock` | `i` | 1/0 = włącz/wyłącz sync BPM |
| `/snapshot save|recall|swap|list` | `s` [+`i`] | stan pedała: zapisz/przywróć/swapnij/wypisz |
| `/names` | — | **odpowiada** listą 20 nazw (`s`) |
| `/status` | — | **odpowiada** stringiem stanu (preset, slot, bpm, bypass) |

Odpowiedzi (`/names`, `/status`) wracają do nadawcy na jego adres. **Przykład z Maxa**: `[udpreceive 9000] → [route /preset /toggle /vol] → [unpack 0 i ...]` albo prościej `[thispatcher]`-wrapped `[udpsend 127.0.0.1 9000]` z `[sprintf /preset %d]`. W m4l: `[udpreceive 9000]` w patchera i dowolne mapowanie na UI.

## Feedback — Live widzi aktualny preset

Mostek tworzy drugi wirtualny port **„ToneX Bridge Out"**. Na każde realne przełączenie (z MIDI, OSC, CLI lub footswitcha) wysyła `CC 127` = nr presetu + `PC`; na zmianę songu `CC 84` = idx. W Live dodaj ten port jako **MIDI From** na tracku i mapuj `CC 127` na swój kontroler/displej (np. mały m4l z `[ctlin]` pokazujący numer aktu); w Maxie wystarczy `[ctlin 127]` w patchera.

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

## Auto-start (LaunchAgent, macOS)

```bash
scripts/install-launchagent.sh    # utworzy ~/Library/LaunchAgents/com.pawelknorps.tonex-bridge.plist
launchctl list | grep tonex       # check
cat ~/Library/Logs/tonex-bridge.log
tail -f ~/Library/Logs/tonex-bridge.err.log   # błędy
scripts/install-launchagent.sh uninstall      # usuń
```

Agent startuje mostek przy logowaniu (`RunAtLoad`) i trzyma go żywym (`KeepAlive`); bez pedała mostek czeka (auto-reconnect). Zamknij skrypt ręcznie: `launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.pawelknorps.tonex-bridge.plist`.

## Znane ograniczenia / uwagi

- **TONEX App nie może działać równolegle** — port serial ma jednego właściciela. Zamknij appkę, odpal mostek.
- Zmiana presetu na TONEX One ma fizyczną krótką przerwę sygnału (~100–300 ms) — to cecha pedała.
- Po aktualizacji firmware pedała: odłącz/podłącz USB.
- Wartości parametrów edytują aktualny preset (jak w TONEX App); zmiany zostają zapisane w pedale.
- `state_info` pokazuje też Slot A/B/C, BPM, tuner itd. (`--verbose`).
- Jeśli TONEX One nie został wykryty: `--scan`, sprawdź kabel (musi być data, nie charge-only), zasilanie pedała.

## Pliki

- `tonex_proto.py` — protokół: HDLC framing, CRC-16, budowa komunikatów, parse stanu, patch presetów/globalsów, patch slotów, tabela PARAM_DEFS
- `tonex_bridge.py` — mostek: serial + MIDI + OSC + CLI + feedback
- `tonex_features.py` — czysta logika: ClockSync, Setlist, TapTempo, noty
- `tonex_osc.py` — minimalny OSC 1.0 (encode/decode + UDP server, stdlib)
- `test_proto.py` / `test_features.py` / `test_osc.py` / `test_device.py` — testy (43)
- `scripts/gen_vectors.js` — generator ground truth z tonex.js (node)
- `scripts/install-launchagent.sh` — auto-start macOS (+ plist template)

## Licencja

Apache 2.0 — wzorowane na Builty/TonexOneController (attribution), PyTonexControl i tonex-one-control. Nieafiliowane z IK Multimedia.