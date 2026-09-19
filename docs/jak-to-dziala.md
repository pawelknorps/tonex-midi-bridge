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

- Wektory byte-exact: `test_proto.py` (10 testów) vs PyTonexControl + `node scripts/gen_vectors.js`.
- Na żywo (Twój pedał, `/dev/cu.usbmodem211401`): sync stanu 164 B ✅, przełączenie 16↔17 z potwierdzeniem pedała ✅, E2E przez prawdziwy IAC (PC 3, CC127=8, PC 16 restore) ✅.
- Mostek leci w jednym procesie, bez GUI; Ctrl+C = czyste zamknięcie portu.