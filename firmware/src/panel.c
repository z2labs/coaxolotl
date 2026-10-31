/*
 * Coaxolotl - app state -> 48-bit lamp image. See docs/panel.md.
 */
#include "panel.h"
#include "decoder.h"

static void on(uint8_t XPTR *img, uint8_t id)
{
    img[id >> 3] |= (uint8_t)(1u << (id & 7u));
}

void panel_render(const app_t XPTR *a, uint8_t XPTR *img)
{
    uint8_t i;

    for (i = 0; i < PANEL_BYTES; i++)
        img[i] = 0;

    if (a->boot == BOOT_LAMPTEST) {          /* every lamp, every colour */
        for (i = 0; i < PANEL_BYTES; i++)
            img[i] = 0xFFu;
        return;
    }
    if (a->boot == BOOT_SWEEP) {             /* one lamp chases the chain */
        on(img, a->boot_i);
        return;
    }

    /* bank: one-hot, or the travelling lamp during a change */
    on(img, (uint8_t)(L_F0 + (a->sw_active ? a->sw_pos : a->bank)));

    /* frame register, exactly as received */
    for (i = 0; i < a->frame_n && i < DEC_NBITS; i++)
        if (a->frame_bits & (uint16_t)(1u << i))
            on(img, (uint8_t)(L_FR0 + i));
    if (a->rx)
        on(img, L_RX);
    if (a->bit_lamp)
        on(img, a->last_bit ? L_BIT1 : L_BIT0);

    /* frame result */
    if (a->result == RES_OK)
        on(img, L_OK);
    if (a->result == RES_ERR) {
        if (!(a->err_blinks & 1u))
            on(img, L_ERR);
        switch (a->err) {
        case ERR_SYNC: on(img, L_ESYNC); break;
        case ERR_CHK:  on(img, L_ECHK);  break;
        case ERR_GAP:  on(img, L_EGAP);  break;
        case ERR_RUNT: on(img, L_ERUNT); break;
        case ERR_TMO:  on(img, L_ETMO);  break;
        default: break;                       /* ERR_DROP: DROP lamp */
        }
    }

    /* RF path */
    on(img, a->lna_path ? L_LNA : L_BYP);
    if (a->auto_byp)
        on(img, L_AUTO);
    if (a->fmn)
        on(img, L_FMN);
    if (a->i_class == I_LO)
        on(img, L_ILO);
    if (a->i_class == I_OK)
        on(img, L_IOK);
    if (a->i_class == I_HI)
        on(img, L_IHI);
    if (a->trip)
        on(img, L_TRIP);

    /* system */
    if (!a->quiet && (a->active || a->run_hb))
        on(img, L_RUN);
    if (a->scap_ok || a->hold_phase)
        on(img, L_HOLD);
    if (a->rail_low)
        on(img, L_LOW);
    if (a->quiet)
        on(img, L_QUIET);
    if (a->st_drop)
        on(img, L_DROP);
    if (a->st_glt)
        on(img, L_GLT);
    if (a->st_rst)
        on(img, L_RST);
}

uint8_t panel_count(const uint8_t XPTR *img)
{
    uint8_t i, b, n = 0;
    for (i = 0; i < PANEL_BYTES; i++)
        for (b = img[i]; b; b &= (uint8_t)(b - 1u))
            n++;
    return n;
}
