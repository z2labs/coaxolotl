/*
 * Coaxolotl - back-channel: the reply packet and its chip sequence.
 *
 * After each frame the box keys a clock onto the coax (on-off keying,
 * Manchester, BCN_CHIP_MS per chip) and the RTL-SDR on the other end
 * hears it. The reply says whether the frame was applied, what the
 * switches now do, and what the box measures:
 *
 *   byte 0  kind[7:6] err[5:3] seq[2:0]      kind = REPLY_*, err = ERR_*
 *   byte 1  bank[7:5] lna_req lna_path fmn auto quiet
 *   byte 2  trip low hold drop glt rst lna_en 0
 *   byte 3  VCOAX   / 20 mV   (0 .. 5.10 V)
 *   byte 4  I_LNA   / 1 mA    (0 .. 255 mA)
 *   byte 5  VSCAP   / 20 mV
 *   byte 6  CRC-8 over bytes 0..5 (poly 0x07, init 0xFF)
 *
 * On air: 8 preamble bits '1', sync byte 0x2D, the 7 bytes MSB first,
 * then one closing 'on' chip. Bit 1 = chips "on off", bit 0 = "off on".
 *
 * Pure C, no hardware access. See docs/protocol.md.
 */
#ifndef COAX_BEACON_H
#define COAX_BEACON_H

#include <stdint.h>
#include "app.h"
#include "decoder.h"

#define BCN_LEN       7u
#define BCN_SYNC      0x2Du
#define BCN_PRE_BITS  8u
#define BCN_NCHIPS    ((BCN_PRE_BITS + 8u + 8u * BCN_LEN) * 2u + 1u)
#define BCN_END       2u    /* bcn_chip(): past the last chip */
#define BCN_MV_LSB    20u

uint8_t bcn_crc8(const uint8_t XPTR *p, uint8_t n);
uint8_t bcn_due(const app_t XPTR *a, const dec_t XPTR *d);
void    bcn_build(uint8_t XPTR *pkt, const app_t XPTR *a,
                  uint16_t vc_mv, uint16_t scap_mv, uint16_t i_ma);
uint8_t bcn_chip(const uint8_t XPTR *pkt, uint8_t i);   /* 1 = carrier on, 0 = off, BCN_END */

#endif
