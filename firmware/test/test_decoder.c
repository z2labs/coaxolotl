/*
 * Host unit tests for the decoder, app logic and panel power budget.
 * Build/run: make -C firmware test
 */
#include "harness.h"
#include "bandplan.h"

#define G0 30.0   /* host nominal timing, see config.h */
#define G1 70.0
#define ON 100.0

static int fails = 0, checks = 0;
#define CHECK(cond, ...) do { checks++; if (!(cond)) { fails++; \
    printf("FAIL %s:%d: ", __FILE__, __LINE__); printf(__VA_ARGS__); printf("\n"); } } while (0)

static sim_t S;
static edge_t E[8192];

static void sim_defaults(void)
{
    memset(&S, 0, sizeof S);
    S.vc_mv = 4300; S.scap_mv = 3900; S.i_ma = 100; S.fault_at = -1;
}

static int count_ev(uint8_t ev) { int i, c = 0; for (i = 0; i < S.nev; i++) c += S.ev[i].ev == ev; return c; }
static const evrec_t *last_ev(uint8_t ev)
{ int i; for (i = S.nev - 1; i >= 0; i--) if (S.ev[i].ev == ev) return &S.ev[i]; return NULL; }

/* one frame after 400 ms idle, nominal or distorted */
static void one_frame(uint8_t p, double g0, double g1, double on)
{
    int n = 0;
    double t;
    E[n++] = (edge_t){ 0, 1 };
    t = add_frame(E, &n, 400, p, g0, g1, on);
    sim_run(&S, E, n, t + 500);
}

static void test_all_payloads(void)
{
    int p;
    for (p = 0; p < 64; p++) {
        const evrec_t *ok;
        sim_defaults();
        one_frame((uint8_t)p, G0, G1, ON);
        ok = last_ev(EV_FRAME_OK);
        CHECK(count_ev(EV_FRAME_OK) == 1 && count_ev(EV_FRAME_ERR) == 0,
              "payload 0x%02X: ok=%d err=%d", p, count_ev(EV_FRAME_OK), count_ev(EV_FRAME_ERR));
        CHECK(ok && ok->payload == p, "payload 0x%02X decoded as 0x%02X", p, ok ? ok->payload : 0xFF);
    }
}

static double urand(void) { return (double)rand() / RAND_MAX; }

/* clock skew x fall delay x jitter: every frame must decode */
static void test_robustness(void)
{
    static const double scale[] = { 0.9, 1.0, 1.1 };
    static const double fall[]  = { 0, 5, 10 };
    int si, fi, r, bad = 0, total = 0;
    srand(12345);
    for (si = 0; si < 3; si++)
        for (fi = 0; fi < 3; fi++)
            for (r = 0; r < 200; r++) {
                int n = 0, i;
                uint8_t p = (uint8_t)(rand() & 63);
                double t;
                sim_defaults();
                E[n++] = (edge_t){ 0, 1 };
                t = add_frame(E, &n, 400, p, G0, G1, ON);
                for (i = 1; i < n; i++) {          /* distort like the real link */
                    E[i].t = E[i].t * scale[si] + (urand() * 6.0 - 3.0);
                    if (!E[i].lvl) E[i].t += fall[fi];
                }
                sim_run(&S, E, n, t * scale[si] + 600);
                total++;
                if (!(count_ev(EV_FRAME_OK) == 1 && last_ev(EV_FRAME_OK)->payload == p)) {
                    bad++;
                    if (bad < 5) printf("  robust miss: scale %.1f fall %.0f p=0x%02X\n",
                                        scale[si], fall[fi], p);
                }
            }
    CHECK(bad == 0, "robustness: %d of %d frames lost", bad, total);
    printf("  robustness: %d frames, skew +/-10%%, fall delay 0..10 ms, jitter +/-3 ms: %d lost\n",
           total, bad);
}

static void test_errors(void)
{
    int n, b[16];
    double t;

    /* flipped data bit -> CHK. Bit k: fall = E[1+2k], rise = E[2+2k]. */
    sim_defaults(); n = 0; E[n++] = (edge_t){ 0, 1 };
    t = add_frame(E, &n, 400, 0x2A, G0, G1, ON);
    frame_bits(0x2A, b);
    { int i; for (i = 2 + 2 * 4; i < n; i++) E[i].t += 30; }   /* k=4: 0 -> 1 */
    sim_run(&S, E, n, t + 600);
    CHECK(b[4] == 0 && count_ev(EV_FRAME_ERR) == 1 && last_ev(EV_FRAME_ERR)->err == ERR_CHK,
          "flipped bit -> CHK");

    /* bad sync: first gap short -> SYNC */
    sim_defaults(); n = 0; E[n++] = (edge_t){ 0, 1 };
    t = add_frame(E, &n, 400, 0x15, G0, G1, ON);
    { int i; for (i = 2; i < n; i++) E[i].t -= 30; }
    sim_run(&S, E, n, t + 600);
    CHECK(count_ev(EV_FRAME_ERR) == 1 && last_ev(EV_FRAME_ERR)->err == ERR_SYNC, "bad sync -> SYNC");
    CHECK(count_ev(EV_FRAME_START) == 1, "aborted frame tail must be ignored (starts=%d)",
          count_ev(EV_FRAME_START));

    /* 150 ms gap mid-frame -> GAP */
    sim_defaults(); n = 0; E[n++] = (edge_t){ 0, 1 };
    t = add_frame(E, &n, 400, 0x33, G0, G1, ON);
    { int i; for (i = 2 * 7 + 2; i < n; i++) E[i].t += 120; }
    sim_run(&S, E, n, t + 800);
    CHECK(count_ev(EV_FRAME_ERR) == 1 && last_ev(EV_FRAME_ERR)->err == ERR_GAP, "long gap -> GAP");

    /* 3 ms spike inside a gap -> RUNT */
    sim_defaults(); n = 0; E[n++] = (edge_t){ 0, 1 };
    E[n++] = (edge_t){ 400, 0 }; E[n++] = (edge_t){ 470, 1 };          /* sync 1 */
    E[n++] = (edge_t){ 570, 0 }; E[n++] = (edge_t){ 600, 1 };          /* sync 0 */
    E[n++] = (edge_t){ 700, 0 }; E[n++] = (edge_t){ 770, 1 };          /* sync 1 */
    E[n++] = (edge_t){ 870, 0 }; E[n++] = (edge_t){ 900, 1 };          /* spike  */
    E[n++] = (edge_t){ 903, 0 }; E[n++] = (edge_t){ 940, 1 };
    sim_run(&S, E, n, 1500);
    CHECK(count_ev(EV_FRAME_ERR) == 1 && last_ev(EV_FRAME_ERR)->err == ERR_RUNT, "spike -> RUNT");

    /* 450 ms of power mid-frame -> TMO, then a clean frame is accepted */
    sim_defaults(); n = 0; E[n++] = (edge_t){ 0, 1 };
    t = add_frame(E, &n, 400, 0x21, G0, G1, ON);
    { int i; for (i = 2 * 6 + 1; i < n; i++) E[i].t += 350; }
    t = add_frame(E, &n, t + 350 + 400, 0x0C, G0, G1, ON);
    sim_run(&S, E, n, t + 600);
    CHECK(count_ev(EV_FRAME_ERR) >= 1 && S.ev[0].ev == EV_FRAME_START, "TMO frame starts");
    { const evrec_t *er = NULL; int i;
      for (i = 0; i < S.nev; i++) if (S.ev[i].ev == EV_FRAME_ERR) { er = &S.ev[i]; break; }
      CHECK(er && er->err == ERR_TMO, "long on-time -> TMO"); }
    CHECK(last_ev(EV_FRAME_OK) && last_ev(EV_FRAME_OK)->payload == 0x0C, "frame after TMO decodes");

    /* 1 s dropout mid-frame -> DROP, ride-through, DROP lamp sticky */
    sim_defaults(); n = 0; E[n++] = (edge_t){ 0, 1 };
    E[n++] = (edge_t){ 400, 0 }; E[n++] = (edge_t){ 470, 1 };
    E[n++] = (edge_t){ 570, 0 }; E[n++] = (edge_t){ 1570, 1 };
    sim_run(&S, E, n, 2500);
    CHECK(count_ev(EV_FRAME_ERR) == 1 && last_ev(EV_FRAME_ERR)->err == ERR_DROP, "dropout -> DROP");
    CHECK(count_ev(EV_DROP_BEGIN) == 1 && count_ev(EV_DROP_END) == 1, "dropout begin/end");
    CHECK(S.app.st_drop && S.app.link, "DROP sticky, link back");

    /* 2 ms crackle on the rail: GLT lamp, frame still fine */
    sim_defaults(); n = 0; E[n++] = (edge_t){ 0, 1 };
    E[n++] = (edge_t){ 200, 0 }; E[n++] = (edge_t){ 202, 1 };
    t = add_frame(E, &n, 600, 0x3E, G0, G1, ON);
    sim_run(&S, E, n, t + 600);
    CHECK(count_ev(EV_GLITCH) == 1 && count_ev(EV_FRAME_OK) == 1 && S.app.st_glt, "glitch tolerated");

    /* no frame start without 300 ms of steady power */
    sim_defaults(); n = 0; E[n++] = (edge_t){ 0, 1 };
    t = add_frame(E, &n, 150, 0x10, G0, G1, ON);
    sim_run(&S, E, n, t + 600);
    CHECK(count_ev(EV_FRAME_START) == 0, "frame right after power-up is ignored");
}

static void test_app(void)
{
    int n = 0;
    double t;

    sim_defaults();
    E[n++] = (edge_t){ 0, 1 };
    t = add_frame(E, &n, 1500, (2 << 3) | 4, G0, G1, ON);     /* 160/80 + LNA */
    sim_run(&S, E, n, t + 800);
    CHECK(S.app.bank == 2 && S.app.auto_byp && !S.app.lna_path, "HF branch forces bypass (AUTO)");

    n = 0; E[n++] = (edge_t){ 0, 1 };
    t = add_frame(E, &n, 1500, (6 << 3) | 4, G0, G1, ON);     /* 2m/AIR + LNA */
    sim_run(&S, E, n, t + 800);
    CHECK(S.app.bank == 6 && S.app.lna_path && S.app.fmn && !S.app.auto_byp, "2m: LNA + FM notch");
    CHECK(S.app.i_class == I_OK, "LNA current window OK");

    S.fault_at = 3000;                                          /* LNA fault */
    n = 0; E[n++] = (edge_t){ 0, 1 };
    t = add_frame(E, &n, 1500, (6 << 3) | 4, G0, G1, ON);
    sim_run(&S, E, n, 4000);
    CHECK(S.app.trip && !S.app.lna_en && !S.app.lna_path, "fault trips LNA to bypass");

    S.fault_at = 3000;                                          /* ... CLR retries it */
    n = 0; E[n++] = (edge_t){ 0, 1 };
    t = add_frame(E, &n, 1500, (6 << 3) | 4, G0, G1, ON);
    t = add_frame(E, &n, 4000, (6 << 3) | 4 | 1, G0, G1, ON);
    S.fault_at = 3000;
    sim_run(&S, E, n, t + 800);
    CHECK(!S.app.trip && S.app.lna_path && !S.app.st_drop, "CLR clears trip and stickies");

    sim_defaults();                                             /* over-current by measurement */
    S.i_ma = 170;
    n = 0; E[n++] = (edge_t){ 0, 1 };
    t = add_frame(E, &n, 1500, (5 << 3) | 4, G0, G1, ON);
    sim_run(&S, E, n, t + 3000);
    CHECK(S.app.trip && !S.app.lna_path, "measured over-current trips");

    sim_defaults();                                             /* quiet mode */
    n = 0; E[n++] = (edge_t){ 0, 1 };
    t = add_frame(E, &n, 1500, (7 << 3) | 4 | 2, G0, G1, ON);
    sim_run(&S, E, n, t + 800);
    CHECK(S.app.quiet && !app_busy(&S.app), "quiet mode settles (MCU may snooze)");
}

/* manchester-decode a chip string back into bytes (host decoder mirror) */
static int chips_to_pkt(const uint8_t *pkt_in, uint8_t *out)
{
    uint8_t ch[BCN_NCHIPS + 4], i, c;
    int n = 0, k, bits = (BCN_PRE_BITS + 8 + 8 * BCN_LEN);
    for (i = 0; (c = bcn_chip(pkt_in, i)) != BCN_END; i++) ch[n++] = c;
    if (n != (int)BCN_NCHIPS || ch[n - 1] != 1) return -1;
    memset(out, 0, BCN_LEN);
    for (k = 0; k < bits; k++) {
        int a = ch[2 * k], b = ch[2 * k + 1], v;
        if (a == b) return -2;                         /* not manchester */
        v = a;
        if (k < (int)BCN_PRE_BITS) { if (!v) return -3; continue; }
        if (k < (int)BCN_PRE_BITS + 8) {
            if (v != (int)((BCN_SYNC >> (7 - (k - BCN_PRE_BITS))) & 1u)) return -4;
            continue;
        }
        k -= BCN_PRE_BITS + 8;
        out[k >> 3] |= (uint8_t)(v << (7 - (k & 7)));
        k += BCN_PRE_BITS + 8;
    }
    return 0;
}

static void test_beacon(void)
{
    static const uint8_t check[] = { '1', '2', '3', '4', '5', '6', '7', '8', '9' };
    uint8_t back[BCN_LEN];
    const txrec_t *r;
    int n;
    double t;

    CHECK(bcn_crc8(check, 9) == 0xFB, "CRC-8 check value 0x%02X (want 0xFB)", bcn_crc8(check, 9));
    CHECK(BCN_NCHIPS == 145u, "chip count %u", BCN_NCHIPS);

    /* boot alone: one HELLO once the show is over */
    sim_defaults(); n = 0; E[n++] = (edge_t){ 0, 1 };
    sim_run(&S, E, n, 3000);
    CHECK(S.ntx == 1 && (S.tx[0].pkt[0] >> 6) == REPLY_HELLO, "HELLO after boot (ntx=%d)", S.ntx);

    /* good frame: ACK 300 ms after the last gap, state and readings inside */
    sim_defaults(); S.i_ma = 97; S.vc_mv = 4212; S.scap_mv = 3611;
    n = 0; E[n++] = (edge_t){ 0, 1 };
    t = add_frame(E, &n, 1500, (6 << 3) | 4, G0, G1, ON);
    sim_run(&S, E, n, t + 1500);
    CHECK(S.ntx == 2, "HELLO + ACK (ntx=%d)", S.ntx);
    r = &S.tx[S.ntx - 1];
    CHECK((r->pkt[0] >> 6) == REPLY_ACK && ((r->pkt[0] >> 3) & 7) == ERR_NONE && (r->pkt[0] & 7) == 1,
          "ACK header 0x%02X", r->pkt[0]);
    CHECK(r->pkt[1] == 0xDC, "state byte 0x%02X (F6 LNA FMN)", r->pkt[1]);
    CHECK(r->pkt[3] == 211 && r->pkt[4] == 97 && r->pkt[5] == 181,
          "readings %u %u %u", r->pkt[3], r->pkt[4], r->pkt[5]);
    CHECK(bcn_crc8(r->pkt, 6) == r->pkt[6], "CRC in packet");
    { double last_rise = E[n - 1].t;
      CHECK(r->t - last_rise >= 299 && r->t - last_rise <= 302, "reply %.0f ms after last gap",
            r->t - last_rise); }
    CHECK(chips_to_pkt(r->pkt, back) == 0 && !memcmp(back, r->pkt, BCN_LEN), "chips round-trip");

    /* bad frame: NAK carries the error code */
    sim_defaults(); n = 0; E[n++] = (edge_t){ 0, 1 };
    t = add_frame(E, &n, 1500, 0x15, G0, G1, ON);
    { int i; for (i = 2; i < n; i++) E[i].t -= 30; }
    sim_run(&S, E, n, t + 1500);
    r = &S.tx[S.ntx - 1];
    CHECK((r->pkt[0] >> 6) == REPLY_NAK && ((r->pkt[0] >> 3) & 7) == ERR_SYNC,
          "NAK SYNC header 0x%02X", r->pkt[0]);
    CHECK(r->t > E[n - 1].t + 299, "NAK only after the host's frame tail");

    /* two frames back to back: one reply each, no reply in mid-frame */
    sim_defaults(); n = 0; E[n++] = (edge_t){ 0, 1 };
    t = add_frame(E, &n, 1500, (5 << 3) | 4, G0, G1, ON);
    t = add_frame(E, &n, t + 1300, (7 << 3) | 4 | 1, G0, G1, ON);
    sim_run(&S, E, n, t + 1500);
    CHECK(S.ntx == 3 && (S.tx[2].pkt[0] & 7) == 2 && (S.tx[2].pkt[1] >> 5) == 7,
          "two ACKs, seq 2, bank 7 (ntx=%d)", S.ntx);

    /* trip shows in the flags, current is reported raw */
    sim_defaults(); S.i_ma = 170;
    n = 0; E[n++] = (edge_t){ 0, 1 };
    t = add_frame(E, &n, 1500, (5 << 3) | 4, G0, G1, ON);
    sim_run(&S, E, n, t + 1500);
    r = &S.tx[S.ntx - 1];
    CHECK((r->pkt[2] & 0x80) && !(r->pkt[2] & 0x02) && !(r->pkt[1] & 0x08) && r->pkt[4] == 170,
          "trip flags 0x%02X state 0x%02X I %u", r->pkt[2], r->pkt[1], r->pkt[4]);
}

static void test_budget(void)
{
    /* worst plausible panel: every sticky, errors, LNA lamps, show mode */
    app_t a;
    uint8_t img[PANEL_BYTES];
    int p, e, worst = 0;
    for (p = 0; p < 64; p++)
        for (e = 0; e <= (int)ERR_DROP; e++) {
            memset(&a, 0, sizeof a);
            a.bank = PL_BANK(p); a.lna_path = 1; a.auto_byp = 1; a.fmn = 1;
            a.i_class = I_HI; a.trip = 1; a.active = 1; a.scap_ok = 1; a.rail_low = 1;
            a.st_drop = a.st_glt = a.st_rst = 1; a.bit_lamp = 1; a.last_bit = 1;
            a.frame_n = 15;
            { int b[16], k; frame_bits((uint8_t)p, b); for (k = 0; k < 15; k++) if (b[k]) a.frame_bits |= 1u << k; }
            a.result = e ? RES_ERR : RES_OK; a.err = (uint8_t)e;
            panel_render(&a, img);
            if (panel_count(img) > worst) worst = panel_count(img);
        }
    printf("  panel worst case outside lamp test: %d lamps\n", worst);
    CHECK(worst <= 24, "LED budget: %d lamps > 24", worst);
}

int main(void)
{
    printf("Coaxolotl host tests (TICK_HZ=%u)\n", TICK_HZ);
    test_all_payloads();
    test_robustness();
    test_errors();
    test_app();
    test_beacon();
    test_budget();
    printf("%s: %d/%d checks passed\n", fails ? "FAILED" : "OK", checks - fails, checks);
    return fails ? 1 : 0;
}
