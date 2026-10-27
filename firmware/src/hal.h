/*
 * Coaxolotl - hardware abstraction. Implemented by hal_efm8bb2.c.
 */
#ifndef COAX_HAL_H
#define COAX_HAL_H

#include <stdint.h>

void     hal_init(void);
uint8_t  hal_reset_abnormal(void);       /* WDT / missing clock / pin reset */
uint16_t hal_now(void);                  /* free-running ticks @ TICK_HZ    */
uint8_t  hal_sense(void);                /* 1 = bias present on the coax    */
uint8_t  hal_edge_pop(uint8_t *level, uint16_t *t);
uint8_t  hal_take_fault(void);           /* TPS2553 FAULT seen since last   */
uint8_t  hal_take_hk(void);              /* ~6.5 s housekeeping tick        */
void     hal_measure(uint16_t *vc_mv, uint16_t *scap_mv, uint16_t *i_ma);
void     hal_rf(uint8_t bank, uint8_t lna_path, uint8_t fmn, uint8_t lna_en);
void     hal_panel(const uint8_t *img);
uint8_t  hal_nv_load(uint8_t dflt);      /* panel mode from flash           */
void     hal_nv_save(uint8_t v);         /* append to the flash log         */
void     hal_wdt_kick(void);
void     hal_idle(void);                 /* sleep until next interrupt      */
void     hal_snooze(void);               /* deep sleep: edge or Timer4 wrap */

#ifdef __SDCC
/* SDCC needs the ISR prototypes visible in the file that holds main() */
void hal_isr_pmatch(void) __interrupt(8);
void hal_isr_timer2(void) __interrupt(5);
#endif

#endif
