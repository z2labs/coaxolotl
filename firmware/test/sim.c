/*
 * coaxolotl-sim: replay bias edges through the real firmware logic and
 * draw the front panel.
 *
 *   python3 host/coaxolotl.py send --bank 6 --lna --backend dry-run \
 *       | firmware/build/sim [options]
 *
 * Input lines: "<t_ms> <level>"  ('#' starts a comment)
 * Options:
 *   --fall-delay MS   delay every falling edge (slow bias discharge)
 *   --jitter MS       uniform +/- jitter on every edge
 *   --seed N          jitter seed
 *   --ilna MA --scap MV --vc MV   simulated analog readings
 *   --fault-at MS     TPS2553 FAULT at this time
 *   --tail MS         keep simulating after the last edge (default 1500)
 *   -q                no event log, panel only
 */
#include "harness.h"

static edge_t E[65536];
static sim_t S;

int main(int argc, char **argv)
{
    double fall = 0, jit = 0, tail = 1500;
    unsigned seed = 1;
    char line[256];
    int n = 0, i;

    memset(&S, 0, sizeof S);
    S.vc_mv = 4300; S.scap_mv = 3900; S.i_ma = 100; S.fault_at = -1; S.verbose = 1;

    for (i = 1; i < argc; i++) {
        const char *a = argv[i];
        const char *v = (i + 1 < argc) ? argv[i + 1] : "0";
        if (!strcmp(a, "--fall-delay")) { fall = atof(v); i++; }
        else if (!strcmp(a, "--jitter")) { jit = atof(v); i++; }
        else if (!strcmp(a, "--seed")) { seed = (unsigned)atoi(v); i++; }
        else if (!strcmp(a, "--ilna")) { S.i_ma = (uint16_t)atoi(v); i++; }
        else if (!strcmp(a, "--scap")) { S.scap_mv = (uint16_t)atoi(v); i++; }
        else if (!strcmp(a, "--vc")) { S.vc_mv = (uint16_t)atoi(v); i++; }
        else if (!strcmp(a, "--fault-at")) { S.fault_at = atof(v); i++; }
        else if (!strcmp(a, "--tail")) { tail = atof(v); i++; }
        else if (!strcmp(a, "-q")) { S.verbose = 0; }
        else { fprintf(stderr, "unknown option %s\n", a); return 2; }
    }

    while (fgets(line, sizeof line, stdin) && n < 65535) {
        double t; int lvl;
        if (line[0] == '#' || sscanf(line, "%lf %d", &t, &lvl) != 2)
            continue;
        E[n++] = (edge_t){ t, lvl ? 1 : 0 };
    }
    if (n == 0 || !E[0].lvl) {
        fprintf(stderr, "sim: need edges, first one must be power-up (level 1)\n");
        return 2;
    }

    srand(seed);
    for (i = 1; i < n; i++) {
        if (!E[i].lvl) E[i].t += fall;
        if (jit > 0) E[i].t += ((double)rand() / RAND_MAX * 2.0 - 1.0) * jit;
        if (E[i].t < E[i - 1].t) E[i].t = E[i - 1].t;
    }

    sim_run(&S, E, n, E[n - 1].t + tail);
    printf("\nstate after %.0f ms: bank F%u, %s%s%s, panel %s\n", E[n - 1].t + tail - E[0].t,
           S.app.bank, S.app.lna_path ? "LNA" : "bypass",
           S.app.fmn ? " + FM notch" : "", S.app.quiet ? ", quiet" : "",
           S.app.panel == PANEL_BANDS ? "bands" : S.app.panel == PANEL_OFF ? "off" : "full");
    panel_print(&S.app);
    return 0;
}
