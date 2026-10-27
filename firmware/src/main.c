/*
 * Coaxolotl - main loop (EFM8BB2).
 *
 *   edges (ISR, timestamped) -> decoder -> app -> RF switches + panel
 *
 * Sleep policy: while anything is time-critical (frame in progress, gap
 * in progress, animation running) the MCU idles with a 1 ms tick.
 * Otherwise it Snoozes: HFOSC stopped, woken only by a bias edge, the
 * LNA fault line or the ~6.5 s Timer4 wrap. The RUN lamp shows which.
 */
#include <stdint.h>
#include "config.h"
#include "decoder.h"
#include "app.h"
#include "panel.h"
#include "hal.h"

static dec_t   dec;
static app_t   app;
static uint8_t img[PANEL_BYTES];

static void pump(uint8_t ev, uint16_t now)
{
    while (ev != EV_NONE) {
        app_event(&app, ev, &dec, now);
        ev = dec_poll(&dec, now);
    }
}

static void refresh(void)
{
    if (app.nv_dirty) {
        app.nv_dirty = 0;
        hal_nv_save(app.panel);
    }
    if (app.rf_dirty) {
        app.rf_dirty = 0;
        hal_rf(app.bank, app.lna_path, app.fmn, app.lna_en);
    }
    if (app.dirty) {
        app.dirty = 0;
        panel_render(&app, img);
        hal_panel(img);
    }
}

void main(void)
{
    uint16_t now, t_meas, te, vc, sc, ia;
    uint8_t lvl;

    hal_init();
    now = hal_now();
    app_init(&app, now, hal_reset_abnormal());
    app_set_panel(&app, hal_nv_load(CFG_DEFAULT_PANEL));
    dec_init(&dec, hal_sense(), now);
    t_meas = now;
    refresh();

    for (;;) {
        hal_wdt_kick();

        while (hal_edge_pop(&lvl, &te))
            pump(dec_edge(&dec, lvl, te), te);

        now = hal_now();
        pump(dec_poll(&dec, now), now);

        if (hal_take_fault())
            app_fault(&app);

        if (hal_take_hk() || (uint16_t)(now - t_meas) >= T_MEAS_ACT) {
            t_meas = now;
            if (hal_sense()) {
                hal_measure(&vc, &sc, &ia);
                app_measure(&app, vc, sc, ia);
            }
        }

        app_tick(&app, now);
        refresh();

        if (dec_busy(&dec) || app_busy(&app)) {
            hal_idle();
        } else {
#if CFG_SNOOZE
            app_set_active(&app, 0, now);
            refresh();
            hal_snooze();
            app_set_active(&app, 1, hal_now());
#else
            hal_idle();
#endif
        }
    }
}
