/*
 * Coaxolotl - application logic. See app.h and docs/panel.md.
 */
#include "app.h"
#include "bandplan.h"
#include "config.h"

#define ELAPSED(now, t, dur) ((uint16_t)((now) - (t)) >= (dur))

/* full panel with animations (ERR blinks, RUN heartbeat, HOLD blink, sweep) */
#define SHOW(a) (!(a)->quiet && (a)->panel == PANEL_FULL)

static void update_rf(app_t *a)
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

void app_init(app_t *a, uint16_t now, uint8_t rst_abnormal)
{
    uint8_t *p = (uint8_t *)a;
    uint16_t i;
    for (i = 0; i < sizeof(app_t); i++)
        p[i] = 0;

    a->quiet   = CFG_DEFAULT_QUIET;
    a->panel   = CFG_DEFAULT_PANEL;
    a->st_rst  = rst_abnormal ? 1u : 0u;
    a->link    = 1;
    a->active  = 1;
    a->boot    = BOOT_LAMPTEST;
    a->t_boot  = now;
    a->t_hold  = now;
    update_rf(a);                 /* bank 0 (THRU), bypass, LNA off */
}

void app_set_active(app_t *a, uint8_t active, uint16_t now)
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

static void apply_payload(app_t *a, uint8_t p, uint16_t now)
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
            a->lna_en = 1;        /* retry the LNA after a trip */
    }
    if (!a->quiet && a->panel != PANEL_OFF && nb != a->bank) {
        a->sw_active = 1;         /* lamp walks to the new bank */
        a->sw_pos = a->bank;
        a->t_sw = now;
    }
    a->bank = nb;
    update_rf(a);
}

void app_set_panel(app_t *a, uint8_t mode)
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

static void apply_config(app_t *a, uint8_t p)
{
    uint8_t m = CF_PANEL(p);          /* 3 = reserved: no change */
    if (m <= PANEL_OFF && m != a->panel) {
        app_set_panel(a, m);
        a->nv_dirty = 1;
    }
}

void app_event(app_t *a, uint8_t ev, const dec_t *d, uint16_t now)
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
        if (d->ftype == FT_CONFIG)
            apply_config(a, d->payload);
        else
            apply_payload(a, d->payload, now);
        break;
    case EV_FRAME_ERR:
        a->frame_n = d->nbits;
        a->frame_bits = d->bits;
        a->rx = 0;
        a->result = RES_ERR;
        a->err = d->err;
        a->reply = REPLY_NAK;
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

void app_fault(app_t *a)
{
    if (a->trip)
        return;
    a->trip = 1;
    a->lna_en = 0;                /* cut LNA supply, route around it */
    a->i_class = I_NONE;
    update_rf(a);
}

void app_measure(app_t *a, uint16_t vc_mv, uint16_t scap_mv, uint16_t i_ma)
{
    uint8_t v;

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
        if (i_ma >= I_TRIP_MA) {
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

void app_tick(app_t *a, uint16_t now)
{
    if (a->boot == BOOT_LAMPTEST) {
        if (ELAPSED(now, a->t_boot, T_LAMPTEST)) {
            a->boot = SHOW(a) ? BOOT_SWEEP : BOOT_DONE;
            a->boot_i = 0;
            a->t_boot = now;
            a->dirty = 1;
            if (a->boot == BOOT_DONE) {
                a->lna_en = (uint8_t)!a->trip;
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
                a->lna_en = (uint8_t)!a->trip;   /* LNA on after the show */
                a->reply = REPLY_HELLO;
                update_rf(a);
            }
        }
        return;
    }

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

uint8_t app_busy(const app_t *a)
{
    return (uint8_t)(a->boot != BOOT_DONE || a->sw_active || a->err_blinks ||
                     a->bit_lamp || a->run_hb ||
                     (!a->scap_ok && SHOW(a) && a->link));
}
