/*
 * Coaxolotl - settings log in flash. See nvlog.h.
 */
#include "nvlog.h"

#define REC_PANEL 0x50u
#define REC_CAL   0x60u

void nv_load(nv_t XPTR *s, uint8_t panel_default)
{
    uint16_t i = 0;
    uint8_t b, k;

    s->panel = panel_default;
    for (k = 0; k < CAL_N; k++)
        s->cal[k] = CAL_ONE;
    while (i < NV_PAGE_SIZE) {
        b = hal_nv_read(i);
        if ((b & 0xFCu) == REC_PANEL) {
            s->panel = (uint8_t)(b & 3u);
            i++;
        } else if ((b & 0xFCu) == REC_CAL && i + 2u < NV_PAGE_SIZE) {
            s->cal[b & 3u] = (uint16_t)(hal_nv_read(i + 1u) | ((uint16_t)hal_nv_read(i + 2u) << 8));
            i += 3u;
        } else {
            break;                      /* 0xFF = end; anything else = stop safely */
        }
    }
    s->end = i;
}

static void put_cal(nv_t XPTR *s, uint8_t ch, uint16_t v)
{
    uint16_t i = s->end;
    hal_nv_write(i + 1u, (uint8_t)v);
    hal_nv_write(i + 2u, (uint8_t)(v >> 8));
    hal_nv_write(i, (uint8_t)(REC_CAL | (ch & 3u)));   /* header last */
    s->end = (uint16_t)(i + 3u);
}

static void put_panel(nv_t XPTR *s)
{
    hal_nv_write(s->end, (uint8_t)(REC_PANEL | (s->panel & 3u)));
    s->end++;
}

/* make room for n bytes; after an erase the current settings go back first */
static void nv_room(nv_t XPTR *s, uint8_t n)
{
    uint8_t k;
    if (s->end + n <= NV_PAGE_SIZE)
        return;
    hal_nv_erase();
    s->end = 0;
    put_panel(s);
    for (k = 0; k < CAL_N; k++)
        if (s->cal[k] != CAL_ONE)
            put_cal(s, k, s->cal[k]);
}

void nv_put_panel(nv_t XPTR *s, uint8_t mode)
{
    nv_room(s, 1u);
    s->panel = mode;
    put_panel(s);
}

void nv_put_cal(nv_t XPTR *s, uint8_t ch, uint16_t q15)
{
    nv_room(s, 3u);
    s->cal[ch & 3u] = q15;
    put_cal(s, ch, q15);
}
