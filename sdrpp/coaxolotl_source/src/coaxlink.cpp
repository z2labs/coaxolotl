// Coaxolotl link, host side. See coaxlink.hpp.
#include "coaxlink.hpp"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <thread>

namespace coax {

// ---- command frames -------------------------------------------------------

bool Timing::valid() const {
    // same guard as host/coaxolotl.py Timing.check()
    return gap0 >= 8 && gap1 >= 1.8 * gap0 && gap1 <= 120 && on >= 40 && on <= 250 && pre >= 330;
}

uint8_t Frame::payload() const {
    return (uint8_t)(((bank & 7) << 3) | (lna ? 4 : 0) | (quiet ? 2 : 0) | (clr ? 1 : 0));
}

std::array<int, 15> Frame::bits() const {
    std::array<int, 15> b{};
    uint8_t p = payload();
    b[0] = 1; b[1] = 0; b[2] = 1;
    for (int i = 0; i < 6; i++) {
        b[3 + i] = (p >> (5 - i)) & 1;
        b[9 + i] = b[3 + i] ^ 1;
    }
    return b;
}

std::vector<Edge> edges(const Frame& f, const Timing& t) {
    std::vector<Edge> e;
    double now = 0;
    e.push_back({ now, true });
    now += t.pre;
    for (int b : f.bits()) {
        e.push_back({ now, false });
        now += b ? t.gap1 : t.gap0;
        e.push_back({ now, true });
        now += t.on;
    }
    return e;
}

std::vector<double> play(const std::vector<Edge>& e, const std::function<void(bool)>& setBias,
                         const std::function<bool()>& abort) {
    using clk = std::chrono::steady_clock;
    std::vector<double> late;
    auto t0 = clk::now();
    for (const Edge& ed : e) {
        auto target = t0 + std::chrono::duration_cast<clk::duration>(
                               std::chrono::duration<double, std::milli>(ed.t_ms));
        for (;;) {
            auto left = target - clk::now();
            if (left <= clk::duration::zero()) { break; }
            if (left > std::chrono::milliseconds(2)) {
                std::this_thread::sleep_for(left - std::chrono::milliseconds(2));
            }
        }
        if (abort && abort()) {
            setBias(true);            // never leave the box unpowered
            break;
        }
        setBias(ed.on);
        late.push_back(std::chrono::duration<double, std::milli>(clk::now() - target).count());
    }
    return late;
}

// ---- replies ----------------------------------------------------------------

uint8_t crc8(const uint8_t* p, int n) {
    uint8_t c = 0xFF;
    while (n--) {
        c ^= *p++;
        for (int i = 0; i < 8; i++) { c = (c & 0x80) ? (uint8_t)((c << 1) ^ 0x07) : (uint8_t)(c << 1); }
    }
    return c;
}

std::vector<int> chips(const std::array<uint8_t, PKT_LEN>& pkt) {
    std::vector<int> bits(PRE_BITS, 1);
    for (int i = 0; i < 8; i++) { bits.push_back((SYNC >> (7 - i)) & 1); }
    for (uint8_t byte : pkt) {
        for (int i = 0; i < 8; i++) { bits.push_back((byte >> (7 - i)) & 1); }
    }
    std::vector<int> c;
    for (int b : bits) {
        c.push_back(b);
        c.push_back(b ^ 1);
    }
    c.push_back(1);
    return c;
}

std::array<uint8_t, PKT_LEN> pack(int kind, int err, int seq, uint8_t state, uint8_t flags,
                                  int vcoax_mv, int i_ma, int vscap_mv) {
    auto q = [](int mv) { return (uint8_t)std::min(255, (mv + 10) / 20); };
    std::array<uint8_t, PKT_LEN> p{};
    p[0] = (uint8_t)((kind << 6) | ((err & 7) << 3) | (seq & 7));
    p[1] = state;
    p[2] = (uint8_t)(flags & 0xFE);
    p[3] = q(vcoax_mv);
    p[4] = (uint8_t)std::min(255, i_ma);
    p[5] = q(vscap_mv);
    p[6] = crc8(p.data(), 6);
    return p;
}

std::optional<Reply> Reply::parse(const std::array<uint8_t, PKT_LEN>& p) {
    if (crc8(p.data(), 6) != p[6]) { return std::nullopt; }
    int k = p[0] >> 6;
    if (k < 1) { return std::nullopt; }
    Reply r{};
    r.kind = (Kind)k;
    r.err = (p[0] >> 3) & 7;
    r.seq = p[0] & 7;
    r.bank = p[1] >> 5;
    r.lnaReq = p[1] & 0x10; r.lnaPath = p[1] & 0x08; r.fmn = p[1] & 0x04;
    r.autoByp = p[1] & 0x02; r.quiet = p[1] & 0x01;
    r.trip = p[2] & 0x80; r.low = p[2] & 0x40; r.hold = p[2] & 0x20; r.drop = p[2] & 0x10;
    r.glt = p[2] & 0x08; r.rst = p[2] & 0x04; r.lnaEn = p[2] & 0x02;
    r.vcoax_v = p[3] * 0.02f;
    r.i_lna_ma = p[4];
    r.vscap_v = p[5] * 0.02f;
    r.raw = p;
    return r;
}

const char* Reply::kindName() const {
    switch (kind) {
    case Kind::HELLO: return "HELLO";
    case Kind::ACK: return "ACK";
    default: return "NAK";
    }
}

const char* Reply::errName() const {
    static const char* n[] = { "-", "SYNC", "CHK", "GAP", "RUNT", "TMO", "DROP", "?" };
    return n[err & 7];
}

std::string Reply::summary() const {
    char buf[256];
    std::string head = kind == Kind::NAK ? std::string("NAK(") + errName() + ")" : kindName();
    std::string path = lnaPath ? "LNA" : "BYP";
    if (autoByp) { path += "/AUTO"; }
    if (fmn) { path += "+FMN"; }
    std::string fl;
    const char* names[] = { "TRIP", "LOW", "HOLD", "DROP", "GLT", "RST" };
    bool on[] = { trip, low, hold, drop, glt, rst };
    for (int i = 0; i < 6; i++) {
        if (on[i]) { fl += (fl.empty() ? "" : ",") + std::string(names[i]); }
    }
    snprintf(buf, sizeof buf, "%s seq=%d F%d %s%s  Vcoax=%.2f V  I_lna=%d mA  Vscap=%.2f V  flags=%s",
             head.c_str(), seq, bank, path.c_str(), quiet ? " QUIET" : "", vcoax_v, i_lna_ma,
             vscap_v, fl.empty() ? "-" : fl.c_str());
    return buf;
}

// ---- demodulator --------------------------------------------------------------
// Same algorithm as host/backchannel.py demodulate():
//  1. spectrogram with frames of <= 0.6 ms
//  2. the beacon bin is the one with the largest 98th/20th percentile
//     spread (it switches on and off; noise and steady carriers do not)
//  3. that bin's envelope -> threshold -> majority filter -> runs
//  4. chip length = median of the single-chip runs; runs -> chips
//  5. find preamble tail + sync, Manchester-decode, check the CRC

namespace {

void fft(std::vector<std::complex<float>>& a) {
    const size_t n = a.size();
    for (size_t i = 1, j = 0; i < n; i++) {
        size_t bit = n >> 1;
        for (; j & bit; bit >>= 1) { j ^= bit; }
        j ^= bit;
        if (i < j) { std::swap(a[i], a[j]); }
    }
    for (size_t len = 2; len <= n; len <<= 1) {
        double ang = -2 * M_PI / (double)len;
        std::complex<float> wl((float)cos(ang), (float)sin(ang));
        for (size_t i = 0; i < n; i += len) {
            std::complex<float> w(1);
            for (size_t j = 0; j < len / 2; j++) {
                auto u = a[i + j], v = a[i + j + len / 2] * w;
                a[i + j] = u + v;
                a[i + j + len / 2] = u - v;
                w *= wl;
            }
        }
    }
}

int fftSize(double fs) {
    int n = 64;
    while (n * 2 <= fs * 0.0006) { n *= 2; }
    return n;
}

float percentile(std::vector<float> v, double q) {
    size_t k = (size_t)std::lround(q / 100.0 * (double)(v.size() - 1));
    std::nth_element(v.begin(), v.begin() + k, v.end());
    return v[k];
}

std::optional<std::array<uint8_t, PKT_LEN>> manchester(const std::vector<int>& seq) {
    std::vector<int> pat = { 1, 0, 1, 0, 1, 0, 1, 0 };
    for (int i = 7; i >= 0; i--) {
        int b = (SYNC >> i) & 1;
        pat.push_back(b);
        pat.push_back(b ^ 1);
    }
    const size_t n = pat.size(), need = n + 16 * PKT_LEN;
    for (size_t i = 0; i + need <= seq.size(); i++) {
        if (!std::equal(pat.begin(), pat.end(), seq.begin() + i)) { continue; }
        std::array<uint8_t, PKT_LEN> out{};
        bool ok = true;
        for (int k = 0; k < PKT_LEN && ok; k++) {
            int v = 0;
            for (int j = 0; j < 8; j++) {
                int a = seq[i + n + 16 * k + 2 * j], b = seq[i + n + 16 * k + 2 * j + 1];
                if (a == b) { ok = false; break; }
                v = (v << 1) | a;
            }
            out[k] = (uint8_t)v;
        }
        if (ok && crc8(out.data(), 6) == out[6]) { return out; }
    }
    return std::nullopt;
}

}  // namespace

Heard demodulate(const std::vector<std::complex<float>>& iq, double fs, double chip_ms,
                 double min_snr_db) {
    Heard h;
    const int n = fftSize(fs);
    const size_t frames = iq.size() / n;
    if (frames < 64) {
        h.note = "capture too short";
        return h;
    }

    // 1. power spectrum per frame, fftshifted, summed over 3 adjacent bins
    const int nb = n - 2;
    std::vector<float> win(n), p(frames * nb);
    for (int i = 0; i < n; i++) { win[i] = 0.5f - 0.5f * (float)cos(2 * M_PI * i / (n - 1)); }
    std::vector<std::complex<float>> buf(n);
    std::vector<float> pw(n);
    for (size_t f = 0; f < frames; f++) {
        for (int i = 0; i < n; i++) { buf[i] = iq[f * n + i] * win[i]; }
        fft(buf);
        for (int i = 0; i < n; i++) { pw[i] = std::norm(buf[(i + n / 2) % n]); }
        for (int j = 0; j < nb; j++) { p[f * nb + j] = pw[j] + pw[j + 1] + pw[j + 2]; }
    }

    // 2. the bin that keys on and off
    std::vector<float> hi(nb), lo(nb), col(frames);
    int best = -1;
    double bestScore = 0;
    const int c = nb / 2;
    for (int j = 0; j < nb; j++) {
        for (size_t f = 0; f < frames; f++) { col[f] = p[f * nb + j]; }
        hi[j] = percentile(col, 98);
        lo[j] = percentile(col, 20) + 1e-20f;
        if (j >= c - 3 && j < c + 3) { continue; }   // dongle DC spike
        double s = hi[j] / lo[j];
        if (s > bestScore) { bestScore = s; best = j; }
    }
    if (best < 0) {
        h.note = "no beacon";
        return h;
    }
    h.snr_db = 10 * log10(bestScore);
    h.offset_hz = (double)(best + 1 - n / 2) * fs / n;
    if (h.snr_db < min_snr_db) {
        h.note = "no beacon";
        return h;
    }

    // 3. envelope -> bits -> majority vote over ~0.3 chip -> runs
    const double frame_ms = n / fs * 1000.0;
    const float thr = std::sqrt(hi[best] * lo[best]);
    std::vector<int> raw(frames), b(frames);
    for (size_t f = 0; f < frames; f++) { raw[f] = p[f * nb + best] > thr; }
    int w = std::max(3, (int)std::lround(0.3 * chip_ms / frame_ms) | 1);
    for (size_t f = 0; f < frames; f++) {
        int s = 0;
        for (int k = -w / 2; k <= w / 2; k++) {
            long i = (long)f + k;
            if (i >= 0 && i < (long)frames) { s += raw[i]; }
        }
        b[f] = s > w / 2;
    }
    struct Run { int lvl; size_t len, start; };
    std::vector<Run> runs;
    for (size_t f = 0; f < frames;) {
        size_t g = f;
        while (g < frames && b[g] == b[f]) { g++; }
        runs.push_back({ b[f], g - f, f });
        f = g;
    }

    // 4. chip clock from the single-chip runs
    std::vector<double> shorts;
    for (size_t i = 1; i + 1 < runs.size(); i++) {
        double ms = runs[i].len * frame_ms;
        if (ms >= 0.6 * chip_ms && ms <= 1.4 * chip_ms) { shorts.push_back(ms); }
    }
    if (shorts.size() < 8) {
        h.note = "no chip clock";
        return h;
    }
    std::nth_element(shorts.begin(), shorts.begin() + shorts.size() / 2, shorts.end());
    const double tChip = shorts[shorts.size() / 2];
    h.chip_ms = tChip;

    // 5. segments of 1- and 2-chip runs, closed by silence
    runs.push_back({ 0, (size_t)1 << 40, frames });
    std::vector<int> seq;
    size_t segStart = 0;
    for (const Run& r : runs) {
        double m = r.len * frame_ms / tChip;
        int k = (int)std::lround(m);
        if (k >= 1 && k <= 2 && std::fabs(m - k) < 0.4) {
            if (seq.empty()) { segStart = r.start; }
            seq.insert(seq.end(), k, r.lvl);
            continue;
        }
        if (r.lvl == 0 && !seq.empty()) {
            seq.push_back(0);
            if (auto pkt = manchester(seq)) {
                h.reply = Reply::parse(*pkt);
                h.t_ms = segStart * frame_ms;
                return h;
            }
        }
        seq.clear();
    }
    h.note = "carrier heard, no valid packet";
    return h;
}

// ---- band plan ----------------------------------------------------------------

const std::vector<Branch>& branches() {
    static const std::vector<Branch> b = {
#include "bandplan.inc"
    };
    return b;
}

int bankFor(double freq_hz) {
    int best = 0;
    double width = 1e18;
    for (const Branch& b : branches()) {
        if (b.id == 0 || freq_hz < b.lo_hz || freq_hz > b.hi_hz) { continue; }
        if (b.hi_hz - b.lo_hz < width) {
            width = b.hi_hz - b.lo_hz;
            best = b.id;
        }
    }
    return best;
}

}  // namespace coax
