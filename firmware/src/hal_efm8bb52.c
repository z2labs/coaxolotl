/*
 * Coaxolotl - EFM8BB52F32G (QFN32) hardware layer, SDCC.
 *
 * Pin map (docs/hardware.md):
 *   P0.0 SENSE     in   VCOAX divider, port-match wake (both edges)
 *   P0.1 FAULT_N   in   TPS2553 FAULT, port-match wake
 *   P0.2 VC_ADC    ana  same divider node as SENSE   (ADC0.0)
 *   P0.3 SCAP_ADC  ana  supercap / 2                 (ADC0.1)
 *   P0.4 TP_TX     out  UART0 TX test point, low     (bootloader UART)
 *   P0.5 TP_RX     in   UART0 RX test point
 *   P0.6 LNA_EN    out  TPS2553 EN        (10k pull-down: off in reset)
 *   P0.7 LNA_PATH  out  input SPDT U6, 1=LNA (10k pull-down: bypass)
 *   P1.0..2 SW_V1..3   both PE42582      (10k pull-downs: THRU in reset)
 *   P1.3 LNA_PATH_OUT out output SPDT U7, same polarity (10k pull-down)
 *   P1.4 FMN       out  FM notch SPDTs   (10k pull-down: out in reset)
 *   P1.5, P1.6     spare, driven low
 *   P1.7 BEACON    out  PCA CEX0 during a reply, low otherwise
 *   P2.0 LED_SER, P2.1 LED_SCK, P2.2 LED_RCK, P2.3 LED_OE_N (10k pull-up)
 *   P2.4 ILNA_ADC  ana  INA180A2 output              (ADC0.13)
 *   P2.5, P2.6, P3.0..P3.4  spare, driven low
 *   P3.7 C2D       debug; held low at reset = factory bootloader
 *
 * SYSCLK stays at the reset default, HFOSC0/8 = 3.0625 MHz, except while a
 * back-channel reply is on the air: then HFOSC0 24.5 MHz feeds the PCA,
 * whose CEX0 toggles at 12.25 MHz on P1.7.
 * The ADC always runs its SAR clock from HFO/2 = 24.5 MHz (it needs
 * 12.5-25 MHz) with the internal 1.8 V reference at gain 0.5: 3.6 V full
 * scale, independent of VDD.
 */
#include <stdint.h>
#include "efm8bb52_sfr.h"
#include "config.h"
#include "bandplan.h"
#include "hal.h"

#define PIN_SENSE  0x01u
#define PIN_FAULT  0x02u

SBIT(LNA_EN,       0x86);   /* P0.6 */
SBIT(LNA_PATH,     0x87);   /* P0.7 */
SBIT(LNA_PATH_OUT, 0x93);   /* P1.3 */
SBIT(FMN,          0x94);   /* P1.4 */
SBIT(BEACON,       0x97);   /* P1.7 */
SBIT(LED_SER,      0xA0);   /* P2.0 */
SBIT(LED_SCK,      0xA1);   /* P2.1 */
SBIT(LED_RCK,      0xA2);   /* P2.2 */
SBIT(LED_OE_N,     0xA3);   /* P2.3 */

/* ADC0MX channels (RM table 13.1, QFN32) */
#define ADC_CH_VC    0u     /* P0.2 */
#define ADC_CH_SCAP  1u     /* P0.3 */
#define ADC_CH_ILNA  13u    /* P2.4 */
#define ADC_REF_MV   3600ul /* 1.8 V / gain 0.5 */
#define ADC_FS12     4095u

/* SYSCLK/12 = 255.2 kHz -> 1 ms; 2.042 MHz while the beacon runs */
#define T2_RELOAD      ((uint16_t)(65536u - 255u))
#define T2_RELOAD_FAST ((uint16_t)(65536u - 2042u))

#define EBUF 8u
static volatile uint8_t  e_head, e_tail, e_lvl[EBUF];
static volatile uint16_t e_t[EBUF];
static volatile uint8_t  g_lvl, g_fault, g_rst, g_hk;
#if !CFG_SNOOZE
static volatile uint16_t g_ms, g_hk_ms;
#endif

static const CODE uint8_t port_map[8] = BP_PORT_INIT;

/* Timer4 runs from LFOSC0/8, asynchronous to SYSCLK. After a wake by
 * another source it is unreadable for up to 3 timer clocks (STSYNC). */
#define T4_READ(dst) do {                                   \
        uint8_t _sp = SFRPAGE, _h, _l;                      \
        SFRPAGE = SFRPAGE_10;                               \
        while (TMR4CN1 & TMR4CN1_STSYNC)                    \
            ;                                               \
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

#if CFG_SNOOZE
/* Timer4 wrap (~6.5 s): housekeeping. Enabled as an interrupt so that it
 * is a Snooze wake source whatever "Timer 4 event" means in the RM. */
void hal_isr_timer4(void) __interrupt(17)
{
    uint8_t sp = SFRPAGE;
    SFRPAGE = SFRPAGE_10;
    TMR4CN0 &= (uint8_t)~TMR4CN0_TF4H;
    SFRPAGE = sp;
    g_hk = 1;
}
#endif

/* ------------------------------------------------------------------ */

void hal_init(void)
{
    EA = 0;
    WDTCN = 0xDE;                       /* watchdog off during init */
    WDTCN = 0xAD;
    SFRPAGE = SFRPAGE_0;

    g_rst = RSTSRC;                     /* supply monitor is always on (RM 9.3.3) */

    /* latches first: safe RF state, lamps dark, spares low */
    P0 = 0x2Fu;                         /* SENSE, FAULT, ADCs, RX high-Z; TX, LNA_EN, LNA_PATH low */
    P1 = 0x00u;                         /* V=000 (THRU), LNA_PATH_OUT, FMN, BEACON low */
    P2 = 0x18u;                         /* lamp chain idle, OE_N=1, P2.4 analog */
    P3 = 0x80u;                         /* P3.0..P3.4 low, C2D released */
    P0MDIN  = 0xF3u;                    /* P0.2, P0.3 analog                    */
    P0MDOUT = 0xD0u;                    /* P0.4, P0.6, P0.7 push-pull           */
    P1MDIN  = 0xFFu;
    P1MDOUT = 0xFFu;
    P2MDOUT = 0x6Fu;                    /* all but P2.4 (analog) push-pull      */
    P0SKIP  = 0xFFu;                    /* no crossbar peripherals              */
    P1SKIP  = 0xFFu;
    SFRPAGE = SFRPAGE_20;
    P2MDIN  = 0xEFu;                    /* P2.4 analog                          */
    P2SKIP  = 0x0Fu;
    P3MDOUT = 0x1Fu;                    /* P3.0..P3.4 push-pull                 */
    SFRPAGE = SFRPAGE_0;
    XBR0 = 0x00u;
    XBR1 = 0x00u;
    XBR2 = XBR2_WEAKPUD | XBR2_XBARE;   /* external pulls only, enable drivers  */

    /* ADC: SARCLK 24.5 MHz, internal 1.8 V at gain 0.5, 4x averaging, off */
    CLKGRP0 = CLKGRP0_SAR_HFO2;
    ADC0CF1 = ADC0CF1_TRACK;
    ADC0CF2 = ADC0CF2_1V8;
    ADC0CN1 = ADC0CN1_AVG4;
    ADC0CN2 = 0x00u;                    /* start on ADBUSY                      */
    ADC0CN0 = ADC0CN0_GAIN_0P5;         /* ADEN = 0                             */

    /* Timer2: 1 ms tick (wakes Idle; is the time base when !CFG_SNOOZE) */
    TMR2CN0 = 0x00u;                    /* SYSCLK/12, 16-bit auto-reload        */
    TMR2RLL = (uint8_t)T2_RELOAD;
    TMR2RLH = (uint8_t)(T2_RELOAD >> 8);
    TMR2L   = (uint8_t)T2_RELOAD;
    TMR2H   = (uint8_t)(T2_RELOAD >> 8);
    ET2 = 1;
    TR2 = 1;

#if CFG_SNOOZE
    /* Timer4: free-running 16-bit from LFOSC0/8 = 10 kHz, runs in Snooze */
    LFO0CN = (uint8_t)((LFO0CN & 0x3Cu) | LFO0CN_OSCLEN | LFO0CN_LFODIV_1);
    SFRPAGE = SFRPAGE_10;
    TMR4CN0 = 0x00u;
    CKCON1  = 0x00u;                    /* T4ML = 0: use T4XCLK source          */
    TMR4CN1 = TMR4CN1_RLF_NONE;
    TMR4RLL = 0x00u;
    TMR4RLH = 0x00u;
    TMR4L   = 0x00u;
    TMR4H   = 0x00u;
    TMR4CN0 = TMR4CN0_TR4 | TMR4CN0_T4XCLK_LFO;
    SFRPAGE = SFRPAGE_0;
    EIE2   |= EIE2_ET4;
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

uint8_t hal_edge_pop(uint8_t XPTR *level, uint16_t XPTR *t)
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
    EA = 0;
    f = g_hk;
    g_hk = 0;
    EA = 1;
    return f;
}

static uint16_t adc_read(uint8_t ch)
{
    uint16_t guard;

    ADC0MX = ch;
    ADINT = 0;
    ADBUSY = 1;                          /* 4 conversions, averaged in hardware */
    for (guard = 0; !ADINT && guard < 2000u; guard++)
        ;
    return (uint16_t)((((uint16_t)ADC0H << 8) | ADC0L) & 0x0FFFu);
}

static uint16_t adc_mv(uint16_t code)
{
    return (uint16_t)(((uint32_t)code * ADC_REF_MV) / ADC_FS12);
}

void hal_measure(uint16_t XPTR *vc_mv, uint16_t XPTR *scap_mv, uint16_t XPTR *i_ma)
{
    uint16_t vc, sc, ia, guard;

    ADEN = 1;
    for (guard = 0; !(ADC0CN1 & ADC0CN1_POWERUP) && guard < 2000u; guard++)
        ;                               /* ADPWR delay, about 10 us */
    vc = adc_mv(adc_read(ADC_CH_VC));
    sc = adc_mv(adc_read(ADC_CH_SCAP));
    ia = adc_mv(adc_read(ADC_CH_ILNA));
    ADEN = 0;

    *vc_mv   = (uint16_t)(((uint32_t)vc * VC_DIV_NUM) / VC_DIV_DEN);
    *scap_mv = (uint16_t)(((uint32_t)sc * SC_DIV_NUM) / SC_DIV_DEN);
    *i_ma    = (uint16_t)(((uint32_t)ia * 1000u) / I_UV_PER_MA);
}

void hal_rf(uint8_t bank, uint8_t lna_path, uint8_t fmn, uint8_t lna_en)
{
    uint8_t code = port_map[bank & 7u];

    /* break before make: leave the LNA path first, enter it last */
    if (!lna_path)
        LNA_PATH = LNA_PATH_OUT = 0;
    if (!lna_en)
        LNA_EN = 0;
    P1 = (uint8_t)((P1 & 0xF8u) | (code & 7u));
    FMN = fmn ? 1 : 0;
    if (lna_en)
        LNA_EN = 1;
    if (lna_path)
        LNA_PATH = LNA_PATH_OUT = 1;
}

void hal_panel(const uint8_t XPTR *img)
{
    uint8_t p = 48u, id, b;

    /* chain position p = 8 * register + output (U14 = register 0, nearest the MCU). Register k sits under strip
     * columns 4k..4k+3: QA-QD drive row 1 (lamps 0-23), QE-QH row 2 (lamps 24-47) - docs/panel.md */
    while (p--) {                        /* U19.QH first, U14.QA last */
        id = (uint8_t)(((p & 4u) ? 24u : 0u) + ((p >> 3) << 2) + (p & 3u));
        b = (uint8_t)((img[id >> 3] >> (id & 7u)) & 1u);
        LED_SER = b ? 0 : 1;             /* sink outputs: 0 = lamp on */
        LED_SCK = 1;
        LED_SCK = 0;
    }
    LED_RCK = 1;
    LED_RCK = 0;
    LED_SER = 0;
    /* all dark (panel OFF): outputs Hi-Z, not just high */
    LED_OE_N = (img[0] | img[1] | img[2] | img[3] | img[4] | img[5]) ? 0 : 1;
}

/* ---- panel mode log in flash ---------------------------------------
 * One 2 kB page (NV_PAGE). Each change appends a record 0x50 | mode;
 * the last record wins. The page is erased only when it is full, so a
 * mode change costs one byte write (70 us), not an erase.
 * Sequence per RM 4.3.3 / AN201: interrupts off, OKVDDF, key, PSCTL,
 * MOVX, PSCTL = 0. The target address is kept in DATA, not XRAM. */
#define NV_REC(v)   ((uint8_t)(0x50u | ((v) & 3u)))
#define NV_IS_REC(b) (((b) & 0xFCu) == 0x50u)

static __data uint16_t nv_addr;

static void nv_op(uint8_t v, uint8_t erase)
{
    uint8_t ea = EA;
    if (nv_addr < NV_PAGE || nv_addr >= NV_PAGE + NV_PAGE_SIZE)
        return;                         /* bounds check (AN201) */
    EA = 0;
    if (PSCTL & PSCTL_OKVDDF) {
        FLKEY = 0xA5u;
        FLKEY = 0xF1u;
        PSCTL = erase ? (PSCTL_PSEE | PSCTL_PSWE) : PSCTL_PSWE;
        *(__xdata uint8_t *)nv_addr = v;
        PSCTL = 0x00u;
    }
    EA = ea;
}

uint8_t hal_nv_load(uint8_t dflt)
{
    uint16_t i;
    uint8_t b, v = dflt;
    for (i = 0; i < NV_PAGE_SIZE; i++) {
        b = *(const __code uint8_t *)(NV_PAGE + i);
        if (b == 0xFFu)
            break;
        if (NV_IS_REC(b))
            v = (uint8_t)(b & 3u);
    }
    return v;
}

void hal_nv_save(uint8_t v)
{
    uint16_t i;
    for (i = 0; i < NV_PAGE_SIZE; i++)
        if (*(const __code uint8_t *)(NV_PAGE + i) == 0xFFu)
            break;
    if (i == NV_PAGE_SIZE) {             /* page full: start over */
        nv_addr = NV_PAGE;
        nv_op(0xFFu, 1);
        i = 0;
    }
    nv_addr = (uint16_t)(NV_PAGE + i);
    nv_op(NV_REC(v), 0);
}

/* ---- back-channel beacon ------------------------------------------ */

uint8_t hal_edge_pending(void)
{
    return (uint8_t)(e_head != e_tail);
}

static void t2_reload(uint16_t r)
{
    TMR2RLL = (uint8_t)r;               /* takes effect on the next wrap */
    TMR2RLH = (uint8_t)(r >> 8);
}

void hal_beacon_on(void)
{
    CLKSEL = CLKSEL_HFOSC0_1;           /* 24.5 MHz: FLRT may stay 0 (< 25 MHz) */
    while (!(CLKSEL & CLKSEL_DIVRDY))
        ;
    t2_reload(T2_RELOAD_FAST);          /* keep the 1 ms tick of the !SNOOZE build */

    CR = 0;
    PCA0MD   = PCA0MD_CPS_SYS;
    PCA0L    = 0;
    PCA0H    = 0;
    PCA0CPL0 = BCN_PCA_CPH;
    PCA0CPH0 = BCN_PCA_CPH;             /* toggle every CPH clocks */
    PCA0CPM0 = PCA0CPM_FREQOUT;
    BEACON = 0;
    P1SKIP = 0x7Fu;                     /* only P1.7 left for the crossbar (P0 all skipped) */
    XBR1   = XBR1_CEX0;
}

void hal_beacon_key(uint8_t on)
{
    CR = on ? 1 : 0;                    /* counter stopped = CEX0 frozen = no carrier */
}

void hal_beacon_off(void)
{
    CR = 0;
    PCA0CPM0 = 0;
    XBR1   = 0x00u;                     /* P1.7 back to its latch (0): no DC into the pad */
    P1SKIP = 0xFFu;
    CLKSEL = CLKSEL_HFOSC0_8;
    while (!(CLKSEL & CLKSEL_DIVRDY))
        ;
    t2_reload(T2_RELOAD);
}

/* ------------------------------------------------------------------ */

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
    if (e_head == e_tail && !g_hk) {     /* nothing arrived meanwhile */
        PCON1 |= PCON1_SNOOZE;          /* SYSCLK is HFOSC0 (/8): allowed */
        __asm nop __endasm;
        __asm nop __endasm;
        SFRPAGE = SFRPAGE_10;
        PSTAT0 = 0x00u;                  /* wake flags, informational only */
        SFRPAGE = SFRPAGE_0;
    }
    TR2 = 1;
#else
    hal_idle();
#endif
}
