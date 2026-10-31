/*
 * Host-side harness: replays a list of bias edges through the real
 * decoder/app/panel code the same way main.c does on the MCU
 * (edges with exact timestamps, dec_poll/app_tick on a 1 ms tick).
 */
#ifndef COAX_HARNESS_H
#define COAX_HARNESS_H

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "config.h"
#include "decoder.h"
#include "app.h"
#include "panel.h"
#include "beacon.h"

typedef struct { double t; int lvl; } edge_t;     /* t in ms */

#define MAXEV 4096
typedef struct { uint8_t ev; uint8_t err; uint8_t payload; double t; } evrec_t;

#define MAXTX 64
typedef struct { uint8_t pkt[BCN_LEN]; double t; } txrec_t;   /* replies sent */

typedef struct {
    dec_t   dec;
    app_t   app;
    evrec_t ev[MAXEV];
    int     nev;
    txrec_t tx[MAXTX];
    int     ntx;
    int     chips;              /* print the chip string of each reply */
    int     max_lamps;          /* outside boot */
    /* simulated analog inputs */
    uint16_t vc_mv, scap_mv, i_ma;
    double   fault_at;          /* ms, <0 = never (one-shot) */
    int      verbose;
} sim_t;

static const char *ev_name(uint8_t e)
{
    static const char *n[] = { "NONE", "FRAME_START", "BIT", "FRAME_OK",
                               "FRAME_ERR", "GLITCH", "DROP_BEGIN", "DROP_END" };
    return e < 8 ? n[e] : "?";
}

static const char *err_name(uint8_t e)
{
    static const char *n[] = { "-", "SYNC", "CHK", "GAP", "RUNT", "TMO", "DROP" };
    return e < 7 ? n[e] : "?";
}

static uint16_t ms2tick(double ms)
{
    return (uint16_t)(int64_t)(ms * (double)TICK_HZ / 1000.0 + 0.5);
}

static void sim_pump(sim_t *s, uint8_t ev, uint16_t now, double t_ms)
{
    while (ev != EV_NONE) {
        if (s->nev < MAXEV) {
            evrec_t *r = &s->ev[s->nev++];
            r->ev = ev; r->err = s->dec.err; r->payload = s->dec.payload; r->t = t_ms;
        }
        if (s->verbose && ev != EV_BIT) {
            printf("[%9.1f ms] %-11s", t_ms, ev_name(ev));
            if (ev == EV_FRAME_ERR)
                printf(" err=%s after %u bits", err_name(s->dec.err), s->dec.nbits);
            if (ev == EV_FRAME_OK) {
                uint8_t p = s->dec.payload;
                printf(" payload=0x%02X bank=%u lna=%u quiet=%u clr=%u",
                       p, PL_BANK(p), PL_LNA(p), PL_QUIET(p), PL_CLR(p));
            }
            printf("\n");
        }
        app_event(&s->app, ev, &s->dec, now);
        ev = dec_poll(&s->dec, now);
    }
}

/* edges must be sorted; edge[0] is the power-up (level 1) */
static void sim_run(sim_t *s, const edge_t *e, int n, double t_end)
{
    uint8_t img[PANEL_BYTES];
    double t0 = e[0].t, t;
    int i = 1;
    double next_meas;

    s->nev = 0;
    s->ntx = 0;
    s->max_lamps = 0;
    dec_init(&s->dec, 1, ms2tick(0));
    app_init(&s->app, ms2tick(0), 0);
    next_meas = 0;

    for (t = 0; t <= t_end - t0; t += 1.0) {
        uint16_t now;
        while (i < n && e[i].t - t0 <= t) {
            uint16_t te = ms2tick(e[i].t - t0);
            app_set_active(&s->app, 1, te);            /* edge wakes the MCU */
            sim_pump(s, dec_edge(&s->dec, (uint8_t)e[i].lvl, te), te, e[i].t - t0);
            i++;
        }
        now = ms2tick(t);
        sim_pump(s, dec_poll(&s->dec, now), now, t);
        if (s->fault_at >= 0 && t >= s->fault_at) {
            if (s->verbose)
                printf("[%9.1f ms] LNA FAULT (TPS2553)\n", t);
            app_fault(&s->app);
            s->fault_at = -1;
        }
        if (t >= next_meas) {
            next_meas = t + 1000.0;
            if (s->dec.level)
                app_measure(&s->app, s->vc_mv, s->scap_mv, s->i_ma);
        }
        app_tick(&s->app, now);
        if (bcn_due(&s->app, &s->dec)) {               /* main.c: reply() */
            txrec_t *r = &s->tx[s->ntx < MAXTX ? s->ntx++ : MAXTX - 1];
            int k;
            app_measure(&s->app, s->vc_mv, s->scap_mv, s->i_ma);
            bcn_build(r->pkt, &s->app, s->vc_mv, s->scap_mv, s->i_ma);
            r->t = t;
            s->app.reply = REPLY_NONE;
            if (s->verbose) {
                printf("[%9.1f ms] REPLY      ", t);
                for (k = 0; k < (int)BCN_LEN; k++) printf(" %02X", r->pkt[k]);
                if (s->chips) {
                    uint8_t c, i;
                    printf(" chips=");
                    for (i = 0; (c = bcn_chip(r->pkt, i)) != BCN_END; i++) putchar('0' + c);
                }
                printf("\n");
            }
        }
        if (!dec_busy(&s->dec) && !app_busy(&s->app))
            app_set_active(&s->app, 0, now);           /* main.c would Snooze */
        if (s->app.boot == BOOT_DONE) {
            int c;
            panel_render(&s->app, img);
            c = panel_count(img);
            if (c > s->max_lamps)
                s->max_lamps = c;
        }
    }
}

/* ---- frame builder (mirror of host/coaxolotl.py) ------------------ */

static int frame_bits(uint8_t p, int *b)
{
    int k = 0, i;
    b[k++] = 1; b[k++] = 0; b[k++] = 1;
    for (i = 5; i >= 0; i--) b[k++] = (p >> i) & 1;
    for (i = 5; i >= 0; i--) b[k++] = ((~p) >> i) & 1;
    return k;
}

/* appends one frame starting with a falling edge at t; returns end time */
static double add_frame(edge_t *e, int *n, double t, uint8_t p,
                        double g0, double g1, double on)
{
    int b[16], k = frame_bits(p, b), i;
    for (i = 0; i < k; i++) {
        e[(*n)++] = (edge_t){ t, 0 };
        t += b[i] ? g1 : g0;
        e[(*n)++] = (edge_t){ t, 1 };
        t += on;
    }
    return t;
}

/* ---- ASCII front panel -------------------------------------------- */

static void panel_print(const app_t *a)
{
    static const char *row_bank[8] = { "THRU", "LF", "160/80", "60-30",
                                       "20-10", "6/4m", "2m/AIR", "70/23cm" };
    uint8_t img[PANEL_BYTES];
    int i;
#define LIT(id) ((img[(id) >> 3] >> ((id) & 7)) & 1)
#define L(id, name) printf(" %s%s", LIT(id) ? "\xe2\x97\x8f" : "\xe2\x97\x8b", name)
    panel_render(a, img);
    printf("+--------------------------------------------------------------------+\n");
    printf("| PWR \xe2\x97\x8f   COAXOLOTL front panel   (%2d lamps lit)                     |\n",
           panel_count(img));
    printf("+--------------------------------------------------------------------+\n");
    printf("  BANK  ");
    for (i = 0; i < 8; i++) L(L_F0 + i, row_bank[i]);
    printf("\n  FRAME  S:");
    for (i = 0; i < 15; i++) {
        if (i == 3) printf("  P:");
        if (i == 9) printf("  C:");
        printf("%s", LIT(L_FR0 + i) ? "\xe2\x97\x8f" : "\xe2\x97\x8b");
    }
    printf("\n  DECODE"); L(L_RX, "RX"); L(L_BIT0, "0"); L(L_BIT1, "1"); L(L_OK, "OK");
    L(L_ERR, "ERR"); printf("  |"); L(L_ESYNC, "SYNC"); L(L_ECHK, "CHK"); L(L_EGAP, "GAP");
    L(L_ERUNT, "RUNT"); L(L_ETMO, "TMO");
    printf("\n  RF    "); L(L_LNA, "LNA"); L(L_BYP, "BYP"); L(L_AUTO, "AUTO"); L(L_FMN, "FMN");
    printf("  |"); L(L_ILO, "I-LO"); L(L_IOK, "I-OK"); L(L_IHI, "I-HI"); L(L_TRIP, "TRIP");
    printf("\n  SYSTEM"); L(L_RUN, "RUN"); L(L_HOLD, "HOLD"); L(L_LOW, "LOW"); L(L_QUIET, "QUIET");
    printf("  |"); L(L_DROP, "DROP"); L(L_GLT, "GLT"); L(L_RST, "RST");
    printf("\n");
#undef L
#undef LIT
}

#endif
