/*
 * Minimal EFM8BB2 SFR map for SDCC (only what Coaxolotl uses).
 *
 * Addresses transcribed from Silicon Labs SI_EFM8BB2_Defs.inc.
 * Paged registers are marked with their SFR page; set SFRPAGE first.
 * You can swap this file for Silicon Labs' SI_EFM8BB2_Register_Enums.h
 * from the 8051 SDK (Simplicity Studio) if you prefer.
 */
#ifndef EFM8BB2_SFR_H
#define EFM8BB2_SFR_H

#define SFR(name, addr)  __sfr __at(addr) name
#define SBIT(name, addr) __sbit __at(addr) name

/* ---- page 0x00 (and all-page) ---------------------------------- */
SFR(P0,      0x80);
SFR(SP,      0x81);
SFR(PCON0,   0x87);
SFR(CKCON0,  0x8E);
SFR(P1,      0x90);
SFR(WDTCN,   0x97);
SFR(PCON1,   0x9A);
SFR(P2,      0xA0);
SFR(P0MDOUT, 0xA4);
SFR(P1MDOUT, 0xA5);
SFR(SFRPAGE, 0xA7);
SFR(IE,      0xA8);
SFR(CLKSEL,  0xA9);
SFR(P3,      0xB0);
SFR(ADC0MX,  0xBB);
SFR(ADC0CF,  0xBC);
SFR(ADC0L,   0xBD);
SFR(ADC0H,   0xBE);
SFR(TMR2CN0, 0xC8);
SFR(PCA0CN0, 0xD8);   /* VERIFY: PCA block, page 0x00 */
SFR(PCA0MD,  0xD9);
SFR(PCA0CPM0,0xDA);
SFR(PCA0L,   0xF9);
SFR(PCA0H,   0xFA);
SFR(PCA0CPL0,0xFB);
SFR(PCA0CPH0,0xFC);
SFR(TMR2RLL, 0xCA);
SFR(TMR2RLH, 0xCB);
SFR(TMR2L,   0xCC);
SFR(TMR2H,   0xCD);
SFR(REF0CN,  0xD1);
SFR(P0SKIP,  0xD4);
SFR(P1SKIP,  0xD5);
SFR(XBR0,    0xE1);
SFR(XBR1,    0xE2);
SFR(XBR2,    0xE3);
SFR(EIE1,    0xE6);
SFR(ADC0CN0, 0xE8);
SFR(RSTSRC,  0xEF);
SFR(P0MDIN,  0xF1);
SFR(P1MDIN,  0xF2);
SFR(P0MAT,   0xFD);
SFR(P0MASK,  0xFE);
SFR(VDM0CN,  0xFF);   /* page 0x00 */

/* ---- page 0x10 --------------------------------------------------- */
SFR(TMR4CN0, 0x98);   /* page 0x10 (shares address with SCON0)   */
SFR(TMR4RLL, 0xA2);   /* page 0x10 */
SFR(TMR4RLH, 0xA3);   /* page 0x10 */
SFR(TMR4L,   0xA4);   /* page 0x10 (shares address with P0MDOUT) */
SFR(TMR4H,   0xA5);   /* page 0x10 (shares address with P1MDOUT) */
SFR(CKCON1,  0xA6);   /* page 0x10 */
SFR(TMR4CN1, 0xFF);   /* page 0x10 (shares address with VDM0CN)  */

#define SFRPAGE_0   0x00
#define SFRPAGE_10  0x10

/* ---- bit-addressable ---------------------------------------------- */
SBIT(EA,      0xAF);  /* IE.7      */
SBIT(ET2,     0xAD);  /* IE.5      */
SBIT(TF2H,    0xCF);  /* TMR2CN0.7 */
SBIT(TR2,     0xCA);  /* TMR2CN0.2 */
SBIT(ADEN,    0xEF);  /* ADC0CN0.7 */
SBIT(ADINT,   0xED);  /* ADC0CN0.5 */
SBIT(ADBUSY,  0xEC);  /* ADC0CN0.4 */
SBIT(CR,      0xDE);  /* PCA0CN0.6: PCA counter run */

/* ---- interrupt vector numbers (SDCC __interrupt(n)) --------------- */
#define TIMER2_IRQn  5
#define PMATCH_IRQn  8

/* ---- bit masks ---------------------------------------------------- */
#define XBR2_WEAKPUD    0x80
#define XBR2_XBARE      0x40
#define EIE1_EMAT       0x02
#define PCON0_IDLE      0x01
#define RSTSRC_PINRSF   0x01
#define RSTSRC_PORSF    0x02
#define RSTSRC_MCDRSF   0x04
#define RSTSRC_WDTRSF   0x08
#define RSTSRC_SWRSF    0x10
#define RSTSRC_FERROR   0x40
#define VDM0CN_VDMEN    0x80
#define TMR4CN0_TF4H    0x80
#define TMR4CN0_TR4     0x04
#define CLKSEL_DIVRDY   0x80
#define CLKSEL_HFOSC0_1 0x00   /* HFOSC0 24.5 MHz, divide by 1          */
#define CLKSEL_HFOSC0_8 0x30   /* HFOSC0 / 8 = 3.0625 MHz (reset value) */
#define PCA0MD_CPS_SYS  0x08   /* PCA clock = SYSCLK                    */
#define PCA0CPM_FREQOUT 0x46   /* ECOM | TOG | PWM: frequency output    */
#define XBR1_CEX0       0x01   /* PCA0ME = CEX0 only                    */

/*
 * VERIFY against the EFM8BB2 Reference Manual before trusting CFG_SNOOZE=1:
 *  - PCON1 SNOOZE bit position
 *  - TMR4CN0.T4XCLK code that selects LFOSC0/8 (with CKCON1.T4ML = 0)
 *  - whether Snooze wakes on Timer 4 overflow without its interrupt enabled
 *  - the PCA SFR addresses above, CLKSEL layout, and the crossbar putting
 *    CEX0 on P0.4 when every other P0/P1 pin is skipped (beacon)
 * docs/bringup.md has a test for each.
 */
#define PCON1_SNOOZE        0x80   /* VERIFY */
#define TMR4CN0_T4XCLK_LFO  0x01   /* VERIFY: LFOSC0 / 8 */

#endif
