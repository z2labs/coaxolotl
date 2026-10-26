/*
 * Coaxolotl - gap-width OOK decoder for bias-T keying.
 *
 * The host switches the RTL-SDR bias-T off for a short gap per bit:
 *   short gap (30 ms) = 0, long gap (70 ms) = 1, 100 ms of power between.
 * Frame = 15 bits:  S S S | P5..P0 | C5..C0
 *   sync  = 1 0 1   (also calibrates the 0/1 threshold)
 *   P     = bank[2:0] lna quiet clr
 *   C     = ~P (6-bit inverse check)
 *
 * Pure C, no hardware access. Time is a free-running uint16_t tick count;
 * the caller must call dec_poll() at least once per 65536 ticks.
 */
#ifndef COAX_DECODER_H
#define COAX_DECODER_H

#include <stdint.h>

#define DEC_NBITS 15u

/* decoder states */
#define DEC_IDLE     0u
#define DEC_RX       1u
#define DEC_DROPOUT  2u

/* events */
#define EV_NONE         0u
#define EV_FRAME_START  1u
#define EV_BIT          2u
#define EV_FRAME_OK     3u
#define EV_FRAME_ERR    4u
#define EV_GLITCH       5u
#define EV_DROP_BEGIN   6u
#define EV_DROP_END     7u

/* error codes (dec_t.err) */
#define ERR_NONE  0u
#define ERR_SYNC  1u   /* sync was not long-short-long            */
#define ERR_CHK   2u   /* check bits are not the inverse of data  */
#define ERR_GAP   3u   /* gap between 130 ms and 300 ms            */
#define ERR_RUNT  4u   /* power-on between bits too short          */
#define ERR_TMO   5u   /* power-on inside a frame too long         */
#define ERR_DROP  6u   /* bias lost (>300 ms) inside a frame       */

/* payload fields */
#define PL_BANK(p)  ((uint8_t)(((p) >> 3) & 7u))
#define PL_LNA(p)   ((uint8_t)(((p) >> 2) & 1u))
#define PL_QUIET(p) ((uint8_t)(((p) >> 1) & 1u))
#define PL_CLR(p)   ((uint8_t)((p) & 1u))

typedef struct {
    uint8_t  state;
    uint8_t  level;     /* last seen rail level, 1 = bias on            */
    uint8_t  idle_ok;   /* rail has been on >= T_IDLE_MIN (frame ready) */
    uint8_t  pend;      /* queued event, returned by next dec_poll()     */
    uint8_t  nbits;     /* bits received in current/last frame           */
    uint8_t  last_bit;
    uint8_t  err;       /* ERR_* of the last failed frame                */
    uint8_t  payload;   /* 6-bit payload of the last good frame          */
    uint16_t bits;      /* bit k = k-th received bit (k = 0 first)       */
    uint16_t t_fall;    /* start of current/last gap                     */
    uint16_t t_rise;    /* start of current/last on-period               */
    uint16_t thr;       /* adaptive 0/1 threshold from sync              */
    uint16_t g[3];      /* sync gap lengths                              */
} dec_t;

void    dec_init(dec_t *d, uint8_t level, uint16_t now);
uint8_t dec_edge(dec_t *d, uint8_t level, uint16_t t);   /* one event */
uint8_t dec_poll(dec_t *d, uint16_t now);                /* call until EV_NONE */
uint8_t dec_busy(const dec_t *d);  /* 1 = has a deadline, keep ticking */

#endif
