// Unit tests for coaxlink (frames, replies, demodulator). Build/run:
//   make -C sdrpp test
//   sdrpp/build/test_coaxlink capture.cf32 2.4e6     # decode a capture
#include "coaxlink.hpp"

#include <cmath>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <random>

static int fails = 0, checks = 0;
#define CHECK(cond, ...) do { checks++; if (!(cond)) { fails++; \
    printf("FAIL %s:%d: ", __FILE__, __LINE__); printf(__VA_ARGS__); printf("\n"); } } while (0)

using cf = std::complex<float>;

// one reply with a lead-in, carrier offset, chip skew, AWGN and a steady interferer
static std::vector<cf> synth(const std::array<uint8_t, coax::PKT_LEN>& pkt, double fs, double off,
                             double chip_ms, double snr_db, unsigned seed, double intf_hz) {
    auto ch = coax::chips(pkt);
    size_t lead = (size_t)(fs * 0.3), body = (size_t)std::lround(ch.size() * fs * chip_ms / 1000);
    size_t n = lead + body + (size_t)(fs * 0.2);
    std::mt19937 rng(seed);
    double sigma = std::sqrt(std::pow(10.0, -snr_db / 10) / 2);
    std::normal_distribution<double> g(0, sigma);
    std::vector<cf> x(n);
    for (size_t i = 0; i < n; i++) {
        double t = i / fs, a = 0;
        if (i >= lead && i < lead + body) { a = ch[(size_t)((i - lead) / (fs * chip_ms / 1000))]; }
        std::complex<double> s = a * std::polar(1.0, 2 * M_PI * off * t) +
                                 3.0 * std::polar(1.0, 2 * M_PI * intf_hz * t);
        x[i] = cf((float)(s.real() + g(rng)), (float)(s.imag() + g(rng)));
    }
    return x;
}

static void test_frames() {
    coax::Frame f{ 6, true, false, false };
    CHECK(f.payload() == 0x34, "payload 0x%02X", f.payload());
    auto b = f.bits();
    int want[15] = { 1, 0, 1, 1, 1, 0, 1, 0, 0, 0, 0, 1, 0, 1, 1 };   // host test vector
    CHECK(std::equal(b.begin(), b.end(), want), "bits of F6+LNA");
    auto e = coax::edges(f);
    CHECK(e.size() == 31 && e[1].t_ms == 400 && !e[1].on && e[2].t_ms == 470 && e.back().on,
          "edge schedule");
    coax::Frame cf;
    cf.config = true;
    cf.panel = 1;
    auto cb = cf.bits();
    int cwant[15] = { 0, 1, 0, 0, 1, 0, 0, 0, 0, 1, 0, 1, 1, 1, 1 };   // host ConfigFrame(panel=1)
    CHECK(cf.payload() == 0x10 && std::equal(cb.begin(), cb.end(), cwant), "config frame bits");
    coax::Timing bad;
    bad.gap1 = 40;
    CHECK(coax::Timing().valid() && !bad.valid(), "timing guard");
}

static void test_reply() {
    const uint8_t chk[] = "123456789";
    CHECK(coax::crc8(chk, 9) == 0xFB, "crc check 0x%02X", coax::crc8(chk, 9));
    auto p = coax::pack(2, 0, 1, 0xDC, 0x22, 4300, 100, 3900);
    const uint8_t fw[] = { 0x81, 0xDC, 0x22, 0xD7, 0x64, 0xC3, 0xAC };     // firmware sim output
    CHECK(!memcmp(p.data(), fw, 7), "pack() == firmware bcn_build()");
    auto r = coax::Reply::parse(p);
    CHECK(r && r->kind == coax::Kind::ACK && r->bank == 6 && r->lnaPath && r->fmn && r->hold &&
          r->i_lna_ma == 100 && std::fabs(r->vcoax_v - 4.30f) < 1e-4, "parse");
    CHECK(r && r->summary() == "ACK seq=1 F6 LNA+FMN  Vcoax=4.30 V  I_lna=100 mA  Vscap=3.90 V  flags=HOLD",
          "summary '%s'", r ? r->summary().c_str() : "");
    p[3] ^= 1;
    CHECK(!coax::Reply::parse(p), "CRC catches a flipped bit");
    CHECK(coax::chips(p).size() == 145, "145 chips");
}

static void test_demod() {
    auto pkt = coax::pack(3, 2, 4, 0x41, 0x84, 4020, 3, 2210);
    struct { double fs, off, chip, snr; } cases[] = {
        { 2.4e6, -700e3, 5.5, -10 }, { 2.4e6, 650e3, 4.5, -10 }, { 2.048e6, 0.3e6, 5.0, -12 },
        { 1.024e6, -200e3, 5.2, -5 }, { 250e3, 40e3, 4.8, 5 },
    };
    unsigned seed = 1;
    for (auto& c : cases) {
        auto iq = synth(pkt, c.fs, c.off, c.chip, c.snr, seed++, -c.off / 2);
        auto h = coax::demodulate(iq, c.fs);
        CHECK(h.reply && h.reply->raw == pkt, "fs=%.0f off=%.0f chip=%.1f: %s", c.fs, c.off, c.chip,
              h.note.c_str());
        CHECK(std::fabs(h.offset_hz - c.off) < std::max(2 * c.fs / 1024, 2000.0), "offset %.0f vs %.0f", h.offset_hz, c.off);
        CHECK(std::fabs(h.chip_ms - c.chip) < 0.3, "chip %.2f vs %.2f", h.chip_ms, c.chip);
        CHECK(std::fabs(h.t_ms - 300) < 2, "t %.1f", h.t_ms);
    }
    auto r = coax::demodulate(synth(pkt, 2.4e6, 0, 5, -80, 9, 300e3), 2.4e6);
    CHECK(!r.reply && r.note == "no beacon", "silence: %s", r.note.c_str());
}

static void test_bandplan() {
    CHECK(coax::branches().size() == 8, "8 branches");
    CHECK(coax::bankFor(145e6) == 6 && coax::bankFor(7.1e6) == 3 && coax::bankFor(1090e6) == 7 &&
          coax::bankFor(100e6) == 0 && coax::bankFor(50.3e6) == 5 && coax::bankFor(137e3) == 1,
          "bankFor");
}

int main(int argc, char** argv) {
    if (argc == 3) {
        std::ifstream f(argv[1], std::ios::binary);
        std::vector<cf> iq;
        cf s;
        while (f.read((char*)&s, sizeof s)) { iq.push_back(s); }
        auto h = coax::demodulate(iq, atof(argv[2]));
        printf("%s\n", h.reply ? h.reply->summary().c_str() : ("no reply: " + h.note).c_str());
        printf("offset %.0f Hz, on/off %.1f dB, chip %.2f ms, t=%.0f ms\n", h.offset_hz, h.snr_db,
               h.chip_ms, h.t_ms);
        return h.reply ? 0 : 1;
    }
    test_frames();
    test_reply();
    test_demod();
    test_bandplan();
    printf("%s: %d/%d checks passed\n", fails ? "FAILED" : "OK", checks - fails, checks);
    return fails ? 1 : 0;
}
