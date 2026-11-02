/*
 * Coaxolotl - gap-width OOK decoder. See decoder.h and docs/protocol.md.
 */
#include "decoder.h"
#include "config.h"

void dec_init(dec_t XPTR *d, uint8_t level, uint16_t now)
{
    d->state    = DEC_IDLE;
    d->level    = level;
    d->idle_ok  = 0;
    d->pend     = EV_NONE;
    d->nbits    = 0;
    d->last_bit = 0;
    d->err      = ERR_NONE;
    d->payload  = 0;
    d->ftype    = FT_STATE;
    d->bits     = 0;
    d->t_fall   = now;
    d->t_rise   = now;
    d->thr      = T_THR_NOM;
    d->g[0] = d->g[1] = d->g[2] = 0;
}

static uint8_t fail(dec_t XPTR *d, uint8_t err)
{
    d->state   = DEC_IDLE;
    d->idle_ok = 0;
    d->err     = err;
    return EV_FRAME_ERR;
}

/* one valid gap inside a frame */
static uint8_t take_bit(dec_t XPTR *d, uint16_t gap)
{
    uint8_t k = d->nbits;
    uint8_t b;

    if (k < 3u) {
        d->g[k] = gap;
        b = (uint8_t)(gap > T_THR_NOM);        /* provisional, for the lamps */
    } else {
        b = (uint8_t)(gap > d->thr);
    }
    if (b)
        d->bits |= (uint16_t)(1u << k);
    d->last_bit = b;
    d->nbits = (uint8_t)(k + 1u);

    if (k == 2u) {
        /* sync: long-short-long = state frame, short-long-short = config
         * frame. The middle gap must differ from both outer ones by 1.5x.
         * Either way the threshold sits halfway between long and short. */
        uint16_t mid = d->g[1];
        uint16_t omin = (d->g[0] < d->g[2]) ? d->g[0] : d->g[2];
        uint16_t omax = (d->g[0] < d->g[2]) ? d->g[2] : d->g[0];
        if ((uint32_t)omin * SYNC_RATIO_DEN >= (uint32_t)mid * SYNC_RATIO_NUM) {
            d->ftype = FT_STATE;
            d->bits  = (uint16_t)((d->bits & ~7u) | 5u);   /* show 1 0 1 */
        } else if ((uint32_t)mid * SYNC_RATIO_DEN >= (uint32_t)omax * SYNC_RATIO_NUM) {
            d->ftype = FT_CONFIG;
            d->bits  = (uint16_t)((d->bits & ~7u) | 2u);   /* show 0 1 0 */
        } else {
            return fail(d, ERR_SYNC);
        }
        d->thr = (uint16_t)(((uint32_t)d->g[0] + d->g[2] + 2u * (uint32_t)mid) / 4u);
    }

    if (d->nbits == DEC_NBITS) {
        uint8_t p = 0, c = 0, i;
        for (i = 0; i < 6u; i++) {
            p = (uint8_t)((p << 1) | ((d->bits >> (3u + i)) & 1u));
            c = (uint8_t)((c << 1) | ((d->bits >> (9u + i)) & 1u));
        }
        if ((uint8_t)(p ^ c) != 0x3Fu)
            return fail(d, ERR_CHK);
        d->state   = DEC_IDLE;
        d->idle_ok = 0;
        d->err     = ERR_NONE;
        d->payload = p;
        return EV_FRAME_OK;
    }
    return EV_BIT;
}

uint8_t dec_edge(dec_t XPTR *d, uint8_t level, uint16_t t)
{
    uint16_t gap, on_before;
    uint8_t ev, started = 0;

    level = level ? 1u : 0u;
    if (level == d->level)
        return EV_NONE;                 /* duplicate report */
    d->level = level;

    if (!level) {                       /* falling edge: a gap starts */
        d->t_fall = t;
        return EV_NONE;
    }

    /* rising edge: a gap ends */
    gap = (uint16_t)(t - d->t_fall);

    if (d->state == DEC_DROPOUT) {
        d->state   = DEC_IDLE;
        d->idle_ok = 0;
        d->t_rise  = t;
        return EV_DROP_END;
    }

    if (gap < T_GLITCH)
        return EV_GLITCH;               /* on-period continues, t_rise kept */

    on_before = (uint16_t)(d->t_fall - d->t_rise);
    d->t_rise = t;

    if (gap >= T_DROPOUT) {             /* dropout that dec_poll() missed */
        if (d->state == DEC_RX) {
            d->pend = EV_DROP_END;
            return fail(d, ERR_DROP);
        }
        d->idle_ok = 0;
        return EV_DROP_END;
    }

    if (gap > T_GAP_MAX) {
        if (d->state == DEC_RX || d->idle_ok || on_before >= T_IDLE_MIN)
            return fail(d, ERR_GAP);
        d->idle_ok = 0;
        return EV_NONE;
    }

    if (d->state != DEC_RX) {
        if (!(d->idle_ok || on_before >= T_IDLE_MIN)) {
            d->idle_ok = 0;             /* tail of an aborted frame: ignore */
            return EV_NONE;
        }
        d->state   = DEC_RX;
        d->idle_ok = 0;
        d->nbits   = 0;
        d->bits    = 0;
        d->thr     = T_THR_NOM;
        started    = 1;
    } else if (on_before < T_ON_MIN) {
        return fail(d, ERR_RUNT);
    }

    ev = take_bit(d, gap);
    if (started) {
        d->pend = ev;
        return EV_FRAME_START;
    }
    return ev;
}

uint8_t dec_poll(dec_t XPTR *d, uint16_t now)
{
    if (d->pend != EV_NONE) {
        uint8_t e = d->pend;
        d->pend = EV_NONE;
        return e;
    }

    if (!d->level) {
        if (d->state != DEC_DROPOUT &&
            (uint16_t)(now - d->t_fall) >= T_DROPOUT) {
            if (d->state == DEC_RX) {
                fail(d, ERR_DROP);
                d->state = DEC_DROPOUT;
                d->pend  = EV_DROP_BEGIN;
                return EV_FRAME_ERR;
            }
            d->state   = DEC_DROPOUT;
            d->idle_ok = 0;
            return EV_DROP_BEGIN;
        }
        return EV_NONE;
    }

    if (d->state == DEC_RX && (uint16_t)(now - d->t_rise) > T_ON_MAX) {
        fail(d, ERR_TMO);
        d->idle_ok = (uint8_t)(T_ON_MAX >= T_IDLE_MIN);
        return EV_FRAME_ERR;
    }
    if (d->state != DEC_RX && !d->idle_ok &&
        (uint16_t)(now - d->t_rise) >= T_IDLE_MIN)
        d->idle_ok = 1;

    return EV_NONE;
}

uint8_t dec_busy(const dec_t XPTR *d)
{
    if (d->pend != EV_NONE)
        return 1;
    if (!d->level)
        return (uint8_t)(d->state != DEC_DROPOUT);  /* waiting for dropout */
    if (d->state == DEC_RX)
        return 1;                                    /* waiting for timeout */
    return (uint8_t)!d->idle_ok;                     /* waiting for idle    */
}
