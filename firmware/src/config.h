/*
 * Coaxolotl - build-time configuration.
 *
 * Everything here is pure C so the same values are used by the EFM8BB2
 * firmware (SDCC) and by the host-side tests/simulator (gcc).
 */
#ifndef COAX_CONFIG_H
#define COAX_CONFIG_H

#include <stdint.h>

#ifdef __SDCC
#define CODE __code
#else
#define CODE
#endif

/* ------------------------------------------------------------------ */
/* Time base                                                           */
/* ------------------------------------------------------------------ */
/*
 * CFG_SNOOZE=1 : timestamps from Timer4 clocked by LFOSC0/8 (10 kHz),
 *                MCU sleeps in Snooze (HFOSC stopped -> no clock spurs)
 *                whenever nothing time-critical is pending.
 * CFG_SNOOZE=0 : bring-up fallback. Timer2 1 ms tick, Idle mode only.
 *                Uses only classic 8051/C8051 registers.
 */
#ifndef CFG_SNOOZE
#define CFG_SNOOZE 1
#endif

#ifndef TICK_HZ
#if CFG_SNOOZE
#define TICK_HZ 10000u
#else
#define TICK_HZ 1000u
#endif
#endif

/* ms -> ticks, folded at compile time for constant arguments */
#define MS2T(ms) ((uint16_t)(((uint32_t)(ms) * (uint32_t)TICK_HZ) / 1000u))

/* ------------------------------------------------------------------ */
/* Link protocol timing (must match host/coaxolotl.py)                 */
/* ------------------------------------------------------------------ */
/* Host nominal: gap '0' = 30 ms, gap '1' = 70 ms, on between bits = 100 ms,
 * bias steady on >= 400 ms before a frame. See docs/protocol.md.
 * Margins: survives +/-10 % clock skew, up to ~10 ms slow bias discharge
 * (falling-edge delay) and +/-3 ms jitter per edge (tested). */
#define T_GLITCH    MS2T(5)    /* gap shorter than this: rail crackle, ignored   */
#define T_THR_NOM   MS2T(50)   /* provisional 0/1 threshold for the sync bits     */
#define T_GAP_MAX   MS2T(130)  /* longer gap (but < dropout) is a protocol error  */
#define T_DROPOUT   MS2T(300)  /* gap this long = bias lost, ride-through on SCAP */
#define T_ON_MIN    MS2T(30)   /* on-time between bits shorter than this = RUNT   */
#define T_ON_MAX    MS2T(300)  /* on-time inside a frame longer than this = TMO   */
#define T_IDLE_MIN  MS2T(300)  /* steady-on time required before a frame start   */

/* sync "1 0 1": both long gaps must be >= 1.5 x the short one */
#define SYNC_RATIO_NUM 3u
#define SYNC_RATIO_DEN 2u

/* ------------------------------------------------------------------ */
/* Back-channel: clock beacon on the coax (see docs/protocol.md)       */
/* ------------------------------------------------------------------ */
/* After every frame (good or bad) and once after boot, the box keys a
 * square wave from the PCA onto the coax: SYSCLK 24.5 MHz / (2 * CPH)
 * = 12.25 MHz, which the RTL-SDR hears on its 3rd harmonic at 36.75 MHz.
 * On-off keyed Manchester chips of BCN_CHIP_MS, so one bit is 2 chips. */
#ifndef CFG_BEACON
#define CFG_BEACON 1
#endif
/* The reply starts as soon as the decoder is ready for the next frame,
 * i.e. after T_IDLE_MIN of steady bias following the host's last gap. */
#define BCN_CHIP_MS   5u
#define T_CHIP        MS2T(BCN_CHIP_MS)
#define BCN_PCA_CPH   1u          /* PCA frequency-output divider: f = SYSCLK / (2 * CPH) */

/* ------------------------------------------------------------------ */
/* Panel / UI                                                          */
/* ------------------------------------------------------------------ */
#ifndef CFG_DEFAULT_QUIET
#define CFG_DEFAULT_QUIET 0     /* 0 = show mode after power-on */
#endif

#define T_LAMPTEST   MS2T(700)  /* all 48 lamps on                       */
#define T_BOOTSWEEP  MS2T(12)   /* per lamp, chaser over all 48 lamps    */
#define T_BANKSTEP   MS2T(60)   /* bank lamp travel per step             */
#define T_ERRBLINK   MS2T(150)  /* ERR lamp half-period, 3 blinks        */
#define T_BITFLASH   MS2T(80)   /* BIT0/BIT1 lamp flash                  */
#define T_HEARTBEAT  MS2T(50)   /* RUN lamp blip on each wake (show)     */
#define T_HOLDBLINK  MS2T(500)  /* HOLD lamp blink while SCAP charges    */
#define T_MEAS_ACT   MS2T(1000) /* housekeeping period while awake       */

/* ------------------------------------------------------------------ */
/* Analog front end (see docs/hardware.md)                             */
/* ------------------------------------------------------------------ */
#define VDD_MV        3300ul    /* 32-bit: SDCC folds constant products */
#define ADC_FS        1023u     /* 10-bit, VDD reference */

/* VCOAX divider 10k (top) / 22k (bottom): Vpin = V * 22/32 */
#define VC_DIV_NUM    32u
#define VC_DIV_DEN    22u
/* SCAP divider 1M / 1M */
#define SC_DIV_NUM    2u
#define SC_DIV_DEN    1u
/* LNA current: shunt 0.39 ohm, INA180A2 gain 50 -> 19.5 mV/mA */
#define I_UV_PER_MA   19500u

#define RAIL_LOW_MV   3900u     /* LOW lamp below this (cable drop / weak bias) */
#define RAIL_HYST_MV  100u
#define SCAP_OK_MV    3600u     /* HOLD lamp steady above this                  */
#define SCAP_HYST_MV  200u
#define I_LO_MA       60u       /* LNA current window                           */
#define I_HI_MA       130u
#define I_TRIP_MA     150u      /* 2 consecutive samples -> firmware trip       */

#endif
