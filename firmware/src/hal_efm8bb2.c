/*
 * Coaxolotl - EFM8BB21F16G (QSOP24) hardware layer, SDCC.
 *
 * Pin map (docs/hardware.md):
 *   P0.0 SENSE     in   VCOAX divider, port-match wake (both edges)
 *   P0.1 VC_ADC    ana  same divider node
 *   P0.2 SCAP_ADC  ana  supercap / 2
 *   P0.3 ILNA_ADC  ana  INA180A2 output
 *   P0.4 TP_TX     out  spare / UART0 TX test point (driven low)
 *   P0.5 FAULT_N   in   TPS2553 FAULT, port-match wake
 *   P0.6 LNA_EN    out  TPS2553 EN        (10k pull-down: off in reset)
 *   P0.7 LNA_PATH  out  SPDT pair, 1=LNA  (10k pull-down: bypass in reset)
 *   P1.0..2 SW_V1..3   both PE42582      (10k pull-downs: THRU in reset)
 *   P1.3 FMN       out  FM notch SPDTs   (10k pull-down: out in reset)
 *   P1.4..7        spare, driven low (test points)
 *   P2.0 LED_SER, P2.1 LED_SCK, P2.2 LED_RCK, P2.3 LED_OE_N (10k pull-up)
 *
 * Pin sides follow the placement: QSOP pins 1-12 (analog, power, debug,
 * lamp chain) face the power/panel area, pins 13-24 (RF switch control,
 * LNA_EN/FAULT_N) face the RF chain - no control line crosses the MCU.
 *   P3.0 C2D       debug
 *
 * SYSCLK stays at the reset default, HFOSC0/8 = 3.0625 MHz.
 */
#include <stdint.h>
#include "efm8bb2_sfr.h"
#include "config.h"
#include "bandplan.h"
#include "hal.h"

#define PIN_SENSE  0x01u
#define PIN_FAULT  0x20u

SBIT(LNA_EN,   0x86);   /* P0.6 */
SBIT(LNA_PATH, 0x87);   /* P0.7 */
SBIT(LED_SER,  0xA0);   /* P2.0 */
SBIT(LED_SCK,  0xA1);   /* P2.1 */
SBIT(LED_RCK,  0xA2);   /* P2.2 */
SBIT(LED_OE_N, 0xA3);   /* P2.3 */

SFR(P2MDOUT, 0xA6);     /* page 0x00 */
SFR(LFO0CN,  0xB1);

/* SYSCLK/12 = 255.2 kHz -> 1 ms */
#define T2_RELOAD ((uint16_t)(65536u - 255u))

#define EBUF 8u
static volatile uint8_t  e_head, e_tail, e_lvl[EBUF];
static volatile uint16_t e_t[EBUF];
static volatile uint8_t  g_lvl, g_fault, g_rst;
#if !CFG_SNOOZE
static volatile uint16_t g_ms, g_hk_ms;
static volatile uint8_t  g_hk;
#endif

static const CODE uint8_t port_map[8] = BP_PORT_INIT;

/* Timer4 runs from LFOSC0/8, asynchronous to SYSCLK: read until stable */
#define T4_READ(dst) do {                                   \
        uint8_t _sp = SFRPAGE, _h, _l;                      \
        SFRPAGE = SFRPAGE_10;                               \
        do { _h = TMR4H; _l = TMR4L; } while (_h != TMR4H); \
        SFRPAGE = _sp;                                      \
        (dst) = (uint16_t)(((uint16_t)_h << 8) | _l);       \
    } while (0)

/* ------------------------------------------------------------------ */

/* runs before the C runtime clears XRAM: the reset-default watchdog
 * must not bite while 2 kB are zeroed at 3 MHz */
unsigned char __sdcc_external_startup(void)
{
    WDTCN = 0xDE;
    WDTCN = 0xAD;
    return 0;
}

void hal_isr_pmatch(void) __interrupt(8)
{
    uint8_t p = P0;
    uint8_t lvl, n;
    uint16_t t;

    P0MAT = p;                          /* re-arm on the opposite level */
    if (!(p & PIN_FAULT))
        g_fault = 1;

    lvl = (p & PIN_SENSE) ? 1u : 0u;
    if (lvl == g_lvl)
        return;
    g_lvl = lvl;
#if CFG_SNOOZE
    T4_READ(t);
#else
    t = g_ms;
#endif
    n = (uint8_t)((e_head + 1u) & (EBUF - 1u));
    if (n != e_tail) {
        e_t[e_head] = t;
        e_lvl[e_head] = lvl;
        e_head = n;
    }
}

void hal_isr_timer2(void) __interrupt(5)
{
    TF2H = 0;
#if !CFG_SNOOZE
    g_ms++;
    if (++g_hk_ms >= 6554u) {           /* same period as a Timer4 wrap */
        g_hk_ms = 0;
        g_hk = 1;
    }
#endif
}

/* ------------------------------------------------------------------ */

void hal_init(void)
{
    uint8_t i;

    EA = 0;
    WDTCN = 0xDE;                       /* watchdog off during init */
    WDTCN = 0xAD;
    SFRPAGE = SFRPAGE_0;

    g_rst = RSTSRC;
    VDM0CN = VDM0CN_VDMEN;              /* supply monitor on ...          */
    for (i = 0; i < 50u; i++)
        ;                               /* ... let it settle (~100 us)   */
    RSTSRC = RSTSRC_PORSF;              /* ... and make it a reset source */

    /* latches first: safe RF state, LEDs dark */
    P0 = 0x2Fu;                         /* LNA_EN=0, LNA_PATH=0, TP=0, inputs 1 */
    P1 = 0x00u;                         /* V=000 (THRU), FMN=0, spares low      */
    P2 = 0x08u;                         /* lamp chain idle, OE_N=1              */
    P0MDIN  = 0xF1u;                    /* P0.1..P0.3 analog                    */
    P0MDOUT = 0xD0u;                    /* P0.4, P0.6, P0.7 push-pull           */
    P1MDIN  = 0xFFu;
    P1MDOUT = 0xFFu;
    P2MDOUT = 0x0Fu;
    P0SKIP  = 0xFFu;                    /* no crossbar peripherals              */
    P1SKIP  = 0xFFu;
    XBR0 = 0x00u;
    XBR1 = 0x00u;
    XBR2 = XBR2_WEAKPUD | XBR2_XBARE;   /* external pulls only, enable drivers  */

    /* ADC: VDD reference, SARCLK = SYSCLK, gain 1, off until used */
    REF0CN = 0x08u;                     /* VERIFY: REFSL = VDD                  */
    ADC0CF = 0x01u;                     /* VERIFY: ADSC = 0, ADGN = 1           */
    ADEN = 0;

    /* Timer2: 1 ms tick (wakes Idle; is the time base when !CFG_SNOOZE) */
    TMR2CN0 = 0x00u;                    /* SYSCLK/12, 16-bit auto-reload        */
    TMR2RLL = (uint8_t)T2_RELOAD;
    TMR2RLH = (uint8_t)(T2_RELOAD >> 8);
    TMR2L   = (uint8_t)T2_RELOAD;
    TMR2H   = (uint8_t)(T2_RELOAD >> 8);
    ET2 = 1;
    TR2 = 1;

#if CFG_SNOOZE
    /* Timer4: free-running 16-bit from LFOSC0/8 = 10 kHz, survives Snooze */
    LFO0CN |= 0x80u;                    /* VERIFY: OSCLEN                       */
    SFRPAGE = SFRPAGE_10;
    TMR4CN0 = 0x00u;
    CKCON1  = 0x00u;                    /* T4ML = 0: use T4XCLK source          */
    TMR4RLL = 0x00u;
    TMR4RLH = 0x00u;
    TMR4L   = 0x00u;
    TMR4H   = 0x00u;
    TMR4CN0 = TMR4CN0_TR4 | TMR4CN0_T4XCLK_LFO;
    SFRPAGE = SFRPAGE_0;
#endif

    /* edge capture on SENSE and FAULT_N */
    g_lvl = (P0 & PIN_SENSE) ? 1u : 0u;
    P0MASK = PIN_SENSE | PIN_FAULT;
    P0MAT  = P0;
    EIE1  |= EIE1_EMAT;

    /* watchdog: longest interval (4^10 LFOSC clocks, ~13 s) */
    WDTCN = 0x07u;
    WDTCN = 0xA5u;

    EA = 1;
}

uint8_t hal_reset_abnormal(void)
{
    if (g_rst & RSTSRC_PORSF)
        return 0;                       /* power-on / brown-out: normal start */
    return (uint8_t)((g_rst & (RSTSRC_WDTRSF | RSTSRC_MCDRSF |
                               RSTSRC_FERROR | RSTSRC_SWRSF)) != 0u);
}

uint16_t hal_now(void)
{
    uint16_t t;
#if CFG_SNOOZE
    T4_READ(t);
#else
    ET2 = 0;
    t = g_ms;
    ET2 = 1;
#endif
    return t;
}

uint8_t hal_sense(void)
{
    return (P0 & PIN_SENSE) ? 1u : 0u;
}

uint8_t hal_edge_pop(uint8_t *level, uint16_t *t)
{
    uint8_t i = e_tail;
    if (i == e_head)
        return 0;
    *t = e_t[i];
    *level = e_lvl[i];
    e_tail = (uint8_t)((i + 1u) & (EBUF - 1u));
    return 1;
}

uint8_t hal_take_fault(void)
{
    uint8_t f;
    EA = 0;
    f = g_fault;
    g_fault = 0;
    EA = 1;
    return (uint8_t)(f || !(P0 & PIN_FAULT));
}

uint8_t hal_take_hk(void)
{
    uint8_t f;
#if CFG_SNOOZE
    SFRPAGE = SFRPAGE_10;
    f = (TMR4CN0 & TMR4CN0_TF4H) ? 1u : 0u;
    if (f)
        TMR4CN0 &= (uint8_t)~TMR4CN0_TF4H;
    SFRPAGE = SFRPAGE_0;
#else
    EA = 0;
    f = g_hk;
    g_hk = 0;
    EA = 1;
#endif
    return f;
}

static uint16_t adc_read(uint8_t ch)
{
    uint16_t acc = 0, guard;
    uint8_t i;

    ADC0MX = ch;
    for (i = 0; i < 4u; i++) {
        ADINT = 0;
        ADBUSY = 1;
        for (guard = 0; !ADINT && guard < 2000u; guard++)
            ;
        acc += (uint16_t)(((uint16_t)ADC0H << 8) | ADC0L);
    }
    return (uint16_t)(acc >> 2);
}

void hal_measure(uint16_t *vc_mv, uint16_t *scap_mv, uint16_t *i_ma)
{
    uint16_t vc, sc, ia;
    uint8_t i;

    ADEN = 1;
    for (i = 0; i < 20u; i++)
        ;                               /* ADC power-up */
    vc = adc_read(1);
    sc = adc_read(2);
    ia = adc_read(3);
    ADEN = 0;

    *vc_mv   = (uint16_t)(((uint32_t)vc * VDD_MV * VC_DIV_NUM) / ((uint32_t)ADC_FS * VC_DIV_DEN));
    *scap_mv = (uint16_t)(((uint32_t)sc * VDD_MV * SC_DIV_NUM) / ((uint32_t)ADC_FS * SC_DIV_DEN));
    *i_ma    = (uint16_t)(((uint32_t)ia * VDD_MV * 1000u / ADC_FS) / I_UV_PER_MA);
}

void hal_rf(uint8_t bank, uint8_t lna_path, uint8_t fmn, uint8_t lna_en)
{
    uint8_t code = port_map[bank & 7u];

    /* break before make: leave the LNA path first, enter it last */
    if (!lna_path)
        LNA_PATH = 0;
    if (!lna_en)
        LNA_EN = 0;
    P1 = (uint8_t)((P1 & 0xF0u) | (code & 7u) | (fmn ? 0x08u : 0u));
    if (lna_en)
        LNA_EN = 1;
    if (lna_path)
        LNA_PATH = 1;
}

void hal_panel(const uint8_t *img)
{
    uint8_t id = 48u, b;

    while (id--) {                       /* lamp 47 first, lamp 0 ends in U1.QA */
        b = (uint8_t)((img[id >> 3] >> (id & 7u)) & 1u);
        LED_SER = b ? 0 : 1;             /* sink outputs: 0 = lamp on */
        LED_SCK = 1;
        LED_SCK = 0;
    }
    LED_RCK = 1;
    LED_RCK = 0;
    LED_OE_N = 0;
}

void hal_wdt_kick(void)
{
    WDTCN = 0xA5u;
}

void hal_idle(void)
{
    PCON0 |= PCON0_IDLE;
    PCON0 = PCON0;                       /* SiLabs recommended dummy write */
}

void hal_snooze(void)
{
#if CFG_SNOOZE
    TR2 = 0;
    if (e_head == e_tail) {              /* nothing arrived meanwhile */
        PCON1 |= PCON1_SNOOZE;
        __asm nop __endasm;
        __asm nop __endasm;
    }
    TR2 = 1;
#else
    hal_idle();
#endif
}
