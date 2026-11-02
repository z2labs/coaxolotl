/*
 * Coaxolotl - back-channel reply. See beacon.h and docs/protocol.md.
 */
#include "beacon.h"
#include "config.h"

uint8_t bcn_crc8(const uint8_t XPTR *p, uint8_t n)
{
    uint8_t c = 0xFFu, i;
    while (n--) {
        c ^= *p++;
        for (i = 0; i < 8u; i++)
            c = (uint8_t)((c & 0x80u) ? (uint8_t)(c << 1) ^ 0x07u : (uint8_t)(c << 1));
    }
    return c;
}

/* a reply is pending and the host has gone quiet: the decoder is ready
 * for the next frame (T_IDLE_MIN of steady bias after the last gap) */
uint8_t bcn_due(const app_t XPTR *a, const dec_t XPTR *d)
{
    return (uint8_t)(a->reply != REPLY_NONE && a->boot == BOOT_DONE &&
                     d->state == DEC_IDLE && d->level && d->idle_ok);
}

static uint8_t sat8(uint16_t v)
{
    return (uint8_t)(v > 255u ? 255u : v);
}

void bcn_build(uint8_t XPTR *pkt, const app_t XPTR *a,
               uint16_t vc_mv, uint16_t scap_mv, uint16_t i_ma)
{
    uint8_t err = (a->reply == REPLY_NAK) ? a->err : ERR_NONE;

    pkt[0] = (uint8_t)((a->reply << 6) | ((err & 7u) << 3) | (a->seq & 7u));
    pkt[1] = (uint8_t)(((a->bank & 7u) << 5) | (a->lna_req ? 0x10u : 0u) |
                       (a->lna_path ? 0x08u : 0u) | (a->fmn ? 0x04u : 0u) |
                       (a->auto_byp ? 0x02u : 0u) | (a->quiet ? 0x01u : 0u));
    pkt[2] = (uint8_t)((a->trip ? 0x80u : 0u) | (a->rail_low ? 0x40u : 0u) |
                       (a->scap_ok ? 0x20u : 0u) | (a->st_drop ? 0x10u : 0u) |
                       (a->st_glt ? 0x08u : 0u) | (a->st_rst ? 0x04u : 0u) |
                       (a->lna_en ? 0x02u : 0u));
    pkt[3] = sat8((uint16_t)((vc_mv + BCN_MV_LSB / 2u) / BCN_MV_LSB));
    pkt[4] = sat8(i_ma);                 /* raw: shows a stuck switch too */
    pkt[5] = sat8((uint16_t)((scap_mv + BCN_MV_LSB / 2u) / BCN_MV_LSB));
    pkt[6] = bcn_crc8(pkt, 6u);
}

uint8_t bcn_chip(const uint8_t XPTR *pkt, uint8_t i)
{
    uint8_t k = (uint8_t)(i >> 1), b;

    if (i >= BCN_NCHIPS - 1u)
        return (i == BCN_NCHIPS - 1u) ? 1u : BCN_END;   /* closing chip */
    if (k < BCN_PRE_BITS)
        b = 1u;
    else if (k < BCN_PRE_BITS + 8u)
        b = (uint8_t)((BCN_SYNC >> (7u - (k - BCN_PRE_BITS))) & 1u);
    else {
        k = (uint8_t)(k - BCN_PRE_BITS - 8u);
        b = (uint8_t)((pkt[k >> 3] >> (7u - (k & 7u))) & 1u);
    }
    return (uint8_t)((i & 1u) ? !b : b);               /* 1 = on,off  0 = off,on */
}
