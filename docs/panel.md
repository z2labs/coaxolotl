# Front panel: 49 lamps, every state visible

The panel is meant to look like a 1970s minicomputer. There is one lamp per
state bit, nothing is hidden in a menu, and you can read the box from across
the room.

* There are 48 lamps on six 74HC595 shift registers, plus a hard-wired
  **PWR** lamp on the coax DC.
* The lamps are statically driven and never multiplexed. When nothing
  changes, no clock runs and no lamp switches, so the panel puts no scan
  noise into the receiver.
* The lamps take their current from the coax DC rail, not from the hold-up.
  During every OOK gap the whole panel dims, so you can see the bits go by.
  During a dropout the panel goes dark, and the hold-up keeps the MCU and the
  switch state alive.
* Each lamp draws about 1 mA (2.2 kΩ). Outside the lamp test the worst case
  is 23 lamps, which the host test `test_budget` checks.

## Layout

```
 ┌──────────────────────────────────────────────────────────────────────────┐
 │ PWR●                         C O A X O L O T L                           │
 │                                                                          │
 │ BANK    ○THRU  ○LF  ○160/80  ○60-30  ○20-10  ○6/4m  ●2m/AIR  ○70/23cm    │
 │ FRAME   ● ○ ●   ● ● ○ ● ○ ○   ○ ○ ● ○ ● ●                    ○RX         │
 │         sync    B2 B1 B0 L Q C  check                                    │
 │ DECODE  ○0 ○1  ●OK ○ERR   ○SYNC ○CHK ○GAP ○RUNT ○TMO                      │
 │ RF      ●LNA ○BYP ○AUTO ●FMN   ○I-LO ●I-OK ○I-HI ○TRIP                    │
 │ SYSTEM  ○RUN ●HOLD ○LOW ○QUIET   ○DROP ○GLT ○RST                          │
 └──────────────────────────────────────────────────────────────────────────┘
```

The physical rows are free. The chain order is fixed by `panel.h`: lamp
`n` sits on `U(n/8+1).Q(n%8)`, and U1 is the register next to the MCU.

## Lamp reference

| # | Lamp | Colour | On when |
|---|---|---|---|
| — | PWR | green | bias present on the coax (hard-wired, works without the MCU) |
| 0–7 | F0…F7 | amber | the selected branch (one-hot). In show mode the lamp walks to a new branch |
| 8–22 | FRAME | amber | the received bits, filled in as they arrive. The last frame stays shown |
| 23 | RX | amber | a frame is being received |
| 24 | 0 | yellow | flash (80 ms): the last gap was a 0 |
| 25 | 1 | yellow | flash (80 ms): the last gap was a 1 |
| 26 | OK | green | the last frame was valid and applied |
| 27 | ERR | red | the last frame was rejected. In show mode it blinks 3× and then stays on |
| 28 | SYNC | red | rejected because of the sync bits |
| 29 | CHK | red | rejected because of the check bits |
| 30 | GAP | red | rejected because of a 130–300 ms gap |
| 31 | RUNT | red | rejected because of a < 30 ms on-pulse |
| 32 | TMO | red | rejected because of a > 300 ms on-time mid-frame |
| 33 | LNA | green | the RF path goes through the LNA |
| 34 | BYP | yellow | the RF path bypasses the LNA |
| 35 | AUTO | yellow | the LNA was asked for, but this branch forbids it (HF/LF) |
| 36 | FMN | amber | the FM notch is inserted |
| 37 | I-LO | yellow | LNA current < 60 mA (open, dead or starved) |
| 38 | I-OK | green | LNA current is 60–130 mA |
| 39 | I-HI | red | LNA current > 130 mA |
| 40 | TRIP | red | the LNA supply was cut (TPS2553 fault, or 2 samples ≥ 150 mA). Latched until CLR |
| 41 | RUN | green | the MCU clock is running. Off means Snooze, so no MCU spurs (show mode only) |
| 42 | HOLD | green | supercap ≥ 3.6 V. Blinks while charging (show mode) |
| 43 | LOW | red | coax DC < 3.9 V (long cable, weak bias) |
| 44 | QUIET | yellow | quiet mode |
| 45 | DROP | red | sticky: bias was lost ≥ 300 ms since the last CLR |
| 46 | GLT | yellow | sticky: rail crackle (< 5 ms dips) was seen |
| 47 | RST | red | sticky: the last reset was a watchdog, missing-clock or flash-error reset |

## Behaviour

**Power-up**
1. Lamp test: all 48 lamps light for 700 ms. The LNA stays off, which keeps
   the inrush small.
2. Chaser (show mode): one lamp runs through all 48 positions in about 0.6 s.
3. The LNA supply switches on and the panel shows the live state. The
   default is THRU and bypass.

**Show mode vs quiet mode**

| | show (QUIET=0) | quiet (QUIET=1) |
|---|---|---|
| Bank change | the lamp walks to the new bank | jumps |
| ERR | blinks 3× | steady |
| HOLD while charging | blinks (keeps the MCU awake) | off until charged |
| RUN | lit while awake, plus a 50 ms blip on every ~6.5 s housekeeping wake | never |
| Wake-ups when idle | one every ~6.5 s plus the RUN blip | one every ~6.5 s |

The decoder lamps (FRAME, RX, 0/1) work the same in both modes, because the
MCU is awake while a frame arrives anyway.

## Example states (generated with `firmware/build/sim`)

2 m with LNA, all healthy, idle:
```
  BANK   ○THRU ○LF ○160/80 ○60-30 ○20-10 ○6/4m ●2m/AIR ○70/23cm
  FRAME  S:●○●  P:●●○●○○  C:○○●○●●
  DECODE ○RX ○0 ○1 ●OK ○ERR  | ○SYNC ○CHK ○GAP ○RUNT ○TMO
  RF     ●LNA ○BYP ○AUTO ●FMN  | ○I-LO ●I-OK ○I-HI ○TRIP
  SYSTEM ○RUN ●HOLD ○LOW ○QUIET  | ○DROP ○GLT ○RST
```

The LNA was asked for on 60-30 m. The branch forbids it, so the box stays in
bypass:
```
  BANK   ○THRU ○LF ○160/80 ●60-30 ○20-10 ○6/4m ○2m/AIR ○70/23cm
  RF     ○LNA ●BYP ●AUTO ○FMN  | ○I-LO ●I-OK ○I-HI ○TRIP
```

A spike inside a gap aborted the frame, and later the rail crackled:
```
  FRAME  S:●○●  P:○○○○○○  C:○○○○○○
  DECODE ○RX ○0 ○1 ○OK ●ERR  | ○SYNC ○CHK ○GAP ●RUNT ○TMO
  SYSTEM ○RUN ●HOLD ○LOW ○QUIET  | ○DROP ●GLT ○RST
```

The I-OK lamp stays meaningful in bypass, because the LNA stays powered
there. It only goes dark after a TRIP.
