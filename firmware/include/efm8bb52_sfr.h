/*
 * Minimal SFR map for the EFM8BB52 (SDCC), only what Coaxolotl uses.
 * Addresses, pages and bit fields are from the EFM8BB52 Reference Manual
 * rev 0.3 (tables 3.2/3.3 and the register chapters named below).
 *
 * Differences from the EFM8BB2 that matter here:
 *  - PCON1 is at 0xCD (BB2: 0x9A); TMR2L/H are at 0xCE/0xCF
 *  - T4XCLK = 3 selects LFOSC0/8 (1 would be the external oscillator)
 *  - no VDM0CN: the supply monitor is always on; RSTSRC.PORSF is read-only
 *  - flash: write the key first, then PSCTL; check PSCTL.OKVDDF
 *  - new 12-bit ADC: SAR clock 12.5-25 MHz from CLKGRP0, own references
 *  - P2/P3 configuration registers live on SFR page 0x20
 */
#ifndef EFM8BB52_SFR_H
#define EFM8BB52_SFR_H

#define SFR(name, addr)  __sfr __at(addr) name
#define SBIT(name, addr) __sbit __at(addr) name

/* ---- all pages ----------------------------------------------------- */
SFR(P0,       0x80);
SFR(SP,       0x81);
SFR(PCON0,    0x87);
SFR(PSCTL,    0x8F);   /* 4.4.1  */
SFR(P1,       0x90);
SFR(WDTCN,    0x97);
SFR(P2,       0xA0);
SFR(SFRPAGE,  0xA7);
SFR(IE,       0xA8);
SFR(CLKSEL,   0xA9);   /* 8.4.1  */
SFR(P3,       0xB0);
SFR(FLKEY,    0xB7);
SFR(PCON1,    0xCD);   /* 7.9.2  */

/* ---- page 0x00 (most also on 0x10/0x20/0x30) ----------------------- */
SFR(CKCON0,   0x8E);
SFR(P0MDOUT,  0xA4);
SFR(P1MDOUT,  0xA5);
SFR(P2MDOUT,  0xA6);   /* pages 0x00, 0x20 (0x10: CKCON1)   */
SFR(CLKGRP0,  0xAF);   /* 8.4.2  */
SFR(LFO0CN,   0xB1);   /* 8.4.6  */
SFR(ADC0CN1,  0xB2);   /* 13.4.2 */
SFR(ADC0CN2,  0xB3);   /* 13.4.3 */
SFR(ADC0CF1,  0xB9);   /* 13.4.4 */
SFR(ADC0MX,   0xBB);   /* 13.4.12 */
SFR(ADC0L,    0xBD);
SFR(ADC0H,    0xBE);
SFR(TMR2CN0,  0xC8);
SFR(TMR2RLL,  0xCA);
SFR(TMR2RLH,  0xCB);
SFR(TMR2L,    0xCE);
SFR(TMR2H,    0xCF);
SFR(P0SKIP,   0xD4);
SFR(P1SKIP,   0xD5);
SFR(PCA0CN0,  0xD8);   /* 19.4.1 */
SFR(PCA0MD,   0xD9);   /* 19.4.2 */
SFR(PCA0CPM0, 0xDA);   /* 19.4.9 */
SFR(ADC0CF2,  0xDF);   /* 13.4.5 */
SFR(XBR0,     0xE1);
SFR(XBR1,     0xE2);   /* 11.4.2 */
SFR(XBR2,     0xE3);   /* 11.4.3 */
SFR(EIE1,     0xE6);
SFR(ADC0CN0,  0xE8);   /* 13.4.1 */
SFR(RSTSRC,   0xEF);   /* 9.4.1, page 0x00 only */
SFR(P0MDIN,   0xF1);
SFR(P1MDIN,   0xF2);
SFR(EIE2,     0xF3);   /* pages 0x00, 0x10 (0x20: P2MDIN)   */
SFR(PCA0L,    0xF9);
SFR(PCA0H,    0xFA);
SFR(PCA0CPL0, 0xFB);
SFR(PCA0CPH0, 0xFC);
SFR(P0MAT,    0xFD);
SFR(P0MASK,   0xFE);

/* ---- page 0x10 ----------------------------------------------------- */
SFR(TMR4CN0,  0x98);   /* 23.4.25 */
SFR(TMR4RLL,  0xA2);
SFR(TMR4RLH,  0xA3);
SFR(TMR4L,    0xA4);   /* shares the address with P0MDOUT */
SFR(TMR4H,    0xA5);   /* shares the address with P1MDOUT */
SFR(CKCON1,   0xA6);   /* 23.4.2, shares the address with P2MDOUT */
SFR(PSTAT0,   0xAA);   /* 7.9.3, Snooze wake flags */
SFR(TMR4CN1,  0xFF);   /* 23.4.26 */

/* ---- page 0x20 ----------------------------------------------------- */
SFR(P3MDOUT,  0x9C);   /* page 0x00/0x10: PCA0CLR */
SFR(P2SKIP,   0xCC);
SFR(P2MDIN,   0xF3);   /* page 0x00/0x10: EIE2    */
SFR(P3MDIN,   0xF4);

#define SFRPAGE_0   0x00
#define SFRPAGE_10  0x10
#define SFRPAGE_20  0x20

/* ---- bit-addressable ---------------------------------------------- */
SBIT(EA,      0xAF);  /* IE.7      */
SBIT(ET2,     0xAD);  /* IE.5      */
SBIT(TF2H,    0xCF);  /* TMR2CN0.7 */
SBIT(TR2,     0xCA);  /* TMR2CN0.2 */
SBIT(CR,      0xDE);  /* PCA0CN0.6 */
SBIT(ADEN,    0xEF);  /* ADC0CN0.7 */
SBIT(ADINT,   0xED);  /* ADC0CN0.5 */
SBIT(ADBUSY,  0xEC);  /* ADC0CN0.4 */

/* ---- interrupt vectors (table 6.1) -------------------------------- */
#define TIMER2_IRQn  5
#define PMATCH_IRQn  8
#define TIMER4_IRQn  17

/* ---- bit masks ---------------------------------------------------- */
#define XBR1_CEX0        0x01   /* PCA0ME = CEX0 only                      */
#define XBR2_WEAKPUD     0x80
#define XBR2_XBARE       0x40
#define EIE1_EMAT        0x02
#define EIE2_ET4         0x04
#define PCON0_IDLE       0x01
#define PCON1_SNOOZE     0x80
#define PSCTL_PSWE       0x01
#define PSCTL_PSEE       0x02
#define PSCTL_OKVDDF     0x40   /* flash supply monitor above threshold */
#define RSTSRC_PINRSF    0x01
#define RSTSRC_PORSF     0x02
#define RSTSRC_MCDRSF    0x04
#define RSTSRC_WDTRSF    0x08
#define RSTSRC_SWRSF     0x10
#define RSTSRC_FERROR    0x40
#define CLKSEL_DIVRDY    0x80
#define CLKSEL_HFOSC0_1  0x00   /* HFOSC0 24.5 MHz output, divide by 1     */
#define CLKSEL_HFOSC0_8  0x30   /* ... divide by 8 = 3.0625 MHz (reset)    */
#define LFO0CN_OSCLEN    0x80
#define LFO0CN_LFODIV_1  0x03   /* 80 kHz out (reset value)                */
#define TMR4CN0_TF4H     0x80
#define TMR4CN0_TR4      0x04
#define TMR4CN0_T4XCLK_LFO 0x03 /* LFOSC0 / 8                              */
#define TMR4CN1_STSYNC   0x10   /* 1 = timer not readable yet after a wake */
#define TMR4CN1_RLF_NONE 0x60   /* RLFSEL = 3: reload on overflow only     */
#define PCA0MD_CPS_SYS   0x08   /* PCA clock = SYSCLK                      */
#define PCA0CPM_FREQOUT  0x46   /* ECOM | TOG | PWM: frequency output      */
#define CLKGRP0_SAR_HFO2 0x11   /* EN_SARCLK, DIV1, HFO / 2 = 24.5 MHz     */
#define ADC0CN0_GAIN_0P5 0x04   /* ADGN = 0.5: full scale = 2 x VREF       */
#define ADC0CN1_POWERUP  0x80
#define ADC0CN1_AVG4     0x11   /* 12-bit, accumulate 4, shift right 2     */
#define ADC0CF1_TRACK    0x3F   /* ADTK = 63 SAR clocks = 2.6 us           */
#define ADC0CF2_1V8      0x3F   /* GND, internal 1.8 V, ADPWR = 15 (10 us) */

#endif
