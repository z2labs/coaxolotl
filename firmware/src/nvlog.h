/*
 * Coaxolotl - settings kept in one flash page (NV_PAGE), as an append-only
 * log so that a change costs a byte write, not a page erase:
 *
 *   0x50 | mode          panel mode (1 byte)
 *   0x60 | ch, lo, hi    calibration factor of channel ch, Q15 (32768 = 1.0)
 *   0xFF                 end of the log
 *
 * The last record of each kind wins. A calibration record is written value
 * first, header last, so a power cut mid-write leaves the log ending before
 * it. When the page is full it is erased and the current settings are
 * written back. Pure C: the HAL supplies byte read / byte write / erase.
 */
#ifndef COAX_NVLOG_H
#define COAX_NVLOG_H

#include <stdint.h>
#include "config.h"

#define CAL_VCOAX  0u
#define CAL_VSCAP  1u
#define CAL_ILNA   2u
#define CAL_ITOT   3u
#define CAL_N      4u
#define CAL_ONE    32768u      /* Q15 1.0 */

typedef struct {
    uint8_t  panel;
    uint16_t cal[CAL_N];
    uint16_t end;          /* first free byte of the log */
} nv_t;

/* HAL primitives (hal_efm8bb*.c; the host tests emulate them) */
uint8_t hal_nv_read(uint16_t i);
void    hal_nv_write(uint16_t i, uint8_t b);
void    hal_nv_erase(void);

void nv_load(nv_t XPTR *s, uint8_t panel_default);    /* call first */
void nv_put_panel(nv_t XPTR *s, uint8_t mode);
void nv_put_cal(nv_t XPTR *s, uint8_t ch, uint16_t q15);

#endif
