/*
 * Coaxolotl - application logic. See app.h and docs/panel.md.
 */
#include "app.h"
#include "bandplan.h"
#include "config.h"

#define ELAPSED(now, t, dur) ((uint16_t)((now) - (t)) >= (dur))

/* full panel with animations (ERR blinks, RUN heartbeat, HOLD blink, sweep) */
#define SHOW(a) (!(a)->quiet && (a)->panel == PANEL_FULL)

static void update_rf(app_t XPTR *a)
{
    uint8_t lna_ok = (uint8_t)((BP_LNA_MASK >> a->bank) & 1u);
    uint8_t path   = (uint8_t)(a->lna_req && lna_ok && a->lna_en && !a->trip);
    uint8_t fmn    = (uint8_t)(path && ((BP_FMN_MASK >> a->bank) & 1u));

    a->auto_byp = (uint8_t)(a->lna_req && !lna_ok);
    if (path != a->lna_path || fmn != a->fmn) {
        a->lna_path = path;
        a->fmn = fmn;
    }
    a->rf_dirty = 1;
    a->dirty = 1;
}

void app_set_cal(app_t XPTR *a, const uint16_t XPTR *cal)
{
    uint8_t k;
    for (k = 0; k < CAL_N; k++)
        a->cal[k] = cal[k];
}

void app_init(app_t XPTR *a, uint16_t now, uint8_t rst_abnormal)
{
    uint8_t XPTR *p = (uint8_t XPTR *)a;
    uint16_t i;
    for (i = 0; i < sizeof(app_t); i++)
        p[i] = 0;

    a->quiet   = CFG_DEFAULT_QUIET;
    for (i = 0; i < CAL_N; i++)
        a->cal[i] = CAL_ONE;
    a->cal_ch  = 0xFFu;
    a->panel   = CFG_DEFAULT_PANEL;
    a->st_rst  = rst_abnormal ? 1u : 0u;
    a->link    = 1;
    a->active  = 1;
    a->boot    = BOOT_LAMPTEST;
    a->t_boot  = now;
    a->t_hold  = now;
    update_rf(a);                 /* bank 0 (THRU), bypass, LNA off */
}

void app_set_active(app_t XPTR *a, uint8_t active, uint16_t now)
{
    active = active ? 1u : 0u;
    if (active == a->active)
        return;
    a->active = active;
    if (SHOW(a)) {
        if (active) {             /* heartbeat: keep RUN visible a moment */
            a->run_hb = 1;
            a->t_run = now;
        }
        a->dirty = 1;
    }
}

/* LNA supply on; start the soft-start window if it was off */
static void lna_on(app_t XPTR *a, uint16_t now)
{
    if (!a->lna_en) {
        a->lna_soft = 1;
        a->t_lna = now;
    }
    a->lna_en = 1;
}

static void apply_payload(app_t XPTR *a, uint8_t p, uint16_t now)
{
    uint8_t nb = PL_BANK(p);

    a->lna_req = PL_LNA(p);
    if (PL_QUIET(p) != a->quiet) {
        a->quiet = PL_QUIET(p);
        a->run_hb = 0;
        a->hold_phase = 0;
    }
    if (PL_CLR(p)) {
        a->st_drop = a->st_glt = a->st_rst = 0;
        a->trip = 0;
        a->trip_cnt = 0;
        if (a->boot == BOOT_DONE)
            lna_on(a, now);       /* retry the LNA after a trip */
    }
    if (!a->quiet && a->panel != PANEL_OFF && nb != a->bank) {
        a->sw_active = 1;         /* lamp walks to the new bank */
        a->sw_pos = a->bank;
        a->t_sw = now;
    }
    a->bank = nb;
    update_rf(a);
}

void app_set_panel(app_t XPTR *a, uint8_t mode)
{
    if (mode > PANEL_OFF || mode == a->panel)
        return;
    a->panel = mode;
    a->run_hb = 0;
    a->hold_phase = 0;
    a->err_blinks = 0;
    if (mode == PANEL_OFF)
        a->sw_active = 0;
    a->dirty = 1;
}

/* Calibration over the link: config frames with PM = 3 carry one nibble
 * each (docs/protocol.md). Header nibble 0..3: adjust channel ch, then 4
 * value nibbles (int16, MSB first): cal *= 1 + v / 32768. Header 8..11:
 * reset channel ch to 1.0. Any other frame or an error drops a half-done
 * sequence. */
static void cal_nibble(app_t XPTR *a, uint8_t nib)
{
    uint32_t f;
    uint8_t ch;

    if (a->cal_ch == 0xFFu) {
        ch = (uint8_t)(nib & 3u);
        if (nib & 8u) {
            a->cal[ch] = CAL_ONE;
            a->cal_dirty |= (uint8_t)(1u << ch);
        } else if (!(nib & 4u)) {
            a->cal_ch = ch;
            a->cal_n = 0;
            a->cal_v = 0;
        }
        return;
    }
    a->cal_v = (uint16_t)((a->cal_v << 4) | (nib & 0xFu));
    if (++a->cal_n < 4u)
        return;
    ch = a->cal_ch;
    a->cal_ch = 0xFFu;
    f = ((uint32_t)a->cal[ch] * (uint32_t)(32768L + (int16_t)a->cal_v)) >> 15;
    if (f < CAL_ONE / 2u)                 /* keep within 0.5 .. 1.5 */
        f = CAL_ONE / 2u;
    else if (f > CAL_ONE + CAL_ONE / 2u)
        f = CAL_ONE + CAL_ONE / 2u;
    a->cal[ch] = (uint16_t)f;
    a->cal_dirty |= (uint8_t)(1u << ch);
}

static void apply_config(app_t XPTR *a, uint8_t p)
{
    uint8_t m = CF_PANEL(p);
    if (m == 3u) {
        cal_nibble(a, (uint8_t)(p & 0x0Fu));
        return;
    }
    a->cal_ch = 0xFFu;
    if (m != a->panel) {
        app_set_panel(a, m);
        a->nv_dirty = 1;
    }
}

void app_event(app_t XPTR *a, uint8_t ev, const dec_t XPTR *d, uint16_t now)
{
    switch (ev) {
    case EV_FRAME_START:
        a->rx = 1;
        a->reply = REPLY_NONE;    /* host talks again: drop a stale reply */
        a->result = RES_NONE;
        a->err = ERR_NONE;
        a->err_blinks = 0;
        a->frame_n = 0;
        a->frame_bits = 0;
        break;
    case EV_BIT:
        a->frame_n = d->nbits;
        a->frame_bits = d->bits;
        a->last_bit = d->last_bit;
        a->bit_lamp = 1;
        a->t_bit = now;
        break;
    case EV_FRAME_OK:
        a->frame_n = d->nbits;
        a->frame_bits = d->bits;
        a->last_bit = d->last_bit;
        a->bit_lamp = 1;
        a->t_bit = now;
        a->rx = 0;
        a->result = RES_OK;
        a->reply = REPLY_ACK;
        a->seq++;
        if (d->ftype == FT_CONFIG) {
            apply_config(a, d->payload);
        } else {
            a->cal_ch = 0xFFu;
            apply_payload(a, d->payload, now);
        }
        break;
    case EV_FRAME_ERR:
        a->frame_n = d->nbits;
        a->frame_bits = d->bits;
        a->rx = 0;
        a->result = RES_ERR;
        a->err = d->err;
        a->reply = REPLY_NAK;
        a->cal_ch = 0xFFu;
        a->err_blinks = SHOW(a) ? 6u : 0u;
        a->t_blink = now;
        if (d->err == ERR_DROP)
            a->st_drop = 1;
        break;
    case EV_GLITCH:
        if (a->st_glt)
            return;
        a->st_glt = 1;
        break;
    case EV_DROP_BEGIN:
        a->link = 0;
        break;
    case EV_DROP_END:
        a->link = 1;
        a->st_drop = 1;
        a->t_hold = now;
        break;
    default:
        return;
    }
    a->dirty = 1;
}

void app_fault(app_t XPTR *a)
{
    if (a->trip || a->lna_soft)
        return;
    a->trip = 1;
    a->lna_en = 0;                /* cut LNA supply, route around it */
    a->i_class = I_NONE;
    update_rf(a);
}

static uint16_t cal_apply(uint16_t raw, uint16_t q15)
{
    return (uint16_t)(((uint32_t)raw * q15) >> 15);
}

void app_measure(app_t XPTR *a, uint16_t vc_mv, uint16_t scap_mv, uint16_t i_ma, uint16_t it_ma)
{
    uint8_t v;

    a->vc_mv   = vc_mv   = cal_apply(vc_mv, a->cal[CAL_VCOAX]);
    a->scap_mv = scap_mv = cal_apply(scap_mv, a->cal[CAL_VSCAP]);
    a->i_ma    = i_ma    = cal_apply(i_ma, a->cal[CAL_ILNA]);
    a->it_ma   = it_ma   = cal_apply(it_ma, a->cal[CAL_ITOT]);

    if (a->link) {
        v = a->budget;
        if (!v && it_ma >= I_BUDGET_MA)
            v = 1;
        else if (v && it_ma < I_BUDGET_MA - I_BUDGET_HYST)
            v = 0;
        if (v != a->budget) {
            a->budget = v;
            a->dirty = 1;
        }
    }

    if (a->link) {
        v = a->rail_low;
        if (!v && vc_mv < RAIL_LOW_MV)
            v = 1;
        else if (v && vc_mv > RAIL_LOW_MV + RAIL_HYST_MV)
            v = 0;
        if (v != a->rail_low) {
            a->rail_low = v;
            a->dirty = 1;
        }
    }

    v = a->scap_ok;
    if (!v && scap_mv >= SCAP_OK_MV)
        v = 1;
    else if (v && scap_mv < SCAP_OK_MV - SCAP_HYST_MV)
        v = 0;
    if (v != a->scap_ok) {
        a->scap_ok = v;
        a->dirty = 1;
    }

    if (a->lna_en && a->link) {
        if (i_ma >= I_TRIP_MA && !a->lna_soft) {
            if (++a->trip_cnt >= 2u) {
                app_fault(a);
                return;
            }
        } else {
            a->trip_cnt = 0;
        }
        v = (i_ma < I_LO_MA) ? I_LO : (i_ma > I_HI_MA) ? I_HI : I_OK;
    } else {
        v = I_NONE;
    }
    if (v != a->i_class) {
        a->i_class = v;
        a->dirty = 1;
    }
}

void app_tick(app_t XPTR *a, uint16_t now)
{
    if (a->boot == BOOT_LAMPTEST) {
        if (ELAPSED(now, a->t_boot, T_LAMPTEST)) {
            a->boot = SHOW(a) ? BOOT_SWEEP : BOOT_DONE;
            a->boot_i = 0;
            a->t_boot = now;
            a->dirty = 1;
            if (a->boot == BOOT_DONE) {
                if (!a->trip)
                    lna_on(a, now);
                a->reply = REPLY_HELLO;
                update_rf(a);
            }
        }
        return;
    }
    if (a->boot == BOOT_SWEEP) {
        if (ELAPSED(now, a->t_boot, T_BOOTSWEEP)) {
            a->t_boot = now;
            a->dirty = 1;
            if (++a->boot_i >= 48u) {
                a->boot = BOOT_DONE;
                if (!a->trip)
                    lna_on(a, now);              /* LNA on after the show */
                a->reply = REPLY_HELLO;
                update_rf(a);
            }
        }
        return;
    }

    if (a->lna_soft && ELAPSED(now, a->t_lna, T_LNA_SOFT))
        a->lna_soft = 0;
    if (a->sw_active && ELAPSED(now, a->t_sw, T_BANKSTEP)) {
        a->t_sw = now;
        if (a->sw_pos < a->bank)
            a->sw_pos++;
        else if (a->sw_pos > a->bank)
            a->sw_pos--;
        if (a->sw_pos == a->bank)
            a->sw_active = 0;
        a->dirty = 1;
    }
    if (a->err_blinks && ELAPSED(now, a->t_blink, T_ERRBLINK)) {
        a->err_blinks--;
        a->t_blink = now;
        a->dirty = 1;
    }
    if (a->bit_lamp && ELAPSED(now, a->t_bit, T_BITFLASH)) {
        a->bit_lamp = 0;
        a->dirty = 1;
    }
    if (a->run_hb && ELAPSED(now, a->t_run, T_HEARTBEAT)) {
        a->run_hb = 0;
        a->dirty = 1;
    }
    if (!a->scap_ok && SHOW(a) && a->link) {
        if (ELAPSED(now, a->t_hold, T_HOLDBLINK)) {
            a->hold_phase ^= 1u;
            a->t_hold = now;
            a->dirty = 1;
        }
    } else if (a->hold_phase) {
        a->hold_phase = 0;
        a->dirty = 1;
    }
}

uint8_t app_busy(const app_t XPTR *a)
{
    return (uint8_t)(a->boot != BOOT_DONE || a->sw_active || a->err_blinks ||
                     a->bit_lamp || a->run_hb || a->lna_soft ||
                     (!a->scap_ok && SHOW(a) && a->link));
}
