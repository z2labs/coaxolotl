/*
 * Coaxolotl - application state: RF routing, health, sticky lamps and
 * the panel animations. Pure C, no hardware access.
 */
#ifndef COAX_APP_H
#define COAX_APP_H

#include <stdint.h>
#include "decoder.h"

#define BOOT_LAMPTEST 1u
#define BOOT_SWEEP    2u
#define BOOT_DONE     0u

#define RES_NONE 0u
#define RES_OK   1u
#define RES_ERR  2u

/* panel modes (config frame, kept in flash) */
#define PANEL_FULL  0u   /* every lamp                                  */
#define PANEL_BANDS 1u   /* bank lamps F0..F7 only                      */
#define PANEL_OFF   2u   /* all 48 dark; only the hard-wired PWR lamp   */

#define I_NONE 0u
#define I_LO   1u
#define I_OK   2u
#define I_HI   3u

typedef struct {
    /* RF routing (what the switches do) */
    uint8_t bank;        /* 0..7                                     */
    uint8_t lna_req;     /* host asked for the LNA                   */
    uint8_t lna_en;      /* LNA supply switch on                     */
    uint8_t lna_path;    /* SPDTs route through the LNA              */
    uint8_t fmn;         /* FM notch inserted                        */
    uint8_t auto_byp;    /* LNA asked for but branch forbids it      */
    uint8_t quiet;       /* no animations, no RUN lamp               */
    uint8_t panel;       /* PANEL_* lamp mode                        */
    uint8_t nv_dirty;    /* panel mode changed: save to flash        */
    uint8_t rf_dirty;    /* switches need updating                   */

    /* health */
    uint8_t link;        /* bias present                             */
    uint8_t trip;        /* LNA over-current, latched until CLR      */
    uint8_t trip_cnt;
    uint8_t i_class;     /* I_NONE / I_LO / I_OK / I_HI              */
    uint8_t rail_low;
    uint8_t scap_ok;
    uint8_t active;      /* MCU clock running (not in Snooze)        */

    /* sticky lamps, cleared by a frame with CLR */
    uint8_t st_drop, st_glt, st_rst;

    /* decoder view */
    uint8_t  rx;
    uint8_t  result;     /* RES_*                                    */
    uint8_t  err;        /* ERR_* when result == RES_ERR             */
    uint8_t  frame_n;
    uint16_t frame_bits;
    uint8_t  last_bit;

    /* animations / timers */
    uint8_t  boot;       /* BOOT_*                                   */
    uint8_t  boot_i;
    uint16_t t_boot;
    uint8_t  sw_active, sw_pos;
    uint16_t t_sw;
    uint8_t  err_blinks;
    uint16_t t_blink;
    uint8_t  bit_lamp;
    uint16_t t_bit;
    uint8_t  run_hb;
    uint16_t t_run;
    uint8_t  hold_phase;
    uint16_t t_hold;

    uint8_t dirty;       /* panel needs re-render                    */
} app_t;

void    app_init(app_t *a, uint16_t now, uint8_t rst_abnormal);
void    app_event(app_t *a, uint8_t ev, const dec_t *d, uint16_t now);
void    app_tick(app_t *a, uint16_t now);
void    app_measure(app_t *a, uint16_t vc_mv, uint16_t scap_mv, uint16_t i_ma);
void    app_fault(app_t *a);
void    app_set_panel(app_t *a, uint8_t mode);   /* at boot, from flash */
void    app_set_active(app_t *a, uint8_t active, uint16_t now);
uint8_t app_busy(const app_t *a);   /* 1 = animation running, keep ticking */

#endif
