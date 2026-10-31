/*
 * Coaxolotl - 48-lamp front panel.
 *
 * Six 74HC595 in one chain, statically driven (no multiplexing, so no
 * scan noise in the receiver). Lamp n sits on U(n/8 + 1).Q(n % 8),
 * U1 being the register nearest the MCU. Outputs sink: LOW = lamp on.
 * The PWR lamp is hard-wired to VCOAX and is not in the chain.
 */
#ifndef COAX_PANEL_H
#define COAX_PANEL_H

#include <stdint.h>
#include "app.h"

#define PANEL_LAMPS 48u
#define PANEL_BYTES 6u

/* U1: bank */
#define L_F0     0u   /* .. L_F0 + 7 = F7 */
/* U2 + U3.QA..QG: received frame, bit k at L_FR0 + k */
#define L_FR0    8u   /* .. 22 */
#define L_RX    23u
/* U4 */
#define L_BIT0  24u
#define L_BIT1  25u
#define L_OK    26u
#define L_ERR   27u
#define L_ESYNC 28u
#define L_ECHK  29u
#define L_EGAP  30u
#define L_ERUNT 31u
/* U5 */
#define L_ETMO  32u
#define L_LNA   33u
#define L_BYP   34u
#define L_AUTO  35u
#define L_FMN   36u
#define L_ILO   37u
#define L_IOK   38u
#define L_IHI   39u
/* U6 */
#define L_TRIP  40u
#define L_RUN   41u
#define L_HOLD  42u
#define L_LOW   43u
#define L_QUIET 44u
#define L_DROP  45u
#define L_GLT   46u
#define L_RST   47u

void    panel_render(const app_t XPTR *a, uint8_t XPTR *img);
uint8_t panel_count(const uint8_t XPTR *img);   /* lamps lit (power budget) */

#endif
