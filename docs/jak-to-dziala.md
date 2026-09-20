# Jak to działa — TONEX One + mostek MIDI (szczegóły techniczne)

## 1. Architektura

```
Ableton / klawiatura / APC
        │  MIDI (CC/PC)
        ▼
IAC Driver Bus (macOS CoreMIDI)            ← lub dowolny port MIDI In
        │
        ▼
tonex_bridge.py  (Python, wątek MIDI → kolejka → wątek główny)
        │  komendy: load_preset, send_param, send_mvol, set_state
        ▼
/dev/cu.usbmodemXXXXX  (CDC-ACM, 115200 8N1)
        │
        ▼
TONEX One (USB-C — ten sam port, którego używa TONEX App)
```

Tonx One **nie ma natywnego MIDI** — nie umie przyjąć Program Change z kabla DIN ani USB-MIDI. Jego port USB-C wystawia natomiast **port szeregowy (CDC-ACM)**, przez który TONEX App steruje pedałem. Ten port mówi prostym protokołem ramkowym (HDLC + CRC), zreversowanym przez społeczność (projekt `vit3k/tonex_controller`, rozbudowany przez Builty, a potem użyty w PyTonexControl i edytorze WebSerial `tonex-one-control`). Mostek jest **czysto software'owym tłumaczem**: MIDI → komendy tego protokołu. Zero dodatkowego sprzętu — pedał zostaje na Twoim kablu USB-C.

## 2. Warstwa transportu

- Port: CDC-ACM, `115200 8N1`, bez flow control.
- **Framing HDLC**: każdy komunikat zaczyna i kończy się flagą `0x7E`; bajty `0x7E`/`0x7D` wewnątrz są escapowane jako `0x7D (bajt^0x20)`.
- **CRC-16**: wielomian `0x8408` (reflektowany), init `0xFFFF`, xorout `0xFFFF`; dołączany little-endian przed zamykającą flagą.
- `unframe` = usunięcie flag, unescape, weryfikacja CRC (odrzucenie skorumpowanej ramki).

Implementacja: `tonex_proto.py` (`frame()`, `unframe()`, `crc16()`). Zweryfikowana byte-po-bycie przeciw ramkom z `tonex.js` i wektorowi testowemu PyTonexControl (`scripts/gen_vectors.js` regeneruje wektory JS).

## 3. Komunikaty (request → response)

| Funkcja | Payload (po unicie) | Opis |
|---|---|---|
| `hello()` | `B9 03 00 82 04 00 80 0B 01 B9 02 02 0B` | uścisk dłoni |
| `req_state()` | `B9 03 00 82 06 00 80 0B 03 B9 02 81 06 03 0B` | pobierz pełny stan pedała |
| `req_mvol()` | `B9 03 81 0D 03 82 05 00 80 0B 03 B9 03 03 00 00` | pobierz global volume |
| `req_preset(i)` | `B9 03 81 00 03 82 06 00 80 0B 03 B9 04 0B 01 <i> <full>` | pobierz nazwę/parametry presetu i |
| `set_state(sd)` | `B9 03 81 06 03 82 <lenLE> 80 0B 03` + bajty stanu | wyślij (zmodyfikowany) stan |
| `send_param(i,v)` | `B9 03 81 09 03 82 0A 00 80 0B 03` + `B9 04 02 00 <i> 88 <f32>` | ustaw parametr presetu i na v |
| `send_mvol(v)` | `B9 03 81 09 03 82 0A 00 80 0B 03` + `B9 04 03 00 00 88 <f32>` | ustaw global volume (wewn. 0..10) |

Pedał odpowiada asynchronicznie; najważniejsza odpowiedź to **stan**: nagłówek `B9 03 81 06 03 82 <N_LE> 80 0B 03 B9 02 81 06 03 0B` + N bajtów stanu.

## 4. Stan pedała (164 bajty na Twoim egzemplarzu)

**Reguła ekstrakcji** (potwierdzona na żywo): `N = payload[6]`, stan = **ostatnie N bajtów** payloadu. Przykład z Twojego pedała:

```
len=164  slotA=16  slotB=3  slotC=12  cur=0 (A)  bypass=0  active=16
bpm=45.4  tempo_src=0(local)  tuneref=440Hz  trim=1.5  cabsim=0
```

### Offsety (STAŁE, niezależne od długości — liczone od końca/od początku)

| Pole | Offert | Typ |
|---|---|---|
| preset w slocie A | `sd[-18]` | byte 0..19 |
| preset w slocie B | `sd[-16]` | byte 0..19 |
| preset w slocie C | `sd[-14]` | byte 0..19 |
| aktywne A/B/C | `sd[-11]` | 0/1/2 |
| bypass | `sd[-12]` | 0/1 |
| DMON (direct monitoring) | `sd[-7]` | byte — **ustaw 1 przy każdym zapisie stanu** |
| źródło tempa | `sd[-6]` | 0=local, 1=global |
| BPM | `sd[-4:]` | float32 LE |
| tuning reference | `sd[-9:-7]` | uint16 LE (Hz) |
| input trim | `sd[15:19]` | float32 LE |
| cab sim bypass | `sd[20]` | byte |

Sloty: TONEX One trzyma do 20 presetów (indeksy 0..19); aktualnie **wybrany** jest ten wskazany w slocie A/B/C. Przełączanie = podmiana numeru w aktywnym slocie.

## 5. Akcja: `load_preset(n)`

1. Mamy żywy stan (z `wait_for_state` — hello → req_state).
2. Kopiujemy stan i patchemy: numer presetu do aktywnego slotu, `bypass=0`, `DMON=1`.
3. Wysyłamy `set_state(patched)`.
4. Pedał odpowiada nowym stanem → reader aktualizuje `dev.state` (potwierdzenie — widoczne w `--verbose`).

**Bezpieczne zachowanie przy powtórce**: jeśli `n == aktywny` i bypass=0, mostek **nic nie wysyła** (log: `already active (skip)`). Dlaczego: pętla klipu w Abletonie wielokrotnie wysyła tę samą wartość CC127; wysyłka = przerwa w audio. `--toggle-on-repeat` przywraca zachowanie edytora (drugi raz ten sam preset = bypass).

**Uwaga**: `load_preset` NIE modyfikuje zapisanych presetów — tylko wybór aktywnego. Zweryfikowane na żywo: 16→17→16.

## 6. Parametry

- **Per-preset (indeksy 0..108)** — `send_param(idx, float)`. Edytuje parametry AKTUALNEGO presetu (jak suwaki w TONEX App) i **może je zapisać w pedale** — rób backup (`bcho-tonex-loader`, masz w `~/tonex-utilities`).
- **Globalsy (110..115)** — BPM, TRIM, CABSIM, TEMPOS, TUNEREF, BYPASS — żyją w stanie → patch stanu + `set_state` (`patch_global()`).
- **Global volume (116)** — osobny komunikat `send_mvol`; wartość wewnętrzna `0..10` odpowiada `-40..+3 dB` (`v = (dB+40)/43*10`). Zmiana głośności globalnej trwa w pedale.
- Konwersja CC 0..127 → jednostki: liniowa w zakres param (tabela `PARAM_DEFS` w `tonex_proto.py`); parametry 0/1 (on/off) — próg ≥64.

## 7. Mapowanie CC (zgodne z MidiCommands.md projektu Builty)

Pełna tabela w README; kluczowe: `PC 0-19` / `CC 127 0-19` → preset, `CC 86/87` → down/up, `CC 122` → global volume, `CC 123` → bypass, `CC 88` → BPM, `CC 2/5/6/8/18/19/32/37/75/102/103/106/107` → parametry. Własna mapa: `--param-map plik.json` (`{"cc": index_parametru}`).

## 8. Zagrożenia / ograniczenia

- **TONEX App i mostek nie mogą działać równolegle** — port serial ma jednego właściciela. Zamknij appkę (i odczekaj sekundę po jej zamknięciu — zwalnia port z opóźnieniem).
- Zmiana presetu = krótka przerwa sygnału (~100-300 ms) — cecha silnika TONEX, nie mostka.
- Po aktualizacji firmware pedała: odepnij/zapnij USB (numer portu może się zmienić — auto-detect i tak znajdzie).
- Kabel USB musi przewodzić dane (nie charge-only).
- Edycja parametrów + global volume = trwałe zmiany w pedale → backup przed eksperymentami.
- Latencja mostka: rzędu milisekund (wątek MIDI → kolejka → zapis serial); dominuje czas przełączania pedała.

## 9. Weryfikacja

- Wektory byte-exact: `test_proto.py` (10) + `test_features.py` (18) + `test_osc.py` (9) + `test_device.py` (6) = **43/43** vs PyTonexControl + `node scripts/gen_vectors.js` + round-trip OSC + fake-serial device layer.
- Na żywo (Twój pedał, `/dev/cu.usbmodem211401`): sync stanu 164 B ✅, przełączenie 16↔17 z potwierdzeniem ✅, E2E przez IAC ✅, OSC `/preset 5` ✅, feedback CC127 odczytany na „ToneX Bridge Out" ✅, A/B toggle A→B→A ✅, snapshot save/recall = dokładne undo ✅, nazwy presetów w logach toggle ✅, tap tempo → BPM 119 ✅, pedał przywrócony bit-w-bit po testach ✅.
- Mostek leci w jednym procesie, bez GUI; Ctrl+C = czyste zamknięcie portu.

## 10. Nazwy presetów

Odpowiedź na `req_preset(i)` zawiera blok nazwy: marker `B9 04 B9 02 BC 21` + 32 bajty (cięte na pierwszym 0x00, UTF-8). Mostek pobiera 20 nazw **sekwencyjnie** przy starcie (reader przypisuje odpowiedzi w kolejności zapytań — pedał odpowiada seryjnie, potwierdzone na żywo: 20/20, poprawnie przypisane). Nazwy pojawiają się w logach (`preset -> 4  [TJ DMBL ODS 124 NRB CLN]`), w `names`, `status` i `--list-presets`.

## 11. MIDI clock → BPM (tonex_features.ClockSync)

- Impulsy 24 ppq → BPM = 60/(dt·24) per interwał; okno 16 próbek, pierwszy commit po 6.
- Bramki: rozsiew okna ≤4 BPM (jitter ignorowany), zmiana ≥1 BPM vs ostatni zapis, cooldown 0,5 s.
- `reset()` na `start/continue/stop` i po 1,5 s ciszy (w pętli głównej).
- Zapis idzie przez globalny parametr 110 (patch stanu → `set_state`) — **trwały dla pedała**. Na żywo: trening 120 BPM zmienił BPM pedała 45→120, przywrócone po teście.

## 12. Setlist / noty / CLI / reconnect

- **Setlist**: `[{"song","preset"}]`; CC 84/85 (konfigurowalne) + CLI `song next|prev|goto`; przeładowanie w locie `setlist <plik>`.
- **Noty**: `--note-base N` — note-on z zakresu N..N+19 → preset; tylko dedicated channel (izolacja od melodii).
- **CLI (stdin)**: `preset/p | up | down | bypass | vol | db | param <idx> <val> | bpm | slot a|b|c <n> | toggle | tap | state | names | status | song | setlist | map <cc> <param> | clock | osc | help | quit`.
- **Reconnect**: `write()` ustawia `failed` przy błędzie serialu (np. odpięcie USB); pętla główna próbuje ponownie co 1,5 s (re-open + re-sync + re-fetch nazw) — bez restartu mostka. Reader ma backoff 50 ms, żeby nie kręcić CPU na odpiętym urządzeniu.
- **Wirtualny port**: `mido.open_input("ToneX Bridge", virtual=True)` — CoreMIDI destination widoczny dla Abletona; fallback na IAC.

## 13. Serializacja zapisów stanu (`_write_state`)

Pedał odpowiada na każde `set_state` **asynchronicznie** pełnym stanem. Przy serii zapisów (np. szybkie zmiany presetów z OSC+MIDI naraz) stara odpowiedź mogła nadpisać nowszy stan — zapis N wykonywał się na stanie sprzed zapisu N-1 (zaobserwowane na żywo jako „slot A = 7" tuż po zapisie „= 0"). Fix: **każdy zapis stanu idzie przez `_write_state`** — lock + optymistyczne ustawienie `self.state` + pauza do momentu, aż reader dostanie echo pedała (max 1 s, potem lokalny patch jest autorytetem). Efekt: zapisy są w pełni serialne i każdy kolejny widzi stan po poprzednim. Na żywo: seria CC124 + /slot + toggle + toggle → końcowy stan pedała zgodny bit-w-bit z oczekiwanym.

## 14. OSC (UDP 9000, `tonex_osc.py`)

Własny, minimalny OSC 1.0 na samym stdlib (big-endian, stringi NUL-terminowane + padding do 4 B — ważne przy ścieżkach/stringach o długości wielokrotności 4, np. `/bpm`; pokryte testami wektorowymi). Serwer: osobny wątek, `recvfrom` z timeoutem 0,2 s, odporność na śmieciowe datagramy.

| Ścieżka | Argumenty | Akcja / odpowiedź |
|---|---|---|
| `/preset` | `i` | load_preset |
| `/param` | `i i,f` | set_param |
| `/vol` / `/db` | `f` | global volume % / dB |
| `/bpm` | `f` | global 110 |
| `/slot` | `i i` | set_slot (0/1/2) |
| `/toggle` | — | toggle_ab |
| `/bypass` / `/up` / `/down` / `/tap` | — | jak CLI |
| `/song next/prev` | — | setlist navigation |
| `/clock` | `i` | sync on/off |
| `/names` | — | **reply** (jeden string 20 nazw, `|`) |
| `/status` | — | **reply** (preset, slot, bpm, bypass) |

Odpowiedzi wracają na adres nadawcy (zapamiętany z `recvfrom`). Host domyślny `0.0.0.0` (LAN/touchOSC), port `--osc-port` (0/`--no-osc` wyłącza). Typy: tylko `i`/`f`/`s` — wystarczające dla tej powierzchni; nieznane ścieżki logują `osc unknown`.

## 15. Feedback i A/B slots

- **`ToneX Bridge Out`** (drugi wirtualny port, `virtual=True`): na każde realne przełączenie (`on_preset_change` — wywoływany z `load_preset`/`set_slot`/`toggle_ab` przy faktycznej zmianie) wysyła `CC 127` = preset + `PC`; `song_nav` dokłada `CC 84` = idx utworu. To sprzężenie zwrotne — Live/m4l widzą aktualny dźwięk niezależnie od źródła sterowania.
- **Sloty A/B/C** = „szuflady" z gotowymi presetami (state: `sd[-18]/-16/-14]`, `cur` = `sd[-11]`): `set_slot_patch` podmienia tylko bajt wskazanego slotu (+DMON), `toggle_slot_patch` odwraca `cur` 0↔1 — identycznie jak footswitch TONEX One. Przełączenie A/B nie wymaga podmiany presetu — pedał skacze na drugi załadowany dźwięk.
- **Tap tempo**: `TapTempo` (okno 2 s, min. zmiana 1 BPM, zakres 40–240) — odstęp ostatnich 2-4 tapsów CC10 → zapis globalnego BPM (trwały!). CLI `tap`, OSC `/tap`.

## 16. Headless + LaunchAgent

- EOF na stdin (pipa, `</dev/null`, LaunchAgent) **nie kończy mostka**, gdy stdin nie jest TTY — to tryb serwerowy: sterowanie wyłącznie MIDI/OSC i przez pipę (`echo "state" | …tonex_bridge.py…`). Przy prawdziwym terminalu EOF = quit jak dotąd.
- `scripts/install-launchagent.sh` — template `com.pawelknorps.tonex-bridge.plist` (podmiana ścieżek venv/bridge/logów) + `launchctl bootstrap`; `RunAtLoad` + `KeepAlive` → mostek wstaje przy logowaniu i restartuje się po crashu; logi `~/Library/Logs/tonex-bridge*.log`. Bez pedała mostek czeka w auto-reconnect.

## 17. Snapshot A/B (undo całego stanu) + nazwy w logach

- `snapshot save <0-9>` — kopiuje żywy stan (`dev.state`, 164 B: sloty A/B/C + cur + bypass + globalsy + BPM) do słownika; `snapshot recall <0-9>` — `_write_state` z zapisanym stanem (dokładny powrót, także do innego `cur`); `snapshot swap` wymienia 1↔2 (dwa brzmienia do porównania); `snapshot list` pokazuje zapisane. Trasa: CLI `snapshot …`, OSC `/snapshot save|recall|swap|list` (string + int). Uwaga: snapshot NIE cofa zmian parametrów per-preset zapisanych w pedale — to undo stanu (sloty/cur/globalsy/BPM).
- **Nazwy presetów w logach**: `ename(ctx, line)` — regex `preset (\d+)` w liniach slot/toggle (MIDI router, OSC, CLI) dokleja nazwę z cache 20 nazw: `A/B toggle -> preset 8 (slot B) [RawMod '64 Custom Deluxe TOP1]`.
- Warstwa urządzenia testowana na fake-serialu z poprawnym echem (`test_device.py`: state echo = `B9 03 81 06 03 82 <N> 80 0B 03 B9 02 81 06 03 0B` + N bajtów, N=payload[6]) — pacing `_write_state` rozwiązuje się na echo, nie na timeout.